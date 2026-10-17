# backend/app/schemas/payment_collection.py
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


class PaymentCollectionCreate(BaseModel):
    dealer_id: int = Field(..., description="ID của đại lý / cửa hàng")
    order_id: Optional[int] = Field(None, description="ID đơn hàng liên quan (nếu có)")
    amount: float = Field(..., gt=0, description="Số tiền thu (bắt buộc > 0 VNĐ)")
    payment_method: str = Field("CASH", description="Phương thức: CASH (Tiền mặt) hoặc BANK_TRANSFER (Chuyển khoản)")
    receipt_image_url: Optional[str] = Field(None, description="Base64 hoặc URL ảnh biên lai / phiếu thu")
    collection_date: Optional[datetime] = Field(None, description="Thời gian thu tiền tại tuyến")
    route_name: Optional[str] = Field(None, description="Tên tuyến bán hàng")
    note: Optional[str] = Field(None, description="Ghi chú chi tiết khoản thu")


class PaymentCollectionRemit(BaseModel):
    remittance_note: Optional[str] = Field(None, description="Ghi chú khi nhân viên nộp tiền về công ty")


class BulkPaymentCollectionRemit(BaseModel):
    collection_ids: List[int] = Field(..., min_length=1, description="Danh sách ID các khoản thu cần nộp")
    remittance_note: Optional[str] = Field(None, description="Ghi chú nộp theo lô")


class PaymentCollectionConfirm(BaseModel):
    confirmation_note: Optional[str] = Field(None, description="Ghi chú kế toán xác nhận nhận đủ tiền")


class BulkPaymentCollectionConfirm(BaseModel):
    collection_ids: List[int] = Field(..., min_length=1, description="Danh sách ID các khoản thu kế toán duyệt")
    confirmation_note: Optional[str] = Field(None, description="Ghi chú xác nhận lô")


class PaymentCollectionReject(BaseModel):
    rejection_reason: Optional[str] = Field(None, description="Lý do từ chối hoặc sai lệch tiền")
    reason: Optional[str] = Field(None, description="Lý do từ chối (fallback)")

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        val = (self.rejection_reason or self.reason or "").strip()
        if not val:
            raise ValueError("Vui lòng nhập lý do từ chối / báo lệch.")
        self.rejection_reason = val
        return self


class PaymentCollectionResponse(BaseModel):
    id: int
    code: str
    sales_id: int
    sales_name: str
    dealer_id: int
    dealer_name: str
    order_id: Optional[int] = None
    order_code: Optional[str] = None
    amount: float
    payment_method: str
    receipt_image_url: Optional[str] = None
    collection_date: datetime
    route_name: Optional[str] = None
    note: Optional[str] = None
    status: str
    submitted_at: Optional[datetime] = None
    remittance_note: Optional[str] = None
    confirmed_by_id: Optional[int] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    confirmation_note: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaymentCollectionListResponse(BaseModel):
    items: List[PaymentCollectionResponse]
    total: int
    total_amount: float
    total_pending_remittance: float
    total_submitted: float
    total_confirmed: float
    page: int = 1
    page_size: int = 10
    total_pages: int = 1


class DailyReconciliationItem(BaseModel):
    date: str  # YYYY-MM-DD
    sales_id: int
    sales_name: str
    total_collected: float
    total_pending_remittance: float
    total_submitted: float
    total_confirmed: float
    total_rejected: float
    collections_count: int
    is_balanced: bool  # True nếu không còn tiền chờ nộp hoặc chờ duyệt
    collections: List[PaymentCollectionResponse]


class ReconciliationSummary(BaseModel):
    grand_total_collected: float
    grand_total_pending_remittance: float
    grand_total_submitted: float
    grand_total_confirmed: float
    grand_total_rejected: float
    total_days: int
    total_sales_staff: int


class DailyReconciliationResponse(BaseModel):
    summary: ReconciliationSummary
    items: List[DailyReconciliationItem]
    total: int = 0
    page: int = 1
    page_size: int = 10
    total_pages: int = 1

