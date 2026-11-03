"""M2-14/15/16/17/18: identity guard, communication, decision cards,
precedents ledger, decision timeout."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.ci.communication import (
    AGENT_MARK,
    comment_body,
    duplicate_close_comment,
    pr_body,
    review_resolution,
)
from pa.judge.precedents import (
    add_precedent,
    apply_precedent,
    check_precedent,
    expired_precedents,
    export_md,
    parse_md,
)
from pa.judge.timeout import expire_due
from pa.notify.cards import build_card_payload, create_decision, handle_callback, sign_callback
from pa.scm.http import Client, Identity, ScmError
from pa.store.db import transaction


def _task(configured, dedup):
    from pa.ingress.manual import ManualAdapter

    return configured.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "x"},
                "dedup_key": dedup,
            }
        )
    )["tasks"][0]


# --- M2-14 identity separation ----------------------------------------------


def test_agent_identity_cannot_merge():
    client = Client(
        "https://api.example.test",
        {"agent": Identity("agent", "t-agent"), "merger": Identity("merger", "t-merger")},
    )

    def expect_blocked(coro):
        try:
            asyncio.run(coro)
        except ScmError as err:
            assert err.kind == "permanent"
            assert "D17" in str(err)
        else:
            raise AssertionError("agent merge was not blocked")

    expect_blocked(client.request("PUT", "/repos/o/r/pulls/7/merge", identity="agent"))
    expect_blocked(client.request("POST", "/repos/o/r/pulls/7/merge", identity="agent"))


def test_merger_identity_merge_reaches_transport():
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("identity")))
        return {"merged": True, "sha": "abc"}

    client = Client("https://api.example.test", {"merger": Identity("merger", "t-merger")})
    client.request = fake_request
    from pa.scm.github import GitHubAdapter

    adapter = GitHubAdapter.__new__(GitHubAdapter)
    adapter.client = client
    from pa.scm.base import PR

    result = asyncio.run(
        adapter.merge(
            "o/r",
            PR(number=7, head="h", base="main", title="t", body="b", state="open"),
            "squash",
        )
    )
    assert result.merged and calls == [("PUT", "/repos/o/r/pulls/7/merge", "merger")]


# --- M2-15 communication templates ------------------------------------------


def test_pr_body_first_line_is_agent_mark():
    body = pr_body(task_link="task-1", risk="medium", summary="改了三处")
    assert body.splitlines()[0] == AGENT_MARK
    assert "任务：task-1" in body and "风险自评：medium" in body


def test_comment_and_resolution_templates():
    assert comment_body("hello").splitlines()[0] == AGENT_MARK
    text_ = review_resolution(
        fixed_issues=[{"severity": "major", "message": "空指针"}],
        declined=[({"severity": "minor", "message": "命名"}, "遵循现有命名约定")],
    )
    assert "[已解决] 空指针" in text_
    assert "[不采纳] 命名：遵循现有命名约定" in text_
    close = duplicate_close_comment(owner_pr_url="https://x/pr/9")
    assert close.startswith(AGENT_MARK) and "https://x/pr/9" in close


# --- M2-16 decision cards ----------------------------------------------------


def _secret():
    return "s3cret"


def _callback_body(decision_id, option, save_as=None):
    payload = {"decision_id": decision_id, "option": option}
    if save_as:
        payload["save_as_precedent"] = save_as
    return json.dumps(payload).encode()


def test_card_payload_structure():
    payload = build_card_payload(
        summary="样例事件摘要",
        human_action={
            "question": "是否批准发布？",
            "options": ["approve", "reject"],
            "recommend": "approve",
            "why": "风险低",
        },
        evidence=["e1", "e2", "e3", "e4"],
        link="https://example/x",
        decision_id="d-1",
    )
    assert payload["header"]["title"]["content"] == "样例事件摘要"
    flat = json.dumps(payload, ensure_ascii=False)
    assert "是否批准发布？" in flat and "https://example/x" in flat
    assert "记为先例" in flat


def test_decision_callback_full_flow(configured):
    task = _task(configured, "dc-1")
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c,
            task_id=task,
            kind="merge_confirm",
            question="是否合并？",
            recommend="approve",
            options=["approve", "reject"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
    secret = _secret()
    body = _callback_body(decision_id, "approve", save_as="decided")
    signature = sign_callback(secret, body)
    with transaction(configured.engine) as c:
        result = handle_callback(
            c,
            body=body,
            signature=signature,
            secret=secret,
            owner_open_id="ou_owner",
            clicker_open_id="ou_owner",
            config=configured.config,
            task_id=task,
        )
    assert result["ok"], result
    with transaction(configured.engine) as c:
        row = (
            c.execute(
                text("SELECT status,answer,save_as_precedent FROM decisions WHERE id=:id"),
                {"id": decision_id},
            )
            .mappings()
            .one()
        )
        event = (
            c.execute(
                text("SELECT type,category,status FROM events WHERE id=:eid"),
                {"eid": result["event_id"]},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "answered" and row["answer"] == "approve"
    assert row["save_as_precedent"] == "decided"
    assert event["type"] == "decision.submitted" and event["category"] == "decision"
    assert event["status"] == "accepted"


def test_decision_callback_rejects(configured):
    task = _task(configured, "dc-2")
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c,
            task_id=task,
            kind="merge_confirm",
            question="是否合并？",
            recommend="approve",
            options=["approve", "reject"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
    secret = _secret()
    body = _callback_body(decision_id, "approve")
    # wrong signature
    with transaction(configured.engine) as c:
        bad = handle_callback(
            c,
            body=body,
            signature="deadbeef",
            secret=secret,
            owner_open_id="ou_owner",
            clicker_open_id="ou_owner",
            config=configured.config,
            task_id=task,
        )
    assert bad == {"ok": False, "reason": "bad_signature"}
    # not owner
    signature = sign_callback(secret, body)
    with transaction(configured.engine) as c:
        stranger = handle_callback(
            c,
            body=body,
            signature=signature,
            secret=secret,
            owner_open_id="ou_owner",
            clicker_open_id="ou_stranger",
            config=configured.config,
            task_id=task,
        )
    assert stranger["ok"] is False and stranger["reason"] == "not_owner"
    # replay: same card clicked twice
    with transaction(configured.engine) as c:
        first = handle_callback(
            c,
            body=body,
            signature=signature,
            secret=secret,
            owner_open_id="ou_owner",
            clicker_open_id="ou_owner",
            config=configured.config,
            task_id=task,
        )
    assert first["ok"]
    with transaction(configured.engine) as c:
        replay = handle_callback(
            c,
            body=body,
            signature=signature,
            secret=secret,
            owner_open_id="ou_owner",
            clicker_open_id="ou_owner",
            config=configured.config,
            task_id=task,
        )
    assert replay["ok"] is False and replay["reason"] == "duplicate_submission"
    # expired
    with transaction(configured.engine) as c:
        expired_id = create_decision(
            c,
            task_id=task,
            kind="k",
            question="过期？",
            recommend="approve",
            options=["approve"],
            expires_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )
    body2 = _callback_body(expired_id, "approve")
    sig2 = sign_callback(secret, body2)
    with transaction(configured.engine) as c:
        late = handle_callback(
            c,
            body=body2,
            signature=sig2,
            secret=secret,
            owner_open_id="ou_owner",
            clicker_open_id="ou_owner",
            config=configured.config,
            task_id=task,
        )
    assert late["ok"] is False and late["reason"] == "expired"
    with transaction(configured.engine) as c:
        status = c.execute(
            text("SELECT status FROM decisions WHERE id=:id"), {"id": expired_id}
        ).scalar_one()
    assert status == "expired"


# --- M2-17 precedents ledger -------------------------------------------------


def _add_precedent(c, title, content, scope="global", kind="precedent_decided", **kw):
    return add_precedent(c, kind=kind, title=title, content=content, scope=scope, **kw)


def test_precedent_add_search_and_apply(configured):
    task = _task(configured, "pc-1")
    with transaction(configured.engine) as c:
        pid = _add_precedent(c, "周末发布审批", "周五晚上的发布一律不批，等周一", scope="prod")
        hit = check_precedent(c, question="周五晚上要不要做发布？", scope="prod")
    assert hit["status"] == "hit" and hit["hit"].precedent.id == pid
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c,
            task_id=task,
            kind="release",
            question="周五晚上要不要做发布？",
            recommend="approve",
            options=["approve", "reject"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
        apply_precedent(c, hit=hit["hit"], decision_id=decision_id)
        row = (
            c.execute(
                text("SELECT status,answer,precedent_id,reason_json FROM decisions WHERE id=:id"),
                {"id": decision_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "precedent" and row["answer"] == "approve"
    assert row["precedent_id"] == pid and "precedent_applied" in row["reason_json"]


def test_precedent_scope_and_never_auto(configured):
    _task(configured, "pc-2")
    with transaction(configured.engine) as c:
        _add_precedent(c, "删除测试数据", "可以删除 tests/ 下的临时数据", scope="sandbox")
        mismatch = check_precedent(c, question="可以删除 tests/ 下的临时数据吗？", scope="prod")
        never = check_precedent(c, question="可以 delete_data 吗？", scope="prod")
    assert mismatch["status"] == "miss"
    assert never["status"] == "never_auto" and never["tag"] == "delete_data"


def test_precedent_rejected_answers_reject(configured):
    task = _task(configured, "pc-3")
    with transaction(configured.engine) as c:
        pid = _add_precedent(c, "引入新框架", "不再引入新的前端框架", kind="precedent_rejected")
        hit = check_precedent(c, question="要不要引入新的前端框架？")
    assert hit["status"] == "hit"
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c,
            task_id=task,
            kind="tech_choice",
            question="要不要引入新的前端框架？",
            recommend="reject",
            options=["approve", "reject"],
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
        apply_precedent(c, hit=hit["hit"], decision_id=decision_id)
        answer = c.execute(
            text("SELECT answer FROM decisions WHERE id=:id"), {"id": decision_id}
        ).scalar_one()
    assert answer == "reject" and pid


def test_precedent_md_mirror_roundtrip(configured):
    _task(configured, "pc-4")
    with transaction(configured.engine) as c:
        _add_precedent(c, "文档改动自动合并", "docs-only 且 <50 行直接合", scope="sandbox")
        _add_precedent(
            c,
            "夜里的重构",
            "夜里不动架构",
            kind="precedent_rejected",
            expires_at="2027-01-01T00:00:00+00:00",
        )
    with transaction(configured.engine) as c:
        exported = export_md(c)
    decided = parse_md(exported["decided"])
    rejected = parse_md(exported["rejected"])
    assert any("文档改动自动合并" in r["title"] for r in decided)
    assert any("夜里的重构" in r["title"] for r in rejected)
    assert all("记为先例" not in line for line in exported["decided"].splitlines())


def test_precedent_expiry_reminder(configured):
    _task(configured, "pc-5")
    with transaction(configured.engine) as c:
        _add_precedent(c, "短期规则", "只在十一月有效", expires_at="2026-10-01T00:00:00+00:00")
        stale = expired_precedents(c)
    assert any(p.title == "短期规则" for p in stale)


# --- M2-18 decision timeout --------------------------------------------------


def test_decision_timeout_suspends_and_reminds_once(configured):
    task = _task(configured, "to-1")
    past = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    with transaction(configured.engine) as c:
        decision_id = create_decision(
            c,
            task_id=task,
            kind="merge_confirm",
            question="是否合并？",
            recommend="approve",
            options=["approve", "reject"],
            expires_at=past,
        )
    with transaction(configured.engine) as c:
        expired = expire_due(c, config=configured.config)
    assert [e["decision_id"] for e in expired] == [decision_id]
    assert expired[0]["task_suspended"] is True
    with transaction(configured.engine) as c:
        task_status = c.execute(
            text("SELECT status FROM tasks WHERE id=:id"), {"id": task}
        ).scalar_one()
        decision_status = c.execute(
            text("SELECT status FROM decisions WHERE id=:id"), {"id": decision_id}
        ).scalar_one()
        reminded = c.execute(
            text("SELECT value FROM system_state WHERE key=:k"),
            {"k": f"decision_timeout_reminded:{decision_id}"},
        ).scalar_one_or_none()
        notified = c.execute(
            text("SELECT COUNT(*) FROM effects WHERE kind='feishu.send'")
        ).scalar_one()
    assert task_status == "suspended"
    assert decision_status == "expired"
    assert reminded is not None
    assert notified >= 1
    # second pass: no duplicate reminder, no re-expire
    with transaction(configured.engine) as c:
        again = expire_due(c, config=configured.config)
    assert again == []
    with transaction(configured.engine) as c:
        notified_again = c.execute(
            text("SELECT COUNT(*) FROM effects WHERE kind='feishu.send'")
        ).scalar_one()
    assert notified_again == notified


def test_decision_timeout_skips_terminal_tasks(configured):
    task = _task(configured, "to-2")
    past = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    with transaction(configured.engine) as c:
        create_decision(
            c,
            task_id=task,
            kind="merge_confirm",
            question="关了吗？",
            recommend="approve",
            options=["approve"],
            expires_at=past,
        )
        c.execute(text("UPDATE tasks SET status='closed' WHERE id=:id"), {"id": task})
    with transaction(configured.engine) as c:
        expired = expire_due(c, config=configured.config)
    assert expired and expired[0]["task_suspended"] is False
    with transaction(configured.engine) as c:
        status = c.execute(text("SELECT status FROM tasks WHERE id=:id"), {"id": task}).scalar_one()
    assert status == "closed"
