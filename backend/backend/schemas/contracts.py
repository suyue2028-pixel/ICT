import re
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    adapter: Literal["offline", "osv", "cisco", "sans", "nvd", "arxiv", "trailofbits", "nist", "govuk", "crossref", "huggingface", "china_policy", "nvidia"]
    config: dict = Field(default_factory=dict)
    enabled: bool = False
    interval_seconds: int = Field(default=3600, ge=60, le=604800)


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    config: dict | None = None


class SourceRead(ORMModel):
    id: UUID
    name: str
    category: str
    adapter: str
    config: dict
    enabled: bool
    interval_seconds: int
    cursor: dict
    next_run_at: datetime | None
    last_attempt_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_new_content_at: datetime | None = None
    last_error_code: str | None = None
    consecutive_failures: int = 0


class TaskRead(ORMModel):
    id: UUID
    source_id: UUID
    status: str
    kind: str
    attempts: int
    result: dict
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DocumentRead(ORMModel):
    id: UUID
    source_id: UUID
    external_id: str
    title: str
    source_url: str
    document_type: str
    content_hash: str
    is_demo: bool
    published_at: datetime | None
    first_collected_at: datetime
    ai_relevant: bool
    relevance: dict
    metadata_json: dict
    content_completeness: str
    source_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None = None
    content_changed_at: datetime | None = Field(default=None, description="实际从旧版本变更的入库时间；首版和旧历史未知为 null")
    indexed_at: datetime | None = Field(default=None, description="当前版本通过索引核验的时间；旧历史未知为 null")
    source_reported_severity: Literal["unknown", "low", "medium", "high", "critical"] | None = None
    excerpt: str = ""
    source_products: list[str] = Field(default_factory=list)
    source_aliases: list[str] = Field(default_factory=list)


class VulnerabilityRead(ORMModel):
    id: UUID
    identifier: str
    title: str
    severity: str
    ai_relevant: bool
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class RawRecord(BaseModel):
    """采集器与处理流水线之间的契约；演示数据必须显式标记。"""
    external_id: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    document_type: Literal["vulnerability", "advisory", "article", "discussion", "paper", "standard", "policy", "regulation"]
    content: str = Field(min_length=1, max_length=200000)
    vulnerability_id: str | None = Field(default=None, pattern=r"^CVE-\d{4}-\d{4,}$")
    severity: Literal["unknown", "low", "medium", "high", "critical"] = "unknown"
    published_at: datetime | None = None
    is_demo: bool
    aliases: list[str] = Field(default_factory=list, max_length=100)
    products: list[str] = Field(default_factory=list, max_length=100)
    raw: dict = Field(default_factory=dict)
    # 仅保存来源实际提供的字段；值统一为带结构标签的 JSON 对象。
    source_fields: dict[str, dict] = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)
    content_completeness: Literal["full_text", "summary", "metadata_only", "partial", "unknown"] = "unknown"
    source_updated_at: datetime | None = None

    @field_validator("published_at", "source_updated_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("published_at must contain a timezone")
        return value


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[UUID] = Field(default_factory=list, max_length=50)
    conversation_id: UUID | None = None


class Citation(BaseModel):
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: HttpUrl
    excerpt: str = Field(min_length=1, max_length=2000)


class QuestionResponse(BaseModel):
    """问答联调协议；实际回答由可选的外部 AI 服务提供。"""
    schema_version: Literal["1"] = "1"
    answer: str
    citations: list[Citation]
    status: Literal["completed", "insufficient_evidence"]
    evidence_summary: list[str] = Field(default_factory=list, max_length=20)
    task_id: UUID | None = None
    conversation_id: UUID | None = None
    retrieval_mode: Literal["keyword", "keyword_fallback", "semantic"] | None = None


class SemanticHitRead(BaseModel):
    citation: Citation
    score: float = Field(ge=-1, le=1)
    chunk_index: int = Field(ge=0)


class SemanticSearchResponse(BaseModel):
    status: Literal["completed", "insufficient_evidence"]
    retrieval_mode: Literal["semantic"] = "semantic"
    items: list[SemanticHitRead]


class ConversationCreate(BaseModel):
    is_demo: bool = False


class EnrichmentCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: HttpUrl
    locator: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=2000)


class EnrichmentField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["known", "unknown", "unprocessed", "not_found", "failed", "pending_review"]
    value: Any = None
    method: Literal["source_structured", "source_link", "rule", "model"]
    evidence: list[EnrichmentCitation] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def known_requires_evidence(self):
        if self.state == "known" and (self.value in (None, [], {}, "") or not self.evidence):
            raise ValueError("Known fields need a value and actual evidence")
        return self


class EnrichmentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    fields: dict[str, EnrichmentField] = Field(min_length=1, max_length=8)

    @field_validator("fields")
    @classmethod
    def supported_fields(cls, fields):
        allowed = {"affected", "cwe", "cvss", "remediation", "analysis", "papers", "poc", "exploitation"}
        if not set(fields) <= allowed:
            raise ValueError("Unsupported enrichment field")
        for key, field in fields.items():
            if field.state != "known":
                continue
            value = field.value
            if key != "exploitation" and not isinstance(value, list):
                raise ValueError("Known enrichment fields must contain structured lists")
            if key == "cwe" and any(not isinstance(item, str) or not re.fullmatch(r"CWE-\d+", item)
                                    for item in value):
                raise ValueError("Invalid CWE identifier")
            if key == "cvss":
                for item in value:
                    if not isinstance(item, dict) or item.get("version") not in {"2.0", "3.0", "3.1", "4.0", "unknown"}:
                        raise ValueError("Invalid CVSS version")
                    score = item.get("score")
                    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))
                                              or not 0 <= score <= 10):
                        raise ValueError("Invalid CVSS score")
                    if item.get("origin") not in {"official", "source_reported", "system_estimate"}:
                        raise ValueError("CVSS origin required")
            if key == "affected":
                for item in value:
                    if not isinstance(item, dict) or not isinstance(item.get("package"), dict) or not item["package"].get("name"):
                        raise ValueError("Affected package must be structured")
                    for version_range in item.get("ranges", []):
                        if not isinstance(version_range, dict) or version_range.get("type") not in {"SEMVER", "ECOSYSTEM", "GIT"}:
                            raise ValueError("Invalid version range")
                        for event in version_range.get("events", []):
                            if not isinstance(event, dict) or len(event) != 1 or not set(event) <= {
                                "introduced", "fixed", "last_affected", "limit"}:
                                raise ValueError("Invalid range event")
            if key in {"remediation", "analysis", "papers", "poc"}:
                for item in value:
                    if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].startswith(("https://", "http://")):
                        raise ValueError("Public link metadata required")
        return fields


class KnowledgeAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["indexed", "failed", "invalidated"]
    detail: dict = Field(default_factory=dict)
    lease_token: UUID | None = None
