"""TopCV IT Job crawler and sync module using Browserbase with Deep JD Extraction."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import List
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

logger = logging.getLogger("app.job_sync.topcv")


async def extract_topcv_detail(url: str) -> dict:
    """Extract detailed JD from TopCV page."""
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
        # Browserbase fetch
        html = await browserbase_client.fetch(url, timeout=20.0)
        if not html:
            # Fallback direct httpx
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
                    if r.status_code == 200:
                        html = r.text
            except Exception:
                pass

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
                    if data.get("description"):
                        details["description"] = clean_text(data["description"])
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"TopCV detail fetch error: {e}")
    return details


class TopCVCrawler(BaseJobCrawler):
    @property
    def name(self) -> str:
        return "topcv"

    @property
    def display_name(self) -> str:
        return "TopCV"

    @property
    def homepage(self) -> str:
        return "https://www.topcv.vn"

    async def crawl(self, keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        jobs: List[JobItem] = []

        try:
            query = f"site:topcv.vn/viec-lam {keyword} tuyển dụng lập trình"
            search_results = await browserbase_client.search(query, num_results=min(limit, 20))
            valid_items = [
                item for item in search_results
                if item.get("title") and re.search(r"/viec-lam/[^/]+/(\d+)\.html", item.get("url", ""))
            ]

            async def process_item(item):
                title = item.get("title", "").split(" - ")[0].split(" | ")[0].strip()
                url = item.get("url", "")
                detail = await extract_topcv_detail(url)

                ext_match = re.search(r"/viec-lam/[^/]+/(\d+)\.html", url)
                ext_id = ext_match.group(1) if ext_match else url
                company = detail.get("company") or "Doanh nghiệp IT trên TopCV"
                location = detail.get("location") or "Hà Nội / TP.HCM"
                description = detail.get("description") or f"Tin tuyển dụng thực tế {title} tại {company} từ nền tảng TopCV."
                requirements = detail.get("requirements") or f"Yêu cầu kỹ năng chuyên môn: {title}."
                skills = detail.get("skills") or extract_skills_from_text(f"{title} {description}")
                seniority = detect_seniority(title)
                category = classify_category(title, skills)
                job_id = generate_job_id(self.name, ext_id, title, company)

                return JobItem(
                    id=job_id,
                    source=self.display_name,
                    external_id=ext_id,
                    title=title,
                    company=company,
                    location=location,
                    salary=detail.get("salary") or "Thương lượng",
                    seniority=seniority,
                    job_category=category,
                    skills=skills,
                    source_url=url,
                    description=description,
                    requirements=requirements,
                    benefits="Chế độ đãi ngộ TopCV.",
                    is_active=detail.get("is_active", True),
                )

            scraped = await asyncio.gather(*[process_item(item) for item in valid_items[:limit]], return_exceptions=True)
            for j in scraped:
                if isinstance(j, JobItem):
                    jobs.append(j)
        except Exception as e:
            logger.warning(f"Browserbase Search for TopCV failed: {e}")

        if not jobs:
            logger.info("Không tìm thấy việc làm nào trên TopCV cho từ khóa '%s'.", keyword)

        return jobs
