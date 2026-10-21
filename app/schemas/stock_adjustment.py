# backend/app/schemas/stock_adjustment.py
from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class StockAdjustmentItemInput(BaseModel):
    product_id: int = Field(..., description="ID sản phẩm cần điều chỉnh")
    quantity: int = Field(..., gt=0, description="Số lượng điều chỉnh giảm (> 0)")
    cost_price: Optional[float] = Field(None, ge=0, description="Đơn giá vốn tại thời điểm lập (nếu để trống, tự lấy từ sản phẩm)")
    loss_value: Optional[float] = Field(None, ge=0, description="Giá trị tổn thất cho mặt hàng này")
    batch_number: Optional[str] = Field(None, description="Số lô (nếu có)")
    note: Optional[str] = Field(None, description="Ghi chú chi tiết cho sản phẩm")


class StockAdjustmentCreate(BaseModel):
    warehouse_id: str = Field(..., description="Mã kho (WH01, WH02, WH03...)")
    reason_type: Literal["damaged", "lost", "expired", "other"] = Field(
        ...,
        description="Lý do điều chỉnh bắt buộc: damaged (Hàng hỏng), lost (Hàng mất), expired (Hàng hết hạn phải huỷ), other (Lý do khác)"
    )
    reason_note: str = Field(
        ...,
        min_length=2,
        description="Ghi chú chi tiết bắt buộc về nguyên nhân tổn thất và người chịu trách nhiệm/phát hiện"
    )
    total_loss_value: Optional[float] = Field(
        None,
        ge=0,
        description="Tổng giá trị tổn thất (VND). Nếu để trống, hệ thống tự tính = tổng (quantity * cost_price)"
    )
    items: List[StockAdjustmentItemInput] = Field(
        ...,
        min_length=1,
        description="Danh sách ít nhất 1 mặt hàng cần điều chỉnh tồn kho"
    )


class StockAdjustmentApprove(BaseModel):
    note: Optional[str] = Field(None, description="Ghi chú khi phê duyệt (tùy chọn)")


class StockAdjustmentReject(BaseModel):
    reason: str = Field(..., min_length=2, description="Lý do từ chối phê duyệt bắt buộc")


class StockAdjustmentItemResponse(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    category: Optional[str] = None
    base_unit: str = "Cái"
    quantity: int
    cost_price: float = 0.0
    loss_value: float = 0.0
    batch_number: Optional[str] = None
    note: Optional[str] = None
    current_actual_stock: Optional[int] = None


class StockAdjustmentResponse(BaseModel):
    id: int
    code: str
    warehouse_id: str
    warehouse_name: str
    reason_type: str
    reason_label: str
    reason_note: str
    total_loss_value: float
    total_items: int
    total_quantity: int
    status: str  # PENDING | APPROVED | REJECTED
    status_label: str  # Chờ duyệt | Đã duyệt | Đã từ chối
    items: List[StockAdjustmentItemResponse] = []
    created_by_id: Optional[int] = None
    created_by: str
    created_by_role: Optional[str] = None
    approved_by_id: Optional[int] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_by_id: Optional[int] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    can_approve: bool = False
    created_at: datetime
    updated_at: datetime


class StockAdjustmentListResponse(BaseModel):
    items: List[StockAdjustmentResponse]
    total: int
    page: int = 1
    page_size: int = 10
    total_pages: int = 1
    pending_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    total_loss_value_sum: float = 0.0
