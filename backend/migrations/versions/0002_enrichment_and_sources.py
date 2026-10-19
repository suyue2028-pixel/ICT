"""Extend the existing framework without replacing its initial schema or data."""

from alembic import op
import sqlalchemy as sa

revision = "0002_enrichment_and_sources"
down_revision = "0001_scaffold"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("data_sources", sa.Column("config", sa.JSON(), nullable=False,
                                             server_default=sa.text("'{}'::json")))
    op.alter_column("documents", "external_id", type_=sa.String(1024),
                    existing_type=sa.String(200), existing_nullable=False)
    op.add_column("documents", sa.Column("first_collected_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE documents SET first_collected_at = created_at")
    op.alter_column("documents", "first_collected_at", nullable=False,
                    server_default=sa.text("now()"))
    op.add_column("documents", sa.Column("ai_relevant", sa.Boolean(), nullable=False,
                                        server_default=sa.text("false")))
    op.add_column("documents", sa.Column("relevance", sa.JSON(), nullable=False,
                                        server_default=sa.text("'{}'::json")))
    # Existing records in this dedicated project are explicitly labelled demo rows.
    op.execute("UPDATE documents SET ai_relevant = true, relevance = "
               "'{\"relevant\": true, \"method\": \"migration-demo-label\"}'::json WHERE is_demo")
    op.create_index("ix_documents_ai_relevant", "documents", ["ai_relevant"])
    op.add_column("vulnerabilities", sa.Column("ai_relevant", sa.Boolean(), nullable=False,
                                              server_default=sa.text("false")))
    op.execute("UPDATE vulnerabilities SET ai_relevant = true WHERE is_demo")
    op.create_index("ix_vulnerabilities_ai_relevant", "vulnerabilities", ["ai_relevant"])
    op.create_table(
        "vulnerability_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vulnerability_id", sa.Uuid(),
                  sa.ForeignKey("vulnerabilities.id"), nullable=False),
        sa.Column("value", sa.String(160), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("value", "is_demo", name="uq_alias_value_demo"),
    )
    op.create_index("ix_vulnerability_aliases_vulnerability_id", "vulnerability_aliases", ["vulnerability_id"])
    op.create_index("ix_vulnerability_aliases_is_demo", "vulnerability_aliases", ["is_demo"])
    op.execute("INSERT INTO vulnerability_aliases (id, vulnerability_id, value, is_demo) "
               "SELECT gen_random_uuid(), id, upper(identifier), is_demo FROM vulnerabilities "
               "WHERE identifier ~* '^(CVE-[0-9]{4}-[0-9]{4,}|GHSA-[A-Za-z0-9-]+|PYSEC-[A-Za-z0-9-]+|OSV-[A-Za-z0-9-]+)$'")
    op.add_column("evidence", sa.Column("state", sa.String(20), nullable=False,
                                       server_default="known"))
    op.add_column("evidence", sa.Column("excerpt", sa.Text()))
    op.drop_constraint("uq_evidence_document_id", "evidence", type_="unique")
    op.create_unique_constraint("uq_evidence_revision_field_method", "evidence",
                                ["document_id", "content_hash", "field", "method"])
    op.add_column("knowledge_events", sa.Column("attempts", sa.Integer(), nullable=False,
                                                server_default="0"))
    op.add_column("knowledge_events", sa.Column("detail", sa.JSON(), nullable=False,
                                               server_default=sa.text("'{}'::json")))


def downgrade():
    raise RuntimeError("Destructive downgrade requires an explicit data retention plan")
