"""自主搜索与网页感知技能。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from scrapling import DynamicFetcher, Fetcher
except ModuleNotFoundError:

    class Fetcher:  # type: ignore[no-redef]
        @staticmethod
        def configure(**kwargs) -> None:
            return None

        def get(self, *args, **kwargs):
            raise RuntimeError("scrapling is not installed")

    class DynamicFetcher:  # type: ignore[no-redef]
        @staticmethod
        def fetch(*args, **kwargs):
            raise RuntimeError("scrapling is not installed")


from config.settings import settings
from src.learning.crawler import CrawlResult, LearningCrawler
from src.utils.logger import log


_DEFAULT_HUNTING_GROUNDS = [
    "finance.sina.com.cn",
    "www.ftchinese.com",
    "36kr.com",
    "www.huxiu.com",
    "www.mofcom.gov.cn",
    "www.chinadaily.com.cn",
    "www.gov.cn",
    "www.xinhuanet.com",
]
_DEFAULT_PROXY_POOL = [
    "http://127.0.0.1:7890",
]


@dataclass
class SearchHit:
    query: str
    title: str
    url: str
    snippet: str
    source: str
    conflict_similarity: float


@dataclass
class DiscoveryResult:
    queries: List[str]
    selected_hits: List[SearchHit]
    crawled_results: List[CrawlResult]
    skipped: bool = False
    reason: str = ""


class ProxyRotator:
    def __init__(self, proxies: Optional[Iterable[str]] = None):
        raw = list(proxies or [])
        self.proxies = [item.strip() for item in raw if item and item.strip()]
        self.index = 0

    def next(self) -> Optional[str]:
        if not self.proxies:
            return None
        proxy = self.proxies[self.index % len(self.proxies)]
        self.index += 1
        return proxy


class WebExplorer:
    """基于 Scrapling 的自主搜索技能。"""

    def __init__(
        self,
        memory_manager,
        vector_retriever,
        decision_brain,
        map_path: str,
        hunting_grounds: Optional[Iterable[str]] = None,
        proxies: Optional[Iterable[str]] = None,
    ):
        self.memory_manager = memory_manager
        self.vector_retriever = vector_retriever
        self.decision_brain = decision_brain
        self.map_path = Path(map_path)
        self.hunting_grounds = list(hunting_grounds or _DEFAULT_HUNTING_GROUNDS)
        env_proxy_pool = os.getenv("ABU_PROXY_POOL", "")
        extra_proxies = [
            item.strip() for item in env_proxy_pool.split(",") if item.strip()
        ]
        default_pool = (
            proxies if proxies is not None else _DEFAULT_PROXY_POOL + extra_proxies
        )
        self.proxy_rotator = ProxyRotator(default_pool)
        self.crawler = LearningCrawler()
        Fetcher.configure(adaptive=True, adaptive_domain=True)

    def run_discovery(self, top_k: int = 3) -> DiscoveryResult:
        if self._gpu_memory_overloaded():
            return DiscoveryResult(
                queries=[],
                selected_hits=[],
                crawled_results=[],
                skipped=True,
                reason="gpu_memory_over_85_percent",
            )

        queries = self.generate_queries()
        hits: List[SearchHit] = []
        for query in queries:
            hits.extend(self.search_query(query))
        deduped_hits = self._dedupe_hits(hits)
        selected_hits = self.select_conflicting_hits(deduped_hits, top_k=top_k)

        crawled_results: List[CrawlResult] = []
        for hit in selected_hits:
            crawl = self.fetch_page(hit.url)
            if crawl.success:
                crawled_results.append(crawl)

        return DiscoveryResult(
            queries=queries,
            selected_hits=selected_hits,
            crawled_results=crawled_results,
            skipped=False,
            reason="ok",
        )

    def generate_queries(self) -> List[str]:
        genome = self.decision_brain.summarize_genome()[:6]
        genome_text = ", ".join(item["gene"] for item in genome)
        prompt = (
            "你是 ABU 的自主发现器。"
            "请基于当前基因组，生成 3 个中文搜索词，偏重金融、外贸、中国文化，"
            "并且要兼顾对现有知识云的补盲与冲突探索。"
            "返回 JSON 数组，不要解释。\n"
            f"当前基因组：{genome_text}\n"
            '示例风格：["2026 AI 外贸自动化机会", "中国文化 出海 品牌叙事", "低成本现金流 生意趋势"]'
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
                    "temperature": 0.4,
                    "top_p": 0.9,
                    "messages": [
                        {"role": "system", "content": "你只输出 JSON 数组。"},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=max(10, settings.REQUEST_TIMEOUT),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            queries = json.loads(content)
            if isinstance(queries, list):
                normalized = [
                    str(item).strip() for item in queries if str(item).strip()
                ]
                if len(normalized) >= 3:
                    return normalized[:3]
        except Exception as exc:
            log.warning("⚠️ 搜索词生成失败，回退默认查询 | error={}", exc)

        return [
            "2026 AI 赚钱机会 中国市场",
            "中国外贸 低成本增量机会 2026",
            "中国文化 出海 品牌叙事 趋势",
        ]

    def search_query(self, query: str) -> List[SearchHit]:
        hits: List[SearchHit] = []
        domains = self.hunting_grounds[:4]
        for domain in domains:
            site_query = f"site:{domain} {query}"
            hits.extend(self._search_duckduckgo(site_query, query))
        if not hits:
            hits.extend(self._search_duckduckgo(query, query))
        return hits

    def fetch_page(self, url: str) -> CrawlResult:
        proxy = self.proxy_rotator.next()
        kwargs: Dict[str, Any] = {
            "headless": True,
            "disable_resources": True,
            "timeout": max(15000, settings.REQUEST_TIMEOUT * 1000),
        }
        try:
            if proxy:
                kwargs["proxy"] = proxy
            page = DynamicFetcher.fetch(url, **kwargs)
            return self._parse_fetched_page(url, page)
        except Exception as exc:
            if proxy:
                try:
                    kwargs.pop("proxy", None)
                    page = DynamicFetcher.fetch(url, **kwargs)
                    return self._parse_fetched_page(url, page)
                except Exception as retry_exc:
                    exc = retry_exc
            return CrawlResult(
                success=False,
                url=url,
                source=urlparse(url).netloc.lower(),
                fetched_at=datetime.now().isoformat(),
                error=str(exc),
            )

    def _parse_fetched_page(self, url: str, page: Any) -> CrawlResult:
        headers = getattr(page, "headers", None)
        body = getattr(page, "body", b"") or b""
        body_bytes = (
            bytes(body) if isinstance(body, (bytes, bytearray, memoryview)) else b""
        )
        if body_bytes:
            return self.crawler.parse_bytes(
                url=url,
                content=body_bytes,
                status_code=getattr(page, "status", None),
                headers=headers,
                content_type=self.crawler._extract_content_type(headers),
            )

        html = str(getattr(page, "html_content", "") or "")
        return self.crawler.parse_html(
            url=url,
            html=html,
            status_code=getattr(page, "status", None),
            raw_source_data=html,
            source_content_type=self.crawler._extract_content_type(headers),
        )

    def select_conflicting_hits(
        self, hits: List[SearchHit], top_k: int = 3
    ) -> List[SearchHit]:
        node_texts = self._current_cloud_texts()
        if not node_texts:
            return hits[:top_k]
        node_vectors = [
            self.vector_retriever.generate_embedding(text) for text in node_texts
        ]

        scored: List[SearchHit] = []
        for hit in hits:
            hit_vector = self.vector_retriever.generate_embedding(
                f"{hit.title} {hit.snippet} {hit.source}"
            )
            similarities = [
                self._cosine_similarity(hit_vector, node_vector)
                for node_vector in node_vectors
            ]
            hit.conflict_similarity = max(similarities) if similarities else 0.0
            scored.append(hit)
        scored.sort(key=lambda item: (item.conflict_similarity, item.url))
        return scored[:top_k]

    def _search_duckduckgo(
        self, search_query: str, original_query: str
    ) -> List[SearchHit]:
        proxy = self.proxy_rotator.next()
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(search_query)}"
        fetcher = Fetcher()
        kwargs: Dict[str, Any] = {}
        try:
            if proxy:
                kwargs["proxy"] = proxy
            page = fetcher.get(search_url, **kwargs)
        except Exception:
            kwargs.pop("proxy", None)
            page = fetcher.get(search_url)
        html = str(page.html_content or "")
        soup = BeautifulSoup(html, "html.parser")

        hits: List[SearchHit] = []
        for result in soup.select(".result")[:8]:
            anchor = result.select_one("a.result__a")
            if not anchor:
                continue
            raw_url = anchor.get("href", "").strip()
            resolved_url = self._resolve_duckduckgo_redirect(raw_url)
            if not resolved_url:
                continue
            title = anchor.get_text(" ", strip=True)
            snippet_node = result.select_one(".result__snippet")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            hits.append(
                SearchHit(
                    query=original_query,
                    title=title,
                    url=resolved_url,
                    snippet=snippet,
                    source=urlparse(resolved_url).netloc.lower(),
                    conflict_similarity=1.0,
                )
            )
        return hits

    def _resolve_duckduckgo_redirect(self, raw_url: str) -> str:
        if not raw_url:
            return ""
        if raw_url.startswith("//"):
            raw_url = f"https:{raw_url}"
        parsed = urlparse(raw_url)
        if (
            parsed.netloc
            and parsed.netloc != "duckduckgo.com"
            and parsed.netloc != "html.duckduckgo.com"
        ):
            return raw_url
        query = parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return unquote(query["uddg"][0])
        return raw_url

    def _current_cloud_texts(self) -> List[str]:
        if not self.map_path.exists():
            return []
        payload = json.loads(self.map_path.read_text(encoding="utf-8"))
        texts = []
        for node in payload.get("wisdom_nodes", []):
            texts.append(f"{node.get('anchor', '')} {node.get('topic_summary', '')}")
        return texts

    def _dedupe_hits(self, hits: List[SearchHit]) -> List[SearchHit]:
        seen = set()
        deduped: List[SearchHit] = []
        for hit in hits:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            deduped.append(hit)
        return deduped

    def _gpu_memory_overloaded(self) -> bool:
        try:
            result = (
                os.popen(
                    "nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits"
                )
                .read()
                .strip()
            )
            if not result:
                return False
            first = result.splitlines()[0]
            total_raw, used_raw = [item.strip() for item in first.split(",")[:2]]
            total = float(total_raw)
            used = float(used_raw)
            return total > 0 and (used / total) >= 0.85
        except Exception:
            return False

    @staticmethod
    def _cosine_similarity(left: List[float], right: List[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(lv * rv for lv, rv in zip(left, right))
        left_norm = sum(lv * lv for lv in left) ** 0.5
        right_norm = sum(rv * rv for rv in right) ** 0.5
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return max(0.0, min(1.0, numerator / (left_norm * right_norm)))
