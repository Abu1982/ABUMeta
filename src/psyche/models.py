"""心理数据模型模块"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
from config.constants import (
    EMOTION_DIMENSIONS,
    EMOTION_MIN,
    EMOTION_MAX,
    EMOTION_DECAY_RATE,
)


@dataclass
class EmotionState:
    """
    情感状态数据类

    包含8维情感：
    1. 快乐 - 正面愉悦情绪
    2. 悲伤 - 负面失落情绪
    3. 决心 - 坚定执行的意愿
    4. 好奇 - 探索和学习的欲望
    5. 沮丧 - 遇到障碍时的负面情绪
    6. 希望 - 对未来的积极期待
    7. 孤独 - 缺乏社交连接的感受
    8. 感恩 - 对他人或事物的感激
    """

    # 8维情感值（0-1范围）
    快乐: float = 0.5
    悲伤: float = 0.2
    决心: float = 0.6
    好奇: float = 0.7
    沮丧: float = 0.3
    希望: float = 0.6
    孤独: float = 0.2
    感恩: float = 0.4

    # 元数据
    last_updated: datetime = None

    def __post_init__(self):
        """初始化后处理"""
        if self.last_updated is None:
            self.last_updated = datetime.now()

        # 限制情感值范围
        self._clamp_emotions()

    def _clamp_emotions(self):
        """限制所有情感值在有效范围内"""
        for dimension in EMOTION_DIMENSIONS:
            value = getattr(self, dimension)
            setattr(self, dimension, max(EMOTION_MIN, min(EMOTION_MAX, value)))

    def get_emotion(self, dimension: str) -> float:
        """
        获取指定维度的情感值

        Args:
            dimension: 情感维度名称

        Returns:
            情感值
        """
        if dimension in EMOTION_DIMENSIONS:
            return getattr(self, dimension)
        raise ValueError(f"无效的情感维度: {dimension}")

    def set_emotion(self, dimension: str, value: float):
        """
        设置指定维度的情感值

        Args:
            dimension: 情感维度名称
            value: 情感值
        """
        if dimension in EMOTION_DIMENSIONS:
            setattr(self, dimension, max(EMOTION_MIN, min(EMOTION_MAX, value)))
        else:
            raise ValueError(f"无效的情感维度: {dimension}")

    def to_dict(self) -> Dict[str, float]:
        """转换为字典（仅包含情感维度）"""
        return {dim: getattr(self, dim) for dim in EMOTION_DIMENSIONS}

    def to_full_dict(self) -> Dict:
        """转换为完整字典（包含元数据）"""
        data = asdict(self)
        data["last_updated"] = self.last_updated.isoformat() if self.last_updated else None
        return data

    def get_dominant_emotion(self) -> tuple:
        """
        获取主导情感

        返回情感值最高的维度

        Returns:
            (情感维度, 情感值) 元组
        """
        emotions = self.to_dict()
        dominant = max(emotions.items(), key=lambda x: x[1])
        return dominant

    def get_emotion_balance(self) -> float:
        """
        计算情感平衡度

        平衡度 = 1 - (正面情感 - 负面情感)的标准差
        越接近1表示越平衡

        Returns:
            平衡度（0-1）
        """
        positive = (self.快乐 + self.希望 + self.感恩) / 3
        negative = (self.悲伤 + self.沮丧 + self.孤独) / 3

        from statistics import stdev
        values = [positive, negative]

        if len(values) < 2:
            return 1.0

        std = stdev(values)
        return max(0.0, 1.0 - std)

    def is_stable(self, threshold: float = 0.2) -> bool:
        """
        判断情感是否稳定

        如果所有情感值都在阈值范围内，则认为稳定

        Args:
            threshold: 稳定阈值

        Returns:
            是否稳定
        """
        emotions = self.to_dict()
        avg = sum(emotions.values()) / len(emotions)

        return all(abs(v - avg) <= threshold for v in emotions.values())


@dataclass
class PsycheState:
    """
    心理状态数据类

    包含完整的心理状态信息
    """

    # 情感状态
    emotions: EmotionState

    # 焦虑指数（0-1）
    anxiety: float = 0.3

    # LLM参数（受心理状态影响）
    temperature: float = 0.7
    top_p: float = 0.9
    defense_mode: bool = False
    suggested_rest_seconds: float = 0.0

    # 心理特征
    stability: float = 0.7      # 稳定性（0-1）
    openness: float = 0.6       # 开放性（0-1）
    neuroticism: float = 0.4    # 神经质（0-1）

    # 元数据
    last_updated: datetime = None

    def __post_init__(self):
        """初始化后处理"""
        if self.last_updated is None:
            self.last_updated = datetime.now()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "emotions": self.emotions.to_full_dict(),
            "anxiety": self.anxiety,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "defense_mode": self.defense_mode,
            "suggested_rest_seconds": self.suggested_rest_seconds,
            "stability": self.stability,
            "openness": self.openness,
            "neuroticism": self.neuroticism,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }

    def calculate_emotion_intensity(self) -> float:
        """
        计算情感强度

        强度 = 所有情感值的平均绝对偏差

        Returns:
            情感强度（0-1）
        """
        emotions = self.emotions.to_dict()
        avg = sum(emotions.values()) / len(emotions)

        intensity = sum(abs(v - avg) for v in emotions.values()) / len(emotions)
        return min(1.0, intensity * 2)  # 放大到0-1范围

    def is_high_anxiety(self, threshold: float = 0.6) -> bool:
        """
        判断是否处于高焦虑状态

        Args:
            threshold: 高焦虑阈值

        Returns:
            是否高焦虑
        """
        return self.anxiety > threshold

    def get_llm_parameters(self) -> Dict[str, float]:
        """
        获取当前的LLM参数

        Returns:
            LLM参数字典
        """
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
