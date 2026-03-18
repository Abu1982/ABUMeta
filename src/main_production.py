"""ABU 生产入口：点火自主巡航与演化闭环。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
import psutil
import sqlite3
import shutil
from urllib.error import URLError
from urllib.request import urlopen
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_probe import ensure_project_runtime


if __name__ == "__main__":
    ensure_project_runtime(
        REPO_ROOT,
        required_modules=("pydantic", "pydantic_settings", "psutil"),
    )

from config.settings import settings
from src.agent import AutonomousLifeLoop
from src.brain import CentralBrain
from src.data_connector import TradeInquiryAdapter
from src.execution import LeadCaptureTarget, SandboxLeadHarvester, ShadowSandbox
from src.observability import close_action_journal, get_action_journal
from src.perception.sensors import HostMachineSensor
from src.social import ThreeAgentCruiseCoordinator
from src.utils.logger import configure_logger, log
from src.utils.map_exporter import export_evolution_map


HEARTBEAT_SECONDS = 300
REPORTS_DIR = Path(settings.BASE_DIR) / "data" / "reports"
REPORTS_TEMPLATE_DIR = REPORTS_DIR / "_templates"
REPORTS_M12X_DIR = REPORTS_DIR / "_m12x"
CRUISE_END_AT: Optional[datetime] = None
HEARTBEAT_FILE = Path(settings.BASE_DIR) / ".abu.heartbeat"
RECOVERY_PROBE_FILE = Path(settings.BASE_DIR) / ".abu.recovery_probe"
PAUSE_PROBE_FILE = Path(settings.BASE_DIR) / ".abu.pause"


@dataclass(frozen=True)
class PendingInquiryFile:
    path: Path
    fingerprint: str


class RealDataWatcher:
    """监控询盘目录并避免重复消费。"""

    def __init__(self, *, inbox_dir: Path, archive_dir: Path, state_path: Path):
        self.inbox_dir = inbox_dir
        self.archive_dir = archive_dir
        self.state_path = state_path
        self.fingerprint_db_path = self.archive_dir / "fingerprints.db"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._processed = self._load_state()
        self._init_fingerprint_db()

    def discover_pending(self) -> list[PendingInquiryFile]:
        pending: list[PendingInquiryFile] = []
        for path in sorted(self.inbox_dir.glob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".csv", ".xlsx", ".xlsm"}:
                continue
            fingerprint = self._fingerprint(path)
            if self._is_already_processed(path, fingerprint):
                continue
            pending.append(PendingInquiryFile(path=path, fingerprint=fingerprint))
        return pending

    def mark_processed(self, pending_file: PendingInquiryFile) -> None:
        self._processed[str(pending_file.path)] = pending_file.fingerprint
        self._save_state()
        self._save_fingerprint(pending_file.path, pending_file.fingerprint)

    def load_latest_report(self) -> Dict[str, Any]:
        report_path = self.archive_dir / "latest_real_data_report.json"
        if not report_path.exists():
            return {}
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_latest_report(self, payload: Dict[str, Any]) -> None:
        target = self.archive_dir / "latest_real_data_report.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_state(self) -> Dict[str, str]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        processed = payload.get("processed", {})
        return processed if isinstance(processed, dict) else {}

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps({"processed": self._processed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _init_fingerprint_db(self) -> None:
        with sqlite3.connect(self.fingerprint_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_files (
                    file_path TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _is_already_processed(self, path: Path, fingerprint: str) -> bool:
        legacy_match = self._processed.get(str(path)) == fingerprint
        if legacy_match:
            return True
        try:
            with sqlite3.connect(self.fingerprint_db_path) as conn:
                row = conn.execute(
                    "SELECT fingerprint FROM processed_files WHERE file_path = ?",
                    (str(path),),
                ).fetchone()
        except sqlite3.Error:
            return legacy_match
        return bool(row and row[0] == fingerprint)

    def _save_fingerprint(self, path: Path, fingerprint: str) -> None:
        try:
            with sqlite3.connect(self.fingerprint_db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO processed_files (file_path, fingerprint, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        fingerprint = excluded.fingerprint,
                        updated_at = excluded.updated_at
                    """,
                    (str(path), fingerprint, datetime.now().isoformat()),
                )
                conn.commit()
        except sqlite3.Error:
            return

    def get_idempotency_status(self) -> Dict[str, Any]:
        db_exists = self.fingerprint_db_path.exists()
        tracked_count = 0
        if db_exists:
            try:
                with sqlite3.connect(self.fingerprint_db_path) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM processed_files"
                    ).fetchone()
                    tracked_count = int(row[0]) if row else 0
            except sqlite3.Error:
                tracked_count = 0
        return {
            "backend": "sqlite+json_compat",
            "fingerprint_db_path": str(self.fingerprint_db_path),
            "fingerprint_db_exists": db_exists,
            "tracked_files": tracked_count,
            "legacy_state_path": str(self.state_path),
            "legacy_entries": len(self._processed),
        }

    @staticmethod
    def _fingerprint(path: Path) -> str:
        stat = path.stat()
        return f"{int(stat.st_mtime_ns)}:{stat.st_size}"


