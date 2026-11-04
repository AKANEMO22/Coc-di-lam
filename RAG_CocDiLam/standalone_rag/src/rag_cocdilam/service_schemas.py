"""Stable HTTP models used by the core and the administration UI."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=16000)
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=100)
    knowledge_base_id: str = Field(default="interview", min_length=1, max_length=64)
    index_version: str | None = Field(default=None, max_length=100)


class CreateKnowledgeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    company: str = Field(default="generic", max_length=120)
    topic: str = Field(default="general", max_length=120)
    language: str = Field(default="vi", max_length=16)
    review_status: str = Field(default="unreviewed", max_length=32)
    confidence: float = Field(default=0.7, ge=0, le=1)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class RetrievalResponse(BaseModel):
    schema_version: str = "1.0"
    request_id: str
    knowledge_base_id: str
    index_version: str
    chunks: list[dict[str, Any]]

