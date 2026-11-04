import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.models.candidate import CandidateProfile, Project
from src.rag.api import create_app
from src.rag.engine import RAGEngine, token_upper_bound
from src.rag.indexing import build_index
from src.rag.ontology import canonical, mentions
from src.rag.retrievers import GraphRetriever, bm25_retrieve, fuse
from src.rag.schemas import FollowUpRequest, RetrievalHit, RetrievalRequest
from src.rag.storage import RAGStore

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    store = RAGStore(tmp_path_factory.mktemp("rag") / "index.db")
    build_index(store, DATA, provider_kind="lsa")
    return RAGEngine(store, DATA)


def request(**kwargs):
    return RetrievalRequest(
        role_id="IF_FE",
        target_level="Fresher",
        query="React hooks virtual DOM JavaScript",
        token_budget=24000,
        **kwargs,
    )


def test_inventory_and_atomic_sources(engine):
    assert engine.payload["audit"]["technical_questions"] == 350
    assert engine.payload["audit"]["behavioral_questions"] == 75
    assert len(engine.questions) == 425
    for q in engine.questions.values():
        assert q["sources"][0]["content_hash"]
        if q["question_type"] == "technical":
            assert all(q["rubric"][k] for k in ("poor", "acceptable", "excellent"))


def test_aliases_and_word_boundaries():
    assert canonical("ReactJS") == "react"
    assert canonical("RAG") == "retrieval augmented generation"
    assert mentions("JavaScript", "java") is False
    assert mentions("Use RAG", "retrieval augmented generation")


def test_index_idempotency_and_provider_guard(engine):
    result = build_index(engine.store, DATA, provider=engine.provider)
    assert result["cached"]
    assert result["index_version"] == engine.version

    class WrongProvider:
        signature = "wrong"

    with pytest.raises(ValueError, match="provider differs"):
        RAGEngine(engine.store, DATA, provider=WrongProvider())


@pytest.mark.parametrize("mode", ["bm25", "dense", "text", "graph", "hybrid", "auto"])
def test_every_retrieval_mode_obeys_guards(engine, mode):
    result = engine.retrieve(request(mode=mode), persist=False)
    assert result.status == "ok"
    assert all(
        q.role_id == "IF_FE" and q.level == "Fresher" for q in result.selected_questions
    )
    assert token_upper_bound(result.prompt_context) <= 24000
    for q in result.selected_questions:
        original = engine.questions[q.question_id]
        assert q.expected_answer == original["expected_answer"]
        assert q.rubric == original["rubric"]
    if mode in {"graph", "hybrid"}:
        assert result.graph_paths
        assert all(
            len(p.nodes) == len(p.relations) + 1 and p.sources
            for p in result.graph_paths
        )


def test_unknown_role_and_senior_are_explicit(engine):
    result = engine.retrieve(
        RetrievalRequest(role_id="NOT_A_ROLE", query="React"), persist=False
    )
    assert result.status == "unsupported_role" and not result.selected_questions
    job = engine.jobs["vnw_2107318"].copy()
    job["levels"] = "Senior"
    from src.models.job import JobDescription

    result = engine.retrieve(
        RetrievalRequest(
            role_id="IF_FE", job=JobDescription.model_validate(job), query="React"
        ),
        persist=False,
    )
    assert result.status == "unsupported_level"


def test_target_level_and_evidence(engine):
    candidate = CandidateProfile(
        candidate_id="C",
        full_name="Private",
        target_role="Frontend",
        category="Frontend",
        current_level="Intern",
        technical_skills=["React"],
        projects=[
            Project(name="P", description="Uses TypeScript", tech_stack=["TypeScript"])
        ],
    )
    result = engine.retrieve(
        RetrievalRequest(
            candidate=candidate,
            role_id="IF_FE",
            target_level="Junior",
            query="React",
            required_skills=["React", "TypeScript", "Docker"],
            token_budget=24000,
        ),
        persist=False,
    )
    assert result.target_level == "Junior" and result.candidate_level == "Intern"
    assert {e.skill: e.status for e in result.skill_evidence} == {
        "react": "claimed",
        "typescript": "project_evidence",
        "docker": "not_evidenced",
    }
    assert "Private" not in json.dumps(result.trace)
    assert "docker" in result.uncovered_requirements


def test_stage_and_budget_and_empty_query(engine):
    assert (
        engine.retrieve(request(stage=10), persist=False).status == "unsupported_stage"
    )
    assert (
        engine.retrieve(
            request().model_copy(update={"token_budget": 256}), persist=False
        ).selected_questions
        == []
    )
    assert (
        engine.retrieve(RetrievalRequest(role_id="IF_FE"), persist=False).status
        == "empty"
    )


