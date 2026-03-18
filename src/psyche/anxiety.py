"""焦虑引擎模块"""

from typing import Dict, Optional
from datetime import datetime
from .models import PsycheState
from config.constants import (
    ANXIETY_THRESHOLD_LOW,
    ANXIETY_THRESHOLD_MEDIUM,
    ANXIETY_THRESHOLD_HIGH,
    ANXIETY_TEMPERATURE_FACTOR,
    ANXIETY_TOP_P_FACTOR,
    ANXIETY_DEFENSE_THRESHOLD,
    ANXIETY_COOLDOWN_THRESHOLD,
    ANXIETY_DEFENSE_TEMPERATURE,
    ANXIETY_DEFENSE_TOP_P,
)
from src.utils.logger import log
from src.utils.helpers import clamp, calculate_percentage_change


class AnxietyEngine:
    """焦虑引擎"""

    def __init__(self, base_temperature: float = 0.7, base_top_p: float = 0.9):
        """
        初始化焦虑引擎

        Args:
            base_temperature: 基础temperature值
            base_top_p: 基础top_p值
        """
        self.base_temperature = base_temperature
        self.base_top_p = base_top_p
        self.current_anxiety = 0.3  # 初始焦虑值

        log.info("😰 焦虑引擎已初始化")

    def calculate_anxiety(self, factors: Dict[str, float]) -> float:
        """
        计算焦虑指数

        影响因素：
        1. 余额压力 - 金库余额低于阈值
        2. 时间压力 - 距离截止时间太近
        3. 任务复杂度 - 任务难度过高
        4. 失败次数 - 连续失败次数
        5. 社交压力 - 长时间无社交互动

        Args:
            factors: 焦虑影响因素字典

        Returns:
            焦虑指数（0-1）
        """
        anxiety = 0.3  # 基础焦虑值

        # 1. 余额压力
        if "balance_ratio" in factors:
            balance_ratio = factors["balance_ratio"]  # 余额占总资金比例
            if balance_ratio < 0.2:
                anxiety += (0.2 - balance_ratio) * 2.0  # 余额越低，焦虑越高

        # 2. 时间压力
        if "time_pressure" in factors:
            time_pressure = factors["time_pressure"]  # 0-1，越接近1压力越大
            anxiety += time_pressure * 0.3

        # 3. 任务复杂度
        if "task_complexity" in factors:
            task_complexity = factors["task_complexity"]  # 0-1，复杂度
            anxiety += task_complexity * 0.2

        # 4. 失败次数
        if "failure_streak" in factors:
            failure_streak = factors["failure_streak"]  # 连续失败次数
            anxiety += min(0.4, failure_streak * 0.1)  # 最多增加0.4

        # 5. 社交压力
        if "social_isolation_hours" in factors:
            isolation_hours = factors["social_isolation_hours"]
            if isolation_hours > 48:  # 超过48小时无社交
                anxiety += min(0.3, (isolation_hours - 48) / 100)

        # 6. 系统性焦虑
        if "systemic_anxiety" in factors:
            anxiety += factors["systemic_anxiety"] * 0.25

        # 7. 宿主机资源压力
        if "host_resource_pressure" in factors:
            anxiety += factors["host_resource_pressure"] * 0.15

        # 8. 情感状态影响
        if "negative_emotions" in factors:
            negative_emotions = factors["negative_emotions"]  # 负面情感强度
            anxiety += negative_emotions * 0.2

        # 限制范围
        self.current_anxiety = clamp(anxiety, 0.0, 1.0)
        log.debug(f"😰 焦虑指数: {self.current_anxiety:.2f}")

        return self.current_anxiety

    def get_anxiety_level(self) -> str:
        """
        获取焦虑等级

        Returns:
            焦虑等级描述
        """
        if self.current_anxiety < ANXIETY_THRESHOLD_LOW:
            return "低"
        elif self.current_anxiety < ANXIETY_THRESHOLD_MEDIUM:
            return "中"
        elif self.current_anxiety < ANXIETY_THRESHOLD_HIGH:
            return "高"
        else:
            return "极高"

    def adjust_llm_parameters(self, psyche_state: PsycheState) -> Dict[str, float]:
        """
        根据焦虑状态调整LLM参数

        低/中焦虑时：
        - Temperature 略微升高
        - Top_p 略微升高

        高焦虑时：
        - 触发心理防御机制
        - 强制降低 temperature/top_p，减少随机性
        - 极高焦虑时建议进入更长时间的恢复性休眠

        Args:
            psyche_state: 心理状态

        Returns:
            调整后的LLM参数
        """
        anxiety = self.current_anxiety
        defense_mode = anxiety >= ANXIETY_DEFENSE_THRESHOLD
        cooldown_mode = anxiety >= ANXIETY_COOLDOWN_THRESHOLD

        if defense_mode:
            new_temperature = ANXIETY_DEFENSE_TEMPERATURE
            new_top_p = ANXIETY_DEFENSE_TOP_P
            suggested_rest_seconds = 0.0

            if cooldown_mode:
                suggested_rest_seconds = 900.0

            psyche_state.defense_mode = True
            psyche_state.suggested_rest_seconds = suggested_rest_seconds
        else:
            # 计算调整因子
            temp_adjustment = anxiety * ANXIETY_TEMPERATURE_FACTOR
            top_p_adjustment = anxiety * ANXIETY_TOP_P_FACTOR

            # 调整参数
            new_temperature = self.base_temperature + temp_adjustment
            new_top_p = self.base_top_p + top_p_adjustment
            psyche_state.defense_mode = False
            psyche_state.suggested_rest_seconds = 0.0

        # 限制范围
        new_temperature = clamp(new_temperature, 0.1, 1.5)
        new_top_p = clamp(new_top_p, 0.1, 1.0)

        # 更新心理状态
        psyche_state.temperature = new_temperature
        psyche_state.top_p = new_top_p
        psyche_state.anxiety = anxiety

        log.debug(
            f"🧠 LLM参数调整: temperature={new_temperature:.2f}, "
            f"top_p={new_top_p:.2f}, anxiety={anxiety:.2f}, "
            f"defense_mode={psyche_state.defense_mode}"
        )

        return {
            "temperature": new_temperature,
            "top_p": new_top_p,
            "anxiety": anxiety,
            "defense_mode": psyche_state.defense_mode,
            "suggested_rest_seconds": psyche_state.suggested_rest_seconds,
        }

    def apply_stressor(self, stressor_type: str, intensity: float = 0.3):
        """
        应用压力源

        压力源类型：
        - financial: 财务压力
        - time: 时间压力
        - social: 社交压力
        - performance: 表现压力
        - uncertainty: 不确定性压力

        Args:
            stressor_type: 压力源类型
            intensity: 强度（0-1）
        """
        intensity = clamp(intensity, 0.0, 1.0)

        if stressor_type == "financial":
            self.current_anxiety += intensity * 0.4
        elif stressor_type == "time":
            self.current_anxiety += intensity * 0.3
        elif stressor_type == "social":
            self.current_anxiety += intensity * 0.2
        elif stressor_type == "performance":
            self.current_anxiety += intensity * 0.35
        elif stressor_type == "uncertainty":
            self.current_anxiety += intensity * 0.25

        # 限制范围
        self.current_anxiety = clamp(self.current_anxiety, 0.0, 1.0)
        log.debug(f"⚠️ 压力源: {stressor_type}, 强度: {intensity:.2f}, "
                  f"焦虑: {self.current_anxiety:.2f}")

    def reduce_anxiety(self, amount: float = 0.1):
        """
        减少焦虑

        通过正面事件、休息、社交等方式减少

        Args:
            amount: 减少量
        """
        self.current_anxiety = max(0.0, self.current_anxiety - amount)
        log.debug(f"😌 焦虑减少: {amount:.2f}, 剩余: {self.current_anxiety:.2f}")

    def is_crisis_mode(self) -> bool:
        """
        判断是否处于危机模式

        危机模式：焦虑 > 0.8

        Returns:
            是否处于危机模式
        """
        return self.current_anxiety > ANXIETY_THRESHOLD_HIGH


