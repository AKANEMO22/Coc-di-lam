"""Versioned ID_Q intake and knowledge-pack API routes."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from pydantic import ValidationError

from .idq_extraction import CVExtractionError, MAX_CV_BYTES, extract_cv
from .idq_schemas import (
    CVIntakeMetadata,
    FormIntakeRequest,
    InterviewTarget,
    IntakePreferences,
    KnowledgeImportRequest,
    CrawlJobsRequest,
    PatchIntakeRequest,
    PrepareRequest,
)
from .idq_service import IDQKnowledgeService
from .indexing import digest

logger = logging.getLogger(__name__)


def tokens_match(supplied: str | None, expected: str | None) -> bool:
    """Constant-time token check that also accepts non-ASCII header values."""
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


_RAG_PACKAGE = Path(__file__).resolve().parent


def public_error_message(exc: Exception, fallback: str) -> str:
    """Expose only messages this package deliberately raised; log anything else."""
    message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
    frame = exc.__traceback__
    while frame is not None and frame.tb_next is not None:
        frame = frame.tb_next
    origin = Path(frame.tb_frame.f_code.co_filename).resolve() if frame is not None else None
    deliberate = (
        type(exc) in (ValueError, KeyError, RuntimeError)
        and " " in message.strip()
        and origin is not None
        and origin.is_relative_to(_RAG_PACKAGE)
    )
    if deliberate:
        return message
    logger.warning("Suppressed internal error detail: %s: %r", type(exc).__name__, exc, exc_info=exc)
    return fallback


def knowledge_admin_authorized(request: Request) -> bool:
    """Admin UI session or X-RAG-Admin-Token; an unset RAG_KNOWLEDGE_ADMIN_TOKEN means closed."""
    admin_authenticated = getattr(request.app.state, "admin_authenticated", None)
    if admin_authenticated and admin_authenticated(request):
        return True
    return tokens_match(request.headers.get("x-rag-admin-token", ""), os.getenv("RAG_KNOWLEDGE_ADMIN_TOKEN", ""))


def create_idq_router(authorize_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["ID_Q intake and interview knowledge"])

    def _collect_form_provenance(prefix: str, value, output: dict):
        if isinstance(value, dict):
            for key, child in value.items():
                _collect_form_provenance(f"{prefix}/{key}", child, output)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                _collect_form_provenance(f"{prefix}/{index}", child, output)
        elif value not in (None, "", []):
            output[prefix] = {"source_type": "user_form", "source_ref": prefix, "confidence": None, "user_confirmed": True}

    def _cv_provenance(profile: dict, sections: list[dict]) -> dict:
        source_by_name = {}
        for section in sections:
            source_by_name.setdefault(section.get("name"), section)
        profile_to_section = {
            "summary": "summary", "experience": "experience", "projects": "projects",
            "education": "education", "certifications": "certifications", "awards": "awards",
            "career_goals": "career_goals", "additional_sections": "additional", "skills": "skills",
        }
        output = {}
        for key, value in profile.items():
            if value in (None, "", []):
                continue
            section = source_by_name.get(profile_to_section.get(key, "additional"))
            ref = section.get("source_ref") if section else "cv_text_layer"
            output[f"/candidate_profile/{key}"] = {
                "source_type": "cv_extraction",
                "source_ref": ref,
                "confidence": None,
                "user_confirmed": False,
                "review_required": True,
            }
        return output

    def _get(request: Request, id_q: str, owner: str):
        try:
            return request.app.state.idq_store.get_intake(id_q, owner)
        except KeyError as exc:
            raise HTTPException(404, "Intake not found") from exc

    def _service(request: Request) -> IDQKnowledgeService:
        return request.app.state.idq_service

    def _resolve_target(request: Request, target: InterviewTarget) -> dict:
        result = target.model_dump()
        frameworks = request.app.state.idq_service.frameworks
        if result.get("role_id") and result["role_id"] not in frameworks:
            raise HTTPException(422, f"Unsupported role_id '{result['role_id']}'")
        if result.get("role_id") and not result.get("role_title"):
            result["role_title"] = frameworks[result["role_id"]].get("role_title")
        if not result.get("role_id") and result.get("role_title"):
            role_id, _options = request.app.state.idq_service._resolve_role(result)
            if role_id:
                result["role_id"] = role_id
                result["role_title"] = frameworks[role_id].get("role_title") or result["role_title"]
        companies, _ = request.app.state.idq_store.company_knowledge()
        company_name = str(result.get("company_name") or "").strip()
        company_id = str(result.get("company_id") or "").strip()
        if company_id and not company_name:
            matches = [company for company in companies if company_id.casefold() == str(company.get("company_id", "")).casefold()]
            if len(matches) == 1:
                result["company_name"] = str(matches[0].get("canonical_name") or "")
        if company_name and not company_id:
            normalized = company_name.casefold()
            matches = [
                company for company in companies
                if normalized == str(company.get("canonical_name", "")).casefold()
                or normalized in {str(alias).casefold() for alias in (company.get("aliases") or [])}
            ]
            if len(matches) == 1:
                result["company_id"] = str(matches[0]["company_id"])
                result["company_name"] = str(matches[0].get("canonical_name") or company_name)
        return result

    def _http_from_store_error(exc: Exception):
        message = str(exc)
        if "revision conflict" in message:
            raise HTTPException(409, "Intake revision conflict; reload the latest revision") from exc
        if "Preparation required" in message:
            raise HTTPException(409, "Prepare the current intake revision before reading this collection") from exc
        if isinstance(exc, KeyError):
            raise HTTPException(404, "Resource not found") from exc
        raise HTTPException(422, public_error_message(exc, "The request is invalid")) from exc

    @router.post("/intakes/form", status_code=status.HTTP_201_CREATED)
    def create_form_intake(
        body: FormIntakeRequest,
        request: Request,
        owner: str = Depends(authorize_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        payload = body.model_dump(mode="json")
        request_hash = digest(payload)
        store = request.app.state.idq_store
        try:
            prior = store.idempotent_response(owner, "create_intake", idempotency_key, request_hash)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if prior:
            return prior
        id_q = store.new_id("iq")
        profile = payload["candidate_profile"]
        target = _resolve_target(request, body.interview_target)
        preferences = payload["preferences"]
        provenance = {}
        _collect_form_provenance("/candidate_profile", profile, provenance)
        _collect_form_provenance("/interview_target", target, provenance)
        _collect_form_provenance("/preferences", preferences, provenance)
        job_id = store.new_id("job")
        response = {"id_q": id_q, "revision": 1, "input_type": "form", "job_id": job_id, "status_url": f"/api/v1/intakes/{id_q}"}
        source = {"kind": "form", "content_hash": "sha256:" + request_hash, "sections": [], "extraction_status": "complete"}
        try:
            created = store.create_intake(owner=owner, input_type="form", profile=profile, target=target, preferences=preferences, provenance=provenance, source=source, response=response, idempotency_key=idempotency_key, request_hash=request_hash)
            if created["id_q"] != id_q:
                return created
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {**response, "job_id": job_id, "status": store.get_intake(id_q, owner)["status"]}

    @router.post("/intakes/cv", status_code=status.HTTP_201_CREATED)
    async def create_cv_intake(
        request: Request,
        metadata: str = Form(..., description="CVIntakeMetadata encoded as JSON"),
        file: UploadFile = File(...),
        owner: str = Depends(authorize_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        try:
            body = CVIntakeMetadata.model_validate_json(metadata)
        except ValidationError as exc:
            raise HTTPException(422, "Invalid CV intake metadata") from exc
        content = await file.read(MAX_CV_BYTES + 1)
        if len(content) > MAX_CV_BYTES:
            raise HTTPException(413, "The CV exceeds the 12 MB limit")
        filename = file.filename or "cv.txt"
        try:
            extracted = await asyncio.to_thread(extract_cv, filename, content)
        except CVExtractionError as exc:
            raise HTTPException(422, str(exc)) from exc
        file_hash = hashlib.sha256(content).hexdigest()
        payload_hash = digest({"file_sha256": file_hash, "metadata": body.model_dump(mode="json")})
        store = request.app.state.idq_store
        try:
            prior = await asyncio.to_thread(store.idempotent_response, owner, "create_intake", idempotency_key, payload_hash)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if prior:
            return prior
        id_q = store.new_id("iq")
        private_path = None
        try:
            private_path = await asyncio.to_thread(store.save_upload, id_q, filename, content)
            target = await asyncio.to_thread(_resolve_target, request, body.interview_target)
            profile = extracted["profile"]
            provenance = _cv_provenance(profile, extracted["sections"])
            _collect_form_provenance("/interview_target", target, provenance)
            _collect_form_provenance("/preferences", body.preferences.model_dump(mode="json"), provenance)
            source = {
                "kind": "cv", "filename": Path(filename.replace("\\", "/")).name,
                "mime": file.content_type or "application/octet-stream",
                "content_hash": "sha256:" + file_hash,
                "private_path": private_path,
                "extraction_status": "complete",
                "page_count": extracted["page_count"],
                "extracted_characters": extracted["extracted_characters"],
                "sections": extracted["sections"],
                "warnings": extracted["warnings"],
            }
            job_id = store.new_id("job")
            response = {"id_q": id_q, "revision": 1, "input_type": "cv", "job_id": job_id, "status_url": f"/api/v1/intakes/{id_q}"}
            created = await asyncio.to_thread(
                lambda: store.create_intake(owner=owner, input_type="cv", profile=profile, target=target, preferences=body.preferences.model_dump(mode="json"), provenance=provenance, source=source, response=response, idempotency_key=idempotency_key, request_hash=payload_hash)
            )
            if created["id_q"] != id_q:
                await asyncio.to_thread(store.remove_upload, private_path)
                return created
        except Exception as exc:
            await asyncio.to_thread(store.remove_upload, private_path)
            if isinstance(exc, ValueError):
                raise HTTPException(422, public_error_message(exc, "The CV intake is invalid")) from exc
            if isinstance(exc, OSError):
                logger.exception("Could not store CV intake")
                raise HTTPException(422, "The CV could not be stored") from exc
            raise
        status_value = (await asyncio.to_thread(store.get_intake, id_q, owner))["status"]
        return {**response, "job_id": job_id, "status": status_value}

    @router.get("/intakes/{id_q}")
    def get_intake(id_q: str, request: Request, owner: str = Depends(authorize_dependency)):
        return _get(request, id_q, owner)

    @router.patch("/intakes/{id_q}")
    def patch_intake(id_q: str, body: PatchIntakeRequest, request: Request, owner: str = Depends(authorize_dependency)):
        if not (body.candidate_profile or body.interview_target or body.preferences):
            raise HTTPException(422, "Provide candidate_profile, interview_target, or preferences")
        current = _get(request, id_q, owner)
        profile = body.candidate_profile.model_dump(mode="json") if body.candidate_profile else current["candidate_profile"]
        target = _resolve_target(request, body.interview_target) if body.interview_target else current["interview_target"]
        preferences = body.preferences.model_dump(mode="json") if body.preferences else current["preferences"]
        provenance = dict(current.get("field_provenance", {}))
        if body.candidate_profile:
            for key in body.candidate_profile.model_fields_set:
                path = f"/candidate_profile/{key}"
                value = getattr(body.candidate_profile, key)
                if value in (None, "", []):
                    provenance.pop(path, None)
                else:
                    provenance[path] = {"source_type": "user_correction", "source_ref": f"revision:{body.expected_revision + 1}", "confidence": None, "user_confirmed": True}
        if body.interview_target:
            for key in body.interview_target.model_fields_set:
                path = f"/interview_target/{key}"
                value = target.get(key)
                if value in (None, "", []):
                    provenance.pop(path, None)
                else:
                    provenance[path] = {"source_type": "user_target", "source_ref": f"revision:{body.expected_revision + 1}", "confidence": None, "user_confirmed": True}
        if body.preferences:
            for key in body.preferences.model_fields_set:
                path = f"/preferences/{key}"
                provenance[path] = {"source_type": "user_preference", "source_ref": f"revision:{body.expected_revision + 1}", "confidence": None, "user_confirmed": True}
        try:
            return request.app.state.idq_store.update_intake(id_q, owner, body.expected_revision, profile, target, preferences, provenance)
        except (KeyError, RuntimeError, ValueError) as exc:
            _http_from_store_error(exc)

    @router.post("/intakes/{id_q}/prepare", status_code=status.HTTP_201_CREATED)
    async def prepare_intake(id_q: str, body: PrepareRequest, request: Request, owner: str = Depends(authorize_dependency)):
        store = request.app.state.idq_store
        intake = await asyncio.to_thread(_get, request, id_q, owner)
        if intake["revision"] != body.expected_revision:
            raise HTTPException(409, "Intake revision conflict; reload the latest revision")
        job_id = await asyncio.to_thread(
            lambda: store.create_job(owner=owner, id_q=id_q, revision=intake["revision"], task_type="prepare_knowledge")
        )
        try:
            result = await request.app.state.idq_service.prepare_async(
                intake,
                top_k=body.top_k,
                web_search_mode=body.web_search_mode,
            )
            knowledge_version = result["knowledge_version"]
            public_result = {key: value for key, value in result.items() if key != "_evaluator"}
            record = await asyncio.to_thread(
                store.publish_preparation, id_q, owner, intake["revision"], knowledge_version,
                {**public_result, "_evaluator": result["_evaluator"]},
            )
            await asyncio.to_thread(lambda: store.finish_job(job_id, result=record))
            return {**record, "job_id": job_id, "limitations": result["limitations"]}
        except Exception as exc:
            await asyncio.to_thread(lambda: store.finish_job(job_id, result={}, error_code="PREPARATION_FAILED"))
            if isinstance(exc, (KeyError, RuntimeError, ValueError)):
                _http_from_store_error(exc)
            raise


    @router.post("/jobs/crawl")
    async def crawl_jobs(body: CrawlJobsRequest, request: Request, owner: str = Depends(authorize_dependency)):
        from rag.job_sync.service import RAGJobCrawlerService
        crawler_service = getattr(request.app.state, "job_crawler_service", None)
        if crawler_service is None:
            crawler_service = RAGJobCrawlerService()
            request.app.state.job_crawler_service = crawler_service
        jobs = await crawler_service.crawl(source=body.source, keyword=body.keyword, limit=body.limit)
        return {
            "status": "success",
            "source": body.source,
            "keyword": body.keyword,
            "total": len(jobs),
            "message": "Không tìm thấy việc làm nào" if not jobs else f"Tìm thấy {len(jobs)} việc làm",
            "jobs": [job.model_dump() for job in jobs],
        }

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, request: Request, owner: str = Depends(authorize_dependency)):
        try:
            return request.app.state.idq_store.get_job(job_id, owner)
        except KeyError as exc:
            raise HTTPException(404, "Job not found") from exc

    def make_collection_endpoint(collection_name: str):
        def endpoint(id_q: str, request: Request, top_k: int = Query(default=10, ge=1, le=50), pack_id: str | None = None, owner: str = Depends(authorize_dependency)):
            try:
                prep = request.app.state.idq_store.get_preparation(id_q, owner, pack_id)
                return request.app.state.idq_service.public_collection(prep, collection_name, top_k)
            except (KeyError, RuntimeError, ValueError) as exc:
                _http_from_store_error(exc)
        return endpoint

    for collection in ("company_knowledge", "rubric", "role_profile", "question_bank"):
        endpoint = make_collection_endpoint(collection)
        router.add_api_route(f"/intakes/{{id_q}}/{collection}", endpoint, methods=["GET"], name=f"get_{collection}")

    @router.get("/internal/intakes/{id_q}/knowledge_pack")
    def get_internal_pack(id_q: str, request: Request, pack_id: str | None = None, owner: str = Depends(authorize_dependency)):
        # Never fall back to RAG_API_TOKEN: every backend caller holds that one.
        core_token = os.getenv("RAG_CORE_TOKEN", "")
        if not core_token:
            raise HTTPException(503, "Core service credentials are not configured")
        if not tokens_match(request.headers.get("x-rag-core-token", ""), core_token):
            raise HTTPException(401, "Core service credentials are required")
        try:
            prep = request.app.state.idq_store.get_preparation(id_q, owner, pack_id)
            intake = request.app.state.idq_store.get_intake_revision(id_q, owner, prep["intake_revision"])
            return {
                "pack_id": prep["pack_id"],
                "id_q": intake["id_q"],
                "intake_revision": intake["revision"],
                "input_type": intake["input_type"],
                "candidate_profile": intake["candidate_profile"],
                "interview_target": intake["interview_target"],
                "preferences": intake["preferences"],
                "field_provenance": intake["field_provenance"],
                "knowledge_pack": request.app.state.idq_service.evaluator_pack(prep),
            }
        except (KeyError, RuntimeError, ValueError) as exc:
            _http_from_store_error(exc)

    @router.delete("/intakes/{id_q}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_intake(id_q: str, request: Request, owner: str = Depends(authorize_dependency)):
        try:
            private_path = request.app.state.idq_store.delete_intake(id_q, owner)
            request.app.state.idq_store.remove_upload(private_path)
        except KeyError as exc:
            raise HTTPException(404, "Intake not found") from exc
        return None

    @router.post("/knowledge/imports")
    def import_knowledge(body: KnowledgeImportRequest, request: Request, owner: str = Depends(authorize_dependency)):
        if not knowledge_admin_authorized(request):
            raise HTTPException(403, "Knowledge administrator credentials are required")
        company_ids = set()
        for company in body.companies:
            if not company.get("company_id") or not company.get("canonical_name"):
                raise HTTPException(422, "Each company requires company_id and canonical_name")
            if not isinstance(company.get("aliases", []), list) or not all(isinstance(alias, str) for alias in company.get("aliases", [])):
                raise HTTPException(422, "Company aliases must be a list of strings")
            if str(company["company_id"]) in company_ids:
                raise HTTPException(422, "Duplicate company_id in import")
            company_ids.add(str(company["company_id"]))
        existing, _ = request.app.state.idq_store.company_knowledge()
        company_ids |= {str(company.get("company_id")) for company in existing}
        jd_ids = set()
        for job in body.job_descriptions:
            required = ("jd_id", "company_id", "title", "role_id", "target_seniority")
            if any(not job.get(field) for field in required):
                raise HTTPException(422, "Each JD requires jd_id, company_id, title, role_id, and target_seniority")
            if str(job["company_id"]) not in company_ids:
                raise HTTPException(422, f"JD references unknown company_id '{job['company_id']}'")
            if str(job["target_seniority"]).casefold() not in {"intern", "fresher", "junior", "mid", "senior"}:
                raise HTTPException(422, "JD target_seniority is unsupported")
            if str(job["role_id"]) not in request.app.state.idq_service.frameworks:
                raise HTTPException(422, f"JD references unsupported role_id '{job['role_id']}'")
            if not job.get("source_url") and not job.get("source_ref"):
                raise HTTPException(422, "Each JD requires a source_url or source_ref")
            if job.get("requirements") is not None and not isinstance(job.get("requirements"), list):
                raise HTTPException(422, "JD requirements must be a list of strings")
            if job.get("requirements") is not None and not all(isinstance(item, str) for item in job["requirements"]):
                raise HTTPException(422, "JD requirements must be a list of strings")
            if job.get("valid_until"):
                try:
                    from datetime import date
                    date.fromisoformat(str(job["valid_until"])[:10])
                except ValueError as exc:
                    raise HTTPException(422, "JD valid_until must be an ISO date") from exc
            if str(job["jd_id"]) in jd_ids:
                raise HTTPException(422, "Duplicate jd_id in import")
            job.setdefault("review_status", "unreviewed")
            jd_ids.add(str(job["jd_id"]))
        result = request.app.state.idq_store.import_company_knowledge(body.companies, body.job_descriptions)
        return {**result, "knowledge_version": _service(request).version}

    @router.get("/knowledge/coverage")
    def get_coverage(request: Request, owner: str = Depends(authorize_dependency)):
        frameworks = request.app.state.idq_service.frameworks
        level_catalog = {"mid": "generic rubric only", "senior": "generic rubric only"}
        roles = []
        for role_id, framework in sorted(frameworks.items()):
            questions = {}
            for level in ("Intern", "Fresher", "Junior", "Mid", "Senior"):
                count = sum(1 for question in framework.get("technical_questions", []) + framework.get("behavioral_questions", []) if str(question.get("level", "")).casefold() == level.casefold())
                questions[level.casefold()] = count if level.casefold() in {str(x).casefold() for x in framework.get("target_levels", [])} else (level_catalog.get(level.casefold(), "unsupported") if count == 0 else count)
            roles.append({"role_id": role_id, "role_title": framework.get("role_title"), "levels": framework.get("target_levels", []), "question_counts": questions})
        return {"knowledge_version": _service(request).version, **request.app.state.idq_store.coverage_counts(), "roles": roles}

    return router
