"""Schemas for private candidate intakes addressed by ID_Q."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Seniority = Literal["intern", "fresher", "junior", "mid", "senior"]
InputType = Literal["cv", "form"]


class CandidateProfile(BaseModel):
    """One canonical shape for CV extraction and user-entered profile data."""

    model_config = ConfigDict(extra="forbid")

    candidate_name: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=6000)
    experience_years: float | None = Field(default=None, ge=0, le=80)
    current_role: str | None = Field(default=None, max_length=300)
    current_seniority: Seniority | None = None
    skills: list[str] = Field(default_factory=list, max_length=300)
    experience: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    projects: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    education: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    certifications: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    awards: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    career_goals: list[str] = Field(default_factory=list, max_length=100)
    additional_sections: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class InterviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=300)
    role_id: str | None = Field(default=None, max_length=100)
    role_title: str | None = Field(default=None, max_length=300)
    target_seniority: Seniority | None = None
    jd_id: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)


class IntakePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interview_language: Literal["vi", "en"] = "vi"
    interview_round: Literal["hr", "technical", "hiring_manager", "combined"] | None = None
    duration_minutes: int | None = Field(default=None, ge=10, le=120)
    additional_requirements: str | None = Field(default=None, max_length=8000)


class FormIntakeRequest(BaseModel):
    """Form-only payload; it intentionally has no CV/file/text field."""

    model_config = ConfigDict(extra="forbid")

    candidate_profile: CandidateProfile = Field(default_factory=CandidateProfile)
    interview_target: InterviewTarget = Field(default_factory=InterviewTarget)
    preferences: IntakePreferences = Field(default_factory=IntakePreferences)


class CVIntakeMetadata(BaseModel):
    """CV target/preferences supplied alongside the uploaded CV file."""

    model_config = ConfigDict(extra="forbid")

    interview_target: InterviewTarget = Field(default_factory=InterviewTarget)
    preferences: IntakePreferences = Field(default_factory=IntakePreferences)


class PatchIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    candidate_profile: CandidateProfile | None = None
    interview_target: InterviewTarget | None = None
    preferences: IntakePreferences | None = None


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    top_k: int = Field(default=20, ge=1, le=50)
    web_search_mode: Literal["auto", "off", "refresh"] = "auto"


class KnowledgeImportRequest(BaseModel):
    """Curated company/JD catalog import. Role, rubric and question seeds stay in data/."""

    model_config = ConfigDict(extra="forbid")

    companies: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    job_descriptions: list[dict[str, Any]] = Field(default_factory=list, max_length=50000)


class CrawlJobsRequest(BaseModel):
    source: str = "all"
    keyword: str = "IT"
    limit: int = 30
