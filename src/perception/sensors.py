"""资源感知传感器抽象与实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.utils.helpers import clamp

try:
    import psutil
except ImportError:  # pragma: no cover - exercised via tests with patched module binding
    psutil = None


@dataclass(frozen=True)
class SensorReading:
    """统一传感器读数结构。"""

    source: str
    status: str
    health_score: float
    pressure_signals: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, object] = field(default_factory=dict)
    confidence: float = 1.0


class BaseSensor(ABC):
    """统一传感器接口。"""

    name: str = "base_sensor"

    @abstractmethod
    def is_available(self) -> bool:
        """返回传感器当前是否可用。"""

    @abstractmethod
    def read(self) -> SensorReading:
        """返回统一结构的原始读数。"""

    def get_health_score(self) -> float:
        """返回 0-1 的健康度。"""
        return clamp(self.read().health_score, 0.0, 1.0)

    def get_pressure_signals(self) -> Dict[str, float]:
        """返回 perception/psyche 可消费的压力信号。"""
        return {
            key: clamp(value, 0.0, 1.0)
            for key, value in self.read().pressure_signals.items()
        }


class SmartHomeSensor(BaseSensor):
    """占位智能家居传感器，始终返回中立状态。"""

    name = "smart_home"

    def is_available(self) -> bool:
        return True

    def read(self) -> SensorReading:
        return SensorReading(
            source="smart_home.placeholder",
            status="standby",
            health_score=1.0,
            pressure_signals={"environment_pressure": 0.0},
            details={
                "mode": "standby",
                "device_count": 0,
                "connected": False,
                "note": "placeholder sensor without live hardware",
            },
            confidence=1.0,
        )


class HostMachineSensor(BaseSensor):
    """读取宿主机 CPU/内存负载的真实传感器。"""

    name = "host_machine"

    def __init__(self, psutil_module=None):
        self._psutil = psutil if psutil_module is None else psutil_module

    def is_available(self) -> bool:
        return self._psutil is not None

    def read(self) -> SensorReading:
        if not self.is_available():
            return SensorReading(
                source="host_machine.psutil",
                status="unavailable",
                health_score=0.0,
                pressure_signals={"host_resource_pressure": 0.0},
                details={"reason": "psutil_not_installed"},
                confidence=0.0,
            )

        cpu_percent = float(self._psutil.cpu_percent(interval=None))
        memory_percent = float(self._psutil.virtual_memory().percent)
        cpu_load = clamp(cpu_percent / 100.0, 0.0, 1.0)
        memory_load = clamp(memory_percent / 100.0, 0.0, 1.0)
        pressure = round(clamp((cpu_load * 0.55) + (memory_load * 0.45), 0.0, 1.0), 3)
        health_score = round(clamp(1.0 - pressure, 0.0, 1.0), 3)

        return SensorReading(
            source="host_machine.psutil",
            status="active",
            health_score=health_score,
            pressure_signals={"host_resource_pressure": pressure},
            details={
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
            },
            confidence=0.95,
        )
