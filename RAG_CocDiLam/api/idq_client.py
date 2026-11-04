"""Private HTTP client for retrieving a prepared ID_Q snapshot from RAG."""

import json
import os
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class IDQServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def fetch_idq_bundle(id_q: str, pack_id: str | None = None) -> dict:
    base_url = os.getenv("RAG_IDQ_BASE_URL", "").rstrip("/")
    token = os.getenv("RAG_IDQ_API_TOKEN") or os.getenv("RAG_API_TOKEN", "")
    core_token = os.getenv("RAG_CORE_TOKEN") or token
    if not base_url or not token or not core_token:
        raise IDQServiceError(503, "ID_Q service credentials are not configured")

    query = urlencode({"pack_id": pack_id}) if pack_id else ""
    url = f"{base_url}/api/v1/internal/intakes/{quote(id_q, safe='')}/knowledge_pack"
    if query:
        url += "?" + query
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-RAG-Core-Token": core_token,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise IDQServiceError(404, "ID_Q intake or knowledge pack was not found") from exc
        if exc.code == 409:
            raise IDQServiceError(409, "Prepare the current ID_Q revision before creating a session") from exc
        if exc.code in {401, 403}:
            raise IDQServiceError(502, "ID_Q service credentials were rejected") from exc
        raise IDQServiceError(502, "ID_Q service could not provide the knowledge pack") from exc
    except Exception as exc:
        raise IDQServiceError(502, "ID_Q service is unavailable") from exc
    if len(raw) > 4 * 1024 * 1024:
        raise IDQServiceError(502, "ID_Q knowledge pack exceeds the supported size")
    try:
        bundle = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IDQServiceError(502, "ID_Q service returned an invalid knowledge pack") from exc
    if not isinstance(bundle, dict) or bundle.get("id_q") != id_q:
        raise IDQServiceError(502, "ID_Q service returned a mismatched intake")
    if pack_id and bundle.get("pack_id") != pack_id:
        raise IDQServiceError(409, "The requested ID_Q knowledge pack is unavailable")
    if not isinstance(bundle.get("candidate_profile"), dict) or not isinstance(bundle.get("knowledge_pack"), dict):
        raise IDQServiceError(502, "ID_Q service returned an incomplete knowledge pack")
    return bundle
