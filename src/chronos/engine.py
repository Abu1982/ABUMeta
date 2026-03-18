"""时间工具模块"""

from typing import Callable, Dict, Optional
import asyncio
from datetime import datetime, timedelta
from .models import TimeState, SleepSchedule, TimeAwareness
from .scheduler import (
    SleepManager,
    TimeScheduler,
    BackgroundTaskManager,
    TimePerception,
)
from src.utils.logger import log
from config.constants import SLEEP_RECOVERY_THRESHOLD, ANXIETY_RECOVERY_SLEEP_MULTIPLIER


class ChronosEngine:
    """时间引擎主类"""

    def __init__(self):
        """
        初始化时间引擎
        """
        # 基础组件
        self.sleep_schedule = SleepSchedule()
        self.time_state = TimeState()
        self.sleep_manager = SleepManager(self.sleep_schedule)
        self.time_scheduler = TimeScheduler()
        self.background_task_manager = BackgroundTaskManager(self.time_scheduler)
        self.time_perception = TimePerception(self.sleep_manager)
        self.cruise_schedule_registry: Dict[str, Dict[str, object]] = {}

        # 统一状态引用，避免多个 TimeState 实例脱节
        self.time_state = self.sleep_manager.time_state

        log.info("⏰ 时间引擎已初始化")

    async def auto_sleep(self, anxiety: Optional[float] = None):
        """自动休眠"""
        # 更新时间感知
        self.time_perception.update_perception()

        if not self.sleep_manager.can_sleep():
            log.info(
                f"⏸️ 当前存在敏感写操作，延迟休眠: {self.time_state.pending_sleep_reason}"
            )
            return False

        # 生成休眠时间
        sleep_duration = self.sleep_manager.generate_sleep_time(anxiety=anxiety)

        # 执行休眠
        return await self.sleep_manager.sleep_async(sleep_duration)

    def apply_sleep_interval_bias(self, bias: float):
        """应用中枢计算出的休眠间隔偏置。"""
        self.sleep_manager.apply_anxiety_distortion(bias)

    def get_sleep_interval_bias(self) -> float:
        """获取当前休眠间隔偏置。"""
        return self.sleep_manager.anxiety_sleep_bias

    def acquire_task_lock(self, reason: str = "sensitive_write"):
        """在敏感写期间阻止自动休眠"""
        self.sleep_manager.acquire_task_lock(reason)

    def release_task_lock(self):
        """释放敏感写锁"""
        self.sleep_manager.release_task_lock()

    def schedule_learning_task(
        self, func: Callable[..., object], interval_hours: int = 6
    ):
        """
        调度学习任务

        每隔指定小时数执行一次学习任务

        Args:
            func: 学习任务函数
            interval_hours: 间隔小时数
        """
        interval_seconds = interval_hours * 3600
        self.time_scheduler.add_interval_job(
            func,
            interval_seconds,
            job_id="learning_task",
        )
        log.info(f"📅 已调度学习任务，间隔: {interval_hours}小时")

    def schedule_heartbeat_task(
        self,
        func: Callable[..., object],
        interval_seconds: int = 900,
        job_id: str = "autonomous_heartbeat",
    ):
        """调度自主巡航心跳任务。"""
        self.time_scheduler.add_interval_job(
            func,
            interval_seconds,
            job_id=job_id,
        )
        self.cruise_schedule_registry[job_id] = {
            "job_type": "interval",
            "interval_seconds": interval_seconds,
            "purpose": "heartbeat",
        }
        log.info(f"📅 已调度自主心跳任务，间隔: {interval_seconds}秒")

    def register_cruise_interval_task(
        self,
        *,
        func: Callable[..., object],
        interval_seconds: int,
        job_id: str,
        purpose: str,
    ):
        self.time_scheduler.add_interval_job(
            func,
            interval_seconds,
            job_id=job_id,
        )
        self.cruise_schedule_registry[job_id] = {
            "job_type": "interval",
            "interval_seconds": interval_seconds,
            "purpose": purpose,
        }
        log.info(
            "📅 已注册巡航任务 | job_id={} | purpose={} | interval={}秒",
            job_id,
            purpose,
            interval_seconds,
        )

    def describe_cruise_schedules(self) -> Dict[str, Dict[str, object]]:
        return dict(self.cruise_schedule_registry)

    def schedule_memory_compression(
        self, func: Callable[..., object], hour: int = 3, minute: int = 0
    ):
        """
        调度记忆压缩任务

        每天凌晨3点执行记忆压缩

        Args:
            func: 记忆压缩函数
            hour: 小时
            minute: 分钟
        """
        self.time_scheduler.add_daily_job(
            func,
            hour,
            minute,
            job_id="memory_compression",
        )
        log.info(f"📅 已调度记忆压缩任务，时间: {hour:02d}:{minute:02d}")

    def schedule_financial_review(
        self, func: Callable[..., object], hour: int = 21, minute: int = 0
    ):
        """
        调度财务审查任务

        每天晚上9点审查财务状况

        Args:
            func: 财务审查函数
            hour: 小时
            minute: 分钟
        """
        self.time_scheduler.add_daily_job(
            func,
            hour,
            minute,
            job_id="financial_review",
        )
        log.info(f"📅 已调度财务审查任务，时间: {hour:02d}:{minute:02d}")

    def start_all_schedules(self):
        """启动所有调度任务"""
        self.time_scheduler.start()
        self.background_task_manager.start_scheduler()
        log.info("▶️  所有调度任务已启动")

    def shutdown(self):
        """关闭时间引擎"""
        self.background_task_manager.shutdown()
        self.time_scheduler.shutdown(wait=False)
        log.info("⏹️  时间引擎已关闭")

    def get_current_time_state(self) -> Dict:
        """
        获取当前时间状态

        Returns:
            时间状态字典
        """
        return {
            "current_time": self.time_state.current_time.isoformat(),
            "time_of_day": self.time_state.get_time_of_day(),
            "is_night": self.time_state.is_night_time(),
            "is_sleeping": self.time_state.is_sleeping,
            "activity_level": self.time_state.activity_level,
            "sleep_stats": self.sleep_manager.get_sleep_statistics(),
        }

    def get_time_context(self) -> str:
        """
        获取时间上下文

        Returns:
            时间上下文描述
        """
        return self.time_perception.get_time_summary()


