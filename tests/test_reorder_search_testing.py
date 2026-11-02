"""M3-08/10/11: reorder endpoints, rule/skill dry-run tests, global search."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from pa.api.app import create_app
from pa.common import PAError
from pa.config.setup import save_files
from pa.console.search import search
from pa.console.testing import dry_run_rules, dry_run_skill
from pa.ingress.base import Event
from pa.store.db import transaction


def _client(facade, **kwargs):
    return TestClient(create_app(facade, api_token="owner-token", **kwargs))


def _add_rules(config_dir, configured):
    """Extend rules.yaml with two extra rules for ordering tests."""
    rules = yaml.safe_load((config_dir / "rules.yaml").read_text())
    rules["rules"] = [
        {"id": "r-a", "rank": 1, "match": {"source": "github"}, "skill": "sample-code"},
        {"id": "r-b", "rank": 2, "match": {"source": "gitea"}, "skill": "sample-code"},
        {"id": "r-c", "rank": 3, "match": {"source": "manual"}, "skill": "sample-direct"},
    ]
    save_files(config_dir, {"rules.yaml": rules})
    configured.config = None


def test_reorder_rules_writes_yaml_and_audits(configured, config_dir):
    _add_rules(config_dir, configured)
    out = configured.reorder_rules(["r-c", "r-a", "r-b"])
    assert out["changed"] is True
    persisted = yaml.safe_load((config_dir / "rules.yaml").read_text())
    assert [r["id"] for r in persisted["rules"]] == ["r-c", "r-a", "r-b"]

    same = configured.reorder_rules(["r-c", "r-a", "r-b"])
    assert same["changed"] is False

    with pytest.raises(PAError):
        configured.reorder_rules(["r-a"])  # incomplete list
    with pytest.raises(PAError):
        configured.reorder_rules(["r-a", "r-a", "r-b", "r-c"])  # duplicate

    audit = configured.list("audit_log")["items"]
    assert any(a["action"] == "rule.reorder" for a in audit)


def test_reorder_rules_endpoint_owner_only(configured, config_dir):
    _add_rules(config_dir, configured)
    client = _client(configured, agent_token="agent-token")
    ok = client.post(
        "/api/v1/rules:reorder",
        json={"order": ["r-c", "r-b", "r-a"]},
        headers={"Authorization": "Bearer owner-token"},
    )
    assert ok.status_code == 200
    persisted = yaml.safe_load((config_dir / "rules.yaml").read_text())
    assert [r["id"] for r in persisted["rules"]] == ["r-c", "r-b", "r-a"]

    denied = client.post(
        "/api/v1/rules:reorder",
        json={"order": ["r-a", "r-b", "r-c"]},
        headers={"Authorization": "Bearer agent-token"},
    )
    assert denied.status_code == 403


def test_rules_test_reports_matches_without_execution(configured, config_dir):
    _add_rules(config_dir, configured)
    out = dry_run_rules(config_dir, {"source": "gitea", "type": "issue.opened", "payload": {}})
    assert out["matched"][0]["rule_id"] == "r-b"
    assert out["matched"][0]["skill"] == "sample-code"
    assert out["matched"][0]["would"] in ("create_task", "skip")
    assert out["policy_version"].startswith("p-")
    # nothing written to the store
    with configured.engine.connect() as c:
        count = c.exec_driver_sql("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 0


def test_rules_test_endpoint(configured, config_dir):
    _add_rules(config_dir, configured)
    client = _client(configured)
    response = client.post(
        "/api/v1/rules:test",
        json={"event": {"source": "github", "type": "pr.opened"}},
        headers={"Authorization": "Bearer owner-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"][0]["rule_id"] == "r-a"
    # agent tokens may also call the read-only test surface
    agent = _client(configured, agent_token="agent-token")
    ok = agent.post(
        "/api/v1/rules:test",
        json={"event": {"source": "github"}},
        headers={"Authorization": "Bearer agent-token"},
    )
    assert ok.status_code == 200


def test_skill_test_reports_routing_rules(configured, config_dir):
    _add_rules(config_dir, configured)
    out = dry_run_skill(config_dir, "sample-code", {"source": "github"})
    assert out["found"] is True
    rule_ids = {r["rule_id"] for r in out["rules"]}
    assert rule_ids == {"r-a", "r-b"}
    github_rule = next(r for r in out["rules"] if r["rule_id"] == "r-a")
    assert github_rule["matches_sample"] is True

    missing = dry_run_skill(config_dir, "no-such-skill")
    assert missing["found"] is False


def test_search_by_text_id_ref_and_url(configured):
    from datetime import UTC, datetime

    event = Event(
        source="manual",
        type="manual.test",
        category="new_source",
        actor="owner",
        subject="repo#12: fix login",
        payload={"skill": "sample-direct", "url": "https://github.com/o/r/issues/12"},
        dedup_key="search-1",
        received_at=datetime.now(UTC),
        summary="修复登录问题 repo#12",
        trust="verified",
    )
    result = configured.emit(event)
    assert result["tasks"], "expected the manual rule to create a task"
    task_id = result["tasks"][0]

    with configured.engine.connect() as c:
        by_text = search(c, "登录")
        assert any(i["kind"] == "task" and i["id"] == task_id for i in by_text["items"])

        by_ref = search(c, "#12")
        assert any(i["kind"] == "event" for i in by_ref["items"])

        by_url = search(c, "https://github.com/o/r/issues/12")
        assert by_url["items"] and by_url["items"][0]["matched_on"] == "url"

        by_prefix = search(c, task_id[:8])
        assert any(i["id"] == task_id for i in by_prefix["items"])

        empty = search(c, "")
        assert empty == {"query": "", "items": []}


def test_search_endpoint(configured):
    from datetime import UTC, datetime

    configured.emit(
        Event(
            source="manual",
            type="manual.test",
            category="new_source",
            payload={},
            dedup_key="search-2",
            received_at=datetime.now(UTC),
            summary="全局搜索样例事件",
            trust="verified",
        )
    )
    client = _client(configured)
    response = client.get(
        "/api/v1/search",
        params={"q": "全局搜索"},
        headers={"Authorization": "Bearer owner-token"},
    )
    assert response.status_code == 200
    assert response.json()["items"]

    unauthorized = client.get("/api/v1/search", params={"q": "x"})
    assert unauthorized.status_code == 401


def test_search_thread_ref(configured):
    from datetime import UTC, datetime

    event = Event(
        source="manual",
        type="manual.test",
        category="new_source",
        actor="owner",
        subject="repo#34",
        payload={"skill": "sample-direct"},
        dedup_key="search-3",
        received_at=datetime.now(UTC),
        summary="线程订阅样例",
        trust="verified",
    )
    result = configured.emit(event)
    task_id = result["tasks"][0]
    with transaction(configured.engine) as c:
        c.exec_driver_sql(
            "INSERT INTO subscriptions(id,task_id,scm_or_source,thread_ref,own,"
            "context_only,created_at) VALUES('sub-1',?, 'github','github:o/r#34',1,0,"
            "'2026-01-01T00:00:00+00:00')",
            (task_id,),
        )
    with configured.engine.connect() as c:
        found = search(c, "github:o/r#34")
    assert any(i["kind"] == "thread" for i in found["items"])


def test_rule_test_cli_json_argument(configured, config_dir, tmp_path):
    from pa.console.testing import dry_run_rules as run_test

    _add_rules(config_dir, configured)
    inline = run_test(config_dir, json.loads('{"source": "github"}'))
    assert inline["matched"][0]["rule_id"] == "r-a"

    sample = tmp_path / "sample.json"
    sample.write_text('{"source": "gitea"}')
    from pa.cli.main import _load_json_argument

    payload = _load_json_argument(f"@{sample}")
    assert run_test(config_dir, payload)["matched"][0]["rule_id"] == "r-b"
