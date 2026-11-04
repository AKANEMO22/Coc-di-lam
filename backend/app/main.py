"""Interview gateway: REST for sessions (CV upload, form, plan, report) and a voice WebSocket per session.

The browser only talks to this service. It signs core service tokens itself and holds the speech
and ID_Q service tokens, so no secret reaches the client.

Creating a session: the CV (or the form) goes to the ID_Q service of RAG_CocDiLam, which stores it,
extracts the profile and prepares a knowledge pack (rubric, questions, JD); core is then created
from that `id_q` + `pack_id` and fetches the pack itself. Core runs no retrieval of its own.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from app import auth
from app.auth import AuthError, Principal
from app.bridge import Bridge, default_ws_connect
from app.config import Settings, get_settings, split_csv
from app.core_client import CoreClient, CoreError
from app.cv_check import analyze_cv_text
from app.documents import DocumentExtractionError, extract_document_text
from app.job_sync.router import router as job_sync_router, scheduler as job_sync_scheduler
from app.limits import RateLimiter
from app.metrics import Metrics
from app.owners import Owners
from app.rag_client import RagClient, RagError
from app.uploads import check_upload

logger = logging.getLogger(__name__)

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_PAYLOAD_CHARS = 200_000
LIST_CONCURRENCY = 8
LIST_MAX_OWNED = 500
MAX_DOCUMENT_CHARS = 20_000  # per in-room document, well below the core's answer limit once combined


SENIORITY = {"intern", "fresher", "junior", "mid", "senior"}
ROUNDS = {"hr", "technical", "hiring_manager", "combined"}
MAX_JD_CHARS = 180_000


class LoginBody(BaseModel):
    student_id: str
    name: str = Field("", max_length=120)
    access_code: str = ""
    # First sign-in only: the profile that registers this student id.
    email: str = Field("", max_length=160)
    major: str = Field("", max_length=120)
    school_year: str = Field("", max_length=40)


class CheckBody(BaseModel):
    student_id: str
    access_code: str = ""


JOB_ADMIN_PATHS = {"/api/sync", "/api/job-sync", "/api/scheduler/start", "/api/scheduler/stop"}


def admin_only(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    settings: Settings = request.app.state.settings
    # Open sign-in identifies people but trusts anyone with a username: job sync stays as usable as without
    # sign-in. Student sign-in (a real deployment) keeps these routes for X-Admin-Token holders.
    if settings.auth_mode != "student":
        return
    expected = settings.admin_token.encode()
    if not expected or not hmac.compare_digest((x_admin_token or "").encode(), expected):
        raise HTTPException(403, "Cần quyền quản trị.")


for _route in job_sync_router.routes:
    if getattr(_route, "path", "") in JOB_ADMIN_PATHS and "POST" in getattr(_route, "methods", ()):
        _route.dependencies.append(Depends(admin_only))


def _text(value: Any, limit: int) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:limit] or None


def _normalize_seniority(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("lead", "manager", "principal", "director"):
        return "senior"
    if s in ("middle", "mid-level"):
        return "mid"
    if s in ("intern", "fresher", "junior", "mid", "senior"):
        return s
    return "mid"


def _intake_parts(data: dict) -> tuple[dict, dict]:
    """ID_Q interview_target + preferences from the create-session payload (unknown values dropped)."""
    seniority = _normalize_seniority(data.get("seniority"))
    interview_round = str(data.get("interview_round") or "").strip().lower()
    target = {
        "company_name": _text(data.get("target_company"), 300),
        "role_title": _text(data.get("target_role"), 300),
        "target_seniority": seniority,
    }
    try:
        minutes = int(data["duration_minutes"]) if data.get("duration_minutes") not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    preferences = {
        "interview_language": "en" if data.get("interview_language") == "en" else "vi",
        "interview_round": interview_round if interview_round in ROUNDS else None,
        "duration_minutes": minutes if minutes is not None and 10 <= minutes <= 120 else None,
        "additional_requirements": _text(data.get("additional_requirements"), 8000),
    }
    return {k: v for k, v in target.items() if v is not None}, preferences


def _form_profile(data: dict) -> dict:
    """ID_Q candidate_profile for the form path (no CV)."""
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    raw_skills = profile.get("skills", data.get("skills"))
    skills = raw_skills if isinstance(raw_skills, list) else str(raw_skills or "").split(",")
    years = profile.get("experience_years", data.get("experience_years"))
    try:
        years = float(years) if years not in (None, "") else None
    except (TypeError, ValueError):
        years = None
    goals = profile.get("career_goals") if isinstance(profile.get("career_goals"), list) else []
    result = {
        "candidate_name": _text(data.get("candidate_name"), 200),
        "summary": _text(profile.get("summary"), 6000),
        "current_role": _text(profile.get("current_role"), 300),
        "experience_years": years if years is not None and 0 <= years <= 80 else None,
        "skills": list(dict.fromkeys(s for s in (str(x).strip()[:120] for x in skills) if s))[:300],
        "career_goals": [g for g in (_text(x, 1000) for x in goals) if g][:100],
    }
    return {k: v for k, v in result.items() if v not in (None, [])}


def create_app(settings: Settings | None = None, core: CoreClient | None = None,
               http: httpx.AsyncClient | None = None, ws_connect=default_ws_connect,
               rag: RagClient | None = None, owners: Owners | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.auth_mode == "open" and not settings.auth_jwt_secret:
        settings.auth_jwt_secret = (secrets.token_urlsafe(48) if settings.database_path == ":memory:" else
                                    auth.load_or_create_secret(Path(settings.database_path).parent / "auth_jwt_secret"))
    core = core or CoreClient(settings)
    rag = rag or RagClient(settings)
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    owners = owners or Owners(settings.database_path)
    metrics = Metrics()
    login_limit = RateLimiter(settings.rate_login_per_min)
    # The sign-in form calls /check before /login: its own bucket keeps a class behind one NAT at full speed.
    check_limit = RateLimiter(settings.rate_login_per_min)
    create_limit = RateLimiter(settings.rate_create_per_min)
    ws_limit = RateLimiter(settings.rate_ws_per_min)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        job_sync_scheduler.start()
        yield
        job_sync_scheduler.stop()
        await core.aclose()
        await rag.aclose()
        await http.aclose()
        owners.close()

    app = FastAPI(title="Interview Gateway", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=split_csv(settings.cors_origins) or ["*"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def observe(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        metrics.request(request.method, getattr(route, "path", "unmatched"), response.status_code)
        logger.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code,
                    (time.perf_counter() - started) * 1000, request_id)
        return response

    @app.exception_handler(CoreError)
    async def core_error(_: Request, exc: CoreError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RagError)
    async def rag_error(_: Request, exc: RagError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "source": "rag"})

    @app.exception_handler(AuthError)
    async def auth_error(_: Request, exc: AuthError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    def sid(session_id: str) -> str:
        return quote(session_id, safe="")

    def client_ip(conn: HTTPConnection) -> str:
        if settings.trust_proxy_headers:
            # X-Real-IP is set by our own proxy chain (edge -> web nginx) and cannot be appended to by the
            # client; the first X-Forwarded-For entry can, so it is only a fallback.
            real = conn.headers.get("x-real-ip", "").strip()
            if real:
                return real
            first = conn.headers.get("x-forwarded-for", "").split(",")[0].strip()
            if first:
                return first
        return conn.client.host if conn.client else "unknown"

    def identify(conn: HTTPConnection, allow_query: bool = False) -> Principal | None:
        token = auth.bearer(conn.headers.get("authorization"))
        if token is None and allow_query:
            token = conn.query_params.get("token")
        return auth.verify(settings, token)

    def signed_in(request: Request) -> Principal | None:
        who = identify(request)
        if settings.auth_required and who is None:
            raise HTTPException(401, "Cần đăng nhập.", headers={"WWW-Authenticate": "Bearer"})
        return who

    def must_own(session_id: str, who: Principal | None) -> None:
        if settings.auth_required and (who is None or owners.owner(session_id) != who.student_id):
            raise HTTPException(404, "Session not found")

    def must_own_intake(id_q: str, who: Principal | None) -> None:
        if settings.auth_required and (who is None or owners.intake_owner(id_q) != who.student_id):
            raise HTTPException(404, "Intake not found")

    def too_many(limiter: RateLimiter, key: str, event: str) -> None:
        if not limiter.allow(key):
            metrics.event(event)
            raise HTTPException(429, "Bạn thao tác quá nhanh, thử lại sau ít phút.", headers={"Retry-After": "60"})

    def public_profile(row: dict | None) -> dict | None:
        return None if row is None else {key: row.get(key) for key in ("name", "email", "major", "school_year")}

    @app.post("/api/auth/check")
    async def check_student(body: CheckBody, request: Request):
        """Is this student id registered? The sign-in form asks for a name and profile only when it is not."""
        too_many(check_limit, client_ip(request), "login_rate_limited")
        student_id = auth.check_account(settings, body.student_id, body.access_code)
        row = owners.student(student_id)
        return {"student_id": student_id, "exists": row is not None, "profile": public_profile(row)}

    @app.post("/api/auth/login")
    async def login(body: LoginBody, request: Request):
        too_many(login_limit, client_ip(request), "login_rate_limited")
        try:
            student_id = auth.check_account(settings, body.student_id, body.access_code)
        except AuthError:
            metrics.event("login_failed")
            raise
        row = owners.student(student_id)
        if row is None and body.name.strip():
            row = owners.register_student(student_id, body.model_dump())
        name = (row or {}).get("name") or body.name.strip()
        issued = auth.issue_token(settings, student_id, name)
        if issued is None:
            return {"access_token": "", "token_type": "none", "expires_in": 0, "student_id": student_id,
                    "profile": public_profile(row)}
        token, ttl = issued
        metrics.event("login_ok")
        logger.info("login student=%s ip=%s", student_id, client_ip(request))
        return {"access_token": token, "token_type": "bearer", "expires_in": ttl, "student_id": student_id,
                "profile": public_profile(row)}

    @app.get("/api/auth/status")
    async def auth_status():
        return {"auth_required": settings.auth_required, "mode": settings.auth_mode,
                "access_code_required": settings.auth_mode == "student" and bool(settings.access_code)}

    @app.get("/api/auth/me")
    async def me(request: Request):
        who = identify(request)
        if who is None:
            raise HTTPException(401, "Cần đăng nhập.", headers={"WWW-Authenticate": "Bearer"})
        return {"student_id": who.student_id, "name": who.name}

    @app.get("/live")
    async def live():
        return {"status": "ok"}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint(authorization: str | None = Header(default=None)):
        token = auth.bearer(authorization)
        if not settings.metrics_token:
            raise HTTPException(404, "Not found")
        if token is None or not hmac.compare_digest(token.encode(), settings.metrics_token.encode()):
            raise HTTPException(401, "Sai token.")
        return metrics.render()

    # ---- health / config ----
    @app.get("/api/health")
    async def health():
        async def probe(url: str) -> dict:
            try:
                response = await http.get(url, timeout=5)
                return {"ok": response.status_code == 200, "status_code": response.status_code,
                        "body": response.json() if "json" in response.headers.get("content-type", "") else None}
            except httpx.HTTPError as exc:
                return {"ok": False, "error": type(exc).__name__}

        core_health, speech_health, rag_health = await asyncio.gather(
            probe(f"{settings.core_url.rstrip('/')}/health/ready"), probe(f"{settings.speech_url.rstrip('/')}/health"),
            probe(f"{settings.rag_url.rstrip('/')}/health"))
        return {"backend": "ok", "core": core_health, "speech": speech_health, "rag": rag_health}

    @app.get("/api/config")
    async def config(_: Principal | None = Depends(signed_in)):
        core_config = await core.request("GET", "/api/v1/inspect/config")
        return {"core": core_config, "tts_enabled": settings.tts_enabled, "stt_language": settings.stt_language}

    # ---- sessions ----
    @app.get("/api/sessions")
    async def list_sessions(q: str = Query("", max_length=160), limit: int = Query(50, ge=1, le=250),
                            offset: int = Query(0, ge=0), who: Principal | None = Depends(signed_in)):
        if not settings.auth_required:
            return await core.request("GET", "/api/v1/inspect/runs", params={"q": q, "limit": limit, "offset": offset})
        # Look up each of the student's own sessions instead of paging through every run of the core
        # (which missed sessions once the core held more runs than the page budget).
        mine = owners.sessions_of(who.student_id)
        gate = asyncio.Semaphore(LIST_CONCURRENCY)

        async def lookup(session_id: str) -> dict | None:
            async with gate:
                try:
                    data = await core.request("GET", "/api/v1/inspect/runs",
                                              params={"q": session_id, "limit": 5, "offset": 0})
                except CoreError as exc:
                    if exc.status_code == 404:
                        return None
                    raise
            return next((run for run in data.get("runs") or [] if run.get("session_id") == session_id), None)

        needle = q.strip().casefold()
        found = [run for run in await asyncio.gather(*(lookup(s) for s in sorted(mine)[:LIST_MAX_OWNED])) if run
                 and (not needle or needle in " ".join(str(run.get(k) or "") for k in
                                                       ("session_id", "candidate_name", "target_role")).casefold())]
        found.sort(key=lambda run: run.get("updated_at") or 0, reverse=True)
        return {"runs": found[offset:offset + limit], "total": len(found), "offset": offset, "limit": limit}

    @app.post("/api/sessions", status_code=202)
    async def create_session(request: Request,
                             payload: str = Form(..., description="Session payload as JSON"),
                             cv_file: UploadFile | None = File(default=None),
                             job_description_file: UploadFile | None = File(default=None),
                             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                             who: Principal | None = Depends(signed_in)):
        """CV (file or pasted text) or form -> ID_Q intake -> knowledge pack -> core session from that pack.

        `payload`: candidate_name, target_role, target_company, seniority, interview_round, duration_minutes,
        interview_language, job_description (text), cv_text, and for the form path `profile`
        (summary, current_role, experience_years, skills, career_goals). Without a CV the form is the source.
        `job_description_file` (PDF/DOCX/TXT) goes to core, which extracts it next to the typed JD.
        """
        throttle_key = who.student_id if who else (client_ip(request) if settings.trust_proxy_headers else None)
        if throttle_key:
            too_many(create_limit, throttle_key, "create_rate_limited")
        if who and settings.quota_sessions_per_day:
            if owners.created_since(who.student_id, time.time() - 86400) >= settings.quota_sessions_per_day:
                metrics.event("quota_exceeded")
                raise HTTPException(429, f"Mỗi ngày chỉ tạo tối đa {settings.quota_sessions_per_day} phiên. "
                                         "Hãy thử lại sau.", headers={"Retry-After": "3600"})
        if len(payload) > MAX_PAYLOAD_CHARS:
            raise HTTPException(413, "payload quá lớn.")
        try:
            data = json.loads(payload)
            assert isinstance(data, dict)
        except (ValueError, AssertionError):
            raise HTTPException(422, "payload phải là JSON object.")
        if who and idempotency_key:
            idempotency_key = f"{who.student_id}:{idempotency_key}"
        candidate_name = _text(data.get("candidate_name"), 200)
        job_description = _text(data.get("job_description"), MAX_JD_CHARS)
        target, preferences = _intake_parts(data)
        cv: tuple[str, bytes, str] | None = None
        if cv_file is not None and cv_file.filename:
            content = await cv_file.read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(413, f"CV tối đa {settings.max_upload_bytes // (1024 * 1024)} MB.")
            if content:
                check_upload(cv_file.filename, content)
                cv = (cv_file.filename, content, cv_file.content_type or "application/octet-stream")
        jd_file: tuple[str, bytes, str] | None = None
        if job_description_file is not None and job_description_file.filename:
            content = await job_description_file.read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(413, f"JD tối đa {settings.max_upload_bytes // (1024 * 1024)} MB.")
            if content:
                check_upload(job_description_file.filename, content)
                jd_file = (job_description_file.filename, content,
                           job_description_file.content_type or "application/octet-stream")
        if cv is None and _text(data.get("cv_text"), 180_000):
            cv = ("cv.txt", str(data["cv_text"]).strip().encode("utf-8"), "text/plain")

        # 1) ID_Q intake: the CV, or the form as the canonical profile. Same key -> same id_q.
        base_intake_key = f"{idempotency_key}:intake" if idempotency_key else None
        intake_key = base_intake_key
        if base_intake_key and (attempt := owners.intake_attempt(base_intake_key)):
            intake_key = f"{base_intake_key}:{attempt}"  # the intake of a failed earlier attempt was deleted
        if cv is not None:
            metadata = json.dumps({"interview_target": target, "preferences": preferences}, ensure_ascii=False)
            intake = await rag.create_cv_intake(*cv, metadata=metadata, idempotency_key=intake_key)
        else:
            profile = _form_profile(data)
            if not any(profile.get(key) for key in ("skills", "summary", "experience_years", "current_role")):
                raise HTTPException(422, "Hãy tải CV lên, dán nội dung CV, hoặc điền hồ sơ (kỹ năng, tóm tắt, kinh nghiệm).")
            intake = await rag.create_form_intake(
                {"candidate_profile": profile, "interview_target": target, "preferences": preferences},
                idempotency_key=intake_key)
        id_q = str(intake["id_q"])

        try:
            session, pack = await _session_from_intake(id_q, intake, data, candidate_name, job_description,
                                                       jd_file, idempotency_key, who)
        except (CoreError, RagError, HTTPException) as exc:
            # The intake holds the candidate's CV. Without a core session nobody owns it, so it could never
            # be deleted through the gateway: drop it now, unless an earlier attempt with this key did create a
            # session on it (its response may have been lost). The next retry then gets a fresh intake key.
            # 409 = the same request is still running elsewhere (double submit): its intake is in use.
            unowned = idempotency_key is None or (who is not None and owners.intake_owner(id_q) is None)
            if unowned and getattr(exc, "status_code", 500) != 409:
                try:
                    await rag.request("DELETE", f"/api/v1/intakes/{quote(id_q, safe='')}")
                    if base_intake_key:
                        owners.bump_intake_attempt(base_intake_key)
                except RagError as cleanup_error:
                    logger.warning("orphan_intake_cleanup_failed id_q=%s status=%s", id_q, cleanup_error.status_code)
            raise
        return {**session, "id_q": id_q, "pack_id": pack["pack_id"], "input_type": intake.get("input_type"),
                "limitations": pack.get("limitations") or []}

    async def _session_from_intake(id_q: str, intake: dict, data: dict, candidate_name: str | None,
                                   job_description: str | None, jd_file: tuple[str, bytes, str] | None,
                                   idempotency_key: str | None, who: Principal | None) -> tuple[dict, dict]:
        # 2) Knowledge pack for that revision (reused when it already exists for this revision).
        pack = await rag.prepare(id_q, int(intake.get("revision") or 1))

        # 3) Core session pinned to the pack; core fetches the pack from ID_Q by these ids.
        body: dict[str, Any] = {
            "id_q": id_q,
            "pack_id": pack["pack_id"],
            "metadata": {**(data.get("metadata") or {}), "client": "interview-frontend", "source": "human",
                         **({"owner": who.student_id} if who else {})},
        }
        if candidate_name:
            body["candidate_name"] = candidate_name
        if job_description:
            body["job_description"] = job_description
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        if jd_file is not None:
            session = await core.request("POST", "/api/v1/sessions/from-idq/upload", data={"payload": json.dumps(body)},
                                         files={"job_description_file": jd_file}, headers=headers)
        else:
            session = await core.request("POST", "/api/v1/sessions/from-idq", json=body, headers=headers)
        if who and session.get("session_id"):
            owners.add(str(session["session_id"]), who.student_id, id_q)
            logger.info("session_created student=%s session=%s", who.student_id, session["session_id"])
        return session, pack

    # ---- ID_Q intake (read / revise / re-prepare) ----
    @app.get("/api/intakes/{id_q}")
    async def get_intake(id_q: str, who: Principal | None = Depends(signed_in)):
        must_own_intake(id_q, who)
        return await rag.request("GET", f"/api/v1/intakes/{quote(id_q, safe='')}")

    @app.patch("/api/intakes/{id_q}")
    async def update_intake(id_q: str, body: dict[str, Any] = Body(...), who: Principal | None = Depends(signed_in)):
        must_own_intake(id_q, who)
        return await rag.request("PATCH", f"/api/v1/intakes/{quote(id_q, safe='')}", json=body)

    @app.post("/api/intakes/{id_q}/prepare", status_code=201)
    async def prepare_intake(id_q: str, body: dict[str, Any] = Body(...), who: Principal | None = Depends(signed_in)):
        must_own_intake(id_q, who)
        return await rag.request("POST", f"/api/v1/intakes/{quote(id_q, safe='')}/prepare", json=body)

    @app.get("/api/intakes/{id_q}/{collection}")
    async def intake_collection(id_q: str, collection: str, pack_id: str | None = None,
                                top_k: int = Query(10, ge=1, le=50), who: Principal | None = Depends(signed_in)):
        must_own_intake(id_q, who)
        if collection not in ("company_knowledge", "role_profile", "rubric", "question_bank"):
            raise HTTPException(404, "Không có collection này.")
        params: dict[str, Any] = {"top_k": top_k, **({"pack_id": pack_id} if pack_id else {})}
        return await rag.request("GET", f"/api/v1/intakes/{quote(id_q, safe='')}/{collection}", params=params)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.session_view(session_id)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        id_q = owners.id_q_of(session_id)
        try:
            result = await core.request("DELETE", f"/api/v1/sessions/{sid(session_id)}")
        except CoreError as exc:
            if exc.status_code != 404:
                raise
            result = {"session_id": session_id, "status": "DELETED"}
        intake_deleted = id_q is None
        if id_q:
            try:
                await rag.request("DELETE", f"/api/v1/intakes/{quote(id_q, safe='')}")
                intake_deleted = True
            except RagError as exc:
                intake_deleted = exc.status_code == 404
                if not intake_deleted:
                    logger.warning("intake_delete_failed session=%s id_q=%s status=%s", session_id, id_q, exc.status_code)
        if who:
            owners.retire(session_id)
            logger.info("session_deleted student=%s session=%s", who.student_id, session_id)
        return {**(result if isinstance(result, dict) else {}), "intake_deleted": intake_deleted}

    @app.patch("/api/sessions/{session_id}/profile")
    async def update_profile(session_id: str, body: dict[str, Any] = Body(...),
                          who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.request("PATCH", f"/api/v1/sessions/{sid(session_id)}/profile", json=body)

    @app.patch("/api/sessions/{session_id}/plan")
    async def update_plan(session_id: str, body: dict[str, Any] = Body(...),
                          who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.request("PATCH", f"/api/v1/sessions/{sid(session_id)}/plan", json=body)

    @app.post("/api/sessions/{session_id}/start")
    async def start(session_id: str, who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.request("POST", f"/api/v1/sessions/{sid(session_id)}/start",
                                  params={"confirm_limitations": "true"})

    @app.post("/api/sessions/{session_id}/end")
    async def end(session_id: str, who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.request("POST", f"/api/v1/sessions/{sid(session_id)}/end")

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str, who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.request("POST", f"/api/v1/sessions/{sid(session_id)}/cancel")

    @app.post("/api/cv/analyze")
    async def analyze_cv(file: UploadFile = File(...), who: Principal | None = Depends(signed_in)):
        """Read a CV before any session exists: which fields the text already gives and which are missing.

        Nothing is stored. A CV without a text layer (a scan or photo) comes back `readable: false`, so the
        browser asks the candidate for those fields instead of failing later at session creation."""
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, f"CV tối đa {settings.max_upload_bytes // (1024 * 1024)} MB.")
        filename = file.filename or "cv"
        check_upload(filename, content)
        try:
            _, text = await asyncio.to_thread(extract_document_text, filename, content)
        except DocumentExtractionError as exc:
            if "No readable text" in str(exc):
                return {"readable": False, "reason": "needs_ocr", "characters": 0, "fields": {}, "sections": {},
                        "warnings": ["needs_ocr"]}
            raise HTTPException(422, f"{filename}: {exc}")
        return analyze_cv_text(text)

    @app.post("/api/sessions/{session_id}/documents")
    async def upload_document(session_id: str, file: UploadFile = File(...),
                              who: Principal | None = Depends(signed_in)):
        """A document the candidate attaches to an answer: its text is returned to the browser, which sends it
        as `answer_context` with the next answer. Nothing is stored here."""
        must_own(session_id, who)
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, f"Tài liệu tối đa {settings.max_upload_bytes // (1024 * 1024)} MB.")
        filename = file.filename or "document"
        check_upload(filename, content)
        try:
            _, text = await asyncio.to_thread(extract_document_text, filename, content)
        except DocumentExtractionError as exc:
            raise HTTPException(422, f"{filename}: {exc}")
        return {"filename": filename, "extracted_text": text[:MAX_DOCUMENT_CHARS],
                "extracted_characters": len(text), "truncated": len(text) > MAX_DOCUMENT_CHARS}

    @app.get("/api/sessions/{session_id}/report")
    async def report(session_id: str, who: Principal | None = Depends(signed_in)):
        must_own(session_id, who)
        return await core.request("GET", f"/api/v1/sessions/{sid(session_id)}/report")

    @app.get("/api/tts/voices")
    async def voices(_: Principal | None = Depends(signed_in)):
        headers = {"Authorization": f"Bearer {settings.speech_service_token}"} if settings.speech_service_token else {}
        try:
            response = await http.get(f"{settings.speech_url.rstrip('/')}/v1/tts/voices", headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Không gọi được speech service: {type(exc).__name__}")
        return JSONResponse(status_code=response.status_code, content=response.json())

    # ---- IT Jobs Sync Service ----
    app.include_router(job_sync_router)

    # ---- voice interview ----
    def ws_refusal(websocket: WebSocket, session_id: str) -> tuple[int, str] | None:
        if not auth.origin_allowed(websocket.headers.get("origin"), split_csv(settings.cors_origins)):
            return 4403, "Origin không được phép"
        if not ws_limit.allow(client_ip(websocket)):
            metrics.event("ws_rate_limited")
            return 4429, "Kết nối quá nhanh"
        if settings.auth_required:
            who = identify(websocket, allow_query=True)
            if who is None:
                return 4401, "Cần đăng nhập"
            if owners.owner(session_id) != who.student_id:
                return 4404, "Không tìm thấy phiên"
        return None

    @app.websocket("/ws/sessions/{session_id}")
    async def interview(websocket: WebSocket, session_id: str, after: int = Query(0, ge=0)):
        refusal = ws_refusal(websocket, session_id)
        if refusal:
            await websocket.accept()
            await websocket.close(code=refusal[0], reason=refusal[1])
            return
        metrics.ws_active += 1
        metrics.event("ws_opened")
        try:
            await Bridge(websocket, session_id, settings, core, http, ws_connect).run(after)
        finally:
            metrics.ws_active -= 1

    return app


app = create_app()
