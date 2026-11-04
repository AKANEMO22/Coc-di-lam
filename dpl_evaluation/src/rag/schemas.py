from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.candidate import CandidateProfile
from src.models.job import JobDescription

Level = Literal["Intern", "Fresher", "Junior"]
Mode = Literal["bm25", "dense", "text", "graph", "hybrid", "auto"]


class SourceRef(BaseModel):
    locator: str
    content_hash: str
    url: str | None = None
    review_status: str = "unreviewed"


class EvidencePath(BaseModel):
    nodes: list[str]
    relations: list[str]
    sources: list[SourceRef]
    confidence: float = Field(ge=0, le=1)


class RetrievalHit(BaseModel):
    question_id: str
    channel: str
    rank: int
    score: float
    paths: list[EvidencePath] = Field(default_factory=list)


class QuestionBundle(BaseModel):
    question_id: str
    original_id: str
    role_id: str
    level: Level
    question_type: Literal["technical", "behavioral"] = "technical"
    applicable_levels: list[Level] = Field(default_factory=list)
    stages: list[int] = Field(default_factory=lambda: [2])
    question: str
    expected_answer: str
    rubric: dict[str, str]
    concepts: list[str]
    sources: list[SourceRef]
    reasons: list[str] = Field(default_factory=list)
    graph_paths: list[EvidencePath] = Field(default_factory=list)
    score: float = 0


class SkillEvidence(BaseModel):
    skill: str
    status: Literal[
        "claimed",
        "project_evidence",
        "not_evidenced",
        "verified_in_interview",
        "failed_verification",
    ]
    evidence: list[str] = Field(default_factory=list)


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str | None = None
    candidate: CandidateProfile | None = None
    job_id: str | None = None
    job: JobDescription | None = None
    role_id: str | None = None
    target_level: Level | None = None
    query: str = Field(default="", max_length=16000)
    required_skills: list[str] = Field(default_factory=list, max_length=100)
    mode: Mode = "hybrid"
    stage: int = Field(default=2, ge=1, le=10)
    top_k: int = Field(default=5, ge=1, le=25)
    candidate_pool: int = Field(default=30, ge=1, le=100)
    token_budget: int = Field(default=6000, ge=256, le=24000)
    asked_question_ids: list[str] = Field(default_factory=list, max_length=500)
    session_id: str | None = None
    tenant_id: str = Field(default="local", min_length=1, max_length=100)
    include_prerequisites: bool = True
    graph_max_hops: int = Field(default=3, ge=1, le=4)
    rerank: bool = True
    plan_coverage: bool = True

    @model_validator(mode="after")
    def validate_input(self):
        if self.candidate and self.candidate_id:
            raise ValueError("Use candidate or candidate_id, not both")
        if self.job and self.job_id:
            raise ValueError("Use job or job_id, not both")
        if not self.job and not self.job_id and not self.role_id:
            raise ValueError("A job or explicit role_id is required")
        if self.candidate_pool < self.top_k:
            raise ValueError("candidate_pool must be >= top_k")
        return self


class RetrievalResponse(BaseModel):
    schema_version: str = "1.0"
    request_id: str
    session_id: str | None = None
    index_version: str
    embedding_provider: str
    status: Literal[
        "ok", "empty", "unsupported_role", "unsupported_level", "unsupported_stage"
    ]
    role_id: str | None = None
    candidate_level: str | None = None
    target_level: Level | None = None
    interviewer_persona: dict = Field(default_factory=dict)
    candidate_summary: dict = Field(default_factory=dict)
    job_requirements: dict = Field(default_factory=dict)
    interview_agenda: list[dict] = Field(default_factory=list)
    skill_evidence: list[SkillEvidence] = Field(default_factory=list)
    selected_questions: list[QuestionBundle] = Field(default_factory=list)
    interview_framework: dict = Field(default_factory=dict)
    covered_requirements: list[str] = Field(default_factory=list)
    uncovered_requirements: list[str] = Field(default_factory=list)
    graph_paths: list[EvidencePath] = Field(default_factory=list)
    supporting_knowledge: list[dict] = Field(default_factory=list)
    prompt_context: str = ""
    degraded_mode: list[str] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)


class FollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    tenant_id: str = "local"
    answer: str = Field(default="", max_length=16000)
    answered_question_id: str | None = None
    # Evidence is supplied by a trusted interviewer/grader, never inferred from a mention.
    verified_skills: list[str] = Field(default_factory=list)
    failed_skills: list[str] = Field(default_factory=list)
    top_k: int = Field(default=3, ge=1, le=25)


class GoldCase(BaseModel):
    id: str
    split: Literal["dev", "test"]
    group_id: str
    query_type: str
    request: RetrievalRequest
    relevance: dict[str, int]
    answerable: bool = True
    label_origin: str = "synthetic_unreviewed"
