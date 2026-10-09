"""Additive public proof schemas; no changes to the strict Agent V1 transport."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class AssociationProof(BaseModel):
    identifier: str
    method: Literal["source_declared_alias", "explicit_identifier", "exact_product_body_fallback", "exact_url_fallback"]
    locator: str
    excerpt: str
    group_identifiers: list[str]
    origin: Literal["recorded_at_ingestion", "reconstructed_from_revision"]
    recorded_at: datetime | None
    vulnerability_id: UUID | None
    active_link: bool


class DocumentAssociations(BaseModel):
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    is_demo: bool
    is_current: bool
    items: list[AssociationProof]
    scope: str


class DocumentVersionProof(BaseModel):
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    is_demo: bool
    is_current: bool
    deleted_at: datetime | None
    version_created_at: datetime
    source_url: str
    title: str
    content: str | None
    revision: dict
    revision_json: str
    associations: list[AssociationProof]
    offset_unit: Literal["unicode_code_points"]


class QuotePosition(BaseModel):
    representation: Literal["content", "revision_json"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    offset_unit: Literal["unicode_code_points"]
    locator_verified: bool


class IndexEventObserved(BaseModel):
    event_id: UUID
    operation: str
    status: str


class AnswerQuote(BaseModel):
    number: int
    document_id: UUID
    content_hash: str
    title: str
    source_url: str
    excerpt: str
    locator: str
    position: QuotePosition | None
    quote_verified: bool
    is_current: bool
    deleted_at: datetime | None
    version_url: str
    associations: list[AssociationProof]
    index_events_observed_now: list[IndexEventObserved]


class AgentInvocationEvidence(BaseModel):
    schema_version: Literal["1"]
    invocation_id: UUID
    session_id: UUID
    request_id: str
    status: str
    generation_mode: Literal["live", "extractive", "mock", "unknown"]
    llm_http_calls: int | None
    answer: str | None
    retrieval_mode: str | None
    cve_ids: list[str]
    evidence: list[AnswerQuote]
    all_quotes_verified: bool
    all_versions_current: bool
    claim_support_status: Literal["not_independently_reviewed"]
    limits: str
