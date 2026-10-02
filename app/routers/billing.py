# app/routers/billing.py
import calendar
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update

from app.database import get_db
from app.models import BillingPhase, BillingService, PriceList, PriceListLine, Merchant, BillingCharge, DispatchOrder, InboundReceipt
from app.schemas import BillingPhaseRequest, BillingServiceRequest, PriceListCreateRequest, PriceListUpdateRequest, DocumentChargesRequest, MonthlyChargesRequest
from app.auth import get_current_operator_payload
from app.audit import log_action, actor_from_payload
from app.timeutils import now_str
from app.billing import BILLING_BASES, BILLING_SOURCES, PRICELESS_BASES, merchant_tariff, apply_charges, charge_dict, build_operations_summary

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
        # Un servizio a consuntivo non ha prezzo di listino: resta voce dei listini, senza prezzo.
        await db.execute(update(PriceListLine).where(PriceListLine.service_id == service_id).values(unit_price=None))
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
            detail=f"Il servizio '{svc.name}' è una voce di {used} listini: toglilo dai listini oppure disattivalo."
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
        # Voci del listino: servizio -> prezzo (None per i servizi a consuntivo o senza prezzo).
        "prices": {str(l.service_id): None if l.unit_price is None else float(l.unit_price) for l in lines},
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
        no_price = [found[p.service_id].name for p in payload.prices
                    if p.unit_price is None and found[p.service_id].basis not in PRICELESS_BASES]
        if no_price:
            raise HTTPException(status_code=400, detail=f"Indica il prezzo per: {', '.join(no_price)}.")

    pl.name, pl.notes, pl.active = name, (payload.notes or "").strip() or None, payload.active
    # Sostituzione completa delle voci: un servizio non presente non è imputabile con questo listino.
    await db.execute(delete(PriceListLine).where(PriceListLine.price_list_id == list_id))
    for p in payload.prices:
        priceless = found[p.service_id].basis in PRICELESS_BASES
        db.add(PriceListLine(price_list_id=list_id, service_id=p.service_id, unit_price=None if priceless else p.unit_price))

    await log_action(db, actor_from_payload(op), "MODIFICA_LISTINO", f"price_list:{list_id}", f"Listino '{name}' aggiornato ({len(payload.prices)} voci).")
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
# STOCCAGGIO MENSILE (DICHIARAZIONE DI FINE MESE)
# ==========================================
MONTHLY_BASES = {code for code, b in BILLING_BASES.items() if b["source"] == "MONTHLY"}
PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def _period_charge_date(period: str) -> str:
    """Data degli addebiti del mese: l'ultimo giorno, a fine giornata. Rifiuta i mesi futuri."""
    year, month = int(period[:4]), int(period[5:])
    if period > now_str()[:7]:
        raise HTTPException(status_code=400, detail="Non si può dichiarare lo stoccaggio di un mese futuro.")
    return f"{period}-{calendar.monthrange(year, month)[1]:02d} 23:59:59"


@router.get("/monthly")
async def get_monthly_storage(period: str = Query(..., pattern=PERIOD_PATTERN), db: AsyncSession = Depends(get_db)):
    """Griglia del mese: per ogni mandante le quantità dichiarate e i servizi mensili del suo listino."""
    charges = (await db.execute(
        select(BillingCharge).where(BillingCharge.period == period, BillingCharge.source_type == "MONTHLY")
    )).scalars().all()
    services = (await db.execute(
        select(BillingService, BillingPhase).join(BillingPhase, BillingService.phase_id == BillingPhase.id)
        .where(BillingService.basis.in_(MONTHLY_BASES))
        .order_by(BillingPhase.sort_order, BillingService.id)
    )).all()
    charged_ids = {c.service_id for c in charges}
    columns = [s for s, _ in services if s.active or s.id in charged_ids]

    list_names = dict((await db.execute(select(PriceList.id, PriceList.name))).all())
    rows = []
    for m in (await db.execute(select(Merchant).order_by(Merchant.company_name))).scalars().all():
        tariff = await merchant_tariff(db, m)
        cells = {}
        for svc in columns:
            entry = tariff.get(svc.id)
            charge = next((c for c in charges if c.merchant_id == m.id and c.service_id == svc.id), None)
            if charge:
                # Addebito già registrato: vale il suo prezzo congelato.
                cells[str(svc.id)] = {"quantity": float(charge.quantity), "unit_price": float(charge.unit_price), "amount": float(charge.amount), "saved": True}
            elif entry:
                cells[str(svc.id)] = {"quantity": None, "unit_price": float(entry["unit_price"]), "amount": 0.0, "saved": False}
        # Tutti i mandanti: senza celle = nessuna voce di stoccaggio nel listino (o nessun listino).
        rows.append({"merchant_id": m.id, "company_name": m.company_name, "account_code": m.account_code,
                     "price_list_name": list_names.get(m.price_list_id), "cells": cells})
    return {
        "period": period,
        "services": [{"id": s.id, "code": s.code, "name": s.name, "unit_label": s.unit_label, "active": s.active} for s in columns],
        "merchants": rows,
        "total": round(sum(float(c.amount) for c in charges), 2),
    }


