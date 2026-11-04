"""Crawlers package initialization."""
from rag.job_sync.crawlers.base import BaseJobCrawler
from rag.job_sync.crawlers.topcv import TopCVCrawler
from rag.job_sync.crawlers.itviec import ITviecCrawler
from rag.job_sync.crawlers.topdev import TopDevCrawler
from rag.job_sync.crawlers.vietnamworks import VietnamWorksCrawler

__all__ = [
    "BaseJobCrawler",
    "TopCVCrawler",
    "ITviecCrawler",
    "TopDevCrawler",
    "VietnamWorksCrawler",
]
