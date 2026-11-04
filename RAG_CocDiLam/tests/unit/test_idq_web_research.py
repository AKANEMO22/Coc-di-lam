from __future__ import annotations

import pytest

from rag.idq_service import IDQKnowledgeService
from rag.jd_fields import extract_jd_fields
from rag.web_research import CompanyJDWebResearch, WebResearchConfig, stable_company_id, _source_score


def test_bilingual_ai_role_requires_ai_in_page_title():
    source = {"title": "FPT Software HCM tuyển dụng Junior AI Engineer", "url": "https://example.test/job", "content_excerpt": "Yêu cầu ứng tuyển: Có kiến thức Python và NLP."}
    assert _source_score(source, "FPT Software", "AI Engineer (Kỹ sư Trí tuệ nhân tạo)")[1]
    source["title"] = "FPT Software tuyển dụng Backend Engineer"
    assert not _source_score(source, "FPT Software", "AI Engineer (Kỹ sư Trí tuệ nhân tạo)")[1]


def test_extract_jd_fields_excludes_promotion_application_and_site_navigation():
    fields = extract_jd_fields(
        "Đăng nhập Dành cho nhà tuyển dụng\n"
        "MÔ TẢ CÔNG VIỆC: Xây dựng mô hình NLP. - Triển khai dịch vụ AI. "
        "QUYỀN LỢI DÀNH CHO BẠN: Đào tạo kỹ năng cùng chuyên gia. "
        "YÊU CẦU ỨNG TUYỂN: - Sinh viên năm 4 ngành CNTT. - Có kiến thức Python và NLP. "
        "- Tiếng Anh IELTS 6.5 hoặc tương đương. "
        "CÁCH THỨC ỨNG TUYỂN: Điền thông tin tại https://forms.gle/apply "
        "Nhanh tay ứng tuyển. Xem thêm việc làm Câu hỏi phỏng vấn Marketing."
    )
    assert fields == {
        "responsibilities": ["Xây dựng mô hình NLP.", "Triển khai dịch vụ AI."],
        "requirements": ["Sinh viên năm 4 ngành CNTT.", "Có kiến thức Python và NLP.", "Tiếng Anh IELTS 6.5 hoặc tương đương."],
    }


def test_unmapped_or_oversized_text_is_not_a_jd_field():
    assert extract_jd_fields("FPT Software company profile. Đăng nhập. Jobs in Marketing.") == {
        "responsibilities": [], "requirements": [],
    }
    assert extract_jd_fields("Requirements: " + "Python " * 100)["requirements"] == []


def test_extract_jd_deduplicates_and_bounds_fields():
    fields = extract_jd_fields("Requirements:\n" + "\n".join(
        ["Experience with Python APIs."] * 4 + [f"Knowledge of technology number {i}." for i in range(20)]
    ))
    assert len(fields["requirements"]) == 12
    assert fields["requirements"].count("Experience with Python APIs.") == 1


def test_saved_web_pack_is_cleaned_for_candidate_and_evaluator_without_mutation():
    job = {"data_origin": "web_search", "description": "Đăng nhập. Requirements: Knowledge of Python. How to apply: Visit https://apply.test", "requirements": []}
    company = {"items": [job], "selected_jd": job, "jd_candidates": []}
    result = {"company_knowledge": company, "role_profile": {}, "question_bank": {}, "limitations": [], "_evaluator": {}, "id_q": "iq", "intake_revision": 1, "knowledge_version": "old"}
    preparation = {"result": result, "id_q": "iq", "pack_id": "pack", "intake_revision": 1, "knowledge_version": "old"}
    public = IDQKnowledgeService.public_collection(preparation, "company_knowledge")
    internal = IDQKnowledgeService.evaluator_pack(preparation)
    assert public["selected_jd"]["description"] == ""
    assert public["items"][0]["requirements"] == ["Knowledge of Python."]
    assert internal["company_knowledge"]["selected_jd"] == public["selected_jd"]
    assert "Đăng nhập" in job["description"]


