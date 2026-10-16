# backend/app/schemas/delivery.py
from __future__ import annotations
import re
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
    estimated_arrival: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    failed_reason: Optional[str] = None
    note: Optional[str] = None

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
    dispatched_at: datetime
    estimated_delivery_time: datetime
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


class DeliveryStopStatusUpdate(BaseModel):
    status: str = Field(..., description="Trạng thái mới: DELIVERED | FAILED | IN_TRANSIT | PENDING")
    failed_reason: Optional[str] = None
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
    estimated_delivery_time: datetime
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