class BehavioralImpact:
    """行为影响器"""

    def __init__(self, anxiety_engine: AnxietyEngine):
        """
        初始化行为影响器

        Args:
            anxiety_engine: 焦虑引擎
        """
        self.anxiety_engine = anxiety_engine

    def modify_response_style(self, response: str, anxiety: Optional[float] = None) -> str:
        """
        根据焦虑状态修改回复风格

        焦虑越高：
        - 回复越简短
        - 语气词越多
        - 可能出现重复或矛盾

        Args:
            response: 原始回复
            anxiety: 焦虑指数，默认使用引擎当前值

        Returns:
            修改后的回复
        """
        if anxiety is None:
            anxiety = self.anxiety_engine.current_anxiety

        if anxiety < ANXIETY_THRESHOLD_LOW:
            # 低焦虑：正常回复
            return response

        elif anxiety < ANXIETY_THRESHOLD_MEDIUM:
            # 中焦虑：稍微简化，增加语气词
            return self._add_filler_words(response)

        elif anxiety < ANXIETY_THRESHOLD_HIGH:
            # 高焦虑：简化回复，增加犹豫
            return self._simplify_response(response, anxiety)

        else:
            # 极高焦虑：非常简短，可能不完整
            return self._crisis_response(response)

    def _add_filler_words(self, response: str) -> str:
        """添加语气词"""
        filler_words = ["呃", "那个", "嗯", "我觉得", "可能", "大概"]
        import random

        if random.random() < 0.3:  # 30%概率添加
            filler = random.choice(filler_words)
            return f"{filler}，{response}"

        return response

    def _simplify_response(self, response: str, anxiety: float) -> str:
        """简化回复"""
        # 根据焦虑程度截断
        max_length = int(len(response) * (1.0 - anxiety * 0.5))
        if len(response) > max_length:
            response = response[:max_length] + "..."

        return self._add_filler_words(response)

    def _crisis_response(self, response: str) -> str:
        """危机模式回复"""
        # 只保留核心内容
        sentences = response.split("。")
        if sentences:
            return sentences[0] + "..."
        return response[:50] + "..."

    def affect_decision_making(self, options: list, anxiety: float) -> list:
        """
        影响决策制定

        高焦虑时：
        - 更倾向于保守选择
        - 可能做出冲动决定
        - 难以权衡长期利益

        Args:
            options: 选项列表
            anxiety: 焦虑指数

        Returns:
            调整后的选项权重
        """
        if not options:
            return []

        if anxiety < ANXIETY_THRESHOLD_LOW:
            # 正常决策
            return options

        elif anxiety < ANXIETY_THRESHOLD_MEDIUM:
            # 略微偏向保守
            return self._bias_conservative(options)

        else:
            # 高焦虑：可能做出非理性选择
            return self._impulsive_decision(options, anxiety)

    def _bias_conservative(self, options: list) -> list:
        """偏向保守选择"""
        # 这里只是标记，实际决策逻辑在外部
        return options

    def _impulsive_decision(self, options: list, anxiety: float) -> list:
        """冲动决策"""
        import random

        # 高焦虑时可能随机选择
        if random.random() < anxiety * 0.5:
            random.shuffle(options)

        return options
