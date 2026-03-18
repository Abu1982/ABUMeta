"""时间调度器模块"""

import asyncio
import random
from typing import Dict, Optional, Callable, List
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from .models import TimeState, SleepSchedule, TimeAwareness
from config.constants import (
    SLEEP_TIME_MEAN,
    SLEEP_TIME_STD,
    NIGHT_DELAY_FACTOR,
    SLEEP_RECOVERY_THRESHOLD,
    ANXIETY_RECOVERY_SLEEP_MULTIPLIER,
)
from src.utils.logger import log
from src.utils.helpers import is_night_time, apply_night_delay


class SleepManager:
    """睡眠管理器"""

    def __init__(self, sleep_schedule: Optional[SleepSchedule] = None):
        """
        初始化睡眠管理器

        Args:
            sleep_schedule: 休眠计划
        """
        self.sleep_schedule = sleep_schedule or SleepSchedule()
        self.time_state = TimeState()
        self.time_awareness = TimeAwareness(self.time_state, self.sleep_schedule)
        self.anxiety_sleep_bias = 1.0

        log.info("😴 睡眠管理器已初始化")

    def acquire_task_lock(self, reason: str = "sensitive_write"):
        """获取任务锁，阻止进入随机休眠"""
        self.time_state.task_lock = True
        self.time_state.pending_sleep_reason = reason
        log.debug(f"🔒 获取任务锁: {reason}")

    def release_task_lock(self):
        """释放任务锁"""
        self.time_state.task_lock = False
        self.time_state.pending_sleep_reason = None
        log.debug("🔓 释放任务锁")

    def can_sleep(self) -> bool:
        """判断当前是否允许休眠"""
        return not self.time_state.task_lock

    def apply_anxiety_distortion(self, bias: float):
        """应用由中枢注入的焦虑休眠偏置。"""
        self.anxiety_sleep_bias = max(0.1, min(3.0, bias))
        log.debug(f"😵 焦虑休眠偏置更新: {self.anxiety_sleep_bias:.2f}")

    def generate_sleep_time(self, anxiety: Optional[float] = None) -> float:
        """
        生成随机休眠时间

        使用正态分布：
        - 均值：500秒（约8分钟）
        - 标准差：150秒

        限制在60秒-3600秒之间

        Args:
            anxiety: 当前焦虑值，高焦虑时会延长恢复性休眠

        Returns:
            休眠时间（秒）
        """
        # 生成正态分布的随机数
        sleep_time = random.gauss(
            self.sleep_schedule.sleep_time_mean,
            self.sleep_schedule.sleep_time_std,
        )

        # 应用夜间延迟（如果是夜间）
        if self.time_state.is_night_time():
            sleep_time = apply_night_delay(sleep_time)

        # 高焦虑时延长恢复性休眠
        if anxiety is not None and anxiety >= SLEEP_RECOVERY_THRESHOLD:
            sleep_time *= ANXIETY_RECOVERY_SLEEP_MULTIPLIER

        # 应用中枢注入的焦虑偏置（偏置 < 1 时更频繁检查）
        sleep_time *= self.anxiety_sleep_bias

        # 限制范围
        sleep_time = max(self.sleep_schedule.min_sleep_time, sleep_time)
        sleep_time = min(self.sleep_schedule.max_sleep_time, sleep_time)

        log.debug(
            f"⏰ 生成休眠时间: {sleep_time / 60:.1f}分钟 | "
            f"anxiety={anxiety if anxiety is not None else 'N/A'} | "
            f"bias={self.anxiety_sleep_bias:.2f}"
        )

        return sleep_time

    async def sleep_async(self, duration: Optional[float] = None):
        """
        异步休眠

        Args:
            duration: 休眠时长（秒），如果为None则自动生成
        """
        if not self.can_sleep():
            log.info(f"⏸️ 跳过休眠，任务锁生效: {self.time_state.pending_sleep_reason}")
            return False

        if duration is None:
            duration = self.generate_sleep_time()

        # 标记开始睡觉
        self.time_state.start_sleep()

        log.info(f"😴 开始休眠 {duration / 60:.1f}分钟...")

        # 异步休眠
        await asyncio.sleep(duration)

        # 标记结束睡觉
        self.time_state.end_sleep()

        log.info(f"😌 休眠结束，当前活跃度: {self.time_state.activity_level:.2f}")
        return True

    def get_sleep_statistics(self) -> Dict:
        """
        获取睡眠统计

        Returns:
            睡眠统计字典
        """
        avg_sleep_time = (
            self.time_state.total_sleep_time / self.time_state.sleep_count
            if self.time_state.sleep_count > 0
            else 0
        )

        return {
            "total_sleep_time": self.time_state.total_sleep_time,
            "sleep_count": self.time_state.sleep_count,
            "average_sleep_time": avg_sleep_time,
            "is_sleeping": self.time_state.is_sleeping,
            "anxiety_sleep_bias": self.anxiety_sleep_bias,
        }


