"""影子执行沙盒。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib
import shutil
import subprocess
import time
import uuid
from typing import Any, Dict, Optional

from src.observability import get_action_journal
from src.security import LogShredder
from src.utils.logger import log
from src.world_model import WorldModel


@dataclass(frozen=True)
class ShadowRunResult:
    """单次影子执行结果。"""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    image: str
    backend: str
    timed_out: bool = False
    container_name: str = ""
    world_model_observation: Optional[Dict[str, Any]] = None
    fallback_reason: str = ""
    trace_context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration": self.duration,
            "image": self.image,
            "backend": self.backend,
            "timed_out": self.timed_out,
            "container_name": self.container_name,
            "world_model_observation": self.world_model_observation,
            "fallback_reason": self.fallback_reason,
            "trace_context": self.trace_context,
        }


class ShadowSandbox:
    """通过 Docker SDK 优先、CLI 降级的双通道隔离执行环境。"""

    def __init__(
        self,
        image: str = "python:3.10-slim",
        *,
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        timeout: int = 20,
        max_ttl_seconds: int = 300,
        auto_pull: bool = True,
        sdk_timeout: int = 3,
    ):
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.timeout = timeout
        self.max_ttl_seconds = max(1, int(max_ttl_seconds))
        self.auto_pull = auto_pull
        self.sdk_timeout = sdk_timeout
        self.backend = "docker_cli"
        self.world_model = WorldModel()
        self._docker_executable = shutil.which("docker")
        self._sdk_module = None
        self._sdk_client = None
        self._sdk_ready = False
        self._sdk_error = ""
        self._log_shredder = LogShredder()
        self._journal = get_action_journal()

        self._initialize_backends()
        self._ensure_image_ready()
        log.info(
            "🧪 影子执行沙盒已初始化 | backend={} | image={} | memory_limit={} | cpu_limit={} | sdk_ready={}",
            self.backend,
            self.image,
            self.memory_limit,
            self.cpu_limit,
            self._sdk_ready,
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "available": True,
            "backend": self.backend,
            "image": self.image,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "timeout": self.timeout,
            "max_ttl_seconds": self.max_ttl_seconds,
            "sdk_ready": self._sdk_ready,
            "sdk_error": self._sdk_error or None,
            "cli_ready": bool(self._docker_executable),
        }

    def execute_shadow_task(
        self,
        python_code: str,
        timeout: Optional[int] = None,
        trace_context: Optional[Dict[str, Any]] = None,
        allow_network: bool = False,
    ) -> Dict[str, Any]:
        effective_timeout = self._resolve_effective_timeout(timeout)
        fallback_reason = ""
        context = trace_context or self._journal.reserve_event_context()
        self._journal.log_event(
            component="ShadowSandbox",
            stage="shadow_execution",
            action="execute_shadow_task",
            status="started",
            payload={
                "image": self.image,
                "backend": self.backend,
                "timeout": effective_timeout,
                "allow_network": allow_network,
            },
            priority="critical",
            context=context,
        )

        if self._sdk_ready:
            try:
                return self._execute_via_sdk(
                    python_code,
                    timeout=effective_timeout,
                    trace_context=context,
                    allow_network=allow_network,
                )
            except Exception as exc:
                fallback_reason = str(exc)
                log.warning("⚠️ SDK 路径执行失败，切换到 CLI | error={}", exc)

        if not self._docker_executable:
            raise RuntimeError("SDK 不可用且未检测到 docker CLI，无法执行影子任务")

        result = self._execute_via_cli(
            python_code,
            timeout=effective_timeout,
            fallback_reason=fallback_reason,
            trace_context=context,
            allow_network=allow_network,
        )
        return result

    def provision_tool(
        self,
        install_cmd: str,
        new_tag: str,
        timeout: int = 60,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        fallback_reason = ""
        effective_timeout = self._resolve_effective_timeout(timeout)
        context = trace_context or self._journal.reserve_event_context()
        self._journal.log_event(
            component="ShadowSandbox",
            stage="tool_provision",
            action="provision_tool",
            status="started",
            payload={
                "image": self.image,
                "new_tag": new_tag,
                "timeout": effective_timeout,
                "max_ttl_seconds": self.max_ttl_seconds,
            },
            priority="critical",
            context=context,
        )

        if self._sdk_ready:
            try:
                sdk_result = self._provision_via_sdk(
                    install_cmd=install_cmd,
                    new_tag=new_tag,
                    timeout=effective_timeout,
                    trace_context=context,
                )
                if sdk_result.get("success"):
                    return sdk_result
                fallback_reason = sdk_result.get("error", "") or "sdk provision failed"
                log.warning(
                    "⚠️ SDK 自供应未完成，切换到 CLI | error={}", fallback_reason
                )
            except Exception as exc:
                fallback_reason = str(exc)
                log.warning("⚠️ SDK 自供应失败，切换到 CLI | error={}", exc)

        if not self._docker_executable:
            raise RuntimeError("SDK 不可用且未检测到 docker CLI，无法执行自供应")

        return self._provision_via_cli(
            install_cmd=install_cmd,
            new_tag=new_tag,
            timeout=effective_timeout,
            fallback_reason=fallback_reason,
            trace_context=context,
        )

    def _resolve_effective_timeout(self, requested_timeout: Optional[int]) -> int:
        desired = int(requested_timeout or self.timeout)
        if desired <= 0:
            desired = self.timeout
        return min(desired, self.max_ttl_seconds)

    def _initialize_backends(self) -> None:
        self._initialize_sdk_client()
        if self._sdk_ready:
            self.backend = "docker_sdk"
            return
        if not self._docker_executable:
            raise RuntimeError("未检测到可用的 Docker SDK 或 CLI，无法启用影子沙盒")
        self._ensure_cli_ready()
        self.backend = "docker_cli"

    def _initialize_sdk_client(self) -> None:
        try:
            docker_module = importlib.import_module("docker")
            client = docker_module.from_env(timeout=self.sdk_timeout)
            client.ping()
            self._sdk_module = docker_module
            self._sdk_client = client
            self._sdk_ready = True
            self._sdk_error = ""
        except Exception as exc:
            self._sdk_module = None
            self._sdk_client = None
            self._sdk_ready = False
            self._sdk_error = str(exc)

    def _ensure_cli_ready(self) -> None:
        docker_executable = self._require_docker_executable()
        probe = subprocess.run(
            [docker_executable, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(
                f"Docker 服务不可用: {(probe.stderr or probe.stdout or '').strip()}"
            )

    def _ensure_image_ready(self) -> None:
        if self._sdk_ready and self._sdk_client is not None:
            try:
                self._sdk_client.images.get(self.image)
                return
            except Exception:
                if not self.auto_pull:
                    raise RuntimeError(f"影子沙盒镜像不存在: {self.image}")
                log.info(
                    "📦 影子沙盒缺少镜像，开始通过 SDK 拉取 | image={}", self.image
                )
                self._sdk_client.images.pull(self.image)
                return

        docker_executable = self._require_docker_executable()
        inspect = subprocess.run(
            [docker_executable, "image", "inspect", self.image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        if inspect.returncode == 0:
            return
        if not self.auto_pull:
            raise RuntimeError(f"影子沙盒镜像不存在: {self.image}")
        log.info("📦 影子沙盒缺少镜像，开始通过 CLI 拉取 | image={}", self.image)
        pull = subprocess.run(
            [docker_executable, "pull", self.image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        if pull.returncode != 0:
            raise RuntimeError(
                f"影子沙盒镜像拉取失败: {(pull.stderr or pull.stdout).strip()}"
            )

    def _execute_via_sdk(
        self,
        python_code: str,
        *,
        timeout: int,
        trace_context: Dict[str, Any],
        allow_network: bool,
    ) -> Dict[str, Any]:
        if self._sdk_client is None:
            raise RuntimeError("Docker SDK 未初始化")

        container_name = f"abu-shadow-{uuid.uuid4().hex[:12]}"
        container = None
        started = time.perf_counter()
        try:
            container = self._sdk_client.containers.run(
                image=self.image,
                command=["python", "-c", python_code],
                detach=True,
                name=container_name,
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1_000_000_000),
                network_disabled=not allow_network,
                read_only=True,
                tmpfs={"/tmp": "rw,nosuid,nodev,size=64m"},
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
                pids_limit=128,
                auto_remove=False,
            )
            exit_status = self._wait_for_sdk_container(container, timeout=timeout)
            stdout = self._decode_logs(container.logs(stdout=True, stderr=False))
            stderr = self._decode_logs(container.logs(stdout=False, stderr=True))
            stdout = self._sanitize_observation_text(stdout)
            stderr = self._sanitize_observation_text(stderr)
            duration = round(time.perf_counter() - started, 6)
            return self._build_result(
                exit_code=int(exit_status.get("StatusCode", 1)),
                stdout=stdout,
                stderr=stderr,
                duration=duration,
                backend="docker_sdk",
                timed_out=False,
                container_name=container_name,
                trace_context=trace_context,
            )
        except Exception as exc:
            duration = round(time.perf_counter() - started, 6)
            if self._is_timeout_error(exc):
                return self._handle_timeout_result(
                    backend="docker_sdk",
                    timeout=timeout,
                    duration=duration,
                    container_name=container_name,
                    container=container,
                    trace_context=trace_context,
                )
            raise RuntimeError(f"SDK 执行失败: {exc}") from exc
        finally:
            self._cleanup_sdk_container(container)

    def _execute_via_cli(
        self,
        python_code: str,
        *,
        timeout: int,
        fallback_reason: str = "",
        trace_context: Dict[str, Any],
        allow_network: bool,
    ) -> Dict[str, Any]:
        container_name = f"abu-shadow-{uuid.uuid4().hex[:12]}"
        use_stdin = len(python_code) >= 6000
        command = self._build_docker_command(
            container_name,
            python_code,
            allow_network=allow_network,
            use_stdin=use_stdin,
        )
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                input=python_code if use_stdin else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            duration = round(time.perf_counter() - started, 6)
            return self._build_result(
                exit_code=completed.returncode,
                stdout=(completed.stdout or "").strip(),
                stderr=(completed.stderr or "").strip(),
                duration=duration,
                backend="docker_cli",
                timed_out=False,
                container_name=container_name,
                fallback_reason=fallback_reason,
                trace_context=trace_context,
            )
        except subprocess.TimeoutExpired as exc:
            duration = round(time.perf_counter() - started, 6)
            self._force_remove_container(container_name)
            stdout = self._normalize_timeout_output(exc.stdout)
            stderr = self._normalize_timeout_output(exc.stderr)
            return self._build_result(
                exit_code=124,
                stdout=stdout,
                stderr=stderr or "shadow sandbox timeout",
                duration=duration,
                backend="docker_cli",
                timed_out=True,
                container_name=container_name,
                fallback_reason=fallback_reason,
                trace_context=trace_context,
            )

    def _provision_via_sdk(
        self,
        *,
        install_cmd: str,
        new_tag: str,
        timeout: int,
        trace_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self._sdk_client is None:
            raise RuntimeError("Docker SDK 未初始化")

        container_name = f"abu-provision-{uuid.uuid4().hex[:12]}"
        container = None
        started = time.perf_counter()
        provision_cmd = self._build_provision_command(install_cmd)
        try:
            container = self._sdk_client.containers.run(
                image=self.image,
                command=["sh", "-lc", provision_cmd],
                detach=True,
                name=container_name,
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1_000_000_000),
                network_disabled=False,
                read_only=False,
                pids_limit=128,
                auto_remove=False,
            )
            exit_status = self._wait_for_sdk_container(container, timeout=timeout)
            stdout = self._decode_logs(container.logs(stdout=True, stderr=False))
            stderr = self._decode_logs(container.logs(stdout=False, stderr=True))
            duration = round(time.perf_counter() - started, 6)
            exit_code = int(exit_status.get("StatusCode", 1))
            if exit_code != 0:
                self._journal.log_event(
                    component="ShadowSandbox",
                    stage="tool_provision",
                    action="provision_tool",
                    status="failed",
                    payload={
                        "backend": "docker_sdk",
                        "new_tag": new_tag,
                        "stderr": stderr,
                        "stdout": stdout,
                    },
                    reason=stderr or stdout or "自供应执行失败",
                    priority="critical",
                    context=trace_context,
                )
                return {
                    "success": False,
                    "backend": "docker_sdk",
                    "new_image": "",
                    "duration": duration,
                    "stdout": stdout,
                    "stderr": stderr,
                    "error": stderr or stdout or "自供应执行失败",
                    "committed": False,
                    "fallback_reason": "",
                }

            repository, tag = self._split_image_tag(new_tag)
            committed = container.commit(repository=repository, tag=tag)
            log.info(
                "🧰 工具自供应完成 | backend=docker_sdk | base_image={} | new_image={} | container={}",
                self.image,
                new_tag,
                container_name,
            )
            self._journal.log_event(
                component="ShadowSandbox",
                stage="tool_provision",
                action="provision_tool",
                status="success",
                payload={"backend": "docker_sdk", "new_tag": new_tag, "stdout": stdout},
                priority="critical",
                context=trace_context,
            )
            return {
                "success": True,
                "backend": "docker_sdk",
                "new_image": new_tag,
                "duration": duration,
                "stdout": stdout,
                "stderr": stderr,
                "error": "",
                "committed": bool(committed),
                "fallback_reason": "",
                "trace_context": trace_context,
            }
        except Exception as exc:
            duration = round(time.perf_counter() - started, 6)
            if self._is_timeout_error(exc):
                self._cleanup_sdk_container(container, force=True)
                return {
                    "success": False,
                    "backend": "docker_sdk",
                    "new_image": "",
                    "duration": duration,
                    "stdout": "",
                    "stderr": "shadow provision timeout",
                    "error": "shadow provision timeout",
                    "committed": False,
                    "fallback_reason": "",
                    "trace_context": trace_context,
                }
            raise RuntimeError(f"SDK 自供应失败: {exc}") from exc
        finally:
            self._cleanup_sdk_container(container, force=True)

    def _provision_via_cli(
        self,
        *,
        install_cmd: str,
        new_tag: str,
        timeout: int,
        fallback_reason: str = "",
        trace_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        docker_executable = self._require_docker_executable()
        container_name = f"abu-provision-{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        provision_cmd = self._build_provision_command(install_cmd)
        run_command = [
            docker_executable,
            "run",
            "--name",
            container_name,
            "--network",
            "bridge",
            "--memory",
            self.memory_limit,
            "--cpus",
            str(self.cpu_limit),
            self.image,
            "sh",
            "-lc",
            provision_cmd,
        ]
        try:
            completed = subprocess.run(
                run_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            duration = round(time.perf_counter() - started, 6)
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()
            stdout = self._sanitize_observation_text(stdout)
            stderr = self._sanitize_observation_text(stderr)
            if completed.returncode != 0:
                self._force_remove_container(container_name)
                self._journal.log_event(
                    component="ShadowSandbox",
                    stage="tool_provision",
                    action="provision_tool",
                    status="failed",
                    payload={
                        "backend": "docker_cli",
                        "new_tag": new_tag,
                        "stderr": stderr,
                        "stdout": stdout,
                    },
                    reason=stderr or stdout or "自供应执行失败",
                    priority="critical",
                    context=trace_context,
                )
                return {
                    "success": False,
                    "backend": "docker_cli",
                    "new_image": "",
                    "duration": duration,
                    "stdout": stdout,
                    "stderr": stderr,
                    "error": stderr or stdout or "自供应执行失败",
                    "committed": False,
                    "fallback_reason": fallback_reason,
                    "trace_context": trace_context,
                }
            commit = subprocess.run(
                [docker_executable, "commit", container_name, new_tag],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            if commit.returncode != 0:
                self._force_remove_container(container_name)
                self._journal.log_event(
                    component="ShadowSandbox",
                    stage="tool_provision",
                    action="provision_tool",
                    status="failed",
                    payload={
                        "backend": "docker_cli",
                        "new_tag": new_tag,
                        "stderr": (commit.stderr or "").strip(),
                    },
                    reason=(commit.stderr or commit.stdout or "镜像固化失败").strip(),
                    priority="critical",
                    context=trace_context,
                )
                return {
                    "success": False,
                    "backend": "docker_cli",
                    "new_image": "",
                    "duration": duration,
                    "stdout": stdout,
                    "stderr": (commit.stderr or "").strip(),
                    "error": (commit.stderr or commit.stdout or "镜像固化失败").strip(),
                    "committed": False,
                    "fallback_reason": fallback_reason,
                    "trace_context": trace_context,
                }
            self._force_remove_container(container_name)
            log.info(
                "🧰 工具自供应完成 | backend=docker_cli | base_image={} | new_image={} | container={}",
                self.image,
                new_tag,
                container_name,
            )
            self._journal.log_event(
                component="ShadowSandbox",
                stage="tool_provision",
                action="provision_tool",
                status="success",
                payload={
                    "backend": "docker_cli",
                    "new_tag": new_tag,
                    "stdout": stdout,
                    "fallback_reason": fallback_reason,
                },
                priority="critical",
                context=trace_context,
            )
            return {
                "success": True,
                "backend": "docker_cli",
                "new_image": new_tag,
                "duration": duration,
                "stdout": stdout,
                "stderr": stderr,
                "error": "",
                "committed": True,
                "fallback_reason": fallback_reason,
                "trace_context": trace_context,
            }
        except subprocess.TimeoutExpired:
            self._force_remove_container(container_name)
            duration = round(time.perf_counter() - started, 6)
            self._journal.log_event(
                component="ShadowSandbox",
                stage="tool_provision",
                action="provision_tool",
                status="failed",
                payload={"backend": "docker_cli", "new_tag": new_tag},
                reason="shadow provision timeout",
                priority="critical",
                context=trace_context,
            )
            return {
                "success": False,
                "backend": "docker_cli",
                "new_image": "",
                "duration": duration,
                "stdout": "",
                "stderr": "shadow provision timeout",
                "error": "shadow provision timeout",
                "committed": False,
                "fallback_reason": fallback_reason,
                "trace_context": trace_context,
            }

    def _build_result(
        self,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration: float,
        backend: str,
        timed_out: bool,
        container_name: str,
        fallback_reason: str = "",
        trace_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        sanitized_stdout = self._sanitize_observation_text(stdout)
        sanitized_stderr = self._sanitize_observation_text(stderr)
        observation = self.world_model.verify_shadow_execution(
            exit_code=exit_code,
            stdout=sanitized_stdout,
            stderr=sanitized_stderr,
            duration_seconds=duration,
            sandbox_backend=backend,
            timed_out=timed_out,
        )
        result = ShadowRunResult(
            success=observation.verified,
            exit_code=exit_code,
            stdout=sanitized_stdout,
            stderr=sanitized_stderr,
            duration=duration,
            image=self.image,
            backend=backend,
            timed_out=timed_out,
            container_name=container_name,
            world_model_observation=asdict(observation),
            fallback_reason=fallback_reason,
            trace_context=trace_context,
        )
        self._journal.log_event(
            component="ShadowSandbox",
            stage="shadow_execution",
            action="execute_shadow_task",
            status="success" if observation.verified else "failed",
            payload={
                "backend": backend,
                "image": self.image,
                "exit_code": exit_code,
                "duration": duration,
                "timed_out": timed_out,
                "fallback_reason": fallback_reason,
            },
            reason=sanitized_stderr or sanitized_stdout[:256],
            priority="critical",
            context=trace_context,
        )
        log.info(
            "🧪 影子任务完成 | backend={} | image={} | exit_code={} | duration={:.3f}s",
            backend,
            self.image,
            exit_code,
            duration,
        )
        return result.to_dict()

    def _sanitize_observation_text(self, text: str) -> str:
        return self._log_shredder.sanitize_text(text)

    def _build_provision_command(self, install_cmd: str) -> str:
        cleanup_cmd = (
            "python -m pip cache purge >/dev/null 2>&1 || true; "
            "rm -rf /root/.cache/pip /tmp/pip-* /tmp/* /var/tmp/* >/dev/null 2>&1 || true; "
            "find / -type d -name __pycache__ -prune -exec rm -rf {} + >/dev/null 2>&1 || true; "
            "history -c >/dev/null 2>&1 || true"
        )
        return f"{install_cmd} && {cleanup_cmd}"

    def _build_docker_command(
        self,
        container_name: str,
        python_code: str,
        *,
        allow_network: bool,
        use_stdin: bool = False,
    ) -> list[str]:
        docker_executable = self._require_docker_executable()
        command = [
            docker_executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "bridge" if allow_network else "none",
            "--memory",
            self.memory_limit,
            "--cpus",
            str(self.cpu_limit),
            "--pids-limit",
            "128",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            self.image,
            "python",
        ]
        if use_stdin:
            command.insert(2, "-i")
            command.append("-")
        else:
            command.extend(["-c", python_code])
        return command

    @staticmethod
    def _decode_logs(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace").strip()
        return str(payload).strip()

    @staticmethod
    def _normalize_timeout_output(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace").strip()
        return str(payload).strip()

    @staticmethod
    def _split_image_tag(image: str) -> tuple[str, str]:
        last_colon = image.rfind(":")
        last_slash = image.rfind("/")
        if last_colon > last_slash:
            return image[:last_colon], image[last_colon + 1 :]
        return image, "latest"

    def _require_docker_executable(self) -> str:
        if not self._docker_executable:
            raise RuntimeError("未检测到 docker CLI")
        return self._docker_executable

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        exc_name = exc.__class__.__name__
        return (
            exc_name in {"ReadTimeout", "Timeout", "APIError", "TimeoutError"}
            and "timeout" in str(exc).lower()
        )

    @staticmethod
    def _wait_for_sdk_container(container: Any, *, timeout: int) -> Dict[str, Any]:
        started = time.perf_counter()
        while time.perf_counter() - started <= timeout:
            container.reload()
            state = (
                container.attrs.get("State", {}) if hasattr(container, "attrs") else {}
            )
            status = str(state.get("Status", "")).lower()
            if status in {"exited", "dead"}:
                return {"StatusCode": int(state.get("ExitCode", 1))}
            time.sleep(0.2)
        raise TimeoutError("shadow sdk timeout")

    def _handle_timeout_result(
        self,
        *,
        backend: str,
        timeout: int,
        duration: float,
        container_name: str,
        container: Any,
        trace_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        self._cleanup_sdk_container(container, force=True)
        log.warning(
            "⏱️ 影子任务超时 | backend={} | image={} | timeout={}s | container={}",
            backend,
            self.image,
            timeout,
            container_name,
        )
        return self._build_result(
            exit_code=124,
            stdout="",
            stderr="shadow sandbox timeout",
            duration=duration,
            backend=backend,
            timed_out=True,
            container_name=container_name,
            trace_context=trace_context,
        )

    def _cleanup_sdk_container(self, container: Any, force: bool = False) -> None:
        if container is None:
            return
        try:
            container.remove(force=force)
        except Exception:
            pass

    def _force_remove_container(self, container_name: str) -> None:
        if not self._docker_executable:
            return
        docker_executable = self._require_docker_executable()
        subprocess.run(
            [docker_executable, "rm", "-f", container_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
