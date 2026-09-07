from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import traceback

from app.database import get_db
from app.models import Item, Merchant, InventoryTransaction
from app.schemas import InboundDDTRequest

router = APIRouter(prefix="/api/inbound", tags=["Inbound"])


async def get_next_sku_sequence(prefix: str, db: AsyncSession) -> int:
    """
    Calcola il massimo progressivo numerico per il prefisso dato.
    Estrae tutti gli SKU compatibili e calcola il massimo intero
    per evitare errori dovuti all'ordinamento alfabetico delle stringhe in SQL.
    """
    stmt = select(Item.sku).where(Item.sku.like(f"{prefix}%"))
    res = await db.execute(stmt)
    existing_skus = res.scalars().all()

    max_seq = 0
    for s in existing_skus:
        try:
            parts = s.split("-")
            val = int(parts[-1])
            if val > max_seq:
                max_seq = val
        except (ValueError, IndexError):
            continue

    return max_seq + 1


@router.post("/ddt")
async def receive_inbound_ddt(payload: InboundDDTRequest, db: AsyncSession = Depends(get_db)):
    """
    Elabora il carico merce da DDT:
    - Rifiuta righe con barcode duplicati all'interno dello stesso documento.
    - Se il barcode è già associato al mandante, ne aggiorna scorta e ubicazione.
    - Se il barcode è nuovo, genera uno SKU sequenziale garantito inedito sia nel DDT che a database.
    - Scrive nel ledger inventariale e traccia gli errori a terminale con traceback completo.
    """
    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Il DDT non contiene articoli."
        )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc_ref = payload.doc_reference.strip()

    # 1. Verifica assenza di barcode duplicati all'interno della richiesta
    raw_barcodes = [
        line.barcode.strip()
        for line in payload.items
        if line.barcode and line.barcode.strip()
    ]
    if len(raw_barcodes) != len(set(raw_barcodes)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rilevate righe duplicate con lo stesso Barcode all'interno dello stesso DDT. Unifica le quantità prima di confermare."
        )

    try:
        merchant = await db.get(Merchant, payload.merchant_id)
        if not merchant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mandante con ID {payload.merchant_id} non trovato."
            )

        clean_code = merchant.account_code.replace("MCH-", "").replace(" ", "").upper()
        prefix = f"SKU-{clean_code}-{datetime.now().year}-"

        # Recupera il contatore iniziale massimo effettivo
        current_seq = await get_next_sku_sequence(prefix, db)
        assigned_in_doc = set()
        processed_lines = []

        for line in payload.items:
            if line.quantity <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tutte le quantità caricate devono essere maggiori di zero."
                )

            desc = line.description.strip()
            bin_loc = line.bin_location.strip().upper() if (line.bin_location and line.bin_location.strip()) else "INBOUND"
            barcode = line.barcode.strip() if (line.barcode and line.barcode.strip()) else ""

            # Ricerca l'articolo per Barcode associato a questo mandante
            existing_item = None
            if barcode:
                stmt_bc = select(Item).where(
                    Item.barcode == barcode,
                    Item.merchant_id == payload.merchant_id
                ).with_for_update()
                res_bc = await db.execute(stmt_bc)
                existing_item = res_bc.scalar_one_or_none()

            if existing_item:
                if existing_item.merchant_id != payload.merchant_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"L'articolo [{existing_item.sku}] appartiene a un altro mandante (#{existing_item.merchant_id})."
                    )
                existing_item.description = desc
                existing_item.bin_location = bin_loc
                existing_item.on_hand_qty += line.quantity
                final_sku = existing_item.sku
            else:
                # Se lo SKU è già stato indicato esplicitamente (non vuoto), usalo, altrimenti genera il sequenziale
                custom_sku = line.sku.strip().upper() if (line.sku and line.sku.strip()) else ""
                
                if custom_sku:
                    final_sku = custom_sku
                else:
                    # Generazione SKU sequenziale garantito sia nel documento che nel database
                    while True:
                        candidate_sku = f"{prefix}{current_seq:04d}"
                        current_seq += 1

                        if candidate_sku in assigned_in_doc:
                            continue

                        chk_stmt = select(Item.sku).where(Item.sku == candidate_sku)
                        chk_res = await db.execute(chk_stmt)
                        if chk_res.scalar_one_or_none() is None:
                            final_sku = candidate_sku
                            break

                assigned_in_doc.add(final_sku)
                final_barcode = barcode if barcode else final_sku

                new_item = Item(
                    sku=final_sku,
                    barcode=final_barcode,
                    merchant_id=payload.merchant_id,
                    description=desc,
                    bin_location=bin_loc,
                    on_hand_qty=line.quantity
                )
                db.add(new_item)

            processed_lines.append((final_sku, line.quantity))

        # Scrive su PostgreSQL per soddisfare i vincoli FK
        await db.flush()

        # Scrittura Ledger di magazzino
        for sku_code, qty in processed_lines:
            tx = InventoryTransaction(
                timestamp=now_str,
                sku=sku_code,
                transaction_type="INBOUND_RECEIVE",
                quantity=qty,
                doc_reference=doc_ref
            )
            db.add(tx)

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        print("\n" + "=" * 50)
        print(">>> TRACEBACK ERRORE INBOUND DDT REGISTRATO <<<")
        traceback.print_exc()
        print("=" * 50 + "\n")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore durante la registrazione del DDT: {str(e)}"
        )

    return {
        "status": "ok",
        "message": f"DDT {doc_ref} registrato con successo. Elaborati {len(payload.items)} articoli con SKU sequenziali univoci.",
        "doc_reference": doc_ref,
        "items_count": len(payload.items)
    }
