from fastapi import APIRouter, Depends, HTTPException, Response, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from app.database import get_db
from app.models import Merchant, Item, DispatchOrder, SmtpSettings
from app.schemas import MerchantLoginRequest, CreateMerchantRequest, UpdatePinRequest, ResetPinRequest
from app.auth import hash_pin, verify_pin, create_merchant_token, get_current_merchant_payload
from app.config import JWT_EXPIRATION_HOURS
from app.email_utils import send_email_background

router = APIRouter(prefix="/api", tags=["Merchants"])

@router.get("/merchants")
async def get_merchants(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            Merchant.id,
            Merchant.account_code,
            Merchant.company_name,
            Merchant.email,
            Merchant.phone,
            func.count(Item.sku).label("sku_count")
        )
        .outerjoin(Item, Merchant.id == Item.merchant_id)
        .group_by(Merchant.id, Merchant.account_code, Merchant.company_name, Merchant.email, Merchant.phone)
        .order_by(Merchant.company_name.asc())
    )
    res = await db.execute(stmt)
    rows = res.fetchall()
    return [{
        "id": r[0], 
        "account_code": r[1], 
        "company_name": r[2], 
        "email": r[3],
        "phone": r[4],
        "sku_count": r[5]
    } for r in rows]


@router.post("/merchants")
async def create_merchant(
    payload: CreateMerchantRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    code = payload.account_code.strip().upper()
    name = payload.company_name.strip()
    pin = payload.pin.strip()
    email = payload.email.strip() if payload.email else None
    phone = payload.phone.strip() if payload.phone else None

    if not code or not name or not pin:
        raise HTTPException(status_code=400, detail="Tutti i campi obbligatori (Codice, Nome, PIN) devono essere compilati.")

    existing = await db.execute(select(Merchant).where(Merchant.account_code == code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Il codice account '{code}' è già esistente.")

    new_m = Merchant(
        account_code=code, 
        company_name=name, 
        pin_hash=hash_pin(pin),
        email=email,
        phone=phone
    )
    db.add(new_m)
    await db.commit()
    
    if email:
        smtp_res = await db.execute(select(SmtpSettings).where(SmtpSettings.id == 1))
        smtp_conf = smtp_res.scalar_one_or_none()
        portal_url = smtp_conf.portal_base_url if smtp_conf else "http://localhost"
        
        html_body = f"""
        <div style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #0284c7;">Benvenuto in AdaptiQ WMS</h2>
            <h3>Gentile {name},</h3>
            <p>Il tuo account Mandante è stato creato con successo.</p>
            <div style="background-color: #f8fafc; padding: 15px; border-left: 4px solid #f59e0b; margin: 20px 0;">
                <p><b>Codice Account:</b> {code}</p>
                <p><b>PIN di Accesso:</b> {pin}</p>
            </div>
            <p>Accedi al portale tramite questo indirizzo: <a href="{portal_url}">{portal_url}</a></p>
        </div>
        """
        background_tasks.add_task(
            send_email_background, 
            db, 
            email, 
            "Credenziali di Accesso Portale AdaptiQ", 
            html_body
        )

    return {"status": "ok", "message": f"Mandante '{name}' registrato con successo."}


@router.post("/merchants/{merchant_id}/reset-pin")
async def reset_merchant_pin(
    merchant_id: int, 
    payload: ResetPinRequest, 
    background_tasks: BackgroundTasks, 
    db: AsyncSession = Depends(get_db)
):
    if payload.new_pin != payload.confirm_pin:
        raise HTTPException(status_code=400, detail="Il PIN e la conferma non coincidono.")
        
    result = await db.execute(select(Merchant).where(Merchant.id == merchant_id))
    merchant = result.scalar_one_or_none()
    
    if not merchant:
        raise HTTPException(status_code=404, detail="Mandante non trovato.")
        
    merchant.pin_hash = hash_pin(payload.new_pin)
    await db.commit()
    
    if merchant.email:
        smtp_res = await db.execute(select(SmtpSettings).where(SmtpSettings.id == 1))
        smtp_conf = smtp_res.scalar_one_or_none()
        portal_url = smtp_conf.portal_base_url if smtp_conf else "http://localhost"
        
        html_body = f"""
        <div style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #0284c7;">AdaptiQ WMS</h2>
            <h3>Gentile {merchant.company_name},</h3>
            <p>Le tue credenziali di accesso al Portale Inventario sono state appena rigenerate per motivi di sicurezza.</p>
            <div style="background-color: #f8fafc; padding: 15px; border-left: 4px solid #f59e0b; margin: 20px 0;">
                <p><b>Codice Account:</b> {merchant.account_code}</p>
                <p><b>Nuovo PIN:</b> {payload.new_pin}</p>
            </div>
            <p>Accedi al portale tramite questo indirizzo: <a href="{portal_url}">{portal_url}</a></p>
        </div>
        """
        background_tasks.add_task(
            send_email_background, 
            db, 
            merchant.email, 
            "Reset Credenziali Portale AdaptiQ", 
            html_body
        )
        
    return {"message": "PIN resettato con successo. Email accodata se l'indirizzo era presente."}


@router.put("/merchants/{merchant_id}/pin")
async def update_pin(merchant_id: int, payload: UpdatePinRequest, db: AsyncSession = Depends(get_db)):
    pin = payload.pin.strip()
    if not pin:
        raise HTTPException(status_code=400, detail="Il PIN non può essere vuoto.")

    stmt = update(Merchant).where(Merchant.id == merchant_id).values(pin_hash=hash_pin(pin))
    res = await db.execute(stmt)
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Mandante non trovato.")
    await db.commit()
    return {"status": "ok", "message": "PIN di accesso aggiornato con successo."}


@router.post("/merchant/login")
async def merchant_login(cred: MerchantLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    code = cred.account_code.strip()
    stmt = select(Merchant).where(Merchant.account_code == code)
    res = await db.execute(stmt)
    merchant = res.scalar_one_or_none()

    if not merchant or not verify_pin(cred.pin.strip(), merchant.pin_hash):
        raise HTTPException(status_code=401, detail="Codice account o PIN non validi.")

    token = create_merchant_token(merchant.id, merchant.account_code)
    response.set_cookie(
        key="adaptiq_token",
        value=token,
        httponly=True,
        max_age=JWT_EXPIRATION_HOURS * 3600,
        samesite="lax",
        secure=False
    )
    return {"merchant_id": merchant.id, "company_name": merchant.company_name}


@router.post("/merchant/logout")
async def merchant_logout(response: Response):
    response.delete_cookie("adaptiq_token")
    return {"status": "ok"}


@router.get("/merchant/me/inventory")
async def merchant_live_inventory(
    auth: dict = Depends(get_current_merchant_payload),
    db: AsyncSession = Depends(get_db)
):
    merchant_id = int(auth["sub"])
    stmt = select(Item).where(Item.merchant_id == merchant_id).order_by(Item.bin_location.asc(), Item.sku.asc())
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {"sku": i.sku, "barcode": i.barcode, "description": i.description, "bin_location": i.bin_location, "on_hand_qty": i.on_hand_qty}
        for i in rows
    ]


@router.get("/merchant/me/orders")
async def merchant_live_orders(
    auth: dict = Depends(get_current_merchant_payload),
    db: AsyncSession = Depends(get_db)
):
    merchant_id = int(auth["sub"])
    stmt = select(DispatchOrder).where(DispatchOrder.merchant_id == merchant_id).order_by(DispatchOrder.id.desc())
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {"id": o.id, "order_number": o.order_number, "processed_at": o.processed_at, "total_units": o.total_units, "status": o.status}
        for o in rows
    ]
