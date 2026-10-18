# backend/app/schemas/debt_reconciliation.py
from __future__ import annotations
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class ReconciliationItemSchema(BaseModel):
    doc_type: str  # "INVOICE" | "PAYMENT"
    doc_code: str
    doc_date: str
    order_code: Optional[str] = None
    description: Optional[str] = None
    increase_amount: float = 0.0  # Mua hàng (làm tăng nợ)
    decrease_amount: float = 0.0  # Trả tiền (làm giảm nợ)
    running_balance: float = 0.0  # Số dư nợ sau chứng từ này
    payment_method: Optional[str] = None  # Tiền mặt, Chuyển khoản...


class DebtReconciliationPreviewRequest(BaseModel):
    dealer_id: int
    from_date: str  # YYYY-MM-DD
    to_date: str    # YYYY-MM-DD


class DebtReconciliationCreateRequest(BaseModel):
    dealer_id: int
    from_date: str  # YYYY-MM-DD
    to_date: str    # YYYY-MM-DD
    note: Optional[str] = None
    send_immediately: bool = False  # True -> trạng thái SENT, False -> DRAFT


class DebtDisputeRequest(BaseModel):
    dealer_reported_balance: float = Field(..., description="Số dư nợ theo ghi nhận của đại lý")
    reason: str = Field(..., min_length=5, description="Lý do chênh lệch")


class DebtResolveRequest(BaseModel):
    resolution_note: str = Field(..., min_length=5, description="Ghi chú giải quyết chênh lệch của kế toán")


class DebtReconciliationResponse(BaseModel):
    id: int
    code: str
    dealer_id: int
    dealer_code: str
    dealer_name: str
    dealer_phone: Optional[str] = None
    dealer_address: Optional[str] = None
    from_date: str
    to_date: str
    period_label: str
    opening_balance: float
    total_increase: float
    total_decrease: float
    closing_balance: float
    status: str
    items: List[ReconciliationItemSchema] = []
    dealer_reported_balance: Optional[float] = None
    dealer_dispute_reason: Optional[str] = None
    disputed_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    confirmed_by_name: Optional[str] = None
    created_by_name: Optional[str] = None
    note: Optional[str] = None
    resolution_note: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


class DebtReconciliationListResponse(BaseModel):
    items: List[DebtReconciliationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    kpi_summary: Dict[str, Any]
