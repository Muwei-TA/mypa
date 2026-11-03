# 2026-11-03 — Business-flow-first acceptance pass

Branch: `codex/acceptance-hardening-20261103` (Mac worktree at `/Users/weimu/Documents/ChatGPT/pa`).

Scope: **business behavior first**. Production services, credentials, merger identities, and deployed configuration were not changed. The new acceptance tests use a real disposable local Git repository, a deterministic fake Agent, and a simulated SCM platform. They do **not** prove live GitHub/Gitea, independent model review, or deployed-version correctness.

## Implemented and checked

- Manual and verified Gitea Issue ingress can create the same code-task workflow; native Gitea HMAC headers, delivery deduplication, and unauthorized sender rejection are tested.
- A writable task Git worktree is used by the Agent; PA commits, pushes and opens the PR. Existing open PRs are reused for fix rounds.
- Failed required CI contexts return the same task to its worktree with the failed check names in the next prompt. Two automatic retries precede the third-failure owner decision.
- Passing CI creates a merge decision for non-auto policies. Approval of SHA A cannot merge SHA B; missing merger identity leads to manual handoff instead of an impossible approval request.
- The worker scans multiple waiting PRs, rather than letting a pending first PR block later ready work.
- Owner Issue and PR comments on subscribed threads are stored as additional task context and can requeue a waiting task. Unauthorized senders do not retask an Agent.
- Optional release workflow dispatch and HTTP readiness verification remain separate from merge. A release requiring owner confirmation is gated; failed readiness stays pending, with no duplicate dispatch or merge on verification retry.
- No-hook merges report `release_verified=false`. An HTTP readiness check is **not** commit-ancestry verification.
- Mandatory platform review/CI contexts cannot be removed from a remote repository's code config; high-risk merges require owner confirmation. Deletions are assessed as high risk, and minor-only review verdicts are not a failing status.

## Evidence

- `tests/test_business_code_flow.py`: business E2E-style tests for manual and Gitea Issue ingress, fix rounds, SHA-bound approval, merge, release decision, HTTP verification, comments, and real-format HMAC delivery.
- Full local pytest suite: **exit code 0** (11/03 Mac run); `ruff check`, `ruff format --check`, `git diff --check`: passed.
- Package build using `uv build --offline`: sdist and wheel succeeded.
- Real disposable loopback Uvicorn smoke: 10/10 HTTP/session/CSRF/config/decision checks passed.
- Real authenticated Mac Codex (disposable config and Git repository): text task closed with 42; code task generated an actual `VALUE=41` → `VALUE=42` patch and stopped at owner review. The original repository and installed production PA were unchanged.
- External calls are mocked. Live Gitea branch protections, independent AI review, actual CI runner results, deployment and rollback are **not** validated by these tests.

## Not yet accepted under PRD v1.1

1. Independent AI review in a strictly isolated execution environment and same-SHA `pa/ai-review` status generation; the tests model platform check status, rather than synthesizing a successful AI review.
2. Real GitHub/Gitea issue-to-merge, required branch-protection verification, Lark decision callbacks, and production release verification by merged/live commit ancestry.
3. Crash-safe platform-side effects via a durable outbox (push/open PR/merge/dispatch), plus reconciliation after a process dies between remote success and the local DB commit.
4. Native macOS GUI driver, real container sandbox, WebDAV recovery and broader M4/M5 whole-system acceptance.

These engineering/reliability gaps remain intentionally visible. Do not mark M1–M5 or the entire PRD as accepted based on the business-flow fixture alone.
