import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_get_jobs_returns_paginated_list(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "jobs" in data
    assert isinstance(data["jobs"], list)
    assert data["total"] >= 0


def test_get_jobs_filter_by_active_status(client):
    res_active = client.get("/api/jobs?is_active=true")
    assert res_active.status_code == 200
    data_active = res_active.json()
    assert all(j["is_active"] is True for j in data_active["jobs"])

    res_inactive = client.get("/api/jobs?is_active=false")
    assert res_inactive.status_code == 200
    data_inactive = res_inactive.json()
    assert all(j["is_active"] is False for j in data_inactive["jobs"])


def test_get_jobs_empty_query_reports_no_jobs(client):
    response = client.get("/api/jobs?query=nonexistent_query_xyz_9999")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["jobs"] == []
    assert data["message"] == "Không tìm thấy việc làm nào"


def test_get_jobs_stats(client):
    response = client.get("/api/jobs/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_jobs" in data
    assert "by_source" in data


def test_get_sources(client):
    response = client.get("/api/sources")
    assert response.status_code == 200
    sources = response.json()
    assert isinstance(sources, list)
    assert any(s["name"] == "ITviec" for s in sources)


def test_admin_jobs_dashboard(client):
    response = client.get("/admin/jobs")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in response.text or "<html" in response.text
