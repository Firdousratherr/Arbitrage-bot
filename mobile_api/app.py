from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.db import Database
from app.exchanges.registry import build_exchanges
from app.scan_diagnostics import get_last_scan_snapshot
from app.scanner import Scanner
from mobile_api.market_math import consume_asks_for_quote, consume_bids_for_base, trading_fee_rate

logging.basicConfig(level=os.getenv("MOBILE_API_LOG_LEVEL", "INFO"))
logger = logging.getLogger("mobile_api")

SCAN_INTERVAL_SECONDS = max(15, int(os.getenv("MOBILE_API_SCAN_INTERVAL_SECONDS", "30")))
DATABASE_PATH = os.getenv("MOBILE_API_DATABASE_PATH", "/tmp/mobile-arbitrage.sqlite3")
RATE_LIMIT_PER_MINUTE = max(30, int(os.getenv("MOBILE_API_RATE_LIMIT_PER_MINUTE", "120")))
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
_request_times: dict[str, deque[float]] = {}


def _public_credentials_provider(_exchange: str) -> dict[str, str]:
    # Public read-only market data only; never load bot credentials.
    return {}


def _scan_age_seconds() -> float | None:
    if not last_scan_at:
        return None
    try:
        timestamp = datetime.fromisoformat(last_scan_at)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - timestamp).total_seconds())
    except (TypeError, ValueError):
        return None


