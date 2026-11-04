"""Version 1 wire contract for the core retrieval API.

This package deliberately owns its copy of the wire types. The service can be
installed and run without importing ``core_ai_agent``.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RetrievalCollection(str, Enum):
    QUESTION_BANK = "question_bank"
    RUBRIC = "rubric"
    ROLE_PROFILE = "role_profile"
    COMPANY_KNOWLEDGE = "company_knowledge"


@dataclass
class RetrievalQuery:
    collection: str
    text: str
    filters: dict[str, Any] = field(default_factory=dict)
    top_k: int = 5
    knowledge_base_id: str = "interview"
    index_version: str | None = None


@dataclass
class RetrievedChunk:
    chunk_id: str
    collection: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "collection": self.collection,
            "content": self.content,
            "score": self.score,
            "metadata": dict(self.metadata),
            "source": self.source,
        }


class RetrievalError(Exception):
    """A retrieval error safe to return to upstream application logs."""

    def __init__(self, message: str) -> None:
        super().__init__(sanitize_secrets(message))


def sanitize_secrets(value: str) -> str:
    """Avoid returning common credential patterns in service error messages."""
    import re

    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", value)
    value = re.sub(
        r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )
    return value[:1000]

