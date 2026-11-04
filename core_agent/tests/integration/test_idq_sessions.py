"""Sessions created from an ID_Q pack of the RAG service: Core pins the pack and runs no retrieval."""

import json
import time
from pathlib import Path
from typing import Any, Dict

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

import api.routes as routes
from api.auth import create_service_token
from api.idq_client import IDQServiceError, fetch_idq_bundle
from api.main import create_app
from config.settings import Settings, get_settings
from core.retrieval.factory import IDQPackOnlyClient, build_retrieval_client
from core.session.state import LifecycleStatus
from tests.fakes import ScriptedInterviewLLM

ID_Q = "iq_" + "1" * 32
PACK_ID = "pack_" + "2" * 32


def _bundle() -> Dict[str, Any]:
    anchors = {str(level): f"Level {level} evidence" for level in range(1, 6)}
    return {
        "pack_id": PACK_ID,
        "id_q": ID_Q,
        "intake_revision": 1,
        "input_type": "cv",
        "candidate_profile": {
            "candidate_name": "Tran Thi C",
            "experience_years": 2,
            "current_role": "Backend Developer",
            "skills": ["Python", "FastAPI"],
            "experience": [{"role": "Backend Developer", "company": "Contoso"}],
        },
        "interview_target": {"company_name": "Acme", "role_title": "Backend Developer", "target_seniority": "junior"},
        "preferences": {"interview_language": "en", "duration_minutes": 20},
        "field_provenance": {},
        "knowledge_pack": {
            "knowledge_version": "kv_test",
            "limitations": ["Generic role framework only"],
            "company_knowledge": {"status": "unavailable", "company": {"canonical_name": "Acme"}},
            "role_profile": {"items": [{"role_family": "backend", "role_title": "Backend Developer",
                                        "expectations": ["Builds reliable APIs"], "source_refs": ["framework:backend"]}]},
            "question_bank": {"status": "available", "items": [
                {"question_id": "q_api", "competency_id": "api_design", "question": "How do you version an API?",
                 "stage_type": "technical", "source_ref": "framework:backend#q_api"},
            ]},
            "evaluator": {
                "rubric": {"rubric_version": "rv1", "scale": {"min": 1, "max": 5}, "pass_threshold": 3.0,
                           "competencies": [{"competency_id": "api_design", "name": "API design", "weight": 1.0,
                                             "pass_level": 3, "anchors": anchors, "source_ref": "framework:backend#api"}]},
                "questions": [{"question_id": "q_api", "evaluator_only": {"expected_answer": "Versioned routes"}}],
            },
        },
    }


