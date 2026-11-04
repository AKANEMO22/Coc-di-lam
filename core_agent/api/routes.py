"""FastAPI REST routes under /api/v1/sessions for session lifecycle management."""

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from typing import Any, Dict, Optional, Sequence, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from api.auth import create_session_token, require_service_token, require_session_or_service_token
from api.idq_client import IDQServiceError, fetch_idq_bundle, validate_idq_bundle
from api.schemas import (
    CancelSessionResponse,
    CreateSessionFromIDQRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionResponse,
    PlanPreferenceRequest,
    ProfileCorrectionRequest,
    RequeueAssessmentsResponse,
    ReviewRequest,
    SessionReportResponse,
    SessionStatusResponse,
    StartInterviewResponse,
)
from config.settings import get_settings
from core.assessor.queue import session_assessment_keys
from core.observability import session_ref
from core.profiler.agent import ProfilerAgent
from core.profiler.idq import selected_jd as idq_selected_jd
from core.profiler.documents import DocumentExtractionError, MAX_DOCUMENT_BYTES, extract_document_text
from core.runtime.agent import sanitize_secrets
from core.runtime.types import LoopEvent, LoopEventType
from core.session.idempotency import IdempotencyConflictError, IdempotencyInFlightError
from core.session.state import (
    InvalidStateTransitionError,
    LifecycleStatus,
    SessionAlreadyExistsError,
    SessionState,
    TERMINAL_STATUSES,
)
from core.workspace.session import SessionWorkspace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
MAX_UPLOAD_BYTES = MAX_DOCUMENT_BYTES
# Bounded wait for cancelled local tasks inside the /cancel request.
CANCEL_UNWIND_SECONDS = 2.0

SAFE_FAILURE_MESSAGES = {
    "PROFILING_FAILED": "Interview preparation failed. Please create a new session.",
    "PROFILING_TIMEOUT": "Interview preparation timed out. Please create a new session.",
    "ASSESSMENT_EXHAUSTED": "Assessment could not be completed. An operator can retry it.",
}


def _event(session_id: str, event_type: LoopEventType, payload: Dict[str, Any], turn_id: str = "lifecycle") -> LoopEvent:
    return LoopEvent(
        schema_version="1.0",
        event_id=f"evt_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        turn_id=turn_id,
        sequence=0,
        timestamp=time.time(),
        type=event_type,
        payload=payload,
    )


async def _fail_session(app: Any, session_id: str, failure_code: str) -> None:
    """Move a session to FAILED with a safe client message and publish session_failed."""
    state_store = app.state.state_mgr
    state = await state_store.aget(session_id)
    if state is None or state.status in TERMINAL_STATUSES:
        return
    correlation_id = uuid.uuid4().hex[:16]
    try:
        await state_store.atransition(
            state,
            expected_version=state.state_version,
            new_status=LifecycleStatus.FAILED,
            metadata={
                "failure_code": failure_code,
                "failure_message": SAFE_FAILURE_MESSAGES.get(failure_code, "Session failed."),
                "correlation_id": correlation_id,
            },
        )
    except InvalidStateTransitionError:
        return
    await app.state.connection_mgr.publish(
        session_id,
        _event(session_id, LoopEventType.SESSION_FAILED, {"failure_code": failure_code, "correlation_id": correlation_id}),
    )
    logger.error(
        "Session failed",
        extra={"session": session_ref(session_id), "failure_code": failure_code, "correlation_id": correlation_id},
    )


async def _run_profiling_task(app: Any, session_id: str, workspace: SessionWorkspace) -> None:
    try:
        await app.state.cancellations.run(session_id, _run_profiling(app, session_id, workspace), app.state.redis_client)
    except asyncio.CancelledError:
        # Session cancellation stops only the child task; cancelling this task is shutdown.
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        # A fenced/cancelled profiling run must not leave the session in PROFILING; an explicit
        # /cancel has already moved it to CANCELLED, which _fail_session leaves untouched.
        state = await app.state.state_mgr.aget(session_id)
        if state is not None and state.status in (LifecycleStatus.INITIALIZED, LifecycleStatus.PROFILING):
            await _fail_session(app, session_id, "PROFILING_FAILED")
        return


