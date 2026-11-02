"""Persist turn allowance before invoking engines, including interrupted calls."""

from alembic import op

revision = "0005_turn_allowance"
down_revision = "0004_engine_reservations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE runs ADD COLUMN turns_charged INTEGER NOT NULL DEFAULT 0 "
        "CHECK(turns_charged>=0)"
    )
    op.execute("ALTER TABLE runs ADD COLUMN turns_estimated INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE runs ADD COLUMN turns_unknown INTEGER NOT NULL DEFAULT 0")
    # Do not invent counts for unfinished tasks executed before this migration.
    op.execute("UPDATE runs SET turns_unknown=1")


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
