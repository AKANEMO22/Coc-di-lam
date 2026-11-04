"""Offline sessions (simulation, tests) through the only creation path: an ID_Q pack from a fake RAG service.

`idq_request(app, payload)` turns a candidate payload (candidate_name, target_role, skills, ...)
into a pack shaped like the RAG service's internal knowledge pack, registers it on the app's fake
fetcher and returns the body for `POST /api/v1/sessions/from-idq`. The rubric and question bank
come from the deterministic mock retrieval corpus, so assessments stay reproducible. Never use this
for real candidates: production packs come from the RAG_CocDiLam ID_Q service.
"""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from api.idq_client import IDQServiceError
from core.retrieval.knowledge import InterviewKnowledgeService
from core.retrieval.mock import MockRetrievalClient

_CORE_FIELDS = ("candidate_name", "experience_years", "skills")


def _mock_knowledge(payload: Dict[str, Any]) -> Dict[str, Any]:
    service = InterviewKnowledgeService(MockRetrievalClient())
    coro = service.build_pack(
        target_company=str(payload.get("target_company") or "Target Company"),
        target_role=str(payload.get("target_role") or "Senior Backend Engineer"),
        skills=[str(s) for s in payload.get("skills") or []],
        experience_years=payload.get("experience_years"),
        job_description=payload.get("job_description"),
        seniority=payload.get("seniority"),
    )
    # Works from sync and async tests alike: the pack is built on a private event loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            return pool.submit(asyncio.run, coro).result()
        except Exception:
            return {}


def pack_bundle(payload: Dict[str, Any], id_q: str, pack_id: str) -> Dict[str, Any]:
    knowledge = _mock_knowledge(payload)
    rubric = knowledge.get("rubric") or {}
    role = knowledge.get("role_profile") or {}
    questions = [q for items in (knowledge.get("question_bank") or {}).values() for q in items]
    return {
        "pack_id": pack_id,
        "id_q": id_q,
        "intake_revision": 1,
        "input_type": "form",
        "candidate_profile": {k: payload[k] for k in _CORE_FIELDS if payload.get(k) is not None},
        "interview_target": {
            "company_name": payload.get("target_company"),
            "role_title": payload.get("target_role"),
            "target_seniority": payload.get("seniority"),
        },
        "preferences": {
            "interview_language": payload.get("interview_language") or "vi",
            "interview_round": payload.get("interview_round"),
            "duration_minutes": payload.get("duration_minutes"),
            "additional_requirements": payload.get("interview_format"),
        },
        "field_provenance": {},
        "knowledge_pack": {
            "knowledge_version": "test_mock_corpus",
            "limitations": [],
            "company_knowledge": {"status": "unavailable"},
            "role_profile": {"items": [{
                "role_family": role.get("role_family") or knowledge.get("role_family") or "",
                "role_title": payload.get("target_role"),
                "expectations": [role["expectations"]] if role.get("expectations") else [],
                "source_refs": [role["source"]] if role.get("source") else [],
            }]},
            "question_bank": {"status": "available" if questions else "unsupported_level", "items": [
                {"question_id": q["question_id"], "competency_id": q["competency_id"], "question": q["text"],
                 "stage_type": q.get("stage_type"), "difficulty": q.get("difficulty"), "source_ref": q.get("source")}
                for q in questions
            ]},
            "evaluator": {
                "rubric": {
                    "rubric_version": rubric.get("rubric_version"),
                    "scale": {"min": 1, "max": 4},
                    "pass_threshold": rubric.get("pass_threshold", 3.0),
                    "competencies": [{**c, "source_ref": c.get("source", "")} for c in rubric.get("competencies", [])],
                },
                "questions": [{"question_id": q["question_id"], "evaluator_only": {"expected_answer": q.get("expected_points", [])}}
                              for q in questions],
            },
        },
    }


def install_fake_rag(app: Any) -> Dict[str, Dict[str, Any]]:
    """Serve registered packs from `app.state.idq_fetch`; unknown ids behave like a 404 from RAG."""
    bundles = getattr(app.state, "idq_test_bundles", None)
    if bundles is None:
        bundles = app.state.idq_test_bundles = {}

        def fetch(settings, id_q, pack_id=None):
            if id_q not in bundles:
                raise IDQServiceError(404, "ID_Q intake or knowledge pack was not found")
            return bundles[id_q]

        app.state.idq_fetch = fetch
    return bundles


def idq_request(app: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Body for POST /api/v1/sessions/from-idq; the same payload always maps to the same id_q."""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]
    id_q, pack_id = f"iq_{digest}", f"pack_{digest}"
    install_fake_rag(app)[id_q] = pack_bundle(payload, id_q, pack_id)
    body: Dict[str, Any] = {"id_q": id_q, "pack_id": pack_id, "candidate_name": payload.get("candidate_name")}
    for key in ("session_id", "job_description", "metadata"):
        if payload.get(key):
            body[key] = payload[key]
    return body
