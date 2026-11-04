"""Recovery of sessions that would otherwise stay non-terminal forever, plus related contract fixes."""

import asyncio
import time
from pathlib import Path

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

import api.routes as routes
from api.auth import AuthError, create_service_token, verify_service_token
from api.main import create_app
from core.assessor.pipeline import AssessmentPipeline
from core.assessor.queue import RedisAssessmentQueue
from core.assessor.synthesizer import ReportSynthesizer
from core.session.state import LifecycleStatus, SessionStateManager
from core.workspace.cleanup import WorkspaceCleanupManager
from core.workspace.retention import RetentionService
from core.workspace.session import SessionWorkspace, SessionWorkspaceManager
from tests.integration.test_idq_sessions import ID_Q, PACK_ID, _bundle
from tests.integration.test_production_hardening import _create, _headers, _llm, _settings


def _age(states: SessionStateManager, session_id: str, seconds: float) -> None:
    state = states.get_state(session_id)
    state.created_at -= seconds
    state.updated_at -= seconds


def _pipeline(tmp_path: Path, states: SessionStateManager, **kwargs) -> AssessmentPipeline:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    synthesizer = ReportSynthesizer(redis, lambda sid: SessionWorkspace(sid, tmp_path / sid), state_manager=states)
    return AssessmentPipeline(redis, states, synthesizer, **kwargs)


@pytest.mark.asyncio
async def test_sweep_fails_stale_profiling_and_expires_idle_sessions(tmp_path: Path):
    states = SessionStateManager()
    for sid, status in (("stale", LifecycleStatus.PROFILING), ("fresh", LifecycleStatus.PROFILING),
                        ("live", LifecycleStatus.PROFILING), ("idle", LifecycleStatus.READY),
                        ("active", LifecycleStatus.STAGE_ACTIVE), ("busy", LifecycleStatus.STAGE_ACTIVE)):
        states.create_session(sid, initial_status=status)
    for sid in ("stale", "live"):
        _age(states, sid, 400)
    for sid in ("idle", "active"):
        _age(states, sid, 2 * 3600)
    pipeline = _pipeline(tmp_path, states, profiling_stale_seconds=300, idle_expiry_seconds=3600)
    pipeline.has_live_work = lambda sid: sid == "live"
    closed = []

    async def on_closed(sid, status, code):
        closed.append((sid, status, code))

    pipeline.session_closed = on_closed
    assert await pipeline.sweep() == 3
    assert (await states.aget("stale")).status == LifecycleStatus.FAILED
    assert (await states.aget("stale")).metadata["failure_code"] == "PROFILING_TIMEOUT"
    assert (await states.aget("fresh")).status == LifecycleStatus.PROFILING
    assert (await states.aget("live")).status == LifecycleStatus.PROFILING
    assert (await states.aget("idle")).status == LifecycleStatus.EXPIRED
    assert (await states.aget("active")).status == LifecycleStatus.EXPIRED
    assert (await states.aget("busy")).status == LifecycleStatus.STAGE_ACTIVE
    assert sorted(closed) == sorted([
        ("stale", LifecycleStatus.FAILED, "PROFILING_TIMEOUT"),
        ("idle", LifecycleStatus.EXPIRED, None),
        ("active", LifecycleStatus.EXPIRED, None),
    ])


@pytest.mark.asyncio
async def test_sweep_continues_after_one_session_fails(tmp_path: Path):
    states = SessionStateManager()
    states.create_session("a", initial_status=LifecycleStatus.DIALOGUE_COMPLETED)
    states.create_session("b", initial_status=LifecycleStatus.DIALOGUE_COMPLETED)
    pipeline = _pipeline(tmp_path, states)
    seen = []

    async def flaky(session_id):
        seen.append(session_id)
        if session_id == "a":
            raise RuntimeError("redis blip")

    pipeline.on_dialogue_completed = flaky
    assert await pipeline.sweep() == 2
    assert sorted(seen) == ["a", "b"]


@pytest.mark.asyncio
async def test_synthesis_lease_is_renewed_while_synthesizing(tmp_path: Path):
    states = SessionStateManager()
    states.create_session("s", initial_status=LifecycleStatus.ASSESSING)
    pipeline = _pipeline(tmp_path, states, synthesis_lock_ttl_seconds=1)
    holders = []

    async def ready(session_id):
        return True, 0, 0, 0

    async def slow(session_id, **kwargs):
        await asyncio.sleep(1.6)
        holders.append(await pipeline.redis.get(f"session:{session_id}:synth_lock"))

    pipeline.synthesizer.check_barrier = ready
    pipeline.synthesizer.synthesize = slow
    assert await pipeline.maybe_synthesize("s") is True
    assert holders and holders[0] is not None  # lease outlived its 1 s TTL
    assert await pipeline.redis.get("session:s:synth_lock") is None


