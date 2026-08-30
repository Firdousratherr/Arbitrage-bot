# ⚡ Arbitrage Terminal

A complete architectural replacement for the Telegram cross-exchange arbitrage bot.

## Pipeline

`Telegram → user configuration → parallel exchange adapters → normalization → deterministic arbitrage engine → validation/filtering/ranking → optional AI → immutable scan snapshot → Telegram UI`

All selected exchanges are equal. LBank and XT are first-class adapters. Exchange-specific recovery is contained inside adapters; one exchange failure becomes a partial scan rather than a false zero-result. Scans are user-triggered and there is no background arbitrage alert loop.

## Key behavior

- Every accepted pair from a scan is retained in its snapshot and paginated without rescanning.
- Raw gap and estimated net profit remain separate.
- Freshness, liquidity, fee availability and confidence are explicit.
- Filter rejections are stored for Debug Coin analysis.
- AI defaults to OFF and is per-user; it never supplies deterministic market data or silently changes settings.
- `/aiprobe` checks configured provider connectivity/authentication/response without bypassing WAF/CAPTCHA/access controls.
- Simulation mode is the default and no trade execution subsystem is included.
- SQLite WAL with normalized user, exchange, scanner and AI configuration plus scan history.
- Docker runs as a non-root user and includes a database health check.

## Run

Copy `.env.example` to `.env`, set `TELEGRAM_BOT_TOKEN`, then `pip install -e .` and run `python -m arbitrage_terminal.main`.

## Tests

Run `pytest -q`. The rebuilt suite covers normalization, deterministic calculations, partial exchange isolation, successful zero-result behavior, AI OFF isolation and dry-run invariants.
