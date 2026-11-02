"""PRD v1.1 baseline tables, FTS5 and immutable mandate guard."""

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = "\n".join(
        line
        for line in Path(__file__).with_suffix(".sql").read_text().splitlines()
        if not line.lstrip().startswith("--")
    )
    for statement in sql.split(";"):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement)
    op.execute("""
    CREATE TRIGGER immutable_mandate BEFORE UPDATE OF mandate_json ON tasks
    WHEN OLD.mandate_json IS NOT NEW.mandate_json
    BEGIN SELECT RAISE(ABORT, 'mandate is immutable'); END
    """)
    op.execute("""
    CREATE TRIGGER task_status_insert BEFORE INSERT ON tasks
    WHEN NEW.status NOT IN (
      'queued','leased','running','awaiting_ci','awaiting_decision','failed',
      'succeeded','judged','closed','suspended','cancelled')
    BEGIN SELECT RAISE(ABORT, 'invalid task status'); END
    """)
    op.execute("""
    CREATE TRIGGER task_status_update BEFORE UPDATE OF status ON tasks
    WHEN NEW.status NOT IN (
      'queued','leased','running','awaiting_ci','awaiting_decision','failed',
      'succeeded','judged','closed','suspended','cancelled')
    BEGIN SELECT RAISE(ABORT, 'invalid task status'); END
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
