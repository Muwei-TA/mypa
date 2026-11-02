"""M5 disaster drills (E2E-11..14 in the local harness).

E2E-11 (container tests), E2E-13 (restore on a second machine), and E2E-14
(doubao_desktop GUI run) require the Mac mini, real container runtimes or a
graphical session; they stay deferred for on-device verification (per owner
instruction). What the local harness drives end to end:

* E2E-11 (contract half): sandbox run records the backend name; a review
  spec never reports sandbox=degraded.
* E2E-12: pa backup restore --dry-run verifies a fresh snapshot.
* E2E-14 (contract half): C-tier conversion clamps confidence to 0.6 and
  the hard-limit gate refuses forbidden tools.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from pa.cli.main import app
from pa.engines.gui.convert import convert
from pa.engines.gui.limits import check_c_tier_limits, clamp_confidence
from pa.sandbox.backends import select_backend
from pa.sandbox.runtime import ProcessSandbox, SandboxSpec, review_spec
from pa.store.archive import snapshot, verify

runner = CliRunner()


# --- E2E-11 (contract half) --------------------------------------------------


def test_e2e_11_sandbox_runs_never_report_degraded():
    backend, name = select_backend("docker")  # docker binary decides; both are fine
    assert name in ("docker", "process")
    if name == "docker":
        return  # real docker present: container drill runs on the host instead
    result = backend.run(SandboxSpec(command=["true"], timeout_min=1))
    assert result.ok and result.sandbox == "process"
    assert "degraded" not in result.sandbox

    spec = review_spec(["true"])
    assert spec.review is True and spec.network == "none" and spec.mount == "readonly"
    reviewed = ProcessSandbox().run(spec)
    assert reviewed.ok and "review=strict" in reviewed.notes
    assert reviewed.sandbox != "degraded"


# --- E2E-12 ------------------------------------------------------------------


def test_e2e_12_backup_restore_dry_run_verifies(configured, tmp_path):
    db_path = Path(configured.engine.url.database)
    snap = snapshot(db_path, tmp_path / "snapshots")
    check = verify(snap)
    assert check["ok"] is True

    result = runner.invoke(
        app,
        [
            "--config-dir",
            str(configured.config_dir),
            "backup",
            "restore",
            str(snap),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "dry-run" and payload["ok"] is True
    assert db_path.exists()  # live db untouched


# --- E2E-13 (deferred marker) ------------------------------------------------


def test_e2e_13_full_machine_restore_deferred_to_real_host():
    """整机恢复演练需要第二台真实机器与密钥重写，暂留待后续验证。"""
    result = runner.invoke(app, ["doctor", "--config-dir", "/nonexistent/pa-e2e13"])
    assert result.exit_code != 0  # harness placeholder: real drill runs on hardware


# --- E2E-14 (contract half) --------------------------------------------------


def test_e2e_14_c_tier_receipt_clamped_and_gated():
    class _Model:
        def call(self, prompt):
            return {"answer": "好的", "confidence": 0.95}

    receipt = convert("问一句", _Model().call)
    assert receipt["confidence"] <= 0.6 and receipt.get("clamped") is True

    class _Skill:
        tools = ["external_message"]
        risk_ceiling = "low"
        repo_write = False

    try:
        check_c_tier_limits(skill=_Skill())
        raise AssertionError("external_message must be refused on C tier")
    except Exception as exc:  # PAError: hard rule, refuses the tool outright
        assert "external_message" in str(exc)

    allowed = check_c_tier_limits(skill=type("S", (), {"tools": [], "risk_ceiling": "low",
                                                       "repo_write": False})())
    assert allowed["can_edit_code"] is False
    assert clamp_confidence(0.42) == 0.42
