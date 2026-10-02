from decimal import Decimal
from pydantic import BaseModel, Field
from typing import List, Optional

# ==========================================
# SCHEMI ARTICOLI & RISOLUZIONE BARCODE
# ==========================================
class ItemResolveRequest(BaseModel):
    code: str = Field(..., min_length=1)
    merchant_id: Optional[int] = None

# ==========================================
# SCHEMI MANDANTI & AUTH
# ==========================================
class MerchantLoginRequest(BaseModel):
    account_code: str = Field(..., min_length=1)
    pin: str = Field(..., min_length=1) # Rimane 1 per retrocompatibilità

class CreateMerchantRequest(BaseModel):
    account_code: str = Field(..., min_length=1)
    company_name: str = Field(..., min_length=1)
    pin: str = Field(..., min_length=8, description="Il PIN per le nuove utenze deve essere di almeno 8 caratteri")
    email: Optional[str] = None
    phone: Optional[str] = None
    price_list_id: Optional[int] = None

MerchantCreateRequest = CreateMerchantRequest

class UpdatePinRequest(BaseModel):
    account_code: str = Field(..., min_length=1, description="Codice univoco mandante")
    old_pin: str = Field(..., min_length=1, description="PIN attuale")
    new_pin: str = Field(..., min_length=1, description="Nuovo PIN")

class ResetPinRequest(BaseModel):
    new_pin: str = Field(..., min_length=8, description="Il nuovo PIN deve essere di almeno 8 caratteri")
    confirm_pin: str = Field(..., min_length=8)

class UpdateMerchantRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    email: Optional[str] = None
    phone: Optional[str] = None
    # Aggiornato solo se presente nel payload (vedi model_fields_set in update_merchant)
    price_list_id: Optional[int] = None

class OperatorLoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

# ==========================================
# SCHEMI SMTP CONFIGURATION
# ==========================================
class SmtpSettingsSchema(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    sender_email: str
    sender_name: str
    use_tls: bool
    portal_base_url: str

# ==========================================
# SCHEMI INBOUND DDT
# ==========================================
class InboundDDTItem(BaseModel):
    sku: Optional[str] = Field(default="")
    barcode: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    bin_location: Optional[str] = Field(default="INBOUND")
    quantity: int = Field(..., gt=0)

class InboundDDTRequest(BaseModel):
    merchant_id: int = Field(..., gt=0)
    doc_reference: str = Field(..., min_length=1)
    items: List[InboundDDTItem] = Field(..., min_items=1)
    charges: List["ChargeInput"] = []

# ==========================================
# SCHEMI ORDINI & SPEDIZIONI
# ==========================================
class OrderValidateItem(BaseModel):
    sku: str
    expected_qty: int = Field(..., gt=0)

class OrderValidateRequest(BaseModel):
    order_number: str
    merchant_id: int
    items: List[OrderValidateItem]

class DispatchItem(BaseModel):
    sku: str
    barcode: Optional[str] = ""
    description: Optional[str] = ""
    bin_location: Optional[str] = ""
    expected_qty: int
    picked_qty: int

class DispatchFulfillRequest(BaseModel):
    order_number: str
    merchant_id: int
    items: List[DispatchItem]
    charges: List["ChargeInput"] = []

OrderFulfillItem = DispatchItem
OrderFulfillRequest = DispatchFulfillRequest

# ==========================================
# SCHEMI INVENTARIO
# ==========================================
class InventoryAdjustRequest(BaseModel):
    sku: str
    new_quantity: int = Field(..., ge=0)
    reason: str

# ==========================================
# SCHEMI MODULO COSTI (FASI, SERVIZI, LISTINI)
# ==========================================
class BillingPhaseRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=30)
    name: str = Field(..., min_length=1, max_length=100)
    sort_order: int = 0

class BillingServiceRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = Field(default=None, max_length=500)
    phase_id: int
    basis: str
    unit_label: str = Field(..., min_length=1, max_length=40)
    active: bool = True

class PriceListCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    notes: Optional[str] = Field(default=None, max_length=500)
    copy_from_id: Optional[int] = None

class PriceListPrice(BaseModel):
    """Voce del listino. `unit_price` obbligatorio, tranne per i servizi a consuntivo (ignorato)."""
    service_id: int
    unit_price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=4)

class PriceListUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    notes: Optional[str] = Field(default=None, max_length=500)
    active: bool = True
    prices: List[PriceListPrice] = []

class ChargeInput(BaseModel):
    """Riga della scheda costi. `id` presente = riga già salvata (se ne aggiorna la quantità,
    il prezzo resta quello dell'addebito originale). `amount` solo per i servizi a consuntivo."""
    id: Optional[int] = None
    service_id: int
    quantity: Decimal = Field(..., gt=0, max_digits=12, decimal_places=3)
    amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    notes: Optional[str] = Field(default=None, max_length=255)

class DocumentChargesRequest(BaseModel):
    charges: List[ChargeInput] = []

class MonthlyChargeInput(BaseModel):
    """Quantità dichiarata a fine mese per un servizio mensile; vuota o 0 = nessun addebito."""
    merchant_id: int
    service_id: int
    quantity: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=3)

class MonthlyChargesRequest(BaseModel):
    period: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    entries: List[MonthlyChargeInput] = []

InboundDDTRequest.model_rebuild()
DispatchFulfillRequest.model_rebuild()
