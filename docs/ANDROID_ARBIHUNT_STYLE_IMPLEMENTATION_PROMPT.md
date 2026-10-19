# ArbiHunt-Inspired Android Arbitrage Scanner — Implementation Prompt

## Purpose
Build an original Android cryptocurrency arbitrage scanner inspired by the publicly documented feature set of ArbiHunt. Reuse the repository only where safe and appropriate. Do not copy ArbiHunt branding, logo, source code, exact written copy, or proprietary assets. This project must be independently implemented and must not claim to be affiliated with ArbiHunt.

## Research basis
Public documentation reviewed on 2026-10-19:
- https://www.arbihunt.com/
- https://www.arbihunt.com/how-it-works
- https://www.arbihunt.com/knowledge-base/guides/understanding-the-opportunity-details
- https://www.arbihunt.com/knowledge-base/guides/reading-the-dashboard
- https://www.arbihunt.com/knowledge-base/guides/how-to-execute-an-arbitrage-trade
- https://www.arbihunt.com/knowledge-base/fees-and-networks/withdrawal-fees-explained
- https://www.arbihunt.com/knowledge-base/fees-and-networks/choosing-the-right-network
- https://www.arbihunt.com/knowledge-base/guides/how-to-trade-triangular-arbitrage
- https://www.arbihunt.com/knowledge-base/help/getting-started-faq

Feature set to approximate with original design and implementation:
1. Live cross-exchange opportunities, exchange/market coverage status and freshness indicators.
2. Search, sorting, filters for spread, liquidity, quote currency and selected exchanges.
3. Opportunity detail page with buy/sell legs, executable bid/ask-based numbers, trade size, trading-fee assumptions, gross spread, estimated proceeds, known costs, and calculation explanation.
4. Token/transfer-route safety panel: network matching, withdrawal/deposit status and contract addresses ONLY where reliable data is available; otherwise show UNKNOWN / VERIFY MANUALLY and never imply verification.
5. Triangular arbitrage tab for three-leg cycles on one exchange; four-leg routes may be clearly marked unsupported until implemented.
6. Saved opportunities / watchlist, notification preferences and user-selectable refresh interval.
7. Exchange directory with direct exchange market/trading links when the URL is known and safe.
8. Settings, dark-friendly mobile interface, helpful loading/empty/error/offline states and a clear non-financial-advice / no-profit-guarantee notice.
9. Local paper-trade calculator or simulation only; never execute a real trade or request private exchange credentials.
10. CI checks for backend unit tests and Android APK assembly.

## Repository and isolation rules
Repository: https://github.com/Firdousratherr/Arbitrage-bot
Work branch: feat/arbihunt-style-android-app
- The existing Telegram bot code under `app/`, current Dockerfile, current Telegram configuration, bot deployment service, and existing tests must remain unchanged unless an absolutely necessary change is justified and isolated. Prefer no changes to them.
- Add the new mobile backend under `mobile-api/` and the native Android application under `android/`.
- Build/test CI for these components in a new dedicated workflow. It must not deploy or restart any service.
- Do not modify `main`, merge the branch, create a production deployment, publish a store release, or trigger any external deployment. Only commit to the specified feature branch.
- Never put credentials, exchange API keys, bot tokens, signing keys, keystore material or real secrets in Git.
- The app must work in read-only mode using public exchange data. No trading endpoints, order placement, withdrawals, wallet connection or private exchange credentials.
- Use an original project identity, visual style, and copy rather than ArbiHunt assets/trade dress. A high-contrast yellow/charcoal palette is allowed as broad inspiration but must remain visually distinct.

## Backend requirements
Implement a separately runnable FastAPI service using CCXT public market data, with its own requirements file and Dockerfile so it cannot replace the Telegram bot's container or startup command.
- Suggested endpoints: `GET /health`, `GET /api/v1/exchanges`, `GET /api/v1/opportunities`, `GET /api/v1/opportunities/{id}`, `GET /api/v1/triangular`, and `GET /api/v1/status`.
- Make exchange list, refresh interval, quote assets, request concurrency, and limits configurable by environment variables.
- Use bounded concurrent requests, per-exchange timeouts, rate-limit support, cache market metadata, and isolate individual exchange failures.
- Detect cross-exchange candidates from overlapping spot symbols. Use order books (not last-trade price or mid-price) for candidates, calculate fee-aware estimates and cap estimated notional to visible order-book depth.
- Every fee must have a source/confidence marker: exchange-reported market fee, configured estimate, or unknown. Do not silently pretend unknown fees are zero.
- Network and withdrawal-fee verification must fail closed. If a public exchange endpoint cannot supply reliable, current deposit/withdrawal or contract data, return `unknown` and require manual verification; never label a route verified just because tickers match.
- If the current API cannot reliably calculate complete profit after transfer cost, expose this explicitly as an incomplete/pre-transfer estimate and do not label it guaranteed/net after all costs.
- Triangular scan must use same-exchange spot markets, direction-aware bid/ask conversion, trading-fee assumptions for each leg, bounded cycle generation and sensible minimum liquidity/profit checks.
- Return timestamps, data freshness, exchange errors/status, calculation components, opportunity IDs, and route verification status.
- Include unit tests with mocked market/order-book responses for profit math, stale data, missing fees, missing networks, exchange failures, and triangular cycles.
- Provide setup/deployment instructions that never suggest replacing the Telegram bot deployment.

## Android requirements
Create a native Android app using Kotlin and Jetpack Compose in `android/`, with its own Gradle configuration and package identity.
- Main navigation: Scanner, Triangular, Saved, Settings.
- Scanner: responsive list/cards; pair, spread, route, estimated profit or clearly qualified estimate, liquidity and freshness; manual refresh/search/sort/filter.
- Detail: buy and sell exchange legs, prices, order-book depth, size, fee breakdown and sources, transfer-network status/contract identity where available, safety warnings, and exchange-link actions.
- Triangular: same-exchange triangle list, route details/step conversion and fee breakdown; if only 3-leg loops are implemented, disclose it instead of pretending 4-leg support.
- Saved: locally persist watchlist/saved items without storing secrets.
- Settings: API base URL, refresh interval, quote currencies, notification threshold/toggle, basic theme choice, help and risk notice.
- Add local notifications for qualifying opportunities only with user permission, sensible deduplication, and battery-conscious refresh/background behavior. Clearly distinguish in-app refresh from background execution.
- Must handle network loss, API unavailable, empty results, stale data, slow loading, server errors and invalid API base URL gracefully.
- Keep minimum touch targets accessible, support system dark theme, sensible screen reader labels, no horizontal overflow on common Android phone widths.
- Show a plain-language risk notice: prices move, transfers may be delayed, fees/volume can change, estimates are not guarantees, and the app does not trade.
- Never require or collect a wallet seed phrase, private key, withdrawal key or exchange API secret.

## Delivery and acceptance criteria
- Add this prompt itself as documentation in the feature branch before implementation; then follow it as the implementation checklist.
- Include architecture and setup docs for mobile API and Android app, plus example environment file containing placeholders only.
- Add isolated CI for mobile backend tests and Android debug APK build. CI must only test/build, not deploy.
- Run all feasible automated checks. Be honest about tests that could not be run in this environment.
- Inspect the final diff and confirm current Telegram bot files, Dockerfile, deployment and main branch are unchanged.
- Provide branch URL, files added, test/build results, known limitations, and exact steps to produce/install the debug APK. Do not claim the app is production-ready merely because a build succeeds.
