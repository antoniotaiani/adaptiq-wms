# app/routers/outbound.py
import io
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

# Importazioni per la generazione del PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from app.database import get_db
from app.models import Item, Merchant, DispatchOrder, DispatchOrderLine, InventoryTransaction
from app.schemas import DispatchFulfillRequest
from app.email_utils import send_email_background, check_smtp_configured

router = APIRouter(prefix="/api/orders", tags=["Outbound"])


# --- Schemi Pydantic per la Convalida Preventiva DDT ---
class DDTLineItem(BaseModel):
    sku: str
    expected_qty: int


class DDTValidationRequest(BaseModel):
    order_number: str
    merchant_id: int
    items: List[DDTLineItem]


# --- Funzione Helper per generare il PDF in memoria ---
def generate_ddt_pdf(order_number: str, merchant_name: str, processed_at: str, items: list) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    styles = getSampleStyleSheet()

    # Intestazione Documento
    elements.append(Paragraph("<b>DOCUMENTO DI TRASPORTO / PACKING SLIP</b>", styles['Title']))
    elements.append(Paragraph("<font color='#0284c7'><b>AdaptiQ WMS</b> - Verbale Ufficiale di Spedizione</font>", styles['Normal']))
    elements.append(Spacer(1, 20))
    
    # Dati Generali
    total_pieces = sum(i.picked_qty for i in items)
    info_text = (
        f"<b>Mandante:</b> {merchant_name}<br/>"
        f"<b>Riferimento Ordine/DDT:</b> {order_number}<br/>"
        f"<b>Data e Ora Evasione:</b> {processed_at}<br/>"
        f"<b>Pezzi Totali Spediti:</b> {total_pieces}"
    )
    elements.append(Paragraph(info_text, styles['Normal']))
    elements.append(Spacer(1, 20))

    # Tabella Articoli
    table_data = [["SKU", "Barcode", "Descrizione", "Ubicaz.", "Q.tà"]]
    for it in items:
        desc = (it.description[:45] + '...') if it.description and len(it.description) > 45 else (it.description or "")
        table_data.append([
            it.sku, 
            it.barcode or "", 
            desc, 
            it.bin_location or "", 
            str(it.picked_qty)
        ])
    
    # Stile Tabella
    t = Table(table_data, colWidths=[80, 90, 240, 60, 40])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#0f172a")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (-1,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f8fafc")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,1), (-1,-1), 9),
    ]))
    elements.append(t)
    
    # Spazio Firme
    elements.append(Spacer(1, 50))
    signature_text = (
        "_______________________________________ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
        "_______________________________________<br/>"
        "Firma Operatore Logistico &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
        "Firma Vettore / Corriere"
    )
    elements.append(Paragraph(signature_text, styles['Normal']))

    doc.build(elements)
    return buffer.getvalue()


# --- 1. Endpoint di Convalida Preventiva DDT di Vendita ---
@router.post("/validate-ddt")
async def validate_ddt(payload: DDTValidationRequest, db: AsyncSession = Depends(get_db)):
    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Il DDT presentato non contiene articoli."
        )

    order_num = payload.order_number.strip()

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
async def fulfill_order(
    payload: DispatchFulfillRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Impossibile evadere un ordine privo di articoli.")

    order_num = payload.order_number.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    res_m = await db.execute(select(Merchant).where(Merchant.id == payload.merchant_id))
    merchant = res_m.scalar_one_or_none()

    if not merchant:
        raise HTTPException(status_code=404, detail="Mandante non trovato.")

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
            raise HTTPException(status_code=400, detail=f"Quantità prelevata non valida ({it.picked_qty}) per SKU [{it.sku}].")

        if it.picked_qty > curr_item.on_hand_qty:
            raise HTTPException(status_code=409, detail=f"Over-picking non consentito per SKU [{it.sku}]. Giacenza disponibile: {curr_item.on_hand_qty}.")

        if it.picked_qty > it.expected_qty:
            raise HTTPException(status_code=400, detail=f"Quantità prelevata ({it.picked_qty}) superiore a quella prevista per SKU [{it.sku}].")

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

    await db.commit()

    # --- VERIFICA CONFIGURAZIONE SMTP E GESTIONE INVIO EMAIL ---
    email_status_info = {"sent": False, "message": ""}
    
    if not merchant.email:
        email_status_info["message"] = "Ordine evaso. Email non inviata: il mandante non ha un indirizzo email registrato."
    else:
        is_smtp_valid, smtp_err = await check_smtp_configured(db)
        if not is_smtp_valid:
            email_status_info["message"] = f"Ordine evaso, ma email non inviata: parametri SMTP incompleti o mancanti in Fase 6 ({smtp_err})."
        else:
            pdf_bytes = generate_ddt_pdf(order_num, merchant.company_name, now_str, payload.items)
            
            html_body = f"""
            <div style="font-family: Arial, sans-serif; color: #333;">
                <h2 style="color: #0284c7;">Spedizione Evasa: {order_num}</h2>
                <p>Gentile <b>{merchant.company_name}</b>,</p>
                <p>Ti informiamo che la spedizione in oggetto è stata finalizzata ed è pronta per il ritiro/consegna.</p>
                <p>In allegato a questa email troverai il <b>Documento di Trasporto (DDT) in formato PDF</b> contenente il dettaglio ufficiale di tutti gli articoli prelevati.</p>
                <br>
                <p style="font-size: 0.9em; color: #666;">
                    Cordiali saluti,<br>
                    <b>Logistica AdaptiQ WMS</b>
                </p>
            </div>
            """
            
            safe_mch = "".join(c for c in merchant.account_code if c.isalnum() or c in "_-")
            safe_ord = "".join(c for c in order_num if c.isalnum() or c in "_-")
            filename = f"DDT_{safe_mch}_{safe_ord}.pdf"
            
            background_tasks.add_task(
                send_email_background,
                db,
                merchant.email,
                f"Notifica Spedizione Evasa - Ordine: {order_num}",
                html_body,
                pdf_bytes,
                filename
            )
            email_status_info["sent"] = True
            email_status_info["message"] = f"Ordine evaso con successo! DDT generato e email con allegato in fase di invio a {merchant.email}."

    return {
        "status": "success", 
        "order_id": disp_order.id, 
        "timestamp": now_str,
        "email_info": email_status_info
    }


@router.get("/history")
async def orders_history(
    date_filter: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    merchant_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(
            DispatchOrder.id, DispatchOrder.order_number, Merchant.company_name,
            DispatchOrder.processed_at, DispatchOrder.total_units, DispatchOrder.status
        )
        .join(Merchant, DispatchOrder.merchant_id == Merchant.id)
    )

    if merchant_id:
        stmt = stmt.where(DispatchOrder.merchant_id == merchant_id)

    if date_from and date_from.strip():
        stmt = stmt.where(DispatchOrder.processed_at >= f"{date_from.strip()} 00:00:00")

    if date_to and date_to.strip():
        stmt = stmt.where(DispatchOrder.processed_at <= f"{date_to.strip()} 23:59:59")

    if date_filter and not (date_from or date_to):
        stmt = stmt.where(DispatchOrder.processed_at.like(f"{date_filter.strip()}%"))

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
