"""REST access to the ID_Q service (RAG_CocDiLam): CV/form intake and knowledge-pack preparation.

The gateway holds the ID_Q bearer token; the browser never sees it. Core later fetches the prepared
pack itself by `id_q` + `pack_id`, so the gateway only forwards the candidate input and the ids.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class RagError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class RagClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(base_url=settings.rag_url.rstrip("/"),
                                                  timeout=settings.rag_timeout_sec)

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {**kwargs.pop("headers", {})}
        if self.settings.rag_api_token:
            headers["Authorization"] = f"Bearer {self.settings.rag_api_token}"
        try:
            response = await self.client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise RagError(504, "Dịch vụ RAG (ID_Q) quá thời gian phản hồi.") from exc
        except httpx.HTTPError as exc:
            raise RagError(502, f"Không kết nối được dịch vụ RAG (ID_Q): {type(exc).__name__}") from exc
        if response.status_code == 204:
            return None
        try:
            data = response.json()
        except ValueError:
            data = {"detail": response.text[:500]}
        if response.status_code >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            # A rejected gateway token is a deployment error, not something the user can fix.
            raise RagError(502 if response.status_code in (401, 403) else response.status_code,
                           "Gateway không được dịch vụ RAG chấp nhận (RAG_API_TOKEN)." if response.status_code in (401, 403)
                           else detail)
        return data

    async def aclose(self) -> None:
        await self.client.aclose()

    # ---- intake ----
    async def create_cv_intake(self, filename: str, content: bytes, content_type: str, metadata: str,
                               idempotency_key: str | None = None) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return await self.request("POST", "/api/v1/intakes/cv", data={"metadata": metadata},
                                  files={"file": (filename, content, content_type)}, headers=headers)

    async def create_form_intake(self, body: dict, idempotency_key: str | None = None) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return await self.request("POST", "/api/v1/intakes/form", json=body, headers=headers)

    async def prepare(self, id_q: str, revision: int) -> dict:
        """Build (or reuse, for the same revision and knowledge version) the intake's knowledge pack."""
        return await self.request("POST", f"/api/v1/intakes/{id_q}/prepare",
                                  json={"expected_revision": revision, "top_k": self.settings.rag_prepare_top_k,
                                        "web_search_mode": self.settings.rag_web_search_mode})
