"""模块 12：在影子沙盒内自供应并抓取外贸线索。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

from src.execution.sandbox import ShadowSandbox
from src.execution.tool_discovery import ToolProvisioner


_ROW_QUALITY_WEIGHTS = {
    "location": 0.1,
    "destination_country": 0.14,
    "published_at": 0.14,
    "buyer_name": 0.1,
    "quantity": 0.1,
    "requirement": 0.12,
    "frequency": 0.08,
    "payment_terms": 0.08,
    "raw_description": 0.14,
}


def _extract_country(location_text: str) -> str:
    if not location_text:
        return ""
    parts = [part.strip() for part in str(location_text).split(",") if part.strip()]
    return parts[-1] if parts else str(location_text).strip()


def _compute_row_quality(row: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    richer_fields: list[str] = []
    score = 0.0
    for field_name, weight in _ROW_QUALITY_WEIGHTS.items():
        value = str(row.get(field_name, "") or "").strip()
        if value:
            richer_fields.append(field_name)
            score += weight
    quality_score = round(min(1.0, score + (0.05 if row.get("notes") else 0.0)), 3)
    missing_fields = [
        field_name
        for field_name in _ROW_QUALITY_WEIGHTS
        if field_name not in richer_fields
    ]
    return quality_score, richer_fields, missing_fields


def _summarize_rows_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "average_row_quality_score": 0.0,
            "max_row_quality_score": 0.0,
            "min_row_quality_score": 0.0,
            "average_richer_field_count": 0.0,
            "missing_field_histogram": {},
        }
    scores = [float(row.get("row_quality_score") or 0.0) for row in rows]
    richer_counts = [int(row.get("richer_field_count") or 0) for row in rows]
    missing_histogram: dict[str, int] = {}
    for row in rows:
        for field_name in row.get("missing_richer_fields", []):
            missing_histogram[field_name] = missing_histogram.get(field_name, 0) + 1
    return {
        "average_row_quality_score": round(sum(scores) / len(scores), 3),
        "max_row_quality_score": round(max(scores), 3),
        "min_row_quality_score": round(min(scores), 3),
        "average_richer_field_count": round(sum(richer_counts) / len(richer_counts), 3),
        "missing_field_histogram": missing_histogram,
    }


def _resolve_host_playwright_python() -> Optional[Path]:
    try:
        import playwright  # type: ignore  # noqa: F401

        return Path(sys.executable)
    except Exception:
        pass

    playwright_cli = shutil.which("playwright")
    if not playwright_cli:
        return None
    cli_path = Path(playwright_cli).resolve()
    if sys.platform.startswith("win"):
        python_path = cli_path.parents[1] / "python.exe"
    else:
        python_path = cli_path.parents[1] / "python"
    return python_path if python_path.exists() else None


@dataclass(frozen=True)
class LeadCaptureTarget:
    url: str
    keyword: str = ""
    source_name: str = ""
    list_selector: str = "a, h1, h2, h3, h4, li"
    title_selector: str = "self"
    link_selector: str = "a"
    time_selector: str = ""
    location_selector: str = ""
    description_selector: str = ""
    detail_list_selector: str = ""
    extraction_profile: str = "default"
    fetch_mode: str = "static"
    headers: dict[str, str] | None = None
    anti_bot_delay_ms: int = 1200
    max_items: int = 10
    alternative_entry_urls: tuple[str, ...] | list[str] = ()
    backend_order: tuple[str, ...] | list[str] = ()
    block_policy: str = "switch"
    strategy_link_keywords: tuple[str, ...] | list[str] = ()
    force_no_fallback: bool = False
    max_expanded_urls: int = 6
    max_attempts_per_target: int = 10


class SandboxLeadHarvester:
    """在 Docker 沙盒内抓取询盘标题，并输出 CSV。"""

    REQUIRED_DEPENDENCIES = ("requests", "beautifulsoup4", "lxml")

    def __init__(
        self,
        sandbox: Optional[ShadowSandbox] = None,
        provisioner: Optional[ToolProvisioner] = None,
    ):
        self.sandbox = sandbox or ShadowSandbox()
        self.provisioner = provisioner or ToolProvisioner(self.sandbox)

    async def ensure_crawler_stack(self) -> list[dict[str, Any]]:
        results = []
        for dependency in self.REQUIRED_DEPENDENCIES:
            results.append(
                await self.provisioner.request_provision(
                    dependency,
                    auto_approve=True,
                    reason="module12_lead_capture_bootstrap",
                )
            )
        return results

    def capture_trade_leads_csv(
        self,
        targets: Iterable[LeadCaptureTarget],
        *,
        output_path: str | Path,
        max_items_per_target: int = 10,
        timeout: int = 45,
    ) -> dict[str, Any]:
        target_list = list(targets)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        code = self._build_capture_script(
            target_list,
            output_path=output,
            max_items_per_target=max_items_per_target,
        )
        result = self.sandbox.execute_shadow_task(
            code,
            timeout=timeout,
            allow_network=True,
        )
        payload = self._extract_capture_payload(result)
        payload = self._maybe_apply_host_browser_fallback(
            target_list,
            payload,
            output_path=output,
            max_items_per_target=max_items_per_target,
        )
        if payload and payload.get("rows"):
            self._write_rows_to_csv(output, payload["rows"])
        diagnostics = payload.get("target_diagnostics", []) if payload else []
        return {
            "success": bool(result.get("success")) and bool(payload is not None),
            "output_path": str(output),
            "target_count": len(target_list),
            "row_count": len(payload.get("rows", [])) if payload else 0,
            "blocked_target_count": sum(
                1 for item in diagnostics if item.get("block_detected")
            ),
            "target_diagnostics": diagnostics,
            "capture_summary": payload.get("capture_summary", {}) if payload else {},
            "capture_payload": payload or {},
            "sandbox_result": result,
        }

    def _maybe_apply_host_browser_fallback(
        self,
        targets: list[LeadCaptureTarget],
        payload: Optional[dict[str, Any]],
        *,
        output_path: Path,
        max_items_per_target: int,
    ) -> Optional[dict[str, Any]]:
        current_payload = payload or {
            "rows": [],
            "target_diagnostics": [],
            "capture_summary": {},
        }
        diagnostics = list(current_payload.get("target_diagnostics", []) or [])
        rows = list(current_payload.get("rows", []) or [])
        fallback_applied = False

        for target in targets:
            matched_index = next(
                (
                    idx
                    for idx, item in enumerate(diagnostics)
                    if str(item.get("input_url") or "") == target.url
                    or str(item.get("source_name") or "") == target.source_name
                ),
                None,
            )
            matched = diagnostics[matched_index] if matched_index is not None else {}
            if not self._should_use_host_browser_fallback(target, matched):
                continue
            host_payload = self._capture_trade_leads_with_host_browser(
                target,
                output_path=output_path,
                max_items_per_target=max_items_per_target,
            )
            if not host_payload or not host_payload.get("rows"):
                continue
            fallback_applied = True
            rows.extend(host_payload["rows"])
            merged_diagnostic = {
                **matched,
                **host_payload.get("diagnostic", {}),
                "source_name": target.source_name
                or matched.get("source_name")
                or target.url,
                "input_url": target.url,
                "output_path": str(output_path),
                "host_browser_applied": True,
                "sandbox_block_kind": matched.get("block_kind", ""),
                "sandbox_block_signals": matched.get("block_signals", []),
                "sandbox_attempts": matched.get("attempts", []),
            }
            if matched_index is None:
                diagnostics.append(merged_diagnostic)
            else:
                diagnostics[matched_index] = merged_diagnostic

        if not fallback_applied:
            return payload

        current_payload["rows"] = rows
        current_payload["target_diagnostics"] = diagnostics
        current_payload["capture_summary"] = _summarize_rows_quality(rows)
        return current_payload

    @staticmethod
    def _should_use_host_browser_fallback(
        target: LeadCaptureTarget,
        diagnostic: Optional[dict[str, Any]],
    ) -> bool:
        item = diagnostic or {}
        if int(item.get("row_count") or 0) > 0:
            return False
        if target.extraction_profile != "tradeindia_buy_leads":
            return False
        if target.fetch_mode != "playwright":
            return False
        return str(item.get("block_kind") or "") in {
            "js_required",
            "anti_bot_interruption",
            "http_403",
        }

    def _capture_trade_leads_with_host_browser(
        self,
        target: LeadCaptureTarget,
        *,
        output_path: Path,
        max_items_per_target: int,
    ) -> Optional[dict[str, Any]]:
        python_path = _resolve_host_playwright_python()
        if python_path is None:
            return None
        target_payload = {
            "url": target.url,
            "source_name": target.source_name,
            "keyword": target.keyword,
            "list_selector": target.list_selector,
            "title_selector": target.title_selector,
            "link_selector": target.link_selector,
            "headers": target.headers or {},
        }
        browser_script = r"""
