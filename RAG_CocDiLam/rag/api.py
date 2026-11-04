"""Backend-only API. A single authenticated principal owns each instance."""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from core.profiler.tools import SafeWebSearchTool

from .engine import RAGEngine
from .browserbase import BrowserbaseWebSearchTool
from .indexing import build_index
from .schemas import RAGFollowUpRequest, RAGInterviewRequest, RAGInterviewResponse
from .storage import RAGStore
from .idq_routes import create_idq_router, knowledge_admin_authorized, public_error_message, tokens_match
from .idq_service import IDQKnowledgeService
from .idq_store import IDQStore
from .web_research import CompanyJDWebResearch, WebResearchConfig

ROOT = Path(__file__).resolve().parents[1]


class IndexCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activate_version: str | None = None


def create_app(
    *,
    store_path=None,
    data_dir=None,
    engine=None,
    api_token=None,
    tenant_id=None,
    allow_local=None,
    web_search_tool=None,
):
    store_path = store_path or os.getenv("RAG_DB_PATH", str(ROOT / "data/rag/index.db"))
    data_dir = data_dir or ROOT / "data"
    token = api_token if api_token is not None else os.getenv("RAG_API_TOKEN", "")
    owner = tenant_id or os.getenv("RAG_TENANT_ID", "local")
    local = (
        allow_local
        if allow_local is not None
        else os.getenv("RAG_ALLOW_LOCAL", "0") == "1"
    )
    guard = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        if not token and not local:
            raise RuntimeError(
                "Set RAG_API_TOKEN or explicitly enable RAG_ALLOW_LOCAL=1 for loopback development"
            )
        if engine is not None:
            app.state.engine = engine
        else:
            index_store = RAGStore(store_path)
            try:
                app.state.engine = RAGEngine(index_store, data_dir)
            except KeyError:
                build_index(index_store, data_dir, provider_kind=os.getenv("RAG_EMBEDDING_PROVIDER", "lsa"))
                app.state.engine = RAGEngine(index_store, data_dir)
        app.state.engine.store.purge()
        private_root = Path(os.getenv("RAG_INTAKE_ROOT", str(Path(store_path).parent / "intakes")))
        intake_db = Path(os.getenv("RAG_INTAKE_DB_PATH", str(Path(store_path).parent / "intake.sqlite3")))
        app.state.idq_store = IDQStore(intake_db, private_root)
        web_enabled = os.getenv("RAG_WEB_SEARCH_ENABLED", "1").casefold() not in {"0", "false", "no", "off"}
        configured_provider = os.getenv("RAG_WEB_SEARCH_PROVIDER", "auto").strip().casefold()
        if configured_provider not in {"auto", "browserbase", "brave"}:
            raise RuntimeError("RAG_WEB_SEARCH_PROVIDER must be auto, browserbase, or brave")
        search_tool = web_search_tool
        if search_tool is None and web_enabled:
            timeout = float(os.getenv("RAG_WEB_SEARCH_TIMEOUT_SECONDS", "10"))
            browserbase_key = os.getenv("BROWSERBASE_API_KEY", "").strip()
            brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
            if configured_provider == "browserbase" or (configured_provider == "auto" and browserbase_key):
                search_tool = BrowserbaseWebSearchTool(browserbase_key, timeout=timeout)
            elif configured_provider == "brave" or (configured_provider == "auto" and brave_key):
                search_tool = SafeWebSearchTool(timeout=timeout, api_key=brave_key)
        web_research = CompanyJDWebResearch(
            search_tool,
            app.state.idq_store,
            WebResearchConfig(
                enabled=web_enabled,
                max_results=max(1, min(int(os.getenv("RAG_WEB_SEARCH_MAX_RESULTS", "4")), 4)),
                max_pages=max(0, min(int(os.getenv("RAG_WEB_SEARCH_MAX_PAGES", "4")), 8)),
                cache_ttl_seconds=max(60, int(os.getenv("RAG_WEB_RESEARCH_CACHE_TTL_SECONDS", "86400"))),
                total_timeout_seconds=max(1.0, float(os.getenv("RAG_WEB_RESEARCH_TOTAL_SECONDS", "12"))),
            ),
        )
        app.state.idq_service = IDQKnowledgeService(
            app.state.idq_store,
            app.state.engine,
            Path(data_dir),
            web_research=web_research,
        )
        companies_path = Path(data_dir) / "knowledge" / "companies.json"
        jobs_path = Path(data_dir) / "knowledge" / "job_descriptions.json"
        if companies_path.exists() or jobs_path.exists():
            import json

            companies = json.loads(companies_path.read_text(encoding="utf-8")) if companies_path.exists() else []
            jobs = json.loads(jobs_path.read_text(encoding="utf-8")) if jobs_path.exists() else []
            app.state.idq_store.import_company_knowledge(companies, jobs)
        yield

    app = FastAPI(title="RoleUp RAG + GraphRAG", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        # Ordinary JSON stays small; the dedicated CV route gets one bounded 12 MiB upload.
        cv_upload = request.url.path == "/api/v1/intakes/cv"
        limit = 12 * 1024 * 1024 + 256 * 1024 if cv_upload else 262144
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    return JSONResponse(status_code=413, content={"detail": "Request exceeds the endpoint size limit"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        size, parts = 0, []
        async for part in request.stream():
            size += len(part)
            if size > limit:
                return JSONResponse(
                    status_code=413, content={"detail": "Request exceeds the endpoint size limit"}
                )
            parts.append(part)
        request._body = b"".join(parts)
        return await call_next(request)

    def authorize(request: Request, authorization: str | None = Header(default=None)):
        admin_authenticated = getattr(app.state, "admin_authenticated", None)
        if admin_authenticated and admin_authenticated(request):
            return owner
        if token:
            supplied = (
                authorization.removeprefix("Bearer ")
                if authorization and authorization.startswith("Bearer ")
                else ""
            )
            if not tokens_match(supplied, token):
                raise HTTPException(401, "Invalid backend credentials")
        elif (
            not local
            or not request.client
            or request.client.host not in {"127.0.0.1", "::1", "localhost"}
        ):
            raise HTTPException(
                403, "Unauthenticated access is restricted to loopback development"
            )
        return owner

    def tenant_check(value, principal):
        if value != principal:
            raise HTTPException(403, "Tenant is not authorized for this instance")

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": public_error_message(exc, "Resource not found")})

    @app.exception_handler(ValueError)
    async def invalid_handler(request, exc):
        return JSONResponse(status_code=422, content={"detail": public_error_message(exc, "The request is invalid")})

    @app.exception_handler(PermissionError)
    async def permission_handler(request, exc):
        return JSONResponse(
            status_code=403, content={"detail": "Resource is not authorized"}
        )

    @app.get("/health")
    def health():
        current = app.state.engine
        return {
            "status": "ready",
            "schema_version": "1.0",
            "index_version": current.version,
            "embedding_provider": current.provider.signature,
            "web_search_enabled": app.state.idq_service.web_research.config.enabled,
            "web_search_configured": bool(
                getattr(app.state.idq_service.web_research.search_tool, "api_key", None)
                or web_search_tool is not None
            ),
            "web_search_provider": getattr(
                app.state.idq_service.web_research.search_tool,
                "provider_name",
                "custom" if web_search_tool is not None else None,
            ),
        }

    @app.post(
        "/rag/context",
        response_model=RAGInterviewResponse,
    )
    def context(body: RAGInterviewRequest, principal: Annotated[str, Depends(authorize)]):
        tenant_check(body.tenant_id, principal)
        with guard:
            return app.state.engine.retrieve(body)

    @app.post("/rag/follow-up", response_model=RAGInterviewResponse)
    def follow_up(body: RAGFollowUpRequest, principal: Annotated[str, Depends(authorize)]):
        tenant_check(body.tenant_id, principal)
        with guard:
            return app.state.engine.follow_up(body)

    @app.get("/rag/trace/{request_id}")
    def trace(request_id: str, principal: Annotated[str, Depends(authorize)]):
        return app.state.engine.store.trace(request_id, principal)

    @app.delete("/rag/session/{session_id}")
    def delete_session(session_id: str, principal: Annotated[str, Depends(authorize)]):
        with guard:
            store = app.state.engine.store
            store.session(session_id, principal)
            store.delete_session(session_id, principal)
        return {"deleted": True}

    @app.post("/rag/index")
    def index(body: IndexCommand, request: Request, principal: Annotated[str, Depends(authorize)]):
        # Trusted admin operation, serialized with serving. No arbitrary paths/models.
        # The plain API token (held by every backend caller) is not enough.
        if not knowledge_admin_authorized(request):
            raise HTTPException(403, "Knowledge administrator credentials are required")
        with guard:
            old = app.state.engine
            if body.activate_version:
                replacement = RAGEngine(
                    old.store, data_dir, version=body.activate_version
                )
                old.store.activate(replacement.version)
                result = {"index_version": replacement.version, "activated": True}
            else:
                result = build_index(old.store, data_dir, old.provider)
                replacement = RAGEngine(old.store, data_dir, provider=old.provider)
            app.state.engine = replacement
            # One reference swap; in-flight prepares keep the snapshot they captured.
            app.state.idq_service.swap_engine(replacement)
        return result

    app.include_router(create_idq_router(authorize))
    app.mount("/assets/ui", StaticFiles(directory=ROOT / "ui-shared"), name="shared-ui")
    app.mount("/idq", StaticFiles(directory=ROOT / "ui-intake", html=True), name="idq-ui")

    return app


app = create_app()
