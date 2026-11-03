"""Isolated ERP-only workflow observation; no HCM contract changes."""
from alembic import op
import sqlalchemy as sa

revision = "0005_workflow_progress"
down_revision = "0004_learning"
branch_labels = None
depends_on = None


def upgrade():
    # No FK to cases: progress writes must never wait for the business row lock.
    op.create_table("workflow_progress",
                    sa.Column("case_id", sa.Integer(), primary_key=True),
                    sa.Column("run_id", sa.String(36), nullable=False),
                    sa.Column("data", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("workflow_progress")
