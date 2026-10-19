# Implementation Prompt: ArbiHunt-Inspired Android Arbitrage Scanner

You are the implementation agent for Firdousratherr/Arbitrage-bot. Follow this brief as the active task, not merely as a proposal.

## Mission
Build an original, production-minded Android cryptocurrency arbitrage scanner inspired by the publicly documented capabilities of ArbiHunt. Do not copy proprietary source code, brand assets, text, or exact UI. Implement equivalent general functionality with original code and design.

## Hard constraints
1. Work only on branch `feat/arbihunt-style-android` until explicitly approved.
2. Never merge to `main`, deploy, restart, rebuild or alter the running production Telegram bot without explicit user approval.
3. Preserve existing Telegram bot behavior, commands, database semantics, environment variables, Docker image/entrypoint and production deployment. Avoid edits to existing bot files unless necessary, justified, tested and separately reviewed.
4. Keep Android app, mobile API and Telegram bot as isolated components. Use a separate API process/container and separate mobile API configuration. Do not require Telegram tokens for the mobile API.
5. Keep the initial product read-only: no automatic trading, order placement, wallet connection, private keys or exchange withdrawal permissions. Never place exchange secrets in the APK or public repository.
6. Do not claim features, exchange support, transfer verification, profit accuracy, test success or live availability unless implemented and tested. Mark unavailable/unknown data explicitly and fail closed when safety-critical transfer metadata cannot be verified.
7. Use live exchange public market data only through documented supported adapters, respect rate limits, bound concurrency, and expose freshness/error diagnostics.
8. Keep each change incremental, reviewable and tested. Do not overwrite existing files without first fetching and reviewing them.

## Research-derived feature scope
ArbiHunt's public website, Google Play listing and knowledge-base documentation describe:
- Cross-exchange spot-market scanning and a unified opportunity board.
- Order-book executable bid/ask prices and depth-aware sizing, rather than relying only on last-trade tickers.
- Gross spread separately from estimated net profit.
- Trading-fee estimates for both buy and sell legs, withdrawal/transfer costs and liquidity/slippage estimates.
- Deposit/withdrawal network compatibility and status, network fee transparency and token contract checks.
- Opportunity details for buy and sell legs, route, fees, network, liquidity, timestamps/freshness and warnings.
- Filtering by exchange, spread, liquidity and network; sorting by spread, net profit, liquidity and freshness; search and watchlist.
- Dashboard KPIs, live refresh, stale opportunity details, exchange status, and a responsive mobile-first list/card layout.
- Authentication/account preferences, cross-device watchlists, notifications, help/community features and optional subscription tiers.
- Read-only/manual execution model; no API keys required and no profit guarantees.

Treat published exchange counts and refresh intervals as claims about ArbiHunt, not requirements to fabricate equivalent coverage. This repository currently has a smaller CCXT exchange registry and an existing Telegram-oriented scanner. Display only actually supported/healthy venues and disclose limitations.

## Delivery architecture
- Existing `app/`: Telegram bot and scanner domain logic. Preserve behavior.
- `mobile_api/`: separate FastAPI service exposing authenticated/read-only JSON endpoints and health/market-data diagnostics. Separate env variables and process lifecycle. It may reuse tested scanner logic, but must not require Telegram bot startup or Telegram credentials.
- `android/`: native Android app using Kotlin and Jetpack Compose; build with Gradle/GitHub Actions. Original UI and branding.
- Use an explicit API contract and versioned endpoints; add pagination/limits and avoid expensive full-market scans per request.
- Initially support public read-only market data, opportunity list/detail, filters/sorting, exchange health, watchlist/preferences when safely persisted, and paper-trade simulation. Add push notifications and account sync only after authentication and abuse/security controls are designed.
- Use TLS in deployment, server-side rate limiting, input validation, safe error responses, and no secrets in logs.
- Separate Android CI from the existing bot CI/deploy path. Android build jobs must not deploy or restart the bot.

## Required implementation sequence
1. Inspect current repository, branch, tests, dependencies, Docker and deployment workflows. Record compatibility risks.
2. Commit this prompt and a design/acceptance checklist to the feature branch.
3. Add a minimal, isolated mobile API with health/readiness endpoints and an explicit API contract. Do not expose fake opportunities or imply verification that has not happened.
4. Add unit tests for API serialization, error states, input validation, freshness, and profit calculations. Reuse existing scanner calculations only where semantics are confirmed.
5. Add the Android project in `android/`: dashboard, opportunity cards/detail, filters/sorting, exchange status, freshness/warnings, settings and offline/error/loading states. Connect to the API via a configurable base URL.
6. Improve scanner depth/fee/network verification only after reviewing existing code and adding regression tests. Keep unknown fees/status as unknown, not zero/verified.
7. Add CI jobs that are independent from production deployment. Run the existing bot tests unchanged plus new API and Android checks.
8. Audit secrets, dependencies, API rate limits, stale data handling, responsive UI/accessibility and documentation.
9. Report exactly what was implemented, what tests actually ran and passed, what remains, and any deployment steps. Stop before merge/deploy/restart.

## Acceptance criteria
- The Telegram bot's existing source behavior and production deployment are untouched.
- Android and API are separate, independently buildable components.
- API never requires Telegram credentials.
- No API keys/private keys are required for public scanning; none are embedded in the APK.
- Opportunity math clearly separates raw spread from estimated net profit and identifies assumptions/unknown costs.
- Data timestamps and stale/error states are visible.
- Transfer/network/contract checks never claim verified without positive evidence.
- CI for the new components is isolated from production deployment.
- Tests and build outcomes are reported honestly.
