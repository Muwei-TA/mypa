"""Decision precedents (M2-17, PRD §11.1 / §13.2 / v1.1·K9).

Before pushing a decision card, the judge layer consults the ledger first:
a precedent with a matching scope and sufficient similarity is applied
automatically and logged as ``precedent_applied``; otherwise a card goes out.
Four categories (``delete_data``/``spend_money``/``secret_change``/
``permission_change``) are never auto-resolved by precedent.

M2 uses FTS5 keyword similarity; the 0.85 vector threshold activates in M5
once ``sqlite-vec`` lands (PRD README A3). The ledger also mirrors to
``precedents/{decided,rejected}.md`` under the config dir for git-managed
editing, with two-way sync.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from pa.common import new_id, now

NEVER_AUTO = ("delete_data", "spend_money", "secret_change", "permission_change")


def _expired(value):
    if not value:
        return False
    try:
        expiry = datetime.fromisoformat(value)
        return expiry.tzinfo is None or expiry <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True


@dataclass(frozen=True)
class Precedent:
    id: str
    kind: str  # precedent_decided | precedent_rejected
    title: str
    content: str
    scope: str
    expires_at: str | None


@dataclass(frozen=True)
class PrecedentHit:
    precedent: Precedent
    similarity: float


def add_precedent(
    connection,
    *,
    kind: str,
    title: str,
    content: str,
    scope: str,
    quote: str = "",
    decided_at: str | None = None,
    decision_id: str | None = None,
    expires_at: str | None = None,
    source_task_id: str | None = None,
) -> str:
    """Write one precedent into ``memories`` (K9)."""
    if kind not in ("precedent_decided", "precedent_rejected"):
        raise ValueError("kind must be a precedent type")
    memory_id = new_id()
    connection.execute(
        text("""
        INSERT INTO memories(id,kind,scope,title,content,quote,decided_at,decision_id,
          tags,source_task_id,expires_at)
        VALUES(:id,:kind,:scope,:title,:content,:quote,:decided_at,:decision_id,
          :tags,:task,:expires_at)
    """),
        {
            "id": memory_id,
            "kind": kind,
            "scope": scope,
            "title": title,
            "content": content,
            "quote": quote,
            "decided_at": decided_at or now(),
            "decision_id": decision_id,
            "tags": json.dumps(["precedent"], ensure_ascii=False),
            "task": source_task_id,
            "expires_at": expires_at,
        },
    )
    _fts_reindex(connection, memory_id)
    return memory_id


def _fts_reindex(connection, memory_id: str) -> None:
    """External-content FTS5 sync: full rebuild from the memories table.

    The 'delete' command requires the previously indexed values, which we do
    not track per write, so a rebuild is the safe primitive here (the table
    stays small: precedents are added manually).
    """
    row = connection.execute(
        text("SELECT id FROM memories WHERE id=:id"), {"id": memory_id}
    ).scalar_one_or_none()
    if row is None:
        return
    connection.execute(text("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"))


def list_precedents(connection, *, kind: str | None = None) -> list[Precedent]:
    query = """
        SELECT id,kind,title,content,scope,expires_at FROM memories
        WHERE kind IN ('precedent_decided','precedent_rejected')
    """
    params: dict = {}
    if kind:
        query += " AND kind=:kind"
        params["kind"] = kind
    query += " ORDER BY decided_at DESC"
    rows = connection.execute(text(query), params).all()
    return [
        Precedent(
            id=row[0],
            kind=row[1],
            title=row[2],
            content=row[3],
            scope=row[4] or "",
            expires_at=row[5],
        )
        for row in rows
    ]


def _scope_applies(precedent_scope: str, question_scope: str) -> bool:
    """A precedent applies when scopes are equal or the precedent is global."""
    ps, qs = (precedent_scope or "").strip().lower(), (question_scope or "").strip().lower()
    return ps in ("", "global") or ps == qs


def _bigrams(value: str) -> set[str]:
    """CJK bigrams + ASCII words; FTS5 unicode61 treats CJK runs as one token."""
    grams = set()
    for run in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", value):
        if run.isascii():
            grams.add(run.lower())
        else:
            grams.update(run[i : i + 2] for i in range(len(run) - 1))
    return grams


def _recall(question_grams: set[str], precedent_grams: set[str]) -> float:
    """Share of the question's keywords covered by the precedent."""
    if not question_grams:
        return 0.0
    return len(question_grams & precedent_grams) / len(question_grams)


