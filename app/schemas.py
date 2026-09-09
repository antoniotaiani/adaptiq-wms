from pydantic import BaseModel, Field
from typing import List, Optional


# ==========================================
# SCHEMI ARTICOLI & RISOLUZIONE BARCODE
# ==========================================

class ItemResolveRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Codice a Barre (EAN) o codice SKU interno")
    merchant_id: Optional[int] = Field(None, description="ID Mandante per contestualizzare la ricerca se il barcode è condiviso")


# ==========================================
# SCHEMI MANDANTI (routers/merchants.py)
# ==========================================

class MerchantLoginRequest(BaseModel):
    account_code: str = Field(..., min_length=1, description="Codice univoco account mandante (es. MCH-DELTA)")
    pin: str = Field(..., min_length=1, description="PIN di accesso mandante")


class CreateMerchantRequest(BaseModel):
    account_code: str = Field(..., min_length=1, description="Codice univoco mandante (es. MCH-DELTA)")
    company_name: str = Field(..., min_length=1, description="Ragione sociale del mandante")
    pin: str = Field(..., min_length=1, description="PIN di sicurezza")


MerchantCreateRequest = CreateMerchantRequest


class UpdatePinRequest(BaseModel):
    account_code: str = Field(..., min_length=1, description="Codice univoco mandante")
    old_pin: str = Field(..., min_length=1, description="PIN attuale")
    new_pin: str = Field(..., min_length=1, description="Nuovo PIN")


# ==========================================
# SCHEMI INBOUND DDT (routers/inbound.py)
# ==========================================

class InboundDDTItem(BaseModel):
    sku: Optional[str] = Field(default="", description="SKU interno. Se vuoto, viene generato progressivamente")
    barcode: str = Field(..., min_length=1, description="Barcode/EAN univoco dell'articolo")
    description: str = Field(..., min_length=1, description="Descrizione prodotto")
    bin_location: Optional[str] = Field(default="INBOUND", description="Ubicazione scaffale")
    quantity: int = Field(..., gt=0, description="Quantità caricata (deve essere maggiore di 0)")


class InboundDDTRequest(BaseModel):
    merchant_id: int = Field(..., gt=0, description="ID del mandante proprietario")
    doc_reference: str = Field(..., min_length=1, description="Numero/Riferimento del documento DDT")
    items: List[InboundDDTItem] = Field(..., min_items=1, description="Lista articoli inclusi nel DDT")


# ==========================================
# SCHEMI ORDINI & SPEDIZIONI (routers/outbound.py)
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
# SCHEMI INVENTARIO (routers/inventory.py)
# ==========================================

class InventoryAdjustRequest(BaseModel):
    sku: str
    new_quantity: int = Field(..., ge=0)
    reason: str
