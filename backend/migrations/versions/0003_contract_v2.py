"""Contract v2: module and contract hash on cases and interpretation memories.

Cases created under contract v1 keep an empty contract_hash, so they are excluded from automatic
memory and must be analysed again before any acceptance or execution."""
from alembic import op
import sqlalchemy as sa

revision = "0003_contract_v2"
down_revision = "0002_orchestration"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("cases", sa.Column("module", sa.String(40), nullable=False, server_default=""))
    op.add_column("cases", sa.Column("contract_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("interpretation_memories", sa.Column("contract_hash", sa.String(64), nullable=False, server_default=""))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("UPDATE cases SET module = COALESCE(workflow->>'module', '') WHERE module = ''")
        op.execute("UPDATE cases SET module = 'timekeeping' WHERE module = 'time'")
        op.execute("UPDATE cases SET module = 'vacations' WHERE module = '' AND snapshot::text <> '{}'")

def downgrade():
    op.drop_column("interpretation_memories", "contract_hash")
    op.drop_column("cases", "contract_hash")
    op.drop_column("cases", "module")
