# backend/app/schemas/delivery.py
from __future__ import annotations
import re
import json
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class InTransitSummaryResponse(BaseModel):
    total_in_transit_value: float = Field(0.0, description="Tổng giá trị tiền hàng đang nằm trên đường")
    active_trips_count: int = Field(0, description="Số chuyến giao hàng đang chạy")
    remaining_stops_count: int = Field(0, description="Tổng số điểm giao chưa hoàn tất")
    overdue_trips_count: int = Field(0, description="Số chuyến đã quá thời gian dự kiến")


class DeliveryStopResponse(BaseModel):
    id: int
    trip_id: int
    stop_order: int
    order_id: Optional[int] = None
    order_code: str
    dealer_id: Optional[int] = None
    dealer_name: str
    delivery_point: str
    receiver_name: Optional[str] = None
    receiver_phone: Optional[str] = None
    amount: float = 0.0
    status: str  # PENDING | IN_TRANSIT | DELIVERED | FAILED
    delivery_status: Optional[str] = None  # 'Pending' | 'Delivered' | 'Failed'
    estimated_arrival: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    actual_delivery_time: Optional[datetime] = None
    failed_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    pod_image_url: Optional[str] = None
    return_status: Optional[str] = None
    items_detail: Optional[List[dict]] = None
    note: Optional[str] = None

    @field_validator('items_detail', mode='before')
    @classmethod
    def normalize_items_detail(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                return None
        if isinstance(v, dict):
            v = v.get("items", [])
        if isinstance(v, list):
            return [it for it in v if isinstance(it, dict)]
        return None

    model_config = {"from_attributes": True}


class DeliveryTripResponse(BaseModel):
    id: int
    trip_code: str
    driver_name: str
    driver_phone: Optional[str] = None
    vehicle_plate: str
    route_name: Optional[str] = None
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
    status: str  # IN_TRANSIT | DELIVERING | COMPLETED | CANCELLED
    dispatched_at: Optional[datetime] = None
    estimated_delivery_time: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    note: Optional[str] = None
    is_overdue: bool = False
    overdue_minutes: int = 0
    total_stops: int = 0
    delivered_stops: int = 0
    remaining_stops: int = 0
    total_trip_value: float = 0.0
    remaining_value: float = 0.0
    stops: List[DeliveryStopResponse] = []

    model_config = {"from_attributes": True}


class DeliveryStopConfirmRequest(BaseModel):
    receiver_name: str = Field(..., min_length=1, description="Họ tên người nhận hàng (bắt buộc)")
    actual_delivery_time: Optional[datetime] = Field(None, description="Thời điểm giao thực tế (mặc định hiện tại)")
    pod_image_url: str = Field(..., min_length=1, description="Đường dẫn ảnh biên bản giao nhận có chữ ký (POD)")
    items_detail: Optional[List[dict]] = Field(None, description="Chi tiết số lượng thực giao từng dòng hàng")
    note: Optional[str] = None


class DeliveryStopFailureRequest(BaseModel):
    failure_reason: str = Field(..., min_length=1, description="Lý do giao không thành công")
    note: Optional[str] = None


class DeliveryStopStatusUpdate(BaseModel):
    status: str = Field(..., description="Trạng thái mới: DELIVERED | FAILED | IN_TRANSIT | PENDING")
    receiver_name: Optional[str] = None
    actual_delivery_time: Optional[datetime] = None
    pod_image_url: Optional[str] = None
    failure_reason: Optional[str] = None
    failed_reason: Optional[str] = None
    items_detail: Optional[List[dict]] = None
    note: Optional[str] = None


class DeliveryTripStatusUpdate(BaseModel):
    status: str = Field(..., description="Trạng thái mới: IN_TRANSIT | DELIVERING | COMPLETED | CANCELLED")
    note: Optional[str] = None


class DeliveryStopCreate(BaseModel):
    order_id: Optional[int] = None
    order_code: str
    dealer_id: Optional[int] = None
    dealer_name: str
    delivery_point: str
    receiver_name: Optional[str] = None
    receiver_phone: Optional[str] = None
    amount: float = 0.0
    estimated_arrival: Optional[datetime] = None
    note: Optional[str] = None


class DeliveryTripCreate(BaseModel):
    trip_code: Optional[str] = None
    driver_name: str
    driver_phone: Optional[str] = None
    vehicle_plate: str
    route_name: Optional[str] = None
    warehouse_id: Optional[str] = "1"
    warehouse_name: Optional[str] = "Kho Tổng Hà Nội"
    estimated_delivery_time: Optional[datetime] = None
    note: Optional[str] = None
    stops: List[DeliveryStopCreate] = []

    @field_validator('driver_phone')
    @classmethod
    def validate_phone(cls, v):
        if v:
            v = v.strip()
            if not v:
                return None
            if not re.match(r'^(03|05|07|08|09)\d{8}$', v):
                raise ValueError('Số điện thoại không đúng định dạng di động Việt Nam (10 số, bắt đầu bằng 03, 05, 07, 08, 09)')
        return v

