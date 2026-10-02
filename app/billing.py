"""Basi di calcolo dei servizi addebitabili.

La base dice da dove arriva la quantità da addebitare. È fissa nel codice (non in
anagrafica) perché il motore degli addebiti deve saperla calcolare: aggiungere una
base nuova significa anche scrivere come si conta.

source:
  INBOUND  -> generata alla registrazione di un carico merce (Fase 2)
  OUTBOUND -> generata all'evasione di un ordine (Fase 1)
  MONTHLY  -> dichiarata a fine mese per mandante (stoccaggio)
  MANUAL   -> inserita a mano dall'operatore
"""
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingCharge, BillingPhase, BillingService, PriceListLine, DispatchOrder, InboundReceipt

BILLING_BASES = {
    "INBOUND_DDT":          {"source": "INBOUND",  "unit": "DDT",               "label": "Per DDT di ingresso"},
    "INBOUND_PALLET":       {"source": "INBOUND",  "unit": "pallet",            "label": "Per pallet ricevuto"},
    "INBOUND_COLLO":        {"source": "INBOUND",  "unit": "collo",             "label": "Per collo ricevuto"},
    "INBOUND_RIGA":         {"source": "INBOUND",  "unit": "referenza",         "label": "Per referenza caricata"},
    "INBOUND_PEZZO":        {"source": "INBOUND",  "unit": "pezzo",             "label": "Per pezzo caricato"},
    "OUTBOUND_ORDINE":      {"source": "OUTBOUND", "unit": "ordine",            "label": "Per ordine evaso"},
    "OUTBOUND_RIGA":        {"source": "OUTBOUND", "unit": "referenza",         "label": "Per referenza prelevata"},
    "OUTBOUND_RIGA_EXTRA":  {"source": "OUTBOUND", "unit": "referenza",         "label": "Per referenza oltre la prima (per ordine)"},
    "OUTBOUND_PEZZO":       {"source": "OUTBOUND", "unit": "pezzo",             "label": "Per pezzo prelevato"},
    "OUTBOUND_PEZZO_EXTRA": {"source": "OUTBOUND", "unit": "pezzo",             "label": "Per pezzo oltre il primo (per ordine)"},
    "OUTBOUND_COLLO":       {"source": "OUTBOUND", "unit": "collo",             "label": "Per collo spedito"},
    "OUTBOUND_PALLET":      {"source": "OUTBOUND", "unit": "pallet",            "label": "Per pallet spedito"},
    "STORAGE_POSTO_PALLET": {"source": "MONTHLY",  "unit": "posto pallet/mese", "label": "Per posto pallet al mese"},
    "STORAGE_MQ":           {"source": "MONTHLY",  "unit": "m²/mese",           "label": "Per m² dedicato al mese"},
    "MANUAL_ORA":           {"source": "MANUAL",   "unit": "ora",               "label": "Manuale: a ore"},
    "MANUAL_QUANTITA":      {"source": "MANUAL",   "unit": "unità",             "label": "Manuale: a quantità"},
    "MANUAL_IMPORTO":       {"source": "MANUAL",   "unit": "a consuntivo",      "label": "Manuale: importo a consuntivo"},
}

BILLING_SOURCES = {
    "INBOUND": "Automatico dal carico merce",
    "OUTBOUND": "Automatico dall'evasione ordine",
    "MONTHLY": "Dichiarazione di fine mese",
    "MANUAL": "Inserimento manuale",
}

# Servizi il cui importo si decide sul singolo addebito: nel listino non hanno prezzo.
PRICELESS_BASES = {"MANUAL_IMPORTO"}


# ==========================================
# SCHEDA COSTI: VALIDAZIONE E PREZZI
# ==========================================
CENT = Decimal("0.01")


async def merchant_tariff(db: AsyncSession, merchant) -> dict:
    """Servizi imputabili al mandante: le voci attive del suo listino. I servizi a consuntivo
    non hanno prezzo (importo deciso sull'addebito); senza listino non c'è nulla di imputabile."""
    prices = {}
    if merchant.price_list_id:
        rows = await db.execute(
            select(PriceListLine.service_id, PriceListLine.unit_price)
            .where(PriceListLine.price_list_id == merchant.price_list_id)
        )
        prices = dict(rows.all())
    services = (await db.execute(
        select(BillingService, BillingPhase)
        .join(BillingPhase, BillingService.phase_id == BillingPhase.id)
        .where(BillingService.active.is_(True))
        .order_by(BillingPhase.sort_order, BillingPhase.name, BillingService.code)
    )).all()
    result = []
    for svc, phase in services:
        if svc.id not in prices:
            continue
        priceless = svc.basis in PRICELESS_BASES
        if not priceless and prices[svc.id] is None:
            continue  # voce rimasta senza prezzo (servizio passato da consuntivo a prezzo fisso)
        result.append({
            "service": svc, "phase": phase, "priceless": priceless,
            "unit_price": None if priceless else prices[svc.id],
        })
    return {s["service"].id: s for s in result}


