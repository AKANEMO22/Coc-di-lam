import time

import httpx

from tests.test_security import build, create, sign_in


def test_a_student_can_delete_their_own_session(tmp_path):
    client, core, _ = build(tmp_path)
    mine = sign_in(client)
    session_id = create(client, mine).json()["session_id"]
    response = client.delete(f"/api/sessions/{session_id}", headers=mine)
    assert response.status_code == 200
    assert any(r.method == "DELETE" and r.url.path == f"/api/v1/sessions/{session_id}" for r in core.requests)
    assert client.get(f"/api/sessions/{session_id}", headers=mine).status_code == 404


def test_nobody_can_delete_someone_elses_session(tmp_path):
    client, core, _ = build(tmp_path)
    owner, other = sign_in(client, "SE100001"), sign_in(client, "SE100002")
    session_id = create(client, owner).json()["session_id"]
    assert client.delete(f"/api/sessions/{session_id}", headers=other).status_code == 404
    assert client.delete(f"/api/sessions/{session_id}").status_code == 401
    assert not any(r.method == "DELETE" for r in core.requests)
    assert client.get(f"/api/sessions/{session_id}", headers=owner).status_code == 200


def test_deleting_a_session_the_core_already_dropped_still_clears_it(tmp_path):
    client, core, _ = build(tmp_path)
    mine = sign_in(client)
    session_id = create(client, mine).json()["session_id"]
    core.gone = True
    assert client.delete(f"/api/sessions/{session_id}", headers=mine).status_code == 200
    assert client.get(f"/api/sessions/{session_id}", headers=mine).status_code == 404


def test_quota_limits_new_sessions_per_student_per_day(tmp_path):
    client, _, _ = build(tmp_path, quota_sessions_per_day=2, rate_create_per_min=50)
    mine, other = sign_in(client), sign_in(client, "SE100002")
    assert create(client, mine).status_code == 202
    assert create(client, mine).status_code == 202
    blocked = create(client, mine)
    assert blocked.status_code == 429 and "2 phiên" in blocked.json()["detail"]
    assert create(client, other).status_code == 202


def test_deleting_sessions_does_not_hand_back_quota(tmp_path):
    client, _, _ = build(tmp_path, quota_sessions_per_day=1, rate_create_per_min=50)
    mine = sign_in(client)
    session_id = create(client, mine).json()["session_id"]
    client.delete(f"/api/sessions/{session_id}", headers=mine)
    assert create(client, mine).status_code == 429


def test_quota_is_off_by_default(tmp_path):
    client, _, _ = build(tmp_path, rate_create_per_min=50)
    mine = sign_in(client)
    assert all(create(client, mine).status_code == 202 for _ in range(8))


def test_old_databases_gain_the_new_column(tmp_path):
    import sqlite3
    path = tmp_path / "gateway.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE owners (session_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, created_at REAL NOT NULL)")
    db.execute("INSERT INTO owners VALUES ('old', 'SE100001', ?)", (time.time(),))
    db.commit()
    db.close()
    from app.owners import Owners
    owners = Owners(str(path))
    assert owners.owner("old") == "SE100001"
    owners.retire("old")
    assert owners.owner("old") is None and owners.created_since("SE100001", 0) == 1


def test_deleting_a_session_also_deletes_the_cv_stored_in_rag(tmp_path):
    client, _, _ = build(tmp_path)
    mine = sign_in(client)
    created = create(client, mine).json()
    response = client.delete(f"/api/sessions/{created['session_id']}", headers=mine)
    assert response.json()["intake_deleted"] is True
    assert any(r.method == "DELETE" and r.url.path == f"/api/v1/intakes/{created['id_q']}" for r in client.rag.requests)


def test_a_failed_intake_delete_is_reported_not_hidden(tmp_path):
    client, _, _ = build(tmp_path)
    mine = sign_in(client)
    session_id = create(client, mine).json()["session_id"]
    client.rag.fail_delete = True
    response = client.delete(f"/api/sessions/{session_id}", headers=mine)
    assert response.status_code == 200 and response.json()["intake_deleted"] is False


def test_intakes_belong_to_the_student_who_created_them(tmp_path):
    client, _, _ = build(tmp_path)
    alice, bob = sign_in(client, "SE100001"), sign_in(client, "SE100002")
    id_q = create(client, alice).json()["id_q"]
    before = len(client.rag.requests)
    assert client.get(f"/api/intakes/{id_q}", headers=alice).status_code == 200
    for method, path in [("get", ""), ("patch", ""), ("post", "/prepare"), ("get", "/rubric")]:
        extra = {"json": {}} if method in ("patch", "post") else {}
        assert getattr(client, method)(f"/api/intakes/{id_q}{path}", headers=bob, **extra).status_code == 404, path
    assert client.get(f"/api/intakes/{id_q}").status_code == 401
    assert len(client.rag.requests) == before + 1


def test_job_sync_changes_need_the_admin_token_while_sign_in_is_required(tmp_path):
    client, _, _ = build(tmp_path, admin_token="a" * 20)
    student = sign_in(client)
    for path in ("/api/sync", "/api/job-sync", "/api/scheduler/start", "/api/scheduler/stop"):
        assert client.post(path, headers=student).status_code == 403, path
        assert client.post(path, headers={"X-Admin-Token": "wrong"}).status_code == 403, path
    assert client.post("/api/scheduler/stop", headers={"X-Admin-Token": "a" * 20}).status_code == 200
    closed, _, _ = build(tmp_path)
    assert closed.post("/api/sync", headers={"X-Admin-Token": ""}).status_code == 403
    assert client.get("/api/sources").status_code == 200
    open_mode, _, _ = build(tmp_path, auth_required=False)
    assert open_mode.get("/api/scheduler/status").status_code == 200
