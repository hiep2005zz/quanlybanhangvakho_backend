# backend/app/schemas/product.py
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

class PriceUpdateRequest(BaseModel):
    sell_price: Optional[float] = Field(None, ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    reason: Optional[str] = "Điều chỉnh giá niêm yết/giá vốn"

class UnitConversionItem(BaseModel):
    unit_name: str = Field(..., min_length=1, max_length=50)
    conversion_rate: float = Field(..., gt=0)  # Hệ số quy đổi bắt buộc > 0

class UnitUpdateRequest(BaseModel):
    base_unit: Optional[str] = Field(None, min_length=1, max_length=50)
    units: Optional[List[UnitConversionItem]] = None

class ProductCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    category: Optional[str] = "Thời trang"
    category_id: Optional[int] = None
    base_unit: Optional[str] = "Cái"
    units: Optional[List[UnitConversionItem]] = []
    packaging_specification: Optional[str] = None
    sell_price: Optional[float] = Field(0.0, ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    images: Optional[List[str]] = []
    status: Optional[str] = "active"
    is_batch_managed: Optional[bool] = False

class ProductUpdateRequest(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[str] = None
    category_id: Optional[int] = None
    base_unit: Optional[str] = None
    units: Optional[List[UnitConversionItem]] = None
    packaging_specification: Optional[str] = None
    sell_price: Optional[float] = Field(None, ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    images: Optional[List[str]] = None
    status: Optional[str] = None
    is_batch_managed: Optional[bool] = None

class ProductItem(BaseModel):
    id: int
    code: str
    name: str
    category: str
    category_id: Optional[int] = None
    stock: int
    sell_price: float
    base_unit: str = "Cái"
    units: List[UnitConversionItem] = []
    packaging_specification: Optional[str] = None
    images: List[str] = []
    status: str = "active"
    is_batch_managed: Optional[bool] = False
    transaction_count: Optional[int] = 0
    # Dữ liệu nhạy cảm (AC 3: Bị lọc bỏ hoàn toàn nếu không phải Admin hoặc Sales Manager)
    cost_price: Optional[float] = None
    profit_margin: Optional[float] = None
    profit_per_unit: Optional[float] = None

class ProductFinancialSummary(BaseModel):
    total_products: int
    total_stock: int
    total_sell_value: float
    total_cost_value: Optional[float] = None
    total_gross_profit: Optional[float] = None
    average_margin_percent: Optional[float] = None

class ProductListResponse(BaseModel):
    items: List[ProductItem]
    total: int
    user_role: str
    is_cost_price_visible: bool
    summary: Optional[ProductFinancialSummary] = None
