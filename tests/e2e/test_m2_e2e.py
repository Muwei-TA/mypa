"""Isolated M2 E2E paths (M2-21).

E2E-05/07/09/10 need a real GitHub sandbox repo, branch protection and
launchd; they stay deferred for on-device verification (per owner
instruction). What the local harness can still drive end-to-end:

* E2E-06: blocker review -> fix rounds -> awaiting_decision card options.
* E2E-08: precedent checkbox -> second identical question skips the card
  and records the ``precedent_applied`` reason code.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.ci.pipeline import decision_options_for_pr, fix_round
from pa.ci.review import record_review, review_verdict
from pa.judge.precedents import apply_precedent, check_precedent
from pa.notify.cards import (
    build_card_payload,
    create_decision,
    handle_callback,
    sign_callback,
)
from pa.store.db import transaction

SECRET = "test-secret"


def _task(configured, dedup):
    from pa.ingress.manual import ManualAdapter

    return configured.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "e2e"},
                "dedup_key": dedup,
            }
        )
    )["tasks"][0]


def _open_pr(c, task, number):
    c.execute(
        text("""
        INSERT INTO pull_requests(id,task_id,scm,repo,number,url,head,base,ci_stage,fix_round)
        VALUES(:id,:task,'github','sandbox/repo',:number,:url,'pa/'||:task,'main','opened',0)
    """),
        {"id": f"pr-{number}", "task": task, "number": number, "url": f"https://x/pr/{number}"},
    )
    return f"pr-{number}"


# --- E2E-06 ------------------------------------------------------------------


def test_e2e_06_blocker_fix_rounds_escalate_to_decision_card(configured):
    """A PR whose review keeps reporting blocker goes through fix rounds,
    lands in awaiting_decision, and the card offers the three options."""
    task = _task(configured, "e2e06")
    with transaction(configured.engine) as c:
        pr_id = _open_pr(c, task, 42)
    blocker = review_verdict(
        [
            {"severity": "blocker", "title": "空指针解引用"},
            {"severity": "major", "title": "未处理异常"},
        ]
    )
    assert blocker["verdict"] == "request_changes"
    # three rounds of failed review
    for round_ in (1, 2, 3):
        with transaction(configured.engine) as c:
            record_review(
                c, pr_id=pr_id, round_=round_, engine_id="reviewer", model="rv",
                result=blocker, tokens=100, task_tokens=1000,
            )
            outcome = fix_round(c, task)
    assert outcome["escalated"] is True and outcome["fix_round"] == 3
    options = decision_options_for_pr()
    assert [o["id"] for o in options] == ["one_more_round", "take_over", "close_pr"]
    # the card for the escalation decision
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c, task_id=task, kind="fix_round_exhausted",
            question="审查 3 轮仍有 blocker，如何处理？",
            recommend="one_more_round",
            options=["one_more_round", "take_over", "close_pr"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
        task_status = c.execute(
            text("SELECT status FROM tasks WHERE id=:id"), {"id": task}
        ).scalar_one()
    assert task_status == "awaiting_decision"
    # owner clicks one_more_round on the card
    body = json.dumps({"decision_id": decision_id, "option": "one_more_round"}).encode()
    with transaction(configured.engine) as c:
        clicked = handle_callback(
            c, body=body, signature=sign_callback(SECRET, body), secret=SECRET,
            owner_open_id="ou_owner", clicker_open_id="ou_owner",
            config=configured.config, task_id=task,
        )
    assert clicked["ok"] and clicked["card_update"]["text"] == "已处理：one_more_round"


# --- E2E-08 ------------------------------------------------------------------


def test_e2e_08_precedent_checkbox_suppresses_second_card(configured):
    """First card answered with 记为先例 -> identical question resolves via
  precedent on the second pass; no card, reason code precedent_applied."""
    task = _task(configured, "e2e08")
    question = "周五晚上要不要做线上发布？"
    # first time: no precedent, judge pushes a card with the checkbox
    payload = build_card_payload(
        summary="发布确认",
        human_action={
            "question": question,
            "options": ["approve", "reject"],
            "recommend": "reject",
            "why": "周五晚发布风险高",
        },
        evidence=["变更含数据库迁移"],
        link="https://x/pr/7",
        decision_id="pending",
    )
    assert "记为先例" in json.dumps(payload, ensure_ascii=False)
    with transaction(configured.engine) as c:
        first = check_precedent(c, question=question, scope="prod")
    assert first["status"] == "miss"
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c, task_id=task, kind="release_confirm", question=question,
            recommend="reject", options=["approve", "reject"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
    # owner clicks reject and checks 记为先例（定了不做的事）
    body = json.dumps(
        {"decision_id": decision_id, "option": "reject", "save_as_precedent": "rejected"}
    ).encode()
    with transaction(configured.engine) as c:
        clicked = handle_callback(
            c, body=body, signature=sign_callback(SECRET, body), secret=SECRET,
            owner_open_id="ou_owner", clicker_open_id="ou_owner",
            config=configured.config, task_id=task,
        )
    assert clicked["ok"]
    # the save_as_precedent choice lands in the ledger
    with transaction(configured.engine) as c:
        saved = c.execute(
            text("SELECT save_as_precedent FROM decisions WHERE id=:id"),
            {"id": decision_id},
        ).scalar_one()
    assert saved == "rejected"
    # judge writes the precedent from the checkbox choice
    with transaction(configured.engine) as c:
        from pa.judge.precedents import add_precedent

        add_precedent(
            c, kind="precedent_rejected", scope="prod",
            title="周五晚上的发布", content="周五晚上不做线上发布",
            decision_id=decision_id,
        )
    # second identical question: no card, precedent applied
    task2 = _task(configured, "e2e08b")
    with transaction(configured.engine) as c:
        second = check_precedent(c, question=question, scope="prod")
    assert second["status"] == "hit"
    with transaction(configured.engine) as c:
        decision2 = create_decision(
            c, task_id=task2, kind="release_confirm", question=question,
            recommend="reject", options=["approve", "reject"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
        apply_precedent(c, hit=second["hit"], decision_id=decision2)
        row = c.execute(
            text("SELECT status,answer,reason_json FROM decisions WHERE id=:id"),
            {"id": decision2},
        ).mappings().one()
    assert row["status"] == "precedent" and row["answer"] == "reject"
    reasons = json.loads(row["reason_json"])
    assert reasons["code"] == "precedent_applied"
