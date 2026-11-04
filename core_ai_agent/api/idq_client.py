"""Private HTTP client for retrieving a prepared ID_Q snapshot from the RAG_CocDiLam service.

Core không tự chạy RAG: rubric, question bank, hồ sơ ứng viên và JD của một phiên
được lấy nguyên khối từ endpoint nội bộ của ID_Q theo id_q (+ pack_id), rồi ghim
vào workspace. Lỗi phía RAG được ánh xạ sang mã HTTP an toàn, không lộ token.
"""

import json
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from config.settings import Settings
from core.profiler.idq import rubric_source_scale

MAX_BUNDLE_BYTES = 4 * 1024 * 1024


class IDQServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def fetch_idq_bundle(settings: Settings, id_q: str, pack_id: str | None = None) -> dict:
    """GET /api/v1/internal/intakes/{id_q}/knowledge_pack (blocking; run it in a thread)."""
    base_url = settings.RAG_IDQ_BASE_URL.rstrip("/")
    token = settings.RAG_IDQ_API_TOKEN
    core_token = settings.RAG_CORE_TOKEN or token
    if not base_url or not token:
        raise IDQServiceError(503, "ID_Q service credentials are not configured")

    url = f"{base_url}/api/v1/internal/intakes/{quote(id_q, safe='')}/knowledge_pack"
    if pack_id:
        url += "?" + urlencode({"pack_id": pack_id})
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
        with urlopen(request, timeout=settings.RAG_IDQ_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BUNDLE_BYTES + 1)
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
    if len(raw) > MAX_BUNDLE_BYTES:
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
    return validate_idq_bundle(bundle)


def _object_section(container: dict, key: str) -> dict:
    """A contract section that must be a JSON object; null/missing is normalized to {}."""
    value = container.get(key)
    if value is None:
        container[key] = {}
        return container[key]
    if not isinstance(value, dict):
        raise IDQServiceError(502, f"ID_Q knowledge pack field '{key}' must be an object")
    return value


def validate_idq_bundle(bundle: dict) -> dict:
    """Normalize nullable sections and refuse packs Core cannot interview from (at create time)."""
    for key in ("interview_target", "preferences"):
        _object_section(bundle, key)
    pack = bundle["knowledge_pack"]
    for key in ("company_knowledge", "role_profile", "evaluator", "question_bank"):
        _object_section(pack, key)
    _object_section(pack["evaluator"], "rubric")
    for section, key in ((pack["role_profile"], "items"), (pack["question_bank"], "items")):
        items = section.get(key)
        if items is None:
            section[key] = []
        elif not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise IDQServiceError(502, f"ID_Q knowledge pack field '{key}' must be a list of objects")
    try:
        rubric_source_scale(bundle)
    except ValueError as exc:
        raise IDQServiceError(422, str(exc)) from exc
    return bundle
