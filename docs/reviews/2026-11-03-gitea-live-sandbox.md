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
