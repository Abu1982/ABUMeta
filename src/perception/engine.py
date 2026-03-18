"""感知引擎模块"""

from datetime import datetime
from typing import Dict, Optional, Tuple

from src.memory import MemoryManager
from src.psyche import EmotionAnalyzer
from src.treasury import TreasuryManager
from src.chronos import ChronosEngine
from src.utils.helpers import clamp
from src.utils.logger import log

from .models import PerceptionSignal, PerceptionState
from .sensors import BaseSensor, HostMachineSensor, SmartHomeSensor


FAILURE_MEMORY_KEYWORDS = (
    "failure",
    "failed",
    "失败",
    "熔断",
    "kill_switch",
    "insufficient_balance",
    "transaction_failed",
)

SOCIAL_MEMORY_KEYWORDS = (
    "user_input",
    "social",
    "help",
)

COMPLEXITY_KEYWORDS = (
    "实现",
    "设计",
    "重构",
    "修复",
    "并行",
    "多模块",
    "架构",
    "系统",
    "integrate",
    "refactor",
    "implement",
    "design",
    "parallel",
)

CONSTRAINT_KEYWORDS = (
    "必须",
    "需要",
    "约束",
    "要求",
    "保持",
    "兼容",
    "避免",
    "确保",
)


class PerceptionEngine:
    """统一聚合环境与交互信号。"""

    def __init__(
        self,
        treasury: TreasuryManager,
        chronos: ChronosEngine,
        memory: MemoryManager,
    ):
        self.treasury = treasury
        self.chronos = chronos
        self.memory = memory
        self.registered_sensors = {
            "treasury": self.treasury,
            "chronos": self.chronos,
            "memory": self.memory,
            "host_machine": HostMachineSensor(),
            "smart_home": SmartHomeSensor(),
        }
        self.current_state: Optional[PerceptionState] = None
        self.last_perception_time = datetime.now()

    def update_perception(
        self,
        last_event: Optional[str],
        user_input: Optional[str] = None,
    ) -> PerceptionState:
        """更新感知状态。"""
        timestamp = datetime.now()
        treasury_stats = self.treasury.get_statistics()
        chronos_state = self.chronos.get_current_time_state()

        balance_ratio = float(treasury_stats.get("balance_ratio", 0.0))
        time_pressure = self._calculate_time_pressure(chronos_state)
        failure_streak = self._get_failure_streak()
        task_complexity = self._estimate_task_complexity(user_input, last_event)
        input_intensity = self._estimate_input_intensity(user_input)
        social_isolation_hours, social_confidence = self._calculate_social_isolation_hours()
        host_resource_pressure, host_confidence = self._collect_host_resource_pressure()
        emotion_hint = EmotionAnalyzer.text_to_emotion(user_input) if user_input else {}

        signals = {
            "balance_ratio": PerceptionSignal(
                raw_value=balance_ratio,
                normalized_value=clamp(balance_ratio, 0.0, 1.0),
                source="treasury.get_statistics",
                confidence=1.0,
            ),
            "time_pressure": PerceptionSignal(
                raw_value=time_pressure,
                normalized_value=clamp(time_pressure, 0.0, 1.0),
                source="chronos.get_current_time_state",
                confidence=0.95,
            ),
            "task_complexity": PerceptionSignal(
                raw_value=task_complexity,
                normalized_value=task_complexity,
                source="user_input" if user_input else "event_fallback",
                confidence=0.8 if user_input else 0.5,
            ),
            "failure_streak": PerceptionSignal(
                raw_value=float(failure_streak),
                normalized_value=min(failure_streak / 5.0, 1.0),
                source="memory.get_recent_memories",
                confidence=0.9,
            ),
            "social_isolation_hours": PerceptionSignal(
                raw_value=social_isolation_hours,
                normalized_value=min(social_isolation_hours / 24.0, 1.0),
                source="memory.get_recent_memories",
                confidence=social_confidence,
            ),
            "input_intensity": PerceptionSignal(
                raw_value=input_intensity,
                normalized_value=input_intensity,
                source="user_input" if user_input else "event_fallback",
                confidence=0.85 if user_input else 0.5,
            ),
            "host_resource_pressure": PerceptionSignal(
                raw_value=host_resource_pressure,
                normalized_value=host_resource_pressure,
                source="host_machine.psutil",
                confidence=host_confidence,
            ),
        }

        confidence = round(
            sum(signal.confidence for signal in signals.values()) / max(len(signals), 1),
            3,
        )
        summary = self._build_summary(
            balance_ratio=balance_ratio,
            time_pressure=time_pressure,
            task_complexity=task_complexity,
            failure_streak=failure_streak,
            social_isolation_hours=social_isolation_hours,
            input_intensity=input_intensity,
            host_resource_pressure=host_resource_pressure,
            user_input=user_input,
            chronos_state=chronos_state,
        )

        state = PerceptionState(
            balance_ratio=balance_ratio,
            time_pressure=time_pressure,
            task_complexity=task_complexity,
            failure_streak=failure_streak,
            social_isolation_hours=social_isolation_hours,
            input_intensity=input_intensity,
            host_resource_pressure=host_resource_pressure,
            last_event=last_event,
            timestamp=timestamp,
            summary=summary,
            confidence=confidence,
            signals=signals,
            emotion_hint=emotion_hint,
        )

        self.current_state = state
        self.last_perception_time = timestamp
        log.debug(
            "👁️ Perception update | "
            f"event={last_event or 'none'} | balance_ratio={balance_ratio:.2%} | "
            f"time_pressure={time_pressure:.2f} | task_complexity={task_complexity:.2f} | "
            f"failure_streak={failure_streak} | isolation_hours={social_isolation_hours:.2f} | "
            f"input_intensity={input_intensity:.2f} | host_resource_pressure={host_resource_pressure:.2f}"
        )
        return state

    def get_perception_summary(self) -> str:
        """获取当前感知摘要。"""
        if self.current_state is None:
            return "感知尚未初始化"
        return self.current_state.summary

    def get_system_health(self) -> float:
        """检查当前已注册传感器的活跃状态并返回健康度评分。"""
        if not self.registered_sensors:
            return 0.0

        health_scores = [self._get_sensor_health_score(sensor) for sensor in self.registered_sensors.values()]
        return round(clamp(sum(health_scores) / len(health_scores), 0.0, 1.0), 3)

    def _calculate_time_pressure(self, chronos_state: Dict[str, object]) -> float:
        if chronos_state.get("is_sleeping"):
            return 0.1
        if chronos_state.get("is_night"):
            return 0.2
        return 0.15

    def _get_failure_streak(self) -> int:
        recent_memories = self.memory.db_manager.get_recent_memories(hours=24, limit=50)
        if not recent_memories:
            return 0

        streak = 0
        for memory in recent_memories:
            searchable = " ".join(filter(None, [memory.event, memory.thought, memory.lesson])).lower()
            if any(keyword.lower() in searchable for keyword in FAILURE_MEMORY_KEYWORDS):
                streak += 1
            else:
                break

        return streak

    def _calculate_social_isolation_hours(self) -> Tuple[float, float]:
        recent_memories = self.memory.db_manager.get_recent_memories(hours=24 * 30, limit=200)
        for memory in recent_memories:
            searchable = " ".join(filter(None, [memory.event, memory.thought, memory.lesson])).lower()
            if any(keyword in searchable for keyword in SOCIAL_MEMORY_KEYWORDS):
                hours = max(0.0, (datetime.now() - memory.timestamp).total_seconds() / 3600)
                return round(hours, 2), 0.9
        return 0.0, 0.3

    def _collect_host_resource_pressure(self) -> Tuple[float, float]:
        sensor = self.registered_sensors.get("host_machine")
        if not isinstance(sensor, BaseSensor):
            return 0.0, 0.0

        reading = sensor.read()
        pressure = clamp(reading.pressure_signals.get("host_resource_pressure", 0.0), 0.0, 1.0)
        return round(pressure, 3), reading.confidence

    def _get_sensor_health_score(self, sensor: object) -> float:
        if sensor is None:
            return 0.0
        if isinstance(sensor, BaseSensor):
            return sensor.get_health_score() if sensor.is_available() else 0.0

        is_active = True
        if hasattr(sensor, "is_running"):
            is_active = bool(getattr(sensor, "is_running"))
        return 1.0 if is_active else 0.0

    def _estimate_task_complexity(self, user_input: Optional[str], last_event: Optional[str]) -> float:
        if not user_input:
            return 0.0

        text = user_input.strip()
        if not text:
            return 0.0

        length_score = min(len(text) / 600.0, 0.35)
        line_break_score = min(text.count("\n") / 8.0, 0.15)
        question_score = min((text.count("?") + text.count("？")) * 0.08, 0.16)
        complexity_hits = sum(1 for keyword in COMPLEXITY_KEYWORDS if keyword.lower() in text.lower())
        constraint_hits = sum(1 for keyword in CONSTRAINT_KEYWORDS if keyword in text)
        keyword_score = min(complexity_hits * 0.08 + constraint_hits * 0.05, 0.34)

        return round(clamp(length_score + line_break_score + question_score + keyword_score, 0.0, 1.0), 3)

    def _estimate_input_intensity(self, user_input: Optional[str]) -> float:
        if not user_input:
            return 0.0

        text = user_input.strip()
        if not text:
            return 0.0

        length_score = min(len(text) / 500.0, 0.45)
        emphasis_score = min((text.count("!") + text.count("！") + text.count("\n")) * 0.05, 0.2)
        emotion_hint = EmotionAnalyzer.text_to_emotion(text)
        emotional_score = min(sum(max(value - 0.2, 0.0) for value in emotion_hint.values()) / 4.0, 0.35)

        return round(clamp(length_score + emphasis_score + emotional_score, 0.0, 1.0), 3)

    def _build_summary(
        self,
        *,
        balance_ratio: float,
        time_pressure: float,
        task_complexity: float,
        failure_streak: int,
        social_isolation_hours: float,
        input_intensity: float,
        host_resource_pressure: float,
        user_input: Optional[str],
        chronos_state: Dict[str, object],
    ) -> str:
        activity_level = float(chronos_state.get("activity_level", 0.0))
        summary = (
            f"{chronos_state.get('time_of_day', '未知时段')}，活跃度: {activity_level:.0%}，"
            f"余额压力: {balance_ratio:.0%}，时间压力: {time_pressure:.2f}，"
            f"任务复杂度: {task_complexity:.2f}，失败序列: {failure_streak}，"
            f"社交间隔: {social_isolation_hours:.1f}h，输入强度: {input_intensity:.2f}，"
            f"宿主机压力: {host_resource_pressure:.2f}"
        )
        if chronos_state.get("is_sleeping"):
            summary += "（正在休息）"
        if user_input:
            summary += "，已检测到用户输入线索"
        return summary
