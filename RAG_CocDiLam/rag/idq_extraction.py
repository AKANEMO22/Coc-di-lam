"""Bounded CV text extraction with section/page locators and no hidden truncation."""

import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .indexing import EXTRA_CONCEPTS
from .ontology import SKILL_CONCEPTS, canonical, mentions

MAX_CV_BYTES = 12 * 1024 * 1024
MAX_CV_CHARS = 180_000
MAX_PDF_PAGES = 120
MAX_DOCX_MEMBERS = 10_000
MAX_DOCX_UNCOMPRESSED_BYTES = 48 * 1024 * 1024

_HEADINGS = {
    "summary": "summary", "profile": "summary", "objective": "career_goals",
    "career objective": "career_goals", "career goals": "career_goals",
    "experience": "experience", "work experience": "experience", "employment": "experience",
    "professional experience": "experience", "kinh nghiệm": "experience",
    "projects": "projects", "project experience": "projects", "dự án": "projects",
    "skills": "skills", "technical skills": "skills", "kỹ năng": "skills",
    "education": "education", "học vấn": "education",
    "certifications": "certifications", "certificates": "certifications", "chứng chỉ": "certifications",
    "awards": "awards", "honors": "awards", "giải thưởng": "awards",
}


class CVExtractionError(ValueError):
    pass


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\f\v ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pages(filename: str, content: bytes) -> list[str]:
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise CVExtractionError("Upload a PDF, DOCX, TXT, or Markdown CV")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise CVExtractionError("Password-protected PDFs are not supported")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise CVExtractionError(f"PDFs are limited to {MAX_PDF_PAGES} pages")
            return [page.extract_text() or "" for page in reader.pages]
        except CVExtractionError:
            raise
        except Exception as exc:
            raise CVExtractionError("The PDF could not be read") from exc
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                members = archive.infolist()
                if len(members) > MAX_DOCX_MEMBERS or sum(m.file_size for m in members) > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise CVExtractionError("The DOCX expands beyond the supported size")
                document = archive.read("word/document.xml")
            root = ElementTree.fromstring(document)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paras = []
            for para in root.iter(f"{ns}p"):
                value = "".join(n.text or "" for n in para.iter(f"{ns}t"))
                if value.strip():
                    paras.append(value)
            return ["\n".join(paras)]
        except CVExtractionError:
            raise
        except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise CVExtractionError("The DOCX file could not be read") from exc
    try:
        return [content.decode("utf-8-sig")]
    except UnicodeDecodeError as exc:
        raise CVExtractionError("Text CVs must use UTF-8 encoding") from exc


def _section_name(line: str) -> str | None:
    value = line.strip().strip("#:* -_").casefold()
    return _HEADINGS.get(value)


def extract_cv(filename: str, content: bytes) -> dict[str, Any]:
    """Extract every readable text section; preserve unmapped content and provenance."""
    if not content:
        raise CVExtractionError("The uploaded CV is empty")
    if len(content) > MAX_CV_BYTES:
        raise CVExtractionError("The CV exceeds the 12 MB limit")
    pages = [_normalize(page) for page in _extract_pages(filename, content)]
    full_text = "\n\n".join(f"[page={i + 1}]\n{page}" for i, page in enumerate(pages) if page)
    if not full_text.strip():
        raise CVExtractionError("No readable text was found. Scanned PDFs require OCR.")
    if len(full_text) > MAX_CV_CHARS:
        raise CVExtractionError("Extracted CV text exceeds 180,000 characters; split the CV before uploading")

    sections: list[dict[str, Any]] = []
    for page_number, page in enumerate(pages, start=1):
        active = "additional"
        lines: list[str] = []
        for line_number, line in enumerate(page.splitlines(), start=1):
            heading = _section_name(line)
            if heading:
                if lines:
                    sections.append({"name": active, "text": "\n".join(lines).strip(), "source_ref": f"page={page_number}"})
                active = heading
                lines = []
            elif line.strip():
                lines.append(line.rstrip())
        if lines:
            sections.append({"name": active, "text": "\n".join(lines).strip(), "source_ref": f"page={page_number}"})

    section_counters: dict[str, int] = {}
    for section in sections:
        page_ref = section["source_ref"]
        section_counters[page_ref] = section_counters.get(page_ref, 0) + 1
        section["section_id"] = f"p{page_ref.split('=')[1]}-s{section_counters[page_ref]}"

    profile: dict[str, Any] = {
        "candidate_name": None,
        "summary": None,
        "experience_years": None,
        "current_role": None,
        "current_seniority": None,
        "skills": [],
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
        "awards": [],
        "career_goals": [],
        "additional_sections": [],
    }
    for section in sections:
        name = section["name"]
        payload = {"text": section["text"], "source_ref": section["source_ref"], "evidence_status": "claimed"}
        if name == "summary" and not profile["summary"]:
            profile["summary"] = section["text"][:6000]
        elif name in {"experience", "projects", "education", "certifications", "awards"}:
            profile[name].append(payload)
        elif name == "career_goals":
            profile["career_goals"].append(section["text"][:3000])
        elif name == "skills":
            profile["skills"].extend(x.strip(" •-*\t") for x in re.split(r"[,;|\n]", section["text"]) if x.strip(" •-*\t"))
        else:
            profile["additional_sections"].append({"name": "additional", **payload})

    for page_number, page in enumerate(pages, start=1):
        for line_number, line in enumerate(page.splitlines(), start=1):
            match = re.match(r"\s*(?:name|full name|họ tên|ho ten)\s*[:：-]\s*(.{2,200})", line, re.I)
            if match and not profile["candidate_name"]:
                profile["candidate_name"] = match.group(1).strip()
            role_match = re.match(r"\s*(?:current role|current position|vị trí hiện tại)\s*[:：-]\s*(.{2,300})", line, re.I)
            if role_match and not profile["current_role"]:
                profile["current_role"] = role_match.group(1).strip()
            years = re.search(r"\b(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?|năm)\s+(?:of\s+)?(?:experience|kinh nghiệm)?", line, re.I)
            if years and profile["experience_years"] is None:
                profile["experience_years"] = float(years.group(1))

    all_text = "\n".join(pages)
    detected = sorted({canonical(skill) for skill in set(EXTRA_CONCEPTS) | set(SKILL_CONCEPTS) if mentions(all_text, skill)})
    profile["skills"] = list(dict.fromkeys(profile["skills"] + detected))[:300]
    return {
        "profile": profile,
        "sections": sections,
        "raw_text": full_text,
        "page_count": len(pages),
        "extracted_characters": len(full_text),
        "warnings": ["CV facts are user-provided claims; review the extracted profile before using it.", "PDFs without a text layer require OCR."],
    }