@pytest.mark.asyncio
async def test_company_listing_with_role_only_in_body_is_not_a_jd():
    class ListingSearch(FakeSearch):
        async def search(self, query, target_url=None, max_results=4):
            if target_url:
                return await super().search(query, target_url=target_url, max_results=max_results)
            return {"status": "success", "results": [{"title": "FPT Software – Jobs & Company Profile", "url": "https://example.test/company/fpt", "snippet": "Backend Developer and other jobs"}]}

    result = await CompanyJDWebResearch(ListingSearch(), MemoryStore()).research(_target(), role_id="IF_BE_GEN", level="junior")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_fetched_page_without_jd_fields_cannot_be_selected():
    class EmptyJDSearch(FakeSearch):
        async def search(self, query, target_url=None, max_results=4):
            if target_url:
                return {"status": "success", "content": "Sign in. Subscribe. FPT Software is a global company. Related jobs: Marketing."}
            return await super().search(query, max_results=max_results)

    result = await CompanyJDWebResearch(EmptyJDSearch(), MemoryStore()).research(_target(), role_id="IF_BE_GEN", level="junior")
    assert result["selected_jd"] is None
    assert result["jd_candidates"][0]["description"] == ""
    assert result["jd_candidates"][0]["extraction_status"] == "insufficient_evidence"


class MemoryStore:
    def __init__(self, companies=None, jobs=None):
        self.companies = companies or []
        self.jobs = jobs or []
        self.cache = {}

    def company_knowledge(self):
        return self.companies, self.jobs

    def get_web_research_cache(self, key):
        return self.cache.get(key)

    def set_web_research_cache(self, key, payload, _ttl):
        self.cache[key] = payload


class FakeSearch:
    def __init__(self):
        self.search_calls = 0
        self.fetch_calls = 0

    async def search(self, query, target_url=None, max_results=4):
        if target_url:
            self.fetch_calls += 1
            return {
                "status": "success",
                "url": target_url,
                "content": (
                    "FPT Software is hiring a Junior Backend Developer. "
                    "Requirements: Candidates must have experience with Python and REST APIs. "
                    "Qualifications: Familiarity with PostgreSQL and Docker is preferred."
                ),
                "content_hash": "page-hash",
                "fetched_at": "2026-10-21T01:00:00+00:00",
            }
        self.search_calls += 1
        return {
            "status": "success",
            "query": query,
            "results": [{
                "source_id": "web_fpt_job",
                "title": "Junior Backend Developer | FPT Software Careers",
                "description": "FPT Software is hiring a Junior Backend Developer.",
                "snippet": "FPT Software is hiring a Junior Backend Developer.",
                "url": "https://fptsoftware.com/careers/junior-backend-developer",
                "query": query,
                "position": 1,
                "content_hash": "search-hash",
            }],
        }


def _target():
    return {
        "company_name": "FPT Software",
        "role_id": "IF_BE_GEN",
        "role_title": "Backend Developer",
        "target_seniority": "junior",
        "location": "Vietnam",
    }


@pytest.mark.asyncio
async def test_web_research_builds_sourced_company_and_selected_jd():
    store = MemoryStore()
    search = FakeSearch()
    research = CompanyJDWebResearch(search, store, WebResearchConfig(max_pages=2))

    result = await research.research(_target(), role_id="IF_BE_GEN", level="junior")

    assert result["status"] == "selected"
    assert result["company"]["company_id"] == stable_company_id("FPT Software")
    assert result["company"]["id_origin"] == "system_generated_from_company_name"
    assert result["selected_jd"]["source_url"].startswith("https://fptsoftware.com/")
    assert result["selected_jd"]["data_origin"] == "web_search"
    assert result["selected_jd"]["requirements"]
    assert result["sources"][0]["trust_tier"] == "company_domain_candidate"
    assert search.fetch_calls == 1


@pytest.mark.asyncio
async def test_web_research_cache_avoids_repeating_provider_calls():
    store = MemoryStore()
    search = FakeSearch()
    research = CompanyJDWebResearch(search, store)

    first = await research.research(_target(), role_id="IF_BE_GEN", level="junior")
    second = await research.research(_target(), role_id="IF_BE_GEN", level="junior")

    assert first["selected_jd"]["jd_id"] == second["selected_jd"]["jd_id"]
    assert second["web_research"]["cache_hit"] is True
    assert search.search_calls == 3


