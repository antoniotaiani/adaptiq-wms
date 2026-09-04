from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

from app.database import get_db
from app.models import Item, Merchant, DispatchOrder, DispatchOrderLine, InventoryTransaction
from app.schemas import DispatchFulfillRequest

router = APIRouter(prefix="/api/orders", tags=["Outbound"])


# --- Schemi Pydantic per la Convalida Preventiva DDT ---
class DDTLineItem(BaseModel):
    sku: str
    expected_qty: int


class DDTValidationRequest(BaseModel):
    order_number: str
    merchant_id: int
    items: List[DDTLineItem]


# --- 1. Endpoint di Convalida Preventiva DDT di Vendita ---
@router.post("/validate-ddt")
async def validate_ddt(payload: DDTValidationRequest, db: AsyncSession = Depends(get_db)):
    """
    Verifica la disponibilità di magazzino al momento della presentazione del DDT.
    Blocca la lavorazione se anche solo un articolo risulta insufficiente a scaffale.
    """
    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Il DDT presentato non contiene articoli."
        )

    order_num = payload.order_number.strip()

    # Verifica se l'ordine/DDT è già presente nel registro evasioni
    check_stmt = select(DispatchOrder.id).where(
        func.lower(DispatchOrder.order_number) == func.lower(order_num),
        DispatchOrder.merchant_id == payload.merchant_id
    )
    existing_order = (await db.execute(check_stmt)).first()
    if existing_order:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"DDT/Ordine [{order_num}] già evaso o registrato a sistema."
        )

    discrepancies = []

    for it in payload.items:
        if it.expected_qty <= 0:
            discrepancies.append({
                "sku": it.sku,
                "error": "Quantità richiesta non valida (minore o uguale a zero)",
                "requested_qty": it.expected_qty,
                "available_qty": 0
            })
            continue

        stmt = select(Item).where(
            Item.sku == it.sku,
            Item.merchant_id == payload.merchant_id
        )
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()

        if not item:
            discrepancies.append({
                "sku": it.sku,
                "error": "Articolo non censito per questo merchant",
                "requested_qty": it.expected_qty,
                "available_qty": 0
            })
        elif item.on_hand_qty < it.expected_qty:
            discrepancies.append({
                "sku": item.sku,
                "description": item.description,
                "bin_location": item.bin_location,
                "requested_qty": it.expected_qty,
                "available_qty": item.on_hand_qty,
                "deficit": it.expected_qty - item.on_hand_qty
            })

    if discrepancies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"DDT [{order_num}] bloccato: disponibilità a magazzino insufficiente.",
                "discrepancies": discrepancies
            }
        )

    return {
        "status": "valid",
        "message": f"DDT [{order_num}] approvato: merce interamente disponibile a scaffale.",
        "order_number": order_num,
        "total_lines": len(payload.items)
    }


