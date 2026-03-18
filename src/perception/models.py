"""感知数据模型模块"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass(frozen=True)
class PerceptionSignal:
    """单个感知信号。"""

    raw_value: float
    normalized_value: float
    source: str
    confidence: float = 1.0


@dataclass(frozen=True)
class PerceptionState:
    """单轮感知结果。"""

    balance_ratio: float
    time_pressure: float
    task_complexity: float
    failure_streak: int
    social_isolation_hours: float
    input_intensity: float
    host_resource_pressure: float
    last_event: Optional[str]
    timestamp: datetime
    summary: str
    confidence: float
    signals: Dict[str, PerceptionSignal] = field(default_factory=dict)
    emotion_hint: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        """转换为字典，便于调试。"""
        return {
            "balance_ratio": self.balance_ratio,
            "time_pressure": self.time_pressure,
            "task_complexity": self.task_complexity,
            "failure_streak": self.failure_streak,
            "social_isolation_hours": self.social_isolation_hours,
            "input_intensity": self.input_intensity,
            "host_resource_pressure": self.host_resource_pressure,
            "last_event": self.last_event,
            "timestamp": self.timestamp.isoformat(),
            "summary": self.summary,
            "confidence": self.confidence,
            "signals": {
                name: {
                    "raw_value": signal.raw_value,
                    "normalized_value": signal.normalized_value,
                    "source": signal.source,
                    "confidence": signal.confidence,
                }
                for name, signal in self.signals.items()
            },
            "emotion_hint": dict(self.emotion_hint),
        }
