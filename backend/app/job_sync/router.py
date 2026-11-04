"""FastAPI Router for IT Job Aggregation and Sync Service."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.job_sync.database import JobDatabase
from app.job_sync.sync_engine import JobSyncEngine
from app.job_sync.scheduler import JobSyncScheduler

logger = logging.getLogger("app.job_sync.router")

WEB_DIR = Path(__file__).parent / "web"
db = JobDatabase()
engine = JobSyncEngine(db)
scheduler = JobSyncScheduler(engine, interval_minutes=60)

router = APIRouter(tags=["jobs"])


@router.get("/admin/jobs", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the standalone IT Jobs Audit Dashboard."""
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(404, "Dashboard HTML file not found.")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@router.get("/api/sources")
async def list_sources():
    """List all supported IT job portals."""
    return engine.list_sources()


@router.get("/api/jobs")
async def get_jobs(
    query: Optional[str] = Query(None, description="Search keyword"),
    source: Optional[str] = Query(None, description="Filter by source, e.g. TopCV, ITviec"),
    seniority: Optional[str] = Query(None, description="Intern, Fresher, Junior, Middle, Senior, Lead"),
    job_category: Optional[str] = Query(None, description="Backend, Frontend, Fullstack, Mobile, DevOps, AI/Data, etc."),
    skill: Optional[str] = Query(None, description="Filter by skill, e.g. Python, React"),
    location: Optional[str] = Query(None, description="City or region"),
    is_active: Optional[bool] = Query(None, description="Filter by active status: true for active, false for inactive"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Retrieve filtered and paginated IT jobs."""
    jobs, total = db.query_jobs(
        query=query,
        source=source,
        seniority=seniority,
        job_category=job_category,
        skill=skill,
        location=location,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "message": "Không tìm thấy việc làm nào" if total == 0 else f"Tìm thấy {total} việc làm",
        "jobs": [j.model_dump() for j in jobs],
    }


@router.get("/api/jobs/stats")
@router.get("/api/stats")
async def get_stats():
    """Retrieve statistics across sources, categories, and top skills."""
    return db.get_stats()


@router.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str):
    """Retrieve details for a single job by ID."""
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(404, f"Job with ID '{job_id}' not found.")
    return job.model_dump()


@router.get("/api/jobs/{job_id}/export-interview")
async def export_for_mock_interview(job_id: str):
    """Convert a synced job into a standard payload for Coc-di-lam mock interview."""
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(404, f"Job with ID '{job_id}' not found.")
    return job.to_interview_request()


@router.post("/api/job-sync")
@router.post("/api/sync")
async def trigger_sync(source: Optional[str] = Query(None, description="Specific source to sync or None for all")):
    """Trigger manual IT jobs synchronization."""
    if source and source.lower() != "all":
        result = await engine.sync_source(source)
        return result.model_dump()
    else:
        results = await engine.sync_all()
        return [r.model_dump() for r in results]


@router.get("/api/scheduler/status")
async def scheduler_status():
    """Check status of the periodic job sync scheduler."""
    return scheduler.status()


@router.post("/api/scheduler/start")
async def start_scheduler():
    scheduler.start()
    return {"message": "Scheduler started", "status": scheduler.status()}


@router.post("/api/scheduler/stop")
async def stop_scheduler():
    scheduler.stop()
    return {"message": "Scheduler stopped", "status": scheduler.status()}
