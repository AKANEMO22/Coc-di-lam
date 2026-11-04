"""Serve the administration UI and current ID_Q API on one origin."""

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from standalone_rag.src.rag_cocdilam.api import create_app as create_admin_app

from .api import ROOT, create_app as create_idq_app


def is_idq_path(path: str) -> bool:
    return (
        path == "/health"
        or path in {"/idq", "/rag"}
        or path.startswith(("/idq/", "/rag/", "/api/v1/intakes/", "/api/v1/internal/intakes/", "/api/v1/knowledge/"))
        or path in {"/api/v1/intakes", "/api/v1/internal/intakes", "/api/v1/knowledge"}
        or path.startswith("/api/v1/jobs/job_")
        or path == "/api/v1/jobs/crawl"
    )


class ServiceRouter:
    def __init__(self, admin, idq):
        self.admin, self.idq = admin, idq

    async def __call__(self, scope, receive, send):
        target = self.idq if is_idq_path(scope.get("path", "/")) else self.admin
        await target(scope, receive, send)


def create_app(*, admin=None, idq=None):
    if admin is None:
        storage = Path(os.getenv("RAG_ADMIN_STORAGE_DIR", "/data/rag-admin"))
        admin = create_admin_app(
            store_path=storage / "index.db",
            storage_dir=storage,
            data_dir=ROOT / "data",
            # Preserve the original local UI password unless explicitly overridden.
            admin_password=os.getenv("RAG_ADMIN_PASSWORD", "local-rag-admin-change-me"),
        )
    if idq is None:
        idq = create_idq_app()

    @asynccontextmanager
    async def lifespan(app):
        async with admin.router.lifespan_context(admin):
            async with idq.router.lifespan_context(idq):
                yield

    app = FastAPI(title="RAG CocDiLam", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.admin_app = admin
    app.state.idq_app = idq

    def admin_authenticated(request):
        session_id = request.cookies.get("rag_admin_session", "")
        return bool(session_id and getattr(admin.state, "sessions", {}).get(session_id, 0) > time.time())

    idq.state.admin_authenticated = admin_authenticated
    app.mount("/assets/ui", StaticFiles(directory=ROOT / "ui-shared"), name="shared-ui")
    app.mount("/", ServiceRouter(admin, idq))
    return app


app = create_app()
