"""Alpaca trading client wrapper.

SDK docs: https://docs.alpaca.markets/reference/
alpaca-py: https://github.com/alpacahq/alpaca-py
"""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetStatus,
    OrderSide,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    CreateWatchlistRequest,
    GetAssetsRequest,
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)
from fastapi import HTTPException

from config import settings


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _to_http(exc: APIError) -> HTTPException:
    """Convert an alpaca-py APIError to a FastAPI HTTPException."""
    http_code = getattr(exc, "status_code", 502)
    # APIError stores the parsed response body in exc.response_body or exc.args[0]
    body = getattr(exc, "response_body", None) or getattr(exc, "args", [None])[0]
    if isinstance(body, dict):
        detail = body  # keep full structured payload (code, message, held_for_orders, etc.)
    elif isinstance(body, str):
        import json as _json
        try:
            detail = _json.loads(body)
        except Exception:
            detail = str(exc)
    else:
        detail = str(exc)
    return HTTPException(status_code=http_code, detail=detail)


def _dump(obj: Any) -> Any:
    """Serialize an alpaca-py pydantic model to a JSON-safe dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


# ── Client ───────────────────────────────────────────────────────────────────────

class AlpacaClient:
    """Thin wrapper around TradingClient with error normalisation."""

    def __init__(self) -> None:
        self._client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )

    # ── Account ─────────────────────────────────────────────────────────────────

    def get_account(self) -> dict[str, Any]:
        """Return account info: balance, buying power, portfolio value, etc."""
        try:
            return _dump(self._client.get_account())
        except APIError as exc:
            raise _to_http(exc) from exc
    def get_clock(self) -> dict[str, Any]:
        """Return the current market clock (is_open, next_open, next_close)."""
        try:
            clock = self._client.get_clock()
            data = _dump(clock)
            # Determine pre/post-market status from timestamps
            now = datetime.now(tz=timezone.utc)
            # Parse next_close and next_open
            is_open = data.get("is_open", False)
            return {
                "is_open": is_open,
                "timestamp": data.get("timestamp"),
                "next_open": data.get("next_open"),
                "next_close": data.get("next_close"),
            }
        except APIError as exc:
            raise _to_http(exc) from exc
    # ── Positions ────────────────────────────────────────────────────────────────

    def get_positions(self) -> list[dict[str, Any]]:
        """Return all open positions."""
        try:
            return [_dump(p) for p in self._client.get_all_positions()]
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Orders ───────────────────────────────────────────────────────────────────

    _STATUS_MAP: dict[str, QueryOrderStatus] = {
        "all":    QueryOrderStatus.ALL,
        "open":   QueryOrderStatus.OPEN,
        "closed": QueryOrderStatus.CLOSED,
    }

    def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return orders filtered by status."""
        try:
            req = GetOrdersRequest(
                status=self._STATUS_MAP.get(status.lower(), QueryOrderStatus.ALL),
                limit=limit,
            )
            return [_dump(o) for o in self._client.get_orders(filter=req)]
        except APIError as exc:
            raise _to_http(exc) from exc

    _TIF_MAP: dict[str, TimeInForce] = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC,
        "fok": TimeInForce.FOK,
        "opg": TimeInForce.OPG,
        "cls": TimeInForce.CLS,
    }

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str,
        time_in_force: str = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict[str, Any]:
        """Submit a new order. Raises HTTPException on validation or API errors."""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = self._TIF_MAP.get(time_in_force.lower(), TimeInForce.DAY)
        sym = symbol.upper()

        match order_type.lower():
            case "market":
                req = MarketOrderRequest(
                    symbol=sym, qty=qty, side=order_side, time_in_force=tif,
                )
            case "limit":
                if limit_price is None:
                    raise HTTPException(422, "limit_price is required for limit orders")
                req = LimitOrderRequest(
                    symbol=sym, qty=qty, side=order_side, time_in_force=tif,
                    limit_price=limit_price,
                )
            case "stop":
                if stop_price is None:
                    raise HTTPException(422, "stop_price is required for stop orders")
                req = StopOrderRequest(
                    symbol=sym, qty=qty, side=order_side, time_in_force=tif,
                    stop_price=stop_price,
                )
            case "stop_limit":
                if limit_price is None or stop_price is None:
                    raise HTTPException(
                        422, "Both limit_price and stop_price are required for stop_limit orders"
                    )
                req = StopLimitOrderRequest(
                    symbol=sym, qty=qty, side=order_side, time_in_force=tif,
                    limit_price=limit_price, stop_price=stop_price,
                )
            case _:
                raise HTTPException(422, f"Unknown order_type: '{order_type}'")

        try:
            return _dump(self._client.submit_order(order_data=req))
        except APIError as exc:
            raise _to_http(exc) from exc

    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order by its ID."""
        try:
            self._client.cancel_order_by_id(order_id)
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Assets ───────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict[str, Any]:
        """Return asset details: tradeable, fractionable, asset class, etc."""
        try:
            return _dump(self._client.get_asset(symbol.upper()))
        except APIError as exc:
            raise _to_http(exc) from exc

    def search_assets(self, query: str) -> list[dict[str, Any]]:
        """Search active assets by symbol (case-insensitive, max 20 results)."""
        try:
            req = GetAssetsRequest(status=AssetStatus.ACTIVE)
            assets = self._client.get_all_assets(filter=req)
            q = query.upper()
            # Exact match first, then prefix, then contains
            exact = []
            prefix = []
            contains = []
            for a in assets:
                sym = a.symbol.upper()
                if sym == q:
                    exact.append(a)
                elif sym.startswith(q):
                    prefix.append(a)
                elif q in sym:
                    contains.append(a)
            matched = exact + prefix + contains
            return [_dump(a) for a in matched[:20]]
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Portfolio History ────────────────────────────────────────────────────────

    def get_portfolio_history(
        self,
        period: str = "1D",
        timeframe: str = "5Min",
        extended_hours: bool = False,
    ) -> dict[str, Any]:
        """Return portfolio equity history as OHLCV-like bars."""
        try:
            req = GetPortfolioHistoryRequest(
                period=period,
                timeframe=timeframe,
                extended_hours=True,
            )
            history = self._client.get_portfolio_history(history_filter=req)
            data = _dump(history)
            # Convert parallel arrays to bar-like list for the chart
            timestamps = data.get("timestamp", [])
            equities = data.get("equity", [])
            pls = data.get("profit_loss", [])
            bars = []
            for i, ts in enumerate(timestamps):
                eq = equities[i] if i < len(equities) else None
                if eq is None:
                    continue
                # Use previous bar's close as open so candle bodies show period change
                prev_eq = equities[i - 1] if i > 0 and equities[i - 1] is not None else eq
                o = prev_eq
                c = eq
                h = max(o, c)
                l = min(o, c)
                bars.append({
                    "timestamp": datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat() if isinstance(ts, (int, float)) else str(ts),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": 0,
                    "profit_loss": pls[i] if i < len(pls) else 0,
                })
            data["bars"] = bars
            return data
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Watchlists (Alpaca /v2/watchlists) ───────────────────────────────────────

    _DEFAULT_WL_NAME = "MyBroker"

    def _get_or_create_default_watchlist(self) -> dict[str, Any]:
        """Return the default watchlist, creating it if it doesn't exist."""
        try:
            watchlists = self._client.get_watchlists()
            for wl in watchlists:
                if wl.name == self._DEFAULT_WL_NAME:
                    return _dump(wl)
            # Not found — create it
            req = CreateWatchlistRequest(
                name=self._DEFAULT_WL_NAME, symbols=[]
            )
            return _dump(self._client.create_watchlist(req))
        except APIError as exc:
            raise _to_http(exc) from exc

    def get_watchlist(self) -> dict[str, Any]:
        """Return the default watchlist with its assets."""
        wl_summary = self._get_or_create_default_watchlist()
        wl_id = wl_summary["id"]
        try:
            return _dump(self._client.get_watchlist_by_id(wl_id))
        except APIError as exc:
            raise _to_http(exc) from exc

    def add_to_watchlist(self, symbol: str) -> dict[str, Any]:
        """Add a symbol to the default watchlist."""
        wl = self._get_or_create_default_watchlist()
        try:
            return _dump(
                self._client.add_asset_to_watchlist_by_id(
                    watchlist_id=wl["id"], symbol=symbol.upper()
                )
            )
        except APIError as exc:
            raise _to_http(exc) from exc

    def remove_from_watchlist(self, symbol: str) -> dict[str, Any]:
        """Remove a symbol from the default watchlist."""
        wl = self._get_or_create_default_watchlist()
        try:
            return _dump(
                self._client.remove_asset_from_watchlist_by_id(
                    watchlist_id=wl["id"], symbol=symbol.upper()
                )
            )
        except APIError as exc:
            raise _to_http(exc) from exc


# Module-level singleton — imported by routers
alpaca_client = AlpacaClient()
