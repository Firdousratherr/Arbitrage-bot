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

from app.exchanges.base import Opportunity, Ticker
from app.exchanges.registry import build_exchanges
from mobile_api.market_math import consume_asks_for_quote, consume_bids_for_base, trading_fee_rate

logging.basicConfig(level=os.getenv("MOBILE_API_LOG_LEVEL", "INFO"))
logger = logging.getLogger("mobile_api")

SCAN_INTERVAL_SECONDS = max(15, int(os.getenv("MOBILE_API_SCAN_INTERVAL_SECONDS", "30")))
RATE_LIMIT_PER_MINUTE = max(30, int(os.getenv("MOBILE_API_RATE_LIMIT_PER_MINUTE", "120")))
MAX_EXCHANGE_CONCURRENCY = max(1, int(os.getenv("MOBILE_API_MAX_EXCHANGE_CONCURRENCY", "3")))
ORDERBOOK_RECOVERY_PER_EXCHANGE = max(0, min(100, int(os.getenv("MOBILE_API_ORDERBOOK_RECOVERY_PER_EXCHANGE", "20"))))
MAX_FALLBACK_SYMBOLS = max(50, min(500, int(os.getenv("MOBILE_API_FALLBACK_SYMBOLS", "200"))))
QUOTE_CURRENCIES = {
    value.strip().upper()
    for value in os.getenv("MOBILE_API_QUOTE_CURRENCIES", "USDT,USDC,USD,BTC,ETH").split(",")
    if value.strip()
}
EXCHANGE_NAMES = [
    value.strip().lower()
    for value in os.getenv(
        "MOBILE_API_EXCHANGES",
        "xt,kucoin,gateio,mexc,okx,htx,kraken,bitget,bitrue,lbank,coinbase,bitfinex,phemex,cryptocom,poloniex",
    ).split(",")
    if value.strip()
]

exchanges: dict[str, Any] = {}
latest_opportunities: list[Opportunity] = []
last_scan_at: str | None = None
last_scan_error: str | None = None
market_diagnostics: dict[str, dict[str, Any]] = {}
scan_task: asyncio.Task | None = None
_request_times: dict[str, deque[float]] = {}


def _public_credentials_provider(_exchange: str) -> dict[str, str]:
    # Public read-only market data only; never load Telegram bot credentials.
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


def _quote_currency(symbol: str, market: dict[str, Any] | None = None) -> str:
    if isinstance(market, dict) and market.get("quote"):
        return str(market["quote"]).upper()
    parts = symbol.split("/")
    if len(parts) > 1:
        return parts[1].split(":")[0].upper()
    return ""


