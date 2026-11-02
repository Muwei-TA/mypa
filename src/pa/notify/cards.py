"""Decision cards (M2-16, PRD §12.3 / v1.1·D16/K9).

One card asks exactly one question taken from the receipt ``human_action``.
The callback verifies signature, owner ``open_id``, nonce anti-replay and
expiry; an accepted click becomes ``Event(category=decision,
type=decision.submitted)`` and the card updates in place. ``notify/cards``
assets live under ``pa/notify/assets/`` (decision.json template).
"""

import hashlib
import hmac
import json
import secrets

from sqlalchemy import text

from pa.common import new_id, now

OPTION_TEMPLATES = (
    "approve",
    "reject",
    "retry",
    "retry_other_engine",
    "take_over",
    "extend_budget",
    "snooze",
    "one_more_round",
)


def build_card_payload(
    *,
    summary: str,
    human_action: dict,
    evidence: list[str],
    link: str,
    decision_id: str,
    save_as_precedent: bool = True,
) -> dict:
    """Feishu interactive card JSON (structure per §12.3, D16/K9).

    The header carries the event summary; elements carry the single question,
    the recommended option with its reason (<=60 chars), at most 3 evidence
    items, the origin link, a console entry, and the ``记为先例`` checkbox.
    """
    options = [o for o in human_action.get("options", []) if o in OPTION_TEMPLATES]
    recommend = human_action.get("recommend") or ""
    why = human_action.get("why") or ""
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": human_action["question"]}},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"推荐：{recommend}（{why}）"},
        },
    ]
    for item in evidence[:3]:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"· {item}"}})
    if link:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "原处链接"},
                        "url": link,
                        "type": "default",
                    }
                ],
            }
        )
    actions = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": option},
            "type": "primary" if option == human_action.get("recommend") else "default",
            "value": {"decision_id": decision_id, "option": option},
        }
        for option in options
    ]
    if save_as_precedent:
        actions.append(
            {
                "tag": "check_box",
                "options": [
                    {
                        "text": {"tag": "plain_text", "content": "记为先例（我定的事）"},
                        "value": "decided",
                    },
                    {
                        "text": {"tag": "plain_text", "content": "记为先例（定了不做的事）"},
                        "value": "rejected",
                    },
                ],
            }
        )
    elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": summary[:80]}, "template": "blue"},
        "elements": elements,
    }


def create_decision(
    connection,
    *,
    task_id: str,
    kind: str,
    question: str,
    recommend: str,
    options: list[str],
    expires_at: str,
    reason_json: str | None = None,
) -> str:
    """Insert one pending decision row; nonce issued per card (anti-replay)."""
    decision_id = new_id()
    nonce = secrets.token_hex(16)
    connection.execute(
        text("""
        INSERT INTO decisions(id,task_id,kind,question,recommend,options_json,nonce,
          card_msg_id,status,reason_json,expires_at)
        VALUES(:id,:task,:kind,:question,:recommend,:options,:nonce,NULL,'pending',
          :reason,:expires_at)
    """),
        {
            "id": decision_id,
            "task": task_id,
            "kind": kind,
            "question": question,
            "recommend": recommend,
            "options": json.dumps(options, ensure_ascii=False),
            "nonce": nonce,
            "reason": reason_json,
            "expires_at": expires_at,
        },
    )
    return decision_id


def sign_callback(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def handle_callback(
    connection,
    *,
    body: bytes,
    signature: str,
    secret: str,
    owner_open_id: str,
    clicker_open_id: str,
    config,
    task_id: str | None = None,
) -> dict:
    """§12.3 callback: verify -> answer -> decision.submitted event.

    Steps: (1) signature check; (2) clicker ``open_id`` must be the owner;
    (3) nonce unused; (4) not expired. On success the card is updated in
    place (``card_msg_id`` payload), the decision flips to ``answered``, and
    a ``decision.submitted`` event re-enters ingress.
    """
    expected = sign_callback(secret, body)
    if not hmac.compare_digest(signature, expected):
        return {"ok": False, "reason": "bad_signature"}
    data = json.loads(body)
    decision_id = data.get("decision_id")
    option = data.get("option")
    save_as = data.get("save_as_precedent")
    row = connection.execute(
        text("""
        SELECT id,task_id,nonce,status,expires_at FROM decisions WHERE id=:id
    """),
        {"id": decision_id},
    ).one_or_none()
    if row is None:
        return {"ok": False, "reason": "unknown_decision"}
    if clicker_open_id != owner_open_id:
        return {"ok": False, "reason": "not_owner"}
    if row[3] != "pending":
        return {"ok": False, "reason": "duplicate_submission"}
    if row[4] and row[4] <= now():
        connection.execute(
            text("UPDATE decisions SET status='expired' WHERE id=:id"), {"id": decision_id}
        )
        return {"ok": False, "reason": "expired"}
    # Consume the nonce: rotate it so the same card cannot be clicked twice.
    connection.execute(
        text("""
        UPDATE decisions SET status='answered',answer=:answer,answered_by=:by,
          save_as_precedent=:save,nonce=:nonce WHERE id=:id
    """),
        {
            "answer": option,
            "by": f"feishu:{clicker_open_id}",
            "save": save_as,
            "nonce": secrets.token_hex(16),
            "id": decision_id,
        },
    )
    event_id = new_id()
    connection.execute(
        text("""
        INSERT INTO events(id,source,type,category,actor,principal,subject,dedup_key,
          trust,status,payload_json,received_at,summary,link,addressed)
        VALUES(:id,'feishu','decision.submitted','decision',:actor,'owner',:subject,
          :dedup,'verified','accepted',:payload,:received_at,:summary,NULL,1)
    """),
        {
            "id": event_id,
            "actor": f"feishu:{clicker_open_id}",
            "subject": task_id or row[1],
            "dedup": f"feishu:decision:{decision_id}:{option}",
            "payload": json.dumps(
                {"decision_id": decision_id, "option": option, "save_as_precedent": save_as},
                ensure_ascii=False,
            ),
            "received_at": now(),
            "summary": f"决定已提交：{option}",
        },
    )
    return {
        "ok": True,
        "decision_id": decision_id,
        "event_id": event_id,
        "card_update": {"decision_id": decision_id, "text": f"已处理：{option}"},
    }
