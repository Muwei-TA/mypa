"""Align version keys and API enums; persist breaker failures and code workspaces."""

from alembic import op

revision = "0008_review_repairs"
down_revision = "0007_task_stage_view"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE config_versions_new(
          revision INTEGER NOT NULL, file TEXT NOT NULL, content_hash TEXT, base_revision INTEGER,
          origin TEXT CHECK(origin IN('console','cli','agent','upgrade')), actor TEXT, at TEXT,
          PRIMARY KEY(file,revision))
    """)
    op.execute("INSERT INTO config_versions_new SELECT * FROM config_versions")
    op.execute("DROP TABLE config_versions")
    op.execute("ALTER TABLE config_versions_new RENAME TO config_versions")
    op.execute("""
        CREATE TABLE budgets_new(id TEXT PRIMARY KEY,
          scope TEXT CHECK(scope IN('global','skill','engine','task')), scope_id TEXT,
          period TEXT CHECK(period IN('day','month')),
          limit_cny REAL, soft_pct REAL, hard_action TEXT)
    """)
    op.execute("INSERT INTO budgets_new SELECT * FROM budgets")
    op.execute("DROP TABLE budgets")
    op.execute("ALTER TABLE budgets_new RENAME TO budgets")
    op.execute("""
        CREATE TABLE memories_new(id TEXT PRIMARY KEY,
          kind TEXT CHECK(kind IN('fact','lesson','context','profile',
            'precedent_decided','precedent_rejected')),
          scope TEXT, title TEXT, content TEXT, quote TEXT, decided_at TEXT, decision_id TEXT,
          tags TEXT, source_task_id TEXT, expires_at TEXT)
    """)
    op.execute("INSERT INTO memories_new SELECT * FROM memories ORDER BY rowid")
    op.execute("DROP TABLE memories")
    op.execute("ALTER TABLE memories_new RENAME TO memories")
    op.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    op.execute("""
        CREATE TABLE engine_failures(id TEXT PRIMARY KEY,engine_id TEXT NOT NULL,
          kind TEXT NOT NULL,at TEXT NOT NULL)
    """)
    op.execute("CREATE INDEX ix_engine_failures ON engine_failures(engine_id,at)")
    op.execute("ALTER TABLE runs ADD COLUMN native_calls INTEGER NOT NULL DEFAULT 0")
    op.execute("""
        CREATE TABLE budget_reservations(run_id TEXT PRIMARY KEY, task_id TEXT, engine_id TEXT,
          skill_id TEXT, cost_cny REAL NOT NULL, at TEXT NOT NULL)
    """)
    op.execute("""
        CREATE TABLE code_workspaces(task_id TEXT PRIMARY KEY REFERENCES tasks(id),
          path TEXT NOT NULL,mirror TEXT NOT NULL,branch TEXT NOT NULL,
          base_sha TEXT NOT NULL,head_sha TEXT,stage TEXT NOT NULL DEFAULT 'prepared')
    """)


def downgrade():
    raise RuntimeError("PA migrations are forward-only; restore a backup to downgrade")
