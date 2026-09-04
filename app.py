import os
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from jose import JWTError, jwt

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import (
    Column, Integer, String, ForeignKey, select, update, func, text
)

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://adaptiq_user:adaptiq_secure_password_2026@localhost:5432/adaptiq_wms"
)
JWT_SECRET = os.getenv("JWT_SECRET", "adaptiq_jwt_dev_secret_key_2026_xyz")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 12

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


# --- Database Models (PostgreSQL) ---
class Merchant(Base):
    __tablename__ = "merchants"
    id = Column(Integer, primary_key=True, index=True)
    account_code = Column(String(50), unique=True, nullable=False, index=True)
    company_name = Column(String(200), nullable=False)
    pin_hash = Column(String(255), nullable=False)
    items = relationship("Item", back_populates="merchant")
    orders = relationship("DispatchOrder", back_populates="merchant")


class Item(Base):
    __tablename__ = "items"
    sku = Column(String(80), primary_key=True, index=True)
    barcode = Column(String(80), unique=True, nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)
    description = Column(String(255), nullable=False)
    bin_location = Column(String(50), nullable=False, index=True)
    on_hand_qty = Column(Integer, default=0, nullable=False)
    merchant = relationship("Merchant", back_populates="items")


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String(50), nullable=False)
    sku = Column(String(80), ForeignKey("items.sku"), nullable=False)
    transaction_type = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    doc_reference = Column(String(100), nullable=True)


class DispatchOrder(Base):
    __tablename__ = "dispatch_orders"
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(100), nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)
    processed_at = Column(String(50), nullable=False)
    status = Column(String(50), default="COMPLETED", nullable=False)
    total_units = Column(Integer, default=0, nullable=False)
    merchant = relationship("Merchant", back_populates="orders")
    lines = relationship("DispatchOrderLine", back_populates="order")


class DispatchOrderLine(Base):
    __tablename__ = "dispatch_order_lines"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("dispatch_orders.id"), nullable=False)
    sku = Column(String(80), nullable=False)
    barcode = Column(String(80), nullable=False)
    description = Column(String(255), nullable=False)
    bin_location = Column(String(50), nullable=False)
    expected_qty = Column(Integer, nullable=False)
    picked_qty = Column(Integer, nullable=False)
    order = relationship("DispatchOrder", back_populates="lines")


# --- Security & Token Helpers ---
def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return f"{salt}:{key.hex()}"


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        salt, key_hex = stored_hash.split(":")
        calc_key = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 100_000)
        return secrets.compare_digest(calc_key.hex(), key_hex)
    except Exception:
        return False


def create_merchant_token(merchant_id: int, account_code: str) -> str:
    exp = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {"sub": str(merchant_id), "code": account_code, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def get_current_merchant_payload(request: Request) -> dict:
    token = request.cookies.get("adaptiq_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or not authenticated.")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization token.")


# --- Lifespan Manager & Initial Seeding ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lock consultivo (ID arbitrario univoco 847291) per serializzare l'avvio su Gunicorn multi-worker
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(847291)"))
        await conn.run_sync(Base.metadata.create_all)
        
        # Inserimento dati seed solo se non presenti
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(func.count(Merchant.id)))
            count = result.scalar()
            if count == 0:
                m1 = Merchant(account_code="MCH-APEX", company_name="Apex Global Logistics Ltd", pin_hash=hash_pin("1234"))
                m2 = Merchant(account_code="MCH-NORDIC", company_name="Nordic Hardware Direct", pin_hash=hash_pin("5678"))
                session.add_all([m1, m2])
                await session.flush()

                items = [
                    Item(sku="SKU-USB-C-PRO", barcode="8001122334455", merchant_id=m1.id, description="Braided Heavy-Duty USB-C 1m", bin_location="A-01-02", on_hand_qty=150),
                    Item(sku="SKU-GAN-65W", barcode="8009988776655", merchant_id=m1.id, description="Ultra-Compact 65W GaN Fast Charger", bin_location="B-03-01", on_hand_qty=80),
                    Item(sku="SKU-STAND-ALU", barcode="8005544332211", merchant_id=m1.id, description="Ergonomic Aluminum Laptop Stand", bin_location="C-02-04", on_hand_qty=45),
                    Item(sku="SKU-HUB-7IN1", barcode="8007788990011", merchant_id=m2.id, description="7-in-1 Multiport Hub 4K HDMI", bin_location="A-02-05", on_hand_qty=60)
                ]
                session.add_all(items)
                await session.commit()

        await conn.execute(text("SELECT pg_advisory_unlock(847291)"))
    yield