class TimeScheduler:
    """时间调度器"""

    def __init__(self):
        """初始化时间调度器"""
        # 创建调度器
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="Asia/Shanghai",
        )

        self.jobs: Dict[str, any] = {}
        self.is_running = False

        log.info("⏱️  时间调度器已初始化")

    def add_interval_job(
        self, func: Callable, interval_seconds: int, job_id: str, **kwargs
    ) -> bool:
        """
        添加间隔任务

        Args:
            func: 任务函数
            interval_seconds: 间隔时间（秒）
            job_id: 任务ID
            **kwargs: 任务函数参数

        Returns:
            是否添加成功
        """
        try:
            trigger = IntervalTrigger(seconds=interval_seconds)
            job = self.scheduler.add_job(
                func,
                trigger,
                id=job_id,
                kwargs=kwargs,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=max(5, interval_seconds // 2),
            )

            self.jobs[job_id] = job
            log.debug(f"➕ 添加间隔任务: {job_id}, 间隔: {interval_seconds}秒")

            return True

        except Exception as e:
            log.error(f"❌ 添加间隔任务失败: {job_id}, error: {e}")
            return False

    def add_cron_job(
        self, func: Callable, cron_expression: str, job_id: str, **kwargs
    ) -> bool:
        """
        添加定时任务（Cron表达式）

        Args:
            func: 任务函数
            cron_expression: Cron表达式（"分 时 日 月 周"）
            job_id: 任务ID
            **kwargs: 任务函数参数

        Returns:
            是否添加成功
        """
        try:
            # 解析Cron表达式
            parts = cron_expression.split()
            if len(parts) != 5:
                raise ValueError("Cron表达式格式错误，应为 '分 时 日 月 周'")

            trigger = CronTrigger.from_crontab(cron_expression)
            job = self.scheduler.add_job(
                func,
                trigger,
                id=job_id,
                kwargs=kwargs,
                replace_existing=True,
            )

            self.jobs[job_id] = job
            log.debug(f"➕ 添加定时任务: {job_id}, Cron: {cron_expression}")

            return True

        except Exception as e:
            log.error(f"❌ 添加定时任务失败: {job_id}, error: {e}")
            return False

    def add_daily_job(
        self, func: Callable, hour: int, minute: int, job_id: str, **kwargs
    ) -> bool:
        """
        添加每日任务

        Args:
            func: 任务函数
            hour: 小时（0-23）
            minute: 分钟（0-59）
            job_id: 任务ID
            **kwargs: 任务函数参数

        Returns:
            是否添加成功
        """
        cron_expr = f"{minute} {hour} * * *"
        return self.add_cron_job(func, cron_expr, job_id, **kwargs)

    def remove_job(self, job_id: str) -> bool:
        """
        移除任务

        Args:
            job_id: 任务ID

        Returns:
            是否移除成功
        """
        try:
            if job_id in self.jobs:
                self.scheduler.remove_job(job_id)
                del self.jobs[job_id]
                log.debug(f"➖ 移除任务: {job_id}")
                return True
            return False

        except Exception as e:
            log.error(f"❌ 移除任务失败: {job_id}, error: {e}")
            return False

    def start(self):
        """启动调度器"""
        if not self.is_running:
            self.scheduler.start()
            self.is_running = True
            log.info("▶️  时间调度器已启动")

    def shutdown(self, wait: bool = True):
        """关闭调度器"""
        if self.is_running:
            self.scheduler.shutdown(wait=wait)
            self.is_running = False
            log.info("⏹️  时间调度器已关闭")

    def get_jobs(self) -> List[Dict]:
        """
        获取所有任务

        Returns:
            任务列表
        """
        return [
            {
                "id": job.id,
                "next_run_time": getattr(job, "next_run_time", None),
                "func": job.func.__name__ if job.func else "unknown",
            }
            for job in self.scheduler.get_jobs()
        ]

    def pause_job(self, job_id: str) -> bool:
        """暂停任务"""
        try:
            if job_id in self.jobs:
                self.scheduler.pause_job(job_id)
                log.debug(f"⏸️  暂停任务: {job_id}")
                return True
            return False
        except Exception as e:
            log.error(f"❌ 暂停任务失败: {job_id}, error: {e}")
            return False

    def resume_job(self, job_id: str) -> bool:
        """恢复任务"""
        try:
            if job_id in self.jobs:
                self.scheduler.resume_job(job_id)
                log.debug(f"▶️  恢复任务: {job_id}")
                return True
            return False
        except Exception as e:
            log.error(f"❌ 恢复任务失败: {job_id}, error: {e}")
            return False


class BackgroundTaskManager:
    """后台任务管理器"""

    def __init__(self, time_scheduler: Optional[TimeScheduler] = None):
        """
        初始化后台任务管理器

        Args:
            time_scheduler: 时间调度器
        """
        self.scheduler = time_scheduler or TimeScheduler()
        self.background_tasks: Dict[str, asyncio.Task] = {}
        self.is_running = False

        log.info("🔧 后台任务管理器已初始化")

    async def run_background_task(self, func: Callable, task_id: str, **kwargs):
        """
        运行后台任务

        任务会在后台持续运行，不影响主循环

        Args:
            func: 任务函数（异步）
            task_id: 任务ID
            **kwargs: 任务函数参数
        """

        async def wrapper():
            log.debug(f"🧵 后台任务开始: {task_id}")
            try:
                await func(**kwargs)
                log.debug(f"✅ 后台任务完成: {task_id}")
            except Exception as e:
                log.error(f"❌ 后台任务失败: {task_id}, error: {e}")
            finally:
                if task_id in self.background_tasks:
                    del self.background_tasks[task_id]

        task = asyncio.create_task(wrapper())
        self.background_tasks[task_id] = task
        log.debug(f"➕ 启动后台任务: {task_id}")

    def cancel_background_task(self, task_id: str) -> bool:
        """
        取消后台任务

        Args:
            task_id: 任务ID

        Returns:
            是否取消成功
        """
        if task_id in self.background_tasks:
            task = self.background_tasks[task_id]
            task.cancel()
            del self.background_tasks[task_id]
            log.debug(f"❌ 取消后台任务: {task_id}")
            return True
        return False

    def get_running_tasks(self) -> List[str]:
        """
        获取运行中的后台任务

        Returns:
            任务ID列表
        """
        return list(self.background_tasks.keys())

    async def wait_for_all_tasks(self):
        """等待所有后台任务完成"""
        if self.background_tasks:
            await asyncio.gather(
                *self.background_tasks.values(), return_exceptions=True
            )
            log.debug("✅ 所有后台任务已完成")

    def start_scheduler(self):
        """启动调度器"""
        self.scheduler.start()

    def shutdown(self):
        """关闭"""
        # 取消所有后台任务
        for task_id in list(self.background_tasks.keys()):
            self.cancel_background_task(task_id)

        # 关闭调度器
        self.scheduler.shutdown(wait=False)

        log.info("⏹️  后台任务管理器已关闭")


class TimePerception:
    """时间感知器"""

    def __init__(self, sleep_manager: SleepManager):
        """
        初始化时间感知器

        Args:
            sleep_manager: 睡眠管理器
        """
        self.sleep_manager = sleep_manager
        self.last_perception_time = datetime.now()

    def get_time_since_last_perception(self) -> float:
        """
        获取距离上次感知的时间（秒）

        Returns:
            时间差（秒）
        """
        delta = datetime.now() - self.last_perception_time
        return delta.total_seconds()

    def update_perception(self):
        """更新时间感知"""
        self.last_perception_time = datetime.now()
        self.sleep_manager.time_state.current_time = datetime.now()
        self.sleep_manager.time_state.update_activity()

    def should_take_break(self, max_active_time: int = 3600) -> bool:
        """
        判断是否应该休息

        Args:
            max_active_time: 最大连续活跃时间（秒）

        Returns:
            是否应该休息
        """
        inactive_time = self.get_time_since_last_perception()
        return inactive_time > max_active_time

    def get_time_summary(self) -> str:
        """
        获取时间摘要（自然语言）

        Returns:
            时间摘要
        """
        time_context = self.sleep_manager.time_awareness.get_time_context()
        activity_level = self.sleep_manager.time_state.activity_level

        summary = f"{time_context}，活跃度: {activity_level:.0%}"

        if self.sleep_manager.time_state.is_sleeping:
            summary += "（正在休息）"

        return summary
