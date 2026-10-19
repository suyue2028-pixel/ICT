"""Add versioned semantic chunks and protected conversation history.

The existing PostgreSQL 16 image has no pgvector extension. Exact cosine search
uses PostgreSQL float arrays at the current small corpus size, preserving its volume.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_semantic_conversations"
down_revision = "0002_enrichment_and_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_events", sa.Column("last_sent_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_events", sa.Column("available_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_events", sa.Column("lease_token", sa.Uuid()))
    op.add_column("knowledge_events", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.create_table(
        "embedding_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "content_hash", "chunk_index", "model",
                            name="uq_embedding_chunk_version_model"),
    )
    op.create_index("ix_embedding_chunks_document_id", "embedding_chunks", ["document_id"])
    op.create_index("ix_embedding_chunks_content_hash", "embedding_chunks", ["content_hash"])
    op.create_index("ix_embedding_chunks_is_demo", "embedding_chunks", ["is_demo"])
    op.create_table(
        "qa_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qa_conversations_is_demo", "qa_conversations", ["is_demo"])
    op.create_table(
        "qa_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("qa_conversations.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("retrieval_mode", sa.String(30)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qa_messages_conversation_id", "qa_messages", ["conversation_id"])


def downgrade():
    raise RuntimeError("Destructive downgrade requires an explicit data retention plan")
