from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, Boolean, Index, Numeric, func
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
    price_list_id = Column(Integer, ForeignKey("price_lists.id"), nullable=True)

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
    # Testata del carico (Fase 2) a cui appartiene il movimento; NULL per i carichi
    # registrati prima dell'introduzione delle testate e per scarichi/rettifiche.
    inbound_receipt_id = Column(Integer, ForeignKey("inbound_receipts.id"), nullable=True, index=True)


class InboundReceipt(Base):
    """Testata di un carico merce (DDT di ingresso): serve ad agganciare i costi dell'operazione."""
    __tablename__ = "inbound_receipts"

    id = Column(Integer, primary_key=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    doc_reference = Column(String(100), nullable=False)
    received_at = Column(String(50), nullable=False)
    total_units = Column(Integer, nullable=False)


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

    __table_args__ = (
        # Un documento può essere evaso una sola volta per mandante (confronto case-insensitive,
        # come nei controlli applicativi): blocca anche i doppi invii simultanei.
        Index("uq_dispatch_orders_merchant_order", "merchant_id", func.lower(order_number), unique=True),
    )


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


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String(50), nullable=False)
    actor = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    target = Column(String(255), nullable=False)
    details = Column(String(500), nullable=True)


# ==========================================
# MODULO COSTI: FASI, SERVIZI, LISTINI
# ==========================================

class BillingPhase(Base):
    """Fase del processo logistico a cui si imputano i costi (raggruppamento dei report)."""
    __tablename__ = "billing_phases"

    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


class BillingService(Base):
    """Voce di costo addebitabile. `basis` (vedi app/billing.py) dice come si ricava la quantità."""
    __tablename__ = "billing_services"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    name = Column(String(150), nullable=False)
    description = Column(String(500), nullable=True)
    phase_id = Column(Integer, ForeignKey("billing_phases.id"), nullable=False)
    basis = Column(String(40), nullable=False)
    unit_label = Column(String(40), nullable=False)
    active = Column(Boolean, nullable=False, default=True)


class PriceList(Base):
    __tablename__ = "price_lists"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    notes = Column(String(500), nullable=True)
    active = Column(Boolean, nullable=False, default=True)


class PriceListLine(Base):
    __tablename__ = "price_list_lines"

    id = Column(Integer, primary_key=True)
    price_list_id = Column(Integer, ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False)
    service_id = Column(Integer, ForeignKey("billing_services.id"), nullable=False)
    # NULL per i servizi a consuntivo (importo deciso sul singolo addebito).
    unit_price = Column(Numeric(12, 4), nullable=True)

    __table_args__ = (
        UniqueConstraint("price_list_id", "service_id", name="uq_price_list_lines_list_service"),
    )


class BillingCharge(Base):
    """Costo imputato a un mandante. Servizio, fase e prezzo sono copiati al momento
    dell'addebito: modificare listini o anagrafica servizi non altera lo storico."""
    __tablename__ = "billing_charges"

    id = Column(Integer, primary_key=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    charge_date = Column(String(50), nullable=False, index=True)
    source_type = Column(String(20), nullable=False)
    dispatch_order_id = Column(Integer, ForeignKey("dispatch_orders.id"), nullable=True, index=True)
    inbound_receipt_id = Column(Integer, ForeignKey("inbound_receipts.id"), nullable=True, index=True)
    service_id = Column(Integer, ForeignKey("billing_services.id"), nullable=False)
    phase_id = Column(Integer, ForeignKey("billing_phases.id"), nullable=False)
    service_code = Column(String(40), nullable=False)
    service_name = Column(String(150), nullable=False)
    phase_name = Column(String(100), nullable=False)
    unit_label = Column(String(40), nullable=False)
    quantity = Column(Numeric(12, 3), nullable=False)
    unit_price = Column(Numeric(12, 4), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    notes = Column(String(255), nullable=True)
    created_at = Column(String(50), nullable=False)
    created_by = Column(String(100), nullable=False)
    # Mese di riferimento (YYYY-MM) degli addebiti di fine mese; univoco per mandante e servizio (migr. 0006).
    period = Column(String(7), nullable=True)
