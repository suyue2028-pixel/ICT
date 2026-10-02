"""Versioned typed Tool responses for generated clients and OpenAPI."""
from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID
from pydantic import BaseModel, Field, HttpUrl

T = TypeVar("T")
Severity = Literal["unknown", "low", "medium", "high", "critical"]


class ToolEnvelope(BaseModel, Generic[T]):
    schema_version: Literal["1"] = "1"
    request_id: str
    status: Literal["completed", "empty"]
    data: T


class ToolDocument(BaseModel):
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_id: str
    title: str
    source_url: HttpUrl
    source_name: str
    source_type: Literal["vulnerability", "vendor", "community", "blog", "academic", "standard", "policy", "offline"]
    adapter: str
    document_type: Literal["vulnerability", "advisory", "discussion", "article", "paper", "standard", "policy", "regulation"]
    published_time: datetime | None
    source_updated_at: datetime | None
    first_collected_at: datetime
    ai_relevant: bool
    metadata: dict | None
    content_completeness: Literal["full_text", "summary", "metadata_only", "partial", "unknown"] | None
    source_reported_severity: Severity | None
    is_demo: bool


class CacheMetadata(BaseModel):
    last_checked_at: datetime | None
    version_updated_at: datetime
    stale: bool


class ToolDocumentPreview(ToolDocument):
    excerpt: str
    cache: CacheMetadata


class ToolDocumentVersion(ToolDocument):
    is_current: bool
    revision: dict
    content: str | None
    version_created_at: datetime


class ToolEvidence(BaseModel):
    field: str
    state: Literal["known", "unknown", "unprocessed", "not_found", "failed", "pending_review"]
    value: dict
    method: str
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: HttpUrl
    locator: str
    excerpt: str | None


class ToolCveSummary(BaseModel):
    id: UUID
    cve_id: str | None
    identifier: str
    title: str
    severity: Severity
    ai_relevant: bool


class ToolCveDetail(BaseModel):
    cve_id: str
    identifier: str
    description: str | None
    severity: Severity
    severity_resolution: str
    published_time: datetime | None
    cvss: list[ToolEvidence]
    aliases: list[str]
    references: list[ToolDocument]
    evidence: list[ToolEvidence]
