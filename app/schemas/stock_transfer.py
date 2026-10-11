# backend/app/schemas/stock_transfer.py
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class StockTransferItemCreate(BaseModel):
    product_id: int = Field(..., gt=0, description="ID sản phẩm cần chuyển")
    transfer_qty: int = Field(..., gt=0, description="Số lượng chuyển đi")


class StockTransferCreateRequest(BaseModel):
    from_warehouse_id: str = Field(..., description="Mã kho gửi (kho đi)")
    to_warehouse_id: str = Field(..., description="Mã kho nhận (kho đến)")
    items: List[StockTransferItemCreate] = Field(..., min_length=1, description="Danh sách mặt hàng cần chuyển")
    note: Optional[str] = Field(None, description="Ghi chú điều chuyển")


class StockTransferReceiveItemRequest(BaseModel):
    product_id: int = Field(..., gt=0)
    received_qty: int = Field(..., ge=0, description="Số lượng thực nhận tại kho đích")
    reason: Optional[str] = Field(None, description="Lý do chênh lệch cho sản phẩm này")


class StockTransferReceiveRequest(BaseModel):
    items: List[StockTransferReceiveItemRequest] = Field(..., min_length=1)
    discrepancy_reason: Optional[str] = Field(None, description="Lý do chênh lệch chung (bắt buộc nếu có chênh lệch)")


class StockTransferItemDetail(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    category: Optional[str] = None
    unit: Optional[str] = "Cái"
    transfer_qty: int
    received_qty: Optional[int] = None
    discrepancy_qty: int = 0
    reason: Optional[str] = None


class StockTransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    from_warehouse_id: str
    from_warehouse_name: str
    to_warehouse_id: str
    to_warehouse_name: str
    status: str  # IN_TRANSIT | COMPLETED | CANCELLED | DRAFT
    note: Optional[str] = None
    total_items: int = 0
    total_transfer_qty: int = 0
    total_received_qty: int = 0
    discrepancy_qty: int = 0
    discrepancy_reason: Optional[str] = None
    items: List[StockTransferItemDetail] = []
    created_by: str
    dispatched_by: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    received_by: Optional[str] = None
    received_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
