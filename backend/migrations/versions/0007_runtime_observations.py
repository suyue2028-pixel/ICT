"""Persist bounded observation windows and actual samples without rewriting history."""
from alembic import op
import sqlalchemy as sa

revision = "0007_runtime_observations"
down_revision = "0006_source_freshness"
branch_labels = depends_on = None


def upgrade():
    op.create_table("runtime_observations", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_seconds", sa.Integer(), nullable=False))
    op.create_table("runtime_samples", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("observation_id", sa.Uuid(), sa.ForeignKey("runtime_observations.id"), nullable=False),
        sa.Column("bucket", sa.Integer(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.UniqueConstraint("observation_id", "bucket", name="uq_runtime_sample_bucket"))
    op.create_index("ix_runtime_samples_observation_id", "runtime_samples", ["observation_id"])


def downgrade():
    raise RuntimeError("Retain observation evidence; rollback requires an explicit retention plan")