@router.put("/monthly")
async def save_monthly_storage(payload: MonthlyChargesRequest, op: dict = Depends(get_current_operator_payload), db: AsyncSession = Depends(get_db)):
    """Allinea gli addebiti del mese alle quantità ricevute: crea (prezzo del listino attuale),
    aggiorna (prezzo originale) o elimina (quantità vuota o zero)."""
    charge_date = _period_charge_date(payload.period)
    actor = actor_from_payload(op)
    existing = {(c.merchant_id, c.service_id): c for c in (await db.execute(
        select(BillingCharge).where(BillingCharge.period == payload.period, BillingCharge.source_type == "MONTHLY")
    )).scalars().all()}
    merchants, tariffs, changes = {}, {}, {}

    for e in payload.entries:
        qty = e.quantity or Decimal("0")
        charge = existing.get((e.merchant_id, e.service_id))
        if charge:
            if qty == 0:
                await db.delete(charge)
                changes.setdefault(e.merchant_id, []).append(f"{charge.service_name} eliminato")
            elif qty != charge.quantity:
                charge.quantity = qty
                charge.amount = (qty * charge.unit_price).quantize(Decimal("0.01"), ROUND_HALF_UP)
                changes.setdefault(e.merchant_id, []).append(f"{charge.service_name} {qty} {charge.unit_label}")
            continue
        if qty == 0:
            continue
        if e.merchant_id not in merchants:
            merchants[e.merchant_id] = await db.get(Merchant, e.merchant_id)
            if not merchants[e.merchant_id]:
                raise HTTPException(status_code=404, detail=f"Mandante #{e.merchant_id} non trovato.")
            tariffs[e.merchant_id] = await merchant_tariff(db, merchants[e.merchant_id])
        merchant, entry = merchants[e.merchant_id], tariffs[e.merchant_id].get(e.service_id)
        if not entry or entry["service"].basis not in MONTHLY_BASES:
            raise HTTPException(status_code=400, detail=f"Il servizio #{e.service_id} non è uno stoccaggio mensile del listino di '{merchant.company_name}'.")
        svc, phase = entry["service"], entry["phase"]
        db.add(BillingCharge(
            merchant_id=merchant.id, charge_date=charge_date, source_type="MONTHLY", period=payload.period,
            service_id=svc.id, phase_id=phase.id, service_code=svc.code, service_name=svc.name,
            phase_name=phase.name, unit_label=svc.unit_label, quantity=qty, unit_price=entry["unit_price"],
            amount=(qty * entry["unit_price"]).quantize(Decimal("0.01"), ROUND_HALF_UP),
            created_at=now_str(), created_by=actor,
        ))
        changes.setdefault(e.merchant_id, []).append(f"{svc.name} {qty} {svc.unit_label}")

    for merchant_id, items in changes.items():
        m = merchants.get(merchant_id) or await db.get(Merchant, merchant_id)
        await log_action(db, actor, "STOCCAGGIO_MENSILE", f"merchant:{m.account_code}",
                         f"Stoccaggio {payload.period} di '{m.company_name}': {'; '.join(items)}."[:500])
    await db.commit()
    return {"status": "ok", "changed_merchants": len(changes)}


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
    return await build_operations_summary(db, merchant, date_from, date_to)
