# ArbiScope Mobile API

Independent read-only public market-data API for the Android app. It is deliberately separate from the Telegram bot and has its own Dockerfile and requirements. It never places orders, withdraws funds, connects a wallet, or requests exchange API credentials.

## Local run

Use Python 3.11+.

~~~bash
cd mobile-api
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

The first startup loads exchange market metadata and then periodically refreshes public tickers. Each refresh prices only a bounded number of candidates against the visible order book. Configure supported exchanges in MOBILE_API_EXCHANGES; starting with the default 8 venues is gentler on exchange rate limits than enabling every exchange at once.

## Docker

~~~bash
docker build -t arbiscope-mobile-api .
docker run --rm -p 8000:8000 --env-file .env arbiscope-mobile-api
~~~

Deploy this service as a separate service/container. Do not replace the Telegram bot Dockerfile, service, database, or environment variables.

## API endpoints

- GET /health
- GET /api/v1/status
- GET /api/v1/exchanges
- GET /api/v1/opportunities?q=BTC&sort=profit&min_spread=0.5&min_liquidity=1000&quote=USDT&limit=100
- GET /api/v1/opportunities/{id}
- GET /api/v1/triangular?exchange=okx&min_profit_pct=0.01
- POST /api/v1/refresh (diagnostics/manual refresh; never submits trades)

## Important cost and safety limitations

Prices and visible depth are sampled at scan time and may change before execution. Trading-fee values come from exchange market metadata when available; otherwise the API uses MOBILE_API_ESTIMATED_TAKER_FEE and clearly labels it as a fallback estimate. Net transfer cost is not assumed to be zero.

Many exchanges do not expose current deposit/withdrawal state, withdrawal fees, or reliable contract identity through anonymous public endpoints. For this reason this API returns transfer/network/contract verification as unknown and the cross-exchange figure is explicitly a pre-transfer estimate. Users must check supported networks, token contracts, wallet status, withdrawal fee, minimum withdrawal, deposit confirmations and their own account fee tier directly at both venues. The service does not mark a route verified merely because the ticker symbols match.

Triangular results use ticker top-of-book conversions and fee assumptions, not a full multi-leg depth simulation. They are estimates only; the UI should preserve that warning.

## Configuration

See .env.example. Enable more venues only after checking regional availability, exchange ToS, rate limits, API status and whether spot market data works without authentication. A failed exchange is isolated from other venues and appears in /api/v1/exchanges.
