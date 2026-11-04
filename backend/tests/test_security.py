import json
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.core_client import CoreClient
from app.rag_client import RagClient
from app.limits import RateLimiter
from app.main import create_app
from tests.test_gateway import Connector, receive_until, tts_handler

AUTH_SECRET = "a" * 40
CORE_SECRET = "s" * 40


class Core:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.created: list[str] = []
        self.gone = False

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        path, method = request.url.path, request.method
        if method == "DELETE" and path.startswith("/api/v1/sessions/"):
            if self.gone:
                return httpx.Response(404, json={"detail": "Session not found"})
            return httpx.Response(200, json={"session_id": path.split("/")[4], "status": "DELETED"})
        if path in ("/api/v1/sessions/from-idq", "/api/v1/sessions/from-idq/upload") and method == "POST":
            session_id = f"sess_{len(self.created) + 1}"
            self.created.append(session_id)
            return httpx.Response(202, json={"session_id": session_id, "status": "PROFILING"})
        if path == "/api/v1/inspect/runs":
            return httpx.Response(200, json={"runs": [{"session_id": s} for s in ["other", *self.created]]})
        if path.startswith("/api/v1/inspect/runs/"):
            return httpx.Response(200, json={"session": {}, "artifacts": [], "events": []})
        if path.startswith("/api/v1/sessions/"):
            return httpx.Response(200, json={"session_id": path.split("/")[4], "status": "STAGE_ACTIVE",
                                             "current_turn": 0})
        return httpx.Response(404, json={"detail": "nope"})


class Rag:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.intakes: list[str] = []
        self.fail_delete = False

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        path, method = request.url.path, request.method
        if method == "POST" and path in ("/api/v1/intakes/cv", "/api/v1/intakes/form"):
            self.intakes.append(f"iq_{len(self.intakes) + 1}")
            return httpx.Response(201, json={"id_q": self.intakes[-1], "revision": 1, "input_type": "cv"})
        if method == "POST" and path.endswith("/prepare"):
            return httpx.Response(201, json={"pack_id": "pack_1", "limitations": []})
        if method == "DELETE" and path.startswith("/api/v1/intakes/"):
            if self.fail_delete:
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(204)
        if path.startswith("/api/v1/intakes/"):
            return httpx.Response(200, json={"id_q": path.split("/")[4], "revision": 1})
        return httpx.Response(404, json={"detail": "nope"})


def build(tmp_path, **overrides):
    options = dict(core_url="http://core", speech_url="http://speech", core_jwt_secret=CORE_SECRET,
                   rag_url="http://rag", rag_api_token="r" * 32,
                   auth_required=True, auth_jwt_secret=AUTH_SECRET, database_path=str(tmp_path / "gateway.db"))
    options.update(overrides)
    settings = Settings(_env_file=None, **options)
    core = Core()
    client_core = CoreClient(settings, httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(core)))
    connector = Connector()
    http = httpx.AsyncClient(transport=httpx.MockTransport(tts_handler))
    rag = Rag()
    rag_client = RagClient(settings, httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(rag)))
    client = TestClient(create_app(settings, client_core, http, connector, rag=rag_client))
    client.rag = rag
    return client, core, connector


def sign_in(client, student_id="SE100001", **extra):
    response = client.post("/api/auth/login", json={"student_id": student_id, "name": "An", **extra})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create(client, headers, **extra):
    return client.post("/api/sessions", data={"payload": json.dumps({"candidate_name": "An", "cv_text": "Python"})},
                       headers=headers, **extra)


def close_code(client, url, **kwargs):
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(url, **kwargs) as ws:
            ws.receive_text()
    return caught.value.code


# ---- login ----

