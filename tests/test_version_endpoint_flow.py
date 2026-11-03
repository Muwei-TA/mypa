"""PRD v1.1 K7: verify the deployed commit, not merely an HTTP 200."""

import asyncio
import subprocess

import httpx
import pytest

from pa.ci import hooks
from pa.config.schema import CodeRepository


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_http_version_endpoint_extracts_only_actual_commit_sha(monkeypatch):
    def response(request):
        if request.url.path == "/ok":
            return httpx.Response(200, json={"sha": "a" * 40})
        if request.url.path == "/invalid":
            return httpx.Response(200, json={"sha": "not-a-commit"})
        return httpx.Response(500, text="unavailable")

    transport = httpx.MockTransport(response)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=transport, **kwargs),
    )
    assert asyncio.run(hooks.fetch_live_sha("https://verify.example/ok")) == "a" * 40
    assert asyncio.run(hooks.fetch_live_sha("https://verify.example/invalid")) is None
    assert asyncio.run(hooks.fetch_live_sha("https://verify.example/fail")) is None


def test_live_sha_ancestor_validation_and_retry(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "f.txt").write_text("base")
    _git(repo, "add", "--all")
    _git(repo, "-c", "user.name=PA", "-c", "user.email=pa@localhost", "commit", "-m", "base")
    old = _git(repo, "rev-parse", "HEAD")
    (repo / "f.txt").write_text("new")
    _git(repo, "add", "--all")
    _git(repo, "-c", "user.name=PA", "-c", "user.email=pa@localhost", "commit", "-m", "merge")
    merged = _git(repo, "rev-parse", "HEAD")
    responses = iter([old, merged])

    async def fetch(_url):
        return next(responses)

    monkeypatch.setattr(hooks, "fetch_live_sha", fetch)
    verified = asyncio.run(
        hooks.verify_hook(
            "version_endpoint",
            args={"url": "https://verify.example/version"},
            mirror=repo,
            merged_sha=merged,
            attempts=2,
            interval_s=0,
        )
    )
    assert verified == {"verified": True, "hook": "version_endpoint", "live_sha": merged}

    async def bad(_url):
        return old

    monkeypatch.setattr(hooks, "fetch_live_sha", bad)
    result = asyncio.run(
        hooks.verify_hook(
            "version_endpoint",
            args={"url": "https://verify.example/version"},
            mirror=repo,
            merged_sha=merged,
            attempts=1,
        )
    )
    assert result["verified"] is False and result["live_sha"] is None


def test_version_endpoint_configuration_requires_url():
    with pytest.raises(ValueError):
        CodeRepository(
            scm="gitea",
            remote="https://example.invalid/o/r.git",
            verify_hook="version_endpoint",
            verify_url="",
        )
