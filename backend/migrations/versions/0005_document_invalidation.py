"""Logical document invalidation with a separate, idempotent delete event."""
from alembic import op
import sqlalchemy as sa
revision = "0005_document_invalidation"
down_revision = "0004_agent_data_contracts"
branch_labels = depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])
    op.drop_constraint("uq_knowledge_events_document_id", "knowledge_events", type_="unique")
    op.create_unique_constraint("uq_knowledge_event_version_operation", "knowledge_events", ["document_id", "content_hash", "operation"])


def downgrade():
    raise RuntimeError("Downgrade requires explicit retention plan")
