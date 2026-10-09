from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String,
    Text, UniqueConstraint, Uuid, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, IdentityMixin, TimestampMixin


class DataSource(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(30))
    adapter: Mapped[str] = mapped_column(String(30))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_new_content_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Document(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_payload: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    ai_relevant: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    relevance: Mapped[dict] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    content_completeness: Mapped[str] = mapped_column(String(30), default="unknown", server_default="unknown")
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def source_reported_severity(self) -> str | None:
        value = (self.raw_payload or {}).get("severity")
        return value if value in {"unknown", "low", "medium", "high", "critical"} else None

    @property
    def excerpt(self) -> str:
        """Original source text only; this is not a model-written summary."""
        return (self.content or "")[:240]

    @property
    def source_products(self) -> list[str]:
        return [value for value in (self.raw_payload or {}).get("products", []) if isinstance(value, str)]

    @property
    def source_aliases(self) -> list[str]:
        return [value for value in (self.raw_payload or {}).get("aliases", []) if isinstance(value, str)]


class DocumentRevision(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "document_revisions"
    __table_args__ = (UniqueConstraint("document_id", "content_hash"),)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class Vulnerability(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "vulnerabilities"
    __table_args__ = (UniqueConstraint("identifier", "is_demo"),)
    identifier: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(20), default="unknown")
    ai_relevant: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, index=True)


class VulnerabilityAlias(IdentityMixin, Base):
    __tablename__ = "vulnerability_aliases"
    __table_args__ = (UniqueConstraint("value", "is_demo", name="uq_alias_value_demo"),)
    vulnerability_id: Mapped[UUID] = mapped_column(ForeignKey("vulnerabilities.id"), index=True)
    value: Mapped[str] = mapped_column(String(160))
    is_demo: Mapped[bool] = mapped_column(Boolean, index=True)


class VulnerabilityDocument(IdentityMixin, Base):
    __tablename__ = "vulnerability_documents"
    __table_args__ = (UniqueConstraint("vulnerability_id", "document_id"),)
    vulnerability_id: Mapped[UUID] = mapped_column(ForeignKey("vulnerabilities.id"), index=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)


class Evidence(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("document_id", "content_hash", "field", "method",
                                      name="uq_evidence_revision_field_method"),)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(50))
    value: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="known")
    excerpt: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(50), default="source_reported")


class TaskRun(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')", name="status"),
        Index("uq_active_source", "source_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
    )
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    kind: Mapped[str] = mapped_column(String(30), default="collect")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskStep(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "task_steps"
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Outbox(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "outbox"
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task_runs.id"), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeEvent(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_events"
    __table_args__ = (UniqueConstraint("document_id", "content_hash", "operation", name="uq_knowledge_event_version_operation"),)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmbeddingChunk(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "embedding_chunks"
    __table_args__ = (UniqueConstraint("document_id", "content_hash", "chunk_index", "model",
                                       name="uq_embedding_chunk_version_model"),)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    is_demo: Mapped[bool] = mapped_column(Boolean, index=True)


class Conversation(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "qa_conversations"
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[str | None] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="deepseek", server_default="deepseek")


class AccessToken(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "access_tokens"
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentInvocation(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "agent_invocations"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_agent_owner_idempotency"),
        Index("uq_agent_active_conversation", "conversation_id", unique=True,
              postgresql_where=text("status = 'running'")),
    )
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("qa_conversations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="running")
    error_code: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    response_payload: Mapped[dict | None] = mapped_column(JSON)
    tool_trace: Mapped[list] = mapped_column(JSON, default=list)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "qa_messages"
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("qa_conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    retrieval_mode: Mapped[str | None] = mapped_column(String(30))


class VerificationProbe(TimestampMixin, Base):
    """开发验证专用；不属于情报业务数据。"""
    __tablename__ = "verification_probes"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class RuntimeObservation(IdentityMixin, Base):
    __tablename__ = "runtime_observations"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sample_seconds: Mapped[int] = mapped_column(Integer, default=60)


class RuntimeSample(IdentityMixin, Base):
    __tablename__ = "runtime_samples"
    __table_args__ = (UniqueConstraint("observation_id", "bucket", name="uq_runtime_sample_bucket"),)
    observation_id: Mapped[UUID] = mapped_column(ForeignKey("runtime_observations.id"), index=True)
    bucket: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict] = mapped_column(JSON)


class EnrichmentQualityBatch(IdentityMixin, Base):
    """Immutable version-bound predictions; never auto-populated with gold labels."""
    __tablename__ = "enrichment_quality_batches"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    is_demo: Mapped[bool] = mapped_column(Boolean, index=True)
    sample_size: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    selection: Mapped[dict] = mapped_column(JSON)
    cases: Mapped[list] = mapped_column(JSON)


class EnrichmentQualityReview(IdentityMixin, Base):
    __tablename__ = "enrichment_quality_reviews"
    __table_args__ = (UniqueConstraint("batch_id", "document_id", name="uq_quality_review_batch_document"),)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("enrichment_quality_batches.id"), index=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    prediction_hash: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(100))
    annotation_origin: Mapped[str] = mapped_column(String(30))
    independent: Mapped[bool] = mapped_column(Boolean)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    field_reviews: Mapped[dict] = mapped_column(JSON)
