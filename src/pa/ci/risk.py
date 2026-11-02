"""Risk assessment (PRD §10.3): final = max(hard-rule risk, ai risk)."""

import fnmatch

from pa.scm.base import Diff

HIGH_PATTERNS = (
    "requirements*.txt",
    "pyproject.toml",
    "package*.json",
    "go.mod",
    "poetry.lock",
    "uv.lock",
    "Cargo.toml",
    "Gemfile",
)
HIGH_PATHS = (".github/**", ".gitea/**", "**/auth/**", "**/secret*")
LOW_PATTERNS = ("docs/**", "**/*.md", "tests/**")
LOW_CONFIG_SUFFIXES = (
    ".md",
    ".txt",
    ".rst",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    # fnmatch on both the whole path and the basename covers "docs/**" and
    # "**/*.md" styles without a glob dependency.
    return any(
        fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path.split("/")[-1], p) for p in patterns
    )


def hard_risk(diff: Diff, *, tags: list[str] | None = None, seven_tags: frozenset) -> str:
    tags = tags or []
    if set(tags) & set(seven_tags):
        return "high"
    if not diff.files:
        return "low"
    deleted = any(f["change_type"] == "removed" for f in diff.files)
    dependency = any(_matches_any(f["path"], HIGH_PATTERNS) for f in diff.files)
    sensitive = any(_matches_any(f["path"], HIGH_PATHS) for f in diff.files)
    if deleted or dependency or sensitive or diff.changed_lines >= 200:
        return "high"
    if diff.changed_lines >= 50:
        return "medium"
    docs_only = all(
        _matches_any(f["path"], LOW_PATTERNS)
        or f["path"].endswith(LOW_CONFIG_SUFFIXES)
        for f in diff.files
    )
    return "low" if docs_only and diff.changed_lines < 50 else "medium"


def final_risk(
    diff: Diff,
    *,
    ai_risk: str = "low",
    tags: list[str] | None = None,
    seven_tags: frozenset,
) -> str:
    hard = hard_risk(diff, tags=tags, seven_tags=seven_tags)
    levels = {"low": 0, "medium": 1, "high": 2}
    return max(hard, ai_risk, key=lambda r: levels[r])
