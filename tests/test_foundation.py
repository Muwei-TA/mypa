import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner

from pa.cli.main import app
from pa.common import PAError, new_id
from pa.config.loader import initialize, load
from pa.config.schema import NEVER_AUTO, PAConfig, Rule, RulesConfig
from pa.effects.store import enqueue
from pa.hub.pause import set_value
from pa.ingress.cron import CronAdapter
from pa.ingress.manual import ManualAdapter
from pa.obs.redact import Redactor
from pa.plugins import GROUPS, discover
from pa.secrets.resolver import check_env_permissions, resolve
from pa.store.db import migrate, transaction
from pa.store.facades import OwnerFacade
from pa.store.models import Reason, metadata
from pa.store.transitions import TRANSITIONS, transition


def event(**overrides):
    e = ManualAdapter().parse({"type": "manual.test", "payload": {"skill": "sample-direct"}})
    return e.model_copy(update=overrides)


def test_cli_workflow(tmp_path):
    runner = CliRunner()
    prefix = ["--config-dir", str(tmp_path / "config")]
    assert runner.invoke(app, ["--help"]).exit_code == 0
    for command in [
        ["init", "--json"],
        ["migrate", "--json"],
        ["doctor", "--json"],
        ["config", "validate", "--json"],
        ["config", "get", "pa.worker.slots", "--json"],
        ["event", "emit", "manual.test", "--payload", '{"skill":"sample-direct"}', "--json"],
        ["task", "list", "--json"],
        ["pause", "--json"],
        ["status", "--json"],
        ["resume", "--json"],
        ["observe", "list", "--json"],
        ["effect", "list", "--json"],
    ]:
        result = runner.invoke(app, prefix + command)
        assert result.exit_code == 0, result.output
        assert isinstance(json.loads(result.stdout), dict)
    result = runner.invoke(app, prefix + ["init", "--json"])
    assert result.exit_code == 2
    assert "conflict" in result.output
    result = runner.invoke(app, prefix + ["task", "show", "missing", "--json"])
    assert result.exit_code == 4
    result = runner.invoke(app, prefix + ["event", "emit", "manual.test", "--payload", "[]"])
    assert result.exit_code == 2


def test_config_errors_and_lazy_secrets(config_dir, monkeypatch):
    path = config_dir / "pa.yaml"
    original = yaml.safe_load(path.read_text())
    for patch, expected in [
        ({"typo": 1}, "typo"),
        ({"worker": {"slots": "3"}}, "worker.slots"),
        (
            {"console": {"auth": {"password_hash": "plaintext", "totp": "secret://env/TOTP"}}},
            "console.auth.password_hash",
        ),
        (
            {"human_gate": {"never_auto_by_precedent": list(NEVER_AUTO)[:-1]}},
            "human_gate.never_auto_by_precedent",
        ),
        ({"limits": {"max_retry": 99}}, "limits.max_retry"),
        ({"server": {"public_paths": ["/api/v1/tasks"]}}, "server.public_paths"),
    ]:
        path.write_text(yaml.safe_dump({**original, **patch}))
        with pytest.raises(PAError, match=expected):
            load(config_dir)
    original["console"] = {
        "auth": {
            "password_hash": "secret://env/NOT_SET",
            "totp": "secret://keychain/pa/totp",
        }
    }
    path.write_text(yaml.safe_dump(original))
    assert load(config_dir).main.console.auth.password_hash == "secret://env/NOT_SET"
    path.write_text("instance: test\ndata_dir: ${PA_TEST_DIR}\n")
    monkeypatch.setenv("PA_TEST_DIR", "../data")
    assert load(config_dir).main.data_dir == "../data"
    monkeypatch.delenv("PA_TEST_DIR")
    with pytest.raises(PAError, match="missing environment"):
        load(config_dir)


def test_config_yaml_duplicate_and_off(config_dir):
    path = config_dir / "rules.yaml"
    path.write_text("rules:\n- id: a\n  rank: 1\n  match: {}\n  skill: x\n  accept_mode: off\n")
    assert load(config_dir).rules.rules[0].accept_mode == "off"
    path.write_text("rules: []\nrules: []\n")
    with pytest.raises(PAError, match="duplicate YAML key"):
        load(config_dir)


