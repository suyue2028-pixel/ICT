"""Retain frozen review samples and append-only manual annotations."""
from alembic import op
import sqlalchemy as sa

revision = "0008_enrichment_quality"
down_revision = "0007_runtime_observations"
branch_labels = depends_on = None


def upgrade():
    op.create_table("enrichment_quality_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("cases", sa.JSON(), nullable=False))
    op.create_index("ix_enrichment_quality_batches_is_demo", "enrichment_quality_batches", ["is_demo"])
    op.create_table("enrichment_quality_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("enrichment_quality_batches.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("prediction_hash", sa.String(64), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("annotation_origin", sa.String(30), nullable=False),
        sa.Column("independent", sa.Boolean(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("field_reviews", sa.JSON(), nullable=False),
        sa.UniqueConstraint("batch_id", "document_id", name="uq_quality_review_batch_document"))
    op.create_index("ix_enrichment_quality_reviews_batch_id", "enrichment_quality_reviews", ["batch_id"])


def downgrade():
    raise RuntimeError("Quality labels and frozen evidence must be retained; use an explicit retention plan")
