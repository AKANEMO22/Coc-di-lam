"""Standalone RAG API, admin UI, retrieval endpoint, and document ingestion lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from .adapter import RAGRetrievalClient
from .contracts import RetrievalError, RetrievalQuery
from .document_retrieval import DocumentRetriever
from .documents import DocumentStore
from .engine import RAGEngine
from .indexing import build_index
from .ingestion import run_worker, validate_upload_name
from .schemas import RAGFollowUpRequest, RAGInterviewRequest, RAGInterviewResponse
from .service_schemas import CreateKnowledgeBase, DocumentMetadata, LoginRequest, RetrievalResponse, SearchRequest
from .storage import RAGStore

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
MAX_UPLOAD_BYTES = int(os.getenv("RAG_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))


class IndexCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activate_version: str | None = None


def create_app(*, store_path=None, data_dir=None, engine=None, api_token=None,
               tenant_id=None, allow_local=None, admin_password=None,
               storage_dir=None, upload_limit=None):
    database = Path(store_path or os.getenv("RAG_DB_PATH", str(ROOT / "data/runtime/index.db")))
    seed_dir = Path(data_dir or os.getenv("RAG_DATA_DIR", str(ROOT / "data/seeds")))
    storage = Path(storage_dir or os.getenv("RAG_STORAGE_DIR", str(ROOT / "data/runtime")))
    upload_dir = storage / "uploads"
    token = api_token if api_token is not None else os.getenv("RAG_API_TOKEN", "")
    password = admin_password if admin_password is not None else os.getenv("RAG_ADMIN_PASSWORD", "")
    owner = tenant_id or os.getenv("RAG_TENANT_ID", "local")
    local = allow_local if allow_local is not None else os.getenv("RAG_ALLOW_LOCAL", "0") == "1"
    max_upload = upload_limit if upload_limit is not None else int(os.getenv("RAG_MAX_UPLOAD_BYTES", str(MAX_UPLOAD_BYTES)))
    lock = threading.RLock()
    cache_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not token and not local:
            raise RuntimeError("Set RAG_API_TOKEN or explicitly enable loopback-only RAG_ALLOW_LOCAL=1")
        if not password:
            raise RuntimeError("Set a dedicated RAG_ADMIN_PASSWORD for the administration UI")
        upload_dir.mkdir(parents=True, exist_ok=True)
        rag_store = RAGStore(database)
        try:
            rag_store.load()
        except KeyError:
            if not (seed_dir / "interview_frameworks.json").is_file():
                raise RuntimeError(f"RAG_DATA_DIR must contain interview_frameworks.json: {seed_dir}")
            build_index(rag_store, seed_dir, provider_kind=os.getenv("RAG_EMBEDDING_PROVIDER", "lsa"))
        initial = engine or RAGEngine(rag_store, seed_dir, include_local_profiles=False)
        docs = DocumentStore(storage / "documents.sqlite3")
        docs.relocate_uploads(upload_dir)
        docs.ensure_default(initial.version)
        docs.recover_interrupted_jobs()
        app.state.rag_store = rag_store
        app.state.document_store = docs
        app.state.document_retriever = DocumentRetriever(docs)
        app.state.engine_cache = {initial.version: (initial, RAGRetrievalClient(data_dir=seed_dir, index_path=database, engine=initial))}
        app.state.cache_lock = cache_lock
        app.state.lock = lock
        app.state.sessions = {}
        app.state.storage_dir = storage
        app.state.upload_dir = upload_dir
        app.state.seed_dir = seed_dir
        app.state.max_upload = max_upload
        app.state.stop_worker = asyncio.Event()
        app.state.worker = asyncio.create_task(run_worker(docs, upload_dir, lambda: _current_engine(app), app.state.stop_worker))
        try:
            yield
        finally:
            app.state.stop_worker.set()
            app.state.worker.cancel()
            try:
                await app.state.worker
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="RAG_CocDiLam", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def bound_json_body(request: Request, call_next):
        # Multipart uploads stream to disk and enforce their own limit in the route.
        content_type = request.headers.get("content-type", "").casefold()
        if content_type.startswith("multipart/form-data"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
                # Allow multipart boundaries and fields in addition to the configured file cap.
                if declared_size > max_upload + 1024 * 1024:
                    return JSONResponse(status_code=413, content={"detail": "Upload request exceeds configured limit"})
            return await call_next(request)
        size = 0
        parts = []
        async for part in request.stream():
            size += len(part)
            if size > 262144:
                return JSONResponse(status_code=413, content={"detail": "Request exceeds 256 KiB"})
            parts.append(part)
        request._body = b"".join(parts)
        return await call_next(request)

    def principal(request: Request, authorization: str | None = Header(default=None)) -> str:
        if token and authorization and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
            if hmac.compare_digest(supplied, token):
                return owner
        session_id = request.cookies.get("rag_admin_session", "")
        expiry = app.state.sessions.get(session_id, 0) if hasattr(app.state, "sessions") else 0
        if session_id and expiry > time.time():
            return "admin"
        if not token and local and request.client and request.client.host in {"127.0.0.1", "::1", "localhost"}:
            return owner
        raise HTTPException(status_code=401, detail="Authentication required")

    def admin_session(request: Request) -> str:
        session_id = request.cookies.get("rag_admin_session", "")
        expiry = app.state.sessions.get(session_id, 0) if hasattr(app.state, "sessions") else 0
        if session_id and expiry > time.time():
            return "admin"
        if not token and local and request.client and request.client.host in {"127.0.0.1", "::1", "localhost"}:
            return owner
        raise HTTPException(status_code=401, detail="Administrator login required")

    @app.exception_handler(KeyError)
    async def missing_handler(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc.args[0])})

    @app.exception_handler(ValueError)
    async def invalid_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RetrievalError)
    async def retrieval_error_handler(request: Request, exc: RetrievalError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})


    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "live"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        version, _ = app.state.rag_store.load()
        active = {kb["id"]: kb["active_version"] for kb in app.state.document_store.knowledge_bases()}
        return {"status": "ready", "schema_version": "1.0", "base_index_version": version,
                "knowledge_bases": active, **app.state.document_store.stats()}

    @app.get("/api/v1/capabilities", dependencies=[Depends(principal)])
    def capabilities():
        return {"schema_version": "1.0", "collections": ["question_bank", "rubric", "role_profile", "company_knowledge"],
                "document_types": ["pdf", "docx", "txt", "md"], "max_top_k": 100}

    @app.post("/api/v1/auth/login")
    def login(body: LoginRequest, response: Response):
        expected = password
        if not expected or not hmac.compare_digest(body.password, expected):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        session_id = secrets.token_urlsafe(32)
        app.state.sessions[session_id] = time.time() + 12 * 60 * 60
        response.set_cookie("rag_admin_session", session_id, httponly=True, secure=os.getenv("RAG_COOKIE_SECURE", "0") == "1",
                            samesite="strict", max_age=12 * 60 * 60, path="/")
        return {"authenticated": True}

    @app.post("/api/v1/auth/logout", dependencies=[Depends(admin_session)])
    def logout(request: Request, response: Response):
        app.state.sessions.pop(request.cookies.get("rag_admin_session", ""), None)
        response.delete_cookie("rag_admin_session", path="/")
        return {"authenticated": False}

    @app.get("/api/v1/auth/me")
    def whoami(request: Request):
        session_id = request.cookies.get("rag_admin_session", "")
        return {"authenticated": bool(session_id and app.state.sessions.get(session_id, 0) > time.time())}

    @app.post("/api/v1/retrieval/search", response_model=RetrievalResponse, dependencies=[Depends(principal)])
    def search(body: SearchRequest):
        store: DocumentStore = app.state.document_store
        version = body.index_version or store.active_version(body.knowledge_base_id)
        base_version = store.version_base(body.knowledge_base_id, version)
        client = _client_for(app, base_version)
        query = RetrievalQuery(collection=body.collection, text=body.text, filters=body.filters,
                               top_k=body.top_k, knowledge_base_id=body.knowledge_base_id, index_version=version)
        with app.state.lock:
            chunks = app.state.document_retriever.search(query, version, client)
        return {"schema_version": "1.0", "request_id": str(uuid4()), "knowledge_base_id": body.knowledge_base_id,
                "index_version": version, "chunks": [chunk.to_dict() for chunk in chunks]}

    @app.get("/api/v1/stats", dependencies=[Depends(admin_session)])
    def stats():
        return app.state.document_store.stats()

    @app.get("/api/v1/knowledge-bases", dependencies=[Depends(admin_session)])
    def list_knowledge_bases():
        return {"items": app.state.document_store.knowledge_bases()}

    @app.post("/api/v1/knowledge-bases", dependencies=[Depends(admin_session)])
    def create_knowledge_base(body: CreateKnowledgeBase):
        base_version, _ = app.state.rag_store.load()
        return app.state.document_store.create_knowledge_base(body.id, body.name, base_version)

    @app.get("/api/v1/knowledge-bases/{kb_id}/documents", dependencies=[Depends(admin_session)])
    def documents(kb_id: str):
        app.state.document_store.active_version(kb_id)
        return {"items": app.state.document_store.documents(kb_id)}

    @app.post("/api/v1/knowledge-bases/{kb_id}/documents", status_code=202, dependencies=[Depends(admin_session)])
    async def upload_document(kb_id: str, file: UploadFile = File(...), metadata: str = Form("{}")):
        store: DocumentStore = app.state.document_store
        store.active_version(kb_id)
        filename, suffix = validate_upload_name(file.filename)
        try:
            parsed_metadata = DocumentMetadata.model_validate_json(metadata).model_dump()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="metadata must be valid document metadata JSON") from exc
        document_id = str(uuid4())
        destination_dir = app.state.upload_dir / kb_id / document_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / ("original" + suffix)
        temporary = destination.with_suffix(destination.suffix + ".uploading")
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("wb") as output:
                while block := await file.read(1024 * 1024):
                    total += len(block)
                    if total > app.state.max_upload:
                        raise HTTPException(status_code=413, detail=f"Maximum file size is {app.state.max_upload} bytes")
                    digest.update(block)
                    output.write(block)
            if total == 0:
                raise HTTPException(status_code=422, detail="Uploaded file is empty")
            os.replace(temporary, destination)
            doc_id, job_id, revision = store.add_document(kb_id, Path(filename).stem, filename, str(destination), digest.hexdigest(), parsed_metadata, document_id=document_id)
            return {"document_id": doc_id, "job_id": job_id, "revision": revision, "status": "queued", "size_bytes": total}
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/v1/documents/{doc_id}", dependencies=[Depends(admin_session)])
    def document_detail(doc_id: str):
        return app.state.document_store.document(doc_id)

    @app.get("/api/v1/documents/{doc_id}/chunks", dependencies=[Depends(admin_session)])
    def document_chunks(doc_id: str):
        return {"items": app.state.document_store.chunks_for_document(doc_id)}

    @app.post("/api/v1/documents/{doc_id}/reprocess", status_code=202, dependencies=[Depends(admin_session)])
    def reprocess_document(doc_id: str):
        job_id, revision = app.state.document_store.queue_reprocess(doc_id)
        return {"job_id": job_id, "revision": revision, "status": "queued"}

    @app.delete("/api/v1/documents/{doc_id}", dependencies=[Depends(admin_session)])
    def delete_document(doc_id: str):
        base, _ = app.state.rag_store.load()
        paths = app.state.document_store.storage_paths(doc_id)
        version = app.state.document_store.delete_document(doc_id, base)
        upload_root = app.state.upload_dir.resolve()
        for path in paths:
            try:
                target = path.resolve()
                target.relative_to(upload_root)
                target.unlink(missing_ok=True)
            except ValueError as exc:
                logger.error("Refusing document deletion outside the upload directory")
                raise HTTPException(status_code=500, detail="Stored document path is invalid") from exc
            except OSError as exc:
                logger.exception("Could not remove a deleted source file")
                raise HTTPException(
                    status_code=500,
                    detail="Document was removed from retrieval; retry deletion to remove its stored file",
                ) from exc
            directory = target.parent
            while directory != upload_root and upload_root in directory.parents:
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent
        return {"deleted": True, "active_version": version}

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(admin_session)])
    def get_job(job_id: str):
        return app.state.document_store.job(job_id)

    @app.get("/api/v1/knowledge-bases/{kb_id}/indexes", dependencies=[Depends(principal)])
    def list_indexes(kb_id: str):
        return {"active_version": app.state.document_store.active_version(kb_id), "items": app.state.document_store.index_versions(kb_id)}

    @app.post("/api/v1/knowledge-bases/{kb_id}/indexes/{version}/activate", dependencies=[Depends(admin_session)])
    def activate_index(kb_id: str, version: str):
        app.state.document_store.activate_version(kb_id, version)
        return {"knowledge_base_id": kb_id, "active_version": version}

    @app.post("/api/v1/knowledge-bases/{kb_id}/rebuild", status_code=202, dependencies=[Depends(admin_session)])
    def rebuild_knowledge_base(kb_id: str):
        app.state.document_store.active_version(kb_id)
        jobs = []
        for item in app.state.document_store.documents(kb_id):
            if item.get("status") in {"ready", "failed"}:
                job_id, revision = app.state.document_store.queue_reprocess(item["id"])
                jobs.append({"job_id": job_id, "document_id": item["id"], "revision": revision})
        return {"queued": len(jobs), "jobs": jobs}

    @app.post("/rag/context", response_model=RAGInterviewResponse, dependencies=[Depends(principal)])
    def context(body: RAGInterviewRequest):
        if body.tenant_id != owner:
            raise HTTPException(status_code=403, detail="Tenant is not authorized for this instance")
        with app.state.lock:
            return _current_engine(app).retrieve(body)

    @app.post("/rag/follow-up", response_model=RAGInterviewResponse, dependencies=[Depends(principal)])
    def follow_up(body: RAGFollowUpRequest):
        if body.tenant_id != owner:
            raise HTTPException(status_code=403, detail="Tenant is not authorized for this instance")
        with app.state.lock:
            return _current_engine(app).follow_up(body)

    @app.get("/rag/trace/{request_id}", dependencies=[Depends(principal)])
    def trace(request_id: str):
        return _current_engine(app).store.trace(request_id, owner)

    @app.delete("/rag/session/{session_id}", dependencies=[Depends(principal)])
    def delete_session(session_id: str):
        current = _current_engine(app)
        current.store.session(session_id, owner)
        current.store.delete_session(session_id, owner)
        return {"deleted": True}

    @app.post("/rag/index", dependencies=[Depends(admin_session)])
    def index(body: IndexCommand):
        with app.state.lock:
            old = _current_engine(app)
            if body.activate_version:
                replacement = RAGEngine(old.store, app.state.seed_dir, version=body.activate_version, include_local_profiles=False)
                old.store.activate(replacement.version)
                result = {"index_version": replacement.version, "activated": True}
            else:
                result = build_index(old.store, app.state.seed_dir, old.provider)
                replacement = RAGEngine(old.store, app.state.seed_dir, provider=old.provider, include_local_profiles=False)
            app.state.engine_cache[replacement.version] = (replacement, RAGRetrievalClient(data_dir=app.state.seed_dir, index_path=database, engine=replacement))
            app.state.document_store.refresh_base_version(replacement.version)
            return result

    @app.get("/", include_in_schema=False)
    def index_ui():
        return FileResponse(FRONTEND / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def ui_script():
        return FileResponse(FRONTEND / "app.js", media_type="text/javascript")

    @app.get("/styles.css", include_in_schema=False)
    def ui_style():
        return FileResponse(FRONTEND / "styles.css", media_type="text/css")

    return app


def _current_engine(app: FastAPI) -> RAGEngine:
    version, _ = app.state.rag_store.load()
    return _engine_for(app, version)


def _engine_for(app: FastAPI, version: str) -> RAGEngine:
    with app.state.cache_lock:
        existing = app.state.engine_cache.get(version)
        if existing:
            return existing[0]
        engine = RAGEngine(app.state.rag_store, app.state.seed_dir, version=version, include_local_profiles=False)
        client = RAGRetrievalClient(data_dir=app.state.seed_dir, index_path=app.state.rag_store.path, engine=engine)
        app.state.engine_cache[version] = (engine, client)
        return engine


def _client_for(app: FastAPI, base_version: str) -> RAGRetrievalClient:
    _engine_for(app, base_version)
    return app.state.engine_cache[base_version][1]


app = create_app()

