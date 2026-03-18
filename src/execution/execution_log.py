"""统一执行日志记录器：记录 ABU 与导师代理的执行事件。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_LOG_PATH = REPO_ROOT / "data" / "reports" / "execution_trace.jsonl"


@dataclass(frozen=True)
class ExecutionEvent:
    actor: str
    event_type: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now().isoformat())


class ExecutionLogger:
    def __init__(self, path: Path | None = None):
        self.path = path or EXECUTION_LOG_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: ExecutionEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def read(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]
