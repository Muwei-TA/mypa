"""Risk assessment (M2-09) and merge permission matrix (M2-10) tests."""

from pa.ci.merge_gate import merge_gate, repo_class
from pa.ci.risk import final_risk, hard_risk
from pa.config.schema import SEVEN_TAGS, MergePolicy
from pa.scm.base import Diff


def _diff(paths, added=10, removed=2, change_type="modified"):
    files = [
        {"path": p, "added": added, "removed": removed, "change_type": change_type, "patch": ""}
        for p in paths
    ]
    return Diff(files=files, additions=added * len(files), deletions=removed * len(files))


def test_low_docs_only():
    diff = _diff(["docs/a.md"], added=10, removed=0)
    assert hard_risk(diff, seven_tags=SEVEN_TAGS) == "low"


def test_medium_under_200_lines():
    diff = _diff(["src/a.py", "src/b.py"], added=90, removed=5)
    assert diff.changed_lines == 190
    assert hard_risk(diff, seven_tags=SEVEN_TAGS) == "medium"


def test_high_at_200_lines():
    diff = _diff(["src/a.py"], added=150, removed=50)
    assert diff.changed_lines == 200
    assert hard_risk(diff, seven_tags=SEVEN_TAGS) == "high"


def test_high_dependency_change():
    diff = _diff(["pyproject.toml"], added=1, removed=1)
    assert hard_risk(diff, seven_tags=SEVEN_TAGS) == "high"


def test_high_sensitive_paths_and_deletion():
    assert hard_risk(_diff(["src/auth/login.py"]), seven_tags=SEVEN_TAGS) == "high"
    assert hard_risk(_diff(["src/secrets.py"]), seven_tags=SEVEN_TAGS) == "high"
    assert hard_risk(_diff([".github/workflows/ci.yml"]), seven_tags=SEVEN_TAGS) == "high"
    assert hard_risk(_diff(["src/a.py"], change_type="removed"), seven_tags=SEVEN_TAGS) == "high"


def test_medium_business_code_under_200():
    diff = _diff(["src/a.py"], added=60, removed=30)
    assert hard_risk(diff, seven_tags=SEVEN_TAGS) == "medium"


def test_seven_tag_forces_high():
    diff = _diff(["docs/a.md"], added=1, removed=0)
    assert hard_risk(diff, tags=["spend_money"], seven_tags=SEVEN_TAGS) == "high"


def test_final_risk_takes_max():
    diff = _diff(["docs/a.md"], added=1, removed=0)
    assert final_risk(diff, ai_risk="high", seven_tags=SEVEN_TAGS) == "high"
    diff = _diff(["src/a.py"], added=150, removed=50)
    assert final_risk(diff, ai_risk="low", seven_tags=SEVEN_TAGS) == "high"


def _policy() -> MergePolicy:
    return MergePolicy.model_validate(
        {
            "repo_classes": {
                "sandbox": {"repos": ["o/sandbox"]},
                "personal": {"repos": ["o/personal"]},
                "prod": {"repos": ["o/prod"]},
            },
            "matrix": {
                "sandbox": {
                    "low": "auto_release",
                    "medium": "auto_merge_notify",
                    "high": "confirm",
                },
                "personal": {
                    "low": "auto_merge_notify",
                    "medium": "confirm",
                    "high": "confirm_merge_and_release",
                },
                "prod": {
                    "low": "confirm",
                    "medium": "confirm",
                    "high": "confirm_no_auto_release",
                },
            },
            "default_class": "personal",
        }
    )


def test_repo_class_resolution():
    policy = _policy()
    assert repo_class("o/sandbox", policy) == "sandbox"
    assert repo_class("o/personal", policy) == "personal"
    assert repo_class("o/unknown", policy) == "personal"


class ChecksPassed:
    all_required_passed = True


class ChecksFailed:
    all_required_passed = False


def test_matrix_nine_cells():
    policy = _policy()
    expected = {
        ("sandbox", "low"): "auto_release",
        ("sandbox", "medium"): "auto_merge_notify",
        ("sandbox", "high"): "confirm",
        ("personal", "low"): "auto_merge_notify",
        ("personal", "medium"): "confirm",
        ("personal", "high"): "confirm_merge_and_release",
        ("prod", "low"): "confirm",
        ("prod", "medium"): "confirm",
        ("prod", "high"): "confirm_no_auto_release",
    }
    for (klass, risk), action in expected.items():
        repo = {"sandbox": "o/sandbox", "personal": "o/personal", "prod": "o/prod"}[klass]
        out = merge_gate(policy, repo=repo, risk=risk, checks=ChecksPassed())
        assert out["action"] == action, (klass, risk)


def test_gate_blocks_when_required_checks_fail():
    policy = _policy()
    out = merge_gate(policy, repo="o/sandbox", risk="low", checks=ChecksFailed())
    assert out["action"] == "blocked" and "required checks" in out["reason"]


def test_policy_schema_validation():
    with __import__("pytest").raises(Exception):
        MergePolicy(
            repo_classes={},
            matrix={"ghost": {"low": "confirm", "medium": "confirm", "high": "confirm"}},
        )
    policy = MergePolicy()
    assert policy.default_class == "personal"
