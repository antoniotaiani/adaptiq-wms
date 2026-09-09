from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.database import get_db
from app.models import Item, Merchant
from app.schemas import ItemResolveRequest

router = APIRouter(prefix="/api/items", tags=["Items"])


@router.post("/resolve")
async def resolve_item(payload: ItemResolveRequest, db: AsyncSession = Depends(get_db)):
    """
    Risolve un articolo per Barcode o SKU.
    Se merchant_id è specificato, filtra per quello specifico mandante,
    permettendo la coesistenza dello stesso barcode tra mandanti diversi.
    """
    code_clean = payload.code.strip()
    m_id = payload.merchant_id

    # 1. Ricerca prioritaria mirata per il mandante selezionato
    if m_id is not None:
        stmt = select(Item).where(
            Item.merchant_id == int(m_id),
            or_(
                Item.barcode == code_clean,
                Item.sku == code_clean
            )
        )
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
    else:
        # Ricerca senza mandante: prima per SKU (univoco globale)
        stmt_sku = select(Item).where(Item.sku == code_clean)
        res_sku = await db.execute(stmt_sku)
        item = res_sku.scalar_one_or_none()

        # Se non trovato, cerca per barcode prendendo il primo
        if not item:
            stmt_bc = select(Item).where(Item.barcode == code_clean)
            res_bc = await db.execute(stmt_bc)
            item = res_bc.scalars().first()

    # 2. Se non trovato per il mandante selezionato, verifica se appartiene ad altri
    if not item:
        if m_id is not None:
            stmt_other = (
                select(Item, Merchant)
                .join(Merchant, Item.merchant_id == Merchant.id)
                .where(
                    or_(
                        Item.barcode == code_clean,
                        Item.sku == code_clean
                    )
                )
            )
            res_other = await db.execute(stmt_other)
            match_other = res_other.first()

            if match_other:
                other_item, other_merchant = match_other
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"Il codice [{code_clean}] appartiene al mandante "
                        f"'{other_merchant.company_name}' (SKU: {other_item.sku}), "
                        f"non a quello attualmente selezionato."
                    )
                )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Codice [{code_clean}] non trovato a catalogo per questo mandante."
        )

    merchant = await db.get(Merchant, item.merchant_id)

    return {
        "sku": item.sku,
        "barcode": item.barcode,
        "description": item.description,
        "bin_location": item.bin_location,
        "on_hand_qty": item.on_hand_qty,
        "merchant_id": item.merchant_id,
        "merchant_name": merchant.company_name if merchant else f"Mandante #{item.merchant_id}"
    }
