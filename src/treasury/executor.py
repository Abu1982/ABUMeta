"""交易执行器模块"""

from typing import Dict, Optional, Callable
from datetime import datetime
from .manager import TreasuryManager
from config.constants import MAX_TRANSACTION_AMOUNT, MIN_PROFIT_MARGIN
from src.decision import ActionIntent, DecisionBrain
from src.memory import MemoryManager
from src.utils.logger import log
from src.utils.helpers import format_currency
from src.world_model import SimulationResult


class TradeExecutor:
    """交易执行器"""

    def __init__(
        self,
        treasury_manager: TreasuryManager,
        memory_manager: Optional[MemoryManager] = None,
    ):
        """
        初始化交易执行器

        Args:
            treasury_manager: 金库管理器
        """
        self.treasury = treasury_manager
        self.trade_count = 0
        self.successful_trades = 0
        self.failed_trades = 0
        self.decision_brain = DecisionBrain()
        self.memory_manager = memory_manager

        log.info("💱 交易执行器已初始化")

    def execute_trade(
        self,
        amount: float,
        strategy_name: str,
        execute_callback: Callable,
        simulation_result: Optional[SimulationResult] = None,
        **kwargs,
    ) -> Dict:
        """
        执行交易

        流程：
        1. 检查熔断机制
        2. 检查余额
        3. 扣除交易金额
        4. 执行交易回调
        5. 处理交易结果

        Args:
            amount: 交易金额
            strategy_name: 策略名称
            execute_callback: 执行回调函数
            **kwargs: 回调函数参数

        Returns:
            交易结果字典
        """
        log.info(
            f"💱 准备执行交易 | 策略: {strategy_name}, 金额: {format_currency(amount)}"
        )

        decision = self.decision_brain.evaluate_treasury_intent(
            ActionIntent(
                domain="treasury",
                intent_text=f"执行交易策略 {strategy_name}，目标金额 {amount:.2f}，波动率 {getattr(simulation_result, 'failure_probability', 0.0):.2f}",
                strategy_name=strategy_name,
                amount=amount,
                volatility=getattr(simulation_result, "failure_probability", 0.0),
                expected_profit=getattr(simulation_result, "expected_value", 0.0),
                estimated_steps=3,
                energy_cost=min(
                    1.0, 0.35 + (amount / max(self.treasury.state.total_balance, 1.0))
                ),
                tags=("交易", "风险", strategy_name),
            )
        )
        if not decision.allowed:
            log.warning(
                "🧬 基因过滤层阻断交易 | "
                f"strategy={strategy_name} | gene={decision.matched_gene} | reasons={list(decision.reasons)}"
            )
            return {
                "success": False,
                "reason": "decision_brain_blocked",
                "details": list(decision.reasons),
                "decision": decision.to_dict(),
            }

        if simulation_result is not None and simulation_result.block_execute:
            log.warning(
                "🛑 世界模型阻断交易 | "
                f"strategy={strategy_name} | reasons={list(simulation_result.reasons)}"
            )
            return {
                "success": False,
                "reason": "world_model_blocked",
                "details": list(simulation_result.reasons),
                "simulation": simulation_result,
                "decision": decision.to_dict(),
            }

        if simulation_result is not None and simulation_result.potential_solution:
            injected_context = str(simulation_result.potential_solution)
            existing_context = kwargs.get("context")
            if existing_context:
                kwargs["context"] = f"{existing_context}\n{injected_context}"
            else:
                kwargs["context"] = injected_context

        effective_amount = amount
        if decision.action == "throttle":
            effective_amount *= decision.execution_probability
        if (
            simulation_result is not None
            and simulation_result.execution_willingness < 1.0
        ):
            effective_amount = max(
                0.0, effective_amount * simulation_result.execution_willingness
            )
            log.info(
                "📉 世界模型下调执行金额 | "
                f"original={format_currency(amount)} | effective={format_currency(effective_amount)}"
            )
        else:
            effective_amount = max(0.0, effective_amount)

        # 检查可用金额
        available_amount = self.treasury.get_available_risk_amount()
        if effective_amount > available_amount:
            log.error(
                f"❌ 交易金额超过可用限额 | "
                f"请求: {format_currency(effective_amount)}, "
                f"可用: {format_currency(available_amount)}"
            )
            return {
                "success": False,
                "reason": "金额超过限额",
                "available": available_amount,
                "decision": decision.to_dict(),
            }

        if effective_amount <= 0:
            return {
                "success": False,
                "reason": "execution_willingness_too_low",
                "simulation": simulation_result,
                "decision": decision.to_dict(),
            }

        # 记录交易前余额
        balance_before = self.treasury.state.total_balance

        # 扣除交易金额（预扣）
        if not self.treasury.spend(
            effective_amount,
            category="risk",
            sub_category=strategy_name,
            description=f"交易预扣: {strategy_name}",
        ):
            return {
                "success": False,
                "reason": "余额不足",
                "decision": decision.to_dict(),
            }

        try:
            # 执行交易
            result = execute_callback(amount=effective_amount, **kwargs)

            if result.get("success"):
                # 交易成功，返还本金并添加收益
                profit = result.get("profit", 0.0)
                return_amount = effective_amount + profit

                self.treasury.add_funds(
                    return_amount,
                    category="risk",
                    description=f"交易收益: {strategy_name}",
                )

                self.successful_trades += 1
                if (
                    simulation_result is not None
                    and simulation_result.potential_solution
                ):
                    self._record_successful_backtrack(
                        strategy_name=strategy_name,
                        solution=simulation_result.potential_solution,
                    )
                success = True
                message = f"✅ 交易成功 | 收益: {format_currency(profit)}"
            else:
                # 交易失败，本金已扣除
                self.failed_trades += 1
                success = False
                message = f"❌ 交易失败 | 损失: {format_currency(effective_amount)}"

            # 记录交易统计
            self.trade_count += 1

            log.info(message)

            return {
                "success": success,
                "profit": result.get("profit", 0.0) if success else -effective_amount,
                "balance_before": balance_before,
                "balance_after": self.treasury.state.total_balance,
                "trade_count": self.trade_count,
                "success_rate": self.successful_trades / self.trade_count
                if self.trade_count > 0
                else 0.0,
                "executed_amount": effective_amount,
                "simulation": simulation_result,
                "decision": decision.to_dict(),
            }

        except Exception as e:
            # 异常情况，尝试返还本金
            log.error(f"⚠️ 交易异常: {e}")
            self.treasury.add_funds(
                effective_amount, category="risk", description="交易异常返还"
            )
            self.failed_trades += 1

            return {
                "success": False,
                "reason": f"异常: {str(e)}",
                "balance_restored": True,
                "decision": decision.to_dict(),
            }

    def _record_successful_backtrack(self, strategy_name: str, solution: str) -> None:
        if self.memory_manager is None:
            return
        self.memory_manager.create_memory(
            event=f"Outcome: Success | strategy={strategy_name}",
            thought="回溯校验后采用了解法并完成执行。",
            lesson=f"通过带解执行成功解决：{solution}",
            importance=0.98,
            source_type="backtrack_resolution",
            verification_status="auto",
            raw_payload={"strategy_name": strategy_name, "solution": solution},
        )

    def get_trade_statistics(self) -> Dict:
        """
        获取交易统计

        Returns:
            交易统计字典
        """
        success_rate = (
            self.successful_trades / self.trade_count if self.trade_count > 0 else 0.0
        )

        return {
            "total_trades": self.trade_count,
            "successful_trades": self.successful_trades,
            "failed_trades": self.failed_trades,
            "success_rate": success_rate,
        }

    def reset_statistics(self):
        """重置交易统计"""
        self.trade_count = 0
        self.successful_trades = 0
        self.failed_trades = 0
        log.info("🔄 交易统计已重置")


