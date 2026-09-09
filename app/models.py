from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


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
    # Rimosso unique=True per consentire lo stesso barcode a mandanti differenti
    barcode = Column(String(80), nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)
    description = Column(String(255), nullable=False)
    bin_location = Column(String(50), nullable=False, index=True)
    on_hand_qty = Column(Integer, default=0, nullable=False)

    merchant = relationship("Merchant", back_populates="items")

    # Vincolo univoco internazionale allineato a PostgreSQL
    __table_args__ = (
        UniqueConstraint("merchant_id", "barcode", name="uq_items_merchant_barcode"),
    )


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
