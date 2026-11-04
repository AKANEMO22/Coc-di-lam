"""TopDev IT Job Crawler using Browserbase with Deep JD Extraction."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup

from rag.job_sync.crawlers.base import (
    BaseJobCrawler,
    clean_text,
    classify_category,
    detect_seniority,
    extract_skills_from_text,
    DEFAULT_HEADERS,
)
from rag.job_sync.crawlers.browserbase_client import browserbase_client
from rag.job_sync.models import JobItem, generate_job_id

logger = logging.getLogger("app.job_sync.topdev")


async def extract_topdev_detail(url: str) -> dict:
    """Fetch and parse detailed JD information from a TopDev job page."""
    details = {
        "company": "",
        "location": "",
        "salary": "",
        "description": "",
        "requirements": "",
        "benefits": "",
        "skills": [],
        "is_active": True,
    }
    try:
        # First attempt via direct httpx for TopDev (fast, handles static LD-JSON)
        html = ""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, follow_redirects=True)
                if r.status_code == 200:
                    html = r.text
        except Exception:
            pass

        # Fallback to Browserbase if direct failed
        if not html:
            html = await browserbase_client.fetch(url, timeout=25.0)

        if not html:
            return details

        soup = BeautifulSoup(html, "html.parser")

        # Expiry Check 1: Title
        if soup.title and soup.title.string:
            title_str = soup.title.string.lower()
            if any(h in title_str for h in ["hết hạn", "het han", "đã đóng", "da dong", "expired"]):
                details["is_active"] = False

        # Expiry Check 2: Page Text & Markers
        page_text = soup.get_text().lower()
        if any(m in page_text for m in ["đã hết hạn", "hết hạn ứng tuyển", "hết hạn nộp hồ sơ", "tin tuyển dụng đã đóng", "vị trí này đã đóng"]):
            details["is_active"] = False

        # 1. Parse JSON-LD metadata for JobPosting
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.string)
                if data.get("@type") == "JobPosting":
                    # Expiry Check 3: JSON-LD validThrough
                    valid_through = data.get("validThrough")
                    if valid_through:
                        vt_str = str(valid_through)[:10]
                        try:
                            vt_date = datetime.strptime(vt_str, "%Y-%m-%d").date()
                            if vt_date < datetime.now().date():
                                details["is_active"] = False
                        except Exception:
                            pass

                    if data.get("hiringOrganization", {}).get("name"):
                        details["company"] = data["hiringOrganization"]["name"].strip()
                    addr = data.get("jobLocation", {}).get("address", {})
                    if addr.get("addressRegion"):
                        details["location"] = addr["addressRegion"]
                    base_sal = data.get("baseSalary", {})
                    if base_sal:
                        raw_val = base_sal.get("value")
                        unit = base_sal.get("currency", "VND")
                        if isinstance(raw_val, dict):
                            sal_text = raw_val.get("value") or (
                                f"{raw_val.get('minValue', '')} - {raw_val.get('maxValue', '')} {unit}".strip()
                                if raw_val.get("minValue") else ""
                            )
                        else:
                            sal_text = str(raw_val or "").strip()
                            if sal_text and unit and unit.lower() not in sal_text.lower():
                                sal_text = f"{sal_text} {unit}"
                        if sal_text:
                            details["salary"] = sal_text
                    if data.get("description"):
                        details["description"] = clean_text(data["description"])
            except Exception:
                pass

        # 2. Extract description if missing
        if not details["description"]:
            for selector in [
                ".job-description",
                ".job-detail__body",
                "#job-description",
                ".content-description",
            ]:
                elem = soup.select_one(selector)
                if elem:
                    details["description"] = clean_text(elem.get_text(separator="\n"))
                    break

        # 3. Extract requirements if missing
        for selector in [
            ".job-requirements",
            ".job-detail__requirements",
            "#job-requirements",
        ]:
            elem = soup.select_one(selector)
            if elem:
                details["requirements"] = clean_text(elem.get_text(separator="\n"))
                break

        # 4. Split Benefits out of Description if bundled
        raw_desc = details.get("description", "")
        if "benefits for you" in raw_desc.lower():
            parts = re.split(r"(?i)benefits\s+for\s+you[:\s]*", raw_desc, maxsplit=1)
            details["description"] = parts[0].strip()
            if len(parts) > 1 and parts[1].strip() and not details.get("benefits"):
                details["benefits"] = parts[1].strip()

        # 5. Extract skills
        all_text = f"{details['description']} {details['requirements']}"
        details["skills"] = extract_skills_from_text(all_text)

    except Exception as exc:
        logger.warning(f"Error extracting TopDev detail from {url}: {exc}")

    return details


class TopDevCrawler(BaseJobCrawler):
    @property
    def name(self) -> str:
        return "topdev"

    @property
    def display_name(self) -> str:
        return "TopDev"

    @property
    def homepage(self) -> str:
        return "https://topdev.vn"

    async def crawl(self, keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        jobs: List[JobItem] = []

        try:
            query = f"site:topdev.vn/viec-lam {keyword} developer engineer"
            search_results = await browserbase_client.search(query, num_results=min(limit, 20))
            
            def is_actual_job_url(u: str) -> bool:
                if not u or "topdev.vn/viec-lam" not in u:
                    return False
                bad = ["tim-kiem", "search", "ordering=", "limit=", "page_size=", "?page=", "cong-ty", "tag/", "100-viec-lam"]
                if any(b in u.lower() for b in bad):
                    return False
                return bool(re.search(r"-\d+$", u.split("?")[0]))

            valid_items = [
                item for item in search_results
                if item.get("title") and is_actual_job_url(item.get("url", ""))
            ]

            async def process_item(item):
                title = item.get("title", "").split(" - ")[0].split(" | ")[0].strip()
                url = item.get("url", "")
                detail = await extract_topdev_detail(url)

                description = detail.get("description", "").strip()
                requirements = detail.get("requirements", "").strip()
                if len(description) < 40 and len(requirements) < 40:
                    description = f"Tuyển dụng {title} tại TopDev."
                    requirements = f"Yêu cầu chuyên môn liên quan đến {title}."

                company = detail.get("company") or "Doanh nghiệp công nghệ trên TopDev"
                location = detail.get("location") or "Việt Nam"
                salary = detail.get("salary") or "Thương lượng"
                benefits = detail.get("benefits") or "Chế độ đãi ngộ chuẩn công nghệ."
                
                skills = detail.get("skills") or extract_skills_from_text(f"{title} {description} {requirements}")
                seniority = detect_seniority(title, requirements)
                category = classify_category(title, skills)
                ext_id = url
                job_id = generate_job_id(self.name, ext_id, title, company)

                return JobItem(
                    id=job_id,
                    source=self.display_name,
                    external_id=ext_id,
                    title=title,
                    company=company,
                    location=location,
                    salary=salary,
                    seniority=seniority,
                    job_category=category,
                    skills=skills,
                    source_url=url,
                    description=description,
                    requirements=requirements,
                    benefits=benefits,
                    is_active=detail.get("is_active", True),
                )

            scraped = await asyncio.gather(*[process_item(item) for item in valid_items[:limit]], return_exceptions=True)
            for j in scraped:
                if isinstance(j, JobItem):
                    jobs.append(j)
        except Exception as e:
            logger.warning(f"Browserbase Search for TopDev failed: {e}")

        if not jobs:
            logger.info("Không tìm thấy việc làm nào trên TopDev cho từ khóa '%s'.", keyword)

        return jobs
