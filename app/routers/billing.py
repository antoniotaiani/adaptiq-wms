# app/routers/billing.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.database import get_db
from app.models import BillingPhase, BillingService, PriceList, PriceListLine, Merchant, BillingCharge, DispatchOrder, InboundReceipt
from app.schemas import BillingPhaseRequest, BillingServiceRequest, PriceListCreateRequest, PriceListUpdateRequest, DocumentChargesRequest
from app.auth import get_current_operator_payload
from app.audit import log_action, actor_from_payload
from app.billing import BILLING_BASES, BILLING_SOURCES, PRICELESS_BASES, merchant_tariff, apply_charges

router = APIRouter(prefix="/api/billing", tags=["Billing"], dependencies=[Depends(get_current_operator_payload)])


@router.get("/meta")
async def billing_meta():
    """Basi di calcolo disponibili (fisse nel codice) con la loro origine."""
    return {
        "bases": [
            {"code": code, **b, "source_label": BILLING_SOURCES[b["source"]], "priceless": code in PRICELESS_BASES}
            for code, b in BILLING_BASES.items()
        ]
    }


# ==========================================
# FASI
# ==========================================
@router.get("/phases")
async def list_phases(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(BillingPhase, func.count(BillingService.id))
        .outerjoin(BillingService, BillingService.phase_id == BillingPhase.id)
        .group_by(BillingPhase.id)
        .order_by(BillingPhase.sort_order, BillingPhase.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {"id": p.id, "code": p.code, "name": p.name, "sort_order": p.sort_order, "services_count": n}
        for p, n in rows
    ]


@router.post("/phases")
async def create_phase(payload: BillingPhaseRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    code = payload.code.strip().upper()
    if (await db.execute(select(BillingPhase.id).where(BillingPhase.code == code))).first():
        raise HTTPException(status_code=409, detail=f"Esiste già una fase con codice '{code}'.")
    phase = BillingPhase(code=code, name=payload.name.strip(), sort_order=payload.sort_order)
    db.add(phase)
    await log_action(db, actor_from_payload(op), "CREAZIONE_FASE_COSTI", f"billing_phase:{code}", f"Fase '{phase.name}' creata.")
    await db.commit()
    return {"status": "ok", "id": phase.id}


@router.put("/phases/{phase_id}")
async def update_phase(phase_id: int, payload: BillingPhaseRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    phase = await db.get(BillingPhase, phase_id)
    if not phase:
        raise HTTPException(status_code=404, detail="Fase non trovata.")
    code = payload.code.strip().upper()
    dup = await db.execute(select(BillingPhase.id).where(BillingPhase.code == code, BillingPhase.id != phase_id))
    if dup.first():
        raise HTTPException(status_code=409, detail=f"Esiste già una fase con codice '{code}'.")
    phase.code, phase.name, phase.sort_order = code, payload.name.strip(), payload.sort_order
    await log_action(db, actor_from_payload(op), "MODIFICA_FASE_COSTI", f"billing_phase:{code}", f"Fase '{phase.name}' aggiornata.")
    await db.commit()
    return {"status": "ok"}


@router.delete("/phases/{phase_id}")
async def delete_phase(phase_id: int, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    phase = await db.get(BillingPhase, phase_id)
    if not phase:
        raise HTTPException(status_code=404, detail="Fase non trovata.")
    used = (await db.execute(select(func.count(BillingService.id)).where(BillingService.phase_id == phase_id))).scalar()
    if used:
        raise HTTPException(status_code=400, detail=f"Impossibile cancellare la fase '{phase.name}': ha ancora {used} servizi associati.")
    await db.delete(phase)
    await log_action(db, actor_from_payload(op), "CANCELLAZIONE_FASE_COSTI", f"billing_phase:{phase.code}", f"Fase '{phase.name}' cancellata.")
    await db.commit()
    return {"status": "ok"}


# ==========================================
# SERVIZI (VOCI DI COSTO)
# ==========================================
def _service_dict(s: BillingService, phase: BillingPhase) -> dict:
    basis = BILLING_BASES.get(s.basis, {})
    return {
        "id": s.id, "code": s.code, "name": s.name, "description": s.description,
        "phase_id": s.phase_id, "phase_name": phase.name, "phase_sort": phase.sort_order,
        "basis": s.basis, "basis_label": basis.get("label", s.basis),
        "source": basis.get("source"), "priceless": s.basis in PRICELESS_BASES,
        "unit_label": s.unit_label, "active": s.active,
    }


async def _validate_service(payload: BillingServiceRequest, db: AsyncSession, exclude_id: int = None) -> str:
    code = payload.code.strip().upper()
    if payload.basis not in BILLING_BASES:
        raise HTTPException(status_code=400, detail=f"Base di calcolo '{payload.basis}' non valida.")
    if not await db.get(BillingPhase, payload.phase_id):
        raise HTTPException(status_code=400, detail="Fase non valida.")
    stmt = select(BillingService.id).where(BillingService.code == code)
    if exclude_id:
        stmt = stmt.where(BillingService.id != exclude_id)
    if (await db.execute(stmt)).first():
        raise HTTPException(status_code=409, detail=f"Esiste già un servizio con codice '{code}'.")
    return code


@router.get("/services")
async def list_services(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(BillingService, BillingPhase)
        .join(BillingPhase, BillingService.phase_id == BillingPhase.id)
        .order_by(BillingPhase.sort_order, BillingPhase.name, BillingService.code)
    )
    rows = (await db.execute(stmt)).all()
    return [_service_dict(s, p) for s, p in rows]


@router.post("/services")
async def create_service(payload: BillingServiceRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    code = await _validate_service(payload, db)
    svc = BillingService(
        code=code, name=payload.name.strip(),
        description=(payload.description or "").strip() or None,
        phase_id=payload.phase_id, basis=payload.basis,
        unit_label=payload.unit_label.strip(), active=payload.active,
    )
    db.add(svc)
    await log_action(db, actor_from_payload(op), "CREAZIONE_SERVIZIO", f"billing_service:{code}", f"Servizio '{svc.name}' creato ({payload.basis}).")
    await db.commit()
    return {"status": "ok", "id": svc.id}


@router.put("/services/{service_id}")
async def update_service(service_id: int, payload: BillingServiceRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    svc = await db.get(BillingService, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Servizio non trovato.")
    code = await _validate_service(payload, db, exclude_id=service_id)
    svc.code, svc.name = code, payload.name.strip()
    svc.description = (payload.description or "").strip() or None
    svc.phase_id, svc.basis = payload.phase_id, payload.basis
    svc.unit_label, svc.active = payload.unit_label.strip(), payload.active
    if svc.basis in PRICELESS_BASES:
        # Un servizio a consuntivo non ha prezzo di listino: eventuali prezzi precedenti non valgono più.
        await db.execute(delete(PriceListLine).where(PriceListLine.service_id == service_id))
    await log_action(db, actor_from_payload(op), "MODIFICA_SERVIZIO", f"billing_service:{code}", f"Servizio '{svc.name}' aggiornato.")
    await db.commit()
    return {"status": "ok"}


@router.delete("/services/{service_id}")
async def delete_service(service_id: int, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    svc = await db.get(BillingService, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Servizio non trovato.")
    used = (await db.execute(select(func.count(PriceListLine.id)).where(PriceListLine.service_id == service_id))).scalar()
    if used:
        raise HTTPException(
            status_code=400,
            detail=f"Il servizio '{svc.name}' ha un prezzo in {used} listini: toglilo dai listini oppure disattivalo."
        )
    charged = (await db.execute(select(func.count(BillingCharge.id)).where(BillingCharge.service_id == service_id))).scalar()
    if charged:
        raise HTTPException(status_code=400, detail=f"Il servizio '{svc.name}' è già stato addebitato {charged} volte: puoi solo disattivarlo.")
    await db.delete(svc)
    await log_action(db, actor_from_payload(op), "CANCELLAZIONE_SERVIZIO", f"billing_service:{svc.code}", f"Servizio '{svc.name}' cancellato.")
    await db.commit()
    return {"status": "ok"}


# ==========================================
# LISTINI
# ==========================================
@router.get("/price-lists")
async def list_price_lists(db: AsyncSession = Depends(get_db)):
    lists = (await db.execute(select(PriceList).order_by(PriceList.name))).scalars().all()
    lines = dict((await db.execute(
        select(PriceListLine.price_list_id, func.count(PriceListLine.id)).group_by(PriceListLine.price_list_id)
    )).all())
    merchants = (await db.execute(
        select(Merchant.price_list_id, Merchant.company_name).where(Merchant.price_list_id.is_not(None)).order_by(Merchant.company_name)
    )).all()
    by_list = {}
    for list_id, name in merchants:
        by_list.setdefault(list_id, []).append(name)
    return [
        {"id": pl.id, "name": pl.name, "notes": pl.notes, "active": pl.active,
         "lines_count": lines.get(pl.id, 0), "merchants": by_list.get(pl.id, [])}
        for pl in lists
    ]


@router.get("/price-lists/{list_id}")
async def get_price_list(list_id: int, db: AsyncSession = Depends(get_db)):
    pl = await db.get(PriceList, list_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Listino non trovato.")
    lines = (await db.execute(select(PriceListLine).where(PriceListLine.price_list_id == list_id))).scalars().all()
    merchants = (await db.execute(
        select(Merchant.id, Merchant.company_name, Merchant.account_code)
        .where(Merchant.price_list_id == list_id).order_by(Merchant.company_name)
    )).all()
    return {
        "id": pl.id, "name": pl.name, "notes": pl.notes, "active": pl.active,
        "prices": {str(l.service_id): float(l.unit_price) for l in lines},
        "merchants": [{"id": m[0], "company_name": m[1], "account_code": m[2]} for m in merchants],
    }


async def _ensure_unique_list_name(name: str, db: AsyncSession, exclude_id: int = None):
    stmt = select(PriceList.id).where(func.lower(PriceList.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(PriceList.id != exclude_id)
    if (await db.execute(stmt)).first():
        raise HTTPException(status_code=409, detail=f"Esiste già un listino chiamato '{name}'.")


@router.post("/price-lists")
async def create_price_list(payload: PriceListCreateRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    name = payload.name.strip()
    await _ensure_unique_list_name(name, db)
    pl = PriceList(name=name, notes=(payload.notes or "").strip() or None, active=True)
    db.add(pl)
    await db.flush()

    details = f"Listino '{name}' creato."
    if payload.copy_from_id:
        source = await db.get(PriceList, payload.copy_from_id)
        if not source:
            raise HTTPException(status_code=404, detail="Listino da duplicare non trovato.")
        src_lines = (await db.execute(select(PriceListLine).where(PriceListLine.price_list_id == source.id))).scalars().all()
        for l in src_lines:
            db.add(PriceListLine(price_list_id=pl.id, service_id=l.service_id, unit_price=l.unit_price))
        details = f"Listino '{name}' creato duplicando '{source.name}'."

    await log_action(db, actor_from_payload(op), "CREAZIONE_LISTINO", f"price_list:{pl.id}", details)
    await db.commit()
    return {"status": "ok", "id": pl.id}


@router.put("/price-lists/{list_id}")
async def update_price_list(list_id: int, payload: PriceListUpdateRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    pl = await db.get(PriceList, list_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Listino non trovato.")
    name = payload.name.strip()
    await _ensure_unique_list_name(name, db, exclude_id=list_id)

    service_ids = [p.service_id for p in payload.prices]
    if len(service_ids) != len(set(service_ids)):
        raise HTTPException(status_code=400, detail="Lo stesso servizio compare più volte nel listino.")
    if service_ids:
        services = (await db.execute(select(BillingService).where(BillingService.id.in_(service_ids)))).scalars().all()
        found = {s.id: s for s in services}
        missing = set(service_ids) - set(found)
        if missing:
            raise HTTPException(status_code=400, detail=f"Servizi inesistenti: {sorted(missing)}.")
        priceless = [s.name for s in services if s.basis in PRICELESS_BASES]
        if priceless:
            raise HTTPException(status_code=400, detail=f"Servizi a consuntivo senza prezzo di listino: {', '.join(priceless)}.")

    pl.name, pl.notes, pl.active = name, (payload.notes or "").strip() or None, payload.active
    # Sostituzione completa delle righe: un servizio senza prezzo semplicemente non è nel listino.
    await db.execute(delete(PriceListLine).where(PriceListLine.price_list_id == list_id))
    for p in payload.prices:
        db.add(PriceListLine(price_list_id=list_id, service_id=p.service_id, unit_price=p.unit_price))

    await log_action(db, actor_from_payload(op), "MODIFICA_LISTINO", f"price_list:{list_id}", f"Listino '{name}' aggiornato ({len(payload.prices)} prezzi).")
    await db.commit()
    return {"status": "ok"}


@router.delete("/price-lists/{list_id}")
async def delete_price_list(list_id: int, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    pl = await db.get(PriceList, list_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Listino non trovato.")
    assigned = (await db.execute(select(func.count(Merchant.id)).where(Merchant.price_list_id == list_id))).scalar()
    if assigned:
        raise HTTPException(status_code=400, detail=f"Impossibile cancellare '{pl.name}': è associato a {assigned} mandanti.")
    await db.execute(delete(PriceListLine).where(PriceListLine.price_list_id == list_id))
    await db.delete(pl)
    await log_action(db, actor_from_payload(op), "CANCELLAZIONE_LISTINO", f"price_list:{list_id}", f"Listino '{pl.name}' cancellato.")
    await db.commit()
    return {"status": "ok"}


# ==========================================
# SCHEDA COSTI DEI DOCUMENTI
# ==========================================
@router.get("/tariff/{merchant_id}")
async def get_merchant_tariff(merchant_id: int, db: AsyncSession = Depends(get_db)):
    """Servizi imputabili al mandante con il prezzo del suo listino (per la scheda costi)."""
    merchant = await db.get(Merchant, merchant_id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Mandante non trovato.")
    price_list = await db.get(PriceList, merchant.price_list_id) if merchant.price_list_id else None
    tariff = await merchant_tariff(db, merchant)
    return {
        "price_list": {"id": price_list.id, "name": price_list.name, "active": price_list.active} if price_list else None,
        "services": [
            {
                "id": e["service"].id, "code": e["service"].code, "name": e["service"].name,
                "phase_name": e["phase"].name, "basis": e["service"].basis,
                "source": BILLING_BASES.get(e["service"].basis, {}).get("source"),
                "unit_label": e["service"].unit_label, "priceless": e["priceless"],
                "unit_price": None if e["unit_price"] is None else float(e["unit_price"]),
            }
            for e in tariff.values()
        ],
    }


def charge_dict(c: BillingCharge) -> dict:
    return {
        "id": c.id, "service_id": c.service_id, "service_code": c.service_code, "service_name": c.service_name,
        "phase_name": c.phase_name, "unit_label": c.unit_label, "quantity": float(c.quantity),
        "unit_price": float(c.unit_price), "amount": float(c.amount), "notes": c.notes,
        "priceless": False, "charge_date": c.charge_date, "created_by": c.created_by,
    }


async def _load_document(kind: str, doc_id: int, db: AsyncSession):
    if kind == "order":
        doc = await db.get(DispatchOrder, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Ordine non trovato.")
        return doc, {"dispatch_order_id": doc_id}, "OUTBOUND", doc.order_number, doc.processed_at
    if kind == "receipt":
        doc = await db.get(InboundReceipt, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Carico non trovato.")
        return doc, {"inbound_receipt_id": doc_id}, "INBOUND", doc.doc_reference, doc.received_at
    raise HTTPException(status_code=400, detail="Tipo documento non valido.")


@router.get("/documents/{kind}/{doc_id}/charges")
async def get_document_charges(kind: str, doc_id: int, db: AsyncSession = Depends(get_db)):
    doc, link, source_type, reference, doc_date = await _load_document(kind, doc_id, db)
    merchant = await db.get(Merchant, doc.merchant_id)
    col = BillingCharge.dispatch_order_id if kind == "order" else BillingCharge.inbound_receipt_id
    charges = (await db.execute(select(BillingCharge).where(col == doc_id).order_by(BillingCharge.id))).scalars().all()
    priceless = set((await db.execute(select(BillingService.id).where(BillingService.basis.in_(PRICELESS_BASES)))).scalars().all())
    rows = []
    for c in charges:
        d = charge_dict(c)
        d["priceless"] = c.service_id in priceless
        rows.append(d)
    return {
        "kind": kind, "id": doc_id, "reference": reference, "date": doc_date,
        "merchant_id": merchant.id, "merchant_name": merchant.company_name,
        "charges": rows, "total": round(sum(r["amount"] for r in rows), 2),
    }


@router.put("/documents/{kind}/{doc_id}/charges")
async def update_document_charges(
    kind: str, doc_id: int, payload: DocumentChargesRequest,
    op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)
):
    doc, link, source_type, reference, doc_date = await _load_document(kind, doc_id, db)
    merchant = await db.get(Merchant, doc.merchant_id)
    actor = actor_from_payload(op)
    # Le nuove righe prendono la data del documento, così finiscono nel periodo giusto del riepilogo.
    total = await apply_charges(db, merchant, payload.charges, source_type=source_type, charge_date=doc_date, actor=actor, **link)
    label = "ordine" if kind == "order" else "carico"
    await log_action(db, actor, "MODIFICA_COSTI_DOCUMENTO", f"{kind}:{doc_id}",
                     f"Costi {label} '{reference}' ({merchant.company_name}) aggiornati: {len(payload.charges)} righe, totale € {total:.2f}.")
    await db.commit()
    return {"status": "ok", "total": float(total)}


# ==========================================
# RIEPILOGO OPERAZIONI PER MANDANTE E PERIODO
# ==========================================
@router.get("/summary")
async def operations_summary(
    merchant_id: int,
    date_from: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: AsyncSession = Depends(get_db)
):
    """Tutte le operazioni del mandante nel periodo (carichi ed evasioni), documento per
    documento, con i costi imputati; più i totali per fase e il totale generale."""
    merchant = await db.get(Merchant, merchant_id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Mandante non trovato.")
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="La data iniziale è successiva a quella finale.")
    start, end = f"{date_from} 00:00:00", f"{date_to} 23:59:59"

    receipts = (await db.execute(
        select(InboundReceipt).where(
            InboundReceipt.merchant_id == merchant_id,
            InboundReceipt.received_at >= start, InboundReceipt.received_at <= end,
        )
    )).scalars().all()
    orders = (await db.execute(
        select(DispatchOrder).where(
            DispatchOrder.merchant_id == merchant_id,
            DispatchOrder.processed_at >= start, DispatchOrder.processed_at <= end,
        )
    )).scalars().all()
    charges = (await db.execute(
        select(BillingCharge).where(
            BillingCharge.merchant_id == merchant_id,
            BillingCharge.charge_date >= start, BillingCharge.charge_date <= end,
        ).order_by(BillingCharge.id)
    )).scalars().all()

    by_order, by_receipt, loose = {}, {}, []
    for c in charges:
        if c.dispatch_order_id:
            by_order.setdefault(c.dispatch_order_id, []).append(charge_dict(c))
        elif c.inbound_receipt_id:
            by_receipt.setdefault(c.inbound_receipt_id, []).append(charge_dict(c))
        else:
            loose.append(charge_dict(c))

    operations = []
    for r in receipts:
        rows = by_receipt.get(r.id, [])
        operations.append({
            "kind": "receipt", "type_label": "Carico", "id": r.id, "reference": r.doc_reference,
            "date": r.received_at, "units": r.total_units, "charges": rows,
            "total": round(sum(x["amount"] for x in rows), 2),
        })
    for o in orders:
        rows = by_order.get(o.id, [])
        operations.append({
            "kind": "order", "type_label": "Spedizione", "id": o.id, "reference": o.order_number,
            "date": o.processed_at, "units": o.total_units, "charges": rows,
            "total": round(sum(x["amount"] for x in rows), 2),
        })
    # Addebiti non legati a un documento (stoccaggio mensile, extra manuali): una voce ciascuno.
    for c in loose:
        operations.append({
            "kind": "charge", "type_label": "Addebito", "id": c["id"], "reference": c["service_name"],
            "date": c["charge_date"], "units": None, "charges": [c], "total": c["amount"],
        })
    operations.sort(key=lambda x: x["date"])

    phases = {}
    for c in charges:
        ph = phases.setdefault(c.phase_id, {"phase_name": c.phase_name, "amount": 0.0, "services": {}})
        ph["amount"] += float(c.amount)
        sv = ph["services"].setdefault(c.service_id, {"service_name": c.service_name, "unit_label": c.unit_label, "quantity": 0.0, "amount": 0.0})
        sv["quantity"] += float(c.quantity)
        sv["amount"] += float(c.amount)
    order_of_phase = dict((await db.execute(select(BillingPhase.id, BillingPhase.sort_order))).all())
    phase_totals = [
        {"phase_name": p["phase_name"], "amount": round(p["amount"], 2),
         "services": [{**s, "quantity": round(s["quantity"], 3), "amount": round(s["amount"], 2)} for s in p["services"].values()]}
        for pid, p in sorted(phases.items(), key=lambda kv: order_of_phase.get(kv[0], 0))
    ]

    return {
        "merchant": {"id": merchant.id, "company_name": merchant.company_name, "account_code": merchant.account_code},
        "date_from": date_from, "date_to": date_to,
        "operations": operations,
        "phase_totals": phase_totals,
        "counts": {"receipts": len(receipts), "orders": len(orders),
                   "units_in": sum(r.total_units for r in receipts), "units_out": sum(o.total_units for o in orders)},
        "grand_total": round(sum(float(c.amount) for c in charges), 2),
        "operations_without_charges": sum(1 for op in operations if op["kind"] != "charge" and not op["charges"]),
    }
