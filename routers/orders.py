from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from database.models import OrderHistory

router = APIRouter()


@router.get("/")
async def list_orders(db: AsyncSession = Depends(get_db)):
    """Return all stored orders."""
    result = await db.execute(
        select(OrderHistory).order_by(OrderHistory.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{order_id}")
async def get_order(order_id: int, db: AsyncSession = Depends(get_db)):
    """Return a single order by local DB id."""
    order = await db.get(OrderHistory, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
