# ArbitrageFinder Reliability + Feature Implementation Plan

## Goal
Finish the current scanner-speed/reliability branch as a production-hardening release: preserve existing arbitrage behavior, reduce scan/alert latency, improve signal quality, harden AI maintenance, and add user-visible controls without risking the production database volume.

## Phase 0 — Release gate
- Fix all syntax/import regressions introduced by the normal-user AI restoration.
- Add a compileall gate before pytest.
- Require a green CI run before deployment.
- Keep `arbitrage_data` intact during EC2 deployment.

## Phase 1 — Scanner and alert-path hardening
- Preload VIP users once per background scan instead of querying SQLite once per opportunity.
- Precompute selected exchanges and filters for the alert cycle.
- Cache transfer metadata by exchange + symbol for the lifetime of one scan/alert cycle.
- Verify buy/sell transfer metadata concurrently.
- Avoid redundant user reads when sending alerts.
- Preserve cooldown and material-change deduplication.

## Phase 2 — Signal quality controls
- Add configurable minimum stable observations for an opportunity.
- Use the existing bounded spread history as the source of stability.
- Keep the default at one observation so existing alert behavior is unchanged.
- Surface stability in opportunity cards and scan diagnostics.
- Add a simple command and UI control for the stability requirement.

## Phase 3 — AI maintenance safety
- Validate proposed patches in an isolated temporary Git worktree rather than testing the unchanged live working tree.
- Record the Git HEAD used for validation.
- Refuse approval when the repository HEAD or working tree changed after validation; require re-validation.
- Expose clean public user-AI methods rather than relying on private maintenance methods.
- Add normal-user AI request throttling/concurrency limits.
- Sanitize user-AI responses for obvious credential/path leakage.

## Phase 4 — Configuration/security hardening
- Remove the insecure built-in admin secret fallback.
- Treat a missing admin secret as an explicit configuration problem.
- Keep admin handlers authorization-gated even when a command is visible.

## Phase 5 — Tests and verification
- Add regression tests for stability filtering.
- Add regression tests for AI request throttling and patch-validation semantics.
- Add tests for alert-cycle user preloading/transfer-cache behavior where practical.
- Run compileall + full pytest.
- Inspect CI logs for failures and fix regressions before deployment.

## Completion criteria
The implementation is considered complete only when:
1. The branch has the planned hardening/features.
2. Compileall passes.
3. The full pytest suite passes.
4. The PR CI is green on the latest commit.
5. No production database volume is deleted or recreated.
6. Deployment is still a separate step after CI verification.
