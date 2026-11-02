"""M3-01 role facades, M3-05 task_stage view, M3-06 reason translations."""

import json
from datetime import UTC, datetime

from sqlalchemy import text

from pa.console.reasons import stage_label, translate, translate_reasons
from pa.store.db import transaction
from pa.store.roles import AGENT_FORBIDDEN_ATTRS, AgentFacade, facade_for


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


def _stage(configured, task_id):
    with configured.engine.connect() as c:
        return c.execute(
            text("SELECT stage FROM task_stage WHERE id=:id"), {"id": task_id}
        ).scalar_one()


# --- M3-05 task_stage --------------------------------------------------------


def test_stage_working_queued(configured):
    task = _task(configured, "st-1")
    assert _stage(configured, task) == "working/queued"


def test_stage_owner_answer(configured):
    task = _task(configured, "st-2")
    with transaction(configured.engine) as c:
        c.execute(
            text("""
            INSERT INTO decisions(id,task_id,kind,question,nonce,status)
            VALUES(:id,:task,'task','要继续吗？','n1','pending')
        """),
            {"id": "d-st2", "task": task},
        )
    assert _stage(configured, task) == "owner/answer"


def test_stage_owner_review(configured):
    task = _task(configured, "st-3")
    with transaction(configured.engine) as c:
        c.execute(
            text("UPDATE tasks SET status='succeeded' WHERE id=:id"), {"id": task}
        )
        c.execute(
            text("""
            INSERT INTO pull_requests(id,task_id,scm,repo,number,url,ci_stage)
            VALUES(:id,:task,'github','o/r',1,'u','merge_gate')
        """),
            {"id": "pr-st3", "task": task},
        )
    assert _stage(configured, task) == "owner/review"


def test_stage_owner_blocked_failed(configured):
    task = _task(configured, "st-4")
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE tasks SET status='failed' WHERE id=:id"), {"id": task})
    assert _stage(configured, task) == "owner/blocked_failed"


def test_stage_owner_delivery(configured):
    task = _task(configured, "st-5")
    with transaction(configured.engine) as c:
        c.execute(
            text("""
            INSERT INTO effects(id,task_id,kind,idem_key,status,attempts)
            VALUES(:id,:task,'feishu.send','st5:1','failed',3)
        """),
            {"id": "ef-st5", "task": task},
        )
    assert _stage(configured, task) == "owner/delivery"


def test_stage_idle_with_open_subscription(configured):
    task = _task(configured, "st-6")
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE tasks SET status='closed' WHERE id=:id"), {"id": task})
        c.execute(
            text("""
            INSERT INTO subscriptions(id,task_id,scm_or_source,thread_ref,own,created_at)
            VALUES(:id,:task,'github','thread-st6',1,:at)
        """),
            {"id": "sub-st6", "task": task, "at": datetime.now(UTC).isoformat()},
        )
    assert _stage(configured, task) == "idle"


def test_stage_working_else(configured):
    task = _task(configured, "st-7")
    with transaction(configured.engine) as c:
        c.execute(text("UPDATE tasks SET status='running' WHERE id=:id"), {"id": task})
    assert _stage(configured, task) == "working"


def test_stage_priority_delivery_beats_answer(configured):
    """Effects failure outranks a pending decision (first match wins)."""
    task = _task(configured, "st-8")
    with transaction(configured.engine) as c:
        c.execute(
            text("""
            INSERT INTO effects(id,task_id,kind,idem_key,status,attempts)
            VALUES(:id,:task,'feishu.send','st8:1','failed',3)
        """),
            {"id": "ef-st8", "task": task},
        )
        c.execute(
            text("""
            INSERT INTO decisions(id,task_id,kind,question,nonce,status)
            VALUES(:id,:task,'task','x?','n8','pending')
        """),
            {"id": "d-st8", "task": task},
        )
    assert _stage(configured, task) == "owner/delivery"


# --- M3-06 reason translations -----------------------------------------------


def test_translate_known_codes():
    assert translate("claimed") == "任务已被引擎领取"
    assert translate("retry_exhausted") == "自动重试已达上限"
    assert translate("precedent_applied") == "已按先例自动处理"
    assert "轮数" in translate("task_turn_limit")


def test_translate_unknown_falls_back():
    assert translate("brand_new_code") == "brand_new_code"


def test_translate_reasons_array_and_object():
    array = translate_reasons(json.dumps([{"code": "claimed"}, {"code": "retry"}]))
    assert [item["text"] for item in array] == ["任务已被引擎领取", "执行失败，自动重派"]
    single = translate_reasons(json.dumps({"code": "precedent_applied", "values": {"k": 1}}))
    assert single[0]["text"] == "已按先例自动处理"
    assert single[0]["values"] == {"k": 1}
    assert translate_reasons(None) == []


def test_stage_labels_cover_seven_buckets():
    labels = {
        stage_label("owner/answer"),
        stage_label("owner/review"),
        stage_label("owner/blocked_failed"),
        stage_label("owner/delivery"),
        stage_label("working/queued"),
        stage_label("working"),
        stage_label("idle"),
    }
    expected = {
        "等你决定",
        "等你审查",
        "受阻 / 失败",
        "投递失败",
        "排队中",
        "执行中",
        "已结束，可续接",
    }
    assert labels == expected


# --- M3-01 role facades ------------------------------------------------------


def test_agent_facade_has_no_forbidden_writes():
    forbidden_found = AGENT_FORBIDDEN_ATTRS & set(dir(AgentFacade))
    assert forbidden_found == set()
    agent = AgentFacade(object())
    for attr in AGENT_FORBIDDEN_ATTRS:
        assert not hasattr(agent, attr), f"agent facade exposes {attr}"


def test_facade_for_roles(configured):
    owner = configured
    assert facade_for("owner", owner) is owner
    agent = facade_for("agent", owner)
    assert isinstance(agent, AgentFacade)
    assert hasattr(agent, "list_tasks")
    assert not hasattr(agent, "cancel")
    executor = facade_for("executor", owner)
    assert not hasattr(executor, "emit")
    hub = facade_for("hub", owner)
    assert hasattr(hub, "emit") and not hasattr(hub, "cancel")
    settings = facade_for("settings", owner)
    assert hasattr(settings, "set_rule_mode") and not hasattr(settings, "cancel")
    try:
        facade_for("intruder", owner)
    except ValueError as err:
        assert "unknown token role" in str(err)
    else:
        raise AssertionError("unknown role accepted")


def test_agent_facade_reads_and_reorder(configured):
    _task(configured, "role-1")
    agent = facade_for("agent", configured)
    tasks = agent.list_tasks(limit=10)
    assert tasks["items"]
    shown = agent.show("tasks", tasks["items"][0]["id"])
    assert shown["id"] == tasks["items"][0]["id"]
    queue_move = agent.move_task(tasks["items"][0]["id"], to_top=True)
    assert queue_move["task_id"] == tasks["items"][0]["id"]


def test_agent_cannot_see_secrets_budget_precedents_writes(configured):
    """The five sensitive write groups are structurally unreachable (D21)."""
    agent = AgentFacade(configured)
    for attr in (
        "set_rule_mode",
        "reorder_skills",
        "effect_retry",
        "effect_drop",
        "pause",
        "resume",
        "cancel",
    ):
        assert not hasattr(agent, attr)
    # secrets / budgets / merge policy / precedents are not even readable via list()
    try:
        agent.list("budgets")
    except Exception as err:
        assert "not exposed" in str(err)
    else:
        raise AssertionError("budgets table exposed to agent")
