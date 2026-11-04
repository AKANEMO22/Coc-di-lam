"""Start a real loopback server, exercise authenticated neural API, then stop it."""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main():
    root = Path(__file__).resolve().parents[1]
    token = secrets.token_urlsafe(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "RAG_API_TOKEN": token,
        "RAG_TENANT_ID": "local",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    }
    with tempfile.TemporaryFile(mode="w+b") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.rag.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, timeout=30) as client:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(
                            "Server startup failed: "
                            + log.read().decode(errors="replace")[-3000:]
                        )
                    try:
                        health = client.get("/health")
                        if health.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError("Server startup timed out")
                body = json.loads(
                    (root / "examples/rag_request.json").read_text(encoding="utf-8")
                )
                assert client.post("/rag/context", json=body).status_code == 401
                headers = {"Authorization": "Bearer " + token}
                result = client.post("/rag/context", headers=headers, json=body)
                result.raise_for_status()
                data = result.json()
                assert data["status"] == "ok" and data["selected_questions"]
                assert data["graph_paths"]
                assert all(
                    q["rubric"] and q["sources"] for q in data["selected_questions"]
                )
                assert (
                    client.get(
                        "/rag/trace/" + data["request_id"], headers=headers
                    ).status_code
                    == 200
                )
                followup = client.post(
                    "/rag/follow-up",
                    headers=headers,
                    json={
                        "session_id": data["session_id"],
                        "tenant_id": "local",
                        "answer": "Tôi dùng embedding và retrieval",
                        "top_k": 1,
                    },
                )
                followup.raise_for_status()
                served = {q["question_id"] for q in data["selected_questions"]}
                assert served.isdisjoint(
                    q["question_id"] for q in followup.json()["selected_questions"]
                )
                assert (
                    client.delete(
                        "/rag/session/" + data["session_id"], headers=headers
                    ).status_code
                    == 200
                )
                print(
                    json.dumps(
                        {
                            "http_smoke": "passed",
                            "provider": data["embedding_provider"],
                            "index_version": data["index_version"],
                            "question_ids": sorted(served),
                            "graph_paths": len(data["graph_paths"]),
                            "latency_ms": data["trace"]["total_ms"],
                        }
                    )
                )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
