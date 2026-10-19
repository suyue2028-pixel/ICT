"""Untrusted, data-only public snapshot v1. UUIDs are hints, never overwrite keys."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Packet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def aware_dates(cls, value):
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("snapshot_timestamp_requires_timezone")
        return value


class SourcePacket(Packet):
    id: UUID
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    adapter: str = Field(max_length=30)
    category: str = Field(max_length=30)
    config: dict = Field(default_factory=dict)


class RevisionPacket(Packet):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload: dict
    created_at: datetime
    updated_at: datetime


class EvidencePacket(Packet):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    field: Literal["affected", "cwe", "cvss", "remediation", "analysis", "papers", "poc", "exploitation"]
    value: dict
    state: Literal["known", "unknown", "unprocessed", "not_found", "failed", "pending_review"]
    excerpt: str | None = None
    source_url: str = Field(max_length=4096)
    locator: str = Field(max_length=200)
    method: str = Field(max_length=50)
    created_at: datetime
    updated_at: datetime


class AssociationPacket(Packet):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    identifier: str = Field(max_length=160)
    method: str = Field(max_length=50)
    locator: str = Field(max_length=200)
    excerpt: str
    group_identifiers: list[str] = Field(max_length=100)
    created_at: datetime


class DocumentPacket(Packet):
    id: UUID
    source_key: str
    external_id: str = Field(min_length=1, max_length=1024)
    current_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    first_collected_at: datetime
    last_checked_at: datetime | None
    content_changed_at: datetime | None
    revisions: list[RevisionPacket] = Field(min_length=1, max_length=1000)
    evidence: list[EvidencePacket] = Field(default_factory=list, max_length=20000)
    associations: list[AssociationPacket] = Field(default_factory=list, max_length=100000)
    vulnerability_ids: list[UUID] = Field(default_factory=list, max_length=1000)


class VulnerabilityPacket(Packet):
    id: UUID
    identifier: str = Field(max_length=100)
    title: str = Field(max_length=500)
    severity: Literal["unknown", "low", "medium", "high", "critical"]
    aliases: list[str] = Field(max_length=10000)


class FilePacket(Packet):
    path: str = Field(pattern=r"^(sources|documents|vulnerabilities)-[0-9]{4}\.jsonl$")
    kind: Literal["sources", "documents", "vulnerabilities"]
    records: int = Field(ge=0)
    bytes: int = Field(ge=0, le=32 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Manifest(Packet):
    format: Literal["cit-public-intelligence"]
    format_version: Literal[1]
    snapshot_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    generated_at: datetime
    required_migration: str = Field(max_length=100)
    exporter_version: Literal["1.0.0"]
    scope: Literal["known-public-sources-real-data-only"]
    files: list[FilePacket] = Field(max_length=1000)
    counts: dict[str, int]
    exclusions: dict[str, int]
    limitations: list[str] = Field(max_length=30)
