
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

## User Commands

Start with `/start`, enter a valid email, select at least two exchanges, and tap **Done**. Enter a VIP key during registration or use `/vipkey YOUR_KEY` later.

| Command | Purpose and example |
| --- | --- |
| `/help` | Show the in-bot command guide. |
| `/status` | Show VIP status, selected exchanges, and key settings. |
| `/vipkey YOUR_KEY` | Redeem a VIP key after registration. |
| `/exchanges` | Open the exchange selection menu; select at least two exchanges. |
| `/scan` | Run an immediate scan of the selected exchanges. |
| `/filters` | Show filter instructions. |
| `/myfilters` | Show current filter values. |
| `/resetfilters` | Restore default filters. |
| `/setmaxresults 10` | Limit displayed alerts and scan results. |
| `/setminprofit 1` | Require at least 1% estimated profit. |
| `/setmaxprofit 50` | Ignore opportunities above 50%. |
| `/setminspread 0.5` | Require at least 0.5% raw spread. |
| `/setmaxspread 20` | Ignore unusually large spreads. |
| `/setminvolume 50000` | Require minimum 24h quote volume. |
| `/setmintradesize 10` | Set minimum paper-trade size. |
| `/setmaxtradesize 1000` | Set maximum paper-trade size and button size. |
| `/setmaxslippage 2` | Set maximum slippage preference. |
| `/setnetworkfee 1` | Set estimated network fee. |
| `/setalertfreq 300` | Set alert cooldown in seconds. |
| `/setdailycap 50` | Set the daily alert limit. |
| `/setquotecurrency USDT` | Use `USDT`, `USDC`, or `BTC`. |
| `/watchlist add BTC/USDT` | Include only selected symbols. Use `remove` to remove one. |
| `/blacklist add DOGE/USDT` | Ignore a symbol. Use `remove` to remove one. |
| `/loosemode on` | Allow unverified transfer routes. Use only with caution. |
| `/setfeeadjusted on` | Enable fee-adjusted filtering. |
| `/pause` or `/resume` | Pause or resume automatic alerts. |
| `/papertrade OPPORTUNITY_ID SIZE` | Record a simulation manually; result buttons are easier. |
| `/paperstats` | View simulated trade statistics. |
| `/leaderboard` | View this period's paper-trade ranking. Add `alltime` for all history. |

Coin alerts include buy/sell prices, gross spread, live fee details, net estimate, liquidity, order books, and a **Paper Trade** button. Paper trading never places a real exchange order.

## Admin Commands

The Telegram user must be listed in `ADMIN_TELEGRAM_IDS`. Run `/admin 8767` once per session before using admin commands.

| Command | Purpose and example |
| --- | --- |
| `/genkey VIP2026 30` | Create a manually chosen 30-day VIP key. Use `lifetime` instead of `30` for no expiry. |
| `/listkeys [status]` | List keys; status can be `unused`, `active`, `expired`, or `revoked`. |
| `/revokekey KEY` | Revoke a key that should no longer work. |
| `/extendvip USER_ID 30` | Add 30 days to a user's VIP access. |
| `/grantvip USER_ID [DAYS]` | Grant lifetime VIP or a fixed number of days. |
| `/revokevip USER_ID` | Remove VIP access. |
| `/userinfo USER_ID_OR_USERNAME` | Inspect a user's account and recent actions. |
| `/listusers [all\|vip\|pending\|banned]` | List users by status. |
| `/ban USER_ID REASON` | Ban a user and record the reason. |
| `/unban USER_ID` | Remove a ban. |
| `/broadcast MESSAGE` | Send a message to active VIP users. |
| `/stats` | Show user, scan, and alert counts. |
| `/health` | Check ticker access for every active exchange. |
| `/exportusers` | Download the user CSV export. |
| `/memstatus` | Show process memory usage. |

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

