"""Learning and observability: operator settings and lessons (counterexamples)."""
from alembic import op
import sqlalchemy as sa

revision = "0004_learning"
down_revision = "0003_contract_v2"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False))
    op.create_table("lessons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False, index=True),
        sa.Column("company_code", sa.String(20), nullable=False, server_default=""),
        sa.Column("module", sa.String(40), nullable=False, index=True),
        sa.Column("contract_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.String(40), nullable=False))

def downgrade():
    op.drop_table("lessons")
    op.drop_table("settings")
