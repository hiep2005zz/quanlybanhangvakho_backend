# backend/app/schemas/batch.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator


class BatchCreateRequest(BaseModel):
    product_id: int = Field(..., description="ID sản phẩm")
    warehouse_id: int = Field(1, description="ID kho lưu trữ")
    batch_number: str = Field(..., min_length=1, max_length=100, description="Số lô hàng")
    manufacturing_date: Optional[datetime] = Field(None, description="Ngày sản xuất")
    expiry_date: datetime = Field(..., description="Hạn sử dụng")
    initial_quantity: float = Field(0.0, ge=0.0, description="Số lượng tồn kho ban đầu theo lô")
    note: Optional[str] = Field(None, max_length=500, description="Ghi chú")

    @model_validator(mode="after")
    def validate_dates(self):
        if self.manufacturing_date and self.expiry_date:
            if self.expiry_date <= self.manufacturing_date:
                raise ValueError("Hạn sử dụng (expiry_date) bắt buộc phải sau Ngày sản xuất (manufacturing_date).")
        return self


class BatchUpdateRequest(BaseModel):
    manufacturing_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.manufacturing_date and self.expiry_date:
            if self.expiry_date <= self.manufacturing_date:
                raise ValueError("Hạn sử dụng (expiry_date) bắt buộc phải sau Ngày sản xuất (manufacturing_date).")
        return self


class BatchResponse(BaseModel):
    id: int
    product_id: int
    product_code: str
    product_name: str
    base_unit: str = "Cái"
    warehouse_id: int
    warehouse_name: str = "Kho Tổng Hà Nội"
    batch_number: str
    manufacturing_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    remaining_quantity: float
    days_until_expiry: Optional[int] = None
    expiry_status: str  # "EXPIRED" | "NEAR_EXPIRY" | "SAFE"
    warning_threshold_days: int = 30
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class BatchListResponse(BaseModel):
    items: List[BatchResponse]
    total: int
    expired_count: int
    near_expiry_count: int
    safe_count: int
    warning_threshold_days: int
    page: int = 1
    page_size: int = 10
    total_pages: int = 1


class BatchTrackingToggleRequest(BaseModel):
    is_batch_managed: bool = Field(..., description="Cờ bật/tắt quản lý lô theo SKU")
    initial_batch_number: Optional[str] = Field(None, max_length=100, description="Số lô khởi tạo nếu sản phẩm đã có tồn kho")
    initial_manufacturing_date: Optional[datetime] = Field(None, description="Ngày sản xuất khởi tạo")
    initial_expiry_date: Optional[datetime] = Field(None, description="Hạn sử dụng khởi tạo")
    reason: Optional[str] = Field(None, max_length=255, description="Lý do chuyển đổi trạng thái")

    @model_validator(mode="after")
    def validate_initial_dates(self):
        if self.initial_manufacturing_date and self.initial_expiry_date:
            if self.initial_expiry_date <= self.initial_manufacturing_date:
                raise ValueError("Hạn sử dụng khởi tạo bắt buộc phải sau Ngày sản xuất khởi tạo.")
        return self


class BatchTrackingToggleResponse(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    is_batch_managed: bool
    message: str
    batch_created: Optional[BatchResponse] = None
