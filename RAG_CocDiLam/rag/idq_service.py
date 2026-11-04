"""Resolve an ID_Q target into a versioned company/role/rubric/question pack."""

import asyncio
import re
from typing import Any, NamedTuple
from datetime import date

from .indexing import digest
from .jd_fields import extract_jd_fields
from .knowledge_fields import POLICY_VERSION, claim_list, claim_text, scope_collection, scope_item, scope_rubric, weight_value
from .ontology import resolve_role, tokens
from .web_research import stable_company_id

SENIORITIES = ("intern", "fresher", "junior", "mid", "senior")
_SUPPORTED_SOURCE_LEVELS = {"intern", "fresher", "junior"}


def _matching_level(raw: str) -> str | None:
    return next((level for level in SENIORITIES if level == str(raw or "").casefold()), None)


def _overlap_score(query: str, text: str) -> tuple[float, int]:
    query_tokens = set(tokens(query))
    doc_tokens = set(tokens(text))
    overlap = len(query_tokens & doc_tokens)
    return (overlap / max(1, len(query_tokens)), overlap)


def _stable_evidence(value: Any) -> Any:
    """Remove request-time fields so unchanged evidence keeps one knowledge version."""
    volatile = {"cache_hit", "collected_at", "fetched_at", "searched_at"}
    if isinstance(value, dict):
        return {key: _stable_evidence(item) for key, item in value.items() if key not in volatile}
    if isinstance(value, list):
        return [_stable_evidence(item) for item in value]
    return value


def _scoped_company(company: dict) -> dict:
    """Also clean web JDs in saved packs created before field extraction existed."""
    def scoped_job(job):
        if not isinstance(job, dict) or job.get("data_origin") != "web_search":
            return job
        if job.get("extraction_status"):
            return dict(job)
        fields = extract_jd_fields(str(job.get("description") or ""))
        requirements = extract_jd_fields("Requirements:\n" + "\n".join(
            value for value in job.get("requirements", []) if isinstance(value, str)
        ))["requirements"]
        fields["requirements"] = list(dict.fromkeys([*fields["requirements"], *requirements]))[:12]
        return {**job, **fields, "description": "\n".join(fields["responsibilities"]),
                "extraction_status": "extracted" if any(fields.values()) else "insufficient_evidence"}

    return {
        **company,
        "selected_jd": scoped_job(company.get("selected_jd")),
        "jd_candidates": [scoped_job(job) for job in company.get("jd_candidates", [])],
        "items": [scoped_job(job) for job in company.get("items", [])],
    }


class _Snapshot(NamedTuple):
    """Engine and its frameworks, swapped together so a prepare never sees a mix."""
    engine: Any
    frameworks: dict


class _PrepareContext(NamedTuple):
    """Per-prepare view: one engine snapshot and one parse of the company catalog."""
    snapshot: _Snapshot
    companies: list
    jobs: list
    knowledge_version: str


def knowledge_version(engine, companies: list, jobs: list) -> str:
    return "idq:" + digest({
        "field_policy": POLICY_VERSION,
        "index": engine.version,
        "companies": companies,
        "jobs": jobs,
        "levels": engine.payload.get("audit", {}).get("framework_digest"),
    })[:24]


def pack_version(base_version: str, top_k: int, company: dict) -> str:
    return "idq:" + digest({
        "knowledge": base_version,
        "top_k": top_k,
        "company_knowledge": _stable_evidence(company),
    })[:24]


