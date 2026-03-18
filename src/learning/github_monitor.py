"""GitHub Trending 学习监控器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

from bs4 import BeautifulSoup

from config.constants import GITHUB_TRENDING_URL
from src.learning.crawler import CrawlResult, LearningCrawler
from src.memory import MemoryManager
from src.utils.helpers import sanitize_text, truncate_text


@dataclass
class TrendingRepository:
    name: str
    description: str
    language: str
    stars: str
    forks: str
    url: str


class GitHubTrendingMonitor:
    """抓取 GitHub Trending 并写入学习记忆。"""

    def __init__(
        self,
        crawler: Optional[LearningCrawler] = None,
        trending_url: str = GITHUB_TRENDING_URL,
    ):
        self.crawler = crawler or LearningCrawler()
        self.trending_url = trending_url

    def fetch_trending_repositories(self) -> List[TrendingRepository]:
        crawl_result = self.crawler.fetch(self.trending_url)
        if not crawl_result.success:
            return []
        return self.parse_trending_html(crawl_result)

    def parse_trending_html(
        self, crawl_result: CrawlResult
    ) -> List[TrendingRepository]:
        soup = BeautifulSoup(crawl_result.raw_html or "", "html.parser")
        repositories: List[TrendingRepository] = []

        for article in soup.select("article.Box-row"):
            title_node = article.select_one("h2 a")
            if not title_node:
                continue

            href = (title_node.get("href") or "").strip()
            name = (
                sanitize_text(title_node.get_text(" ", strip=True))
                .replace(" / ", "/")
                .replace(" ", "")
            )
            description_node = article.select_one("p")
            language_node = article.select_one('[itemprop="programmingLanguage"]')
            stars_node = article.select_one("a[href$='/stargazers']")
            forks_node = article.select_one("a[href$='/forks']")

            repositories.append(
                TrendingRepository(
                    name=name,
                    description=sanitize_text(
                        description_node.get_text(" ", strip=True)
                    )
                    if description_node
                    else "",
                    language=sanitize_text(language_node.get_text(" ", strip=True))
                    if language_node
                    else "",
                    stars=sanitize_text(stars_node.get_text(" ", strip=True))
                    if stars_node
                    else "",
                    forks=sanitize_text(forks_node.get_text(" ", strip=True))
                    if forks_node
                    else "",
                    url=f"https://github.com{href}" if href.startswith("/") else href,
                )
            )

        return repositories

    def ingest_trending_repositories(
        self, memory_manager: MemoryManager, limit: int = 10
    ) -> Dict[str, object]:
        repositories = self.fetch_trending_repositories()[:limit]
        memory_ids: List[int] = []

        for repository in repositories:
            payload = cast(Dict[str, Any], self.build_memory_payload(repository))
            memory_id = memory_manager.create_memory(**payload)
            if memory_id is not None:
                memory_ids.append(int(memory_id))

        return {
            "count": len(memory_ids),
            "memory_ids": memory_ids,
            "repositories": repositories,
        }

    def build_memory_payload(self, repository: TrendingRepository) -> Dict[str, Any]:
        language = repository.language or "unknown"
        popularity = ", ".join(
            part
            for part in [
                f"stars={repository.stars}" if repository.stars else "",
                f"forks={repository.forks}" if repository.forks else "",
            ]
            if part
        )
        popularity_suffix = f" | 热度: {popularity}" if popularity else ""
        description = truncate_text(repository.description or "No description", 220)

        return {
            "event": truncate_text(
                f"GitHub Trending: {repository.name} ({language})。项目简介: {description}{popularity_suffix}",
                500,
            ),
            "thought": truncate_text(
                f"技术信号: {repository.name} 进入 Trending，说明 {language} 生态当前更关注 {description}",
                400,
            ),
            "lesson": "学习经验: 关注 Trending 项目可以快速捕捉当前技术热点、工具链迁移方向与社区偏好。",
            "importance": self._calculate_importance(repository),
            "source_type": "github_trending",
            "source_url": repository.url,
            "source_reputation": 0.82,
            "verification_status": "auto",
            "raw_payload": {
                "name": repository.name,
                "description": repository.description,
                "language": repository.language,
                "stars": repository.stars,
                "forks": repository.forks,
                "url": repository.url,
            },
        }

    def _calculate_importance(self, repository: TrendingRepository) -> float:
        score = 0.55
        if repository.language:
            score += 0.05
        if repository.description:
            score += 0.1
        if repository.stars:
            score += 0.1
        if repository.forks:
            score += 0.05
        return min(1.0, round(score, 4))