@pytest.mark.asyncio
async def test_search_snippet_is_candidate_but_not_automatically_selected():
    class SearchWithoutFetch(FakeSearch):
        async def search(self, query, target_url=None, max_results=4):
            if target_url:
                return {"status": "error", "reason": "page unavailable"}
            return await super().search(query, max_results=max_results)

    result = await CompanyJDWebResearch(SearchWithoutFetch(), MemoryStore()).research(
        _target(), role_id="IF_BE_GEN", level="junior"
    )

    assert result["status"] == "needs_selection"
    assert result["selected_jd"] is None
    assert result["jd_candidates"][0]["evidence_quality"] == "search_snippet"


@pytest.mark.asyncio
async def test_news_article_is_not_treated_as_a_job_description():
    class NewsSearch(FakeSearch):
        async def search(self, query, target_url=None, max_results=4):
            if target_url:
                return await super().search(query, target_url=target_url, max_results=max_results)
            return {
                "status": "success",
                "results": [{
                    "source_id": "web_news",
                    "title": "FPT Software welcomes backend interns",
                    "snippet": "FPT Software celebrates its new backend interns and their careers.",
                    "url": "https://fptsoftware.com/en/newsroom/news/backend-interns",
                    "query": query,
                    "position": 1,
                }],
            }

    result = await CompanyJDWebResearch(NewsSearch(), MemoryStore()).research(
        _target(), role_id="IF_BE_GEN", level="junior"
    )

    assert result["status"] == "not_found"
    assert result["jd_candidates"] == []


class FakeEngine:
    version = "index-v1"
    payload = {
        "audit": {"framework_digest": "framework-v1"},
        "frameworks": {
            "IF_BE_GEN": {
                "role_title": "Backend Developer",
                "role_family": "backend",
                "target_levels": ["Junior"],
                "target_competencies": ["REST APIs"],
                "interview_stages": [],
                "evaluation_matrix": [{"pillar": "Backend", "pillar_en": "Backend", "criteria": "API design", "weight_percent": 100}],
                "scoring_anchors": {"1": "weak", "2": "basic", "3": "good", "4": "strong"},
                "passing_thresholds": {},
                "technical_questions": [],
                "behavioral_questions": [],
            }
        },
    }


@pytest.mark.asyncio
async def test_prepare_async_uses_web_only_when_catalog_jd_is_missing(tmp_path):
    store = MemoryStore()
    search = FakeSearch()
    service = IDQKnowledgeService(
        store,
        FakeEngine(),
        tmp_path,
        web_research=CompanyJDWebResearch(search, store),
    )
    intake = {
        "id_q": "iq_test",
        "revision": 1,
        "candidate_profile": {"summary": "Python backend", "skills": ["Python"], "projects": []},
        "interview_target": _target(),
        "preferences": {"interview_round": "technical"},
    }

    result = await service.prepare_async(intake)

    company = result["company_knowledge"]
    assert company["status"] == "selected"
    assert company["selected_jd"]["company_id"] == company["company"]["company_id"]
    assert company["web_research"]["used_as_fallback"] is True
    assert any("unreviewed" in item for item in result["limitations"])


@pytest.mark.asyncio
async def test_prepare_async_does_not_search_when_curated_jd_is_complete(tmp_path):
    company = {"company_id": "fpt", "canonical_name": "FPT Software", "aliases": ["FPT Software"]}
    job = {
        "jd_id": "fpt-backend-junior",
        "company_id": "fpt",
        "title": "Backend Developer",
        "role_id": "IF_BE_GEN",
        "target_seniority": "junior",
        "description": "Build backend APIs.",
        "requirements": ["Python"],
        "source_url": "https://fptsoftware.com/careers/backend",
    }
    store = MemoryStore([company], [job])
    search = FakeSearch()
    service = IDQKnowledgeService(
        store,
        FakeEngine(),
        tmp_path,
        web_research=CompanyJDWebResearch(search, store),
    )
    intake = {
        "id_q": "iq_test",
        "revision": 1,
        "candidate_profile": {"summary": None, "skills": [], "projects": []},
        "interview_target": _target(),
        "preferences": {"interview_round": "technical"},
    }

    result = await service.prepare_async(intake)

    assert result["company_knowledge"]["selected_jd"]["jd_id"] == job["jd_id"]
    assert result["company_knowledge"]["web_research"]["status"] == "not_needed"
    assert search.search_calls == 0
