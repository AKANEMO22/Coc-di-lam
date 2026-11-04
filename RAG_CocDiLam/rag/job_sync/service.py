"""Job crawler service running within RAG_CocDiLam microservice."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from rag.job_sync.crawlers.base import BaseJobCrawler
from rag.job_sync.crawlers.topcv import TopCVCrawler
from rag.job_sync.crawlers.itviec import ITviecCrawler
from rag.job_sync.crawlers.topdev import TopDevCrawler
from rag.job_sync.crawlers.vietnamworks import VietnamWorksCrawler
from rag.job_sync.models import JobItem

logger = logging.getLogger("rag.job_sync")


class RAGJobCrawlerService:
    def __init__(self):
        self.crawlers: Dict[str, BaseJobCrawler] = {
            "topcv": TopCVCrawler(),
            "itviec": ITviecCrawler(),
            "topdev": TopDevCrawler(),
            "vietnamworks": VietnamWorksCrawler(),
        }

    async def crawl_source(self, source_name: str, keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        key = source_name.lower().strip()
        crawler = self.crawlers.get(key)
        if not crawler:
            logger.warning("Unknown crawler source: %s", source_name)
            return []
        try:
            return await crawler.crawl(keyword=keyword, limit=limit)
        except Exception as e:
            logger.error("Error crawling %s: %s", source_name, e, exc_info=True)
            return []

    async def crawl(self, source: str = "all", keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        source_key = (source or "all").lower().strip()
        if source_key == "all":
            tasks = [
                self.crawl_source(name, keyword=keyword, limit=limit)
                for name in self.crawlers.keys()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            jobs: List[JobItem] = []
            for r in results:
                if isinstance(r, list):
                    jobs.extend(r)
            return jobs
        return await self.crawl_source(source_key, keyword=keyword, limit=limit)