# --- 2. Endpoint Esistenti di Workflow Outbound ---
@router.get("/check/{order_number}")
async def check_order_exists(order_number: str, merchant_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    ord_clean = order_number.strip()
    stmt = (
        select(DispatchOrder.id, DispatchOrder.processed_at, Merchant.company_name)
        .join(Merchant, DispatchOrder.merchant_id == Merchant.id)
        .where(func.lower(DispatchOrder.order_number) == func.lower(ord_clean))
    )
    if merchant_id:
        stmt = stmt.where(DispatchOrder.merchant_id == merchant_id)

    res = await db.execute(stmt)
    row = res.first()
    if row:
        return {"exists": True, "order_id": row[0], "processed_at": row[1], "merchant": row[2]}
    return {"exists": False}


@router.post("/fulfill")
async def fulfill_order(payload: DispatchFulfillRequest, db: AsyncSession = Depends(get_db)):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Impossibile evadere un ordine privo di articoli.")

    order_num = payload.order_number.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with db.begin():
        check_stmt = select(DispatchOrder.id).where(
            func.lower(DispatchOrder.order_number) == func.lower(order_num),
            DispatchOrder.merchant_id == payload.merchant_id
        )
        existing_order = (await db.execute(check_stmt)).first()
        if existing_order:
            raise HTTPException(status_code=409, detail=f"L'ordine [{order_num}] risulta già evaso.")

        total_units = sum(i.picked_qty for i in payload.items)
        disp_order = DispatchOrder(
            order_number=order_num,
            merchant_id=payload.merchant_id,
            processed_at=now_str,
            status="COMPLETED",
            total_units=total_units
        )
        db.add(disp_order)
        await db.flush()

        for it in payload.items:
            item_stmt = select(Item).where(Item.sku == it.sku).with_for_update()
            item_res = await db.execute(item_stmt)
            curr_item = item_res.scalar_one_or_none()

            if not curr_item:
                raise HTTPException(status_code=404, detail=f"Lo SKU [{it.sku}] non esiste più nel catalogo.")

            if it.picked_qty <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Quantità prelevata non valida ({it.picked_qty}) per SKU [{it.sku}]."
                )

            if it.picked_qty > curr_item.on_hand_qty:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Over-picking non consentito per SKU [{it.sku}] (Ubicazione: {curr_item.bin_location}). "
                        f"Giacenza disponibile: {curr_item.on_hand_qty}, richiesta dal prelievo: {it.picked_qty}."
                    )
                )

            if it.picked_qty > it.expected_qty:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Quantità prelevata ({it.picked_qty}) superiore a quella "
                        f"prevista dall'ordine ({it.expected_qty}) per SKU [{it.sku}]."
                    )
                )

            curr_item.on_hand_qty -= it.picked_qty

            line = DispatchOrderLine(
                order_id=disp_order.id,
                sku=it.sku,
                barcode=it.barcode,
                description=it.description,
                bin_location=it.bin_location,
                expected_qty=it.expected_qty,
                picked_qty=it.picked_qty
            )
            db.add(line)

            tx = InventoryTransaction(
                timestamp=now_str,
                sku=it.sku,
                transaction_type="OUTBOUND_PICK",
                quantity=-it.picked_qty,
                doc_reference=order_num
            )
            db.add(tx)

    return {"status": "success", "order_id": disp_order.id, "timestamp": now_str}


@router.get("/history")
async def orders_history(date_filter: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            DispatchOrder.id, DispatchOrder.order_number, Merchant.company_name,
            DispatchOrder.processed_at, DispatchOrder.total_units, DispatchOrder.status
        )
        .join(Merchant, DispatchOrder.merchant_id == Merchant.id)
    )
    if date_filter:
        stmt = stmt.where(DispatchOrder.processed_at.like(f"{date_filter}%"))
    stmt = stmt.order_by(DispatchOrder.id.desc())

    res = await db.execute(stmt)
    rows = res.fetchall()
    return [
        {"id": r[0], "order_number": r[1], "merchant": r[2], "processed_at": r[3], "total_units": r[4], "status": r[5]}
        for r in rows
    ]


@router.get("/{order_id}")
async def order_detail(order_id: int, db: AsyncSession = Depends(get_db)):
    stmt_header = (
        select(DispatchOrder.id, DispatchOrder.order_number, Merchant.company_name, DispatchOrder.processed_at, DispatchOrder.total_units)
        .join(Merchant, DispatchOrder.merchant_id == Merchant.id)
        .where(DispatchOrder.id == order_id)
    )
    res_h = await db.execute(stmt_header)
    h = res_h.first()
    if not h:
        raise HTTPException(status_code=404, detail="Ordine non trovato.")

    stmt_lines = select(DispatchOrderLine).where(DispatchOrderLine.order_id == order_id)
    res_l = await db.execute(stmt_lines)
    lines = res_l.scalars().all()

    return {
        "header": {"id": h[0], "order_number": h[1], "merchant": h[2], "processed_at": h[3], "total_units": h[4]},
        "lines": [
            {"sku": l.sku, "barcode": l.barcode, "description": l.description, "bin_location": l.bin_location, "expected_qty": l.expected_qty, "picked_qty": l.picked_qty}
            for l in lines
        ]
    }
