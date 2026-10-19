"""Local import provenance; never included in the public intelligence export."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.base import Base, IdentityMixin


class SnapshotSource(Base):
    __tablename__ = "snapshot_sources"
    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id"))


class SnapshotImport(IdentityMixin, Base):
    __tablename__ = "snapshot_imports"
    snapshot_id: Mapped[str] = mapped_column(String(100), unique=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    summary: Mapped[dict] = mapped_column(JSON)


class SnapshotMember(IdentityMixin, Base):
    __tablename__ = "snapshot_members"
    __table_args__ = (UniqueConstraint("import_id", "remote_document_id", name="uq_snapshot_import_document"),)
    import_id: Mapped[UUID] = mapped_column(ForeignKey("snapshot_imports.id"), index=True)
    remote_document_id: Mapped[str] = mapped_column(String(36))
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    disposition: Mapped[str] = mapped_column(String(30))
