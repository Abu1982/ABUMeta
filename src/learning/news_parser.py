"""财经新闻解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.learning.crawler import CrawlResult, LearningCrawler
from src.utils.helpers import sanitize_text, truncate_text


FINANCE_KEYWORDS = (
    "stock",
    "market",
    "shares",
    "earnings",
    "fed",
    "inflation",
    "bond",
    "crypto",
    "bitcoin",
    "oil",
    "bank",
    "fund",
    "profit",
    "loss",
    "财",
    "股",
    "市场",
    "利率",
    "通胀",
    "央行",
    "债",
    "基金",
    "收益",
    "亏损",
    "公募",
    "研判",
    "投资机会",
    "赛道",
    "基本面",
)

HARDWARE_KEYWORDS = (
    "gpu",
    "显卡",
    "芯片",
    "硬件",
    "cpu",
    "算力",
    "服务器",
    "nvlink",
    "hbm",
)

CULTURE_KEYWORDS = (
    "minimalism",
    "minimalist",
    "minimal",
    "open source culture",
    "open-source culture",
    "open source community",
    "community ethos",
    "philosophy",
    "philosophical",
    "thought",
    "civilization",
    "tradition",
    "sun tzu",
    "art of war",
    "strategy classic",
    "classics",
    "极简主义",
    "极简",
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
    "格物致知",
    "经世致用",
    "大道至简",
    "兼收并蓄",
    "文化",
)

CAUSAL_MARKERS = (
    "because",
    "due to",
    "after",
    "as ",
    "therefore",
    "so ",
    "led to",
    "driven by",
    "因",
    "由于",
    "因此",
    "导致",
    "带动",
    "推动",
)

ENTITY_STOPWORDS = {
    "The",
    "This",
    "That",
    "With",
    "From",
    "Into",
    "Over",
    "Under",
    "About",
}

SOURCE_REPUTATION_MAP = {
    "finance.sina.com.cn": 0.9,
    "k.sina.cn": 0.88,
    "ftchinese.com": 0.93,
    "www.ftchinese.com": 0.93,
    "36kr.com": 0.84,
    "www.36kr.com": 0.84,
    "www.huxiu.com": 0.83,
    "huxiu.com": 0.83,
    "www.mofcom.gov.cn": 0.96,
    "www.gov.cn": 0.98,
    "www.xinhuanet.com": 0.92,
    "www.chinadaily.com.cn": 0.89,
    "www.ey.com": 0.9,
    "www.mckinsey.com.cn": 0.91,
    "www.mckinsey.com": 0.91,
}


@dataclass
class ParsedNews:
    url: str
    title: str
    summary: str
    entities: List[str]
    causal_signals: List[str]
    event: str
    thought: str
    lesson: str
    importance: float
    category: str
    source: str
    fetched_at: str
    raw_text: str
    source_reputation: float = 0.72
    raw_html: str = ""
    raw_source_data: Optional[bytes | str] = None
    source_encoding: str = ""
    source_content_type: str = ""


class NewsParser:
    """把新闻正文转成可写入记忆系统的结构。"""

    def __init__(self, crawler: Optional[LearningCrawler] = None):
        self.crawler = crawler or LearningCrawler()

    def parse_url(self, url: str) -> ParsedNews:
        crawl_result = self.crawler.fetch(url)
        return self.parse_crawl_result(crawl_result)

    def parse_crawl_result(self, crawl_result: CrawlResult) -> ParsedNews:
        title = sanitize_text(crawl_result.title)
        text = sanitize_text(crawl_result.clean_text or crawl_result.raw_text)
        summary = self._build_summary(text)
        entities = self._extract_entities(title, text)
        causal_signals = self._extract_causal_signals(text)
        category = self._infer_category(title, text)
        event = self._build_event(title, summary, entities)
        thought = self._build_thought(causal_signals, category)
        lesson = self._build_lesson(category, causal_signals, entities)
        importance = self._calculate_importance(
            title, text, causal_signals, entities, category
        )

        return ParsedNews(
            url=crawl_result.url,
            title=title,
            summary=summary,
            entities=entities,
            causal_signals=causal_signals,
            event=event,
            thought=thought,
            lesson=lesson,
            importance=importance,
            category=category,
            source=crawl_result.source,
            fetched_at=crawl_result.fetched_at,
            raw_text=text,
            source_reputation=self._infer_source_reputation(crawl_result.source),
            raw_html=crawl_result.raw_html,
            raw_source_data=crawl_result.raw_source_data,
            source_encoding=crawl_result.source_encoding,
            source_content_type=crawl_result.source_content_type,
        )

    def build_memory_payload(self, parsed_news: ParsedNews) -> Dict[str, object]:
        return {
            "event": parsed_news.event,
            "thought": parsed_news.thought,
            "lesson": parsed_news.lesson,
            "importance": parsed_news.importance,
            "source_type": "web",
            "source_url": parsed_news.url,
            "source_reputation": parsed_news.source_reputation,
            "verification_status": "auto",
            "full_text": parsed_news.raw_text,
            "raw_source_data": parsed_news.raw_source_data
            if parsed_news.raw_source_data is not None
            else (parsed_news.raw_html or parsed_news.raw_text),
            "source_encoding": parsed_news.source_encoding,
            "source_content_type": parsed_news.source_content_type,
            "raw_payload": {
                "title": parsed_news.title,
                "summary": parsed_news.summary,
                "entities": parsed_news.entities,
                "source": parsed_news.source,
                "category": parsed_news.category,
                "fetched_at": parsed_news.fetched_at,
                "source_encoding": parsed_news.source_encoding,
                "source_content_type": parsed_news.source_content_type,
            },
        }

    def _build_summary(self, text: str) -> str:
        if not text:
            return ""
        sentences = [
            segment.strip()
            for segment in text.replace("。", ".").split(".")
            if segment.strip()
        ]
        summary = ". ".join(sentences[:2]) if sentences else text
        return truncate_text(summary, 220)

    def _extract_entities(self, title: str, text: str) -> List[str]:
        haystack = f"{title} {text}"
        matches = []
        for token in haystack.split():
            normalized = token.strip(" ,.;:!?()[]{}\"'")
            if len(normalized) < 2:
                continue
            if normalized in ENTITY_STOPWORDS:
                continue
            if normalized[:1].isupper() or any(
                "\u4e00" <= char <= "\u9fff" for char in normalized
            ):
                matches.append(normalized)
        unique = []
        seen = set()
        for item in matches:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique.append(item)
        return unique[:8]

    def _extract_causal_signals(self, text: str) -> List[str]:
        sentences = [
            sanitize_text(segment)
            for segment in text.replace("\n", ". ").split(".")
            if segment.strip()
        ]
        signals = []
        for sentence in sentences:
            lowered = sentence.lower()
            if any(marker in lowered for marker in CAUSAL_MARKERS):
                signals.append(truncate_text(sentence, 180))
        return signals[:3]

    def _infer_category(self, title: str, text: str) -> str:
        lowered = f"{title} {text}".lower()
        finance_score = sum(1 for keyword in FINANCE_KEYWORDS if keyword in lowered)
        culture_score = sum(1 for keyword in CULTURE_KEYWORDS if keyword in lowered)
        hardware_score = sum(1 for keyword in HARDWARE_KEYWORDS if keyword in lowered)
        if finance_score > 0 and finance_score >= hardware_score:
            return "finance"
        if finance_score > 0 and hardware_score > 0:
            return "finance"
        if culture_score >= 2:
            return "culture"
        if hardware_score > 0:
            return "hardware"
        return "learning"

    def _build_event(self, title: str, summary: str, entities: List[str]) -> str:
        entity_text = f" | 关键实体: {', '.join(entities[:4])}" if entities else ""
        return truncate_text(f"新闻事实: {title}。摘要: {summary}{entity_text}", 500)

    def _build_thought(self, causal_signals: List[str], category: str) -> str:
        if causal_signals:
            return truncate_text(f"因果分析: {'；'.join(causal_signals)}", 400)
        if category == "finance":
            return (
                "因果分析: 市场价格变化通常由宏观预期、盈利数据或流动性变化共同驱动。"
            )
        if category == "culture":
            return "因果分析: 文化文本更适合提炼为价值取向、共同体习惯与跨时代可迁移的认知框架。"
        return "因果分析: 该信息更接近学习观察，适合作为后续模式归纳样本。"

    def _build_lesson(
        self, category: str, causal_signals: List[str], entities: List[str]
    ) -> str:
        if category == "finance":
            if causal_signals:
                return "泛化经验: 先识别驱动价格与风险偏好的根因，再判断行情是否具备持续性。"
            return "泛化经验: 财经新闻应优先拆分为驱动因素、影响对象和持续时间。"
        if category == "culture":
            if causal_signals:
                return "泛化经验: 文化语料要先提炼价值母题，再观察它如何塑造群体协作、审美取向与长期策略。"
            entity_text = f"，重点关注 {', '.join(entities[:3])}" if entities else ""
            return f"泛化经验: 文化学习要沉淀思想母题、共同体精神与可迁移的文明策略{entity_text}。"
        entity_text = f"，重点关注 {', '.join(entities[:3])}" if entities else ""
        return (
            f"泛化经验: 技术与信息流学习要先抽取主题、信号和可迁移模式{entity_text}。"
        )

    def _calculate_importance(
        self,
        title: str,
        text: str,
        causal_signals: List[str],
        entities: List[str],
        category: str,
    ) -> float:
        score = 0.45
        score += min(0.2, len(text) / 4000)
        score += min(0.15, len(causal_signals) * 0.05)
        score += min(0.1, len(entities) * 0.02)
        if category == "finance":
            score += 0.1
        if category == "culture":
            score += 0.08
        if any(keyword in (title or "").lower() for keyword in FINANCE_KEYWORDS):
            score += 0.05
        if (
            sum(1 for keyword in CULTURE_KEYWORDS if keyword in (title or "").lower())
            >= 1
        ):
            score += 0.04
        return max(0.1, min(1.0, round(score, 4)))

    def _infer_source_reputation(self, source: str) -> float:
        normalized = (source or "").lower().strip()
        if not normalized:
            return 0.72
        if normalized in SOURCE_REPUTATION_MAP:
            return SOURCE_REPUTATION_MAP[normalized]
        for domain, score in SOURCE_REPUTATION_MAP.items():
            if normalized.endswith(domain):
                return score
        if normalized.endswith(".gov.cn"):
            return 0.96
        if normalized.endswith(".edu.cn"):
            return 0.9
        if normalized.endswith(".org"):
            return 0.82
        return 0.72
