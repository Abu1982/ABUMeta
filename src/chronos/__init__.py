"""
时间系统模块

提供完整的时间管理功能，包括：
- 随机休眠时间生成（正态分布）
- 作息模拟（夜间回复变慢）
- 异步后台任务调度
- 定时任务管理
"""

from .models import TimeState, SleepSchedule, TimeAwareness
from .scheduler import (
    SleepManager,
    TimeScheduler,
    BackgroundTaskManager,
    TimePerception,
)
from .engine import ChronosEngine, simulate_random_delays, apply_time_distortion, TimeUtils

__all__ = [
    "TimeState",
    "SleepSchedule",
    "TimeAwareness",
    "SleepManager",
    "TimeScheduler",
    "BackgroundTaskManager",
    "TimePerception",
    "ChronosEngine",
    "simulate_random_delays",
    "apply_time_distortion",
    "TimeUtils",
]
