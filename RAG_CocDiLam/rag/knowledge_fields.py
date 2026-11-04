"""Explicit field contracts for role, rubric and question evidence."""

from copy import deepcopy
import math
import re
from typing import Any


POLICY_VERSION = 1
_NOISE = re.compile(
    r"\b(?:đăng nhập|đăng ký tài khoản|dành cho nhà tuyển dụng|"
    r"nhanh tay ứng tuyển|cách thức ứng tuyển|điền thông tin ứng tuyển|"
    r"xem thêm việc làm|việc làm liên quan|tìm kiếm phổ biến|"
    r"sign in|log in|apply now|how to apply|related jobs|"
    r"subscribe to|accept all cookies|cookie consent|all rights reserved)\b"
    r"|https?://(?:forms\.gle|forms\.google\.)", re.I,
)
_COMMON = {"source_ref", "source_refs", "review_status", "data_origin"}
_FIELDS = {
    "role_profile": {"role_id", "role_title", "role_family", "target_seniority", "supported_source_levels", "expectations", "interview_stages"},
    "rubric": {"competency_id", "name", "description", "weight", "must_have", "anchors"},
    "question_bank": {"question_id", "role_id", "target_seniority", "competency_id", "stage_type", "difficulty", "question", "score"},
}


def claim_text(value: Any, limit: int = 1200) -> str:
    """Reject malformed, promotional or overlong claims instead of stringifying them."""
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value if 0 < len(value) <= limit and not _NOISE.search(value) else ""


def claim_list(value: Any, limit: int = 24) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for raw in value:
        text = claim_text(raw, 600)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
            if len(result) == limit:
                break
    return result


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def weight_value(value: Any) -> float | None:
    return float(value) if _number(value) and value >= 0 else None


def _evaluator_fields(value: dict) -> dict:
    result = {}
    for key, allowed in {
        "scoring_rubric": {"poor", "acceptable", "excellent"},
        "star_criteria": {"situation_task", "action_positive", "action_negative", "result"},
    }.items():
        if isinstance(value.get(key), dict):
            result[key] = {k: text for k, v in value[key].items() if k in allowed and (text := claim_text(v, 3000))}
    answer = value.get("expected_answer")
    if isinstance(answer, list):
        result["expected_answer"] = claim_list(answer, 20)
    elif text := claim_text(answer, 6000):
        result["expected_answer"] = text
    if text := claim_text(value.get("evaluation_focus"), 3000):
        result["evaluation_focus"] = text
    return result


def scope_item(collection: str, item: Any, *, internal: bool = False) -> dict:
    if not isinstance(item, dict):
        return {}
    allowed = _FIELDS[collection] | _COMMON
    result = {}
    for key in allowed & item.keys():
        value = item[key]
        if key in {"expectations", "source_refs", "supported_source_levels"}:
            result[key] = claim_list(value)
        elif key == "interview_stages":
            stages = []
            for stage in value if isinstance(value, list) else []:
                if not isinstance(stage, dict):
                    continue
                name = claim_text(stage.get("stage_name"), 200)
                description = claim_text(stage.get("description"))
                if not name or not description:
                    continue
                clean = {"stage_name": name, "description": description}
                weight = stage.get("weight_percent")
                if _number(weight) and 0 <= weight <= 100:
                    clean["weight_percent"] = weight
                stages.append(clean)
                if len(stages) == 12:
                    break
            result[key] = stages
        elif key == "anchors":
            if internal and isinstance(value, dict):
                result[key] = {str(k): text for k, v in value.items() if str(k) in {"1", "2", "3", "4", "5"} and (text := claim_text(v))}
        elif key in {"weight", "score"}:
            if _number(value) and value >= 0:
                result[key] = value
        elif key == "must_have":
            result[key] = value if isinstance(value, bool) else False
        elif key == "competency_id" and value is None:
            result[key] = None
        elif key == "difficulty" and _number(value):
            result[key] = value
        else:
            text = claim_text(value, 1800 if key == "question" else 1200 if key == "description" else 300)
            if text:
                result[key] = text
    sourced = bool(result.get("source_ref") or result.get("source_refs"))
    if collection == "rubric" and not (result.get("competency_id") and result.get("name") and result.get("description") and sourced):
        return {}
    if collection == "question_bank" and not (result.get("question_id") and result.get("question") and sourced):
        return {}
    if collection == "role_profile" and not (result.get("role_id") and result.get("role_title") and sourced):
        return {}
    if internal and collection == "question_bank" and isinstance(item.get("evaluator_only"), dict):
        result["evaluator_only"] = _evaluator_fields(item["evaluator_only"])
    return result


def scope_rubric(value: dict, *, internal: bool) -> dict:
    allowed = {"rubric_version", "role_id", "target_seniority", "scale", "complete_for_scoring"}
    if internal:
        allowed |= {"pass_threshold", "source_refs"}
    result = {}
    for key in allowed & value.keys():
        if key == "source_refs":
            result[key] = claim_list(value[key])
        elif key in {"scale", "complete_for_scoring"}:
            continue
        elif key == "pass_threshold":
            if _number(value[key]):
                result[key] = value[key]
        elif text := claim_text(value[key], 300):
            result[key] = text
    scale = value.get("scale") or {}
    result["scale"] = {key: scale[key] for key in ("min", "max") if _number(scale.get(key))}
    result["competencies"] = [clean for item in value.get("competencies", []) if (clean := scope_item("rubric", item, internal=internal))]
    if internal:
        required = {str(i) for i in range(1, int(result["scale"].get("max", 4)) + 1)} if result["scale"].get("max") in {4, 5} else set()
        result["complete_for_scoring"] = value.get("complete_for_scoring") is not False and bool(required and result["competencies"]) and len(result["competencies"]) == len(value.get("competencies", [])) and all(
            required <= item.get("anchors", {}).keys() and _number(item.get("weight")) and item["weight"] > 0
            for item in result["competencies"]
        )
    else:
        result["complete_for_scoring"] = bool(value.get("complete_for_scoring", False))
    return result


def scope_collection(collection: str, value: dict, *, internal: bool = False) -> dict:
    """Project saved or new records without modifying the stored snapshot."""
    allowed = {"status", "total", "complete_for_scoring", "coverage_incomplete", "limitations"}
    result = {key: deepcopy(value[key]) for key in allowed & value.keys()}
    items = value.get("items") or []
    result["items"] = [clean for item in items if (clean := scope_item(collection, item, internal=internal))]
    removed = len(items) - len(result["items"])
    result["total"] = max(len(result["items"]), int(value.get("total") or len(items)) - removed)
    if removed and not result["items"]:
        result["status"] = "insufficient_evidence"
    if collection == "rubric" and isinstance(value.get("rubric"), dict):
        # Derive completeness before hiding evaluator anchors.
        complete = scope_rubric(value["rubric"], internal=True)
        result["complete_for_scoring"] = complete["complete_for_scoring"]
        result["rubric"] = complete if internal else scope_rubric(complete, internal=False)
        if removed:
            result["complete_for_scoring"] = result["rubric"]["complete_for_scoring"] = False
    return result
