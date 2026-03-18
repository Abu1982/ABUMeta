"""ABU 通用页面获取后端。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

_SCRAPLING_AVAILABLE = True
try:
    from scrapling import DynamicFetcher as ScraplingDynamicFetcher
    from scrapling import Fetcher as ScraplingFetcher
except ModuleNotFoundError:
    _SCRAPLING_AVAILABLE = False

    class ScraplingFetcher:  # type: ignore[no-redef]
        def get(self, *args, **kwargs):
            raise RuntimeError("scrapling is not installed")

    class ScraplingDynamicFetcher:  # type: ignore[no-redef]
        @staticmethod
        def fetch(*args, **kwargs):
            raise RuntimeError("scrapling is not installed")


@dataclass(frozen=True)
class PageFetchResult:
    success: bool
    backend: str
    url: str
    status_code: Optional[int]
    html: str
    error: str = ""
    final_url: str = ""
    block_kind: str = ""
    block_signals: list[str] = field(default_factory=list)
    strategy_hints: list[str] = field(default_factory=list)
    page_kind: str = "generic"


@dataclass(frozen=True)
class ContentBlock:
    tag: str
    text: str
    score: float
    link_count: int
    text_length: int
    profile: str


class PageFetcher:
    def fetch(
        self,
        url: str,
        *,
        backend: str,
        headers: Optional[dict[str, str]] = None,
        timeout: int = 12,
    ) -> PageFetchResult:
        if backend == "requests":
            return self._fetch_with_requests(url, headers=headers, timeout=timeout)
        if backend == "scrapling":
            return self._fetch_with_scrapling(url, timeout=timeout)
        if backend == "scrapling_dynamic":
            return self._fetch_with_scrapling_dynamic(url, timeout=timeout)
        return self._fetch_with_requests(url, headers=headers, timeout=timeout)

    def analyze_access(
        self,
        *,
        url: str,
        status_code: Optional[int],
        html: str,
        error: str = "",
    ) -> dict[str, Any]:
        metadata = self.extract_metadata_candidates(html=html) if html else {}
        title = self._sanitize_text(metadata.get("title", ""))
        body_text = self.extract_main_text(url, html)[:4000] if html else ""
        joined = " ".join(
            part for part in (title, body_text, error, url) if str(part).strip()
        ).lower()
        signals: list[str] = []
        strategy_hints: list[str] = []
        block_kind = ""
        page_kind = "generic"

        if status_code in {401, 403, 429}:
            signals.append(f"http_status:{status_code}")

        interruption_tokens = (
            "pardon our interruption",
            "made us think you were a bot",
            "unusual traffic from your network",
            "automated queries",
            "security check",
            "verify you are human",
            "access denied",
            "forbidden",
            "cf-chl",
            "captcha",
        )
        js_block_tokens = (
            "you've disabled javascript",
            "please enable javascript",
            "javascript is disabled",
            "requires javascript",
            "turn on javascript",
        )
        login_phrases = (
            "sign in to continue",
            "login to continue",
            "log in to continue",
            "member login",
            "members only",
            "create an account to continue",
            "view buyer details after login",
            "please sign in",
        )
        supplier_directory_tokens = (
            "manufacturers directory",
            "suppliers directory",
            "product catalogs",
            "catalog directory",
            "wholesale",
            "manufacturers, suppliers",
            "products from",
            "find ",
        )
        trade_hub_tokens = (
            "buy lead",
            "trade lead",
            "buying request",
            "buyer is looking for",
            "rfq",
            "request for quotation",
        )
        product_detail_tokens = (
            "product details",
            "contact supplier",
            "inquire now",
            "send inquiry",
            "minimum order quantity",
            "supply ability",
            "port:",
        )
        trade_product_signal = any(
            token in joined
            for token in (
                *supplier_directory_tokens,
                *trade_hub_tokens,
                *product_detail_tokens,
                "/product-details/",
                "/ec-market/",
                "pump",
                "lubrication",
                "gear pump",
            )
        )
        hard_interruption_tokens = (
            "pardon our interruption",
            "made us think you were a bot",
            "unusual traffic from your network",
            "automated queries",
            "security check",
            "verify you are human",
            "cf-chl",
            "captcha",
        )
        soft_access_denied_tokens = (
            "access denied",
            "forbidden",
        )

        if any(token in joined for token in hard_interruption_tokens):
            block_kind = "anti_bot_interruption"
            strategy_hints.extend(
                ["switch_backend:dynamic", "change_entrypoint", "skip_candidate"]
            )
            signals.append("block:anti_bot_interruption")
        elif any(token in joined for token in soft_access_denied_tokens):
            if trade_product_signal and status_code not in {401, 403, 429}:
                signals.append("anti_bot_guard:product_signal_override")
            else:
                block_kind = "anti_bot_interruption"
                strategy_hints.extend(
                    ["switch_backend:dynamic", "change_entrypoint", "skip_candidate"]
                )
                signals.append("block:anti_bot_interruption")
        elif any(token in joined for token in js_block_tokens):
            block_kind = "js_required"
            strategy_hints.extend(
                ["switch_backend:dynamic", "change_entrypoint", "skip_candidate"]
            )
            signals.append("block:js_required")
        else:
            login_score = sum(1 for phrase in login_phrases if phrase in joined)
            if login_score >= 1 and any(
                token in joined for token in ("buyer", "inquiry", "member", "account")
            ):
                block_kind = "login_wall"
                strategy_hints.extend(["change_entrypoint", "skip_candidate"])
                signals.append("block:login_wall")
            elif status_code in {401, 403, 429} and not trade_product_signal:
                block_kind = f"http_{status_code}"
                strategy_hints.extend(
                    ["switch_backend:dynamic", "change_entrypoint", "skip_candidate"]
                )

        if any(token in joined for token in trade_hub_tokens):
            page_kind = "trade_lead_hub"
            signals.append("page_kind:trade_lead_hub")
            strategy_hints.append("extract_directly")
        elif (
            "/ec-market/" in url.lower()
            and "--" not in url.lower()
            and any(
                token in joined
                for token in ("pump", "lubrication", "gear pump", "hydraulic")
            )
        ):
            page_kind = "product_detail"
            signals.append("page_kind:product_detail")
            strategy_hints.append("extract_directly")
        elif (
            any(token in joined for token in product_detail_tokens)
            or "/product-details/" in url.lower()
        ):
            page_kind = "product_detail"
            signals.append("page_kind:product_detail")
            strategy_hints.append("extract_directly")
        elif any(token in joined for token in supplier_directory_tokens):
            page_kind = "supplier_directory"
            signals.append("page_kind:supplier_directory")
            strategy_hints.append("follow_internal_entry")
            if "/ec-market/" in url.lower() or "pump" in joined:
                strategy_hints.append("follow_product_detail")
        elif "/ec-market/" in url.lower() and trade_product_signal:
            page_kind = "supplier_directory"
            signals.append("page_kind:supplier_directory")
            strategy_hints.extend(["follow_internal_entry", "follow_product_detail"])

        deduped_hints: list[str] = []
        for hint in strategy_hints:
            if hint not in deduped_hints:
                deduped_hints.append(hint)

        return {
            "title": title,
            "body_text": body_text,
            "block_kind": block_kind,
            "block_signals": signals,
            "strategy_hints": deduped_hints,
            "page_kind": page_kind,
            "hard_block": bool(block_kind),
        }

    def extract_main_text(self, url: str, html: str) -> str:
        if not html.strip():
            return ""
        blocks = self.extract_content_blocks(
            html=html, extraction_profile="news_article_detail"
        )
        if blocks:
            return blocks[0].text
        soup = BeautifulSoup(html, "html.parser")
        return self._sanitize_text(soup.get_text(" ", strip=True))

    def extract_content_blocks(
        self, *, html: str, extraction_profile: str, limit: int = 5
    ) -> list[ContentBlock]:
        if not html.strip():
            return []
        soup = BeautifulSoup(html, "html.parser")
        self._prune_noise(soup)
        selectors = ["article", "main", "section", "div", "li"]
        scored_blocks: list[ContentBlock] = []
        for selector in selectors:
            for node in soup.select(selector):
                text = self._sanitize_text(node.get_text(" ", strip=True))
                if len(text) < 40:
                    continue
                link_count = len(node.select("a[href]"))
                score = self._score_block(node, text, link_count, extraction_profile)
                if score <= 0:
                    continue
                scored_blocks.append(
                    ContentBlock(
                        tag=node.name or selector,
                        text=text[:1200],
                        score=round(score, 3),
                        link_count=link_count,
                        text_length=len(text),
                        profile=extraction_profile,
                    )
                )
        scored_blocks.sort(
            key=lambda item: (item.score, item.text_length), reverse=True
        )
        return scored_blocks[:limit]

    def extract_metadata_candidates(self, *, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        title_node = soup.select_one("meta[property='og:title'], title, h1")
        if title_node is not None:
            if getattr(title_node, "get", None) is not None:
                title = str(title_node.get("content") or "").strip()
            title = title or self._sanitize_text(title_node.get_text(" ", strip=True))
        description = ""
        desc_node = soup.select_one(
            "meta[name='description'], meta[property='og:description']"
        )
        if desc_node is not None and getattr(desc_node, "get", None) is not None:
            description = str(desc_node.get("content") or "").strip()
        return {"title": title, "description": description}

    def _prune_noise(self, soup: BeautifulSoup) -> None:
        for tag in soup(
            [
                "script",
                "style",
                "noscript",
                "svg",
                "header",
                "footer",
                "nav",
                "form",
                "aside",
            ]
        ):
            tag.decompose()

    def _score_block(
        self,
        node: Any,
        text: str,
        link_count: int,
        extraction_profile: str,
    ) -> float:
        score = min(len(text) / 120.0, 10.0)
        attrs = " ".join(
            str(item) for item in [node.get("class", []), node.get("id", "")] if item
        ).lower()
        positive_tokens = {
            "trade_lead_detail": [
                "product",
                "detail",
                "request",
                "lead",
                "content",
                "description",
                "buy",
            ],
            "news_article_detail": [
                "article",
                "content",
                "post",
                "detail",
                "news",
                "blog",
                "story",
            ],
        }
        negative_tokens = [
            "menu",
            "nav",
            "footer",
            "header",
            "sidebar",
            "breadcrumb",
            "share",
        ]
        score += sum(
            1.8
            for token in positive_tokens.get(extraction_profile, [])
            if token in attrs
        )
        score -= sum(2.0 for token in negative_tokens if token in attrs)
        score -= min(link_count, 12) * 0.35
        text_lower = text.lower()
        if extraction_profile == "trade_lead_detail":
            if any(
                token in text_lower
                for token in (
                    "supplier",
                    "manufacturer",
                    "request",
                    "buy",
                    "pump",
                    "lubrication",
                )
            ):
                score += 2.5
        if extraction_profile == "news_article_detail":
            if any(
                token in text_lower
                for token in (
                    "ai",
                    "edge",
                    "cloud",
                    "kubernetes",
                    "article",
                    "community",
                )
            ):
                score += 2.5
        return score

    @staticmethod
    def _sanitize_text(text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        return text

    def choose_backend(self, *, fetch_mode: str, extraction_profile: str) -> str:
        if not _SCRAPLING_AVAILABLE:
            return "requests"
        if fetch_mode == "playwright":
            return "scrapling_dynamic"
        if extraction_profile in {"trade_lead_detail", "news_article_detail"}:
            return "scrapling"
        return "requests"

    def _fetch_with_requests(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]],
        timeout: int,
    ) -> PageFetchResult:
        try:
            response = requests.get(url, timeout=timeout, headers=headers or {})
            analysis = self.analyze_access(
                url=response.url or url,
                status_code=response.status_code,
                html=response.text,
            )
            return PageFetchResult(
                success=response.status_code < 400,
                backend="requests",
                url=url,
                status_code=response.status_code,
                html=response.text,
                error=""
                if response.status_code < 400
                else f"HTTP {response.status_code}",
                final_url=response.url or url,
                block_kind=analysis["block_kind"],
                block_signals=analysis["block_signals"],
                strategy_hints=analysis["strategy_hints"],
                page_kind=analysis["page_kind"],
            )
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            html = getattr(response, "text", "") or ""
            final_url = getattr(response, "url", url) or url
            analysis = self.analyze_access(
                url=final_url,
                status_code=status_code,
                html=html,
                error=str(exc),
            )
            return PageFetchResult(
                success=False,
                backend="requests",
                url=url,
                status_code=status_code,
                html=html,
                error=str(exc),
                final_url=final_url,
                block_kind=analysis["block_kind"],
                block_signals=analysis["block_signals"],
                strategy_hints=analysis["strategy_hints"],
                page_kind=analysis["page_kind"],
            )

    def _fetch_with_scrapling(self, url: str, *, timeout: int) -> PageFetchResult:
        try:
            page = ScraplingFetcher().get(url, timeout=timeout * 1000)
            html = str(getattr(page, "html_content", "") or "")
            status = getattr(page, "status", None)
            final_url = str(getattr(page, "url", "") or url)
            analysis = self.analyze_access(
                url=final_url,
                status_code=status,
                html=html,
            )
            return PageFetchResult(
                success=bool(html),
                backend="scrapling",
                url=url,
                status_code=status,
                html=html,
                final_url=final_url,
                block_kind=analysis["block_kind"],
                block_signals=analysis["block_signals"],
                strategy_hints=analysis["strategy_hints"],
                page_kind=analysis["page_kind"],
            )
        except Exception as exc:
            analysis = self.analyze_access(
                url=url,
                status_code=None,
                html="",
                error=str(exc),
            )
            return PageFetchResult(
                success=False,
                backend="scrapling",
                url=url,
                status_code=None,
                html="",
                error=str(exc),
                final_url=url,
                block_kind=analysis["block_kind"],
                block_signals=analysis["block_signals"],
                strategy_hints=analysis["strategy_hints"],
                page_kind=analysis["page_kind"],
            )

    def _fetch_with_scrapling_dynamic(
        self, url: str, *, timeout: int
    ) -> PageFetchResult:
        try:
            page = ScraplingDynamicFetcher.fetch(
                url,
                headless=True,
                disable_resources=True,
                timeout=max(15000, timeout * 1000),
            )
            html = str(getattr(page, "html_content", "") or "")
            status = getattr(page, "status", None)
            final_url = str(getattr(page, "url", "") or url)
            analysis = self.analyze_access(
                url=final_url,
                status_code=status,
                html=html,
            )
            return PageFetchResult(
                success=bool(html),
                backend="scrapling_dynamic",
                url=url,
                status_code=status,
                html=html,
                final_url=final_url,
                block_kind=analysis["block_kind"],
                block_signals=analysis["block_signals"],
                strategy_hints=analysis["strategy_hints"],
                page_kind=analysis["page_kind"],
            )
        except Exception as exc:
            analysis = self.analyze_access(
                url=url,
                status_code=None,
                html="",
                error=str(exc),
            )
            return PageFetchResult(
                success=False,
                backend="scrapling_dynamic",
                url=url,
                status_code=None,
                html="",
                error=str(exc),
                final_url=url,
                block_kind=analysis["block_kind"],
                block_signals=analysis["block_signals"],
                strategy_hints=analysis["strategy_hints"],
                page_kind=analysis["page_kind"],
            )
