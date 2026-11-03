"""Global search across events, threads (subscriptions) and tasks (M3-10).

A single `?q=` query dispatches on shape:
- event id / task id (exact prefix) -> direct entity lookup
- `#12` -> tasks/events whose payload or summary mentions that ref
- URL (http...) -> events whose payload_json or link contains it
- plain text -> LIKE search over event summaries, task ids, thread refs

Each result carries {kind, id, title, link, matched_on} so the console can
render one mixed list. Limit is applied per kind after ranking.
"""

from sqlalchemy import text

_LIMIT_PER_KIND = 20


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search(connection, query: str, *, limit: int = _LIMIT_PER_KIND) -> dict:
    q = (query or "").strip()
    if not q:
        return {"query": q, "items": []}

    items: list[dict] = []

    # 1) exact / prefix id lookup (events, tasks)
    like = _like(q)
    sql = (
        "SELECT id, summary, type, link FROM events "
        "WHERE id LIKE :prefix OR summary LIKE :like LIMIT :lim"
    )
    params = {"prefix": f"{q}%", "like": like, "lim": limit}
    for row in connection.execute(text(sql), params).mappings():
        items.append(
            {
                "kind": "event",
                "id": row["id"],
                "title": row["summary"] or row["type"],
                "link": f"/tasks?event={row['id']}",
                "matched_on": "id" if row["id"].startswith(q) else "summary",
            }
        )
    sql = (
        "SELECT t.id, t.status, t.skill_id, e.summary FROM tasks t "
        "LEFT JOIN events e ON e.id = t.event_id "
        "WHERE t.id LIKE :prefix LIMIT :lim"
    )
    params = {"prefix": f"{q}%", "lim": limit}
    for row in connection.execute(text(sql), params).mappings():
        items.append(
            {
                "kind": "task",
                "id": row["id"],
                "title": row["summary"] or f"{row['skill_id']} ({row['status']})",
                "link": f"/tasks/{row['id']}",
                "matched_on": "id",
            }
        )

    # 2) #ref lookup: match summary / payload / subject / thread refs
    if q.startswith("#"):
        ref = q
        sql = (
            "SELECT id, summary, type FROM events "
            "WHERE summary LIKE :like OR subject LIKE :like OR payload_json LIKE :like "
            "ORDER BY received_at DESC LIMIT :lim"
        )
        params = {"like": _like(ref), "lim": limit}
        for row in connection.execute(text(sql), params).mappings():
            items.append(
                {
                    "kind": "event",
                    "id": row["id"],
                    "title": row["summary"] or row["type"],
                    "link": f"/tasks?event={row['id']}",
                    "matched_on": "ref",
                }
            )

    # 3) URL lookup: events with matching link or payload
    if q.startswith("http://") or q.startswith("https://"):
        like = _like(q)
        sql = (
            "SELECT id, summary, type, link FROM events "
            "WHERE link LIKE :like OR payload_json LIKE :like "
            "ORDER BY received_at DESC LIMIT :lim"
        )
        params = {"like": like, "lim": limit}
        for row in connection.execute(text(sql), params).mappings():
            items.append(
                {
                    "kind": "event",
                    "id": row["id"],
                    "title": row["summary"] or row["type"],
                    "link": f"/tasks?event={row['id']}",
                    "matched_on": "url",
                }
            )

    # 4) thread lookup: subscription thread_refs
    sql = "SELECT DISTINCT thread_ref FROM subscriptions WHERE thread_ref LIKE :like LIMIT :lim"
    params = {"like": _like(q), "lim": limit}
    for row in connection.execute(text(sql), params).mappings():
        items.append(
            {
                "kind": "thread",
                "id": row["thread_ref"],
                "title": row["thread_ref"],
                "link": f"/tasks?thread={row['thread_ref']}",
                "matched_on": "thread_ref",
            }
        )

    # 5) plain text: task payloads / summaries via events
    like = _like(q)
    sql = (
        "SELECT t.id, t.status, t.skill_id, e.summary FROM tasks t "
        "JOIN events e ON e.id = t.event_id "
        "WHERE e.summary LIKE :like OR e.payload_json LIKE :like "
        "ORDER BY t.enqueued_at DESC LIMIT :lim"
    )
    params = {"like": like, "lim": limit}
    for row in connection.execute(text(sql), params).mappings():
        if any(i["kind"] == "task" and i["id"] == row["id"] for i in items):
            continue
        items.append(
            {
                "kind": "task",
                "id": row["id"],
                "title": row["summary"] or f"{row['skill_id']} ({row['status']})",
                "link": f"/tasks/{row['id']}",
                "matched_on": "text",
            }
        )

    return {"query": q, "items": items}