def _build_tickers(name: str, raw: dict[str, Any], spot_symbols: set[str]) -> tuple[list[Ticker], list[str]]:
    result: list[Ticker] = []
    missing: list[str] = []
    markets = getattr(exchanges[name].client, "markets", {}) or {}
    for symbol, data in (raw or {}).items():
        if symbol not in spot_symbols or not isinstance(data, dict):
            continue
        try:
            bid = float(data.get("bid") or 0)
            ask = float(data.get("ask") or 0)
            quote_volume = float(data.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        if bid > 0 and ask > 0:
            result.append(Ticker(name, symbol, bid, ask, max(0.0, quote_volume)))
        else:
            missing.append(symbol)
    result.sort(key=lambda item: item.quote_volume, reverse=True)
    return result, missing


async def _collect_exchange(name: str, adapter: Any, semaphore: asyncio.Semaphore) -> tuple[str, list[Ticker], dict[str, Any]]:
    started = time.monotonic()
    try:
        async with semaphore:
            symbols = await asyncio.wait_for(adapter.get_active_spot_symbols(), timeout=25)
        markets = getattr(adapter.client, "markets", {}) or {}
        spot_symbols = {
            symbol for symbol in symbols
            if _quote_currency(symbol, markets.get(symbol)) in QUOTE_CURRENCIES
        }
        if not spot_symbols:
            return name, [], {
                "status": "no_supported_quote_markets",
                "market_count": 0,
                "usable_tickers": 0,
                "orderbook_recovered": 0,
                "error": None,
                "duration_seconds": round(time.monotonic() - started, 2),
            }

        raw: dict[str, Any] = {}
        fetch_error = None
        try:
            async with semaphore:
                raw = await asyncio.wait_for(adapter.client.fetch_tickers(), timeout=25)
        except Exception as exc:
            fetch_error = type(exc).__name__
            logger.info("%s bulk ticker endpoint failed (%s); using bounded adapter fallback", name, fetch_error)
            fallback_symbols = sorted(spot_symbols)[:MAX_FALLBACK_SYMBOLS]
            async with semaphore:
                recovered = await asyncio.wait_for(adapter.fetch_tickers(fallback_symbols), timeout=30)
            tickers = [t for t in recovered if t.symbol in spot_symbols and t.bid > 0 and t.ask > 0]
            return name, tickers, {
                "status": "partial" if tickers else "fetch_failed",
                "market_count": len(spot_symbols),
                "usable_tickers": len(tickers),
                "orderbook_recovered": 0,
                "error": fetch_error,
                "duration_seconds": round(time.monotonic() - started, 2),
            }

        tickers, missing = _build_tickers(name, raw, spot_symbols)
        recovered_count = 0
        candidates = [
            symbol for symbol in missing
            if symbol in spot_symbols
        ][:ORDERBOOK_RECOVERY_PER_EXCHANGE]
        if candidates:
            recovery_semaphore = asyncio.Semaphore(min(MAX_EXCHANGE_CONCURRENCY, 4))
            async def recover(symbol: str) -> Ticker | None:
                async with recovery_semaphore:
                    try:
                        book = await asyncio.wait_for(adapter.fetch_order_book(symbol, limit=1), timeout=8)
                        bids = book.get("bids") or []
                        asks = book.get("asks") or []
                        if not bids or not asks:
                            return None
                        bid, ask = float(bids[0][0]), float(asks[0][0])
                        if bid <= 0 or ask <= 0:
                            return None
                        quote_volume = 0.0
                        try:
                            quote_volume = max(0.0, float((raw.get(symbol) or {}).get("quoteVolume") or 0))
                        except (TypeError, ValueError):
                            pass
                        return Ticker(name, symbol, bid, ask, quote_volume)
                    except Exception as exc:
                        logger.debug("%s order book recovery for %s failed (%s)", name, symbol, type(exc).__name__)
                        return None
            results = await asyncio.gather(*(recover(symbol) for symbol in candidates))
            existing = {ticker.symbol for ticker in tickers}
            for ticker in results:
                if ticker is not None and ticker.symbol not in existing:
                    tickers.append(ticker)
                    existing.add(ticker.symbol)
                    recovered_count += 1

        status = "ok" if tickers and not missing else ("partial" if tickers else "no_usable_tickers")
        return name, tickers, {
            "status": status,
            "market_count": len(spot_symbols),
            "usable_tickers": len(tickers),
            "orderbook_recovered": recovered_count,
            "error": None,
            "bulk_ticker_missing_count": len(missing),
            "duration_seconds": round(time.monotonic() - started, 2),
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("%s mobile scan failed (%s)", name, type(exc).__name__)
        return name, [], {
            "status": "failed",
            "market_count": 0,
            "usable_tickers": 0,
            "orderbook_recovered": 0,
            "error": type(exc).__name__,
            "duration_seconds": round(time.monotonic() - started, 2),
        }


async def _scan_cycle() -> list[Opportunity]:
    """Compare each pair of venues where a matching spot market exists.

    Unlike the Telegram scanner's user-selected-exchange intersection, this scan
    does not require a symbol to be listed on every enabled exchange.
    """
    semaphore = asyncio.Semaphore(MAX_EXCHANGE_CONCURRENCY)
    collected = await asyncio.gather(*(
        _collect_exchange(name, adapter, semaphore)
        for name, adapter in exchanges.items()
    ))
    diagnostics: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, list[Ticker]] = {}
    for name, tickers, status in collected:
        diagnostics[name] = status
        for ticker in tickers:
            by_symbol.setdefault(ticker.symbol, []).append(ticker)

    observed_at = datetime.now(UTC).isoformat()
    best_by_symbol: list[Opportunity] = []
    for symbol, tickers in by_symbol.items():
        if len(tickers) < 2:
            continue
        # Keep the route with the strongest gross top-of-book spread for this pair.
        best: tuple[Ticker, Ticker, float] | None = None
        for buy in tickers:
            for sell in tickers:
                if buy.exchange == sell.exchange or buy.ask <= 0 or sell.bid <= 0:
                    continue
                spread = ((sell.bid - buy.ask) / buy.ask) * 100.0
                if spread <= 0:
                    continue
                if best is None or spread > best[2]:
                    best = (buy, sell, spread)
        if best is None:
            continue
        buy, sell, raw_spread = best
        buy_rate, buy_fee_source = trading_fee_rate(exchanges[buy.exchange].client, symbol)
        sell_rate, sell_fee_source = trading_fee_rate(exchanges[sell.exchange].client, symbol)
        fees_known = buy_rate is not None and sell_rate is not None
        fee_adjusted = None
        if fees_known:
            fee_adjusted = raw_spread - (float(buy_rate) + float(sell_rate)) * 100.0
        metadata = {
            "observed_at": observed_at,
            "headline_only": True,
            "coverage_complete": True,
            "provisional_fee_adjusted_spread_pct": fee_adjusted,
            "buy_taker_fee_rate": buy_rate,
            "sell_taker_fee_rate": sell_rate,
            "buy_fee_source": buy_fee_source,
            "sell_fee_source": sell_fee_source,
            "fee_data_complete": fees_known,
            "selected_exchange_count": len(exchanges),
        }
        best_by_symbol.append(Opportunity(
            symbol=symbol,
            buy_exchange=buy.exchange,
            sell_exchange=sell.exchange,
            buy_price=buy.ask,
            sell_price=sell.bid,
            raw_spread=raw_spread,
            # The dataclass requires a float. When fee data is unknown, net_profit
            # is not exposed as a profit estimate; API sorting places it after known estimates.
            net_profit=fee_adjusted if fee_adjusted is not None else 0.0,
            volume_buy=buy.quote_volume,
            volume_sell=sell.quote_volume,
            metadata=metadata,
        ))

    def sort_key(item: Opportunity):
        estimate = item.metadata.get("provisional_fee_adjusted_spread_pct")
        return (estimate is not None, float(estimate) if estimate is not None else float("-inf"), item.raw_spread)

    best_by_symbol.sort(key=sort_key, reverse=True)
    return best_by_symbol


async def _scan_loop() -> None:
    global latest_opportunities, last_scan_at, last_scan_error, market_diagnostics
    while True:
        started = time.monotonic()
        try:
            results = await _scan_cycle()
            latest_opportunities = results
            last_scan_at = datetime.now(UTC).isoformat()
            market_diagnostics = {
                name: value for name, value in market_diagnostics.items()
            } if not results and not market_diagnostics else market_diagnostics
            last_scan_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_scan_error = type(exc).__name__ + ": scan failed"
            logger.exception("mobile scan cycle failed")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, SCAN_INTERVAL_SECONDS - elapsed))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global exchanges, scan_task, market_diagnostics
    exchanges = build_exchanges(EXCHANGE_NAMES, _public_credentials_provider)
    market_diagnostics = {
        name: {"status": "not_scanned_yet", "market_count": None, "usable_tickers": None, "error": None}
        for name in exchanges
    }
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


