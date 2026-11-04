import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from src.rag.engine import RAGEngine
from src.rag.gemini import GeminiEmbedding
from src.rag.indexing import build_index
from src.rag.schemas import RetrievalRequest
from src.rag.storage import RAGStore


def test_gemini_protocol_cache_and_normalization(tmp_path):
    bodies = []

    def handle(req):
        bodies.append(json.loads(req.content))
        assert req.headers["x-goog-api-key"] == "test-secret"
        return httpx.Response(200, json={"embedding": {"values": [1] * 768}})

    transport = httpx.MockTransport(handle)
    p = GeminiEmbedding(
        api_key="test-secret", cache_path=tmp_path / "cache.db", transport=transport
    )
    docs = p.encode(["React", "Java"])
    assert docs.shape == (2, 768)
    assert np.allclose(np.linalg.norm(docs, axis=1), 1)
    p.encode(["React"])
    assert len(bodies) == 2
    p.encode(["user@example.com React"], query=True)
    p.encode(["user@example.com React"], query=True)
    assert len(bodies) == 3
    assert bodies[0]["content"]["parts"][0]["text"] == "title: none | text: React"
    assert (
        bodies[2]["content"]["parts"][0]["text"]
        == "task: search result | query: [email] React"
    )
    assert "taskType" not in bodies[0]
    p2 = GeminiEmbedding(cache_path=tmp_path / "cache.db")
    assert np.allclose(p2.encode(["React"]), docs[:1])


def test_gemini_retries_and_safe_errors():
    calls, sleeps = [], []

    def handle(req):
        calls.append(req)
        return httpx.Response(
            429, headers={"Retry-After": "100"}, json={"error": "secret content"}
        )

    p = GeminiEmbedding(
        api_key="hidden-key", transport=httpx.MockTransport(handle), sleep=sleeps.append
    )
    with pytest.raises(RuntimeError, match="HTTP 429") as exc:
        p.encode(["private query"], query=True)
    assert len(calls) == 3 and sleeps == [5, 5]
    assert "hidden-key" not in str(exc.value) and "private" not in str(exc.value)


@pytest.mark.parametrize("values", [[0] * 768, [1] * 10, ["Infinity"] * 768])
def test_gemini_invalid_vectors(values):
    p = GeminiEmbedding(
        api_key="test",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"embedding": {"values": values}})
        ),
    )
    with pytest.raises(RuntimeError):
        p.encode(["test"])


def test_gemini_missing_key_and_validation(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiEmbedding().encode(["test"])
    with pytest.raises(ValueError):
        GeminiEmbedding(dimensions=10)


def test_gemini_snapshot_reload_and_fallback(tmp_path, monkeypatch):
    data = Path(__file__).resolve().parents[1] / "data"
    store = RAGStore(tmp_path / "index.db")
    p = GeminiEmbedding(
        api_key="test",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"embedding": {"values": [1] * 768}})
        ),
    )
    build_index(store, data, provider=p)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    engine = RAGEngine(store, data)
    assert engine.provider.signature == p.signature
    result = engine.retrieve(
        RetrievalRequest(
            role_id="IF_FE",
            target_level="Fresher",
            query="React hooks",
            token_budget=24000,
        ),
        persist=False,
    )
    assert result.selected_questions
    assert "dense_unavailable:RuntimeError" in result.degraded_mode


def test_failed_build_preserves_active_snapshot(tmp_path):
    data = Path(__file__).resolve().parents[1] / "data"
    store = RAGStore(tmp_path / "index.db")
    build_index(store, data, provider_kind="lsa")
    old, _ = store.load()
    bad = GeminiEmbedding(
        api_key="test", transport=httpx.MockTransport(lambda req: httpx.Response(401))
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        build_index(store, data, provider=bad)
    assert store.load()[0] == old
