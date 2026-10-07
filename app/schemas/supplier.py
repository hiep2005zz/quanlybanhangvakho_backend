# backend/app/schemas/supplier.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SupplierCreate(BaseModel):
    """Dữ liệu khai báo nhà cung cấp mới. Không nhận trường lạ."""
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., max_length=30)
    name: str = Field(..., max_length=255)
    tax_code: Optional[str] = Field(None, max_length=20)
    contact_person: Optional[str] = Field(None, max_length=255)
    payment_terms: Optional[str] = Field(None, max_length=255)


class SupplierUpdate(BaseModel):
    """Cập nhật thông tin. Mã nhà cung cấp và trạng thái KHÔNG được sửa ở đây."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=255)
    tax_code: Optional[str] = Field(None, max_length=20)
    contact_person: Optional[str] = Field(None, max_length=255)
    payment_terms: Optional[str] = Field(None, max_length=255)


class SupplierDeactivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, max_length=255)


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    tax_code: Optional[str] = None
    contact_person: Optional[str] = None
    payment_terms: Optional[str] = None
    is_active: bool
    inactive_reason: Optional[str] = None
    deactivated_at: Optional[datetime] = None
    deactivated_by: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SupplierListResponse(BaseModel):
    items: List[SupplierResponse]
    total: int
    active_count: int
    inactive_count: int