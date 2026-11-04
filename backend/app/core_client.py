"""REST access to the core AI with gateway-signed service tokens."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from app.config import Settings

_TURN_ARTIFACT = re.compile(r"^turns/turn_(\d+)_(candidate|interviewer)\.json$")
_CONTEXT_ARTIFACTS = ("inputs/form_input.json", "context/interview_brief.json", "context/candidate_profile.json")


class CoreError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class CoreClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(base_url=settings.core_url.rstrip("/"),
                                                  timeout=settings.core_timeout_sec)
        self._token: tuple[str, float] | None = None

    def token(self) -> str:
        """Service token (iss=backend, aud=ai-runtime), cached until 5 minutes before expiry."""
        now = time.time()
        if self._token is None or self._token[1] - now < 300:
            exp = int(now) + 3600
            token = jwt.encode({"iss": "backend", "aud": "ai-runtime", "iat": int(now), "exp": exp},
                               self.settings.core_jwt_secret, algorithm=self.settings.core_jwt_algorithm)
            self._token = (token, exp)
        return self._token[0]

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token()}", **kwargs.pop("headers", {})}
        try:
            response = await self.client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise CoreError(504, "Core quá thời gian phản hồi.") from exc
        except httpx.HTTPError as exc:
            raise CoreError(502, f"Không kết nối được core: {type(exc).__name__}") from exc
        try:
            data = response.json()
        except ValueError:
            data = {"detail": response.text[:500]}
        if response.status_code >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            raise CoreError(response.status_code, detail)
        return data

    async def aclose(self) -> None:
        await self.client.aclose()

    # ---- session view ----
    async def artifact(self, session_id: str, path: str) -> Any:
        encoded = "/".join(quote(part, safe="") for part in path.split("/"))
        data = await self.request("GET", f"/api/v1/inspect/runs/{quote(session_id, safe='')}/artifacts/{encoded}")
        content = data.get("content") if isinstance(data, dict) else None
        return content.get("payload", content) if isinstance(content, dict) else content

    async def session_view(self, session_id: str) -> dict:
        """Status, form, profile, plan and transcript of one session in a single payload."""
        detail = await self.request("GET", f"/api/v1/inspect/runs/{quote(session_id, safe='')}")
        artifacts = detail.get("artifacts") or []
        paths = [a["path"] for a in artifacts if _TURN_ARTIFACT.match(a.get("path", "")) or a.get("path") in _CONTEXT_ARTIFACTS]

        async def load(path: str) -> tuple[str, Any]:
            try:
                return path, await self.artifact(session_id, path)
            except CoreError:
                return path, None

        loaded = dict(await asyncio.gather(*(load(path) for path in paths)))
        messages = []
        for path, data in loaded.items():
            match = _TURN_ARTIFACT.match(path)
            if not match or not isinstance(data, dict):
                continue
            message = {"turn": int(match[1]), "role": match[2], "text": str(data.get("content") or ""),
                       "timestamp": data.get("timestamp")}
            if match[2] == "candidate" and data.get("vad_metrics"):
                message["fluency"] = data["vad_metrics"]
            messages.append(message)
        messages.sort(key=lambda m: (m["turn"], m["role"] != "candidate"))
        events = detail.get("events") or []
        return {
            "session_id": session_id,
            "session": detail.get("session") or {},
            "form": loaded.get("inputs/form_input.json"),
            "brief": loaded.get("context/interview_brief.json"),
            "candidate_profile": loaded.get("context/candidate_profile.json"),
            "messages": messages,
            "last_sequence": max((int(e.get("sequence") or 0) for e in events), default=0),
            "execution_origin": detail.get("execution_origin"),
        }
