"""学习系统网页抓取器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, List, Mapping, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config.settings import settings
from src.utils.helpers import sanitize_text


@dataclass
class CrawlResult:
    success: bool
    url: str
    title: str = ""
    raw_html: str = ""
    raw_source_data: Optional[bytes | str] = None
    raw_text: str = ""
    clean_text: str = ""
    source: str = ""
    fetched_at: str = ""
    links: List[str] = field(default_factory=list)
    status_code: Optional[int] = None
    source_encoding: str = ""
    source_content_type: str = ""
    error: Optional[str] = None


class LearningCrawler:
    """轻量通用网页抓取器。"""

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ):
        self.user_agent = user_agent or settings.USER_AGENT
        self.timeout = timeout or settings.REQUEST_TIMEOUT
        self.max_retries = max_retries or settings.MAX_RETRIES
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def fetch(self, url: str) -> CrawlResult:
        last_error: Optional[str] = None

        for _ in range(max(1, self.max_retries)):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return self.parse_response(url, response)
            except requests.RequestException as exc:
                last_error = str(exc)

        return CrawlResult(
            success=False,
            url=url,
            source=self._infer_source(url),
            fetched_at=datetime.now().isoformat(),
            error=last_error or "request_failed",
        )

    def parse_response(self, url: str, response: Any) -> CrawlResult:
        content = self._extract_response_bytes(response)
        content_type = self._extract_content_type(getattr(response, "headers", None))
        html, encoding = self._smart_decode(
            content=content,
            headers=getattr(response, "headers", None),
            apparent_encoding=getattr(response, "apparent_encoding", None),
        )
        if getattr(response, "encoding", None) != encoding:
            try:
                response.encoding = encoding
            except Exception:
                pass
        return self.parse_html(
            url,
            html,
            status_code=getattr(response, "status_code", None),
            raw_source_data=content,
            source_encoding=encoding,
            source_content_type=content_type,
        )

    def parse_bytes(
        self,
        url: str,
        content: bytes,
        *,
        status_code: Optional[int] = None,
        headers: Optional[Mapping[str, str]] = None,
        apparent_encoding: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> CrawlResult:
        html, encoding = self._smart_decode(
            content=content,
            headers=headers,
            apparent_encoding=apparent_encoding,
        )
        return self.parse_html(
            url,
            html,
            status_code=status_code,
            raw_source_data=content,
            source_encoding=encoding,
            source_content_type=content_type or self._extract_content_type(headers),
        )

    def parse_html(
        self,
        url: str,
        html: str,
        status_code: Optional[int] = None,
        *,
        raw_source_data: Optional[bytes | str] = None,
        source_encoding: Optional[str] = None,
        source_content_type: Optional[str] = None,
    ) -> CrawlResult:
        soup = BeautifulSoup(html or "", "html.parser")
        self._remove_noise(soup)

        title = self._extract_title(soup)
        raw_text = self._extract_main_text(soup)
        clean_text = sanitize_text(raw_text)
        links = self._extract_links(soup, url)

        return CrawlResult(
            success=bool(clean_text or title),
            url=url,
            title=title,
            raw_html=html or "",
            raw_source_data=raw_source_data
            if raw_source_data is not None
            else (html or ""),
            raw_text=raw_text,
            clean_text=clean_text,
            source=self._infer_source(url),
            fetched_at=datetime.now().isoformat(),
            links=links,
            status_code=status_code,
            source_encoding=source_encoding or "",
            source_content_type=source_content_type or "",
            error=None if (clean_text or title) else "empty_content",
        )

    def _extract_response_bytes(self, response: Any) -> bytes:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            return content
        if isinstance(content, bytearray):
            return bytes(content)
        if isinstance(content, memoryview):
            return content.tobytes()

        text = getattr(response, "text", "") or ""
        encoding = getattr(response, "encoding", None) or getattr(
            response, "apparent_encoding", None
        )
        try:
            return text.encode(encoding or "utf-8", errors="ignore")
        except LookupError:
            return text.encode("utf-8", errors="ignore")

    def _smart_decode(
        self,
        *,
        content: bytes,
        headers: Optional[Mapping[str, str]] = None,
        apparent_encoding: Optional[str] = None,
    ) -> tuple[str, str]:
        if not content:
            return "", "utf-8"

        candidates = self._build_encoding_candidates(
            headers=headers,
            content=content,
            apparent_encoding=apparent_encoding,
        )
        decoded_variants: List[tuple[float, int, str, str]] = []
        for index, encoding in enumerate(candidates):
            try:
                text = content.decode(encoding, errors="strict")
            except (LookupError, UnicodeDecodeError):
                continue
            decoded_variants.append(
                (self._score_decoded_text(text), index, text, encoding)
            )

        if decoded_variants:
            decoded_variants.sort(key=lambda item: (-item[0], item[1]))
            _, _, text, encoding = decoded_variants[0]
            return text, encoding

        for encoding in ("gb18030", "utf-8"):
            try:
                return content.decode(encoding, errors="replace"), encoding
            except LookupError:
                continue
        return content.decode("utf-8", errors="ignore"), "utf-8"

    def _build_encoding_candidates(
        self,
        *,
        headers: Optional[Mapping[str, str]],
        content: bytes,
        apparent_encoding: Optional[str],
    ) -> List[str]:
        candidates: List[str] = []
        for encoding in (
            self._extract_charset_from_headers(headers),
            self._extract_charset_from_meta(content),
            apparent_encoding,
            "utf-8",
            "utf-8-sig",
            "gb18030",
        ):
            normalized = self._normalize_encoding(encoding)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _extract_content_type(self, headers: Optional[Mapping[str, str]]) -> str:
        if not headers:
            return ""
        for key, value in headers.items():
            if str(key).lower() == "content-type":
                return str(value or "")
        return ""

    def _extract_charset_from_headers(
        self, headers: Optional[Mapping[str, str]]
    ) -> Optional[str]:
        content_type = self._extract_content_type(headers)
        if not content_type:
            return None
        match = re.search(r"charset\s*=\s*['\"]?([a-zA-Z0-9_.:-]+)", content_type)
        return match.group(1) if match else None

    def _extract_charset_from_meta(self, content: bytes) -> Optional[str]:
        head = content[:4096].decode("ascii", errors="ignore")
        patterns = (
            r"<meta[^>]+charset\s*=\s*['\"]?\s*([a-zA-Z0-9_.:-]+)",
            r"<meta[^>]+content\s*=\s*['\"][^>]*charset\s*=\s*([a-zA-Z0-9_.:-]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, head, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _normalize_encoding(self, encoding: Optional[str]) -> Optional[str]:
        normalized = (encoding or "").strip().strip("\"'").lower()
        if not normalized:
            return None
        aliases = {
            "utf8": "utf-8",
            "utf_8": "utf-8",
            "utf-8-sig": "utf-8-sig",
            "gbk2312": "gb18030",
            "gb2312": "gb18030",
            "gbk": "gb18030",
        }
        return aliases.get(normalized, normalized)

    def _score_decoded_text(self, text: str) -> float:
        if not text:
            return float("-inf")
        replacement_penalty = text.count("\ufffd") * 5
        mojibake_penalty = (
            sum(text.count(marker) for marker in ("锟斤拷", "Ã", "æ", "ï»¿")) * 2
        )
        cjk_bonus = sum(1 for char in text if "\u4e00" <= char <= "\u9fff") * 0.1
        printable_bonus = sum(1 for char in text if char.isprintable()) * 0.001
        return cjk_bonus + printable_bonus - replacement_penalty - mojibake_penalty

    def _remove_noise(self, soup: BeautifulSoup) -> None:
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

    def _extract_title(self, soup: BeautifulSoup) -> str:
        candidates = [
            soup.find("meta", attrs={"property": "og:title"}),
            soup.find("h1"),
            soup.find("title"),
        ]
        for node in candidates:
            if not node:
                continue
            if getattr(node, "get", None):
                content = node.get("content")
                if content:
                    return sanitize_text(content)
            text = node.get_text(" ", strip=True)
            if text:
                return sanitize_text(text)
        return ""

    def _extract_main_text(self, soup: BeautifulSoup) -> str:
        containers = [
            soup.find("article"),
            soup.find("main"),
            soup.find(
                "div", class_=["article", "content", "post-content", "article-content"]
            ),
            soup.body,
        ]

        for container in containers:
            if not container:
                continue
            chunks: List[str] = []
            for element in container.find_all(["p", "h1", "h2", "h3", "li"]):
                text = sanitize_text(element.get_text(" ", strip=True))
                if len(text) >= 20:
                    chunks.append(text)
            if chunks:
                return "\n".join(chunks)

        fallback = sanitize_text(soup.get_text(" ", strip=True))
        return fallback

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        seen = set()
        links: List[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href.startswith("#"):
                continue
            absolute = urljoin(base_url, href)
            if absolute in seen:
                continue
            seen.add(absolute)
            links.append(absolute)
        return links[:50]

    def _infer_source(self, url: str) -> str:
        return urlparse(url).netloc.lower()