def test_login_returns_a_token_that_identifies_the_student(tmp_path):
    client, _, _ = build(tmp_path)
    headers = sign_in(client)
    assert client.get("/api/auth/me", headers=headers).json() == {"student_id": "SE100001", "name": "An"}
    token = headers["Authorization"].split()[1]
    claims = jwt.decode(token, AUTH_SECRET, algorithms=["HS256"], issuer="interview-gateway")
    assert claims["sub"] == "SE100001" and claims["exp"] - claims["iat"] == 8 * 3600


def test_frontend_can_ask_whether_sign_in_is_needed_without_a_token(tmp_path):
    on, _, _ = build(tmp_path, access_code="abc")
    assert on.get("/api/auth/status").json() == {"auth_required": True, "mode": "student",
                                                   "access_code_required": True}
    off, _, _ = build(tmp_path, auth_required=False)
    assert off.get("/api/auth/status").json() == {"auth_required": False, "mode": "off",
                                                    "access_code_required": False}


def test_student_id_is_stored_in_upper_case(tmp_path):
    client, _, _ = build(tmp_path)
    lower = client.post("/api/auth/login", json={"student_id": " se100001 ", "name": "An"})
    assert lower.status_code == 200 and lower.json()["student_id"] == "SE100001"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {lower.json()['access_token']}"})
    assert me.json()["student_id"] == "SE100001"
    for bad in ("se145", "S1234567", "SE12345", "SE1234567", "abc"):
        assert client.post("/api/auth/login", json={"student_id": bad}).status_code == 422


def test_login_rejects_bad_student_ids_and_patterns(tmp_path):
    client, _, _ = build(tmp_path, student_id_pattern=r"SE\d{6}")
    assert client.post("/api/auth/login", json={"student_id": "a b"}).status_code == 422
    assert client.post("/api/auth/login", json={"student_id": "AI000001"}).status_code == 403
    assert client.post("/api/auth/login", json={"student_id": "SE000001"}).status_code == 200


def test_access_code_is_checked_when_configured(tmp_path):
    client, _, _ = build(tmp_path, access_code="mon-hoc-2026")
    assert client.post("/api/auth/login", json={"student_id": "SE100001"}).status_code == 401
    assert client.post("/api/auth/login", json={"student_id": "SE100001", "access_code": "sai"}).status_code == 401
    assert client.post("/api/auth/login", json={"student_id": "SE100001", "access_code": "mon-hoc-2026"}).status_code == 200


