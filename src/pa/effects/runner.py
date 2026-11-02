import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import new_id, now
from pa.hub.pause import state
from pa.obs.redact import redact
from pa.store.db import transaction


class EffectRunner:
    def __init__(self, engine, handlers):
        self.engine = engine
        self.handlers = handlers

    async def once(self):
        lease_owner = new_id()
        with transaction(self.engine) as c:
            if state(c)["paused"]:
                return None
            effect = (
                c.execute(
                    text("""
                SELECT e.* FROM effects e
                WHERE ((e.status IN ('pending','failed') AND e.attempts<8)
                  OR (e.status='running' AND e.lease_until<:now))
                  AND (e.next_at IS NULL OR e.next_at<=:now)
                  AND NOT EXISTS (
                    SELECT 1 FROM effects prev WHERE prev.order_key=e.order_key
                      AND prev.rowid<e.rowid AND prev.status NOT IN ('done','dropped'))
                ORDER BY e.rowid LIMIT 1
            """),
                    {"now": now()},
                )
                .mappings()
                .first()
            )
            if not effect:
                return None
            effect = dict(effect)
            # Feishu de-duplicates uuids for one hour. An uncertain older send needs inspection.
            if effect["first_attempt_at"] and effect["kind"].startswith("feishu."):
                age = datetime.now(UTC) - datetime.fromisoformat(effect["first_attempt_at"])
                if age > timedelta(minutes=50):
                    c.execute(
                        text("""
                        UPDATE effects SET status='failed',attempts=8,
                          last_error='delivery_unknown: inspect before retry' WHERE id=:id
                    """),
                        {"id": effect["id"]},
                    )
                    return {"id": effect["id"], "status": "failed", "reason": "delivery_unknown"}
            if effect["kind"] not in self.handlers:
                c.execute(
                    text(
                        "UPDATE effects SET status='failed',attempts=8,"
                        "last_error='handler unavailable' WHERE id=:id"
                    ),
                    {"id": effect["id"]},
                )
                return {"id": effect["id"], "status": "failed"}
            c.execute(
                text("""
                UPDATE effects SET status='running',attempts=attempts+1,
                  lease_owner=:owner,lease_until=:until,last_attempt_at=:now,
                  first_attempt_at=COALESCE(first_attempt_at,:now) WHERE id=:id
            """),
                {
                    "id": effect["id"],
                    "owner": lease_owner,
                    "now": now(),
                    "until": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                },
            )
        handler = self.handlers[effect["kind"]]
        result, error, next_at, status = None, None, None, "done"
        try:
            if not await handler.already_done(effect):
                result = await handler.execute(effect)
        except Exception as exc:
            attempt = effect["attempts"] + 1
            status = "dropped" if getattr(exc, "permanent", False) else "failed"
            error = redact(str(exc))[:500]
            base, maximum = (5, 300) if effect["kind"].startswith("feishu.") else (2, 60)
            next_at = (
                datetime.now(UTC) + timedelta(seconds=min(maximum, base * 2 ** (attempt - 1)))
            ).isoformat()
        with transaction(self.engine) as c:
            c.execute(
                text("""
                UPDATE effects SET status=:status,result_json=:result,last_error=:error,
                  next_at=:next,lease_owner=NULL,lease_until=NULL,
                  dropped_reason=CASE WHEN :status='dropped' THEN :error ELSE NULL END
                WHERE id=:id AND lease_owner=:owner
            """),
                {
                    "id": effect["id"],
                    "owner": lease_owner,
                    "status": status,
                    "result": json.dumps(redact(result)),
                    "error": error,
                    "next": next_at,
                },
            )
        return {"id": effect["id"], "status": status}
