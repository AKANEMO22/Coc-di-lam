"""Barge-in, in-room documents, answer context and orphan-intake cleanup."""
import json
import time

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.core_client import CoreClient
from app.main import create_app
from app.rag_client import RagClient
from tests.test_gateway import ID_Q, Connector, make_client, rag_handler, receive_until, tts_handler


def _token(turn_id: str, sequence: int, token: str) -> dict:
    return {"type": "response_token", "turn_id": turn_id, "sequence": sequence, "timestamp": time.time(),
            "payload": {"token": token}}


def _complete(turn_id: str, turn: int, sequence: int, text: str, interrupted: bool = False) -> dict:
    return {"type": "turn_complete", "turn_id": turn_id, "sequence": sequence, "timestamp": time.time(),
            "payload": {"turn_number": turn, "interviewer_response": text, "interrupted": interrupted}}


def _core_messages(connector: Connector, kind: str) -> list[dict]:
    return [m for m in (json.loads(raw) for raw in connector.core.sent) if m.get("type") == kind]


def test_barge_in_cancels_the_generation_and_mutes_the_rest_of_the_reply():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        connector.core.push(_token("turn_001", 10, "Câu đầu tiên khá dài đây. "))
        receive_until(ws, "tts_sentence")

        ws.send_json({"type": "ptt_start"})
        receive_until(ws, "tts_stop")
        connector.core.push(_token("turn_001", 11, "Phần sau không được đọc nữa. "))
        connector.core.push(_complete("turn_001", 1, 12, "Câu đầu tiên khá dài đây.", interrupted=True))
        messages = receive_until(ws, "core")  # token 11
        messages += receive_until(ws, "core")  # turn_complete
        ws.send_json({"type": "ptt_cancel"})
    assert len(_core_messages(connector, "cancel_generation")) == 1
    assert not any(m["type"] == "tts_sentence" for m in messages)


def test_text_answer_over_a_streaming_reply_waits_for_its_turn_to_finish():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        original = connector._core_send

        async def on_send(socket, message):
            data = json.loads(message)
            if data["type"] == "cancel_generation":
                socket.push(_complete("turn_001", 1, 12, "Câu hỏi bị ngắt.", interrupted=True))
                return
            await original(socket, message)

        connector.core.on_send = on_send
        connector.core.push(_token("turn_001", 10, "Câu hỏi đang "))
        receive_until(ws, "core")
        ws.send_json({"type": "text_answer", "text": "Em xin trả lời luôn."})
        sent = receive_until(ws, "answer_sent")[-1]
    assert sent["expected_turn"] == 2
    assert connector.core_answers[0]["expected_turn"] == 2


def test_no_active_generation_is_not_shown_to_the_candidate():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        connector.core.push({"type": "error", "sequence": 9, "payload": {"code": "NO_ACTIVE_GENERATION"}})
        ws.send_json({"type": "text_answer", "text": "Em làm backend."})
        messages = receive_until(ws, "tts_idle")
    assert not any(m["type"] == "core" and m["event"]["type"] == "error" for m in messages)


def test_answer_context_rides_on_the_next_answer_only():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        ws.send_json({"type": "answer_context", "text": "Tài liệu ứng viên đính kèm: cv.txt\nPython 3 năm"})
        ws.send_json({"type": "text_answer", "text": "Em gửi CV."})
        sent = receive_until(ws, "answer_sent")[-1]
        receive_until(ws, "answer_accepted")
    assert sent["text"] == "Em gửi CV."  # the transcript shows what the candidate said
    answer = connector.core_answers[0]["candidate_answer"]
    assert answer.startswith("Em gửi CV.\n\n") and "Python 3 năm" in answer


def test_answer_context_is_cut_to_the_core_answer_limit():
    client, _, connector = make_client(max_answer_chars=100)
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        ws.send_json({"type": "answer_context", "text": "x" * 500})
        ws.send_json({"type": "text_answer", "text": "Em gửi tài liệu."})
        receive_until(ws, "answer_sent")
    assert len(connector.core_answers[0]["candidate_answer"]) <= 100


