"""Real-time price feed for WebSocket clients.

Architecture
------------
Two modes, selected automatically based on market hours:

LIVE mode  (market open, Mon–Fri 09:30–16:00 ET)
  • Connects to Alpaca's WebSocket stream via StockDataStream for trades.
  • Runs the SDK's blocking loop in a background thread.
  • An asyncio.Queue bridges the stream thread → the main event loop.
  • Falls back to fast polling (2 s) if stream fails.
  • Checks subscription changes every 5 s (not 60 s).

POLLING mode  (market closed or stream unavailable)
  • Polls get_snapshot() for every subscribed symbol.
  • Interval: POLL_INTERVAL_LIVE (2 s) when open, POLL_INTERVAL_CLOSED (15 s) when closed.

The feed_loop refreshes which mode to run every SUBSCRIPTION_CHECK seconds
so subscription changes are picked up fast.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings
from alpaca_integration.market_data import alpaca_market_data
from streaming.manager import ConnectionManager

logger = logging.getLogger(__name__)

POLL_INTERVAL_LIVE    = 1    # seconds between REST polls during market hours
POLL_INTERVAL_CLOSED  = 10   # seconds between REST polls outside market hours
SUBSCRIPTION_CHECK    = 2    # how often to check if subscriptions changed in live mode
RECONNECT_BASE_DELAY  = 1    # exponential back-off base (seconds)
RECONNECT_MAX_DELAY   = 30   # cap

_ET_OFFSET = timedelta(hours=-5)   # EST; change to -4 during EDT


def _is_market_open() -> bool:
    """Return True during NYSE regular trading hours (09:30–16:00 ET, Mon–Fri)."""
    et_now = datetime.now(timezone.utc).astimezone(timezone(_ET_OFFSET))
    if et_now.weekday() >= 5:
        return False
    open_  = et_now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = et_now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= et_now < close_


def _build_price_msg(symbol: str, data: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw trade/snapshot dict to the standard price WebSocket message."""
    price = data.get("price")
    bid = data.get("bid_price") or data.get("bp")
    ask = data.get("ask_price") or data.get("ap")
    if price is None and bid is not None and ask is not None:
        try:
            price = round((float(bid) + float(ask)) / 2, 4)
        except (TypeError, ValueError):
            pass
    if price is None:
        price = bid or ask
    return {
        "type": "price",
        "symbol": symbol.upper(),
        "price": price,
        "bid": bid,
        "ask": ask,
        "timestamp": str(data.get("timestamp", "")),
    }


