"""Track local snapshot provenance separately from source discovery timestamps."""
from alembic import op
import sqlalchemy as sa

revision = "0010_public_snapshots"
down_revision = "0009_association_evidence"
branch_labels = depends_on = None


def upgrade():
    op.create_table("snapshot_sources",
        sa.Column("source_key", sa.String(100), primary_key=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("data_sources.id"), nullable=False))
    op.create_table("snapshot_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("snapshot_id", sa.String(100), unique=True, nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False))
    op.create_table("snapshot_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("import_id", sa.Uuid(), sa.ForeignKey("snapshot_imports.id"), nullable=False),
        sa.Column("remote_document_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(30), nullable=False),
        sa.UniqueConstraint("import_id", "remote_document_id", name="uq_snapshot_import_document"))
    op.create_index("ix_snapshot_members_import_id", "snapshot_members", ["import_id"])
    op.create_index("ix_snapshot_members_document_id", "snapshot_members", ["document_id"])


def downgrade():
    raise RuntimeError("Retain import provenance; use a separately reviewed retention plan")
