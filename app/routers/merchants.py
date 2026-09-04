from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from app.database import get_db
from app.models import Merchant, Item, DispatchOrder
from app.schemas import MerchantLoginRequest, CreateMerchantRequest, UpdatePinRequest
from app.auth import hash_pin, verify_pin, create_merchant_token, get_current_merchant_payload
from app.config import JWT_EXPIRATION_HOURS

router = APIRouter(prefix="/api", tags=["Merchants"])

@router.get("/merchants")
async def get_merchants(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            Merchant.id,
            Merchant.account_code,
            Merchant.company_name,
            func.count(Item.sku).label("sku_count")
        )
        .outerjoin(Item, Merchant.id == Item.merchant_id)
        .group_by(Merchant.id, Merchant.account_code, Merchant.company_name)
        .order_by(Merchant.company_name.asc())
    )
    res = await db.execute(stmt)
    rows = res.fetchall()
    return [{"id": r[0], "account_code": r[1], "company_name": r[2], "sku_count": r[3]} for r in rows]

@router.post("/merchants")
async def create_merchant(payload: CreateMerchantRequest, db: AsyncSession = Depends(get_db)):
    code = payload.account_code.strip().upper()
    name = payload.company_name.strip()
    pin = payload.pin.strip()

    if not code or not name or not pin:
        raise HTTPException(status_code=400, detail="All enrollment fields are required.")

    existing = await db.execute(select(Merchant).where(Merchant.account_code == code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Account code '{code}' already exists.")

    new_m = Merchant(account_code=code, company_name=name, pin_hash=hash_pin(pin))
    db.add(new_m)
    await db.commit()
    return {"status": "ok", "message": f"Merchant '{name}' registered successfully."}

@router.put("/merchants/{merchant_id}/pin")
async def update_pin(merchant_id: int, payload: UpdatePinRequest, db: AsyncSession = Depends(get_db)):
    pin = payload.pin.strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN cannot be empty.")

    stmt = update(Merchant).where(Merchant.id == merchant_id).values(pin_hash=hash_pin(pin))
    res = await db.execute(stmt)
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    await db.commit()
    return {"status": "ok", "message": "Access PIN updated successfully."}

@router.post("/merchant/login")
async def merchant_login(cred: MerchantLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    code = cred.account_code.strip()
    stmt = select(Merchant).where(Merchant.account_code == code)
    res = await db.execute(stmt)
    merchant = res.scalar_one_or_none()

    if not merchant or not verify_pin(cred.pin.strip(), merchant.pin_hash):
        raise HTTPException(status_code=401, detail="Invalid merchant code or PIN.")

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