def test_config_version_ignores_yaml_format(config_dir):
    first = load(config_dir).policy_version
    path = config_dir / "pa.yaml"
    path.write_text("# comment\n" + path.read_text() + "\n")
    assert load(config_dir).policy_version == first
    path = config_dir / "rules.yaml"
    data = yaml.safe_load(path.read_text())
    data["rules"][0]["accept_mode"] = "auto"
    path.write_text(yaml.safe_dump(data))
    assert load(config_dir).policy_version != first


def test_init_preserves_existing(config_dir):
    existing = (config_dir / "pa.yaml").read_bytes()
    with pytest.raises(PAError):
        initialize(config_dir)
    assert (config_dir / "pa.yaml").read_bytes() == existing


def test_schema_and_pragmas(facade):
    migrate(facade.engine)
    assert {"tasks", "events", "effects", "audit_log", "memories", "memories_fts"} <= set(
        inspect(facade.engine).get_table_names()
    )
    assert "tasks" in metadata(facade.engine).tables
    with facade.engine.connect() as c:
        for name, expected in [
            ("journal_mode", "wal"),
            ("synchronous", 1),
            ("busy_timeout", 5000),
            ("foreign_keys", 1),
        ]:
            assert c.exec_driver_sql(f"PRAGMA {name}").scalar_one() == expected
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []


@pytest.mark.parametrize("source", list(TRANSITIONS))
@pytest.mark.parametrize("target", list(TRANSITIONS))
def test_all_state_transitions(facade, source, target):
    id_ = new_id()
    with transaction(facade.engine) as c:
        c.execute(
            text("INSERT INTO tasks(id,status,priority) VALUES(:id,:status,'P2')"),
            {
                "id": id_,
                "status": source,
            },
        )
    legal = target in (TRANSITIONS[source] | {"cancelled"}) and target != source
    with transaction(facade.engine) as c:
        if legal:
            result = transition(c, id_, target, 0, [Reason(code="test")])
            assert result["version"] == 1
            assert c.execute(text("SELECT count(*) FROM task_events")).scalar_one() == 1
        else:
            with pytest.raises(PAError, match="illegal transition"):
                transition(c, id_, target, 0, [Reason(code="test")])
            assert c.execute(text("SELECT count(*) FROM task_events")).scalar_one() == 0


def test_transition_conflict_and_atomic_effect(facade):
    task_id = facade.emit(event())["tasks"][0]
    with pytest.raises(RuntimeError), transaction(facade.engine) as c:
        transition(c, task_id, "leased", 0, [Reason(code="test")])
        enqueue(c, task_id=task_id, kind="test", idem_key="atomic", payload={})
        raise RuntimeError("crash")
    assert facade.show("tasks", task_id)["version"] == 0
    assert facade.list("effects")["items"] == []
    with transaction(facade.engine) as c:
        transition(c, task_id, "leased", 0, [Reason(code="test")])
        first = enqueue(c, kind="test", idem_key="atomic", payload={})
        assert enqueue(c, kind="test", idem_key="atomic", payload={}) == first
    with transaction(facade.engine) as c:
        with pytest.raises(PAError, match="version conflict"):
            transition(c, task_id, "running", 0, [Reason(code="test")])
    assert len(facade.show("tasks", task_id)["timeline"]) == 1


def test_mandate_and_json_guard(facade):
    task_id = facade.emit(event())["tasks"][0]
    with pytest.raises(IntegrityError), transaction(facade.engine) as c:
        c.execute(text("UPDATE tasks SET mandate_json='{}' WHERE id=:id"), {"id": task_id})
    with pytest.raises(IntegrityError), transaction(facade.engine) as c:
        c.execute(
            text(
                "INSERT INTO effects(id,kind,idem_key,status,payload_json) "
                "VALUES('bad','test','bad','pending','not-json')"
            )
        )


def test_duplicate_across_concurrent_connections(facade, config_dir):
    e = event()

    def emit(_):
        service = OwnerFacade(config_dir)
        try:
            return service.emit(e)
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(emit, range(4)))
    assert sorted(r["event"]["status"] for r in results) == ["accepted"] + ["duplicate"] * 3
    assert len(facade.list("tasks")["items"]) == 1


