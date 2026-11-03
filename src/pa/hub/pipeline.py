import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pa.common import new_id, now
from pa.config.schema import LoadedConfig
from pa.hub.router import matches
from pa.ingress.base import Event
from pa.obs.redact import redact


def principal_for(event: Event, config: LoadedConfig) -> str | None:
    # Internal adapters are trusted only after the ingress boundary verifies their source.
    if event.trust != "verified":
        return None
    if event.source in {"manual", "cron"} and event.actor == "owner":
        return "owner"
    owner = config.principals.owner
    identities = {
        f"{source}:{identity}"
        for source, identity in (
            ("github", owner.github),
            ("gitea", owner.gitea),
            ("feishu", owner.feishu_open_id),
        )
        if identity
    }
    return "owner" if event.actor in identities else None


def ingest(connection: Connection, event: Event, config: LoadedConfig) -> dict:
    existing = connection.execute(
        text("SELECT id FROM events WHERE source=:source AND dedup_key=:key"),
        {"source": event.source, "key": event.dedup_key},
    ).scalar_one_or_none()
    if existing:
        return {"event": {"id": existing, "status": "duplicate"}, "tasks": []}
    principal = principal_for(event, config)
    status = "rejected" if event.trust != "verified" else "observed"
    if principal:
        status = "accepted"
    data = event.model_dump(mode="json")
    connection.execute(
        text("""
        INSERT INTO events(id,source,type,category,actor,principal,subject,dedup_key,trust,
          status,payload_json,received_at,summary,link,addressed)
        VALUES(:id,:source,:type,:category,:actor,:principal,:subject,:dedup_key,:trust,
          :status,:payload,:received_at,:summary,:link,:addressed)
    """),
        {
            **{
                k: data[k]
                for k in (
                    "id",
                    "source",
                    "type",
                    "category",
                    "actor",
                    "subject",
                    "dedup_key",
                    "trust",
                    "received_at",
                    "summary",
                    "link",
                    "addressed",
                )
            },
            "principal": principal,
            "status": status,
            "payload": json.dumps(redact(data["payload"])),
        },
    )
    task_ids = []
    # A verified owner comment on a subscribed Issue or PR is part of that
    # task, not a new unrelated issue. The context is attached durably and
    # waiting/closed tasks are requeued after the subscription's checks.
    if principal and event.source in {"github", "gitea"} and ".comment." in event.type:
        from pa.hub.subscriptions import route as route_thread
        from pa.store.models import Reason
        from pa.store.transitions import transition

        for thread in event.threads:
            routed = route_thread(
                connection,
                config,
                thread_ref=thread,
                actor=event.actor or "",
                body=event.payload.get("body", ""),
            )
            if routed["action"] not in {"deliver", "resume"}:
                continue
            task_id = routed["task_id"]
            current = (
                connection.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task_id})
                .mappings()
                .one()
            )
            connection.execute(
                text(
                    "INSERT INTO task_events(id,task_id,from_status,to_status,"
                    "reason_json,actor,at) VALUES(:id,:task,NULL,NULL,:reason,:actor,:at)"
                ),
                {
                    "id": new_id(),
                    "task": task_id,
                    "actor": event.actor or "owner",
                    "at": now(),
                    "reason": json.dumps(
                        [
                            {
                                "code": "context_added",
                                "values": {"event_id": event.id},
                            }
                        ]
                    ),
                },
            )
            if current["status"] in {"awaiting_ci", "awaiting_decision", "closed"}:
                if current["status"] == "awaiting_decision":
                    connection.execute(
                        text(
                            "UPDATE decisions SET status='expired' "
                            "WHERE task_id=:id AND status='pending'"
                        ),
                        {"id": task_id},
                    )
                transition(
                    connection,
                    task_id,
                    "queued",
                    current["version"],
                    [Reason(code="owner_comment_requeue", values={"event_id": event.id})],
                )
            return {
                "event": {"id": event.id, "status": "accepted"},
                "tasks": [task_id],
                "subscription": routed["action"],
            }
    if principal:
        for rule in sorted(config.rules.rules, key=lambda r: (r.rank, r.id)):
            if not rule.enabled or not matches(rule, event):
                continue
            if rule.require.actor_in and principal not in rule.require.actor_in:
                continue
            explicit = event.source == "manual" or event.addressed or bool(rule.require.actor_in)
            mode = rule.accept_mode or config.main.defaults.accept_mode_proactive
            if not explicit and mode != "off" and (mode == "observe" or rule.assess):
                # TODO(PRD/M1-09): wire the model assessor. It must not silently approve auto.
                connection.execute(
                    text("""
                    INSERT INTO observations(id,event_id,rule_id,would_accept,assessment_json,
                      policy_version,at) VALUES(:id,:event,:rule,:would,:assessment,:policy,:at)
                """),
                    {
                        "id": new_id(),
                        "event": event.id,
                        "rule": rule.id,
                        "would": int(not rule.assess),
                        "assessment": json.dumps(
                            {"status": "pending" if rule.assess else "not_required"}
                        ),
                        "policy": config.policy_version,
                        "at": now(),
                    },
                )
            elif explicit or mode == "auto":
                merged = None
                if rule.merge_by == "subject" and event.subject:
                    merged = connection.execute(
                        text("""
                        SELECT t.id FROM tasks t JOIN events e ON e.id=t.event_id
                        WHERE e.source=:source AND e.subject=:subject AND t.skill_id=:skill
                          AND t.status NOT IN (
                            'closed','cancelled','succeeded','judged','suspended')
                        ORDER BY t.enqueued_at LIMIT 1
                    """),
                        {"source": event.source, "subject": event.subject, "skill": rule.skill},
                    ).scalar_one_or_none()
                task_id = merged or new_id()
                if merged:
                    connection.execute(
                        text("""
                        INSERT INTO task_events(
                          id,task_id,from_status,to_status,reason_json,actor,at)
                        VALUES(:id,:task,NULL,NULL,:reason,'system',:at)
                    """),
                        {
                            "id": new_id(),
                            "task": task_id,
                            "at": now(),
                            "reason": json.dumps(
                                [
                                    {"code": "context_added", "values": {"event_id": event.id}},
                                ]
                            ),
                        },
                    )
                else:
                    mandate = {
                        "policy_version": config.policy_version,
                        "strength": "explicit" if explicit else "inferred",
                        "via": "rule",
                        "actor": event.actor,
                        "matched_rules": [rule.id],
                        "scope": event.summary,
                        "source_scope": f"{event.source}:{event.subject or ''}",
                    }
                    connection.execute(
                        text("""
                        INSERT INTO tasks(
                          id,event_id,skill_id,status,priority,skill_rank,enqueued_at,
                          mandate_json,policy_version,repo,concurrency_key)
                        VALUES(:id,:event,:skill,'queued',:priority,:rank,:at,:mandate,:policy,:repo,:key)
                    """),
                        {
                            "id": task_id,
                            "event": event.id,
                            "skill": rule.skill,
                            "priority": rule.priority,
                            "rank": rule.rank,
                            "at": now(),
                            "mandate": json.dumps(mandate),
                            "policy": config.policy_version,
                            "repo": rule.repo
                            or (
                                event.payload.get("repo")
                                if isinstance(event.payload.get("repo"), str)
                                else None
                            ),
                            "key": event.subject
                            if rule.concurrency_key == "subject"
                            else rule.concurrency_key,
                        },
                    )
                if event.source in {"github", "gitea"}:
                    from pa.hub.subscriptions import subscribe

                    for thread in event.threads:
                        subscribe(connection, task_id=task_id, thread_ref=thread)
                task_ids.append(task_id)
            if not rule.continue_:
                break
    return {"event": {"id": event.id, "status": status}, "tasks": task_ids}