import json
import re
import sys
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


def clean_text(value):
    return " ".join(str(value or "").split())


def first_match(pattern, text):
    match = re.search(pattern, text or "", re.IGNORECASE)
    if not match:
        return ""
    return clean_text(match.group(1)).strip(" :-")


def extract_country(location_text):
    parts = [part.strip() for part in str(location_text or "").split(",") if part.strip()]
    return parts[-1] if parts else clean_text(location_text)


def extract_listing_rows(page, target, limit):
    items = page.locator(target.get("list_selector") or "[role='option'], a[href*='/buyoffer/']")
    total = min(items.count(), max(limit * 4, 12))
    rows = []
    seen = set()
    for index in range(total):
        element = items.nth(index)
        text = clean_text(element.inner_text())
        if not text:
            continue
        link = element.locator("a[href*='/buyoffer/']").first
        href = ""
        title = ""
        if link.count() > 0:
            href = clean_text(link.get_attribute("href"))
            title = clean_text(link.inner_text())
        else:
            href = clean_text(element.get_attribute("href"))
            title = clean_text(text.split("Date posted", 1)[0])
        if not href or "/buyoffer/" not in href:
            continue
        if not title or title.lower() == "more":
            continue
        full_url = urljoin(target["url"], href)
        if full_url in seen:
            continue
        seen.add(full_url)
        published_at = first_match(r"Date posted\s*:?\s*([^\n]+?\d{4})", text)
        location = first_match(rf"{re.escape(title)}\s+(.+?)\s+Date posted", text)
        raw_description = first_match(r"(Buyer is looking for.+?)(?:\s+More|$)", text)
        rows.append(
            {
                "url": full_url,
                "title": title,
                "published_at": published_at,
                "location": location,
                "raw_description": raw_description,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def enrich_detail(context, row):
    page = context.new_page()
    try:
        page.goto(row["url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1800)
        body = clean_text(page.locator("body").inner_text())
        title = clean_text(page.title())
        title_pattern = re.escape(clean_text(row.get("title")))
        detail_location = first_match(rf"suppliers of {title_pattern}\s+([A-Za-z][A-Za-z\s.&-]+,\s*[A-Za-z][A-Za-z\s.&-]+)\s+Date posted", body)
        detail_date = first_match(r"Date posted\s*:?\s*([^\n]+?\d{4})", body)
        quantity = first_match(r"Quantity Required\s*:?\s*(.+?)(?:Want to Buy|Preferred Time|Requirement Type|Purpose|GST Available|Whatsapp Availability|Contact buyer|$)", body)
        want_to_buy = first_match(r"Want to Buy\s*:?\s*(.+?)(?:Preferred Time|Requirement Type|Purpose|GST Available|Whatsapp Availability|Buyer is looking for|Contact buyer|$)", body)
        detail_description = first_match(r"(Buyer is looking for '.+?'\.)", body)
        detail_description = detail_description or first_match(r"(Buyer is looking for suppliers of .+?)(?: Quantity Required| Want to Buy| Contact buyer|$)", body)
        row["location"] = clean_text(detail_location or row.get("location"))
        row["published_at"] = clean_text(detail_date or row.get("published_at"))
        row["quantity"] = clean_text(quantity)
        row["requirement"] = clean_text(want_to_buy or row.get("title") or title)
        row["raw_description"] = clean_text(detail_description or row.get("raw_description"))[:500]
    finally:
        page.close()
    return row


target = json.loads(sys.argv[1])
item_limit = int(sys.argv[2])
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=False)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        locale="en-US",
    )
    page = context.new_page()
    page.goto(target["url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3500)
    listing_rows = extract_listing_rows(page, target, item_limit)
    page.close()
    rows = []
    for item in listing_rows:
        enriched = enrich_detail(context, item)
        rows.append(
            {
                "captured_at": enriched.get("captured_at") or __import__("datetime").datetime.utcnow().isoformat(),
                "source_name": target.get("source_name") or target["url"],
                "url": enriched["url"],
                "keyword": target.get("keyword") or "",
                "title": enriched.get("title") or "",
                "location": enriched.get("location") or "India",
                "destination_country": extract_country(enriched.get("location") or "India"),
                "published_at": enriched.get("published_at") or "",
                "buyer_name": "",
                "quantity": enriched.get("quantity") or "",
                "requirement": enriched.get("requirement") or enriched.get("title") or "",
                "frequency": "",
                "payment_terms": "",
                "raw_description": enriched.get("raw_description") or "",
                "notes": "host_playwright_tradeindia",
                "source_reputation": "0.78",
            }
        )
    browser.close()
    print(json.dumps({"rows": rows}, ensure_ascii=False))
"""
        try:
            result = subprocess.run(
                [
                    str(python_path),
                    "-c",
                    browser_script,
                    json.dumps(target_payload, ensure_ascii=False),
                    str(max_items_per_target),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        stdout = (result.stdout or "").strip().splitlines()
        if not stdout:
            return None
        try:
            host_payload = json.loads(stdout[-1])
        except json.JSONDecodeError:
            return None
        host_rows = list(host_payload.get("rows", []) or [])
        for row in host_rows:
            if (
                target.extraction_profile == "tradeindia_buy_leads"
                and str(row.get("location") or "").strip()
                and "india" not in str(row.get("location") or "").lower()
            ):
                row["location"] = f"{str(row.get('location')).strip()}, India"
            row["destination_country"] = _extract_country(row.get("location", ""))
            quality_score, richer_fields, missing_fields = _compute_row_quality(row)
            row["richer_field_count"] = len(richer_fields)
            row["row_quality_score"] = quality_score
            row["missing_richer_fields"] = missing_fields
        return {
            "rows": host_rows,
            "diagnostic": {
                "used_url": target.url,
                "used_backend": "host_playwright_msedge",
                "row_count": len(host_rows),
                "block_detected": False,
                "block_kind": "",
                "block_signals": ["host_playwright_fallback"],
                "page_kind": "trade_lead_hub",
                "attempt_count": 1,
                "quality_summary": _summarize_rows_quality(host_rows),
            },
        }

    @staticmethod
    def _extract_capture_payload(result: dict[str, Any]) -> Optional[dict[str, Any]]:
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            return None
        last_line = stdout.splitlines()[-1].strip()
        try:
            payload = json.loads(last_line)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _write_rows_to_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        fieldnames = [
            "captured_at",
            "source_name",
            "url",
            "keyword",
            "title",
            "location",
            "destination_country",
            "published_at",
            "buyer_name",
            "quantity",
            "requirement",
            "frequency",
            "payment_terms",
            "raw_description",
            "notes",
            "richer_field_count",
            "row_quality_score",
            "source_reputation",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(
                {key: row.get(key, "") for key in fieldnames} for row in rows
            )

    @staticmethod
    def _build_capture_script(
        targets: list[LeadCaptureTarget],
        *,
        output_path: Path,
        max_items_per_target: int,
    ) -> str:
        serialized_targets = [
            {
                "url": item.url,
                "keyword": item.keyword,
                "source_name": item.source_name,
                "list_selector": item.list_selector,
                "title_selector": item.title_selector,
                "link_selector": item.link_selector,
                "time_selector": item.time_selector,
                "location_selector": item.location_selector,
                "description_selector": item.description_selector,
                "detail_list_selector": item.detail_list_selector,
                "headers": item.headers or {},
                "anti_bot_delay_ms": item.anti_bot_delay_ms,
                "max_items": item.max_items,
                "extraction_profile": getattr(item, "extraction_profile", "default"),
                "fetch_mode": getattr(item, "fetch_mode", "static"),
                "alternative_entry_urls": list(
                    getattr(item, "alternative_entry_urls", ()) or ()
                ),
                "backend_order": list(getattr(item, "backend_order", ()) or ()),
                "block_policy": getattr(item, "block_policy", "switch"),
                "strategy_link_keywords": list(
                    getattr(item, "strategy_link_keywords", ()) or ()
                ),
                "force_no_fallback": bool(getattr(item, "force_no_fallback", False)),
                "max_expanded_urls": int(getattr(item, "max_expanded_urls", 6) or 6),
                "max_attempts_per_target": int(
                    getattr(item, "max_attempts_per_target", 10) or 10
                ),
            }
            for item in targets
        ]
        return rf"""
from datetime import datetime
import json
import re
import time
from urllib.parse import urljoin

try:
    import requests
except ModuleNotFoundError:
    requests = None

from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

try:
    from scrapling import DynamicFetcher, Fetcher
    SCRAPLING_AVAILABLE = True
except Exception:
    SCRAPLING_AVAILABLE = False

TARGETS = {serialized_targets!r}
MAX_ITEMS = {int(max_items_per_target)!r}
OUTPUT_PATH = {str(output_path)!r}

rows = []
target_diagnostics = []


def clean_text(node):
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def slugify_label(label):
    sanitized = []
    previous_is_sep = False
    for char in label.lower():
        if char.isalnum():
            sanitized.append(char)
            previous_is_sep = False
        else:
            if not previous_is_sep:
                sanitized.append("_")
                previous_is_sep = True
    return "".join(sanitized).strip("_")


def extract_labeled_value(text, labels):
    for label in labels:
        pattern = re.compile(rf"{{{{re.escape(label)}}}}\s*:?\s*(.+?)(?=(?:[A-Z][A-Za-z ]+\s*:)|$)", re.IGNORECASE)
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip(" :-")
    return ""


def extract_labeled_value_from_node(node, labels):
    raw = clean_text(node)
    for label in labels:
        pattern = re.compile(rf"{{{{re.escape(label)}}}}\s*:?\s*(.+)$", re.IGNORECASE)
        match = pattern.search(raw)
        if match:
            return match.group(1).strip(" :-")
    return ""


def extract_labeled_value_ordered(text, target_label, labels):
    escaped = [re.escape(item) for item in labels]
    alternation = "|".join(escaped)
    pattern = re.compile(
        rf"{{{{re.escape(target_label)}}}}\s*:?\s*(.+?)(?=(?:{{alternation}})\s*:?|$)",
        re.IGNORECASE,
    )
    match = pattern.search(text or "")
    if match:
        return match.group(1).strip(" :-")
    return ""


def extract_country(location_text):
    if not location_text:
        return ""
    parts = [part.strip() for part in location_text.split(",") if part.strip()]
    return parts[-1] if parts else location_text


def choose_backend(fetch_mode, extraction_profile):
    if not SCRAPLING_AVAILABLE:
        return "requests"
    if fetch_mode == "playwright":
        return "scrapling_dynamic"
    if extraction_profile in ("trade_lead_detail", "news_article_detail"):
        return "scrapling"
    return "requests"


def default_backend_order(fetch_mode, extraction_profile):
    ordered = []
    if fetch_mode == "playwright":
        ordered.extend(["scrapling_dynamic", "scrapling", "requests"])
    elif extraction_profile in ("trade_lead_detail", "news_article_detail"):
        ordered.extend(["scrapling", "requests", "scrapling_dynamic"])
    else:
        ordered.extend(["requests", "scrapling", "scrapling_dynamic"])
    deduped = []
    for backend in ordered:
        if backend.startswith("scrapling") and not SCRAPLING_AVAILABLE:
            continue
        if backend not in deduped:
            deduped.append(backend)
    return deduped or ["requests"]


def fetch_html(url, headers, fetch_mode, extraction_profile, backend_override=""):
    backend = backend_override or choose_backend(fetch_mode, extraction_profile)
    if backend.startswith("scrapling") and not SCRAPLING_AVAILABLE:
        return backend, "", None, "scrapling_unavailable"
    if backend == "scrapling_dynamic":
        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                disable_resources=True,
                timeout=15000,
            )
            html = str(getattr(page, "html_content", "") or "")
            status = getattr(page, "status", None)
            return backend, html, status, ""
        except Exception as exc:
            return backend, "", None, str(exc)
    if backend == "scrapling":
        try:
            page = Fetcher().get(url, timeout=12000)
            html = str(getattr(page, "html_content", "") or "")
            status = getattr(page, "status", None)
            return backend, html, status, ""
        except Exception as exc:
            return backend, "", None, str(exc)
    try:
        if requests is not None:
            response = requests.get(url, timeout=12, headers=headers, allow_redirects=True)
            return (
                backend,
                response.text,
                response.status_code,
                "" if response.status_code < 400 else f"HTTP {{response.status_code}}",
            )
        request = Request(url, headers=headers)
        with urlopen(request, timeout=12) as response:
            body = response.read().decode("utf-8", errors="ignore")
            status_code = getattr(response, "status", None) or response.getcode()
            return (
                backend,
                body,
                status_code,
                "" if (status_code or 0) < 400 else f"HTTP {{status_code}}",
            )
    except Exception as exc:
        return backend, "", None, str(exc)


def extract_page_title(soup_obj):
    for selector in ("meta[property='og:title']", "h1", "title"):
        node = soup_obj.select_one(selector)
        if node is None:
            continue
        if getattr(node, "get", None) is not None:
            content = str(node.get("content") or "").strip()
            if content:
                return content
        value = clean_text(node)
        if value:
            return value
    return ""


def extract_page_snapshot(html):
    if not html:
        return "", ""
    soup_obj = BeautifulSoup(html, "lxml")
    return extract_page_title(soup_obj), clean_text(soup_obj.body)[:4000]


def page_has_trade_product_signal(title, body_text, active_url=""):
    joined = f"{{title}} {{body_text}} {{active_url}}".lower()
    return any(
        token in joined
        for token in (
            "buy lead",
            "trade lead",
            "buying request",
            "rfq",
            "product details",
            "contact supplier",
            "inquire now",
            "send inquiry",
            "manufacturers directory",
            "suppliers directory",
            "product catalogs",
            "products from",
            "/product-details/",
            "/ec-market/",
            "pump",
            "lubrication",
            "gear pump",
        )
    )


def detect_block_page(title, body_text, status_code, error_text="", active_url=""):
    joined = f"{{title}} {{body_text}} {{error_text}} {{active_url}}".lower()
    block_signals = []
    block_kind = ""
    if status_code in (401, 403, 429):
        block_signals.append(f"http_status:{{status_code}}")
    product_signal = page_has_trade_product_signal(title, body_text, active_url)
    trade_lead_signal = detect_page_kind(title, body_text, active_url) == "trade_lead_hub"
    hard_interruption_tokens = (
        "pardon our interruption",
        "made us think you were a bot",
        "captcha",
        "security check",
        "verify you are human",
        "unusual traffic from your network",
        "automated queries",
    )
    soft_access_denied_tokens = (
        "access denied",
        "forbidden",
    )
    js_tokens = (
        "you've disabled javascript",
        "please enable javascript",
        "javascript is disabled",
        "requires javascript",
        "turn on javascript",
    )
    login_tokens = (
        "sign in to continue",
        "login to continue",
        "log in to continue",
        "member login",
        "members only",
        "please sign in",
        "view buyer details after login",
    )
    if any(token in joined for token in hard_interruption_tokens):
        block_kind = "anti_bot_interruption"
        block_signals.append("block:anti_bot_interruption")
    elif any(token in joined for token in soft_access_denied_tokens):
        if product_signal and status_code not in (401, 403, 429):
            block_signals.append("anti_bot_guard:product_signal_override")
        else:
            block_kind = "anti_bot_interruption"
            block_signals.append("block:anti_bot_interruption")
    elif any(token in joined for token in js_tokens):
        if product_signal or trade_lead_signal:
            block_signals.append("anti_bot_guard:js_required_override")
        else:
            block_kind = "js_required"
            block_signals.append("block:js_required")
    elif any(token in joined for token in login_tokens):
        if product_signal or trade_lead_signal:
            block_signals.append("anti_bot_guard:login_wall_override")
        else:
            block_kind = "login_wall"
            block_signals.append("block:login_wall")
    elif status_code in (401, 403, 429) and not product_signal:
        block_kind = f"http_{{status_code}}"
    return block_kind, block_signals, bool(block_kind)


def detect_page_kind(title, body_text, active_url=""):
    joined = f"{{title}} {{body_text}} {{active_url}}".lower()
    lowered_url = active_url.lower()
    if any(
        token in joined
        for token in ("buy lead", "trade lead", "buying request", "rfq", "buyer is looking for")
    ):
        return "trade_lead_hub"
    if (
        "/ec-market/" in lowered_url
        and "--" not in lowered_url
        and any(token in joined for token in ("pump", "lubrication", "gear pump", "hydraulic"))
    ):
        return "product_detail"
    if any(
        token in joined
        for token in (
            "product details",
            "contact supplier",
            "inquire now",
            "send inquiry",
            "minimum order quantity",
            "/product-details/",
        )
    ):
        return "product_detail"
    if any(
        token in joined
        for token in (
            "manufacturers directory",
            "suppliers directory",
            "product catalogs",
            "catalog directory",
            "wholesale",
            "manufacturers, suppliers",
            "products from",
        )
    ):
        return "supplier_directory"
    if "/ec-market/" in active_url.lower() and page_has_trade_product_signal(title, body_text, active_url):
        return "supplier_directory"
    return "generic"


def discover_internal_urls(active_url, html, extraction_profile, strategy_link_keywords, limit=3):
    soup = BeautifulSoup(html or "", "lxml")
    keywords = [str(item).strip().lower() for item in (strategy_link_keywords or []) if str(item).strip()]
    scored = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True)
        if not href or not text:
            continue
        if href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        resolved = urljoin(active_url, href)
        joined = f"{{text}} {{resolved}}".lower()
        if any(token in joined for token in ("help", "login", "signup", "privacy", "terms", "mailto:")):
            continue
        score = 0
        score += sum(4 for token in keywords if token in joined)
        score += sum(
            3
            for token in (
                "buy lead",
                "trade lead",
                "buying request",
                "rfq",
                "inquiry",
                "inquiries",
                "pumps",
                "pump",
                "lubrication",
                "product details",
                "contact supplier",
                "inquire now",
                "send inquiry",
            )
            if token in joined
        )
        if extraction_profile == "trade_lead_detail" and any(
            token in joined for token in ("/ec-market/", "/product-details/")
        ):
            score += 3
        if "/product-details/" in resolved.lower():
            score += 4
        if any(
            token in joined
            for token in ("contact supplier", "inquire now", "send inquiry", "contactnow")
        ):
            score += 3
        if any(token in joined for token in ("directory", "manufacturers directory", "suppliers directory")):
            score -= 2
        if score > 0:
            scored.append((score, resolved))
    scored.sort(key=lambda item: (-item[0], item[1]))
    urls = []
    for _, url in scored:
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def should_skip_candidate(text, href, extraction_profile):
    lowered_text = (text or "").strip().lower()
    lowered_href = (href or "").strip().lower()
    blocked_text_tokens = (
        "view in english",
        "support article",
        "sign in",
        "login",
        "change password",
        "my inquiries",
        "membership",
    )
    blocked_href_tokens = (
        "mailto:",
        "/support",
        "/help",
        "/login",
        "/signin",
        "/signup",
        "/account",
        "/password",
    )
    if any(token in lowered_text for token in blocked_text_tokens):
        return True
    if any(token in lowered_href for token in blocked_href_tokens):
        return True
    if extraction_profile == "trade_lead_detail":
        trade_required = (
            "buy",
            "lead",
            "trade",
            "rfq",
            "import",
            "supplier",
            "product-details",
            "ec-market",
            "contact-supplier",
            "contactnow",
            "send-inquiry",
        )
        if lowered_href and not any(token in lowered_href for token in trade_required):
            return True
    if extraction_profile == "news_article_detail":
        news_required = ("blog", "news", "article", "post", "developer", "ai")
        if lowered_href and not any(token in lowered_href for token in news_required):
            return True
    return False


def should_skip_resolved_url(lead_url, extraction_profile):
    lowered = (lead_url or "").strip().lower()
    blocked = (
        "mailto:",
        "/help",
        "/support",
        "help-third-party-plugins",
        "/login",
        "/signin",
        "/signup",
        "/account",
        "/password",
    )
    if any(token in lowered for token in blocked):
        return True
    if extraction_profile == "trade_lead_detail":
        required = (
            "buy",
            "lead",
            "trade",
            "rfq",
            "import",
            "supplier",
            "product-details",
            "ec-market",
            "contact-supplier",
            "contactnow",
            "send-inquiry",
        )
        return not any(token in lowered for token in required)
    if extraction_profile == "news_article_detail":
        required = ("blog", "news", "article", "post", "developer", "ai")
        return not any(token in lowered for token in required)
    return False


def pick_first_text(soup_obj, selectors):
    for selector in selectors:
        if not selector:
            continue
        node = soup_obj.select_one(selector)
        value = clean_text(node)
        if value:
            return value
    return ""


def sanitize_field_value(value, field_name):
    text = (value or "").strip()
    lowered = text.lower()
    blocked_common = {{"sign in", "login", "filter", "join free", "help", "search"}}
    if lowered in blocked_common:
        return ""
    if field_name == "buyer_name" and (
        len(text) > 60
        or lowered.startswith("looking for ")
        or "date posted" in lowered
        or "i am interested" in lowered
    ):
        return ""
    if field_name in {{"buyer_name", "location", "published_at", "requirement"}} and len(text) <= 1:
        return ""
    if field_name == "published_at" and not extract_date_from_text(text) and lowered not in {{"today", "yesterday"}}:
        return ""
    return text


def canonicalize_detail_map(detail_map):
    canonical = {{
        "buyer_name": detail_map.get("buyer_name", ""),
        "quantity": detail_map.get("quantity", ""),
        "requirement": detail_map.get("requirement", ""),
        "frequency": detail_map.get("frequency", ""),
        "payment_terms": detail_map.get("payment_terms", "") or detail_map.get("payment_mode", ""),
    }}
    alias_pairs = (
        ("buyer_name", ("buyer_name", "buyer", "company", "member_name")),
        ("quantity", ("quantity", "qty", "amount", "minimum_order_quantity")),
        ("requirement", ("requirement", "product_name", "product", "need")),
        ("frequency", ("frequency", "purchase_frequency", "buying_frequency")),
        ("payment_terms", ("payment_terms", "payment_mode", "payment", "terms")),
    )
    for field_name, aliases in alias_pairs:
        if canonical[field_name]:
            continue
        for alias in aliases:
            value = str(detail_map.get(alias, "") or "").strip()
            if value:
                canonical[field_name] = value
                break
    return canonical


def compute_row_quality(row):
    weights = {{
        "location": 0.1,
        "destination_country": 0.14,
        "published_at": 0.14,
        "buyer_name": 0.1,
        "quantity": 0.1,
        "requirement": 0.12,
        "frequency": 0.08,
        "payment_terms": 0.08,
        "raw_description": 0.14,
    }}
    richer_fields = []
    score = 0.0
    for field_name, weight in weights.items():
        value = str(row.get(field_name, "") or "").strip()
        if value:
            richer_fields.append(field_name)
            score += weight
    quality_score = round(min(1.0, score + (0.05 if row.get("notes") else 0.0)), 3)
    missing_fields = [field for field in weights if field not in richer_fields]
    return quality_score, richer_fields, missing_fields


def summarize_rows_quality(rows):
    if not rows:
        return {{
            "average_row_quality_score": 0.0,
            "max_row_quality_score": 0.0,
            "min_row_quality_score": 0.0,
            "average_richer_field_count": 0.0,
            "missing_field_histogram": {{}},
        }}
    scores = [float(row.get("row_quality_score") or 0.0) for row in rows]
    richer_counts = [int(row.get("richer_field_count") or 0) for row in rows]
    missing_histogram = {{}}
    for row in rows:
        for field_name in row.get("missing_richer_fields", []):
            missing_histogram[field_name] = missing_histogram.get(field_name, 0) + 1
    return {{
        "average_row_quality_score": round(sum(scores) / len(scores), 3),
        "max_row_quality_score": round(max(scores), 3),
        "min_row_quality_score": round(min(scores), 3),
        "average_richer_field_count": round(sum(richer_counts) / len(richer_counts), 3),
        "missing_field_histogram": missing_histogram,
    }}


def extract_top_blocks(soup_obj, extraction_profile, limit=3):
    blocks = []
    for selector in ("article", "main", "section", "div", "li"):
        for node in soup_obj.select(selector):
            text = clean_text(node)
            if len(text) < 40:
                continue
            attrs = f"{{node.get('class', [])}} {{node.get('id', '')}}".lower()
            link_count = len(node.select("a[href]"))
            score = min(len(text) / 120.0, 10.0) - min(link_count, 12) * 0.35
            if extraction_profile == "trade_lead_detail":
                if any(
                    token in attrs
                    for token in ("product", "detail", "request", "lead", "description", "content")
                ):
                    score += 2.0
                if any(
                    token in text.lower()
                    for token in ("supplier", "manufacturer", "buy", "pump", "lubrication")
                ):
                    score += 2.0
            else:
                if any(
                    token in attrs
                    for token in ("article", "content", "post", "detail", "blog", "story")
                ):
                    score += 2.0
                if any(
                    token in text.lower()
                    for token in ("ai", "edge", "cloud", "community", "article")
                ):
                    score += 2.0
            if any(token in attrs for token in ("menu", "nav", "footer", "sidebar", "share", "breadcrumb")):
                score -= 2.0
            if score > 0:
                blocks.append((score, text))
    blocks.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return [item[1] for item in blocks[:limit]]


def extract_date_from_text(text):
    if not text:
        return ""
    patterns = [
        r"\\b\\d{{1, 2}}\\s+[A-Za-z]+\\s+\\d{{4}}\\b",
        r"\\b[A-Za-z]+\\s+\\d{{1, 2}},\\s*\\d{{4}}\\b",
        r"\\b\\d{{4}}-\\d{{2}}-\\d{{2}}\\b",
        r"\\b\\d{{4}}年\\d{{1, 2}}月\\d{{1, 2}}日\\b",
        r"\\b\\d{{4}}/\\d{{1, 2}}/\\d{{1, 2}}\\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def extract_date_from_html(html):
    candidates = []
    patterns = [
        r"\\d{{4}}-\\d{{2}}-\\d{{2}}",
        r"\\d{{4}}/\\d{{1, 2}}/\\d{{1, 2}}",
        r"\\d{{4}}年\\d{{1, 2}}月\\d{{1, 2}}日",
        r"\\d{{1, 2}}\\s+[A-Za-z]+\\s+\\d{{4}}",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, html or ""))
    return candidates[0] if candidates else ""


def enrich_from_detail_page(url, headers, extraction_profile, fetch_mode):
    _, detail_html, _, _ = fetch_html(url, headers, fetch_mode, extraction_profile)
    if not detail_html:
        return {{
            "published_at": "",
            "location": "",
            "raw_description": "",
            "buyer_name": "",
            "quantity": "",
            "requirement": "",
        }}
    detail_soup = BeautifulSoup(detail_html, "lxml")
    meta_description = ""
    meta_published = ""
    meta_node = detail_soup.select_one(
        "meta[name='description'], meta[property='og:description']"
    )
    if meta_node is not None and getattr(meta_node, "get", None) is not None:
        meta_description = str(meta_node.get("content") or "").strip()
    published_node = detail_soup.select_one(
        "meta[property='article:published_time'], meta[name='pubdate'], meta[name='publishdate'], meta[itemprop='datePublished']"
    )
    if published_node is not None and getattr(published_node, "get", None) is not None:
        meta_published = str(
            published_node.get("content") or published_node.get("datetime") or ""
        ).strip()
    body_text = clean_text(detail_soup.body)
    top_blocks = extract_top_blocks(detail_soup, extraction_profile, limit=3)
    if extraction_profile == "trade_lead_detail":
        description = (
            meta_description
            or pick_first_text(
                detail_soup,
                [
                    ".buying-request-content",
                    ".detail-description",
                    "article p",
                    ".description",
                    "p",
                ],
            )
            or (top_blocks[0] if top_blocks else "")
            or body_text[:500]
        )
        location = pick_first_text(
            detail_soup,
            [".location", ".country", "[class*='country']", "[class*='location']", "li"],
        )
        published_at = pick_first_text(
            detail_soup, ["time", "[class*='date']", "[class*='posted']", "li"]
        )
        buyer_name = pick_first_text(
            detail_soup,
            ["[class*='buyer']", "[class*='company']", "[class*='member']", "strong", "b"],
        )
        quantity = pick_first_text(
            detail_soup, ["[class*='quantity']", "[class*='qty']", "[class*='amount']"]
        )
        requirement = pick_first_text(
            detail_soup,
            [
                "[class*='requirement']",
                "[class*='require']",
                ".buying-request-content",
                "article p",
                "p",
            ],
        ) or (top_blocks[1] if len(top_blocks) > 1 else "")
    elif extraction_profile == "tradeindia_buy_leads":
        description = (
            pick_first_text(
                detail_soup,
                [
                    ".buylead_main_cont",
                    ".buylead_detail",
                    ".buylead_desc",
                    ".content",
                    "main",
                ],
            )
            or meta_description
            or (top_blocks[0] if top_blocks else "")
            or body_text[:500]
        )
        location = extract_labeled_value(body_text, ["Delivery Place"]) or pick_first_text(
            detail_soup,
            [".location", "[class*='location']", "[class*='city']"],
        )
        published_at = meta_published or extract_labeled_value(body_text, ["Date posted"])
        buyer_name = pick_first_text(
            detail_soup,
            [".buyer_name", "[class*='buyer']", "[class*='company']"],
        )
        quantity = extract_labeled_value(body_text, ["Quantity Required", "Quantity"])
        requirement = extract_labeled_value(body_text, ["Want to Buy"]) or pick_first_text(
            detail_soup,
            [".buylead_title", "h1", "h2"],
        )
        frequency = ""
        payment_terms = ""
    elif extraction_profile == "exportersindia_buy_leads":
        description = (
            pick_first_text(
                detail_soup,
                [
                    "div.other_info",
                    "div.buying-request-content",
                    "article p",
                    ".description",
                    "p",
                ],
            )
            or meta_description
            or (top_blocks[0] if top_blocks else "")
            or body_text[:500]
        )
        location = pick_first_text(
            detail_soup,
            ["div.lead-location", ".location", "[class*='location']", "[class*='country']"],
        )
        published_at = meta_published or pick_first_text(
            detail_soup, ["div.lead-date", "time", "[class*='date']", "[class*='posted']"]
        )
        buyer_name = extract_labeled_value(body_text, ["Buyer Name", "Buyer"]) or pick_first_text(
            detail_soup,
            ["[class*='buyer']", "[class*='company']", "strong", "b"],
        )
        quantity = extract_labeled_value(body_text, ["Quantity", "Qty"])
        requirement = extract_labeled_value(body_text, ["Requirement"]) or description
        frequency = extract_labeled_value(body_text, ["Frequency"])
        payment_terms = extract_labeled_value(body_text, ["Mode Of Payment", "Payment Mode", "payment mode"])
    else:
        description = (
            meta_description
            or pick_first_text(detail_soup, ["article p", ".description", ".content p", "p"])
            or (top_blocks[0] if top_blocks else "")
            or body_text[:500]
        )
        location = pick_first_text(
            detail_soup,
            [".location", ".country", "[class*='location']", "[class*='country']"],
        )
        published_at = meta_published or pick_first_text(
            detail_soup, ["time", "[class*='date']", "[class*='posted']"]
        )
        buyer_name = ""
        quantity = ""
        requirement = ""
    if not published_at:
        published_at = extract_date_from_text(body_text) or extract_date_from_html(detail_html)
    return {{
        "published_at": sanitize_field_value(published_at, "published_at"),
        "location": sanitize_field_value(location, "location"),
        "raw_description": description[:500],
        "buyer_name": sanitize_field_value(buyer_name[:120], "buyer_name"),
        "quantity": quantity[:120],
        "requirement": sanitize_field_value(requirement[:300], "requirement"),
        "frequency": frequency[:120] if 'frequency' in locals() else "",
        "payment_terms": payment_terms[:120] if 'payment_terms' in locals() else "",
    }}


def parse_specialized_trade_card(element, active_url, extraction_profile):
    card_text = clean_text(element)
    if extraction_profile == "exportersindia_buy_leads":
        title = clean_text(element.select_one("div.lead_det-title h3"))
        if not title:
            return None
        detail_map = {{}}
        for detail_node in element.select("ul._info_p > li"):
            label_node = detail_node.select_one("b")
            value_node = detail_node.select_one("span")
            label = clean_text(label_node).rstrip(":")
            value = clean_text(value_node)
            if label and value:
                detail_map[slugify_label(label)] = value
        if not detail_map:
            labels = [
                "Buyer Name",
                "Mobile No.",
                "Mobile No",
                "Quantity",
                "Requirement",
                "Frequency",
                "Mode Of Payment",
                "Payment Mode",
                "payment mode",
                "Usage/Application",
                "Physical State",
                "Size",
                "Color",
            ]
            detail_map = {{
                "buyer_name": extract_labeled_value_ordered(card_text, "Buyer Name", labels),
                "quantity": extract_labeled_value_ordered(card_text, "Quantity", labels),
                "requirement": extract_labeled_value_ordered(card_text, "Requirement", labels),
                "frequency": extract_labeled_value_ordered(card_text, "Frequency", labels),
                "payment_terms": extract_labeled_value_ordered(card_text, "Mode Of Payment", labels)
                or extract_labeled_value_ordered(card_text, "Payment Mode", labels)
                or extract_labeled_value_ordered(card_text, "payment mode", labels),
            }}
        detail_map["requirement"] = detail_map.get("requirement") or title
        return {{
            "title": title,
            "url": active_url,
            "published_at": clean_text(element.select_one("div.lead-date")),
            "location": clean_text(element.select_one("div.lead-location")),
            "raw_description": clean_text(element.select_one("div.other_info")),
            "detail_map": canonicalize_detail_map(detail_map),
            "notes": [],
        }}
    if extraction_profile == "tradeindia_buy_leads":
        link_node = element.select_one("a[href*='/buyoffer/']")
        title = clean_text(link_node) if link_node is not None else ""
        href = str(link_node.get("href") or "").strip() if link_node is not None and getattr(link_node, "get", None) else ""
        if not title:
            return None
        date_text = extract_labeled_value(card_text, ["Date posted"])
        location = "India" if "India" in card_text else ""
        description = extract_labeled_value(card_text, ["Buyer is looking for"]) or f"Buyer is looking for {{title}}"
        detail_map = canonicalize_detail_map(
            {{
                "requirement": extract_labeled_value(card_text, ["Want to Buy"]) or title,
                "quantity": extract_labeled_value(card_text, ["Quantity Required", "Quantity"]),
            }}
        )
        return {{
            "title": title,
            "url": urljoin(active_url, href) if href else active_url,
            "published_at": date_text,
            "location": location,
            "raw_description": description,
            "detail_map": detail_map,
            "notes": ["tradeindia_list_card"],
        }}
    return None


def extract_rows_from_page(target, active_url, html, headers, extraction_profile, fetch_mode, item_limit):
    soup = BeautifulSoup(html, "lxml")
    seen = set()
    output_rows = []
    list_selector = target.get("list_selector") or "a, h1, h2, h3, h4, li"
    title_selector = target.get("title_selector") or "self"
    link_selector = target.get("link_selector") or "a"
    time_selector = target.get("time_selector") or ""
    location_selector = target.get("location_selector") or ""
    description_selector = target.get("description_selector") or ""
    detail_list_selector = target.get("detail_list_selector") or ""
    keyword = (target.get("keyword") or "").strip().lower()
    for element in soup.select(list_selector):
        specialized = parse_specialized_trade_card(element, active_url, extraction_profile)
        if specialized is not None:
            text = specialized["title"]
            if len(text) < 12:
                continue
            if keyword and keyword not in text.lower():
                continue
            if text in seen:
                continue
            seen.add(text)
            lead_url = specialized["url"]
            if should_skip_resolved_url(lead_url, extraction_profile):
                continue
            published_at = specialized.get("published_at", "")
            location = specialized.get("location", "")
            raw_description = specialized.get("raw_description", "")
            detail_map = specialized.get("detail_map", {{}})
            notes = list(specialized.get("notes", []))
            if not published_at or not location or not raw_description or not detail_map.get("buyer_name"):
                detail_payload = enrich_from_detail_page(
                    lead_url, headers, extraction_profile, fetch_mode
                )
                published_at = published_at or detail_payload.get("published_at", "")
                location = location or detail_payload.get("location", "")
                raw_description = raw_description or detail_payload.get("raw_description", "")
                for field_name in ("buyer_name", "quantity", "requirement", "frequency", "payment_terms"):
                    if not detail_map.get(field_name) and detail_payload.get(field_name):
                        detail_map[field_name] = detail_payload.get(field_name, "")
            row_payload = {{
                "captured_at": datetime.utcnow().isoformat(),
                "source_name": target.get("source_name") or active_url,
                "url": lead_url,
                "keyword": target.get("keyword") or "",
                "title": text,
                "location": location,
                "destination_country": extract_country(location),
                "published_at": published_at,
                "buyer_name": detail_map.get("buyer_name", ""),
                "quantity": detail_map.get("quantity", ""),
                "requirement": detail_map.get("requirement", ""),
                "frequency": detail_map.get("frequency", ""),
                "payment_terms": detail_map.get("payment_terms", ""),
                "raw_description": raw_description,
                "notes": " ; ".join(notes),
                "source_reputation": "0.82" if element.select_one("img._verify") else "0.72",
            }}
            quality_score, richer_fields, missing_fields = compute_row_quality(row_payload)
            row_payload["richer_field_count"] = len(richer_fields)
            row_payload["row_quality_score"] = quality_score
            row_payload["missing_richer_fields"] = missing_fields
            output_rows.append(row_payload)
            if len(seen) >= item_limit:
                break
            continue
        title_node = element if title_selector == "self" else element.select_one(title_selector)
        if title_node is None:
            continue
        text = clean_text(title_node)
        if len(text) < 12:
            continue
        if keyword and keyword not in text.lower():
            continue
        if text in seen:
            continue
        seen.add(text)
        link_node = title_node if link_selector == "self" else element.select_one(link_selector)
        href = ""
        if link_node is not None and getattr(link_node, "get", None) is not None:
            href = str(link_node.get("href") or "").strip()
        if should_skip_candidate(text, href, extraction_profile):
            continue
        lead_url = urljoin(active_url, href) if href else active_url
        if should_skip_resolved_url(lead_url, extraction_profile):
            continue
        published_at = ""
        if time_selector:
            time_node = element.select_one(time_selector)
            if time_node is not None:
                published_at = clean_text(time_node)
        location = clean_text(element.select_one(location_selector)) if location_selector else ""
        raw_description = clean_text(element.select_one(description_selector)) if description_selector else ""
        detail_map = {{}}
        if detail_list_selector:
            for detail_node in element.select(detail_list_selector):
                label_node = detail_node.find("b")
                value_node = detail_node.find("span")
                label = clean_text(label_node).rstrip(":")
                value = clean_text(value_node)
                if label and value:
                    detail_map[slugify_label(label)] = value
        notes = []
        for key, value in detail_map.items():
            if key in {{"buyer_name", "quantity", "requirement", "frequency", "payment_mode"}}:
                continue
            notes.append(f"{{key}}={{value}}")
        detail_map = canonicalize_detail_map(detail_map)
        if not published_at or not location or not raw_description or not detail_map.get("buyer_name"):
            detail_payload = enrich_from_detail_page(
                lead_url, headers, extraction_profile, fetch_mode
            )
            published_at = published_at or detail_payload.get("published_at", "")
            location = location or detail_payload.get("location", "")
            raw_description = raw_description or detail_payload.get("raw_description", "")
            if not detail_map.get("buyer_name") and detail_payload.get("buyer_name"):
                detail_map["buyer_name"] = detail_payload.get("buyer_name", "")
            if not detail_map.get("quantity") and detail_payload.get("quantity"):
                detail_map["quantity"] = detail_payload.get("quantity", "")
            if not detail_map.get("requirement") and detail_payload.get("requirement"):
                detail_map["requirement"] = detail_payload.get("requirement", "")
        row_payload = {{
                "captured_at": datetime.utcnow().isoformat(),
                "source_name": target.get("source_name") or active_url,
                "url": lead_url,
                "keyword": target.get("keyword") or "",
                "title": text,
                "location": location,
                "destination_country": extract_country(location),
                "published_at": published_at,
                "buyer_name": detail_map.get("buyer_name", ""),
                "quantity": detail_map.get("quantity", ""),
                "requirement": detail_map.get("requirement", ""),
                "frequency": detail_map.get("frequency", ""),
                "payment_terms": detail_map.get("payment_terms", ""),
                "raw_description": raw_description,
                "notes": " ; ".join(notes),
                "source_reputation": "0.82" if element.select_one("img._verify") else "0.72",
        }}
        quality_score, richer_fields, missing_fields = compute_row_quality(row_payload)
        row_payload["richer_field_count"] = len(richer_fields)
        row_payload["row_quality_score"] = quality_score
        row_payload["missing_richer_fields"] = missing_fields
        output_rows.append(row_payload)
        if len(seen) >= item_limit:
            break
    return output_rows


def build_fallback_row(target, active_url, html, headers, extraction_profile, fetch_mode, page_kind):
    if page_kind == "supplier_directory" or bool(target.get("force_no_fallback")):
        return None
    soup = BeautifulSoup(html, "lxml")
    detail_payload = enrich_from_detail_page(active_url, headers, extraction_profile, fetch_mode)
    fallback_title = extract_page_title(soup) or target.get("source_name") or active_url
    fallback_description = detail_payload.get("raw_description", "") or clean_text(soup.body)[:500]
    if extraction_profile == "tradeindia_buy_leads" and (
        fallback_title.strip().lower() == str(target.get("source_name") or "").strip().lower()
        or "buy trade leads - view business trade offers" in fallback_description.lower()
    ):
        return None
    keyword = (target.get("keyword") or "").strip().lower()
    keyword_ok = not keyword or keyword in f"{{fallback_title}} {{fallback_description}}".lower()
    _, _, hard_block = detect_block_page(fallback_title, fallback_description, None)
    if not keyword_ok or hard_block:
        return None
    row_payload = {{
        "captured_at": datetime.utcnow().isoformat(),
        "source_name": target.get("source_name") or active_url,
        "url": active_url,
        "keyword": target.get("keyword") or "",
        "title": fallback_title,
        "location": detail_payload.get("location", ""),
        "destination_country": extract_country(detail_payload.get("location", "")),
        "published_at": detail_payload.get("published_at", ""),
        "buyer_name": detail_payload.get("buyer_name", ""),
        "quantity": detail_payload.get("quantity", ""),
        "requirement": detail_payload.get("requirement", ""),
        "frequency": "",
        "payment_terms": "",
        "raw_description": fallback_description,
        "notes": "fallback_direct_page",
        "source_reputation": "0.68",
    }}
    quality_score, richer_fields, missing_fields = compute_row_quality(row_payload)
    row_payload["richer_field_count"] = len(richer_fields)
    row_payload["row_quality_score"] = quality_score
    row_payload["missing_richer_fields"] = missing_fields
    return row_payload


for target in TARGETS:
    headers = {{"User-Agent": "ABU-Module12/1.0"}}
    headers.update(target.get("headers") or {{}})
    fetch_mode = target.get("fetch_mode") or "static"
    extraction_profile = target.get("extraction_profile") or "default"
    item_limit = min(MAX_ITEMS, int(target.get("max_items") or MAX_ITEMS))
    backend_candidates = list(
        target.get("backend_order") or default_backend_order(fetch_mode, extraction_profile)
    )
    max_expanded_urls = max(1, int(target.get("max_expanded_urls") or 6))
    max_attempts_per_target = max(
        len(backend_candidates),
        int(target.get("max_attempts_per_target") or 10),
    )
    strategy_link_keywords = list(target.get("strategy_link_keywords") or [])
    if not strategy_link_keywords and target.get("keyword"):
        strategy_link_keywords = [str(target.get("keyword") or "").strip().lower()]
    url_candidates = [target["url"]]
    for alt_url in target.get("alternative_entry_urls") or []:
        if alt_url and alt_url not in url_candidates:
            url_candidates.append(alt_url)

    target_rows = []
    attempts = []
    discovered_internal_urls = []
    block_detected = False
    block_kind = ""
    block_signals = []
    page_kind = "generic"
    used_url = target["url"]
    used_backend = ""
    product_detail_hits = 0
    anti_bot_guarded = False

    for active_url in url_candidates:
        if len(attempts) >= max_attempts_per_target:
            break
        for backend_hint in backend_candidates:
            if len(attempts) >= max_attempts_per_target:
                break
            backend_used, html, status_code, error_text = fetch_html(
                active_url,
                headers,
                fetch_mode,
                extraction_profile,
                backend_hint,
            )
            title, body_text = extract_page_snapshot(html)
            matched_block_kind, matched_block_signals, hard_block = detect_block_page(
                title, body_text, status_code, error_text, active_url
            )
            matched_page_kind = detect_page_kind(title, body_text, active_url)
            attempts.append(
                {{
                    "url": active_url,
                    "backend": backend_used,
                    "status_code": status_code,
                    "block_kind": matched_block_kind,
                    "page_kind": matched_page_kind,
                }}
            )
            page_kind = matched_page_kind or page_kind
            if matched_page_kind == "product_detail":
                product_detail_hits += 1
            if "anti_bot_guard:product_signal_override" in matched_block_signals:
                anti_bot_guarded = True
            if hard_block:
                block_detected = True
                block_kind = block_kind or matched_block_kind
                for signal in matched_block_signals:
                    if signal not in block_signals:
                        block_signals.append(signal)
                continue
            candidate_rows = extract_rows_from_page(
                target,
                active_url,
                html,
                headers,
                extraction_profile,
                fetch_mode,
                item_limit,
            )
            if candidate_rows:
                target_rows = candidate_rows
                used_url = active_url
                used_backend = backend_used
                break
            if matched_page_kind in {"supplier_directory", "trade_lead_hub"}:
                new_internal_url_count = 0
                for discovered_url in discover_internal_urls(
                    active_url,
                    html,
                    extraction_profile,
                    strategy_link_keywords,
                    limit=3,
                ):
                    if (
                        discovered_url not in url_candidates
                        and len(url_candidates) < max_expanded_urls
                    ):
                        url_candidates.append(discovered_url)
                        new_internal_url_count += 1
                    if discovered_url not in discovered_internal_urls:
                        discovered_internal_urls.append(discovered_url)
                if new_internal_url_count > 0:
                    continue
            fallback_row = build_fallback_row(
                target,
                active_url,
                html,
                headers,
                extraction_profile,
                fetch_mode,
                matched_page_kind,
            )
            if fallback_row is not None:
                target_rows = [fallback_row]
                used_url = active_url
                used_backend = backend_used
                break
        if target_rows:
            break

    rows.extend(target_rows)
    target_diagnostics.append(
        {{
            "source_name": target.get("source_name") or target["url"],
            "input_url": target["url"],
            "used_url": used_url,
            "used_backend": used_backend,
            "row_count": len(target_rows),
            "block_detected": block_detected,
            "block_kind": block_kind,
            "block_signals": block_signals,
            "page_kind": page_kind,
            "attempt_count": len(attempts),
            "max_attempts_per_target": max_attempts_per_target,
            "max_expanded_urls": max_expanded_urls,
            "attempts": attempts,
            "discovered_internal_urls": discovered_internal_urls,
            "internal_expansion_count": len(discovered_internal_urls),
            "product_detail_hits": product_detail_hits,
            "anti_bot_guarded": anti_bot_guarded,
            "quality_summary": summarize_rows_quality(target_rows),
            "output_path": OUTPUT_PATH,
        }}
    )

    delay_ms = int(target.get("anti_bot_delay_ms") or 0)
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

print(
    json.dumps(
        {{
            "rows": rows,
            "target_diagnostics": target_diagnostics,
            "capture_summary": summarize_rows_quality(rows),
        }},
        ensure_ascii=False,
    )
)
"""
