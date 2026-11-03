"""Transactional decision submission shared by REST and signed card callbacks."""

import json
import secrets

from sqlalchemy import text

from pa.common import PAError, new_id, now
from pa.store.models import Reason
from pa.store.transitions import transition

LEGACY_OPTIONS = ["approve", "reject", "retry", "retry_other_engine", "take_over"]


def submit(connection, decision_id, option, *, actor="owner", source="manual", save_as=None):
    row = (
        connection.execute(text("SELECT * FROM decisions WHERE id=:id"), {"id": decision_id})
        .mappings()
        .first()
    )
    if row is None:
        raise PAError("decision not found", "not_found", 4)
    if row["status"] != "pending":
        raise PAError("decision is no longer pending", "conflict")
    if row["expires_at"] and row["expires_at"] <= now():
        raise PAError("decision expired", "conflict")
    options = json.loads(row["options_json"]) if row["options_json"] else LEGACY_OPTIONS
    if option not in options:
        raise PAError("option is not offered by this decision", "bad_request")
    if save_as not in (None, "decided", "rejected"):
        raise PAError("invalid precedent choice", "bad_request")
    result = connection.execute(
        text(
            "UPDATE decisions SET status='answered',answer=:option,answered_by=:actor,"
            "save_as_precedent=:save,nonce=:nonce WHERE id=:id AND status='pending'"
        ),
        {
            "id": decision_id,
            "option": option,
            "actor": actor,
            "save": save_as,
            "nonce": secrets.token_hex(16),
        },
    )
    if result.rowcount != 1:
        raise PAError("decision already answered", "conflict")
    event_id = new_id()
    connection.execute(
        text(
            "INSERT INTO events(id,source,type,category,actor,principal,subject,dedup_key,"
            "trust,status,payload_json,received_at,summary,addressed) "
            "VALUES(:id,:source,'decision.submitted','decision',:actor,'owner',:task,:dedup,"
            "'verified','accepted',:payload,:at,:summary,1)"
        ),
        {
            "id": event_id,
            "source": source,
            "actor": actor,
            "task": row["task_id"],
            "dedup": f"decision:{decision_id}",
            "payload": json.dumps(
                {"decision_id": decision_id, "option": option, "save_as_precedent": save_as}
            ),
            "at": now(),
            "summary": f"决定已提交：{option}",
        },
    )
    task = (
        connection.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": row["task_id"]})
        .mappings()
        .first()
    )
    if task and task["status"] == "awaiting_decision":
        reason = [
            Reason(code="decision_answered", values={"decision_id": decision_id, "option": option})
        ]
        target = None
        if option in ("retry", "retry_other_engine", "one_more_round"):
            target = "queued"
            if option == "retry_other_engine":
                connection.execute(
                    text(
                        "UPDATE tasks SET engine_hint=(SELECT engine_id FROM runs "
                        "WHERE task_id=:id "
                        "ORDER BY rowid DESC LIMIT 1) WHERE id=:id"
                    ),
                    {"id": task["id"]},
                )
        elif option in ("reject", "take_over"):
            target = "suspended"
        elif option == "approve":
            target = "running" if row["kind"] in ("merge_confirm", "tool_gate") else "closed"
        if target:
            current = transition(connection, task["id"], target, task["version"], reason)
            if row["kind"] == "merge_confirm" and target == "running":
                transition(connection, task["id"], "awaiting_ci", current["version"], reason)
            connection.execute(
                text(
                    "UPDATE tasks SET lease_owner=NULL,lease_until=NULL,lease_engine_id=NULL,"
                    "stopped_by_owner=:stopped WHERE id=:id"
                ),
                {"id": task["id"], "stopped": int(target == "suspended")},
            )
    connection.execute(
        text(
            "INSERT INTO audit_log(id,actor,action,target,at) "
            "VALUES(:id,'owner',:action,:target,:at)"
        ),
        {"id": new_id(), "action": f"decision.answer:{option}", "target": decision_id, "at": now()},
    )
    return {
        "decision_id": decision_id,
        "answer": option,
        "status": "answered",
        "event_id": event_id,
    }