def _opportunity_payload(item: Any) -> dict[str, Any]:
    metadata = item.metadata or {}
    observed_at = metadata.get("observed_at")
    age = None
    if observed_at:
        try:
            parsed = datetime.fromisoformat(observed_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            age = max(0.0, (datetime.now(UTC) - parsed).total_seconds())
        except (TypeError, ValueError):
            pass
    return {
        "symbol": item.symbol,
        "buy_exchange": item.buy_exchange,
        "sell_exchange": item.sell_exchange,
        "buy_ask": item.buy_price,
        "sell_bid": item.sell_price,
        "gross_spread_pct": round(item.raw_spread, 6),
        # Existing Telegram scanner may use an exchange-default or fallback fee.
        # Preserve this as provisional, never as verified executable net profit.
        "provisional_fee_adjusted_spread_pct": round(item.net_profit, 6),
        "buy_quote_volume_24h": item.volume_buy,
        "sell_quote_volume_24h": item.volume_sell,
        "observed_at": observed_at,
        "data_age_seconds": age,
        "coverage_complete": bool(metadata.get("coverage_complete", False)),
        "transfer_verified": False,
        "order_book_depth_verified": False,
        "estimate_type": "headline_only",
        "warnings": [
            "This list is a ticker-level headline estimate; use opportunity details for an order-book depth estimate.",
            "The list estimate may use a fallback trading-fee assumption.",
            "Withdrawal/network fees and deposit/withdrawal availability are not deducted or verified here.",
            "Price gaps can close quickly; this is not a guaranteed profit or trade instruction.",
        ],
    }


def _network_rows(currency_info: dict[str, Any] | None, currency: str) -> list[dict[str, Any]]:
    if not isinstance(currency_info, dict):
        return []
    networks = currency_info.get("networks") or {}
    if not isinstance(networks, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, raw in networks.items():
        value = raw or {}
        if not isinstance(value, dict):
            continue
        info = value.get("info") or {}
        if not isinstance(info, dict):
            info = {}
        network_id = str(value.get("id") or value.get("network") or key)
        contract = (
            value.get("contract")
            or info.get("contractAddress")
            or info.get("contract_address")
            or info.get("contractAddressUrl")
        )
        cost = value.get("fee")
        if cost is None:
            cost = value.get("withdrawFee")
        rows.append({
            "currency": currency,
            "network": str(key),
            "network_id": network_id,
            "contract": str(contract).strip() if contract else None,
            "deposit_enabled": value.get("deposit") if isinstance(value.get("deposit"), bool) else None,
            "withdraw_enabled": value.get("withdraw") if isinstance(value.get("withdraw"), bool) else None,
            "withdrawal_fee": cost if isinstance(cost, (int, float)) else None,
        })
    return rows[:50]


async def _fetch_transfer_networks(adapter: Any, currency: str) -> list[dict[str, Any]]:
    try:
        await asyncio.wait_for(adapter.client.load_markets(), timeout=8)
        currencies = await asyncio.wait_for(adapter.client.fetch_currencies(), timeout=8)
        info = currencies.get(currency)
        if info is None:
            for value in (currencies or {}).values():
                if isinstance(value, dict) and str(value.get("code", "")).upper() == currency.upper():
                    info = value
                    break
        return _network_rows(info, currency)
    except Exception as exc:
        logger.info("Transfer metadata unavailable for %s/%s (%s)", adapter.name, currency, type(exc).__name__)
        return []


def _verified_routes(
    buy_networks: list[dict[str, Any]], sell_networks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for source in buy_networks:
        if source.get("withdraw_enabled") is not True:
            continue
        source_id = "".join(ch for ch in str(source.get("network_id", "")).lower() if ch.isalnum())
        source_contract = source.get("contract")
        if not source_id or not source_contract:
            continue
        for destination in sell_networks:
            if destination.get("deposit_enabled") is not True:
                continue
            destination_id = "".join(ch for ch in str(destination.get("network_id", "")).lower() if ch.isalnum())
            destination_contract = destination.get("contract")
            if not destination_id or source_id != destination_id or not destination_contract:
                continue
            if source_contract.lower() != destination_contract.lower():
                continue
            routes.append({
                "network": source.get("network_id"),
                "withdraw_from_buy_exchange_enabled": True,
                "deposit_to_sell_exchange_enabled": True,
                "contract_match": True,
                "buy_exchange_withdrawal_fee": source.get("withdrawal_fee"),
                "sell_exchange_deposit_confirmed_by_api": True,
                "verified": True,
            })
    return routes[:20]


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
    version="0.2.0",
    description="Read-only mobile API. Ticker estimates are provisional; depth and route checks are separately reported.",
    lifespan=lifespan,
)


@app.middleware("http")
async def simple_ip_rate_limit(request: Request, call_next):
    # In-memory per-process limit for basic abuse protection. Production should
    # use a shared gateway/Redis rate limiter, particularly behind a proxy.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    queue = _request_times.setdefault(ip, deque())
    while queue and now - queue[0] >= 60:
        queue.popleft()
    if len(queue) >= RATE_LIMIT_PER_MINUTE:
        return JSONResponse(
            status_code=429,
            content={"detail": "Request limit reached. Please wait before retrying."},
            headers={"Retry-After": "60"},
        )
    queue.append(now)
    if len(_request_times) > 2048:
        for address in list(_request_times)[:512]:
            if address != ip and (not _request_times[address] or now - _request_times[address][-1] >= 60):
                _request_times.pop(address, None)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_PER_MINUTE)
    return response


@app.get("/healthz")
async def healthz():
    age = _scan_age_seconds()
    stale = age is None or age > max(90, SCAN_INTERVAL_SECONDS * 3)
    configured = len(exchanges) >= 2
    healthy = configured and not stale and not last_scan_error
    return {
        "status": "ok" if healthy else "degraded",
        "service": "mobile-api",
        "scanner_configured": configured,
        "exchange_count": len(exchanges),
        "last_scan_at": last_scan_at,
        "last_scan_age_seconds": age,
        "stale": stale,
        "last_scan_error": last_scan_error,
        "mode": "public_read_only",
    }


@app.get("/api/v1/exchanges")
async def list_exchanges():
    snapshot = get_last_scan_snapshot()
    statuses = (snapshot.get("summary") or {}).get("exchange_status") or {}
    items = []
    for name in sorted(exchanges):
        diagnostic = statuses.get(name) or {}
        items.append({
            "id": name,
            "status": diagnostic.get("status", "not_scanned_yet"),
            "market_count": diagnostic.get("market_count"),
            "usable_tickers": diagnostic.get("usable_tickers"),
            "error": diagnostic.get("error"),
        })
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "exchanges": items,
        "note": "Configured status is not a guarantee of current exchange availability.",
    }


