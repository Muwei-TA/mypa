"""AI review (M2-11), pipeline (M2-12) and hooks (M2-13) tests."""

import asyncio
import subprocess

from sqlalchemy import text

from pa.ci.hooks import release_hook, verify_hook, version_contains
from pa.ci.pipeline import STAGES, decision_options_for_pr, fix_round, next_stage
from pa.ci.review import record_review, review_verdict, token_ratio
from pa.store.db import transaction


def test_review_verdict_grading():
    clean = review_verdict([])
    assert clean == {"issues": [], "severity": None, "verdict": "approve"}
    minor = review_verdict([{"severity": "minor", "title": "naming"}])
    assert minor["verdict"] == "comment" and minor["severity"] == "minor"
    major = review_verdict([{"severity": "minor"}, {"severity": "major"}])
    assert major["verdict"] == "request_changes" and major["severity"] == "major"
    blocker = review_verdict([{"severity": "major"}, {"severity": "blocker"}])
    assert blocker["verdict"] == "request_changes" and blocker["severity"] == "blocker"


def test_token_ratio_bounds():
    assert token_ratio(1000, 80) == 0.08
    assert token_ratio(0, 10) == 0.0


def test_record_review_row(configured):
    task = configured.emit(
        __import__("pa.ingress.manual", fromlist=["ManualAdapter"])
        .ManualAdapter()
        .parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "x"},
                "dedup_key": "rv-1",
            }
        )
    )["tasks"][0]
    with transaction(configured.engine) as c:
        pr_id = "pr-1"
        c.execute(
            text("""
            INSERT INTO pull_requests(id,task_id,scm,repo,number,url) 
            VALUES(:id,:task,'github','o/r',7,'u')
        """),
            {"id": pr_id, "task": task},
        )
        result = review_verdict([{"severity": "major", "title": "bug"}])
        record_review(
            c,
            pr_id=pr_id,
            round_=1,
            engine_id="hermes",
            model="m2",
            result=result,
            tokens=80,
            task_tokens=1000,
        )
        row = (
            c.execute(
                text("SELECT verdict,token_ratio,issues_json FROM reviews WHERE pr_id=:p"),
                {"p": pr_id},
            )
            .mappings()
            .one()
        )
    assert row["verdict"] == "request_changes"
    assert 0.07 < row["token_ratio"] < 0.09
    assert "major" in row["issues_json"]


def test_pipeline_stage_order():
    assert STAGES[0] == "opened" and STAGES[-1] == "delivered"
    assert next_stage("opened", "pass") == "ai_review"
    assert next_stage("ai_review", "pass") == "full_check"
    assert next_stage("full_check", "pass") == "merge_gate"
    assert next_stage("merge_gate", "pass") == "merged"
    assert next_stage("delivered", "pass") == "delivered"
    assert next_stage("ai_review", "fix") == "opened"
    assert next_stage("full_check", "timeout") == "full_check"


def test_fix_round_escalation(configured):
    task = configured.emit(
        __import__("pa.ingress.manual", fromlist=["ManualAdapter"])
        .ManualAdapter()
        .parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "x"},
                "dedup_key": "fr-1",
            }
        )
    )["tasks"][0]
    with transaction(configured.engine) as c:
        first = fix_round(c, task, max_rounds=3)
    assert first["fix_round"] == 1 and first["escalated"] is False
    with transaction(configured.engine) as c:
        second = fix_round(c, task, max_rounds=3)
    assert second["fix_round"] == 2 and second["escalated"] is False
    with transaction(configured.engine) as c:
        third = fix_round(c, task, max_rounds=3)
    assert third["fix_round"] == 3 and third["escalated"] is True
    with configured.engine.connect() as c:
        status = c.execute(text("SELECT status FROM tasks WHERE id=:t"), {"t": task}).scalar_one()
    assert status == "awaiting_decision"


def test_decision_card_options():
    options = decision_options_for_pr()
    assert [o["id"] for o in options] == ["one_more_round", "take_over", "close_pr"]


def test_release_hooks():
    assert asyncio.run(release_hook("none"))["released"] is True
    out = asyncio.run(release_hook("tag", sha="abcdef123456"))
    assert out["tag"] == "pa-release-abcdef12"
    out = asyncio.run(release_hook("script", script="exit 0"))
    assert out["released"] is True
    try:
        asyncio.run(release_hook("script", script="exit 1"))
        raise AssertionError("must fail")
    except Exception:
        pass


def test_verify_none_and_script():
    assert asyncio.run(verify_hook("none"))["verified"] is True
    out = asyncio.run(verify_hook("script", args={"script": "exit 0"}))
    assert out["verified"] is True


def test_version_endpoint_contains(tmp_path):
    src = tmp_path / "repo"
    src.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=src, capture_output=True, check=True)
    (src / "a.txt").write_text("1")
    subprocess.run(["git", "add", "."], cwd=src, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c1"],
        cwd=src,
        capture_output=True,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    (src / "b.txt").write_text("2")
    subprocess.run(["git", "add", "."], cwd=src, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c2"],
        cwd=src,
        capture_output=True,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert version_contains(src, base, head)
    assert not version_contains(src, head, base)
    # Fast retry loop: contained immediately, no sleeps needed.
    out = asyncio.run(
        verify_hook("version_endpoint", mirror=src, merged_sha=base, live_sha=head, attempts=2)
    )
    assert out["verified"] is True


def test_version_endpoint_retries_then_fails(tmp_path):
    src = tmp_path / "repo2"
    src.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=src, capture_output=True, check=True)
    (src / "a.txt").write_text("1")
    subprocess.run(["git", "add", "."], cwd=src, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c1"],
        cwd=src,
        capture_output=True,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    # The tip cannot be an ancestor of itself as a distinct "live" ref:
    # merged==live passes trivially; use a bogus merged sha to force failure.
    out = asyncio.run(
        verify_hook(
            "version_endpoint",
            mirror=src,
            merged_sha="0" * 40,
            live_sha=head,
            attempts=2,
            interval_s=0.01,
        )
    )
    assert out["verified"] is False