async def _run_profiling(app: Any, session_id: str, workspace: SessionWorkspace) -> None:
    """Run pre-flight profiling under a deadline; READY on success, FAILED otherwise."""
    profiler: Optional[ProfilerAgent] = getattr(app.state, "profiler_agent", None)
    settings = app.state.settings
    state_store = app.state.state_mgr
    # Sessions exist only for prepared ID_Q packs; Core never profiles from raw inputs or its own RAG.
    if profiler is None or not await asyncio.to_thread(workspace.exists, "inputs/idq_bundle.json"):
        await _fail_session(app, session_id, "PROFILING_FAILED")
        return

    async def report_progress(step: str) -> None:
        # Stored in state metadata so the gateway's status poll can show the current step.
        current = await state_store.aget(session_id)
        if current is None or current.status != LifecycleStatus.PROFILING:
            return
        try:
            await state_store.atransition(
                current,
                expected_version=current.state_version,
                metadata={"profiling_step": step, "profiling_step_at": time.time()},
            )
        except InvalidStateTransitionError:
            pass

    semaphore = getattr(app.state, "profiler_semaphore", None)

    async def profile() -> Dict[str, Any]:
        if semaphore is None:
            return await profiler.run_profiling(workspace, on_progress=report_progress)
        await report_progress("queued")
        async with semaphore:
            return await profiler.run_profiling(workspace, on_progress=report_progress)

    try:
        # One deadline covers the semaphore wait too, so a queued session cannot outlive it.
        result = await asyncio.wait_for(profile(), timeout=settings.PROFILING_DEADLINE_SECONDS)
    except asyncio.TimeoutError:
        await _fail_session(app, session_id, "PROFILING_TIMEOUT")
        return
    except Exception as e:
        logger.error("Profiling crashed for session %s: %s", session_ref(session_id), sanitize_secrets(str(e)))
        await _fail_session(app, session_id, "PROFILING_FAILED")
        return

    if result.get("status") != "SUCCESS":
        await _fail_session(app, session_id, "PROFILING_FAILED")
        return

    ready_metadata: Dict[str, Any] = {"retrieval_status": result.get("retrieval_status", "disabled")}
    for hook in getattr(app.state, "post_profiling_hooks", []):
        try:
            extra = await hook(session_id, workspace)
            if isinstance(extra, dict):
                ready_metadata.update(extra)
        except Exception as e:
            logger.warning("Post-profiling hook failed: %s", sanitize_secrets(str(e)))

    current = await state_store.aget(session_id)
    if current is not None and current.status == LifecycleStatus.PROFILING:
        try:
            await state_store.atransition(
                current,
                expected_version=current.state_version,
                new_status=LifecycleStatus.READY,
                metadata=ready_metadata,
            )
            logger.info("Session ready", extra={"session": session_ref(session_id)})
        except InvalidStateTransitionError:
            pass


async def _validate_session_access(request: Request, claims: Dict[str, Any], session_id: str) -> None:
    """Allow service tokens for any session; session tokens only for their own, non-revoked session."""
    if claims.get("aud") == "ai-runtime":
        return
    token_session = claims.get("session_id") or claims.get("sub")
    if token_session != session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is not authorized for this session",
        )
    revocations = getattr(request.app.state, "revocations", None)
    if revocations is not None and await revocations.is_revoked(session_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session token revoked")


async def _require_state(request: Request, session_id: str) -> SessionState:
    state = await request.app.state.state_mgr.aget(session_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return state


async def _create_session(
    request: Request,
    request_body: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str],
    claims: Dict[str, Any],
    *,
    idempotency_payload: Dict[str, Any],
    pinned_artifacts: Sequence[Tuple[str, str, Dict[str, Any]]] = (),
    documents: Sequence[Tuple[str, str, bytes, str, str]] = (),
) -> JSONResponse:
    """Initialize a session from an ID_Q pack and schedule pre-flight profiling.

    Every input (form view of the pack, pinned pack, original JD document) is written before
    profiling is scheduled, so the profiler never sees a half-created workspace.

    - Repeating an Idempotency-Key with the same payload returns the original response (202).
    - Reusing it with a different payload returns 409; a concurrent duplicate returns 409 too.
    - Exceeding the per-caller creation rate returns 429.
    """
    app = request.app
    settings = getattr(app.state, "settings", get_settings())
    state_store = app.state.state_mgr
    idempotency = app.state.idempotency_mgr

    limiter = getattr(app.state, "session_create_limiter", None)
    caller = str(claims.get("iss") or "service")
    if limiter is not None and not limiter.acquire(caller):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Session creation rate limit exceeded",
            headers={"Retry-After": "1"},
        )

    if idempotency_key:
        try:
            claimed, cached = await idempotency.abegin(f"create_session:{idempotency_key}", payload=idempotency_payload)
        except IdempotencyConflictError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
        except IdempotencyInFlightError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
        if not claimed:
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=cached)

    try:
        session_id = request_body.session_id or f"sess_{uuid.uuid4().hex[:12]}"
        # A deleted id stays revoked (its old tokens must keep failing); reusing it would start a
        # session whose work is fenced off immediately.
        revocations = getattr(app.state, "revocations", None)
        if revocations is not None and await revocations.is_revoked(session_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session id was deleted and cannot be reused")
        try:
            state = await state_store.acreate(
                session_id=session_id,
                initial_status=LifecycleStatus.INITIALIZED,
                metadata={
                    **request_body.metadata,
                    "interview_language": request_body.interview_language,
                    "seniority": request_body.seniority,
                    "interview_round": request_body.interview_round,
                    "duration_minutes": request_body.duration_minutes,
                },
            )
        except SessionAlreadyExistsError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already exists") from e
        cancellations = getattr(app.state, "cancellations", None)
        if cancellations is not None and not cancellations.has_live_tasks(session_id):
            # Drop a stale in-process refusal marker left by an earlier deletion of this id.
            cancellations.forget(session_id)

        workspace = app.state.workspace_mgr.get_or_create_workspace(session_id)
        workspace.initialize()
        form_data: Dict[str, Any] = {
            "candidate_name": request_body.candidate_name,
            "experience_years": request_body.experience_years,
            "target_company": request_body.target_company,
            "target_role": request_body.target_role,
            "skills": request_body.skills,
            "job_description": request_body.job_description,
            "interview_format": request_body.interview_format,
            "interview_language": request_body.interview_language,
            "seniority": request_body.seniority,
            "interview_round": request_body.interview_round,
            "duration_minutes": request_body.duration_minutes,
        }
        document_records = []
        for kind, extension, content, extracted, digest in documents:
            relative_path = f"inputs/documents/{kind}_original.{extension}"
            await asyncio.to_thread(workspace.write_bytes, relative_path, content)
            document_records.append({
                "kind": kind,
                "path": relative_path,
                "sha256": digest,
                "size_bytes": len(content),
                "extracted_characters": len(extracted),
                "extraction": "pypdf" if extension == "pdf" else "docx_xml" if extension == "docx" else "utf8",
            })
        if document_records:
            form_data["documents"] = document_records
        await asyncio.to_thread(
            workspace.write_artifact, "inputs/form_input.json", artifact_type="input", payload=form_data
        )
        for path, artifact_type, payload in pinned_artifacts:
            await asyncio.to_thread(workspace.write_artifact, path, artifact_type=artifact_type, payload=payload)

        archive = getattr(app.state, "artifact_archive", None)
        if archive is not None:
            try:
                await asyncio.to_thread(
                    archive.archive_session,
                    session_id,
                    workspace.root_path,
                    owner_id=session_id,
                )
            except Exception as archive_error:
                logger.warning(
                    "Initial artifact archive pending for session %s: %s",
                    session_ref(session_id),
                    sanitize_secrets(str(archive_error)),
                )

        state = await state_store.atransition(
            state, expected_version=state.state_version, new_status=LifecycleStatus.PROFILING
        )
        session_token = create_session_token(session_id=session_id, secret=settings.JWT_SECRET)
        background_tasks.add_task(_run_profiling_task, app=app, session_id=session_id, workspace=workspace)

        response_data = CreateSessionResponse(
            session_id=session_id,
            status=LifecycleStatus.PROFILING,
            state_version=state.state_version,
            session_token=session_token,
            websocket_stream_url=f"/ws/sessions/{session_id}/stream",
            created_at=state.created_at,
        ).model_dump(mode="json")
    except Exception:
        if idempotency_key:
            await idempotency.arelease(f"create_session:{idempotency_key}")
        raise

    if idempotency_key:
        await idempotency.arecord(f"create_session:{idempotency_key}", result=response_data, payload=idempotency_payload)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=response_data)