@app.get("/api/v1/opportunities")
async def list_opportunities(
    limit: int = Query(default=50, ge=1, le=200),
    min_spread_pct: float = Query(default=0, ge=0, le=10000),
    min_profit_pct: float = Query(default=-100, ge=-100, le=10000),
    min_volume: float = Query(default=0, ge=0, le=1e15),
    search: str = Query(default="", max_length=64),
    buy_exchange: str = Query(default="", max_length=40),
    sell_exchange: str = Query(default="", max_length=40),
    sort_by: str = Query(default="provisional_profit", pattern="^(provisional_profit|gross_spread|volume|symbol)$"),
):
    age = _scan_age_seconds()
    stale = age is None or age > max(90, SCAN_INTERVAL_SECONDS * 3)
    if not last_scan_at:
        if last_scan_error:
            raise HTTPException(status_code=503, detail="Scanner has not completed a successful cycle yet.")
        return JSONResponse(status_code=503, content={"detail": "Scanner is warming up; no successful scan is available yet."})

    q = search.strip().upper()
    buy_filter = buy_exchange.strip().lower()
    sell_filter = sell_exchange.strip().lower()
    results = [
        item for item in latest_opportunities
        if item.raw_spread >= min_spread_pct
        and item.net_profit >= min_profit_pct
        and min(item.volume_buy, item.volume_sell) >= min_volume
        and (not q or q in item.symbol.upper())
        and (not buy_filter or item.buy_exchange.lower() == buy_filter)
        and (not sell_filter or item.sell_exchange.lower() == sell_filter)
    ]
    if sort_by == "gross_spread":
        results.sort(key=lambda item: item.raw_spread, reverse=True)
    elif sort_by == "volume":
        results.sort(key=lambda item: min(item.volume_buy, item.volume_sell), reverse=True)
    elif sort_by == "symbol":
        results.sort(key=lambda item: item.symbol)
    else:
        results.sort(key=lambda item: item.net_profit, reverse=True)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "last_scan_at": last_scan_at,
        "last_scan_age_seconds": age,
        "stale": stale,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "count": len(results),
        "data_quality": "headline_only",
        "items": [_opportunity_payload(item) for item in results[:limit]],
        "filters": {
            "min_spread_pct": min_spread_pct,
            "min_profit_pct": min_profit_pct,
            "min_volume": min_volume,
            "search": q,
            "buy_exchange": buy_filter,
            "sell_exchange": sell_filter,
            "sort_by": sort_by,
        },
        "warnings": [
            "Values are provisional ticker-based estimates, not verified executable net profit.",
            "The list estimate may use a fallback fee; details use reported fee metadata where available.",
            "Withdrawal costs are not deducted from the list estimate.",
        ],
    }