class IDQKnowledgeService:
    """Builds deterministic retrieval snapshots without indexing private CV content."""

    def __init__(self, store, engine, data_dir, web_research=None):
        self.store = store
        self.data_dir = data_dir
        self.web_research = web_research
        self._snapshot = _Snapshot(engine, engine.payload["frameworks"])

    @property
    def engine(self):
        return self._snapshot.engine

    @engine.setter
    def engine(self, value) -> None:
        self.swap_engine(value)

    @property
    def frameworks(self) -> dict:
        return self._snapshot.frameworks

    def swap_engine(self, engine) -> None:
        """Atomically replace the engine; in-flight prepares keep their captured snapshot."""
        self._snapshot = _Snapshot(engine, engine.payload["frameworks"])

    @property
    def version(self) -> str:
        companies, jobs = self.store.company_knowledge()
        return knowledge_version(self._snapshot.engine, companies, jobs)

    def _context(self) -> _PrepareContext:
        snapshot = self._snapshot
        companies, jobs = self.store.company_knowledge()
        return _PrepareContext(snapshot, companies, jobs, knowledge_version(snapshot.engine, companies, jobs))

    @staticmethod
    def _set_pack_version(result: dict, top_k: int, context: _PrepareContext) -> dict:
        result["knowledge_version"] = pack_version(context.knowledge_version, top_k, result.get("company_knowledge", {}))
        return result

    def _resolve_role(self, target: dict, frameworks: dict | None = None) -> tuple[str | None, list[str]]:
        frameworks = self.frameworks if frameworks is None else frameworks
        role_id = str(target.get("role_id") or "").strip()
        if role_id:
            return (role_id, []) if role_id in frameworks else (None, [])
        title = str(target.get("role_title") or "").strip()
        if not title:
            return None, []
        resolved = resolve_role(title, set(frameworks))
        if resolved:
            return resolved, []
        folded = title.casefold()
        matches = [
            rid for rid, framework in frameworks.items()
            if folded in str(framework.get("role_title", "")).casefold()
            or str(framework.get("role_title", "")).casefold() in folded
        ]
        if len(matches) == 1:
            return matches[0], []
        if matches:
            return matches[0], []
        if "IF_SWE" in frameworks:
            return "IF_SWE", []
        if frameworks:
            return next(iter(frameworks)), []
        return None, []

    def _company_result(
        self, target: dict, role_id: str | None, level: str | None, catalog: tuple[list, list] | None = None
    ) -> dict:
        company_id = str(target.get("company_id") or "").strip()
        company_name = str(target.get("company_name") or "").strip()
        derived_company_id = stable_company_id(company_name) if company_name and not company_id else ""
        companies, jobs = catalog if catalog is not None else self.store.company_knowledge()
        aliases = {company_id.casefold(), company_name.casefold()}
        matched_company = next((c for c in companies if str(c.get("company_id", "")).casefold() == company_id.casefold() or company_name.casefold() in {str(a).casefold() for a in c.get("aliases", [])} | {str(c.get("canonical_name", "")).casefold()}), None)
        if matched_company:
            aliases |= {str(matched_company.get("company_id", "")).casefold(), str(matched_company.get("canonical_name", "")).casefold()}
            aliases |= {str(a).casefold() for a in matched_company.get("aliases", [])}
            company_name = str(matched_company.get("canonical_name") or company_name)
            company_id = str(matched_company.get("company_id") or company_id)
        matching = []
        expired = []
        for job in jobs:
            same_company = str(job.get("company_id", "")).casefold() in aliases
            if company_id and str(job.get("company_id", "")).casefold() == company_id.casefold():
                same_company = True
            if not same_company:
                continue
            if role_id and job.get("role_id") and job.get("role_id") != role_id:
                continue
            job_level = _matching_level(job.get("target_seniority"))
            if level and job_level and level != job_level:
                continue
            valid_until = str(job.get("valid_until") or "").strip()
            if valid_until:
                try:
                    if date.fromisoformat(valid_until[:10]) < date.today():
                        expired.append(job)
                        continue
                except ValueError:
                    expired.append(job)
                    continue
            matching.append(job)
        chosen_id = target.get("jd_id")
        selected = next((job for job in matching if job.get("jd_id") == chosen_id), None) if chosen_id else None
        if chosen_id and selected is None:
            return {"status": "not_found", "company": matched_company, "selected_jd": None, "jd_candidates": [], "items": [], "total": 0, "limitations": ["The selected JD does not match this company, role, or level."]}
        if not chosen_id and matching:
            selected = matching[0]
        status = "selected" if selected else "not_found" if not expired else "expired"
        visible_matches = [] if selected else matching[:50]
        return {
            "status": status,
            "company": matched_company or ({
                "company_id": company_id or derived_company_id,
                "canonical_name": company_name,
                "aliases": [company_name],
                "id_origin": "user_supplied" if company_id else "system_generated_from_company_name",
                "data_origin": "user_input",
                "review_status": "unreviewed",
            } if company_name else None),
            "selected_jd": selected,
            "jd_candidates": visible_matches,
            "items": [selected] if selected else visible_matches,
            "total": 1 if selected else len(matching),
            "limitations": [] if selected else (["More than one JD matches. Select a specific JD."] if matching else ["Matching JD records have expired and need refresh." if expired else "No company-specific JD is in the curated catalog. Generic role context is not a company JD."]),
        }

    def prepare(
        self,
        intake: dict,
        *,
        top_k: int = 20,
        company_override: dict | None = None,
        context: _PrepareContext | None = None,
    ) -> dict:
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        context = context or self._context()
        frameworks = context.snapshot.frameworks
        profile = intake["candidate_profile"]
        target = intake["interview_target"]
        role_id, role_options = self._resolve_role(target, frameworks)
        level = _matching_level(target.get("target_seniority"))
        if not target.get("company_name"):
            raise ValueError("interview_target.company_name is required before preparation")
        if not role_id:
            if role_options:
                raise ValueError("Role title is ambiguous; choose a role_id from: " + ", ".join(role_options))
            raise ValueError("Choose a supported role_id or role_title before preparation")
        if not level:
            raise ValueError("Choose a target_seniority before preparation")
        framework = frameworks[role_id]
        source_levels = {str(x).casefold() for x in framework.get("target_levels", [])}
        limitations: list[str] = []
        if level in _SUPPORTED_SOURCE_LEVELS and level not in source_levels:
            raise ValueError(f"Role {role_id} has no reviewed source for target level '{level}'")
        if level in {"mid", "senior"}:
            limitations.append("Role profile and rubric use the project-maintained generic level framework; this is not a company-specific benchmark.")

        role_profile = {
            "role_id": role_id,
            "role_title": framework.get("role_title"),
            "role_family": framework.get("role_family") or framework.get("major_category"),
            "target_seniority": level,
            "supported_source_levels": framework.get("target_levels", []),
            "expectations": claim_list(framework.get("target_competencies", [])),
            "interview_stages": framework.get("interview_stages", []),
            "source_refs": [f"interview_frameworks.json#{role_id}"],
            "review_status": "source_corpus_unreviewed",
        }

        generic_level = None
        if level in {"mid", "senior"}:
            import json

            rubric_path = self.data_dir / "it_seniority_rubrics.json"
            if rubric_path.exists():
                generic_level = json.loads(rubric_path.read_text(encoding="utf-8")).get("levels", {}).get(level)
            if generic_level:
                role_profile["expectations"] = [str(generic_level.get("expectations", "")), *role_profile["expectations"]]
                role_profile["review_status"] = "generic_project_framework"
                role_profile["source_refs"].append(f"it_seniority_rubrics.json#{level}")
        role_profile = scope_item("role_profile", role_profile)
        rubric_items = []
        if generic_level:
            for item in generic_level.get("competencies", []):
                rubric_items.append({
                    "competency_id": f"{role_id}:{level}:{item['id']}",
                    "name": item.get("name", item["id"]),
                    "description": item.get("description", ""),
                    "weight": weight_value(item.get("weight")),
                    "must_have": item.get("must_have", False),
                    "anchors": item.get("anchors", {}),
                    "source_ref": f"it_seniority_rubrics.json#{level}/{item['id']}",
                    "review_status": "generic_project_framework",
                })
        else:
            pillars = framework.get("evaluation_matrix", [])
            total = sum(weight_value(item.get("weight_percent")) or 0 for item in pillars) or 1
            anchors = {str(k): v for k, v in (framework.get("scoring_anchors") or {}).items() if str(k) in {"1", "2", "3", "4", "5"}}
            for index, item in enumerate(pillars):
                pillar_id = "competency:" + digest([role_id, item.get("pillar", index)])[:20]
                rubric_items.append({
                    "competency_id": pillar_id,
                    "name": item.get("pillar_en") or item.get("pillar"),
                    "description": item.get("criteria", ""),
                    "weight": weight_value(item.get("weight_percent")) / total if weight_value(item.get("weight_percent")) is not None else None,
                    "must_have": False,
                    "anchors": anchors,
                    "source_ref": f"interview_frameworks.json#{role_id}/evaluation_matrix/{index}",
                    "review_status": "source_corpus_unreviewed",
                })
        source_scale = 5 if any("5" in item.get("anchors", {}) for item in rubric_items) else 4
        raw_threshold = str((framework.get("passing_thresholds") or {}).get(level.title(), ""))
        threshold_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", raw_threshold)
        rubric = {
            "rubric_version": digest({"role_id": role_id, "level": level, "items": rubric_items})[:20],
            "role_id": role_id, "target_seniority": level, "scale": {"min": 1, "max": 4 if generic_level else source_scale},
            "pass_threshold": generic_level.get("pass_threshold", 3.0) if generic_level else float(threshold_match.group(1)) if threshold_match else 3.0,
            "competencies": rubric_items,
            "complete_for_scoring": bool(rubric_items) and all(item.get("anchors") for item in rubric_items),
            "source_refs": sorted({item["source_ref"] for item in rubric_items}),
        }
        rubric = scope_rubric(rubric, internal=True)
        rubric_items = rubric["competencies"]
        if not rubric["complete_for_scoring"]:
            limitations.append("One or more rubric criteria have no source scoring anchors; human review is required before using the pack to score.")

        company = _scoped_company(
            company_override or self._company_result(target, role_id, level, (context.companies, context.jobs))
        )
        selected_jd = company.get("selected_jd") or {}
        jd_context = " ".join([
            str(selected_jd.get("title") or ""),
            " ".join(str(x) for x in selected_jd.get("requirements", []) if isinstance(x, str)),
            str(selected_jd.get("description") or ""),
        ])
        query_text = " ".join([
            str(target.get("role_title") or framework.get("role_title") or ""),
            str(target.get("company_name") or ""),
            str(profile.get("summary") or ""),
            " ".join(profile.get("skills", [])),
            " ".join(str(x.get("text", "")) for x in profile.get("projects", []) if isinstance(x, dict)),
            str(target.get("jd_id") or ""),
            jd_context,
        ]).strip()
        raw_questions = []
        for question in framework.get("technical_questions", []) + framework.get("behavioral_questions", []):
            if str(question.get("level", "")).casefold() != level:
                continue
            question_text = claim_text(question.get("question"), 1800)
            if not question_text:
                continue
            score, overlap = _overlap_score(query_text, question_text + " " + " ".join(claim_list(question.get("key_concepts", []))))
            rubric_id = None
            pillars = rubric_items
            best = max(pillars, key=lambda item: _overlap_score(question_text, item["name"] + " " + item["description"])[1], default=None)
            if best and _overlap_score(question_text, best["name"] + " " + best["description"])[1] > 0:
                rubric_id = best["competency_id"]
            is_behavioral = "evaluation_focus" in question or "star_criteria" in question
            requested_round = str(intake["preferences"].get("interview_round") or "combined").casefold()
            if requested_round == "technical" and is_behavioral:
                continue
            if requested_round == "hr" and not is_behavioral:
                continue
            raw_questions.append({
                "question_id": str(question.get("id") or digest(question_text)[:16]),
                "role_id": role_id,
                "target_seniority": level,
                "competency_id": rubric_id,
                "stage_type": "behavioral" if is_behavioral else "technical",
                "difficulty": question.get("difficulty"),
                "question": question_text,
                "score": round(score, 4),
                "source_ref": f"interview_frameworks.json#{role_id}/{'behavioral_questions' if is_behavioral else 'technical_questions'}/{question.get('id', '')}",
                "review_status": "source_corpus_unreviewed",
                "evaluator_only": {
                    "expected_answer": question.get("expected_answer"),
                    "scoring_rubric": question.get("scoring_rubric"),
                    "evaluation_focus": question.get("evaluation_focus"),
                    "star_criteria": question.get("star_criteria"),
                },
                "_overlap": overlap,
            })
        raw_questions.sort(key=lambda item: (-item["score"], -item["_overlap"], item["question_id"]))
        selected_questions = raw_questions[:top_k]
        questions = [scope_item("question_bank", item) for item in selected_questions]
        selected_questions = [scope_item("question_bank", item, internal=True) for item in selected_questions]
        question_status = "retrieved" if questions else "unsupported_level"
        limitations.extend(company.get("limitations", []))
        covered_competencies = {item.get("competency_id") for item in questions if item.get("competency_id")}
        required_competencies = {item["competency_id"] for item in rubric_items}
        return {
            "schema_version": "1.0",
            "id_q": intake["id_q"],
            "intake_revision": intake["revision"],
            "knowledge_version": pack_version(context.knowledge_version, top_k, company),
            "company_knowledge": company,
            "role_profile": {"status": "ok", "items": [role_profile], "total": 1},
            "rubric": {"status": "ok" if rubric_items else "not_found", "items": rubric_items[:top_k], "total": len(rubric_items), "complete_for_scoring": rubric["complete_for_scoring"], "rubric": rubric},
            "question_bank": {"status": question_status, "items": questions, "total": len(raw_questions), "coverage_incomplete": not required_competencies.issubset(covered_competencies)},
            "_evaluator": {"rubric": rubric, "questions": selected_questions},
            "limitations": list(dict.fromkeys(limitations)),
        }

    async def prepare_async(
        self,
        intake: dict,
        *,
        top_k: int = 20,
        web_search_mode: str = "auto",
    ) -> dict:
        """Prepare a pack, using bounded public-web evidence only when catalog data is missing."""
        # Catalog parsing and pack building are synchronous; keep them off the event loop.
        # One context = one engine snapshot and one catalog parse for the whole call.
        context = await asyncio.to_thread(self._context)
        result = await asyncio.to_thread(lambda: self.prepare(intake, top_k=top_k, context=context))
        company = result["company_knowledge"]
        selected = company.get("selected_jd") or {}
        important_missing = (
            any(not selected.get(field) for field in ("title", "description", "requirements"))
            or not (selected.get("source_url") or selected.get("source_ref"))
        )
        needs_fallback = company.get("status") in {"not_found", "expired"} or important_missing
        if web_search_mode == "off" or (web_search_mode != "refresh" and not needs_fallback):
            company.setdefault("web_research", {
                "status": "disabled" if web_search_mode == "off" else "not_needed",
                "used_as_fallback": False,
                "cache_hit": False,
            })
            return self._set_pack_version(result, top_k, context)
        if self.web_research is None:
            company["web_research"] = {
                "status": "unavailable",
                "reason": "search_provider_not_configured",
                "used_as_fallback": False,
                "cache_hit": False,
            }
            result["limitations"] = list(dict.fromkeys([
                *result["limitations"],
                "Company web research is unavailable because no search provider is configured.",
            ]))
            return self._set_pack_version(result, top_k, context)

        target = intake["interview_target"]
        role_id, _ = self._resolve_role(target, context.snapshot.frameworks)
        level = _matching_level(target.get("target_seniority"))
        if not role_id or not level:
            return self._set_pack_version(result, top_k, context)
        research_target = dict(target)
        existing_company = company.get("company") or {}
        if (
            existing_company.get("company_id")
            and existing_company.get("id_origin") != "system_generated_from_company_name"
            and not research_target.get("company_id")
        ):
            research_target["company_id"] = existing_company["company_id"]
        researched = await self.web_research.research(
            research_target,
            role_id=role_id,
            level=level,
            refresh=web_search_mode == "refresh",
        )

        if company.get("status") == "selected":
            # Refresh can fill gaps but never overwrites curated values.
            researched_jd = researched.get("selected_jd") or {}
            merged_jd = dict(selected)
            for key, value in researched_jd.items():
                if merged_jd.get(key) in (None, "", []) and value not in (None, "", []):
                    merged_jd[key] = value
            merged_company = dict(company)
            merged_company["selected_jd"] = merged_jd
            merged_company["items"] = [merged_jd]
            merged_company["sources"] = researched.get("sources", [])
            merged_company["web_research"] = researched.get("web_research", {})
            merged_company["limitations"] = list(dict.fromkeys([
                *company.get("limitations", []),
                *researched.get("limitations", []),
            ]))
        else:
            merged_company = researched
            if existing_company and not existing_company.get("id_origin"):
                merged_company["company"] = {
                    **(researched.get("company") or {}),
                    **existing_company,
                }
        return await asyncio.to_thread(
            lambda: self.prepare(intake, top_k=top_k, company_override=merged_company, context=context)
        )

    @staticmethod
    def public_collection(preparation: dict, collection: str, top_k: int | None = None) -> dict:
        result = preparation["result"]
        if collection not in {"company_knowledge", "role_profile", "rubric", "question_bank"}:
            raise KeyError("Knowledge collection not found")
        value = _scoped_company(result[collection]) if collection == "company_knowledge" else scope_collection(collection, result[collection])
        response = {key: item for key, item in value.items() if key != "_evaluator"}
        if top_k is not None and isinstance(response.get("items"), list):
            if not 1 <= top_k <= 50:
                raise ValueError("top_k must be between 1 and 50")
            response["items"] = response.get("items", [])[:top_k]
        response.update({"id_q": preparation["id_q"], "pack_id": preparation["pack_id"], "intake_revision": preparation["intake_revision"], "knowledge_version": preparation["knowledge_version"], "collection": collection, "limitations": result.get("limitations", [])})
        return response

    @staticmethod
    def evaluator_pack(preparation: dict) -> dict:
        """Internal pack consumed by Core; evaluator-only material is never in public GETs."""
        result = preparation["result"]
        return {
            **{key: result[key] for key in ("id_q", "intake_revision", "knowledge_version", "company_knowledge", "role_profile", "question_bank", "limitations")},
            "company_knowledge": _scoped_company(result["company_knowledge"]),
            "role_profile": scope_collection("role_profile", result["role_profile"]),
            "question_bank": scope_collection("question_bank", result["question_bank"]),
            "evaluator": {
                "rubric": scope_rubric(result["_evaluator"].get("rubric", {}), internal=True),
                "questions": [clean for item in result["_evaluator"].get("questions", []) if (clean := scope_item("question_bank", item, internal=True))],
            },
        }