async def _create_from_idq(
    request: Request,
    request_body: CreateSessionFromIDQRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str],
    claims: Dict[str, Any],
    documents: Sequence[Tuple[str, str, bytes, str, str]] = (),
) -> JSONResponse:
    """Fetch the private ID_Q snapshot from RAG, pin it in the workspace, then run profiling on it.

    Core does no retrieval: profile, rubric, question bank and JD all come from the pinned pack,
    so a later change on the RAG side never alters a running interview.
    """
    settings = getattr(request.app.state, "settings", get_settings())
    fetch = getattr(request.app.state, "idq_fetch", None) or fetch_idq_bundle
    try:
        bundle = await asyncio.to_thread(fetch, settings, request_body.id_q, request_body.pack_id)
    except IDQServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if fetch is not fetch_idq_bundle:
        # Injected fetchers (tests, simulations) get the same contract checks as the HTTP client.
        if not isinstance(bundle, dict) or not isinstance(bundle.get("candidate_profile"), dict) or not isinstance(bundle.get("knowledge_pack"), dict):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ID_Q service returned an incomplete knowledge pack")
        try:
            bundle = validate_idq_bundle(bundle)
        except IDQServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    profile = bundle["candidate_profile"]
    target = bundle.get("interview_target") or {}
    preferences = bundle.get("preferences") or {}
    pack = bundle.get("knowledge_pack") or {}
    company_result = pack.get("company_knowledge") or {}
    # Same rule as the profiler projection: only the JD the candidate actually selected.
    selected_jd = idq_selected_jd(bundle)
    company_record = company_result.get("company") if isinstance(company_result.get("company"), dict) else {}
    role_record = (pack.get("role_profile", {}).get("items") or [{}])[0]
    profile_skills = profile.get("skills") if isinstance(profile.get("skills"), list) else []
    skills = [
        str(item.get("name") or "").strip() if isinstance(item, dict) else str(item).strip()
        for item in profile_skills
    ]
    job_description = "\n".join(
        part for part in [
            str(selected_jd.get("title") or ""),
            str(selected_jd.get("description") or ""),
            "\n".join(str(item) for item in (selected_jd.get("requirements") or []) if isinstance(item, str)),
            str(request_body.job_description or ""),
        ] if part.strip()
    )[:180_000]
    intake_revision = int(bundle.get("intake_revision") or 0)
    pack_id = str(bundle.get("pack_id") or "")
    link = {
        "id_q": request_body.id_q,
        "intake_revision": intake_revision,
        "pack_id": pack_id,
        "knowledge_version": pack.get("knowledge_version"),
        "input_type": bundle.get("input_type"),
    }
    core_request = CreateSessionRequest(
        candidate_name=str(request_body.candidate_name or profile.get("candidate_name") or "").strip() or "Candidate",
        experience_years=profile.get("experience_years"),
        target_company=str(target.get("company_name") or company_record.get("canonical_name") or "Target Company"),
        target_role=str(target.get("role_title") or role_record.get("role_title") or "") or None,
        skills=[item for item in skills if item],
        session_id=request_body.session_id,
        job_description=job_description or None,
        interview_format=preferences.get("additional_requirements"),
        seniority=target.get("target_seniority"),
        interview_round=preferences.get("interview_round"),
        duration_minutes=preferences.get("duration_minutes"),
        interview_language=preferences.get("interview_language") or "vi",
        metadata={
            **request_body.metadata,
            "id_q": request_body.id_q,
            "idq_intake_revision": intake_revision,
            "idq_pack_id": pack_id,
            "idq_knowledge_version": pack.get("knowledge_version"),
            "idq_input_type": bundle.get("input_type"),
            **({"upload_sha256": {kind: digest for kind, _ext, _c, _t, digest in documents}} if documents else {}),
        },
    )
    return await _create_session(
        request,
        core_request,
        background_tasks,
        idempotency_key,
        claims,
        idempotency_payload={
            "request": request_body.model_dump(mode="json"),
            "documents": {kind: digest for kind, _ext, _c, _t, digest in documents},
        },
        pinned_artifacts=(
            ("inputs/idq_bundle.json", "idq_bundle", bundle),
            ("context/idq_link.json", "idq_link", link),
        ),
        documents=documents,
    )


