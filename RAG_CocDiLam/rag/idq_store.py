"""Durable, owner-scoped storage for ID_Q intakes, jobs, and prepared packs."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


# All callers run in worker threads, so a short wait is enough and never stalls the event loop.
_BUSY_TIMEOUT_SECONDS = 10


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class IDQStore:
    """SQLite repository isolated from the shared retrieval index and CV corpus."""

    def __init__(self, path: str | Path, upload_root: str | Path):
        self.path = Path(path)
        self.upload_root = Path(upload_root).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_root.mkdir(parents=True, exist_ok=True)
        # WAL is persistent in the database file: readers no longer block on the writer.
        wal = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_SECONDS)
        try:
            wal.execute("PRAGMA journal_mode=WAL")
        finally:
            wal.close()
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS idq_intakes (
                    id_q TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    input_type TEXT NOT NULL CHECK(input_type IN ('cv','form')),
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    target_json TEXT NOT NULL,
                    preferences_json TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS idq_owner_updated
                    ON idq_intakes(owner_id, updated_at);
                CREATE TABLE IF NOT EXISTS idq_revisions (
                    id_q TEXT NOT NULL REFERENCES idq_intakes(id_q) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    profile_json TEXT NOT NULL,
                    target_json TEXT NOT NULL,
                    preferences_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    PRIMARY KEY(id_q, revision)
                );
                CREATE TABLE IF NOT EXISTS idq_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    id_q TEXT NOT NULL REFERENCES idq_intakes(id_q) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error_code TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idq_preparations (
                    pack_id TEXT PRIMARY KEY,
                    id_q TEXT NOT NULL REFERENCES idq_intakes(id_q) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    knowledge_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(id_q, revision, knowledge_version)
                );
                CREATE TABLE IF NOT EXISTS idq_active_preparations (
                    id_q TEXT NOT NULL REFERENCES idq_intakes(id_q) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    pack_id TEXT NOT NULL REFERENCES idq_preparations(pack_id),
                    PRIMARY KEY(id_q, revision)
                );
                CREATE TABLE IF NOT EXISTS idq_idempotency (
                    owner_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    idem_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, operation, idem_key)
                );
                CREATE TABLE IF NOT EXISTS idq_companies (
                    company_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idq_job_descriptions (
                    jd_id TEXT PRIMARY KEY,
                    company_id TEXT NOT NULL REFERENCES idq_companies(company_id),
                    payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idq_web_research_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idq_web_research_expiry
                    ON idq_web_research_cache(expires_at);
                PRAGMA user_version=2;
                """
            )
            revision_columns = {row[1] for row in db.execute("PRAGMA table_info(idq_revisions)")}
            if "provenance_json" not in revision_columns:
                db.execute("ALTER TABLE idq_revisions ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_SECONDS)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_SECONDS * 1000}")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    def save_upload(self, id_q: str, filename: str, content: bytes) -> str:
        """Write an opaque private path; never use the client filename as a path."""
        safe_suffix = Path(filename).suffix.lower()
        if safe_suffix not in {".pdf", ".docx", ".txt", ".md"}:
            raise ValueError("Upload a PDF, DOCX, TXT, or Markdown CV")
        destination = (self.upload_root / f"{id_q}{safe_suffix}").resolve()
        if destination.parent != self.upload_root:
            raise ValueError("Invalid upload path")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return str(destination)

    def remove_upload(self, path: str | None) -> None:
        if not path:
            return
        candidate = Path(path).resolve()
        if candidate.parent == self.upload_root and candidate.is_file():
            candidate.unlink()

    def idempotent_response(self, owner: str, operation: str, key: str | None, request_hash: str):
        if not key:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT request_hash,response_json FROM idq_idempotency "
                "WHERE owner_id=? AND operation=? AND idem_key=?",
                (owner, operation, key),
            ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ValueError("Idempotency key was already used with a different request")
        return json.loads(row["response_json"])

    def create_intake(
        self,
        *,
        owner: str,
        input_type: str,
        profile: dict,
        target: dict,
        preferences: dict,
        provenance: dict,
        source: dict,
        response: dict,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> dict:
        now = time.time()
        status = "needs_input" if not target.get("company_name") or not target.get("role_id") or not target.get("target_seniority") else "ready"
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                prior = db.execute(
                    "SELECT request_hash,response_json FROM idq_idempotency "
                    "WHERE owner_id=? AND operation='create_intake' AND idem_key=?",
                    (owner, idempotency_key),
                ).fetchone()
                if prior:
                    if prior["request_hash"] != request_hash:
                        raise ValueError("Idempotency key was already used with a different request")
                    return json.loads(prior["response_json"])
            db.execute(
                "INSERT INTO idq_intakes VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (response["id_q"], owner, input_type, 1, status, _json(profile), _json(target), _json(preferences), _json(source), now, now),
            )
            db.execute(
                "INSERT INTO idq_revisions(id_q,revision,profile_json,target_json,preferences_json,provenance_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (response["id_q"], 1, _json(profile), _json(target), _json(preferences), _json(provenance), now),
            )
            if response.get("job_id"):
                db.execute(
                    "INSERT INTO idq_jobs VALUES(?,?,?,?,?,'completed',?,NULL,?,?)",
                    (response["job_id"], owner, response["id_q"], 1, "extract_cv" if input_type == "cv" else "normalize_form", _json({"id_q": response["id_q"], "status": "complete"}), now, now),
                )
            if idempotency_key:
                db.execute(
                    "INSERT INTO idq_idempotency VALUES(?,?,?,?,?,?)",
                    (owner, "create_intake", idempotency_key, request_hash or "", _json(response), now),
                )
        return response

    def get_intake(self, id_q: str, owner: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT i.*,r.provenance_json FROM idq_intakes i JOIN idq_revisions r ON r.id_q=i.id_q AND r.revision=i.revision WHERE i.id_q=? AND i.owner_id=? AND i.deleted_at IS NULL",
                (id_q, owner),
            ).fetchone()
        if row is None:
            raise KeyError("Intake not found")
        return self._intake_dict(row)

    def get_intake_revision(self, id_q: str, owner: str, revision: int) -> dict:
        """Return a pinned intake revision, including after a later edit is made."""
        with self.connection() as db:
            row = db.execute(
                "SELECT i.id_q,i.input_type,i.status,i.source_json,i.created_at,i.updated_at,"
                "r.revision,r.profile_json,r.target_json,r.preferences_json,r.provenance_json "
                "FROM idq_intakes i JOIN idq_revisions r ON r.id_q=i.id_q "
                "WHERE i.id_q=? AND i.owner_id=? AND i.deleted_at IS NULL AND r.revision=?",
                (id_q, owner, revision),
            ).fetchone()
        if row is None:
            raise KeyError("Intake revision not found")
        return self._intake_dict(row)

    @staticmethod
    def _intake_dict(row: sqlite3.Row) -> dict:
        source = json.loads(row["source_json"])
        source.pop("private_path", None)
        return {
            "id_q": row["id_q"],
            "input_type": row["input_type"],
            "revision": row["revision"],
            "status": row["status"],
            "candidate_profile": json.loads(row["profile_json"]),
            "interview_target": json.loads(row["target_json"]),
            "preferences": json.loads(row["preferences_json"]),
            "field_provenance": json.loads(row["provenance_json"]),
            "source": source,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_intake(self, id_q: str, owner: str, revision: int, profile: dict, target: dict, preferences: dict, provenance: dict):
        now = time.time()
        status = "needs_input" if not target.get("company_name") or not target.get("role_id") or not target.get("target_seniority") else "ready"
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM idq_intakes WHERE id_q=? AND owner_id=? AND deleted_at IS NULL",
                (id_q, owner),
            ).fetchone()
            if row is None:
                raise KeyError("Intake not found")
            if row["revision"] != revision:
                raise RuntimeError("Intake revision conflict")
            new_revision = revision + 1
            db.execute(
                "UPDATE idq_intakes SET revision=?,status=?,profile_json=?,target_json=?,preferences_json=?,updated_at=? WHERE id_q=? AND owner_id=?",
                (new_revision, status, _json(profile), _json(target), _json(preferences), now, id_q, owner),
            )
            db.execute(
                "INSERT INTO idq_revisions(id_q,revision,profile_json,target_json,preferences_json,provenance_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (id_q, new_revision, _json(profile), _json(target), _json(preferences), _json(provenance), now),
            )
        return self.get_intake(id_q, owner)

    def create_job(self, *, owner: str, id_q: str, revision: int, task_type: str, job_id: str | None = None) -> str:
        job_id, now = job_id or self.new_id("job"), time.time()
        with self.connection() as db:
            db.execute(
                "INSERT INTO idq_jobs VALUES(?,?,?,?,?,'queued','{}',NULL,?,?)",
                (job_id, owner, id_q, revision, task_type, now, now),
            )
        return job_id

    def finish_job(self, job_id: str, *, result: dict, error_code: str | None = None) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE idq_jobs SET status=?,result_json=?,error_code=?,updated_at=? WHERE job_id=?",
                ("failed" if error_code else "completed", _json(result), error_code, time.time(), job_id),
            )

    def get_job(self, job_id: str, owner: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM idq_jobs WHERE job_id=? AND owner_id=?", (job_id, owner)
            ).fetchone()
        if row is None:
            raise KeyError("Job not found")
        return {
            "job_id": row["job_id"], "id_q": row["id_q"], "revision": row["revision"],
            "task_type": row["task_type"], "status": row["status"],
            "result": json.loads(row["result_json"]), "error_code": row["error_code"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def publish_preparation(self, id_q: str, owner: str, revision: int, knowledge_version: str, result: dict) -> dict:
        pack_id, now = self.new_id("pack"), time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM idq_intakes WHERE id_q=? AND owner_id=? AND deleted_at IS NULL",
                (id_q, owner),
            ).fetchone()
            if row is None:
                raise KeyError("Intake not found")
            if row["revision"] != revision:
                raise RuntimeError("Intake revision conflict")
            existing = db.execute(
                "SELECT pack_id,created_at FROM idq_preparations WHERE id_q=? AND revision=? AND knowledge_version=?",
                (id_q, revision, knowledge_version),
            ).fetchone()
            if existing:
                pack_id, now = existing["pack_id"], existing["created_at"]
            else:
                db.execute(
                    "INSERT INTO idq_preparations VALUES(?,?,?,?,'ready',?,?)",
                    (pack_id, id_q, revision, knowledge_version, _json(result), now),
                )
            db.execute(
                "INSERT OR REPLACE INTO idq_active_preparations VALUES(?,?,?)",
                (id_q, revision, pack_id),
            )
        return {"pack_id": pack_id, "id_q": id_q, "intake_revision": revision, "knowledge_version": knowledge_version, "status": "ready", "created_at": now}

    def get_preparation(self, id_q: str, owner: str, pack_id: str | None = None) -> dict:
        with self.connection() as db:
            intake = db.execute(
                "SELECT revision FROM idq_intakes WHERE id_q=? AND owner_id=? AND deleted_at IS NULL",
                (id_q, owner),
            ).fetchone()
            if intake is None:
                raise KeyError("Intake not found")
            if pack_id:
                row = db.execute(
                    "SELECT p.* FROM idq_preparations p JOIN idq_intakes i ON i.id_q=p.id_q WHERE p.id_q=? AND p.pack_id=? AND i.owner_id=?",
                    (id_q, pack_id, owner),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT p.* FROM idq_active_preparations a JOIN idq_preparations p ON p.pack_id=a.pack_id WHERE a.id_q=? AND a.revision=?",
                    (id_q, intake["revision"]),
                ).fetchone()
        if row is None:
            raise RuntimeError("Preparation required for this intake revision")
        return {
            "pack_id": row["pack_id"], "id_q": row["id_q"],
            "intake_revision": row["revision"], "knowledge_version": row["knowledge_version"],
            "status": row["status"], "result": json.loads(row["result_json"]),
        }

    def import_company_knowledge(self, companies: list[dict], job_descriptions: list[dict]) -> dict:
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for company in companies:
                company_id = str(company["company_id"])
                db.execute(
                    "INSERT INTO idq_companies VALUES(?,?,?) ON CONFLICT(company_id) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                    (company_id, _json(company), now),
                )
            for job in job_descriptions:
                jd_id, company_id = str(job["jd_id"]), str(job["company_id"])
                content_hash = hashlib.sha256(_json(job).encode()).hexdigest()
                db.execute(
                    "INSERT INTO idq_job_descriptions VALUES(?,?,?,?,?) ON CONFLICT(jd_id) DO UPDATE SET company_id=excluded.company_id,payload_json=excluded.payload_json,content_hash=excluded.content_hash,updated_at=excluded.updated_at",
                    (jd_id, company_id, _json(job), content_hash, now),
                )
        return {"companies_imported": len(companies), "job_descriptions_imported": len(job_descriptions)}

    def company_knowledge(self) -> tuple[list[dict], list[dict]]:
        with self.connection() as db:
            companies = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM idq_companies ORDER BY company_id")]
            jobs = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM idq_job_descriptions ORDER BY jd_id")]
        return companies, jobs

    def get_web_research_cache(self, cache_key: str) -> dict | None:
        """Return fresh public research evidence and lazily discard expired entries."""
        now = time.time()
        with self.connection() as db:
            row = db.execute(
                "SELECT payload_json,expires_at FROM idq_web_research_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                db.execute("DELETE FROM idq_web_research_cache WHERE cache_key=?", (cache_key,))
                return None
        return json.loads(row["payload_json"])

    def set_web_research_cache(self, cache_key: str, payload: dict, ttl_seconds: int) -> None:
        now = time.time()
        expires_at = now + max(60, int(ttl_seconds))
        with self.connection() as db:
            db.execute(
                "INSERT INTO idq_web_research_cache(cache_key,payload_json,created_at,expires_at) "
                "VALUES(?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET "
                "payload_json=excluded.payload_json,created_at=excluded.created_at,expires_at=excluded.expires_at",
                (cache_key, _json(payload), now, expires_at),
            )

    def coverage_counts(self) -> dict:
        with self.connection() as db:
            return {
                "company_count": db.execute("SELECT count(*) FROM idq_companies").fetchone()[0],
                "job_description_count": db.execute("SELECT count(*) FROM idq_job_descriptions").fetchone()[0],
            }

    def delete_intake(self, id_q: str, owner: str) -> str | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT source_json FROM idq_intakes WHERE id_q=? AND owner_id=? AND deleted_at IS NULL",
                (id_q, owner),
            ).fetchone()
            if row is None:
                raise KeyError("Intake not found")
            source = json.loads(row["source_json"])
            db.execute("DELETE FROM idq_intakes WHERE id_q=? AND owner_id=?", (id_q, owner))
        return source.get("private_path")
