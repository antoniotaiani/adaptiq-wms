from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Optional

from app.database import get_db
from app.models import Item, Merchant, InventoryTransaction
from app.schemas import InventoryAdjustRequest

router = APIRouter(prefix="/api", tags=["Inventory"])

@router.get("/inventory")
async def get_inventory(merchant_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            Item.sku, Item.barcode, Item.description, Item.bin_location,
            Item.on_hand_qty, Merchant.company_name, Item.merchant_id
        )
        .join(Merchant, Item.merchant_id == Merchant.id)
    )
    if merchant_id:
        stmt = stmt.where(Item.merchant_id == merchant_id).order_by(Item.bin_location.asc(), Item.sku.asc())
    else:
        stmt = stmt.order_by(Merchant.company_name.asc(), Item.bin_location.asc(), Item.sku.asc())

    res = await db.execute(stmt)
    rows = res.fetchall()
    return [
        {
            "sku": r[0], "barcode": r[1], "description": r[2],
            "bin_location": r[3], "on_hand_qty": r[4],
            "merchant_name": r[5], "merchant_id": r[6]
        }
        for r in rows
    ]

@router.post("/inventory/adjust")
async def adjust_inventory(payload: InventoryAdjustRequest, db: AsyncSession = Depends(get_db)):
    if payload.new_quantity < 0:
        raise HTTPException(status_code=400, detail="On-hand stock cannot be negative.")

    sku = payload.sku.strip().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with db.begin():
        stmt = select(Item).where(Item.sku == sku).with_for_update()
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="SKU not found.")

        delta = payload.new_quantity - item.on_hand_qty
        item.on_hand_qty = payload.new_quantity

        tx = InventoryTransaction(
            timestamp=now_str,
            sku=sku,
            transaction_type="CYCLE_COUNT_ADJUSTMENT",
            quantity=delta,
            doc_reference=payload.reason.strip() if payload.reason else "MANUAL_CYCLE_COUNT"
        )
        db.add(tx)

    return {"status": "ok", "message": f"Stock for {sku} adjusted to {payload.new_quantity} (Delta: {delta:+d})"}

@router.post("/items/resolve")
async def resolve_item(payload: dict, db: AsyncSession = Depends(get_db)):
    code = payload.get("code", "").strip()
    stmt = (
        select(
            Item.sku, Item.barcode, Item.description, Item.bin_location,
            Item.on_hand_qty, Item.merchant_id, Merchant.company_name
        )
        .join(Merchant, Item.merchant_id == Merchant.id)
        .where((Item.sku == code) | (Item.barcode == code))
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Barcode/SKU [{code}] not found.")

    return {
        "sku": row[0], "barcode": row[1], "description": row[2],
        "bin_location": row[3], "on_hand_qty": row[4],
        "merchant_id": row[5], "merchant_name": row[6]
    }
