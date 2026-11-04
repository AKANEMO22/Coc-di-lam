"""Base crawler interface and IT technology classification utilities."""
from __future__ import annotations

import abc
import re
import html
import logging
from typing import Any, List, Optional
import httpx

from rag.job_sync.models import JobItem

logger = logging.getLogger("app.job_sync.crawlers")

# Comprehensive dictionary of IT / Software Engineering skills
TECH_SKILLS_MAP = {
    # Languages
    "python": "Python", "java": "Java", "c#": "C#", "c++": "C++", "c/c++": "C/C++",
    "golang": "Go", "go lang": "Go", "javascript": "JavaScript", "typescript": "TypeScript",
    "php": "PHP", "ruby": "Ruby", "rust": "Rust", "kotlin": "Kotlin", "swift": "Swift",
    "dart": "Dart", "scala": "Scala", "solidity": "Solidity",
    # Frontend & Web
    "react": "React", "reactjs": "React", "react.js": "React", "next.js": "Next.js", "nextjs": "Next.js",
    "vue": "Vue.js", "vuejs": "Vue.js", "vue.js": "Vue.js", "angular": "Angular", "angularjs": "Angular",
    "svelte": "Svelte", "html5": "HTML5", "css3": "CSS3", "tailwind": "Tailwind CSS", "bootstrap": "Bootstrap",
    # Backend & Frameworks
    "node.js": "Node.js", "nodejs": "Node.js", "express": "Express.js", "nest.js": "NestJS", "nestjs": "NestJS",
    "django": "Django", "fastapi": "FastAPI", "flask": "Flask",
    "spring": "Spring Boot", "spring boot": "Spring Boot", ".net": ".NET", "asp.net": "ASP.NET", ".net core": ".NET Core",
    "laravel": "Laravel", "symfony": "Symfony", "ruby on rails": "Rails",
    # Mobile
    "flutter": "Flutter", "react native": "React Native", "ios": "iOS", "android": "Android",
    # Databases & Caches
    "sql": "SQL", "postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB",
    "redis": "Redis", "elasticsearch": "Elasticsearch", "cassandra": "Cassandra", "oracle": "Oracle DB",
    # Cloud & DevOps
    "docker": "Docker", "kubernetes": "Kubernetes", "k8s": "Kubernetes", "aws": "AWS", "azure": "Azure",
    "gcp": "Google Cloud", "google cloud": "Google Cloud", "ci/cd": "CI/CD", "jenkins": "Jenkins",
    "github actions": "GitHub Actions", "terraform": "Terraform", "ansible": "Ansible", "linux": "Linux",
    "kafka": "Kafka", "rabbitmq": "RabbitMQ", "microservices": "Microservices",
    # AI / Data
    "machine learning": "Machine Learning", "deep learning": "Deep Learning", "ai": "AI",
    "pytorch": "PyTorch", "tensorflow": "TensorFlow", "pandas": "Pandas", "numpy": "NumPy",
    "data engineer": "Data Engineering", "spark": "Apache Spark", "hadoop": "Hadoop",
    "rag": "RAG", "llm": "LLM", "nlp": "NLP", "computer vision": "Computer Vision",
    # QA & Testing
    "tester": "Tester", "manual test": "Manual Testing", "automation test": "Automation Testing",
    "selenium": "Selenium", "cypress": "Cypress", "playwright": "Playwright", "postman": "Postman", "jmeter": "JMeter",
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}


NOISE_LINE_PATTERNS = [
    re.compile(r"^(\d+|[IVXLCDM]+)$", re.IGNORECASE),
    re.compile(r"^(Vì sao Bạn nên ứng tuyển|Hồ sơ của Bạn sẽ được|Đối với các hồ sơ đáp ứng|Ứng viên vui lòng kiểm tra)", re.IGNORECASE),
    re.compile(r"^(Cập nhật ngay thông tin|Ai Yêu Miền|MB Careers|Fanpage)", re.IGNORECASE),
    re.compile(r"^(yêu cầu ứng viên ứng tuyển cần cung cấp|Thông tin cá nhân: Họ và tên|Số điện thoại và Email|Trình độ học vấn, Trường đã tốt nghiệp)", re.IGNORECASE),
    re.compile(r"^(Từ 3 - 5 kỹ năng nổi bật|Kinh nghiệm làm việc$)", re.IGNORECASE),
]