class RiskManager:
    """风险管理器"""

    def __init__(self, treasury_manager: TreasuryManager, risk_tolerance: float = 0.3):
        """
        初始化风险管理器

        Args:
            treasury_manager: 金库管理器
            risk_tolerance: 风险容忍度（0-1）
        """
        self.treasury = treasury_manager
        self.risk_tolerance = risk_tolerance
        self.max_position_size = 0.1  # 单笔最大仓位10%

        log.info(f"🛡️ 风险管理器已初始化 | 风险容忍度: {risk_tolerance}")

    def calculate_position_size(
        self, confidence: float, volatility: float = 0.5
    ) -> float:
        """
        计算仓位大小

        考虑因素：
        1. 置信度（confidence）
        2. 波动率（volatility）
        3. 风险容忍度
        4. 可用资金

        Args:
            confidence: 策略置信度（0-1）
            volatility: 波动率（0-1）

        Returns:
            仓位大小（金额）
        """
        # 基础仓位 = 可用资金 × 最大仓位比例
        available_funds = self.treasury.get_available_risk_amount()
        base_position = available_funds * self.max_position_size

        # 调整因子 = 置信度 × (1 - 波动率) × 风险容忍度
        adjustment_factor = confidence * (1 - volatility) * self.risk_tolerance

        # 最终仓位
        position_size = base_position * adjustment_factor

        # 限制在合理范围内
        position_size = min(position_size, available_funds * 0.1)  # 不超过可用资金的10%
        position_size = max(position_size, available_funds * 0.01)  # 不低于可用资金的1%

        log.debug(
            f"📊 仓位计算 | "
            f"可用资金: {format_currency(available_funds)}, "
            f"置信度: {confidence:.2f}, "
            f"波动率: {volatility:.2f}, "
            f"仓位: {format_currency(position_size)}"
        )

        return position_size

    def assess_trade_risk(self, potential_loss: float, potential_profit: float) -> Dict:
        """
        评估交易风险

        计算风险收益比

        Args:
            potential_loss: 潜在损失
            potential_profit: 潜在收益

        Returns:
            风险评估字典
        """
        if potential_loss <= 0:
            risk_reward_ratio = float("inf")
        else:
            risk_reward_ratio = potential_profit / potential_loss

        # 风险等级
        if risk_reward_ratio > 3:
            risk_level = "低"
        elif risk_reward_ratio > 1.5:
            risk_level = "中"
        else:
            risk_level = "高"

        # 是否可接受
        acceptable = (
            risk_reward_ratio >= 1.5
            and potential_loss <= self.treasury.get_available_risk_amount() * 0.05
        )

        return {
            "risk_reward_ratio": risk_reward_ratio,
            "risk_level": risk_level,
            "acceptable": acceptable,
            "potential_loss": potential_loss,
            "potential_profit": potential_profit,
        }

    def check_risk_limits(self) -> Dict:
        """
        检查风险限制

        Returns:
            风险限制检查结果
        """
        state = self.treasury.state

        # 余额比例
        balance_ratio = state.get_balance_ratio()

        # 当日亏损
        daily_loss_percent = state.get_daily_loss_percent()

        # 风险资金占比
        risk_ratio = (
            state.risk_fund / state.total_balance if state.total_balance > 0 else 0
        )

        return {
            "balance_ratio": balance_ratio,
            "daily_loss_percent": daily_loss_percent,
            "risk_ratio": risk_ratio,
            "hunger_mode": state.hunger_mode,
            "can_trade": not state.hunger_mode and daily_loss_percent < 0.1,
        }

    def should_stop_trading(self) -> tuple:
        """
        判断是否应该停止交易

        Returns:
            (是否停止, 原因)
        """
        checks = self.check_risk_limits()

        if checks["hunger_mode"]:
            return True, "进入饥饿模式"

        if checks["daily_loss_percent"] > 0.1:
            return True, "当日亏损超限"

        if checks["balance_ratio"] < 0.2:
            return True, "余额过低"

        return False, ""