@pytest.mark.parametrize(
    "mode,count,observations", [("off", 0, 0), ("observe", 0, 1), ("auto", 1, 0)]
)
def test_inferred_modes(facade, mode, count, observations):
    facade.config.rules = RulesConfig(
        rules=[
            Rule(
                id="inferred",
                rank=1,
                match={},
                skill="sample-direct",
                accept_mode=mode,
            )
        ]
    )
    result = facade.emit(event(source="cron", addressed=False))
    assert len(result["tasks"]) == count
    assert len(facade.list("observations")["items"]) == observations


@pytest.mark.parametrize("mode", ["off", "observe", "auto"])
def test_explicit_bypasses_mode(facade, mode):
    facade.config.rules.rules[0].accept_mode = mode
    assert len(facade.emit(event())["tasks"]) == 1


def test_rule_order_continue_assess_and_untrusted(facade):
    a = Rule(id="first", rank=1, match={}, skill="a", accept_mode="auto")
    b = Rule(id="second", rank=2, match={}, skill="b", accept_mode="auto")
    facade.config.rules = RulesConfig(rules=[b, a])
    assert len(facade.emit(event())["tasks"]) == 1
    a.continue_ = True
    assert len(facade.emit(event())["tasks"]) == 2
    assert facade.emit(event(actor="stranger"))["event"]["status"] == "observed"
    assert facade.emit(event(trust="unverified"))["event"]["status"] == "rejected"
    a.assess = True
    a.continue_ = False
    result = facade.emit(event(source="cron", addressed=False))
    assert result["tasks"] == []
    assert facade.list("observations")["items"][0]["would_accept"] == 0


def test_subject_merge_keeps_context_reference(facade):
    facade.config.rules.rules[0].merge_by = "subject"
    first = facade.emit(event(subject="issue-1"))["tasks"][0]
    second_event = event(subject="issue-1")
    assert facade.emit(second_event)["tasks"] == [first]
    timeline = facade.show("tasks", first)["timeline"]
    assert second_event.id in timeline[0]["reason_json"]


def test_pause_still_ingests_and_extends_decisions(facade):
    facade.pause()
    with transaction(facade.engine) as c:
        set_value(c, "paused_at", (datetime.now(UTC) - timedelta(hours=2)).isoformat())
        c.execute(
            text("INSERT INTO decisions(id,status,expires_at) VALUES('d','pending',:at)"),
            {
                "at": datetime.now(UTC).isoformat(),
            },
        )
    assert facade.emit(event())["tasks"]
    assert facade.status()["paused"] is True
    facade.resume()
    assert facade.status()["paused"] is False
    with facade.engine.connect() as c:
        expires = datetime.fromisoformat(
            c.execute(text("SELECT expires_at FROM decisions")).scalar_one()
        )
        assert expires > datetime.now(UTC) + timedelta(minutes=119)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-testABCDE",
        "ghp_testABCDE",
        "github_pat_testABCDE",
        "xoxb-testABCDE",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJabc.def.ghi",
        "https://user:password@example.org/path",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
        "Authorization: Bearer anything",
        "Cookie: session=anything",
    ],
)
def test_redaction(secret):
    redactor = Redactor()
    result = redactor(secret)
    assert "[REDACTED]" in result
    assert secret not in result


def test_env_secret_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_TEST_SECRET", "specific-sensitive-value")
    assert resolve("secret://env/PA_TEST_SECRET") == "specific-sensitive-value"
    from pa.obs.redact import redact

    assert redact("specific-sensitive-value") == "[REDACTED]"
    path = tmp_path / ".env"
    path.write_text("example")
    path.chmod(0o644)
    with pytest.raises(PAError):
        check_env_permissions(path)
    path.chmod(0o600)
    check_env_permissions(path)


def test_plugins_and_cron():
    assert len(GROUPS) == 9
    for group in GROUPS:
        for entry in discover(group).values():
            assert entry.load()
    cron = CronAdapter()
    data = {"id": "daily", "scheduled_at": "2026-11-02T09:00:00+08:00", "event_type": "cron.tick"}
    first = cron.parse(data)
    data["scheduled_at"] = "2026-11-02T01:00:00+00:00"
    assert cron.parse(data).dedup_key == first.dedup_key


def test_event_constraints():
    with pytest.raises(ValidationError):
        ManualAdapter().parse({"type": "manual.test", "summary": "x" * 81})
    with pytest.raises(ValidationError):
        PAConfig(instance="x", data_dir="data", worker={"slots": "3"})
