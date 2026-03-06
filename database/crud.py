"""CRUD helpers for local SQLite database.

All functions accept an AsyncSession and are async.
They do NOT commit — callers are responsible for committing when needed,
or using the get_db dependency which auto-commits on success.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import OrderHistory, Watchlist


# ── Watchlist ──────────────────────────────────────────────────────────────────


async def get_watchlist(db: AsyncSession) -> list[Watchlist]:
    """Return all watchlist entries ordered by symbol."""
    result = await db.execute(select(Watchlist).order_by(Watchlist.symbol))
    return list(result.scalars().all())


async def add_to_watchlist(
    db: AsyncSession,
    symbol: str,
    name: str | None = None,
    asset_class: str | None = None,
    broker: str | None = None,
    notes: str | None = None,
) -> Watchlist:
    """Insert a new watchlist entry. Raises ValueError if symbol already exists."""
    sym = symbol.upper()
    existing = await db.execute(select(Watchlist).where(Watchlist.symbol == sym))
    if existing.scalar_one_or_none():
        raise ValueError(f"'{sym}' is already in the watchlist")

    entry = Watchlist(
        symbol=sym,
        name=name,
        asset_class=asset_class,
        broker=broker,
        notes=notes,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def remove_from_watchlist(db: AsyncSession, symbol: str) -> bool:
    """Delete a watchlist entry. Returns True if deleted, False if not found."""
    sym = symbol.upper()
    result = await db.execute(select(Watchlist).where(Watchlist.symbol == sym))
    entry = result.scalar_one_or_none()
    if not entry:
        return False
    await db.delete(entry)
    await db.commit()
    return True


async def is_in_watchlist(db: AsyncSession, symbol: str) -> bool:
    """Return True if symbol is in the watchlist."""
    result = await db.execute(
        select(Watchlist).where(Watchlist.symbol == symbol.upper())
    )
    return result.scalar_one_or_none() is not None


# ── OrderHistory ───────────────────────────────────────────────────────────────


def _parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse an ISO 8601 string or datetime to a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def sync_order(db: AsyncSession, order_data: dict[str, Any]) -> OrderHistory:
    """Insert or update an order from Alpaca into the local order history.

    ``order_data`` must be the dict returned by AlpacaClient.get_orders()
    or place_order() — i.e. already serialised via model_dump(mode="json").

    Key mapping:
        order_data["id"]               → broker_order_id
        order_data["type"]             → order_type
        order_data["filled_avg_price"] → filled_price
    """
    alpaca_id = order_data["id"]

    result = await db.execute(
        select(OrderHistory).where(OrderHistory.broker_order_id == alpaca_id)
    )
    record = result.scalar_one_or_none()

    def _float(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    fields: dict[str, Any] = {
        "broker_order_id": alpaca_id,
        "symbol": order_data.get("symbol", ""),
        "side": order_data.get("side", ""),
        "qty": _float(order_data.get("qty")) or 0.0,
        "order_type": order_data.get("type", ""),
        "time_in_force": order_data.get("time_in_force"),
        "limit_price": _float(order_data.get("limit_price")),
        "stop_price": _float(order_data.get("stop_price")),
        "filled_price": _float(order_data.get("filled_avg_price")),
        "filled_qty": _float(order_data.get("filled_qty")),
        "status": order_data.get("status", ""),
        "broker": "alpaca",
        "submitted_at": _parse_dt(order_data.get("submitted_at") or order_data.get("created_at")),
        "filled_at": _parse_dt(order_data.get("filled_at")),
    }

    if record is None:
        record = OrderHistory(**fields)
        db.add(record)
    else:
        for k, v in fields.items():
            setattr(record, k, v)

    await db.commit()
    await db.refresh(record)
    return record


async def get_order_history(
    db: AsyncSession, limit: int = 100
) -> list[OrderHistory]:
    """Return the most recent orders from local history, newest first."""
    result = await db.execute(
        select(OrderHistory)
        .order_by(OrderHistory.submitted_at.desc().nullslast())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_order_by_alpaca_id(
    db: AsyncSession, alpaca_order_id: str
) -> OrderHistory | None:
    """Find a specific order by its Alpaca order ID."""
    result = await db.execute(
        select(OrderHistory).where(OrderHistory.broker_order_id == alpaca_order_id)
    )
    return result.scalar_one_or_none()