class PriceFeed:
    """Background price feed — start it in FastAPI's lifespan."""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager
        self._running  = False

        self._feed_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None

        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

        self._stream: Any = None
        self._stream_thread: threading.Thread | None = None
        self._stream_symbols: set[str] = set()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._main_loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._consumer_task = asyncio.create_task(self._consumer(), name="price-consumer")
        self._feed_task     = asyncio.create_task(self._feed_loop(), name="price-feed")
        logger.info("PriceFeed started")

    async def stop(self) -> None:
        self._running = False
        self._stop_stream()
        for task in (self._feed_task, self._consumer_task):
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        logger.info("PriceFeed stopped")

    # ── Internal: consumer ─────────────────────────────────────────────────────

    async def _consumer(self) -> None:
        """Drain the bridge queue and broadcast price messages with zero delay."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=0.05)  # type: ignore[arg-type]
                symbol = msg.get("symbol", "")
                if symbol:
                    await self._manager.broadcast_to_subscribers(symbol, msg)
                self._queue.task_done()  # type: ignore[union-attr]
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Consumer error: %s", exc)

    # ── Internal: main feed loop ───────────────────────────────────────────────

    async def _feed_loop(self) -> None:
        reconnect_delay = RECONNECT_BASE_DELAY

        while self._running:
            symbols = self._manager.get_all_subscribed_symbols()

            if not symbols:
                await asyncio.sleep(2)
                reconnect_delay = RECONNECT_BASE_DELAY
                continue

            if _is_market_open():
                try:
                    await self._run_live_mode(symbols)
                    reconnect_delay = RECONNECT_BASE_DELAY
                except Exception as exc:
                    logger.error("Live mode failed: %s — polling fallback for %ds", exc, reconnect_delay)
                    self._stop_stream()
                    # Fallback: do fast polling while waiting to retry stream
                    await self._run_polling_mode(symbols, POLL_INTERVAL_LIVE)
                    reconnect_delay = min(reconnect_delay * 2, RECONNECT_MAX_DELAY)
            else:
                try:
                    await self._run_polling_mode(symbols, POLL_INTERVAL_CLOSED)
                except Exception as exc:
                    logger.warning("Polling error: %s", exc)
                    await asyncio.sleep(2)

    # ── Internal: live (Alpaca stream) ─────────────────────────────────────────

    async def _run_live_mode(self, symbols: set[str]) -> None:
        """Run Alpaca streaming WebSocket. Check subscriptions every few seconds."""
        logger.info("Live mode: subscribing to %s", symbols)
        self._stop_stream()

        stock_syms  = {s for s in symbols if "/" not in s}
        crypto_syms = {s for s in symbols if "/" in s}

        if not stock_syms and not crypto_syms:
            await asyncio.sleep(2)
            return

        self._stream_symbols = set(symbols)
        self._stream_thread = threading.Thread(
            target=self._run_stream_thread,
            args=(stock_syms, crypto_syms),
            daemon=True,
            name="alpaca-stream",
        )
        self._stream_thread.start()

        # Now loop with fast checks — stream delivers ticks, we just monitor state
        while self._running:
            await asyncio.sleep(SUBSCRIPTION_CHECK)

            current = self._manager.get_all_subscribed_symbols()
            if not _is_market_open():
                logger.info("Market closed — switching to polling")
                break
            if current != self._stream_symbols:
                logger.info("Subscriptions changed — restarting stream")
                break
            if self._stream_thread and not self._stream_thread.is_alive():
                raise RuntimeError("Stream thread died unexpectedly")

    def _run_stream_thread(self, stock_syms: set[str], crypto_syms: set[str]) -> None:
        """Background thread — Alpaca stream (blocking call)."""
        try:
            from alpaca.data.live import CryptoDataStream, StockDataStream
        except ImportError as exc:
            logger.error("alpaca-py streaming not available: %s", exc)
            return

        loop = self._main_loop
        queue = self._queue

        async def on_trade(data: Any) -> None:
            try:
                raw = data.model_dump(mode="json") if hasattr(data, "model_dump") else dict(data)
                symbol = raw.get("symbol", "")
                msg = _build_price_msg(symbol, raw)
                if loop and queue:
                    asyncio.run_coroutine_threadsafe(queue.put(msg), loop)
            except Exception as exc:
                logger.warning("on_trade error: %s", exc)

        async def on_quote(data: Any) -> None:
            """Quotes arrive faster than trades — use mid-price for real-time updates."""
            try:
                raw = data.model_dump(mode="json") if hasattr(data, "model_dump") else dict(data)
                symbol = raw.get("symbol", "")
                msg = _build_price_msg(symbol, raw)
                if msg.get("price") is not None and loop and queue:
                    asyncio.run_coroutine_threadsafe(queue.put(msg), loop)
            except Exception as exc:
                logger.warning("on_quote error: %s", exc)

        try:
            if stock_syms:
                self._stream = StockDataStream(
                    api_key=settings.alpaca_api_key,
                    secret_key=settings.alpaca_secret_key,
                    feed='iex',
                )
                self._stream.subscribe_trades(on_trade, *stock_syms)
                self._stream.subscribe_quotes(on_quote, *stock_syms)
                logger.info("Alpaca stream subscribing to stock trades+quotes: %s", stock_syms)
                self._stream.run()

            if crypto_syms:
                crypto_stream = CryptoDataStream(
                    api_key=settings.alpaca_api_key,
                    secret_key=settings.alpaca_secret_key,
                )
                crypto_stream.subscribe_trades(on_trade, *crypto_syms)
                logger.info("Alpaca stream subscribing to crypto trades: %s", crypto_syms)
                crypto_stream.run()

        except Exception as exc:
            logger.error("Alpaca stream thread error: %s", exc)
        finally:
            logger.info("Alpaca stream thread exiting")

    def _stop_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            self._stream = None
        if self._stream_thread is not None and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=3)
            self._stream_thread = None

    # ── Internal: polling mode ──────────────────────────────────────────────────

    async def _run_polling_mode(self, symbols: set[str], interval: int) -> None:
        """Poll snapshot for each subscribed symbol, then sleep."""
        logger.debug("Polling %d symbols (interval %ds)", len(symbols), interval)
        tasks = [self._poll_symbol(sym) for sym in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

        for _ in range(interval):
            if not self._running:
                return
            if _is_market_open() and interval == POLL_INTERVAL_CLOSED:
                return
            # Also break early if subscriptions changed
            current = self._manager.get_all_subscribed_symbols()
            if current != symbols:
                return
            await asyncio.sleep(1)

    async def _poll_symbol(self, symbol: str) -> None:
        """Fetch latest price via snapshot and push to queue."""
        try:
            snap = await asyncio.to_thread(alpaca_market_data.get_snapshot, symbol)
            if snap and self._queue:
                price = snap.get("price")
                msg = {
                    "type": "price",
                    "symbol": symbol.upper(),
                    "price": price,
                    "bid": None,
                    "ask": None,
                    "timestamp": str(snap.get("latest_trade", {}).get("timestamp", "")),
                }
                await self._queue.put(msg)
        except Exception as exc:
            logger.debug("Poll failed for %s: %s", symbol, exc)
