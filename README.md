# ⚡ Arbitrage Terminal

A scanner-first Telegram cross-exchange arbitrage terminal.

## Pipeline

`Telegram → user configuration → parallel exchange adapters → normalization → deterministic arbitrage engine → validation/filtering/ranking → optional AI → immutable scan snapshot → Telegram UI`

All selected exchanges are equal. LBank and XT are first-class adapters. Exchange-specific recovery is contained inside adapters; one exchange failure becomes a partial scan rather than a false zero-result. Scans are user-triggered and there is no background arbitrage alert loop.

## Key behavior

- Every accepted pair from a scan is retained in its snapshot and paginated without rescanning.
- Raw gap and estimated net profit remain separate.
- Freshness, liquidity, fee availability and confidence are explicit.
- Filter rejections are stored for Debug Coin analysis.
- Strict validation checks compatible deposit/withdrawal networks and contract/address metadata when available; Loose mode explicitly bypasses those checks.
- AI defaults to OFF and is per-user; it analyzes only the supplied deterministic snapshot and never invents missing market facts.
- `/aiprobe` checks configured provider connectivity/authentication/response without bypassing WAF/CAPTCHA/access controls.
- Simulation mode is the default and no trade execution subsystem is included.
- SQLite WAL with normalized user, exchange, scanner and AI configuration plus scan history.
- Per-user scan locking prevents accidental concurrent scans; exchange and transfer operations have bounded timeouts.
- Docker runs as a non-root user and includes a database health check.

## Telegram commands

- `/dashboard` — open the main dashboard
- `/scan` — open the scanner
- `/results` — scan history and saved results
- `/exchanges` — select exchanges
- `/filters` — inspect active scanner filters
- `/settings` — validation and AI settings
- `/status` — current selected-exchange/runtime status
- `/diagnostics` — diagnostics from the latest saved scan
- `/ai` — AI result mode
- `/vipkey` — activate VIP access
- `/help` — command reference
- Admin: `/genkey KEY DAYS|lifetime`, `/aiprobe`

## Run

Copy `.env.example` to `.env`, set `TELEGRAM_BOT_TOKEN`, then `pip install -e .` and run `python -m arbitrage_terminal.main`.

## Tests

Run `pytest -q`. The suite covers normalization, deterministic calculations, partial exchange isolation, zero-result behavior, validation modes, AI isolation, exchange isolation and dry-run invariants.
