"""Durable interpretation workflow and separately scoped memory."""
from alembic import op
import sqlalchemy as sa

revision = "0002_orchestration"
down_revision = "0001_cases"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("cases", sa.Column("workflow", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table("interpretation_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False, index=True),
        sa.Column("actor", sa.String(100), nullable=False, index=True),
        sa.Column("company_code", sa.String(20), nullable=False),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("catalog_version", sa.String(80), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False))

def downgrade():
    op.drop_table("interpretation_memories")
    op.drop_column("cases", "workflow")
