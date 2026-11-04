from copy import deepcopy
from types import SimpleNamespace

import pytest

from rag.idq_service import IDQKnowledgeService
from rag.knowledge_fields import claim_list, scope_collection, scope_item, scope_rubric


def criterion(**extra):
    return {"competency_id": "api", "name": "API design", "description": "Explain API design trade-offs.",
            "weight": 1.0, "must_have": True, "anchors": {str(i): f"Evidence at score {i}" for i in range(1, 5)},
            "source_ref": "framework#backend/api", **extra}


def rubric(**extra):
    return {"rubric_version": "v1", "role_id": "backend", "target_seniority": "junior",
            "scale": {"min": 1, "max": 4}, "pass_threshold": 3,
            "competencies": [criterion()], "complete_for_scoring": True, **extra}


def question(**extra):
    return {"question_id": "q1", "question": "How do you design a REST API?", "role_id": "backend",
            "target_seniority": "junior", "source_ref": "framework#backend/q1",
            "evaluator_only": {"expected_answer": "Explain resources and status codes.", "description": "unrelated JD"}, **extra}


def test_role_profile_has_only_role_fields_and_bounded_nested_stages():
    item = {
        "role_id": "backend", "role_title": "Backend Developer", "source_refs": ["framework#backend"],
        "expectations": ["API design", "API design", {"description": "foreign JD"}, "Đăng nhập để ứng tuyển", "x" * 601],
        "interview_stages": [{"stage_name": "Technical", "description": "Discuss API design.", "weight_percent": 80, "expected_answer": "secret"}],
        "description": "Full scraped page", "requirements": ["Job application"], "question": "Foreign question",
        "anchors": {"1": "scoring"}, "content": "raw page",
    }
    result = scope_item("role_profile", item)
    assert result["expectations"] == ["API design"]
    assert result["interview_stages"] == [{"stage_name": "Technical", "description": "Discuss API design.", "weight_percent": 80}]
    assert not {"description", "requirements", "question", "anchors", "content"} & result.keys()


def test_rubric_only_keeps_criteria_and_internal_scoring_fields():
    item = criterion(requirements=["foreign JD"], expectations=["role expectation"], question="foreign question", expected_answer="secret", content="web page")
    public = scope_item("rubric", item)
    internal = scope_item("rubric", item, internal=True)
    assert set(public) == {"competency_id", "name", "description", "weight", "must_have", "source_ref"}
    assert set(internal) == set(public) | {"anchors"}
    assert internal["anchors"] == item["anchors"]


@pytest.mark.parametrize("extra", [
    {"description": "Apply now to join us"}, {"description": "x" * 1201},
    {"source_ref": ""}, {"description": {"text": "must not stringify"}},
])
def test_invalid_criterion_is_omitted_and_rubric_is_incomplete(extra):
    result = scope_rubric(rubric(competencies=[criterion(**extra)]), internal=True)
    assert result["competencies"] == []
    assert result["complete_for_scoring"] is False


@pytest.mark.parametrize("extra", [{"anchors": {"1": "Basic"}}, {"weight": float("nan")}, {"weight": -1}, {"weight": True}])
def test_invalid_scoring_data_never_makes_a_complete_rubric(extra):
    assert scope_rubric(rubric(competencies=[criterion(**extra)]), internal=True)["complete_for_scoring"] is False


def test_question_never_falls_back_to_description_or_leaks_evaluator_content():
    item = question(description="Foreign JD", expectations=["Role"], anchors={"1": "score"}, expected_answer="secret")
    public = scope_item("question_bank", item)
    assert "evaluator_only" not in public
    assert not {"description", "expectations", "anchors", "expected_answer"} & public.keys()
    internal = scope_item("question_bank", item, internal=True)
    assert internal["evaluator_only"] == {"expected_answer": "Explain resources and status codes."}
    assert scope_item("question_bank", question(question="", description="Fallback must not be used")) == {}


def test_missing_claims_report_insufficient_evidence_without_fallback():
    result = scope_collection("question_bank", {"status": "retrieved", "items": [question(question="Đăng nhập để xem câu hỏi")], "total": 1, "content": "raw page"})
    assert result == {"status": "insufficient_evidence", "items": [], "total": 0}
    assert claim_list("API design") == []


