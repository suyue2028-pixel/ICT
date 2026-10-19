"""Explicit observations and actual version changes; retain all existing records."""
from alembic import op
import sqlalchemy as sa

revision = "0006_source_freshness"
down_revision = "0005_document_invalidation"
branch_labels = depends_on = None


def upgrade():
    for name in ("last_attempt_at", "last_checked_at", "last_success_at", "last_new_content_at"):
        op.add_column("data_sources", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("data_sources", sa.Column("last_error_code", sa.String(100)))
    op.add_column("data_sources", sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"))
    for name in ("last_checked_at", "content_changed_at", "indexed_at"):
        op.add_column("documents", sa.Column(name, sa.DateTime(timezone=True)))
    op.create_index("ix_documents_content_changed_at", "documents", ["content_changed_at"])
    # Only restore timestamps actually present in task history, never invent now().
    op.execute("""UPDATE data_sources s SET
        last_attempt_at = (SELECT max(started_at) FROM task_runs t WHERE t.source_id=s.id),
        last_success_at = (SELECT max(finished_at) FROM task_runs t WHERE t.source_id=s.id AND t.status='succeeded'),
        last_checked_at = (SELECT max(finished_at) FROM task_runs t WHERE t.source_id=s.id AND t.status='succeeded')""")
    # Legacy document changes/index confirmations stay unknown. Generic updated_at
    # includes rule edits and replay, so cannot be relabelled as content/index time.


def downgrade():
    raise RuntimeError("Downgrade requires an explicit retention plan")