@app.get("/api/v1/opportunity-details")
async def opportunity_details(
    symbol: str = Query(min_length=3, max_length=40),
    buy_exchange: str = Query(min_length=1, max_length=40),
    sell_exchange: str = Query(min_length=1, max_length=40),
    trade_size_usdt: float = Query(default=100, gt=0, le=100000),
):
    normalized = symbol.strip().upper()
    buy_name = buy_exchange.strip().lower()
    sell_name = sell_exchange.strip().lower()
    if buy_name == sell_name:
        raise HTTPException(status_code=400, detail="Buy and sell exchanges must be different.")

    opportunity = next((
        item for item in latest_opportunities
        if item.symbol.upper() == normalized
        and item.buy_exchange.lower() == buy_name
        and item.sell_exchange.lower() == sell_name
    ), None)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="This route is no longer in the latest scan. Refresh opportunities.")

    buy_adapter = exchanges.get(buy_name)
    sell_adapter = exchanges.get(sell_name)
    if buy_adapter is None or sell_adapter is None:
        raise HTTPException(status_code=503, detail="One of the exchanges is unavailable in this API process.")

    try:
        await asyncio.gather(
            asyncio.wait_for(buy_adapter.client.load_markets(), timeout=8),
            asyncio.wait_for(sell_adapter.client.load_markets(), timeout=8),
        )
        buy_book, sell_book = await asyncio.gather(
            asyncio.wait_for(buy_adapter.fetch_order_book(normalized, limit=25), timeout=8),
            asyncio.wait_for(sell_adapter.fetch_order_book(normalized, limit=25), timeout=8),
        )
    except Exception as exc:
        logger.info("Depth request failed for %s (%s/%s): %s", normalized, buy_name, sell_name, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not fetch fresh order books from both exchanges.") from exc

    asks = buy_book.get("asks") or []
    bids = sell_book.get("bids") or []
    if not asks or not bids:
        raise HTTPException(status_code=502, detail="One exchange returned an empty side of the order book.")

    buy_depth = consume_asks_for_quote(asks, trade_size_usdt)
    sell_depth = consume_bids_for_base(bids, float(buy_depth["base_amount"]))
    buy_vwap = float(buy_depth["average_price"])
    sell_vwap = float(sell_depth["average_price"])
    both_books_complete = bool(buy_depth["complete"] and sell_depth["complete"])
    gross_pnl = float(sell_depth["quote_received"]) - float(buy_depth["quote_spent"])

    buy_fee_rate, buy_fee_source = trading_fee_rate(buy_adapter.client, normalized)
    sell_fee_rate, sell_fee_source = trading_fee_rate(sell_adapter.client, normalized)
    fees_known = buy_fee_rate is not None and sell_fee_rate is not None
    trading_fee_amount = None
    profit_after_trading_fees = None
    if fees_known and both_books_complete:
        trading_fee_amount = (
            float(buy_depth["quote_spent"]) * float(buy_fee_rate)
            + float(sell_depth["quote_received"]) * float(sell_fee_rate)
        )
        profit_after_trading_fees = gross_pnl - trading_fee_amount

    base_currency = normalized.split("/")[0]
    try:
        buy_networks, sell_networks = await asyncio.gather(
            _fetch_transfer_networks(buy_adapter, base_currency),
            _fetch_transfer_networks(sell_adapter, base_currency),
        )
    except Exception:
        buy_networks, sell_networks = [], []
    transfer_routes = _verified_routes(buy_networks, sell_networks)
    trade_size_complete = both_books_complete

    warning_messages = [
        "Order-book values are a snapshot and can change before an order fills.",
        "Trading fees are included only when both venues expose a readable fee rate.",
        "Full route profit remains unknown until withdrawal fees and other transfer costs are confirmed.",
    ]
    if not trade_size_complete:
        warning_messages.append("Available order-book depth could not fill the requested trade size on both legs.")
    if not fees_known:
        warning_messages.append("At least one trading fee rate is unavailable; after-fee profit is not calculated.")
    if not transfer_routes:
        warning_messages.append("No transfer route could be positively verified with enabled withdraw/deposit status and matching contract metadata.")

    return {
        "symbol": normalized,
        "buy_exchange": buy_name,
        "sell_exchange": sell_name,
        "quote_currency": normalized.split("/")[1] if "/" in normalized else None,
        "requested_trade_size_quote": trade_size_usdt,
        "buy": {
            "best_ask": float(asks[0][0]),
            "volume_weighted_average_price": buy_vwap,
            "quote_spent": float(buy_depth["quote_spent"]),
            "base_acquired": float(buy_depth["base_amount"]),
            "book_complete_for_requested_size": bool(buy_depth["complete"]),
            "taker_fee_rate": buy_fee_rate,
            "fee_source": buy_fee_source,
        },
        "sell": {
            "best_bid": float(bids[0][0]),
            "volume_weighted_average_price": sell_vwap,
            "quote_received": float(sell_depth["quote_received"]),
            "base_sold": float(sell_depth["base_sold"]),
            "book_complete_for_requested_size": bool(sell_depth["complete"]),
            "taker_fee_rate": sell_fee_rate,
            "fee_source": sell_fee_source,
        },
        "gross_pnl_before_fees": gross_pnl if both_books_complete else None,
        "known_trading_fee_estimate": trading_fee_amount,
        "profit_after_known_trading_fees": profit_after_trading_fees,
        "full_route_net_profit": None,
        "order_book_depth_verified": both_books_complete,
        "transfer_route_verified": bool(transfer_routes),
        "verified_transfer_routes": transfer_routes,
        "available_network_metadata": {
            "buy_exchange": buy_networks[:20],
            "sell_exchange": sell_networks[:20],
        },
        "observed_at": datetime.now(UTC).isoformat(),
        "warnings": warning_messages,
    }
