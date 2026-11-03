"""Task stage view (M3-05, PRD §20.3 / v1.1·D10): 7 ordered stages."""

from alembic import op

revision = "0007_task_stage_view"
down_revision = "0006_worker_checkpoints"
branch_labels = None
depends_on = None

# First match wins; mutually exclusive; covers every case (docs 02 §3.9).
VIEW = """
CREATE VIEW task_stage AS SELECT t.id,
 CASE
  WHEN EXISTS(SELECT 1 FROM effects e WHERE e.task_id=t.id AND e.status='failed'
              AND e.attempts>=3) THEN 'owner/delivery'
  WHEN EXISTS(SELECT 1 FROM decisions d WHERE d.task_id=t.id AND d.status='pending')
       THEN 'owner/answer'
  WHEN t.status='succeeded' AND EXISTS(SELECT 1 FROM pull_requests p WHERE p.task_id=t.id
              AND p.ci_stage NOT IN ('merged','released','verified','delivered'))
       THEN 'owner/review'
  WHEN t.status IN ('failed','blocked') THEN 'owner/blocked_failed'
  WHEN t.status IN ('queued','leased') THEN 'working/queued'
  WHEN t.status IN ('closed','succeeded','judged') AND EXISTS(SELECT 1 FROM subscriptions s
              WHERE s.task_id=t.id AND s.closed_at IS NULL) THEN 'idle'
  ELSE 'working' END AS stage FROM tasks t
"""


def upgrade():
    op.execute(VIEW)


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a verified backup instead")