def test_behavioral_stage(engine):
    stages = engine.payload["frameworks"]["IF_FE"]["interview_stages"]
    stage = next(
        s["stage"]
        for s in stages
        if "behavior" in s["stage_name"].lower() or "culture" in s["stage_name"].lower()
    )
    query = engine.payload["frameworks"]["IF_FE"]["behavioral_questions"][0]["question"]
    result = engine.retrieve(
        RetrievalRequest(
            role_id="IF_FE",
            target_level="Junior",
            stage=stage,
            query=query,
            mode="text",
            token_budget=24000,
        ),
        persist=False,
    )
    assert result.selected_questions
    assert all(
        q.question_type == "behavioral" and q.level == "Junior"
        for q in result.selected_questions
    )


def test_filter_before_bm25():
    questions = {"good": {}, **{f"bad{i}": {} for i in range(200)}}
    texts = {qid: "java java java" for qid in questions}
    hits = bm25_retrieve("java", questions, texts, {"good"}, 1)
    assert [h.question_id for h in hits] == ["good"]


def test_real_rrf_does_not_add_fallback_documents():
    channels = {
        "bm25": [RetrievalHit(question_id="A", channel="bm25", rank=1, score=20)],
        "dense": [RetrievalHit(question_id="B", channel="dense", rank=1, score=0.8)],
    }
    assert {qid for qid, _ in fuse(channels)} == {"A", "B"}
    assert fuse({}) == []


def test_multihop_graph_has_provenance_and_limits():
    from src.rag.ontology import entity_id

    a, b = entity_id("react"), entity_id("javascript")
    ref = {"locator": "test#relation", "content_hash": "h"}
    graph = GraphRetriever(
        {
            "concepts": {a: "react", b: "javascript"},
            "edges": [
                {
                    "source": a,
                    "target": b,
                    "relation": "HAS_PREREQUISITE",
                    "confidence": 0.8,
                    "ref": ref,
                },
                {
                    "source": b,
                    "target": "Q",
                    "relation": "TESTED_BY",
                    "confidence": 1,
                    "ref": ref,
                },
            ],
        }
    )
    hits, _trace = graph.retrieve("React", {"Q"}, 5)
    assert hits[0].paths[0].relations == ["HAS_PREREQUISITE", "TESTED_BY"]
    assert not graph.retrieve("React", {"Q"}, 5, include_prerequisites=False)[0]
    assert not graph.retrieve("React", {"Q"}, 5, max_hops=1)[0]
    assert graph.retrieve("React", {"Q"}, 5, max_expansions=0)[1]["truncated"]


def test_durable_session_excludes_served_and_is_tenant_owned(engine):
    first = engine.retrieve(request(top_k=1, tenant_id="owner"))
    assert first.session_id
    with pytest.raises(KeyError):
        engine.store.session(first.session_id, "other")
    restarted = RAGEngine(engine.store, DATA)
    second = restarted.follow_up(
        FollowUpRequest(
            session_id=first.session_id,
            tenant_id="owner",
            answer="React",
            verified_skills=["React"],
            top_k=1,
        )
    )
    assert {q.question_id for q in first.selected_questions}.isdisjoint(
        q.question_id for q in second.selected_questions
    )
    assert "react" in engine.store.session(first.session_id, "owner")["verified"]
    with pytest.raises(ValueError, match="not served"):
        engine.follow_up(
            FollowUpRequest(
                session_id=first.session_id,
                tenant_id="owner",
                answered_question_id="foreign",
            )
        )


def test_trace_isolation_and_expiry(engine):
    result = engine.retrieve(request(tenant_id="traceowner", top_k=1))
    with pytest.raises(KeyError):
        engine.store.trace(result.request_id, "other")
    assert (
        engine.store.trace(result.request_id, "traceowner")["index_version"]
        == engine.version
    )
    engine.store.save_session("expired", "owner", {}, ttl=-1)
    engine.store.purge()
    with pytest.raises(KeyError):
        engine.store.session("expired", "owner")


def test_snapshot_rollover_and_rollback(tmp_path):
    store = RAGStore(tmp_path / "index.db")
    store.publish("one", {"questions": [1]})
    store.publish("two", {"questions": []})
    assert store.load()[1]["questions"] == []
    store.activate("one")
    assert store.load()[1]["questions"] == [1]
    with pytest.raises(KeyError):
        store.activate("missing")


