"""统一行动账本。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import time
import uuid
from typing import Any, Deque, Dict, Optional

from config.settings import settings
from src.security import LogShredder
from src.utils.logger import log


ALLOWED_STATUSES = {"started", "success", "failed", "pending", "rejected", "skipped"}
ALLOWED_PRIORITIES = {"critical", "normal"}


@dataclass(frozen=True)
class EventContext:
    trace_id: str
    span_id: str
    parent_trace_id: str
    parent_span_id: str
    exchange_id: str
    node_seq: int
    lamport_seq: int
    node_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_trace_id": self.parent_trace_id,
            "parent_span_id": self.parent_span_id,
            "exchange_id": self.exchange_id,
            "node_seq": self.node_seq,
            "lamport_seq": self.lamport_seq,
            "node_id": self.node_id,
        }


class ActionJournal:
    """异步双通道行动账本。"""

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        node_id: str = "abu-prime-node-01",
        normal_queue_maxsize: int = 512,
        critical_queue_maxsize: int = 128,
        critical_merge_limit: int = 64,
        flush_interval: float = 0.5,
        max_batch_size: int = 50,
        max_string_chars: int = 4000,
        max_file_size_bytes: int = 50 * 1024 * 1024,
    ):
        self.path = (
            Path(path)
            if path
            else Path(settings.BASE_DIR) / "data" / "action_journal.jsonl"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.node_id = node_id
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self.max_string_chars = max_string_chars
        self.max_file_size_bytes = max(1024, int(max_file_size_bytes))
        self._shredder = LogShredder()
        self._normal_queue: queue.Queue[Dict[str, Any]] = queue.Queue(
            maxsize=normal_queue_maxsize
        )
        self._critical_queue: queue.Queue[Dict[str, Any]] = queue.Queue(
            maxsize=critical_queue_maxsize
        )
        self._critical_merge_limit = critical_merge_limit
        self._critical_merge: Dict[str, Dict[str, Any]] = {}
        self._critical_merge_order: Deque[str] = deque()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="action-journal-writer", daemon=True
        )
        self._node_seq = 0
        self._lamport_seq = 0
        self._dropped_counts = defaultdict(int)
        self._merged_counts = defaultdict(int)
        self._redacted_counts = 0
        self._last_error = ""
        self._high_watermark = 0
        self._last_drop_snapshot = 0
        self._writer_thread.start()

    def reserve_event_context(
        self,
        *,
        trace_id: Optional[str] = None,
        parent_trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        exchange_id: Optional[str] = None,
        remote_lamport_seq: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._state_lock:
            self._node_seq += 1
            remote = int(remote_lamport_seq or 0)
            self._lamport_seq = max(self._lamport_seq, remote) + 1
            context = EventContext(
                trace_id=trace_id or self._new_id("trace"),
                span_id=self._new_id("span"),
                parent_trace_id=parent_trace_id or "",
                parent_span_id=parent_span_id or "",
                exchange_id=exchange_id or "",
                node_seq=self._node_seq,
                lamport_seq=self._lamport_seq,
                node_id=self.node_id,
            )
            return context.to_dict()

    def new_exchange_id(self) -> str:
        return self._new_id("exchange")

    def log_event(
        self,
        *,
        component: str,
        stage: str,
        action: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
        reason: str = "",
        priority: str = "normal",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event_context = self._materialize_event_context(context)
        event = self._build_event(
            component=component,
            stage=stage,
            action=action,
            status=status,
            payload=payload or {},
            reason=reason,
            priority=priority,
            context=event_context,
        )
        self._enqueue_event(event)
        return event

    def get_health(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "path": str(self.path),
                "writer_alive": self._writer_thread.is_alive(),
                "normal_queue_size": self._normal_queue.qsize(),
                "critical_queue_size": self._critical_queue.qsize(),
                "critical_merge_size": len(self._critical_merge),
                "dropped_counts": dict(self._dropped_counts),
                "merged_counts": dict(self._merged_counts),
                "redacted_count": self._redacted_counts,
                "node_seq": self._node_seq,
                "lamport_seq": self._lamport_seq,
                "high_watermark": self._high_watermark,
                "last_error": self._last_error,
            }

    def close(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._writer_thread.join(timeout=timeout)

    def _build_event(
        self,
        *,
        component: str,
        stage: str,
        action: str,
        status: str,
        payload: Dict[str, Any],
        reason: str,
        priority: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"非法状态: {status}")
        if priority not in ALLOWED_PRIORITIES:
            raise ValueError(f"非法优先级: {priority}")

        sanitized_payload, redaction_count, truncated = self._sanitize_payload(payload)
        sanitized_reason = self._shredder.sanitize_text(reason or "")[:512]
        security = {
            "redacted": redaction_count > 0,
            "redaction_count": redaction_count,
            "truncated": truncated,
        }
        with self._state_lock:
            self._redacted_counts += redaction_count

        return {
            "timestamp": datetime.now().isoformat(),
            **context,
            "component": component,
            "stage": stage,
            "action": action,
            "status": status,
            "reason": sanitized_reason,
            "payload": sanitized_payload,
            "priority": priority,
            "security": security,
        }

    def _materialize_event_context(
        self, base_context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        seed = base_context or self.reserve_event_context()
        with self._state_lock:
            self._node_seq += 1
            remote = int(seed.get("lamport_seq") or 0)
            self._lamport_seq = max(self._lamport_seq, remote) + 1
            return {
                "trace_id": str(seed.get("trace_id") or self._new_id("trace")),
                "span_id": self._new_id("span"),
                "parent_trace_id": str(seed.get("parent_trace_id") or ""),
                "parent_span_id": str(
                    seed.get("span_id") or seed.get("parent_span_id") or ""
                ),
                "exchange_id": str(seed.get("exchange_id") or ""),
                "node_seq": self._node_seq,
                "lamport_seq": self._lamport_seq,
                "node_id": self.node_id,
            }

    def _sanitize_payload(self, payload: Any) -> tuple[Any, int, bool]:
        redaction_count = 0
        truncated = False

        def _walk(value: Any) -> Any:
            nonlocal redaction_count, truncated
            if isinstance(value, dict):
                return {str(key): _walk(item) for key, item in value.items()}
            if isinstance(value, list):
                return [_walk(item) for item in value]
            if isinstance(value, tuple):
                return [_walk(item) for item in value]
            if isinstance(value, str):
                raw = value
                if len(raw) > self.max_string_chars:
                    raw = raw[: self.max_string_chars] + "...[TRUNCATED]"
                    truncated = True
                sanitized = self._shredder.sanitize_text(raw)
                redaction_count += sanitized.count("[REDACTED]")
                return sanitized
            return value

        return _walk(payload), redaction_count, truncated

    def _enqueue_event(self, event: Dict[str, Any]) -> None:
        queue_obj = (
            self._critical_queue
            if event["priority"] == "critical"
            else self._normal_queue
        )
        try:
            queue_obj.put_nowait(event)
            self._update_high_watermark()
        except queue.Full:
            if event["priority"] == "critical":
                self._merge_or_drop_critical(event)
            else:
                self._record_drop(event, category="normal")

    def _merge_or_drop_critical(self, event: Dict[str, Any]) -> None:
        key = self._critical_merge_key(event)
        with self._state_lock:
            if key in self._critical_merge:
                self._critical_merge[key]["payload"]["count"] += 1
                self._critical_merge[key]["payload"]["last_timestamp"] = event[
                    "timestamp"
                ]
                self._merged_counts["critical"] += 1
                return
            if len(self._critical_merge_order) >= self._critical_merge_limit:
                self._record_drop_locked(event, category="critical")
                return
            merged_event = {
                **event,
                "action": f"{event['action']}.merged",
                "payload": {
                    "count": 1,
                    "sample_payload": event.get("payload", {}),
                    "last_timestamp": event["timestamp"],
                },
            }
            self._critical_merge[key] = merged_event
            self._critical_merge_order.append(key)
            self._merged_counts["critical"] += 1

    def _record_drop(self, event: Dict[str, Any], *, category: str) -> None:
        with self._state_lock:
            self._record_drop_locked(event, category=category)

    def _record_drop_locked(self, event: Dict[str, Any], *, category: str) -> None:
        key = f"{category}:{event['component']}:{event['stage']}:{event['action']}"
        self._dropped_counts[key] += 1

    def _writer_loop(self) -> None:
        while not self._stop_event.is_set() or self._has_pending_events():
            batch = self._collect_batch()
            if not batch:
                time.sleep(self.flush_interval)
                continue
            try:
                self._rotate_if_needed()
                with self.path.open("a", encoding="utf-8") as handle:
                    for event in batch:
                        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                    handle.flush()
                self._write_drop_summary_if_needed()
            except Exception as exc:
                with self._state_lock:
                    self._last_error = str(exc)
                log.warning("⚠️ 行动账本写入失败 | error={}", exc)
                time.sleep(self.flush_interval)

    def _collect_batch(self) -> list[Dict[str, Any]]:
        batch: list[Dict[str, Any]] = []
        while len(batch) < self.max_batch_size:
            event = self._dequeue_one()
            if event is None:
                break
            batch.append(event)
        return batch

    def _dequeue_one(self) -> Optional[Dict[str, Any]]:
        try:
            return self._critical_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            return self._normal_queue.get_nowait()
        except queue.Empty:
            pass
        with self._state_lock:
            if self._critical_merge_order:
                key = self._critical_merge_order.popleft()
                return self._critical_merge.pop(key, None)
        return None

    def _write_drop_summary_if_needed(self) -> None:
        with self._state_lock:
            total_dropped = sum(self._dropped_counts.values())
            if total_dropped == self._last_drop_snapshot or total_dropped == 0:
                return
            self._node_seq += 1
            self._lamport_seq += 1
            summary = {
                "timestamp": datetime.now().isoformat(),
                "trace_id": self._new_id("trace"),
                "span_id": self._new_id("span"),
                "parent_trace_id": "",
                "parent_span_id": "",
                "exchange_id": "",
                "node_seq": self._node_seq,
                "lamport_seq": self._lamport_seq,
                "node_id": self.node_id,
                "component": "ActionJournal",
                "stage": "observability",
                "action": "events_dropped",
                "status": "failed",
                "reason": "观测队列已发生丢弃",
                "payload": {
                    "dropped_counts": dict(self._dropped_counts),
                    "high_watermark": self._high_watermark,
                },
                "priority": "critical",
                "security": {
                    "redacted": False,
                    "redaction_count": 0,
                    "truncated": False,
                },
            }
            self._last_drop_snapshot = total_dropped
        try:
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
        except Exception as exc:
            with self._state_lock:
                self._last_error = str(exc)

    def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return
        try:
            current_size = self.path.stat().st_size
        except OSError as exc:
            with self._state_lock:
                self._last_error = str(exc)
            return
        if current_size < self.max_file_size_bytes:
            return
        rotated_path = self.path.with_name(
            f"{self.path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{self.path.suffix}"
        )
        if rotated_path.exists():
            rotated_path = self.path.with_name(
                f"{self.path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}{self.path.suffix}"
            )
        try:
            self.path.replace(rotated_path)
            log.info(
                "🗂️ 行动账本已自动归档 | active={} | archive={} | max_file_size_bytes={}",
                self.path,
                rotated_path,
                self.max_file_size_bytes,
            )
        except OSError as exc:
            with self._state_lock:
                self._last_error = str(exc)

    def _critical_merge_key(self, event: Dict[str, Any]) -> str:
        return "|".join(
            [
                str(event.get("trace_id", "")),
                str(event.get("component", "")),
                str(event.get("stage", "")),
                str(event.get("action", "")),
                str(event.get("status", "")),
            ]
        )

    def _update_high_watermark(self) -> None:
        current = (
            self._critical_queue.qsize()
            + self._normal_queue.qsize()
            + len(self._critical_merge)
        )
        with self._state_lock:
            self._high_watermark = max(self._high_watermark, current)

    def _has_pending_events(self) -> bool:
        return (
            not self._critical_queue.empty()
            or not self._normal_queue.empty()
            or bool(self._critical_merge)
        )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"


_DEFAULT_JOURNAL: Optional[ActionJournal] = None
_DEFAULT_LOCK = threading.Lock()


def get_action_journal(path: Optional[str] = None) -> ActionJournal:
    global _DEFAULT_JOURNAL
    with _DEFAULT_LOCK:
        if _DEFAULT_JOURNAL is None:
            _DEFAULT_JOURNAL = ActionJournal(path=path)
        return _DEFAULT_JOURNAL


def close_action_journal() -> None:
    global _DEFAULT_JOURNAL
    with _DEFAULT_LOCK:
        if _DEFAULT_JOURNAL is not None:
            _DEFAULT_JOURNAL.close()
            _DEFAULT_JOURNAL = None
