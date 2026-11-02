"""Persistent APScheduler jobs; delivery cursors live in system_state."""

from alembic import op

revision = "0003_schedules"
down_revision = "0002_effect_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE scheduler_jobs (
            id VARCHAR(191) NOT NULL PRIMARY KEY,
            next_run_time FLOAT,
            job_state BLOB NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_scheduler_jobs_next_run_time ON scheduler_jobs(next_run_time)")


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
