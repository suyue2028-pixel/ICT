"""Strict HTTP models matching the backend's version 1 Agent gateway.

Backend document UUIDs and hashes are transport identities. They must never be
replaced with the RAG store's independently derived identifiers or hashes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictBool, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class BackendHistory(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)
    citations: list[AgentEvidence] = Field(default_factory=list, max_length=50)


class QueryLimits(StrictModel):
    max_tool_calls: int = Field(ge=1, le=10, strict=True)
    max_answer_tokens: int = Field(ge=1, le=4096, strict=True)


class BackendQuery(StrictModel):
    schema_version: Literal["1"]
    query: str = Field(min_length=1, max_length=4000)
    session_id: UUID
    is_demo: StrictBool
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    invocation_id: UUID
    history: list[BackendHistory] = Field(default_factory=list, max_length=8)
    limits: QueryLimits
    state_owner: Literal["backend"]

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty query")
        return value.strip()


class ToolTrace(StrictModel):
    tool: str = Field(min_length=1, max_length=80)
    status: Literal["completed", "failed", "empty"]
    duration_ms: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=100)
    input_summary: str | None = Field(default=None, max_length=500)
    document_ids: list[UUID] = Field(default_factory=list, max_length=30)


class AnswerCoverage(StrictModel):
    """Display-only answer scope; never a substitute for source evidence."""
    answered_topics: list[Annotated[str, Field(min_length=1, max_length=120, strict=True)]] = Field(
        default_factory=list, max_length=12)
    missing_topics: list[Annotated[str, Field(min_length=1, max_length=120, strict=True)]] = Field(
        default_factory=list, max_length=12)


class RequirementCoverage(StrictModel):
    requirement_id: str = Field(min_length=1,max_length=40)
    label: str = Field(min_length=1,max_length=120)
    status: Literal['supported','partial','conflicting','missing']
    evidence_ids: list[Annotated[str,Field(pattern=r'^[1-9][0-9]*$')]] = Field(default_factory=list,max_length=50)
    reason: str = Field(max_length=200)


class RetrievalSummary(StrictModel):
    rounds: int = Field(default=0,ge=0,le=3)
    stop_reason: str = Field(default='not_started',max_length=80)
    initial_tools: list[str] = Field(default_factory=list,max_length=10)
    supplemental_tools: list[str] = Field(default_factory=list,max_length=10)


class AgentAnswer(StrictModel):
    schema_version: Literal["1"] = "1"
    status: Literal["completed", "partial", "insufficient_evidence"]
    answer: str = Field(min_length=1, max_length=12000)
    response_kind: Literal["knowledge", "conversation"] = "knowledge"
    coverage: AnswerCoverage = Field(default_factory=AnswerCoverage)
    question_type: str | None = Field(default=None, max_length=80)
    requirement_coverage: list[RequirementCoverage] = Field(default_factory=list, max_length=40)
    retrieval_summary: RetrievalSummary | None = None
    limitations: list[str] = Field(default_factory=list, max_length=20)
    sources: list[AgentSource] = Field(max_length=30)
    cve_ids: list[str] = Field(max_length=100)
    risk_level: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    evidence: list[AgentEvidence] = Field(max_length=50)
    tool_calls: list[str] = Field(max_length=30)
    tool_trace: list[ToolTrace] = Field(default_factory=list, max_length=30)
    partial_errors: list[str] = Field(default_factory=list, max_length=20)
    retrieval_mode: Literal["keyword", "keyword_fallback", "semantic", "hybrid", "structured", "conversation", "llm_unverified"]

    @field_validator("cve_ids")
    @classmethod
    def validate_cves(cls, values):
        import re
        if any(not re.fullmatch(r"CVE-\d{4}-\d{4,}", value) for value in values):
            raise ValueError("invalid CVE")
        return values


class IndexVerification(StrictModel):
    schema_version: Literal["1"]
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_id: str = Field(min_length=1, max_length=200)
    is_demo: StrictBool
    operation: Literal["add", "update", "delete"]


class IndexProof(StrictModel):
    schema_version: Literal["1"]
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_id: str = Field(min_length=1, max_length=200)
    is_demo: StrictBool
    queryable: StrictBool
    removed: StrictBool
