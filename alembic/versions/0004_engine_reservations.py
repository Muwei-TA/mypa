"""Reserve an engine slot as part of the task lease, before creating a run."""

from alembic import op

revision = "0004_engine_reservations"
down_revision = "0003_schedules"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tasks ADD COLUMN lease_engine_id TEXT")
    op.execute("CREATE INDEX ix_tasks_engine_lease ON tasks(lease_engine_id,status)")


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
