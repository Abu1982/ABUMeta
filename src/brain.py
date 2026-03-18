"""中枢大脑与全域快照模块"""

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import inspect

from src.chronos import ChronosEngine
from src.goal_focus import FocusSnapshot, GoalCandidate, GoalFocusManager
from src.memory import MemoryManager
from src.perception import PerceptionEngine
from src.psyche import PsycheEngine
from src.treasury import TransactionRecord, TreasuryManager
from src.utils.logger import log
from src.world_model import HistorySample, WorldModel, WorldModelSnapshot
from config.constants import KILL_SWITCH_SINGLE_TRANSACTION_PERCENT


FAILURE_MEMORY_KEYWORDS = (
    "failure",
    "failed",
    "失败",
    "熔断",
    "kill_switch",
    "insufficient_balance",
    "transaction_failed",
)


@dataclass(frozen=True)
class AgentState:
    """单轮认知周期的不可变全域快照。"""

    timestamp: datetime
    balance: float
    balance_ratio: float
    daily_loss_percent: float
    is_hunger_mode: bool
    anxiety: float
    dominant_emotion: str
    psychological_summary: str
    activity_level: float
    is_sleeping: bool
    time_of_day: str
    sleep_interval_bias: float
    last_event: Optional[str]
    failure_streak: int
    time_pressure: float
    task_complexity: float
    social_isolation_hours: float
    input_intensity: float
    host_resource_pressure: float
    perception_confidence: float
    active_goal_id: Optional[str]
    active_phase: Optional[str]
    focus_level: float
    focus_depletion: float
    background_task_count: int
    switch_cost_delay_seconds: float
    goal_switch_blocked: bool


