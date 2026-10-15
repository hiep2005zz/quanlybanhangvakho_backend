# backend/app/schemas/inventory.py
from datetime import datetime
from typing import Optional, List, Union
from pydantic import BaseModel, Field

class StockAdjustRequest(BaseModel):
    product_id: int
    adjustment: int = Field(..., ge=-10_000_000, le=10_000_000, description="Số lượng thay đổi (tối đa 10.000.000)")
    reason: str = Field(..., min_length=2, max_length=200)
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

class StockReceiptRequest(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0, le=10_000_000, description="Số lượng nhập kho (tối đa 10.000.000)")
    supplier: str = Field(..., min_length=2, max_length=150)
    note: Optional[str] = None
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

class StockIssueRequest(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0, le=10_000_000, description="Số lượng xuất kho (tối đa 10.000.000)")
    destination: str = Field(..., min_length=2, max_length=150)
    note: Optional[str] = None
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)
    # SCRUM-81: Quản lý lô xuất kho
    warehouse_id: Optional[Union[int, str]] = None
    selected_batch_id: Optional[int] = None
    suggested_batch_id: Optional[int] = None
    batch_change_reason: Optional[str] = None

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


class AvailableStockResponse(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    dealer_id: int
    dealer_name: str
    warehouse_id: str
    warehouse_name: str
    actual_stock: int
    reserved_stock: int
    available_stock: int
    base_unit: str = "Cái"


class ProductStockSummaryItem(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    base_unit: str = "Cái"
    actual_stock: int
    reserved_stock: int
    available_stock: int


class DealerStockSummaryResponse(BaseModel):
    dealer_id: int
    dealer_name: str
    warehouse_id: str
    warehouse_name: str
    items: List[ProductStockSummaryItem]


class WarehouseInfo(BaseModel):
    warehouse_id: str
    warehouse_name: str


class WarehouseThresholdItem(BaseModel):
    warehouse_id: str
    warehouse_name: str
    product_id: int
    product_code: str
    product_name: str
    category: Optional[str] = "Thời trang"
    base_unit: str = "Cái"
    actual_stock: int
    reserved_stock: int
    available_stock: int
    min_stock: int
    is_low_stock: bool
    shortage_quantity: int


class WarehouseThresholdPageResponse(BaseModel):
    items: List[WarehouseThresholdItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    total_count: Optional[int] = 0
    low_count: Optional[int] = 0
    safe_count: Optional[int] = 0
    active_warehouses: Optional[int] = 3


class UpdateThresholdRequest(BaseModel):
    warehouse_id: str
    product_id: int
    min_stock: int = Field(..., ge=0)


class LowStockAlertSummary(BaseModel):
    total_low_stock: int
    total_alerts: int = 0
    total_products: int
    out_of_stock_count: Optional[int] = 0
    near_depleted_count: Optional[int] = 0
    warehouses: List[dict]
    items: List[WarehouseThresholdItem]
    page: Optional[int] = None
    page_size: Optional[int] = None
    total_pages: Optional[int] = None


class StockBookItem(BaseModel):
    warehouse_id: str
    warehouse_name: str
    product_id: int
    product_code: str
    product_name: str
    category: str
    base_unit: str
    actual_stock: int
    reserved_stock: int
    available_stock: int
    min_stock: int
    is_low_stock: bool
    status: str


class StockBookSummary(BaseModel):
    total_actual_stock: int
    total_reserved_stock: int
    total_available_stock: int
    total_skus: int


class StockBookResponse(BaseModel):
    summary: StockBookSummary
    items: List[StockBookItem]
    warehouses: List[WarehouseInfo]
    categories: List[str]
    total: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None


