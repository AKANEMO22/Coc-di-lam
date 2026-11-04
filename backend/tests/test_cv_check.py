from app.cv_check import analyze_cv_text
from tests.test_security import build, sign_in

CV = """Nguyen Van An
Backend Developer
an.nguyen@example.com | 0912 345 678
Summary
3 years of experience building APIs.
Skills
Python, FastAPI, PostgreSQL, Docker
Experience
Acme Corp - Backend Developer (2022 - now)
Education
PTIT
"""


def test_analysis_reads_name_role_years_and_skills():
    result = analyze_cv_text(CV)
    fields = result["fields"]
    assert fields["candidate_name"] == "Nguyen Van An"
    assert fields["current_role"] == "Backend Developer"
    assert fields["experience_years"] == 3
    assert fields["seniority"] == "mid"
    assert fields["email"] == "an.nguyen@example.com"
    assert {"Python", "FastAPI", "Docker"} <= set(fields["skills"])
    assert result["sections"]["experience"] and result["sections"]["education"]


def test_thin_cv_reports_warnings_instead_of_guessing():
    result = analyze_cv_text("hello")
    assert result["fields"]["seniority"] is None
    assert {"low_text", "no_skills", "no_experience"} <= set(result["warnings"])


def test_analyze_endpoint_reads_text_cv(tmp_path):
    client, _, _ = build(tmp_path)
    headers = sign_in(client)
    response = client.post("/api/cv/analyze", files={"file": ("cv.txt", CV.encode(), "text/plain")}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["current_role"] == "Backend Developer"


def test_analyze_endpoint_flags_cv_without_text(tmp_path):
    client, _, _ = build(tmp_path)
    headers = sign_in(client)
    response = client.post("/api/cv/analyze", files={"file": ("cv.txt", b"   \n  ", "text/plain")}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["readable"] is False and response.json()["reason"] == "needs_ocr"


def test_analyze_endpoint_requires_sign_in(tmp_path):
    client, _, _ = build(tmp_path)
    assert client.post("/api/cv/analyze", files={"file": ("cv.txt", CV.encode(), "text/plain")}).status_code == 401
