"""Synchronization Engine calling RAG microservice for web crawling."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from app.job_sync.database import JobDatabase
from app.job_sync.models import JobItem, SyncResult

logger = logging.getLogger("app.job_sync.engine")

SUPPORTED_SOURCES = [
    {"id": "topcv", "name": "TopCV", "homepage": "https://www.topcv.vn"},
    {"id": "itviec", "name": "ITviec", "homepage": "https://itviec.com"},
    {"id": "topdev", "name": "TopDev", "homepage": "https://topdev.vn"},
    {"id": "vietnamworks", "name": "VietnamWorks", "homepage": "https://www.vietnamworks.com"},
]


class JobSyncEngine:
    def __init__(
        self,
        db: Optional[JobDatabase] = None,
        rag_url: Optional[str] = None,
        rag_token: Optional[str] = None,
    ):
        self.db = db or JobDatabase()
        self.rag_url = (rag_url or os.getenv("RAG_URL", "http://rag-idq:8000")).rstrip("/")
        self.rag_token = rag_token if rag_token is not None else os.getenv("RAG_API_TOKEN", "")
        self.sources: Dict[str, Dict[str, str]] = {s["id"]: s for s in SUPPORTED_SOURCES}
        self.is_syncing: bool = False

    def list_sources(self) -> List[Dict[str, str]]:
        return list(self.sources.values())

    async def sync_source(self, source_name: str, keyword: str = "IT", limit: int = 30) -> SyncResult:
        key = source_name.lower().strip()
        source_meta = self.sources.get(key, {"id": key, "name": source_name})
        display_name = source_meta.get("name", source_name)

        start = time.perf_counter()
        logger.info(f"Triggering remote crawl on RAG service for {display_name}...")
        headers = {}
        if self.rag_token:
            headers["Authorization"] = f"Bearer {self.rag_token}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.rag_url}/api/v1/jobs/crawl",
                    json={"source": key, "keyword": keyword, "limit": limit},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            raw_jobs = data.get("jobs", [])
            jobs: List[JobItem] = []
            for item in raw_jobs:
                try:
                    jobs.append(JobItem(**item))
                except Exception as val_err:
                    logger.warning("Skipping malformed job item: %s", val_err)

            duration = round(time.perf_counter() - start, 2)
            inserted, updated = self.db.upsert_jobs(jobs)
            result = SyncResult(
                source=display_name,
                status="success" if jobs else "partial",
                total_fetched=len(jobs),
                new_jobs=inserted,
                updated_jobs=updated,
                duration_seconds=duration,
            )
            self.db.log_sync(result)
            logger.info(
                f"Completed {display_name} via RAG: {len(jobs)} jobs (inserted={inserted}, updated={updated}) in {duration}s"
            )
            return result
        except Exception as e:
            duration = round(time.perf_counter() - start, 2)
            logger.error(f"Failed sync via RAG for {display_name}: {e}", exc_info=True)
            result = SyncResult(
                source=display_name,
                status="error",
                error_message=str(e),
                duration_seconds=duration,
            )
            self.db.log_sync(result)
            return result

    async def sync_all(self, keyword: str = "IT", limit_per_source: int = 30) -> List[SyncResult]:
        if self.is_syncing:
            logger.info("sync_all already in progress, skipping duplicate trigger.")
            return [
                SyncResult(source="Browserbase (RAG)", status="success", total_fetched=0, new_jobs=0, updated_jobs=0)
            ]
        self.is_syncing = True
        try:
            logger.info(f"Starting sync_all across {len(self.sources)} sources via RAG microservice...")
            tasks = [
                self.sync_source(name, keyword=keyword, limit=limit_per_source)
                for name in self.sources.keys()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            return list(results)
        finally:
            self.is_syncing = False
