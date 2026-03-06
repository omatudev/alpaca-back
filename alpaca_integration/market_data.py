"""Alpaca market data client wrapper.

Handles historical bars, latest quotes, and snapshots for both
stocks/ETFs (StockHistoricalDataClient) and crypto (CryptoHistoricalDataClient).

SDK docs: https://docs.alpaca.markets/reference/
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestQuoteRequest,
    CryptoSnapshotRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from fastapi import HTTPException

from config import settings


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _to_http(exc: APIError) -> HTTPException:
    code = getattr(exc, "status_code", 502)
    return HTTPException(status_code=code, detail=str(exc))


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


_INTRADAY_TFS = {"1min", "5min", "15min", "30min", "1hour"}
_ET = ZoneInfo("America/New_York")
_MARKET_OPEN  = time(9, 30)
_MARKET_CLOSE = time(16, 0)


def _filter_regular_hours(bars: list[dict], timeframe: str) -> list[dict]:
    """For intraday SIP bars, keep only regular market-hours candles (9:30–16:00 ET)."""
    if timeframe.lower() not in _INTRADAY_TFS:
        return bars
    result = []
    for b in bars:
        ts = b.get("timestamp")
        if not ts:
            result.append(b)
            continue
        # Alpaca timestamps are UTC ISO-8601; convert to ET
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = ts
        dt_et = dt.astimezone(_ET)
        t = dt_et.time()
        if _MARKET_OPEN <= t < _MARKET_CLOSE:
            result.append(b)
    return result


_TIMEFRAME_MAP: dict[str, TimeFrame] = {
    "1min":   TimeFrame.Minute,
    "5min":   TimeFrame(5,  TimeFrameUnit.Minute),
    "15min":  TimeFrame(15, TimeFrameUnit.Minute),
    "30min":  TimeFrame(30, TimeFrameUnit.Minute),
    "1hour":  TimeFrame.Hour,
    "4hour":  TimeFrame(4,  TimeFrameUnit.Hour),
    "1day":   TimeFrame.Day,
    "1week":  TimeFrame.Week,
    "1month": TimeFrame.Month,
}


def _parse_timeframe(tf: str) -> TimeFrame:
    """Parse a timeframe string (case-insensitive) to an alpaca TimeFrame."""
    mapped = _TIMEFRAME_MAP.get(tf.lower())
    if not mapped:
        valid = ", ".join(_TIMEFRAME_MAP.keys())
        raise HTTPException(422, f"Unknown timeframe '{tf}'. Valid options: {valid}")
    return mapped


def _is_crypto(symbol: str) -> bool:
    """Detect crypto symbols: Alpaca uses 'BTC/USD' format."""
    return "/" in symbol


# ── Market Data Client ───────────────────────────────────────────────────────────

class AlpacaMarketData:
    """Provides historical bars, quotes, and snapshots via alpaca-py."""

    def __init__(self) -> None:
        self._stock = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )
        self._crypto = CryptoHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )

    # ── Latest Quote ─────────────────────────────────────────────────────────────

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """Return the latest bid/ask quote for a symbol.

        Returns a dict with keys: symbol, bid_price, ask_price, bid_size, ask_size, timestamp.
        """
        try:
            if _is_crypto(symbol):
                req = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
                result = self._crypto.get_crypto_latest_quote(req)
            else:
                req = StockLatestQuoteRequest(symbol_or_symbols=symbol.upper(), feed='iex')
                result = self._stock.get_stock_latest_quote(req)

            sym = symbol.upper()
            quote = result.get(sym) or result.get(symbol)
            if quote is None:
                raise HTTPException(404, f"No quote data found for '{symbol}'")

            data = _dump(quote)
            data["symbol"] = sym
            return data
        except HTTPException:
            raise
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Historical Bars ──────────────────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return OHLCV bars for a symbol.

        Args:
            symbol:    Ticker (e.g. "AAPL") or crypto pair (e.g. "BTC/USD").
            timeframe: One of 1Min, 5Min, 15Min, 30Min, 1Hour, 4Hour, 1Day, 1Week, 1Month.
            start:     ISO 8601 start datetime (e.g. "2024-01-01").
            end:       ISO 8601 end datetime. Defaults to now.
            limit:     Maximum number of bars to return.

        Returns a dict with 'symbol', 'timeframe', and 'bars' list.
        """
        try:
            tf = _parse_timeframe(timeframe)
            start_dt = datetime.fromisoformat(start) if start else (datetime.now() - timedelta(days=365))
            end_dt   = datetime.fromisoformat(end)   if end   else None

            if _is_crypto(symbol):
                req = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=tf,
                    start=start_dt,
                    end=end_dt,
                    limit=limit,
                )
                bar_set = self._crypto.get_crypto_bars(req)
            else:
                req = StockBarsRequest(
                    symbol_or_symbols=symbol.upper(),
                    timeframe=tf,
                    start=start_dt,
                    end=end_dt,
                    limit=limit,
                )
                bar_set = self._stock.get_stock_bars(req)

            sym = symbol.upper()
            raw_bars = bar_set.data.get(sym) or bar_set.data.get(symbol) or []
            bars = _filter_regular_hours([_dump(b) for b in raw_bars], timeframe)

            return {"symbol": sym, "timeframe": timeframe, "bars": bars}
        except HTTPException:
            raise
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Snapshot ─────────────────────────────────────────────────────────────────

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        """Return a full snapshot: latest trade, quote, daily bar, prev daily bar.

        Includes derived fields: price, change, change_pct, volume.
        """
        try:
            if _is_crypto(symbol):
                req = CryptoSnapshotRequest(symbol_or_symbols=symbol)
                result = self._crypto.get_crypto_snapshot(req)
            else:
                req = StockSnapshotRequest(symbol_or_symbols=symbol.upper(), feed='iex')
                result = self._stock.get_stock_snapshot(req)

            sym = symbol.upper()
            snap = result.get(sym) or result.get(symbol)
            if snap is None:
                raise HTTPException(404, f"No snapshot data found for '{symbol}'")

            data = _dump(snap)
            data["symbol"] = sym
            self._enrich_snapshot(data)

            return data
        except HTTPException:
            raise
        except APIError as exc:
            raise _to_http(exc) from exc

    # ── Multiple Snapshots ────────────────────────────────────────────────────────

    def get_multiple_snapshots(self, symbols: list[str]) -> dict[str, Any]:
        """Return snapshots for multiple symbols in a single API call.

        Separates stocks and crypto automatically and merges results.
        """
        stock_syms  = [s.upper() for s in symbols if not _is_crypto(s)]
        crypto_syms = [s for s in symbols if _is_crypto(s)]
        results: dict[str, Any] = {}

        try:
            if stock_syms:
                req = StockSnapshotRequest(symbol_or_symbols=stock_syms, feed='iex')
                snaps = self._stock.get_stock_snapshot(req)
                for sym, snap in snaps.items():
                    data = _dump(snap)
                    data["symbol"] = sym
                    self._enrich_snapshot(data)
                    results[sym] = data

            if crypto_syms:
                req = CryptoSnapshotRequest(symbol_or_symbols=crypto_syms)
                snaps = self._crypto.get_crypto_snapshot(req)
                for sym, snap in snaps.items():
                    data = _dump(snap)
                    data["symbol"] = sym
                    self._enrich_snapshot(data)
                    results[sym] = data

        except APIError as exc:
            raise _to_http(exc) from exc

        return results


    @staticmethod
    def _enrich_snapshot(data: dict[str, Any]) -> None:
        """Add derived price / change / change_pct fields to a snapshot dict."""
        try:
            price = data.get("latest_trade", {}).get("price")
            if not price:
                return
            data["price"] = price

            # Try previous_daily_bar.close first, then daily_bar.open as fallback
            prev_close = (data.get("previous_daily_bar") or {}).get("close")
            if not prev_close:
                prev_close = (data.get("daily_bar") or {}).get("open")
            if prev_close:
                change = round(price - prev_close, 4)
                change_pct = round(change / prev_close * 100, 4)
                data["change"] = change
                data["change_pct"] = change_pct
        except Exception:
            pass


# Module-level singleton — imported by routers
alpaca_market_data = AlpacaMarketData()