def search_precedents(connection, *, question: str, scope: str = "") -> list[PrecedentHit]:
    """Keyword retrieval over precedents (M2 stand-in for the M5 vector search).

    FTS5's unicode61 tokenizer treats a CJK run as a single token, so MATCH
    cannot hit Chinese keywords; the index stays synced for M5, while this
    path scores candidates with bigram recall (question -> precedent) plus
    scope matching. Hits require recall >= 0.5.
    """
    q_grams = _bigrams(question)
    if not q_grams:
        return []
    rows = connection.execute(
        text("""
        SELECT id,kind,title,content,scope,expires_at FROM memories
        WHERE kind IN ('precedent_decided','precedent_rejected')
        ORDER BY decided_at DESC
    """)
    ).all()
    hits = []
    for row in rows:
        if _expired(row[5]):
            continue
        if not _scope_applies(row[4], scope):
            continue
        p_grams = _bigrams(row[2]) | _bigrams(row[3])
        similarity = _recall(q_grams, p_grams)
        # Spoken-form questions dilute bigram recall with filler words
        # (要不要/吗), so the hit floor sits at 0.4 for M2 keyword mode.
        if similarity >= 0.4:
            hits.append(
                PrecedentHit(
                    precedent=Precedent(
                        id=row[0],
                        kind=row[1],
                        title=row[2],
                        content=row[3],
                        scope=row[4] or "",
                        expires_at=row[5],
                    ),
                    similarity=similarity,
                )
            )
    hits.sort(key=lambda h: h.similarity, reverse=True)
    return hits


def check_precedent(
    connection, *, question: str, scope: str = "", min_similarity: float = 0.4
) -> dict:
    """Judge-layer entry: ``{"status": "hit"|"miss"|"never_auto", ...}``."""
    tag = next((t for t in NEVER_AUTO if t in question or t in scope), None)
    if tag:
        return {"status": "never_auto", "tag": tag}
    hits = search_precedents(connection, question=question, scope=scope)
    if hits and hits[0].similarity >= min_similarity:
        return {"status": "hit", "hit": hits[0]}
    return {"status": "miss"}


def apply_precedent(connection, *, hit: PrecedentHit, decision_id: str) -> None:
    """Mark the decision resolved by precedent and record why (K9)."""
    from pa.common import PAError

    current = connection.execute(
        text("SELECT kind,expires_at FROM memories WHERE id=:id"), {"id": hit.precedent.id}
    ).first()
    if current is None or current[0] != hit.precedent.kind or _expired(current[1]):
        raise PAError("precedent is expired or no longer valid", "conflict")
    kind = (
        "precedent_decided" if hit.precedent.kind == "precedent_decided" else "precedent_rejected"
    )
    answer = "reject" if hit.precedent.kind == "precedent_rejected" else "approve"
    connection.execute(
        text("""
        UPDATE decisions SET status='precedent',answer=:answer,answered_by='precedent',
          precedent_id=:pid,reason_json=:reason WHERE id=:id
    """),
        {
            "answer": answer,
            "pid": hit.precedent.id,
            "reason": json.dumps(
                {
                    "code": "precedent_applied",
                    "values": {
                        "precedent_id": hit.precedent.id,
                        "similarity": round(hit.similarity, 3),
                        "kind": kind,
                    },
                },
                ensure_ascii=False,
            ),
            "id": decision_id,
        },
    )


def expired_precedents(connection) -> list[Precedent]:
    rows = connection.execute(
        text("""
        SELECT id,kind,title,content,scope,expires_at FROM memories
        WHERE kind IN ('precedent_decided','precedent_rejected')
          AND expires_at IS NOT NULL AND expires_at < :now
    """),
        {"now": now()},
    ).all()
    return [
        Precedent(
            id=row[0],
            kind=row[1],
            title=row[2],
            content=row[3],
            scope=row[4] or "",
            expires_at=row[5],
        )
        for row in rows
    ]


# --- Markdown mirror (config dir, git-managed) -----------------------------

MD_HEADER = "<!-- pa:precedents mirror; generated file, edits sync back -->\n"


def export_md(connection) -> dict[str, str]:
    """Render decided/rejected markdown for the config-dir mirror."""
    out = {"decided": MD_HEADER, "rejected": MD_HEADER}
    for kind, key in (("precedent_decided", "decided"), ("precedent_rejected", "rejected")):
        rows = list_precedents(connection, kind=kind)
        lines = [f"# 定了{'' if key == 'decided' else '不做'}的事\n"]
        for p in rows:
            expires = f"（有效期至 {p.expires_at}）" if p.expires_at else ""
            lines.append(f"- [{p.id}] {p.title}{expires}：{p.content}\n")
        out[key] += "".join(lines)
    return out


def parse_md(content: str) -> list[dict]:
    """Parse a mirror file back into records; used by two-way sync."""
    records = []
    pattern = re.compile(
        r"^- \[(?P<id>[^\]]+)\] "
        r"(?P<title>[^：\n]+?)(（有效期至 (?P<exp>[^）]+)）)?：(?P<content>.+)$"
    )
    for line in content.splitlines():
        match = pattern.match(line.strip())
        if match:
            records.append(
                {
                    "id": match.group("id"),
                    "title": match.group("title"),
                    "expires_at": match.group("exp"),
                    "content": match.group("content"),
                }
            )
    return records
