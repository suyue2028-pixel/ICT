"""Version 1 shared contract: backend owns data/session, Agent owns retrieval/reasoning."""
from typing import Annotated, Literal
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PrivateAttr, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentQuery(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: UUID | None = None
    is_demo: bool = False

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty query")
        return value.strip()


class AgentSource(StrictModel):
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(max_length=500)
    url: HttpUrl
    source_type: str = Field(max_length=30)
    source_name: str = Field(max_length=100)
    published_time: datetime | None = None


class AgentEvidence(StrictModel):
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(min_length=1, max_length=2000)
    source: HttpUrl
    locator: str = Field(min_length=1, max_length=200)
    score: float | None = Field(default=None, ge=-1, le=1)
    score_type: Literal["cosine_similarity", "relevance", "not_scored"] = "not_scored"


class ToolTrace(StrictModel):
    tool: str = Field(min_length=1, max_length=80)
    status: Literal["completed", "failed", "empty"]
    duration_ms: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=100)
    input_summary: str | None = Field(default=None, max_length=500)
    document_ids: list[UUID] = Field(default_factory=list, max_length=30)


class AnswerCoverage(StrictModel):
    """Display-only answer scope; source revision validation remains mandatory."""
    answered_topics: list[Annotated[str, Field(min_length=1, max_length=120, strict=True)]] = Field(
        default_factory=list, max_length=12)
    missing_topics: list[Annotated[str, Field(min_length=1, max_length=120, strict=True)]] = Field(
        default_factory=list, max_length=12)


class AgentAnswer(StrictModel):
    _rag_headers: dict[str, str] = PrivateAttr(default_factory=dict)
    schema_version: Literal["1"]
    status: Literal["completed", "partial", "insufficient_evidence"]
    answer: str = Field(min_length=1, max_length=12000)
    response_kind: Literal["knowledge", "conversation"] = "knowledge"
    coverage: AnswerCoverage = Field(default_factory=AnswerCoverage)
    sources: list[AgentSource] = Field(max_length=30)
    cve_ids: list[str] = Field(max_length=100)
    risk_level: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    evidence: list[AgentEvidence] = Field(max_length=50)
    tool_calls: list[str] = Field(max_length=30)
    tool_trace: list[ToolTrace] = Field(default_factory=list, max_length=30)
    partial_errors: list[str] = Field(default_factory=list, max_length=20)
    retrieval_mode: Literal["keyword", "keyword_fallback", "semantic", "hybrid", "structured", "conversation"]

    @field_validator("cve_ids")
    @classmethod
    def validate_ids(cls, values):
        import re
        if any(not re.fullmatch(r"CVE-\d{4}-\d{4,}", v) for v in values):
            raise ValueError("invalid CVE")
        return values


class AgentQueryResponse(AgentAnswer):
    session_id: UUID
    invocation_id: UUID
    request_id: str
    generation_mode: Literal["live", "extractive", "mock"] | None = None
    llm_http_calls: int | None = Field(default=None, ge=0, le=3)


class TokenCreate(StrictModel):
    owner_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    expires_in_hours: int = Field(default=24, ge=1, le=720)