app = FastAPI(
    title="AdaptiQ WMS - Adaptive Logistics & Fulfillment Suite",
    description="High-velocity, multi-tenant warehouse operating system.",
    lifespan=lifespan
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# --- Pydantic Request Schemas ---
class InboundReceiveRequest(BaseModel):
    merchant_id: int
    sku: str
    barcode: str
    description: str
    bin_location: str
    quantity: int
    doc_reference: Optional[str] = "INBOUND-RECEIPT"


class OrderLineItem(BaseModel):
    sku: str
    barcode: str
    description: str
    bin_location: str
    expected_qty: int
    picked_qty: int


class DispatchFulfillRequest(BaseModel):
    order_number: str
    merchant_id: int
    items: List[OrderLineItem]


class MerchantLoginRequest(BaseModel):
    account_code: str
    pin: str


class CreateMerchantRequest(BaseModel):
    account_code: str
    company_name: str
    pin: str


class UpdatePinRequest(BaseModel):
    pin: str


class InventoryAdjustRequest(BaseModel):
    sku: str
    new_quantity: int
    reason: Optional[str] = "CYCLE_COUNT_ADJUSTMENT"


# --- HTML Template Handlers ---
@app.get("/", response_class=HTMLResponse)
async def serve_workstation(request: Request):
    return templates.TemplateResponse(request, "operator.html")


@app.get("/portal", response_class=HTMLResponse)
async def serve_portal(request: Request):
    return templates.TemplateResponse(request, "portal.html")


# --- Merchant Account APIs ---
@app.get("/api/merchants")
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


@app.post("/api/merchants")
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


@app.put("/api/merchants/{merchant_id}/pin")
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


# --- Warehouse Operations APIs ---
@app.get("/api/orders/check/{order_number}")
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


@app.get("/api/inventory")
async def get_inventory(merchant_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            Item.sku, Item.barcode, Item.description, Item.bin_location,
            Item.on_hand_qty, Merchant.company_name, Item.merchant_id
        )
        .join(Merchant, Item.merchant_id == Merchant.id)
    )
    if merchant_id:
        stmt = stmt.where(Item.merchant_id == merchant_id).order_by(Item.bin_location.asc(), Item.sku.asc())
    else:
        stmt = stmt.order_by(Merchant.company_name.asc(), Item.bin_location.asc(), Item.sku.asc())

    res = await db.execute(stmt)
    rows = res.fetchall()
    return [
        {
            "sku": r[0], "barcode": r[1], "description": r[2],
            "bin_location": r[3], "on_hand_qty": r[4],
            "merchant_name": r[5], "merchant_id": r[6]
        }
        for r in rows
    ]


@app.post("/api/inventory/adjust")
async def adjust_inventory(payload: InventoryAdjustRequest, db: AsyncSession = Depends(get_db)):
    if payload.new_quantity < 0:
        raise HTTPException(status_code=400, detail="On-hand stock cannot be negative.")

    sku = payload.sku.strip().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with db.begin():
        stmt = select(Item).where(Item.sku == sku).with_for_update()
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="SKU not found.")

        delta = payload.new_quantity - item.on_hand_qty
        item.on_hand_qty = payload.new_quantity

        tx = InventoryTransaction(
            timestamp=now_str,
            sku=sku,
            transaction_type="CYCLE_COUNT_ADJUSTMENT",
            quantity=delta,
            doc_reference=payload.reason.strip() if payload.reason else "MANUAL_CYCLE_COUNT"
        )
        db.add(tx)

    return {"status": "ok", "message": f"Stock for {sku} adjusted to {payload.new_quantity} (Delta: {delta:+d})"}


@app.post("/api/inbound/receive")
async def receive_inbound(item: InboundReceiveRequest, db: AsyncSession = Depends(get_db)):
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")

    sku = item.sku.strip().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with db.begin():
        stmt = select(Item).where(Item.sku == sku).with_for_update()
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing:
            existing.barcode = item.barcode.strip()
            existing.description = item.description.strip()
            existing.bin_location = item.bin_location.strip()
            existing.on_hand_qty += item.quantity
        else:
            new_item = Item(
                sku=sku,
                barcode=item.barcode.strip(),
                merchant_id=item.merchant_id,
                description=item.description.strip(),
                bin_location=item.bin_location.strip(),
                on_hand_qty=item.quantity
            )
            db.add(new_item)

        tx = InventoryTransaction(
            timestamp=now_str,
            sku=sku,
            transaction_type="INBOUND_RECEIVE",
            quantity=item.quantity,
            doc_reference=item.doc_reference.strip()
        )
        db.add(tx)

    return {"status": "ok", "message": f"Successfully received {item.quantity} units for {sku}"}


