# Mobile API (separate read-only service)

This FastAPI service is intentionally independent of the Telegram bot process. It reads public exchange market data only, does not load the Telegram environment, and does not place exchange orders or withdrawals.

## Local development

From the repository root:

```bash
python -m pip install -e .
python -m pip install -r mobile_api/requirements.txt
MOBILE_API_DATABASE_PATH=./data/mobile-api.sqlite3 uvicorn mobile_api.app:app --host 127.0.0.1 --port 8000
```

Open `/docs` for the generated OpenAPI spec and `/healthz` for readiness and stale-scan status.

Environment settings:
- `MOBILE_API_DATABASE_PATH`: separate SQLite path; never point it to the Telegram bot database in production.
- `MOBILE_API_EXCHANGES`: comma-separated exchange IDs supported by the CCXT registry.
- `MOBILE_API_SCAN_INTERVAL_SECONDS`: requested delay between scan cycles (minimum 15 seconds; long scans are not overlapped).
- `MOBILE_API_MAX_EXCHANGE_CONCURRENCY`: maximum parallel exchange requests used by the scanner.
- `MOBILE_API_RATE_LIMIT_PER_MINUTE`: basic per-client-IP limit for API calls (default 120).

## Main endpoints

- `GET /healthz`: service status, last successful scan and stale-data indicators.
- `GET /api/v1/exchanges`: configured exchanges and latest per-exchange scan diagnostics when available.
- `GET /api/v1/opportunities`: cached opportunity list, with `limit`, `min_spread_pct`, `min_profit_pct`, `min_volume`, `search`, `buy_exchange`, `sell_exchange` and `sort_by`.
- `GET /api/v1/opportunity-details?symbol=BTC%2FUSDT&buy_exchange=kucoin&sell_exchange=okx&trade_size_quote=100`: fresh top-of-book/depth walk for the given route, explicit fee metadata when available, and conservative network/contract checking. The requested amount is denominated in the pair's quote currency.

## Calculation and data-quality contract

List results reuse the Telegram scanner's existing ticker estimates. Those figures are labelled `headline_only` and may rely on that scanner's fallback fee assumption; do not treat the list estimate as executable net profit.

The detail endpoint independently fetches up to 25 levels on both legs. It walks asks to estimate how much base asset a quote-currency budget can buy, then walks bids for the same quantity. It calculates fee-adjusted trading P&L only when both books cover the requested size and both venues publish a usable taker fee rate. If fee or depth data is missing, P&L is unknown rather than guessed.

Transfer-route verification is conservative: an enabled withdrawal on the buy venue, enabled deposit on the sell venue, a matching normalized network ID and an exact matching contract address must all be present. Missing metadata is not approved. A returned route does not imply the withdrawal fee is known, and `full_route_net_profit` is intentionally null until transfer costs can be included.

## Security and deployment

The basic per-IP limiter is process-local protection, not a production abuse-control system. Before exposing the API publicly:
- terminate TLS at a trusted reverse proxy or load balancer;
- use a shared gateway/Redis rate limiter, request-size limits and production monitoring;
- use persistent storage for the mobile API's own database;
- validate proxy/IP configuration; do not trust arbitrary forwarded headers;
- keep API errors sanitized and avoid logging sensitive information;
- configure a real API domain in the Android release build;
- configure release signing and validate exchange market/fee/network semantics in live tests.

Do not copy or reuse the Telegram bot's production environment variables, database volume, Docker entrypoint or service unit. Build and run `Dockerfile.mobile` as a separate service. This initial API is read-only and public-data-only but should not be considered production internet-ready until the security gates above are completed.
