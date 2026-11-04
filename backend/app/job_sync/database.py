"""SQLite storage and query engine for IT jobs synchronization."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.job_sync.models import JobItem, SyncResult

logger = logging.getLogger("app.job_sync.database")

DB_PATH = Path(__file__).parent / "data" / "jobs.db"


class JobDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    company_logo TEXT,
                    location TEXT,
                    salary TEXT,
                    experience TEXT,
                    seniority TEXT,
                    job_category TEXT,
                    skills TEXT,
                    source_url TEXT UNIQUE NOT NULL,
                    description TEXT,
                    requirements TEXT,
                    benefits TEXT,
                    posted_at TEXT,
                    synced_at TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs (source);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_seniority ON jobs (seniority);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs (job_category);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_synced ON jobs (synced_at DESC);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_fetched INTEGER DEFAULT 0,
                    new_jobs INTEGER DEFAULT 0,
                    updated_jobs INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0,
                    error_message TEXT,
                    synced_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def upsert_jobs(self, jobs: List[JobItem]) -> Tuple[int, int]:
        """Save jobs into database. Returns (inserted_count, updated_count)."""
        if not jobs:
            return 0, 0

        inserted = 0
        updated = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for job in jobs:
                # Check if exists by id or source_url
                cursor.execute(
                    "SELECT id FROM jobs WHERE id = ? OR source_url = ?",
                    (job.id, job.source_url)
                )
                row = cursor.fetchone()
                skills_json = json.dumps(job.skills, ensure_ascii=False)

                if row:
                    # Update existing job
                    cursor.execute("""
                        UPDATE jobs SET
                            title = ?,
                            company = ?,
                            company_logo = COALESCE(?, company_logo),
                            location = ?,
                            salary = ?,
                            experience = ?,
                            seniority = ?,
                            job_category = ?,
                            skills = ?,
                            description = ?,
                            requirements = ?,
                            benefits = ?,
                            synced_at = ?,
                            is_active = ?
                        WHERE id = ? OR source_url = ?
                    """, (
                        job.title, job.company, job.company_logo, job.location,
                        job.salary, job.experience, job.seniority, job.job_category,
                        skills_json, job.description, job.requirements, job.benefits,
                        job.synced_at, 1 if job.is_active else 0, job.id, job.source_url
                    ))
                    updated += 1
                else:
                    # Insert new job
                    cursor.execute("""
                        INSERT INTO jobs (
                            id, source, external_id, title, company, company_logo,
                            location, salary, experience, seniority, job_category,
                            skills, source_url, description, requirements, benefits,
                            posted_at, synced_at, is_active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        job.id, job.source, job.external_id, job.title, job.company,
                        job.company_logo, job.location, job.salary, job.experience,
                        job.seniority, job.job_category, skills_json, job.source_url,
                        job.description, job.requirements, job.benefits,
                        job.posted_at, job.synced_at, 1 if job.is_active else 0
                    ))
                    inserted += 1
            conn.commit()

        return inserted, updated

    def query_jobs(
        self,
        query: Optional[str] = None,
        source: Optional[str] = None,
        seniority: Optional[str] = None,
        job_category: Optional[str] = None,
        skill: Optional[str] = None,
        location: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[JobItem], int]:
        """Query jobs with dynamic filters and pagination."""
        conditions: List[str] = []
        params: List[Any] = []

        if is_active is not None:
            conditions.append("is_active = ?")
            params.append(1 if is_active else 0)

        if query:
            q_like = f"%{query.strip()}%"
            conditions.append("(title LIKE ? OR company LIKE ? OR skills LIKE ? OR description LIKE ?)")
            params.extend([q_like, q_like, q_like, q_like])

        if source and source.lower() != "all":
            conditions.append("source = ?")
            params.append(source)

        if seniority and seniority.lower() != "all":
            conditions.append("seniority = ?")
            params.append(seniority)

        if job_category and job_category.lower() != "all":
            conditions.append("job_category = ?")
            params.append(job_category)

        if skill and skill.lower() != "all":
            conditions.append("skills LIKE ?")
            params.append(f"%{skill.strip()}%")

        if location and location.lower() != "all":
            conditions.append("location LIKE ?")
            params.append(f"%{location.strip()}%")

        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._get_connection() as conn:
            # Count total
            count_cursor = conn.execute(f"SELECT COUNT(*) as total FROM jobs {where_clause}", params)
            total = count_cursor.fetchone()["total"]

            # Fetch page
            offset = max(0, (page - 1) * page_size)
            query_sql = f"""
                SELECT * FROM jobs
                {where_clause}
                ORDER BY synced_at DESC, posted_at DESC
                LIMIT ? OFFSET ?
            """
            cursor = conn.execute(query_sql, params + [page_size, offset])
            rows = cursor.fetchall()

            jobs = []
            for r in rows:
                skills_val = []
                try:
                    skills_val = json.loads(r["skills"]) if r["skills"] else []
                except Exception:
                    skills_val = []

                jobs.append(JobItem(
                    id=r["id"],
                    source=r["source"],
                    external_id=r["external_id"],
                    title=r["title"],
                    company=r["company"],
                    company_logo=r["company_logo"],
                    location=r["location"] or "Toàn quốc",
                    salary=r["salary"] or "Thỏa thuận",
                    experience=r["experience"] or "Không yêu cầu",
                    seniority=r["seniority"] or "Junior",
                    job_category=r["job_category"] or "Software Engineer",
                    skills=skills_val,
                    source_url=r["source_url"],
                    description=r["description"] or "",
                    requirements=r["requirements"] or "",
                    benefits=r["benefits"] or "",
                    posted_at=r["posted_at"] or "",
                    synced_at=r["synced_at"] or "",
                    is_active=bool(r["is_active"]),
                ))

            return jobs, total

    def get_job_by_id(self, job_id: str) -> Optional[JobItem]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            r = cursor.fetchone()
            if not r:
                return None
            skills_val = []
            try:
                skills_val = json.loads(r["skills"]) if r["skills"] else []
            except Exception:
                pass
            return JobItem(
                id=r["id"],
                source=r["source"],
                external_id=r["external_id"],
                title=r["title"],
                company=r["company"],
                company_logo=r["company_logo"],
                location=r["location"] or "",
                salary=r["salary"] or "Thỏa thuận",
                experience=r["experience"] or "",
                seniority=r["seniority"] or "Junior",
                job_category=r["job_category"] or "Backend",
                skills=skills_val,
                source_url=r["source_url"],
                description=r["description"] or "",
                requirements=r["requirements"] or "",
                benefits=r["benefits"] or "",
                posted_at=r["posted_at"] or "",
                synced_at=r["synced_at"] or "",
                is_active=bool(r["is_active"]),
            )

    def log_sync(self, result: SyncResult):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO sync_logs (
                    source, status, total_fetched, new_jobs, updated_jobs,
                    duration_seconds, error_message, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.source, result.status, result.total_fetched,
                result.new_jobs, result.updated_jobs, result.duration_seconds,
                result.error_message, result.synced_at
            ))
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            total_jobs = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE is_active = 1").fetchone()["c"]
            
            # By source
            by_source = {}
            for row in conn.execute("SELECT source, COUNT(*) as c FROM jobs WHERE is_active = 1 GROUP BY source"):
                by_source[row["source"]] = row["c"]

            # By seniority
            by_seniority = {}
            for row in conn.execute("SELECT seniority, COUNT(*) as c FROM jobs WHERE is_active = 1 GROUP BY seniority"):
                by_seniority[row["seniority"]] = row["c"]

            # By category
            by_category = {}
            for row in conn.execute("SELECT job_category, COUNT(*) as c FROM jobs WHERE is_active = 1 GROUP BY job_category"):
                by_category[row["job_category"]] = row["c"]

            # Recent sync logs
            recent_logs = []
            for row in conn.execute("SELECT * FROM sync_logs ORDER BY id DESC LIMIT 10"):
                recent_logs.append(dict(row))

            return {
                "total_jobs": total_jobs,
                "by_source": by_source,
                "by_seniority": by_seniority,
                "by_category": by_category,
                "recent_logs": recent_logs,
            }
