"""时间数据模型模块"""

from typing import Dict, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from config.constants import (
    SLEEP_TIME_MEAN,
    SLEEP_TIME_STD,
    WAKE_UP_TIME,
    BED_TIME,
    NIGHT_DELAY_FACTOR,
)


@dataclass
class TimeState:
    """
    时间状态数据类

    管理Agent的时间感知，包括：
    - 作息时间
    - 休眠周期
    - 时间流逝
    """

    # 当前时间
    current_time: datetime = None

    # 作息时间
    wake_up_time: int = WAKE_UP_TIME        # 起床时间（小时）
    bed_time: int = BED_TIME                # 睡觉时间（小时）

    # 休眠状态
    is_sleeping: bool = False               # 是否在睡觉
    sleep_start_time: Optional[datetime] = None  # 睡觉开始时间
    sleep_end_time: Optional[datetime] = None    # 睡觉结束时间

    # 休眠统计
    total_sleep_time: float = 0.0           # 累计睡眠时间（秒）
    sleep_count: int = 0                    # 睡眠次数

    # 休眠控制
    task_lock: bool = False                  # 是否存在敏感写操作
    pending_sleep_reason: Optional[str] = None  # 延迟休眠原因

    # 活跃度
    activity_level: float = 1.0             # 活跃度（0-1）
    last_active_time: datetime = None       # 最后活跃时间

    # 元数据
    last_updated: datetime = None

    def __post_init__(self):
        """初始化后处理"""
        if self.current_time is None:
            self.current_time = datetime.now()

        if self.last_updated is None:
            self.last_updated = datetime.now()

        if self.last_active_time is None:
            self.last_active_time = datetime.now()

    def to_dict(self) -> Dict:
        """转换为字典"""
        data = asdict(self)
        data["current_time"] = self.current_time.isoformat()
        data["sleep_start_time"] = (
            self.sleep_start_time.isoformat() if self.sleep_start_time else None
        )
        data["sleep_end_time"] = (
            self.sleep_end_time.isoformat() if self.sleep_end_time else None
        )
        data["last_updated"] = self.last_updated.isoformat()
        data["last_active_time"] = self.last_active_time.isoformat()
        return data

    def is_night_time(self) -> bool:
        """
        判断当前是否为夜间

        夜间：23点-7点

        Returns:
            是否为夜间
        """
        current_hour = self.current_time.hour
        return current_hour < self.wake_up_time or current_hour >= self.bed_time

    def get_time_of_day(self) -> str:
        """
        获取一天中的时间段

        Returns:
            时间段描述
        """
        hour = self.current_time.hour

        if 5 <= hour < 8:
            return "清晨"
        elif 8 <= hour < 12:
            return "上午"
        elif 12 <= hour < 14:
            return "中午"
        elif 14 <= hour < 18:
            return "下午"
        elif 18 <= hour < 21:
            return "傍晚"
        else:
            return "深夜"

    def calculate_activity_level(self) -> float:
        """
        计算当前活跃度

        活跃度受以下因素影响：
        1. 时间段（白天活跃，夜间降低）
        2. 睡眠状态
        3. 距离上次活跃的时间

        Returns:
            活跃度（0-1）
        """
        base_level = 1.0

        # 夜间活跃度降低
        if self.is_night_time():
            base_level *= 0.5

        # 睡眠时活跃度为0
        if self.is_sleeping:
            base_level = 0.0

        # 长时间不活跃降低活跃度
        inactive_seconds = (datetime.now() - self.last_active_time).total_seconds()
        if inactive_seconds > 3600:  # 1小时
            base_level *= 0.8

        return max(0.0, min(1.0, base_level))

    def update_activity(self):
        """更新活跃状态"""
        self.last_active_time = datetime.now()
        self.activity_level = self.calculate_activity_level()

    def start_sleep(self):
        """开始睡觉"""
        self.is_sleeping = True
        self.pending_sleep_reason = None
        self.sleep_start_time = datetime.now()
        self.activity_level = 0.0
        log = __import__("src.utils.logger").utils.logger.log
        log.debug(f"😴 开始睡觉 | 时间: {self.sleep_start_time}")

    def end_sleep(self):
        """结束睡觉"""
        if self.sleep_start_time:
            self.is_sleeping = False
            self.sleep_end_time = datetime.now()

            # 计算睡眠时长
            sleep_duration = (self.sleep_end_time - self.sleep_start_time).total_seconds()
            self.total_sleep_time += sleep_duration
            self.sleep_count += 1

            self.activity_level = self.calculate_activity_level()

            log = __import__("src.utils.logger").utils.logger.log
            log.debug(f"😴 结束睡觉 | 时长: {sleep_duration/60:.1f}分钟")


@dataclass
class SleepSchedule:
    """
    休眠计划数据类

    定义Agent的休眠规律
    """

    # 休眠参数
    sleep_time_mean: float = SLEEP_TIME_MEAN      # 平均休眠时间（秒）
    sleep_time_std: float = SLEEP_TIME_STD        # 休眠时间标准差（秒）

    # 最小/最大休眠时间
    min_sleep_time: float = 60.0                  # 最小60秒
    max_sleep_time: float = 3600.0                # 最大1小时

    # 作息规律
    prefers_daytime: bool = True                  # 偏好白天活动
    night_activity_penalty: float = 0.5           # 夜间活跃度惩罚

    def to_dict(self) -> Dict:
        """转换为字典"""
        return asdict(self)


class TimeAwareness:
    """时间感知类"""

    def __init__(self, time_state: TimeState, sleep_schedule: SleepSchedule):
        """
        初始化时间感知

        Args:
            time_state: 时间状态
            sleep_schedule: 休眠计划
        """
        self.time_state = time_state
        self.sleep_schedule = sleep_schedule

    def get_time_context(self) -> str:
        """
        获取时间上下文（自然语言描述）

        Returns:
            时间上下文描述
        """
        time_of_day = self.time_state.get_time_of_day()
        is_night = self.time_state.is_night_time()

        context = f"现在是{time_of_day}"

        if is_night:
            context += "，深夜时分"

        if self.time_state.is_sleeping:
            context += "（正在休息）"

        return context

    def should_sleep_now(self) -> bool:
        """
        判断是否应该睡觉

        考虑因素：
        1. 是否为夜间
        2. 距离上次睡觉的时间
        3. 活跃度

        Returns:
            是否应该睡觉
        """
        # 夜间更可能睡觉
        if self.time_state.is_night_time():
            import random
            return random.random() < 0.7  # 70%概率

        # 白天也可能小憩
        inactive_seconds = (datetime.now() - self.time_state.last_active_time).total_seconds()
        if inactive_seconds > 7200:  # 2小时不活跃
            import random
            return random.random() < 0.3  # 30%概率

        return False
