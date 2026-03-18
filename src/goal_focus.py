"""目标管理与专注控制模块"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from config.constants import (
    GOAL_FOCUS_DEPLETION_PER_TASK,
    GOAL_FOCUS_MAX_PARALLEL,
    GOAL_FOCUS_MIN_LEVEL,
    GOAL_FOCUS_RECOVERY_PER_TICK,
    GOAL_PRIORITY_SWITCH_MARGIN,
    GOAL_SWITCH_COST_BASE_SECONDS,
    GOAL_SWITCH_COST_MAX_SECONDS,
    GOAL_SWITCH_COST_PER_CHANGE_SECONDS,
    GOAL_SWITCH_WINDOW_SECONDS,
)
from src.utils.helpers import clamp


@dataclass(frozen=True)
class GoalCandidate:
    """候选阶段目标。"""

    goal_id: str
    phase: str
    priority: float
    source: str = "unknown"


@dataclass(frozen=True)
class FocusSnapshot:
    """对外暴露的不可变专注快照。"""

    active_goal_id: Optional[str]
    active_phase: Optional[str]
    active_goal_priority: float
    background_task_count: int
    focus_level: float
    focus_depletion: float
    switch_count_recent: int
    switch_cost_delay_seconds: float
    goal_switch_blocked: bool
    goal_lock_reason: Optional[str]


class GoalFocusManager:
    """维护当前阶段目标、专注损耗与切换代价。"""

    def __init__(self, now_provider: Optional[Callable[[], datetime]] = None):
        self._now_provider = now_provider or datetime.now
        self._active_goal: Optional[GoalCandidate] = None
        self._background_candidates: dict[str, GoalCandidate] = {}
        self._focus_depletion = 0.0
        self._switch_timestamps: list[datetime] = []
        self._goal_switch_blocked = False
        self._goal_lock_reason: Optional[str] = None
        self._background_task_count = 0
        self._switch_cost_delay_seconds = 0.0
        self._focus_level = 1.0

    def _now(self) -> datetime:
        return self._now_provider()

    def _prune_switch_history(self, now: Optional[datetime] = None):
        current_time = now or self._now()
        window_start = current_time - timedelta(seconds=GOAL_SWITCH_WINDOW_SECONDS)
        self._switch_timestamps = [
            timestamp for timestamp in self._switch_timestamps if timestamp >= window_start
        ]

    def set_active_goal(self, candidate: GoalCandidate) -> bool:
        """尝试切换前台阶段目标。"""
        self._goal_switch_blocked = False
        self._goal_lock_reason = None
        self._background_candidates[candidate.goal_id] = candidate

        if self._active_goal is None:
            self._active_goal = candidate
            self._background_candidates.pop(candidate.goal_id, None)
            return True

        if candidate.goal_id == self._active_goal.goal_id:
            self._active_goal = candidate
            self._background_candidates.pop(candidate.goal_id, None)
            return True

        required_priority = self._active_goal.priority + GOAL_PRIORITY_SWITCH_MARGIN
        if candidate.priority < required_priority:
            self._goal_switch_blocked = True
            self._goal_lock_reason = "priority_gap_too_small"
            return False

        previous_goal = self._active_goal
        self._active_goal = candidate
        self._background_candidates.pop(candidate.goal_id, None)
        if previous_goal is not None:
            self._background_candidates[previous_goal.goal_id] = previous_goal
        self._switch_timestamps.append(self._now())
        self._prune_switch_history()
        return True

    def evaluate_tick(self, background_task_count: int) -> FocusSnapshot:
        """根据后台任务数更新专注损耗与切换代价。"""
        self._background_task_count = max(0, background_task_count)
        extra_tasks = max(0, self._background_task_count - GOAL_FOCUS_MAX_PARALLEL)
        self._focus_depletion = clamp(
            self._focus_depletion + (extra_tasks * GOAL_FOCUS_DEPLETION_PER_TASK) - GOAL_FOCUS_RECOVERY_PER_TICK,
            0.0,
            1.0 - GOAL_FOCUS_MIN_LEVEL,
        )
        self._focus_level = clamp(1.0 - self._focus_depletion, GOAL_FOCUS_MIN_LEVEL, 1.0)

        self._prune_switch_history()
        switch_count_recent = len(self._switch_timestamps)
        if switch_count_recent > 0:
            self._switch_cost_delay_seconds = min(
                GOAL_SWITCH_COST_MAX_SECONDS,
                GOAL_SWITCH_COST_BASE_SECONDS + switch_count_recent * GOAL_SWITCH_COST_PER_CHANGE_SECONDS,
            )
        else:
            self._switch_cost_delay_seconds = 0.0

        return self.export_snapshot()

    def get_decision_modifiers(self) -> dict[str, float]:
        """给其他认知链路提供轻量修正量。"""
        return {
            "task_complexity_delta": round(self._focus_depletion, 3),
            "focus_level": round(self._focus_level, 3),
        }

    def get_switch_delay_seconds(self) -> float:
        """获取当前切换代价延迟。"""
        return self._switch_cost_delay_seconds

    def export_snapshot(self) -> FocusSnapshot:
        """导出当前不可变快照。"""
        switch_count_recent = len(self._switch_timestamps)
        return FocusSnapshot(
            active_goal_id=self._active_goal.goal_id if self._active_goal else None,
            active_phase=self._active_goal.phase if self._active_goal else None,
            active_goal_priority=self._active_goal.priority if self._active_goal else 0.0,
            background_task_count=self._background_task_count,
            focus_level=round(self._focus_level, 3),
            focus_depletion=round(self._focus_depletion, 3),
            switch_count_recent=switch_count_recent,
            switch_cost_delay_seconds=round(self._switch_cost_delay_seconds, 3),
            goal_switch_blocked=self._goal_switch_blocked,
            goal_lock_reason=self._goal_lock_reason,
        )
