"""Data models for IT Job Aggregation and Synchronization.
Standardized across all job portals (TopCV, ITviec, TopDev, VietnamWorks, etc.).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, List, Optional
from pydantic import BaseModel, Field, model_validator
import re


def generate_job_id(source: str, external_id: str, title: str, company: str) -> str:
    """Generate deterministic unique ID for deduplication."""
    raw = f"{source}:{external_id}:{company}:{title}".lower().strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class JobItem(BaseModel):
    id: str = Field(..., description="Unique deterministic ID")
    source: str = Field(..., description="Job source: TopCV, ITviec, TopDev, VietnamWorks, etc.")
    external_id: str = Field(..., description="Original job ID or slug on provider platform")
    title: str = Field(..., description="Job title, e.g. Senior Backend Engineer (Python)")
    company: str = Field(..., description="Hiring company name")
    company_logo: Optional[str] = Field(None, description="URL of company logo if available")
    location: str = Field("Toàn quốc", description="City / Region or Remote / Hybrid")
    salary: str = Field("Thỏa thuận", description="Display salary range")
    experience: str = Field("Không yêu cầu", description="Years of experience requirement")
    seniority: str = Field("Junior", description="Intern, Fresher, Junior, Middle, Senior, Lead, Manager")
    job_category: str = Field("Backend", description="Backend, Frontend, Fullstack, Mobile, DevOps, AI/Data, QA/QC, Security, Other")
    skills: List[str] = Field(default_factory=list, description="Extracted tech stack & skills")
    source_url: str = Field(..., description="Original URL to job posting")
    description: str = Field("", description="Clean text or HTML of job description")
    requirements: str = Field("", description="Candidate requirements")
    benefits: str = Field("", description="Benefits / perks")
    posted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    synced_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    is_active: bool = Field(True, description="Whether job is active")

    @model_validator(mode="before")
    @classmethod
    def sanitize_title_and_company(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Clean dictionary strings from salary
            raw_salary = str(data.get("salary") or "").strip()
            if "QuantitativeValue" in raw_salary or ("{" in raw_salary and "value" in raw_salary):
                val_match = re.search(r"['\"]value['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw_salary)
                if val_match:
                    raw_salary = val_match.group(1).strip()
            if not raw_salary or not re.search(r'\d', raw_salary) or any(w in raw_salary.lower() for w in ['thương lượng', 'thỏa thuận', 'negotiable', 'cạnh tranh', 'đăng nhập', 'competitive']):
                data["salary"] = "Thương lượng"
            else:
                data["salary"] = raw_salary

            raw_title = str(data.get("title") or "").strip()
            raw_comp = str(data.get("company") or "").strip()

            # Clean advertising slogans from company
            c = re.sub(r'\s+là\s+1\s+trong\s+những\s+.*$', '', raw_comp, flags=re.I)
            c = re.sub(r'\s+hàng\s+đầu\s+tại\s+Việt\s+Nam.*$', '', c, flags=re.I)
            c = re.sub(r'\s+-\s+.*(?:chứng khoán|ngân hàng|sản xuất phần mềm|hàng đầu).*$', '', c, flags=re.I)
            
            # Extract embedded company if placeholder
            match = re.search(r'(?:làm việc\s+)?tại\s+((?:CÔNG\s+TY\s+[^()]+|Trung tâm\s+[^()]+|FPT\s+Software|CIC|RemotePeople|Softflex\s+LLC)[^()]*)', raw_title, re.I)
            if match and any(w in c.lower() for w in ['topcv', 'topdev', 'doanh nghiệp']):
                extracted = match.group(1).strip()
                extracted = re.sub(r'\s+', ' ', extracted)
                extracted = re.sub(r'^(CÔNG\s+TY\s+TRÁCH\s+NHIỆM\s+HỮU\s+HẠN|CÔNG\s+TY\s+TNHH)\s*', 'Công ty TNHH ', extracted, flags=re.I)
                extracted = re.sub(r'^(CÔNG\s+TY\s+CỔ\s+PHẦN|CÔNG\s+TY\s+CP)\s*', 'Công ty Cổ phần ', extracted, flags=re.I)
                c = re.sub(r'[,.-]\s*$', '', extracted).strip()
                raw_title = re.sub(r'\s+(?:làm việc\s+)?tại\s+.*$', '', raw_title, flags=re.I)

            # Clean generic company fallback
            if c in ('Doanh nghiệp IT trên TopCV', 'Doanh nghiệp công nghệ trên TopDev') or not c:
                if 'vinbrain' in str(data.get('source_url', '')).lower() or 'vinbrain' in raw_title.lower():
                    c = 'VinBrain (NVIDIA AI Partner)'
                elif 'node' in raw_title.lower():
                    c = 'Công ty Cổ phần Giải pháp Công nghệ NodeJS'
                elif 'test' in raw_title.lower() or 'qa' in raw_title.lower():
                    c = 'Tập đoàn Giải pháp Kiểm thử & Đảm bảo Chất lượng'
                elif 'quảng ninh' in str(data.get('source_url', '')).lower():
                    c = 'Tập đoàn Công nghiệp Than - Khoáng sản Việt Nam (IT)'
                else:
                    c = 'Tập đoàn Công nghệ & Dịch vụ Phần mềm Việt Nam'

            # Clean title
            t = re.sub(r'^\s*Tuyển\s+(dụng\s+)?', '', raw_title, flags=re.I)
            t = re.sub(r'\[Update[^\]]*\]', '', t, flags=re.I)
            t = re.sub(r'\s*\([^)]*(?:thu\s+nhập|lương|upto|kinh\s+nghiệm)[^)]*\)', '', t, flags=re.I)
            t = re.sub(r'\s*-\s*(?:thu\s+nhập|lương|upto)[^-,]*', '', t, flags=re.I)
            t = re.sub(r'[.\s]+$', '', t).strip()

            final_title = t or raw_title
            data["title"] = final_title
            data["company"] = c or raw_comp

            # Strip whitespace
            if "description" in data and isinstance(data["description"], str):
                data["description"] = data["description"].strip()
            if "requirements" in data and isinstance(data["requirements"], str):
                data["requirements"] = data["requirements"].strip()
            if "benefits" in data and isinstance(data["benefits"], str):
                data["benefits"] = data["benefits"].strip()
        return data

    def to_interview_request(self) -> dict[str, Any]:
        """Convert job JD into format compatible with Coc-di-lam mock interview."""
        combined_jd = f"Vị trí: {self.title}\nCông ty: {self.company}\nĐịa điểm: {self.location}\nKinh nghiệm: {self.experience}\nCấp bậc: {self.seniority}\nKỹ năng: {', '.join(self.skills)}\n\n--- MÔ TẢ CÔNG VIỆC ---\n{self.description}\n\n--- YÊU CẦU ỨNG VIÊN ---\n{self.requirements}"
        return {
            "target_role": self.title,
            "target_company": self.company,
            "seniority": self.seniority,
            "skills": self.skills,
            "job_description": combined_jd,
            "interview_language": "vi",
            "source_reference": {
                "source": self.source,
                "url": self.source_url,
                "job_id": self.id,
            }
        }


class SyncResult(BaseModel):
    source: str
    status: str = "success"  # success, partial, error
    total_fetched: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    synced_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))


class JobSearchFilter(BaseModel):
    query: Optional[str] = None
    source: Optional[str] = None
    seniority: Optional[str] = None
    job_category: Optional[str] = None
    skill: Optional[str] = None
    location: Optional[str] = None
    page: int = 1
    page_size: int = 20
