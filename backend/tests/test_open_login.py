"""AUTH_MODE=open: any username signs in without a password, and each username only sees its own data."""
import json

import pytest

from app.config import Settings
from tests.test_gateway import make_client


def _login(client, username: str) -> dict:
    response = client.post("/api/auth/login", json={"student_id": username, "name": username})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create(client, headers: dict) -> str:
    response = client.post("/api/sessions", headers=headers,
                           data={"payload": json.dumps({"candidate_name": "An", "cv_text": "Python 3 năm"})})
    assert response.status_code == 202, response.text
    return response.json()["session_id"]


def test_open_mode_signs_in_any_username_and_reports_its_mode():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:")
    status = client.get("/api/auth/status").json()
    assert status == {"auth_required": True, "mode": "open", "access_code_required": False}
    response = client.post("/api/auth/login", json={"student_id": "  Nguyễn   An@x.com "})
    assert response.status_code == 200 and response.json()["student_id"] == "nguyễn an@x.com"


def test_open_mode_rejects_empty_or_odd_usernames():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:")
    for bad in ("", "   ", "a/b", "x" * 65):
        assert client.post("/api/auth/login", json={"student_id": bad}).status_code == 422


def test_open_mode_requires_a_token():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:")
    assert client.get("/api/sessions").status_code == 401


def test_each_username_only_reaches_its_own_sessions():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:")
    an, binh = _login(client, "an"), _login(client, "binh")
    session_id = _create(client, an)
    refused = client.get(f"/api/sessions/{session_id}/report", headers=binh)
    assert refused.status_code == 404 and refused.json()["detail"] == "Session not found"
    assert client.delete(f"/api/sessions/{session_id}", headers=binh).status_code == 404
    with client.websocket_connect(f"/ws/sessions/{session_id}?token={binh['Authorization'].split()[1]}") as ws:
        closed = ws.receive()
    assert closed["type"] == "websocket.close" and closed["code"] == 4404
    # the same username typed in another case is the same account, and may delete its session
    same = _login(client, "AN")
    deleted = client.delete(f"/api/sessions/{session_id}", headers=same)
    assert deleted.status_code == 200 and deleted.json()["status"] == "DELETED"


def test_open_mode_keeps_job_sync_usable_without_an_admin_token():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:")
    response = client.post("/api/scheduler/stop")
    assert response.status_code != 403


def test_explicit_auth_required_is_not_weakened_by_open_mode():
    settings = Settings(_env_file=None, auth_mode="open", auth_required=True, auth_jwt_secret="s" * 40)
    assert settings.auth_mode == "student"


def test_student_mode_still_needs_a_secret():
    with pytest.raises(ValueError):
        Settings(_env_file=None, auth_mode="student")


def test_open_mode_secret_is_generated_once_and_reused(tmp_path):
    from app import auth

    path = tmp_path / "auth_jwt_secret"
    first = auth.load_or_create_secret(path)
    assert len(first) >= 32 and auth.load_or_create_secret(path) == first


def test_first_sign_in_registers_the_profile_once():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:")
    assert client.post("/api/auth/check", json={"student_id": "SV001"}).json()["exists"] is False
    created = client.post("/api/auth/login", json={"student_id": "SV001", "name": "Nguyễn An",
                                                   "email": "an@x.com", "major": "CNTT"})
    assert created.status_code == 200 and created.json()["profile"]["name"] == "Nguyễn An"
    checked = client.post("/api/auth/check", json={"student_id": " sv001 "}).json()
    assert checked["exists"] is True and checked["profile"]["major"] == "CNTT"
    # a later sign-in with only the id keeps the stored name
    again = client.post("/api/auth/login", json={"student_id": "sv001"}).json()
    assert again["profile"]["name"] == "Nguyễn An"
    assert client.post("/api/auth/check", json={"student_id": "a/b"}).status_code == 422


def test_check_works_without_sign_in_mode():
    client, _, _ = make_client(database_path=":memory:")
    client.post("/api/auth/login", json={"student_id": "se100001", "name": "An"})
    assert client.post("/api/auth/check", json={"student_id": " SE100001"}).json()["exists"] is True
    # off mode keeps the student-id format
    assert client.post("/api/auth/check", json={"student_id": "a b"}).status_code == 422


def test_check_and_login_do_not_share_a_rate_limit_slot():
    client, _, _ = make_client(auth_mode="open", database_path=":memory:", rate_login_per_min=2)
    for name in ("an", "binh"):
        assert client.post("/api/auth/check", json={"student_id": name}).status_code == 200
        assert client.post("/api/auth/login", json={"student_id": name, "name": name}).status_code == 200
