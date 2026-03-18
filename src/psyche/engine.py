"""心理引擎主模块"""

from typing import Dict, Optional
from datetime import datetime
from .models import PsycheState, EmotionState
from .emotion import EmotionManager, EmotionAnalyzer
from .anxiety import AnxietyEngine, BehavioralImpact
from config.settings import settings
from src.utils.logger import log


class PsycheEngine:
    """心理引擎主类"""

    def __init__(self):
        """初始化心理引擎"""
        # 情感管理
        self.emotion_manager = EmotionManager()

        # 焦虑引擎
        self.anxiety_engine = AnxietyEngine(
            base_temperature=settings.DEFAULT_TEMPERATURE,
            base_top_p=settings.DEFAULT_TOP_P,
        )

        # 行为影响
        self.behavioral_impact = BehavioralImpact(self.anxiety_engine)

        # 心理状态
        self.psyche_state = PsycheState(
            emotions=self.emotion_manager.current_state,
            anxiety=self.anxiety_engine.current_anxiety,
            temperature=settings.DEFAULT_TEMPERATURE,
            top_p=settings.DEFAULT_TOP_P,
        )

        # 最后更新时间
        self.last_decay_time = datetime.now()

        log.info("🧠 心理引擎已初始化")

    def update_state(self):
        """更新心理状态"""
        # 更新情感（定期衰减）
        current_time = datetime.now()
        if (current_time - self.last_decay_time).total_seconds() > 3600:  # 每小时衰减一次
            self.emotion_manager.decay_emotions()
            self.last_decay_time = current_time

        # 更新心理状态引用
        self.psyche_state.emotions = self.emotion_manager.current_state
        self.psyche_state.anxiety = self.anxiety_engine.current_anxiety

        # 调整LLM参数
        self.anxiety_engine.adjust_llm_parameters(self.psyche_state)

    def process_event(self, event_type: str, intensity: float = 0.3):
        """
        处理事件对心理的影响

        Args:
            event_type: 事件类型
            intensity: 事件强度
        """
        # 更新情感
        self.emotion_manager.apply_event_impact(event_type, intensity)

        # 如果是负面事件，增加焦虑
        negative_events = ["failure", "challenge", "loss"]
        if event_type in negative_events:
            self.anxiety_engine.apply_stressor("performance", intensity * 0.5)

        # 更新状态
        self.update_state()

        log.debug(f"💭 事件处理: {event_type}, 强度: {intensity:.2f}")

    def calculate_anxiety_factors(self, balance_ratio: Optional[float] = None,
                                   time_pressure: Optional[float] = None,
                                   task_complexity: Optional[float] = None,
                                   failure_streak: Optional[int] = None,
                                   social_isolation_hours: Optional[float] = None,
                                   system_health: Optional[float] = None,
                                   host_resource_pressure: Optional[float] = None) -> Dict[str, float]:
        """
        计算焦虑影响因素

        Args:
            balance_ratio: 余额比例
            time_pressure: 时间压力
            task_complexity: 任务复杂度
            failure_streak: 连续失败次数
            social_isolation_hours: 社交隔离时长（小时）

        Returns:
            焦虑因素字典
        """
        factors = {}

        if balance_ratio is not None:
            factors["balance_ratio"] = balance_ratio

        if time_pressure is not None:
            factors["time_pressure"] = time_pressure

        if task_complexity is not None:
            factors["task_complexity"] = task_complexity

        if failure_streak is not None:
            factors["failure_streak"] = failure_streak

        if social_isolation_hours is not None:
            factors["social_isolation_hours"] = social_isolation_hours

        if system_health is not None:
            factors["system_health"] = system_health
            if system_health < 0.5:
                factors["systemic_anxiety"] = round((0.5 - system_health) * 2, 3)

        if host_resource_pressure is not None:
            factors["host_resource_pressure"] = host_resource_pressure

        # 添加负面情感强度
        negative_emotions = (
            self.psyche_state.emotions.悲伤 +
            self.psyche_state.emotions.沮丧 +
            self.psyche_state.emotions.孤独
        ) / 3
        factors["negative_emotions"] = negative_emotions

        return factors

    def adjust_for_anxiety(self, balance_ratio: Optional[float] = None,
                           time_pressure: Optional[float] = None,
                           task_complexity: Optional[float] = None,
                           failure_streak: Optional[int] = None,
                           social_isolation_hours: Optional[float] = None,
                           system_health: Optional[float] = None,
                           host_resource_pressure: Optional[float] = None) -> Dict[str, float]:
        """
        根据外部因素调整焦虑

        Args:
            balance_ratio: 余额比例
            time_pressure: 时间压力
            task_complexity: 任务复杂度
            failure_streak: 连续失败次数
            social_isolation_hours: 社交隔离时长（小时）

        Returns:
            调整后的LLM参数
        """
        factors = self.calculate_anxiety_factors(
            balance_ratio,
            time_pressure,
            task_complexity,
            failure_streak,
            social_isolation_hours,
            system_health,
            host_resource_pressure,
        )

        # 计算新的焦虑指数
        self.anxiety_engine.calculate_anxiety(factors)

        # 调整LLM参数
        return self.anxiety_engine.adjust_llm_parameters(self.psyche_state)

    def modify_response(self, response: str) -> str:
        """
        根据心理状态修改回复

        Args:
            response: 原始回复

        Returns:
            修改后的回复
        """
        return self.behavioral_impact.modify_response_style(
            response,
            self.psyche_state.anxiety
        )

    def get_psychological_summary(self) -> str:
        """
        获取心理状态摘要

        Returns:
            心理状态的文字描述
        """
        emotion_summary = self.emotion_manager.get_emotional_summary()
        anxiety_level = self.anxiety_engine.get_anxiety_level()

        if self.anxiety_engine.is_crisis_mode():
            return f"⚠️ [危机状态] 情绪: {emotion_summary}，焦虑等级: {anxiety_level}"
        else:
            return f"💭 情绪: {emotion_summary}，焦虑等级: {anxiety_level}"

    def get_current_state(self) -> Dict:
        """
        获取当前完整心理状态

        Returns:
            心理状态字典
        """
        self.update_state()
        return self.psyche_state.to_dict()

    def reduce_stress(self, amount: float = 0.1):
        """
        减轻压力

        通过正面事件或休息

        Args:
            amount: 减轻量
        """
        self.anxiety_engine.reduce_anxiety(amount)
        self.update_state()
        log.debug(f"😌 压力减轻: {amount:.2f}")

    def is_stable(self) -> bool:
        """
        判断心理状态是否稳定

        Returns:
            是否稳定
        """
        emotion_stable = self.emotion_manager.current_state.is_stable(threshold=0.3)
        anxiety_level = self.anxiety_engine.get_anxiety_level()

        return emotion_stable and anxiety_level in ["低", "中"]


class MoodSimulator:
    """情绪模拟器（用于测试）"""

    @staticmethod
    def simulate_success(psyche_engine: PsycheEngine, intensity: float = 0.5):
        """模拟成功事件"""
        psyche_engine.process_event("success", intensity)

    @staticmethod
    def simulate_failure(psyche_engine: PsycheEngine, intensity: float = 0.5):
        """模拟失败事件"""
        psyche_engine.process_event("failure", intensity)

    @staticmethod
    def simulate_learning(psyche_engine: PsycheEngine, intensity: float = 0.4):
        """模拟学习事件"""
        psyche_engine.process_event("learning", intensity)

    @staticmethod
    def simulate_social_interaction(psyche_engine: PsycheEngine, intensity: float = 0.6):
        """模拟社交互动"""
        psyche_engine.process_event("social", intensity)

    @staticmethod
    def simulate_financial_stress(psyche_engine: PsycheEngine, intensity: float = 0.7):
        """模拟财务压力"""
        psyche_engine.anxiety_engine.apply_stressor("financial", intensity)
        psyche_engine.update_state()
