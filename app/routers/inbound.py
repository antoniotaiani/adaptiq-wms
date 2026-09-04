from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from app.database import get_db
from app.models import Item, Merchant, InventoryTransaction
from app.schemas import InboundDDTRequest

router = APIRouter(prefix="/api/inbound", tags=["Inbound"])

@router.post("/ddt")
async def receive_inbound_ddt(payload: InboundDDTRequest, db: AsyncSession = Depends(get_db)):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc_ref = payload.doc_reference.strip()

    try:
        # 1. Verifica mandante
        merchant = await db.get(Merchant, payload.merchant_id)
        if not merchant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail=f"Mandante con ID {payload.merchant_id} non trovato."
            )

        # 2. Processa anagrafiche articoli
        for line in payload.items:
            sku = line.sku.strip().upper()
            barcode = line.barcode.strip() if (line.barcode and line.barcode.strip()) else sku
            desc = line.description.strip()
            bin_loc = line.bin_location.strip().upper() if line.bin_location else "INBOUND"

            stmt = select(Item).where(Item.sku == sku).with_for_update()
            res = await db.execute(stmt)
            existing_item = res.scalar_one_or_none()

            if existing_item:
                if existing_item.merchant_id != payload.merchant_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Lo SKU {sku} appartiene già al mandante #{existing_item.merchant_id}."
                    )
                existing_item.barcode = barcode
                existing_item.description = desc
                existing_item.bin_location = bin_loc
                existing_item.on_hand_qty += line.quantity
            else:
                new_item = Item(
                    sku=sku,
                    barcode=barcode,
                    merchant_id=payload.merchant_id,
                    description=desc,
                    bin_location=bin_loc,
                    on_hand_qty=line.quantity
                )
                db.add(new_item)

        # FORZA LA SCRITTURA IMMEDIATA DI ITEMS SU POSTGRESQL
        await db.flush()

        # 3. Ora che gli SKU esistono fisicamente, inserisci le transazioni
        for line in payload.items:
            sku = line.sku.strip().upper()
            tx = InventoryTransaction(
                timestamp=now_str,
                sku=sku,
                transaction_type="INBOUND_RECEIVE",
                quantity=line.quantity,
                doc_reference=doc_ref
            )
            db.add(tx)

        # 4. Commit finale persistente
        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore DB durante registrazione: {str(e)}"
        )

    return {
        "status": "ok",
        "message": f"DDT {doc_ref} registrato con successo ({len(payload.items)} articoli).",
        "doc_reference": doc_ref,
        "items_count": len(payload.items)
    }
