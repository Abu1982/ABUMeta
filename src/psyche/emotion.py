"""情感管理模块"""

from typing import Dict, Optional, List
from datetime import datetime, timedelta
from .models import EmotionState
from config.constants import (
    EMOTION_DIMENSIONS,
    EMOTION_DECAY_RATE,
    ANXIETY_THRESHOLD_LOW,
    ANXIETY_THRESHOLD_MEDIUM,
    ANXIETY_THRESHOLD_HIGH,
)
from src.utils.logger import log
from src.utils.helpers import clamp, exponential_decay


class EmotionManager:
    """情感管理器"""

    def __init__(self):
        """初始化情感管理器"""
        self.current_state = EmotionState()
        log.info("😊 情感管理器已初始化")

    def update_emotion(self, dimension: str, delta: float, decay: bool = False):
        """
        更新指定维度的情感

        Args:
            dimension: 情感维度名称
            delta: 变化量（可正可负）
            decay: 是否应用衰减
        """
        current_value = self.current_state.get_emotion(dimension)
        new_value = current_value + delta

        # 限制范围并规避浮点尾差
        new_value = round(clamp(new_value, 0.0, 1.0), 6)

        # 应用衰减（如果需要）
        if decay:
            hours_passed = self._get_hours_since_last_update()
            new_value = exponential_decay(new_value, EMOTION_DECAY_RATE, hours_passed)

        # 更新情感值
        self.current_state.set_emotion(dimension, new_value)
        self.current_state.last_updated = datetime.now()

        log.debug(f"🎭 情感更新: {dimension} {current_value:.2f} → {new_value:.2f}")

    def apply_event_impact(self, event_type: str, intensity: float = 0.3):
        """
        应用事件对情感的影响

        事件类型与情感维度的映射：
        - success: 增加快乐、希望、感恩；减少悲伤、沮丧
        - failure: 增加悲伤、沮丧；减少快乐、希望
        - learning: 增加好奇、决心
        - social: 减少孤独；增加快乐
        - challenge: 增加决心、好奇，并带来一些沮丧
        - help: 增加感恩、快乐

        Args:
            event_type: 事件类型
            intensity: 影响强度（0-1）
        """
        intensity = clamp(intensity, 0.0, 1.0)

        if event_type == "success":
            self.update_emotion("快乐", intensity * 0.4)
            self.update_emotion("希望", intensity * 0.3)
            self.update_emotion("感恩", intensity * 0.2)
            self.update_emotion("悲伤", -intensity * 0.3)
            self.update_emotion("沮丧", -intensity * 0.2)

        elif event_type == "failure":
            self.update_emotion("悲伤", intensity * 0.5)
            self.update_emotion("沮丧", intensity * 0.4)
            self.update_emotion("快乐", -intensity * 0.4)
            self.update_emotion("希望", -intensity * 0.3)

        elif event_type == "learning":
            self.update_emotion("好奇", intensity * 0.6)
            self.update_emotion("决心", intensity * 0.3)
            self.update_emotion("快乐", intensity * 0.2)

        elif event_type == "social":
            self.update_emotion("孤独", -intensity * 0.5)
            self.update_emotion("快乐", intensity * 0.4)
            self.update_emotion("感恩", intensity * 0.3)

        elif event_type == "challenge":
            self.update_emotion("决心", intensity * 0.5)
            self.update_emotion("好奇", intensity * 0.3)
            # 挑战也会增加一些负面情绪
            self.update_emotion("沮丧", intensity * 0.2)

        elif event_type == "help":
            self.update_emotion("感恩", intensity * 0.7)
            self.update_emotion("快乐", intensity * 0.4)
            self.update_emotion("希望", intensity * 0.2)

        log.debug(f"💫 事件影响: {event_type}, 强度: {intensity:.2f}")

    def decay_emotions(self):
        """
        衰减所有情感

        定期调用，模拟情感随时间自然衰减
        """
        hours_passed = self._get_hours_since_last_update()
        if hours_passed < 1:
            return

        for dimension in EMOTION_DIMENSIONS:
            current_value = self.current_state.get_emotion(dimension)
            decayed_value = exponential_decay(
                current_value,
                EMOTION_DECAY_RATE,
                hours_passed
            )
            self.current_state.set_emotion(dimension, decayed_value)

        self.current_state.last_updated = datetime.now()
        log.debug(f"⏳ 情感衰减: {hours_passed:.1f} 小时")

    def get_emotional_summary(self) -> str:
        """
        获取情感摘要（自然语言描述）

        Returns:
            情感状态的文字描述
        """
        dominant_emotion, value = self.current_state.get_dominant_emotion()

        # 根据主导情感和强度生成描述
        intensity_desc = ""
        if value > 0.8:
            intensity_desc = "非常"
        elif value > 0.6:
            intensity_desc = "比较"
        elif value > 0.4:
            intensity_desc = "有点"
        else:
            intensity_desc = "略微"

        descriptions = {
            "快乐": f"{intensity_desc}开心和满足",
            "悲伤": f"{intensity_desc}难过和失落",
            "决心": f"{intensity_desc}坚定和专注",
            "好奇": f"{intensity_desc}好奇和求知",
            "沮丧": f"{intensity_desc}沮丧和困扰",
            "希望": f"{intensity_desc}充满希望和期待",
            "孤独": f"{intensity_desc}感到孤单",
            "感恩": f"{intensity_desc}感激和满足",
        }

        # 检查负面情感是否显著
        negative_emotions = ["悲伤", "沮丧", "孤独"]
        negative_sum = sum(self.current_state.get_emotion(d) for d in negative_emotions)

        if negative_sum > 1.5:
            return f"情绪{intensity_desc}低落，{descriptions[dominant_emotion]}"
        elif self.current_state.孤独 > 0.7:
            return f"感到有些孤单，{descriptions[dominant_emotion]}"
        else:
            return f"感觉{descriptions[dominant_emotion]}"

    def _get_hours_since_last_update(self) -> float:
        """获取距离上次更新的小时数"""
        if self.current_state.last_updated is None:
            return 0.0

        delta = datetime.now() - self.current_state.last_updated
        return delta.total_seconds() / 3600

    def get_emotion_history(self, dimension: str, hours: int = 24) -> List[Dict]:
        """
        获取指定情感维度的历史记录

        注意：当前实现仅返回当前状态，需要结合记忆系统记录历史

        Args:
            dimension: 情感维度
            hours: 历史时长（小时）

        Returns:
            历史记录列表
        """
        # TODO: 结合记忆系统获取历史情感数据
        current_value = self.current_state.get_emotion(dimension)
        return [{"timestamp": datetime.now().isoformat(), "value": current_value}]


