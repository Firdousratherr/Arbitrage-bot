from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .scanner import MarketScanner


def _csv_env(name: str, default: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, default).split(",") if part.strip()]


scanner = MarketScanner(
    exchange_ids=_csv_env("MOBILE_API_EXCHANGES", ",".join(MarketScanner.DEFAULT_EXCHANGES)),
    quote_assets=_csv_env("MOBILE_API_QUOTE_ASSETS", "USDT,USDC"),
    scan_interval=int(os.getenv("MOBILE_API_SCAN_INTERVAL_SECONDS", "30")),
    concurrency=int(os.getenv("MOBILE_API_REQUEST_CONCURRENCY", "6")),
    max_candidates=int(os.getenv("MOBILE_API_MAX_CANDIDATES", "40")),
    max_notional_usd=float(os.getenv("MOBILE_API_MAX_NOTIONAL_USD", "500")),
    estimated_taker_fee=float(os.getenv("MOBILE_API_ESTIMATED_TAKER_FEE", "0.001")),
    min_24h_quote_volume=float(os.getenv("MOBILE_API_MIN_24H_QUOTE_VOLUME", "5000")),
    stale_after=int(os.getenv("MOBILE_API_STALE_AFTER_SECONDS", "90")),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await scanner.start()
    try:
        yield
    finally:
        await scanner.stop()


app = FastAPI(
    title="ArbiScope Mobile Market API",
    version="0.1.0",
    description="Read-only crypto market scanning API. It does not place trades or withdraw funds.",
    lifespan=lifespan,
)

_origins = _csv_env("MOBILE_API_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "arbiscope-mobile-api", **scanner.status()}


@app.get("/api/v1/status")
async def status() -> dict[str, Any]:
    return scanner.status()


@app.get("/api/v1/exchanges")
async def exchanges() -> dict[str, Any]:
    return {"items": scanner.list_exchanges(), "updated_at": scanner.updated_at}


@app.get("/api/v1/opportunities")
async def opportunities(
    q: str = "",
    sort: str = Query(default="profit", pattern="^(profit|spread|liquidity|latest)$"),
    min_spread: float = Query(default=0.0, ge=0.0, le=1000.0),
    min_liquidity: float = Query(default=0.0, ge=0.0, le=1_000_000_000.0),
    quote: str = "",
    exchanges_filter: str = Query(default="", alias="exchanges"),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    items = scanner.list_opportunities(
        q=q,
        sort=sort,
        min_spread=min_spread,
        min_liquidity=min_liquidity,
        quote=quote,
        exchanges=[name for name in exchanges_filter.split(",") if name.strip()],
        limit=limit,
    )
    return {
        "items": items,
        "updated_at": scanner.updated_at,
        "stale": scanner.is_stale(),
        "scan_duration_ms": scanner.scan_duration_ms,
        "exchange_status": scanner.list_exchanges(),
        "count": len(items),
    }


@app.get("/api/v1/opportunities/{opportunity_id}")
async def opportunity_detail(opportunity_id: str) -> dict[str, Any]:
    item = scanner.get_opportunity(opportunity_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Opportunity not found or no longer in the current scan")
    return item


@app.get("/api/v1/triangular")
async def triangular(
    exchange: str = "",
    q: str = "",
    min_profit_pct: float = Query(default=0.0, ge=0.0, le=1000.0),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    items = scanner.list_triangular(exchange=exchange, q=q, min_profit_pct=min_profit_pct, limit=limit)
    return {"items": items, "updated_at": scanner.updated_at, "stale": scanner.is_stale(), "count": len(items)}


@app.post("/api/v1/refresh", include_in_schema=False)
async def refresh() -> dict[str, Any]:
    # Explicitly limited to a serialized scan cycle; it does not place orders.
    await asyncio.wait_for(scanner.refresh(), timeout=120)
    return scanner.status()
