"""工具发现与自供应。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from datetime import timezone
import inspect
import re
import shutil
import subprocess
from typing import Any, Dict, Optional

from src.execution.sandbox import ShadowSandbox
from src.observability import get_action_journal
from src.utils.logger import log


@dataclass(frozen=True)
class PendingProvisionTask:
    """待审批的工具补全任务。"""

    dependency: str
    install_command: str
    target_image: str
    requested_at: str
    reason: str
    approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ToolProvisioner:
    """根据影子执行反馈自动补全缺失依赖。"""

    SAFE_ALLOW_LIST = {
        "pandas",
        "numpy",
        "beautifulsoup4",
        "requests",
        "lxml",
        "openpyxl",
    }

    def __init__(self, sandbox: ShadowSandbox):
        self.sandbox = sandbox
        self.journal = get_action_journal()

    def cleanup_orphaned_images(
        self,
        *,
        keep_last_n: int = 3,
        max_age_hours: int = 48,
    ) -> Dict[str, Any]:
        docker_executable = shutil.which("docker")
        if not docker_executable:
            self.journal.log_event(
                component="ToolProvisioner",
                stage="image_gc",
                action="cleanup_orphaned_images",
                status="failed",
                payload={"keep_last_n": keep_last_n, "max_age_hours": max_age_hours},
                reason="未检测到 docker CLI",
                priority="normal",
            )
            return {
                "success": False,
                "removed": [],
                "kept": [],
                "skipped": [],
                "error": "未检测到 docker CLI",
            }

        images_result = subprocess.run(
            [
                docker_executable,
                "images",
                "--format",
                "{{.Repository}}:{{.Tag}}|{{.ID}}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if images_result.returncode != 0:
            self.journal.log_event(
                component="ToolProvisioner",
                stage="image_gc",
                action="cleanup_orphaned_images",
                status="failed",
                payload={"keep_last_n": keep_last_n, "max_age_hours": max_age_hours},
                reason=(images_result.stderr or images_result.stdout).strip(),
                priority="normal",
            )
            return {
                "success": False,
                "removed": [],
                "kept": [],
                "skipped": [],
                "error": (images_result.stderr or images_result.stdout).strip(),
            }

        candidates = []
        for line in images_result.stdout.splitlines():
            item = line.strip()
            if not item or "|" not in item:
                continue
            name, image_id = item.split("|", 1)
            if not name.startswith("abu-env-"):
                continue
            inspect = subprocess.run(
                [
                    docker_executable,
                    "image",
                    "inspect",
                    name,
                    "--format",
                    "{{.Created}}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if inspect.returncode != 0:
                continue
            created_at = self._parse_image_created_at(inspect.stdout.strip())
            candidates.append(
                {
                    "name": name,
                    "id": image_id.strip(),
                    "created_at": created_at,
                }
            )

        candidates.sort(key=lambda item: item["created_at"], reverse=True)
        now = datetime.now(timezone.utc)
        protected = {self.sandbox.image}
        kept, removed, skipped = [], [], []
        for index, item in enumerate(candidates):
            image_name = str(item["name"])
            age_hours = max(
                0.0,
                (now - item["created_at"]).total_seconds() / 3600.0,
            )
            if (
                image_name in protected
                or index < keep_last_n
                or age_hours < max_age_hours
            ):
                kept.append(image_name)
                continue
            delete = subprocess.run(
                [docker_executable, "rmi", image_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            if delete.returncode == 0:
                removed.append(image_name)
            else:
                skipped.append(
                    {
                        "image": image_name,
                        "reason": (delete.stderr or delete.stdout).strip()
                        or "镜像删除失败",
                    }
                )

        result = {
            "success": True,
            "removed": removed,
            "kept": kept,
            "skipped": skipped,
            "total_candidates": len(candidates),
        }
        self.journal.log_event(
            component="ToolProvisioner",
            stage="image_gc",
            action="cleanup_orphaned_images",
            status="success",
            payload=result,
            priority="normal",
        )
        return result

    async def request_provision(
        self,
        dep_name: str,
        *,
        reason: str = "world_model_missing_dependency",
        auto_approve: bool = False,
        timeout: int = 180,
        trace_context: Optional[Dict[str, Any]] = None,
        parent_trace_id: str = "",
        exchange_id: str = "",
    ) -> Dict[str, Any]:
        context = trace_context or self.journal.reserve_event_context(
            parent_trace_id=parent_trace_id,
            exchange_id=exchange_id,
        )
        normalized = self._normalize_dependency_name(dep_name)
        if normalized.lower() not in self.SAFE_ALLOW_LIST:
            self.journal.log_event(
                component="ToolProvisioner",
                stage="request_provision",
                action="request_provision",
                status="rejected",
                payload={"dependency": normalized},
                reason=f"工具 '{normalized}' 不在安全白名单中",
                priority="critical",
                context=context,
            )
            return {
                "status": "rejected",
                "requested_dependency": normalized,
                "reason": f"工具 '{normalized}' 不在安全白名单中",
                "allowed": False,
            }

        task = self._build_pending_task(normalized, reason=reason)
        if not auto_approve:
            log.info(
                "📝 工具补全进入待审批队列 | dependency={} | target_image={}",
                task.dependency,
                task.target_image,
            )
            return {
                "status": "pending",
                "requested_dependency": normalized,
                "allowed": True,
                "task": task.to_dict(),
                "trace_context": context,
            }
        return await self.approve_pending_task(
            task, timeout=timeout, trace_context=context
        )

    async def approve_pending_task(
        self,
        task: PendingProvisionTask,
        *,
        timeout: int = 180,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = trace_context or self.journal.reserve_event_context()
        self.journal.log_event(
            component="ToolProvisioner",
            stage="approve_provision",
            action="approve_pending_task",
            status="started",
            payload={"dependency": task.dependency, "target_image": task.target_image},
            priority="critical",
            context=context,
        )
        provision_signature = inspect.signature(self.sandbox.provision_tool)
        if "trace_context" in provision_signature.parameters:
            result = await asyncio.to_thread(
                self.sandbox.provision_tool,
                task.install_command,
                task.target_image,
                timeout,
                context,
            )
        else:
            result = await asyncio.to_thread(
                self.sandbox.provision_tool,
                task.install_command,
                task.target_image,
                timeout,
            )
        payload = {
            "status": "completed" if result.get("success") else "failed",
            "requested_dependency": task.dependency,
            "install_command": task.install_command,
            "new_image": result.get("new_image", ""),
            "backend": result.get("backend"),
            "success": bool(result.get("success")),
            "error": result.get("error", ""),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "committed": bool(result.get("committed")),
            "task": task.to_dict(),
            "trace_context": context,
        }
        if payload["success"]:
            self.sandbox.image = task.target_image
            log.info(
                "🧰 工具发现完成 | dependency={} | new_image={} | backend={}",
                task.dependency,
                task.target_image,
                payload["backend"],
            )
        else:
            log.warning(
                "⚠️ 工具发现失败 | dependency={} | backend={} | error={}",
                task.dependency,
                payload["backend"],
                payload["error"],
            )
        self.journal.log_event(
            component="ToolProvisioner",
            stage="approve_provision",
            action="approve_pending_task",
            status="success" if payload["success"] else "failed",
            payload={
                "dependency": task.dependency,
                "backend": payload.get("backend"),
                "new_image": payload.get("new_image"),
                "committed": payload.get("committed"),
            },
            reason=payload.get("error", ""),
            priority="critical",
            context=context,
        )
        return payload

    async def solve_missing_dependency(
        self,
        dep_name: str,
        *,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        pending = await self.request_provision(
            dep_name,
            auto_approve=True,
            timeout=timeout,
        )
        return pending

    async def solve_from_shadow_result(
        self,
        shadow_result: Dict[str, Any],
        *,
        timeout: int = 180,
        auto_approve: bool = False,
    ) -> Dict[str, Any]:
        parent_trace_id = ""
        trace_context = shadow_result.get("trace_context") or {}
        if isinstance(trace_context, dict):
            parent_trace_id = str(trace_context.get("trace_id") or "")
        observation = shadow_result.get("world_model_observation") or {}
        dep_name = observation.get("missing_dependency")
        if not dep_name:
            return {
                "success": False,
                "requested_dependency": "",
                "error": "当前影子结果未识别出缺失依赖",
                "skipped": True,
            }
        payload = await self.request_provision(
            dep_name,
            reason=observation.get("failure_cause") or "world_model_missing_dependency",
            auto_approve=auto_approve,
            timeout=timeout,
            parent_trace_id=parent_trace_id,
        )
        payload["triggered_by"] = observation.get("failure_cause")
        payload["next_priority_target"] = observation.get("next_priority_target")
        return payload

    def _build_pending_task(
        self, dep_name: str, *, reason: str
    ) -> PendingProvisionTask:
        normalized = self._normalize_dependency_name(dep_name)
        return PendingProvisionTask(
            dependency=normalized,
            install_command=f"python -m pip install {normalized} --no-cache-dir",
            target_image=f"abu-env-{normalized}",
            requested_at=datetime.now().isoformat(),
            reason=reason,
        )

    @staticmethod
    def _normalize_dependency_name(dep_name: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "", dep_name or "").strip()
        if not normalized:
            raise ValueError("缺失依赖名称不能为空")
        return normalized

    @staticmethod
    def _parse_image_created_at(raw_value: str) -> datetime:
        normalized = (raw_value or "").strip().replace("Z", "+00:00")
        if not normalized:
            return datetime.fromtimestamp(0, tz=timezone.utc)
        try:
            created = datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.fromtimestamp(0, tz=timezone.utc)
        if created.tzinfo is None:
            return created.replace(tzinfo=timezone.utc)
        return created.astimezone(timezone.utc)
