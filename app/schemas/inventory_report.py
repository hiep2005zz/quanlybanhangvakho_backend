# backend/app/schemas/inventory_report.py
from typing import Optional, List
from pydantic import BaseModel


class WarehouseBreakdown(BaseModel):
    warehouse_id: str
    warehouse_name: str
    total_value: float
    total_stock: int
    sku_count: int
    percentage: float


class CategoryBreakdown(BaseModel):
    category: str
    total_value: float
    total_stock: int
    sku_count: int
    percentage: float


class TurnoverReportItem(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    category: str
    base_unit: str
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
    current_stock: int
    cost_price: float
    inventory_value: float
    sold_quantity: int
    cogs: float
    turnover_rate: float
    dsi: Optional[float] = None
    last_sale_date: Optional[str] = None
    days_without_sale: Optional[int] = None
    status: str  # stagnant, slow, normal, fast, out_of_stock
    status_label: str  # Tồn bất động, Chậm luân chuyển, Bình thường, Luân chuyển nhanh, Hết hàng
    status_severity: str  # danger, warning, info, success, neutral
    warning_note: Optional[str] = None


class TurnoverReportSummary(BaseModel):
    total_inventory_value: float
    slow_stagnant_value: float
    slow_stagnant_skus: int
    total_skus: int
    avg_turnover_rate: float
    avg_dsi: Optional[float] = None
    total_cogs: float
    period_days: int


class TurnoverSummaryResponse(BaseModel):
    summary: TurnoverReportSummary
    warehouse_breakdown: List[WarehouseBreakdown]
    category_breakdown: List[CategoryBreakdown]
    warehouses: List[dict]
    categories: List[str]


class TurnoverReportResponse(BaseModel):
    summary: Optional[TurnoverReportSummary] = None
    warehouse_breakdown: Optional[List[WarehouseBreakdown]] = None
    category_breakdown: Optional[List[CategoryBreakdown]] = None
    items: List[TurnoverReportItem]
    warehouses: Optional[List[dict]] = None
    categories: Optional[List[str]] = None
    total: int
    page: int
    page_size: int
    total_pages: int
