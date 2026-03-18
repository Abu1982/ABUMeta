"""基于认知基因的决策引擎。"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.utils.helpers import sanitize_text


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP_PATH = REPO_ROOT / "evolution_map.json"
DEFAULT_MANIFESTO_PATH = REPO_ROOT / "decision_manifesto.md"
_RADIUS_BY_Z = {
    1.0: 1.6,
    2.0: 2.5,
    3.0: 3.4,
    4.0: 4.2,
    5.0: 5.0,
}
_GENE_ALIAS_MAP = {
    "溯因止损": "交易未稳",
    "学习复盘": "结果导向",
    "大道至简": "大道至简",
    "兼收并蓄": "兼收并蓄",
    "格物致知": "格物致知",
    "经世致用": "经世致用",
    "文明传统": "文明传统",
    "维护者文": "维护者文化",
    "无为而治": "无为而治",
    "知足不辱": "知足不辱",
    "择邻处": "择邻处",
    "性本善": "性本善",
}
_CATEGORY_CANONICAL_WEIGHT = {
    "finance": "风险厌恶",
    "learning": "结果导向",
    "culture": "逻辑简化与低能耗",
}
_STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "及",
    "在",
    "先",
    "后",
    "再",
    "把",
    "会",
    "要",
    "是",
    "更",
    "并",
    "或",
    "也",
    "让",
    "一个",
    "一种",
    "进行",
    "通过",
    "以及",
    "当前",
    "需要",
    "可以",
    "然后",
    "继续",
    "动作",
    "意图",
    "策略",
    "执行",
    "学习",
    "交易",
}
_HIGH_RISK_MARKERS = (
    "高杠杆",
    "追涨",
    "翻本",
    "先冲",
    "梭哈",
    "激进",
    "aggressive",
    "leverage",
    "allin",
    "忽略波动",
    "不等确认",
)
_CULTURAL_DIRECTIVE_MARKERS = {
    "无为": ("无为", "无为而治", "为道日损", "治大国若烹小鲜"),
    "择邻处": ("择邻处", "信誉", "近善源", "良源", "高信誉"),
    "知足": ("知足", "知止", "不知足", "知足不辱"),
    "性本善": ("性本善", "教贵专", "性相近", "习相远"),
}


@dataclass(frozen=True)
class GeneNode:
    """星团对应的认知基因。"""

    cluster_id: str
    raw_anchor: str
    canonical_gene: str
    category: str
    topic_summary: str
    importance: float
    gravity: float
    weight: float
    x: float
    y: float
    z: float
    contains: tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def source_count(self) -> int:
        return len(self.contains)

    @property
    def vector(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def searchable_text(self) -> str:
        summaries = " ".join(str(item.get("summary", "")) for item in self.contains)
        return sanitize_text(
            f"{self.raw_anchor} {self.canonical_gene} {self.topic_summary} {summaries}"
        )


@dataclass(frozen=True)
class ActionIntent:
    """待裁决动作意图。"""

    domain: str
    intent_text: str
    strategy_name: str = ""
    amount: float = 0.0
    volatility: float = 0.0
    expected_profit: float = 0.0
    estimated_steps: int = 1
    energy_cost: float = 0.2
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_domain(self) -> str:
        lowered = (self.domain or "").strip().lower()
        if lowered in {"treasury", "risk", "finance"}:
            return "treasury"
        if lowered in {"culture", "cultural"}:
            return "culture"
        if lowered in {"learning", "study", "culture"}:
            return "learning"
        return "learning"

    @property
    def searchable_text(self) -> str:
        tag_text = " ".join(self.tags)
        metadata_text = " ".join(
            f"{key}:{value}" for key, value in sorted(self.metadata.items())
        )
        return sanitize_text(
            f"{self.intent_text} {self.strategy_name} {tag_text} {metadata_text}"
        )


@dataclass(frozen=True)
class DecisionOutcome:
    """决策结果。"""

    allowed: bool
    execution_probability: float
    action: str
    matched_gene: Optional[str]
    matched_cluster_id: Optional[str]
    similarity: float
    reasons: tuple[str, ...]
    simplified_plan: tuple[str, ...] = field(default_factory=tuple)
    gene_weight: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "execution_probability": self.execution_probability,
            "action": self.action,
            "matched_gene": self.matched_gene,
            "matched_cluster_id": self.matched_cluster_id,
            "similarity": self.similarity,
            "reasons": list(self.reasons),
            "simplified_plan": list(self.simplified_plan),
            "gene_weight": self.gene_weight,
        }


class DecisionBrain:
    """在执行动作前提供基因过滤与语义干预。"""

    def __init__(self, map_path: Optional[str] = None):
        self.map_path = Path(map_path) if map_path else DEFAULT_MAP_PATH
        self.genes: List[GeneNode] = []
        self._cluster_cache: Dict[str, GeneNode] = {}
        self.cultural_directives: Dict[str, float] = {}
        self.reload()

    def reload(self) -> None:
        self.genes = self._load_genes()
        self._cluster_cache = {gene.cluster_id: gene for gene in self.genes}
        self.cultural_directives = self._build_cultural_directives()

    def evaluate_treasury_intent(self, intent: ActionIntent) -> DecisionOutcome:
        return self.evaluate_intent(intent)

    def evaluate_learning_intent(self, intent: ActionIntent) -> DecisionOutcome:
        return self.evaluate_intent(intent)

    def evaluate_intent(self, intent: ActionIntent) -> DecisionOutcome:
        if not self.genes:
            return DecisionOutcome(
                allowed=True,
                execution_probability=0.5,
                action="allow",
                matched_gene=None,
                matched_cluster_id=None,
                similarity=0.0,
                reasons=("基因图谱缺失，退化为中性模式",),
            )

        intent_vector = self._build_intent_vector(intent)
        scored = [
            (self._score_intent_against_gene(intent, intent_vector, gene), gene)
            for gene in self.genes
        ]
        scored.sort(
            key=lambda item: (item[0], item[1].weight, item[1].cluster_id), reverse=True
        )
        best_score, best_gene = scored[0]

        if best_gene.canonical_gene == "交易未稳" and best_score > 0.6:
            return DecisionOutcome(
                allowed=False,
                execution_probability=0.0,
                action="lock",
                matched_gene=best_gene.canonical_gene,
                matched_cluster_id=best_gene.cluster_id,
                similarity=round(best_score, 6),
                reasons=(
                    "当前交易意图与财域基因“交易未稳”高度共振",
                    "该基因要求先定位根因，再决定是否暴露风险敞口",
                    f"原始星团锚点为“{best_gene.raw_anchor}”",
                ),
                gene_weight=best_gene.weight,
            )

        source_guard = self._source_reputation_guard(intent)
        if source_guard is not None:
            return source_guard

        simplicity_gene = self._find_gene_by_canonical("大道至简")
        wuwei_weight = self.get_cultural_directive_weight("无为")
        simplicity_conflict = 0.0
        if simplicity_gene is not None or wuwei_weight > 0.0:
            simplicity_conflict = self._simplicity_conflict(intent, simplicity_gene)
        if simplicity_conflict > 0.55:
            matched_gene = "大道至简" if simplicity_gene is not None else "无为"
            return DecisionOutcome(
                allowed=False,
                execution_probability=0.0,
                action="refactor_minimalism",
                matched_gene=matched_gene,
                matched_cluster_id=simplicity_gene.cluster_id
                if simplicity_gene
                else None,
                similarity=round(simplicity_conflict, 6),
                reasons=(
                    f"当前动作属于高能耗或高步骤路径，与“{matched_gene}”发生冲突",
                    "系统拒绝冗余步骤，要求先重构为最短验证路径",
                ),
                simplified_plan=self._build_minimalism_plan(intent),
                gene_weight=simplicity_gene.weight if simplicity_gene else wuwei_weight,
            )

        result_gene = self._find_gene_by_canonical("结果导向")
        result_drag = self._result_orientation_drag(intent, result_gene)
        if result_drag > 0.35:
            probability = max(0.2, round(1.0 - result_drag, 6))
            reasons = ["当前学习意图输出物不明确，结果闭环不足"]
            if result_gene is not None:
                reasons.append(f"结果导向基因来源锚点为“{result_gene.raw_anchor}”")
            return DecisionOutcome(
                allowed=True,
                execution_probability=probability,
                action="throttle",
                matched_gene=result_gene.canonical_gene if result_gene else "结果导向",
                matched_cluster_id=result_gene.cluster_id if result_gene else None,
                similarity=round(result_drag, 6),
                reasons=tuple(reasons),
                gene_weight=result_gene.weight if result_gene else 0.0,
            )

        return DecisionOutcome(
            allowed=True,
            execution_probability=(
                1.0
                if intent.normalized_domain == "treasury"
                else round(max(0.35, 1.0 - (1.0 - best_score) * 0.4), 6)
            ),
            action="allow",
            matched_gene=best_gene.canonical_gene,
            matched_cluster_id=best_gene.cluster_id,
            similarity=round(best_score, 6),
            reasons=(
                f"当前意图与基因“{best_gene.canonical_gene}”保持一致，可继续执行",
            ),
            gene_weight=best_gene.weight,
        )

    def generate_manifesto(self, output_path: Optional[str] = None) -> Path:
        destination = Path(output_path) if output_path else DEFAULT_MANIFESTO_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = self._build_manifesto_content()
        destination.write_text(content, encoding="utf-8")
        return destination

    def summarize_genome(self) -> List[Dict[str, Any]]:
        totals: Dict[str, float] = {}
        for gene in self.genes:
            totals[gene.canonical_gene] = (
                totals.get(gene.canonical_gene, 0.0) + gene.weight
            )
        grand_total = sum(totals.values()) or 1.0
        ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        return [
            {
                "gene": gene,
                "weight": round(weight, 6),
                "ratio": round(weight / grand_total, 6),
            }
            for gene, weight in ranked
        ]

    def get_cultural_directive_weight(self, directive: str) -> float:
        return self.cultural_directives.get(directive, 0.0)

    def _load_genes(self) -> List[GeneNode]:
        if not self.map_path.exists():
            return []
        payload = json.loads(self.map_path.read_text(encoding="utf-8"))
        genes: List[GeneNode] = []
        for node in payload.get("wisdom_nodes", []):
            category = self._extract_category(node.get("id", ""))
            raw_anchor = str(node.get("anchor", ""))
            canonical_gene = _GENE_ALIAS_MAP.get(raw_anchor, raw_anchor or "未命名基因")
            importance = float(node.get("importance", 0.0) or 0.0)
            gravity = float(node.get("gravity", 0.0) or 0.0)
            weight = round(0.55 * gravity + 0.45 * importance, 6)
            genes.append(
                GeneNode(
                    cluster_id=str(node.get("id", "")),
                    raw_anchor=raw_anchor,
                    canonical_gene=canonical_gene,
                    category=category,
                    topic_summary=str(node.get("topic_summary", "")),
                    importance=importance,
                    gravity=gravity,
                    weight=weight,
                    x=float(node.get("x", 0.0) or 0.0),
                    y=float(node.get("y", 0.0) or 0.0),
                    z=float(node.get("z", 0.0) or 0.0),
                    contains=tuple(node.get("contains", []) or []),
                )
            )
        return sorted(genes, key=lambda gene: (gene.z, gene.cluster_id))

    def _score_intent_against_gene(
        self,
        intent: ActionIntent,
        intent_vector: tuple[float, float, float],
        gene: GeneNode,
    ) -> float:
        vector_similarity = self._cosine_similarity(intent_vector, gene.vector)
        text_similarity = self._token_similarity(
            intent.searchable_text, gene.searchable_text
        )
        category_bias = (
            1.0 if self._intent_matches_category(intent, gene.category) else 0.0
        )
        score = 0.45 * vector_similarity + 0.35 * text_similarity + 0.20 * category_bias
        if gene.canonical_gene == "交易未稳":
            score += self._finance_risk_bonus(intent)
        return round(min(1.0, score), 6)

    def _build_intent_vector(self, intent: ActionIntent) -> tuple[float, float, float]:
        digest = hashlib.sha256(intent.searchable_text.encode("utf-8")).hexdigest()
        angle = (int(digest[:16], 16) % 360) + int(digest[16:24], 16) / float(16**8)
        z = self._resolve_intent_z(intent)
        radius = _RADIUS_BY_Z.get(z, 4.2)
        theta = math.radians(angle % 360.0)
        return (
            round(radius * math.cos(theta), 6),
            round(radius * math.sin(theta), 6),
            z,
        )

    def _resolve_intent_z(self, intent: ActionIntent) -> float:
        text = intent.searchable_text.lower()
        if intent.normalized_domain == "culture":
            return 5.0
        if any(marker in text for marker in ("极简", "文化", "哲学", "制度", "文明")):
            return 5.0
        if intent.normalized_domain == "treasury":
            return 3.0
        return 4.0

    def _intent_matches_category(self, intent: ActionIntent, category: str) -> bool:
        if intent.normalized_domain == "treasury":
            return category == "finance"
        if intent.normalized_domain == "culture":
            return category == "culture"
        if category == "learning":
            return True
        return category == "culture" and any(
            tag in intent.searchable_text for tag in ("文化", "哲学", "制度", "极简")
        )

    def _simplicity_conflict(
        self, intent: ActionIntent, gene: Optional[GeneNode]
    ) -> float:
        if intent.normalized_domain not in {"treasury", "learning", "culture"}:
            return 0.0
        high_energy = min(1.0, max(0.0, intent.energy_cost))
        step_penalty = min(1.0, max(0.0, (intent.estimated_steps - 3) / 6.0))
        clutter_signal = self._token_similarity(
            intent.searchable_text, "复杂 冗余 重复 多轮 堆叠 高频 大而全"
        )
        cultural_affinity = (
            self._score_intent_against_gene(
                intent, self._build_intent_vector(intent), gene
            )
            if gene is not None
            else 0.0
        )
        wuwei_weight = self.get_cultural_directive_weight("无为")
        score = round(
            0.35 * high_energy
            + 0.35 * step_penalty
            + 0.15 * clutter_signal
            + 0.15 * cultural_affinity,
            6,
        ) + round(min(0.2, 0.18 * wuwei_weight), 6)
        return round(min(1.0, score), 6)

    def _result_orientation_drag(
        self, intent: ActionIntent, gene: Optional[GeneNode]
    ) -> float:
        if intent.normalized_domain not in {"learning", "culture"}:
            return 0.0
        output_signal = self._token_similarity(
            intent.searchable_text, "交付 输出 验证 结果 产出 文档 测试 构建"
        )
        exploration_signal = self._token_similarity(
            intent.searchable_text, "看看 逛一逛 顺便 多收集 广泛 浏览 资料"
        )
        step_penalty = min(1.0, max(0.0, (intent.estimated_steps - 2) / 5.0))
        gene_bonus = (
            gene.weight / max(sum(item.weight for item in self.genes), 1.0)
            if gene is not None
            else 0.0
        )
        return round(
            max(
                0.0,
                0.45 * (1.0 - output_signal)
                + 0.25 * exploration_signal
                + 0.20 * step_penalty
                + 0.10 * gene_bonus,
            ),
            6,
        )

    def _finance_risk_bonus(self, intent: ActionIntent) -> float:
        if intent.normalized_domain != "treasury":
            return 0.0
        lowered = intent.searchable_text.lower()
        marker_hits = sum(1 for marker in _HIGH_RISK_MARKERS if marker in lowered)
        amount_pressure = min(0.18, max(0.0, intent.amount / 2000.0))
        volatility_pressure = min(0.18, max(0.0, intent.volatility) * 0.2)
        keyword_pressure = min(0.32, marker_hits * 0.12)
        return round(keyword_pressure + amount_pressure + volatility_pressure, 6)

    def _build_minimalism_plan(self, intent: ActionIntent) -> tuple[str, ...]:
        steps = [
            "先缩成单一目标，只保留一个可验证输出物",
            "先执行最短路径验证，再决定是否扩展步骤",
            f"当前建议围绕“{intent.strategy_name or intent.intent_text[:12] or '动作目标'}”保留 1-2 个关键动作",
        ]
        return tuple(steps)

    def _find_gene_by_canonical(self, canonical_gene: str) -> Optional[GeneNode]:
        matches = [gene for gene in self.genes if gene.canonical_gene == canonical_gene]
        if not matches:
            return None
        return max(matches, key=lambda gene: (gene.weight, gene.cluster_id))

    def _build_manifesto_content(self) -> str:
        genome = self.summarize_genome()
        lines = ["# ABU 决策宣言", "", "## 当前基因组", ""]
        for item in genome:
            lines.append(
                f"- {item['gene']}：权重 {item['weight']:.3f}，占比 {item['ratio']:.1%}"
            )
        lines.extend(["", "### 星团位点", ""])
        for gene in sorted(
            self.genes, key=lambda item: (-item.weight, item.cluster_id)
        ):
            lines.append(
                f"- {gene.canonical_gene} <- {gene.raw_anchor} | 类别 {gene.category} | 权重 {gene.weight:.3f} | 星团 {gene.cluster_id}"
            )
        lines.extend(["", "### 文化逻辑底座", ""])
        for directive, weight in sorted(
            self.cultural_directives.items(), key=lambda item: item[1], reverse=True
        ):
            lines.append(f"- {directive}：激活权重 {weight:.3f}")
        lines.extend(
            [
                "",
                "## 行为准则",
                "",
                "- 不在根因未明时继续追加风险敞口。",
                "- 不为复杂而复杂，不接受高能耗且无法验证的冗余步骤。",
                "- 不做无法沉淀为结果、规则或可复用资产的学习动作。",
                "",
                "## 演化方向",
                "",
                self._predict_evolution_direction(),
            ]
        )
        return "\n".join(lines) + "\n"

    def _predict_evolution_direction(self) -> str:
        outer_ring = [
            gene for gene in self.genes if math.isclose(gene.z, 5.0, abs_tol=1e-6)
        ]
        if not outer_ring:
            return "- 当前最外层轨道样本不足，系统仍以中性策略运行。"
        dominant = sorted(outer_ring, key=lambda gene: (-gene.weight, gene.cluster_id))
        anchors = "、".join(gene.canonical_gene for gene in dominant[:4])
        return (
            f"- 最外层轨道由 {anchors} 主导，ABU 的下一步性格将继续朝极简、制度化协作、求真与务实落地收敛，"
            "而不是朝高噪声冒险或无边界扩张发展。"
        )

    def _build_cultural_directives(self) -> Dict[str, float]:
        totals = {directive: 0.0 for directive in _CULTURAL_DIRECTIVE_MARKERS}
        total_weight = sum(gene.weight for gene in self.genes) or 1.0
        for gene in self.genes:
            searchable = gene.searchable_text
            for directive, markers in _CULTURAL_DIRECTIVE_MARKERS.items():
                if any(marker in searchable for marker in markers):
                    totals[directive] += gene.weight
        return {
            directive: round(weight / total_weight, 6)
            for directive, weight in totals.items()
            if weight > 0.0
        }

    def _source_reputation_guard(
        self, intent: ActionIntent
    ) -> Optional[DecisionOutcome]:
        if intent.normalized_domain not in {"learning", "culture"}:
            return None
        if "source_reputation" not in intent.metadata:
            return None

        directive_weight = self.get_cultural_directive_weight("择邻处")
        if directive_weight <= 0.0:
            return None

        source_reputation = float(intent.metadata.get("source_reputation", 0.0) or 0.0)
        floor = min(0.9, 0.58 + directive_weight * 0.35)
        if source_reputation >= floor:
            return None

        source_label = str(intent.metadata.get("source_label", "unknown"))
        gap = min(1.0, floor - source_reputation)
        similarity = round(min(1.0, directive_weight + gap), 6)
        return DecisionOutcome(
            allowed=False,
            execution_probability=0.0,
            action="lock",
            matched_gene="择邻处",
            matched_cluster_id=None,
            similarity=similarity,
            reasons=(
                f"文化原则“择邻处”要求先校验数据源信誉，当前来源 {source_label} 信誉值仅为 {source_reputation:.2f}",
                f"当前信誉值低于门槛 {floor:.2f}，系统拒绝直接吸收该输入",
            ),
            gene_weight=directive_weight,
        )

    @staticmethod
    def _extract_category(cluster_id: str) -> str:
        parts = (cluster_id or "").split(":")
        if len(parts) >= 3:
            return parts[1]
        return "learning"

    @staticmethod
    def _token_similarity(left: str, right: str) -> float:
        left_tokens = set(DecisionBrain._tokenize(left))
        right_tokens = set(DecisionBrain._tokenize(right))
        if not left_tokens or not right_tokens:
            return 0.0
        return round(
            len(left_tokens & right_tokens) / len(left_tokens | right_tokens), 6
        )

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        normalized = sanitize_text((text or "").lower())
        chunks: List[str] = []
        buffer = []
        for char in normalized:
            if "\u4e00" <= char <= "\u9fff" or char.isalnum():
                buffer.append(char)
            else:
                if buffer:
                    chunks.append("".join(buffer))
                    buffer = []
        if buffer:
            chunks.append("".join(buffer))

        tokens: List[str] = []
        for chunk in chunks:
            if chunk in _STOPWORDS:
                continue
            tokens.append(chunk)
            if all("\u4e00" <= char <= "\u9fff" for char in chunk) and len(chunk) > 2:
                tokens.extend(
                    chunk[index : index + 2] for index in range(len(chunk) - 1)
                )
        return [token for token in tokens if token and token not in _STOPWORDS]

    @staticmethod
    def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
        if not left or not right:
            return 0.0
        numerator = sum(lv * rv for lv, rv in zip(left, right))
        left_norm = math.sqrt(sum(lv * lv for lv in left))
        right_norm = math.sqrt(sum(rv * rv for rv in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        cosine = numerator / (left_norm * right_norm)
        return round(max(0.0, min(1.0, (cosine + 1.0) / 2.0)), 6)
