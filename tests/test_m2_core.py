"""M2-05 env vars, M2-06 mandate revalidation, M2-08 engine roles."""

import json

from sqlalchemy import text

from pa.engines.roles import last_author, review_candidates
from pa.hub.mandate import revalidate, void_mandate
from pa.skills.env_vars import VAR_NAMES, build_env, filter_env
from pa.skills.loader import load_skill
from pa.store.db import transaction


def _put_run(facade, task_id, engine_id, code_changed, model="m1"):
    with transaction(facade.engine) as c:
        c.execute(
            text("""
            INSERT INTO runs(id,task_id,attempt,engine_id,tier,started_at,receipt_json)
            VALUES(:id,:task,1,:engine,'A',:at,:receipt)
        """),
            {
                "id": "run-" + engine_id,
                "task": task_id,
                "engine": engine_id,
                "at": "2026-11-03T00:00:00+00:00",
                "receipt": json.dumps({"code_changed": code_changed, "model": model}),
            },
        )


def test_env_vars_exactly_eleven_and_no_credentials():
    env = build_env(
        task_id="t1",
        task_name="Fix login",
        workspace="/tmp/w",
        skill="sample-code",
        rule="r1",
        repo="o/r",
        scm="github",
        issue=12,
        pr_head="pa/t1",
        branch="pa/t1",
        blocked_commands=["rm -rf"],
    )
    assert len(env) == 11
    assert set(env) == set(VAR_NAMES)
    assert env["PA_ISSUE"] == "12" and env["PA_BLOCKED_COMMANDS"] == "rm -rf"
    # No credential-shaped value may appear.
    assert not any("token" in v or "secret" in v for v in env.values())


def test_filter_env_drops_parent_credentials():
    injected = build_env(
        task_id="t",
        task_name="n",
        workspace="w",
        skill="s",
        rule="r",
        repo=None,
        scm=None,
        issue=None,
        pr_head=None,
        branch=None,
    )
    parent = {
        "PATH": "/usr/bin",
        "HOME": "/root",
        "GITHUB_TOKEN": "ghp_leak",
        "AWS_SECRET_ACCESS_KEY": "leak",
        "PA_TASK_ID": "t",
    }
    filtered = filter_env(parent, injected)
    assert "GITHUB_TOKEN" not in filtered and "AWS_SECRET_ACCESS_KEY" not in filtered
    assert filtered["PA_TASK_ID"] == "t" and filtered["PATH"] == "/usr/bin"
    assert all(
        k in VAR_NAMES or k in {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR"} for k in filtered
    )


def _mandate(strength, matched=None, actor="github:owner"):
    return json.dumps(
        {
            "policy_version": "p-1",
            "strength": strength,
            "via": "rule" if strength == "inferred" else "manual",
            "actor": actor,
            "matched_rules": matched or [],
            "scope": "fix repo#12",
            "source_scope": "github:o/r:issues",
        }
    )


def _set_rule_auto(config_dir):
    import yaml

    path = config_dir / "rules.yaml"
    rules = yaml.safe_load(path.read_text())
    rules["rules"][0]["accept_mode"] = "auto"
    path.write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False))


def test_revalidate_inferred_requires_enabled_auto_rule(configured, config_dir):
    _set_rule_auto(config_dir)
    from pa.config.loader import load

    config = load(config_dir)
    mandate = _mandate("inferred", ["r-manual-direct"])
    out = revalidate(None, mandate_json=mandate, config=config)
    assert out["valid"] is True


def test_revalidate_inferred_fails_when_mode_not_auto(configured):
    config = configured.config
    out = revalidate(None, mandate_json=_mandate("inferred", ["r-manual-direct"]), config=config)
    assert out["valid"] is False and out["reason"] == "mode_not_auto"


def test_revalidate_explicit_requires_allowlist(configured):
    config = configured.config
    assert (
        revalidate(None, mandate_json=_mandate("explicit", actor="github:owner"), config=config)[
            "valid"
        ]
        is False
    )
    # Register owner github identity, then it passes.
    import yaml

    path = configured.config_dir / "principals.yaml"
    path.write_text(yaml.safe_dump({"owner": {"github": "owner"}}))
    from pa.config.loader import load

    config2 = load(configured.config_dir)
    out = revalidate(None, mandate_json=_mandate("explicit", actor="github:owner"), config=config2)
    assert out["valid"] is True


def test_void_mandate_records_event(configured):
    task = configured.emit(
        __import__("pa.ingress.manual", fromlist=["ManualAdapter"])
        .ManualAdapter()
        .parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "hi"},
                "dedup_key": "mv-1",
            }
        )
    )["tasks"][0]
    with transaction(configured.engine) as c:
        void_mandate(c, task, "rule_disabled")
    with configured.engine.connect() as c:
        rows = (
            c.execute(text("SELECT reason_json FROM task_events WHERE task_id=:t"), {"t": task})
            .scalars()
            .all()
        )
    assert any("mandate_voided" in r for r in rows)


def _fake_config_with_roles(config, review_engines):
    from pa.config.schema import EnginesConfig

    engines = EnginesConfig.model_validate(
        {
            "engines": {
                name: {
                    "adapter": "codex_cli",
                    "command": ["/bin/true"],
                    "enabled": True,
                }
                for name in set(review_engines) | {"codex", "hermes"}
            },
            "roles": {"review": list(review_engines), "dev": ["codex", "hermes"]},
        }
    )

    class FakeConfig:
        pass

    fake = FakeConfig()
    fake.engines = engines
    return fake


def test_review_role_skips_last_author_engine(configured):
    task = configured.emit(
        __import__("pa.ingress.manual", fromlist=["ManualAdapter"])
        .ManualAdapter()
        .parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "hi"},
                "dedup_key": "rr-1",
            }
        )
    )["tasks"][0]
    _put_run(configured, task, "codex", code_changed=True, model="gpt-x")
    skill = load_skill("sample-direct")
    config = _fake_config_with_roles(configured.config, ["codex", "hermes"])
    out = review_candidates(config, skill, configured.engine, task)
    assert out["author"] == "codex"
    assert out["engines"] == ["hermes"]
    assert out["same_model_fallback"] is False


def test_review_role_flags_same_model_fallback(configured):
    task = configured.emit(
        __import__("pa.ingress.manual", fromlist=["ManualAdapter"])
        .ManualAdapter()
        .parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "hi"},
                "dedup_key": "rr-2",
            }
        )
    )["tasks"][0]
    _put_run(configured, task, "hermes", code_changed=True, model="m2")
    skill = load_skill("sample-direct")
    config = _fake_config_with_roles(configured.config, ["hermes"])
    out = review_candidates(config, skill, configured.engine, task)
    assert out["same_model_fallback"] is True
    assert out["engines"] == ["hermes"]


def test_last_author_none_without_code_changes(configured):
    task = configured.emit(
        __import__("pa.ingress.manual", fromlist=["ManualAdapter"])
        .ManualAdapter()
        .parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "hi"},
                "dedup_key": "rr-3",
            }
        )
    )["tasks"][0]
    _put_run(configured, task, "codex", code_changed=False)
    assert last_author(configured.engine, task) is None
