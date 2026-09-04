from pydantic import BaseModel, Field
from typing import List, Optional

class InboundLineItem(BaseModel):
    sku: str = Field(..., min_length=1)
    barcode: Optional[str] = None
    description: str = Field(..., min_length=1)
    bin_location: str = Field(default="INBOUND")
    quantity: int = Field(..., gt=0)

class InboundDDTRequest(BaseModel):
    merchant_id: int
    doc_reference: str = Field(..., min_length=1)
    items: List[InboundLineItem] = Field(..., min_items=1)

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
