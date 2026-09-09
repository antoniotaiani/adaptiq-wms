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
    - Se il barcode esiste già per lo stesso mandante, incrementa la giacenza e aggiorna l'ubicazione.
    - Se il barcode appartiene a un altro mandante, solleva un errore 400 esplicito.
    - Se il barcode è nuovo, genera uno SKU sequenziale garantito sia nel DDT che a database.
    - Registra i movimenti nel ledger di magazzino (InventoryTransaction).
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

            # 2. Ricerca globale per Barcode (indipendentemente dal mandante)
            # Questo evita la violazione del vincolo UNIQUE ix_items_barcode
            existing_item = None
            if barcode:
                stmt_bc = select(Item).where(Item.barcode == barcode).with_for_update()
                res_bc = await db.execute(stmt_bc)
                existing_item = res_bc.scalar_one_or_none()

            # 3. Se non trovato per barcode ma è stato passato uno SKU manuale, cerca per SKU
            custom_sku = line.sku.strip().upper() if (line.sku and line.sku.strip()) else ""
            if not existing_item and custom_sku:
                stmt_sku = select(Item).where(Item.sku == custom_sku).with_for_update()
                res_sku = await db.execute(stmt_sku)
                existing_item = res_sku.scalar_one_or_none()

            if existing_item:
                # Se l'articolo appartiene a un altro mandante, blocchiamo l'operazione
                if existing_item.merchant_id != payload.merchant_id:
                    # Recupera ragione sociale mandante proprietario per un messaggio trasparente
                    owner_mch = await db.get(Merchant, existing_item.merchant_id)
                    owner_name = owner_mch.company_name if owner_mch else f"ID #{existing_item.merchant_id}"
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Il Barcode [{barcode or existing_item.barcode}] (SKU: {existing_item.sku}) "
                            f"è già registrato a sistema per un altro mandante: {owner_name}."
                        )
                    )

                # Articolo già esistente per questo mandante: aggiorniamo scorta e ubicazione (se fornita)
                if desc:
                    existing_item.description = desc
                if bin_loc and bin_loc != "INBOUND":
                    existing_item.bin_location = bin_loc
                elif not existing_item.bin_location:
                    existing_item.bin_location = bin_loc

                existing_item.on_hand_qty += line.quantity
                final_sku = existing_item.sku

            else:
                # Articolo nuovo: assegna o genera lo SKU
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

        # Esegue il flush per confermare la corretta consistenza
        await db.flush()

        # Registrazione transazioni di inventario
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
