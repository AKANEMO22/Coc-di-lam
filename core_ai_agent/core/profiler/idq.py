"""Projection from the private ID_Q service contract into Core profiler artifacts."""

from typing import Any


SUPPORTED_RUBRIC_SCALE_MAX = {4, 5}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def selected_jd(bundle: dict[str, Any]) -> dict[str, Any]:
    """The JD the candidate selected in ID_Q; none while the pack still `needs_selection`.

    Shared by session creation (typed JD text) and the profiler projection so both describe
    the same JD; an unselected candidate list is never guessed from.
    """
    company = _as_dict(_as_dict(bundle.get("knowledge_pack")).get("company_knowledge"))
    return _as_dict(company.get("selected_jd"))


def rubric_source_scale(bundle: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return the ID_Q rubric scale and its max; ValueError when Core cannot score it."""
    evaluator = _as_dict(_as_dict(bundle.get("knowledge_pack")).get("evaluator"))
    raw_scale = _as_dict(evaluator.get("rubric")).get("scale")
    if raw_scale is None:
        raw_scale = {"min": 1, "max": 4}
    if not isinstance(raw_scale, dict):
        raise ValueError("Core cannot score an ID_Q rubric without a scale object")
    try:
        source_max = int(raw_scale.get("max", 4))
    except (TypeError, ValueError) as exc:
        raise ValueError("Core cannot score an ID_Q rubric with a non-numeric scale") from exc
    if source_max not in SUPPORTED_RUBRIC_SCALE_MAX:
        raise ValueError(f"Core cannot score an ID_Q rubric with scale 1-{source_max}")
    return raw_scale, source_max


def company_evidence(bundle: dict[str, Any]) -> dict[str, Any]:
    target = bundle.get("interview_target", {})
    company_pack = bundle.get("knowledge_pack", {}).get("company_knowledge", {})
    selected = selected_jd(bundle)
    company_name = str(target.get("company_name") or (company_pack.get("company") or {}).get("canonical_name") or "Target Company")
    jd_id = str(selected.get("jd_id") or "")
    source_id = f"jd:{jd_id}" if jd_id else ""
    requirements = [str(item) for item in selected.get("requirements", []) if isinstance(item, str)]
    content = " ".join(part for part in [str(selected.get("title") or ""), str(selected.get("description") or ""), *requirements] if part).strip()
    sources = []
    if source_id:
        sources.append({
            "source_id": source_id,
            "title": str(selected.get("title") or "Job description"),
            "url": str(selected.get("source_url") or selected.get("source_ref") or ""),
            "snippet": f"{company_name}: {content[:1200]}",
            "source_type": "company_jd",
            "trust_tier": str(selected.get("review_status") or "unreviewed"),
            "verification_status": str(selected.get("review_status") or "unreviewed"),
            "collected_at": selected.get("updated_at"),
            "content_hash": selected.get("content_hash"),
        })
    role = (bundle.get("knowledge_pack", {}).get("role_profile", {}).get("items") or [{}])[0]
    role_expectations = [str(item) for item in role.get("expectations", []) if isinstance(item, str)]
    company_intel = {
        "company": company_name,
        "culture": None,
        "tech_stack": [],
        "hiring_bar": None,
        "interview_focus": requirements,
        "source_ids": [source_id] if source_id else [],
        "evidence_status": str(selected.get("review_status") or "company_jd_unavailable"),
        "role_expectations": role_expectations,
        "limitations": list(bundle.get("knowledge_pack", {}).get("limitations", [])),
    }
    return {
        "company_intel": company_intel,
        "company_recon": {
            "web_recon": sources,
            "web_recon_status": "available" if sources else "unavailable",
            "knowledge_base": [{"topic": selected.get("title"), "content": content, "source": sources[0]["url"] if sources else "", "source_id": source_id, "is_generic": False}] if content else [],
            "prepared_from": "idq_curated_catalog",
        },
    }


def knowledge_for_core(bundle: dict[str, Any]) -> dict[str, Any]:
    pack = bundle.get("knowledge_pack", {})
    target = bundle.get("interview_target", {})
    role = (pack.get("role_profile", {}).get("items") or [{}])[0]
    evaluator = pack.get("evaluator", {})
    raw_rubric = evaluator.get("rubric", {})
    rubric_items = raw_rubric.get("competencies", [])
    source_scale, source_max = rubric_source_scale(bundle)
    source_threshold = float(raw_rubric.get("pass_threshold", 3.0))
    core_threshold = round(1 + (source_threshold - 1) * 3 / 4, 2) if source_max == 5 else source_threshold
    scale_note = (
        "The source rubric uses 1-5; Core projects anchors 1-4 and maps the pass threshold proportionally to its 1-4 scoring scale."
        if source_max == 5 else None
    )
    rubric = {
        "rubric_version": str(raw_rubric.get("rubric_version") or pack.get("knowledge_version") or "idq_unknown"),
        "role_family": role.get("role_family") or "",
        "level": str(target.get("target_seniority") or ""),
        "scale": {"min": 1, "max": 4},
        "source_scale": source_scale,
        "pass_threshold": core_threshold,
        "source_pass_threshold": source_threshold,
        "scale_mapping": scale_note,
        "competencies": [
            {
                **item,
                "anchors": {key: value for key, value in item.get("anchors", {}).items() if key in {"1", "2", "3", "4"}},
                "pass_level": min(4, int(item.get("pass_level", 3))),
                "source": item.get("source_ref", ""),
            }
            for item in rubric_items if isinstance(item, dict)
        ],
    }
    question_bank: dict[str, list[dict[str, Any]]] = {}
    by_question_id = {
        str(item.get("question_id")): item
        for item in evaluator.get("questions", []) if isinstance(item, dict)
    }
    for item in pack.get("question_bank", {}).get("items", []):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id") or "")
        competency_id = str(item.get("competency_id") or "")
        if not question_id or not competency_id:
            continue
        evaluator_item = by_question_id.get(question_id, {}).get("evaluator_only", {})
        expected = evaluator_item.get("expected_answer")
        if isinstance(expected, str):
            expected_points = [expected[:3000]] if expected.strip() else []
        elif isinstance(expected, list):
            expected_points = [str(value)[:1000] for value in expected[:20]]
        else:
            expected_points = []
        question_bank.setdefault(competency_id, []).append({
            "question_id": question_id,
            "competency_id": competency_id,
            "text": str(item.get("question") or ""),
            "stage_type": str(item.get("stage_type") or "technical"),
            "difficulty": item.get("difficulty"),
            "expected_points": expected_points,
            "follow_ups": [],
            "score": item.get("score", 0),
            "source": item.get("source_ref", ""),
        })

    company_context = []
    selected = selected_jd(bundle)
    if selected:
        text = " ".join(part for part in [str(selected.get("title") or ""), str(selected.get("description") or ""), *[str(x) for x in selected.get("requirements", []) if isinstance(x, str)]] if part)
        company_context.append({
            "topic": str(selected.get("title") or "Job description"),
            "content": text[:6000],
            "confidence": 0.8 if selected.get("review_status") in {"official_verified", "firsthand_reviewed"} else 0.45,
            "is_generic": False,
            "source": str(selected.get("source_url") or selected.get("source_ref") or ""),
            "source_id": f"jd:{selected.get('jd_id')}",
        })
    source_refs = list(role.get("source_refs", []))
    source_refs.extend(str(item.get("source_ref")) for item in rubric_items if isinstance(item, dict) and item.get("source_ref"))
    source_refs.extend(str(item.get("source_ref")) for item in pack.get("question_bank", {}).get("items", []) if item.get("source_ref"))
    if selected:
        source_refs.append(str(selected.get("source_url") or selected.get("source_ref") or ""))
    return {
        "status": "ok" if rubric["competencies"] else "degraded",
        "retrieval_backend": "idq",
        "role_family": rubric["role_family"],
        "level": rubric["level"],
        "source_level": rubric["level"],
        "role_profile": {
            "role_family": role.get("role_family") or "",
            "level": rubric["level"],
            "expectations": " ".join(str(item) for item in role.get("expectations", []) if item),
            "competencies": [item["competency_id"] for item in rubric["competencies"]],
            "pass_threshold": rubric["pass_threshold"],
            "rubric_version": rubric["rubric_version"],
            "source": ",".join(role.get("source_refs", [])),
        },
        "rubric": rubric,
        "question_bank": question_bank,
        "question_bank_status": pack.get("question_bank", {}).get("status", "unsupported_level"),
        "company_context": company_context,
        "sources": list(dict.fromkeys(value for value in source_refs if value)),
        "limitations": list(pack.get("limitations", [])) + ([scale_note] if scale_note else []),
        "idq": {
            "id_q": bundle.get("id_q"),
            "intake_revision": bundle.get("intake_revision"),
            "pack_id": bundle.get("pack_id"),
            "knowledge_version": bundle.get("knowledge_version") or pack.get("knowledge_version"),
        },
    }
