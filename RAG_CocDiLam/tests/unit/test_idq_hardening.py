"""Auth, error-detail, SQLite, deadline, and snapshot hardening of the ID_Q service."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag.api import create_app
from rag.idq_routes import public_error_message, tokens_match
from rag.idq_service import IDQKnowledgeService, knowledge_version
from rag.idq_store import IDQStore
from rag.web_research import CompanyJDWebResearch, WebResearchConfig

from .test_idq_web_research import FakeEngine, FakeSearch, MemoryStore, _target

API = {"Authorization": "Bearer api-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_INTAKE_DB_PATH", str(tmp_path / "idq.sqlite3"))
    monkeypatch.setenv("RAG_INTAKE_ROOT", str(tmp_path / "intakes"))
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "lsa")
    monkeypatch.setenv("RAG_WEB_SEARCH_ENABLED", "false")
    monkeypatch.delenv("RAG_CORE_TOKEN", raising=False)
    monkeypatch.delenv("RAG_KNOWLEDGE_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("RAG_API_TOKEN", "api-token")
    seeds = Path(__file__).resolve().parents[2] / "data"
    app = create_app(store_path=tmp_path / "idq/index.db", data_dir=seeds, api_token="api-token")
    with TestClient(app, raise_server_exceptions=False) as session:
        yield session


def test_tokens_match_handles_non_ascii_and_empty_values():
    assert tokens_match("tökén", "tökén")
    assert not tokens_match("tökén", "token")
    assert not tokens_match("", "")
    assert not tokens_match(None, "token")


def test_non_ascii_bearer_is_rejected_not_a_server_error(client):
    response = client.get("/api/v1/intakes/iq_missing", headers={"Authorization": "Bearer tökén".encode("utf-8")})
    assert response.status_code == 401


def test_internal_pack_never_accepts_the_api_token_as_core_token(client, monkeypatch):
    path = "/api/v1/internal/intakes/iq_missing/knowledge_pack"
    # Unset core token: closed, even when the caller replays the API token.
    assert client.get(path, headers={**API, "X-RAG-Core-Token": "api-token"}).status_code == 503
    monkeypatch.setenv("RAG_CORE_TOKEN", "core-token")
    assert client.get(path, headers={**API, "X-RAG-Core-Token": "api-token"}).status_code == 401
    assert client.get(path, headers={**API, "X-RAG-Core-Token": "cöre".encode("utf-8")}).status_code == 401
    assert client.get(path, headers={**API, "X-RAG-Core-Token": "core-token"}).status_code == 404


def test_admin_endpoints_are_closed_to_the_plain_api_token(client, monkeypatch):
    empty_import = {"companies": [], "job_descriptions": []}
    assert client.post("/api/v1/knowledge/imports", json=empty_import, headers=API).status_code == 403
    assert client.post("/rag/index", json={}, headers=API).status_code == 403
    monkeypatch.setenv("RAG_KNOWLEDGE_ADMIN_TOKEN", "admin-token")
    assert client.post("/api/v1/knowledge/imports", json=empty_import, headers={**API, "X-RAG-Admin-Token": "api-token"}).status_code == 403
    response = client.post("/api/v1/knowledge/imports", json=empty_import, headers={**API, "X-RAG-Admin-Token": "admin-token"})
    assert response.status_code == 200, response.text


def test_only_deliberate_messages_are_exposed():
    with pytest.raises(ValueError) as stdlib_error:
        json.loads("{not json")
    assert public_error_message(stdlib_error.value, "generic") == "generic"
    with pytest.raises(KeyError) as lookup_error:
        {}["internal_key"]
    assert public_error_message(lookup_error.value, "generic") == "generic"
    service = IDQKnowledgeService(MemoryStore(), FakeEngine(), Path("."))
    with pytest.raises(ValueError) as deliberate:
        service.prepare({"candidate_profile": {}, "interview_target": {}, "preferences": {}}, top_k=0)
    assert public_error_message(deliberate.value, "generic") == "top_k must be between 1 and 50"


def test_idq_store_uses_wal_and_a_busy_timeout(tmp_path):
    store = IDQStore(tmp_path / "idq.sqlite3", tmp_path / "uploads")
    with store.connection() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    with sqlite3.connect(tmp_path / "idq.sqlite3") as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.asyncio
async def test_web_research_total_deadline_returns_partial_results_uncached():
    class SlowFetch(FakeSearch):
        async def search(self, query, target_url=None, max_results=4):
            if target_url:
                await asyncio.sleep(30)
            return await super().search(query, target_url=target_url, max_results=max_results)

    store = MemoryStore()
    research = CompanyJDWebResearch(SlowFetch(), store, WebResearchConfig(total_timeout_seconds=0.3))
    started = time.monotonic()
    result = await research.research(_target(), role_id="IF_BE_GEN", level="junior")
    assert time.monotonic() - started < 5
    assert result["web_research"]["timed_out"] is True
    assert any("time limit" in item for item in result["limitations"])
    # Search snippets survive as candidates; the partial answer is not cached.
    assert result["jd_candidates"]
    assert store.cache == {}


class CountingStore(MemoryStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog_reads = 0

    def company_knowledge(self):
        self.catalog_reads += 1
        return super().company_knowledge()


def _intake():
    return {
        "id_q": "iq_test",
        "revision": 1,
        "candidate_profile": {"summary": "Python backend", "skills": ["Python"], "projects": []},
        "interview_target": _target(),
        "preferences": {"interview_round": "technical"},
    }


@pytest.mark.asyncio
async def test_prepare_async_parses_catalog_once_and_versions_consistently(tmp_path):
    store = CountingStore()
    service = IDQKnowledgeService(store, FakeEngine(), tmp_path, web_research=CompanyJDWebResearch(FakeSearch(), store))
    result = await service.prepare_async(_intake())
    assert store.catalog_reads == 1
    assert result["company_knowledge"]["status"] == "selected"
    assert service.version == knowledge_version(service.engine, [], [])


def test_prepare_keeps_its_captured_engine_snapshot_across_a_swap(tmp_path):
    service = IDQKnowledgeService(MemoryStore(), FakeEngine(), tmp_path)
    context = service._context()

    class EmptyEngine:
        version = "index-v2"
        payload = {"audit": {}, "frameworks": {}}

    service.swap_engine(EmptyEngine())
    assert service.frameworks == {} and service.engine.version == "index-v2"
    result = service.prepare(_intake(), context=context)
    assert result["role_profile"]["items"][0]["role_id"] == "IF_BE_GEN"
