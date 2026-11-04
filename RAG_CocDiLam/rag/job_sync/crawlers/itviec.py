"""ITviec crawler - parses IT jobs directly from itviec.com with deep detail extraction."""
from __future__ import annotations

import logging
import re
from typing import List, Tuple
from bs4 import BeautifulSoup
import httpx

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

logger = logging.getLogger("app.job_sync.itviec")


def extract_itviec_real_jd(html: str) -> Tuple[str, str, str, List[str], bool]:
    """Extract authentic job description, requirements, benefits, skills and active status from ITviec detail HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Job Description / Responsibilities
    desc = ""
    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(strip=True).lower()
        if "job description" in t or "mô tả công việc" in t:
            p = h.find_parent("div")
            if p:
                text = p.get_text(separator="\n", strip=True)
                desc = re.sub(r"^(?:job description|mô tả công việc)\s*", "", text, flags=re.IGNORECASE).strip()
                break

    # 2. Skills and experience / Requirements
    reqs = ""
    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(strip=True).lower()
        if "skills and experience" in t or "skills & experience" in t or "yêu cầu công việc" in t or "yêu cầu ứng viên" in t:
            p = h.find_parent("div")
            if p:
                text = p.get_text(separator="\n", strip=True)
                reqs = re.sub(r"^(?:your skills and experience|your skills & experience|yêu cầu công việc|yêu cầu ứng viên)\s*", "", text, flags=re.IGNORECASE).strip()
                break

    # 3. Benefits / Why you'll love working here
    bens_parts = []
    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(strip=True).lower()
        if "top 3 reasons" in t or "reasons to join" in t:
            p = h.find_parent("div")
            if p:
                text = p.get_text(separator="\n", strip=True)
                txt = re.sub(r"^(?:top 3 reasons to join us|reasons to join us)\s*", "", text, flags=re.IGNORECASE).strip()
                if txt:
                    bens_parts.append(txt)
            break

    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(strip=True).lower()
        if "why you'll love" in t or "why you will love" in t or "chế độ đãi ngộ" in t or "quyền lợi" in t:
            p = h.find_parent("div")
            if p:
                text = p.get_text(separator="\n", strip=True)
                txt = re.sub(r"^(?:why you\'ll love working here|why you will love working here|chế độ đãi ngộ|quyền lợi)\s*", "", text, flags=re.IGNORECASE).strip()
                if txt and txt not in bens_parts:
                    bens_parts.append(txt)
            break

    bens = "\n\n".join(bens_parts).strip()

    # 4. Authentic Skill Tags
    skills = []
    for a in soup.find_all("a", class_=lambda c: c and "itag" in c and "stretched-link" not in c):
        sk = a.get_text(strip=True)
        if sk and sk not in skills:
            skills.append(sk)

    # 5. Active Status
    page_text = soup.get_text().lower()
    is_active = True
    if any(m in page_text for m in ["việc làm đã hết hạn", "tin tuyển dụng đã hết hạn", "tin tuyển dụng này đã hết hạn", "ngừng nhận hồ sơ", "vị trí này đã đóng"]):
        is_active = False

    return desc, reqs, bens, skills, is_active


async def fetch_itviec_detail(url: str) -> Tuple[str, str, str, List[str], bool]:
    """Fetch detail page asynchronously via Browserbase or direct httpx."""
    html_content = ""
    try:
        html_content = await browserbase_client.fetch(url)
    except Exception:
        pass

    if not html_content:
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=DEFAULT_HEADERS)
                if resp.status_code == 200:
                    html_content = resp.text
                elif resp.status_code in [404, 410]:
                    return "", "", "", [], False
        except Exception:
            pass

    if html_content:
        return extract_itviec_real_jd(html_content)
    return "", "", "", [], True


class ITviecCrawler(BaseJobCrawler):
    @property
    def name(self) -> str:
        return "itviec"

    @property
    def display_name(self) -> str:
        return "ITviec"

    @property
    def homepage(self) -> str:
        return "https://itviec.com"

    async def crawl(self, keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        url = "https://itviec.com/it-jobs"
        if keyword and keyword.lower() not in ["it", "all"]:
            clean_kw = keyword.lower().replace(" ", "-")
            url = f"https://itviec.com/it-jobs/{clean_kw}"

        jobs: List[JobItem] = []
        html_content = ""

        # 1. Try Browserbase Cloud Fetch first
        try:
            logger.info(f"Fetching ITviec jobs via Browserbase: {url}")
            html_content = await browserbase_client.fetch(url)
        except Exception as exc:
            logger.warning(f"Browserbase Fetch ITviec failed: {exc}")

        # 2. Fallback to direct httpx if Browserbase returned empty
        if not html_content:
            logger.info("Falling back to direct httpx for ITviec")
            async with httpx.AsyncClient(timeout=15.0) as client:
                try:
                    resp = await client.get(url, headers=DEFAULT_HEADERS)
                    if resp.status_code == 200:
                        html_content = resp.text
                    else:
                        logger.warning(f"ITviec direct returned status {resp.status_code}")
                except Exception as e:
                    logger.error(f"Error direct crawling ITviec: {e}")

        if not html_content:
            return []

        try:
            soup = BeautifulSoup(html_content, "html.parser")
            h3_tags = soup.find_all("h3")

            for h3 in h3_tags:
                if len(jobs) >= limit:
                    break
                a_tag = h3.find("a")
                if not a_tag or not a_tag.get("href"):
                    continue

                title = a_tag.get_text(strip=True)
                if not title or len(title) < 4:
                    continue

                href = a_tag.get("href", "")
                if href.startswith("/"):
                    job_url = f"https://itviec.com{href}"
                else:
                    job_url = href

                slug_match = re.search(r"/it-jobs/([a-zA-Z0-9\-]+)", job_url)
                ext_id = slug_match.group(1) if slug_match else job_url

                parent = h3.find_parent("div", class_=lambda c: c and ("job-card" in c or "search-job-item" in c or "job_content" in c or "border" in c))
                if not parent:
                    parent = h3.find_parent("div")

                company = "Doanh nghiệp IT"
                logo_url = None
                salary = "Thương lượng"
                location = "Hồ Chí Minh / Hà Nội"
                is_active = True

                if parent:
                    parent_text = parent.get_text().lower()
                    if any(m in parent_text for m in ["hết hạn", "đã đóng", "expired", "closed"]):
                        is_active = False

                    comp_link = parent.find("a", href=lambda h: h and "/companies/" in h)
                    if comp_link:
                        company_text = comp_link.get_text(strip=True) or comp_link.get("title")
                        if company_text:
                            company = re.sub(r"\s+là 1 trong những.*$", "", company_text, flags=re.IGNORECASE).strip()

                    img_tag = parent.find("img")
                    if img_tag:
                        logo_url = img_tag.get("data-src") or img_tag.get("src")

                    salary_div = parent.find(class_=lambda c: c and "salary" in c)
                    if salary_div:
                        sal_text = salary_div.get_text(strip=True)
                        if "Sign in to view salary" in sal_text or "Đăng nhập" in sal_text:
                            salary = "Thương lượng"
                        elif sal_text:
                            salary = sal_text

                # Deep detail fetch for authentic JD
                real_desc, real_reqs, real_bens, real_skills, detail_active = await fetch_itviec_detail(job_url)

                skills_list = real_skills if real_skills else extract_skills_from_text(f"{title} {href}")
                seniority = detect_seniority(title)
                category = classify_category(title, skills_list)
                job_id = generate_job_id(self.name, ext_id, title, company)

                final_desc = real_desc if real_desc else f"Vị trí {title} tại {company}."
                final_reqs = real_reqs if real_reqs else f"Yêu cầu chuyên môn: {', '.join(skills_list) if skills_list else 'Chuyên ngành IT'}."
                final_bens = real_bens if real_bens else "Chế độ đãi ngộ theo quy định doanh nghiệp."
                final_active = is_active and detail_active

                job = JobItem(
                    id=job_id,
                    source=self.display_name,
                    external_id=ext_id,
                    title=title,
                    company=company,
                    company_logo=logo_url,
                    location=location,
                    salary=salary,
                    experience="Theo thỏa thuận",
                    seniority=seniority,
                    job_category=category,
                    skills=skills_list,
                    source_url=job_url,
                    description=final_desc,
                    requirements=final_reqs,
                    benefits=final_bens,
                    is_active=final_active,
                )
                jobs.append(job)

        except Exception as e:
            logger.error(f"Error parsing ITviec HTML: {e}", exc_info=True)

        return jobs
