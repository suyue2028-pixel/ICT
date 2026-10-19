"""Add password accounts without changing existing user tokens or conversations."""
from alembic import op
import sqlalchemy as sa

revision = "0012_phone_accounts"
down_revision = "0011_enrichment_tasks"
branch_labels = depends_on = None


def upgrade():
    op.create_table("user_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("phone", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("phone_verified", sa.Boolean(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("phone", name="uq_user_accounts_phone"))
    op.add_column("access_tokens", sa.Column("account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_access_tokens_account_id_user_accounts", "access_tokens", "user_accounts", ["account_id"], ["id"])
    op.create_index("ix_access_tokens_account_id", "access_tokens", ["account_id"])


def downgrade():
    raise RuntimeError("Account data must be retained; destructive downgrade requires a retention plan")
