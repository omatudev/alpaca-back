"""Connection manager for WebSocket clients.

Each connected client gets a unique client_id and can subscribe to
any set of symbols. The manager tracks which symbols each client wants
and routes price updates only to interested subscribers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages all active WebSocket connections and their symbol subscriptions."""

    def __init__(self) -> None:
        # client_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # client_id → set of subscribed symbols
        self._subscriptions: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept a new WebSocket connection and register the client."""
        await websocket.accept()
        async with self._lock:
            self._connections[client_id] = websocket
            self._subscriptions[client_id] = set()
        logger.info("Client %s connected. Total: %d", client_id, len(self._connections))

    async def disconnect(self, client_id: str) -> None:
        """Remove a client and clean up its subscriptions."""
        async with self._lock:
            self._connections.pop(client_id, None)
            self._subscriptions.pop(client_id, None)
        logger.info("Client %s disconnected. Total: %d", client_id, len(self._connections))

    # ── Subscriptions ──────────────────────────────────────────────────────────

    async def subscribe(self, client_id: str, symbols: list[str]) -> set[str]:
        """Add symbols to a client's subscription set.

        Returns the client's full updated subscription set.
        """
        normalised = {s.upper() for s in symbols}
        async with self._lock:
            if client_id not in self._subscriptions:
                return set()
            self._subscriptions[client_id].update(normalised)
            return set(self._subscriptions[client_id])

    async def unsubscribe(self, client_id: str, symbols: list[str]) -> set[str]:
        """Remove symbols from a client's subscription set.

        Returns the client's full updated subscription set.
        """
        normalised = {s.upper() for s in symbols}
        async with self._lock:
            if client_id not in self._subscriptions:
                return set()
            self._subscriptions[client_id].difference_update(normalised)
            return set(self._subscriptions[client_id])

    def get_subscriptions(self, client_id: str) -> set[str]:
        """Return the current subscription set for a client (snapshot, no lock)."""
        return set(self._subscriptions.get(client_id, set()))

    def get_all_subscribed_symbols(self) -> set[str]:
        """Return the union of all symbols any client is subscribed to."""
        result: set[str] = set()
        for subs in self._subscriptions.values():
            result.update(subs)
        return result

    @property
    def client_count(self) -> int:
        return len(self._connections)

    # ── Messaging ──────────────────────────────────────────────────────────────

    async def send(self, client_id: str, data: dict[str, Any]) -> None:
        """Send a message to a single client. Silently disconnects on error."""
        ws = self._connections.get(client_id)
        if ws is None:
            return
        try:
            await ws.send_json(data)
        except Exception as exc:
            logger.warning("Failed to send to %s: %s", client_id, exc)
            await self.disconnect(client_id)

    async def broadcast_to_subscribers(self, symbol: str, data: dict[str, Any]) -> None:
        """Send a price update to all clients subscribed to the given symbol."""
        sym = symbol.upper()
        # Snapshot the relevant clients without holding the lock during I/O
        targets: list[str] = []
        async with self._lock:
            for client_id, subs in self._subscriptions.items():
                if sym in subs:
                    targets.append(client_id)

        if not targets:
            return

        await asyncio.gather(
            *(self.send(cid, data) for cid in targets),
            return_exceptions=True,
        )