def test_login_is_rate_limited_per_client(tmp_path):
    client, _, _ = build(tmp_path, rate_login_per_min=3)
    codes = [client.post("/api/auth/login", json={"student_id": "SE100001"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_forwarded_header_is_only_trusted_when_enabled(tmp_path):
    client, _, _ = build(tmp_path, rate_login_per_min=1, trust_proxy_headers=True)
    first = client.post("/api/auth/login", json={"student_id": "SE100001"}, headers={"X-Forwarded-For": "10.0.0.1"})
    second = client.post("/api/auth/login", json={"student_id": "SE100002"}, headers={"X-Forwarded-For": "10.0.0.2"})
    again = client.post("/api/auth/login", json={"student_id": "SE100002"}, headers={"X-Forwarded-For": "10.0.0.2"})
    assert [first.status_code, second.status_code, again.status_code] == [200, 200, 429]
    plain, _, _ = build(tmp_path, rate_login_per_min=1)
    one = plain.post("/api/auth/login", json={"student_id": "SE100001"}, headers={"X-Forwarded-For": "10.0.0.1"})
    two = plain.post("/api/auth/login", json={"student_id": "SE100002"}, headers={"X-Forwarded-For": "10.0.0.2"})
    assert [one.status_code, two.status_code] == [200, 429]


# ---- who may call what ----

def test_rest_needs_a_token_when_auth_is_required(tmp_path):
    client, core, _ = build(tmp_path)
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/tts/voices").status_code == 401
    assert client.get("/api/sessions/sess_1").status_code == 401
    assert client.post("/api/sessions", data={"payload": "{}"}).status_code == 401
    assert client.get("/live").status_code == 200
    assert core.requests == []


def test_bad_tokens_are_refused(tmp_path):
    client, _, _ = build(tmp_path)
    now = int(time.time())
    base = {"iss": "interview-gateway", "sub": "SE100001"}
    expired = jwt.encode({**base, "iat": now - 100, "exp": now - 10}, AUTH_SECRET, algorithm="HS256")
    forged = jwt.encode({**base, "exp": now + 600}, "x" * 40, algorithm="HS256")
    core_token = jwt.encode({"iss": "backend", "aud": "ai-runtime", "exp": now + 600}, CORE_SECRET, algorithm="HS256")
    for token in (expired, forged, core_token, "not-a-token"):
        assert client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_in_the_url_is_not_accepted_for_rest(tmp_path):
    client, _, _ = build(tmp_path)
    token = sign_in(client)["Authorization"].split()[1]
    assert client.get(f"/api/sessions?token={token}").status_code == 401


def test_a_student_only_reaches_their_own_sessions(tmp_path):
    client, core, _ = build(tmp_path)
    alice, bob = sign_in(client, "SE100001"), sign_in(client, "SE100002")
    assert create(client, alice).json()["session_id"] == "sess_1"
    assert create(client, bob).json()["session_id"] == "sess_2"
    assert client.get("/api/sessions/sess_1", headers=alice).status_code == 200
    assert client.get("/api/sessions/sess_1/report", headers=alice).status_code == 200
    before = len(core.requests)
    for method, path in [("get", ""), ("get", "/report"), ("post", "/start"), ("post", "/end"),
                         ("post", "/cancel"), ("patch", "/profile"), ("patch", "/plan")]:
        response = getattr(client, method)(f"/api/sessions/sess_1{path}", headers=bob,
                                           **({"json": {}} if method == "patch" else {}))
        assert response.status_code == 404 and response.json() == {"detail": "Session not found"}, path
    assert len(core.requests) == before


def test_unknown_session_looks_the_same_as_someone_elses(tmp_path):
    client, _, _ = build(tmp_path)
    headers = sign_in(client)
    assert client.get("/api/sessions/never-existed", headers=headers).status_code == 404


def test_session_list_only_shows_own_sessions(tmp_path):
    client, _, _ = build(tmp_path)
    alice, bob = sign_in(client, "SE100001"), sign_in(client, "SE100002")
    create(client, alice)
    create(client, bob)
    create(client, alice)
    mine = client.get("/api/sessions", headers=alice).json()
    assert [run["session_id"] for run in mine["runs"]] == ["sess_1", "sess_3"] and mine["total"] == 2
    assert [run["session_id"] for run in client.get("/api/sessions", headers=bob).json()["runs"]] == ["sess_2"]
    paged = client.get("/api/sessions?limit=1&offset=1", headers=alice).json()
    assert [run["session_id"] for run in paged["runs"]] == ["sess_3"]
    assert client.get("/api/sessions", headers=sign_in(client, "SE100003")).json()["runs"] == []


def test_core_learns_the_owner_and_idempotency_keys_are_per_student(tmp_path):
    client, core, _ = build(tmp_path)
    headers = {**sign_in(client, "SE100001"), "Idempotency-Key": "k1"}
    create(client, headers)
    request = core.requests[-1]
    assert json.loads(request.content)["metadata"]["owner"] == "SE100001"
    assert request.headers["Idempotency-Key"] == "SE100001:k1"


def test_ownership_survives_a_gateway_restart(tmp_path):
    first, _, _ = build(tmp_path)
    alice = sign_in(first, "SE100001")
    create(first, alice)
    second, _, _ = build(tmp_path)
    assert second.get("/api/sessions/sess_1", headers=alice).status_code == 200
    assert second.get("/api/sessions/sess_1", headers=sign_in(second, "SE100002")).status_code == 404


def test_creating_sessions_is_rate_limited_per_student(tmp_path):
    client, _, _ = build(tmp_path, rate_create_per_min=2)
    alice, bob = sign_in(client, "SE100001"), sign_in(client, "SE100002")
    codes = [create(client, alice).status_code for _ in range(3)]
    assert codes == [202, 202, 429]
    assert create(client, bob).status_code == 202


def test_anonymous_creates_are_only_throttled_when_clients_can_be_told_apart(tmp_path):
    shared, _, _ = build(tmp_path, auth_required=False, rate_create_per_min=1)
    assert [create(shared, {}).status_code for _ in range(3)] == [202, 202, 202]
    behind_proxy, _, _ = build(tmp_path, auth_required=False, rate_create_per_min=1, trust_proxy_headers=True)
    one = create(behind_proxy, {"X-Forwarded-For": "10.0.0.1"})
    two = create(behind_proxy, {"X-Forwarded-For": "10.0.0.2"})
    again = create(behind_proxy, {"X-Forwarded-For": "10.0.0.1"})
    assert [one.status_code, two.status_code, again.status_code] == [202, 202, 429]


# ---- uploads ----

def test_uploads_must_look_like_what_they_claim(tmp_path):
    client, core, _ = build(tmp_path, rate_create_per_min=50)
    headers = sign_in(client)

    def upload(name, content):
        return create(client, headers, files={"cv_file": (name, content, "application/octet-stream")})

    assert upload("cv.exe", b"MZ....").status_code == 415
    assert upload("cv.pdf", b"<html>not a pdf</html>").status_code == 415
    assert upload("cv.docx", b"%PDF-1.4").status_code == 415
    assert upload("cv.txt", b"abc\x00def").status_code == 415
    assert upload("noext", b"hello").status_code == 415
    assert core.requests == [] and client.rag.requests == []
    assert upload("cv.pdf", b"%PDF-1.7 ...").status_code == 202
    assert upload("cv.pdf", b"\xef\xbb\xbf\n  %PDF-1.4 ...").status_code == 202
    assert upload("cv.pdf", b"x" * 2000 + b"%PDF-1.4").status_code == 415
    assert upload("cv.docx", b"PK\x03\x04....").status_code == 202
    assert upload("cv.txt", "Tôi là An".encode()).status_code == 202


def test_oversized_payload_and_files_are_rejected(tmp_path):
    client, _, _ = build(tmp_path, max_upload_bytes=100)
    headers = sign_in(client)
    huge = client.post("/api/sessions", data={"payload": json.dumps({"candidate_name": "x" * 250_000})}, headers=headers)
    assert huge.status_code == 413
    big = create(client, headers, files={"cv_file": ("cv.pdf", b"%PDF" + b"0" * 200, "application/pdf")})
    assert big.status_code == 413


# ---- websocket ----

def test_websocket_needs_the_owners_token(tmp_path):
    client, _, _ = build(tmp_path)
    alice, bob = sign_in(client, "SE100001"), sign_in(client, "SE100002")
    create(client, alice)
    token = lambda headers: headers["Authorization"].split()[1]
    assert close_code(client, "/ws/sessions/sess_1") == 4401
    assert close_code(client, "/ws/sessions/sess_1?token=garbage") == 4401
    assert close_code(client, f"/ws/sessions/sess_1?token={token(bob)}") == 4404
    with client.websocket_connect(f"/ws/sessions/sess_1?token={token(alice)}") as ws:
        assert receive_until(ws, "ready")[-1]["current_turn"] == 0


def test_websocket_origin_is_checked_against_the_allow_list(tmp_path):
    client, _, _ = build(tmp_path, auth_required=False, cors_origins="http://127.0.0.1:5190")
    assert close_code(client, "/ws/sessions/sess_1", headers={"origin": "http://evil.example"}) == 4403
    with client.websocket_connect("/ws/sessions/sess_1", headers={"origin": "http://127.0.0.1:5190"}) as ws:
        receive_until(ws, "ready")
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")


def test_websocket_connections_are_rate_limited(tmp_path):
    client, _, _ = build(tmp_path, auth_required=False, rate_ws_per_min=2)
    for _ in range(2):
        with client.websocket_connect("/ws/sessions/sess_1") as ws:
            receive_until(ws, "ready")
    assert close_code(client, "/ws/sessions/sess_1") == 4429


# ---- operations ----

def test_every_response_carries_a_request_id(tmp_path):
    client, _, _ = build(tmp_path)
    assert len(client.get("/live").headers["X-Request-ID"]) == 16
    assert client.get("/live", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    assert client.get("/live", headers={"X-Request-ID": "bad id!"}).headers["X-Request-ID"] != "bad id!"


def test_metrics_are_off_without_a_token_and_protected_with_one(tmp_path):
    off, _, _ = build(tmp_path)
    assert off.get("/metrics").status_code == 404
    client, _, _ = build(tmp_path, metrics_token="m" * 20)
    client.post("/api/auth/login", json={"student_id": "x"})
    sign_in(client)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    text = client.get("/metrics", headers={"Authorization": "Bearer " + "m" * 20}).text
    assert 'gateway_events_total{event="login_ok"} 1' in text
    assert 'gateway_events_total{event="login_failed"} 1' in text
    assert 'route="/api/auth/login"' in text and "gateway_ws_active 0" in text


def test_auth_stays_off_by_default_so_the_current_frontend_keeps_working(tmp_path):
    client, core, _ = build(tmp_path, auth_required=False, auth_jwt_secret="")
    assert client.post("/api/sessions", data={"payload": json.dumps({"cv_text": "Python"})}).status_code == 202
    assert "owner" not in json.loads(core.requests[-1].content)["metadata"]
    assert client.get("/api/sessions/sess_1").status_code == 200


def test_a_valid_token_is_still_recorded_when_auth_is_optional(tmp_path):
    client, core, _ = build(tmp_path, auth_required=False)
    headers = sign_in(client)
    create(client, headers)
    assert json.loads(core.requests[-1].content)["metadata"]["owner"] == "SE100001"


# ---- settings ----

def test_settings_refuse_unsafe_combinations():
    with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
        Settings(_env_file=None, auth_required=True, auth_jwt_secret="short")
    with pytest.raises(ValueError, match="CORE_JWT_SECRET"):
        Settings(_env_file=None, app_env="production", cors_origins="https://a.example")
    with pytest.raises(ValueError, match="RAG_API_TOKEN"):
        Settings(_env_file=None, app_env="production", core_jwt_secret="c" * 40, cors_origins="https://a.example")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings(_env_file=None, app_env="production", core_jwt_secret="c" * 40, rag_api_token="r" * 32)
    with pytest.raises(ValueError, match="STUDENT_ID_PATTERN"):
        Settings(_env_file=None, student_id_pattern="(")
    ok = Settings(_env_file=None, app_env="production", core_jwt_secret="c" * 40, rag_api_token="r" * 32,
                  cors_origins="https://a.example")
    assert ok.cors_origins == "https://a.example"


def test_sliding_window_limiter_forgets_old_hits():
    limiter = RateLimiter(2, window=0.2)
    assert [limiter.allow("k") for _ in range(3)] == [True, True, False]
    assert limiter.allow("other")
    time.sleep(0.25)
    assert limiter.allow("k")
    assert RateLimiter(0).allow("k")


def test_env_example_loads_and_keeps_the_safe_defaults():
    settings = Settings(_env_file=".env.example")
    assert settings.auth_required is False and settings.app_env == "development"
    assert settings.rate_login_per_min == 60 and settings.rate_create_per_min == 6 and settings.rate_ws_per_min == 120
    assert settings.database_path == "data/gateway.db" and settings.trust_proxy_headers is False
