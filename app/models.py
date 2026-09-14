from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from app.database import Base


class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, index=True)
    account_code = Column(String(50), unique=True, nullable=False, index=True)
    company_name = Column(String(200), nullable=False)
    pin_hash = Column(String(255), nullable=False)
    # Nuovi campi aggiunti
    email = Column(String(150), nullable=True)
    phone = Column(String(50), nullable=True)

    items = relationship("Item", back_populates="merchant")
    orders = relationship("DispatchOrder", back_populates="merchant")


class Item(Base):
    __tablename__ = "items"

    sku = Column(String(80), primary_key=True, index=True)
    barcode = Column(String(80), nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)
    description = Column(String(255), nullable=False)
    bin_location = Column(String(50), nullable=False, index=True)
    on_hand_qty = Column(Integer, default=0, nullable=False)

    merchant = relationship("Merchant", back_populates="items")

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


# ==========================================
# NUOVI MODELLI (SMTP & OPERATOR)
# ==========================================

class SmtpSettings(Base):
    __tablename__ = "smtp_settings"

    id = Column(Integer, primary_key=True, index=True)
    smtp_host = Column(String(150), nullable=False, default='')
    smtp_port = Column(Integer, nullable=False, default=587)
    smtp_user = Column(String(150), nullable=False, default='')
    smtp_password = Column(String(255), nullable=False, default='')
    sender_email = Column(String(150), nullable=False, default='')
    sender_name = Column(String(150), nullable=False, default='AdaptiQ Logistics')
    use_tls = Column(Boolean, nullable=False, default=True)
    portal_base_url = Column(String(200), nullable=False, default='http://localhost')


class OperatorUser(Base):
    __tablename__ = "operator_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
