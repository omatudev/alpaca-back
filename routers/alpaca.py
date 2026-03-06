"""Alpaca API router.

All endpoints live under the /api/alpaca prefix registered in main.py.

Endpoint summary
----------------
Account & Portfolio
  GET  /account           → account info (balance, buying power, portfolio value)
  GET  /positions         → open positions

Orders
  GET  /orders            → list orders  (?status=all|open|closed  &limit=50)
  POST /orders            → place a new order
  DELETE /orders/{id}     → cancel an order

Assets
  GET  /assets/search     → search active assets by name/symbol  (?q=apple)
  GET  /assets/{symbol}   → asset details  (NOTE: must come AFTER /assets/search)

Market Data
  GET  /market/quote/{symbol}        → latest bid/ask quote
  GET  /market/bars/{symbol}         → OHLCV bars (?timeframe=1Day&start=&end=&limit=100)
  GET  /market/snapshot/{symbol}     → full snapshot (price, change, daily bar)
  GET  /market/snapshots             → multiple snapshots (?symbols=AAPL,TSLA,BTC/USD)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from alpaca_integration.client import alpaca_client
from alpaca_integration.market_data import alpaca_market_data

router = APIRouter()


# ── Request / Response models ────────────────────────────────────────────────────

from pydantic import BaseModel


class PlaceOrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str                        # "buy" | "sell"
    order_type: str                  # "market" | "limit" | "stop" | "stop_limit"
    time_in_force: str = "day"       # "day" | "gtc" | "ioc" | "fok"
    limit_price: float | None = None
    stop_price:  float | None = None

    model_config = {"json_schema_extra": {
        "examples": [{
            "symbol": "AAPL",
            "qty": 1,
            "side": "buy",
            "order_type": "market",
            "time_in_force": "day",
        }]
    }}


# ── Account ──────────────────────────────────────────────────────────────────────

@router.get(
    "/account",
    summary="Account info",
    description="Returns balance, buying power, portfolio value, and account status.",
)
def get_account() -> dict[str, Any]:
    return alpaca_client.get_account()


# ── Positions ─────────────────────────────────────────────────────────────────────

@router.get(
    "/positions",
    summary="Open positions",
    description="Returns all currently open positions.",
)
def get_positions() -> list[dict[str, Any]]:
    return alpaca_client.get_positions()


# ── Orders ────────────────────────────────────────────────────────────────────────

@router.get(
    "/orders",
    summary="List orders",
    description="Returns a list of orders filtered by status.",
)
def get_orders(
    status: str = Query(default="all", description="all | open | closed"),
    limit: int  = Query(default=50,  ge=1, le=500),
) -> list[dict[str, Any]]:
    return alpaca_client.get_orders(status=status, limit=limit)


@router.post(
    "/orders",
    status_code=201,
    summary="Place order",
    description="Submit a new market, limit, stop, or stop-limit order.",
)
def place_order(body: PlaceOrderRequest) -> dict[str, Any]:
    return alpaca_client.place_order(
        symbol=body.symbol,
        qty=body.qty,
        side=body.side,
        order_type=body.order_type,
        time_in_force=body.time_in_force,
        limit_price=body.limit_price,
        stop_price=body.stop_price,
    )


@router.delete(
    "/orders/{order_id}",
    status_code=204,
    summary="Cancel order",
    description="Cancel an open order by its Alpaca order ID.",
)
def cancel_order(order_id: str) -> None:
    alpaca_client.cancel_order(order_id)


# ── Assets ────────────────────────────────────────────────────────────────────────
# IMPORTANT: /assets/search MUST be declared before /assets/{symbol}
# so FastAPI does not treat "search" as a symbol value.

@router.get(
    "/assets/search",
    summary="Search assets",
    description="Search active assets by name or symbol (case-insensitive). Returns up to 50 results.",
)
def search_assets(
    q: str = Query(..., min_length=1, description="Search query (name or ticker)"),
) -> list[dict[str, Any]]:
    return alpaca_client.search_assets(q)


@router.get(
    "/assets/{symbol}",
    summary="Asset info",
    description="Return details for an asset: tradeable, fractionable, asset class, exchange, etc.",
)
def get_asset(symbol: str) -> dict[str, Any]:
    return alpaca_client.get_asset(symbol)


# ── Market Data ───────────────────────────────────────────────────────────────────

@router.get(
    "/market/quote/{symbol}",
    summary="Latest quote",
    description="Return the latest bid/ask/size quote for a stock or crypto symbol.",
)
def get_quote(symbol: str) -> dict[str, Any]:
    return alpaca_market_data.get_latest_quote(symbol)


@router.get(
    "/market/bars/{symbol}",
    summary="Historical bars",
    description=(
        "Return OHLCV candlestick bars. "
        "Timeframe options: 1Min, 5Min, 15Min, 30Min, 1Hour, 4Hour, 1Day, 1Week, 1Month."
    ),
)
def get_bars(
    symbol: str,
    timeframe: str = Query(default="1Day", description="Bar timeframe"),
    start: str | None = Query(default=None, description="ISO 8601 start date, e.g. 2024-01-01"),
    end:   str | None = Query(default=None, description="ISO 8601 end date"),
    limit: int | None = Query(default=None, ge=1, le=10_000),
) -> dict[str, Any]:
    return alpaca_market_data.get_bars(
        symbol=symbol, timeframe=timeframe, start=start, end=end, limit=limit
    )


@router.get(
    "/market/snapshot/{symbol}",
    summary="Asset snapshot",
    description=(
        "Return a full market snapshot including latest trade, quote, "
        "daily bar, previous daily bar, and derived price/change fields."
    ),
)
def get_snapshot(symbol: str) -> dict[str, Any]:
    return alpaca_market_data.get_snapshot(symbol)


@router.get(
    "/market/snapshots",
    summary="Multiple snapshots",
    description=(
        "Return snapshots for multiple symbols in a single call. "
        "Pass comma-separated tickers: ?symbols=AAPL,TSLA,BTC/USD"
    ),
)
def get_multiple_snapshots(
    symbols: str = Query(..., description="Comma-separated list of symbols"),
) -> dict[str, Any]:
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    return alpaca_market_data.get_multiple_snapshots(symbol_list)


@router.get(
    "/market/clock",
    summary="Market clock",
    description="Returns whether the market is open and next open/close times.",
)
def get_market_clock() -> dict[str, Any]:
    return alpaca_client.get_clock()
