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
    Column, Integer, String, ForeignKey, select, update, func, text, CheckConstraint, UniqueConstraint, or_
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
    __table_args__ = (
        CheckConstraint("on_hand_qty >= 0", name="chk_item_on_hand_qty_non_negative"),
        UniqueConstraint("merchant_id", "barcode", name="uq_items_merchant_barcode"),
    )

    sku = Column(String(80), primary_key=True, index=True)
    barcode = Column(String(80), nullable=False, index=True)
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


# --- Lifespan Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(847291)"))
        await conn.run_sync(Base.metadata.create_all)
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


# --- Pydantic Schemas ---
class ItemResolveRequest(BaseModel):
    code: str
    merchant_id: Optional[int] = None


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


# --- HTML Handlers ---
@app.get("/", response_class=HTMLResponse)
async def serve_workstation(request: Request):
    return templates.TemplateResponse(request, "operator.html")


@app.get("/portal", response_class=HTMLResponse)
async def serve_portal(request: Request):
    return templates.TemplateResponse(request, "portal.html")


# --- Warehouse Operations APIs ---
@app.post("/api/items/resolve")
async def resolve_item(payload: ItemResolveRequest, db: AsyncSession = Depends(get_db)):
    code = payload.code.strip()
    m_id = payload.merchant_id

    if m_id is not None:
        stmt = select(Item).where(
            Item.merchant_id == int(m_id),
            or_(Item.sku == code, Item.barcode == code)
        )
        res = await db.execute(stmt)
        item = res.scalar_one_or_none()
    else:
        stmt = select(Item).where(or_(Item.sku == code, Item.barcode == code))
        res = await db.execute(stmt)
        item = res.scalars().first()

    if not item:
        raise HTTPException(status_code=404, detail=f"Barcode/SKU [{code}] non trovato per questo mandante.")

    merchant = await db.get(Merchant, item.merchant_id)

    return {
        "sku": item.sku,
        "barcode": item.barcode,
        "description": item.description,
        "bin_location": item.bin_location,
        "on_hand_qty": item.on_hand_qty,
        "merchant_id": item.merchant_id,
        "merchant_name": merchant.company_name if merchant else ""
    }
