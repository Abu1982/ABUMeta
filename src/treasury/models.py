"""金库数据模型模块"""

from typing import Dict, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
from config.constants import (
    KILL_SWITCH_SINGLE_TRANSACTION_PERCENT,
    KILL_SWITCH_DAILY_LOSS_PERCENT,
    HUNGER_MODE_THRESHOLD,
    HUNGER_TOKEN_REDUCTION,
)


@dataclass
class TreasuryState:
    """
    金库状态数据类

    管理Agent的所有资金，包括：
    - 运营成本金（日常运营）
    - 风险博弈金（投资交易）
    """

    # 资金分配
    operational_fund: float = 500.0     # 运营成本金
    risk_fund: float = 2000.0          # 风险博弈金
    initial_total_balance: float = 0.0  # 初始总余额基准

    # 统计信息
    total_balance: float = 0.0         # 总余额
    total_spent: float = 0.0           # 累计支出
    total_earned: float = 0.0          # 累计收入

    # 当日统计
    daily_spent: float = 0.0           # 当日支出
    daily_earned: float = 0.0          # 当日收入
    daily_start_balance: float = 0.0   # 当日开始时的余额

    # 饥饿模式
    hunger_mode: bool = False          # 是否进入饥饿模式

    # 元数据
    last_updated: datetime = None
    last_transaction_time: datetime = None

    def __post_init__(self):
        """初始化后处理"""
        if self.last_updated is None:
            self.last_updated = datetime.now()

        if self.last_transaction_time is None:
            self.last_transaction_time = datetime.now()

        # 初始化总余额
        if self.total_balance == 0.0:
            self.total_balance = self.operational_fund + self.risk_fund

        if self.initial_total_balance == 0.0:
            self.initial_total_balance = self.total_balance

        if self.daily_start_balance == 0.0:
            self.daily_start_balance = self.total_balance

    def to_dict(self) -> Dict:
        """转换为字典"""
        data = asdict(self)
        data["last_updated"] = self.last_updated.isoformat() if self.last_updated else None
        data["last_transaction_time"] = (
            self.last_transaction_time.isoformat() if self.last_transaction_time else None
        )
        return data

    def get_balance_ratio(self) -> float:
        """
        计算余额比例

        余额比例 = 当前余额 / 初始余额

        Returns:
            余额比例（0-1）
        """
        initial_balance = self.initial_total_balance
        if initial_balance == 0:
            return 1.0

        return self.total_balance / initial_balance

    def is_in_hunger_mode(self) -> bool:
        """
        判断是否进入饥饿模式

        饥饿模式：余额低于初始余额的10%

        Returns:
            是否处于饥饿模式
        """
        ratio = self.get_balance_ratio()
        return ratio < HUNGER_MODE_THRESHOLD

    def calculate_available_funds(self) -> Dict[str, float]:
        """
        计算可用资金

        返回各类资金的可用金额

        Returns:
            可用资金字典
        """
        return {
            "total": self.total_balance,
            "operational": self.operational_fund,
            "risk": self.risk_fund,
            "daily_remaining": self.total_balance - self.daily_spent,
        }

    def get_daily_loss(self) -> float:
        """
        计算当日亏损

        亏损 = 当日开始余额 - 当前余额

        Returns:
            当日亏损金额
        """
        return max(0.0, self.daily_start_balance - self.total_balance)

    def get_daily_loss_percent(self) -> float:
        """
        计算当日亏损百分比

        Returns:
            当日亏损百分比（0-1）
        """
        if self.daily_start_balance == 0:
            return 0.0

        loss = self.get_daily_loss()
        return loss / self.daily_start_balance


@dataclass
class TransactionRecord:
    """
    交易记录数据类

    记录每笔交易的详细信息
    """

    # 基本信息
    transaction_id: str                    # 交易ID
    timestamp: datetime                    # 交易时间
    amount: float                          # 交易金额（正数为收入，负数为支出）
    transaction_type: str                  # 交易类型

    # 分类信息
    category: str                          # 分类（运营/风险/其他）
    sub_category: Optional[str] = None     # 子分类

    # 描述信息
    description: Optional[str] = None      # 交易描述
    related_memory_id: Optional[str] = None  # 相关记忆ID

    # 交易前后状态
    balance_before: float = 0.0            # 交易前余额
    balance_after: float = 0.0             # 交易后余额

    # 其他元数据
    tags: Optional[list] = None            # 标签
    metadata: Optional[Dict] = None        # 其他元数据

    def to_dict(self) -> Dict:
        """转换为字典"""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    def is_income(self) -> bool:
        """是否为收入"""
        return self.amount > 0

    def is_expense(self) -> bool:
        """是否为支出"""
        return self.amount < 0

    def get_absolute_amount(self) -> float:
        """获取绝对金额"""
        return abs(self.amount)


class BudgetAllocation:
    """预算分配类"""

    def __init__(self, operational_percent: float = 0.2, risk_percent: float = 0.8):
        """
        初始化预算分配

        Args:
            operational_percent: 运营资金比例
            risk_percent: 风险资金比例
        """
        self.operational_percent = operational_percent
        self.risk_percent = risk_percent

    def allocate(self, total_amount: float) -> Dict[str, float]:
        """
        分配资金

        Args:
            total_amount: 总金额

        Returns:
            分配结果字典
        """
        return {
            "operational": total_amount * self.operational_percent,
            "risk": total_amount * self.risk_percent,
        }
