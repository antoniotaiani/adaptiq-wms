from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, select, func

from app.config import STATIC_DIR
from app.database import engine, Base, AsyncSessionLocal
from app.models import Merchant, Item
from app.auth import hash_pin
from app.routers import views, merchants, inventory, inbound, outbound, items

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(847291)"))
        await conn.run_sync(Base.metadata.create_all)
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(func.count(Merchant.id)))
            count = result.scalar()
            if count == 0:
                m1 = Merchant(account_code="MCH-APEX", company_name="Apex Global Logistics Ltd", pin_hash=hash_pin("1234"))
                m2 = Merchant(account_code="MCH-NORDIC", company_name="Nordic Hardware Direct", pin_hash=hash_pin("5678"))
                session.add_all([m1, m2])
                await session.flush()

                seed_items = [
                    Item(sku="SKU-USB-C-PRO", barcode="8001122334455", merchant_id=m1.id, description="Braided Heavy-Duty USB-C 1m", bin_location="A-01-02", on_hand_qty=150),
                    Item(sku="SKU-GAN-65W", barcode="8009988776655", merchant_id=m1.id, description="Ultra-Compact 65W GaN Fast Charger", bin_location="B-03-01", on_hand_qty=80),
                    Item(sku="SKU-STAND-ALU", barcode="8005544332211", merchant_id=m1.id, description="Ergonomic Aluminum Laptop Stand", bin_location="C-02-04", on_hand_qty=45),
                    Item(sku="SKU-HUB-7IN1", barcode="8007788990011", merchant_id=m2.id, description="7-in-1 Multiport Hub 4K HDMI", bin_location="A-02-05", on_hand_qty=60)
                ]
                session.add_all(seed_items)
                await session.commit()

        await conn.execute(text("SELECT pg_advisory_unlock(847291)"))
    yield

app = FastAPI(
    title="AdaptiQ WMS - Adaptive Logistics & Fulfillment Suite",
    description="High-velocity, multi-tenant warehouse operating system.",
    lifespan=lifespan
)

STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Registrazione Router modulari
app.include_router(views.router)
app.include_router(merchants.router)
app.include_router(inventory.router)
app.include_router(inbound.router)
app.include_router(outbound.router)
app.include_router(items.router)
