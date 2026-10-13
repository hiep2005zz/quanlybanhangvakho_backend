# backend/app/schemas/inventory.py
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

class StockAdjustRequest(BaseModel):
    product_id: int
    adjustment: int  # Số lượng thay đổi (dương hoặc âm) theo đơn vị đã chọn (hoặc đơn vị cơ sở)
    reason: str = Field(..., min_length=2, max_length=200)
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

class StockReceiptRequest(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    supplier: str = Field(..., min_length=2, max_length=150)
    note: Optional[str] = None
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

class StockIssueRequest(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    destination: str = Field(..., min_length=2, max_length=150)
    note: Optional[str] = None
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

class StockUpdateRequest(BaseModel):
    new_stock: int = Field(..., ge=0)
    reason: str = Field(..., min_length=2, max_length=200)

class InventoryTransaction(BaseModel):
    id: int
    product_id: int
    product_name: str
    type: str  # "receipt", "issue", "adjust", "stock_count"
    quantity: int
    previous_stock: int
    new_stock: int
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = 1.0
    base_quantity: Optional[int] = None
    performed_by: str
    user_role: str
    reason: str
    created_at: str

class InventoryResponse(BaseModel):
    status: str = "success"
    message: str
    product_id: int
    current_stock: int
    transaction: Optional[InventoryTransaction] = None
