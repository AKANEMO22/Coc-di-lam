"""Bounded web fallback for missing public company and job-description metadata."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .indexing import digest
from .jd_fields import extract_jd_fields
from .ontology import tokens


_GENERIC_COMPANY_WORDS = {
    "company", "corporation", "corp", "group", "holdings", "limited", "ltd",
    "software", "technology", "technologies", "solutions", "services", "global",
}
_GENERIC_ROLE_WORDS = {
    "engineer", "engineering", "developer", "development", "software", "specialist",
    "junior", "senior", "intern", "fresher", "mid", "lead",
}
_JOB_SIGNALS = re.compile(
    r"\b(job|jobs|career|careers|vacancy|vacancies|position|opening|hiring|recruit|"
    r"apply|responsibilit|requirement|qualification|what you(?:'|’)ll do|mô tả công việc|"
    r"yêu cầu công việc|tuyển dụng)\b",
    re.I,
)
_EXPLICIT_JOB_PAGE = re.compile(
    r"\b(job description|job opening|job details?|career portal|vacancy|apply now|tuyển dụng)\b|/jobs?/",
    re.I,
)
_NON_JOB_PAGE = re.compile(r"/news(?:room)?/|press[-_/ ]release|/blog/|/articles?/", re.I)
_KNOWN_JOB_HOSTS = {
    "linkedin.com", "www.linkedin.com", "jobs.lever.co", "boards.greenhouse.io",
    "apply.workable.com", "jobs.ashbyhq.com", "myworkdayjobs.com",
}


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip()


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", _fold(value)).strip("-")
    return clean[:60] or "company"


def stable_company_id(company_name: str) -> str:
    """Create a stable internal identifier without claiming it came from the company."""
    suffix = hashlib.sha256(_fold(company_name).encode("utf-8")).hexdigest()[:8]
    return f"web_{_slug(company_name)}_{suffix}"


def _meaningful_words(value: str, ignored: set[str]) -> set[str]:
    return {word for word in tokens(_fold(value)) if len(word) > 2 and word not in ignored}


def _host_matches_company(host: str, company_name: str) -> bool:
    compact_host = re.sub(r"[^a-z0-9]", "", _fold(host))
    words = _meaningful_words(company_name, _GENERIC_COMPANY_WORDS)
    return any(word in compact_host for word in words)


def _trust_tier(url: str, company_name: str) -> str:
    host = urlparse(url).hostname or ""
    if _host_matches_company(host, company_name):
        # A matching hostname is useful evidence, but is not proof that a domain is official.
        return "company_domain_candidate"
    if host.casefold() in _KNOWN_JOB_HOSTS or any(host.casefold().endswith("." + item) for item in _KNOWN_JOB_HOSTS):
        return "recognized_job_platform"
    return "third_party_job_source"


def _source_score(source: dict[str, Any], company_name: str, role_title: str) -> tuple[int, bool]:
    title_url = " ".join([str(source.get("title") or ""), str(source.get("url") or "")])
    original_text = " ".join([
        str(source.get("title") or ""),
        str(source.get("snippet") or ""),
        str(source.get("content_excerpt") or ""),
        str(source.get("url") or ""),
    ])
    text = _fold(original_text)
    company_words = _meaningful_words(company_name, _GENERIC_COMPANY_WORDS)
    role_variants = [
        {word for word in tokens(_fold(variant)) if len(word) >= 2 and word not in _GENERIC_ROLE_WORDS}
        for variant in re.split(r"[()]", role_title) if variant.strip()
    ]
    role_words = set().union(*role_variants) if role_variants else set()
    company_hits = len(company_words & set(tokens(text)))
    role_hits = len(role_words & set(tokens(text)))
    # A brand can be represented in the hostname without appearing in a page excerpt.
    company_match = bool(company_hits) or _host_matches_company(urlparse(str(source.get("url") or "")).hostname or "", company_name)
    # Related-jobs links in the body do not identify this page's JD.
    title_words = set(tokens(_fold(str(source.get("title") or ""))))
    role_match = any(words and words.issubset(title_words) for words in role_variants) if role_words else bool(re.search(r"engineer|developer|analyst|tester", str(source.get("title") or ""), re.I))
    job_match = bool(_JOB_SIGNALS.search(original_text))
    explicit_job_page = bool(_EXPLICIT_JOB_PAGE.search(title_url))
    non_job_page = bool(_NON_JOB_PAGE.search(title_url)) and not explicit_job_page
    fetched = bool(source.get("content_excerpt"))
    company_domain = source.get("trust_tier") == "company_domain_candidate"
    score = company_hits * 3 + role_hits * 2 + int(job_match) * 3 + int(fetched) * 3 + int(company_domain) * 2
    return score, company_match and role_match and job_match and not non_job_page


@dataclass(frozen=True)
class WebResearchConfig:
    enabled: bool = True
    max_results: int = 4
    max_pages: int = 4
    cache_ttl_seconds: int = 86_400
    # Whole search + fetch budget; callers wait 20 s (core) to 180 s (gateway prepare).
    total_timeout_seconds: float = 12.0


async def _gather_until(coros: list, deadline: float) -> tuple[list[Any], bool]:
    """Run coroutines until the shared deadline; unfinished ones become TimeoutError."""
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    if not tasks:
        return [], False
    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    done, pending = await asyncio.wait(tasks, timeout=remaining)
    for task in pending:
        task.cancel()
    results: list[Any] = []
    for task in tasks:
        if task not in done or task.cancelled():
            results.append(asyncio.TimeoutError())
        elif task.exception() is not None:
            results.append(task.exception())
        else:
            results.append(task.result())
    return results, bool(pending)


class CompanyJDWebResearch:
    """Search and normalize public evidence without using private candidate data."""

    def __init__(self, search_tool: Any | None, store: Any, config: WebResearchConfig | None = None):
        self.search_tool = search_tool
        self.store = store
        self.config = config or WebResearchConfig()

    def _cache_key(self, target: dict[str, Any], role_id: str, level: str) -> str:
        return digest({
            "version": 3,
            "provider": getattr(self.search_tool, "provider_name", type(self.search_tool).__name__),
            "company": _fold(target.get("company_name") or ""),
            "role_id": role_id,
            "role_title": _fold(target.get("role_title") or ""),
            "level": level,
            "location": _fold(target.get("location") or ""),
        })

    async def research(
        self,
        target: dict[str, Any],
        *,
        role_id: str,
        level: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        company_name = str(target.get("company_name") or "").strip()
        role_title = str(target.get("role_title") or role_id).strip()
        company_id = str(target.get("company_id") or stable_company_id(company_name))
        company = {
            "company_id": company_id,
            "canonical_name": company_name,
            "aliases": [company_name],
            "id_origin": "user_supplied" if target.get("company_id") else "system_generated_from_company_name",
            "data_origin": "user_input",
            "review_status": "unreviewed",
        }
        base = {
            "status": "not_found",
            "company": company,
            "selected_jd": None,
            "jd_candidates": [],
            "items": [],
            "total": 0,
            "sources": [],
            "limitations": [],
            "web_research": {"status": "unavailable", "used_as_fallback": False, "cache_hit": False},
        }
        if not self.config.enabled:
            base["web_research"]["reason"] = "web_search_disabled"
            base["limitations"].append("Company web research is disabled.")
            return base
        if self.search_tool is None:
            base["web_research"]["reason"] = "search_provider_not_configured"
            base["limitations"].append("Company web research is unavailable because no search provider is configured.")
            return base

        cache_key = self._cache_key(target, role_id, level)
        if not refresh:
            cached = await asyncio.to_thread(self.store.get_web_research_cache, cache_key)
            if cached is not None:
                cached.setdefault("web_research", {})["cache_hit"] = True
                return cached

        location = str(target.get("location") or "").strip()
        suffix = f" {location}" if location else ""
        queries = [
            f'"{company_name}" "{role_title}" careers job{suffix}',
            f'"{company_name}" "{role_title}" job description requirements{suffix}',
            f'"{company_name}" {level} {role_title} hiring{suffix}',
        ]
        deadline = asyncio.get_running_loop().time() + max(0.0, float(self.config.total_timeout_seconds))
        search_results, timed_out = await _gather_until(
            [self.search_tool.search(query=query, max_results=self.config.max_results) for query in queries],
            deadline,
        )
        sources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        statuses: list[str] = []
        provider_reasons: list[str] = []
        for result in search_results:
            if isinstance(result, asyncio.TimeoutError):
                statuses.append("timeout")
                continue
            if isinstance(result, BaseException) or not isinstance(result, dict):
                statuses.append("error")
                continue
            statuses.append(str(result.get("status") or "unknown"))
            if result.get("reason"):
                provider_reasons.append(str(result["reason"]))
            for source in result.get("results") or []:
                if not isinstance(source, dict):
                    continue
                url = str(source.get("url") or source.get("source") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                normalized = dict(source)
                normalized["url"] = url
                normalized["source"] = url
                normalized["trust_tier"] = _trust_tier(url, company_name)
                normalized["verification_status"] = "unreviewed"
                sources.append(normalized)

        async def fetch(source: dict[str, Any]) -> tuple[dict[str, Any], Any]:
            result = await self.search_tool.search(
                query=str(source.get("query") or queries[0]),
                target_url=source["url"],
                max_results=1,
            )
            return source, result

        page_results, fetch_timed_out = await _gather_until(
            [fetch(source) for source in sources[: self.config.max_pages]],
            deadline,
        )
        timed_out = timed_out or fetch_timed_out
        for page_result in page_results:
            if isinstance(page_result, BaseException):
                continue
            source, fetched = page_result
            if isinstance(fetched, dict) and fetched.get("status") == "success":
                source["content_excerpt"] = str(fetched.get("content") or "")[:8_000]
                source["fetched_at"] = fetched.get("fetched_at")
                source["page_content_hash"] = fetched.get("content_hash")

        ranked = []
        for source in sources:
            score, matches = _source_score(source, company_name, role_title)
            source["match_score"] = score
            if matches:
                ranked.append(source)
        ranked.sort(key=lambda item: (-int(item.get("match_score") or 0), int(item.get("position") or 999)))

        candidates = []
        for source in ranked[:10]:
            candidate = self._job_from_source(
                source,
                company_id=company_id,
                company_name=company_name,
                role_id=role_id,
                role_title=role_title,
                level=level,
                location=location,
            )
            candidates.append(candidate)

        # Search snippets are useful leads but are not enough to become the selected JD.
        eligible = [item for item in candidates if item.get("evidence_quality") == "fetched_page" and (item.get("requirements") or item.get("responsibilities"))]
        selected = eligible[0] if eligible else None
        if len(eligible) > 1 and eligible[0]["confidence"] - eligible[1]["confidence"] >= 0.15:
            selected = eligible[0]
        if (
            len(eligible) > 1
            and eligible[0].get("trust_tier") == "company_domain_candidate"
            and eligible[1].get("trust_tier") != "company_domain_candidate"
        ):
            selected = eligible[0]
        visible = [] if selected else candidates
        status = "selected" if selected else "needs_selection" if candidates else "not_found"
        result = {
            **base,
            "status": status,
            "company": company,
            "selected_jd": selected,
            "jd_candidates": visible,
            "items": [selected] if selected else visible,
            "total": 1 if selected else len(visible),
            "sources": [self._public_source(source) for source in ranked[:10]],
            "limitations": (
                ["Web-derived company and JD metadata is unreviewed; verify it against the linked source before treating it as company policy."]
                if selected else
                ["Web search found possible JD sources, but none was uniquely strong enough to select automatically."]
                if candidates else
                ["No matching company-specific JD was found in the curated catalog or configured web search."]
            ) + (
                ["Company web research reached its time limit; results may be incomplete."]
                if timed_out else []
            ),
            "web_research": {
                "status": (
                    "available" if ranked else "timeout" if timed_out else "empty" if "success" in statuses
                    else "error" if "error" in statuses else "unavailable"
                ),
                "provider": getattr(self.search_tool, "provider_name", "custom"),
                "used_as_fallback": bool(selected or candidates),
                "cache_hit": False,
                "searched_at": time.time(),
                "queries": queries,
                "timed_out": timed_out,
            },
        }
        if provider_reasons:
            result["web_research"]["reason"] = provider_reasons[0]
        if ranked and not timed_out:
            # Partial (timed-out) evidence is returned but never cached as the answer.
            await asyncio.to_thread(self.store.set_web_research_cache, cache_key, result, self.config.cache_ttl_seconds)
        return result

    @staticmethod
    def _job_from_source(
        source: dict[str, Any],
        *,
        company_id: str,
        company_name: str,
        role_id: str,
        role_title: str,
        level: str,
        location: str,
    ) -> dict[str, Any]:
        url = str(source.get("url") or "")
        content = str(source.get("content_excerpt") or "")
        source_title = str(source.get("title") or "").strip()
        source_id = str(source.get("source_id") or "web_" + digest(url)[:20])
        fetched = bool(content)
        fields = extract_jd_fields(content)
        confidence = min(0.92, 0.42 + int(fetched) * 0.22 + min(int(source.get("match_score") or 0), 14) * 0.02)
        return {
            "jd_id": "web_jd_" + digest({"url": url, "role_id": role_id, "level": level})[:20],
            "company_id": company_id,
            "company_name": company_name,
            "title": role_title[:300],
            "title_origin": "user_input",
            "source_title": source_title[:400],
            "role_id": role_id,
            "target_seniority": level,
            "location": location,
            "description": "\n".join(fields["responsibilities"]),
            "responsibilities": fields["responsibilities"],
            "requirements": fields["requirements"],
            "extraction_status": "extracted" if any(fields.values()) else "insufficient_evidence",
            "source_url": url,
            "source_refs": [source_id],
            "data_origin": "web_search",
            "trust_tier": source.get("trust_tier") or "unreviewed",
            "verification_status": "unreviewed",
            "review_status": "web_unreviewed",
            "confidence": round(confidence, 2),
            "evidence_quality": "fetched_page" if fetched else "search_snippet",
            "collected_at": source.get("fetched_at") or source.get("collected_at"),
            "content_hash": source.get("page_content_hash") or source.get("content_hash"),
        }

    @staticmethod
    def _public_source(source: dict[str, Any]) -> dict[str, Any]:
        return {
            key: source.get(key)
            for key in (
                "source_id", "title", "url", "snippet", "trust_tier", "verification_status",
                "query", "position", "published_at", "source_age", "collected_at", "fetched_at",
                "content_hash", "page_content_hash", "match_score",
            )
            if source.get(key) is not None
        }
