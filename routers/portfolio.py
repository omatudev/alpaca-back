"""Portfolio router — /api/portfolio

Endpoints
---------
Portfolio summary
  GET    /api/portfolio/summary              account info + positions with P&L

Portfolio equity chart
  GET    /api/portfolio/equity-history       equity history as bar-like data

Order history
  GET    /api/portfolio/history              sync closed orders → return full history
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from alpaca_integration.client import alpaca_client
from alpaca_integration.market_data import alpaca_market_data
from database import get_db
from database.crud import (
    get_order_history,
    sync_order,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────────


def _row_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy model row to a plain dict."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# ── Portfolio summary ──────────────────────────────────────────────────────────────


@router.get(
    "/summary",
    summary="Portfolio summary",
    description=(
        "Combines Alpaca account info and open positions. "
        "Each position is enriched with current price and P&L "
        "fetched from a single batch snapshot call."
    ),
)
def get_summary() -> dict[str, Any]:
    account = alpaca_client.get_account()
    positions = alpaca_client.get_positions()

    # Enrich positions with live price + unrealized P&L
    enriched: list[dict[str, Any]] = []
    if positions:
        symbols = [p["symbol"] for p in positions]
        try:
            snapshots = alpaca_market_data.get_multiple_snapshots(symbols)
        except Exception:
            snapshots = {}

        for pos in positions:
            sym = pos["symbol"]
            snap = snapshots.get(sym, {})
            current_price = snap.get("price") or _safe_float(pos.get("current_price"))
            avg_entry    = _safe_float(pos.get("avg_entry_price"))
            qty          = _safe_float(pos.get("qty"))

            unrealized_pl = None
            unrealized_pl_pct = None
            if current_price and avg_entry and qty:
                unrealized_pl = round((current_price - avg_entry) * qty, 4)
                if avg_entry:
                    unrealized_pl_pct = round((current_price - avg_entry) / avg_entry * 100, 4)

            enriched.append({
                **pos,
                "current_price": current_price,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                "daily_change": snap.get("change"),
                "daily_change_pct": snap.get("change_pct"),
            })

    return {
        "account": {
            "id": account.get("id"),
            "account_number": account.get("account_number"),
            "status": account.get("status"),
            "currency": account.get("currency"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "regt_buying_power": account.get("regt_buying_power"),
            "portfolio_value": account.get("portfolio_value"),
            "equity": account.get("equity"),
            "is_paper": account.get("is_paper_account", True),
        },
        "positions": enriched,
        "position_count": len(enriched),
    }


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Portfolio equity history ──────────────────────────────────────────────────────


@router.get(
    "/equity-history",
    summary="Portfolio equity chart",
    description=(
        "Returns portfolio equity over time as bar-like data. "
        "period: 1D, 1W, 1M, 3M, 1A. timeframe: 5Min, 15Min, 1H, 1D."
    ),
)
def get_equity_history(
    period: str = Query(default="1D", description="1D, 1W, 1M, 3M, 1A"),
    timeframe: str = Query(default="5Min", description="5Min, 15Min, 1H, 1D"),
) -> dict[str, Any]:
    return alpaca_client.get_portfolio_history(
        period=period,
        timeframe=timeframe,
    )


# ── Order history ─────────────────────────────────────────────────────────────────


@router.get(
    "/history",
    summary="Order history",
    description=(
        "Fetches all closed orders from Alpaca, syncs them to the database, then returns the complete local order history."
    ),
)
async def get_history(
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    # Pull closed orders from Alpaca and persist locally
    try:
        closed = alpaca_client.get_orders(status="closed", limit=500)
        for order in closed:
            await sync_order(db, order)
    except HTTPException:
        pass  # If Alpaca is unreachable, still return whatever is cached locally

    history = await get_order_history(db, limit=limit)
    return [_row_to_dict(h) for h in history]