@router.post(
    "/from-idq",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an interview session from a prepared ID_Q knowledge pack",
)
async def create_session_from_idq(
    request: Request,
    request_body: CreateSessionFromIDQRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    claims: Dict[str, Any] = Depends(require_service_token),
) -> Any:
    """The only way to create a session: from an ID_Q pack prepared by the RAG service."""
    return await _create_from_idq(request, request_body, background_tasks, idempotency_key, claims)


@router.post(
    "/from-idq/upload",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an interview session from an ID_Q pack plus a job-description document",
)
async def create_session_from_idq_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: str = Form(..., description="CreateSessionFromIDQRequest encoded as JSON"),
    job_description_file: Optional[UploadFile] = File(default=None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    claims: Dict[str, Any] = Depends(require_service_token),
) -> Any:
    """Same as /from-idq; the JD document (PDF/DOCX/TXT) text is added after any typed JD text."""
    try:
        request_body = CreateSessionFromIDQRequest.model_validate_json(payload)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid session payload") from exc

    documents: list[Tuple[str, str, bytes, str, str]] = []
    if job_description_file is not None:
        content = await job_description_file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Each document is limited to 12 MB")
        try:
            extension, extracted = extract_document_text(job_description_file.filename or "", content)
        except DocumentExtractionError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        documents.append(("job_description", extension, content, extracted, hashlib.sha256(content).hexdigest()))
        job_description = "\n\n".join(part for part in (request_body.job_description, extracted) if part)
        if len(job_description) > 180_000:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Extracted text exceeds the 180,000 character limit")
        request_body = request_body.model_copy(update={"job_description": job_description or None})
    return await _create_from_idq(request, request_body, background_tasks, idempotency_key, claims, documents)


def _artifact_payload(workspace: SessionWorkspace, path: str) -> Dict[str, Any]:
    data = workspace.read_json(path)
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        return data["payload"]
    return data if isinstance(data, dict) else {}


async def _ensure_still_editable(request: Request, session_id: str, detail: str) -> None:
    """Re-check READY after the (slow) plan rebuild, right before the rebuilt artifacts are written."""
    latest = await _require_state(request, session_id)
    if latest.status != LifecycleStatus.READY or latest.current_turn != 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _evict_runner(request: Request, session_id: str) -> None:
    """Drop a runner cached from the previous plan so /start builds from the rebuilt artifacts."""
    controller = getattr(request.app.state, "controller", None)
    if controller is not None:
        controller.runners.evict(session_id)