async def apply_charges(
    db: AsyncSession, merchant, inputs: list, *, source_type: str, charge_date: str, actor: str,
    dispatch_order_id: int = None, inbound_receipt_id: int = None,
) -> Decimal:
    """Allinea gli addebiti del documento alla scheda costi ricevuta (senza commit).

    - riga senza id: nuovo addebito al prezzo attuale del listino del mandante;
    - riga con id: addebito esistente, se ne aggiorna quantità/importo mantenendo il prezzo originale;
    - addebiti esistenti del documento non presenti nella scheda: eliminati.
    Restituisce il totale del documento.
    """
    doc_filter = (
        BillingCharge.dispatch_order_id == dispatch_order_id if dispatch_order_id
        else BillingCharge.inbound_receipt_id == inbound_receipt_id
    )
    existing = {c.id: c for c in (await db.execute(select(BillingCharge).where(doc_filter))).scalars().all()}
    tariff = None
    kept, total = set(), Decimal("0")
    priceless_ids = set((await db.execute(
        select(BillingService.id).where(BillingService.basis.in_(PRICELESS_BASES))
    )).scalars().all())

    for row in inputs:
        if row.id is not None:
            charge = existing.get(row.id)
            if not charge or charge.service_id != row.service_id:
                raise HTTPException(status_code=400, detail="Riga di costo non appartenente a questo documento.")
            kept.add(row.id)
            charge.quantity = row.quantity
            if charge.service_id in priceless_ids:
                if row.amount is None:
                    raise HTTPException(status_code=400, detail=f"Indica l'importo per '{charge.service_name}' (servizio a consuntivo).")
                charge.amount = row.amount
                charge.unit_price = (row.amount / row.quantity).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            else:
                charge.amount = (row.quantity * charge.unit_price).quantize(CENT, ROUND_HALF_UP)
            charge.notes = (row.notes or "").strip() or None
            total += charge.amount
            continue

        if tariff is None:
            tariff = await merchant_tariff(db, merchant)
        entry = tariff.get(row.service_id)
        if not entry:
            raise HTTPException(
                status_code=400,
                detail=f"Il servizio #{row.service_id} non è imputabile a '{merchant.company_name}': "
                       f"non è attivo o non ha un prezzo nel listino del mandante."
            )
        svc, phase = entry["service"], entry["phase"]
        if BILLING_BASES.get(svc.basis, {}).get("source") == "MONTHLY":
            raise HTTPException(status_code=400, detail=f"'{svc.name}' è un servizio di fine mese: si addebita da Fase 7 > Stoccaggio Mensile.")
        if entry["priceless"]:
            if row.amount is None:
                raise HTTPException(status_code=400, detail=f"Indica l'importo per '{svc.name}' (servizio a consuntivo).")
            amount = row.amount
            unit_price = (row.amount / row.quantity).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        else:
            unit_price = entry["unit_price"]
            amount = (row.quantity * unit_price).quantize(CENT, ROUND_HALF_UP)
        db.add(BillingCharge(
            merchant_id=merchant.id, charge_date=charge_date, source_type=source_type,
            dispatch_order_id=dispatch_order_id, inbound_receipt_id=inbound_receipt_id,
            service_id=svc.id, phase_id=phase.id, service_code=svc.code, service_name=svc.name,
            phase_name=phase.name, unit_label=svc.unit_label, quantity=row.quantity,
            unit_price=unit_price, amount=amount, notes=(row.notes or "").strip() or None,
            created_at=charge_date, created_by=actor,
        ))
        total += amount

    for charge_id, charge in existing.items():
        if charge_id not in kept:
            await db.delete(charge)
    return total


def charge_dict(c: BillingCharge) -> dict:
    return {
        "id": c.id, "service_id": c.service_id, "service_code": c.service_code, "service_name": c.service_name,
        "phase_name": c.phase_name, "unit_label": c.unit_label, "quantity": float(c.quantity),
        "unit_price": float(c.unit_price), "amount": float(c.amount), "notes": c.notes,
        "priceless": False, "charge_date": c.charge_date, "created_by": c.created_by, "period": c.period,
    }


# ==========================================
# RIEPILOGO OPERAZIONI (FASE 7 E PORTALE MANDANTE)
# ==========================================
async def build_operations_summary(db: AsyncSession, merchant, date_from: str, date_to: str) -> dict:
    """Tutte le operazioni del mandante nel periodo (carichi ed evasioni), documento per
    documento, con i costi imputati; più i totali per fase e il totale generale."""
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="La data iniziale è successiva a quella finale.")
    start, end = f"{date_from} 00:00:00", f"{date_to} 23:59:59"

    receipts = (await db.execute(
        select(InboundReceipt).where(
            InboundReceipt.merchant_id == merchant.id,
            InboundReceipt.received_at >= start, InboundReceipt.received_at <= end,
        )
    )).scalars().all()
    orders = (await db.execute(
        select(DispatchOrder).where(
            DispatchOrder.merchant_id == merchant.id,
            DispatchOrder.processed_at >= start, DispatchOrder.processed_at <= end,
        )
    )).scalars().all()
    charges = (await db.execute(
        select(BillingCharge).where(
            BillingCharge.merchant_id == merchant.id,
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
            "kind": "charge", "type_label": "Addebito", "id": c["id"],
            "reference": f"{c['service_name']} — {c['period']}" if c["period"] else c["service_name"],
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