def test_saved_public_reads_do_not_remove_internal_scoring_or_leak_other_sections():
    raw_rubric = rubric(content="raw web", requirements=["foreign JD"])
    role = {"role_id": "backend", "role_title": "Backend Developer", "expectations": ["API design"], "source_refs": ["framework#backend"], "question": "foreign question"}
    result = {
        "id_q": "iq", "intake_revision": 1, "knowledge_version": "old", "limitations": [],
        "company_knowledge": {}, "role_profile": {"items": [role]},
        "rubric": {"items": raw_rubric["competencies"], "rubric": raw_rubric},
        "question_bank": {"items": [question()]}, "_evaluator": {"rubric": raw_rubric, "questions": [question()]},
    }
    saved = deepcopy(result)
    prep = {"result": result, "id_q": "iq", "pack_id": "p", "intake_revision": 1, "knowledge_version": "old"}
    public = IDQKnowledgeService.public_collection(prep, "rubric")
    assert "pass_threshold" not in public["rubric"]
    assert "anchors" not in public["rubric"]["competencies"][0]
    assert "content" not in public["rubric"]
    assert public["complete_for_scoring"] is True
    internal = IDQKnowledgeService.evaluator_pack(prep)
    assert internal["evaluator"]["rubric"]["pass_threshold"] == 3
    assert internal["evaluator"]["rubric"]["competencies"][0]["anchors"] == criterion()["anchors"]
    assert "question" not in internal["role_profile"]["items"][0]
    assert "evaluator_only" not in internal["question_bank"]["items"][0]
    assert internal["evaluator"]["questions"][0]["evaluator_only"]["expected_answer"]
    assert result == saved


def test_prepare_filters_source_noise_before_question_selection(tmp_path):
    framework = {
        "role_title": "Backend Developer", "target_levels": ["Junior"],
        "target_competencies": ["API design", "Đăng nhập để xem thông tin", {"question": "foreign question"}],
        "interview_stages": [{"stage_name": "Technical", "description": "Discuss API design.", "requirements": ["foreign JD"]}],
        "evaluation_matrix": [{"pillar": "API design", "criteria": "Explain API design trade-offs.", "weight_percent": 100},
                              {"pillar": "Advertisement", "criteria": "Apply now to join us", "weight_percent": "bad"}],
        "scoring_anchors": {str(i): f"Evidence at score {i}" for i in range(1, 5)},
        "technical_questions": [{"id": "noise", "level": "Junior", "question": "API design API design. Sign in to apply now."},
                                {"id": "good", "level": "Junior", "question": "How do you explain API design trade-offs?", "expected_answer": "Explain constraints.", "description": "foreign JD"}],
    }
    engine = SimpleNamespace(version="test", payload={"frameworks": {"backend": framework}})
    store = SimpleNamespace(company_knowledge=lambda: ([], []))
    service = IDQKnowledgeService(store, engine, tmp_path)
    intake = {"id_q": "iq", "revision": 1, "candidate_profile": {}, "preferences": {},
              "interview_target": {"company_name": "Example", "role_id": "backend", "target_seniority": "junior"}}
    result = service.prepare(intake, top_k=1)
    assert result["role_profile"]["items"][0]["expectations"] == ["API design"]
    assert "requirements" not in result["role_profile"]["items"][0]["interview_stages"][0]
    assert len(result["rubric"]["rubric"]["competencies"]) == 1
    assert result["rubric"]["complete_for_scoring"] is False
    assert result["question_bank"]["items"][0]["question_id"] == "good"
    assert "description" not in result["question_bank"]["items"][0]
    assert result["_evaluator"]["questions"][0]["evaluator_only"]["expected_answer"] == "Explain constraints."


def test_internal_nested_answer_contract_rejects_foreign_fields():
    item = question(evaluator_only={
        "expected_answer": {"description": "must not stringify"},
        "scoring_rubric": {"poor": "No concrete explanation", "requirements": ["foreign JD"]},
        "star_criteria": {"action_positive": "Explains personal action", "company": "foreign company"},
    })
    clean = scope_item("question_bank", item, internal=True)["evaluator_only"]
    assert clean == {"scoring_rubric": {"poor": "No concrete explanation"}, "star_criteria": {"action_positive": "Explains personal action"}}
