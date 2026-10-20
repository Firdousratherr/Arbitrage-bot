# Android Arbitrage Scanner: Research and Acceptance Blueprint

## Product research snapshot (checked 19 October 2026)

Sources:
- ArbiHunt home page: https://www.arbihunt.com/
- How it works: https://www.arbihunt.com/how-it-works
- Google Play listing: https://play.google.com/store/apps/details?id=com.digitalwarriors.arbihunt
- Dashboard guide: https://www.arbihunt.com/knowledge-base/guides/reading-the-dashboard
- Opportunity details guide: https://www.arbihunt.com/knowledge-base/guides/understanding-the-opportunity-details
- Getting started FAQ: https://www.arbihunt.com/knowledge-base/help/getting-started-faq

ArbiHunt's public product materials describe an information-only scanner, not an auto-trader. They highlight cross-exchange spot scanning, executable order-book pricing, spread versus net profit, trading fees on both legs, withdrawal fees, liquidity/depth, matching transfer networks and active deposit/withdrawal status, contract verification, live/stale timestamps, route details, filters, sorting, watchlists, account sync, mobile/web access and community features. Published exchange totals and refresh intervals vary across its public pages/listing, so they must be treated as product claims that may change, not promises for our implementation.

## Current repository findings

- Repository: Firdousratherr/Arbitrage-bot, default branch `main`.
- Existing application is a Python Telegram bot using Python 3.11+, python-telegram-bot, CCXT async support and SQLite.
- Existing scanner supports a limited configured exchange registry and can calculate a best headline ticker spread.
- Current scanner's opportunity metadata marks results `headline_only: true`; its current `net_profit` calculation subtracts estimated taker fees but does not currently subtract live withdrawal fees or model executable multi-level order-book depth. It also records `transfer_verified: false` and `executable_complete: false`.
- Therefore the mobile API must not label current values as verified net profit or executable opportunity. It must expose clear warnings until order-book sizing, live fee and network verification are implemented and tested.
- The existing Dockerfile installs and starts the Telegram bot. Do not repurpose it for the mobile API or Android build.
- Existing Settings requires Telegram configuration, so the mobile API must not call that settings constructor. It must use separate environment variables and an independent process/database.
- Existing project history includes an explicit preference to avoid automatic Docker rebuilds/deployments and wait for approval. No merge/deploy/restart is authorized by this task.

## Product specification

### Dashboard
- Refreshing opportunity list with visible last-update time and stale-data banner.
- KPIs: current opportunity count, highest gross spread, top provisional fee-adjusted spread, last scan duration/refresh age.
- Responsive card list showing pair, buy venue, sell venue, buy ask, sell bid, gross spread, provisional fee-adjusted spread, volume indicators and confidence/data-quality labels.
- No fabricated data; empty/error/loading states are first-class.

### Opportunity detail
- Two exchange legs with links to official venue pages where valid links are known.
- Gross spread formula and every deduction shown separately.
- Data age, bid/ask source, liquidity/depth at selected trade size, estimated slippage.
- Network compatibility, deposits/withdrawals enabled, withdrawal fee and asset contract addresses when authoritative data is available.
- Unknown data stays unknown. Block an “actionable/verified” label when critical transfer or asset identity information is missing.
- Prominent risk notice that prices move, orders may not fill and profit is not guaranteed.

### Discovery and controls
- Search pair/symbol; filter exchange pair, minimum gross spread, minimum net estimate, minimum quote volume and transfer network.
- Sort by net estimate, gross spread, liquidity and freshness.
- Watchlist and saved preferences, initially stored locally or via authenticated API only when identity/auth design is implemented.
- Exchange health/status with disabled/unavailable reasons.
- Manual refresh plus bounded polling; respect exchange rate limits.

### Notifications and optional future features
- Local/push notifications for matching opportunities, with deduplication and cooldown.
- Paper-trade simulation and performance history, clearly labelled simulated.
- Optional account sync, subscription plans and community chat are later milestones requiring security, privacy, moderation and operational planning. Do not claim they are implemented in the MVP.

## Engineering architecture

- Preserve `app/` and its Telegram entrypoint.
- `mobile_api/`: separate FastAPI service; separate environment and SQLite database; never requires `TELEGRAM_BOT_TOKEN`.
- `android/`: Kotlin/Jetpack Compose project with a configurable API base URL.
- `docs/`: prompt, architecture and runbook.
- New CI workflows must build/test only and must not deploy the Telegram bot.

## Release gates

1. Existing Telegram tests pass without regression.
2. API tests cover serialization, validation, freshness and errors.
3. Android debug build and tests pass.
4. Scanner math tests validate fees, depth, precision and missing-data behavior.
5. Security review checks secrets, TLS configuration, API rate limiting, input validation and logs.
6. No false “verified” status or guaranteed-profit language.
7. Manual approval required before merge to main or any deployment/restart.
