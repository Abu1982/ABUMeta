"""金库管理模块"""

from typing import Dict, Optional, List
from datetime import datetime, timedelta
from .models import TreasuryState, TransactionRecord, BudgetAllocation
from config.constants import (
    KILL_SWITCH_SINGLE_TRANSACTION_PERCENT,
    KILL_SWITCH_DAILY_LOSS_PERCENT,
    HUNGER_MODE_THRESHOLD,
)
from src.utils.logger import log
from src.utils.helpers import clamp, format_currency, generate_unique_id


class TreasuryManager:
    """金库管理器"""

    def __init__(self, operational_fund: float = 500.0, risk_fund: float = 2000.0):
        """
        初始化金库管理器

        Args:
            operational_fund: 运营成本金
            risk_fund: 风险博弈金
        """
        self.state = TreasuryState(
            operational_fund=operational_fund,
            risk_fund=risk_fund,
        )

        self.transaction_history: List[TransactionRecord] = []
        self.last_reset_date = datetime.now().date()
        self.last_failure_reason: Optional[str] = None

        log.info(
            f"💰 金库管理器已初始化 | "
            f"运营资金: {format_currency(operational_fund)} | "
            f"风险资金: {format_currency(risk_fund)} | "
            f"总余额: {format_currency(self.state.total_balance)}"
        )

    def add_funds(self, amount: float, category: str = "other", description: str = "") -> bool:
        """
        添加资金

        Args:
            amount: 金额
            category: 分类
            description: 描述

        Returns:
            是否成功
        """
        if amount <= 0:
            log.warning(f"⚠️ 无效的充值金额: {amount}")
            return False

        # 记录交易前状态
        balance_before = self.state.total_balance

        # 更新余额
        self.state.total_balance += amount
        self.state.total_earned += amount
        self.state.daily_earned += amount

        # 更新对应资金池
        if category == "operational":
            self.state.operational_fund += amount
        elif category == "risk":
            self.state.risk_fund += amount

        # 记录交易
        self._record_transaction(
            amount=amount,
            transaction_type="deposit",
            category=category,
            description=description,
            balance_before=balance_before,
            balance_after=self.state.total_balance,
        )

        # 更新时间
        self.state.last_updated = datetime.now()
        self.state.last_transaction_time = datetime.now()

        log.info(
            f"💰 充值成功 | "
            f"金额: {format_currency(amount)} | "
            f"新余额: {format_currency(self.state.total_balance)}"
        )

        return True

    def spend(self, amount: float, category: str = "operational",
              sub_category: str = "", description: str = "") -> bool:
        """
        花费资金

        执行硬熔断检查：
        1. 单笔交易不能超过余额的10%
        2. 当日亏损不能超过10%

        Args:
            amount: 金额
            category: 分类（operational/risk）
            sub_category: 子分类
            description: 描述

        Returns:
            是否成功
        """
        self.last_failure_reason = None

        if amount <= 0:
            self.last_failure_reason = "invalid_amount"
            log.warning(f"⚠️ 无效的花费金额: {amount}")
            return False

        # 检查熔断机制
        if not self._check_kill_switch(amount):
            return False

        # 检查余额是否充足
        if amount > self.state.total_balance:
            self.last_failure_reason = "insufficient_balance"
            log.error(f"❌ 余额不足 | 需要: {format_currency(amount)}, "
                      f"当前余额: {format_currency(self.state.total_balance)}")
            return False

        # 记录交易前状态
        balance_before = self.state.total_balance

        # 更新余额
        self.state.total_balance -= amount
        self.state.total_spent += amount
        self.state.daily_spent += amount

        # 更新对应资金池
        if category == "operational":
            self.state.operational_fund -= amount
        elif category == "risk":
            self.state.risk_fund -= amount

        # 检查是否进入饥饿模式
        self.state.hunger_mode = self.state.is_in_hunger_mode()

        # 记录交易
        self._record_transaction(
            amount=-amount,
            transaction_type="expense",
            category=category,
            sub_category=sub_category,
            description=description,
            balance_before=balance_before,
            balance_after=self.state.total_balance,
        )

        # 更新时间
        self.state.last_updated = datetime.now()
        self.state.last_transaction_time = datetime.now()

        log.info(
            f"💸 花费成功 | "
            f"金额: {format_currency(amount)} | "
            f"分类: {category} | "
            f"剩余余额: {format_currency(self.state.total_balance)}"
        )

        return True

    def earn(self, amount: float, category: str = "risk",
             sub_category: str = "", description: str = "") -> bool:
        """
        获得收入

        Args:
            amount: 金额
            category: 分类
            sub_category: 子分类
            description: 描述

        Returns:
            是否成功
        """
        if amount <= 0:
            log.warning(f"⚠️ 无效的收入金额: {amount}")
            return False

        # 记录交易前状态
        balance_before = self.state.total_balance

        # 更新余额
        self.state.total_balance += amount
        self.state.total_earned += amount
        self.state.daily_earned += amount

        # 更新对应资金池
        if category == "operational":
            self.state.operational_fund += amount
        elif category == "risk":
            self.state.risk_fund += amount

        # 记录交易
        self._record_transaction(
            amount=amount,
            transaction_type="income",
            category=category,
            sub_category=sub_category,
            description=description,
            balance_before=balance_before,
            balance_after=self.state.total_balance,
        )

        # 更新时间
        self.state.last_updated = datetime.now()
        self.state.last_transaction_time = datetime.now()

        log.info(
            f"💵 收入到账 | "
            f"金额: {format_currency(amount)} | "
            f"新余额: {format_currency(self.state.total_balance)}"
        )

        return True

    def _check_kill_switch(self, amount: float) -> bool:
        """
        检查熔断机制

        熔断条件：
        1. 单笔交易 > 余额的10%
        2. 当日亏损 > 初始余额的10%

        Args:
            amount: 交易金额

        Returns:
            是否通过检查
        """
        # 检查1：单笔交易限制
        single_transaction_limit = self.state.total_balance * KILL_SWITCH_SINGLE_TRANSACTION_PERCENT
        if amount > single_transaction_limit:
            self.last_failure_reason = "single_transaction_kill_switch"
            log.error(
                f"🔥 熔断：单笔交易超过限制 | "
                f"金额: {format_currency(amount)} | "
                f"限制: {format_currency(single_transaction_limit)} | "
                f"当前余额: {format_currency(self.state.total_balance)}"
            )
            return False

        # 检查2：当日亏损限制
        daily_loss_percent = self.state.get_daily_loss_percent()
        if daily_loss_percent > KILL_SWITCH_DAILY_LOSS_PERCENT:
            self.last_failure_reason = "daily_loss_kill_switch"
            log.error(
                f"🔥 熔断：当日亏损超过限制 | "
                f"亏损比例: {daily_loss_percent:.1%} | "
                f"限制: {KILL_SWITCH_DAILY_LOSS_PERCENT:.1%}"
            )
            return False

        return True

    def _record_transaction(self, amount: float, transaction_type: str,
                            category: str, sub_category: str = "",
                            description: str = "", balance_before: float = 0.0,
                            balance_after: float = 0.0):
        """
        记录交易

        Args:
            amount: 金额
            transaction_type: 交易类型
            category: 分类
            sub_category: 子分类
            description: 描述
            balance_before: 交易前余额
            balance_after: 交易后余额
        """
        transaction = TransactionRecord(
            transaction_id=generate_unique_id("txn_"),
            timestamp=datetime.now(),
            amount=amount,
            transaction_type=transaction_type,
            category=category,
            sub_category=sub_category,
            description=description,
            balance_before=balance_before,
            balance_after=balance_after,
        )

        self.transaction_history.append(transaction)

    def get_transaction_history(self, days: int = 7) -> List[TransactionRecord]:
        """
        获取交易历史

        Args:
            days: 最近多少天

        Returns:
            交易记录列表
        """
        cutoff_time = datetime.now() - timedelta(days=days)
        return [
            txn for txn in self.transaction_history
            if txn.timestamp >= cutoff_time
        ]

    def get_statistics(self) -> Dict:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "total_balance": self.state.total_balance,
            "operational_fund": self.state.operational_fund,
            "risk_fund": self.state.risk_fund,
            "total_spent": self.state.total_spent,
            "total_earned": self.state.total_earned,
            "daily_spent": self.state.daily_spent,
            "daily_earned": self.state.daily_earned,
            "balance_ratio": self.state.get_balance_ratio(),
            "daily_loss_percent": self.state.get_daily_loss_percent(),
            "hunger_mode": self.state.hunger_mode,
            "transaction_count": len(self.transaction_history),
        }

    def reset_daily_stats(self):
        """重置当日统计"""
        today = datetime.now().date()

        # 只在新日期重置
        if today > self.last_reset_date:
            log.info(f"📅 重置当日统计 | 日期: {today}")

            self.state.daily_spent = 0.0
            self.state.daily_earned = 0.0
            self.state.daily_start_balance = self.state.total_balance
            self.last_reset_date = today

    def get_balance_summary(self) -> str:
        """
        获取余额摘要（自然语言描述）

        Returns:
            余额状态的文字描述
        """
        ratio = self.state.get_balance_ratio()
        daily_loss_percent = self.state.get_daily_loss_percent()

        summary = f"💰 当前余额: {format_currency(self.state.total_balance)}"

        # 饥饿模式
        if self.state.hunger_mode:
            summary += " (⚠️ 饥饿模式)"
        elif ratio < 0.3:
            summary += " (📉 余额偏低)"

        # 当日亏损
        if daily_loss_percent > 0.05:
            summary += f" | 今日亏损: {daily_loss_percent:.1%}"

        return summary

    def allocate_budget(self, total_amount: float) -> Dict[str, float]:
        """
        分配预算

        Args:
            total_amount: 总金额

        Returns:
            分配结果
        """
        allocation = BudgetAllocation(
            operational_percent=0.2,
            risk_percent=0.8,
        )
        return allocation.allocate(total_amount)

    def get_available_risk_amount(self) -> float:
        """
        获取可用的风险投资金额

        考虑熔断限制

        Returns:
            可用金额
        """
        # 单笔交易限制
        single_limit = self.state.total_balance * KILL_SWITCH_SINGLE_TRANSACTION_PERCENT

        # 当日亏损限制剩余
        daily_loss_limit = self.state.daily_start_balance * KILL_SWITCH_DAILY_LOSS_PERCENT
        daily_loss_used = self.state.get_daily_loss()
        daily_limit_remaining = daily_loss_limit - daily_loss_used

        # 取最小值
        available = min(single_limit, daily_limit_remaining, self.state.risk_fund)

        return max(0.0, available)


