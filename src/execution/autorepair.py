"""M0X-AUTOREPAIR：ABU 自主修复实验记录与有限可变面管理。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTOREPAIR_RESULTS_PATH = REPO_ROOT / "data" / "reports" / "repair_results.jsonl"


@dataclass(frozen=True)
class RepairTask:
    task_id: str
    objective: str
    mutable_surfaces: list[str]
    success_metrics: list[str]
    created_at: str


@dataclass(frozen=True)
class RepairResult:
    task_id: str
    status: str
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    recorded_at: str = field(default_factory=lambda: datetime.now().isoformat())


class AutoRepairManager:
    def __init__(self, results_path: Path | None = None):
        self.results_path = results_path or AUTOREPAIR_RESULTS_PATH
        self.results_path.parent.mkdir(parents=True, exist_ok=True)

    def create_task(
        self,
        *,
        objective: str,
        mutable_surfaces: list[str],
        success_metrics: list[str],
    ) -> RepairTask:
        slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        return RepairTask(
            task_id=f"autorepair-{slug}",
            objective=objective,
            mutable_surfaces=mutable_surfaces,
            success_metrics=success_metrics,
            created_at=datetime.now().isoformat(),
        )

    def record_result(self, result: RepairResult) -> None:
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    def read_results(self) -> list[dict[str, Any]]:
        if not self.results_path.exists():
            return []
        rows = []
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


@dataclass(frozen=True)
class LoopStrategyState:
    stage: str
    objective: str
    current_focus: str
    stop_condition: str
    last_updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


LOOP_STRATEGY_PATH = REPO_ROOT / "data" / "reports" / "loop_strategy_state.json"


class LoopStrategyManager:
    def __init__(self, path: Path | None = None):
        self.path = path or LOOP_STRATEGY_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, state: LoopStrategyState) -> None:
        self.path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def default_mutable_surfaces() -> list[str]:
    return [
        "src/execution/site_onboarding.py",
        "src/execution/lead_capture.py",
        "src/execution/page_fetcher.py",
        "scripts/run_autonomous_site_onboarding.py",
        "scripts/sandbox_capture_trade_leads.py",
    ]
