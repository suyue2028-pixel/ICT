"""Add version-scoped automatic association proof without rewriting existing data."""
from alembic import op
import sqlalchemy as sa

revision = "0009_association_evidence"
down_revision = "0008_enrichment_quality"
branch_labels = depends_on = None


def upgrade():
    op.create_table("association_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("identifier", sa.String(160), nullable=False),
        sa.Column("method", sa.String(50), nullable=False),
        sa.Column("locator", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("group_identifiers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "content_hash", "identifier", name="uq_association_version_identifier"))
    op.create_index("ix_association_evidence_document_id", "association_evidence", ["document_id"])


def downgrade():
    raise RuntimeError("Retain association history; use a separately reviewed retention plan")
