"""WebSocket router — ws://localhost:8000/ws/prices

Protocol (JSON messages)
------------------------
Client → Server:
  { "action": "subscribe",   "symbols": ["AAPL", "TSLA"] }
  { "action": "unsubscribe", "symbols": ["AAPL"] }
  { "action": "ping" }

Server → Client:
  { "type": "price",        "symbol": "AAPL", "price": 189.5, "bid": 189.4,
                             "ask": 189.6, "timestamp": "2026-01-01T14:30:00Z" }
  { "type": "subscribed",   "symbols": ["AAPL", "TSLA"] }
  { "type": "unsubscribed", "symbols": ["AAPL"] }
  { "type": "pong" }
  { "type": "error",        "message": "..." }
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from auth import verify_ws_token
from streaming.manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared manager instance — imported by main.py to pass to PriceFeed
manager = ConnectionManager()


@router.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
    """WebSocket endpoint for real-time price updates."""
    if not verify_ws_token(token):
        await websocket.close(code=4001)
        return
    client_id = str(uuid.uuid4())
    await manager.connect(websocket, client_id)

    try:
        while True:
            # Receive and parse a client message
            try:
                raw = await websocket.receive_text()
                data = json.loads(raw)
            except (ValueError, KeyError):
                await manager.send(client_id, {"type": "error", "message": "Invalid JSON"})
                continue

            action = data.get("action", "")

            if action == "subscribe":
                symbols: list[str] = data.get("symbols", [])
                if not symbols or not all(isinstance(s, str) for s in symbols):
                    await manager.send(
                        client_id,
                        {"type": "error", "message": "'symbols' must be a non-empty list of strings"},
                    )
                    continue
                current = await manager.subscribe(client_id, symbols)
                await manager.send(
                    client_id,
                    {"type": "subscribed", "symbols": sorted(current)},
                )
                logger.debug("Client %s subscribed to %s", client_id, symbols)

            elif action == "unsubscribe":
                symbols = data.get("symbols", [])
                if not symbols:
                    await manager.send(
                        client_id,
                        {"type": "error", "message": "'symbols' must be a non-empty list"},
                    )
                    continue
                current = await manager.unsubscribe(client_id, symbols)
                await manager.send(
                    client_id,
                    {"type": "unsubscribed", "symbols": sorted(current)},
                )
                logger.debug("Client %s unsubscribed from %s", client_id, symbols)

            elif action == "ping":
                await manager.send(client_id, {"type": "pong"})

            else:
                await manager.send(
                    client_id,
                    {"type": "error", "message": f"Unknown action: '{action}'"},
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket error for client %s: %s", client_id, exc)
    finally:
        await manager.disconnect(client_id)