class EmotionAnalyzer:
    """情感分析器"""

    @staticmethod
    def text_to_emotion(text: str) -> Dict[str, float]:
        """
        从文本分析情感（简单规则版）

        Args:
            text: 待分析的文本

        Returns:
            情感维度字典
        """
        text_lower = text.lower()

        # 简单关键词匹配
        keywords = {
            "快乐": ["开心", "高兴", "快乐", "愉快", "喜悦", "😄", "😊"],
            "悲伤": ["悲伤", "难过", "伤心", "失望", "沮丧", "😢", "😞"],
            "决心": ["一定", "必须", "坚持", "努力", "决心", "💪", "🔥"],
            "好奇": ["好奇", "想知道", "为什么", "怎么", "🤔", "❓"],
            "沮丧": ["沮丧", "烦", "累", "困", "郁闷", "😩", "😫"],
            "希望": ["希望", "期待", "相信", "未来", "🌟", "✨"],
            "孤独": ["孤独", "孤单", "寂寞", "一个人", "😔", "🙁"],
            "感恩": ["感谢", "谢谢", "感激", "感恩", "🙏", "❤️"],
        }

        emotions = {dim: 0.2 for dim in EMOTION_DIMENSIONS}  # 基础值

        for dimension, words in keywords.items():
            count = sum(1 for word in words if word in text_lower)
            if count > 0:
                emotions[dimension] = min(1.0, 0.2 + count * 0.2)

        return emotions

    @staticmethod
    def detect_emotion_shift(old_state: EmotionState,
                             new_state: EmotionState) -> Dict[str, float]:
        """
        检测情感变化

        Args:
            old_state: 旧情感状态
            new_state: 新情感状态

        Returns:
            变化量字典（正值表示增加，负值表示减少）
        """
        shifts = {}
        for dimension in EMOTION_DIMENSIONS:
            old_value = old_state.get_emotion(dimension)
            new_value = new_state.get_emotion(dimension)
            shift = new_value - old_value

            if abs(shift) > 0.1:  # 只关注显著变化
                shifts[dimension] = shift

        return shifts

    @staticmethod
    def calculate_emotional_stability(emotions: Dict[str, float]) -> float:
        """
        计算情感稳定性

        稳定性 = 1 - 标准差
        越接近1越稳定

        Args:
            emotions: 情感字典

        Returns:
            稳定性评分（0-1）
        """
        values = list(emotions.values())

        if not values:
            return 1.0

        from statistics import stdev
        if len(values) < 2:
            return 1.0

        std = stdev(values)
        stability = max(0.0, 1.0 - std)
        return stability
