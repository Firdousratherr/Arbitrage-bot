
# Telegram Cross-Exchange Arbitrage Scanner

An async Python 3.11+ Telegram bot foundation for VIP-gated, cross-exchange arbitrage alerts. It uses `python-telegram-bot`, `ccxt.async_support`, `aiosqlite`, and REST polling designed for a small cloud VM.

This repository implements the first runnable milestone: registration, single-use VIP keys, role-aware access, SQLite auditing, filters, loose mode, paper trading, leaderboard, admin commands, pluggable exchange adapters, and a bounded-concurrency scanner. Live trading is intentionally not implemented.

## Project Structure

```text
app/
	config.py              Environment-backed settings
	db.py                  SQLite schema and persistence operations
	filters.py             Filter defaults and opportunity matching
	handlers.py            Registration, VIP, user, paper, and admin commands
	logging_setup.py       Rotating console/file logging
	main.py                Telegram lifecycle and scanner alert fan-out
	scanner.py             Global ticker scan and opportunity calculation
	exchanges/
		base.py              Adapter and opportunity protocols
		ccxt_adapter.py      Generic async CCXT adapter
		registry.py          Supported exchange registry
pyproject.toml
.env.example
```

## Database Schema

The database is created automatically at `DATABASE_PATH`:

| Table | Purpose |
| --- | --- |
| `users` | Telegram identity, email, selected exchanges, VIP state, filters, bans, leaderboard privacy |
| `vip_keys` | Single-use key, creator, redemption, expiry, and status |
| `user_actions` | Rolling last 20 actions per user |
| `admin_actions` | Immutable admin accountability log |
| `opportunities` | Compact alert snapshots used by Details and paper trading |
| `paper_trades` | Simulated trade size, P&L, period, and timestamp |
| `exchange_overrides` | Manual network/deposit/withdrawal metadata extension point |
| `stats` | Small persistent counters such as scans and alerts |

SQLite is configured with WAL mode, foreign keys, a bounded cache, and additive `CREATE TABLE IF NOT EXISTS` initialization.

## Setup

```bash
cp .env.example .env
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
```

Set `TELEGRAM_BOT_TOKEN`, a comma-separated `ADMIN_IDS`, and `ADMIN_SECRET_KEY` in `.env`. An allowlisted admin must run `/admin 8767` before using admin commands. API credentials are optional for public ticker scanning; add exchange-specific credentials only when private endpoints are required.

Run with:

```bash
.venv/bin/arbitrage-bot
```

The bot initializes SQLite, creates async CCXT clients for enabled exchanges, starts a scanner task, and closes all sessions during shutdown. Use `/start` to register. After unlocking with `/admin 8767`, admins create chosen VIP keys with `/genkey YOUR_KEY 30` or `/genkey YOUR_KEY lifetime`; the user redeems that key during registration.

## AWS EC2 Deployment

This repository includes a Docker image and a `systemd` service template for an Ubuntu EC2 instance. Use an instance with at least 1 GB RAM, attach persistent storage for SQLite, and restrict SSH access in the security group.

On the server:

```bash
sudo apt-get update
sudo apt-get install -y docker.io git
sudo systemctl enable --now docker
sudo git clone <your-repository-url> /opt/arbitrage-bot
cd /opt/arbitrage-bot
sudo cp .env.example .env
sudo nano .env
sudo mkdir -p data logs
sudo docker build -t arbitrage-bot:latest .
sudo cp deploy/aws/arbitrage-bot.service /etc/systemd/system/arbitrage-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now arbitrage-bot
```

Put the real `TELEGRAM_BOT_TOKEN` and `ADMIN_TELEGRAM_IDS` in `/opt/arbitrage-bot/.env` on the server. Never commit `.env` or paste the token into `.env.example`. Check operation with `sudo systemctl status arbitrage-bot` and `sudo journalctl -u arbitrage-bot -f`. Back up `/opt/arbitrage-bot/data/arbitrage.sqlite3` regularly.

## Commands

Users get `/status`, `/exchanges`, `/filters`, `/myfilters`, `/scan`, `/vipkey`, `/setmaxresults`, filter setters, `/loosemode`, `/setfeeadjusted`, `/papertrade`, `/paperstats`, and `/leaderboard` after registration or VIP activation as applicable. `/vipkey YOUR_KEY` redeems a manually chosen VIP key after registration. `/setmaxresults N` limits each user's alerts and manual scan results to the top N opportunities by net profit. Admins get key management including `/listkeys` and `/extendvip`, user auditing, bans, broadcasts, CSV export, statistics, `/health`, and `/memstatus`.

Details buttons are intentionally on-demand. The base scan retains only best bid/ask, volume, and a compact metadata snapshot; it does not retain full order books for all symbols.

## Safety and Operational Notes

- Loose mode is explicitly labeled as unverified and skips transfer verification.
- Normal alerts require deposit/withdrawal metadata and a matching network plus contract address from both exchange APIs.
- Exchange failures are isolated per adapter and bounded by `MAX_EXCHANGE_CONCURRENCY`.
- The scanner uses batch `fetch_tickers()` where supported and discards raw responses after extracting compact ticker fields.
- Paper trading is simulated only and never submits an exchange order.
- Transfer metadata is fetched through each exchange's CCXT currency endpoint for one exchange at a time; missing metadata fails closed unless loose mode is explicitly enabled. The `exchange_overrides` table remains available for a future admin-managed override resolver.
- Before production use, add deployment secrets management, integration tests with mocked CCXT responses, alert cooldown/daily-cap enforcement, and a review of each exchange's network metadata semantics. Never treat an arbitrage alert as an instruction to trade.

## Verification

The package compiles with the project-local Python interpreter, imports successfully with installed dependencies, and includes a SQLite smoke path covering registration, VIP redemption, active access, and rolling audit retention.

