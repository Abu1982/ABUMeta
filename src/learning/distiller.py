"""学习模块侧蒸馏封装。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, cast

from config.constants import (
    CULTURE_SOURCES,
    MAX_ARTICLES_PER_SESSION,
    NEWS_SOURCES,
    SCRAPING_INTERVAL_HOURS,
)
from src.decision import ActionIntent, DecisionBrain
from src.learning.culture_seed_bank import CultureSeedEntry, DEFAULT_CULTURE_SEEDS
from src.learning.github_monitor import GitHubTrendingMonitor
from src.learning.news_parser import NewsParser, ParsedNews
from src.memory import MemoryManager
from src.utils.helpers import sanitize_text, truncate_text
from src.utils.map_exporter import export_evolution_map


class LearningDistiller:
    """把学习输入接到记忆、蒸馏与地图导出链路。"""

    def __init__(
        self,
        memory_manager: Optional[MemoryManager] = None,
        news_parser: Optional[NewsParser] = None,
        github_monitor: Optional[GitHubTrendingMonitor] = None,
    ):
        self.memory_manager = memory_manager or MemoryManager()
        self.news_parser = news_parser or NewsParser()
        self.github_monitor = github_monitor or GitHubTrendingMonitor()
        self.decision_brain = DecisionBrain()

    def ingest_news_url(
        self, url: str, export_map: bool = False, repo_root: Optional[str] = None
    ) -> Dict[str, object]:
        self._assert_learning_allowed(
            ActionIntent(
                domain="learning",
                intent_text=f"抓取并解析新闻链接 {url}",
                strategy_name="news_parse",
                estimated_steps=3,
                energy_cost=0.45,
                tags=("学习", "新闻", "解析"),
            )
        )
        parsed_news = self.news_parser.parse_url(url)
        return self.ingest_parsed_news(
            parsed_news, export_map=export_map, repo_root=repo_root
        )

    def ingest_culture_url(
        self, url: str, export_map: bool = False, repo_root: Optional[str] = None
    ) -> Dict[str, object]:
        self._assert_learning_allowed(
            ActionIntent(
                domain="learning",
                intent_text=f"抓取并解析文化链接 {url}",
                strategy_name="culture_parse",
                estimated_steps=3,
                energy_cost=0.5,
                tags=("学习", "文化", "解析"),
            )
        )
        parsed_news = self.news_parser.parse_url(url)
        return self.ingest_parsed_news(
            parsed_news,
            export_map=export_map,
            repo_root=repo_root,
            trigger_type="culture_parse",
        )

    def ingest_culture_batch(
        self,
        urls: Optional[Iterable[str]] = None,
        seed_entries: Optional[Iterable[CultureSeedEntry]] = None,
        export_map: bool = False,
        repo_root: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        if seed_entries is not None:
            return self._ingest_culture_seed_entries(
                list(seed_entries), export_map=export_map, repo_root=repo_root
            )

        culture_urls = list(urls or CULTURE_SOURCES)[:MAX_ARTICLES_PER_SESSION]
        return [
            self.ingest_culture_url(url, export_map=False, repo_root=repo_root)
            for url in culture_urls
        ]

    def ingest_parsed_news(
        self,
        parsed_news: ParsedNews,
        export_map: bool = False,
        repo_root: Optional[str] = None,
        trigger_type: Optional[str] = None,
    ) -> Dict[str, object]:
        parsed_news = self._compress_parsed_news(parsed_news, trigger_type=trigger_type)
        decision = self.decision_brain.evaluate_learning_intent(
            ActionIntent(
                domain="learning",
                intent_text=f"沉淀学习内容：{parsed_news.title} {parsed_news.summary}",
                strategy_name=trigger_type or parsed_news.category or "news_parse",
                estimated_steps=4,
                energy_cost=0.4 if parsed_news.category != "culture" else 0.5,
                tags=(
                    "学习",
                    parsed_news.category or "learning",
                    parsed_news.source or "",
                ),
                metadata={
                    "source_reputation": getattr(
                        parsed_news, "source_reputation", 0.72
                    ),
                    "source_label": parsed_news.source,
                },
            )
        )
        if not decision.allowed:
            return {
                "memory_ids": [],
                "distillation": {
                    "triggered": False,
                    "created": 0,
                    "skipped": 1,
                    "reason": "decision_brain_blocked",
                    "decision": decision.to_dict(),
                },
                "parsed_news": parsed_news,
                "map_payload": None,
            }
        payload = cast(
            Dict[str, Any], self.news_parser.build_memory_payload(parsed_news)
        )
        memory_id = self.memory_manager.create_memory(**payload)
        created_memory_ids = [int(memory_id)] if memory_id is not None else []
        source_memories = (
            [self.memory_manager.db_manager.get_memory_by_id(int(memory_id))]
            if memory_id is not None
            else []
        )
        source_memories = [memory for memory in source_memories if memory is not None]
        source_memories = self._expand_recent_batch(source_memories)

        final_trigger_type = trigger_type or (
            "culture_parse" if parsed_news.category == "culture" else "news_parse"
        )
        distill_result = self._distill_from_sources(
            source_memories, trigger_type=final_trigger_type
        )
        map_payload = self._maybe_export_map(export_map=export_map, repo_root=repo_root)

        return {
            "memory_ids": created_memory_ids,
            "distillation": distill_result,
            "parsed_news": parsed_news,
            "map_payload": map_payload,
            "decision": decision.to_dict(),
        }

    def ingest_trending(
        self,
        limit: int = MAX_ARTICLES_PER_SESSION,
        export_map: bool = False,
        repo_root: Optional[str] = None,
    ) -> Dict[str, object]:
        self._assert_learning_allowed(
            ActionIntent(
                domain="learning",
                intent_text="批量抓取 GitHub Trending 并沉淀为学习样本",
                strategy_name="github_trending",
                estimated_steps=max(4, limit),
                energy_cost=min(1.0, 0.45 + limit * 0.08),
                tags=("学习", "trending", "批量抓取"),
                metadata={"source_reputation": 0.78, "source_label": "github_trending"},
            )
        )
        ingest_result = self.github_monitor.ingest_trending_repositories(
            self.memory_manager, limit=limit
        )
        ingest_memory_ids = cast(List[int], ingest_result["memory_ids"])
        memory_ids = [int(memory_id) for memory_id in ingest_memory_ids]
        source_memories = [
            self.memory_manager.db_manager.get_memory_by_id(memory_id)
            for memory_id in memory_ids
        ]
        source_memories = [memory for memory in source_memories if memory is not None]

        distill_result = self._distill_from_sources(
            source_memories, trigger_type="github_trending"
        )
        map_payload = self._maybe_export_map(export_map=export_map, repo_root=repo_root)

        return {
            "memory_ids": memory_ids,
            "repositories": ingest_result["repositories"],
            "distillation": distill_result,
            "map_payload": map_payload,
        }

    def run_learning_session(
        self,
        news_urls: Optional[Iterable[str]] = None,
        include_trending: bool = True,
        export_map: bool = False,
        repo_root: Optional[str] = None,
        culture_urls: Optional[Iterable[str]] = None,
        include_culture: bool = False,
    ) -> Dict[str, object]:
        news_urls = list(news_urls or NEWS_SOURCES)[:MAX_ARTICLES_PER_SESSION]
        news_results = []
        for url in news_urls:
            news_results.append(
                self.ingest_news_url(url, export_map=False, repo_root=repo_root)
            )

        culture_results = []
        if include_culture:
            culture_targets = list(culture_urls or CULTURE_SOURCES)[
                :MAX_ARTICLES_PER_SESSION
            ]
            for url in culture_targets:
                culture_results.append(
                    self.ingest_culture_url(url, export_map=False, repo_root=repo_root)
                )

            if not culture_targets:
                culture_results.extend(
                    self.ingest_culture_batch(
                        seed_entries=DEFAULT_CULTURE_SEEDS,
                        export_map=False,
                        repo_root=repo_root,
                    )
                )

        trending_result = None
        if include_trending:
            trending_result = self.ingest_trending(
                limit=MAX_ARTICLES_PER_SESSION, export_map=False, repo_root=repo_root
            )

        map_payload = self._maybe_export_map(export_map=export_map, repo_root=repo_root)

        return {
            "interval_hours": SCRAPING_INTERVAL_HOURS,
            "news_results": news_results,
            "culture_results": culture_results,
            "trending_result": trending_result,
            "map_payload": map_payload,
        }

    def schedule_with_chronos(
        self, chronos_engine, repo_root: Optional[str] = None
    ) -> None:
        chronos_engine.schedule_learning_task(
            lambda: self.run_learning_session(export_map=True, repo_root=repo_root),
            interval_hours=SCRAPING_INTERVAL_HOURS,
        )

    def _distill_from_sources(
        self, source_memories: List, trigger_type: str
    ) -> Dict[str, object]:
        if not source_memories:
            return {
                "triggered": False,
                "created": 0,
                "skipped": 0,
                "wisdom_ids": [],
                "source_memory_ids": [],
                "trigger_type": trigger_type,
            }
        directives = self._build_distillation_directives(trigger_type, source_memories)
        return self.memory_manager.distill_memory(
            source_memories=source_memories,
            trigger_type=trigger_type,
            distillation_directives=directives,
        )

    def _compress_parsed_news(
        self,
        parsed_news: ParsedNews,
        trigger_type: Optional[str] = None,
    ) -> ParsedNews:
        raw_text = sanitize_text(
            " ".join(
                filter(
                    None,
                    [
                        parsed_news.title,
                        parsed_news.summary,
                        parsed_news.raw_text,
                        parsed_news.lesson,
                    ],
                )
            )
        )
        if len(raw_text) <= 100:
            parsed_news.summary = self._normalize_wisdom(parsed_news.summary)
            parsed_news.lesson = self._normalize_wisdom(parsed_news.lesson)
            parsed_news.thought = self._normalize_wisdom(parsed_news.thought)
            return parsed_news

        directives = [
            "把输入压缩成一句不超过40字的中文短句。",
            "保留投资/研判主线，不要输出长段落。",
            "如果出现赛道、基本面、公募、投资机会，优先保留金融语义。",
        ]
        compressed = self.memory_manager.distiller.generate_wisdom(
            context=raw_text,
            trigger_type=trigger_type or parsed_news.category or "news_parse",
            directives=directives,
        )
        compact = self._normalize_wisdom(compressed)
        parsed_news.summary = compact
        parsed_news.lesson = compact
        parsed_news.thought = self._normalize_wisdom(parsed_news.thought)
        parsed_news.event = f"新闻事实: {parsed_news.title}。摘要: {compact}"
        return parsed_news

    def _normalize_wisdom(self, text: str) -> str:
        normalized = sanitize_text(text or "")
        normalized = normalized.replace("；", "，").replace(";", "，")
        if len(normalized) > 40:
            normalized = truncate_text(normalized, 40)
        if normalized and normalized[-1] not in "。！？!?":
            normalized += "。"
        return normalized

    def _build_distillation_directives(
        self,
        trigger_type: str,
        source_memories: List,
    ) -> List[str]:
        directives = [
            "输出一句 16-40 字的中文智慧短句，必须包含因果与行动偏向。",
            "默认遵循无为原则：优先低能耗、少扰动、剪裁冗余步骤。",
            "默认遵循择邻处原则：若来源参差不齐，必须强调先筛选高信誉来源。",
        ]

        wuwei_weight = self.decision_brain.get_cultural_directive_weight("无为")
        zelin_weight = self.decision_brain.get_cultural_directive_weight("择邻处")
        if wuwei_weight > 0.0:
            directives.append(
                f"当前无为基因权重为 {wuwei_weight:.3f}，蒸馏时优先提炼减法、克制与少步骤。"
            )
        if zelin_weight > 0.0:
            directives.append(
                f"当前择邻处基因权重为 {zelin_weight:.3f}，蒸馏时优先强调来源信誉和择优吸收。"
            )

        hints = self._summarize_source_memories(source_memories)
        if hints:
            directives.extend(hints)

        trigger_hints = {
            "github_trending": "如果是趋势学习，强调高价值交付、自动化与社区真实信号。",
            "culture_parse": "如果是文化材料，强调原则、长期主义与人格塑形。",
            "culture_seed": "如果是经典文化种子，优先凝结成可进入决策基因层的法则。",
            "news_parse": "如果是新闻材料，强调驱动因、影响面与可信信号。",
            "autonomous_learning": "如果来自自主巡航学习，强调高价值提取而非琐碎抓取。",
            "autonomous_culture": "如果来自自主巡航文化动作，强调原则落地与制度化表达。",
        }
        if trigger_type in trigger_hints:
            directives.append(trigger_hints[trigger_type])
        return directives

    def _summarize_source_memories(self, source_memories: List) -> List[str]:
        text = " ".join(
            sanitize_text(
                " ".join(filter(None, [memory.event, memory.thought, memory.lesson]))
            )
            for memory in source_memories
        )
        lowered = text.lower()
        directives: List[str] = []
        if "无为" in lowered:
            directives.append("输入中出现无为主题，输出必须体现少扰、减法与克制。")
        if "知足" in lowered:
            directives.append("输入中出现知足主题，输出必须体现边界感与风险收束。")
        if "择邻处" in lowered or "信誉" in lowered:
            directives.append("输入中出现择邻处/信誉主题，输出必须强调先筛来源再吸收。")
        if "性本善" in lowered:
            directives.append("输入中出现性本善主题，输出应兼顾善意与专注教化。")
        if any(keyword in lowered for keyword in ["minimalism", "极简", "大道至简"]):
            directives.append("输入中出现极简主题，输出应强调少步骤与高信噪比。")
        if any(
            keyword in lowered
            for keyword in ["open source", "开源", "community", "社区"]
        ):
            directives.append("输入中出现社区/开源主题，输出应强调制度与长期协作。")
        if any(
            keyword in lowered for keyword in ["sun tzu", "兵法", "孙子", "strategy"]
        ):
            directives.append("输入中出现兵法/谋势主题，输出应强调先观势再定行。")
        if not directives:
            directives.append("输出要把输入沉淀成长期可复用的规则，而不是复述事实。")
        return directives

    def _expand_recent_batch(self, source_memories: List) -> List:
        if len(source_memories) >= self.memory_manager.distiller.min_candidate_count:
            return source_memories
        recent = self.memory_manager.db_manager.get_recent_memories(
            hours=24 * 365, limit=self.memory_manager.distiller.min_candidate_count
        )
        if not recent:
            return source_memories
        ordered = list(source_memories)
        seen = set()
        for memory in source_memories:
            if memory is not None and memory.id is not None:
                seen.add(memory.id)
        for memory in sorted(recent, key=lambda item: item.id):
            if memory is None or memory.id in seen:
                continue
            seen.add(memory.id)
            ordered.append(memory)
            if len(ordered) >= self.memory_manager.distiller.min_candidate_count:
                break
        return ordered

    def _ingest_culture_seed_entries(
        self,
        seed_entries: List[CultureSeedEntry],
        export_map: bool = False,
        repo_root: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        grouped: Dict[str, List[CultureSeedEntry]] = {}
        for entry in seed_entries:
            grouped.setdefault(entry.cluster_key, []).append(entry)

        results: List[Dict[str, object]] = []
        for cluster_key, entries in grouped.items():
            avg_reputation = sum(entry.source_reputation for entry in entries) / max(
                len(entries), 1
            )
            decision = self.decision_brain.evaluate_learning_intent(
                ActionIntent(
                    domain="culture",
                    intent_text="注入文化逻辑种子并沉淀为长期认知基因",
                    strategy_name=cluster_key,
                    estimated_steps=max(2, len(entries)),
                    energy_cost=min(1.0, 0.2 + len(entries) * 0.08),
                    tags=("文化", "经典注入", cluster_key),
                    metadata={
                        "source_reputation": avg_reputation,
                        "source_label": entries[0].source,
                    },
                )
            )
            if not decision.allowed:
                results.append(
                    {
                        "memory_ids": [],
                        "distillation": {
                            "triggered": False,
                            "created": 0,
                            "skipped": 1,
                            "reason": "decision_brain_blocked",
                            "decision": decision.to_dict(),
                        },
                        "seed_titles": [entry.title for entry in entries],
                        "map_payload": None,
                        "decision": decision.to_dict(),
                    }
                )
                continue

            memory_ids: List[int] = []
            source_memories = []
            for entry in entries:
                parsed_news = self._seed_entry_to_parsed_news(entry)
                payload = cast(
                    Dict[str, Any], self.news_parser.build_memory_payload(parsed_news)
                )
                memory_id = self.memory_manager.create_memory(**payload)
                if memory_id is None:
                    continue
                memory_ids.append(int(memory_id))
                memory = self.memory_manager.db_manager.get_memory_by_id(int(memory_id))
                if memory is not None:
                    source_memories.append(memory)

            distill_result = self._distill_from_sources(
                source_memories, trigger_type="culture_seed"
            )
            results.append(
                {
                    "memory_ids": memory_ids,
                    "distillation": distill_result,
                    "seed_titles": [entry.title for entry in entries],
                    "map_payload": None,
                    "decision": decision.to_dict(),
                }
            )

        if export_map:
            map_payload = self._maybe_export_map(export_map=True, repo_root=repo_root)
            for item in results:
                item["map_payload"] = map_payload
        return results

    def _seed_entry_to_parsed_news(self, entry: CultureSeedEntry) -> ParsedNews:
        event = f"文化事实: {entry.title}。摘要: {entry.summary}"
        thought = f"因果分析: {entry.summary}"
        lesson = f"泛化经验: {entry.lesson}"
        return ParsedNews(
            url=entry.source,
            title=entry.title,
            summary=entry.summary,
            entities=[entry.title],
            causal_signals=[entry.summary],
            event=event,
            thought=thought,
            lesson=lesson,
            importance=0.9,
            category="culture",
            source=entry.source,
            fetched_at="1970-01-01T00:00:00",
            raw_text=f"{entry.title} {entry.summary} {entry.lesson}",
            source_reputation=entry.source_reputation,
        )

    def _maybe_export_map(
        self, export_map: bool, repo_root: Optional[str]
    ) -> Optional[Dict[str, object]]:
        if not export_map:
            return None
        return export_evolution_map(self.memory_manager, repo_root=repo_root)

    def _assert_learning_allowed(self, intent: ActionIntent) -> None:
        decision = self.decision_brain.evaluate_learning_intent(intent)
        if decision.allowed:
            return
        reasons = "；".join(decision.reasons)
        raise RuntimeError(f"决策引擎拒绝学习动作: {reasons}")
