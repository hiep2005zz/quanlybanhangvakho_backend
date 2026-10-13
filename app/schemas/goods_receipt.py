# backend/app/schemas/goods_receipt.py
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict


class GoodsReceiptItemCreate(BaseModel):
    product_id: int
    uom_id: Optional[int] = None
    unit_name: Optional[str] = None
    quantity: float = Field(..., gt=0, description="Số lượng nhập theo ĐVT phải > 0")
    conversion_rate: Optional[float] = Field(None, gt=0, description="Tỷ lệ quy đổi ra ĐVT cơ sở")
    unit_price: Optional[float] = Field(0.0, ge=0, description="Đơn giá nhập")
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    note: Optional[str] = None


class GoodsReceiptItemUpdate(BaseModel):
    id: Optional[int] = None
    product_id: int
    uom_id: Optional[int] = None
    unit_name: Optional[str] = None
    quantity: float = Field(..., gt=0, description="Số lượng nhập theo ĐVT phải > 0")
    conversion_rate: Optional[float] = Field(None, gt=0)
    unit_price: Optional[float] = Field(0.0, ge=0)
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    note: Optional[str] = None


class GoodsReceiptItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    receipt_note_id: int
    product_id: int
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    uom_id: Optional[int] = None
    unit_name: str
    quantity: float
    conversion_rate: float
    base_quantity: float
    unit_price: float
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    note: Optional[str] = None


class GoodsReceiptCreateRequest(BaseModel):
    supplier_id: int = Field(..., description="ID nhà cung cấp")
    reference_number: Optional[str] = Field(None, max_length=100, description="Số hóa đơn / phiếu giao hàng")
    receipt_date: Optional[datetime] = Field(None, description="Ngày nhập hàng")
    warehouse_id: int = Field(..., description="ID kho nhận hàng")
    note: Optional[str] = Field(None, description="Ghi chú phiếu nhập")
    items: List[GoodsReceiptItemCreate] = Field(..., description="Danh sách các dòng sản phẩm nhập")

    @field_validator("items")
    @classmethod
    def validate_items(cls, v: List[GoodsReceiptItemCreate]) -> List[GoodsReceiptItemCreate]:
        if not v or len(v) == 0:
            raise ValueError("Phiếu nhập kho bắt buộc phải có ít nhất 1 dòng hàng.")
        return v


class GoodsReceiptUpdateRequest(BaseModel):
    supplier_id: Optional[int] = None
    reference_number: Optional[str] = None
    receipt_date: Optional[datetime] = None
    warehouse_id: Optional[int] = None
    note: Optional[str] = None
    items: Optional[List[GoodsReceiptItemCreate]] = None


class GoodsReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    supplier_id: int
    supplier_code: Optional[str] = None
    supplier_name: Optional[str] = None
    reference_number: Optional[str] = None
    receipt_date: datetime
    warehouse_id: int
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None
    status: str  # DRAFT, CONFIRMED, CANCELLED
    note: Optional[str] = None
    total_items: int
    total_quantity: float
    total_amount: float
    created_by: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[GoodsReceiptItemResponse] = []


class GoodsReceiptListResponse(BaseModel):
    items: List[GoodsReceiptResponse]
    total: int


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    address: Optional[str] = None
    is_active: bool


class UnitOfMeasureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: Optional[str] = None
