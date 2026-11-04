"""VietnamWorks IT job crawler using VietnamWorks public search API."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import List
import httpx

from rag.job_sync.crawlers.base import (
    BaseJobCrawler,
    clean_text,
    classify_category,
    detect_seniority,
    extract_skills_from_text,
)
from rag.job_sync.models import JobItem, generate_job_id

logger = logging.getLogger("app.job_sync.vietnamworks")


class VietnamWorksCrawler(BaseJobCrawler):
    @property
    def name(self) -> str:
        return "vietnamworks"

    @property
    def display_name(self) -> str:
        return "VietnamWorks"

    @property
    def homepage(self) -> str:
        return "https://www.vietnamworks.com"

    async def crawl(self, keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        endpoint = "https://ms.vietnamworks.com/job-search/v1.0/search"
        payload = {
            "userId": 0,
            "query": keyword or "Software Developer",
            "filter": [],
            "ranges": [],
            "order": [],
            "hitsPerPage": min(limit, 50),
            "page": 0,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-source": "Page-Search",
        }

        jobs: List[JobItem] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(endpoint, json=payload, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"VietnamWorks returned status {resp.status_code}")
                    return []
                data = resp.json()
                raw_jobs = data.get("data", [])
                for item in raw_jobs:
                    title = item.get("jobTitle", "").strip()
                    if not title:
                        continue
                    company = item.get("companyName", "Doanh nghiệp tuyển dụng").strip()
                    ext_id = str(item.get("jobId") or item.get("jobUrl", ""))
                    job_url = item.get("jobUrl", "")
                    if job_url and not job_url.startswith("http"):
                        job_url = f"https://www.vietnamworks.com{job_url}"
                    
                    salary = item.get("prettySalary") or item.get("salary") or "Thương lượng"
                    raw_desc = item.get("jobDescription", "")
                    raw_req = item.get("jobRequirement", "")
                    clean_desc = clean_text(raw_desc)
                    clean_req = clean_text(raw_req)

                    # Extract skills
                    skills_list = []
                    for s in item.get("skills", []):
                        if isinstance(s, dict) and s.get("skillName"):
                            skills_list.append(s["skillName"])
                    if not skills_list:
                        skills_list = extract_skills_from_text(f"{title} {clean_desc} {clean_req}")

                    # Location
                    location = "Toàn quốc"
                    loc_list = item.get("locations", [])
                    if loc_list and isinstance(loc_list, list):
                        loc_names = [l.get("cityName") for l in loc_list if isinstance(l, dict) and l.get("cityName")]
                        if loc_names:
                            location = ", ".join(loc_names)

                    # Expiration detection
                    is_active = True
                    if item.get("isActive") is False or item.get("isOnline") is False:
                        is_active = False

                    expired_on = item.get("expiredOn")
                    if expired_on:
                        try:
                            exp_dt = datetime.fromisoformat(expired_on)
                            if exp_dt < datetime.now(timezone.utc):
                                is_active = False
                        except Exception:
                            pass

                    seniority = detect_seniority(title, clean_req)
                    category = classify_category(title, skills_list)
                    job_id = generate_job_id(self.name, ext_id, title, company)

                    job = JobItem(
                        id=job_id,
                        source=self.display_name,
                        external_id=ext_id,
                        title=title,
                        company=company,
                        company_logo=item.get("companyLogo"),
                        location=location,
                        salary=salary,
                        experience=item.get("experience", "Theo yêu cầu"),
                        seniority=seniority,
                        job_category=category,
                        skills=skills_list,
                        source_url=job_url,
                        description=clean_desc,
                        requirements=clean_req,
                        benefits=clean_text(item.get("benefits", "")),
                        posted_at=item.get("approvedOn") or item.get("publishedOn") or "",
                        is_active=is_active,
                    )
                    jobs.append(job)
            except Exception as e:
                logger.error(f"Error crawling VietnamWorks: {e}", exc_info=True)

        return jobs