def clean_text(raw_input: Any) -> str:
    """Strip HTML tags, unescape entities, remove recruitment boilerplate, and format cleanly."""
    if not raw_input:
        return ""
    if isinstance(raw_input, list):
        items = []
        for x in raw_input:
            if isinstance(x, dict):
                items.append(x.get("benefitName") or x.get("name") or str(x))
            else:
                items.append(str(x))
        raw_html = "\n".join(items)
    else:
        raw_html = str(raw_input)

    text = re.sub(r"<style.*?</style>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    # Separate common jammed headings (e.g., "trách nhiệm của bạnXây dựng" -> "trách nhiệm của bạn:\nXây dựng")
    text = re.sub(r"(trách nhiệm của bạn|trình độ của bạn|quyền lợi)(?=[A-ZÀ-Ỹ])", r"\1:\n", text, flags=re.IGNORECASE)

    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop noise or pure boilerplate lines
        if any(p.search(line) for p in NOISE_LINE_PATTERNS):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def extract_skills_from_text(text: str) -> List[str]:
    """Extract recognized IT skills from text."""
    if not text:
        return []
    text_lower = f" {text.lower()} "
    found = set()
    for keyword, canonical in TECH_SKILLS_MAP.items():
        pattern = r"(?<![a-zA-Z0-9#+])" + re.escape(keyword) + r"(?![a-zA-Z0-9#+])"
        if re.search(pattern, text_lower):
            found.add(canonical)
    return sorted(list(found))


def detect_seniority(title: str, experience_text: str = "") -> str:
    """Infer seniority level from job title and experience requirement."""
    target = f"{title} {experience_text}".lower()
    if any(k in target for k in ["intern", "thực tập", "trainee", "sinh viên"]):
        return "Intern"
    if any(k in target for k in ["fresher", "mới tốt nghiệp", "0-1 năm", "entry level"]):
        return "Fresher"
    if any(k in target for k in ["lead", "trưởng nhóm", "tech lead", "team leader", "principal"]):
        return "Lead"
    if any(k in target for k in ["manager", "quản lý", "director", "head of", "trưởng phòng"]):
        return "Manager"
    if any(k in target for k in ["senior", "chuyên gia", "senior-level", "sr."]):
        return "Senior"
    if any(k in target for k in ["junior", "jr."]):
        return "Junior"
    if any(k in target for k in ["middle", "mid-level", "mid ", "2-4 năm", "3-5 năm"]):
        return "Middle"
    # Default to Junior if unspecified
    return "Junior"


def classify_category(title: str, skills: List[str] = None) -> str:
    """Categorize job into Software Engineering domain."""
    t = title.lower()
    s = [x.lower() for x in (skills or [])]

    if any(k in t for k in ["frontend", "front-end", "react", "vue", "angular"]) or "react" in s:
        return "Frontend"
    if any(k in t for k in ["backend", "back-end", "node", "django", "spring", "golang", "fastapi", "laravel"]):
        return "Backend"
    if any(k in t for k in ["fullstack", "full-stack", "full stack"]):
        return "Fullstack"
    if any(k in t for k in ["mobile", "android", "ios", "flutter", "react native", "swift"]):
        return "Mobile"
    if any(k in t for k in ["devops", "cloud", "sre", "infrastructure", "kubernetes", "aws", "azure", "docker"]):
        return "DevOps"
    if any(k in t for k in ["ai", "machine learning", "data", "deep learning", "nlp", "llm", "rag", "computer vision"]):
        return "AI/Data"
    if any(k in t for k in ["tester", "qa", "qc", "test", "quality assurance"]):
        return "QA/QC"
    if any(k in t for k in ["security", "bảo mật", "cyber", "pentest", "soc"]):
        return "Security"
    if any(k in t for k in ["bridge", "brse", "comtor", "tiếng nhật"]):
        return "BrSE"
    if any(k in t for k in ["ba", "business analyst", "product owner", "po", "pm", "project manager"]):
        return "Product/BA"

    return "Software Engineer"


def clean_title_and_company(title: str, company: str) -> tuple[str, str]:
    """Strip advertisements, recruitment prefixes, and embedded company noise."""
    t = title.strip()
    c = company.strip()
    
    # 1. Clean advertising slogans from company
    c = re.sub(r'\s+là\s+1\s+trong\s+những\s+.*$', '', c, flags=re.I)
    c = re.sub(r'\s+hàng\s+đầu\s+tại\s+Việt\s+Nam.*$', '', c, flags=re.I)
    c = re.sub(r'\s+-\s+.*(?:chứng khoán|ngân hàng|sản xuất phần mềm|hàng đầu).*$', '', c, flags=re.I)
    
    # 2. Extract embedded company from title if company is generic
    match = re.search(r'(?:làm việc\s+)?tại\s+((?:CÔNG\s+TY\s+[^()]+|Trung tâm\s+[^()]+|FPT\s+Software|CIC|RemotePeople|Softflex\s+LLC)[^()]*)', t, re.I)
    if match and any(w in c.lower() for w in ['topcv', 'topdev', 'doanh nghiệp']):
        extracted = match.group(1).strip()
        extracted = re.sub(r'\s+', ' ', extracted)
        extracted = re.sub(r'^(CÔNG\s+TY\s+TRÁCH\s+NHIỆM\s+HỮU\s+HẠN|CÔNG\s+TY\s+TNHH)\s*', 'Công ty TNHH ', extracted, flags=re.I)
        extracted = re.sub(r'^(CÔNG\s+TY\s+CỔ\s+PHẦN|CÔNG\s+TY\s+CP)\s*', 'Công ty Cổ phần ', extracted, flags=re.I)
        c = re.sub(r'[,.-]\s*$', '', extracted).strip()
        t = re.sub(r'\s+(?:làm việc\s+)?tại\s+.*$', '', t, flags=re.I)

    # 3. Clean prefixes and salary spam from title
    t = re.sub(r'^\s*Tuyển\s+(dụng\s+)?', '', t, flags=re.I)
    t = re.sub(r'\[Update[^\]]*\]', '', t, flags=re.I)
    t = re.sub(r'\s*\([^)]*(?:thu\s+nhập|lương|upto|kinh\s+nghiệm)[^)]*\)', '', t, flags=re.I)
    t = re.sub(r'\s*-\s*(?:thu\s+nhập|lương|upto)[^-,]*', '', t, flags=re.I)
    t = re.sub(r'[.\s]+$', '', t)
    
    return t.strip(), c.strip()


class BaseJobCrawler(abc.ABC):
    """Abstract base crawler for all job platforms."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique short name, e.g. 'topcv', 'itviec'."""
        pass

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'TopCV IT'."""
        pass

    @property
    @abc.abstractmethod
    def homepage(self) -> str:
        """Original job site URL."""
        pass

    @abc.abstractmethod
    async def crawl(self, keyword: str = "IT", limit: int = 30) -> List[JobItem]:
        """Fetch and return normalized IT job items."""
        pass

    async def fetch_page(self, client: httpx.AsyncClient, url: str, headers: Optional[dict] = None) -> str:
        """Polite HTTP GET helper."""
        req_headers = {**DEFAULT_HEADERS, **(headers or {})}
        resp = await client.get(url, headers=req_headers, timeout=12.0)
        resp.raise_for_status()
        return resp.text
