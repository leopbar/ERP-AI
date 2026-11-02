"""Initial case ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0001_cases"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.String(10), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("company_code", sa.String(20), nullable=False),
        sa.Column("catalog_version", sa.String(80), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False))

def downgrade():
    op.drop_table("cases")
