"""Pre-session CV check: what the extracted text already gives, and what the candidate still has to supply.

The interview needs a target company, role and level before ID_Q can prepare the knowledge pack. A CV
rarely states the company it is aimed at, and scanned PDFs have no text layer at all, so the browser
runs this check right after upload and asks for the missing fields before a session is created.
Nothing is stored here; the result is a best-effort reading of the text, never a verified fact.
"""
from __future__ import annotations

import re
from typing import Any

MIN_READABLE_CHARS = 200

_SECTIONS = {
    "experience": ("experience", "work experience", "employment", "professional experience", "kinh nghiệm",
                   "kinh nghiệm làm việc"),
    "education": ("education", "học vấn", "trình độ học vấn"),
    "skills": ("skills", "technical skills", "kỹ năng", "kĩ năng"),
    "projects": ("projects", "project experience", "dự án"),
    "summary": ("summary", "profile", "about me", "giới thiệu", "tóm tắt", "objective", "career objective",
                "mục tiêu nghề nghiệp"),
}
_ROLE_WORDS = (
    "engineer", "developer", "intern", "analyst", "scientist", "designer", "tester", "qa", "devops",
    "architect", "manager", "lead", "specialist", "consultant", "administrator", "kỹ sư", "lập trình viên",
    "thực tập sinh", "chuyên viên", "nhân viên",
)
_LABELLED_ROLE = re.compile(
    r"^\s*(?:current role|current position|position|title|desired position|vị trí hiện tại|vị trí ứng tuyển|"
    r"vị trí mong muốn|chức danh)\s*[:：-]\s*(.{2,120})$", re.I)
_LABELLED_NAME = re.compile(r"^\s*(?:name|full name|họ tên|họ và tên|ho ten)\s*[:：-]\s*(.{2,100})$", re.I)
_YEARS = re.compile(r"\b(\d{1,2}(?:[.,]\d)?)\s*\+?\s*(?:years?|yrs?|năm)\s+(?:of\s+)?(?:experience|kinh nghiệm)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?84|0)(?:[\s.-]?\d){8,10}\b")
_SKILL_SPLIT = re.compile(r"[,;|•·\n]")
_KNOWN_SKILLS = (
    "python", "java", "javascript", "typescript", "react", "node.js", "nodejs", "vue", "angular", "c++", "c#",
    "golang", "go", "rust", "php", "ruby", "kotlin", "swift", "sql", "mysql", "postgresql", "mongodb", "redis",
    "docker", "kubernetes", "aws", "azure", "gcp", "linux", "git", "fastapi", "django", "flask", "spring",
    ".net", "html", "css", "tailwind", "pytorch", "tensorflow", "machine learning", "deep learning", "nlp",
    "pandas", "numpy", "excel", "power bi", "tableau", "figma", "selenium", "graphql", "rest", "kafka",
)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _heading(line: str) -> str | None:
    value = line.strip().strip("#:*-_ ").casefold()
    for section, names in _SECTIONS.items():
        if value in names:
            return section
    return None


def _name(lines: list[str]) -> str | None:
    for line in lines[:40]:
        match = _LABELLED_NAME.match(line)
        if match:
            return match.group(1).strip()
    # The first short line made only of letters is usually the name heading of a CV.
    for line in lines[:5]:
        words = line.split()
        if 2 <= len(words) <= 6 and len(line) <= 60 and all(w.replace(".", "").isalpha() for w in words) \
                and not _heading(line):
            return line
    return None


def _role(lines: list[str]) -> str | None:
    for line in lines[:80]:
        match = _LABELLED_ROLE.match(line)
        if match:
            return match.group(1).strip()
    for line in lines[1:8]:
        folded = line.casefold()
        if len(line) <= 80 and not _EMAIL.search(line) and any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", folded)
                                                                for w in _ROLE_WORDS):
            return line
    return None


def _skills(text: str, lines: list[str]) -> list[str]:
    found: list[str] = []
    active = None
    for line in lines:
        section = _heading(line)
        if section:
            active = section
            continue
        if active == "skills":
            found.extend(part.strip(" -*\t") for part in _SKILL_SPLIT.split(line))
    folded = text.casefold()
    found.extend(skill for skill in _KNOWN_SKILLS
                 if re.search(rf"(?<![\w+#.]){re.escape(skill)}(?![\w+#])", folded))
    seen: dict[str, str] = {}
    for skill in found:
        if 1 < len(skill) <= 60:
            seen.setdefault(skill.casefold(), skill)
    return list(seen.values())[:60]


def _seniority(role: str | None, years: float | None) -> str | None:
    folded = (role or "").casefold()
    for words, level in ((("intern", "thực tập"), "intern"), (("fresher",), "fresher"), (("junior",), "junior"),
                         (("senior", "lead", "principal", "trưởng"), "senior"), (("middle", "mid"), "mid")):
        if any(w in folded for w in words):
            return level
    if years is None:
        return None
    if years < 1:
        return "fresher"
    if years < 2:
        return "junior"
    if years < 5:
        return "mid"
    return "senior"


def analyze_cv_text(text: str) -> dict[str, Any]:
    """Fields detected in the CV text, the sections it has, and warnings for the candidate."""
    lines = _lines(text)
    years_match = _YEARS.search(text)
    years = float(years_match.group(1).replace(",", ".")) if years_match else None
    role = _role(lines)
    sections = {section: False for section in _SECTIONS}
    for line in lines:
        if section := _heading(line):
            sections[section] = True
    skills = _skills(text, lines)
    fields = {
        "candidate_name": _name(lines),
        "email": (_EMAIL.search(text) or [None])[0],
        "phone": (_PHONE.search(text) or [None])[0],
        "current_role": role,
        "experience_years": years,
        "seniority": _seniority(role, years),
        "skills": skills,
    }
    warnings = []
    if len(text) < MIN_READABLE_CHARS:
        warnings.append("low_text")
    if not skills:
        warnings.append("no_skills")
    if not sections["experience"] and not sections["projects"]:
        warnings.append("no_experience")
    return {"readable": True, "characters": len(text), "fields": fields, "sections": sections,
            "warnings": warnings}
