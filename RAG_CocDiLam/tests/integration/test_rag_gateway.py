from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from rag.api import create_app as create_idq_app
from rag.gateway import create_app, is_idq_path
from standalone_rag.src.rag_cocdilam.api import create_app as create_admin_app
from standalone_rag.src.rag_cocdilam.documents import DocumentStore
from standalone_rag.src.rag_cocdilam.ingestion import extract_pages


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_INTAKE_DB_PATH", str(tmp_path / "idq.sqlite3"))
    monkeypatch.setenv("RAG_INTAKE_ROOT", str(tmp_path / "intakes"))
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "lsa")
    monkeypatch.setenv("RAG_WEB_SEARCH_ENABLED", "false")
    seeds = Path(__file__).resolve().parents[2] / "data"
    admin = create_admin_app(store_path=tmp_path / "admin/index.db", storage_dir=tmp_path / "admin",
                             data_dir=seeds, api_token="test-token", admin_password="test-password")
    idq = create_idq_app(store_path=tmp_path / "idq/index.db", data_dir=seeds, api_token="test-token")
    with TestClient(create_app(admin=admin, idq=idq)) as session:
        yield session


def test_admin_and_idq_share_origin_and_login(client):
    root = client.get("/")
    assert root.status_code == 200
    assert "CocDiLam" in root.text
    assert client.get("/assets/ui/design-system.css").status_code == 200
    assert client.get("/assets/ui/workspace.js").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200
    assert "web-search-mode" in client.get("/idq/").text
    assert client.get("/health/ready").json()["status"] == "ready"
    assert client.get("/health").json()["web_search_enabled"] is False
    assert client.get("/api/v1/stats").status_code == 401
    assert client.post("/api/v1/intakes/form", json={}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "test-password"}).status_code == 200
    assert client.get("/api/v1/stats").status_code == 200
    result = client.post("/api/v1/intakes/form", json={"candidate_profile": {"candidate_name": "Test Candidate"}})
    assert result.status_code in {200, 201}, result.text
    payload = result.json()
    assert client.get(f'/api/v1/jobs/{payload["job_id"]}').status_code == 200
    client.post("/api/v1/auth/logout")
    assert client.get(f'/api/v1/intakes/{payload["id_q"]}').status_code == 401
    assert client.get(f'/api/v1/intakes/{payload["id_q"]}', headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_admin_upload_job_does_not_go_to_idq(client):
    client.post("/api/v1/auth/login", json={"password": "test-password"})
    # Exceeds the ID_Q JSON limit: document multipart uploads must reach admin.
    response = client.post("/api/v1/knowledge-bases/interview/documents",
                           files={"file": ("company.txt", b"Python backend requirements.\n" + b" " * 270000, "text/plain")})
    assert response.status_code == 202, response.text
    job = client.get(f'/api/v1/jobs/{response.json()["job_id"]}')
    assert job.status_code == 200, job.text
    assert not job.json()["id"].startswith("job_")


def test_route_selection():
    assert is_idq_path("/api/v1/jobs/job_123")
    assert not is_idq_path("/api/v1/jobs/9af6-123")
    assert not is_idq_path("/api/v1/knowledge-bases")
    assert is_idq_path("/api/v1/knowledge/coverage")


def test_old_upload_paths_follow_preserved_volume(tmp_path):
    store = DocumentStore(tmp_path / "documents.sqlite3")
    store.ensure_default("test-version")
    upload = tmp_path / "uploads/interview/doc/original.txt"
    upload.parent.mkdir(parents=True)
    upload.write_text("Python requirements", encoding="utf-8")
    doc_id, _, _ = store.add_document("interview", "Test", "original.txt",
                                     "/data/rag/uploads/interview/doc/original.txt", "hash", {})
    store.relocate_uploads(tmp_path / "uploads")
    assert store.storage_paths(doc_id) == [upload.resolve()]


def test_docx_ingestion_without_extra_dependency(tmp_path):
    path = tmp_path / "requirements.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Python backend requirements</w:t></w:r></w:p></w:body></w:document>')
    assert extract_pages(path) == ["Python backend requirements"]