class CostTracker:
    """成本追踪器"""

    def __init__(self, treasury_manager: TreasuryManager):
        """
        初始化成本追踪器

        Args:
            treasury_manager: 金库管理器
        """
        self.treasury = treasury_manager
        self.cost_categories: Dict[str, float] = {}
        self.last_report_time = datetime.now()

    def track_cost(self, category: str, amount: float, description: str = ""):
        """
        追踪成本

        Args:
            category: 成本分类
            amount: 金额
            description: 描述
        """
        if category not in self.cost_categories:
            self.cost_categories[category] = 0.0

        self.cost_categories[category] += amount

        # 记录到金库
        self.treasury.spend(amount, category="operational", sub_category=category, description=description)

        log.debug(f"📊 成本追踪: {category} - {format_currency(amount)}")

    def get_category_spending(self, category: str) -> float:
        """
        获取分类支出

        Args:
            category: 分类名称

        Returns:
            支出金额
        """
        return self.cost_categories.get(category, 0.0)

    def get_spending_report(self) -> Dict[str, float]:
        """
        获取支出报告

        Returns:
            支出报告字典
        """
        return self.cost_categories.copy()

    def generate_report(self) -> str:
        """
        生成支出报告（文字版）

        Returns:
            报告文字
        """
        report = "📊 支出报告:\n"
        total = 0.0

        for category, amount in sorted(self.cost_categories.items(), key=lambda x: x[1], reverse=True):
            report += f"  {category}: {format_currency(amount)}\n"
            total += amount

        report += f"  ──────────\n"
        report += f"  总计: {format_currency(total)}\n"

        return report