class ProductionRuntime:
    """生产态运行时封装。"""

    def __init__(self, heartbeat_seconds: int = HEARTBEAT_SECONDS):
        self.repo_root = Path(settings.BASE_DIR)
        self.heartbeat_seconds = heartbeat_seconds
        self.brain: Optional[CentralBrain] = None
        self.life_loop: Optional[AutonomousLifeLoop] = None
        self.running = True
        self.started_at: Optional[datetime] = None
        self.start_resources: Dict[str, Any] = {}
        self.shutdown_resources: Dict[str, Any] = {}
        self.report_path = REPORTS_DIR / "overnight_cruise_report.md"
        self.heartbeat_path = HEARTBEAT_FILE
        self.recovery_probe_path = RECOVERY_PROBE_FILE
        self.pause_probe_path = PAUSE_PROBE_FILE
        self.real_data_inbox = Path(settings.BASE_DIR) / "data" / "inquiries"
        self.real_data_archive = self.real_data_inbox / "processed"
        self.real_data_adapter = TradeInquiryAdapter()
        self.real_data_state_path = self.real_data_archive / "state.json"
        self.real_data_watcher = RealDataWatcher(
            inbox_dir=self.real_data_inbox,
            archive_dir=self.real_data_archive,
            state_path=self.real_data_state_path,
        )
        self.stress_sample_path = (
            Path(settings.BASE_DIR)
            / "data"
            / "samples"
            / "foreign_trade_risk_stress_samples.json"
        )
        self.gate_report_json_path = (
            REPORTS_DIR / "foreign_trade_warning_gate_daily_report.json"
        )
        self.gate_report_md_path = (
            REPORTS_DIR / "foreign_trade_warning_gate_daily_report.md"
        )
        self.gate_report_schema_path = (
            Path(settings.BASE_DIR)
            / "data"
            / "schemas"
            / "foreign_trade_warning_report.schema.json"
        )
        self.latest_gate_report: Dict[str, Any] = self._load_existing_gate_report()
        self.latest_real_data_report: Dict[str, Any] = (
            self.real_data_watcher.load_latest_report()
        )
        self.real_data_watch_task: Optional[asyncio.Task] = None
        self.report_pattern_state_path = (
            self.real_data_archive / "distilled_report_patterns.json"
        )
        self.latest_pattern_distillation_path = (
            self.real_data_archive / "latest_pattern_distillation.json"
        )
        self.pattern_distillation_log_path = (
            self.real_data_archive / "pattern_distillation_log.jsonl"
        )
        self.pattern_distillation_report_path = (
            REPORTS_DIR / "pattern_distillation_daily_report.json"
        )
        self.pattern_distillation_report_md_path = (
            REPORTS_DIR / "pattern_distillation_daily_report.md"
        )
        self.pattern_distillation_batch_report_path = (
            REPORTS_DIR / "pattern_distillation_batch_report.json"
        )
        self.pattern_distillation_batch_report_md_path = (
            REPORTS_DIR / "pattern_distillation_batch_report.md"
        )
        self.pattern_cluster_state_path = (
            self.real_data_archive / "pattern_cluster_state.json"
        )
        self.latest_pattern_promotion_path = (
            self.real_data_archive / "latest_pattern_promotion.json"
        )
        self.pattern_anchor_review_path = REPORTS_DIR / "pattern_anchor_review.json"
        self.pattern_anchor_review_md_path = REPORTS_DIR / "pattern_anchor_review.md"
        self.pattern_anchor_registry_path = REPORTS_DIR / "pattern_anchor_registry.json"
        self.pattern_anchor_registry_md_path = (
            REPORTS_DIR / "pattern_anchor_registry.md"
        )
        self.latest_pattern_distillation: Dict[str, Any] = (
            self._load_latest_pattern_distillation()
        )
        self.latest_pattern_promotion: Dict[str, Any] = (
            self._load_latest_pattern_promotion()
        )
        self.manifesto_draft_snapshot_path = (
            REPORTS_DIR / "decision_manifesto_draft_snapshot.json"
        )
        self.manifesto_draft_snapshot_md_path = (
            REPORTS_DIR / "decision_manifesto_draft_snapshot.md"
        )
        self.manifesto_draft_log_path = (
            REPORTS_DIR / "decision_manifesto_draft_log.jsonl"
        )
        self.manifesto_draft_compare_path = (
            REPORTS_DIR / "decision_manifesto_draft_compare.json"
        )
        self.manifesto_draft_compare_md_path = (
            REPORTS_DIR / "decision_manifesto_draft_compare.md"
        )
        self.manifesto_review_path = REPORTS_DIR / "decision_manifesto_review.json"
        self.manifesto_review_md_path = REPORTS_DIR / "decision_manifesto_review.md"
        self.manifesto_approval_gate_path = (
            REPORTS_DIR / "decision_manifesto_approval_gate.json"
        )
        self.manifesto_approval_gate_md_path = (
            REPORTS_DIR / "decision_manifesto_approval_gate.md"
        )
        self.manifesto_rewrite_candidate_path = (
            REPORTS_DIR / "decision_manifesto_rewrite_candidate.json"
        )
        self.manifesto_rewrite_candidate_md_path = (
            REPORTS_DIR / "decision_manifesto_rewrite_candidate.md"
        )
        self.manifesto_rewrite_simulation_path = (
            REPORTS_DIR / "decision_manifesto_rewrite_simulation.json"
        )
        self.manifesto_rewrite_simulation_md_path = (
            REPORTS_DIR / "decision_manifesto_rewrite_simulation.md"
        )
        self.manifesto_controlled_rewrite_path = (
            REPORTS_DIR / "decision_manifesto_controlled_rewrite.json"
        )
        self.manifesto_controlled_rewrite_md_path = (
            REPORTS_DIR / "decision_manifesto_controlled_rewrite.md"
        )
        self.manifesto_writeback_gate_path = (
            REPORTS_DIR / "decision_manifesto_formal_writeback_gate.json"
        )
        self.manifesto_writeback_gate_md_path = (
            REPORTS_DIR / "decision_manifesto_formal_writeback_gate.md"
        )
        self.manifesto_writeback_authorization_path = (
            REPORTS_DIR / "decision_manifesto_formal_writeback_authorization.json"
        )
        self.manifesto_writeback_authorization_md_path = (
            REPORTS_DIR / "decision_manifesto_formal_writeback_authorization.md"
        )
        self.manifesto_writeback_policy_path = (
            REPORTS_DIR / "decision_manifesto_writeback_policy.json"
        )
        self.manifesto_writeback_policy_md_path = (
            REPORTS_DIR / "decision_manifesto_writeback_policy.md"
        )
        self.runtime_timeline_path = REPORTS_DIR / "runtime_timeline.json"
        self.runtime_status_snapshot_path = REPORTS_DIR / "runtime_status_snapshot.json"
        self.external_risk_hit_event_path = REPORTS_DIR / "external_risk_hit_event.json"
        self.runtime_external_risk_audit_path = (
            REPORTS_DIR / "runtime_external_risk_audit.json"
        )
        self.external_risk_cache_path = (
            Path(settings.BASE_DIR)
            / "data"
            / "cache"
            / "external_risk_runtime_cache.json"
        )
        self.runtime_health_event_path = REPORTS_DIR / "runtime_health_event.json"
        self.runtime_health_log_path = REPORTS_DIR / "runtime_health_log.jsonl"
        self.runtime_governance_event_path = (
            REPORTS_DIR / "runtime_governance_event.json"
        )
        self.runtime_governance_log_path = REPORTS_DIR / "runtime_governance_log.jsonl"
        self.report_archive_root = REPORTS_DIR / "_archive"
        self.processed_retention_root = self.real_data_archive / "_retained"
        self.latest_manifesto_draft: Dict[str, Any] = (
            self._load_manifesto_draft_snapshot()
        )
        self.latest_heartbeat_outputs: Dict[str, Any] = {}
        self.latest_map_refresh: Dict[str, Any] = {}
        self.steady_running_streak = 0
        self.stable_window_started_at: Optional[datetime] = None
        self.lead_capture_path = self.real_data_inbox / "trade_leads.csv"
        self.shutdown_reason = "manual"
        self._finalized = False
        self.current_phase = "idle"
        self.stable_phase = "idle"
        self.last_transition_phase = "idle"
        self.last_recovery: Dict[str, Any] = {}
        self.last_recovery_actions: list[Dict[str, Any]] = []
        self.pause_reason: Optional[str] = None
        self.pattern_batch_every_heartbeats = 3
        self.shadow_sandbox: Optional[ShadowSandbox] = None
        self.recovery_attempts = 0
        self.recovery_cooldown_seconds = 120
        self.recovery_max_attempts = 3
        self.last_recovery_started_at: Optional[datetime] = None
        self.last_recovery_backoff_seconds = 0
        self.runtime_health_window: list[Dict[str, Any]] = []
        self.pause_state: Dict[str, Any] = {"status": "running", "updated_at": None}
        self.pattern_batch_task: Optional[asyncio.Task] = None
        self.latest_pattern_batch_status: Dict[str, Any] = {
            "status": "idle",
            "running": False,
            "last_heartbeat": 0,
        }
        self.heartbeat_action_task: Optional[asyncio.Task] = None
        self.latest_heartbeat_action_status: Dict[str, Any] = {
            "status": "idle",
            "running": False,
            "last_heartbeat": 0,
        }
        self.maintenance_task: Optional[asyncio.Task] = None
        self.latest_maintenance_status: Dict[str, Any] = {
            "status": "idle",
            "running": False,
            "last_heartbeat": 0,
        }
        self.shadow_sandbox_health: Dict[str, Any] = {
            "available": False,
            "backend": None,
            "image": None,
        }
        self._hydrate_latest_map_refresh()

    async def initialize(self) -> None:
        self._transition_phase("starting", result={"subphase": "bootstrap"})
        self.started_at = datetime.now()
        self.start_resources = capture_resource_snapshot(self.repo_root)
        log.info("🧭 生产巡航初始化开始 | stage=brain_boot")
        self.brain = CentralBrain()
        log.info("🧭 生产巡航初始化继续 | stage=autonomous_loop_boot")
        self.life_loop = AutonomousLifeLoop(
            self.brain,
            repo_root=str(self.repo_root),
            heartbeat_seconds=self.heartbeat_seconds,
            shadow_commit_enabled=False,
        )
        self._register_cruise_schedules()
        try:
            self.shadow_sandbox = ShadowSandbox()
            self.shadow_sandbox_health = self.shadow_sandbox.describe()
            log.info(
                "🧪 生产巡航初始化继续 | stage=shadow_sandbox_boot | backend={} | image={}",
                self.shadow_sandbox_health.get("backend"),
                self.shadow_sandbox_health.get("image"),
            )
        except Exception as exc:
            self.shadow_sandbox = None
            self.shadow_sandbox_health = {
                "available": False,
                "backend": "docker_cli",
                "image": "python:3.10-slim",
                "error": str(exc),
            }
            log.warning("⚠️ 影子沙盒初始化失败 | error={}", exc)
        log.info("🧭 生产巡航初始化继续 | stage=cultural_foundation")
        await asyncio.wait_for(
            self.life_loop.ensure_cultural_foundation(export_map=True),
            timeout=max(self.heartbeat_seconds * 2, 90),
        )
        log.info("🧭 生产巡航初始化继续 | stage=heartbeat_registration")
        self._ensure_seed_trade_leads_csv()
        self.brain.chronos.start_all_schedules()
        log.info(
            "🚀 生产态点火完成 | heartbeat_seconds={} | repo_root={}",
            self.heartbeat_seconds,
            self.repo_root,
        )
        log.info(
            "🧱 启动资源基线 | memory_percent={} | disk_percent={} | disk_free_gb={}",
            self.start_resources.get("memory_percent"),
            self.start_resources.get("disk_percent"),
            self.start_resources.get("disk_free_gb"),
        )
        self._transition_phase("running", result={"subphase": "initialized"})

    async def run_forever(self) -> None:
        if self.life_loop is None:
            raise RuntimeError("生产态自主巡航尚未初始化")

        while self.running:
            if self._should_finish_by_schedule():
                await self._complete_planned_cruise()
                break
            try:
                self._harvest_pattern_batch_task()
                self._harvest_heartbeat_action_task()
                self._harvest_maintenance_task()
                if self._check_pause_probe():
                    next_tick_at = datetime.now().timestamp() + self.heartbeat_seconds
                    self._transition_phase(
                        "paused",
                        result={
                            "subphase": "pause_wait",
                            "heartbeat": self.life_loop.heartbeat_count,
                            "next_tick_epoch": next_tick_at,
                        },
                    )
                    await asyncio.sleep(min(self.heartbeat_seconds, 30))
                    continue
                self._check_recovery_probe()
                if (
                    self.life_loop
                    and self.life_loop.heartbeat_count > 0
                    and self.life_loop.heartbeat_count
                    % self.pattern_batch_every_heartbeats
                    == 0
                ):
                    self._schedule_pattern_batch(self.life_loop.heartbeat_count)
                    self.latest_heartbeat_outputs = {
                        "pattern_batch_triggered": True,
                        "pattern_batch_status": self.latest_pattern_batch_status,
                        "pattern_distillation_status": self.latest_pattern_distillation.get(
                            "status"
                        ),
                        "pattern_promotion_status": self.latest_pattern_promotion.get(
                            "status"
                        ),
                        "pattern_promotion_generated": len(
                            self.latest_pattern_promotion.get("generated", [])
                        ),
                        "external_risk_hits": self._count_external_risk_hits(),
                        "external_risk_hit_details": self._record_external_risk_hit_event(),
                        "manifesto_candidate_count": len(
                            self.latest_manifesto_draft.get("candidates", [])
                        ),
                    }
                else:
                    self.latest_heartbeat_outputs = {
                        "pattern_batch_triggered": False,
                        "pattern_batch_status": self.latest_pattern_batch_status,
                        "pattern_distillation_status": self.latest_pattern_distillation.get(
                            "status"
                        ),
                        "pattern_promotion_status": self.latest_pattern_promotion.get(
                            "status"
                        ),
                        "pattern_promotion_generated": len(
                            self.latest_pattern_promotion.get("generated", [])
                        ),
                        "external_risk_hits": self._count_external_risk_hits(),
                        "external_risk_hit_details": self._record_external_risk_hit_event(),
                        "manifesto_candidate_count": len(
                            self.latest_manifesto_draft.get("candidates", [])
                        ),
                    }
                self._transition_phase(
                    "running",
                    result={
                        "subphase": "heartbeat_start",
                        "heartbeat": self.life_loop.heartbeat_count + 1,
                    },
                )
                result = await asyncio.wait_for(
                    self.life_loop.run_heartbeat(defer_execution=True),
                    timeout=max(self.heartbeat_seconds * 2, 90),
                )
                self._transition_phase("running", result=result)
                self.steady_running_streak += 1
                if self.stable_window_started_at is None:
                    self.stable_window_started_at = datetime.now()
                result["real_data"] = self._schedule_real_data_watch(
                    trigger=f"heartbeat_{result.get('heartbeat')}"
                )
                result["discovery_scheduled"] = self._should_run_discovery(result)
                result["maintenance"] = self._schedule_post_heartbeat_maintenance(
                    result
                )
                self._report_heartbeat(result)
                result["heartbeat_action"] = self._schedule_heartbeat_action(result)
                self._transition_phase("running", result=result)
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                self.running = False
                log.warning("⏹️ 接收到键盘中断，准备关闭生产态自主巡航")
            except Exception as exc:
                recovery_started_at = datetime.now().isoformat()
                self.last_recovery = {
                    "status": "triggered",
                    "phase": "heartbeat",
                    "error": str(exc),
                    "at": recovery_started_at,
                }
                self._transition_phase(
                    "recovering",
                    result={
                        "subphase": "heartbeat_exception",
                        "error": str(exc),
                        "heartbeat": self.life_loop.heartbeat_count,
                    },
                )
                log.exception("❌ 生产态心跳异常 | error={}", exc)
                recovery_result = self._perform_recovery_actions(exc)
                self.last_recovery_actions = recovery_result.get("actions", [])
                self.last_recovery = {
                    "status": recovery_result.get("status", "recovery_failed"),
                    "phase": "heartbeat",
                    "error": str(exc),
                    "at": recovery_started_at,
                    "finished_at": datetime.now().isoformat(),
                    "summary": recovery_result.get("summary", ""),
                    "recovery_tier": recovery_result.get("recovery_tier"),
                    "degrade_reason": recovery_result.get("degrade_reason"),
                    "failure_class": recovery_result.get("failure_class"),
                }
                self._transition_phase(
                    recovery_result.get("phase", "recovery_failed"),
                    result={
                        "subphase": "recovery_finished",
                        "heartbeat": self.life_loop.heartbeat_count,
                        "recovery_status": recovery_result.get("status"),
                    },
                )
                self._refresh_evolution_map(
                    reason=f"recovery:{recovery_result.get('status')}"
                )
                self.steady_running_streak = 0
                self.stable_window_started_at = None
                await asyncio.sleep(min(self.heartbeat_seconds, 30))
                continue

            if self._should_finish_by_schedule():
                await self._complete_planned_cruise()
                break
            next_tick_at = datetime.now().timestamp() + self.heartbeat_seconds
            log.info(
                "🌙 进入心跳休眠 | sleep_seconds={} | next_tick_epoch={:.0f}",
                self.heartbeat_seconds,
                next_tick_at,
            )
            self._transition_phase(
                "running",
                result={
                    "subphase": "sleeping",
                    "heartbeat": self.life_loop.heartbeat_count,
                    "next_tick_epoch": next_tick_at,
                },
            )
            await asyncio.sleep(self.heartbeat_seconds)

    async def shutdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self.running = False
        if (
            self.real_data_watch_task is not None
            and not self.real_data_watch_task.done()
        ):
            await asyncio.wait([self.real_data_watch_task], timeout=5)
        if self.pattern_batch_task is not None and not self.pattern_batch_task.done():
            await asyncio.wait([self.pattern_batch_task], timeout=5)
        if (
            self.heartbeat_action_task is not None
            and not self.heartbeat_action_task.done()
        ):
            await asyncio.wait([self.heartbeat_action_task], timeout=5)
        if self.maintenance_task is not None and not self.maintenance_task.done():
            await asyncio.wait([self.maintenance_task], timeout=5)
        self.shutdown_resources = capture_resource_snapshot(self.repo_root)
        if self.brain is not None:
            self.brain.chronos.shutdown()
        self._write_energy_and_growth_report()
        self._transition_phase("stopped")
        close_action_journal()
        if self.shutdown_reason == "scheduled":
            log.info("✅ 巡航任务已按计划完成，进程退出")
        else:
            log.info("🛑 生产态自主巡航已关闭")

    def _report_heartbeat(self, result: Dict[str, Any]) -> None:
        sync = result.get("sync") or {}
        discovery_triggered = bool(result.get("discovery", {}).get("triggered"))
        log.info(
            "💓 生产心跳完成 | heartbeat={} | domain={} | allowed={} | template={} | sync={} | discovery={}",
            result.get("heartbeat"),
            result.get("domain"),
            result.get("allowed"),
            result.get("template"),
            bool(sync),
            discovery_triggered,
        )

    async def _run_sync_step_async(
        self,
        step_name: str,
        func,
        *,
        timeout_seconds: int,
    ):
        log.debug("🧱 开始同步步骤 | step={}", step_name)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(func),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning(
                "⏱️ 同步步骤超时，已跳过 | step={} | timeout_seconds={}",
                step_name,
                timeout_seconds,
            )
            return {
                "status": "timeout",
                "step": step_name,
                "timeout_seconds": timeout_seconds,
            }
        log.debug("✅ 同步步骤完成 | step={}", step_name)
        return result

    def _schedule_post_heartbeat_maintenance(
        self, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        heartbeat = int(result.get("heartbeat") or 0)
        if self.maintenance_task is not None and not self.maintenance_task.done():
            self.latest_maintenance_status = {
                "status": "running",
                "running": True,
                "last_heartbeat": heartbeat,
                "started_at": self.latest_maintenance_status.get("started_at"),
            }
            log.warning(
                "⏳ 后置维护任务仍在运行，跳过重复调度 | heartbeat={}", heartbeat
            )
            return self.latest_maintenance_status

        self.latest_maintenance_status = {
            "status": "running",
            "running": True,
            "last_heartbeat": heartbeat,
            "started_at": datetime.now().isoformat(),
        }
        self.maintenance_task = asyncio.create_task(
            self._run_post_heartbeat_maintenance_async(heartbeat, dict(result))
        )
        return self.latest_maintenance_status

    def _schedule_heartbeat_action(self, result: Dict[str, Any]) -> Dict[str, Any]:
        heartbeat = int(result.get("heartbeat") or 0)
        if not result.get("execution_deferred"):
            return {
                "status": "not_required",
                "running": False,
                "last_heartbeat": heartbeat,
            }
        if (
            self.heartbeat_action_task is not None
            and not self.heartbeat_action_task.done()
        ):
            self.latest_heartbeat_action_status = {
                "status": "running",
                "running": True,
                "last_heartbeat": heartbeat,
                "dropped_heartbeat": heartbeat,
            }
            log.warning(
                "⏳ 心跳动作任务仍在运行，跳过重复调度 | heartbeat={}", heartbeat
            )
            return self.latest_heartbeat_action_status
        if self.life_loop is None:
            return {
                "status": "skipped",
                "running": False,
                "last_heartbeat": heartbeat,
                "reason": "life_loop_not_ready",
            }

        self.latest_heartbeat_action_status = {
            "status": "running",
            "running": True,
            "last_heartbeat": heartbeat,
            "started_at": datetime.now().isoformat(),
            "kind": result.get("deferred_action"),
        }
        self.heartbeat_action_task = asyncio.create_task(
            self._run_heartbeat_action_async(heartbeat, dict(result))
        )
        return self.latest_heartbeat_action_status

    async def _run_heartbeat_action_async(
        self, heartbeat: int, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        timeout_seconds = max(self.heartbeat_seconds * 4, 180)
        if self.life_loop is None:
            return {
                "status": "skipped",
                "running": False,
                "last_heartbeat": heartbeat,
                "reason": "life_loop_not_ready",
            }
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self.life_loop.execute_deferred_heartbeat_action, result
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.latest_heartbeat_action_status = {
                "status": "timeout",
                "running": False,
                "last_heartbeat": heartbeat,
                "finished_at": datetime.now().isoformat(),
                "timeout_seconds": timeout_seconds,
            }
            log.warning(
                "⏱️ 心跳动作任务超时 | heartbeat={} | timeout_seconds={}",
                heartbeat,
                timeout_seconds,
            )
            return self.latest_heartbeat_action_status
        except Exception as exc:
            self.latest_heartbeat_action_status = {
                "status": "failed",
                "running": False,
                "last_heartbeat": heartbeat,
                "finished_at": datetime.now().isoformat(),
                "error": str(exc),
            }
            log.exception(
                "❌ 心跳动作任务失败 | heartbeat={} | error={}", heartbeat, exc
            )
            return self.latest_heartbeat_action_status

        self.latest_heartbeat_action_status = {
            "status": payload.get("status", "completed"),
            "running": False,
            "last_heartbeat": heartbeat,
            "finished_at": datetime.now().isoformat(),
            "kind": payload.get("kind"),
        }
        log.info(
            "✅ 心跳动作任务完成 | heartbeat={} | kind={} | status={}",
            heartbeat,
            payload.get("kind"),
            payload.get("status", "completed"),
        )
        return payload

    def _harvest_heartbeat_action_task(self) -> None:
        if self.heartbeat_action_task is None or not self.heartbeat_action_task.done():
            return
        try:
            self.heartbeat_action_task.result()
        except Exception:
            pass
        finally:
            self.heartbeat_action_task = None

    async def _run_post_heartbeat_maintenance_async(
        self, heartbeat: int, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        timeout_seconds = max(self.heartbeat_seconds * 2, 90)
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_post_heartbeat_maintenance_sync,
                    heartbeat,
                    result,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.latest_maintenance_status = {
                "status": "timeout",
                "running": False,
                "last_heartbeat": heartbeat,
                "finished_at": datetime.now().isoformat(),
                "timeout_seconds": timeout_seconds,
            }
            log.warning(
                "⏱️ 后置维护任务超时 | heartbeat={} | timeout_seconds={}",
                heartbeat,
                timeout_seconds,
            )
            return self.latest_maintenance_status
        except Exception as exc:
            self.latest_maintenance_status = {
                "status": "failed",
                "running": False,
                "last_heartbeat": heartbeat,
                "finished_at": datetime.now().isoformat(),
                "error": str(exc),
            }
            log.exception(
                "❌ 后置维护任务失败 | heartbeat={} | error={}", heartbeat, exc
            )
            raise
        self.latest_maintenance_status = {
            "status": payload.get("status", "completed"),
            "running": False,
            "last_heartbeat": heartbeat,
            "finished_at": datetime.now().isoformat(),
            "map_refresh_status": payload.get("map_refresh_status"),
            "runtime_governance_status": payload.get("runtime_governance_status"),
            "discovery_status": payload.get("discovery_status"),
        }
        log.info(
            "✅ 后置维护任务完成 | heartbeat={} | status={}",
            heartbeat,
            payload.get("status", "completed"),
        )
        return payload

    def _run_post_heartbeat_maintenance_sync(
        self, heartbeat: int, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        map_refresh = self._refresh_evolution_map(reason=f"heartbeat_{heartbeat}")
        discovery_status = "skipped"
        if result.get("discovery_scheduled") and self.life_loop is not None:
            discovery_result = self.life_loop._run_discovery_phase_sync()
            result["discovery"] = discovery_result
            result["gate_report"] = self._run_gate_daily_report(
                trigger=f"heartbeat_{heartbeat}"
            )
            discovery_status = (
                "completed" if discovery_result.get("triggered") else "skipped"
            )
        self._update_runtime_health(result)
        runtime_governance = self._apply_runtime_governance()
        return {
            "status": "completed",
            "heartbeat": heartbeat,
            "map_refresh_status": map_refresh.get("status", "generated"),
            "runtime_governance_status": runtime_governance.get("status", "generated"),
            "discovery_status": discovery_status,
        }

    def _harvest_maintenance_task(self) -> None:
        if self.maintenance_task is None or not self.maintenance_task.done():
            return
        try:
            self.maintenance_task.result()
        except Exception:
            pass
        finally:
            self.maintenance_task = None

    def _schedule_pattern_batch(self, heartbeat: int) -> None:
        if self.pattern_batch_task is not None and not self.pattern_batch_task.done():
            self.latest_pattern_batch_status = {
                "status": "running",
                "running": True,
                "last_heartbeat": heartbeat,
                "started_at": self.latest_pattern_batch_status.get("started_at"),
            }
            log.warning("⏳ 模式批处理仍在运行，跳过重复触发 | heartbeat={}", heartbeat)
            return

        self.latest_pattern_batch_status = {
            "status": "running",
            "running": True,
            "last_heartbeat": heartbeat,
            "started_at": datetime.now().isoformat(),
        }
        log.info("🧩 已异步调度模式批处理 | heartbeat={}", heartbeat)
        self.pattern_batch_task = asyncio.create_task(
            self._run_pattern_batch_async(heartbeat)
        )

    async def _run_pattern_batch_async(self, heartbeat: int) -> Dict[str, Any]:
        timeout_seconds = max(self.heartbeat_seconds * 4, 180)
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(self._run_pattern_batch_sync, heartbeat),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.latest_pattern_batch_status = {
                "status": "timeout",
                "running": False,
                "last_heartbeat": heartbeat,
                "finished_at": datetime.now().isoformat(),
                "timeout_seconds": timeout_seconds,
            }
            log.warning(
                "⏱️ 模式批处理超时 | heartbeat={} | timeout_seconds={}",
                heartbeat,
                timeout_seconds,
            )
            return self.latest_pattern_batch_status
        except Exception as exc:
            self.latest_pattern_batch_status = {
                "status": "failed",
                "running": False,
                "last_heartbeat": heartbeat,
                "finished_at": datetime.now().isoformat(),
                "error": str(exc),
            }
            log.exception("❌ 模式批处理失败 | heartbeat={} | error={}", heartbeat, exc)
            raise
        self.latest_pattern_batch_status = {
            "status": payload.get("status", "completed"),
            "running": False,
            "last_heartbeat": heartbeat,
            "finished_at": datetime.now().isoformat(),
            "pattern_distillation_status": payload.get("pattern_distillation_status"),
            "pattern_promotion_status": payload.get("pattern_promotion_status"),
            "manifesto_status": payload.get("manifesto_status"),
        }
        log.info(
            "✅ 模式批处理完成 | heartbeat={} | status={}",
            heartbeat,
            payload.get("status", "completed"),
        )
        return payload

    def _run_pattern_batch_sync(self, heartbeat: int) -> Dict[str, Any]:
        self._sync_external_risk_runtime_cache()
        self._maybe_distill_pending_trade_reports()
        self.latest_pattern_promotion = self._promote_pattern_clusters_to_wisdom()
        self.latest_manifesto_draft = self._generate_manifesto_draft_snapshot()
        return {
            "status": "completed",
            "heartbeat": heartbeat,
            "pattern_distillation_status": self.latest_pattern_distillation.get(
                "status"
            ),
            "pattern_promotion_status": self.latest_pattern_promotion.get("status"),
            "manifesto_status": self.latest_manifesto_draft.get("status"),
        }

    def _harvest_pattern_batch_task(self) -> None:
        if self.pattern_batch_task is None or not self.pattern_batch_task.done():
            return
        try:
            self.pattern_batch_task.result()
        except Exception:
            pass
        finally:
            self.pattern_batch_task = None

    @staticmethod
    def _should_run_discovery(result: Dict[str, Any]) -> bool:
        heartbeat = result.get("heartbeat")
        if heartbeat is None:
            return False
        try:
            return int(heartbeat) > 0 and int(heartbeat) % 5 == 0
        except (TypeError, ValueError):
            return False

    def _write_energy_and_growth_report(self) -> None:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        final_map = read_map_baseline(self.repo_root / "evolution_map.json")
        stats = (
            self.life_loop.get_cruise_statistics()
            if self.life_loop is not None
            else {
                "heartbeats": 0,
                "intent_counters": {},
                "outcome_counters": {},
                "recent_logs": [],
            }
        )
        started_at = self.started_at or datetime.now()
        ended_at = datetime.now()
        duration_hours = round((ended_at - started_at).total_seconds() / 3600.0, 3)
        content = [
            "# ABU 能耗与认知增长报告",
            "",
            f"- 启动时间：{started_at.isoformat()}",
            f"- 结束时间：{ended_at.isoformat()}",
            f"- 巡航时长（小时）：{duration_hours}",
            f"- 心跳间隔（秒）：{self.heartbeat_seconds}",
            f"- 心跳次数：{stats.get('heartbeats', 0)}",
            f"- 收尾原因：{self.shutdown_reason}",
            "",
            "## 资源水位",
            "",
            f"- 启动内存占用：{self.start_resources.get('memory_percent')}",
            f"- 启动磁盘占用：{self.start_resources.get('disk_percent')}",
            f"- 启动剩余磁盘（GB）：{self.start_resources.get('disk_free_gb')}",
            f"- 收尾内存占用：{self.shutdown_resources.get('memory_percent')}",
            f"- 收尾磁盘占用：{self.shutdown_resources.get('disk_percent')}",
            f"- 收尾剩余磁盘（GB）：{self.shutdown_resources.get('disk_free_gb')}",
            "",
            "## 认知增长",
            "",
            f"- 最新星图时间：{final_map.get('generated_at')}",
            f"- wisdom_nodes 数量：{final_map.get('wisdom_nodes')}",
            f"- 意图分布：{json.dumps(stats.get('intent_counters', {}), ensure_ascii=False)}",
            f"- 结果分布：{json.dumps(stats.get('outcome_counters', {}), ensure_ascii=False)}",
            "",
            "## 门控样本日报",
            "",
            f"- 最近状态：{self.latest_gate_report.get('status', 'not_generated')}",
            f"- 最近触发：{self.latest_gate_report.get('trigger', 'none')}",
            f"- 报告编号：{self.latest_gate_report.get('report_id')}",
            f"- 样本数量：{self.latest_gate_report.get('sample_count')}",
            f"- 门控样本：{self.latest_gate_report.get('gated_count')}",
            f"- Markdown：{self.latest_gate_report.get('markdown_path')}",
            "",
            "## 真实询盘接入",
            "",
            f"- 最近状态：{self.latest_real_data_report.get('status', 'idle')}",
            f"- 最近触发：{self.latest_real_data_report.get('trigger', 'none')}",
            f"- 最近输入：{self.latest_real_data_report.get('input_path')}",
            f"- 最近报告：{self.latest_real_data_report.get('report_id')}",
            f"- 最近门控数：{self.latest_real_data_report.get('gated_count')}",
            f"- 最近 Markdown：{self.latest_real_data_report.get('markdown_path')}",
            "",
            "## 最近巡航记录",
            "",
        ]
        for item in stats.get("recent_logs", []):
            content.append(
                f"- heartbeat={item.get('heartbeat')} | domain={item.get('domain')} | allowed={item.get('allowed')} | template={item.get('template')}"
            )
        self.report_path.write_text("\n".join(content) + "\n", encoding="utf-8")
        log.info("📘 已写入能耗与认知增长报告 | path={}", self.report_path)

    def _write_heartbeat_sentinel(
        self,
        phase: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._write_runtime_snapshot(self.heartbeat_path, phase=phase, result=result)
        self._write_runtime_status_snapshot(phase=phase, result=result)

    def _write_runtime_snapshot(
        self,
        path: Path,
        *,
        phase: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = self._build_heartbeat_payload(phase=phase, result=result)
        path.write_text(
            json.dumps(self._to_json_safe(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_runtime_status_snapshot(
        self,
        *,
        phase: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        heartbeat_payload = self._build_heartbeat_payload(phase=phase, result=result)
        snapshot = {
            "generated_at": datetime.now().isoformat(),
            "runtime_availability": heartbeat_payload.get("phase"),
            "heartbeat_timestamp": heartbeat_payload.get("timestamp"),
            "heartbeat_count": heartbeat_payload.get("heartbeat_count"),
            "heartbeat_seconds": heartbeat_payload.get("heartbeat_seconds"),
            "pause_reason": heartbeat_payload.get("lifecycle", {}).get("pause_reason"),
            "shutdown_reason": heartbeat_payload.get("lifecycle", {}).get(
                "shutdown_reason"
            ),
            "map_generated_at": self.latest_map_refresh.get("generated_at"),
            "map_refresh_status": self.latest_map_refresh.get("status"),
            "maintenance_status": self.latest_maintenance_status,
            "pattern_batch_status": self.latest_pattern_batch_status,
            "heartbeat_action_status": self.latest_heartbeat_action_status,
            "latest_gate_report": {
                "generated_at": self.latest_gate_report.get("generated_at"),
                "report_id": self.latest_gate_report.get("report_id"),
                "status": self.latest_gate_report.get("status"),
            },
            "latest_real_data_report": {
                "generated_at": self.latest_real_data_report.get("generated_at"),
                "report_id": self.latest_real_data_report.get("report_id"),
                "status": self.latest_real_data_report.get("status"),
            },
        }
        self.runtime_status_snapshot_path.write_text(
            json.dumps(self._to_json_safe(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _to_json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): self._to_json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._to_json_safe(item) for item in value]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return self._to_json_safe(model_dump(mode="json"))
            except TypeError:
                return self._to_json_safe(model_dump())
        if hasattr(value, "__dict__"):
            return self._to_json_safe(vars(value))
        return str(value)

    def _register_cruise_schedules(self) -> None:
        if self.brain is None:
            return
        self.brain.chronos.register_cruise_interval_task(
            func=lambda: None,
            interval_seconds=self.heartbeat_seconds,
            job_id="evolution_map_refresh",
            purpose="map_refresh",
        )
        self.brain.chronos.register_cruise_interval_task(
            func=lambda: None,
            interval_seconds=self.heartbeat_seconds
            * self.pattern_batch_every_heartbeats,
            job_id="pattern_distillation_batch",
            purpose="distillation",
        )
        self.brain.chronos.register_cruise_interval_task(
            func=lambda: None,
            interval_seconds=self.heartbeat_seconds,
            job_id="external_risk_sync",
            purpose="external_risk",
        )

    def _transition_phase(
        self, phase: str, result: Optional[Dict[str, Any]] = None
    ) -> None:
        self.last_transition_phase = phase
        self.current_phase = phase
        self.stable_phase = "running" if phase in {"starting", "recovered"} else phase
        self._write_heartbeat_sentinel(phase=phase, result=result)
        self._write_runtime_timeline()

    def _build_heartbeat_payload(
        self,
        phase: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        visible_phase = "running" if phase in {"starting", "recovered"} else phase
        process_tree = collect_process_tree(os.getpid())
        api_stats = {}
        memory_governance = {}
        chronos_registry = {}
        if self.brain is not None and getattr(self.brain, "memory", None) is not None:
            distiller = getattr(self.brain.memory, "distiller", None)
            if distiller is not None:
                api_stats = {
                    "last_api_latency_seconds": getattr(
                        distiller, "last_api_latency_seconds", None
                    ),
                    "last_api_trigger_type": getattr(
                        distiller, "last_api_trigger_type", None
                    ),
                    "last_api_finished_at": getattr(
                        distiller, "last_api_finished_at", None
                    ),
                }
            memory_governance = self.brain.memory.export_memory_governance_snapshot()
            chronos_registry = self.brain.chronos.describe_cruise_schedules()
        heartbeat_count = (
            self.life_loop.heartbeat_count if self.life_loop is not None else 0
        )
        return {
            "timestamp": datetime.now().isoformat(),
            "phase": visible_phase,
            "phase_raw": phase,
            "lifecycle": {
                "current_phase": self.current_phase,
                "transition_phase": self.last_transition_phase,
                "stable_phase": self.stable_phase,
                "shutdown_reason": self.shutdown_reason,
                "pause_reason": self.pause_reason,
                "stable_window_started_at": self.stable_window_started_at.isoformat()
                if self.stable_window_started_at
                else None,
            },
            "heartbeat_count": heartbeat_count,
            "heartbeat_seconds": self.heartbeat_seconds,
            "process_tree": process_tree,
            "last_result": result or {},
            "api": api_stats,
            "chronos_registry": chronos_registry,
            "shadow_sandbox": self.shadow_sandbox_health,
            "action_journal": get_action_journal().get_health(),
            "real_data_idempotency": self.real_data_watcher.get_idempotency_status(),
            "recovery_probe": {
                "path": str(self.recovery_probe_path),
                "exists": self.recovery_probe_path.exists(),
            },
            "pause_probe": {
                "path": str(self.pause_probe_path),
                "exists": self.pause_probe_path.exists(),
                "state": self.pause_state,
            },
            "last_recovery": self.last_recovery,
            "recovery_actions": self.last_recovery_actions,
            "recovery_control": {
                "attempts": self.recovery_attempts,
                "max_attempts": self.recovery_max_attempts,
                "cooldown_seconds": self.recovery_cooldown_seconds,
                "last_started_at": self.last_recovery_started_at.isoformat()
                if self.last_recovery_started_at
                else None,
                "backoff_seconds": self.last_recovery_backoff_seconds,
            },
            "gate_report": self.latest_gate_report,
            "real_data_report": self.latest_real_data_report,
            "pattern_distillation": self.latest_pattern_distillation,
            "pattern_promotion": self.latest_pattern_promotion,
            "memory_governance": memory_governance,
            "manifesto_draft": self.latest_manifesto_draft,
            "heartbeat_outputs": self.latest_heartbeat_outputs,
            "map_refresh": self.latest_map_refresh,
            "runtime_health": self._build_runtime_health_snapshot(),
            "runtime_governance": self._load_json_or_empty(
                self.runtime_governance_event_path
            ),
            "steady_running_streak": self.steady_running_streak,
            "stable_running_seconds": self._calculate_stable_running_seconds(),
        }

    def _check_recovery_probe(self) -> None:
        if not self.recovery_probe_path.exists():
            return
        try:
            payload = self.recovery_probe_path.read_text(encoding="utf-8").strip()
        except OSError:
            payload = "manual_probe"
        try:
            self.recovery_probe_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"recovery_probe_triggered:{payload or 'manual_probe'}")

    @staticmethod
    def _parse_recovery_probe_command(payload: str) -> Dict[str, Any]:
        stripped = payload.strip()
        if not stripped:
            return {"mode": "trigger", "reason": "manual_probe", "metadata": {}}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {"mode": "trigger", "reason": stripped, "metadata": {}}
        if not isinstance(parsed, dict):
            return {
                "mode": "trigger",
                "reason": str(parsed) or "manual_probe",
                "metadata": {},
            }
        mode = str(parsed.get("mode") or "trigger").lower()
        if mode not in {"trigger", "inject_recovery_action"}:
            mode = "trigger"
        metadata = {
            str(key): value
            for key, value in parsed.items()
            if key not in {"mode", "reason", "target_action", "status", "failure_class"}
        }
        return {
            "mode": mode,
            "reason": str(parsed.get("reason") or "manual_probe"),
            "target_action": parsed.get("target_action"),
            "status": str(parsed.get("status") or "failed").lower(),
            "failure_class": str(parsed.get("failure_class") or "hard_failed").lower(),
            "metadata": metadata,
        }

    def _check_pause_probe(self) -> bool:
        if not self.pause_probe_path.exists():
            if self.pause_state.get("status") == "paused":
                self.pause_state = {
                    "status": "running",
                    "updated_at": datetime.now().isoformat(),
                    "reason": "pause_probe_cleared",
                }
                self.pause_reason = None
                self._transition_phase(
                    "running",
                    result={
                        "subphase": "pause_released",
                        "reason": "pause_probe_cleared",
                    },
                )
            return False
        try:
            payload = self.pause_probe_path.read_text(encoding="utf-8").strip()
        except OSError:
            payload = "manual_pause"
        command = self._parse_pause_probe_command(payload)
        if command["action"] == "resume":
            self.pause_reason = None
            self.pause_state = {
                "status": "running",
                "updated_at": datetime.now().isoformat(),
                "reason": command["reason"],
                "protocol": "pause_probe",
                "command": "resume",
                "metadata": command["metadata"],
            }
            try:
                self.pause_probe_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._transition_phase(
                "running",
                result={
                    "subphase": "pause_resume_requested",
                    "reason": command["reason"],
                    "metadata": command["metadata"],
                },
            )
            return False
        self.pause_reason = command["reason"]
        self.pause_state = {
            "status": "paused",
            "updated_at": datetime.now().isoformat(),
            "reason": self.pause_reason,
            "protocol": "pause_probe",
            "command": "pause",
            "metadata": command["metadata"],
        }
        self._transition_phase(
            "paused",
            result={
                "subphase": "paused_by_probe",
                "reason": self.pause_reason,
                "metadata": command["metadata"],
            },
        )
        return True

    @staticmethod
    def _parse_pause_probe_command(payload: str) -> Dict[str, Any]:
        stripped = payload.strip()
        if not stripped:
            return {"action": "pause", "reason": "manual_pause", "metadata": {}}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {"action": "pause", "reason": stripped, "metadata": {}}
        if not isinstance(parsed, dict):
            return {
                "action": "pause",
                "reason": str(parsed) or "manual_pause",
                "metadata": {},
            }
        action = str(parsed.get("action") or parsed.get("status") or "pause").lower()
        if action not in {"pause", "resume"}:
            action = "pause"
        default_reason = "manual_resume" if action == "resume" else "manual_pause"
        reason = str(parsed.get("reason") or parsed.get("message") or default_reason)
        metadata = {
            str(key): value
            for key, value in parsed.items()
            if key not in {"action", "status", "reason", "message"}
        }
        return {"action": action, "reason": reason, "metadata": metadata}

    def _update_runtime_health(self, result: Dict[str, Any]) -> None:
        report_dir_count = (
            len(self._list_runtime_report_pressure_files())
            if REPORTS_DIR.exists()
            else 0
        )
        processed_dir_count = (
            sum(1 for _ in self.real_data_archive.glob("*"))
            if self.real_data_archive.exists()
            else 0
        )
        event = {
            "timestamp": datetime.now().isoformat(),
            "heartbeat": result.get("heartbeat"),
            "phase": result.get("phase") or self.current_phase,
            "steady_running_streak": self.steady_running_streak,
            "recovery_attempts": self.recovery_attempts,
            "report_dir_count": report_dir_count,
            "processed_dir_count": processed_dir_count,
            "map_refresh_status": self.latest_map_refresh.get("status"),
        }
        self.runtime_health_window.append(event)
        self.runtime_health_window = self.runtime_health_window[-20:]
        snapshot = self._build_runtime_health_snapshot()
        payload = {
            "generated_at": datetime.now().isoformat(),
            "event": event,
            "snapshot": snapshot,
        }
        self.runtime_health_event_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.runtime_health_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _build_runtime_health_snapshot(self) -> Dict[str, Any]:
        directory_pressure_policy = self._build_directory_pressure_policy()
        reports_alert_threshold = int(
            directory_pressure_policy.get("reports_alert_threshold", 50)
        )
        processed_alert_threshold = int(
            directory_pressure_policy.get("processed_alert_threshold", 200)
        )
        reports_pause_threshold = int(
            directory_pressure_policy.get(
                "reports_pause_threshold", reports_alert_threshold
            )
        )
        processed_pause_threshold = int(
            directory_pressure_policy.get(
                "processed_pause_threshold", processed_alert_threshold
            )
        )
        recent = self.runtime_health_window[-10:]
        if not recent:
            return {
                "status": "bootstrap",
                "steady_running_streak": self.steady_running_streak,
                "stable_running_seconds": self._calculate_stable_running_seconds(),
                "long_run_state": "bootstrap",
                "recovery_attempts": self.recovery_attempts,
                "abnormal_windows": 0,
                "directory_pressure": {"reports": 0, "processed": 0},
                "directory_pressure_policy": directory_pressure_policy,
                "pause_control_protocol": self._build_pause_control_protocol(),
                "recovery_status_detail": self._build_recovery_status_detail(),
            }
        abnormal_windows = sum(
            1
            for item in recent
            if item.get("phase") not in {"running", "paused"}
            or item.get("map_refresh_status") == "failed"
        )
        latest = recent[-1]
        report_pressure = int(latest.get("report_dir_count", 0))
        processed_pressure = int(latest.get("processed_dir_count", 0))
        pressure_alerts: list[str] = []
        if report_pressure >= reports_alert_threshold:
            pressure_alerts.append("reports_dir_growth")
        if processed_pressure >= processed_alert_threshold:
            pressure_alerts.append("processed_dir_growth")
        sustained_pressure_windows = sum(
            1
            for item in recent
            if int(item.get("report_dir_count", 0)) >= reports_alert_threshold
            or int(item.get("processed_dir_count", 0)) >= processed_alert_threshold
        )
        stable_running_seconds = self._calculate_stable_running_seconds()
        long_run_state = (
            "stable"
            if stable_running_seconds >= self.heartbeat_seconds * 3
            else "warming"
            if stable_running_seconds > 0
            else "bootstrap"
        )
        health_grade = (
            "A"
            if abnormal_windows == 0
            and not pressure_alerts
            and long_run_state == "stable"
            else "B"
            if abnormal_windows <= 1 and not pressure_alerts
            else "C"
        )
        governance_actions = []
        if long_run_state != "stable":
            governance_actions.append("continue_warmup")
        if pressure_alerts:
            governance_actions.append("review_directory_pressure")
        if abnormal_windows > 0:
            governance_actions.append("inspect_recent_abnormal_window")
        if self.recovery_attempts >= 3:
            governance_actions.append("review_recovery_pressure")
        if abnormal_windows >= 2 and pressure_alerts:
            governance_actions.append("consider_protective_pause")
        protective_pause_ready = (
            bool(pressure_alerts)
            and (
                report_pressure >= reports_pause_threshold
                or processed_pressure >= processed_pause_threshold
            )
            and sustained_pressure_windows
            >= directory_pressure_policy["protective_pause_requires"][
                "sustained_pressure_windows"
            ]
            and self.pause_state.get("status") not in {"paused", "pause_requested"}
        )
        if protective_pause_ready:
            governance_actions.append("engage_protective_pause")
        directory_pressure_expansion_assessment = (
            self._build_directory_pressure_expansion_assessment(
                report_pressure=report_pressure,
                processed_pressure=processed_pressure,
                sustained_pressure_windows=sustained_pressure_windows,
                protective_pause_ready=protective_pause_ready,
            )
        )
        return {
            "status": "healthy"
            if abnormal_windows == 0 and not pressure_alerts
            else "watch",
            "steady_running_streak": self.steady_running_streak,
            "stable_running_seconds": stable_running_seconds,
            "long_run_state": long_run_state,
            "health_grade": health_grade,
            "recovery_attempts": self.recovery_attempts,
            "abnormal_windows": abnormal_windows,
            "directory_pressure": {
                "reports": report_pressure,
                "processed": processed_pressure,
            },
            "sustained_pressure_windows": sustained_pressure_windows,
            "directory_pressure_policy": directory_pressure_policy,
            "pressure_alerts": pressure_alerts,
            "pause_state": self.pause_state,
            "pause_control_protocol": self._build_pause_control_protocol(),
            "recovery_status_detail": self._build_recovery_status_detail(),
            "protective_pause_ready": protective_pause_ready,
            "directory_pressure_expansion_assessment": directory_pressure_expansion_assessment,
            "governance_actions": governance_actions,
        }

    def _build_directory_pressure_policy(self) -> Dict[str, Any]:
        return {
            "reports_alert_threshold": 50,
            "processed_alert_threshold": 200,
            "reports_pause_threshold": 70,
            "processed_pause_threshold": 200,
            "protective_pause_requires": {
                "abnormal_windows": 2,
                "pressure_alerts": True,
                "sustained_pressure_windows": 3,
            },
            "auto_actions": ["pause_probe", "manual_resume_required"],
            "report_archive_rotation": {
                "keep_latest": 2,
                "candidate_prefixes": [
                    "real_data_",
                    "runtime_controlled_recovery_sample",
                ],
                "archive_root": str(self.report_archive_root),
            },
            "processed_retention": {
                "keep_latest_adapted": 12,
                "candidate_glob": "*.adapted.json",
                "retention_root": str(self.processed_retention_root),
            },
        }

    def _list_runtime_report_pressure_files(self) -> list[Path]:
        if not REPORTS_DIR.exists():
            return []
        candidates = []
        for path in REPORTS_DIR.glob("*.json"):
            name = path.name
            if name.endswith(".template.json") or name.endswith(
                ".template.refined.json"
            ):
                continue
            if name.startswith("m12x_"):
                continue
            candidates.append(path)
        return candidates

    @staticmethod
    def _build_directory_pressure_expansion_assessment(
        *,
        report_pressure: int,
        processed_pressure: int,
        sustained_pressure_windows: int,
        protective_pause_ready: bool,
    ) -> Dict[str, Any]:
        reasons: list[str] = []
        recommendation = "pause_only_sufficient"
        if report_pressure >= 100:
            reasons.append("reports_dir_over_100")
        if processed_pressure >= 400:
            reasons.append("processed_dir_over_400")
        if sustained_pressure_windows >= 3:
            reasons.append("pressure_persisted_multiple_windows")
        if protective_pause_ready and reasons:
            recommendation = "adopt_archive_rotation_next"
        return {
            "recommendation": recommendation,
            "reasons": reasons,
            "candidate_actions": (
                ["report_archive_rotation", "processed_retention_review"]
                if recommendation == "adopt_archive_rotation_next"
                else ["continue_pause_only_guard"]
            ),
        }

    def _build_pause_control_protocol(self) -> Dict[str, Any]:
        return {
            "path": str(self.pause_probe_path),
            "mode": "file_probe",
            "payload_format": "plain_text_or_json",
            "supported_actions": ["pause", "resume", "clear_probe"],
            "probe_exists": self.pause_probe_path.exists(),
        }

    def _build_recovery_status_detail(self) -> Dict[str, Any]:
        return {
            "status": self.last_recovery.get("status", "idle"),
            "recovery_tier": self.last_recovery.get("recovery_tier"),
            "failure_class": self.last_recovery.get("failure_class"),
            "degrade_reason": self.last_recovery.get("degrade_reason"),
            "backoff_seconds": self.last_recovery_backoff_seconds,
        }

    def _apply_runtime_governance(self) -> Dict[str, Any]:
        snapshot = self._build_runtime_health_snapshot()
        actions: list[Dict[str, Any]] = []
        cache_result: Optional[Dict[str, Any]] = None

        def record(name: str, status: str, detail: Any) -> None:
            actions.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                    "at": datetime.now().isoformat(),
                }
            )

        if self._should_refresh_external_risk_cache():
            cache_result = self._sync_external_risk_runtime_cache()
            record("refresh_external_risk_cache", "completed", cache_result)
            if cache_result.get("status") == "refreshed_with_fallback":
                record(
                    "external_risk_remote_fallback",
                    "watch",
                    {
                        "remote_summary": cache_result.get("remote_summary", {}),
                        "source_priority": cache_result.get("source_priority", {}),
                    },
                )

        external_risk_audit = self._write_runtime_external_risk_audit(cache_result)
        if external_risk_audit.get("fallback_sources"):
            record(
                "external_risk_fallback_audit",
                "watch",
                {
                    "fallback_sources": external_risk_audit.get("fallback_sources", []),
                    "action_recommendations": external_risk_audit.get(
                        "action_recommendations", []
                    ),
                },
            )
        ordered_sources = external_risk_audit.get("conflict_summary", {}).get(
            "ordered_sources", []
        )
        if len(ordered_sources) > 1:
            record(
                "external_risk_conflict_audit",
                "watch",
                {
                    "conflict_summary": external_risk_audit.get("conflict_summary", {}),
                    "action_recommendations": external_risk_audit.get(
                        "action_recommendations", []
                    ),
                },
            )

        if snapshot.get("pressure_alerts"):
            record(
                "directory_pressure_watch",
                "watch",
                {
                    "pressure_alerts": snapshot.get("pressure_alerts", []),
                    "directory_pressure": snapshot.get("directory_pressure", {}),
                },
            )

        if snapshot.get("health_grade") == "C":
            record(
                "protective_pause_recommendation",
                "watch",
                {
                    "health_grade": snapshot.get("health_grade"),
                    "governance_actions": snapshot.get("governance_actions", []),
                },
            )

        if snapshot.get("protective_pause_ready"):
            pause_payload = self._engage_directory_pressure_protective_pause(snapshot)
            record("protective_pause_engaged", "completed", pause_payload)

        expansion = snapshot.get("directory_pressure_expansion_assessment", {})
        if expansion.get("recommendation") == "adopt_archive_rotation_next":
            report_rotation = self._rotate_reports_directory()
            record(
                "report_archive_rotation",
                "completed" if report_rotation.get("moved_count") else "watch",
                report_rotation,
            )
            processed_retention = self._apply_processed_retention_policy()
            record(
                "processed_retention_review",
                "completed" if processed_retention.get("moved_count") else "watch",
                processed_retention,
            )

        payload = {
            "generated_at": datetime.now().isoformat(),
            "status": "applied" if actions else "idle",
            "snapshot": snapshot,
            "action_names": [item["name"] for item in actions],
            "control_protocol": snapshot.get("pause_control_protocol", {}),
            "directory_pressure_policy": snapshot.get("directory_pressure_policy", {}),
            "directory_pressure_expansion_assessment": snapshot.get(
                "directory_pressure_expansion_assessment", {}
            ),
            "external_risk_audit": external_risk_audit,
            "actions": actions,
        }
        self.runtime_governance_event_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.runtime_governance_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def _engage_directory_pressure_protective_pause(
        self, snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload = {
            "action": "pause",
            "reason": "auto_directory_pressure_guard",
            "source": "runtime_governance",
            "pressure_alerts": snapshot.get("pressure_alerts", []),
            "directory_pressure": snapshot.get("directory_pressure", {}),
            "sustained_pressure_windows": snapshot.get("sustained_pressure_windows", 0),
            "resume_hint": "目录压力缓解后写入 resume 指令或清除 pause probe。",
            "generated_at": datetime.now().isoformat(),
        }
        self.pause_reason = str(payload["reason"])
        self.pause_state = {
            "status": "pause_requested",
            "updated_at": payload["generated_at"],
            "reason": payload["reason"],
            "protocol": "pause_probe",
            "command": "pause",
            "metadata": {
                "source": payload["source"],
                "pressure_alerts": payload["pressure_alerts"],
                "sustained_pressure_windows": payload["sustained_pressure_windows"],
            },
        }
        self.pause_probe_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload

    def _rotate_reports_directory(self) -> Dict[str, Any]:
        policy = self._build_directory_pressure_policy().get(
            "report_archive_rotation", {}
        )
        keep_latest = int(policy.get("keep_latest", 2))
        archive_root = Path(str(policy.get("archive_root") or self.report_archive_root))
        candidates = self._list_report_rotation_candidates()
        if len(candidates) <= keep_latest:
            return {
                "status": "skipped",
                "candidate_count": len(candidates),
                "moved_count": 0,
                "archive_root": str(archive_root),
            }
        archive_dir = archive_root / datetime.now().strftime("%Y%m%d")
        archive_dir.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []
        for path in candidates[:-keep_latest]:
            target = archive_dir / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved.append(str(target))
        return {
            "status": "completed",
            "candidate_count": len(candidates),
            "moved_count": len(moved),
            "archive_root": str(archive_root),
            "archive_dir": str(archive_dir),
            "moved_files": moved[:20],
            "kept_latest": keep_latest,
        }

    def _list_report_rotation_candidates(self) -> list[Path]:
        if not REPORTS_DIR.exists():
            return []
        protected_exact = {
            self.gate_report_json_path.name,
            self.gate_report_md_path.name,
            self.pattern_distillation_report_path.name,
            self.pattern_distillation_report_md_path.name,
            self.pattern_distillation_batch_report_path.name,
            self.pattern_distillation_batch_report_md_path.name,
            self.pattern_anchor_review_path.name,
            self.pattern_anchor_review_md_path.name,
            self.pattern_anchor_registry_path.name,
            self.pattern_anchor_registry_md_path.name,
            self.manifesto_draft_snapshot_path.name,
            self.manifesto_draft_snapshot_md_path.name,
            self.manifesto_draft_log_path.name,
            self.manifesto_draft_compare_path.name,
            self.manifesto_draft_compare_md_path.name,
            self.manifesto_review_path.name,
            self.manifesto_review_md_path.name,
            self.manifesto_approval_gate_path.name,
            self.manifesto_approval_gate_md_path.name,
            self.manifesto_rewrite_candidate_path.name,
            self.manifesto_rewrite_candidate_md_path.name,
            self.manifesto_rewrite_simulation_path.name,
            self.manifesto_rewrite_simulation_md_path.name,
            self.manifesto_controlled_rewrite_path.name,
            self.manifesto_controlled_rewrite_md_path.name,
            self.manifesto_writeback_gate_path.name,
            self.manifesto_writeback_gate_md_path.name,
            self.manifesto_writeback_authorization_path.name,
            self.manifesto_writeback_authorization_md_path.name,
            self.runtime_timeline_path.name,
            self.external_risk_hit_event_path.name,
            self.runtime_health_event_path.name,
            self.runtime_health_log_path.name,
            self.runtime_governance_event_path.name,
            self.runtime_governance_log_path.name,
            "progress_issue_ledger.json",
            "progress_issue_ledger.md",
        }
        prefixes = tuple(
            self._build_directory_pressure_policy()
            .get("report_archive_rotation", {})
            .get("candidate_prefixes", [])
        )
        candidates: list[Path] = []
        for path in REPORTS_DIR.iterdir():
            if not path.is_file():
                continue
            if path.name in protected_exact:
                continue
            if not prefixes or not path.name.startswith(prefixes):
                continue
            candidates.append(path)
        return sorted(candidates, key=lambda item: item.stat().st_mtime)

    def _apply_processed_retention_policy(self) -> Dict[str, Any]:
        policy = self._build_directory_pressure_policy().get("processed_retention", {})
        keep_latest = int(policy.get("keep_latest_adapted", 12))
        retention_root = Path(
            str(policy.get("retention_root") or self.processed_retention_root)
        )
        candidates = sorted(
            self.real_data_archive.glob(
                str(policy.get("candidate_glob") or "*.adapted.json")
            ),
            key=lambda item: item.stat().st_mtime,
        )
        if len(candidates) <= keep_latest:
            return {
                "status": "skipped",
                "candidate_count": len(candidates),
                "moved_count": 0,
                "retention_root": str(retention_root),
            }
        retention_dir = retention_root / datetime.now().strftime("%Y%m%d")
        retention_dir.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []
        for path in candidates[:-keep_latest]:
            target = retention_dir / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved.append(str(target))
        return {
            "status": "completed",
            "candidate_count": len(candidates),
            "moved_count": len(moved),
            "retention_root": str(retention_root),
            "retention_dir": str(retention_dir),
            "moved_files": moved[:20],
            "kept_latest": keep_latest,
        }

    def _resolve_recovery_injection(self, exc: Exception) -> Dict[str, Any]:
        message = str(exc)
        prefix = "recovery_probe_triggered:"
        if not message.startswith(prefix):
            return {"enabled": False}
        command = self._parse_recovery_probe_command(message[len(prefix) :])
        if command.get("mode") != "inject_recovery_action":
            return {"enabled": False, "reason": command.get("reason")}
        status = str(command.get("status") or "failed").lower()
        if status not in {"failed", "degraded"}:
            status = "failed"
        failure_class = str(command.get("failure_class") or "hard_failed").lower()
        if failure_class not in {"hard_failed", "degraded"}:
            failure_class = "hard_failed" if status == "failed" else "degraded"
        return {
            "enabled": True,
            "target_action": str(command.get("target_action") or "reload_gate_report"),
            "status": status,
            "failure_class": failure_class,
            "reason": str(command.get("reason") or "controlled_recovery_injection"),
            "metadata": command.get("metadata", {}),
        }

    @staticmethod
    def _should_inject_recovery_action(
        injection_plan: Dict[str, Any], action_name: str
    ) -> bool:
        return (
            bool(injection_plan.get("enabled"))
            and injection_plan.get("target_action") == action_name
        )

    @staticmethod
    def _build_injected_recovery_detail(
        injection_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "injected": True,
            "reason": injection_plan.get("reason"),
            "failure_class": injection_plan.get("failure_class"),
            "metadata": injection_plan.get("metadata", {}),
        }

    def _should_refresh_external_risk_cache(self) -> bool:
        if not self.external_risk_cache_path.exists():
            return True
        try:
            payload = json.loads(
                self.external_risk_cache_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return True
        generated_at = payload.get("generated_at")
        if not generated_at:
            return True
        try:
            age_seconds = (
                datetime.now() - datetime.fromisoformat(str(generated_at))
            ).total_seconds()
        except ValueError:
            return True
        return age_seconds >= max(self.heartbeat_seconds * 3, 900)

    def _perform_recovery_actions(self, exc: Exception) -> Dict[str, Any]:
        actions: list[Dict[str, Any]] = []
        recovery_started_at = datetime.now()
        injection_plan = self._resolve_recovery_injection(exc)

        def record_action(name: str, status: str, detail: Any) -> None:
            actions.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                    "at": datetime.now().isoformat(),
                }
            )

        previous_started_at = self.last_recovery_started_at
        self.last_recovery_started_at = recovery_started_at
        self.recovery_attempts += 1
        if previous_started_at is not None:
            elapsed = (recovery_started_at - previous_started_at).total_seconds()
            if elapsed < self.recovery_cooldown_seconds:
                self.last_recovery_backoff_seconds = int(
                    self.recovery_cooldown_seconds - elapsed
                )
                record_action(
                    "recovery_cooldown_guard",
                    "degraded",
                    {
                        "elapsed_seconds": round(elapsed, 3),
                        "backoff_seconds": self.last_recovery_backoff_seconds,
                    },
                )
                return self._build_recovery_result(
                    actions=actions,
                    exc=exc,
                    summary_override="恢复过于频繁，已触发冷却窗口。",
                    status_override="degraded",
                    phase_override="recovery_failed",
                    recovery_tier_override="degraded",
                )
        if self.recovery_attempts > self.recovery_max_attempts:
            self.last_recovery_backoff_seconds = min(
                self.heartbeat_seconds * self.recovery_attempts,
                300,
            )
            record_action(
                "recovery_attempt_guard",
                "degraded",
                {
                    "attempts": self.recovery_attempts,
                    "max_attempts": self.recovery_max_attempts,
                    "backoff_seconds": self.last_recovery_backoff_seconds,
                },
            )
            return self._build_recovery_result(
                actions=actions,
                exc=exc,
                summary_override="恢复次数超限，进入降级退避窗口。",
                status_override="degraded",
                phase_override="recovery_failed",
                recovery_tier_override="degraded",
            )

        record_action(
            "cleanup_temp_resources",
            "completed",
            self._cleanup_recovery_temp_resources(),
        )
        record_action(
            "cleanup_runtime_probe_files",
            "completed",
            self._cleanup_runtime_probe_files(),
        )
        record_action(
            "reset_runtime_queues",
            "completed",
            self._reset_runtime_recovery_queues(),
        )
        if self._should_inject_recovery_action(
            injection_plan, "refresh_external_risk_cache"
        ):
            record_action(
                "refresh_external_risk_cache",
                injection_plan["status"],
                self._build_injected_recovery_detail(injection_plan),
            )
        else:
            record_action(
                "refresh_external_risk_cache",
                "completed",
                self._sync_external_risk_runtime_cache(),
            )

        try:
            if self.shadow_sandbox is None:
                if self._should_inject_recovery_action(
                    injection_plan, "rebuild_shadow_sandbox"
                ):
                    record_action(
                        "rebuild_shadow_sandbox",
                        injection_plan["status"],
                        self._build_injected_recovery_detail(injection_plan),
                    )
                else:
                    self.shadow_sandbox = ShadowSandbox()
                    self.shadow_sandbox_health = self.shadow_sandbox.describe()
                    record_action(
                        "rebuild_shadow_sandbox",
                        "completed",
                        self.shadow_sandbox_health,
                    )
            else:
                if self._should_inject_recovery_action(
                    injection_plan, "refresh_shadow_sandbox_health"
                ):
                    record_action(
                        "refresh_shadow_sandbox_health",
                        injection_plan["status"],
                        self._build_injected_recovery_detail(injection_plan),
                    )
                else:
                    self.shadow_sandbox_health = self.shadow_sandbox.describe()
                    record_action(
                        "refresh_shadow_sandbox_health",
                        "completed",
                        self.shadow_sandbox_health,
                    )
        except Exception as sandbox_exc:
            self.shadow_sandbox = None
            self.shadow_sandbox_health = {
                "available": False,
                "backend": "docker_cli",
                "image": "python:3.10-slim",
                "error": str(sandbox_exc),
            }
            record_action("recover_shadow_sandbox", "failed", str(sandbox_exc))

        try:
            if self._should_inject_recovery_action(
                injection_plan, "reload_gate_report"
            ):
                record_action(
                    "reload_gate_report",
                    injection_plan["status"],
                    self._build_injected_recovery_detail(injection_plan),
                )
            else:
                self.latest_gate_report = self._load_existing_gate_report()
                record_action(
                    "reload_gate_report",
                    "completed",
                    self.latest_gate_report.get("report_id"),
                )
        except Exception as gate_exc:
            record_action("reload_gate_report", "failed", str(gate_exc))

        try:
            if self._should_inject_recovery_action(
                injection_plan, "reload_real_data_report"
            ):
                record_action(
                    "reload_real_data_report",
                    injection_plan["status"],
                    self._build_injected_recovery_detail(injection_plan),
                )
            else:
                self.latest_real_data_report = (
                    self.real_data_watcher.load_latest_report()
                )
                record_action(
                    "reload_real_data_report",
                    "completed",
                    self.latest_real_data_report.get("report_id"),
                )
        except Exception as real_exc:
            record_action("reload_real_data_report", "failed", str(real_exc))

        try:
            journal_health = get_action_journal().get_health()
            record_action("snapshot_action_journal", "completed", journal_health)
        except Exception as journal_exc:
            record_action("snapshot_action_journal", "failed", str(journal_exc))

        has_failure = any(item["status"] == "failed" for item in actions)
        has_degraded = any(item["status"] == "degraded" for item in actions)
        if not has_failure and not has_degraded:
            self.recovery_attempts = 0
            self.last_recovery_backoff_seconds = 0
        return self._build_recovery_result(actions=actions, exc=exc)

    def _build_recovery_result(
        self,
        actions: list[Dict[str, Any]],
        exc: Exception,
        summary_override: Optional[str] = None,
        status_override: Optional[str] = None,
        phase_override: Optional[str] = None,
        recovery_tier_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        has_failure = any(item["status"] == "failed" for item in actions)
        has_degraded = any(item["status"] == "degraded" for item in actions)
        status = status_override or (
            "recovered"
            if not has_failure and not has_degraded
            else "degraded"
            if has_degraded and not has_failure
            else "recovery_failed"
        )
        recovery_tier = recovery_tier_override or (
            "full"
            if status == "recovered"
            else "degraded"
            if status == "degraded"
            else "failed"
        )
        failure_class = self._classify_recovery_failure_class(
            status=status,
            recovery_tier=recovery_tier,
            actions=actions,
        )
        return {
            "status": status,
            "phase": phase_override
            or ("recovered" if status == "recovered" else "recovery_failed"),
            "summary": summary_override
            or (
                "恢复动作完成"
                if status == "recovered"
                else "恢复动作已执行，但系统进入降级/退避状态。"
                if status == "degraded"
                else f"恢复动作部分失败: {exc}"
            ),
            "actions": actions,
            "recovery_tier": recovery_tier,
            "failure_class": failure_class,
            "degrade_reason": self._summarize_recovery_degrade_reason(actions),
        }

    @staticmethod
    def _classify_recovery_failure_class(
        status: str,
        recovery_tier: str,
        actions: list[Dict[str, Any]],
    ) -> str:
        if status == "recovered" and recovery_tier == "full":
            return "recovered"
        if any(item.get("status") == "failed" for item in actions):
            return "hard_failed"
        if status == "degraded" or recovery_tier == "degraded":
            return "degraded"
        return "hard_failed"

    def _cleanup_recovery_temp_resources(self) -> Dict[str, Any]:
        cleaned: list[str] = []
        scanned = 0
        for root in (REPORTS_DIR, self.real_data_archive):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                scanned += 1
                if path.suffix.lower() not in {".tmp", ".lock", ".bak"}:
                    continue
                try:
                    path.unlink(missing_ok=True)
                    cleaned.append(str(path))
                except OSError:
                    continue
        return {
            "scanned_file_count": scanned,
            "cleaned_file_count": len(cleaned),
            "cleaned_files": cleaned[:20],
        }

    def _cleanup_runtime_probe_files(self) -> Dict[str, Any]:
        removed = []
        for path in (self.recovery_probe_path, self.pause_probe_path):
            if not path.exists():
                continue
            try:
                path.unlink(missing_ok=True)
                removed.append(str(path))
            except OSError:
                continue
        return {"removed_probe_files": removed, "removed_count": len(removed)}

    def _reset_runtime_recovery_queues(self) -> Dict[str, Any]:
        state = self._load_pattern_distillation_state()
        pending_paths = [
            item
            for item in state.get("pending_report_paths", [])
            if isinstance(item, str) and Path(item).exists()
        ]
        state["pending_report_paths"] = pending_paths[-200:]
        self._save_pattern_distillation_state(state)
        idempotency = self.real_data_watcher.get_idempotency_status()
        return {
            "pending_report_paths": len(state["pending_report_paths"]),
            "real_data_processed_entries": idempotency.get("legacy_entries"),
        }

    @staticmethod
    def _summarize_recovery_degrade_reason(
        actions: list[Dict[str, Any]],
    ) -> Optional[str]:
        degraded = [
            item.get("name") for item in actions if item.get("status") == "degraded"
        ]
        failed = [
            item.get("name") for item in actions if item.get("status") == "failed"
        ]
        if failed:
            return f"failed:{','.join(str(item) for item in failed)}"
        if degraded:
            return f"degraded:{','.join(str(item) for item in degraded)}"
        return None

    async def _complete_planned_cruise(self) -> None:
        self.shutdown_reason = "scheduled"
        self.running = False
        final_status = self._run_final_sync()
        self._report_heartbeat(final_status)

    def _run_final_sync(self) -> Dict[str, Any]:
        if self.brain is None:
            raise RuntimeError("生产态关闭前未初始化 CentralBrain")
        if self.life_loop is None:
            raise RuntimeError("生产态关闭前未初始化 AutonomousLifeLoop")

        self.brain.chronos.shutdown()
        payload = self._refresh_evolution_map(reason="scheduled_shutdown")
        self.life_loop.decision_brain.reload()
        self.life_loop.decision_brain.generate_manifesto(
            output_path=str(self.repo_root / "decision_manifesto.md")
        )
        gate_report = self._run_gate_daily_report(trigger="scheduled_shutdown")
        real_data_report = self._run_real_data_watch(trigger="scheduled_shutdown")
        return {
            "heartbeat": self.life_loop.heartbeat_count,
            "domain": "system",
            "allowed": True,
            "template": "scheduled_shutdown",
            "sync": {
                "wisdom_node_count": len(payload.get("wisdom_nodes", [])),
                "generated_at": payload.get("generated_at"),
                "gate_report": gate_report,
                "real_data_report": real_data_report,
            },
        }

    def _refresh_evolution_map(self, reason: str) -> Dict[str, Any]:
        if self.brain is None:
            return {"status": "skipped", "reason": "brain_not_ready"}
        last_error: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                payload = export_evolution_map(
                    self.brain.memory,
                    output_path=str(self.repo_root / "evolution_map.json"),
                    repo_root=str(self.repo_root),
                    brain=self.brain,
                )
                self.latest_map_refresh = {
                    "status": "generated",
                    "reason": reason,
                    "generated_at": payload.get("generated_at"),
                    "wisdom_node_count": len(payload.get("wisdom_nodes", [])),
                    "module_edge_count": len(payload.get("module_edges", [])),
                    "attempt": attempt,
                }
                return payload
            except Exception as exc:
                last_error = exc
        self.latest_map_refresh = {
            "status": "failed",
            "reason": reason,
            "error": str(last_error),
            "generated_at": datetime.now().isoformat(),
            "attempt": 2,
        }
        return self.latest_map_refresh

    def _hydrate_latest_map_refresh(self) -> None:
        baseline = read_map_baseline(self.repo_root / "evolution_map.json")
        if baseline.get("generated_at") is None:
            self.latest_map_refresh = {
                "status": "idle",
                "reason": "bootstrap",
                "generated_at": None,
            }
            return
        self.latest_map_refresh = {
            "status": "hydrated",
            "reason": "bootstrap",
            "generated_at": baseline.get("generated_at"),
            "wisdom_node_count": baseline.get("wisdom_nodes", 0),
        }

    def _run_real_data_watch(self, *, trigger: str) -> Dict[str, Any]:
        pending = self.real_data_watcher.discover_pending()
        if not pending:
            if self.latest_real_data_report:
                return self.latest_real_data_report
            self.latest_real_data_report = {
                "status": "idle",
                "trigger": trigger,
                "reason": "no_new_inquiry_files",
            }
            return self.latest_real_data_report

        latest_result: Dict[str, Any] = {}
        pending_limit = 1
        remaining_pending_count = max(0, len(pending) - pending_limit)
        for item in pending[:pending_limit]:
            report_prefix = f"real_data_{item.path.stem}_report"
            sample_set = self.real_data_adapter.adapt_file(
                item.path,
                sample_set_id=f"trade_inquiry_import_{item.path.stem}",
            )
            sample_set_path = self.real_data_archive / f"{item.path.stem}.adapted.json"
            sample_set_path.write_text(
                json.dumps(
                    sample_set.model_dump(mode="json"), ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            report_json_path = REPORTS_DIR / f"{report_prefix}.json"
            report_md_path = REPORTS_DIR / f"{report_prefix}.md"
            coordinator = ThreeAgentCruiseCoordinator(journal=get_action_journal())
            report = coordinator.run_sample_cruise(
                sample_set,
                output_path=report_json_path,
                markdown_path=report_md_path,
                schema_path=self.gate_report_schema_path,
            )
            gated_count = sum(
                1
                for warning in report.warnings
                if warning.disposition == "gated_continue"
            )
            latest_result = {
                "status": "generated",
                "trigger": trigger,
                "input_path": str(item.path),
                "sample_set_path": str(sample_set_path),
                "report_id": report.report_id,
                "sample_count": report.sample_count,
                "gated_count": gated_count,
                "markdown_path": str(report_md_path),
                "json_path": str(report_json_path),
                "generated_at": report.generated_at,
                "remaining_pending_count": remaining_pending_count,
            }
            latest_result["pattern_distillation"] = self._distill_trade_report_patterns(
                report_json_path, report.report_id
            )
            self.real_data_watcher.mark_processed(item)
            self.real_data_watcher.save_latest_report(latest_result)
            log.info(
                "📥 已消费真实询盘文件 | trigger={} | input={} | report_id={} | gated_count={}",
                trigger,
                item.path,
                report.report_id,
                gated_count,
            )
        self.latest_real_data_report = latest_result
        return latest_result

    def _schedule_real_data_watch(self, *, trigger: str) -> Dict[str, Any]:
        if self.real_data_watch_task and not self.real_data_watch_task.done():
            return {
                "status": "running_async",
                "trigger": trigger,
                "latest_report_id": self.latest_real_data_report.get("report_id"),
            }

        async def _runner() -> None:
            await asyncio.to_thread(self._run_real_data_watch, trigger=trigger)

        self.real_data_watch_task = asyncio.create_task(_runner())
        return {
            "status": "scheduled_async",
            "trigger": trigger,
            "latest_report_id": self.latest_real_data_report.get("report_id"),
        }

    def _ensure_seed_trade_leads_csv(self) -> None:
        if self.lead_capture_path.exists():
            return
        source = (
            Path(settings.BASE_DIR)
            / "data"
            / "samples"
            / "foreign_trade_inquiry_template.csv"
        )
        if source.exists():
            self.real_data_inbox.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, self.lead_capture_path)

    async def bootstrap_module12_lead_capture(
        self,
        targets: list[LeadCaptureTarget],
        *,
        max_items_per_target: int = 10,
    ) -> Dict[str, Any]:
        if self.shadow_sandbox is None:
            raise RuntimeError("影子沙盒尚未初始化，无法执行模块 12 抓取")
        harvester = SandboxLeadHarvester(sandbox=self.shadow_sandbox)
        provision = await harvester.ensure_crawler_stack()
        capture = harvester.capture_trade_leads_csv(
            targets,
            output_path=self.lead_capture_path,
            max_items_per_target=max_items_per_target,
        )
        return {"provision": provision, "capture": capture}

    def _run_gate_daily_report(self, *, trigger: str) -> Dict[str, Any]:
        if not self.stress_sample_path.exists():
            self.latest_gate_report = {
                "status": "skipped",
                "trigger": trigger,
                "reason": "missing_stress_sample_set",
                "sample_path": str(self.stress_sample_path),
            }
            return self.latest_gate_report

        coordinator = ThreeAgentCruiseCoordinator(journal=get_action_journal())
        sample_set = coordinator.load_sample_set(self.stress_sample_path)
        report = coordinator.run_sample_cruise(
            sample_set,
            output_path=self.gate_report_json_path,
            markdown_path=self.gate_report_md_path,
            schema_path=self.gate_report_schema_path,
        )
        gated_count = sum(
            1 for item in report.warnings if item.disposition == "gated_continue"
        )
        self.latest_gate_report = {
            "status": "generated",
            "trigger": trigger,
            "report_id": report.report_id,
            "sample_count": report.sample_count,
            "gated_count": gated_count,
            "markdown_path": str(self.gate_report_md_path),
            "json_path": str(self.gate_report_json_path),
            "generated_at": report.generated_at,
        }
        log.info(
            "🧾 已生成门控样本日报 | trigger={} | report_id={} | gated_count={}",
            trigger,
            report.report_id,
            gated_count,
        )
        return self.latest_gate_report

    def _load_existing_gate_report(self) -> Dict[str, Any]:
        if not self.gate_report_json_path.exists():
            return {}
        try:
            payload = json.loads(self.gate_report_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        gated_count = sum(
            1
            for item in payload.get("warnings", [])
            if item.get("disposition") == "gated_continue"
        )
        return {
            "status": "generated",
            "trigger": "bootstrap",
            "report_id": payload.get("report_id"),
            "sample_count": payload.get("sample_count"),
            "gated_count": gated_count,
            "markdown_path": str(self.gate_report_md_path),
            "json_path": str(self.gate_report_json_path),
            "generated_at": payload.get("generated_at"),
        }

    def _distill_trade_report_patterns(
        self, report_json_path: Path, report_id: str
    ) -> Dict[str, Any]:
        if self.brain is None:
            return {"status": "skipped", "reason": "brain_not_ready"}
        state = self._load_pattern_distillation_state()
        if report_id in state.get("processed_report_ids", []):
            return {
                "status": "skipped",
                "reason": "report_already_distilled",
                "report_id": report_id,
            }
        try:
            payload = json.loads(report_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "failed",
                "reason": f"report_load_error:{exc}",
                "report_id": report_id,
            }

        candidates = self._extract_trade_pattern_candidates(payload)
        if not candidates:
            return {
                "status": "skipped",
                "reason": "no_pattern_candidates",
                "report_id": report_id,
            }

        memory_entries = []
        memory_ids = []
        for candidate in candidates:
            memory_id = self.brain.memory.create_memory(
                event=candidate["event"],
                thought=candidate["thought"],
                lesson=candidate["lesson"],
                source_type="trade_report_pattern",
                source_url=str(report_json_path),
                source_reputation=0.92,
                verification_status="verified",
                raw_payload={
                    "report_id": report_id,
                    "pattern_key": candidate["pattern_key"],
                    "sample_ids": candidate["sample_ids"],
                    "risk_vector": candidate["risk_vector"],
                },
                full_text=" ".join(
                    [candidate["event"], candidate["thought"], candidate["lesson"]]
                ),
            )
            if not memory_id:
                continue
            memory = self.brain.memory.db_manager.get_memory_by_id(int(memory_id))
            if memory is None:
                continue
            memory_entries.append(memory)
            memory_ids.append(int(memory_id))

        if not memory_entries:
            return {
                "status": "failed",
                "reason": "memory_creation_failed",
                "report_id": report_id,
            }

        distill_result = self.brain.memory.distill_memory(
            source_memories=memory_entries,
            trigger_type="trade_report_pattern",
            distillation_directives=[
                "优先将真实外贸报告中的重复风险模式压缩成可复用的风控法则。",
                "保留风险向量、处置建议与触发条件，避免写成样本流水账。",
            ],
        )
        if distill_result.get("created", 0) > 0 or distill_result.get("wisdom_ids"):
            processed_ids = list(state.get("processed_report_ids", []))
            processed_ids.append(report_id)
            state["processed_report_ids"] = processed_ids[-200:]
            self._save_pattern_distillation_state(state)
        result = {
            "status": "generated",
            "report_id": report_id,
            "candidate_count": len(candidates),
            "memory_ids": memory_ids,
            "distillation": distill_result,
            "generated_at": datetime.now().isoformat(),
        }
        self.latest_pattern_distillation = result
        self._save_latest_pattern_distillation(result)
        self._append_pattern_distillation_log(result)
        self._write_pattern_distillation_report(result)
        self._refresh_evolution_map(reason=f"pattern_distillation:{report_id}")
        return result

    def _extract_trade_pattern_candidates(
        self, payload: Dict[str, Any]
    ) -> list[Dict[str, Any]]:
        warnings = payload.get("warnings", []) or []
        grouped: Dict[str, Dict[str, Any]] = {}
        for warning in warnings:
            disposition = str(warning.get("disposition") or "")
            risk_vector = str(warning.get("primary_risk_vector") or "unknown")
            if disposition not in {"observe", "gated_continue", "block"}:
                continue
            key = f"{risk_vector}:{disposition}"
            bucket = grouped.setdefault(
                key,
                {
                    "risk_vector": risk_vector,
                    "disposition": disposition,
                    "sample_ids": [],
                    "scenario_names": [],
                    "tags": set(),
                },
            )
            bucket["sample_ids"].append(str(warning.get("sample_id") or ""))
            bucket["scenario_names"].append(str(warning.get("scenario_name") or ""))
            for tag in warning.get("explanation_tags", []) or []:
                bucket["tags"].add(str(tag))

        candidates: list[Dict[str, Any]] = []
        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: (
                -len(item[1]["sample_ids"]),
                item[1]["risk_vector"],
                item[1]["disposition"],
                item[0],
            ),
        )
        for key, bucket in ordered_groups:
            tags = sorted(tag for tag in bucket["tags"] if tag)
            scenario_preview = "；".join(
                name for name in bucket["scenario_names"][:2] if name
            )
            sample_count = len(bucket["sample_ids"])
            event = (
                f"真实外贸报告 {payload.get('report_id')} 显示 {bucket['risk_vector']} 风险向量在 {bucket['disposition']} 档位重复出现，"
                f"涉及 {sample_count} 个样本。"
            )
            thought = (
                f"模式判断：当主风险向量为 {bucket['risk_vector']} 且处置落在 {bucket['disposition']} 时，"
                f"样本常伴随标签 {', '.join(tags) if tags else '无显式标签'}；代表场景包括 {scenario_preview or '无场景预览'}。"
            )
            lesson = (
                f"风控法则：若后续报告再次出现 {bucket['risk_vector']} + {bucket['disposition']} 组合，"
                f"应优先复用既有复核路径，而不是从零判断。"
            )
            candidates.append(
                {
                    "pattern_key": key,
                    "risk_vector": bucket["risk_vector"],
                    "sample_count": sample_count,
                    "disposition": bucket["disposition"],
                    "review_status": "candidate",
                    "sample_ids": bucket["sample_ids"],
                    "event": event,
                    "thought": thought,
                    "lesson": lesson,
                }
            )
        summary = payload.get("summary") or {}
        if len(candidates) < 3 and summary:
            dominant_vectors = summary.get("dominant_risk_vectors") or []
            candidates.append(
                {
                    "pattern_key": f"summary:{payload.get('report_id')}",
                    "risk_vector": ",".join(
                        str(item) for item in dominant_vectors if item
                    ),
                    "sample_count": len(summary.get("top_warning_sample_ids") or []),
                    "disposition": "summary",
                    "review_status": "candidate",
                    "sample_ids": list(summary.get("top_warning_sample_ids") or []),
                    "event": (
                        f"真实外贸报告 {payload.get('report_id')} 的总览显示主要风险向量集中在 "
                        f"{', '.join(str(item) for item in dominant_vectors if item) or '未知风险轴'}。"
                    ),
                    "thought": (
                        f"总览判断：medium={summary.get('medium_count', 0)}，low={summary.get('low_count', 0)}，"
                        f"top samples={', '.join(str(item) for item in summary.get('top_warning_sample_ids', [])[:3]) or '无'}。"
                    ),
                    "lesson": "风控法则：先看总览主轴，再回到单样本核对是否属于已知模式簇。",
                }
            )
        return candidates[:3]

    def _load_pattern_distillation_state(self) -> Dict[str, Any]:
        if not self.report_pattern_state_path.exists():
            return {"processed_report_ids": [], "pending_report_paths": []}
        try:
            payload = json.loads(
                self.report_pattern_state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {"processed_report_ids": [], "pending_report_paths": []}
        if not isinstance(payload.get("processed_report_ids"), list):
            payload["processed_report_ids"] = []
        if not isinstance(payload.get("pending_report_paths"), list):
            payload["pending_report_paths"] = []
        return payload

    def _save_pattern_distillation_state(self, payload: Dict[str, Any]) -> None:
        self.report_pattern_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_latest_pattern_distillation(self) -> Dict[str, Any]:
        if not self.latest_pattern_distillation_path.exists():
            return {}
        try:
            return json.loads(
                self.latest_pattern_distillation_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_latest_pattern_distillation(self, payload: Dict[str, Any]) -> None:
        self.latest_pattern_distillation_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _append_pattern_distillation_log(self, payload: Dict[str, Any]) -> None:
        with self.pattern_distillation_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _maybe_distill_pending_trade_reports(self) -> None:
        if self.brain is None:
            return
        state = self._load_pattern_distillation_state()
        discovered_paths = self._discover_trade_report_candidates()
        pending_paths = [
            Path(item)
            for item in state.get("pending_report_paths", [])
            if isinstance(item, str) and item.strip()
        ]
        for discovered in discovered_paths:
            if discovered not in pending_paths:
                pending_paths.append(discovered)
        latest_known = self.latest_real_data_report.get("json_path")
        if latest_known:
            latest_path = Path(str(latest_known))
            if latest_path.exists() and latest_path not in pending_paths:
                pending_paths.append(latest_path)

        deduped_by_report_id: Dict[str, Path] = {}
        for report_path in sorted(
            pending_paths,
            key=lambda path: (
                path.stat().st_mtime if path.exists() else 0.0,
                str(path),
            ),
            reverse=True,
        ):
            if not report_path.exists():
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            report_id = str(payload.get("report_id") or report_path.stem)
            deduped_by_report_id.setdefault(report_id, report_path)
        pending_paths = list(deduped_by_report_id.values())[:20]

        remaining: list[str] = []
        batch_results: list[Dict[str, Any]] = []
        for report_path in pending_paths:
            if not report_path.exists():
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                remaining.append(str(report_path))
                continue
            report_id = str(payload.get("report_id") or report_path.stem)
            result = self._distill_trade_report_patterns(report_path, report_id)
            batch_results.append(
                {
                    "report_id": report_id,
                    "path": str(report_path),
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "wisdom_ids": result.get("distillation", {}).get("wisdom_ids", []),
                }
            )
            if result.get("status") != "generated":
                remaining.append(str(report_path))

        state["pending_report_paths"] = remaining[-200:]
        self._save_pattern_distillation_state(state)
        if batch_results:
            self._write_pattern_distillation_batch_report(batch_results)

    def queue_trade_report_for_distillation(self, report_json_path: Path) -> None:
        state = self._load_pattern_distillation_state()
        pending = [
            item
            for item in state.get("pending_report_paths", [])
            if isinstance(item, str)
        ]
        report_str = str(report_json_path)
        if report_str not in pending:
            pending.append(report_str)
        state["pending_report_paths"] = pending[-200:]
        self._save_pattern_distillation_state(state)

    def _discover_trade_report_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for path in sorted(REPORTS_DIR.glob("real_data_*_report.json")):
            if path.name == "pattern_distillation_daily_report.json":
                continue
            candidates.append(path)
        return candidates[-50:]

    def _write_pattern_distillation_report(self, payload: Dict[str, Any]) -> None:
        report_payload = {
            "report_type": "pattern_distillation_daily",
            "generated_at": datetime.now().isoformat(),
            "latest": payload,
            "log_path": str(self.pattern_distillation_log_path),
        }
        self.pattern_distillation_report_path.write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 蒸馏日报",
            "",
            f"- 生成时间：{report_payload['generated_at']}",
            f"- 报告 ID：{payload.get('report_id')}",
            f"- 模式候选数：{payload.get('candidate_count')}",
            f"- 记忆条目：{', '.join(str(item) for item in payload.get('memory_ids', [])) or '无'}",
            f"- Wisdom 节点：{', '.join(str(item) for item in payload.get('distillation', {}).get('wisdom_ids', [])) or '无'}",
            f"- 触发类型：{payload.get('distillation', {}).get('trigger_type', 'unknown')}",
            f"- 日志文件：{self.pattern_distillation_log_path}",
        ]
        self.pattern_distillation_report_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_pattern_distillation_batch_report(
        self, batch_results: list[Dict[str, Any]]
    ) -> None:
        payload = {
            "report_type": "pattern_distillation_batch",
            "generated_at": datetime.now().isoformat(),
            "batch_count": len(batch_results),
            "generated_count": sum(
                1 for item in batch_results if item.get("status") == "generated"
            ),
            "skipped_count": sum(
                1 for item in batch_results if item.get("status") == "skipped"
            ),
            "unique_report_count": len(
                {str(item.get("report_id") or "") for item in batch_results}
            ),
            "results": batch_results,
        }
        self.pattern_distillation_batch_report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 批次蒸馏日报",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 批次数量：{payload['batch_count']}",
            f"- 唯一报告数：{payload['unique_report_count']}",
            f"- 成功生成：{payload['generated_count']}",
            f"- 跳过数量：{payload['skipped_count']}",
            "",
            "## 结果",
        ]
        for item in batch_results:
            lines.append(
                f"- {item.get('report_id')} | status={item.get('status')} | wisdom={item.get('wisdom_ids', [])}"
            )
        self.pattern_distillation_batch_report_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _load_pattern_cluster_state(self) -> Dict[str, Any]:
        if not self.pattern_cluster_state_path.exists():
            return {"promoted_cluster_keys": []}
        try:
            payload = json.loads(
                self.pattern_cluster_state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {"promoted_cluster_keys": []}
        if not isinstance(payload.get("promoted_cluster_keys"), list):
            payload["promoted_cluster_keys"] = []
        return payload

    def _save_pattern_cluster_state(self, payload: Dict[str, Any]) -> None:
        self.pattern_cluster_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_latest_pattern_promotion(self) -> Dict[str, Any]:
        if not self.latest_pattern_promotion_path.exists():
            return {}
        try:
            return json.loads(
                self.latest_pattern_promotion_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_latest_pattern_promotion(self, payload: Dict[str, Any]) -> None:
        self.latest_pattern_promotion_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_pattern_anchor_manual_review(self) -> Dict[str, Any]:
        path = REPORTS_DIR / "pattern_anchor_manual_review.json"
        payload = self._load_json_or_empty(path)
        if not payload:
            return {"status": "missing", "approved_cluster_keys": [], "path": str(path)}
        payload.setdefault("status", "loaded")
        payload.setdefault("approved_cluster_keys", [])
        payload.setdefault("path", str(path))
        return payload

    def _write_pattern_anchor_review(self, promotion_payload: Dict[str, Any]) -> None:
        manual_review = self._load_pattern_anchor_manual_review()
        generated = promotion_payload.get("generated", []) or []
        review_items = [
            {
                "cluster_key": item.get("cluster_key"),
                "report_ids": item.get("report_ids", []),
                "wisdom_ids": item.get("wisdom_ids", []),
                "status": (
                    "approved"
                    if item.get("cluster_key")
                    in manual_review.get("approved_cluster_keys", [])
                    else "review_required"
                ),
            }
            for item in generated
        ]
        payload = {
            "generated_at": datetime.now().isoformat(),
            "status": "ready" if review_items else "idle",
            "manual_review": manual_review,
            "items": review_items,
        }
        self.pattern_anchor_review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 模式锚点评审",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 状态：{payload['status']}",
            f"- 人工复核：{manual_review.get('status')}",
        ]
        for item in review_items:
            lines.append(
                f"- {item['cluster_key']} | status={item['status']} | wisdom={item['wisdom_ids']}"
            )
        self.pattern_anchor_review_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _sync_pattern_anchor_registry(
        self, promotion_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        existing = self._load_json_or_empty(self.pattern_anchor_registry_path)
        manual_review = self._load_pattern_anchor_manual_review()
        cluster_state = self._load_pattern_cluster_state()
        registry_items = {
            str(item.get("cluster_key")): item
            for item in existing.get("items", [])
            if str(item.get("cluster_key") or "").strip()
        }
        approved = set(
            str(item) for item in manual_review.get("approved_cluster_keys", [])
        )
        promoted_keys = set(
            str(item) for item in cluster_state.get("promoted_cluster_keys", [])
        )
        grouped_from_logs: Dict[str, Dict[str, Any]] = {}
        if self.pattern_distillation_log_path.exists():
            try:
                rows = [
                    json.loads(line)
                    for line in self.pattern_distillation_log_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError):
                rows = []
            for row in rows[-200:]:
                distillation = row.get("distillation", {}) or {}
                trigger_type = str(distillation.get("trigger_type") or "").strip()
                category = str(
                    distillation.get("category") or "pattern_cluster"
                ).strip()
                if not trigger_type:
                    continue
                cluster_key = f"{category}:{trigger_type}"
                entry = grouped_from_logs.setdefault(
                    cluster_key,
                    {
                        "cluster_key": cluster_key,
                        "report_ids": set(),
                        "wisdom_ids": set(),
                        "memory_ids": set(),
                        "observation_count": 0,
                        "last_seen_at": None,
                    },
                )
                if row.get("report_id"):
                    entry["report_ids"].add(str(row.get("report_id")))
                for wisdom_id in distillation.get("wisdom_ids", []) or []:
                    if str(wisdom_id).isdigit():
                        entry["wisdom_ids"].add(int(wisdom_id))
                for memory_id in row.get("memory_ids", []) or []:
                    if str(memory_id).isdigit():
                        entry["memory_ids"].add(int(memory_id))
                entry["observation_count"] += 1
                last_seen_at = str(row.get("generated_at") or "").strip() or None
                if last_seen_at:
                    entry["last_seen_at"] = last_seen_at

        for cluster_key, entry in grouped_from_logs.items():
            current = registry_items.get(cluster_key, {})
            report_ids = sorted(
                set(current.get("report_ids", []) or []).union(entry["report_ids"])
            )
            wisdom_ids = sorted(
                set(current.get("wisdom_ids", []) or []).union(entry["wisdom_ids"])
            )
            memory_ids = sorted(
                set(current.get("memory_ids", []) or []).union(entry["memory_ids"])
            )
            if cluster_key in approved or cluster_key in promoted_keys:
                status = "stable_anchor"
            elif len(report_ids) >= 2 or len(wisdom_ids) >= 1:
                status = "review_pending"
            else:
                status = "deferred"
            registry_items[cluster_key] = {
                **current,
                "cluster_key": cluster_key,
                "report_ids": report_ids,
                "wisdom_ids": wisdom_ids,
                "memory_ids": memory_ids,
                "status": status,
                "anchored_at": current.get("anchored_at")
                or (datetime.now().isoformat() if status == "stable_anchor" else None),
                "observation_count": max(
                    int(current.get("observation_count") or 0),
                    int(entry.get("observation_count") or 0),
                ),
                "last_seen_at": entry.get("last_seen_at")
                or current.get("last_seen_at"),
            }

        for item in promotion_payload.get("generated", []) or []:
            cluster_key = str(item.get("cluster_key") or "")
            if not cluster_key:
                continue
            current = registry_items.get(cluster_key, {})
            report_ids = sorted(
                set(current.get("report_ids", []) or []).union(
                    item.get("report_ids", []) or []
                )
            )
            wisdom_ids = sorted(
                set(current.get("wisdom_ids", []) or []).union(
                    item.get("wisdom_ids", []) or []
                )
            )
            memory_ids = sorted(set(current.get("memory_ids", []) or []))
            status = (
                "stable_anchor"
                if cluster_key in approved or cluster_key in promoted_keys
                else "review_pending"
            )
            registry_items[cluster_key] = {
                **current,
                "cluster_key": cluster_key,
                "report_ids": report_ids,
                "wisdom_ids": wisdom_ids,
                "memory_ids": memory_ids,
                "status": status,
                "anchored_at": datetime.now().isoformat()
                if status == "stable_anchor"
                else None,
                "observation_count": max(int(current.get("observation_count") or 0), 1),
                "last_seen_at": datetime.now().isoformat(),
            }
        counts = {
            "stable_anchor": 0,
            "review_pending": 0,
            "deferred": 0,
        }
        for item in registry_items.values():
            item_status = str(item.get("status") or "deferred")
            if item_status not in counts:
                counts[item_status] = 0
            counts[item_status] += 1
        payload = {
            "generated_at": datetime.now().isoformat(),
            "status": "active" if registry_items else "idle",
            "counts": counts,
            "items": sorted(
                registry_items.values(), key=lambda item: item["cluster_key"]
            ),
        }
        self.pattern_anchor_registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 模式锚点注册表",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 状态：{payload['status']}",
            f"- 稳定锚点：{payload['counts'].get('stable_anchor', 0)}",
            f"- 待复核：{payload['counts'].get('review_pending', 0)}",
            f"- 暂不晋升：{payload['counts'].get('deferred', 0)}",
        ]
        for item in payload["items"]:
            lines.append(
                f"- {item['cluster_key']} | status={item['status']} | wisdom={item['wisdom_ids']} | reports={item.get('report_ids', [])}"
            )
        self.pattern_anchor_registry_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        return payload

    def _load_manifesto_draft_snapshot(self) -> Dict[str, Any]:
        if not self.manifesto_draft_snapshot_path.exists():
            return {}
        try:
            return json.loads(
                self.manifesto_draft_snapshot_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {}

    def _generate_manifesto_draft_snapshot(self) -> Dict[str, Any]:
        candidates = []
        for item in self.latest_pattern_promotion.get("generated", [])[:5]:
            candidates.append(
                {
                    "candidate_type": "pattern_promotion",
                    "cluster_key": item.get("cluster_key"),
                    "report_ids": item.get("report_ids", []),
                    "wisdom_ids": item.get("wisdom_ids", []),
                }
            )
        if (
            not candidates
            and self.latest_pattern_distillation.get("status") == "generated"
        ):
            candidates.append(
                {
                    "candidate_type": "pattern_distillation",
                    "report_id": self.latest_pattern_distillation.get("report_id"),
                    "wisdom_ids": self.latest_pattern_distillation.get(
                        "distillation", {}
                    ).get("wisdom_ids", []),
                }
            )
        payload = {
            "generated_at": datetime.now().isoformat(),
            "status": "generated" if candidates else "idle",
            "version_id": f"manifesto-draft-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "candidates": candidates,
            "draft": (
                "宣言草案建议：提高对跨报告重复风险法则、外部风险源命中和合规单轴突刺的优先级，"
                "在未来决策中优先复用已验证的风控路径。"
                if candidates
                else "当前暂无足够的新候选，维持现有宣言。"
            ),
        }
        self.manifesto_draft_snapshot_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.manifesto_draft_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._write_manifesto_draft_compare(payload)
        self._write_manifesto_review(payload)
        self._write_manifesto_approval_gate(payload)
        self._write_manifesto_rewrite_candidate(payload)
        md_lines = [
            "# 宣言草案快照",
            "",
            f"- 版本：{payload['version_id']}",
            f"- 生成时间：{payload['generated_at']}",
            f"- 状态：{payload['status']}",
            f"- 候选数：{len(candidates)}",
            f"- 草案：{payload['draft']}",
        ]
        self.manifesto_draft_snapshot_md_path.write_text(
            "\n".join(md_lines) + "\n", encoding="utf-8"
        )
        self._refresh_evolution_map(reason=f"manifesto_draft:{payload['version_id']}")
        return payload

    def _write_manifesto_draft_compare(self, latest_payload: Dict[str, Any]) -> None:
        history: list[Dict[str, Any]] = []
        if self.manifesto_draft_log_path.exists():
            try:
                history = [
                    json.loads(line)
                    for line in self.manifesto_draft_log_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError):
                history = []
        previous = history[-2] if len(history) >= 2 else {}
        payload = {
            "generated_at": datetime.now().isoformat(),
            "current_version": latest_payload.get("version_id"),
            "previous_version": previous.get("version_id"),
            "candidate_delta": len(latest_payload.get("candidates", []))
            - len(previous.get("candidates", [])),
            "draft_changed": latest_payload.get("draft") != previous.get("draft"),
            "current_status": latest_payload.get("status"),
            "previous_status": previous.get("status"),
            "draft_diff": self._build_manifesto_draft_diff(
                previous.get("draft"), latest_payload.get("draft")
            ),
        }
        self.manifesto_draft_compare_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 宣言草案版本比较",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 当前版本：{payload['current_version']}",
            f"- 上一版本：{payload['previous_version'] or '无'}",
            f"- 候选数变化：{payload['candidate_delta']}",
            f"- 草案是否变化：{payload['draft_changed']}",
            f"- 差异摘要：{payload['draft_diff']['summary']}",
        ]
        self.manifesto_draft_compare_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_manifesto_review(self, latest_payload: Dict[str, Any]) -> None:
        manual_review = self._load_manifesto_manual_review()
        payload = {
            "generated_at": datetime.now().isoformat(),
            "version_id": latest_payload.get("version_id"),
            "status": "review_required"
            if latest_payload.get("status") == "generated"
            else "idle",
            "manual_review": manual_review,
            "review_notes": [
                "核查草案是否过度提高对外部风险源命中的权重。",
                "核查草案是否改变既有合规与信用的平衡。",
                "确认草案仅作为快照候选，尚未反写正式宣言。",
            ],
        }
        self.manifesto_review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 宣言草案评审记录",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 版本：{payload['version_id']}",
            f"- 状态：{payload['status']}",
            f"- 人工复核：{payload['manual_review'].get('status', 'missing')}",
            "- 评审项：",
            *[f"  - {item}" for item in payload["review_notes"]],
        ]
        self.manifesto_review_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _load_manifesto_manual_review(self) -> Dict[str, Any]:
        path = REPORTS_DIR / "decision_manifesto_manual_review.json"
        md_path = REPORTS_DIR / "decision_manifesto_manual_review.md"
        payload = self._load_json_or_empty(path)
        if not payload:
            payload = {
                "status": "missing",
                "approved": False,
                "reviewer": "",
                "comment": "",
                "updated_at": None,
                "path": str(path),
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            md_lines = [
                "# 宣言人工复核输入",
                "",
                "- 说明：人工将 JSON 中 approved 改为 true 才会通过审批门。",
                f"- JSON 路径：{path}",
            ]
            md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
            return payload
        payload.setdefault("path", str(path))
        payload.setdefault("approved", False)
        payload.setdefault("status", "loaded")
        payload.setdefault("reviewer", "")
        payload.setdefault("comment", "")
        payload.setdefault("updated_at", None)
        return payload

    def _load_manifesto_writeback_authorization(self) -> Dict[str, Any]:
        payload = self._load_json_or_empty(self.manifesto_writeback_authorization_path)
        if not payload:
            payload = {
                "status": "missing",
                "approved": False,
                "version_id": "",
                "reviewer": "",
                "comment": "",
                "updated_at": None,
                "path": str(self.manifesto_writeback_authorization_path),
            }
            self.manifesto_writeback_authorization_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            md_lines = [
                "# 宣言正式回写授权输入",
                "",
                "- 说明：只有在 approved=true 且 version_id 与当前候选一致时，才允许正式覆盖 decision_manifesto.md。",
                f"- JSON 路径：{self.manifesto_writeback_authorization_path}",
            ]
            self.manifesto_writeback_authorization_md_path.write_text(
                "\n".join(md_lines) + "\n", encoding="utf-8"
            )
            return payload
        payload.setdefault("status", "loaded")
        payload.setdefault("approved", False)
        payload.setdefault("version_id", "")
        payload.setdefault("reviewer", "")
        payload.setdefault("comment", "")
        payload.setdefault("updated_at", None)
        payload.setdefault("path", str(self.manifesto_writeback_authorization_path))
        return payload

    def _load_manifesto_writeback_policy(self) -> Dict[str, Any]:
        payload = self._load_json_or_empty(self.manifesto_writeback_policy_path)
        if not payload:
            payload = {
                "status": "active",
                "decision": "drill_only",
                "formal_writeback_allowed": False,
                "reviewer": "system_default",
                "comment": "默认保持演练与正式落盘隔离，待后续人工制度升级。",
                "updated_at": datetime.now().isoformat(),
                "path": str(self.manifesto_writeback_policy_path),
                "allow_when": [
                    "人工明确改为 formal_allowed",
                    "候选版本通过 approval gate",
                    "正式授权文件 approved=true 且 version_id 匹配",
                ],
                "deny_when": [
                    "仅完成 happy-path drill 但未形成制度升级结论",
                    "授权文件缺失或版本不匹配",
                    "任何证据链字段缺失",
                ],
            }
            self.manifesto_writeback_policy_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            md_lines = [
                "# 宣言正式回写制度策略",
                "",
                f"- 当前决策：{payload['decision']}",
                f"- 正式落盘允许：{payload['formal_writeback_allowed']}",
                f"- 说明：{payload['comment']}",
                f"- JSON 路径：{self.manifesto_writeback_policy_path}",
            ]
            self.manifesto_writeback_policy_md_path.write_text(
                "\n".join(md_lines) + "\n", encoding="utf-8"
            )
            return payload
        payload.setdefault("status", "active")
        payload.setdefault("decision", "drill_only")
        payload.setdefault("formal_writeback_allowed", False)
        payload.setdefault("reviewer", "")
        payload.setdefault("comment", "")
        payload.setdefault("updated_at", None)
        payload.setdefault("path", str(self.manifesto_writeback_policy_path))
        payload.setdefault("allow_when", [])
        payload.setdefault("deny_when", [])
        return payload

    @staticmethod
    def _compute_manifesto_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _write_manifesto_formal_writeback_gate(
        self, rewrite_candidate_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        approval = self._load_json_or_empty(self.manifesto_approval_gate_path)
        manual_review = self._load_manifesto_manual_review()
        authorization = self._load_manifesto_writeback_authorization()
        policy = self._load_manifesto_writeback_policy()
        version_id = str(rewrite_candidate_payload.get("version_id") or "")
        authorization_matches = (
            authorization.get("approved")
            and str(authorization.get("version_id") or "") == version_id
        )
        policy_allows_formal_writeback = (
            bool(policy.get("formal_writeback_allowed"))
            and str(policy.get("decision") or "") == "formal_allowed"
        )
        rewrite_ready = approval.get("status") == "approved_for_rewrite"
        candidate_ready = (
            rewrite_candidate_payload.get("status") == "approved_candidate"
        )
        status = (
            "approved_for_formal_writeback"
            if rewrite_ready
            and candidate_ready
            and authorization_matches
            and policy_allows_formal_writeback
            else "policy_blocks_formal_writeback"
            if rewrite_ready and candidate_ready and authorization_matches
            else "awaiting_writeback_authorization"
            if rewrite_ready and candidate_ready
            else "awaiting_rewrite_conditions"
        )
        payload = {
            "generated_at": datetime.now().isoformat(),
            "version_id": version_id,
            "status": status,
            "approval_status": approval.get("status"),
            "manual_review": {
                "approved": manual_review.get("approved"),
                "reviewer": manual_review.get("reviewer"),
                "updated_at": manual_review.get("updated_at"),
            },
            "formal_authorization": {
                "approved": authorization.get("approved"),
                "version_id": authorization.get("version_id"),
                "reviewer": authorization.get("reviewer"),
                "updated_at": authorization.get("updated_at"),
            },
            "policy": {
                "decision": policy.get("decision"),
                "formal_writeback_allowed": policy.get("formal_writeback_allowed"),
                "reviewer": policy.get("reviewer"),
                "updated_at": policy.get("updated_at"),
            },
            "gate_breakdown": {
                "approved_candidate": candidate_ready,
                "rewrite_ready": rewrite_ready,
                "manual_review_passed": bool(manual_review.get("approved")),
                "formal_authorization_passed": bool(authorization.get("approved")),
                "authorization_version_matches": authorization_matches,
                "policy_allows_formal_writeback": policy_allows_formal_writeback,
            },
            "evidence_chain": [
                str(self.manifesto_approval_gate_path),
                str(REPORTS_DIR / "decision_manifesto_manual_review.json"),
                str(self.manifesto_rewrite_candidate_path),
                str(self.manifesto_rewrite_simulation_path),
                str(self.manifesto_writeback_authorization_path),
                str(self.manifesto_writeback_policy_path),
            ],
        }
        self.manifesto_writeback_gate_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        md_lines = [
            "# 宣言正式回写制度门",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 版本：{version_id}",
            f"- 状态：{status}",
            f"- 审批门：{payload['approval_status']}",
            f"- 人工复核通过：{payload['gate_breakdown']['manual_review_passed']}",
            f"- 正式授权通过：{payload['gate_breakdown']['formal_authorization_passed']}",
            f"- 版本匹配：{payload['gate_breakdown']['authorization_version_matches']}",
            f"- 制度决策：{payload['policy']['decision']}",
            f"- 制度允许正式回写：{payload['gate_breakdown']['policy_allows_formal_writeback']}",
        ]
        self.manifesto_writeback_gate_md_path.write_text(
            "\n".join(md_lines) + "\n", encoding="utf-8"
        )
        return payload

    @staticmethod
    def _build_manifesto_draft_diff(
        previous_draft: Any, current_draft: Any
    ) -> Dict[str, Any]:
        previous_text = str(previous_draft or "").strip()
        current_text = str(current_draft or "").strip()
        if not previous_text and current_text:
            return {"summary": "首次生成草案", "before": "", "after": current_text}
        if previous_text == current_text:
            return {
                "summary": "草案内容无变化",
                "before": previous_text,
                "after": current_text,
            }
        return {
            "summary": "草案内容已变化",
            "before": previous_text,
            "after": current_text,
        }

    def _write_manifesto_approval_gate(self, latest_payload: Dict[str, Any]) -> None:
        candidate_count = len(latest_payload.get("candidates", []))
        external_hits = self._count_external_risk_hits()
        review_ready = latest_payload.get("status") == "generated"
        manual_review_required = True
        manual_review_passed = bool(
            self._load_manifesto_manual_review().get("approved", False)
        )
        candidate_ready = candidate_count >= 1
        rewrite_ready = candidate_count >= 2 and external_hits > 0
        approved = candidate_ready and review_ready and manual_review_passed
        payload = {
            "generated_at": datetime.now().isoformat(),
            "version_id": latest_payload.get("version_id"),
            "status": (
                "approved_for_rewrite"
                if approved and rewrite_ready
                else "approved_candidate"
                if approved
                else "pending_review"
            ),
            "approval_breakdown": {
                "candidate_ready": candidate_ready,
                "external_risk_ready": external_hits > 0,
                "review_ready": review_ready,
                "manual_review_required": manual_review_required,
                "manual_review_passed": manual_review_passed,
                "rewrite_ready": rewrite_ready,
            },
            "approval_requirements": [
                "至少 2 个宣言候选输入",
                "至少 1 个外部风险命中事件",
                "人工复核通过后才能正式回写 decision_manifesto.md",
            ],
            "current_metrics": {
                "candidate_count": candidate_count,
                "external_risk_hits": external_hits,
            },
        }
        self.manifesto_approval_gate_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 宣言回写审批门",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 版本：{payload['version_id']}",
            f"- 状态：{payload['status']}",
            f"- 候选数：{payload['current_metrics']['candidate_count']}",
            f"- 外部风险命中：{payload['current_metrics']['external_risk_hits']}",
            f"- 人工复核通过：{payload['approval_breakdown']['manual_review_passed']}",
        ]
        self.manifesto_approval_gate_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_manifesto_rewrite_candidate(
        self, latest_payload: Dict[str, Any]
    ) -> None:
        approval = self._load_json_or_empty(self.manifesto_approval_gate_path)
        approved = approval.get("status") in {
            "approved_for_rewrite",
            "approved_candidate",
        }
        payload = {
            "generated_at": datetime.now().isoformat(),
            "version_id": latest_payload.get("version_id"),
            "status": "approved_candidate" if approved else "candidate_pool",
            "draft": latest_payload.get("draft"),
            "approval_status": approval.get("status"),
            "approval_summary": self._build_manifesto_approval_summary(approval),
            "candidate_queue": latest_payload.get("candidates", []),
            "approved_queue": latest_payload.get("candidates", []) if approved else [],
            "dry_run": {
                "status": "ready" if approved else "pending_approval",
                "target_path": str(self.repo_root / "decision_manifesto.md"),
                "summary": "仅生成正式回写候选，不直接覆盖正式宣言。",
                "diff": self._build_manifesto_draft_diff(
                    self._read_current_manifesto_text(), latest_payload.get("draft")
                ),
            },
        }
        self.manifesto_rewrite_candidate_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 宣言正式回写候选流",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 版本：{payload['version_id']}",
            f"- 状态：{payload['status']}",
            f"- 审批门状态：{payload['approval_status']}",
            f"- 候选池数量：{len(payload['candidate_queue'])}",
            f"- 获批池数量：{len(payload['approved_queue'])}",
            f"- 审批摘要：{payload['approval_summary']}",
            f"- Dry-run：{payload['dry_run']['status']}",
            f"- Dry-run 差异：{payload['dry_run']['diff']['summary']}",
        ]
        self.manifesto_rewrite_candidate_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        self._write_manifesto_rewrite_simulation(payload)
        self._write_manifesto_controlled_rewrite(payload)

    def _write_manifesto_rewrite_simulation(
        self, rewrite_candidate_payload: Dict[str, Any]
    ) -> None:
        approved = rewrite_candidate_payload.get("status") == "approved_candidate"
        formal_gate = self._write_manifesto_formal_writeback_gate(
            rewrite_candidate_payload
        )
        payload = {
            "generated_at": datetime.now().isoformat(),
            "version_id": rewrite_candidate_payload.get("version_id"),
            "status": "simulated" if approved else "blocked",
            "target_path": str(self.repo_root / "decision_manifesto.md"),
            "rewrite_mode": "dry_run_only",
            "approval_status": rewrite_candidate_payload.get("approval_status"),
            "formal_writeback_gate_status": formal_gate.get("status"),
            "candidate_count": len(rewrite_candidate_payload.get("approved_queue", [])),
            "draft_preview": str(rewrite_candidate_payload.get("draft") or "")[:240],
            "diff": rewrite_candidate_payload.get("dry_run", {}).get("diff", {}),
        }
        self.manifesto_rewrite_simulation_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 宣言正式回写执行模拟",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 版本：{payload['version_id']}",
            f"- 状态：{payload['status']}",
            f"- 审批状态：{payload['approval_status']}",
            f"- 正式回写门：{payload['formal_writeback_gate_status']}",
            f"- 候选数：{payload['candidate_count']}",
            f"- 目标文件：{payload['target_path']}",
            f"- 差异摘要：{payload['diff'].get('summary', '')}",
        ]
        self.manifesto_rewrite_simulation_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_manifesto_controlled_rewrite(
        self, rewrite_candidate_payload: Dict[str, Any]
    ) -> None:
        approved = rewrite_candidate_payload.get("status") == "approved_candidate"
        formal_gate = self._load_json_or_empty(self.manifesto_writeback_gate_path)
        target_path = self.repo_root / "decision_manifesto.md"
        current_text = self._read_current_manifesto_text()
        payload = {
            "generated_at": datetime.now().isoformat(),
            "version_id": rewrite_candidate_payload.get("version_id"),
            "status": "executed_shadow_write" if approved else "blocked",
            "target_path": str(target_path),
            "shadow_output_path": str(
                REPORTS_DIR / "decision_manifesto_controlled_rewrite_applied.md"
            ),
            "approval_status": rewrite_candidate_payload.get("approval_status"),
            "formal_writeback_gate_status": formal_gate.get("status"),
            "candidate_count": len(rewrite_candidate_payload.get("approved_queue", [])),
            "formal_writeback_applied": False,
            "backup_path": "",
            "digest_before": self._compute_manifesto_digest(current_text),
            "digest_after": self._compute_manifesto_digest(current_text),
        }
        if approved:
            shadow_path = (
                REPORTS_DIR / "decision_manifesto_controlled_rewrite_applied.md"
            )
            shadow_path.write_text(
                str(rewrite_candidate_payload.get("draft") or ""), encoding="utf-8"
            )
        if formal_gate.get("status") == "approved_for_formal_writeback":
            draft_text = str(rewrite_candidate_payload.get("draft") or "")
            backup_path = (
                REPORTS_DIR
                / f"decision_manifesto_formal_backup_{rewrite_candidate_payload.get('version_id')}.md"
            )
            backup_path.write_text(current_text, encoding="utf-8")
            target_path.write_text(draft_text, encoding="utf-8")
            payload["status"] = "applied_formal_writeback"
            payload["formal_writeback_applied"] = True
            payload["backup_path"] = str(backup_path)
            payload["digest_after"] = self._compute_manifesto_digest(draft_text)
        self.manifesto_controlled_rewrite_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# 宣言正式回写受控执行",
            "",
            f"- 生成时间：{payload['generated_at']}",
            f"- 版本：{payload['version_id']}",
            f"- 状态：{payload['status']}",
            f"- 审批状态：{payload['approval_status']}",
            f"- 正式回写门：{payload['formal_writeback_gate_status']}",
            f"- 候选数：{payload['candidate_count']}",
            f"- 目标文件：{payload['target_path']}",
            f"- 受控输出：{payload['shadow_output_path']}",
            f"- 正式回写：{payload['formal_writeback_applied']}",
            f"- 备份文件：{payload['backup_path'] or '无'}",
        ]
        self.manifesto_controlled_rewrite_md_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _read_current_manifesto_text(self) -> str:
        path = self.repo_root / "decision_manifesto.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _build_manifesto_approval_summary(approval: Dict[str, Any]) -> str:
        breakdown = approval.get("approval_breakdown", {}) or {}
        if approval.get("status") == "approved_for_rewrite":
            return "候选、外部风险、评审与人工复核均已通过"
        if approval.get("status") == "approved_candidate":
            return "候选、评审与人工复核已通过，等待进入正式回写条件"
        waiting = [
            key
            for key, value in breakdown.items()
            if key.endswith("_ready") or key.endswith("_passed")
            if not value
        ]
        return "待满足：" + ("、".join(waiting) if waiting else "审批条件")

    def _write_runtime_timeline(self) -> None:
        runtime_health_snapshot = self._build_runtime_health_snapshot()
        runtime_governance = self._load_json_or_empty(
            self.runtime_governance_event_path
        )
        runtime_health_event = self._load_json_or_empty(self.runtime_health_event_path)
        payload = {
            "generated_at": datetime.now().isoformat(),
            "events": [
                {
                    "type": "heartbeat",
                    "timestamp": datetime.now().isoformat(),
                    "phase": self.current_phase,
                    "stable_phase": self.stable_phase,
                    "stable_running_seconds": self._calculate_stable_running_seconds(),
                    "heartbeat_count": self.life_loop.heartbeat_count
                    if self.life_loop
                    else 0,
                },
                {
                    "type": "pause_control",
                    "status": self.pause_state.get("status"),
                    "reason": self.pause_state.get("reason"),
                    "protocol": self._build_pause_control_protocol(),
                },
                {
                    "type": "recovery_control",
                    "attempts": self.recovery_attempts,
                    "max_attempts": self.recovery_max_attempts,
                    "cooldown_seconds": self.recovery_cooldown_seconds,
                    "backoff_seconds": self.last_recovery_backoff_seconds,
                },
                {
                    "type": "recovery_event",
                    "status": self.last_recovery.get("status"),
                    "failure_class": self.last_recovery.get("failure_class"),
                    "recovery_tier": self.last_recovery.get("recovery_tier"),
                    "degrade_reason": self.last_recovery.get("degrade_reason"),
                },
                {
                    "type": "gate_report",
                    "report_id": self.latest_gate_report.get("report_id"),
                },
                {
                    "type": "real_report",
                    "report_id": self.latest_real_data_report.get("report_id"),
                },
                {
                    "type": "pattern_distillation",
                    "report_id": self.latest_pattern_distillation.get("report_id"),
                    "status": self.latest_pattern_distillation.get("status"),
                },
                {
                    "type": "pattern_promotion",
                    "status": self.latest_pattern_promotion.get("status"),
                    "generated_count": len(
                        self.latest_pattern_promotion.get("generated", [])
                    ),
                },
                {
                    "type": "external_risk_event",
                    "hit_count": self._count_external_risk_hits(),
                    "details": self._collect_external_risk_hit_details(),
                    "aggregations": self._load_json_or_empty(
                        self.external_risk_hit_event_path
                    ).get("aggregations", {}),
                },
                {
                    "type": "runtime_health",
                    "status": runtime_health_snapshot.get("status"),
                    "health_grade": runtime_health_snapshot.get("health_grade"),
                    "long_run_state": runtime_health_snapshot.get("long_run_state"),
                    "abnormal_windows": runtime_health_snapshot.get("abnormal_windows"),
                    "pressure_alerts": runtime_health_snapshot.get(
                        "pressure_alerts", []
                    ),
                    "pause_state": self.pause_state,
                },
                {
                    "type": "runtime_health_event",
                    "timestamp": runtime_health_event.get("generated_at"),
                    "status": runtime_health_event.get("snapshot", {}).get("status"),
                    "long_run_state": runtime_health_event.get("snapshot", {}).get(
                        "long_run_state"
                    ),
                },
                {
                    "type": "runtime_governance",
                    "timestamp": runtime_governance.get("generated_at"),
                    "status": runtime_governance.get("status"),
                    "action_names": runtime_governance.get("action_names", []),
                },
                {
                    "type": "directory_pressure",
                    "directory_pressure": runtime_health_snapshot.get(
                        "directory_pressure", {}
                    ),
                    "policy": runtime_health_snapshot.get(
                        "directory_pressure_policy", {}
                    ),
                },
                {
                    "type": "manifesto_approval_gate",
                    "status": self._load_json_or_empty(
                        self.manifesto_approval_gate_path
                    ).get("status"),
                },
                {
                    "type": "manifesto_formal_writeback_gate",
                    "status": self._load_json_or_empty(
                        self.manifesto_writeback_gate_path
                    ).get("status"),
                },
                {
                    "type": "manifesto_rewrite_candidate",
                    "status": self._load_json_or_empty(
                        self.manifesto_rewrite_candidate_path
                    ).get("status"),
                    "approval_summary": self._load_json_or_empty(
                        self.manifesto_rewrite_candidate_path
                    ).get("approval_summary"),
                },
                {
                    "type": "manifesto_rewrite_simulation",
                    "status": self._load_json_or_empty(
                        self.manifesto_rewrite_simulation_path
                    ).get("status"),
                },
                {
                    "type": "manifesto_controlled_rewrite",
                    "status": self._load_json_or_empty(
                        self.manifesto_controlled_rewrite_path
                    ).get("status"),
                    "formal_writeback_applied": self._load_json_or_empty(
                        self.manifesto_controlled_rewrite_path
                    ).get("formal_writeback_applied"),
                },
            ],
        }
        self.runtime_timeline_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _load_json_or_empty(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _count_external_risk_hits(self) -> int:
        report_path = self.latest_real_data_report.get("json_path")
        if not report_path:
            return 0
        try:
            payload = json.loads(Path(str(report_path)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        try:
            catalog = json.loads(
                (
                    Path(settings.BASE_DIR) / "config" / "external_risk_catalog.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return 0
        keywords = [
            str(keyword).lower()
            for source in catalog.get("sources", []) or []
            for rule in source.get("rules", []) or []
            for keyword in rule.get("keywords", []) or []
        ]
        hits = 0
        for warning in payload.get("warnings", []) or []:
            text = f"{warning.get('scenario_name', '')} {warning.get('decision_reason', '')}".lower()
            if any(keyword in text for keyword in keywords):
                hits += 1
        return hits

    @staticmethod
    def _load_external_risk_catalog() -> Dict[str, Any]:
        config_dir = Path(settings.BASE_DIR) / "config"
        sources = []
        for path in sorted(config_dir.glob("external_risk_catalog*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for source in payload.get("sources", []) or []:
                merged = dict(source)
                merged["catalog_file"] = path.name
                sources.append(merged)
        cache_path = (
            Path(settings.BASE_DIR)
            / "data"
            / "cache"
            / "external_risk_runtime_cache.json"
        )
        try:
            cache_payload = (
                json.loads(cache_path.read_text(encoding="utf-8"))
                if cache_path.exists()
                else {}
            )
        except (OSError, json.JSONDecodeError):
            cache_payload = {}
        for source in cache_payload.get("sources", []) or []:
            merged = dict(source)
            merged["catalog_file"] = str(
                cache_payload.get("cache_source") or cache_path.name
            )
            merged["cache_mode"] = "runtime_cache"
            sources.append(merged)
        return {
            "version": "aggregated-runtime-catalog",
            "sources": sources,
        }

    def _collect_external_risk_hit_details(self) -> list[Dict[str, Any]]:
        report_path = self.latest_real_data_report.get("json_path")
        if not report_path:
            return []
        try:
            payload = json.loads(Path(str(report_path)).read_text(encoding="utf-8"))
            catalog = self._load_external_risk_catalog()
        except (OSError, json.JSONDecodeError):
            return []
        details: list[Dict[str, Any]] = []
        for warning in payload.get("warnings", []) or []:
            text = f"{warning.get('scenario_name', '')} {warning.get('decision_reason', '')}".lower()
            for source in catalog.get("sources", []) or []:
                for rule in source.get("rules", []) or []:
                    keywords = [
                        str(keyword).lower()
                        for keyword in rule.get("keywords", []) or []
                    ]
                    if any(keyword in text for keyword in keywords):
                        details.append(
                            {
                                "sample_id": warning.get("sample_id"),
                                "report_id": payload.get("report_id"),
                                "source_id": source.get("source_id"),
                                "rule_id": rule.get("rule_id"),
                                "risk_note": rule.get("risk_note"),
                                "external_conflict_level": warning.get(
                                    "external_conflict_level"
                                ),
                                "external_resolution_advice": warning.get(
                                    "external_resolution_advice"
                                ),
                            }
                        )
                        break
        return details[:10]

    def _record_external_risk_hit_event(self) -> list[Dict[str, Any]]:
        details = self._collect_external_risk_hit_details()
        by_source: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}
        by_sample: Dict[str, list[str]] = {}
        source_priority = self._build_external_source_priority()
        source_confidence: Dict[str, float] = {}
        for item in details:
            source_id = str(item.get("source_id") or "unknown")
            rule_id = str(item.get("rule_id") or "unknown")
            sample_id = str(item.get("sample_id") or "unknown")
            by_source[source_id] = by_source.get(source_id, 0) + 1
            by_rule[rule_id] = by_rule.get(rule_id, 0) + 1
            by_sample.setdefault(sample_id, []).append(rule_id)
            source_confidence[source_id] = max(
                source_confidence.get(source_id, 0.0),
                float(source_priority.get(source_id, {}).get("confidence_score", 0.5)),
            )
        conflict_summary = self._build_external_conflict_summary(
            by_source=by_source,
            source_priority=source_priority,
            source_confidence=source_confidence,
        )
        payload = {
            "generated_at": datetime.now().isoformat(),
            "status": "detected" if details else "no_hit",
            "hit_count": len(details),
            "latest_report_id": self.latest_real_data_report.get("report_id"),
            "details": details,
            "aggregations": {
                "by_source": by_source,
                "by_rule": by_rule,
                "by_sample": by_sample,
                "source_priority": source_priority,
                "source_confidence": source_confidence,
                "conflict_summary": conflict_summary,
            },
        }
        self.external_risk_hit_event_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return details

    def _write_runtime_external_risk_audit(
        self, cache_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        cache_payload = cache_result or self._load_json_or_empty(
            self.external_risk_cache_path
        )
        hit_payload = self._load_json_or_empty(self.external_risk_hit_event_path)
        remote_summary = cache_payload.get("remote_summary", {}) or {}
        fallback_sources = []
        for source_id, summary in remote_summary.items():
            fallback_cache = int(summary.get("fallback_cache") or 0)
            fallback_local = int(summary.get("fallback_local") or 0)
            if fallback_cache or fallback_local:
                fallback_sources.append(
                    {
                        "source_id": source_id,
                        "fallback_cache": fallback_cache,
                        "fallback_local": fallback_local,
                        "selected_remote_rank": summary.get("selected_remote_rank"),
                    }
                )
        conflict_summary = (
            hit_payload.get("aggregations", {}).get("conflict_summary", {}) or {}
        )
        ordered_sources = conflict_summary.get("ordered_sources", []) or []
        status = (
            "watch"
            if fallback_sources or len(ordered_sources) > 1
            else "healthy"
            if cache_payload or hit_payload
            else "idle"
        )
        action_recommendations = []
        if fallback_sources:
            action_recommendations.append("prefer_runtime_cache_until_remote_recovers")
        if len(ordered_sources) > 1:
            action_recommendations.append(
                conflict_summary.get("resolution_advice")
                or "manual_review_with_priority_context"
            )
        executive_summary = self._build_external_risk_audit_summary(
            fallback_sources=fallback_sources,
            conflict_summary=conflict_summary,
            action_recommendations=action_recommendations,
        )
        payload = {
            "generated_at": datetime.now().isoformat(),
            "status": status,
            "fallback_sources": fallback_sources,
            "conflict_summary": conflict_summary,
            "source_priority": cache_payload.get("source_priority", {}),
            "action_recommendations": action_recommendations,
            "executive_summary": executive_summary,
        }
        self.runtime_external_risk_audit_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload

    @staticmethod
    def _build_external_risk_audit_summary(
        *,
        fallback_sources: list[Dict[str, Any]],
        conflict_summary: Dict[str, Any],
        action_recommendations: list[str],
    ) -> Dict[str, Any]:
        ordered_sources = list(conflict_summary.get("ordered_sources", []) or [])
        selected_source = str(conflict_summary.get("top_source") or "").strip() or None
        suppressed_sources = [
            source_id
            for source_id in ordered_sources
            if source_id and source_id != selected_source
        ]
        resolution_basis = str(
            conflict_summary.get("resolution_basis") or ""
        ).strip() or (
            "prefer_runtime_cache_until_remote_recovers"
            if fallback_sources
            else "no_fallback_no_conflict"
        )
        if not fallback_sources and not ordered_sources:
            human_status = "本轮无 fallback / 无冲突。"
        elif fallback_sources and not ordered_sources:
            human_status = f"本轮发生 fallback，当前优先复用 {fallback_sources[0].get('source_id') or 'runtime_cache'} 的运行缓存。"
        elif selected_source:
            suppressed_text = (
                ", ".join(suppressed_sources) if suppressed_sources else "无"
            )
            human_status = (
                f"本轮选用 {selected_source} 作为主来源；压制来源：{suppressed_text}；"
                f"依据：{resolution_basis}。"
            )
        else:
            human_status = "本轮存在外部风险审计事件，但尚未形成明确主来源。"

        audit_completeness = {
            "has_selected_source": bool(selected_source),
            "has_suppressed_sources": bool(suppressed_sources),
            "has_resolution_basis": bool(resolution_basis),
            "has_action_recommendations": bool(action_recommendations),
            "zero_event_explicit": not fallback_sources and not ordered_sources,
        }
        return {
            "selected_source": selected_source,
            "suppressed_sources": suppressed_sources,
            "resolution_basis": resolution_basis,
            "human_status": human_status,
            "recommended_action": action_recommendations[0]
            if action_recommendations
            else "no_action",
            "audit_completeness": audit_completeness,
        }

    def _calculate_stable_running_seconds(self) -> int:
        if self.stable_window_started_at is None:
            return 0
        return max(
            0, int((datetime.now() - self.stable_window_started_at).total_seconds())
        )

    def _sync_external_risk_runtime_cache(self) -> Dict[str, Any]:
        self.external_risk_cache_path.parent.mkdir(parents=True, exist_ok=True)
        previous_cache_sources = self._load_existing_external_risk_cache_sources()
        sources = []
        remote_status = []
        remote_summary: Dict[str, Dict[str, Any]] = {}
        config_dir = Path(settings.BASE_DIR) / "config"
        for path in sorted(config_dir.glob("external_risk_catalog*.json")):
            payload = self._load_json_or_empty(path)
            if not payload:
                continue
            for source in payload.get("sources", []) or []:
                merged = dict(source)
                merged["catalog_file"] = path.name
                source_id = str(merged.get("source_id") or path.stem)
                remote_urls = []
                if source.get("remote_url"):
                    remote_urls.append(str(source.get("remote_url")))
                remote_urls.extend(
                    [
                        str(item)
                        for item in source.get("remote_urls", []) or []
                        if str(item)
                    ]
                )
                merged["remote_urls"] = remote_urls
                merged["priority"] = int(source.get("priority") or len(sources) + 1)
                merged["fallback_policy"] = str(
                    source.get("fallback_policy") or "prefer_runtime_cache"
                )
                fetched = False
                selected_remote_rank: Optional[int] = None
                for remote_rank, remote_url in enumerate(remote_urls, start=1):
                    summary = remote_summary.setdefault(
                        source_id,
                        {
                            "attempts": 0,
                            "fetched": 0,
                            "fallback_cache": 0,
                            "fallback_local": 0,
                            "selected_remote_rank": None,
                        },
                    )
                    summary["attempts"] += 1
                    try:
                        with urlopen(str(remote_url), timeout=8) as response:
                            remote_payload = json.loads(response.read().decode("utf-8"))
                        if isinstance(remote_payload, dict):
                            merged.update(remote_payload)
                            merged["remote_url"] = remote_url
                            merged["source_mode"] = "remote_live"
                            merged["selected_remote_rank"] = remote_rank
                            merged["selected_remote_url"] = remote_url
                            remote_status.append(
                                {
                                    "source_id": merged.get("source_id"),
                                    "remote_url": remote_url,
                                    "status": "fetched",
                                    "remote_rank": remote_rank,
                                }
                            )
                            summary["fetched"] += 1
                            summary["selected_remote_rank"] = remote_rank
                            selected_remote_rank = remote_rank
                            fetched = True
                            break
                        remote_status.append(
                            {
                                "source_id": merged.get("source_id"),
                                "remote_url": remote_url,
                                "status": "invalid_payload",
                                "remote_rank": remote_rank,
                            }
                        )
                    except (OSError, json.JSONDecodeError, URLError):
                        remote_status.append(
                            {
                                "source_id": merged.get("source_id"),
                                "remote_url": remote_url,
                                "status": "unreachable",
                                "remote_rank": remote_rank,
                            }
                        )
                if remote_urls and not fetched:
                    cached_source = previous_cache_sources.get(source_id)
                    if (
                        cached_source
                        and merged.get("fallback_policy") == "prefer_runtime_cache"
                    ):
                        merged = self._merge_external_risk_source_with_cache(
                            source=merged,
                            cached_source=cached_source,
                        )
                        merged["source_mode"] = "remote_fallback_cache"
                        remote_status.append(
                            {
                                "source_id": merged.get("source_id"),
                                "remote_url": cached_source.get("selected_remote_url")
                                or cached_source.get("remote_url"),
                                "status": "fallback_cache",
                                "fallback_from": "runtime_cache",
                            }
                        )
                        remote_summary[source_id]["fallback_cache"] += 1
                    else:
                        merged["source_mode"] = "remote_fallback_local"
                        remote_status.append(
                            {
                                "source_id": merged.get("source_id"),
                                "remote_url": None,
                                "status": "fallback_local",
                                "fallback_from": "catalog_file",
                            }
                        )
                        remote_summary[source_id]["fallback_local"] += 1
                else:
                    merged["source_mode"] = merged.get("source_mode") or "local_mirror"
                if selected_remote_rank is not None:
                    merged["selected_remote_rank"] = selected_remote_rank
                sources.append(merged)
        source_priority = self._build_external_source_priority_from_sources(sources)
        cache_payload = {
            "generated_at": datetime.now().isoformat(),
            "status": (
                "refreshed_with_fallback"
                if any(
                    item.get("fallback_cache") or item.get("fallback_local")
                    for item in remote_summary.values()
                )
                else "refreshed"
            ),
            "source_count": len(sources),
            "remote_status": remote_status,
            "remote_summary": remote_summary,
            "source_priority": source_priority,
            "cache_source": "external_risk_runtime_cache",
            "sources": sources,
        }
        self.external_risk_cache_path.write_text(
            json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return cache_payload

    def _load_existing_external_risk_cache_sources(self) -> Dict[str, Dict[str, Any]]:
        payload = self._load_json_or_empty(self.external_risk_cache_path)
        sources = payload.get("sources", []) or []
        return {
            str(item.get("source_id") or f"cached-{index}"): dict(item)
            for index, item in enumerate(sources)
        }

    @staticmethod
    def _merge_external_risk_source_with_cache(
        *, source: Dict[str, Any], cached_source: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = dict(source)
        for key, value in cached_source.items():
            if key in {
                "source_id",
                "source_type",
                "provider",
                "catalog_file",
                "priority",
                "fallback_policy",
                "remote_urls",
            }:
                continue
            merged[key] = value
        merged["fallback_cache_used"] = True
        merged["selected_remote_url"] = cached_source.get(
            "selected_remote_url"
        ) or cached_source.get("remote_url")
        merged["selected_remote_rank"] = cached_source.get("selected_remote_rank")
        return merged

    def _build_external_source_priority(self) -> Dict[str, Dict[str, Any]]:
        sources = self._load_external_risk_catalog().get("sources", []) or []
        return self._build_external_source_priority_from_sources(sources)

    @staticmethod
    def _build_external_source_priority_from_sources(
        sources: list[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        priority_map: Dict[str, Dict[str, Any]] = {}
        for index, source in enumerate(sources):
            source_id = str(source.get("source_id") or f"source-{index}")
            confidence_raw = source.get("confidence", "medium")
            if isinstance(confidence_raw, (int, float)):
                confidence_score = float(confidence_raw)
                confidence = confidence_raw
            else:
                confidence = confidence_raw
                confidence_score = {"high": 0.9, "medium": 0.7, "low": 0.5}.get(
                    str(confidence).lower(), 0.6
                )
            priority_map[source_id] = {
                "rank": int(source.get("priority") or index + 1),
                "provider": source.get("provider"),
                "confidence": confidence,
                "confidence_score": confidence_score,
                "source_mode": source.get("source_mode"),
                "fallback_policy": source.get("fallback_policy"),
                "selected_remote_rank": source.get("selected_remote_rank"),
            }
        return priority_map

    @staticmethod
    def _build_external_conflict_summary(
        *,
        by_source: Dict[str, int],
        source_priority: Dict[str, Dict[str, Any]],
        source_confidence: Dict[str, float],
    ) -> Dict[str, Any]:
        ordered_sources = sorted(
            by_source.items(),
            key=lambda item: (
                source_priority.get(item[0], {}).get("rank", 999),
                -source_confidence.get(item[0], 0.0),
                -item[1],
            ),
        )
        top_source = ordered_sources[0][0] if ordered_sources else None
        return {
            "ordered_sources": [item[0] for item in ordered_sources],
            "top_source": top_source,
            "resolution_advice": "prefer_highest_priority_source"
            if len(ordered_sources) > 1
            else "single_source_or_no_hit",
        }

    def _promote_pattern_clusters_to_wisdom(self) -> Dict[str, Any]:
        if self.brain is None or not self.pattern_distillation_log_path.exists():
            return {"status": "skipped", "reason": "missing_prerequisite"}
        state = self._load_pattern_cluster_state()
        promoted_keys = set(
            str(item) for item in state.get("promoted_cluster_keys", [])
        )
        anchor_registry = self._load_json_or_empty(self.pattern_anchor_registry_path)
        anchored_keys = {
            str(item.get("cluster_key"))
            for item in anchor_registry.get("items", [])
            if item.get("status") == "anchored"
        }
        try:
            rows = [
                json.loads(line)
                for line in self.pattern_distillation_log_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            return {"status": "failed", "reason": "pattern_log_parse_error"}

        grouped: Dict[str, list[Dict[str, Any]]] = {}
        for item in rows[-50:]:
            distillation = item.get("distillation", {}) or {}
            cluster_key = f"{distillation.get('category', 'pattern_cluster')}:{distillation.get('trigger_type', 'unknown')}"
            grouped.setdefault(cluster_key, []).append(item)

        generated = []
        for cluster_key, items in grouped.items():
            if cluster_key in promoted_keys:
                continue
            report_ids = sorted(
                {
                    str(item.get("report_id") or "")
                    for item in items
                    if item.get("report_id")
                }
            )
            memory_ids = sorted(
                {
                    int(memory_id)
                    for item in items
                    for memory_id in item.get("memory_ids", [])
                    if str(memory_id).isdigit()
                }
            )
            if len(memory_ids) < 2:
                continue
            memories = []
            for memory_id in memory_ids:
                memory = self.brain.memory.db_manager.get_memory_by_id(memory_id)
                if memory is not None:
                    memories.append(memory)
            if len(memories) < 2:
                continue
            result = self.brain.memory.distill_memory(
                source_memories=memories,
                trigger_type="pattern_cluster_promoted",
                distillation_directives=[
                    "将多份真实报告的模式簇进一步凝练成更稳定的风险法则。",
                    "突出跨报告复用价值，不要重复单个样本细节。",
                ],
            )
            if result.get("created", 0) > 0 or result.get("wisdom_ids"):
                promoted_keys.add(cluster_key)
                generated.append(
                    {
                        "cluster_key": cluster_key,
                        "report_ids": report_ids,
                        "wisdom_ids": result.get("wisdom_ids", []),
                        "anchor_status": (
                            "anchored" if cluster_key in anchored_keys else "candidate"
                        ),
                    }
                )

        state["promoted_cluster_keys"] = sorted(promoted_keys)
        self._save_pattern_cluster_state(state)
        result = {
            "status": "generated" if generated else "skipped",
            "generated": generated,
            "generated_at": datetime.now().isoformat(),
        }
        self.latest_pattern_promotion = result
        self._save_latest_pattern_promotion(result)
        self._write_pattern_anchor_review(result)
        self._sync_pattern_anchor_registry(result)
        self._refresh_evolution_map(reason="pattern_promotion")
        return result

    @staticmethod
    def _should_finish_by_schedule(now: Optional[datetime] = None) -> bool:
        if CRUISE_END_AT is None:
            return False
        return (now or datetime.now()) >= CRUISE_END_AT


def read_map_baseline(map_path: Path) -> Dict[str, Any]:
    # 生产入口启动前先读取当前星图基线，便于确认实仓巡航与地图是否同步。
    if not map_path.exists():
        return {"generated_at": None, "wisdom_nodes": 0}
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    return {
        "generated_at": payload.get("generated_at"),
        "wisdom_nodes": len(payload.get("wisdom_nodes", [])),
    }


def capture_resource_snapshot(repo_root: Path) -> Dict[str, Any]:
    sensor = HostMachineSensor()
    reading = sensor.read()
    disk = shutil.disk_usage(repo_root)
    disk_total = max(disk.total, 1)
    return {
        "memory_percent": reading.details.get("memory_percent"),
        "cpu_percent": reading.details.get("cpu_percent"),
        "disk_percent": round((disk.used / disk_total) * 100.0, 3),
        "disk_free_gb": round(disk.free / (1024**3), 3),
        "health_score": reading.health_score,
    }


def collect_process_tree(root_pid: int) -> Dict[str, Any]:
    try:
        process = psutil.Process(root_pid)
    except psutil.Error:
        return {"root_pid": root_pid, "parent_pid": None, "child_pids": []}
    children = process.children(recursive=True)
    return {
        "root_pid": root_pid,
        "parent_pid": process.ppid(),
        "child_pids": [child.pid for child in children],
    }


async def _run(heartbeat_seconds: int = HEARTBEAT_SECONDS) -> None:
    runtime = ProductionRuntime(heartbeat_seconds=heartbeat_seconds)

    loop = asyncio.get_running_loop()

    def _handle_signal(*_: Any) -> None:
        runtime.running = False
        log.warning("📴 捕获停止信号，准备安全退出生产态主循环")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: _handle_signal())

    try:
        await runtime.initialize()
        baseline = read_map_baseline(runtime.repo_root / "evolution_map.json")
        log.info(
            "🛰️ 当前星图基线 | generated_at={} | wisdom_nodes={}",
            baseline["generated_at"],
            baseline["wisdom_nodes"],
        )
        await runtime.run_forever()
    finally:
        await runtime.shutdown()


def main() -> None:
    configure_logger(profile="mini_pc")
    heartbeat_seconds = int(
        os.environ.get("ABU_HEARTBEAT_SECONDS") or HEARTBEAT_SECONDS
    )
    asyncio.run(_run(heartbeat_seconds=heartbeat_seconds))


if __name__ == "__main__":
    main()
