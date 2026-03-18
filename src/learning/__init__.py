"""学习系统模块。"""

from .crawler import CrawlResult, LearningCrawler
from .news_parser import NewsParser, ParsedNews
from .github_monitor import GitHubTrendingMonitor, TrendingRepository
from .distiller import LearningDistiller

__all__ = [
    "CrawlResult",
    "LearningCrawler",
    "NewsParser",
    "ParsedNews",
    "GitHubTrendingMonitor",
    "TrendingRepository",
    "LearningDistiller",
]
