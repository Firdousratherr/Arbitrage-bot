from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app.db import Database
from app.exchanges.registry import build_exchanges
from app.scanner import Scanner

logging.basicConfig(level=os.getenv("MOBILE_API_LOG_LEVEL", "INFO"))
logger = logging.getLogger("mobile_api")

SCAN_INTERVAL_SECONDS = max(15, int(os.getenv("MOBILE_API_SCAN_INTERVAL_SECONDS", "30")))
DATABASE_PATH = os.getenv("MOBILE_API_DATABASE_PATH", "/tmp/mobile-arbitrage.sqlite3")
EXCHANGE_NAMES = [
    value.strip().lower()
    for value in os.getenv(
        "MOBILE_API_EXCHANGES",
        "xt,kucoin,gateio,mexc,okx,htx,kraken,bitget,bitrue,lbank,coinbase,bitfinex,phemex,cryptocom,poloniex",
    ).split(",")
    if value.strip()
]

db: Database | None = None
exchanges: dict[str, Any] = {}
scanner: Scanner | None = None
latest_opportunities: list[Any] = []
last_scan_at: str | None = None
last_scan_error: str | None = None
scan_task: asyncio.Task | None = None


def _public_credentials_provider(_exchange: str) -> dict[str, str]:
    # Public read-only market data only; never load bot credentials.
    return {}


def _opportunity_payload(item: Any) -> dict[str, Any]:
    metadata = item.metadata or {}
    return {
        "symbol": item.symbol,
        "buy_exchange": item.buy_exchange,
        "sell_exchange": item.sell_exchange,
        "buy_ask": item.buy_price,
        "sell_bid": item.sell_price,
        "gross_spread_pct": round(item.raw_spread, 6),
        "provisional_fee_adjusted_spread_pct": round(item.net_profit, 6),
        "buy_quote_volume_24h": item.volume_buy,
        "sell_quote_volume_24h": item.volume_sell,
        "observed_at": metadata.get("observed_at"),
        "coverage_complete": bool(metadata.get("coverage_complete", False)),
        "transfer_verified": False,
        "order_book_depth_verified": False,
        "estimate_type": "headline_only",
        "warnings": [
            "Provisional estimate: ticker values only; multi-level executable order-book sizing is not implemented.",
            "Withdrawal/network fees and deposit/withdrawal availability are not deducted or verified.",
            "Price gaps can close quickly; this is not a guaranteed profit or trade instruction.",
        ],
    }


async def _scan_loop() -> None:
    global latest_opportunities, last_scan_at, last_scan_error
    assert scanner is not None
    while True:
        started = datetime.now(UTC)
        try:
            results = await scanner.run_cycle(
                require_matching_user=False,
                exchange_names=set(exchanges),
            )
            latest_opportunities = results
            last_scan_at = datetime.now(UTC).isoformat()
            last_scan_error = None
            logger.info("mobile scan completed: %s provisional opportunities", len(results))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_scan_error = f"{type(exc).__name__}: scan failed"
            logger.exception("mobile scan cycle failed")
        elapsed = (datetime.now(UTC) - started).total_seconds()
        await asyncio.sleep(max(1.0, SCAN_INTERVAL_SECONDS - elapsed))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global db, exchanges, scanner, scan_task
    db = Database(DATABASE_PATH)
    await db.connect()
    exchanges = build_exchanges(EXCHANGE_NAMES, _public_credentials_provider)
    scanner = Scanner(
        db=db,
        exchanges=exchanges,
        interval=SCAN_INTERVAL_SECONDS,
        concurrency=max(1, int(os.getenv("MOBILE_API_MAX_EXCHANGE_CONCURRENCY", "3"))),
    )
    if len(exchanges) >= 2:
        scan_task = asyncio.create_task(_scan_loop())
    else:
        logger.error("mobile API scanner needs at least two supported exchanges")
    try:
        yield
    finally:
        if scan_task:
            scan_task.cancel()
            try:
                await scan_task
            except asyncio.CancelledError:
                pass
        await asyncio.gather(*(exchange.close() for exchange in exchanges.values()), return_exceptions=True)
        if db:
            await db.close()


app = FastAPI(
    title="Arbitrage Mobile API",
    version="0.1.0",
    description="Read-only mobile API. Opportunity figures are provisional until depth and transfer checks are implemented.",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": "mobile-api",
        "scanner_configured": len(exchanges) >= 2,
        "exchange_count": len(exchanges),
        "last_scan_at": last_scan_at,
        "last_scan_error": last_scan_error,
    }


@app.get("/api/v1/exchanges")
async def list_exchanges():
    return {
        "exchanges": [{"id": name, "status": "configured"} for name in sorted(exchanges)],
        "note": "Configured does not mean currently healthy; per-exchange scan diagnostics are not yet exposed.",
    }


@app.get("/api/v1/opportunities")
async def list_opportunities(
    limit: int = Query(default=50, ge=1, le=200),
    min_spread_pct: float = Query(default=0, ge=0, le=10000),
    sort_by: str = Query(default="provisional_profit", pattern="^(provisional_profit|gross_spread|volume)$"),
):
    if not last_scan_at:
        if last_scan_error:
            raise HTTPException(status_code=503, detail="Scanner has not completed a successful cycle yet.")
        return JSONResponse(status_code=503, content={"detail": "Scanner is warming up; no successful scan is available yet."})

    results = [item for item in latest_opportunities if item.raw_spread >= min_spread_pct]
    if sort_by == "gross_spread":
        results.sort(key=lambda item: item.raw_spread, reverse=True)
    elif sort_by == "volume":
        results.sort(key=lambda item: min(item.volume_buy, item.volume_sell), reverse=True)
    else:
        results.sort(key=lambda item: item.net_profit, reverse=True)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "last_scan_at": last_scan_at,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "count": len(results),
        "data_quality": "headline_only",
        "items": [_opportunity_payload(item) for item in results[:limit]],
        "warnings": [
            "Values are provisional ticker-based estimates, not verified executable net profit.",
            "Withdrawal fees, network availability, token contracts and multi-level order-book depth are not yet included.",
        ],
    }


@app.get("/api/v1/opportunities/{symbol}")
async def get_opportunity(symbol: str):
    normalized = symbol.strip().upper()
    for item in latest_opportunities:
        if item.symbol.upper() == normalized:
            return _opportunity_payload(item)
    raise HTTPException(status_code=404, detail="No current opportunity found for this symbol.")
