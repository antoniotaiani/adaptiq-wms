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

OrderFulfillItem = DispatchItem
OrderFulfillRequest = DispatchFulfillRequest

# ==========================================
# SCHEMI INVENTARIO
# ==========================================
class InventoryAdjustRequest(BaseModel):
    sku: str
    new_quantity: int = Field(..., ge=0)
    reason: str
