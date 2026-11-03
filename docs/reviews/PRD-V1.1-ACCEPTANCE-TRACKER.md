# PA PRD v1.1 Acceptance Tracker

This is the repository's **live acceptance register**. Source of truth: `docs/dev-kit/PA-私人AI自动化系统-PRD.md`, phase DoD in `docs/dev-kit/01-开发任务拆解.md`, Issue [#4](https://github.com/Muwei-TA/mypa/issues/4), [#5](https://github.com/Muwei-TA/mypa/issues/5), [#6](https://github.com/Muwei-TA/mypa/issues/6), and Draft PR [#3](https://github.com/Muwei-TA/mypa/pull/3).

**Acceptance statuses:** `PASS` = executed, reproducible evidence; `PARTIAL` = tested using mocks / with an important requirement missing; `BLOCKED` = needs credentials, external system, or human approval; `NOT RUN` = no execution evidence. A passing unit test does not substitute for external end-to-end acceptance. Closing the PR requires every v1.1 mandatory item to have PASS evidence, or an owner-approved PRD change committed and linked to the issue.

| PRD phase / required outcome | Current status | Evidence or next test |
| --- | --- | --- |
| M1 manual trigger → task → completion | PASS | `docs/reviews/2026-11-03-repairs.md`, real Mac Codex 2026-11-03 verification |
| M1 resume / effect idempotency after forced kill | PARTIAL | Existing unit tests; need true external side-effect kill/restart test (#6) |
| M1 rule / observe / constraints / budget | PARTIAL | Unit tests; proactive model assessor still TODO (#6) |
| M2 real GitHub/Gitea Issue → PR | PARTIAL | Real sandbox [Issue #1](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/issues/1) and [PR #2](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/pulls/2) created from an actual Mac PA/Codex patch; authenticated Git push/PR creation by PA itself is blocked (see live sandbox report, #5) |
| M2 independent strict AI review, fail/repair/3-round cap | PARTIAL | Worker now dispatches a separate Hermes reviewer, records SHA/usage, posts genuine `pa/ai-review` status after validated JSON; Mac real Hermes + simulated SCM verified, real Gitea status was still coordinator-mediated (#4/#5) |
| M2 mandatory `pa/ai-review` and `ci` platform branch protection | PARTIAL | Sandbox main now requires actual Gitea Actions CI and `pa/ai-review`; non-draft unreviewed PR #3 denied by real server HTTP 405. Positive merge with distinct identity still pending (#5) |
| M2 SHA-bound approval, safe merge, no privilege bypass | PARTIAL | Simulated PR tests; real platform reject-bypass test pending (#5) |
| M2 Issue/PR comment resumes original mandate | PARTIAL | Fake Gitea replay verified; real webhooks pending (#5) |
| M2 release hook + deployed SHA ancestry verification | PARTIAL | Workflow dispatch + HTTP smoke simulated; `version_endpoint` now parses deployed SHA and checks Git ancestry in a refreshed mirror; real deployed service endpoint and real Gitea merge are pending (#5) |
| M2 decision precedents avoid repeated owner prompts | NOT RUN | End-to-end precedent-case suite required (#4/#5) |
| M3 control panel covers everyday operations | PARTIAL | Isolated real Uvicorn auth/config/decision 10 checks passed; drag-and-drop/other operations pending (#6) |
| M4 native macOS GUI runs `sample-direct` with risk limits | NOT RUN | Synthetic GUI does not meet PRD acceptance (#6) |
| M5 isolated disposable containers for test/review/build | NOT RUN | Native Mac sandbox is not a container; container adapters not implemented (#6) |
| M5 memory and prior decision retrieval | PARTIAL | SQLite/FTS components; full vector and precedent behavior not demonstrated (#6) |
| M5 WebDAV archive + recovery drill | NOT RUN | Full archive/restore destructive-failure drill pending (#6) |
| M5 cost budgets, breaker, secret rotation, weekly cleanup | PARTIAL | Unit-level coverage; real interventions and scheduling pending (#6) |

## Current validation evidence

- `docs/reviews/2026-11-03-business-flow.md` describes 2026-11-03 Mac worktree / simulated SCM acceptance with full local pytest, Ruff, build and real Mac Codex.
- `.github/workflows/ci.yml` adds remote CI. **This checks lint/unit tests/build only; it does not provide the mandatory independent `pa/ai-review` context.**
- `tests/test_version_endpoint_flow.py` covers version payload parsing, rejection of stale live SHAs, and subsequent SHA-ancestry verification against a real disposable Git history. This is not a real deployment acceptance test.

## Remote testing policy

- Use a dedicated, disposable **private** repository for Gitea acceptance. Do not enable auto-merge or auto-release on the production PA repository.
- No GitHub/Gitea token values, passwords or webhook HMAC values belong in Git, issue bodies or test logs.
- Any real merge or release must be preceded by verification that the target repo and target service are dedicated test assets.

- `docs/reviews/2026-11-03-gitea-live-sandbox.md`: real, authenticated Gitea Issue/PR events with real Codex patch and CI queue; gap assessment.

- 2026-11-03 follow-up: real Mac `CodeExecutor.prepare` cloned the private sandbox via write-enabled SSH deploy key; isolated PA Worker invoked real Hermes to record a SHA-bound review and issue mocked status update. Sandbox protected merge *negative case* passed, but automatic PA Git/API integration and positive merge are still blocked on least-privileged identities.

- 2026-11-03 latest: the **main GitHub CI** remains full pytest, while the single-runner **NAS Gitea CI** runs deterministic M2 smoke suites, lint and build to avoid long Runner starvation. Full tests were rerun on Mac with exit code 0 before the review integration commit. The restored Gitea runner still needs a successful latest-head smoke run.

- Service-account identity validation: Gitea user search confirms two non-admin accounts `pa-agent-e2e` / `pa-merger-e2e`. MyPA now checks distinct secret references and resolved credential material. The sandbox collaborator grants, merger whitelist and real agent/merger credentials have **not** been verified or configured because the available management access was blocked; M2 positive protected merge is therefore still unaccepted.

- Service identity permission check (2026-11-03): both non-admin Gitea accounts are verified `write` collaborators on **sandbox only**, with `none` on production `muwei/pa`; protected `main` restricts merges to `pa-merger-e2e`, with CI and AI review required. Token material has **not** been generated/read in this chat; local-only setup/validation helper prepared. Real negative agent-token merge and positive merger-token merge remain **BLOCKED**, not PASS.

- 2026-11-03 Mac Chrome cross-check: both sandbox collaborators are Write, `main` only allows `pa-merger-e2e` merges, has exact CI/AI review status contexts, disables direct/forced pushes and bypass (administrators obey rule). Existing configuration needed no change. **Gitea PR approval count is 0**: the PA owner-decision gate has not been proven end-to-end with the real merger account. The local service-account token directory is absent; no credentials installed, positive protected merge and live SHA release verification remain unaccepted. See `docs/reviews/2026-11-03-gitea-live-sandbox.md`.

- Sandbox Agent API evidence: `pa-agent-e2e` PAT securely stored in Mac (`0700` directory, `0600` file), passed principal/sandbox-write/no-production-write validation, and posted a real sandbox Issue #1 comment `96` as `pa-agent-e2e`. Merger PAT and end-to-end merge still **BLOCKED**; no review/merge outcome can be inferred from this Issue API check.

- 2026-11-03 scoped credentials accepted: **both** `pa-agent-e2e` and `pa-merger-e2e` have validated Mac-local PATs (distinct, files `0600`, no production Write). New `scripts/verify-gitea-sandbox-readonly.py` passes using the **real MyPA Gitea adapter** under both identities: Issue #1, PR #2 exact SHA, one-line diff, two mandatory successful checks, merger-account independent read. **Agent-account merge negative test was blocked at the tool safety layer, not rejected by the Gitea server; positive approved merge and live-release SHA verification are still NOT RUN.**

- Live **PA Agent PR creation**: after sandbox-only authenticated SSH push of branch `pa/e2e-agent-api-20261103` (`1975b445`), the actual MyPA `GiteaAdapter.open_pr()` using `pa-agent-e2e` credentials created [sandbox PR #4](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/pulls/4). Its CI was pending and `pa/ai-review` absent at first inspection, PR open/unmerged. This verifies a real agent-token remote PR API write, **not** yet complete Worker-led Issue → PR → independently reviewed, SHA-bound Owner/merger approval → release.
