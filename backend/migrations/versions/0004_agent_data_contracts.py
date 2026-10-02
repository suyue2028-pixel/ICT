"""Add source metadata, user credentials and Agent call audit; preserve old rows."""
from alembic import op
import sqlalchemy as sa

revision = "0004_agent_data_contracts"
down_revision = "0003_semantic_conversations"
branch_labels = depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("documents", sa.Column("content_completeness", sa.String(30), nullable=False, server_default="unknown"))
    op.add_column("documents", sa.Column("source_updated_at", sa.DateTime(timezone=True)))
    op.add_column("qa_conversations", sa.Column("owner_id", sa.String(100)))
    op.add_column("qa_conversations", sa.Column("provider", sa.String(20), nullable=False, server_default="deepseek"))
    op.create_index("ix_qa_conversations_owner_id", "qa_conversations", ["owner_id"])
    op.create_table("access_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_access_tokens_owner_id", "access_tokens", ["owner_id"])
    op.create_table("agent_invocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("qa_conversations.id"), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("response_payload", sa.JSON()),
        sa.Column("tool_trace", sa.JSON(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_agent_owner_idempotency"))
    op.create_index("ix_agent_invocations_owner_id", "agent_invocations", ["owner_id"])
    op.create_index("ix_agent_invocations_conversation_id", "agent_invocations", ["conversation_id"])
    op.create_index("uq_agent_active_conversation", "agent_invocations", ["conversation_id"], unique=True,
                    postgresql_where=sa.text("status = 'running'"))
    # Existing versions remain unchanged; mark completeness conservatively.
    op.execute("UPDATE documents SET content_completeness='summary' WHERE source_id IN "
               "(SELECT id FROM data_sources WHERE adapter IN ('cisco','sans'))")


def downgrade():
    raise RuntimeError("Destructive downgrade requires an explicit retention plan")