def test_document_upload_returns_its_text():
    client, _, _ = make_client()
    response = client.post("/api/sessions/sess_1/documents",
                           files={"file": ("notes.txt", "Dự án Redis\r\n\r\n\r\nKafka".encode(), "text/plain")})
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "notes.txt" and data["extracted_text"] == "Dự án Redis\n\nKafka"
    assert data["truncated"] is False


def test_document_upload_rejects_unsupported_files():
    client, _, _ = make_client()
    response = client.post("/api/sessions/sess_1/documents", files={"file": ("a.exe", b"MZ", "application/x-msdownload")})
    assert response.status_code == 415


def _failing_core_client(core_status: int, **overrides):
    settings = Settings(_env_file=None, core_url="http://core", speech_url="http://speech", core_jwt_secret="s" * 40,
                        rag_url="http://rag", rag_api_token="r" * 32, database_path=":memory:", **overrides)
    rag_requests: list[httpx.Request] = []
    core = CoreClient(settings, httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(
        lambda request: httpx.Response(core_status, json={"detail": "core down"}))))
    rag = RagClient(settings, httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(
        rag_handler(rag_requests))))
    http = httpx.AsyncClient(transport=httpx.MockTransport(tts_handler))
    return TestClient(create_app(settings, core, http, Connector(), rag=rag)), rag_requests


def test_failed_core_create_without_a_key_deletes_the_orphan_intake():
    client, rag_requests = _failing_core_client(500)
    body = {"payload": json.dumps({"candidate_name": "An", "cv_text": "Python 3 năm"})}
    assert client.post("/api/sessions", data=body).status_code == 500
    assert [(r.method, r.url.path) for r in rag_requests][-1] == ("DELETE", f"/api/v1/intakes/{ID_Q}")


def test_failed_core_create_of_a_student_deletes_the_intake_and_retries_get_a_fresh_one():
    client, rag_requests = _failing_core_client(500, auth_required=True, auth_jwt_secret="a" * 40)
    token = client.post("/api/auth/login", json={"student_id": "SE123456"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "k9"}
    body = {"payload": json.dumps({"candidate_name": "An", "cv_text": "Python 3 năm"})}
    assert client.post("/api/sessions", data=body, headers=headers).status_code == 500
    assert [(r.method, r.url.path) for r in rag_requests][-1] == ("DELETE", f"/api/v1/intakes/{ID_Q}")
    assert rag_requests[0].headers["Idempotency-Key"] == "SE123456:k9:intake"

    rag_requests.clear()
    client.post("/api/sessions", data=body, headers=headers)
    assert rag_requests[0].headers["Idempotency-Key"] == "SE123456:k9:intake:1"


def test_failed_core_create_with_a_key_and_no_sign_in_keeps_the_intake():
    # Without owners the gateway cannot tell whether an earlier attempt's session uses this intake.
    client, rag_requests = _failing_core_client(500)
    body = {"payload": json.dumps({"candidate_name": "An", "cv_text": "Python 3 năm"})}
    client.post("/api/sessions", data=body, headers={"Idempotency-Key": "k11"})
    assert not any(r.method == "DELETE" for r in rag_requests)


def test_intake_is_kept_when_the_same_request_is_still_running():
    client, rag_requests = _failing_core_client(409)
    body = {"payload": json.dumps({"candidate_name": "An", "cv_text": "Python 3 năm"})}
    assert client.post("/api/sessions", data=body, headers={"Idempotency-Key": "k10"}).status_code == 409
    assert not any(r.method == "DELETE" for r in rag_requests)


def test_proxy_real_ip_is_used_for_rate_limits_when_trusted():
    client, _, _ = make_client(trust_proxy_headers=True, rate_login_per_min=1)
    first = client.post("/api/auth/login", json={"student_id": "SE123456"}, headers={"X-Real-IP": "10.0.0.1"})
    other = client.post("/api/auth/login", json={"student_id": "SE123456"}, headers={"X-Real-IP": "10.0.0.2"})
    again = client.post("/api/auth/login", json={"student_id": "SE123456"}, headers={"X-Real-IP": "10.0.0.1"})
    assert first.status_code != 429 and other.status_code != 429 and again.status_code == 429
