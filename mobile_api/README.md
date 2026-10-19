# Mobile API (isolated service)

This service is separate from the Telegram bot process. It uses public exchange market data only and does not read exchange credentials or require `TELEGRAM_BOT_TOKEN`.

## Local development

From the repository root:

```bash
python -m pip install -e .
python -m pip install -r mobile_api/requirements.txt
MOBILE_API_DATABASE_PATH=./data/mobile-api.sqlite3 uvicorn mobile_api.app:app --host 127.0.0.1 --port 8000
```

Open `/docs` for the OpenAPI explorer and `/healthz` for health status.

## Important current limitation

The existing shared scanner labels its opportunities `headline_only`. This API preserves that limitation explicitly: it does **not** claim live withdrawal-fee deduction, matched network verification, contract verification, or multi-level order-book execution. Those features must be implemented and tested before describing opportunities as actionable net profit.

Do not expose this initial API directly to the public internet. Authentication, server-side rate limiting, TLS/reverse-proxy configuration, per-exchange health diagnostics and production persistence must be completed before public deployment. Use a separate service/container and never replace the existing Telegram bot Docker entrypoint.
