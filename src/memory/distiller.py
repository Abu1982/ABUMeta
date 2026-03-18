"""记忆蒸馏模块"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
import time
from typing import Callable, Iterable, List, Optional, Sequence

import requests

from config.constants import EPISODIC_MEMORY_MAX
from config.settings import settings
from src.memory.models import MemoryEntry
from src.utils.logger import log


SPATIAL_Z_MAP = {
    "hardware": 1.0,
    "survival": 2.0,
    "finance": 3.0,
    "learning": 4.0,
    "culture": 5.0,
}

CATEGORY_KEYWORDS = {
    "hardware": (
        "硬件",
        "设备",
        "gpu",
        "cpu",
        "传感器",
        "芯片",
        "驱动",
        "内存",
        "磁盘",
        "显卡",
    ),
    "survival": (
        "故障",
        "生存",
        "资源",
        "告警",
        "风险",
        "兜底",
        "宕机",
        "恢复",
        "降级",
        "异常",
    ),
    "finance": (
        "支付",
        "交易",
        "余额",
        "收益",
        "亏损",
        "订单",
        "账单",
        "成本",
        "财务",
        "退款",
        "fed",
        "inflation",
        "rate",
        "rates",
        "bond",
        "bonds",
        "yield",
        "yields",
        "market",
        "markets",
        "asset",
        "assets",
    ),
    "learning": (
        "学习",
        "模式",
        "经验",
        "总结",
        "优化",
        "复盘",
        "教训",
        "知识",
        "改进",
        "瓶颈",
    ),
    "culture": (
        "极简主义",
        "极简",
        "minimalism",
        "minimalist",
        "open source culture",
        "open-source culture",
        "开源文化",
        "社区文化",
        "哲学",
        "思想",
        "文明",
        "传统",
        "兵法",
        "孙子",
        "孙子兵法",
        "经世",
        "致知",
        "格物",
        "经世致用",
        "大道至简",
        "兼收并蓄",
        "文化",
        "文域",
        "strategy classic",
        "classics",
        "ethos",
        "community ethos",
    ),
}


@dataclass(frozen=True)
class DistillationCandidate:
    memory_id: int
    event: str
    thought: Optional[str]
    lesson: Optional[str]
    importance: float
    sync_transaction_id: str


@dataclass(frozen=True)
class DistillationResult:
    triggered: bool
    created: int
    skipped: int
    wisdom_ids: List[int]
    trigger_type: str
    source_memory_ids: List[int]


class MemoryDistiller:
    """负责把一簇情景记忆蒸馏为高密度语义智慧。"""

    def __init__(
        self, capacity_trigger_ratio: float = 0.9, min_candidate_count: int = 3
    ):
        self.capacity_trigger_ratio = capacity_trigger_ratio
        self.min_candidate_count = min_candidate_count
        self._http_timeout = max(5, settings.REQUEST_TIMEOUT)
        self.last_api_latency_seconds: Optional[float] = None
        self.last_api_trigger_type: Optional[str] = None
        self.last_api_finished_at: Optional[str] = None

    def should_distill(self, episodic_count: int, goal_completed: bool = False) -> bool:
        if goal_completed:
            return True
        threshold = max(1, int(EPISODIC_MEMORY_MAX * self.capacity_trigger_ratio))
        return episodic_count >= threshold

    def select_candidates(
        self, memories: List[MemoryEntry], limit: int = 8
    ) -> List[MemoryEntry]:
        ranked = sorted(
            memories,
            key=lambda memory: (-memory.importance, memory.timestamp, memory.id),
        )
        return ranked[:limit]

    def build_distillation_context(
        self,
        memories: List[MemoryEntry],
        combine_text: Callable[[str, Optional[str], Optional[str]], str],
    ) -> str:
        return "\n".join(
            combine_text(
                str(memory.event), str(memory.thought or ""), str(memory.lesson or "")
            )
            for memory in memories
        )

    def generate_wisdom(
        self,
        context: str,
        generator: Optional[Callable[[str], str]] = None,
        trigger_type: str = "generic",
        directives: Optional[Sequence[str]] = None,
    ) -> str:
        if generator is not None:
            return self._normalize_wisdom(generator(context))

        if (
            settings.OPENAI_API_KEY
            and settings.OPENAI_BASE_URL
            and settings.OPENAI_MODEL
        ):
            try:
                log.info(
                    "🧪 开始远程语义蒸馏 | trigger_type={} | model={} | context_chars={} | directives={}",
                    trigger_type,
                    settings.OPENAI_MODEL,
                    len(context),
                    len(directives or ()),
                )
                llm_text = self._generate_wisdom_via_llm(
                    context=context,
                    trigger_type=trigger_type,
                    directives=list(directives or ()),
                )
                log.info(
                    "✅ 远程语义蒸馏完成 | trigger_type={} | output_chars={}",
                    trigger_type,
                    len(llm_text),
                )
                return self._normalize_wisdom(llm_text)
            except Exception as exc:
                log.warning(
                    "⚠️ 远程蒸馏失败，回退本地规则 | trigger_type={} | error={}",
                    trigger_type,
                    exc,
                )

        fragments = [
            fragment.strip()
            for fragment in context.replace("\n", " ").split()
            if fragment.strip()
        ]
        if not fragments:
            return "经验沉淀为法则。"

        compact = "".join(fragments)[:24]
        if len(compact) < 8:
            compact = f"{compact}值得反复校验"
        return self._normalize_wisdom(f"{compact}，先识别根因再行动。")

    def infer_category(
        self,
        wisdom_text: str,
        source_memories: Optional[Iterable[MemoryEntry]] = None,
    ) -> str:
        haystacks = [wisdom_text or ""]
        for memory in source_memories or []:
            haystacks.append(str(memory.event or ""))
            haystacks.append(str(memory.thought or ""))
            haystacks.append(str(memory.lesson or ""))
        combined = " ".join(haystacks).lower()

        scores = {
            category: sum(1 for keyword in keywords if keyword in combined)
            for category, keywords in CATEGORY_KEYWORDS.items()
        }
        best_category, best_score = max(scores.items(), key=lambda item: item[1])
        return best_category if best_score > 0 else "learning"

    def calculate_spatial_coords(
        self, embedding: Optional[List[float]], category: str
    ) -> tuple[float, float, float]:
        vector = [float(value) for value in (embedding or [])]
        z = self.get_z_for_category(category)
        if not vector:
            return self._explode_from_z(category, z)

        midpoint = max(1, len(vector) // 2)
        left_half = vector[:midpoint]
        right_half = vector[midpoint:] or vector[:midpoint]
        even_components = vector[::2]
        odd_components = vector[1::2] or vector[::2]

        x_raw = (sum(left_half) - sum(right_half)) / max(len(vector), 1)
        y_raw = (sum(even_components) - sum(odd_components)) / max(len(vector), 1)
        x = max(-1.0, min(1.0, math.tanh(x_raw)))
        y = max(-1.0, min(1.0, math.tanh(y_raw)))
        if abs(x) < 1e-6 and abs(y) < 1e-6:
            return self._explode_from_z(category, z, vector)
        return x, y, z

    def calculate_gravity(
        self,
        importance: float,
        source_count: int = 1,
        category: Optional[str] = None,
    ) -> float:
        category_bonus = 0.05 if category == "survival" else 0.0
        density_bonus = max(0, source_count - 1) * 0.05
        return round(max(0.1, importance + density_bonus + category_bonus), 6)

    def get_z_for_category(self, category: str) -> float:
        return SPATIAL_Z_MAP.get(category or "learning", SPATIAL_Z_MAP["learning"])

    def _explode_from_z(
        self,
        category: str,
        z: float,
        embedding: Optional[List[float]] = None,
    ) -> tuple[float, float, float]:
        seed_material = (
            f"{category}|{z}|{','.join(f'{value:.6f}' for value in (embedding or []))}"
        )
        digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
        theta_ratio = int(digest[:16], 16) / float(16**16 - 1)
        theta = theta_ratio * 2 * math.pi
        scale_ratio = int(digest[16:24], 16) / float(16**8 - 1)
        scale = 0.18 + 0.12 * scale_ratio
        radius = max(abs(z) * scale, 0.18)
        x = round(math.sin(theta) * radius, 6)
        y = round(math.cos(theta) * radius, 6)
        if abs(x) < 1e-6 and abs(y) < 1e-6:
            x = round(radius, 6)
        return x, y, z

    def calculate_z_layer_delta(self, query_z: float, node_z: float) -> float:
        return 1.0 / (1.0 + abs(query_z - node_z))

    def calculate_distance_xyz(
        self,
        query_coords: tuple[float, float, float],
        node_coords: tuple[float, float, float],
    ) -> float:
        return math.sqrt(
            sum((left - right) ** 2 for left, right in zip(query_coords, node_coords))
        )

    def calculate_gravity_score(
        self,
        similarity: float,
        gravity: float,
        distance_xyz: float,
        delta_z: float,
    ) -> float:
        return (
            0.4 * similarity + 0.6 * (gravity / ((distance_xyz + 0.01) ** 2)) * delta_z
        )

    def _normalize_wisdom(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            cleaned = "经验沉淀为法则。"
        cleaned = cleaned.replace("\n", " ").strip()
        if len(cleaned) > 40:
            cleaned = cleaned[:39].rstrip("，；。,. ") + "。"
        if cleaned[-1] not in "。；!?！？":
            cleaned += "。"
        return cleaned

    def _generate_wisdom_via_llm(
        self,
        context: str,
        trigger_type: str,
        directives: Sequence[str],
    ) -> str:
        prompt = self._build_distillation_prompt(context, trigger_type, directives)
        started = time.perf_counter()
        response = requests.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENAI_MODEL,
                "temperature": 0.2,
                "top_p": 0.85,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是 ABU 的认知蒸馏核心。"
                            "你必须把输入压缩成一句 16-40 字的中文智慧短句。"
                            "不要复述原文，不要列清单，不要输出解释。"
                            "必须保留因果、约束与行动倾向。"
                            "文化基因优先级：无为=低能耗与减步骤；择邻处=先校验来源信誉。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=self._http_timeout,
        )
        self.last_api_latency_seconds = round(time.perf_counter() - started, 6)
        self.last_api_trigger_type = trigger_type
        self.last_api_finished_at = datetime.now().isoformat()
        log.info(
            "🌐 百炼蒸馏接口已响应 | trigger_type={} | status_code={}",
            trigger_type,
            response.status_code,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("百炼返回中缺少 choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text_fragments = []
            for item in content:
                if isinstance(item, dict):
                    text_fragments.append(str(item.get("text", "")))
                else:
                    text_fragments.append(str(item))
            content = "".join(text_fragments)
        if not content:
            raise RuntimeError("百炼返回内容为空")
        return str(content)

    def _build_distillation_prompt(
        self,
        context: str,
        trigger_type: str,
        directives: Sequence[str],
    ) -> str:
        directive_lines = (
            "\n".join(f"- {item}" for item in directives)
            or "- 保留核心因果与最小行动法则"
        )
        return (
            f"触发类型：{trigger_type}\n"
            f"文化蒸馏约束：\n{directive_lines}\n"
            "请把下面的上下文蒸馏成一句高密度中文智慧短句，"
            "要求体现‘少扰、减法、择优来源、先辨因后行动’中的相关原则。\n"
            f"上下文：\n{context}"
        )
