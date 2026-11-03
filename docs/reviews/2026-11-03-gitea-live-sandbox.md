# 2026-11-03 — Real Gitea sandbox integration (PRD v1.1 / M2)

Dedicated **private** repository: [`muwei/pa-v11-acceptance-sandbox`](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox). This repository is intentionally separate from production. All tests and changes below were confined to this sandbox.

## Evidence (verified)

1. Gitea MCP created [Issue #1](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/issues/1): request `VALUE=41` → `VALUE=42`.
2. Authenticated Gitea file API committed a baseline README, `tests/test_value.py` and `.gitea/workflows/ci.yml` to the sandbox default branch. Gitea Actions automatically queued run [#222](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/actions/runs/222).
3. A disposable local PA instance on Mac invoked **real Codex**, using the `sample-code` Skill. Task `01M6FQMA5TDA96QQZNY1BZ8B2A` produced a one-file, one-line Git patch, a `needs_human` receipt and a `awaiting_decision` task, with no remote push.
4. Gitea MCP committed this exact patch on branch `pa/01M6FQMA5TDA96QQZNY1BZ8B2A` and opened [Draft PR #2](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/pulls/2). Head SHA: `adc35115f368fc2b76731e1da7685c9db7ad8b22`. `get_diff` confirmed only `VALUE=41` → `VALUE=42`.
5. Real Gitea Actions queued the push [#223](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/actions/runs/223) and pull request [#224](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/actions/runs/224), creating pending contexts `PA v1.1 Sandbox CI / ci (push)` and `PA v1.1 Sandbox CI / ci (pull_request)`. **Queued is not passed**.
6. Real API shapes exposed two adapter defects now fixed in MyPA: `GET /pulls/{n}/files` returns a JSON array, not `GET /diff` JSON; Gitea's merge POST accepts lowercase `Do` values and returns an empty success body, so merged SHA must be fetched from the PR afterward. Regression tests cover deleted-file hard risk, head-SHA pinning, a missing-merge fail-closed result, and lowercase strategy.

## Mandatory acceptance not yet proved

- **BLOCKED — Full PA automation**: Mac cannot authenticate Git over the repository's `ssh://gitea@192.168.50.28:2222` endpoint; the user's public key is not registered. Git-over-NAS direct bare-repo push was **rejected** by Gitea's pre-receive hook, and was not bypassed. The MCP-created remote commit proves platform functionality but not PA's own Git push/PR orchestration.
- **PENDING — Real CI completion**: Sandbox Actions jobs are queued behind an existing long-running Gitea Runner job; results are not yet green.
- **BLOCKED — Platform branch protection**: Need the sandbox main branch to require independent `pa/ai-review` and actual CI status checks, and demonstrate that forbidden merges fail *at the server*, not just in PA memory.
- **NOT RUN — Independent AI reviewer**: Real model review under a no-network/no-secrets/read-only sandbox, genuine `pa/ai-review` commit status, and three-round fix/owner escalation. No synthetic success check will be posted to satisfy this.
- **NOT RUN — Post-merge deployment commit ancestry**: Only run after branch protection and reviewer checks are active. A successful HTTP 200 alone is insufficient.

## Identity setup (user action)

A **sandbox-only** Ed25519 key pair was generated on the Mac at `~/.ssh/pa_v11_acceptance_ed25519`. The private key stays on the Mac and has mode 0600. The corresponding `.pub` is intended as a **write-enabled deploy key only for this test repository**. Fingerprint: `SHA256:Ke6KCMmi6Nc/TN0X9GvWOjHoGQ8IOeAmuHaiYlEzDow`.

Do not add this key to a production repository or expose any access token in Git or acceptance logs. Once the key is configured, PA should use the authenticated Gitea SSH clone URL and a separately configured agent/merger API identity.

## Follow-up: sandbox SSH, CI protection and independent review

7. The user installed a repo-only, write-enabled Mac SSH deploy key. Mac verified `git ls-remote` and successfully pushed branch `acceptance/ssh-write-check` (`cd4fb433`) to `ssh://gitea@192.168.50.28:2222/muwei/pa-v11-acceptance-sandbox.git`. No production key was reused. `CodeExecutor.prepare()` also independently cloned this real private Gitea repository over that SSH key, and created an isolated task worktree at the actual sandbox base SHA `fc5dc07e`.
8. Sandbox `main` was protected with `enable_status_check=true`, required exact contexts `PA v1.1 Sandbox CI / ci (pull_request)` and `pa/ai-review`, `block_admin_merge_override=true` and `enable_push=false`. Real negative-control [PR #3](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/pulls/3) was not a draft; an attempted non-forced squash merge on head `cd4fb433` failed with **Gitea HTTP 405**, and follow-up read showed `merged=false`, PR still open. No unauthorized merge was executed.
9. Gitea Actions [#222](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/actions/runs/222), [#223](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/actions/runs/223) and [#224](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/actions/runs/224) all eventually completed **success**. The exact sandbox PR head `adc35115` was checked. Gitea Actions uses the native CI status name `PA v1.1 Sandbox CI / ci (pull_request)`, not the literal `ci`; MyPA now supports an owner-configured exact `ci_context` while requiring `pa/ai-review` independently.
10. A **genuine separate Hermes model** `/models/model.gguf` reviewed the actual Issue #1 diff in a new isolated reviewer context, with only the `clarify` toolset: `{issues: [], severity: null, verdict: approve}`; token usage 3,500 input + 353 output. Review and SHA were revalidated, logged to [PR #2](https://git.muwei.xyz/muwei/pa-v11-acceptance-sandbox/pulls/2#issuecomment-94), and a trusted acceptance coordinator posted `pa/ai-review=success` on **that SHA only**, via a previously connected Gitea admin identity. The status was a *real review result*, but was **not posted by the PA Worker with a least-privileged reviewer API identity**. PR #2 remains unmerged.
11. Separately, the new MyPA `CodeExecutor.poll()` real-review integration was tested with a disposable local Git SCM fixture and real Hermes inference: PA Worker automatically called Hermes, captured a structured verdict, recorded the reviewer model and SHA in SQLite, and sent `pa/ai-review=success` through the mocked status API. Tests cover malformed JSON, major findings, and stale SHA; none can silently grant a passing status. The native CLI `--max-turns` mismatch was fixed; Gitea native CI context is now explicitly configurable. This **does not replace** a real, scoped API credential run of PA against the private Gitea repository.

## Remaining for full M2 acceptance

- Dedicated least-privileged **agent**, **review status publisher**, and separately authorized **merger** credentials; the existing `muwei` Gitea MCP administrator identity must not be reused by an unattended PA agent.
- Execute **PA's own full integration**, not coordinator-mediated PR creation: signed Gitea Issue webhook → CodeExecutor over authenticated SSH → real Gitea PR and independent review → required CI contexts → SHA-bound owner decision → merger identity → optional release with live SHA verification.
- Prove a **positive protected merge** after explicit owner approval; PR #2 is still Draft and PR #3 intentionally has no `pa/ai-review` status. Neither has been merged.
- M5 hard isolation and failure recovery remain separate milestones; a `clarify`-only Hermes subprocess is a PRD-approved **M2 degraded** review mode, not a disposable container.

12. The newest independent review orchestration tests and Mac full pytest completed with exit code 0. `src/pa/ci/executor.py` now runs an actual Hermes reviewer under the owner-configured `review` role, parses and normalizes strict JSON, rejects conflicted severity/verdict, checks SHA before/after inference, writes reviews to SQLite, and only then calls `ScmAdapter.set_status`. No configured reviewer means **no status**, not a synthetic success. The different engine/model and reviewer-token trace were verified in an actual PA Worker run with a local Git fixture; the Gitea HTTP adapter in that run remained simulated. Gitea Actions workflow in the main project was adjusted to a concise M2 smoke suite after the single NAS runner bottleneck and an earlier full-suite timeout.

## Service-account handoff (2026-11-03 follow-up)

The owner confirmed creation of two Gitea regular users. Verified via authenticated Gitea user search: `pa-agent-e2e` (user ID 3) and `pa-merger-e2e` (user ID 4), both with `is_admin=false`. PR #2 is still open and Draft on the same SHA, and its real Gitea CI and `pa/ai-review` statuses remain successful. An authorized administrative **read-only** Gitea API check subsequently confirmed that both accounts have exactly `write` collaborator access to `pa-v11-acceptance-sandbox` and `none` to production `muwei/pa`. The existing branch rule was updated and then confirmed to require the real PR CI context and `pa/ai-review`, with `enable_merge_whitelist=true`, `merge_whitelist_usernames=[pa-merger-e2e]`, `block_admin_merge_override=true`, `enable_push=false` and `enable_force_push=false`. Tokens remain unconfigured.

Added an independent fail-closed guarantee in MyPA: remote `agent_token` and `merger_token` may not be the same secret reference, and separate references resolving to identical token material are rejected at runtime. Corresponding unit tests verify both rejection and valid distinct-token configuration. This is a necessary local check, **not** proof that Gitea permissions enforce actual identity separation.

Next owner action: create least-privilege PATs in each user's own Gitea account and enter them **only on the Mac** via `scripts/setup-gitea-sandbox-credentials.py`. The helper validates the exact account, sandbox Write, no production Write/Admin, and stores separate mode-0600 tokens. PAT generation through the remote admin tool was blocked by the execution safety layer; the operator did not create, obtain or print any account token. Never send token material through chat or commit it to Git.

## Scoped identity checks and credential handoff

- `pa-agent-e2e` (ID 3) and `pa-merger-e2e` (ID 4) are non-admin users. Gitea API `GET /repos/muwei/{repo}/collaborators/{user}/permission` returned **write** for the private acceptance sandbox and **none** for the production PA repository for both identities.
- Verified protected `main`: `enable_merge_whitelist=true`, `merge_whitelist_usernames=[pa-merger-e2e]`; required statuses remain `PA v1.1 Sandbox CI / ci (pull_request)` and `pa/ai-review`; administrator override and direct/forced branch pushes remain disabled. No PR was merged during this permission update.
- GitHub and NAS Gitea Actions both reported **success** for MyPA's preceding `295ef90` review-integration commit. A subsequent `4fa515d` patch rejects identical agent/merger secret references and identical resolved credential values.
- `scripts/setup-gitea-sandbox-credentials.py` is a local TTY-only handoff, not a bot credential generator. The owner must create the separate account PATs from Gitea and enter each into the Mac terminal (no chat/token echo). `tests/test_gitea_sandbox_credential_setup.py` verifies wrong principal rejection, production-write rejection, private file modes, and overwrite refusal.
- **Not yet validated:** Gitea PR creation and status updates with the actual `pa-agent-e2e` token; agent-identity forbidden merge; Merger-only positive merge with fresh owner approval; real release/live SHA. These depend on the user-supplied account PATs.
