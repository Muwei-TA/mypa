"""Durable completion and retry checkpoints while judgement is paused."""

from alembic import op

revision = "0006_worker_checkpoints"
down_revision = "0005_turn_allowance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE worker_checkpoints (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id),
            owner TEXT,
            lease_until TEXT,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
        )
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
