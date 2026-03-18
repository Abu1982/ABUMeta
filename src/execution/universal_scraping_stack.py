"""ABU 通用抓取工具栈注册表。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ScrapingToolSpec:
    name: str
    source: str
    primary_use_cases: list[str]
    strengths: list[str]
    weaknesses: list[str]
    integration_mode: str
    priority: str


UNIVERSAL_SCRAPING_STACK = [
    ScrapingToolSpec(
        name="requests+beautifulsoup",
        source="内置",
        primary_use_cases=["静态页面", "简单列表页", "低成本首轮探测"],
        strengths=["轻量", "稳定", "易于调试"],
        weaknesses=["对动态页面弱", "正文抽取能力一般"],
        integration_mode="已接入",
        priority="high",
    ),
    ScrapingToolSpec(
        name="Scrapling",
        source="D4Vinci/Scrapling",
        primary_use_cases=["半结构页面", "动态页面", "站型化页面理解"],
        strengths=["自适应抓取", "可处理动态内容", "适合做通用后端"],
        weaknesses=["并非对所有站点都稳", "需要与站型策略联动"],
        integration_mode="已开始接入",
        priority="high",
    ),
    ScrapingToolSpec(
        name="Trafilatura",
        source="adbar/trafilatura",
        primary_use_cases=["资讯正文抽取", "文章元数据提取", "科技情报站"],
        strengths=["正文抽取强", "适合文章页", "支持结构化输出"],
        weaknesses=["对商品页/询盘页帮助有限"],
        integration_mode="建议下一步接入",
        priority="high",
    ),
    ScrapingToolSpec(
        name="Scrapy",
        source="scrapy/scrapy",
        primary_use_cases=["大规模爬取", "任务调度", "多页面 pipeline"],
        strengths=["成熟", "生态强", "适合大规模任务"],
        weaknesses=["接入成本较高", "不适合作为轻量站点探测器"],
        integration_mode="建议作为后续大规模采集骨架",
        priority="medium",
    ),
    ScrapingToolSpec(
        name="Crawl4AI",
        source="unclecode/crawl4ai",
        primary_use_cases=["LLM 友好抓取", "面向 AI 的网页整理"],
        strengths=["适合 AI 数据流", "与 LLM 路线契合"],
        weaknesses=["需要进一步验证与现有 Python 体系的集成成本"],
        integration_mode="建议作为 LLM 抓取增强候选",
        priority="medium",
    ),
    ScrapingToolSpec(
        name="Crawlee",
        source="apify/crawlee",
        primary_use_cases=["复杂站点", "Node/TS 侧大规模爬取", "代理轮换"],
        strengths=["复杂站点能力强", "大规模采集成熟"],
        weaknesses=["跨语言接入成本高"],
        integration_mode="建议保留为复杂站点后备方案",
        priority="medium",
    ),
    ScrapingToolSpec(
        name="SeleniumBase",
        source="seleniumbase/SeleniumBase",
        primary_use_cases=["高反爬浏览器自动化", "复杂交互站点"],
        strengths=["浏览器自动化强", "带反检测能力"],
        weaknesses=["成本高", "不适合默认后端"],
        integration_mode="建议作为高阻力站点兜底后端",
        priority="medium",
    ),
]


def export_universal_scraping_stack() -> list[dict[str, Any]]:
    return [asdict(item) for item in UNIVERSAL_SCRAPING_STACK]
