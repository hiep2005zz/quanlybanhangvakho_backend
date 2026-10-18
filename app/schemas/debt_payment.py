# backend/app/schemas/debt_payment.py
from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class AllocationItemRequest(BaseModel):
    invoice_id: int = Field(..., description="ID của hoá đơn được đối trừ")
    allocated_amount: float = Field(..., gt=0, description="Số tiền đối trừ vào hoá đơn này (> 0)")


class CreatePaymentReceiptRequest(BaseModel):
    """
    Tiêu chí 1: Ghi số tiền, hình thức thu, ngày thu, người thu, ghi chú.
    Tiêu chí 2: allocation_mode: AUTO_FIFO hoặc MANUAL.
    """
    dealer_id: int = Field(..., description="ID của đại lý nộp tiền")
    amount: float = Field(..., gt=0, description="Số tiền thu (> 0)")
    payment_method: str = Field(default="Tiền mặt", description="Hình thức thu: Tiền mặt, Chuyển khoản, Khác")
    payment_date: Optional[datetime] = Field(default=None, description="Ngày thu (mặc định hiện tại)")
    collector_name: Optional[str] = Field(default=None, description="Tên người thu tiền")
    note: Optional[str] = Field(default=None, description="Ghi chú thu tiền")
    allocation_mode: str = Field(default="AUTO_FIFO", description="Chế độ đối trừ: AUTO_FIFO | MANUAL")
    allocations: Optional[List[AllocationItemRequest]] = Field(default=None, description="Danh sách phân bổ thủ công khi allocation_mode=MANUAL")
    save_history: Optional[bool] = Field(default=True, description="Lưu vào lịch sử phiếu thu (mặc định True, đặt False nếu không lưu vào lịch sử)")


class PreviewFifoRequest(BaseModel):
    dealer_id: int = Field(..., description="ID đại lý")
    amount: float = Field(..., gt=0, description="Số tiền thu dự kiến")


class FifoPreviewItem(BaseModel):
    invoice_id: int
    invoice_code: str
    issue_date: str
    due_date: str
    total_amount: float
    paid_before: float
    remaining_before: float
    allocated_amount: float
    remaining_after: float
    new_status: str
    new_status_label: str


class InvoiceAdjustmentItem(BaseModel):
    invoice_id: int = Field(..., description="ID của hoá đơn được điều chỉnh")
    amount: float = Field(..., ge=0, description="Số tiền điều chỉnh giảm cho hoá đơn này (>= 0)")


class CreateAdjustmentRequest(BaseModel):
    """
    Tiêu chí 5: Đã ghi nhận thì không xoá, chỉ lập bút toán điều chỉnh.
    BẮT BUỘC nhập lý do điều chỉnh *.
    """
    reason: str = Field(..., min_length=1, description="Lý do điều chỉnh (Bắt buộc)")
    adjustment_amount: Optional[float] = Field(default=None, gt=0, description="Số tiền điều chỉnh (mặc định toàn bộ)")
    invoice_id: Optional[int] = Field(default=None, description="ID hoá đơn điều chỉnh cụ thể (tuỳ chọn)")
    invoice_adjustments: Optional[List[InvoiceAdjustmentItem]] = Field(default=None, description="Danh sách chi tiết điều chỉnh từng hoá đơn (tuỳ chọn)")


class PaymentAllocationResponse(BaseModel):
    id: int
    payment_id: int
    invoice_id: int
    invoice_code: Optional[str] = None
    allocated_amount: float
    current_allocated_amount: float
    invoice_total: Optional[float] = None
    invoice_paid: Optional[float] = None
    invoice_remaining: Optional[float] = None
    invoice_status: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentAdjustmentResponse(BaseModel):
    id: int
    adjustment_code: str
    payment_id: int
    adjusted_by_id: Optional[int] = None
    adjusted_by_name: str
    adjustment_amount: float
    reason: str
    details_json: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentReceiptResponse(BaseModel):
    id: int
    receipt_code: str
    dealer_id: int
    dealer_code: Optional[str] = None
    dealer_name: str
    amount: float
    payment_method: str
    payment_date: datetime
    collector_id: Optional[int] = None
    collector_name: str
    note: Optional[str] = None
    allocation_mode: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    allocations: List[PaymentAllocationResponse] = []
    adjustments: List[PaymentAdjustmentResponse] = []

    model_config = ConfigDict(from_attributes=True)


class DealerDebtSummaryResponse(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    credit_limit: float
    total_receivable: float
    total_paid: float
    total_remaining_debt: float
    open_invoices_count: int
    overdue_invoices_count: int
    is_overdue: bool
    is_over_limit: bool


class InvoiceAllocationOption(BaseModel):
    id: int
    invoice_code: str
    order_code: Optional[str] = None
    issue_date: str
    due_date: str
    days_until_due: int
    is_overdue: bool
    total_amount: float
    paid_amount: float
    remaining_debt: float
    remaining_amount: float
    status: str
    status_label: str
    items_count: int = 0
    note: Optional[str] = None