async def simulate_random_delays(min_delay: float = 0.1, max_delay: float = 2.0):
    """
    模拟随机延迟

    用于模拟人类的思考和打字延迟

    Args:
        min_delay: 最小延迟（秒）
        max_delay: 最大延迟（秒）
    """
    import random

    delay = random.uniform(min_delay, max_delay)
    await asyncio.sleep(delay)


def apply_time_distortion(response_time: float, activity_level: float) -> float:
    """
    应用时间扭曲

    根据活跃度调整响应时间

    Args:
        response_time: 基础响应时间
        activity_level: 活跃度

    Returns:
        调整后的响应时间
    """
    # 活跃度越低，响应越慢
    distortion_factor = 1.0 / max(0.1, activity_level)
    return response_time * distortion_factor


class TimeUtils:
    """时间工具类"""

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        格式化时长

        Args:
            seconds: 秒数

        Returns:
            格式化字符串
        """
        if seconds < 60:
            return f"{seconds:.1f}秒"
        elif seconds < 3600:
            return f"{seconds / 60:.1f}分钟"
        else:
            return f"{seconds / 3600:.1f}小时"

    @staticmethod
    def now() -> datetime:
        """获取当前时间，便于测试替换"""
        return datetime.now()

    @staticmethod
    def is_business_hours() -> bool:
        """
        判断是否为工作时间

        工作时间：9:00-18:00

        Returns:
            是否为工作时间
        """
        now = TimeUtils.now()
        return 9 <= now.hour < 18

    @staticmethod
    def get_time_until_next_hour() -> float:
        """
        计算距离下一个整点的时间（秒）

        Returns:
            秒数
        """
        now = TimeUtils.now()
        next_hour = now.replace(minute=0, second=0, microsecond=0)
        if next_hour <= now:
            next_hour += timedelta(hours=1)
        return (next_hour - now).total_seconds()
