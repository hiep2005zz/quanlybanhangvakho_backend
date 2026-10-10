# backend/app/schemas/stock_audit.py
from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class StockAuditItemInput(BaseModel):
    product_id: int
    actual_stock: Optional[int] = Field(None, description="Số lượng thực tế (>= 0 hoặc None nếu chưa kiểm đếm)")
    reason: Optional[str] = Field(None, description="Lý do chênh lệch (bắt buộc khi chênh lệch != 0 lúc xác nhận)")


class StockAuditCreate(BaseModel):
    scope_type: Literal["WAREHOUSE", "CATEGORY"] = "WAREHOUSE"
    warehouse_id: str = Field(..., description="Mã kho kiểm kê (ví dụ: WH01, WH02, WH03)")
    category_id: Optional[int] = Field(None, description="ID nhóm hàng nếu scope_type == CATEGORY")
    note: Optional[str] = None
    product_ids: Optional[List[int]] = Field(None, description="Danh sách sản phẩm được chọn (nếu để trống, tự động nạp tất cả thuộc phạm vi)")


class StockAuditUpdate(BaseModel):
    note: Optional[str] = None
    items: List[StockAuditItemInput]


class StockAuditConfirm(BaseModel):
    note: Optional[str] = None
    items: Optional[List[StockAuditItemInput]] = None


class StockAuditCancel(BaseModel):
    reason: Optional[str] = None


class StockAuditItemResponse(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    category_name: Optional[str] = None
    base_unit: str = "Cái"
    system_stock: int
    actual_stock: Optional[int] = None
    discrepancy: Optional[int] = None
    result_status: str = "PENDING"  # MATCH, DEFICIT, SURPLUS, PENDING
    result_label: str = "Chưa kiểm"  # Khớp, Thiếu X, Thừa X, Chưa kiểm
    reason: Optional[str] = None
    reserved_stock: int = 0
    current_actual_stock: Optional[int] = None  # Tồn kho thực tế ở kho hiện tại lúc xem chi tiết


class StockAuditResponse(BaseModel):
    id: int
    code: str
    warehouse_id: str
    warehouse_name: str
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    scope_type: str
    status: str  # IN_PROGRESS | CONFIRMED | CANCELLED
    status_label: str  # Đang kiểm kê | Hoàn tất | Đã hủy
    note: Optional[str] = None
    total_items: int = 0
    discrepancy_items_count: int = 0
    total_discrepancy_qty: int = 0
    items: List[StockAuditItemResponse] = []
    created_by: str
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class StockAuditListItem(BaseModel):
    id: int
    code: str
    warehouse_id: str
    warehouse_name: str
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    scope_type: str
    status: str
    status_label: str
    note: Optional[str] = None
    total_items: int = 0
    discrepancy_items_count: int = 0
    total_discrepancy_qty: int = 0
    created_by: str
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class StockAuditListResponse(BaseModel):
    items: List[StockAuditListItem]
    total: int
    page: int
    page_size: int
    total_pages: int