app = FastAPI(
    title="Arbitrage Mobile API",
    version="0.3.0",
    description="Read-only mobile API. Ticker estimates are provisional; depth and route checks are separately reported.",
    lifespan=lifespan,
)


@app.middleware("http")
async def simple_ip_rate_limit(request: Request, call_next):
    # Basic per-process abuse protection. Production should use a shared gateway limiter.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    queue = _request_times.setdefault(ip, deque())
    while queue and now - queue[0] >= 60:
        queue.popleft()
    if len(queue) >= RATE_LIMIT_PER_MINUTE:
        return JSONResponse(status_code=429, content={"detail": "Request limit reached. Please wait."}, headers={"Retry-After": "60"})
    queue.append(now)
    if len(_request_times) > 2048:
        for address in list(_request_times)[:512]:
            if address != ip and (not _request_times[address] or now - _request_times[address][-1] >= 60):
                _request_times.pop(address, None)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_PER_MINUTE)
    return response


def _opportunity_payload(item: Opportunity) -> dict[str, Any]:
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
    fee_estimate = metadata.get("provisional_fee_adjusted_spread_pct")
    return {
        "symbol": item.symbol,
        "buy_exchange": item.buy_exchange,
        "sell_exchange": item.sell_exchange,
        "buy_ask": item.buy_price,
        "sell_bid": item.sell_price,
        "gross_spread_pct": round(item.raw_spread, 6),
        "provisional_fee_adjusted_spread_pct": round(float(fee_estimate), 6) if fee_estimate is not None else None,
        "buy_taker_fee_rate": metadata.get("buy_taker_fee_rate"),
        "sell_taker_fee_rate": metadata.get("sell_taker_fee_rate"),
        "fee_data_complete": bool(metadata.get("fee_data_complete", False)),
        "buy_quote_volume_24h": item.volume_buy,
        "sell_quote_volume_24h": item.volume_sell,
        "observed_at": observed_at,
        "data_age_seconds": age,
        "coverage_complete": bool(metadata.get("coverage_complete", False)),
        "transfer_verified": False,
        "order_book_depth_verified": False,
        "estimate_type": "headline_only",
        "warnings": [
            "Headline opportunity based on spot ticker bid/ask values; not executable net profit.",
            "Fee-adjusted spread is shown only when both venues publish a readable taker fee rate.",
            "Withdrawal/network costs and deposit/withdrawal availability are checked only in route details and may remain unknown.",
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
        contract = value.get("contract") or info.get("contractAddress") or info.get("contract_address")
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
            "withdrawal_fee": cost if isinstance(cost, (int, float)) and cost >= 0 else None,
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


def _verified_routes(buy_networks: list[dict[str, Any]], sell_networks: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            fee = source.get("withdrawal_fee")
            routes.append({
                "network": source.get("network_id"),
                "withdraw_from_buy_exchange_enabled": True,
                "deposit_to_sell_exchange_enabled": True,
                "contract_match": True,
                "buy_exchange_withdrawal_fee": fee,
                "withdrawal_fee_asset": source.get("currency"),
                "withdrawal_fee_known": isinstance(fee, (int, float)) and fee >= 0,
                "sell_exchange_deposit_confirmed_by_api": True,
                "verified": True,
            })
    return routes[:20]


@app.get("/healthz")
async def healthz():
    age = _scan_age_seconds()
    stale = age is None or age > max(90, SCAN_INTERVAL_SECONDS * 3)
    successful = sum(1 for item in market_diagnostics.values() if item.get("status") in {"ok", "partial"})
    configured = len(exchanges) >= 2
    healthy = configured and successful >= 2 and not stale and not last_scan_error
    return {
        "status": "ok" if healthy else "degraded",
        "service": "mobile-api",
        "scanner_configured": configured,
        "exchange_count": len(exchanges),
        "healthy_exchange_count": successful,
        "last_scan_at": last_scan_at,
        "last_scan_age_seconds": age,
        "stale": stale,
        "last_scan_error": last_scan_error,
        "mode": "public_read_only",
    }


@app.get("/api/v1/exchanges")
async def list_exchanges():
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "exchanges": [
            {"id": name, **(market_diagnostics.get(name) or {"status": "not_scanned_yet"})}
            for name in sorted(exchanges)
        ],
        "note": "An exchange marked configured or partial is not guaranteed to be currently healthy.",
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
    results = []
    for item in latest_opportunities:
        estimate = item.metadata.get("provisional_fee_adjusted_spread_pct")
        if item.raw_spread < min_spread_pct or min(item.volume_buy, item.volume_sell) < min_volume:
            continue
        if q and q not in item.symbol.upper():
            continue
        if buy_filter and item.buy_exchange.lower() != buy_filter:
            continue
        if sell_filter and item.sell_exchange.lower() != sell_filter:
            continue
        if min_profit_pct > -100 and (estimate is None or estimate < min_profit_pct):
            continue
        results.append(item)

    def sort_key(item: Opportunity):
        estimate = item.metadata.get("provisional_fee_adjusted_spread_pct")
        if sort_by == "gross_spread":
            return (item.raw_spread,)
        if sort_by == "volume":
            return (min(item.volume_buy, item.volume_sell),)
        if sort_by == "symbol":
            return (item.symbol,)
        return (estimate is not None, float(estimate) if estimate is not None else float("-inf"), item.raw_spread)

    results.sort(key=sort_key, reverse=True)
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
            "Values are spot ticker estimates, not verified executable net profit.",
            "Fee-adjusted spread is unknown when a selected route lacks fee metadata for either venue.",
            "Withdrawal costs are not included in the opportunity list; inspect route details.",
        ],
    }


@app.get("/api/v1/opportunity-details")
async def opportunity_details(
    symbol: str = Query(min_length=3, max_length=40),
    buy_exchange: str = Query(min_length=1, max_length=40),
    sell_exchange: str = Query(min_length=1, max_length=40),
    trade_size_quote: float = Query(default=100, gt=0, le=100000),
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

    buy_depth = consume_asks_for_quote(asks, trade_size_quote)
    base_acquired = float(buy_depth["base_amount"])
    if base_acquired <= 0:
        raise HTTPException(status_code=502, detail="The buy order book contains no usable ask liquidity.")
    base_currency = normalized.split("/")[0]
    try:
        buy_networks, sell_networks = await asyncio.gather(
            _fetch_transfer_networks(buy_adapter, base_currency),
            _fetch_transfer_networks(sell_adapter, base_currency),
        )
    except Exception:
        buy_networks, sell_networks = [], []
    transfer_routes = _verified_routes(buy_networks, sell_networks)
    known_fee_routes = [route for route in transfer_routes if route.get("withdrawal_fee_known")]
    selected_route = min(
        known_fee_routes or transfer_routes,
        key=lambda route: float(route["buy_exchange_withdrawal_fee"]) if route.get("withdrawal_fee_known") else float("inf"),
        default=None,
    )
    withdrawal_fee_base = None
    base_for_sale = base_acquired
    if selected_route and selected_route.get("withdrawal_fee_known"):
        withdrawal_fee_base = float(selected_route["buy_exchange_withdrawal_fee"])
        base_for_sale = max(0.0, base_acquired - withdrawal_fee_base)
    if base_for_sale <= 0:
        raise HTTPException(status_code=422, detail="The verified route withdrawal fee consumes the entire estimated base amount.")

    sell_depth = consume_bids_for_base(bids, base_for_sale)
    buy_vwap = float(buy_depth["average_price"])
    sell_vwap = float(sell_depth["average_price"])
    both_books_complete = bool(buy_depth["complete"] and sell_depth["complete"])
    gross_pnl = float(sell_depth["quote_received"]) - float(buy_depth["quote_spent"])
    buy_rate, buy_fee_source = trading_fee_rate(buy_adapter.client, normalized)
    sell_rate, sell_fee_source = trading_fee_rate(sell_adapter.client, normalized)
    fees_known = buy_rate is not None and sell_rate is not None
    trading_fee_amount = None
    profit_after_trading_fees = None
    if fees_known and both_books_complete:
        trading_fee_amount = float(buy_depth["quote_spent"]) * float(buy_rate) + float(sell_depth["quote_received"]) * float(sell_rate)
        profit_after_trading_fees = gross_pnl - trading_fee_amount

    warning_messages = [
        "Order-book values are a snapshot and can change before an order fills.",
        "Trading-fee estimates use reported market/exchange fee metadata only.",
    ]
    if withdrawal_fee_base is None:
        warning_messages.append("Withdrawal fee is unknown or no strict route was verified; the sell quantity is not transfer-fee-adjusted.")
        warning_messages.append("Full-route net profit is unknown until transfer costs are confirmed.")
    else:
        warning_messages.append("Known withdrawal fee was deducted from the base quantity before estimating sale proceeds.")
    warning_messages.append("Other transfer costs, timing risk, exchange limits and price movement may not be captured.")
    if not both_books_complete:
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
        "requested_trade_size_quote": trade_size_quote,
        "buy_slippage_pct": max(0.0, (buy_vwap - float(asks[0][0])) / float(asks[0][0]) * 100.0) if buy_depth["complete"] and float(asks[0][0]) > 0 else None,
        "sell_slippage_pct": max(0.0, (float(bids[0][0]) - sell_vwap) / float(bids[0][0]) * 100.0) if sell_depth["complete"] and float(bids[0][0]) > 0 else None,
        "buy": {
            "best_ask": float(asks[0][0]),
            "volume_weighted_average_price": buy_vwap,
            "quote_spent": float(buy_depth["quote_spent"]),
            "base_acquired": base_acquired,
            "book_complete_for_requested_size": bool(buy_depth["complete"]),
            "taker_fee_rate": buy_rate,
            "fee_source": buy_fee_source,
        },
        "sell": {
            "best_bid": float(bids[0][0]),
            "volume_weighted_average_price": sell_vwap,
            "quote_received": float(sell_depth["quote_received"]),
            "base_sold": float(sell_depth["base_sold"]),
            "book_complete_for_requested_size": bool(sell_depth["complete"]),
            "taker_fee_rate": sell_rate,
            "fee_source": sell_fee_source,
        },
        "gross_pnl_before_fees": gross_pnl if both_books_complete else None,
        "gross_pnl_before_trading_fees": gross_pnl if both_books_complete else None,
        "known_trading_fee_estimate": trading_fee_amount,
        "profit_after_known_trading_fees": profit_after_trading_fees,
        "profit_after_known_fees": profit_after_trading_fees,
        "withdrawal_fee_deducted_base": withdrawal_fee_base,
        "full_route_net_profit": None,
        "order_book_depth_verified": both_books_complete,
        "transfer_route_verified": bool(transfer_routes),
        "verified_transfer_routes": transfer_routes,
        "available_network_metadata": {"buy_exchange": buy_networks[:20], "sell_exchange": sell_networks[:20]},
        "observed_at": datetime.now(UTC).isoformat(),
        "warnings": warning_messages,
    }
