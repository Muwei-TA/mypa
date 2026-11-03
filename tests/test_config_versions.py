"""M3-03 config versioning: base_revision, 409 conflicts, git commits."""

import subprocess

from sqlalchemy import text

from pa.config.versions import (
    ConfigConflict,
    content_hash,
    current_revision,
    drop_same_as_default,
    save_versioned,
    versions_of,
)
from pa.store.db import transaction


def _notify(configured):
    from pa.config.loader import load

    config = load(configured.config_dir)
    return config.notify.model_dump(mode="json")


def _git_init(config_dir):
    subprocess.run(
        ["git", "-C", str(config_dir), "init", "-q"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(config_dir), "add", "-A"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(config_dir),
            "-c",
            "user.name=test",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )


def test_first_save_and_revision_chain(configured):
    value = _notify(configured)
    with transaction(configured.engine) as c:
        result = save_versioned(
            c,
            configured.config_dir,
            file_name="notify.yaml",
            value=value,
            base_revision=0,
        )
        assert result["revision"] == 1
        # same content again: no new version (identical override semantics)
        same = save_versioned(
            c, configured.config_dir, file_name="notify.yaml", value=value, base_revision=1
        )
        assert same["unchanged"] is True and same["revision"] == 1
        # changed content: chain advances
        again = save_versioned(
            c,
            configured.config_dir,
            file_name="notify.yaml",
            value={"target": {"type": "chat", "id": "oc_new"}},
            base_revision=1,
        )
        assert again["revision"] == 2 and again["unchanged"] is False
    with configured.engine.connect() as c:
        assert current_revision(c, "notify.yaml") == 2
        chain = versions_of(c, "notify.yaml")
    assert [item["revision"] for item in chain] == [2, 1]
    assert chain[0]["base_revision"] == 1


def test_stale_base_returns_409_and_keeps_draft(configured):
    value = _notify(configured)
    with transaction(configured.engine) as c:
        save_versioned(
            c, configured.config_dir, file_name="notify.yaml", value=value, base_revision=0
        )
    # a second writer wins the race with changed content
    with transaction(configured.engine) as c:
        save_versioned(
            c,
            configured.config_dir,
            file_name="notify.yaml",
            value={"target": {"type": "chat", "id": "oc_race"}},
            base_revision=1,
        )
    # stale writer now hits 409; the draft dict stays untouched on their side
    try:
        with transaction(configured.engine) as c:
            save_versioned(
                c,
                configured.config_dir,
                file_name="notify.yaml",
                value=value,
                base_revision=1,
            )
    except ConfigConflict as err:
        assert err.expected_revision == 2
        assert "409" in str(err) or "moved from revision" in str(err)
    else:
        raise AssertionError("stale base did not conflict")
    with configured.engine.connect() as c:
        assert current_revision(c, "notify.yaml") == 2


def test_invalid_content_rejected_before_write(configured):

    bad = {"notify": {"target": {"type": "user", "id": "ou_x"}, "bogus_key": True}}
    try:
        with transaction(configured.engine) as c:
            save_versioned(
                c,
                configured.config_dir,
                file_name="notify.yaml",
                value=bad,
                base_revision=0,
            )
    except Exception as err:
        assert not isinstance(err, ConfigConflict)
    else:
        raise AssertionError("invalid config accepted")
    # file on disk unchanged
    from pa.config.loader import load

    assert load(configured.config_dir).notify.target.id


def test_git_commit_recorded_when_repo(configured):
    _git_init(configured.config_dir)
    with transaction(configured.engine) as c:
        save_versioned(
            c,
            configured.config_dir,
            file_name="notify.yaml",
            value={"target": {"type": "chat", "id": "oc_git"}},
            base_revision=0,
        )
    log = subprocess.run(
        ["git", "-C", str(configured.config_dir), "log", "--oneline"],
        capture_output=True,
        text=True,
    ).stdout
    assert "config: notify.yaml r1" in log


def test_drop_override_same_as_default(configured):
    value = _notify(configured)
    with transaction(configured.engine) as c:
        save_versioned(
            c, configured.config_dir, file_name="notify.yaml", value=value, base_revision=0
        )
    # same content as "new default" -> override auto-removed
    with transaction(configured.engine) as c:
        result = drop_same_as_default(
            c, configured.config_dir, file_name="notify.yaml", default=value
        )
    assert result["removed"] is True
    assert not (configured.config_dir / "notify.yaml").exists()
    with configured.engine.connect() as c:
        assert current_revision(c, "notify.yaml") == 2
        marker = c.execute(
            text("""
            SELECT origin FROM config_versions WHERE file='notify.yaml'
              AND revision=2
        """)
        ).scalar_one()
    assert marker == "upgrade"


def test_drop_keeps_custom_override(configured):
    value = _notify(configured)
    with transaction(configured.engine) as c:
        save_versioned(
            c, configured.config_dir, file_name="notify.yaml", value=value, base_revision=0
        )
    custom = {"target": {"type": "chat", "id": "oc_custom"}}
    with transaction(configured.engine) as c:
        result = drop_same_as_default(
            c, configured.config_dir, file_name="notify.yaml", default=custom
        )
    assert result["removed"] is False
    assert (configured.config_dir / "notify.yaml").exists()


def test_content_hash_stable():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})
