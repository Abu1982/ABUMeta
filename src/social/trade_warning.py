"""3-Agent 外贸风险结构化预警。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.observability import get_action_journal
from src.world_model import WorldModel


RiskLevel = Literal["low", "medium", "high", "critical"]
Disposition = Literal["pass", "observe", "gated_continue", "block"]


class AgentRosterEntry(BaseModel):
    """预警协同中的 Agent 定义。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="Agent 唯一标识")
    agent_name: str = Field(description="Agent 展示名称")
    focus: str = Field(description="Agent 负责的风险维度")


class TradeRiskSample(BaseModel):
    """外贸风险结构化样本。"""

    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(description="样本编号")
    scenario_name: str = Field(description="样本标题")
    counterparty_name: str = Field(description="对手方名称")
    destination_country: str = Field(description="目的国")
    product_name: str = Field(description="产品名称")
    product_category: str = Field(description="产品分类")
    trade_value_usd: float = Field(ge=0, description="订单金额，单位 USD")
    payment_method: str = Field(description="付款方式")
    payment_days: int = Field(ge=0, description="账期天数")
    deposit_ratio: float = Field(ge=0, le=1, description="首付款比例")
    incoterm: str = Field(description="贸易术语")
    requested_discount_ratio: float = Field(ge=0, le=1, description="客户要求折扣比例")
    account_age_days: int = Field(ge=0, description="账号或合作主体建立天数")
    uses_free_email: bool = Field(description="是否使用免费邮箱")
    asks_undervalue_invoice: bool = Field(description="是否要求低报发票")
    asks_split_payment_to_third_party: bool = Field(description="是否要求第三方收款")
    asks_release_bl_before_payment: bool = Field(description="是否要求先放单后收尾款")
    hs_code_sensitive: bool = Field(description="HS 编码是否敏感")
    sanction_watch_hit: bool = Field(description="是否命中制裁或观察名单")
    transshipment_country: Optional[str] = Field(
        default=None, description="过境国家，如无则为空"
    )
    documents_complete_ratio: float = Field(ge=0, le=1, description="单证完备度")
    kyc_verified: bool = Field(description="是否完成 KYC/UBO 核验")
    export_license_ready: bool = Field(description="是否完成出口许可准备")
    production_buffer_days: int = Field(ge=0, description="排产缓冲天数")
    logistics_delay_days: int = Field(ge=0, description="已知物流延误天数")
    gross_margin_ratio: float = Field(ge=0, le=1, description="毛利率")
    inventory_ready_ratio: float = Field(ge=0, le=1, description="可用库存比例")
    prior_dispute_count: int = Field(ge=0, description="历史争议次数")
    source_reputation: float = Field(ge=0, le=1, description="线索可信度")
    external_match_count: int = Field(ge=0, default=0, description="外部风险源命中数")
    external_conflict_level: str = Field(
        default="none", description="外部风险源冲突级别"
    )
    external_match_sources: list[str] = Field(
        default_factory=list, description="外部命中来源列表"
    )
    external_resolution_advice: str = Field(
        default="no_action", description="外部风险源建议动作"
    )
    external_conflict_summary: Dict[str, Any] = Field(
        default_factory=dict, description="外部多源冲突裁决摘要"
    )
    notes: list[str] = Field(default_factory=list, description="人工备注")


class TradeRiskSampleSet(BaseModel):
    """用于巡航的样本集。"""

    model_config = ConfigDict(extra="forbid")

    sample_set_id: str = Field(description="样本集标识")
    cruise_goal: str = Field(description="本次巡航目标")
    agent_roles: list[AgentRosterEntry] = Field(description="本次巡航的 3-Agent 角色")
    samples: list[TradeRiskSample] = Field(description="结构化样本列表")


class ScenarioSnapshot(BaseModel):
    """报告中保留的样本快照。"""

    model_config = ConfigDict(extra="forbid")

    destination_country: str
    product_name: str
    trade_value_usd: float
    payment_method: str
    incoterm: str
    deposit_ratio: float


