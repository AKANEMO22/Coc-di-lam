import threading
import time

import httpx
import numpy as np
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.stt import ElevenLabsTranscriber, ResilientTranscriber
from app.tts import ElevenLabsSynthesizer, VieneuSynthesizer
from tests.test_api import FakeVieneu
from tests.test_stt_live import receive_until


class FakeGipformer:
    model_name = "gipformer-fake"
    configured = True

    def __init__(self):
        self.calls: list[int] = []
        self.threads: set[int] = set()

    def is_available(self):
        return True

    def transcribe_pcm(self, pcm: bytes) -> str:
        self.calls.append(len(pcm))
        self.threads.add(threading.get_ident())
        return "xin chào"


def test_live_gipformer_uses_shared_preloaded_instance():
    settings = Settings(_env_file=None, elevenlabs_api_key="k", tts_preload=False, stt_backend="gipformer",
                        gipformer_preload=False)
    gip = FakeGipformer()
    transcriber = ResilientTranscriber(settings, gipformer=gip, online=ElevenLabsTranscriber(settings))
    app = create_app(settings, transcriber, VieneuSynthesizer(settings, model=FakeVieneu()))
    with TestClient(app).websocket_connect("/v1/stt/stream") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["model"] == "gipformer-fake"
        for _ in range(30):  # 3 s of audio arriving faster than real time: interims are throttled
            ws.send_bytes(b"\x00\x00" * 1600)
        ws.send_json({"type": "end"})
        final = receive_until(ws, "final")[-1]
    assert final["text"] == "xin chào"
    assert len(gip.calls) <= 2  # at most one interim + the final
    assert threading.get_ident() not in gip.threads


class FailingVieneu(FakeVieneu):
    def __init__(self, fail_after: int):
        super().__init__()
        self.fail_after = fail_after

    def infer_stream(self, text, voice=None, **_):
        for i in range(4):
            if i == self.fail_after:
                raise RuntimeError("boom")
            yield np.full(self.sample_rate // 4, 0.1, dtype=np.float32)


def _vieneu_with_fallback(model, handler):
    settings = Settings(_env_file=None, elevenlabs_api_key="k", tts_preload=False, tts_voice="Mai Anh")
    fallback = ElevenLabsSynthesizer(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    return settings, VieneuSynthesizer(settings, model=model, fallback=fallback)


def test_vieneu_failure_before_speech_falls_back_at_announced_rate():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, content=b"\x01\x00" * 100)

    model = FailingVieneu(fail_after=0)
    model.sample_rate = 24_000
    _, synth = _vieneu_with_fallback(model, handler)
    audio = b"".join(synth.stream("Xin chào", "Mai Anh"))
    assert urls and "output_format=pcm_24000" in urls[0]
    assert b"\x01\x00" * 100 in audio


def test_vieneu_failure_mid_speech_ends_stream_without_rate_switch():
    urls = []
    _, synth = _vieneu_with_fallback(FailingVieneu(fail_after=2), lambda r: urls.append(r) or httpx.Response(200))
    audio = b"".join(synth.stream("Xin chào", "Mai Anh"))
    assert urls == []
    assert len(audio) // 2 > 2 * 48_000 // 4  # padding + two chunks, stream closed cleanly


class SlowStartSynth:
    """Minimal synthesizer whose stream never yields its first chunk in time."""
    provider = model_name = device = backend = "fake"
    default_voice = "v"
    sample_rate = 16_000
    state = "ready"
    error = None

    def __init__(self):
        self.cancelled = threading.Event()

    def stream(self, text, voice, cancel):
        while not cancel.is_set():
            time.sleep(0.05)
        self.cancelled.set()
        return
        yield b""


def test_tts_stream_first_chunk_timeout_returns_504_and_cancels():
    settings = Settings(_env_file=None, elevenlabs_api_key="k", tts_preload=False, tts_first_chunk_timeout_sec=0.3)
    synth = SlowStartSynth()
    client = TestClient(create_app(settings, synthesizer=synth))
    response = client.post("/v1/tts/stream", json={"text": "Xin chào"})
    assert response.status_code == 504
    assert synth.cancelled.wait(2)


def test_cache_is_thread_safe():
    settings = Settings(_env_file=None, tts_preload=False)
    synth = VieneuSynthesizer(settings, model=FakeVieneu())
    errors = []

    def work(n):
        try:
            for i in range(2000):
                synth._cache_put(f"{n}-{i}", b"x")
                synth._cache_get(f"{n}-{i - 1}")
        except Exception as exc:  # pragma: no cover - only on a race
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(synth._cache) <= 128
