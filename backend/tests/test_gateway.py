import asyncio
import json
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core_client import CoreClient
from app.main import create_app
from app.rag_client import RagClient
from app.sentences import SentenceSplitter


# ---- sentence splitting ----

def test_splitter_emits_sentences_while_streaming():
    splitter = SentenceSplitter()
    out = []
    for token in ["Chào ", "bạn. ", "Hãy giới thiệu ", "về **dự án** gần nhất? ", "Bạn dùng gì"]:
        out += splitter.feed(token)
    assert out == ["Chào bạn.", "Hãy giới thiệu về dự án gần nhất?"]
    assert splitter.flush() == ["Bạn dùng gì"]


def test_splitter_does_not_speak_a_tiny_first_sentence_alone():
    splitter = SentenceSplitter()
    assert splitter.feed("Vâng. ") == []
    assert splitter.feed("Cảm ơn bạn đã chia sẻ. ") == ["Vâng. Cảm ơn bạn đã chia sẻ."]


# ---- fakes ----

class FakeSocket:
    """websockets-like connection: `await connect(url)` or `async with connect(url)`."""

    def __init__(self, on_send):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.on_send = on_send

    async def send(self, message):
        self.sent.append(message)
        await self.on_send(self, message)

    async def recv(self):
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    def push(self, message: dict):
        self.inbox.put_nowait(json.dumps(message, ensure_ascii=False))

    async def close(self):
        self.inbox.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


class Connector:
    def __init__(self):
        self.core: FakeSocket | None = None
        self.stt: list[FakeSocket] = []
        self.core_answers: list[dict] = []
        self.stt_audio = bytearray()
        self.reply_tokens = ["Cảm ơn bạn đã ", "chia sẻ. ", "Câu tiếp theo nhé?"]

    def __call__(self, url, **_):
        connector = self

        class Pending:
            def __await__(self):
                return connector._open(url).__await__()

            async def __aenter__(self):
                self.socket = await connector._open(url)
                return self.socket

            async def __aexit__(self, *exc):
                await self.socket.close()

        return Pending()

    async def _open(self, url):
        if "/ws/sessions/" in url:
            self.core = FakeSocket(self._core_send)
            return self.core
        socket = FakeSocket(self._stt_send)
        self.stt.append(socket)
        return socket

    async def _core_send(self, socket, message):
        data = json.loads(message)
        if data["type"] == "auth":
            socket.push({"type": "auth_ok"})
        elif data["type"] == "candidate_answer":
            self.core_answers.append(data)
            now = time.time()
            for i, token in enumerate(self.reply_tokens):
                socket.push({"type": "response_token", "turn_id": "turn_001", "sequence": 10 + i,
                             "timestamp": now, "payload": {"token": token}})
            socket.push({"type": "turn_complete", "turn_id": "turn_001", "sequence": 20, "timestamp": now,
                         "payload": {"turn_number": 1, "interviewer_response": "Cảm ơn bạn đã chia sẻ. Câu tiếp theo nhé?"}})

    async def _stt_send(self, socket, message):
        if isinstance(message, bytes):
            self.stt_audio.extend(message)
            return
        data = json.loads(message)
        if data["type"] == "start":
            assert data["fluency"] is True
            socket.push({"type": "ready"})
        elif data["type"] == "end":
            socket.push({"type": "interim", "text": "ừm em dùng"})
            socket.push({"type": "final", "text": "Em dùng Redis.", "verbatim": "Ừm, em dùng Redis.",
                         "fluency": {"filler_count": 1, "pause_count": 0, "speech_duration_ms": 1200}})


def core_handler(captured):
    def handler(request: httpx.Request):
        captured.append(request)
        path = request.url.path
        if path.endswith("/api/v1/sessions/sess_1"):
            return httpx.Response(200, json={"session_id": "sess_1", "status": "STAGE_ACTIVE", "current_turn": 0})
        if path.endswith(("/api/v1/sessions/from-idq", "/api/v1/sessions/from-idq/upload")):
            return httpx.Response(202, json={"session_id": "sess_new", "status": "PROFILING"})
        return httpx.Response(404, json={"detail": "nope"})
    return handler