class AgentAssessment(BaseModel):
    """单个 Agent 的分析结果。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="Agent 标识")
    agent_name: str = Field(description="Agent 名称")
    focus: str = Field(description="Agent 关注维度")
    risk_score: int = Field(ge=0, le=100, description="风险分")
    risk_level: RiskLevel = Field(description="风险等级")
    confidence: float = Field(ge=0, le=1, description="结论置信度")
    evidence: list[str] = Field(description="证据点")
    missing_information: list[str] = Field(description="待补信息")
    next_actions: list[str] = Field(description="建议动作")


class CoordinatedWarning(BaseModel):
    """多 Agent 协同后的样本预警。"""

    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(description="样本编号")
    scenario_name: str = Field(description="样本标题")
    counterparty_name: str = Field(description="对手方名称")
    scenario_snapshot: ScenarioSnapshot = Field(description="场景快照")
    overall_risk_score: int = Field(ge=0, le=100, description="综合风险分")
    overall_risk_level: RiskLevel = Field(description="综合风险等级")
    coordination_confidence: float = Field(ge=0, le=1, description="协同置信度")
    primary_risk_vector: str = Field(description="主风险向量")
    disposition: Disposition = Field(description="最终处置策略")
    decision_band_label: str = Field(description="解释层决策标签")
    decision_reason: str = Field(description="解释层决策原因")
    explanation_tags: list[str] = Field(description="解释层标签")
    external_conflict_level: str = Field(description="外部风险冲突级别")
    external_match_count: int = Field(ge=0, description="外部风险命中数")
    external_match_sources: list[str] = Field(description="外部命中来源")
    external_resolution_advice: str = Field(description="外部建议处置动作")
    external_conflict_summary: Dict[str, Any] = Field(
        default_factory=dict, description="外部多源冲突裁决摘要"
    )
    blocking_issues: list[str] = Field(description="阻断项")
    final_recommendation: str = Field(description="最终建议")
    recommended_owner: str = Field(description="建议责任方")
    coordination_adjustments: list[str] = Field(
        default_factory=list, description="协调层补偿与加权记录"
    )
    immediate_actions: list[str] = Field(description="立即动作")
    agent_assessments: list[AgentAssessment] = Field(description="三 Agent 明细")


class ReportSummary(BaseModel):
    """报告总览。"""

    model_config = ConfigDict(extra="forbid")

    total_samples: int = Field(ge=0, description="样本总数")
    critical_count: int = Field(ge=0, description="critical 样本数")
    high_count: int = Field(ge=0, description="high 样本数")
    medium_count: int = Field(ge=0, description="medium 样本数")
    low_count: int = Field(ge=0, description="low 样本数")
    blocked_count: int = Field(ge=0, description="阻断样本数")
    top_warning_sample_ids: list[str] = Field(description="优先关注样本")
    dominant_risk_vectors: list[str] = Field(description="高频风险向量")


class ForeignTradeWarningReport(BaseModel):
    """最终输出的协同预警报告。"""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(description="报告编号")
    schema_version: str = Field(description="报告 Schema 版本")
    generated_at: str = Field(description="报告生成时间")
    cruise_name: str = Field(description="巡航任务名称")
    cruise_goal: str = Field(description="巡航目标")
    sample_set_id: str = Field(description="输入样本集标识")
    sample_count: int = Field(ge=0, description="样本数量")
    agent_roster: list[AgentRosterEntry] = Field(description="Agent 编组")
    summary: ReportSummary = Field(description="汇总统计")
    warnings: list[CoordinatedWarning] = Field(description="样本级预警")
    final_watch_items: list[str] = Field(description="总级别关注动作")
    trace_context: Dict[str, Any] = Field(description="行动账本追踪上下文")


def _dedupe_keep_order(items: Iterable[str], *, limit: int = 5) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _risk_level_from_score(score: int) -> RiskLevel:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def _clip_score(score: float) -> int:
    return max(0, min(100, int(round(score))))


class BaseWarningAgent:
    """风险 Agent 基类。"""

    agent_id = ""
    agent_name = ""
    focus = ""

    def __init__(self, *, world_model: WorldModel, journal=None):
        self.world_model = world_model
        self.journal = journal or get_action_journal()

    def analyze(
        self, sample: TradeRiskSample, *, trace_context: Dict[str, Any]
    ) -> AgentAssessment:
        self.journal.log_event(
            component=self.agent_name,
            stage="trade_warning",
            action="analyze_sample",
            status="started",
            payload={"sample_id": sample.sample_id, "focus": self.focus},
            priority="normal",
            context=trace_context,
        )
        score, evidence, missing, actions = self._score_sample(sample)
        evidence = _dedupe_keep_order(evidence, limit=6)
        missing = _dedupe_keep_order(missing, limit=4)
        actions = _dedupe_keep_order(actions, limit=5)
        confidence_seed = max(1, min(8, len(evidence) + len(missing)))
        confidence = round(
            min(0.95, self.world_model.estimate_confidence(confidence_seed) + 0.08), 3
        )
        assessment = AgentAssessment(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            focus=self.focus,
            risk_score=_clip_score(score),
            risk_level=_risk_level_from_score(_clip_score(score)),
            confidence=confidence,
            evidence=evidence,
            missing_information=missing,
            next_actions=actions,
        )
        self.journal.log_event(
            component=self.agent_name,
            stage="trade_warning",
            action="analyze_sample",
            status="success",
            payload={
                "sample_id": sample.sample_id,
                "risk_score": assessment.risk_score,
                "risk_level": assessment.risk_level,
            },
            priority="normal",
            context=trace_context,
        )
        return assessment

    def _score_sample(
        self, sample: TradeRiskSample
    ) -> tuple[int, list[str], list[str], list[str]]:
        raise NotImplementedError


class CounterpartyRadarAgent(BaseWarningAgent):
    """对手方与收款条件审查 Agent。"""

    agent_id = "counterparty_radar"
    agent_name = "CounterpartyRadarAgent"
    focus = "counterparty_payment"

    def _score_sample(
        self, sample: TradeRiskSample
    ) -> tuple[int, list[str], list[str], list[str]]:
        score = 0
        evidence: list[str] = []
        missing: list[str] = []
        actions: list[str] = []
        payment_method = sample.payment_method.lower()

        if sample.uses_free_email:
            score += 15
            evidence.append("对手方使用免费邮箱，主体绑定偏弱。")
            actions.append("要求切换企业域名邮箱并补充固定电话核验。")
        if sample.account_age_days < 90:
            score += 18
            evidence.append("合作主体建立时间不足 90 天，历史沉淀偏薄。")
        elif sample.account_age_days < 365:
            score += 8
            evidence.append("合作主体建立时间不足 1 年，仍需留意首单质量。")
        if payment_method in {"open_account", "tt_after_bl", "da", "dp_90"}:
            score += 18
            evidence.append(f"付款方式为 {sample.payment_method}，回款控制力偏弱。")
            actions.append("改谈信用证、保理或缩短账期。")
        if sample.payment_days >= 60:
            score += 12
            evidence.append(f"账期达到 {sample.payment_days} 天，现金回笼压力增大。")
        if sample.deposit_ratio < 0.2:
            score += 18
            evidence.append("首付款低于 20%，难覆盖备料与试产风险。")
            actions.append("将首付款提高到 30% 以上再排产。")
        elif sample.deposit_ratio < 0.3:
            score += 10
            evidence.append("首付款低于 30%，安全垫偏薄。")
        if not sample.kyc_verified:
            score += 20
            evidence.append("KYC/受益所有人核验未完成。")
            missing.append("缺少营业执照、受益所有人、开户主体一致性校验。")
            actions.append("补齐 KYC、银行资信与 UBO 材料。")
        else:
            evidence.append("KYC 已闭环，对手方身份链条可追溯。")
        if sample.asks_split_payment_to_third_party:
            score += 24
            evidence.append("客户要求第三方收款，资金路径与合同主体不一致。")
            actions.append("冻结第三方收款要求，改为合同主体同名账户。")
        if sample.asks_release_bl_before_payment:
            score += 20
            evidence.append("客户要求先放单后收尾款，提单控制权存在缺口。")
            actions.append("坚持尾款到账后放单，必要时转信用证或托收。")
        if sample.prior_dispute_count > 0:
            increment = min(16, sample.prior_dispute_count * 8)
            score += increment
            evidence.append(
                f"历史争议 {sample.prior_dispute_count} 次，履约摩擦已有记录。"
            )
        if sample.documents_complete_ratio < 0.7:
            score += 10
            evidence.append("客户单证完备度低于 70%，线索闭环不足。")
            missing.append("缺少完整公司注册、收货地址或银行抬头资料。")
        if sample.requested_discount_ratio > 0.08:
            score += 8
            evidence.append("客户要求折扣超过 8%，价格锚点偏激进。")
        if sample.source_reputation < 0.6:
            score += 10
            evidence.append("线索可信度偏低，需防伪询盘或代采中间层。")
            actions.append("补做同站反查、海关记录和 LinkedIn 交叉验证。")
        elif sample.source_reputation >= 0.8:
            evidence.append("线索可信度较高，可作为初步加分项。")

        if not actions:
            actions.append("维持基础收款条款，继续观察付款节点表现。")
        return score, evidence, missing, actions


class ComplianceSentinelAgent(BaseWarningAgent):
    """合规与制裁审查 Agent。"""

    agent_id = "compliance_sentinel"
    agent_name = "ComplianceSentinelAgent"
    focus = "compliance_sanctions"

    HIGH_RISK_DESTINATIONS = {"Russia", "Iran", "Belarus", "Syria"}
    ELEVATED_DESTINATIONS = {
        "Nigeria",
        "Kazakhstan",
        "United Arab Emirates",
        "Turkey",
    }
    ELEVATED_TRANSIT = {"Turkey", "Georgia", "Armenia", "United Arab Emirates"}

    def _score_sample(
        self, sample: TradeRiskSample
    ) -> tuple[int, list[str], list[str], list[str]]:
        score = 0
        evidence: list[str] = []
        missing: list[str] = []
        actions: list[str] = []

        if sample.sanction_watch_hit:
            score += 35
            evidence.append("命中制裁或观察名单，存在直接合规阻断。")
            actions.append("立即冻结商机并转法务/合规人工复核。")
        if sample.asks_undervalue_invoice:
            score += 30
            evidence.append("客户明确要求低报发票，存在报关与税务违规风险。")
            actions.append("拒绝低报要求，保留正式报价与申报口径。")
        if sample.product_category == "dual_use":
            score += 20
            evidence.append("产品属于双用途或受控类别，出口审查门槛较高。")
        if sample.hs_code_sensitive:
            score += 18
            evidence.append("HS 编码敏感，易触发额外许可证或查验。")
        if sample.hs_code_sensitive and not sample.export_license_ready:
            score += 15
            evidence.append("敏感品类仍未准备出口许可，无法直接放行。")
            missing.append("缺少出口许可、终端用途声明或最终用户文件。")
            actions.append("先补齐许可证与终端用途声明，再谈出货。")
        elif sample.export_license_ready:
            evidence.append("许可材料已就绪，合规准备较完整。")
        if sample.destination_country in self.HIGH_RISK_DESTINATIONS:
            score += 20
            evidence.append("目的国处于高风险区，需额外筛查终端用途。")
        elif sample.destination_country in self.ELEVATED_DESTINATIONS:
            score += 12
            evidence.append("目的国处于提升关注区，需做额外终端客户核验。")
        if sample.transshipment_country:
            score += 8
            evidence.append(
                f"存在过境国 {sample.transshipment_country}，需核对转口链路。"
            )
            if sample.transshipment_country in self.ELEVATED_TRANSIT:
                score += 10
                evidence.append("过境路径位于敏感转运链路，需补查最终收货人。")
                actions.append("补做转口路径、最终收货人与用途声明核验。")
        if sample.documents_complete_ratio < 0.8:
            score += 8
            evidence.append("单证完备度不足 80%，申报与核验链路不完整。")
            missing.append("缺少装箱单、报关要素或最终用户声明。")
        else:
            evidence.append("主要单证基本齐备，合规落地阻力较小。")
        if sample.source_reputation < 0.6:
            score += 8
            evidence.append("线索可信度不足，需防范灰色转单。")

        if not actions:
            actions.append("沿用当前合规清单，按出货前节点复核即可。")
        return score, evidence, missing, actions


class FulfillmentPulseAgent(BaseWarningAgent):
    """交付、毛利与排产风险 Agent。"""

    agent_id = "fulfillment_pulse"
    agent_name = "FulfillmentPulseAgent"
    focus = "fulfillment_margin"

    def _score_sample(
        self, sample: TradeRiskSample
    ) -> tuple[int, list[str], list[str], list[str]]:
        score = 0
        evidence: list[str] = []
        missing: list[str] = []
        actions: list[str] = []
        incoterm = sample.incoterm.upper()

        if sample.production_buffer_days < 5:
            score += 18
            evidence.append("排产缓冲不足 5 天，临时改单会直接冲击交付。")
            actions.append("先锁排产与关键料，再确认装运窗口。")
        elif sample.production_buffer_days < 10:
            score += 10
            evidence.append("排产缓冲不足 10 天，抗扰动能力一般。")
        else:
            evidence.append("排产缓冲相对充足，交付节奏可控。")
        if sample.logistics_delay_days > 14:
            score += 18
            evidence.append("已知物流延误超过 14 天，交付波动较大。")
            actions.append("改走备选航线或拆分批次，避免单点延误。")
        elif sample.logistics_delay_days > 7:
            score += 10
            evidence.append("已知物流延误超过 7 天，需重新校准 ETA。")
        if incoterm in {"DDP", "DAP"}:
            score += 12
            evidence.append(f"贸易术语为 {sample.incoterm}，我方承担更多落地履约责任。")
            actions.append("复核清关责任、税费边界与到门服务 SLA。")
        if sample.gross_margin_ratio < 0.1:
            score += 18
            evidence.append("毛利率低于 10%，容错空间过窄。")
            actions.append("重算报价，确认是否需要调价或砍配。")
        elif sample.gross_margin_ratio < 0.15:
            score += 10
            evidence.append("毛利率低于 15%，需谨慎承接额外售后责任。")
        else:
            evidence.append("毛利率仍有缓冲，可覆盖基础异常成本。")
        if sample.inventory_ready_ratio < 0.5:
            score += 16
            evidence.append("现货可用比例低于 50%，缺货和补料风险偏高。")
            missing.append("缺少关键料到料时间与替代料方案。")
            actions.append("先锁定关键料与替代料，再确认承诺交期。")
        elif sample.inventory_ready_ratio < 0.8:
            score += 8
            evidence.append("库存可用比例低于 80%，需防止滚动缺料。")
        else:
            evidence.append("库存准备较充分，短期履约压力可控。")
        if sample.trade_value_usd > 150000:
            score += 10
            evidence.append("订单金额较大，一旦延误会放大资金和索赔敞口。")
        if sample.deposit_ratio < 0.2:
            score += 15
            evidence.append("低首付下承接大额订单，会挤压生产现金流。")
        if sample.prior_dispute_count > 0:
            score += 6
            evidence.append("历史争议会放大售后和交付沟通成本。")

        if not actions:
            actions.append("按照标准交付节奏推进，保留常规排产与物流监控。")
        return score, evidence, missing, actions


class ThreeAgentCruiseCoordinator:
    """3-Agent 外贸风险结构化样本大巡航。"""

    SCHEMA_VERSION = "2026-03-15.trade-warning.v2"
    CRUISE_NAME = "3-Agent 结构化样本大巡航"

    def __init__(self, *, journal=None, world_model: Optional[WorldModel] = None):
        self.journal = journal or get_action_journal()
        self.world_model = world_model or WorldModel()
        self.agents = [
            CounterpartyRadarAgent(world_model=self.world_model, journal=self.journal),
            ComplianceSentinelAgent(world_model=self.world_model, journal=self.journal),
            FulfillmentPulseAgent(world_model=self.world_model, journal=self.journal),
        ]

    @staticmethod
    def load_sample_set(path: str | Path) -> TradeRiskSampleSet:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return TradeRiskSampleSet.model_validate(payload)

    @staticmethod
    def export_report_schema(path: str | Path) -> dict[str, Any]:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        schema = ForeignTradeWarningReport.model_json_schema()
        target.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return schema

    def run_sample_cruise(
        self,
        sample_set: TradeRiskSampleSet,
        *,
        output_path: Optional[str | Path] = None,
        markdown_path: Optional[str | Path] = None,
        schema_path: Optional[str | Path] = None,
    ) -> ForeignTradeWarningReport:
        context = self.journal.reserve_event_context()
        self.journal.log_event(
            component="ThreeAgentCruiseCoordinator",
            stage="trade_warning",
            action="run_sample_cruise",
            status="started",
            payload={
                "sample_set_id": sample_set.sample_set_id,
                "sample_count": len(sample_set.samples),
            },
            priority="critical",
            context=context,
        )

        warnings: list[CoordinatedWarning] = []
        for sample in sample_set.samples:
            sample_context = self.journal.reserve_event_context(
                parent_trace_id=context["trace_id"],
                exchange_id=self.journal.new_exchange_id(),
                remote_lamport_seq=context["lamport_seq"],
            )
            assessments = [
                agent.analyze(sample, trace_context=sample_context)
                for agent in self.agents
            ]
            warnings.append(self._coordinate_warning(sample, assessments))

        warnings.sort(key=lambda item: item.overall_risk_score, reverse=True)
        summary = self._build_summary(warnings)
        watch_items = self._build_watch_items(warnings)
        report = ForeignTradeWarningReport(
            report_id=f"trade-warning-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            schema_version=self.SCHEMA_VERSION,
            generated_at=datetime.now().isoformat(),
            cruise_name=self.CRUISE_NAME,
            cruise_goal=sample_set.cruise_goal,
            sample_set_id=sample_set.sample_set_id,
            sample_count=len(sample_set.samples),
            agent_roster=sample_set.agent_roles,
            summary=summary,
            warnings=warnings,
            final_watch_items=watch_items,
            trace_context=context,
        )

        if schema_path:
            self.export_report_schema(schema_path)
        if output_path:
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    report.model_dump(mode="json"), ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        if markdown_path:
            target = Path(markdown_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._render_markdown(report), encoding="utf-8")

        self.journal.log_event(
            component="ThreeAgentCruiseCoordinator",
            stage="trade_warning",
            action="run_sample_cruise",
            status="success",
            payload={
                "report_id": report.report_id,
                "critical_count": report.summary.critical_count,
                "blocked_count": report.summary.blocked_count,
            },
            priority="critical",
            context=context,
        )
        return report

    def _coordinate_warning(
        self,
        sample: TradeRiskSample,
        assessments: list[AgentAssessment],
    ) -> CoordinatedWarning:
        coordination_adjustments: list[str] = []
        weighted_score = _clip_score(
            assessments[0].risk_score * 0.4
            + assessments[1].risk_score * 0.35
            + assessments[2].risk_score * 0.25
        )
        high_count = sum(1 for item in assessments if item.risk_score >= 60)
        if high_count >= 2:
            weighted_score = _clip_score(weighted_score + 8)
            coordination_adjustments.append("多 Agent 高分耦合补偿 +8")
        sorted_assessments = sorted(
            assessments, key=lambda item: item.risk_score, reverse=True
        )
        primary_agent = sorted_assessments[0]
        secondary_agent = sorted_assessments[1]
        blocking_issues = self._collect_blocking_issues(sample)
        if blocking_issues:
            weighted_score = _clip_score(
                weighted_score + min(35, 12 * len(blocking_issues))
            )
            coordination_adjustments.append(
                f"阻断项放大 +{min(35, 12 * len(blocking_issues))}"
            )
        elif primary_agent.risk_score >= 90:
            weighted_score = _clip_score(weighted_score + 22)
            coordination_adjustments.append("主风险极高放大 +22")
        elif primary_agent.risk_score >= 80:
            weighted_score = _clip_score(weighted_score + 12)
            coordination_adjustments.append("主风险高位放大 +12")
        compliance_overflow_bonus = self._resolve_compliance_overflow_bonus(
            primary_agent
        )
        if compliance_overflow_bonus > 0:
            weighted_score = _clip_score(weighted_score + compliance_overflow_bonus)
            coordination_adjustments.append(
                f"合规单项溢出补偿 +{compliance_overflow_bonus}"
            )
        confidence_avg = sum(item.confidence for item in assessments) / len(assessments)
        coordination_confidence = round(min(0.95, confidence_avg + 0.05), 3)
        disposition = self._resolve_disposition(weighted_score, blocking_issues)
        decision_band_label = self._build_decision_band_label(
            disposition,
            primary_agent.focus,
            blocking_issues,
            sample.external_conflict_level,
            sample.external_resolution_advice,
        )
        decision_reason = self._build_decision_reason(
            disposition,
            primary_agent,
            secondary_agent,
            weighted_score,
            blocking_issues,
            sample.external_conflict_level,
            sample.external_resolution_advice,
        )
        recommendation = self._build_recommendation(disposition, primary_agent.focus)
        owner = self._resolve_owner(primary_agent.focus)
        immediate_actions = _dedupe_keep_order(
            action
            for assessment in sorted_assessments
            for action in assessment.next_actions
        )
        explanation_tags = self._build_explanation_tags(
            disposition,
            primary_agent.focus,
            secondary_agent.focus,
            bool(blocking_issues),
            bool(compliance_overflow_bonus),
            sample.external_conflict_level,
            sample.external_resolution_advice,
        )

        return CoordinatedWarning(
            sample_id=sample.sample_id,
            scenario_name=sample.scenario_name,
            counterparty_name=sample.counterparty_name,
            scenario_snapshot=ScenarioSnapshot(
                destination_country=sample.destination_country,
                product_name=sample.product_name,
                trade_value_usd=sample.trade_value_usd,
                payment_method=sample.payment_method,
                incoterm=sample.incoterm,
                deposit_ratio=sample.deposit_ratio,
            ),
            overall_risk_score=weighted_score,
            overall_risk_level=_risk_level_from_score(weighted_score),
            coordination_confidence=coordination_confidence,
            primary_risk_vector=primary_agent.focus,
            disposition=disposition,
            decision_band_label=decision_band_label,
            decision_reason=decision_reason,
            explanation_tags=explanation_tags,
            external_conflict_level=sample.external_conflict_level,
            external_match_count=sample.external_match_count,
            external_match_sources=sample.external_match_sources,
            external_resolution_advice=sample.external_resolution_advice,
            external_conflict_summary=sample.external_conflict_summary,
            blocking_issues=blocking_issues,
            final_recommendation=recommendation,
            recommended_owner=owner,
            coordination_adjustments=coordination_adjustments,
            immediate_actions=immediate_actions,
            agent_assessments=assessments,
        )

    @staticmethod
    def _resolve_compliance_overflow_bonus(primary_agent: AgentAssessment) -> int:
        if primary_agent.focus != "compliance_sanctions":
            return 0
        if primary_agent.risk_score >= 65:
            return 12
        if primary_agent.risk_score >= 50:
            return 8
        return 0

    @staticmethod
    def _collect_blocking_issues(sample: TradeRiskSample) -> list[str]:
        issues: list[str] = []
        if sample.sanction_watch_hit:
            issues.append("命中制裁或观察名单")
        if sample.asks_undervalue_invoice:
            issues.append("客户要求低报发票")
        if sample.asks_split_payment_to_third_party:
            issues.append("客户要求第三方收款")
        if sample.asks_release_bl_before_payment:
            issues.append("客户要求先放单后收尾款")
        if sample.deposit_ratio < 0.2 and sample.payment_days >= 60:
            issues.append("回款条件与现金流安全垫失衡")
        return issues

    @staticmethod
    def _resolve_disposition(
        weighted_score: int, blocking_issues: list[str]
    ) -> Disposition:
        if blocking_issues or weighted_score >= 80:
            return "block"
        if weighted_score >= 60:
            return "gated_continue"
        if weighted_score >= 35:
            return "observe"
        return "pass"

    @staticmethod
    def _build_recommendation(disposition: Disposition, focus: str) -> str:
        if disposition == "block":
            return "立即暂停推进，待关键阻断项清零后再恢复。"
        if disposition == "gated_continue":
            return "仅在补齐保障条件后继续推进，并设置人工复核门。"
        if disposition == "observe":
            return "可保守推进，但必须按节点复核付款、单证与交付。"
        return f"当前可继续跟进，重点维持 {focus} 维度的基础监控。"

    @staticmethod
    def _resolve_owner(focus: str) -> str:
        mapping = {
            "counterparty_payment": "销售 + 财务风控",
            "compliance_sanctions": "法务 + 合规",
            "fulfillment_margin": "供应链 + 交付负责人",
        }
        return mapping.get(focus, "项目负责人")

    @staticmethod
    def _build_decision_band_label(
        disposition: Disposition,
        focus: str,
        blocking_issues: list[str],
        external_conflict_level: str,
        external_resolution_advice: str,
    ) -> str:
        focus_map = {
            "counterparty_payment": {
                "block": "credit hard stop",
                "gated_continue": "credit-led gate",
                "observe": "credit watch",
                "pass": "credit greenlight",
            },
            "compliance_sanctions": {
                "block": "compliance veto",
                "gated_continue": "compliance-led gate",
                "observe": "compliance watch",
                "pass": "compliance greenlight",
            },
            "fulfillment_margin": {
                "block": "fulfillment hard stop",
                "gated_continue": "fulfillment-led gate",
                "observe": "fulfillment watch",
                "pass": "fulfillment greenlight",
            },
        }
        if blocking_issues and focus == "compliance_sanctions":
            return "compliance veto"
        if external_conflict_level == "conflict":
            return f"{focus_map.get(focus, {}).get(disposition, 'manual review')}+source-conflict"
        if external_resolution_advice == "manual_review":
            return f"{focus_map.get(focus, {}).get(disposition, 'manual review')}+external-review"
        return focus_map.get(focus, {}).get(disposition, "manual review")

    @staticmethod
    def _build_decision_reason(
        disposition: Disposition,
        primary_agent: AgentAssessment,
        secondary_agent: AgentAssessment,
        weighted_score: int,
        blocking_issues: list[str],
        external_conflict_level: str,
        external_resolution_advice: str,
    ) -> str:
        focus_label = {
            "counterparty_payment": "信用/收款",
            "compliance_sanctions": "合规",
            "fulfillment_margin": "履约",
        }
        primary = focus_label.get(primary_agent.focus, primary_agent.focus)
        secondary = focus_label.get(secondary_agent.focus, secondary_agent.focus)
        if disposition == "block" and blocking_issues:
            reason = f"{primary} 维度触发硬阻断，核心原因是：{'、'.join(blocking_issues[:2])}。"
            if external_conflict_level == "conflict":
                reason += f" 同时外部风险源存在冲突，建议动作为 {external_resolution_advice}。"
            return reason
        if external_conflict_level == "conflict":
            return (
                f"{primary} 与 {secondary} 形成主次耦合，且外部风险源之间存在冲突，"
                f"当前建议动作是 {external_resolution_advice}。"
            )
        if disposition == "block":
            return (
                f"{primary} 风险分达到 {primary_agent.risk_score}，并与 {secondary} 压力叠加，"
                f"使综合分抬升到 {weighted_score}。"
            )
        if disposition == "gated_continue":
            return (
                f"{primary} 主导风险尚未触发一票否决，但与 {secondary} 次级压力耦合后，"
                f"综合分达到 {weighted_score}，因此需要人工设门后推进。"
            )
        if disposition == "observe":
            return (
                f"{primary} 是当前主要拉高项，但其余维度仍保留缓冲，"
                f"综合分 {weighted_score} 适合继续观察而非立即拦截。"
            )
        return (
            f"{primary} 与 {secondary} 均未形成实质性耦合放大，"
            f"综合分 {weighted_score} 维持在可放行区间。"
        )

    @staticmethod
    def _build_explanation_tags(
        disposition: Disposition,
        primary_focus: str,
        secondary_focus: str,
        has_blocking_issue: bool,
        has_compliance_overflow: bool,
        external_conflict_level: str,
        external_resolution_advice: str,
    ) -> list[str]:
        short = {
            "counterparty_payment": "credit",
            "compliance_sanctions": "compliance",
            "fulfillment_margin": "fulfillment",
        }
        tags = [
            disposition,
            f"primary:{short.get(primary_focus, primary_focus)}",
            f"secondary:{short.get(secondary_focus, secondary_focus)}",
        ]
        if disposition == "gated_continue":
            tags.append("manual-review")
        if has_blocking_issue:
            tags.append("hard-boundary")
        if has_compliance_overflow:
            tags.append("compliance-overflow")
        if external_conflict_level == "conflict":
            tags.append("external-conflict")
        if external_resolution_advice == "manual_review":
            tags.append("external-manual-review")
        return tags

    @staticmethod
    def _build_summary(warnings: list[CoordinatedWarning]) -> ReportSummary:
        level_counter = Counter(item.overall_risk_level for item in warnings)
        vector_counter = Counter(item.primary_risk_vector for item in warnings)
        top_ids = [item.sample_id for item in warnings[:3]]
        dominant_vectors = [
            item[0] for item in vector_counter.most_common(3) if item[1] > 0
        ]
        blocked_count = sum(1 for item in warnings if item.disposition == "block")
        return ReportSummary(
            total_samples=len(warnings),
            critical_count=level_counter.get("critical", 0),
            high_count=level_counter.get("high", 0),
            medium_count=level_counter.get("medium", 0),
            low_count=level_counter.get("low", 0),
            blocked_count=blocked_count,
            top_warning_sample_ids=top_ids,
            dominant_risk_vectors=dominant_vectors,
        )

    @staticmethod
    def _build_watch_items(warnings: list[CoordinatedWarning]) -> list[str]:
        action_counter = Counter()
        for warning in warnings:
            if warning.disposition not in {"block", "gated_continue"}:
                continue
            for action in warning.immediate_actions:
                action_counter[action] += 1
        return [item[0] for item in action_counter.most_common(5)]

    @staticmethod
    def _render_markdown(report: ForeignTradeWarningReport) -> str:
        title = (
            "# Actionable Stress Report"
            if "stress" in report.sample_set_id
            else "# 首轮 3-Agent 协同预警报告"
        )
        lines = [
            title,
            "",
            f"- 报告编号：{report.report_id}",
            f"- 生成时间：{report.generated_at}",
            f"- 样本集：{report.sample_set_id}",
            f"- 样本数量：{report.sample_count}",
            f"- 阻断样本：{report.summary.blocked_count}",
            "",
            "## 总览",
            "",
            f"- critical：{report.summary.critical_count}",
            f"- high：{report.summary.high_count}",
            f"- medium：{report.summary.medium_count}",
            f"- low：{report.summary.low_count}",
            f"- 主风险向量：{', '.join(report.summary.dominant_risk_vectors)}",
            "",
            "## 门控决策面板",
            "",
        ]
        gated_items = [
            item for item in report.warnings if item.disposition == "gated_continue"
        ]
        if gated_items:
            for warning in gated_items:
                lines.extend(
                    [
                        f"- {warning.sample_id} | {warning.decision_band_label} | {warning.recommended_owner}",
                        f"  原因：{warning.decision_reason}",
                    ]
                )
        else:
            lines.append("- 当前没有需要人工设门的样本。")

        lines.extend(["", "## 样本明细", ""])
        for warning in report.warnings:
            selected_source = (
                warning.external_conflict_summary.get("top_source") or "none"
            )
            ordered_sources = (
                warning.external_conflict_summary.get("ordered_sources", []) or []
            )
            suppressed_sources = [
                item for item in ordered_sources if item and item != selected_source
            ]
            resolution_basis = (
                warning.external_conflict_summary.get("resolution_basis")
                or "no_external_conflict"
            )
            lines.extend(
                [
                    f"### {warning.sample_id} | {warning.overall_risk_level} | {warning.scenario_name}",
                    f"- 综合风险分：{warning.overall_risk_score}",
                    f"- 主风险向量：{warning.primary_risk_vector}",
                    f"- 处置策略：{warning.disposition}",
                    f"- 解释标签：{warning.decision_band_label}",
                    f"- 外部风险：{warning.external_conflict_level} | hits={warning.external_match_count} | advice={warning.external_resolution_advice}",
                    f"- 外部裁决：selected={selected_source} | suppressed={','.join(suppressed_sources) or 'none'} | reason={resolution_basis} | action={warning.external_resolution_advice}",
                    f"- 决策原因：{warning.decision_reason}",
                    f"- 协调补偿：{'; '.join(warning.coordination_adjustments) if warning.coordination_adjustments else '无'}",
                    f"- 最终建议：{warning.final_recommendation}",
                    f"- 阻断项：{'; '.join(warning.blocking_issues) if warning.blocking_issues else '无'}",
                    f"- 立即动作：{'; '.join(warning.immediate_actions)}",
                    "- 三 Agent：",
                ]
            )
            for assessment in warning.agent_assessments:
                lines.append(
                    f"  - {assessment.agent_name} | {assessment.risk_score} | {assessment.risk_level} | {assessment.evidence[0]}"
                )
            lines.append("")

        if report.final_watch_items:
            lines.extend(["## 总级别关注动作", ""])
            for item in report.final_watch_items:
                lines.append(f"- {item}")
            lines.append("")
        return "\n".join(lines)
