# backend/app/schemas/return_order.py
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class ReturnOrderItemCreate(BaseModel):
    order_item_id: Optional[int] = None
    product_id: int
    return_quantity: int = Field(..., gt=0, description="Số lượng trả phải lớn hơn 0")
    return_reason: str = Field(..., min_length=2, description="Lý do trả hàng bắt buộc")
    batch_id: Optional[int] = None
    batch_number: Optional[str] = None
    target_warehouse_id: Optional[int] = None
    note: Optional[str] = None


class ReturnOrderCreate(BaseModel):
    order_id: int
    items: List[ReturnOrderItemCreate] = Field(..., min_length=1, description="Danh sách sản phẩm trả")
    note: Optional[str] = None


class ReturnOrderItemResponse(BaseModel):
    id: int
    return_order_id: int
    order_item_id: Optional[int] = None
    product_id: int
    product_code: Optional[str] = None
    product_name: str = "Sản phẩm"
    unit_name: Optional[str] = "Cái"
    unit_price: float = 0.0
    original_quantity: int = 0
    return_quantity: int = 1
    refund_amount: float = 0.0
    return_reason: str = "Trả hàng đại lý"
    is_damaged: bool = False
    target_warehouse_id: Optional[int] = None
    target_warehouse_code: Optional[str] = None
    target_warehouse_name: Optional[str] = None
    batch_id: Optional[int] = None
    batch_number: Optional[str] = None
    note: Optional[str] = None


class ReturnOrderResponse(BaseModel):
    id: int
    code: str
    order_id: Optional[int] = 0
    order_code: Optional[str] = ""
    dealer_id: Optional[int] = 0
    dealer_name: Optional[str] = "Đại lý"
    status: str = "PENDING_WAREHOUSE"
    total_items: int = 0
    total_quantity: int = 0
    total_refund_amount: float = 0.0
    note: Optional[str] = None
    created_by: Optional[str] = "Hệ thống"
    created_at: str = ""
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[str] = None
    cancel_reason: Optional[str] = None
    items: List[ReturnOrderItemResponse] = []


class ReturnOrderListResponse(BaseModel):
    items: List[ReturnOrderResponse]
    total: int
    page: int = 1
    page_size: int = 20


class ReturnOrderConfirmRequest(BaseModel):
    note: Optional[str] = None


class ReturnOrderCancelRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500, description="Lý do hủy phiếu trả")


class ReturnableItemInfo(BaseModel):
    order_item_id: int
    product_id: int
    product_code: Optional[str] = None
    product_name: str
    unit_name: str
    unit_price: float
    original_quantity: int
    delivered_quantity: int
    already_returned_quantity: int
    remaining_quantity: int
    is_batch_managed: bool
    selected_batch_id: Optional[int] = None
    selected_batch_number: Optional[str] = None
    available_batches: List[dict] = []


class OrderReturnableInfoResponse(BaseModel):
    order_id: int
    order_code: str
    dealer_id: int
    dealer_name: str
    order_status: str
    order_created_at: str
    items: List[ReturnableItemInfo]
    damaged_warehouse: dict
    default_warehouse: dict
