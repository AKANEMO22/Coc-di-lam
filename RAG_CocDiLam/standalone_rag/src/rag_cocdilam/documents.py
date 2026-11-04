"""Persistent document catalog and immutable per-knowledge-base index manifests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class DocumentStore:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    active_version TEXT,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
                    title TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS documents_kb ON documents(knowledge_base_id, deleted);
                CREATE TABLE IF NOT EXISTS document_revisions (
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    revision INTEGER NOT NULL,
                    original_name TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    page_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL,
                    PRIMARY KEY(document_id, revision)
                );
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL,
                    updated REAL NOT NULL,
                    FOREIGN KEY(document_id, revision) REFERENCES document_revisions(document_id, revision)
                );
                CREATE INDEX IF NOT EXISTS jobs_status ON ingestion_jobs(status, created);
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    page INTEGER,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    vector BLOB,
                    FOREIGN KEY(document_id, revision) REFERENCES document_revisions(document_id, revision)
                );
                CREATE INDEX IF NOT EXISTS chunks_revision ON document_chunks(document_id, revision);
                CREATE TABLE IF NOT EXISTS knowledge_base_versions (
                    id TEXT PRIMARY KEY,
                    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
                    base_version TEXT NOT NULL,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS version_documents (
                    version_id TEXT NOT NULL REFERENCES knowledge_base_versions(id),
                    document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY(version_id, document_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def relocate_uploads(self, upload_dir: Path) -> None:
        """Resolve original-container upload paths after remounting its volume."""
        legacy_root = Path("/data/rag/uploads")
        upload_dir = upload_dir.resolve()
        with self.connect() as db:
            rows = db.execute("SELECT document_id, revision, storage_path FROM document_revisions").fetchall()
            for row in rows:
                try:
                    relative = Path(row["storage_path"]).relative_to(legacy_root)
                    target = (upload_dir / relative).resolve()
                    target.relative_to(upload_dir)
                except ValueError:
                    continue
                if target.is_file():
                    db.execute("UPDATE document_revisions SET storage_path=? WHERE document_id=? AND revision=?",
                               (str(target), row["document_id"], row["revision"]))

    def ensure_default(self, base_version: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT id FROM knowledge_bases WHERE id='interview'").fetchone()
            if row:
                active = db.execute("SELECT active_version FROM knowledge_bases WHERE id='interview'").fetchone()[0]
                if active:
                    return
            version = self._version_id("interview", base_version, [])
            db.execute(
                "INSERT OR IGNORE INTO knowledge_bases(id,name,created) VALUES('interview','Interview knowledge',?)",
                (time.time(),),
            )
            db.execute(
                "INSERT OR IGNORE INTO knowledge_base_versions(id,knowledge_base_id,base_version,created) VALUES(?,?,?,?)",
                (version, "interview", base_version, time.time()),
            )
            db.execute("UPDATE knowledge_bases SET active_version=? WHERE id='interview'", (version,))

    def refresh_base_version(self, base_version: str) -> None:
        """Publish a new composite version for every knowledge base after seed rebuild."""
        with self.connect() as db:
            bases = db.execute("SELECT id FROM knowledge_bases").fetchall()
            now = time.time()
            for base in bases:
                kb_id = base["id"]
                rows = db.execute("""
                    SELECT d.id, MAX(r.revision) AS revision FROM documents d
                    JOIN document_revisions r ON r.document_id=d.id
                    WHERE d.knowledge_base_id=? AND d.deleted=0 AND r.status='ready' GROUP BY d.id
                """, (kb_id,)).fetchall()
                members = [(row["id"], int(row["revision"])) for row in rows]
                version = self._version_id(kb_id, base_version, members)
                db.execute("INSERT OR IGNORE INTO knowledge_base_versions(id,knowledge_base_id,base_version,created) VALUES(?,?,?,?)", (version, kb_id, base_version, now))
                db.executemany("INSERT OR IGNORE INTO version_documents(version_id,document_id,revision) VALUES(?,?,?)", [(version, doc_id, rev) for doc_id, rev in members])
                db.execute("UPDATE knowledge_bases SET active_version=? WHERE id=?", (version, kb_id))

    @staticmethod
    def _version_id(kb_id: str, base_version: str, documents: list[tuple[str, int]]) -> str:
        raw = _json([kb_id, base_version, sorted(documents)]).encode()
        return "idx-" + hashlib.sha256(raw).hexdigest()[:24]

    def create_knowledge_base(self, kb_id: str, name: str, base_version: str) -> dict:
        if not kb_id or len(kb_id) > 64 or not all(c.isalnum() or c in "-_" for c in kb_id):
            raise ValueError("Knowledge base ID may contain letters, numbers, hyphen and underscore")
        version = self._version_id(kb_id, base_version, [])
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO knowledge_bases(id,name,active_version,created) VALUES(?,?,?,?)", (kb_id, name, version, now))
            db.execute("INSERT INTO knowledge_base_versions(id,knowledge_base_id,base_version,created) VALUES(?,?,?,?)", (version, kb_id, base_version, now))
        return {"id": kb_id, "name": name, "active_version": version, "created": now}

    def knowledge_bases(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,active_version,created FROM knowledge_bases ORDER BY created,id")]

    def add_document(self, kb_id: str, title: str, filename: str, path: str, checksum: str, metadata: dict, document_id: str | None = None) -> tuple[str, str, int]:
        doc_id, job_id, now = document_id or str(uuid4()), str(uuid4()), time.time()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone():
                raise KeyError("Knowledge base not found")
            db.execute("INSERT INTO documents(id,knowledge_base_id,title,metadata,created) VALUES(?,?,?,?,?)", (doc_id, kb_id, title, _json(metadata), now))
            db.execute("INSERT INTO document_revisions(document_id,revision,original_name,storage_path,content_hash,status,created) VALUES(?,1,?,?,?,'queued',?)", (doc_id, filename, path, checksum, now))
            db.execute("INSERT INTO ingestion_jobs(id,document_id,revision,status,created,updated) VALUES(?,?,1,'queued',?,?)", (job_id, doc_id, now, now))
        return doc_id, job_id, 1

    def queue_reprocess(self, doc_id: str) -> tuple[str, int]:
        job_id, now = str(uuid4()), time.time()
        with self.connect() as db:
            doc = db.execute("SELECT deleted FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not doc or doc[0]:
                raise KeyError("Document not found")
            previous = db.execute("SELECT * FROM document_revisions WHERE document_id=? ORDER BY revision DESC LIMIT 1", (doc_id,)).fetchone()
            revision = int(previous["revision"]) + 1
            db.execute("INSERT INTO document_revisions(document_id,revision,original_name,storage_path,content_hash,status,created) VALUES(?,?,?,?,?,'queued',?)", (doc_id, revision, previous["original_name"], previous["storage_path"], previous["content_hash"], now))
            db.execute("INSERT INTO ingestion_jobs(id,document_id,revision,status,created,updated) VALUES(?,?,?,'queued',?,?)", (job_id, doc_id, revision, now, now))
        return job_id, revision

    def claim_job(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM ingestion_jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            now = time.time()
            db.execute("UPDATE ingestion_jobs SET status='processing',updated=? WHERE id=?", (now, row["id"]))
            db.execute("UPDATE document_revisions SET status='processing',error='' WHERE document_id=? AND revision=?", (row["document_id"], row["revision"]))
            return {**dict(row), "status": "processing"}

    def job_input(self, document_id: str, revision: int) -> dict:
        with self.connect() as db:
            row = db.execute("""
                SELECT d.id,d.knowledge_base_id,d.title,d.metadata,d.deleted,r.original_name,
                       r.storage_path,r.content_hash,r.revision
                FROM documents d JOIN document_revisions r ON r.document_id=d.id
                WHERE d.id=? AND r.revision=?
            """, (document_id, revision)).fetchone()
        if not row or row["deleted"]:
            raise KeyError("Document is unavailable")
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def storage_paths(self, doc_id: str) -> list[Path]:
        """Return original-file paths for every revision so deletion can purge them."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT storage_path FROM document_revisions WHERE document_id=?",
                (doc_id,),
            ).fetchall()
        return [Path(row["storage_path"]) for row in rows]

    def complete_job(self, job: dict, pages: list[str], chunks: list[dict], base_version: str) -> str:
        now = time.time()
        with self.connect() as db:
            current = db.execute("SELECT deleted,knowledge_base_id FROM documents WHERE id=?", (job["document_id"],)).fetchone()
            if not current or current["deleted"]:
                db.execute("UPDATE ingestion_jobs SET status='cancelled',updated=? WHERE id=?", (now, job["id"]))
                return ""
            for chunk in chunks:
                db.execute("""
                    INSERT OR REPLACE INTO document_chunks(id,document_id,revision,chunk_index,page,content,metadata,vector)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (chunk["id"], job["document_id"], job["revision"], chunk["chunk_index"], chunk.get("page"), chunk["content"], _json(chunk["metadata"]), chunk.get("vector")))
            db.execute("UPDATE document_revisions SET status='ready',page_count=?,chunk_count=?,error='' WHERE document_id=? AND revision=?", (len(pages), len(chunks), job["document_id"], job["revision"]))
            db.execute("UPDATE ingestion_jobs SET status='ready',updated=?,error='' WHERE id=?", (now, job["id"]))
            # Keep a previous successful revision active when a reprocess has not finished.
            rows = db.execute("""
                SELECT d.id, MAX(r.revision) AS revision
                FROM documents d JOIN document_revisions r ON r.document_id=d.id
                WHERE d.knowledge_base_id=? AND d.deleted=0 AND r.status='ready'
                GROUP BY d.id
            """, (current["knowledge_base_id"],)).fetchall()
            members = [(row["id"], int(row["revision"])) for row in rows]
            version = self._version_id(current["knowledge_base_id"], base_version, members)
            db.execute("INSERT OR IGNORE INTO knowledge_base_versions(id,knowledge_base_id,base_version,created) VALUES(?,?,?,?)", (version, current["knowledge_base_id"], base_version, now))
            db.executemany("INSERT OR IGNORE INTO version_documents(version_id,document_id,revision) VALUES(?,?,?)", [(version, doc_id, rev) for doc_id, rev in members])
            db.execute("UPDATE knowledge_bases SET active_version=? WHERE id=?", (version, current["knowledge_base_id"]))
        return version

    def fail_job(self, job: dict, message: str) -> None:
        message = message[:1000]
        now = time.time()
        with self.connect() as db:
            db.execute("UPDATE document_revisions SET status='failed',error=? WHERE document_id=? AND revision=?", (message, job["document_id"], job["revision"]))
            db.execute("UPDATE ingestion_jobs SET status='failed',error=?,updated=? WHERE id=?", (message, now, job["id"]))

    def delete_document(self, doc_id: str, base_version: str) -> str:
        now = time.time()
        with self.connect() as db:
            row = db.execute("SELECT knowledge_base_id,deleted FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                raise KeyError("Document not found")
            kb_id = row["knowledge_base_id"]
            if row["deleted"]:
                active = db.execute("SELECT active_version FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
                return active[0]
            db.execute("UPDATE documents SET deleted=1 WHERE id=?", (doc_id,))
            db.execute("UPDATE ingestion_jobs SET status='cancelled',updated=? WHERE document_id=? AND status IN ('queued','processing')", (now, doc_id))
            # Search snapshots keep their manifest, but deleted source text and vectors are purged.
            db.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
            rows = db.execute("""
                SELECT d.id, MAX(r.revision) AS revision FROM documents d
                JOIN document_revisions r ON r.document_id=d.id
                WHERE d.knowledge_base_id=? AND d.deleted=0 AND r.status='ready' GROUP BY d.id
            """, (kb_id,)).fetchall()
            members = [(r["id"], int(r["revision"])) for r in rows]
            version = self._version_id(kb_id, base_version, members)
            db.execute("INSERT OR IGNORE INTO knowledge_base_versions(id,knowledge_base_id,base_version,created) VALUES(?,?,?,?)", (version, kb_id, base_version, now))
            db.executemany("INSERT OR IGNORE INTO version_documents(version_id,document_id,revision) VALUES(?,?,?)", [(version, did, rev) for did, rev in members])
            db.execute("UPDATE knowledge_bases SET active_version=? WHERE id=?", (version, kb_id))
        return version

    def activate_version(self, kb_id: str, version: str) -> None:
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM knowledge_base_versions WHERE id=? AND knowledge_base_id=?", (version, kb_id)).fetchone():
                raise KeyError("Index version not found")
            db.execute("UPDATE knowledge_bases SET active_version=? WHERE id=?", (version, kb_id))

    def active_version(self, kb_id: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT active_version FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
        if not row:
            raise KeyError("Knowledge base not found")
        return row[0]

    def version_base(self, kb_id: str, version: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT base_version FROM knowledge_base_versions WHERE id=? AND knowledge_base_id=?", (version, kb_id)).fetchone()
        if not row:
            raise KeyError("Index version not found")
        return row[0]

    def chunks_for_version(self, kb_id: str, version: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT c.*,d.title,d.metadata AS document_metadata,r.original_name,r.content_hash
                FROM version_documents vd
                JOIN documents d ON d.id=vd.document_id
                JOIN document_revisions r ON r.document_id=vd.document_id AND r.revision=vd.revision
                JOIN document_chunks c ON c.document_id=vd.document_id AND c.revision=vd.revision
                WHERE vd.version_id=? AND d.knowledge_base_id=? AND d.deleted=0
                ORDER BY c.document_id,c.chunk_index
            """, (version, kb_id)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata"))
            item["document_metadata"] = json.loads(item["document_metadata"])
            result.append(item)
        return result

    def documents(self, kb_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT d.id,d.title,d.metadata,d.created,r.original_name,r.content_hash,r.status,
                       r.error,r.page_count,r.chunk_count,r.revision,r.created AS revision_created
                FROM documents d LEFT JOIN document_revisions r ON r.document_id=d.id
                AND r.revision=(SELECT MAX(r2.revision) FROM document_revisions r2 WHERE r2.document_id=d.id)
                WHERE d.knowledge_base_id=? AND d.deleted=0 ORDER BY d.created DESC
            """, (kb_id,)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            output.append(item)
        return output

    def document(self, doc_id: str) -> dict:
        rows = [row for row in self.documents_for_id(doc_id)]
        if not rows:
            raise KeyError("Document not found")
        return rows[0]

    def documents_for_id(self, doc_id: str) -> list[dict]:
        with self.connect() as db:
            row = db.execute("SELECT knowledge_base_id FROM documents WHERE id=? AND deleted=0", (doc_id,)).fetchone()
        return self.documents(row[0]) if row else []

    def chunks_for_document(self, doc_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT c.id,c.revision,c.chunk_index,c.page,c.content,c.metadata
                FROM document_chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.document_id=? AND d.deleted=0
                AND c.revision=(SELECT MAX(r.revision) FROM document_revisions r WHERE r.document_id=c.document_id AND r.status='ready')
                ORDER BY c.chunk_index
            """, (doc_id,)).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("Ingestion job not found")
        return dict(row)

    def index_versions(self, kb_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT v.id,v.base_version,v.created,
                       (SELECT COUNT(*) FROM version_documents vd WHERE vd.version_id=v.id) AS document_count,
                       (SELECT COUNT(*) FROM document_chunks c JOIN version_documents vd
                         ON vd.document_id=c.document_id AND vd.revision=c.revision WHERE vd.version_id=v.id) AS chunk_count
                FROM knowledge_base_versions v WHERE v.knowledge_base_id=? ORDER BY v.created DESC
            """, (kb_id,)).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self.connect() as db:
            documents = db.execute("SELECT COUNT(*) FROM documents WHERE deleted=0").fetchone()[0]
            chunks = db.execute("SELECT COUNT(*) FROM document_chunks c JOIN documents d ON d.id=c.document_id WHERE d.deleted=0").fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status IN ('queued','processing')").fetchone()[0]
            failed = db.execute("SELECT COUNT(*) FROM ingestion_jobs WHERE status='failed'").fetchone()[0]
        return {"documents": documents, "chunks": chunks, "jobs_pending": pending, "jobs_failed": failed}

    def recover_interrupted_jobs(self) -> None:
        with self.connect() as db:
            now = time.time()
            db.execute("UPDATE ingestion_jobs SET status='queued',updated=? WHERE status='processing'", (now,))
            db.execute("UPDATE document_revisions SET status='queued' WHERE status='processing'")