class RecordingLLM(ScriptedInterviewLLM):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list = []

    async def stream_chat(self, messages, tools=None, **kwargs):  # type: ignore[override]
        self.prompts.append(next((str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""))
        async for chunk in super().stream_chat(messages, tools=tools, **kwargs):
            yield chunk


def _settings() -> Settings:
    return get_settings().model_copy(update={
        "RETRIEVAL_BACKEND": "idq", "RAG_IDQ_BASE_URL": "http://rag-idq:8000", "RAG_IDQ_API_TOKEN": "t" * 32,
        "ASSESSOR_POOL_SIZE": 0,
    })


def _wait_ready(client: TestClient, session_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    deadline = time.time() + 15
    while time.time() < deadline:
        status = client.get(f"/api/v1/sessions/{session_id}", headers=headers).json()
        if status["status"] in (LifecycleStatus.READY.value, LifecycleStatus.FAILED.value):
            return status
        time.sleep(0.05)
    raise AssertionError("profiling did not finish")


def _payload(workspace_root: Path, path: str) -> Dict[str, Any]:
    data = json.loads((workspace_root / path).read_text(encoding="utf-8"))
    return data.get("payload", data)


def test_idq_backend_never_searches():
    client = build_retrieval_client(Settings(RETRIEVAL_BACKEND="idq"))
    assert isinstance(client, IDQPackOnlyClient) and client.backend_name == "idq"


def test_fetch_requires_configured_service():
    with pytest.raises(IDQServiceError) as error:
        fetch_idq_bundle(Settings(RAG_IDQ_BASE_URL="", RAG_IDQ_API_TOKEN=""), ID_Q)
    assert error.value.status_code == 503


def test_session_from_idq_pins_the_pack_and_skips_retrieval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested = []

    def fake_fetch(settings, id_q, pack_id=None):
        requested.append((settings.RAG_IDQ_BASE_URL, id_q, pack_id))
        return _bundle()

    monkeypatch.setattr(routes, "fetch_idq_bundle", fake_fetch)
    settings = _settings()
    llm = RecordingLLM()
    app = create_app(workspace_base=str(tmp_path / "ws"), redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
                     llm_client=llm, settings=settings)
    headers = {"Authorization": f"Bearer {create_service_token(issuer='backend', audience='ai-runtime', secret=settings.JWT_SECRET)}"}

    with TestClient(app) as client:
        assert app.state.profiler_agent.knowledge_service is None  # no in-process RAG
        res = client.post("/api/v1/sessions/from-idq", headers={**headers, "Idempotency-Key": "idq-1"},
                          json={"id_q": ID_Q, "pack_id": PACK_ID, "job_description": "Must know PostgreSQL"})
        assert res.status_code == 202, res.text
        session_id = res.json()["session_id"]
        assert requested == [("http://rag-idq:8000", ID_Q, PACK_ID)]
        status = _wait_ready(client, session_id, headers)
        assert status["status"] == LifecycleStatus.READY.value, status
        assert status["retrieval_status"] == "ok"

        root = app.state.workspace_mgr.get_or_create_workspace(session_id).root_path
        knowledge = _payload(root, "context/retrieved_knowledge.json")
        assert knowledge["retrieval_backend"] == "idq" and knowledge["idq"]["pack_id"] == PACK_ID
        rubric = _payload(root, "context/rubric.json")
        assert [c["competency_id"] for c in rubric["competencies"]] == ["api_design"]
        assert set(rubric["competencies"][0]["anchors"]) == {"1", "2", "3", "4"}  # 1-5 source mapped to 1-4
        profile = _payload(root, "context/candidate_profile.json")
        assert profile["current_role"] == "Backend Developer" and profile["skills"] == ["Python", "FastAPI"]
        form = _payload(root, "inputs/form_input.json")
        assert form["candidate_name"] == "Tran Thi C" and form["seniority"] == "junior"
        assert form["interview_language"] == "en" and "Must know PostgreSQL" in form["job_description"]
        assert _payload(root, "context/idq_link.json")["pack_id"] == PACK_ID
        # The CV was extracted by ID_Q: Core asked the model only for the interview spec and plan.
        assert llm.prompts and not any("generate `candidate_profile`" in prompt for prompt in llm.prompts)

        # Seniority is part of the pack; changing it needs a new ID_Q revision.
        plan = client.patch(f"/api/v1/sessions/{session_id}/plan", headers=headers, json={"seniority": "senior"})
        assert plan.status_code == 409
        plan = client.patch(f"/api/v1/sessions/{session_id}/plan", headers=headers, json={"duration_minutes": 30})
        assert plan.status_code == 200, plan.text
        assert _payload(root, "context/retrieved_knowledge.json")["idq"]["pack_id"] == PACK_ID

        # Retrying with the same key returns the same session and keeps the pinned pack.
        again = client.post("/api/v1/sessions/from-idq", headers={**headers, "Idempotency-Key": "idq-1"},
                            json={"id_q": ID_Q, "pack_id": PACK_ID, "job_description": "Must know PostgreSQL"})
        assert again.status_code == 202 and again.json()["session_id"] == session_id


def test_session_from_idq_maps_service_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(settings, id_q, pack_id=None):
        raise IDQServiceError(404, "ID_Q intake or knowledge pack was not found")

    monkeypatch.setattr(routes, "fetch_idq_bundle", missing)
    settings = _settings()
    app = create_app(workspace_base=str(tmp_path / "ws"), redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
                     llm_client=ScriptedInterviewLLM(), settings=settings)
    headers = {"Authorization": f"Bearer {create_service_token(issuer='backend', audience='ai-runtime', secret=settings.JWT_SECRET)}"}
    with TestClient(app) as client:
        res = client.post("/api/v1/sessions/from-idq", headers=headers, json={"id_q": ID_Q})
        assert res.status_code == 404
        assert client.post("/api/v1/sessions/from-idq", headers=headers, json={"id_q": "not-an-id"}).status_code == 422


def test_jd_document_upload_and_legacy_routes_are_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "fetch_idq_bundle", lambda settings, id_q, pack_id=None: _bundle())
    settings = _settings()
    app = create_app(workspace_base=str(tmp_path / "ws"), redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
                     llm_client=ScriptedInterviewLLM(), settings=settings)
    headers = {"Authorization": f"Bearer {create_service_token(issuer='backend', audience='ai-runtime', secret=settings.JWT_SECRET)}"}
    with TestClient(app) as client:
        for path in ("/api/v1/sessions", "/api/v1/sessions/upload"):
            assert client.post(path, headers=headers, json={"candidate_name": "Old"}).status_code in (404, 405)
        res = client.post(
            "/api/v1/sessions/from-idq/upload", headers=headers,
            data={"payload": json.dumps({"id_q": ID_Q, "pack_id": PACK_ID, "job_description": "Typed JD"})},
            files={"job_description_file": ("jd.txt", "Kafka and Redis required".encode(), "text/plain")},
        )
        assert res.status_code == 202, res.text
        session_id = res.json()["session_id"]
        assert _wait_ready(client, session_id, headers)["status"] == LifecycleStatus.READY.value
        root = app.state.workspace_mgr.get_or_create_workspace(session_id).root_path
        form = _payload(root, "inputs/form_input.json")
        assert "Typed JD" in form["job_description"] and "Kafka and Redis required" in form["job_description"]
        assert form["documents"][0]["path"] == "inputs/documents/job_description_original.txt"
        assert (root / "inputs/documents/job_description_original.txt").read_bytes() == b"Kafka and Redis required"
        bad = client.post("/api/v1/sessions/from-idq/upload", headers=headers, data={"payload": json.dumps({"id_q": ID_Q})},
                          files={"job_description_file": ("jd.exe", b"MZ", "application/octet-stream")})
        assert bad.status_code == 422
