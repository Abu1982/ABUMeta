"""
心理引擎模块

提供完整的心理状态管理功能，包括：
- 8维情感模型
- 情绪连续性和衰减
- 焦虑计算和影响
- LLM参数动态调整
- 行为风格修改
"""

from .models import EmotionState, PsycheState
from .emotion import EmotionManager, EmotionAnalyzer
from .anxiety import AnxietyEngine, BehavioralImpact
from .engine import PsycheEngine, MoodSimulator

__all__ = [
    "EmotionState",
    "PsycheState",
    "EmotionManager",
    "EmotionAnalyzer",
    "AnxietyEngine",
    "BehavioralImpact",
    "PsycheEngine",
    "MoodSimulator",
]
