"""M12X：业务驱动的站点发现、探测与模板初稿生成。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config.settings import settings
from src.execution.page_fetcher import PageFetcher


@dataclass(frozen=True)
class BusinessIntent:
    business: str
    search_queries: list[str]
    preferred_site_types: list[str]
    extraction_goal: str


@dataclass(frozen=True)
class CandidateSite:
    site_id: str
    label: str
    business_tags: list[str]
    site_type: str
    entry_url: str
    fetch_mode: str
    notes: str = ""


@dataclass(frozen=True)
class PageProbe:
    url: str
    http_status: int | None
    reachable: bool
    requires_playwright: bool
    title: str
    signals: list[str]
    blocked: bool = False
    block_kind: str = ""
    page_kind: str = "generic"
    strategy_hints: list[str] = field(default_factory=list)
    alternative_entry_urls: list[str] = field(default_factory=list)
    strategy_keywords: list[str] = field(default_factory=list)
    llm_strategy_used: bool = False
    strategy_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureQualityReport:
    passed: bool
    row_count: int
    valid_title_count: int
    navigation_noise_count: int
    field_fill_rate: float
    relevance_score: float
    advice: list[str]


SEED_SITE_REGISTRY = [
    CandidateSite(
        site_id="exportersindia_buy_leads",
        label="ExportersIndia Buyers",
        business_tags=["外贸", "外贸询盘", "b2b", "买家线索"],
        site_type="trade_leads",
        entry_url="https://www.exportersindia.com/buyers/",
        fetch_mode="static",
        notes="现有首站模板来源。",
    ),
    CandidateSite(
        site_id="tradeindia_buy_leads",
        label="TradeIndia Buy Leads",
        business_tags=["外贸", "外贸询盘", "b2b", "买家线索"],
        site_type="trade_leads",
        entry_url="https://www.tradeindia.com/TradeLeads/buy/",
        fetch_mode="playwright",
        notes="第二真实站点，入口已验证。",
    ),
    CandidateSite(
        site_id="hackernews_frontpage",
        label="Hacker News",
        business_tags=["科技", "科技情报", "ai", "开源"],
        site_type="news_feed",
        entry_url="https://news.ycombinator.com/",
        fetch_mode="static",
        notes="科技资讯流候选站点。",
    ),
]


class SiteOnboardingPlanner:
    """根据业务意图输出候选站点与模板初稿。"""

    def __init__(self):
        self.page_fetcher = PageFetcher()

    def build_intent(self, business: str) -> BusinessIntent:
        normalized = str(business or "").strip()
        if not normalized:
            raise ValueError("business 不能为空")
        trade_tokens = (
            "外贸",
            "询盘",
            "买家",
            "出口",
            "泵",
            "阀",
            "轴承",
            "电机",
            "传感器",
            "润滑",
            "机床",
            "包装机",
            "钢材",
            "塑料",
            "化工",
            "连接器",
        )
        tech_tokens = (
            "ai",
            "人工智能",
            "芯片",
            "网关",
            "边缘",
            "模型",
            "框架",
            "大模型",
            "agent",
            "网卡",
            "算力",
            "gpu",
        )
        if any(token.lower() in normalized.lower() for token in tech_tokens):
            return BusinessIntent(
                business=normalized,
                search_queries=[
                    f"{normalized} latest updates",
                    f"{normalized} developer blog",
                    f"{normalized} industry analysis",
                ],
                preferred_site_types=["news_feed", "blog_index", "research_feed"],
                extraction_goal="抓取资讯标题、时间、摘要、链接并沉淀为科技情报样本。",
            )
        if any(token in normalized for token in trade_tokens):
            intent = BusinessIntent(
                business=normalized,
                search_queries=[
                    f"{normalized} buy leads suppliers importers",
                    f"{normalized} trade leads rfq",
                    f"{normalized} wholesale suppliers marketplace",
                ],
                preferred_site_types=["trade_leads", "supplier_directory"],
                extraction_goal="抓取买家线索、国家、日期、需求描述并落到 richer CSV。",
            )
            return self._enhance_intent_with_llm(intent)
        intent = BusinessIntent(
            business=normalized,
            search_queries=[
                "technology news latest updates",
                "ai industry analysis news",
                "developer research blog technology",
            ],
            preferred_site_types=["news_feed", "blog_index", "research_feed"],
            extraction_goal="抓取资讯标题、时间、摘要、链接并沉淀为科技情报样本。",
        )
        return self._enhance_intent_with_llm(intent)

    def _enhance_intent_with_llm(self, intent: BusinessIntent) -> BusinessIntent:
        if not (
            settings.OPENAI_API_KEY
            and settings.OPENAI_BASE_URL
            and settings.OPENAI_MODEL
        ):
            return intent
        prompt = (
            "你是 ABU 的行业发现导师。"
            "请根据给定业务主题，返回 JSON："
            '{"business_kind":"trade|tech","search_queries":[...]}。'
            "search_queries 给出 3 条英文搜索词，要求更适合真实站点发现，避免概念解释页。"
            f"业务主题：{intent.business}\n"
            f"当前初始查询：{intent.search_queries}"
        )
        try:
            response = requests.post(
                f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": "你只输出 JSON 对象。"},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=max(15, settings.REQUEST_TIMEOUT),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(content)
            queries = [
                str(item).strip()
                for item in payload.get("search_queries", [])
                if str(item).strip()
            ]
            business_kind = str(payload.get("business_kind") or "").strip().lower()
            if business_kind == "trade":
                preferred = ["trade_leads", "supplier_directory"]
                goal = "抓取买家线索、国家、日期、需求描述并落到 richer CSV。"
            elif business_kind == "tech":
                preferred = ["news_feed", "blog_index", "research_feed"]
                goal = "抓取资讯标题、时间、摘要、链接并沉淀为科技情报样本。"
            else:
                preferred = intent.preferred_site_types
                goal = intent.extraction_goal
            return BusinessIntent(
                business=intent.business,
                search_queries=queries[:3] or intent.search_queries,
                preferred_site_types=preferred,
                extraction_goal=goal,
            )
        except Exception:
            return intent

    def _llm_enabled(self) -> bool:
        return bool(
            settings.OPENAI_API_KEY
            and settings.OPENAI_BASE_URL
            and settings.OPENAI_MODEL
        )

    def _call_llm_json(self, prompt: str) -> dict[str, Any]:
        if not self._llm_enabled():
            return {}
        try:
            response = requests.post(
                f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": "你只输出 JSON 对象。"},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=max(18, settings.REQUEST_TIMEOUT),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            start = str(content).find("{")
            end = str(content).rfind("}")
            if start == -1 or end == -1 or end <= start:
                return {}
            payload = json.loads(str(content)[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _build_page_sketch(
        self,
        candidate: CandidateSite,
        *,
        html: str,
        title: str,
        page_kind: str,
        block_kind: str,
        status_code: int | None,
        limit: int = 8,
    ) -> dict[str, Any]:
        soup = BeautifulSoup(html or "", "lxml")
        links: list[dict[str, str]] = []
        for anchor in soup.select("a[href]"):
            href = str(anchor.get("href") or "").strip()
            text = anchor.get_text(" ", strip=True)
            if not href or not text:
                continue
            if href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            links.append(
                {
                    "text": text[:80],
                    "href": href[:200],
                }
            )
            if len(links) >= limit:
                break
        body_text = self.page_fetcher.extract_main_text(candidate.entry_url, html)[
            :1800
        ]
        return {
            "candidate": {
                "site_id": candidate.site_id,
                "label": candidate.label,
                "site_type": candidate.site_type,
                "entry_url": candidate.entry_url,
                "fetch_mode": candidate.fetch_mode,
                "business_tags": candidate.business_tags,
            },
            "title": title[:240],
            "status_code": status_code,
            "heuristic_page_kind": page_kind,
            "heuristic_block_kind": block_kind or "none",
            "body_excerpt": body_text,
            "links": links,
        }

    def _infer_page_strategy(
        self,
        candidate: CandidateSite,
        *,
        html: str,
        title: str,
        page_kind: str,
        block_kind: str,
        status_code: int | None,
    ) -> dict[str, Any]:
        if not self._llm_enabled() or not html.strip():
            return {}
        if candidate.site_type != "trade_leads":
            return {}
        if not block_kind and page_kind not in {"supplier_directory", "generic"}:
            return {}
        sketch = self._build_page_sketch(
            candidate,
            html=html,
            title=title,
            page_kind=page_kind,
            block_kind=block_kind,
            status_code=status_code,
        )
        prompt = (
            "你是 ABU 的页面摄食策略器。"
            "任务：判断当前页面应该如何被 ABU 吃掉。"
            "只输出 JSON，对象结构固定为："
            '{"page_kind":"trade_lead_hub|supplier_directory|product_detail|article_detail|anti_bot_interruption|login_wall|generic",'
            '"block_kind":"anti_bot_interruption|js_required|login_wall|http_403|http_429|none",'
            '"confidence":0.0,'
            '"next_action":"extract_here|follow_internal_links|switch_entry|skip_candidate",'
            '"recommended_backend":"keep|requests|scrapling|scrapling_dynamic",'
            '"entry_link_keywords":["..."],'
            '"candidate_paths":["/foo","https://example.com/bar"],'
            '"suggested_list_selector":"",'
            '"force_no_fallback":true,'
            '"reason":"一句话"}。'
            "规则："
            "1) 如果是 Pardon Our Interruption / JS challenge / 403 / login wall，就不要建议 extract_here。"
            "2) 如果是供应目录页，优先给 follow_internal_links，并输出 2-5 个适合站内继续深入的关键词或路径。"
            "3) candidate_paths 必须是当前站点内入口；entry_link_keywords 应偏产品词/询盘词。"
            "4) confidence < 0.55 时，尽量保守。"
            f"\n页面素描：{json.dumps(sketch, ensure_ascii=False)}"
        )
        payload = self._call_llm_json(prompt)
        if not payload:
            return {}
        confidence = float(payload.get("confidence") or 0.0)
        if confidence < 0.55:
            return {}
        normalized = {
            "page_kind": str(payload.get("page_kind") or "generic").strip(),
            "block_kind": str(payload.get("block_kind") or "none").strip(),
            "next_action": str(payload.get("next_action") or "extract_here").strip(),
            "recommended_backend": str(
                payload.get("recommended_backend") or "keep"
            ).strip(),
            "suggested_list_selector": str(
                payload.get("suggested_list_selector") or ""
            ).strip(),
            "force_no_fallback": bool(payload.get("force_no_fallback")),
            "reason": str(payload.get("reason") or "").strip(),
            "confidence": round(confidence, 3),
            "entry_link_keywords": [
                str(item).strip().lower()
                for item in payload.get("entry_link_keywords", [])
                if str(item).strip()
            ][:6],
            "candidate_paths": [
                str(item).strip()
                for item in payload.get("candidate_paths", [])
                if str(item).strip()
            ][:6],
        }
        return normalized

    def discover_candidates(
        self,
        intent: BusinessIntent,
        *,
        exclude_known_sites: bool = False,
    ) -> list[CandidateSite]:
        candidates = []
        for site in SEED_SITE_REGISTRY:
            if any(tag in intent.business for tag in site.business_tags) or any(
                site.site_type == site_type for site_type in intent.preferred_site_types
            ):
                candidates.append(site)
        if exclude_known_sites:
            candidates = []
        discovered = self.search_candidates(
            intent, exclude_urls={site.entry_url for site in SEED_SITE_REGISTRY}
        )
        candidates.extend(discovered)
        deduped: dict[str, CandidateSite] = {site.site_id: site for site in candidates}
        return list(deduped.values())

    def search_candidates(
        self,
        intent: BusinessIntent,
        *,
        exclude_urls: set[str] | None = None,
        per_query_limit: int = 5,
    ) -> list[CandidateSite]:
        exclude = {self._normalize_url(url) for url in (exclude_urls or set())}
        discovered: dict[str, CandidateSite] = {}
        for query in intent.search_queries:
            for title, resolved in self._search_public_engines(
                query, per_query_limit=per_query_limit * 2
            ):
                normalized = self._normalize_url(resolved)
                if normalized in exclude:
                    continue
                if not type(self)._looks_like_candidate_site(intent, resolved, title):
                    continue
                site_id = self._site_id_from_url(resolved)
                if site_id in discovered:
                    continue
                inferred_type = self._infer_site_type(intent, resolved)
                discovered[site_id] = CandidateSite(
                    site_id=site_id,
                    label=title or site_id,
                    business_tags=[intent.business],
                    site_type=inferred_type,
                    entry_url=resolved,
                    fetch_mode="playwright"
                    if inferred_type == "trade_leads"
                    else "static",
                    notes=f"M12X 自动搜索发现 | query={query}",
                )
                if len(discovered) >= per_query_limit:
                    break
            if len(discovered) >= per_query_limit:
                break
        if not discovered and any(
            token in intent.business for token in ("外贸", "询盘", "买家")
        ):
            for title, resolved in self._search_trade_domain_priors(
                per_query_limit=per_query_limit * 2
            ):
                normalized = self._normalize_url(resolved)
                if normalized in exclude:
                    continue
                if not type(self)._looks_like_candidate_site(intent, resolved, title):
                    continue
                site_id = self._site_id_from_url(resolved)
                if site_id in discovered:
                    continue
                inferred_type = self._infer_site_type(intent, resolved)
                discovered[site_id] = CandidateSite(
                    site_id=site_id,
                    label=title or site_id,
                    business_tags=[intent.business],
                    site_type=inferred_type,
                    entry_url=resolved,
                    fetch_mode="playwright"
                    if inferred_type == "trade_leads"
                    else "static",
                    notes="M12X 行业域名先验搜索发现",
                )
                if len(discovered) >= per_query_limit:
                    break
        if len(discovered) < per_query_limit:
            for candidate in type(self)._generate_prior_candidates(self, intent):
                normalized = self._normalize_url(candidate.entry_url)
                if normalized in exclude:
                    continue
                discovered.setdefault(candidate.site_id, candidate)
                if len(discovered) >= per_query_limit:
                    break
        return list(discovered.values())

    def _search_public_engines(
        self, query: str, *, per_query_limit: int
    ) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        results.extend(self._search_bing(query, per_query_limit=per_query_limit))
        if len(results) < per_query_limit:
            results.extend(
                self._search_duckduckgo(query, per_query_limit=per_query_limit)
            )
        deduped: dict[str, tuple[str, str]] = {}
        for title, url in results:
            deduped.setdefault(self._normalize_url(url), (title, url))
        return list(deduped.values())[:per_query_limit]

    def _search_bing(
        self, query: str, *, per_query_limit: int
    ) -> list[tuple[str, str]]:
        search_url = f"https://www.bing.com/search?q={quote_plus(query)}"
        try:
            response = requests.get(
                search_url, timeout=12, headers={"User-Agent": "ABU-M12X/1.0"}
            )
            response.raise_for_status()
        except requests.RequestException:
            return []
        soup = BeautifulSoup(response.text, "lxml")
        hits: list[tuple[str, str]] = []
        for anchor in soup.select("li.b_algo h2 a")[:per_query_limit]:
            href = str(anchor.get("href") or "").strip()
            if href.startswith("http"):
                hits.append((anchor.get_text(" ", strip=True), href))
        return hits

    def _search_duckduckgo(
        self, query: str, *, per_query_limit: int
    ) -> list[tuple[str, str]]:
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = requests.get(
                search_url, timeout=12, headers={"User-Agent": "ABU-M12X/1.0"}
            )
            response.raise_for_status()
        except requests.RequestException:
            return []
        soup = BeautifulSoup(response.text, "lxml")
        hits: list[tuple[str, str]] = []
        for anchor in soup.select("a.result__a, a[href*='uddg=']")[:per_query_limit]:
            href = str(anchor.get("href") or "").strip()
            resolved = self._resolve_search_result_url(href)
            if resolved:
                hits.append((anchor.get_text(" ", strip=True), resolved))
        return hits

    def _search_trade_domain_priors(
        self, *, per_query_limit: int
    ) -> list[tuple[str, str]]:
        domain_priors = [
            "globalsources.com",
            "ec21.com",
            "tradekey.com",
            "go4worldbusiness.com",
            "exporthub.com",
            "ecplaza.net",
            "tradeford.com",
            "made-in-china.com",
        ]
        hits: list[tuple[str, str]] = []
        for domain in domain_priors:
            query = f'site:{domain} rfq OR "buy leads" OR importer'
            hits.extend(self._search_bing(query, per_query_limit=2))
            if len(hits) >= per_query_limit:
                break
        return hits[:per_query_limit]

    def _generate_prior_candidates(self, intent: BusinessIntent) -> list[CandidateSite]:
        candidates: list[CandidateSite] = []
        if "trade_leads" in intent.preferred_site_types:
            priors = {
                "globalsources.com": ["/tradeleads", "/buying-request", "/"],
                "ec21.com": ["/buy-leads", "/trade-leads", "/"],
                "tradekey.com": ["/buyoffers.htm", "/buyoffers/", "/"],
                "go4worldbusiness.com": ["/buy/", "/buy-leads/", "/"],
                "exporthub.com": ["/buyers/", "/rfq/", "/"],
                "ecplaza.net": ["/buying/", "/buying-leads/", "/"],
            }
            for domain, paths in priors.items():
                for path in paths:
                    url = f"https://{domain}{path}"
                    candidates.append(
                        CandidateSite(
                            site_id=self._site_id_from_url(url),
                            label=f"{domain}{path}",
                            business_tags=[intent.business],
                            site_type="trade_leads",
                            entry_url=url,
                            fetch_mode="playwright",
                            notes="M12X 行业先验候选生成",
                        )
                    )
        else:
            priors = {
                "techcrunch.com": ["/", "/category/artificial-intelligence/"],
                "arstechnica.com": ["/ai/", "/information-technology/", "/"],
                "venturebeat.com": ["/ai/", "/category/data-infrastructure/", "/"],
                "thenextweb.com": ["/news", "/neural/", "/"],
                "developer.apple.com": ["/news/", "/machine-learning/", "/"],
            }
            for domain, paths in priors.items():
                for path in paths:
                    url = f"https://{domain}{path}"
                    candidates.append(
                        CandidateSite(
                            site_id=self._site_id_from_url(url),
                            label=f"{domain}{path}",
                            business_tags=[intent.business],
                            site_type="news_feed",
                            entry_url=url,
                            fetch_mode="static",
                            notes="M12X 科技先验候选生成",
                        )
                    )
        return candidates

    def probe_page(self, candidate: CandidateSite, timeout: int = 12) -> PageProbe:
        backend = self.page_fetcher.choose_backend(
            fetch_mode=candidate.fetch_mode,
            extraction_profile=(
                "trade_lead_detail"
                if candidate.site_type == "trade_leads"
                else "news_article_detail"
            ),
        )
        fetch = self.page_fetcher.fetch(
            candidate.entry_url,
            backend=backend,
            headers={
                "User-Agent": "ABU-M12X/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=timeout,
        )
        try:
            title = self.page_fetcher.extract_metadata_candidates(html=fetch.html).get(
                "title"
            ) or _extract_title(fetch.html)
            signals = list(fetch.block_signals)
            lower = fetch.html.lower()
            if "date posted" in lower:
                signals.append("has_posted_date")
            if "buyer is looking for" in lower:
                signals.append("has_buy_lead_phrase")
            if fetch.page_kind == "supplier_directory":
                signals.append("page_kind:supplier_directory")
            if fetch.page_kind == "trade_lead_hub":
                signals.append("page_kind:trade_lead_hub")
            llm_strategy = self._infer_page_strategy(
                candidate,
                html=fetch.html,
                title=title,
                page_kind=fetch.page_kind,
                block_kind=fetch.block_kind,
                status_code=fetch.status_code,
            )
            llm_page_kind = str(llm_strategy.get("page_kind") or "").strip()
            llm_block_kind = str(llm_strategy.get("block_kind") or "").strip()
            strategy_hints = list(fetch.strategy_hints)
            if llm_strategy:
                signals.append("llm_strategy_used")
                if llm_page_kind and llm_page_kind != "generic":
                    signals.append(f"llm_page_kind:{llm_page_kind}")
                if llm_block_kind and llm_block_kind != "none":
                    signals.append(f"llm_block_kind:{llm_block_kind}")
                next_action = str(llm_strategy.get("next_action") or "").strip()
                if next_action == "follow_internal_links":
                    strategy_hints.append("follow_internal_links")
                elif next_action == "switch_entry":
                    strategy_hints.append("change_entrypoint")
                elif next_action == "skip_candidate":
                    strategy_hints.append("skip_candidate")
                recommended_backend = str(
                    llm_strategy.get("recommended_backend") or ""
                ).strip()
                if recommended_backend == "scrapling_dynamic":
                    strategy_hints.append("switch_backend:dynamic")
            effective_page_kind = (
                llm_page_kind
                if llm_page_kind
                and llm_page_kind not in {"none", "anti_bot_interruption", "login_wall"}
                else fetch.page_kind
            )
            effective_block_kind = (
                llm_block_kind
                if llm_block_kind and llm_block_kind != "none"
                else fetch.block_kind
            )
            alternative_entry_urls = self._suggest_alternative_entry_urls(candidate)
            for path in llm_strategy.get("candidate_paths", []) if llm_strategy else []:
                resolved = (
                    path
                    if path.startswith("http://") or path.startswith("https://")
                    else urljoin(candidate.entry_url, path)
                )
                if self._normalize_url(resolved) == self._normalize_url(
                    candidate.entry_url
                ):
                    continue
                if resolved not in alternative_entry_urls:
                    alternative_entry_urls.append(resolved)
            requires_playwright = (
                candidate.fetch_mode == "playwright"
                or backend == "scrapling_dynamic"
                or effective_block_kind in {"anti_bot_interruption", "js_required"}
                or any(hint == "switch_backend:dynamic" for hint in strategy_hints)
            )
            return PageProbe(
                url=candidate.entry_url,
                http_status=fetch.status_code,
                reachable=fetch.success and not bool(effective_block_kind),
                requires_playwright=requires_playwright,
                title=title,
                signals=signals + [f"backend:{backend}"],
                blocked=bool(effective_block_kind),
                block_kind=effective_block_kind,
                page_kind=effective_page_kind,
                strategy_hints=strategy_hints,
                alternative_entry_urls=alternative_entry_urls,
                strategy_keywords=list(llm_strategy.get("entry_link_keywords", []))
                if llm_strategy
                else [],
                llm_strategy_used=bool(llm_strategy),
                strategy_payload=llm_strategy,
            )
        except Exception as exc:
            return PageProbe(
                url=candidate.entry_url,
                http_status=None,
                reachable=False,
                requires_playwright=candidate.fetch_mode == "playwright",
                title="",
                signals=[f"request_failed:{exc.__class__.__name__}"],
                blocked=False,
            )

    def build_template_draft(
        self,
        candidate: CandidateSite,
        probe: PageProbe,
    ) -> dict[str, Any]:
        backend_order = self._build_backend_order(candidate, probe)
        strategy_payload = probe.strategy_payload or {}
        if candidate.site_type == "trade_leads":
            directory_like = probe.page_kind == "supplier_directory"
            product_detail_like = probe.page_kind == "product_detail"
            use_direct_buyoffer_links = (
                candidate.site_id == "tradeindia_buy_leads"
                or "has_buy_lead_phrase" in probe.signals
                or product_detail_like
            )
            list_selector = (
                str(strategy_payload.get("suggested_list_selector") or "").strip()
                if str(strategy_payload.get("suggested_list_selector") or "").strip()
                else "a[href*='contact-supplier'], a[href*='contactnow'], a[href*='send-inquiry'], a[href*='/buyoffer/'], a[href*='rfq']"
                if product_detail_like
                else "a[href*='/buyoffer/']"
                if use_direct_buyoffer_links
                else "a[href*='/product-details/'], a[href*='contact-supplier'], a[href*='contactnow'], a[href*='send-inquiry'], a[href*='/buyoffer/'], a[href*='buy-leads'], a[href*='trade-leads'], a[href*='buying-request'], a[href*='rfq'], a[href*='/ec-market/']"
                if directory_like
                else "div[role='option'], option"
                if "has_posted_date" in probe.signals
                else "a, li"
            )
            return {
                "site_id": candidate.site_id,
                "entry_url": candidate.entry_url,
                "source_name": candidate.label.lower().replace(" ", "_"),
                "keyword": "",
                "template_version": "m12x-draft-v1",
                "fetch_mode": "playwright"
                if probe.requires_playwright
                else candidate.fetch_mode,
                "list_selector": list_selector,
                "title_selector": "self" if use_direct_buyoffer_links else "a",
                "link_selector": "self" if use_direct_buyoffer_links else "a",
                "time_selector": "" if use_direct_buyoffer_links else "div, span",
                "location_selector": "" if use_direct_buyoffer_links else "div, span",
                "description_selector": "" if use_direct_buyoffer_links else "div",
                "detail_list_selector": "",
                "backend_order": backend_order,
                "alternative_entry_urls": probe.alternative_entry_urls,
                "strategy_link_keywords": probe.strategy_keywords,
                "force_no_fallback": bool(
                    strategy_payload.get("force_no_fallback") or directory_like
                ),
                "max_expanded_urls": 6,
                "max_attempts_per_target": 10,
                "block_policy": "switch_candidate"
                if probe.blocked
                else "switch_backend",
                "headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                },
                "anti_bot_delay_ms": 2200,
                "max_items": 10,
                "extraction_profile": "trade_lead_detail",
                "quality_score": 0.65
                if product_detail_like
                else 0.45
                if probe.blocked or directory_like
                else 0.78
                if probe.reachable
                else 0.2,
                "probe": asdict(probe),
            }
        return {
            "site_id": candidate.site_id,
            "entry_url": candidate.entry_url,
            "source_name": candidate.label.lower().replace(" ", "_"),
            "keyword": "",
            "template_version": "m12x-draft-v1",
            "fetch_mode": candidate.fetch_mode,
            "list_selector": "tr.athing, a.storylink, span.titleline",
            "title_selector": "a",
            "link_selector": "a",
            "time_selector": "span.age",
            "location_selector": "",
            "description_selector": "",
            "detail_list_selector": "",
            "backend_order": backend_order,
            "alternative_entry_urls": probe.alternative_entry_urls,
            "strategy_link_keywords": probe.strategy_keywords,
            "force_no_fallback": bool(strategy_payload.get("force_no_fallback")),
            "max_expanded_urls": 4,
            "max_attempts_per_target": 6,
            "block_policy": "switch_candidate" if probe.blocked else "switch_backend",
            "headers": {"User-Agent": "ABU-M12X/1.0"},
            "anti_bot_delay_ms": 1000,
            "max_items": 10,
            "extraction_profile": "news_article_detail",
            "quality_score": 0.7 if probe.reachable else 0.2,
            "probe": asdict(probe),
        }

    def plan(self, business: str) -> dict[str, Any]:
        intent = self.build_intent(business)
        candidates = self.discover_candidates(intent)
        probed = []
        template_catalog: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            probe = self.probe_page(candidate)
            template_draft = self.build_template_draft(candidate, probe)
            template_catalog[candidate.site_id] = {
                key: value
                for key, value in template_draft.items()
                if key not in {"site_id", "probe", "quality_score"}
            }
            probed.append(
                {
                    "candidate": asdict(candidate),
                    "probe": asdict(probe),
                    "template_draft": template_draft,
                }
            )
        return {
            "intent": asdict(intent),
            "candidate_count": len(probed),
            "candidates": probed,
            "template_catalog": template_catalog,
        }

    def discover_internal_candidates(
        self,
        candidate: CandidateSite,
        *,
        expected_terms: list[str],
        critical_terms: list[str] | None = None,
        limit: int = 5,
    ) -> list[CandidateSite]:
        probe = self.probe_page(candidate)
        if probe.blocked:
            return self._candidate_variants_from_urls(
                candidate, probe.alternative_entry_urls, limit=limit
            )
        strategy_keywords = [item.lower() for item in probe.strategy_keywords if item]
        backend = "requests"
        if any(signal.startswith("backend:") for signal in probe.signals):
            backend = next(
                signal.split(":", 1)[1]
                for signal in probe.signals
                if signal.startswith("backend:")
            )
        fetch = self.page_fetcher.fetch(
            candidate.entry_url,
            backend=backend,
            headers={"User-Agent": "ABU-M12X/1.0"},
            timeout=12,
        )
        if not fetch.success or not fetch.html.strip():
            return self._candidate_variants_from_urls(
                candidate, probe.alternative_entry_urls, limit=limit
            )
        soup = BeautifulSoup(fetch.html, "lxml")
        links = []
        for anchor in soup.select("a[href]"):
            text = anchor.get_text(" ", strip=True)
            href = str(anchor.get("href") or "").strip()
            if not text or not href:
                continue
            if href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            url = (
                href if href.startswith("http") else urljoin(candidate.entry_url, href)
            )
            links.append((text, url))
        scored = []
        critical_terms = [term.lower() for term in (critical_terms or []) if term]
        generic_trade_hints = [
            "mechanical",
            "machinery",
            "industrial",
            "equipment",
            "components",
            "hydraulic",
            "pump",
            "lubrication",
            "gear pump",
            "product details",
            "contact supplier",
            "send inquiry",
        ]
        pump_bias = any(term in critical_terms for term in ("pump", "lubrication"))
        for text, url in links:
            joined = f"{text} {url}".lower()
            if any(
                token in joined
                for token in (
                    "help",
                    "login",
                    "report",
                    "privacy",
                    "terms",
                    "javascript:",
                )
            ):
                continue
            score = sum(1 for term in expected_terms if term and term in joined)
            score += sum(3 for term in critical_terms if term and term in joined)
            score += sum(4 for term in strategy_keywords if term and term in joined)
            critical_hit_count = sum(
                1 for term in critical_terms if term and term in joined
            )
            if critical_hit_count >= 2:
                score += 4
            if any(
                phrase in joined
                for phrase in (
                    "lubrication pump",
                    "industrial lubrication pump",
                    "edge ai",
                    "边缘 ai",
                    "边缘人工智能",
                )
            ):
                score += 5
            score += sum(1 for term in generic_trade_hints if term in joined)
            if pump_bias and any(
                token in joined
                for token in ("mechanical", "pump", "hydraulic", "valve", "gear")
            ):
                score += 3
            if any(token in joined for token in ("buy lead", "trade lead", "rfq")):
                score += 4
            if "/ec-market/" in url.lower() or "/product-details/" in url.lower():
                score += 3
            if "/product-details/" in url.lower():
                score += 4
            if any(
                token in joined
                for token in (
                    "contact supplier",
                    "inquire now",
                    "send inquiry",
                    "contactnow",
                )
            ):
                score += 3
            if any(token in joined for token in ("agents", "china", "directory")):
                score -= 2
            if score <= 0:
                continue
            scored.append((score, text, url))
        scored.sort(key=lambda item: (-item[0], item[2]))
        internal_candidates: list[CandidateSite] = []
        for _, text, url in scored[:limit]:
            internal_fetch_mode = candidate.fetch_mode
            if "/ec-market/" in url.lower() or "/product-details/" in url.lower():
                internal_fetch_mode = "static"
            internal_candidates.append(
                CandidateSite(
                    site_id=self._site_id_from_url(url),
                    label=text,
                    business_tags=candidate.business_tags,
                    site_type=candidate.site_type,
                    entry_url=url,
                    fetch_mode=internal_fetch_mode,
                    notes=f"M12X 站内二次发现 | parent={candidate.site_id}",
                )
            )
        if probe.strategy_payload.get("next_action") == "switch_entry":
            internal_candidates = self._candidate_variants_from_urls(
                candidate,
                probe.alternative_entry_urls
                + [item.entry_url for item in internal_candidates],
                limit=limit,
            )
        if not internal_candidates and probe.alternative_entry_urls:
            internal_candidates.extend(
                self._candidate_variants_from_urls(
                    candidate, probe.alternative_entry_urls, limit=limit
                )
            )
        return internal_candidates

    def evaluate_capture_rows(
        self,
        rows: list[dict[str, Any]],
        expected_terms: list[str] | None = None,
        critical_terms: list[str] | None = None,
    ) -> CaptureQualityReport:
        valid_titles = 0
        navigation_noise = 0
        filled_fields = 0
        total_fields = 0
        relevance_hits = 0
        directory_like = 0
        advice: list[str] = []
        expected_terms = [term.lower() for term in (expected_terms or []) if term]
        critical_terms = [term.lower() for term in (critical_terms or []) if term]
        critical_hits = 0
        for row in rows:
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip().lower()
            raw_description = str(row.get("raw_description") or "").strip().lower()
            joined_text = f"{title} {raw_description}".lower()
            if (
                title
                and title.lower()
                not in {
                    "more",
                    "login",
                    "sign in",
                    "my inquiries",
                    "support article",
                }
                and "@" not in title
                and "pardon our interruption" not in title.lower()
            ):
                valid_titles += 1
            if any(
                token in title.lower()
                for token in (
                    "password",
                    "membership",
                    "login",
                    "inquiries",
                    "view in english",
                    "support article",
                    "cloudbbs@",
                    "pardon our interruption",
                )
            ):
                navigation_noise += 1
            if url.startswith("mailto:") or "help" in url:
                navigation_noise += 1
            if any(
                token in raw_description
                for token in (
                    "you've disabled javascript",
                    "made us think you were a bot",
                    "captcha",
                    "access denied",
                )
            ):
                navigation_noise += 1
            notes = str(row.get("notes") or "").strip().lower()
            if (
                any(
                    token in joined_text
                    for token in (
                        "manufacturers directory",
                        "suppliers directory",
                        "product catalogs",
                        "wholesale",
                    )
                )
                and "fallback_direct_page" in notes
            ):
                directory_like += 1
            for key in ("published_at", "location", "raw_description"):
                total_fields += 1
                if str(row.get(key) or "").strip():
                    filled_fields += 1
            if expected_terms and any(term in joined_text for term in expected_terms):
                relevance_hits += 1
            if critical_terms and any(term in joined_text for term in critical_terms):
                critical_hits += 1
        field_fill_rate = (
            round(filled_fields / total_fields, 3) if total_fields else 0.0
        )
        relevance_score = round(relevance_hits / len(rows), 3) if rows else 0.0
        if navigation_noise > 0:
            advice.append("收紧链接选择器，排除导航/登录菜单")
        if len(rows) == 0:
            advice.append("疑似反爬页或目标块未命中，需切换后端或入口")
        if field_fill_rate < 0.2:
            advice.append("补时间、地点、描述字段选择器")
        if expected_terms and relevance_score < 0.5:
            advice.append("候选结果与产品意图不匹配，需扩展下一批候选")
        if critical_terms and critical_hits == 0:
            advice.append("未命中产品核心词，当前结果更像泛行业噪声")
        if directory_like > 0:
            advice.append("命中供应目录页，需切换站内入口或下一候选")
        passed = (
            len(rows) >= 1
            and valid_titles >= 1
            and navigation_noise == 0
            and directory_like == 0
            and (not expected_terms or relevance_score >= 0.5)
            and (not critical_terms or critical_hits >= 1)
        )
        return CaptureQualityReport(
            passed=passed,
            row_count=len(rows),
            valid_title_count=valid_titles,
            navigation_noise_count=navigation_noise,
            field_fill_rate=field_fill_rate,
            relevance_score=relevance_score,
            advice=advice,
        )

    def refine_template_draft(
        self,
        candidate: CandidateSite,
        template_draft: dict[str, Any],
        quality: CaptureQualityReport,
    ) -> dict[str, Any]:
        refined = dict(template_draft)
        if quality.navigation_noise_count > 0 and candidate.site_type == "trade_leads":
            refined["list_selector"] = (
                "a[href*='/buyoffer/'], a[href*='buy-leads'], a[href*='trade-leads'], a[href*='buying-request'], a[href*='rfq']"
            )
            refined["title_selector"] = "self"
            refined["link_selector"] = "self"
            refined["description_selector"] = "article p, .description, .content p, p"
        if candidate.site_type == "news_feed":
            refined["list_selector"] = (
                "article h1 a, article h2 a, article h3 a, h1 a:not([href^='mailto:']), h2 a:not([href^='mailto:']), h3 a:not([href^='mailto:'])"
            )
            refined["title_selector"] = "self"
            refined["link_selector"] = "self"
            refined["time_selector"] = "time, span, div"
            refined["description_selector"] = "p, div"
            refined["extraction_profile"] = "news_article_detail"
        if quality.field_fill_rate < 0.2:
            refined["time_selector"] = "time, span, div"
            refined["location_selector"] = "span, div"
            refined["description_selector"] = "p, div"
            refined.setdefault(
                "extraction_profile",
                "trade_lead_detail"
                if candidate.site_type == "trade_leads"
                else "news_article_detail",
            )
        if any("目录页" in item for item in quality.advice):
            refined["list_selector"] = (
                "a[href*='/product-details/'], a[href*='contact-supplier'], a[href*='contactnow'], a[href*='send-inquiry'], a[href*='/ec-market/'], a[href*='/buyoffer/'], a[href*='buy-leads'], a[href*='trade-leads'], a[href*='buying-request'], a[href*='rfq']"
            )
            refined["title_selector"] = "self"
            refined["link_selector"] = "self"
        refined["template_version"] = (
            str(refined.get("template_version", "m12x-draft-v1")) + ".r1"
        )
        return refined

    def _build_backend_order(
        self, candidate: CandidateSite, probe: PageProbe
    ) -> list[str]:
        order: list[str] = []
        if any(hint == "switch_backend:dynamic" for hint in probe.strategy_hints):
            order.extend(["scrapling_dynamic", "scrapling", "requests"])
        elif candidate.fetch_mode == "playwright" or probe.requires_playwright:
            order.extend(["scrapling_dynamic", "scrapling", "requests"])
        else:
            order.extend(["scrapling", "requests", "scrapling_dynamic"])
        deduped: list[str] = []
        for backend in order:
            if backend not in deduped:
                deduped.append(backend)
        return deduped

    def _suggest_alternative_entry_urls(self, candidate: CandidateSite) -> list[str]:
        parsed = urlparse(candidate.entry_url)
        domain = parsed.netloc.lower().replace("www.", "")
        alternatives: list[str] = []
        if "globalsources.com" in domain:
            alternatives.extend(
                [
                    "https://www.globalsources.com/tradeleads",
                    "https://www.globalsources.com/buying-request",
                    "https://www.globalsources.com/",
                ]
            )
        elif "ec21.com" in domain:
            alternatives.extend(
                [
                    "https://www.ec21.com/trade-leads",
                    "https://www.ec21.com/buy-leads",
                    "https://www.ec21.com/ec-market/Pumps--1814.html",
                    "https://www.ec21.com/product-details/",
                    "https://www.ec21.com/ec-market/",
                ]
            )
        elif "tradekey.com" in domain:
            alternatives.extend(
                [
                    "https://www.tradekey.com/buyoffers.htm",
                    "https://www.tradekey.com/buyoffers/",
                ]
            )
        elif "go4worldbusiness.com" in domain:
            alternatives.extend(
                [
                    "https://www.go4worldbusiness.com/buy/",
                    "https://www.go4worldbusiness.com/buy-leads/",
                ]
            )
        deduped: list[str] = []
        normalized_current = self._normalize_url(candidate.entry_url)
        for url in alternatives:
            if self._normalize_url(url) == normalized_current:
                continue
            if url not in deduped:
                deduped.append(url)
        return deduped

    def _candidate_variants_from_urls(
        self,
        candidate: CandidateSite,
        urls: list[str],
        *,
        limit: int,
    ) -> list[CandidateSite]:
        variants: list[CandidateSite] = []
        for url in urls[:limit]:
            variants.append(
                CandidateSite(
                    site_id=self._site_id_from_url(url),
                    label=urlparse(url).path.strip("/") or urlparse(url).netloc,
                    business_tags=candidate.business_tags,
                    site_type=candidate.site_type,
                    entry_url=url,
                    fetch_mode="playwright",
                    notes=f"M12X anti-bot 入口切换 | parent={candidate.site_id}",
                )
            )
        return variants

    @staticmethod
    def _resolve_search_result_url(href: str) -> str:
        if not href:
            return ""
        if href.startswith("http://") or href.startswith("https://"):
            parsed = urlparse(href)
            if "duckduckgo.com" in parsed.netloc:
                target = parse_qs(parsed.query).get("uddg", [""])[0]
                return target or ""
            return href
        return ""

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    @staticmethod
    def _site_id_from_url(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "").replace(".", "_")
        path = parsed.path.strip("/").replace("/", "_") or "root"
        return f"{host}_{path}"

    @staticmethod
    def _infer_site_type(intent: BusinessIntent, url: str) -> str:
        lower = url.lower()
        if any(token in intent.business for token in ("外贸", "询盘", "买家")):
            if any(
                token in lower for token in ("buy", "lead", "trade", "import", "export")
            ):
                return "trade_leads"
            return "supplier_directory"
        return "news_feed"

    @staticmethod
    def _looks_like_candidate_site(
        intent: BusinessIntent, url: str, title: str
    ) -> bool:
        lower = f"{url} {title}".lower()
        blocked_tokens = (
            "zhidao.baidu",
            "baike.baidu",
            "zhihu.com",
            "tieba.baidu",
            "bilibili.com",
            "douyin.com",
            "weforum.org/stories",
            "pathofexile.com",
            "/question/",
            "/answers/",
            "microsoft.com",
            "support.",
            "wikipedia.org",
            "linkedin.com",
        )
        if any(token in lower for token in blocked_tokens):
            return False
        if "trade_leads" in intent.preferred_site_types:
            preferred_domains = (
                "globalsources.com",
                "ec21.com",
                "tradewheel.com",
                "exporthub.com",
                "go4worldbusiness.com",
                "ecplaza.net",
                "made-in-china.com",
                "tradekey.com",
                "tradeford.com",
            )
            if any(domain in lower for domain in preferred_domains):
                return True
            reject = (
                "/news/",
                "/article/",
                "/blog/",
                "gov.uk",
                "government/news",
                "weforum.org",
            )
            if any(token in lower for token in reject):
                return False
            strong_required = (
                "buy lead",
                "trade lead",
                "rfq",
                "importer",
                "supplier",
                "marketplace",
                "wholesale",
            )
            reject = ("game", "forum", "story", "opinion", "news")
            return any(token in lower for token in strong_required) and not any(
                token in lower for token in reject
            )
        required = (
            "developer.apple.com",
            "techcrunch.com",
            "arstechnica.com",
            "venturebeat.com",
            "thenextweb.com",
            "huggingface.co",
            "openai.com",
            "anthropic.com",
            "techcrunch",
            "arstechnica",
            "venturebeat",
            "thenextweb",
            "wired",
            "technology",
            "research",
            "developer",
            "ai",
            "news",
            "blog",
            "hacker",
        )
        reject = ("shop", "buy", "trade", "marketplace", "product")
        return any(token in lower for token in required) and not any(
            token in lower for token in reject
        )


def _extract_title(html: str) -> str:
    start = html.lower().find("<title>")
    end = html.lower().find("</title>")
    if start == -1 or end == -1 or end <= start:
        return ""
    return html[start + 7 : end].strip()
