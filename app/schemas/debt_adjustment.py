# backend/app/schemas/debt_adjustment.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class DebtAdjustmentItemCreate(BaseModel):
    product_id: int
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    unit_name: Optional[str] = None
    return_quantity: int = Field(gt=0, description="Số lượng hàng thực trả lại")
    original_unit_price: Optional[float] = None


class DebtAdjustmentCreate(BaseModel):
    return_order_id: int
    reason: str = Field(min_length=1, max_length=500, description="Lý do điều chỉnh giảm trừ")
    note: Optional[str] = Field(None, max_length=500, description="Ghi chú bổ sung")
    adjustment_date: Optional[datetime] = None
    items: Optional[List[DebtAdjustmentItemCreate]] = None


class DebtAdjustmentItemResponse(BaseModel):
    id: int
    adjustment_id: int
    product_id: int
    product_code: str
    product_name: str
    unit_name: Optional[str] = None
    return_quantity: int
    original_unit_price: float
    line_total: float

    model_config = {"from_attributes": True}


class DebtAdjustmentResponse(BaseModel):
    id: int
    adjustment_code: str
    dealer_id: int
    dealer_name: str
    return_order_id: Optional[int] = None
    return_order_code: str
    original_invoice_id: int
    original_invoice_code: str
    adjustment_date: datetime
    subtotal_amount: float
    tax_amount: float
    total_adjusted_amount: float
    status: str
    reason: str
    note: Optional[str] = None
    created_by_id: Optional[int] = None
    created_by_name: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[DebtAdjustmentItemResponse] = []

    model_config = {"from_attributes": True}


class DebtAdjustmentPreviewResponse(BaseModel):
    return_order_id: int
    return_order_code: str
    original_invoice_id: int
    original_invoice_code: str
    order_id: Optional[int] = None
    order_code: str
    dealer_id: int
    dealer_code: Optional[str] = None
    dealer_name: str
    current_debt: float
    credit_limit: float
    remaining_credit: float
    invoice_total_amount: float
    invoice_remaining_debt: float
    subtotal_amount: float
    tax_amount: float
    total_adjusted_amount: float
    estimated_remaining_debt: float
    estimated_dealer_debt: float
    estimated_remaining_credit: float
    reason: str
    items: List[Dict[str, Any]]


class PendingReturnOrderResponse(BaseModel):
    id: int
    return_code: str
    order_id: Optional[int] = None
    order_code: str
    original_invoice_id: Optional[int] = None
    original_invoice_code: str
    dealer_id: int
    dealer_name: str
    status: str
    reason: str
    note: Optional[str] = None
    subtotal_amount: float
    tax_amount: float
    total_amount: float
    total_items_count: int
    confirmed_at: Optional[datetime] = None
    created_at: datetime
    items: List[Dict[str, Any]] = []

    model_config = {"from_attributes": True}


class DebtAdjustmentListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: List[DebtAdjustmentResponse]
