"""Background scheduler for periodic job synchronization."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.job_sync.sync_engine import JobSyncEngine

logger = logging.getLogger("app.job_sync.scheduler")


class JobSyncScheduler:
    def __init__(self, engine: JobSyncEngine, interval_minutes: int = 60):
        self.engine = engine
        self.interval_minutes = interval_minutes
        self._task: Optional[asyncio.Task] = None
        self._is_running = False
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.run_count = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    def status(self) -> dict:
        return {
            "is_running": self._is_running,
            "interval_minutes": self.interval_minutes,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
        }

    def start(self):
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"JobSyncScheduler started (interval: {self.interval_minutes}m).")

    def stop(self):
        if not self._is_running:
            return
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("JobSyncScheduler stopped.")

    async def _loop(self):
        # Run immediately once on start
        await self._execute_sync()
        while self._is_running:
            try:
                next_time = datetime.now(timezone.utc) + timedelta(minutes=self.interval_minutes)
                self.next_run = next_time.strftime("%Y-%m-%d %H:%M:%S UTC")
                await asyncio.sleep(self.interval_minutes * 60)
                if self._is_running:
                    await self._execute_sync()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _execute_sync(self):
        try:
            logger.info("Scheduler triggering periodic sync_all...")
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            self.last_run = now_str
            self.run_count += 1
            await self.engine.sync_all()
        except Exception as e:
            logger.error(f"Error in scheduler execution: {e}", exc_info=True)