ID_Q = "iq_" + "a" * 32
PACK = "pack_" + "b" * 32


def rag_handler(captured):
    def handler(request: httpx.Request):
        captured.append(request)
        path = request.url.path
        if path in ("/api/v1/intakes/cv", "/api/v1/intakes/form"):
            return httpx.Response(201, json={"id_q": ID_Q, "revision": 1, "input_type": path.rsplit("/", 1)[1],
                                             "status": "ready_for_review"})
        if path == f"/api/v1/intakes/{ID_Q}/prepare":
            return httpx.Response(201, json={"pack_id": PACK, "id_q": ID_Q, "intake_revision": 1, "status": "ready",
                                             "limitations": ["No company JD"]})
        if path == f"/api/v1/intakes/{ID_Q}":
            return httpx.Response(200, json={"id_q": ID_Q, "revision": 1})
        return httpx.Response(404, json={"detail": "Intake not found"})
    return handler


def tts_handler(request: httpx.Request):
    assert request.url.path == "/v1/tts/stream"
    return httpx.Response(200, content=b"\x01\x00" * 999 + b"\x02", headers={"x-sample-rate": "24000"})


def make_client(tts=tts_handler, **overrides):
    settings = Settings(_env_file=None, core_url="http://core", speech_url="http://speech",
                        core_jwt_secret="s" * 40, rag_url="http://rag", rag_api_token="r" * 32, **overrides)
    captured: list[httpx.Request] = []
    core = CoreClient(settings, httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(core_handler(captured))))
    connector = Connector()
    http = httpx.AsyncClient(transport=httpx.MockTransport(tts))
    rag_requests: list[httpx.Request] = []
    rag = RagClient(settings, httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(rag_handler(rag_requests))))
    client = TestClient(create_app(settings, core, http, connector, rag=rag))
    client.rag_requests = rag_requests
    return client, captured, connector


# ---- REST ----

def test_create_session_sends_cv_to_rag_then_core_by_id():
    client, captured, _ = make_client()
    payload = {"candidate_name": "An", "target_role": "Backend Developer", "target_company": "FPT", "seniority": "junior",
               "interview_language": "vi", "duration_minutes": 30, "job_description": "Python, FastAPI"}
    response = client.post("/api/sessions", data={"payload": json.dumps(payload)},
                           files={"cv_file": ("cv.pdf", b"%PDF-1.4", "application/pdf")},
                           headers={"Idempotency-Key": "k1"})
    assert response.status_code == 202
    data = response.json()
    assert data["session_id"] == "sess_new" and data["id_q"] == ID_Q and data["pack_id"] == PACK
    assert data["limitations"] == ["No company JD"]

    intake, prepare = client.rag_requests
    assert intake.url.path == "/api/v1/intakes/cv" and intake.headers["Authorization"] == "Bearer " + "r" * 32
    assert intake.headers["Idempotency-Key"] == "k1:intake"
    body = intake.content.decode("latin-1")
    assert "cv.pdf" in body and '"target_seniority": "junior"' in body and '"company_name": "FPT"' in body
    assert json.loads(prepare.content)["expected_revision"] == 1

    request = captured[-1]
    assert request.url.path == "/api/v1/sessions/from-idq" and request.headers["Idempotency-Key"] == "k1"
    claims = jwt.decode(request.headers["Authorization"].split()[1], "s" * 40, algorithms=["HS256"], audience="ai-runtime")
    assert claims["iss"] == "backend"
    sent = json.loads(request.content)
    assert sent["id_q"] == ID_Q and sent["pack_id"] == PACK and sent["candidate_name"] == "An"
    assert sent["job_description"] == "Python, FastAPI" and sent["metadata"]["client"] == "interview-frontend"


def test_pasted_cv_text_is_sent_as_a_text_cv():
    client, _, _ = make_client()
    response = client.post("/api/sessions", data={"payload": json.dumps({"candidate_name": "An", "cv_text": "Python 3 năm"})})
    assert response.status_code == 202
    intake = client.rag_requests[0]
    assert intake.url.path == "/api/v1/intakes/cv" and b"Python 3 n" in intake.content and b"cv.txt" in intake.content


