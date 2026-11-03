# PA PRD v1.1 Acceptance Tracker

This is the repository's **live acceptance register**. Source of truth: `docs/dev-kit/PA-私人AI自动化系统-PRD.md`, phase DoD in `docs/dev-kit/01-开发任务拆解.md`, Issue [#4](https://github.com/Muwei-TA/mypa/issues/4), [#5](https://github.com/Muwei-TA/mypa/issues/5), [#6](https://github.com/Muwei-TA/mypa/issues/6), and Draft PR [#3](https://github.com/Muwei-TA/mypa/pull/3).

**Acceptance statuses:** `PASS` = executed, reproducible evidence; `PARTIAL` = tested using mocks / with an important requirement missing; `BLOCKED` = needs credentials, external system, or human approval; `NOT RUN` = no execution evidence. A passing unit test does not substitute for external end-to-end acceptance. Closing the PR requires every v1.1 mandatory item to have PASS evidence, or an owner-approved PRD change committed and linked to the issue.

| PRD phase / required outcome | Current status | Evidence or next test |
| --- | --- | --- |
| M1 manual trigger → task → completion | PASS | `docs/reviews/2026-11-03-repairs.md`, real Mac Codex 2026-11-03 verification |
| M1 resume / effect idempotency after forced kill | PARTIAL | Existing unit tests; need true external side-effect kill/restart test (#6) |
| M1 rule / observe / constraints / budget | PARTIAL | Unit tests; proactive model assessor still TODO (#6) |
| M2 real GitHub/Gitea Issue → PR | PARTIAL | `tests/test_business_code_flow.py` simulated platform; real Gitea pending (#5) |
| M2 independent strict AI review, fail/repair/3-round cap | PARTIAL | Failure paths simulate `pa/ai-review`; no independent real reviewer in PR orchestration (#4) |
| M2 mandatory `pa/ai-review` and `ci` platform branch protection | BLOCKED | Can check API when sandbox repo is available (#5) |
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
