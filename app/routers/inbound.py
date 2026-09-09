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
async def receive_inbound_ddt(
    payload: InboundDDTRequest,
    allow_shared_barcode: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """
    Elabora il carico merce da DDT:
    - Rifiuta righe con barcode duplicati all'interno dello stesso documento.
    - Se il barcode appartiene già al mandante selezionato, incrementa la giacenza.
    - Se appartiene a un altro mandante e 'allow_shared_barcode' è False, risponde con HTTP 409 Conflict.
    - Se confermato (allow_shared_barcode=True), crea un nuovo SKU sequenziale per il nuovo mandante.
    - Registra i movimenti nel ledger di magazzino.
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

        # 2. Controllo preventivo: barcode condivisi con altri committenti
        if not allow_shared_barcode:
            conflicts = []
            for line in payload.items:
                bc = line.barcode.strip() if line.barcode else ""
                if not bc:
                    continue

                # Cerca se già esiste per un altro mandante
                stmt_other = select(Item, Merchant).join(
                    Merchant, Item.merchant_id == Merchant.id
                ).where(
                    Item.barcode == bc,
                    Item.merchant_id != payload.merchant_id
                )
                res_other = await db.execute(stmt_other)
                other_owner = res_other.first()

                # Cerca se esiste già per il mandante corrente
                stmt_self = select(Item).where(
                    Item.barcode == bc,
                    Item.merchant_id == payload.merchant_id
                )
                res_self = await db.execute(stmt_self)
                self_item = res_self.scalar_one_or_none()

                # Se appartiene ad un altro e non è ancora registrato per questo mandante
                if other_owner and not self_item:
                    item_obj, mch_obj = other_owner
                    conflicts.append({
                        "barcode": bc,
                        "description": line.description,
                        "other_merchant_name": mch_obj.company_name,
                        "other_sku": item_obj.sku
                    })

            if conflicts:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Uno o più barcode appartengono ad altri committenti.",
                        "conflicts": conflicts
                    }
                )

        clean_code = merchant.account_code.replace("MCH-", "").replace(" ", "").upper()
        prefix = f"SKU-{clean_code}-{datetime.now().year}-"

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

            # Ricerca l'articolo ESCLUSIVAMENTE per il mandante selezionato
            existing_item = None
            if barcode:
                stmt_bc = select(Item).where(
                    Item.barcode == barcode,
                    Item.merchant_id == payload.merchant_id
                ).with_for_update()
                res_bc = await db.execute(stmt_bc)
                existing_item = res_bc.scalar_one_or_none()

            custom_sku = line.sku.strip().upper() if (line.sku and line.sku.strip()) else ""
            if not existing_item and custom_sku:
                stmt_sku = select(Item).where(
                    Item.sku == custom_sku,
                    Item.merchant_id == payload.merchant_id
                ).with_for_update()
                res_sku = await db.execute(stmt_sku)
                existing_item = res_sku.scalar_one_or_none()

            if existing_item:
                if desc:
                    existing_item.description = desc
                if bin_loc and bin_loc != "INBOUND":
                    existing_item.bin_location = bin_loc
                elif not existing_item.bin_location:
                    existing_item.bin_location = bin_loc

                existing_item.on_hand_qty += line.quantity
                final_sku = existing_item.sku
            else:
                # Assegnazione o generazione SKU garantito univoco per questo mandante
                if custom_sku:
                    final_sku = custom_sku
                else:
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

        await db.flush()

        # Registrazione Ledger inventariale
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
        "message": f"DDT {doc_ref} registrato con successo. Elaborati {len(payload.items)} articoli.",
        "doc_reference": doc_ref,
        "items_count": len(payload.items)
    }
