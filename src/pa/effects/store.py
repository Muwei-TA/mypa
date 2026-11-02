import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pa.common import new_id, now
from pa.obs.redact import redact


def enqueue(
    connection: Connection,
    *,
    kind: str,
    idem_key: str,
    payload: dict,
    task_id: str | None = None,
    target: str | None = None,
    order_key: str | None = None,
) -> str:
    """Call in the SAME transaction as the business-state change. No I/O here."""
    id_ = new_id()
    connection.execute(
        text("""
        INSERT INTO effects(id,task_id,kind,target,idem_key,
          payload_json,status,created_at,order_key)
        VALUES(:id,:task,:kind,:target,:key,:payload,'pending',:at,:order_key)
        ON CONFLICT(idem_key) DO NOTHING
    """),
        {
            "id": id_,
            "task": task_id,
            "kind": kind,
            "target": target,
            "key": idem_key,
            "payload": json.dumps(redact(payload)),
            "at": now(),
            "order_key": order_key,
        },
    )
    return connection.execute(
        text("SELECT id FROM effects WHERE idem_key=:key"),
        {
            "key": idem_key,
        },
    ).scalar_one()