class CentralBrain:
    """最小可运行的中枢编排器。"""

    def __init__(
        self,
        treasury: Optional[TreasuryManager] = None,
        psyche: Optional[PsycheEngine] = None,
        chronos: Optional[ChronosEngine] = None,
        memory: Optional[MemoryManager] = None,
        perception: Optional[PerceptionEngine] = None,
        goal_focus: Optional[GoalFocusManager] = None,
    ):
        self.treasury = treasury or TreasuryManager()
        self.psyche = psyche or PsycheEngine()
        self.chronos = chronos or ChronosEngine()
        self.memory = memory or MemoryManager()
        self.perception = perception or PerceptionEngine(
            self.treasury, self.chronos, self.memory
        )
        self.goal_focus = goal_focus or GoalFocusManager()
        self.world_model = WorldModel()
        self.state = self._build_state(last_event="bootstrap")
        log.info("🧠 CentralBrain 已初始化")

    def _capture_treasury_failure(self, last_event: Optional[str] = None):
        """将最近一次 Treasury 失败写入记忆，供焦虑链路读取。"""
        failure_reason = getattr(self.treasury, "last_failure_reason", None)
        if not failure_reason:
            return

        event = f"transaction_failed:{failure_reason}"
        existing = self.memory.db_manager.search_memories(
            query=event,
            limit=1,
            time_range=(datetime.now() - timedelta(minutes=1), datetime.now()),
        )
        if existing:
            return

        self.memory.create_memory(
            event=event,
            thought=f"last_event={last_event or 'unknown'}",
            lesson="最近一次资金动作失败，需要提高警觉",
            importance=0.8,
            source_type="system",
            verification_status="auto",
            raw_payload={"failure_reason": failure_reason},
        )
        log.info(
            f"🧠 记录失败记忆 | reason={failure_reason} | trigger={last_event or 'none'}"
        )

    def _get_failure_streak(self) -> int:
        """从近期失败记忆估算连续失败次数。"""
        now = datetime.now()
        recent_memories = self.memory.db_manager.get_recent_memories(hours=24, limit=50)
        if not recent_memories:
            return 0

        streak = 0
        matched = []
        for memory in recent_memories:
            searchable = " ".join(
                filter(None, [memory.event, memory.thought, memory.lesson])
            ).lower()
            if any(
                keyword.lower() in searchable for keyword in FAILURE_MEMORY_KEYWORDS
            ):
                matched.append(memory.event)
                streak += 1
            else:
                break

        if streak > 0:
            log.debug(f"🧠 检测到失败序列 | streak={streak} | matched={matched[:3]}")
        return streak

    def _calculate_time_pressure(self) -> float:
        """根据当前时间状态给出轻量时间压力估计。"""
        time_state = self.chronos.get_current_time_state()
        if time_state["is_sleeping"]:
            return 0.1
        if time_state["is_night"]:
            return 0.2
        return 0.15

    def _calculate_sleep_interval_bias(self, anxiety: float) -> float:
        """焦虑越高，下一次休眠间隔越短。"""
        bias = 1.15 - (anxiety * 0.7)
        return max(0.45, min(1.15, bias))

    def _get_background_task_count(self) -> int:
        """读取当前后台任务数。"""
        return len(self.chronos.background_task_manager.get_running_tasks())

    def _build_focus_snapshot(
        self, background_task_count: Optional[int] = None
    ) -> FocusSnapshot:
        """统一构造当前专注快照。"""
        return self.goal_focus.evaluate_tick(
            background_task_count=self._get_background_task_count()
            if background_task_count is None
            else background_task_count
        )

    def set_phase_goal(
        self, goal_id: str, phase: str, priority: float, source: str = "brain"
    ) -> bool:
        """设置前台阶段目标，并应用优先级门控。"""
        return self.goal_focus.set_active_goal(
            GoalCandidate(
                goal_id=goal_id, phase=phase, priority=priority, source=source
            )
        )

    def _build_state(self, last_event: Optional[str] = None) -> AgentState:
        treasury_stats = self.treasury.get_statistics()
        psyche_state = self.psyche.get_current_state()
        chronos_state = self.chronos.get_current_time_state()
        dominant_emotion, _ = self.psyche.psyche_state.emotions.get_dominant_emotion()
        sleep_bias = self.chronos.get_sleep_interval_bias()
        perception_state = (
            self.perception.current_state
            or self.perception.update_perception(last_event)
        )
        focus_snapshot = self._build_focus_snapshot()

        return AgentState(
            timestamp=datetime.now(),
            balance=treasury_stats["total_balance"],
            balance_ratio=treasury_stats["balance_ratio"],
            daily_loss_percent=treasury_stats["daily_loss_percent"],
            is_hunger_mode=treasury_stats["hunger_mode"],
            anxiety=psyche_state["anxiety"],
            dominant_emotion=dominant_emotion,
            psychological_summary=self.psyche.get_psychological_summary(),
            activity_level=chronos_state["activity_level"],
            is_sleeping=chronos_state["is_sleeping"],
            time_of_day=chronos_state["time_of_day"],
            sleep_interval_bias=sleep_bias,
            last_event=last_event,
            failure_streak=perception_state.failure_streak,
            time_pressure=perception_state.time_pressure,
            task_complexity=perception_state.task_complexity,
            social_isolation_hours=perception_state.social_isolation_hours,
            input_intensity=perception_state.input_intensity,
            host_resource_pressure=perception_state.host_resource_pressure,
            perception_confidence=perception_state.confidence,
            active_goal_id=focus_snapshot.active_goal_id,
            active_phase=focus_snapshot.active_phase,
            focus_level=focus_snapshot.focus_level,
            focus_depletion=focus_snapshot.focus_depletion,
            background_task_count=focus_snapshot.background_task_count,
            switch_cost_delay_seconds=focus_snapshot.switch_cost_delay_seconds,
            goal_switch_blocked=focus_snapshot.goal_switch_blocked,
        )

    def _build_world_model_snapshot(self) -> WorldModelSnapshot:
        """由中枢统一组装给世界模型消费的只读快照。"""
        history_samples = tuple(
            self._transaction_to_history_sample(record)
            for record in self.treasury.get_transaction_history(days=30)
            if record.category == "risk" and record.sub_category
        )
        return WorldModelSnapshot(
            agent_state=self.state,
            history_samples=history_samples,
            perception_confidence=self.state.perception_confidence,
        )

    def _should_trigger_memory_distillation(
        self, goal_completed: bool = False
    ) -> Optional[str]:
        episodic_count = self.memory.db_manager.count_memories(memory_type="episodic")
        if goal_completed:
            return "goal_completed"
        if self.memory.distiller.should_distill(episodic_count, goal_completed=False):
            return "capacity"
        return None

    def _schedule_memory_distillation(self, goal_completed: bool = False):
        trigger_type = self._should_trigger_memory_distillation(
            goal_completed=goal_completed
        )
        if not trigger_type:
            return

        task_id = f"memory_distill_{trigger_type}"
        manager = self.chronos.background_task_manager
        running = manager.get_running_tasks()
        if task_id in running:
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def distill_memory(trigger_type: str, goal_completed: bool = False):
            self.memory.maybe_distill_memory(
                goal_completed=goal_completed, trigger_type=trigger_type
            )

        task_runner = manager.run_background_task
        if getattr(task_runner, "__func__", None) is not getattr(
            type(manager), "run_background_task", None
        ):
            maybe_coro = task_runner(
                distill_memory,
                task_id,
                trigger_type=trigger_type,
                goal_completed=goal_completed,
            )
            if inspect.iscoroutine(maybe_coro):
                try:
                    maybe_coro.send(None)
                except StopIteration:
                    pass
            elif inspect.isawaitable(maybe_coro):
                asyncio.create_task(maybe_coro)
            return

        manager.background_tasks[task_id] = asyncio.create_task(
            distill_memory(trigger_type=trigger_type, goal_completed=goal_completed)
        )

    @staticmethod
    def _transaction_to_history_sample(record: TransactionRecord) -> HistorySample:
        outcome_type = "success" if record.amount > 0 else "failure"
        return HistorySample(
            action_type="EXECUTE",
            strategy_name=record.sub_category or "unknown",
            outcome_type=outcome_type,
            pnl=record.amount,
            timestamp=record.timestamp,
        )

    def evaluate_action(
        self,
        action_type: str,
        strategy_name: str,
        amount: float,
        volatility: float,
        expected_profit: float = 0.0,
    ):
        """基于当前全域快照评估待执行动作。"""
        from src.world_model import ActionCandidate

        snapshot = self._build_world_model_snapshot()
        candidate = ActionCandidate(
            action_type=action_type,
            strategy_name=strategy_name,
            amount=amount,
            volatility=volatility,
            expected_profit=expected_profit,
        )
        result = self.world_model.evaluate_action(snapshot, candidate)
        if result.block_execute:
            result = self._backtrack_verification(snapshot, candidate, result)
        if result.intuition_unstable:
            self.psyche.anxiety_engine.apply_stressor("uncertainty", intensity=0.4)
        return result

    def _backtrack_verification(self, snapshot, candidate, result):
        if not result.block_execute:
            return result

        current_balance = max(float(getattr(snapshot.agent_state, "balance", 0.0)), 0.0)
        hard_limit = current_balance * KILL_SWITCH_SINGLE_TRANSACTION_PERCENT
        if current_balance <= 0 or candidate.amount > hard_limit:
            return result

        anchor_hint = self._match_negative_wisdom_anchor(candidate)
        action_query = f"{candidate.strategy_name} {candidate.action_type} {anchor_hint or ''}".strip()
        backtrack = self.memory.backtrack_recent_solution(
            action_query=action_query,
            anchor_hint=anchor_hint,
            limit=10,
        )
        solution = backtrack.get("solution")
        if not solution:
            return result

        log.info(
            "🧭 回溯校验命中解决方案 | action_key={} | anchor={} | solution={}",
            candidate.action_key,
            anchor_hint,
            solution,
        )
        return result.with_backtrack_solution(
            solution=solution,
            anchor=anchor_hint,
            memory_ids=tuple(backtrack.get("memory_ids", ())),
        )

    def _match_negative_wisdom_anchor(self, candidate) -> Optional[str]:
        query = f"{candidate.strategy_name} {candidate.action_type}".strip()
        matches = self.memory.vector_retriever.search_similar(
            query,
            top_k=5,
            min_similarity=0.45,
        )
        for match in matches:
            metadata = match.get("metadata", {}) or {}
            if metadata.get("type") != "semantic_wisdom":
                continue
            text = str(match.get("document", "") or "")
            if any(
                keyword in text
                for keyword in ("未稳", "告急", "止损", "风险", "根因", "边界", "显存")
            ):
                return text[:16]
        return None

    def update_cognition(
        self,
        last_event: Optional[str] = None,
        user_input: Optional[str] = None,
        goal_completed: bool = False,
    ) -> AgentState:
        """执行单轮原子认知更新。"""
        self._capture_treasury_failure(last_event=last_event)
        perception_state = self.perception.update_perception(last_event, user_input)
        treasury_stats = self.treasury.get_statistics()
        chronos_state = self.chronos.get_current_time_state()

        psyche_state = self.psyche.get_current_state()
        dominant_emotion, _ = self.psyche.psyche_state.emotions.get_dominant_emotion()
        sleep_bias = self._calculate_sleep_interval_bias(psyche_state["anxiety"])
        self.chronos.apply_sleep_interval_bias(sleep_bias)

        background_task_count = self._get_background_task_count()
        focus_snapshot = self.goal_focus.evaluate_tick(
            background_task_count=background_task_count
        )
        decision_modifiers = self.goal_focus.get_decision_modifiers()
        effective_task_complexity = min(
            1.0,
            perception_state.task_complexity
            + decision_modifiers["task_complexity_delta"],
        )

        self.psyche.adjust_for_anxiety(
            balance_ratio=perception_state.balance_ratio,
            time_pressure=perception_state.time_pressure,
            task_complexity=effective_task_complexity,
            failure_streak=perception_state.failure_streak,
            social_isolation_hours=perception_state.social_isolation_hours,
            system_health=self.perception.get_system_health(),
            host_resource_pressure=perception_state.host_resource_pressure,
        )
        psyche_state = self.psyche.get_current_state()
        dominant_emotion, _ = self.psyche.psyche_state.emotions.get_dominant_emotion()
        sleep_bias = self._calculate_sleep_interval_bias(psyche_state["anxiety"])
        self.chronos.apply_sleep_interval_bias(sleep_bias)

        new_state = AgentState(
            timestamp=datetime.now(),
            balance=treasury_stats["total_balance"],
            balance_ratio=treasury_stats["balance_ratio"],
            daily_loss_percent=treasury_stats["daily_loss_percent"],
            is_hunger_mode=treasury_stats["hunger_mode"],
            anxiety=psyche_state["anxiety"],
            dominant_emotion=dominant_emotion,
            psychological_summary=self.psyche.get_psychological_summary(),
            activity_level=chronos_state["activity_level"],
            is_sleeping=chronos_state["is_sleeping"],
            time_of_day=chronos_state["time_of_day"],
            sleep_interval_bias=sleep_bias,
            last_event=last_event,
            failure_streak=perception_state.failure_streak,
            time_pressure=perception_state.time_pressure,
            task_complexity=effective_task_complexity,
            social_isolation_hours=perception_state.social_isolation_hours,
            input_intensity=perception_state.input_intensity,
            host_resource_pressure=perception_state.host_resource_pressure,
            perception_confidence=perception_state.confidence,
            active_goal_id=focus_snapshot.active_goal_id,
            active_phase=focus_snapshot.active_phase,
            focus_level=focus_snapshot.focus_level,
            focus_depletion=focus_snapshot.focus_depletion,
            background_task_count=focus_snapshot.background_task_count,
            switch_cost_delay_seconds=focus_snapshot.switch_cost_delay_seconds,
            goal_switch_blocked=focus_snapshot.goal_switch_blocked,
        )

        previous_state = self.state
        self.state = new_state
        self._schedule_memory_distillation(goal_completed=goal_completed)
        self.treasury.last_failure_reason = None

        log.info(
            "🧠 Cognition update | "
            f"event={last_event or 'none'} | "
            f"balance={new_state.balance:.2f} ({new_state.balance_ratio:.2%}) | "
            f"anxiety={new_state.anxiety:.2f} | "
            f"emotion={new_state.dominant_emotion} | "
            f"sleep_bias={new_state.sleep_interval_bias:.2f} | "
            f"failure_streak={new_state.failure_streak} | "
            f"task_complexity={new_state.task_complexity:.2f} | "
            f"time_pressure={new_state.time_pressure:.2f} | "
            f"host_resource_pressure={new_state.host_resource_pressure:.2f} | "
            f"perception_confidence={new_state.perception_confidence:.2f} | "
            f"prev_anxiety={previous_state.anxiety:.2f}"
        )

        return new_state