def test_api_auth_validation_trace_and_followup(engine):
    app = create_app(engine=engine, api_token="test-token", tenant_id="apiowner")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert (
            client.post("/rag/context", json=request().model_dump()).status_code == 401
        )
        assert (
            client.post(
                "/rag/context", headers=headers, json=request().model_dump()
            ).status_code
            == 403
        )
        response = client.post(
            "/rag/context",
            headers=headers,
            json=request(tenant_id="apiowner", top_k=1).model_dump(),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert (
            client.get("/rag/trace/" + data["request_id"], headers=headers).status_code
            == 200
        )
        second = client.post(
            "/rag/follow-up",
            headers=headers,
            json={
                "tenant_id": "apiowner",
                "session_id": data["session_id"],
                "answer": "React",
                "top_k": 1,
            },
        )
        assert second.status_code == 200
        assert (
            client.post(
                "/rag/context", headers=headers, json={"role_id": "IF_FE", "top_k": -1}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/rag/context", headers=headers, content="x" * 262145
            ).status_code
            == 413
        )
        assert (
            client.delete(
                "/rag/session/" + data["session_id"], headers=headers
            ).status_code
            == 200
        )


def test_metrics_and_label_review_gate(engine):
    from src.rag.evaluation import evaluate, generate_cases, retrieval_metrics

    metrics = retrieval_metrics(["bad", "good"], {"good": 2}, 5)
    assert (
        metrics["recall_at_k"] == 1
        and metrics["precision_at_k"] == 0.2
        and metrics["mrr"] == 0.5
    )
    assert retrieval_metrics([], {}, 5)["abstention_correct"] == 1
    cases = generate_cases(engine.payload)
    assert len(cases) == 150
    assert not (
        {c.group_id for c in cases if c.split == "dev"}
        & {c.group_id for c in cases if c.split == "test"}
    )
    with pytest.raises(ValueError, match="human-reviewed"):
        evaluate(engine, cases, require_reviewed=True)


def test_intake_preserves_evidence(tmp_path):
    from src.rag.parsing import parse_document

    source = tmp_path / "cv.txt"
    source.write_text("Project uses ReactJS and Python", encoding="utf-8")
    result = parse_document(source, "cv")
    assert "react" in result["skills"] and "python" in result["skills"]
    assert result["review_required"] and result["evidence"][0]["page"] == 1


def test_legacy_assembler_adapter(engine):
    from src.context_builder.assembler import ContextAssembler
    from src.rag.adapter import HybridGraphRAGRetriever

    ctx = ContextAssembler(retriever=HybridGraphRAGRetriever(engine)).assemble(
        "CV_FE_001", "vnw_2107318"
    )
    assert ctx.retrieved_knowledge.retrieval_strategy_used == "hybrid_graph_rag_v1"
    assert ctx.retrieved_knowledge.index_version == engine.version
    assert ctx.retrieved_knowledge.source_refs


def test_grounding_metrics_require_reviewed_independent_evidence():
    from src.rag.grounding import GroundingSample, evaluate_grounding

    sample = GroundingSample(
        id="G",
        response="A true and an unsupported claim",
        retrieved_source_locators=["source:1"],
        claims=[
            {"text": "true", "supported": True, "source_locators": ["source:1"]},
            {"text": "unsupported", "supported": False},
        ],
        human_reviewed=True,
        predicted_grade=3,
        reference_grade=4,
    )
    result = evaluate_grounding([sample])
    assert result["faithfulness"] == 0.5 and result["grade_mae"] == 1
    with pytest.raises(ValueError, match="human-reviewed"):
        evaluate_grounding([sample.model_copy(update={"human_reviewed": False})])


def test_legacy_faithfulness_does_not_fabricate_scores():
    from src.evaluation.metrics import FaithfulnessLLMJudge

    assert FaithfulnessLLMJudge().evaluate("a formatted prompt") is None


def test_unsupported_competency_does_not_return_unrelated_questions(engine):
    result = engine.retrieve(
        RetrievalRequest(
            role_id="IF_AI_SWE",
            target_level="Junior",
            required_skills=["RAG"],
            query="RAG vector database",
            token_budget=24000,
        ),
        persist=False,
    )
    assert result.status == "empty"
    assert result.uncovered_requirements == ["retrieval augmented generation"]


def test_graph_inventory_has_no_dangling_references(engine):
    nodes = engine.payload["nodes"]
    assert {n["kind"] for n in nodes.values()} >= {
        "Role",
        "Question",
        "Rubric",
        "Level",
        "Concept",
        "Competency",
        "InterviewStage",
    }
    assert all(
        e["source"] in nodes and e["target"] in nodes for e in engine.payload["edges"]
    )


def test_qwen_embedding_query_document_protocol():
    from unittest.mock import Mock

    import numpy as np

    from src.rag.embedding import NeuralEmbedding

    provider = NeuralEmbedding.__new__(NeuralEmbedding)
    provider.name = "Qwen/Qwen3-Embedding-0.6B"
    provider.resolved_revision = "test-revision"
    provider.model = Mock()
    provider.model.encode.return_value = np.ones((1, 1024))
    assert provider._encode(["RAG"], query=True).shape == (1, 1024)
    query_kwargs = provider.model.encode.call_args.kwargs
    assert query_kwargs["prompt_name"] == "query"
    assert query_kwargs["normalize_embeddings"] is True
    provider._encode(["A retrieval document"], query=False)
    document_kwargs = provider.model.encode.call_args.kwargs
    assert document_kwargs["prompt"] == ""
    assert "prompt_name" not in document_kwargs
    assert provider.signature.endswith(":512:qwen-query-v1")
