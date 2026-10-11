from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocType = Literal["vulnerability", "advisory", "paper", "standard", "policy", "blog"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc)


class Identifier(Model):
    namespace: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def normalize(self):
        self.namespace = self.namespace.upper().strip()
        self.value = self.value.strip()
        if self.namespace in {"CVE", "GHSA"}:
            self.value = self.value.upper()
        return self


class AIScope(Model):
    relevant: bool = True
    reason: str = "source concerns AI security"


class DocumentInput(Model):
    external_id: str | None = None
    source_name: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    source_url: str
    doc_type: DocType
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: str = "en"
    published_at: datetime | None = None
    source_modified_at: datetime | None = None
    collected_at: datetime
    identifiers: list[Identifier] = Field(default_factory=list)
    product_entities: list[str] = Field(default_factory=list)
    ai_scope: AIScope = Field(default_factory=AIScope)
    risk_category: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] | None = None

    _timestamps = field_validator("published_at", "source_modified_at", "collected_at")(utc)

    @field_validator("source_url")
    @classmethod
    def url(cls, value):
        from urllib.parse import urlsplit
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("source_url must be a public http(s) URL without credentials")
        return value


class EvidenceDetail(Model):
    evidence_id: str
    document_id: str
    version: int
    title: str
    url: str
    excerpt: str
    locator: dict[str, Any]
    published_at: datetime | None = None
    collected_at: datetime


class Assertion(Model):
    assertion_id: str = ""
    document_id: str
    version: int
    entity_id: str
    field: str
    value: Any
    evidence_ids: list[str] = Field(min_length=1)
    origin: Literal["source_structured", "rule_extracted", "llm_candidate"]
    verification: Literal["source_recorded", "needs_review"]
    recorded_at: datetime


class Relation(Model):
    relation_id: str = ""
    document_id: str
    version: int
    subject: str
    predicate: str
    object: str
    evidence_ids: list[str] = Field(min_length=1)
    origin: Literal["source_structured", "rule_extracted", "llm_candidate"] = "rule_extracted"
    verification: Literal["source_recorded", "needs_review"] = "source_recorded"


class IngestItem(Model):
    external_id: str | None = None
    document_id: str | None = None
    version: int | None = None
    status: Literal["inserted", "updated", "unchanged", "failed"]
    error: str | None = None


class IngestResult(Model):
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    document_ids: list[str] = Field(default_factory=list)
    items: list[IngestItem] = Field(default_factory=list)


class EnrichResult(Model):
    assertions_added: int = 0
    assertions_retained: int = 0
    relations_added: int = 0
    relations_retained: int = 0
    candidates: int = 0
    warnings: list[str] = Field(default_factory=list)


class Filters(Model):
    identifier: list[str] = Field(default_factory=list, max_length=8)
    product: str | None = None
    doc_types: list[DocType] = Field(default_factory=list)
    source_kinds: list[str] = Field(default_factory=list)
    published_from: datetime | None = None
    published_to: datetime | None = None
    min_cvss_score: float | None = Field(default=None, ge=0, le=10)
    cvss_version: Literal["2.0", "3.0", "3.1", "4.0"] | None = None
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None

    _timestamps = field_validator("published_from", "published_to")(utc)

    @model_validator(mode="after")
    def dates(self):
        if self.published_from and self.published_to and self.published_from > self.published_to:
            raise ValueError("published_from must not exceed published_to")
        if (self.min_cvss_score is not None or self.severity) and not self.cvss_version:
            self.cvss_version = "3.1"
        self.identifier = [i.strip().upper() for i in self.identifier]
        return self


class Message(Model):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=12000)


class QueryRequest(Model):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Message] = Field(default_factory=list, max_length=8)
    context: dict[str, list[str]] = Field(default_factory=dict)
    filters: Filters = Field(default_factory=Filters)
    timezone: str = "Asia/Shanghai"
    now: datetime | None = None

    _now = field_validator("now")(utc)

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("unknown IANA timezone") from None
        return value

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class Claim(Model):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    kind: Literal["fact", "inference"] = "fact"
    topic_ids: list[str] = Field(default_factory=list, max_length=8)


class Metrics(Model):
    elapsed_ms: float = 0
    llm_calls: int = 0
    tool_calls: int = 0
    retries: int = 0
    embedding_mode: str = "unknown"
    index_generation: int = 0


class AnswerCoverage(Model):
    answered_topics: list[Annotated[str, Field(min_length=1, max_length=120, strict=True)]] = Field(default_factory=list, max_length=12)
    missing_topics: list[Annotated[str, Field(min_length=1, max_length=120, strict=True)]] = Field(default_factory=list, max_length=12)


class QueryResponse(Model):
    status: Literal["ok", "partial", "insufficient_evidence", "error"]
    answer: str
    response_kind: Literal["knowledge", "conversation"] = "knowledge"
    coverage: AnswerCoverage = Field(default_factory=AnswerCoverage)
    claims: list[Claim] = Field(default_factory=list)
    sources: list[EvidenceDetail] = Field(default_factory=list)
    facts: list[Assertion] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace_id: str
    metrics: Metrics = Field(default_factory=Metrics)
    mode: Literal["live", "mock", "extractive"]


class Plan(Model):
    route: Literal["exact", "hybrid", "compare"]
    subqueries: list[str] = Field(min_length=1, max_length=3)
    expand_relations: bool = False


class Draft(Model):
    claims: list[Claim] = Field(default_factory=list, max_length=8)
    # Models may propose facts, but validation requires an exact stored assertion.
    facts: list[Assertion] = Field(default_factory=list)
