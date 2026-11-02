"""Leased, recoverable outbox execution and persisted delivery results."""

from alembic import op

revision = "0002_effect_leases"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        "lease_owner TEXT",
        "lease_until TEXT",
        "last_attempt_at TEXT",
        "first_attempt_at TEXT",
        "result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json))",
    ):
        op.execute("ALTER TABLE effects ADD COLUMN " + column)


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
