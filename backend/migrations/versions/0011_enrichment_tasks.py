"""Separate per-document enrichment jobs from the source collection mutex."""
from alembic import op
import sqlalchemy as sa

revision = "0011_enrichment_tasks"
down_revision = "0010_public_snapshots"
branch_labels = depends_on = None


def upgrade():
    op.drop_index("uq_active_source", table_name="task_runs")
    op.create_index("uq_active_source", "task_runs", ["source_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued','running') AND kind IN ('collect','offline_demo')"))


def downgrade():
    busy = op.get_bind().execute(sa.text("SELECT source_id FROM task_runs WHERE status IN ('queued','running') GROUP BY source_id HAVING count(*)>1 LIMIT 1")).first()
    if busy:
        raise RuntimeError("Finish active document jobs before restoring the old source mutex")
    op.drop_index("uq_active_source", table_name="task_runs")
    op.create_index("uq_active_source", "task_runs", ["source_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"))
