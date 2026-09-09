from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional

from app.database import get_db
from app.models import Item, Merchant, InventoryTransaction, DispatchOrderLine
from app.schemas import InventoryAdjustRequest

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])


@router.get("")
async def get_inventory(
    merchant_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Restituisce l'elenco giacenze di magazzino arricchito con la ragione sociale del mandante.
    """
    stmt = select(
        Item.sku,
        Item.barcode,
        Item.description,
        Item.bin_location,
        Item.on_hand_qty,
        Item.merchant_id,
        Merchant.company_name.label("merchant_name")
    ).join(Merchant, Item.merchant_id == Merchant.id)

    if merchant_id:
        stmt = stmt.where(Item.merchant_id == merchant_id)

    stmt = stmt.order_by(Item.bin_location, Item.sku)
    res = await db.execute(stmt)
    rows = res.all()

    return [
        {
            "sku": r.sku,
            "barcode": r.barcode,
            "description": r.description,
            "bin_location": r.bin_location,
            "on_hand_qty": r.on_hand_qty,
            "merchant_id": r.merchant_id,
            "merchant_name": r.merchant_name or f"Mandante #{r.merchant_id}"
        }
        for r in rows
    ]


@router.post("/adjust")
async def adjust_stock(payload: InventoryAdjustRequest, db: AsyncSession = Depends(get_db)):
    """
    Rettifica la giacenza a scaffale di un articolo e traccia la modifica.
    """
    item = await db.get(Item, payload.sku.strip())
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Articolo con SKU '{payload.sku}' non trovato."
        )

    old_qty = item.on_hand_qty
    delta = payload.new_quantity - old_qty
    item.on_hand_qty = payload.new_quantity

    from datetime import datetime
    tx = InventoryTransaction(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        sku=item.sku,
        transaction_type=f"ADJUSTMENT ({payload.reason})",
        quantity=delta,
        doc_reference=f"RETTIFICA DA {old_qty} A {payload.new_quantity}"
    )
    db.add(tx)
    await db.commit()

    return {
        "status": "ok",
        "sku": item.sku,
        "old_quantity": old_qty,
        "new_quantity": item.on_hand_qty
    }


@router.delete("/merchant-item/{sku}")
async def remove_item_from_merchant(sku: str, db: AsyncSession = Depends(get_db)):
    """
    Rimuove la referenza e la relativa giacenza dal mandante indicato.
    Non tocca gli altri committenti che condividono lo stesso barcode.
    """
    sku_clean = sku.strip()

    item = await db.get(Item, sku_clean)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Articolo [{sku_clean}] non trovato a magazzino."
        )

    stmt_orders = select(DispatchOrderLine).where(DispatchOrderLine.sku == sku_clean)
    res_orders = await db.execute(stmt_orders)
    if res_orders.first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Impossibile stornare la referenza [{sku_clean}]: risulta già inclusa in documenti "
                f"di spedizione/ordini evasi per questo cliente."
            )
        )

    merchant = await db.get(Merchant, item.merchant_id)
    merchant_name = merchant.company_name if merchant else f"ID #{item.merchant_id}"
    qty_stornata = item.on_hand_qty

    try:
        await db.execute(
            delete(InventoryTransaction).where(InventoryTransaction.sku == sku_clean)
        )
        await db.delete(item)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore durante lo storno dell'articolo: {str(e)}"
        )

    return {
        "status": "ok",
        "message": f"Referenza [{sku_clean}] e giacenza ({qty_stornata} pz) rimosse con successo dal mandante '{merchant_name}'."
    }