@router.patch(
    "/{session_id}/plan",
    summary="Edit the inferred interview brief before the first interview turn",
)
async def update_interview_plan(
    session_id: str,
    body: PlanPreferenceRequest,
    request: Request,
    claims: Dict[str, Any] = Depends(require_session_or_service_token),
) -> Dict[str, Any]:
    await _validate_session_access(request, claims, session_id)
    state = await _require_state(request, session_id)
    if state.status != LifecycleStatus.READY:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Interview plan can only be edited before the interview starts")

    workspace = request.app.state.workspace_mgr.get_or_create_workspace(session_id)
    if not workspace.root_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session workspace not found")
    try:
        form_data = await asyncio.to_thread(_artifact_payload, workspace, "inputs/form_input.json")
        candidate_profile = await asyncio.to_thread(_artifact_payload, workspace, "context/candidate_profile.json")
    except (OSError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Interview plan artifacts are not ready")

    if "seniority" in body.model_fields_set and workspace.exists("inputs/idq_bundle.json"):
        idq_bundle = await asyncio.to_thread(_artifact_payload, workspace, "inputs/idq_bundle.json")
        pinned_level = (idq_bundle.get("interview_target") or {}).get("target_seniority")
        if body.seniority != pinned_level:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ID_Q seniority is pinned to its knowledge pack; revise and prepare a new ID_Q revision to change it",
            )

    for field_name in body.model_fields_set:
        form_data[field_name] = getattr(body, field_name)
    profiler = getattr(request.app.state, "profiler_agent", None)
    if profiler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Interview planner is unavailable")
    try:
        rebuilt = await asyncio.wait_for(
            profiler.rebuild_reviewed_plan(workspace, form_data, candidate_profile),
            timeout=request.app.state.settings.PROFILING_DEADLINE_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Plan refresh timed out; previous plan is unchanged") from exc
    except Exception as exc:
        logger.warning("Reviewed plan refresh failed: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to refresh the plan; previous plan is unchanged") from exc
    knowledge = rebuilt["knowledge"]
    brief = rebuilt["brief"]
    shaped_plan = rebuilt["stage_plan"]
    await _ensure_still_editable(request, session_id, "Interview started while the plan was refreshing; plan unchanged")
    await asyncio.to_thread(workspace.write_artifact, "inputs/form_input.json", artifact_type="input", payload=form_data)
    await asyncio.to_thread(workspace.write_artifact, "context/retrieved_knowledge.json", artifact_type="retrieved_knowledge", payload=knowledge)
    if knowledge.get("status") == "ok" and isinstance(knowledge.get("rubric"), dict):
        await asyncio.to_thread(workspace.write_artifact, "context/rubric.json", artifact_type="rubric", payload=knowledge["rubric"])
    else:
        await asyncio.to_thread(workspace.delete_artifact, "context/rubric.json")
    await asyncio.to_thread(workspace.write_artifact, "context/company_recon.json", artifact_type="company_recon", payload=rebuilt["company_recon"])
    await asyncio.to_thread(workspace.write_artifact, "context/interview_brief.json", artifact_type="interview_brief", payload=brief)
    await asyncio.to_thread(workspace.write_artifact, "context/stage_plan.json", artifact_type="stage_plan", payload=shaped_plan)
    await asyncio.to_thread(workspace.write_artifact, "context/interview_spec.json", artifact_type="interview_spec", payload=rebuilt["interview_spec"])
    _evict_runner(request, session_id)

    archive = getattr(request.app.state, "artifact_archive", None)
    if archive is not None:
        try:
            await asyncio.to_thread(archive.archive_session, session_id, workspace.root_path, owner_id=session_id)
        except Exception as archive_error:
            logger.warning(
                "Updated interview plan archive pending for session %s: %s",
                session_ref(session_id),
                sanitize_secrets(str(archive_error)),
            )
    return {"session_id": session_id, "brief": brief, "stage_plan": shaped_plan}


@router.patch(
    "/{session_id}/profile",
    summary="Save candidate corrections to the extracted CV profile before the interview",
)
async def update_candidate_profile(
    session_id: str,
    body: ProfileCorrectionRequest,
    request: Request,
    claims: Dict[str, Any] = Depends(require_session_or_service_token),
) -> Dict[str, Any]:
    await _validate_session_access(request, claims, session_id)
    state = await _require_state(request, session_id)
    if state.status != LifecycleStatus.READY or state.current_turn != 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The extracted profile can only be edited before the interview starts")

    allowed_fields = {
        "candidate_name", "summary", "career_goals", "experience", "projects", "skills",
        "education", "awards", "certifications", "strengths", "gaps", "probes", "claims",
        "experience_years", "missing_or_conflicting_fields",
        # ID_Q canonical profile fields (sessions created from /from-idq)
        "current_role", "current_seniority", "additional_sections",
    }
    profile = dict(body.candidate_profile)
    if set(profile) - allowed_fields:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Candidate profile contains unsupported fields")
    if not isinstance(profile.get("candidate_name"), str) or not profile["candidate_name"].strip() or len(profile["candidate_name"].strip()) > 120:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Candidate name is required")
    list_fields = allowed_fields - {"candidate_name", "summary", "experience_years", "current_role", "current_seniority"}
    if any(key in profile and not isinstance(profile[key], list) for key in list_fields):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Profile sections must remain JSON arrays")
    if profile.get("experience_years") is not None:
        raw_years = profile["experience_years"]
        try:
            years = float(raw_years)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Experience years must be a number") from exc
        if isinstance(raw_years, bool) or not math.isfinite(years) or not 0 <= years <= 80:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Experience years must be between 0 and 80")
        profile["experience_years"] = years
    try:
        profile_json = json.dumps(profile, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Profile must contain finite JSON values") from exc
    if len(profile_json) > 180_000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Reviewed profile exceeds the supported size")

    workspace = request.app.state.workspace_mgr.get_or_create_workspace(session_id)
    try:
        form_data = await asyncio.to_thread(_artifact_payload, workspace, "inputs/form_input.json")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Profile artifacts are not ready") from exc

    form_data["candidate_name"] = str(profile["candidate_name"]).strip()
    if profile.get("experience_years") is not None:
        form_data["experience_years"] = profile["experience_years"]
    profile_skills = profile.get("skills") or []
    reviewed_skill_names = [
        str(item.get("name") or "").strip() if isinstance(item, dict) else str(item).strip()
        for item in profile_skills
    ]
    existing_skills = form_data.get("skills") if isinstance(form_data.get("skills"), list) else []
    form_data["skills"] = list(dict.fromkeys(
        [str(item).strip() for item in existing_skills if str(item).strip()]
        + [item for item in reviewed_skill_names if item]
    ))
    profiler = getattr(request.app.state, "profiler_agent", None)
    if profiler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Interview planner is unavailable")
    try:
        rebuilt = await asyncio.wait_for(
            profiler.rebuild_reviewed_plan(workspace, form_data, profile),
            timeout=request.app.state.settings.PROFILING_DEADLINE_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Plan refresh timed out; previous profile is unchanged") from exc
    except Exception as exc:
        logger.warning("Reviewed profile refresh failed: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to refresh the plan; previous profile is unchanged") from exc
    knowledge = rebuilt["knowledge"]
    shaped_plan = rebuilt["stage_plan"]
    await _ensure_still_editable(request, session_id, "Interview started while the plan was refreshing; profile unchanged")
    await asyncio.to_thread(workspace.write_artifact, "context/candidate_profile.json", artifact_type="candidate_profile", payload=profile)
    await asyncio.to_thread(workspace.write_artifact, "context/profile_review.json", artifact_type="profile_review", payload={"reviewed_by": "candidate", "reviewed_at": time.time(), "source": "candidate_correction"})
    await asyncio.to_thread(workspace.write_artifact, "inputs/form_input.json", artifact_type="input", payload=form_data)
    await asyncio.to_thread(workspace.write_artifact, "context/retrieved_knowledge.json", artifact_type="retrieved_knowledge", payload=knowledge)
    if knowledge.get("status") == "ok" and isinstance(knowledge.get("rubric"), dict):
        await asyncio.to_thread(workspace.write_artifact, "context/rubric.json", artifact_type="rubric", payload=knowledge["rubric"])
    else:
        await asyncio.to_thread(workspace.delete_artifact, "context/rubric.json")
    await asyncio.to_thread(workspace.write_artifact, "context/company_recon.json", artifact_type="company_recon", payload=rebuilt["company_recon"])
    await asyncio.to_thread(workspace.write_artifact, "context/interview_brief.json", artifact_type="interview_brief", payload=rebuilt["brief"])
    await asyncio.to_thread(workspace.write_artifact, "context/interview_spec.json", artifact_type="interview_spec", payload=rebuilt["interview_spec"])
    await asyncio.to_thread(workspace.write_artifact, "context/stage_plan.json", artifact_type="stage_plan", payload=shaped_plan)
    _evict_runner(request, session_id)

    archive = getattr(request.app.state, "artifact_archive", None)
    if archive is not None:
        try:
            await asyncio.to_thread(archive.archive_session, session_id, workspace.root_path, owner_id=session_id)
        except Exception as archive_error:
            logger.warning(
                "Reviewed profile archive pending for session %s: %s",
                session_ref(session_id),
                sanitize_secrets(str(archive_error)),
            )
    return {"session_id": session_id, "candidate_profile": profile, "brief": rebuilt["brief"], "stage_plan": shaped_plan}


@router.get(
    "/{session_id}",
    response_model=SessionStatusResponse,
    summary="Retrieve current session state, stage info and counters",
)
async def get_session_status(
    session_id: str,
    request: Request,
    claims: Dict[str, Any] = Depends(require_session_or_service_token),
) -> SessionStatusResponse:
    """Lifecycle status, version, stage, turn counter, safe failure details and readiness flags."""
    await _validate_session_access(request, claims, session_id)
    state = await _require_state(request, session_id)
    metadata = dict(state.metadata)
    coverage = None
    blackboard = getattr(request.app.state, "blackboard", None)
    if blackboard is not None:
        full = await blackboard.get(session_id, "coverage")
        if full:
            coverage = {
                "uncovered": full.get("uncovered", []),
                "must_have_uncovered": full.get("must_have_uncovered", []),
                "criteria": {
                    cid: {"level": c.get("level"), "confidence": c.get("confidence"), "covered": c.get("covered")}
                    for cid, c in (full.get("criteria") or {}).items()
                },
            }
    return SessionStatusResponse(
        session_id=state.session_id,
        status=state.status,
        state_version=state.state_version,
        current_stage_id=state.current_stage_id,
        is_stage_complete=state.is_stage_complete,
        current_turn=state.current_turn,
        metadata={k: v for k, v in metadata.items() if k not in ("failure_message", "correlation_id")},
        failure_code=metadata.get("failure_code"),
        failure_message=metadata.get("failure_message"),
        retrieval_status=metadata.get("retrieval_status"),
        brief_status=metadata.get("brief_status"),
        coverage=coverage,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


@router.post(
    "/{session_id}/start",
    response_model=StartInterviewResponse,
    summary="Start the interview and generate the opening question",
)
async def start_interview(
    session_id: str,
    request: Request,
    confirm_limitations: bool = False,
    claims: Dict[str, Any] = Depends(require_session_or_service_token),
) -> Any:
    """Backend-driven start: READY → STAGE_ACTIVE plus the opening question (turn 0).

    Events are also published to the session stream. Idempotent: repeated calls return the
    recorded opening.
    """
    await _validate_session_access(request, claims, session_id)
    await _require_state(request, session_id)
    workspace = request.app.state.workspace_mgr.get_or_create_workspace(session_id)
    result = await request.app.state.controller.start_interview(session_id, confirm_limitations=confirm_limitations)

    if result.status == "busy":
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=StartInterviewResponse(session_id=session_id, status="busy", error_code=result.error_code).model_dump(),
            headers={"Retry-After": str(max(1, (result.retry_after_ms or 1000) // 1000))},
        )
    if result.status == "rejected":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.error_code)
    if result.status == "failed":
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Opening generation failed; retry")

    text = None
    if workspace.exists("turns/turn_0_interviewer.json"):
        text = workspace.read_json("turns/turn_0_interviewer.json").get("content")
    return StartInterviewResponse(
        session_id=session_id, status=result.status, turn_id=result.turn_id, interviewer_response=text
    )


@router.get(
    "/{session_id}/report",
    response_model=SessionReportResponse,
    summary="Retrieve final interview report and assessment",
)
async def get_session_report(
    session_id: str,
    request: Request,
    claims: Dict[str, Any] = Depends(require_session_or_service_token),
) -> Any:
    """200 in REPORT_READY, 202 while processing, 409 for failed/cancelled/expired, 404 if absent."""
    await _validate_session_access(request, claims, session_id)
    state = await _require_state(request, session_id)

    if state.status == LifecycleStatus.REPORT_READY:
        workspace = request.app.state.workspace_mgr.get_or_create_workspace(session_id)
        report_md: Optional[str] = None
        assessment_data: Optional[Dict[str, Any]] = None
        decision: Optional[Dict[str, Any]] = None
        if workspace.exists("reports/interview_report.md"):
            report_md = workspace.read_text("reports/interview_report.md")
        if workspace.exists("reports/final_assessment.json"):
            assessment_data = workspace.read_json("reports/final_assessment.json")
        if workspace.exists("reports/decision.json"):
            decision = json.loads(workspace.read_text("reports/decision.json"))
            if workspace.exists("reports/review.json"):
                review = workspace.read_json("reports/review.json")
                decision["review"] = review.get("payload", review)

        verdict = None
        if decision is not None:
            verdict = (decision.get("review") or {}).get("final_verdict") or decision.get("verdict")
        resp = SessionReportResponse(
            session_id=session_id,
            status=state.status,
            report_markdown=report_md,
            final_assessment=assessment_data,
            decision=decision,
            verdict=verdict,
            created_at=state.created_at,
            completed_at=state.updated_at,
            message="Final assessment and report ready",
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=resp.model_dump(mode="json"))

    if state.status in (
        LifecycleStatus.FAILED,
        LifecycleStatus.ASSESSMENT_FAILED,
        LifecycleStatus.CANCELLED,
        LifecycleStatus.EXPIRED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is in terminal non-reportable status: {state.status.value}",
        )

    resp = SessionReportResponse(
        session_id=session_id,
        status=state.status,
        created_at=state.created_at,
        message="Assessment evaluation and report generation are currently processing.",
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=resp.model_dump(mode="json"))


@router.post(
    "/{session_id}/cancel",
    response_model=CancelSessionResponse,
    summary="Cancel an active interview session",
)
async def cancel_session(
    session_id: str,
    request: Request,
    claims: Dict[str, Any] = Depends(require_session_or_service_token),
) -> CancelSessionResponse:
    """Idempotently cancel a session: stops generation, delegated work and pending synthesis."""
    await _validate_session_access(request, claims, session_id)
    state = await _require_state(request, session_id)
    app = request.app

    if state.status == LifecycleStatus.CANCELLED:
        return CancelSessionResponse(session_id=session_id, status=LifecycleStatus.CANCELLED, message="Session already cancelled")
    if state.status in (LifecycleStatus.REPORT_READY, LifecycleStatus.FAILED, LifecycleStatus.EXPIRED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel session in terminal status '{state.status.value}'",
        )

    app.state.controller.cancel_session(session_id)
    try:
        updated = await app.state.state_mgr.atransition(
            state, expected_version=state.state_version, new_status=LifecycleStatus.CANCELLED
        )
    except InvalidStateTransitionError:
        updated = await _require_state(request, session_id)
        if updated.status != LifecycleStatus.CANCELLED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Concurrent state change; retry")
    # CANCELLED is final: fence other processes, let local tasks unwind, then drop RAM markers.
    pipeline = getattr(app.state, "pipeline", None)
    if pipeline is not None:
        await pipeline.stop_work(session_id)
    await app.state.controller.cancellations.cancel_session_and_wait(session_id, timeout=CANCEL_UNWIND_SECONDS)
    if pipeline is not None:
        await pipeline.release_session(session_id)
    else:
        app.state.controller.cancellations.forget(session_id)
    await app.state.connection_mgr.publish(session_id, _event(session_id, LoopEventType.SESSION_CANCELLED, {}))
    return CancelSessionResponse(session_id=session_id, status=updated.status, message="Session cancelled successfully")


@router.post("/{session_id}/end", summary="Stop session agents and generate the final partial report")
async def end_session(session_id: str, request: Request,
    claims: Dict[str, Any] = Depends(require_session_or_service_token)) -> Any:
    await _validate_session_access(request, claims, session_id)
    state = await _require_state(request, session_id)
    if state.status in (LifecycleStatus.CANCELLED, LifecycleStatus.FAILED, LifecycleStatus.EXPIRED):
        raise HTTPException(status_code=409, detail=f"Cannot end session in status {state.status.value}")
    ended_statuses = (LifecycleStatus.ENDING, LifecycleStatus.REPORT_READY, LifecycleStatus.ASSESSMENT_FAILED)
    try:
        await request.app.state.pipeline.end_session(session_id)
    except Exception as exc:
        # Only a persisted ENDING intent can be finished later by retries/the sweep.
        latest = await _require_state(request, session_id)
        if latest.status not in ended_statuses or (
            latest.status == LifecycleStatus.ASSESSMENT_FAILED and not latest.metadata.get("early_ended")
        ):
            if isinstance(exc, InvalidStateTransitionError):
                detail = ("Session changed to a non-reportable terminal state" if latest.status in TERMINAL_STATUSES
                          else "Concurrent state change; retry")
                raise HTTPException(status_code=409, detail=detail)
            logger.error("Ending session failed: %s", sanitize_secrets(str(exc)))
            raise HTTPException(status_code=503, detail="Session could not be ended; retry")
        logger.error("Early-end report pending: %s", sanitize_secrets(str(exc)))
    state = await _require_state(request, session_id)
    if state.status == LifecycleStatus.REPORT_READY:
        code, message = 200, "Final report ready"
    elif state.status == LifecycleStatus.ASSESSMENT_FAILED:
        code, message = 200, "Session ended; final report failed and requires operator review"
    else:
        code, message = 202, "Session ended; final report is processing"
    return JSONResponse(status_code=code, content={"session_id": session_id, "status": state.status.value, "message": message})


@router.post(
    "/{session_id}/assessments/requeue",
    response_model=RequeueAssessmentsResponse,
    summary="Operator: requeue dead-lettered assessments",
)
async def requeue_assessments(
    session_id: str,
    request: Request,
    claims: Dict[str, Any] = Depends(require_service_token),
) -> RequeueAssessmentsResponse:
    """Re-enqueue a session's dead-lettered jobs and move ASSESSMENT_FAILED → ASSESSING (idempotent)."""
    await _require_state(request, session_id)
    app = request.app
    count = await app.state.pipeline.requeue_failed(session_id, app.state.assessment_queue)
    state = await _require_state(request, session_id)
    return RequeueAssessmentsResponse(session_id=session_id, requeued_jobs=count, status=state.status)


@router.post(
    "/{session_id}/review",
    summary="Record a human reviewer's final verdict",
)
async def review_report(
    session_id: str,
    body: ReviewRequest,
    request: Request,
    claims: Dict[str, Any] = Depends(require_service_token),
) -> Any:
    """Attach a human review to decision.json (required when the verdict is NEEDS_REVIEW in hiring_assist mode)."""
    state = await _require_state(request, session_id)
    if state.status != LifecycleStatus.REPORT_READY:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Report not ready")
    reviewer = getattr(request.app.state, "review_service", None)
    if reviewer is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Review service unavailable")
    decision = await reviewer.record_review(session_id, body.reviewer_id, body.final_verdict, body.notes)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"session_id": session_id, "decision": decision})


@router.delete(
    "/{session_id}",
    response_model=DeleteSessionResponse,
    summary="Delete session workspace and artifacts under retention policy",
)
async def delete_session(
    session_id: str,
    request: Request,
    claims: Dict[str, Any] = Depends(require_service_token),
) -> DeleteSessionResponse:
    """Cancel active work, purge every Redis key and the workspace, revoke tokens, audit (content-free)."""
    app = request.app
    state = await app.state.state_mgr.aget(session_id)
    workspace_exists = app.state.cleanup_mgr.get_workspace_size_bytes(session_id) > 0
    archive = getattr(app.state, "artifact_archive", None)
    archived_session_exists = bool(archive and archive.has_session(session_id))
    if state is None and not workspace_exists and not archived_session_exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    await purge_session(app, session_id)
    return DeleteSessionResponse(
        session_id=session_id,
        status="DELETED",
        message="Session and associated workspace artifacts deleted successfully",
    )


async def purge_session(app: Any, session_id: str) -> None:
    """Retention deletion shared by the DELETE endpoint and the retention sweeper."""
    redis = app.state.redis_client
    revocations = getattr(app.state, "revocations", None)
    if revocations is not None:
        await revocations.revoke(session_id)
    else:
        await redis.set(f"session:{session_id}:revoked", "1", ex=7 * 24 * 3600)
    # Dựng hàng rào trước khi đợi/cancel: worker đang gọi LLM không được ghi lại dữ liệu.
    queue = app.state.assessment_queue
    async with queue.session_mutation(session_id):
        async with queue.session_mutation(session_id, purpose="synth_lock"):
            await _purge_session_contents(app, session_id)


async def _purge_session_contents(app: Any, session_id: str) -> None:
    """Purge under the assessment and synthesis leases after revocation is visible."""
    controller = getattr(app.state, "controller", None)
    if controller is not None:
        controller.runners.evict(session_id)
        # Background work (orchestrator, delegations, summaries) must stop before files go away.
        await controller.cancellations.cancel_session_and_wait(session_id)
    await app.state.connection_mgr.close_session(session_id, reason="Session deleted")

    archive = getattr(app.state, "artifact_archive", None)
    if archive is not None:
        try:
            await asyncio.to_thread(archive.delete_session, session_id, owner_id=session_id)
        except Exception as e:
            logger.error(
                "Archived artifact deletion pending for session %s: %s",
                session_ref(session_id),
                sanitize_secrets(str(e)),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Artifact deletion is pending; retry deleting this session",
            ) from e

    redis = app.state.redis_client
    await app.state.assessment_queue.purge_session(session_id)
    await app.state.state_mgr.adelete(session_id)
    await app.state.connection_mgr.event_store.purge(session_id)
    keys = session_assessment_keys(session_id) + [
        f"session:{session_id}:messages",
        f"session:{session_id}:turn_lock",
        f"session:{session_id}:blackboard",
        f"session:{session_id}:summary_lock",
        f"session:{session_id}:work_stopped",
    ]
    await redis.delete(*keys)
    try:
        await asyncio.to_thread(app.state.cleanup_mgr.delete_workspace, session_id)
    except Exception as e:
        logger.warning("Workspace deletion error for session %s: %s", session_ref(session_id), sanitize_secrets(str(e)))
    app.state.workspace_mgr.evict_workspace(session_id)
    if controller is not None:
        controller.cancellations.forget(session_id)
        # Keep refusing work for the deleted id within this process.
        controller.cancellations.cancel_session(session_id)
    try:
        await redis.xadd(
            "audit:session_deletions",
            {"session_ref": session_ref(session_id), "deleted_at": str(time.time())},
            maxlen=100000,
            approximate=True,
        )
    except Exception as e:
        logger.warning("Audit append failed: %s", sanitize_secrets(str(e)))
    logger.info("Session deleted", extra={"session": session_ref(session_id)})