@app.post("/api/items/resolve")
async def resolve_item(payload: dict, db: AsyncSession = Depends(get_db)):
    code = payload.get("code", "").strip()
    stmt = (
        select(
            Item.sku, Item.barcode, Item.description, Item.bin_location,
            Item.on_hand_qty, Item.merchant_id, Merchant.company_name
        )
        .join(Merchant, Item.merchant_id == Merchant.id)
        .where((Item.sku == code) | (Item.barcode == code))
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Barcode/SKU [{code}] not found.")

    return {
        "sku": row[0], "barcode": row[1], "description": row[2],
        "bin_location": row[3], "on_hand_qty": row[4],
        "merchant_id": row[5], "merchant_name": row[6]
    }


@app.post("/api/orders/fulfill")
async def fulfill_order(payload: DispatchFulfillRequest, db: AsyncSession = Depends(get_db)):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Cannot dispatch an empty order.")

    order_num = payload.order_number.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Transazione atomica sicura con lock su scorte
    async with db.begin():
        check_stmt = select(DispatchOrder.id).where(
            func.lower(DispatchOrder.order_number) == func.lower(order_num),
            DispatchOrder.merchant_id == payload.merchant_id
        )
        existing_order = (await db.execute(check_stmt)).first()
        if existing_order:
            raise HTTPException(status_code=409, detail=f"Order [{order_num}] has already been fulfilled.")

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
            # Lock pessimistico sulla riga di inventario per evitare scorte negative
            item_stmt = select(Item).where(Item.sku == it.sku).with_for_update()
            item_res = await db.execute(item_stmt)
            curr_item = item_res.scalar_one_or_none()

            if not curr_item:
                raise HTTPException(status_code=404, detail=f"SKU {it.sku} no longer exists.")

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

    return {"status": "success", "order_id": disp_order.id, "timestamp": now_str}


@app.get("/api/orders/history")
async def orders_history(date_filter: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            DispatchOrder.id, DispatchOrder.order_number, Merchant.company_name,
            DispatchOrder.processed_at, DispatchOrder.total_units, DispatchOrder.status
        )
        .join(Merchant, DispatchOrder.merchant_id == Merchant.id)
    )
    if date_filter:
        stmt = stmt.where(DispatchOrder.processed_at.like(f"{date_filter}%"))
    stmt = stmt.order_by(DispatchOrder.id.desc())

    res = await db.execute(stmt)
    rows = res.fetchall()
    return [
        {"id": r[0], "order_number": r[1], "merchant": r[2], "processed_at": r[3], "total_units": r[4], "status": r[5]}
        for r in rows
    ]


@app.get("/api/orders/{order_id}")
async def order_detail(order_id: int, db: AsyncSession = Depends(get_db)):
    stmt_header = (
        select(DispatchOrder.id, DispatchOrder.order_number, Merchant.company_name, DispatchOrder.processed_at, DispatchOrder.total_units)
        .join(Merchant, DispatchOrder.merchant_id == Merchant.id)
        .where(DispatchOrder.id == order_id)
    )
    res_h = await db.execute(stmt_header)
    h = res_h.first()
    if not h:
        raise HTTPException(status_code=404, detail="Order not found.")

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


# --- Authenticated Merchant Portal APIs (JWT + HttpOnly Cookie) ---
@app.post("/api/merchant/login")
async def merchant_login(cred: MerchantLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    code = cred.account_code.strip()
    stmt = select(Merchant).where(Merchant.account_code == code)
    res = await db.execute(stmt)
    merchant = res.scalar_one_or_none()

    if not merchant or not verify_pin(cred.pin.strip(), merchant.pin_hash):
        raise HTTPException(status_code=401, detail="Invalid merchant code or PIN.")

    token = create_merchant_token(merchant.id, merchant.account_code)
    # Imposta cookie HttpOnly cifrato per mitigare attacchi XSS
    response.set_cookie(
        key="adaptiq_token",
        value=token,
        httponly=True,
        max_age=JWT_EXPIRATION_HOURS * 3600,
        samesite="lax",
        secure=False # Impostare su True in produzione con HTTPS attivo
    )
    return {"merchant_id": merchant.id, "company_name": merchant.company_name}


@app.post("/api/merchant/logout")
async def merchant_logout(response: Response):
    response.delete_cookie("adaptiq_token")
    return {"status": "ok"}


@app.get("/api/merchant/me/inventory")
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


@app.get("/api/merchant/me/orders")
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
