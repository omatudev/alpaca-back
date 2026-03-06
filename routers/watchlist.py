"""Watchlist router — backed by Alpaca /v2/watchlists API.

All endpoints live under the /api/watchlist prefix registered in main.py.
Uses a single default watchlist named 'MyBroker' on the Alpaca account.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from alpaca_integration.client import alpaca_client

router = APIRouter()


class WatchlistAddRequest(BaseModel):
    symbol: str

    model_config = {"json_schema_extra": {"examples": [{"symbol": "AAPL"}]}}


@router.get("/", summary="Get watchlist")
def get_watchlist() -> dict[str, Any]:
    """Return the default watchlist with all assets."""
    return alpaca_client.get_watchlist()


@router.post("/", status_code=201, summary="Add symbol to watchlist")
def add_to_watchlist(body: WatchlistAddRequest) -> dict[str, Any]:
    """Add a symbol to the default watchlist."""
    return alpaca_client.add_to_watchlist(body.symbol)


@router.delete("/{symbol}", status_code=204, summary="Remove symbol from watchlist")
def remove_from_watchlist(symbol: str) -> None:
    """Remove a symbol from the default watchlist."""
    alpaca_client.remove_from_watchlist(symbol)