@pytest.mark.asyncio
async def test_cancelled_profiling_fails_instead_of_staying_profiling(tmp_path: Path):
    settings = _settings()
    app = create_app(workspace_base=str(tmp_path), redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
                     llm_client=_llm(), settings=settings)
    await app.state.state_mgr.acreate("p", initial_status=LifecycleStatus.PROFILING)
    workspace = app.state.workspace_mgr.get_or_create_workspace("p")
    workspace.initialize()
    await app.state.redis_client.set("session:p:work_stopped", "1")
    await routes._run_profiling_task(app, "p", workspace)
    state = await app.state.state_mgr.aget("p")
    assert state.status == LifecycleStatus.FAILED
    assert state.metadata["failure_code"] == "PROFILING_FAILED"


@pytest.mark.asyncio
async def test_deleted_session_id_cannot_be_reused(tmp_path: Path):
    settings = _settings()
    app = create_app(workspace_base=str(tmp_path), redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
                     llm_client=_llm(), settings=settings)
    sid, _ = await _create(app, settings)
    routes_body = {"id_q": ID_Q, "pack_id": PACK_ID, "session_id": sid}
    app.state.idq_fetch = lambda settings, id_q, pack_id=None: _bundle()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.delete(f"/api/v1/sessions/{sid}", headers=_headers(settings))).status_code == 200
        res = await client.post("/api/v1/sessions/from-idq", json=routes_body, headers=_headers(settings))
    assert res.status_code == 409, res.text
    assert await app.state.state_mgr.aget(sid) is None


@pytest.mark.asyncio
async def test_idq_contract_nulls_are_normalized_and_bad_scale_fails_create(tmp_path: Path):
    settings = _settings()
    app = create_app(workspace_base=str(tmp_path), redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
                     llm_client=_llm(), settings=settings)
    nullable = _bundle()
    nullable["knowledge_pack"]["role_profile"] = None
    nullable["knowledge_pack"]["company_knowledge"] = {
        "status": "needs_selection", "jd_candidates": [{"title": "Guessed JD", "description": "not chosen"}],
    }
    nullable["preferences"] = None
    bad_scale = _bundle()
    bad_scale["knowledge_pack"]["evaluator"]["rubric"]["scale"] = {"min": 1, "max": 10}
    null_id, scale_id = "iq_" + "a" * 32, "iq_" + "b" * 32
    for bundle, id_q in ((nullable, null_id), (bad_scale, scale_id)):
        bundle["id_q"] = id_q
    bundles = {null_id: nullable, scale_id: bad_scale}
    app.state.idq_fetch = lambda settings, id_q, pack_id=None: bundles[id_q]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        ok = await client.post("/api/v1/sessions/from-idq", json={"id_q": null_id}, headers=_headers(settings))
        bad = await client.post("/api/v1/sessions/from-idq", json={"id_q": scale_id}, headers=_headers(settings))
    assert ok.status_code == 202, ok.text
    form = app.state.workspace_mgr.get_or_create_workspace(ok.json()["session_id"]).read_json("inputs/form_input.json")
    # needs_selection: no JD is guessed from the candidate list (same rule as the profiler).
    assert "Guessed JD" not in str(form.get("payload", form).get("job_description") or "")
    assert bad.status_code == 422, bad.text
    assert "scale 1-10" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_ack_deletes_stream_entry():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue = RedisAssessmentQueue(redis)
    await queue.setup_consumer_group("g", "0")
    await queue.enqueue({"session_id": "s", "turn_id": "turn_1", "candidate_text": "answer"})
    [(message_id, _job)] = await queue.read_jobs("g", "c", count=1)
    await queue.ack_job("g", message_id)
    assert await redis.xlen(queue.stream_key) == 0
    assert (await redis.xpending(queue.stream_key, "g"))["pending"] == 0


@pytest.mark.asyncio
async def test_raw_upload_retention_reaches_nested_documents(tmp_path: Path):
    states = SessionStateManager()
    states.create_session("r", initial_status=LifecycleStatus.REPORT_READY,
                          metadata={"report_synthesized_at": time.time() - 48 * 3600})
    manager = SessionWorkspaceManager(root_base=str(tmp_path))
    workspace = manager.get_or_create_workspace("r")
    workspace.initialize()
    workspace.write_bytes("inputs/documents/job_description_original.pdf", b"%PDF raw")
    workspace.write_text("inputs/idq_bundle.json", "{}")
    workspace.write_text("reports/final_assessment.json", "{}")
    service = RetentionService(states, manager, WorkspaceCleanupManager(root_base=str(tmp_path)), raw_upload_retention_hours=24)
    result = await service.sweep()
    assert result["raw_uploads_deleted"] == 2
    assert not workspace.exists("inputs/documents/job_description_original.pdf")
    assert workspace.exists("reports/final_assessment.json")


def test_service_token_issuer_is_enforced():
    secret = "s" * 40
    for issuer in ("backend", "core-studio"):
        assert verify_service_token(create_service_token(issuer=issuer, secret=secret), secret=secret)["iss"] == issuer
    with pytest.raises(AuthError):
        verify_service_token(create_service_token(issuer="intruder", secret=secret), secret=secret)
