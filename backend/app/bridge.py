"""Per-session voice bridge: one browser WebSocket <-> core interview stream + speech STT/TTS.

Browser -> gateway (`WS /ws/sessions/{id}?after=<last_sequence>`):
  binary                          PCM16 LE mono 16 kHz, only while the answer key is held
  {"type": "ptt_start"}           key pressed: interviewer audio stops, an STT turn opens
  {"type": "ptt_end"}             key released: the answer is complete -> STT final -> core
  {"type": "ptt_cancel"}          press too short / aborted: discard the audio
  {"type": "text_answer", "text"} typed answer (no audio)
  {"type": "answer_context", "text"} text of documents attached in the room, appended to the next answer
  {"type": "retry_answer"}        resend the pending answer with the same message_id
  {"type": "tts_stop"}            stop the interviewer audio
Gateway -> browser:
  {"type": "ready", "current_turn", "tts_enabled"}
  {"type": "core", "event"}       every core stream event (response_token, turn_complete, busy, ...)
  {"type": "stt_ready"} / {"type": "stt_interim", "text"}
  {"type": "stt_final", "text", "verbatim", "fluency"}   text "" = nothing heard, nothing sent
  {"type": "answer_sent", "message_id", "text", "expected_turn", "fluency"}
  {"type": "answer_accepted", "turn"}
  {"type": "tts_sentence", "turn_id", "text", "sample_rate", "gestures"}, then binary PCM16 at that rate;
                                  `gestures` (avatar tags of that sentence, in order) start with its audio
  {"type": "gesture", "turn_id", "gestures"}   gestures with no sentence of their own (TTS off or
                                  failed, tags after the last sentence): play after the queued audio
  {"type": "tts_idle"}            the interviewer finished speaking (nothing queued or streaming)
  {"type": "tts_stop"}            playback must stop now
  {"type": "error", "detail", "code"?}

Events replayed from before this connection are forwarded as text but never spoken.
Barge-in (ptt_start, or an answer sent while a reply is still streaming) stops the speech, sends
`cancel_generation` to core and mutes the rest of that reply; an answer waits for the interrupted
turn_complete so its expected_turn is the next turn.
Core tokens may carry avatar gesture tags ("[nod] ..."): they are forwarded untouched inside `core`
events (the browser strips them for display) but removed from everything sent to TTS.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any, Callable

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from app.config import Settings
from app.core_client import CoreClient
from app.gestures import GestureStream, strip_gestures
from app.sentences import SentenceSplitter, clean_for_speech

logger = logging.getLogger(__name__)

_END = object()
_BUSY_CODES = {"TURN_IN_PROGRESS", "GENERATION_BUSY"}
_MAX_ANSWER_RETRIES = 40  # ~ up to a minute while the previous turn finishes
_TURN_SETTLE_SEC = 10.0  # wait for a cancelled reply's turn_complete before answering over it
# Core errors the gateway causes itself and handles: not shown to the candidate.
_INTERNAL_CODES = {"NO_ACTIVE_GENERATION"}
TTS_LOOKAHEAD = 3  # sentences synthesized at the same time (the one playing and the next ones)


def default_ws_connect(url: str, **kwargs: Any):
    from websockets.asyncio.client import connect

    return connect(url, max_size=None, open_timeout=10, **kwargs)


class BridgeError(Exception):
    pass


class SttTurn:
    """One spoken answer: audio is queued from the key press, so nothing is lost while STT connects."""

    def __init__(self, bridge: "Bridge"):
        self.bridge = bridge
        self.frames: asyncio.Queue = asyncio.Queue()
        self.final: asyncio.Future = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._run())

    def feed(self, frame: bytes) -> None:
        self.frames.put_nowait(frame)

    def end(self) -> None:
        self.frames.put_nowait(_END)

    def cancel(self) -> None:
        self.task.cancel()
        if not self.final.done():
            self.final.cancel()

    async def _run(self) -> None:
        settings = self.bridge.settings
        start = {"type": "start", "language": settings.stt_language, "fluency": True}
        if settings.speech_service_token:
            start["token"] = settings.speech_service_token
        try:
            async with self.bridge.ws_connect(f"{settings.speech_ws_url}/v1/stt/stream") as ws:
                await ws.send(json.dumps(start))
                ready = json.loads(await ws.recv())
                if ready.get("type") != "ready":
                    raise BridgeError(ready.get("detail") or "STT không sẵn sàng.")
                await self.bridge.send_json({"type": "stt_ready"})

                async def sender() -> None:
                    while True:
                        item = await self.frames.get()
                        if item is _END:
                            await ws.send(json.dumps({"type": "end"}))
                            return
                        await ws.send(item)

                sending = asyncio.create_task(sender())
                try:
                    async for raw in ws:
                        message = json.loads(raw)
                        kind = message.get("type")
                        if kind == "interim":
                            await self.bridge.send_json({"type": "stt_interim", "text": message.get("text", "")})
                        elif kind == "final":
                            if not self.final.done():
                                self.final.set_result(message)
                            return
                        elif kind == "error":
                            raise BridgeError(message.get("detail") or "STT lỗi.")
                finally:
                    sending.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.final.done():
                self.final.set_exception(exc if isinstance(exc, BridgeError) else BridgeError(
                    f"Không kết nối được STT: {type(exc).__name__}"))
        finally:
            if not self.final.done():
                self.final.set_exception(BridgeError("STT đóng trước khi trả kết quả."))


class Bridge:
    def __init__(self, browser: WebSocket, session_id: str, settings: Settings, core: CoreClient,
                 http: httpx.AsyncClient, ws_connect: Callable = default_ws_connect):
        self.browser = browser
        self.session_id = session_id
        self.settings = settings
        self.core = core
        self.http = http
        self.ws_connect = ws_connect
        self.core_ws: Any = None
        self.connected_at = 0.0
        self.after = 0
        self.current_turn = 0
        self.pending: dict | None = None
        self.retries = 0
        self.retry_timer: asyncio.Task | None = None
        self.stt: SttTurn | None = None
        self.recording = False
        # Sentences in speaking order. Each one's TTS request starts as soon as it is queued, so the
        # next sentence is ready when the current one ends (no gap while the speech service warms up).
        self.tts_queue: asyncio.Queue = asyncio.Queue()
        self._tts_slots = asyncio.Semaphore(TTS_LOOKAHEAD)
        self.tts_generation = 0
        self.splitters: dict[str, SentenceSplitter] = {}
        # Per turn: tag filter for the streamed tokens and gestures waiting for their sentence.
        # Kept across tts_stop so a tag cut in half by barge-in never leaks into speech.
        self.gesture_streams: dict[str, GestureStream] = {}
        self.pending_gestures: dict[str, list[str]] = {}
        # Turns whose reply is still streaming (tokens seen, no turn_complete yet), and turns silenced by
        # barge-in / tts_stop: their remaining tokens are shown but never spoken.
        self.streaming: set[str] = set()
        self.muted: set[str] = set()
        self._turn_finished = asyncio.Event()
        # Text of documents attached in the room, sent along with the next answer.
        self.answer_context = ""
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # ---- plumbing ----
    async def send_json(self, message: dict) -> None:
        async with self._send_lock:
            await self.browser.send_text(json.dumps(message, ensure_ascii=False))

    async def send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self.browser.send_bytes(data)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def run(self, after: int = 0) -> None:
        await self.browser.accept()
        self.after = after
        try:
            status = await self.core.request("GET", f"/api/v1/sessions/{self.session_id}")
            self.current_turn = int(status.get("current_turn") or 0)
            await self._open_core()
        except Exception as exc:
            await self._fail(f"Không mở được luồng phỏng vấn: {getattr(exc, 'detail', exc)}")
            return
        await self.send_json({"type": "ready", "current_turn": self.current_turn,
                              "status": status.get("status"), "tts_enabled": self.settings.tts_enabled})
        workers = [asyncio.create_task(self._pump_browser()), asyncio.create_task(self._pump_core()),
                   asyncio.create_task(self._tts_worker())]
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    logger.warning("Bridge %s ended: %r", self.session_id, exc)
                    await self._fail(f"Luồng phỏng vấn bị ngắt: {exc}")
        finally:
            for task in [*workers, *self._tasks, self.retry_timer]:
                if task is not None:
                    task.cancel()
            if self.stt is not None:
                self.stt.cancel()
            with contextlib.suppress(Exception):
                await self.core_ws.close()
            with contextlib.suppress(Exception):
                await self.browser.close()

    async def _fail(self, detail: str, code: str | None = None) -> None:
        with contextlib.suppress(Exception):
            await self.send_json({"type": "error", "detail": detail, "code": code, "fatal": True})
            await self.browser.close(code=1011)

    async def _open_core(self) -> None:
        url = f"{self.settings.core_ws_url}/ws/sessions/{self.session_id}/stream"
        self.core_ws = await self.ws_connect(url)
        await self.core_ws.send(json.dumps({"type": "auth", "token": self.core.token()}))
        deadline = time.monotonic() + 10
        while True:
            raw = await asyncio.wait_for(self.core_ws.recv(), max(0.1, deadline - time.monotonic()))
            event = json.loads(raw)
            if event.get("type") == "auth_ok":
                break
            if event.get("type") == "error":
                raise BridgeError((event.get("payload") or {}).get("code") or "Core từ chối xác thực.")
        self.connected_at = time.time()
        await self.core_ws.send(json.dumps({"type": "replay", "last_sequence": self.after}))

    async def _send_core(self, message: dict) -> None:
        await self.core_ws.send(json.dumps(message, ensure_ascii=False))

    # ---- browser ----
    async def _pump_browser(self) -> None:
        while True:
            message = await self.browser.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                if self.recording and self.stt is not None:
                    self.stt.feed(message["bytes"])
                continue
            try:
                command = json.loads(message.get("text") or "{}")
            except ValueError:
                continue
            kind = command.get("type")
            if kind == "ptt_start":
                await self._barge_in()
                if self.stt is not None:
                    self.stt.cancel()
                self.stt, self.recording = SttTurn(self), True
            elif kind in ("ptt_end", "ptt_cancel") and self.stt is not None:
                turn, self.stt, self.recording = self.stt, None, False
                if kind == "ptt_cancel":
                    turn.cancel()
                else:
                    turn.end()
                    self._spawn(self._finish_answer(turn))
            elif kind == "text_answer":
                text = str(command.get("text") or "").strip()
                if text:
                    self._spawn(self._submit(text, None))
            elif kind == "answer_context":
                self.answer_context = str(command.get("text") or "").strip()[:self.settings.max_answer_chars]
            elif kind == "retry_answer" and self.pending is not None:
                self.retries = 0
                await self._send_core({**self.pending, "type": "candidate_answer"})
            elif kind == "tts_stop":
                await self._stop_tts()

    async def _finish_answer(self, turn: SttTurn) -> None:
        try:
            final = await asyncio.wait_for(turn.final, self.settings.stt_final_timeout_sec)
        except asyncio.CancelledError:
            return
        except (asyncio.TimeoutError, BridgeError) as exc:
            turn.cancel()
            await self.send_json({"type": "error", "code": "STT_FAILED",
                                  "detail": str(exc) or "STT không trả kết quả kịp."})
            return
        text = (final.get("text") or "").strip()
        await self.send_json({"type": "stt_final", "text": text, "verbatim": final.get("verbatim"),
                              "fluency": final.get("fluency")})
        if text:
            await self._submit(text, final.get("fluency"))

    async def _barge_in(self) -> None:
        """The candidate takes the floor: silence the interviewer and stop the reply still being generated."""
        await self._stop_tts()
        if self.streaming:
            await self._send_core({"type": "cancel_generation"})

    async def _submit(self, text: str, fluency: dict | None) -> None:
        if self.pending is not None:
            await self.send_json({"type": "error", "code": "ANSWER_PENDING",
                                  "detail": "Câu trả lời trước chưa được core nhận; đợi hoặc gửi lại."})
            return
        if self.streaming:
            # Answering over a reply still being generated: stop it and wait for its (interrupted)
            # turn_complete, otherwise expected_turn would point at that turn and the answer be misfiled.
            await self._barge_in()
            self._turn_finished.clear()
            try:
                await asyncio.wait_for(self._turn_finished.wait(), _TURN_SETTLE_SEC)
            except asyncio.TimeoutError:
                self.streaming.clear()  # a reply that never completed (e.g. replayed from a crashed run)
            if self.pending is not None:  # another answer got in while waiting
                return
        answer = text
        if self.answer_context:
            room = self.settings.max_answer_chars - len(text) - 2
            if room > 0:
                answer = f"{text}\n\n{self.answer_context[:room]}"
            self.answer_context = ""
        self.pending = {"message_id": uuid.uuid4().hex, "candidate_answer": answer,
                        "expected_turn": self.current_turn + 1, "vad_metrics": fluency or {}}
        self.retries = 0
        await self._send_core({**self.pending, "type": "candidate_answer"})
        await self.send_json({"type": "answer_sent", "message_id": self.pending["message_id"], "text": text,
                              "expected_turn": self.pending["expected_turn"], "fluency": fluency})

    def _schedule_retry(self, delay_ms: float) -> None:
        if self.pending is None:
            return
        if self.retries >= _MAX_ANSWER_RETRIES:
            self._spawn(self.send_json({"type": "error", "code": "ANSWER_NOT_ACCEPTED", "retryable": True,
                                        "detail": "Core vẫn bận nên chưa nhận câu trả lời. Bấm gửi lại để thử tiếp."}))
            return
        self.retries += 1

        async def resend() -> None:
            await asyncio.sleep(max(0.15, min(2.0, delay_ms / 1000)))
            if self.pending is not None:
                await self._send_core({**self.pending, "type": "candidate_answer"})

        if self.retry_timer is not None:
            self.retry_timer.cancel()
        self.retry_timer = asyncio.create_task(resend())

    # ---- core ----
    async def _pump_core(self) -> None:
        async for raw in self.core_ws:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            kind = event.get("type")
            if kind in ("pong", "auth", "auth_ok") or int(event.get("sequence") or 0) and int(event["sequence"]) <= self.after:
                continue
            payload = event.get("payload") or {}
            if kind == "error" and payload.get("code") in _INTERNAL_CODES:
                continue
            await self.send_json({"type": "core", "event": event})
            live = (_timestamp(event.get("timestamp")) or time.time()) >= self.connected_at - 1.0
            if kind == "response_token":
                turn_id = str(event.get("turn_id") or "")
                self.streaming.add(turn_id)
                if live and turn_id not in self.muted:
                    await self._speak_tokens(turn_id, str(payload.get("token") or ""))
            elif kind == "turn_complete":
                await self._turn_complete(event, payload, live)
            elif kind in ("busy", "error", "generation_cancelled") and self.pending is not None:
                code = payload.get("code")
                if kind == "busy" or code in _BUSY_CODES:
                    self._schedule_retry(float(payload.get("retry_after_ms") or 500))

    async def _turn_complete(self, event: dict, payload: dict, live: bool) -> None:
        turn_number = int(payload.get("turn_number") or 0)
        self.current_turn = max(self.current_turn, turn_number)
        if self.pending is not None and turn_number >= self.pending["expected_turn"]:
            self.pending = None
            await self.send_json({"type": "answer_accepted", "turn": turn_number})
        turn_id = str(event.get("turn_id") or payload.get("turn_id") or "")
        self.streaming.discard(turn_id)
        self._turn_finished.set()
        muted = turn_id in self.muted
        self.muted.discard(turn_id)
        splitter = self.splitters.pop(turn_id, None)
        stream = self.gesture_streams.pop(turn_id, None)
        if not live or muted or payload.get("interrupted"):
            self.pending_gestures.pop(turn_id, None)
            return
        if stream is not None:
            splitter = splitter or SentenceSplitter()
            await self._consume(turn_id, splitter, stream.flush())
            for sentence in splitter.flush():
                await self._enqueue_tts(turn_id, sentence)
        else:  # no tokens were streamed for this turn: speak the whole reply
            text = strip_gestures(str(payload.get("interviewer_response") or ""))
            splitter = SentenceSplitter()
            for sentence in [*splitter.feed(text + " "), *splitter.flush()]:
                await self._enqueue_tts(turn_id, sentence)
        leftover = self.pending_gestures.pop(turn_id, None)
        if leftover and self.settings.tts_enabled:  # tags after the last sentence: play once it is spoken
            self._queue_tts(turn_id, None, leftover)
        elif leftover:
            await self._send_gestures(turn_id, leftover)

    # ---- TTS ----
    async def _speak_tokens(self, turn_id: str, token: str) -> None:
        stream = self.gesture_streams.setdefault(turn_id, GestureStream())
        splitter = self.splitters.setdefault(turn_id, SentenceSplitter())
        await self._consume(turn_id, splitter, stream.feed(token))

    async def _consume(self, turn_id: str, splitter: SentenceSplitter, segments: list) -> None:
        """Speak tag-free text; a gesture waits in `pending_gestures` for the next sentence."""
        for segment in segments:
            if isinstance(segment, str):
                for sentence in splitter.feed(segment):
                    await self._enqueue_tts(turn_id, sentence)
            else:
                self.pending_gestures.setdefault(turn_id, []).append(segment[1])

    async def _enqueue_tts(self, turn_id: str, text: str) -> None:
        # A gesture tagged anywhere in a sentence starts with that sentence's audio.
        gestures = self.pending_gestures.pop(turn_id, [])
        speech = clean_for_speech(text)
        if self.settings.tts_enabled and (speech or gestures):
            self._queue_tts(turn_id, text if speech else None, gestures)
        elif gestures:
            await self._send_gestures(turn_id, gestures)

    async def _send_gestures(self, turn_id: str, gestures: list[str]) -> None:
        await self.send_json({"type": "gesture", "turn_id": turn_id, "gestures": gestures})

    def _queue_tts(self, turn_id: str, text: str | None, gestures: list[str]) -> None:
        """Queue a sentence (or, with text None, gestures after the last one) and start its synthesis."""
        chunks: asyncio.Queue = asyncio.Queue()
        fetch = self._spawn(self._fetch_tts(text, chunks)) if text is not None else None
        self.tts_queue.put_nowait((self.tts_generation, turn_id, text, gestures, chunks, fetch))

    async def _stop_tts(self) -> None:
        # Tokens still to come for a reply being spoken must not restart its speech mid-sentence.
        self.muted |= self.streaming | set(self.splitters)
        self.tts_generation += 1
        while not self.tts_queue.empty():
            *_, fetch = self.tts_queue.get_nowait()
            if fetch is not None:
                fetch.cancel()
        self.splitters.clear()
        await self.send_json({"type": "tts_stop"})

    async def _fetch_tts(self, text: str, chunks: asyncio.Queue) -> None:
        """Synthesize one sentence into `chunks`: ("start", rate), ("chunk", bytes)..., then ("end", None);
        ("error", detail) instead of "start" when it failed. Always ends with ("end", None)."""
        headers = {"Authorization": f"Bearer {self.settings.speech_service_token}"} if self.settings.speech_service_token else {}
        body_voice = {"voice": self.settings.tts_voice} if self.settings.tts_voice else {}
        try:
            async with self._tts_slots:
                async with self.http.stream("POST", f"{self.settings.speech_url.rstrip('/')}/v1/tts/stream",
                                            json={"text": text, **body_voice}, headers=headers) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode("utf-8", "replace")[:300]
                        chunks.put_nowait(("error", detail))
                        return
                    chunks.put_nowait(("start", int(response.headers.get("x-sample-rate") or 24_000)))
                    async for chunk in response.aiter_bytes():
                        chunks.put_nowait(("chunk", chunk))
        except httpx.HTTPError as exc:
            chunks.put_nowait(("error", f"Không gọi được TTS: {type(exc).__name__}"))
        finally:
            chunks.put_nowait(("end", None))

    async def _tts_worker(self) -> None:
        while True:
            generation, turn_id, text, gestures, chunks, fetch = await self.tts_queue.get()
            if generation != self.tts_generation:
                if fetch is not None:
                    fetch.cancel()
                continue
            # text None = gestures after the turn's last sentence, kept in order behind its audio.
            spoken = text is not None and await self._speak(generation, turn_id, text, gestures, chunks)
            if fetch is not None and generation != self.tts_generation:
                fetch.cancel()
            if gestures and not spoken and generation == self.tts_generation:
                await self._send_gestures(turn_id, gestures)
            if self.tts_queue.empty() and not self.splitters and generation == self.tts_generation:
                await self.send_json({"type": "tts_idle"})

    async def _speak(self, generation: int, turn_id: str, text: str, gestures: list[str],
                     chunks: asyncio.Queue) -> bool:
        """Forward one synthesized sentence to the browser; False when TTS failed before any audio."""
        kind, value = await chunks.get()
        if kind != "start":
            if kind == "error":
                await self.send_json({"type": "error", "code": "TTS_FAILED", "detail": value})
            return False
        await self.send_json({"type": "tts_sentence", "turn_id": turn_id, "text": text,
                              "sample_rate": value, "gestures": gestures})
        carry = b""
        while True:
            kind, value = await chunks.get()
            if kind != "chunk" or generation != self.tts_generation:
                return True
            data = carry + value
            cut = len(data) - len(data) % 2  # browsers need whole 16-bit samples
            carry = data[cut:]
            if cut:
                await self.send_bytes(data[:cut])


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        from datetime import datetime

        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return None
