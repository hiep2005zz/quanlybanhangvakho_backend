# backend/app/schemas/warehouse.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


# --- WAREHOUSE ---
class WarehouseBase(BaseModel):
    code: str = Field(..., min_length=2, max_length=50, description="Mã kho hàng (duy nhất)")
    name: str = Field(..., min_length=2, max_length=255, description="Tên kho hàng")
    address: Optional[str] = Field(None, max_length=500, description="Địa chỉ kho")
    manager_name: Optional[str] = Field(None, max_length=100, description="Người phụ trách kho")
    phone: Optional[str] = Field(None, max_length=50, description="Số điện thoại liên hệ")
    status: Optional[str] = Field("Đang hoạt động", max_length=50, description="Trạng thái hoạt động")
    is_active: Optional[bool] = Field(True, description="Kích hoạt")


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    manager_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    status: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None


class WarehouseResponse(BaseModel):
    id: int
    code: str
    name: str
    address: Optional[str] = None
    manager_name: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = "Đang hoạt động"
    is_active: bool = True
    created_at: Optional[datetime] = None
    locations_count: int = 0
    total_products_count: int = 0
    total_stock_quantity: int = 0

    model_config = ConfigDict(from_attributes=True)


# --- WAREHOUSE LOCATIONS ---
class WarehouseLocationCreate(BaseModel):
    location_code: str = Field(..., min_length=2, max_length=50, description="Mã vị trí / kệ / khu vực")
    location_name: Optional[str] = Field(None, max_length=255, description="Tên gợi nhớ vị trí")
    zone: Optional[str] = Field(None, max_length=50, description="Khu vực lưu trữ (Khu A, Khu B...)")
    aisle: Optional[str] = Field(None, max_length=50, description="Dãy kệ (Dãy 01...)")
    rack: Optional[str] = Field(None, max_length=50, description="Tầng kệ (Tầng 1, Tầng 2...)")
    bin: Optional[str] = Field(None, max_length=50, description="Ô chứa / Hộc hàng (Ô 01...)")
    max_capacity: Optional[float] = Field(1000.0, ge=0, description="Sức chứa tối đa")
    status: Optional[str] = Field("Đang sử dụng", max_length=50)
    is_active: Optional[bool] = Field(True)
    note: Optional[str] = Field(None)


class WarehouseLocationUpdate(BaseModel):
    location_code: Optional[str] = Field(None, min_length=2, max_length=50)
    location_name: Optional[str] = Field(None, max_length=255)
    zone: Optional[str] = Field(None, max_length=50)
    aisle: Optional[str] = Field(None, max_length=50)
    rack: Optional[str] = Field(None, max_length=50)
    bin: Optional[str] = Field(None, max_length=50)
    max_capacity: Optional[float] = Field(None, ge=0)
    status: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    note: Optional[str] = None


class WarehouseLocationResponse(BaseModel):
    id: int
    warehouse_id: int
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None
    location_code: str
    location_name: Optional[str] = None
    zone: Optional[str] = None
    aisle: Optional[str] = None
    rack: Optional[str] = None
    bin: Optional[str] = None
    max_capacity: Optional[float] = 1000.0
    is_active: bool = True
    status: Optional[str] = "Đang sử dụng"
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    items_count: int = 0
    total_quantity: int = 0

    model_config = ConfigDict(from_attributes=True)


# --- LOCATION STOCKS & PRODUCT ASSIGNMENT ---
class AssignProductToLocationRequest(BaseModel):
    location_id: int
    product_id: int
    quantity: int = Field(..., ge=0, description="Số lượng gán tại vị trí (0 để xóa sản phẩm khỏi vị trí)")


class TransferLocationProductRequest(BaseModel):
    from_location_id: int
    to_location_id: int
    product_id: int
    quantity: int = Field(..., gt=0, description="Số lượng chuyển vị trí")


class LocationProductStockItem(BaseModel):
    id: int
    location_id: int
    location_code: str
    location_name: Optional[str] = None
    zone: Optional[str] = None
    aisle: Optional[str] = None
    rack: Optional[str] = None
    bin: Optional[str] = None
    product_id: int
    product_code: str
    product_name: str
    base_unit: str = "Cái"
    quantity: int
    warehouse_total_stock: int = 0


# --- SOẠN ĐƠN / PICKING LOCATIONS ---
class ProductPickingLocation(BaseModel):
    location_id: int
    location_code: str
    location_name: str
    zone: Optional[str] = None
    aisle: Optional[str] = None
    rack: Optional[str] = None
    bin: Optional[str] = None
    available_quantity: int


class OrderPickingItemResponse(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    ordered_quantity: int
    unit_name: str
    warehouse_id: Optional[int] = None
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None
    locations: List[ProductPickingLocation] = []


# --- MASTER DATA: KHU VỰC (ZONE) & KỆ / DÃY (RACK / AISLE) ---
class WarehouseZoneCreate(BaseModel):
    zone_code: str = Field(..., min_length=1, max_length=50, description="Mã khu vực (vd: KHU-A)")
    zone_name: str = Field(..., min_length=1, max_length=100, description="Tên khu vực (vd: Khu A - Thời trang)")
    description: Optional[str] = Field(None, description="Ghi chú / loại hàng hóa")
    is_active: Optional[bool] = Field(True)


class WarehouseZoneUpdate(BaseModel):
    zone_code: Optional[str] = Field(None, min_length=1, max_length=50)
    zone_name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class WarehouseZoneResponse(BaseModel):
    id: int
    warehouse_id: int
    zone_code: str
    zone_name: str
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    locations_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class WarehouseRackCreate(BaseModel):
    rack_code: str = Field(..., min_length=1, max_length=50, description="Mã kệ / dãy (vd: DAY-01, TANG-1)")
    rack_name: str = Field(..., min_length=1, max_length=100, description="Tên kệ / dãy (vd: Dãy 1, Tầng 1)")
    rack_type: Optional[str] = Field("rack", max_length=50, description="Loại: aisle (dãy kệ) | rack (tầng/kệ) | bin (ô chứa)")
    zone_id: Optional[int] = Field(None, description="ID khu vực trực thuộc nếu có")
    max_capacity: Optional[float] = Field(1000.0, ge=0)
    is_active: Optional[bool] = Field(True)


class WarehouseRackUpdate(BaseModel):
    rack_code: Optional[str] = Field(None, min_length=1, max_length=50)
    rack_name: Optional[str] = Field(None, min_length=1, max_length=100)
    rack_type: Optional[str] = Field(None, max_length=50)
    zone_id: Optional[int] = None
    max_capacity: Optional[float] = Field(None, ge=0)
    is_active: Optional[bool] = None


class WarehouseRackResponse(BaseModel):
    id: int
    warehouse_id: int
    zone_id: Optional[int] = None
    zone_name: Optional[str] = None
    rack_code: str
    rack_name: str
    rack_type: str = "rack"
    max_capacity: Optional[float] = 1000.0
    is_active: bool = True
    created_at: Optional[datetime] = None
    locations_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class MasterDataOptionItem(BaseModel):
    id: Optional[int] = None
    code: str
    name: str
    type: Optional[str] = None
    zone_name: Optional[str] = None


class WarehouseMasterDataResponse(BaseModel):
    zones: List[MasterDataOptionItem] = []
    aisles: List[MasterDataOptionItem] = []
    racks: List[MasterDataOptionItem] = []
    bins: List[MasterDataOptionItem] = []