def test_create_session_without_cv_uses_the_form_intake():
    client, captured, _ = make_client()
    payload = {"candidate_name": "An", "target_role": "QA", "profile": {"skills": ["Selenium", "SQL"], "experience_years": 2,
                                                                       "summary": "QA manual"}}
    response = client.post("/api/sessions", data={"payload": json.dumps(payload)})
    assert response.status_code == 202
    form = json.loads(client.rag_requests[0].content)
    assert client.rag_requests[0].url.path == "/api/v1/intakes/form"
    assert form["candidate_profile"] == {"candidate_name": "An", "summary": "QA manual", "experience_years": 2.0,
                                         "skills": ["Selenium", "SQL"]}
    assert form["interview_target"] == {"role_title": "QA"}
    assert captured[-1].url.path == "/api/v1/sessions/from-idq"


def test_jd_file_goes_to_core_upload_with_the_pack_ids():
    client, captured, _ = make_client()
    response = client.post("/api/sessions", data={"payload": json.dumps({"candidate_name": "An", "cv_text": "Python"})},
                           files={"job_description_file": ("jd.docx", b"PK", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert response.status_code == 202
    request = captured[-1]
    assert request.url.path == "/api/v1/sessions/from-idq/upload"
    body = request.content.decode("latin-1")
    assert "jd.docx" in body and ID_Q in body and PACK in body


def test_create_session_needs_a_cv_or_a_profile():
    client, captured, _ = make_client()
    response = client.post("/api/sessions", data={"payload": json.dumps({"candidate_name": "An"})})
    assert response.status_code == 422 and not client.rag_requests and not captured


def test_rag_errors_keep_status_and_detail():
    client, _, _ = make_client()
    response = client.get("/api/intakes/iq_missing")
    assert response.status_code == 404 and response.json()["detail"] == "Intake not found"


def test_core_errors_keep_status_and_detail():
    client, _, _ = make_client()
    response = client.get("/api/sessions/missing/report")
    assert response.status_code == 404 and response.json() == {"detail": "nope"}


# ---- voice bridge ----

def receive_until(ws, kind, binary=None):
    messages = []
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            if binary is not None:
                binary.extend(message["bytes"])
            continue
        data = json.loads(message["text"])
        messages.append(data)
        if data["type"] == kind:
            return messages


def test_push_to_talk_turn_reaches_core_with_fluency_and_reply_is_spoken():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1?after=5") as ws:
        ready = receive_until(ws, "ready")[-1]
        assert ready["current_turn"] == 0
        assert json.loads(connector.core.sent[1]) == {"type": "replay", "last_sequence": 5}

        ws.send_json({"type": "ptt_start"})
        ws.send_bytes(b"\x00\x01" * 1600)  # sent before STT is ready: must be queued, not lost
        ws.send_bytes(b"\x00\x02" * 1600)
        ws.send_json({"type": "ptt_end"})

        messages = receive_until(ws, "answer_sent")
        final = next(m for m in messages if m["type"] == "stt_final")
        assert final["text"] == "Em dùng Redis." and final["fluency"]["filler_count"] == 1
        assert len(connector.stt_audio) == 6400

        audio = bytearray()
        messages = receive_until(ws, "tts_idle", audio)
    answer = connector.core_answers[0]
    assert answer["candidate_answer"] == "Em dùng Redis." and answer["expected_turn"] == 1
    assert answer["vad_metrics"]["filler_count"] == 1
    assert any(m["type"] == "answer_accepted" and m["turn"] == 1 for m in messages)
    spoken = [m["text"] for m in messages if m["type"] == "tts_sentence"]
    assert spoken == ["Cảm ơn bạn đã chia sẻ.", "Câu tiếp theo nhé?"]
    assert all(m["sample_rate"] == 24000 for m in messages if m["type"] == "tts_sentence")
    assert len(audio) % 2 == 0 and len(audio) == 2 * 1998  # odd trailing byte of each clip dropped


def test_gesture_tags_never_reach_tts_and_ride_on_their_sentence():
    client, _, connector = make_client()
    connector.reply_tokens = ["[smi", "le] Cảm ơn bạn đã ", "chia sẻ. [no", "d]] Câu tiếp theo nhé? [write]"]
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        ws.send_json({"type": "text_answer", "text": "Em làm backend."})
        messages = receive_until(ws, "gesture")
    sentences = [m for m in messages if m["type"] == "tts_sentence"]
    assert [m["text"] for m in sentences] == ["Cảm ơn bạn đã chia sẻ.", "Câu tiếp theo nhé?"]
    assert [m["gestures"] for m in sentences] == [["smile"], ["nod"]]
    assert messages[-1]["gestures"] == ["write"]  # after the last sentence, behind its audio
    tokens = "".join(m["event"]["payload"]["token"] for m in messages
                     if m["type"] == "core" and m["event"]["type"] == "response_token")
    assert tokens.startswith("[smile]")  # the browser still gets the raw stream


def test_next_sentences_are_synthesized_while_the_first_plays():
    started: list[str] = []

    async def slow_tts(request: httpx.Request):
        started.append(json.loads(request.content)["text"])
        await asyncio.sleep(0.4)  # speech service latency before the first audio
        return httpx.Response(200, content=b"\x01\x00" * 100, headers={"x-sample-rate": "24000"})

    client, _, connector = make_client(tts=slow_tts)
    connector.reply_tokens = ["Câu thứ nhất khá dài. ", "Câu thứ hai cũng vậy. ", "Câu thứ ba hỏi gì nhỉ?"]
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        begin = time.monotonic()
        ws.send_json({"type": "text_answer", "text": "Em làm backend."})
        messages = receive_until(ws, "tts_idle")
        elapsed = time.monotonic() - begin
    spoken = [m["text"] for m in messages if m["type"] == "tts_sentence"]
    assert spoken == ["Câu thứ nhất khá dài.", "Câu thứ hai cũng vậy.", "Câu thứ ba hỏi gì nhỉ?"]  # still in order
    assert elapsed < 1.0  # three 0.4 s syntheses overlapped instead of 1.2 s back to back


def test_gestures_without_tts_are_sent_directly():
    client, _, connector = make_client(tts_enabled=False)
    connector.reply_tokens = ["[nod] Cảm ơn bạn. ", "[think] Vì sao vậy?"]
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        ws.send_json({"type": "text_answer", "text": "Em làm backend."})
        first = receive_until(ws, "gesture")
        second = receive_until(ws, "gesture")
    assert first[-1]["gestures"] == ["nod"] and second[-1]["gestures"] == ["think"]
    assert not any(m["type"] == "tts_sentence" for m in first + second)


def test_replayed_events_are_shown_but_not_spoken():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        old = time.time() - 120
        connector.core.push({"type": "response_token", "turn_id": "turn_000", "sequence": 3, "timestamp": old,
                             "payload": {"token": "Câu hỏi cũ đã hỏi từ trước. "}})
        connector.core.push({"type": "turn_complete", "turn_id": "turn_000", "sequence": 4, "timestamp": old,
                             "payload": {"turn_number": 0, "interviewer_response": "Câu hỏi cũ đã hỏi từ trước."}})
        ws.send_json({"type": "text_answer", "text": "Em làm backend."})
        messages = receive_until(ws, "tts_idle")
    spoken = [m["text"] for m in messages if m["type"] == "tts_sentence"]
    assert "Câu hỏi cũ đã hỏi từ trước." not in spoken
    assert any(m["type"] == "core" and m["event"]["sequence"] == 3 for m in messages)


def test_busy_core_gets_the_same_answer_again():
    client, _, connector = make_client()
    with client.websocket_connect("/ws/sessions/sess_1") as ws:
        receive_until(ws, "ready")
        original = connector._core_send
        calls = []

        async def busy_once(socket, message):
            data = json.loads(message)
            if data["type"] == "candidate_answer" and not calls:
                calls.append(data)
                socket.push({"type": "busy", "payload": {"retry_after_ms": 150}})
                return
            await original(socket, message)

        connector.core.on_send = busy_once
        ws.send_json({"type": "text_answer", "text": "Em làm backend."})
        receive_until(ws, "answer_accepted")
    assert calls[0]["message_id"] == connector.core_answers[0]["message_id"]
