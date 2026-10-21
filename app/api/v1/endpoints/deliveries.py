# backend/app/api/v1/endpoints/deliveries.py
from __future__ import annotations
import os
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.core.database import get_db
from app.api.deps import get_current_user, require_roles
from app.schemas.auth import UserResponse
from app.models.entities import DeliveryTripEntity, DeliveryStopEntity, OrderEntity
from app.schemas.delivery import (
    InTransitSummaryResponse,
    DeliveryTripResponse,
    DeliveryStopResponse,
    DeliveryStopStatusUpdate,
    DeliveryTripStatusUpdate,
    DeliveryTripCreate,
    DeliveryStopConfirmRequest,
    DeliveryStopFailureRequest,
)

router = APIRouter()

DELIVERY_VIEW_ROLES = ["warehouse_mgr", "warehouse_manager", "admin", "warehouse", "sales_manager", "driver"]
DELIVERY_MANAGE_ROLES = ["warehouse_mgr", "warehouse_manager", "admin", "warehouse"]
DELIVERY_STOP_UPDATE_ROLES = ["warehouse_mgr", "warehouse_manager", "admin", "warehouse", "driver"]

def get_utc_now():
    return datetime.now(timezone.utc)

def _seed_sample_trips_if_empty(db: Session):
    """Khởi tạo một số chuyến giao hàng mẫu nếu bảng delivery_trips đang trống."""
    trips_without_time = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.estimated_delivery_time.is_(None)).all()
    if trips_without_time:
        for t in trips_without_time:
            t.estimated_delivery_time = (t.dispatched_at or get_utc_now()) + timedelta(hours=3)
        db.commit()

    count = db.query(func.count(DeliveryTripEntity.id)).scalar()
    if count and count > 0:
        return

    now = get_utc_now()
    # Chuyến 1: Đang chạy trên đường, đúng hạn
    trip1 = DeliveryTripEntity(
        trip_code="CGH-20261015-01",
        driver_name="Nguyễn Văn Tuấn",
        driver_phone="0981234567",
        vehicle_plate="29C-889.12",
        route_name="Tuyến Cầu Giấy - Ba Đình - Đống Đa",
        warehouse_id="1",
        warehouse_name="Kho Tổng Hà Nội",
        status="IN_TRANSIT",
        dispatched_at=now - timedelta(hours=2),
        estimated_delivery_time=now + timedelta(hours=2),
        note="Giao trong ngày, chú ý hàng dễ vỡ",
        created_by="kho",
    )
    db.add(trip1)
    db.flush()

    stop1_1 = DeliveryStopEntity(
        trip_id=trip1.id,
        stop_order=1,
        order_code="DH-202610-001",
        dealer_name="Đại Lý Phân Phối Cầu Giấy",
        delivery_point="Số 120 Cầu Giấy, P. Quan Hoa, Cầu Giấy, Hà Nội",
        receiver_name="Anh Trần Văn Hùng",
        receiver_phone="0912345678",
        amount=18500000.0,
        status="DELIVERED",
        estimated_arrival=now - timedelta(hours=1),
        delivered_at=now - timedelta(minutes=45),
        note="Đã ký nhận biên bản bàn giao",
    )
    stop1_2 = DeliveryStopEntity(
        trip_id=trip1.id,
        stop_order=2,
        order_code="DH-202610-002",
        dealer_name="Đại Lý Kim Mã - Ba Đình",
        delivery_point="Số 250 Kim Mã, Ba Đình, Hà Nội",
        receiver_name="Chị Hoàng Mai",
        receiver_phone="0934567890",
        amount=24000000.0,
        status="IN_TRANSIT",
        estimated_arrival=now + timedelta(minutes=30),
        note="Đang trên đường đến điểm giao",
    )
    stop1_3 = DeliveryStopEntity(
        trip_id=trip1.id,
        stop_order=3,
        order_code="DH-202610-003",
        dealer_name="Nhà Phân Phối Thái Hà",
        delivery_point="Số 88 Thái Hà, Đống Đa, Hà Nội",
        receiver_name="Anh Lê Minh",
        receiver_phone="0987654321",
        amount=31500000.0,
        status="PENDING",
        estimated_arrival=now + timedelta(hours=1, minutes=45),
        note="Gọi trước khi đến 15 phút",
    )
    db.add_all([stop1_1, stop1_2, stop1_3])

    # Chuyến 2: Đang chạy nhưng BỊ TRỄ HẠN (overdue)
    trip2 = DeliveryTripEntity(
        trip_code="CGH-20261015-02",
        driver_name="Lê Hoàng Nam",
        driver_phone="0978999888",
        vehicle_plate="29H-654.32",
        route_name="Tuyến Thanh Xuân - Hà Đông - Nam Từ Liêm",
        warehouse_id="1",
        warehouse_name="Kho Tổng Hà Nội",
        status="IN_TRANSIT",
        dispatched_at=now - timedelta(hours=5),
        estimated_delivery_time=now - timedelta(minutes=45),  # Quá hạn 45 phút
        note="Kẹt xe đường Nguyễn Trãi giờ cao điểm",
        created_by="kho",
    )
    db.add(trip2)
    db.flush()

    stop2_1 = DeliveryStopEntity(
        trip_id=trip2.id,
        stop_order=1,
        order_code="DH-202610-004",
        dealer_name="Đại Lý Nguyễn Trãi",
        delivery_point="Số 332 Nguyễn Trãi, Thanh Xuân, Hà Nội",
        receiver_name="Anh Đức",
        receiver_phone="0901234567",
        amount=15000000.0,
        status="DELIVERED",
        estimated_arrival=now - timedelta(hours=3),
        delivered_at=now - timedelta(hours=2, minutes=30),
        note="Giao đủ hàng",
    )
    stop2_2 = DeliveryStopEntity(
        trip_id=trip2.id,
        stop_order=2,
        order_code="DH-202610-005",
        dealer_name="Đại Lý Quang Trung Hà Đông",
        delivery_point="Số 108 Quang Trung, Hà Đông, Hà Nội",
        receiver_name="Chị Bích",
        receiver_phone="0945678123",
        amount=28000000.0,
        status="IN_TRANSIT",
        estimated_arrival=now - timedelta(minutes=45),
        note="Trễ do tắc đường, đang gọi khách",
    )
    stop2_3 = DeliveryStopEntity(
        trip_id=trip2.id,
        stop_order=3,
        order_code="DH-202610-006",
        dealer_name="Đại Lý Tố Hữu - Nam Từ Liêm",
        delivery_point="Tòa nhà HH2 Tố Hữu, Nam Từ Liêm, Hà Nội",
        receiver_name="Anh Thắng",
        receiver_phone="0967891234",
        amount=22500000.0,
        status="PENDING",
        estimated_arrival=now,
        note="Giao trước 18h",
    )
    db.add_all([stop2_1, stop2_2, stop2_3])

    # Chuyến 3: Chuyến giao thứ 3 đang giao khu vực Long Biên - Gia Lâm
    trip3 = DeliveryTripEntity(
        trip_code="CGH-20261015-03",
        driver_name="Phạm Quốc Huy",
        driver_phone="0911223344",
        vehicle_plate="29B-512.78",
        route_name="Tuyến Long Biên - Gia Lâm",
        warehouse_id="1",
        warehouse_name="Kho Tổng Hà Nội",
        status="IN_TRANSIT",
        dispatched_at=now - timedelta(hours=1),
        estimated_delivery_time=now + timedelta(hours=3),
        note="Đường thoáng, dự kiến đúng giờ",
        created_by="warehouse_mgr",
    )
    db.add(trip3)
    db.flush()

    stop3_1 = DeliveryStopEntity(
        trip_id=trip3.id,
        stop_order=1,
        order_code="DH-202610-007",
        dealer_name="Đại Lý Nguyễn Văn Cừ",
        delivery_point="Số 450 Nguyễn Văn Cừ, Long Biên, Hà Nội",
        receiver_name="Anh Tuấn Anh",
        receiver_phone="0922334455",
        amount=42000000.0,
        status="PENDING",
        estimated_arrival=now + timedelta(hours=1),
        note="Hàng cồng kềnh, cần 2 người bốc xếp",
    )
    stop3_2 = DeliveryStopEntity(
        trip_id=trip3.id,
        stop_order=2,
        order_code="DH-202610-008",
        dealer_name="Đại Lý Gia Lâm",
        delivery_point="Khu Đô Thị Đặng Xá, Gia Lâm, Hà Nội",
        receiver_name="Chị Thu",
        receiver_phone="0933445566",
        amount=19500000.0,
        status="PENDING",
        estimated_arrival=now + timedelta(hours=2, minutes=30),
        note="Gọi trước khi đến",
    )
    db.add_all([stop3_1, stop3_2])

    # Chuyến 4: Đã hoàn tất hôm qua (để kiểm tra bộ lọc và tính chính xác)
    trip4 = DeliveryTripEntity(
        trip_code="CGH-20261014-01",
        driver_name="Trần Đình Trọng",
        driver_phone="0909090909",
        vehicle_plate="29C-111.22",
        route_name="Tuyến Hoàng Mai - Hai Bà Trưng",
        warehouse_id="1",
        warehouse_name="Kho Tổng Hà Nội",
        status="COMPLETED",
        dispatched_at=now - timedelta(days=1, hours=4),
        estimated_delivery_time=now - timedelta(days=1, hours=1),
        completed_at=now - timedelta(days=1, hours=1),
        note="Đã giao toàn bộ thành công hôm qua",
        created_by="kho",
    )
    db.add(trip4)
    db.flush()

    stop4_1 = DeliveryStopEntity(
        trip_id=trip4.id,
        stop_order=1,
        order_code="DH-202610-000",
        dealer_name="Đại Lý Minh Khai",
        delivery_point="Số 100 Minh Khai, Hai Bà Trưng, Hà Nội",
        receiver_name="Anh Khải",
        receiver_phone="0918181818",
        amount=35000000.0,
        status="DELIVERED",
        estimated_arrival=now - timedelta(days=1, hours=2),
        delivered_at=now - timedelta(days=1, hours=1),
        note="Hoàn tất",
    )
    db.add(stop4_1)
    db.commit()


def _format_stop_response(stop: DeliveryStopEntity, db: Optional[Session] = None) -> DeliveryStopResponse:
    items_list = None
    if stop.items_detail:
        try:
            items_list = json.loads(stop.items_detail)
        except Exception:
            items_list = None

    if items_list is None and db and stop.order_code:
        try:
            order = db.query(OrderEntity).filter(OrderEntity.order_code == stop.order_code).first()
            if order and order.items_json:
                items_list = json.loads(order.items_json)
        except Exception:
            items_list = None

    if isinstance(items_list, dict):
        items_list = items_list.get("items", [])
    if not isinstance(items_list, list):
        items_list = None
    elif items_list:
        items_list = [it for it in items_list if isinstance(it, dict)]

    actual_time = stop.actual_delivery_time or stop.delivered_at
    fail_rs = stop.failure_reason or stop.failed_reason
    deliv_status = stop.delivery_status or ("Delivered" if stop.status == "DELIVERED" else ("Failed" if stop.status == "FAILED" else stop.status))

    return DeliveryStopResponse(
        id=stop.id,
        trip_id=stop.trip_id,
        stop_order=stop.stop_order,
        order_id=stop.order_id,
        order_code=stop.order_code,
        dealer_id=stop.dealer_id,
        dealer_name=stop.dealer_name,
        delivery_point=stop.delivery_point,
        receiver_name=stop.receiver_name,
        receiver_phone=stop.receiver_phone,
        amount=stop.amount,
        status=stop.status,
        delivery_status=deliv_status,
        estimated_arrival=stop.estimated_arrival,
        delivered_at=actual_time,
        actual_delivery_time=actual_time,
        failed_reason=fail_rs,
        failure_reason=fail_rs,
        pod_image_url=stop.pod_image_url,
        return_status=stop.return_status,
        items_detail=items_list,
        note=stop.note,
    )


def _format_trip_response(trip: DeliveryTripEntity, now: datetime, db: Optional[Session] = None) -> DeliveryTripResponse:
    stops = trip.stops or []
    total_stops = len(stops)
    delivered_stops = sum(1 for s in stops if s.status == "DELIVERED")
    remaining_stops = sum(1 for s in stops if s.status != "DELIVERED")
    total_trip_value = sum(s.amount for s in stops)
    
    # Quy tắc nghiệp vụ 2: Điểm nào đã giao thành công thì trừ tiền điểm đó ra khỏi giá trị "hàng nằm trên đường"
    remaining_value = sum(s.amount for s in stops if s.status != "DELIVERED")

    # Kiểm tra quá hạn: chuyến chưa hoàn thành và thời gian hiện tại > thời gian dự kiến
    is_active = trip.status in ["IN_TRANSIT", "DELIVERING"]
    est_time = trip.estimated_delivery_time
    is_overdue = False
    overdue_minutes = 0
    if est_time is not None:
        if est_time.tzinfo is None:
            est_time = est_time.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        is_overdue = is_active and now > est_time
        overdue_minutes = max(0, int((now - est_time).total_seconds() / 60)) if is_overdue else 0

    dispatched_at = trip.dispatched_at or now
    estimated_delivery_time = trip.estimated_delivery_time or (dispatched_at + timedelta(hours=3))

    return DeliveryTripResponse(
        id=trip.id,
        trip_code=trip.trip_code,
        driver_name=trip.driver_name,
        driver_phone=trip.driver_phone,
        vehicle_plate=trip.vehicle_plate or "",
        route_name=trip.route_name,
        warehouse_id=trip.warehouse_id,
        warehouse_name=trip.warehouse_name,
        status=trip.status,
        dispatched_at=dispatched_at,
        estimated_delivery_time=estimated_delivery_time,
        completed_at=trip.completed_at,
        note=trip.note,
        is_overdue=is_overdue,
        overdue_minutes=overdue_minutes,
        total_stops=total_stops,
        delivered_stops=delivered_stops,
        remaining_stops=remaining_stops,
        total_trip_value=total_trip_value,
        remaining_value=remaining_value,
        stops=[_format_stop_response(s, db) for s in stops],
    )


@router.get("/in-transit-summary", response_model=InTransitSummaryResponse)
def get_in_transit_summary(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_VIEW_ROLES)),
):
    """
    SCRUM-84: Chỉ số tổng quan tiền hàng nằm trên đường và các chuyến đang giao.
    - total_in_transit_value: Tổng tiền các điểm giao chưa DELIVERED của các chuyến đang chạy.
    - active_trips_count: Số chuyến có status IN_TRANSIT hoặc DELIVERING.
    - remaining_stops_count: Số điểm giao chưa DELIVERED trên các chuyến đang chạy.
    - overdue_trips_count: Số chuyến quá thời gian dự kiến hoàn thành mà chưa xong.
    """
    _seed_sample_trips_if_empty(db)
    now = get_utc_now()

    # 1. Số chuyến đang chạy
    active_trips_count = db.query(func.count(DeliveryTripEntity.id)).filter(
        DeliveryTripEntity.status.in_(["IN_TRANSIT", "DELIVERING"])
    ).scalar() or 0

    # 2. Tổng giá trị tiền hàng còn nằm trên đường (các điểm chưa DELIVERED trên các chuyến đang chạy)
    total_in_transit_value = db.query(
        func.coalesce(func.sum(DeliveryStopEntity.amount), 0.0)
    ).join(
        DeliveryTripEntity, DeliveryStopEntity.trip_id == DeliveryTripEntity.id
    ).filter(
        DeliveryTripEntity.status.in_(["IN_TRANSIT", "DELIVERING"]),
        DeliveryStopEntity.status != "DELIVERED"
    ).scalar() or 0.0

    # 3. Số điểm giao còn lại
    remaining_stops_count = db.query(
        func.count(DeliveryStopEntity.id)
    ).join(
        DeliveryTripEntity, DeliveryStopEntity.trip_id == DeliveryTripEntity.id
    ).filter(
        DeliveryTripEntity.status.in_(["IN_TRANSIT", "DELIVERING"]),
        DeliveryStopEntity.status != "DELIVERED"
    ).scalar() or 0

    # 4. Số chuyến trễ hạn (overdue)
    overdue_trips_count = db.query(
        func.count(DeliveryTripEntity.id)
    ).filter(
        DeliveryTripEntity.status.in_(["IN_TRANSIT", "DELIVERING"]),
        DeliveryTripEntity.estimated_delivery_time < now
    ).scalar() or 0

    return InTransitSummaryResponse(
        total_in_transit_value=float(total_in_transit_value),
        active_trips_count=int(active_trips_count),
        remaining_stops_count=int(remaining_stops_count),
        overdue_trips_count=int(overdue_trips_count),
    )


@router.get("/in-transit", response_model=List[DeliveryTripResponse])
def get_in_transit_trips(
    status: Optional[str] = Query("IN_TRANSIT", description="Lọc trạng thái: IN_TRANSIT | ALL | COMPLETED"),
    search: Optional[str] = Query(None, description="Tìm theo mã chuyến, tài xế, biển số xe, tuyến đường"),
    is_overdue: Optional[bool] = Query(None, description="Lọc riêng các chuyến quá hạn"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_VIEW_ROLES)),
):
    """
    SCRUM-84: Lấy danh sách các chuyến giao hàng đang chạy kèm thông tin điểm giao và tính toán động.
    """
    _seed_sample_trips_if_empty(db)
    now = get_utc_now()

    query = db.query(DeliveryTripEntity)

    # Lọc trạng thái
    if status and status.upper() != "ALL":
        if status.upper() in ["IN_TRANSIT", "DELIVERING"]:
            query = query.filter(DeliveryTripEntity.status.in_(["IN_TRANSIT", "DELIVERING"]))
        else:
            query = query.filter(DeliveryTripEntity.status == status.upper())

    # Tìm kiếm theo từ khóa
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                DeliveryTripEntity.trip_code.ilike(term),
                DeliveryTripEntity.driver_name.ilike(term),
                DeliveryTripEntity.vehicle_plate.ilike(term),
                DeliveryTripEntity.route_name.ilike(term),
            )
        )

    # Lọc quá hạn nếu có
    if is_overdue is True:
        query = query.filter(
            DeliveryTripEntity.status.in_(["IN_TRANSIT", "DELIVERING"]),
            DeliveryTripEntity.estimated_delivery_time < now
        )
    elif is_overdue is False:
        query = query.filter(
            or_(
                DeliveryTripEntity.status == "COMPLETED",
                DeliveryTripEntity.estimated_delivery_time >= now
            )
        )

    trips = query.order_by(DeliveryTripEntity.dispatched_at.desc()).all()

    return [_format_trip_response(t, now, db) for t in trips]


@router.get("/orders-ready")
def get_orders_ready_for_delivery(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_MANAGE_ROLES)),
):
    """Lấy danh sách đơn hàng đã xuất kho hoặc sẵn sàng giao để tạo chuyến giao mới."""
    orders = db.query(OrderEntity).filter(
        OrderEntity.status.in_(["EXPORTED", "APPROVED", "CONFIRMED", "PICKING"])
    ).order_by(OrderEntity.created_at.desc()).limit(50).all()

    return [
        {
            "id": o.id,
            "order_code": o.order_code,
            "dealer_id": o.dealer_id,
            "dealer_name": o.dealer_name or "Đại lý",
            "delivery_point": "Điểm nhận hàng đại lý",
            "amount": float(o.total_amount or 0.0),
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in orders
    ]


@router.get("/trips/{trip_id}", response_model=DeliveryTripResponse)
def get_trip_detail(
    trip_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_VIEW_ROLES)),
):
    """Lấy chi tiết 1 chuyến giao hàng theo ID hoặc Mã chuyến (trip_code)."""
    now = get_utc_now()
    if trip_id.isdigit():
        trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == int(trip_id)).first()
    else:
        trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.trip_code == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Không tìm thấy chuyến giao hàng này.")
    return _format_trip_response(trip, now, db)


@router.post("/trips", response_model=DeliveryTripResponse, status_code=status.HTTP_201_CREATED)
def create_delivery_trip(
    data: DeliveryTripCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_MANAGE_ROLES)),
):
    """Tạo một chuyến giao hàng mới và gắn các điểm giao / đơn hàng."""
    now = get_utc_now()
    trip_code = data.trip_code
    if not trip_code:
        today_str = now.strftime("%Y%m%d")
        existing_count = db.query(func.count(DeliveryTripEntity.id)).filter(
            DeliveryTripEntity.trip_code.like(f"CGH-{today_str}-%")
        ).scalar() or 0
        trip_code = f"CGH-{today_str}-{existing_count + 1:02d}"

    est_delivery = data.estimated_delivery_time or (now + timedelta(hours=3))

    trip = DeliveryTripEntity(
        trip_code=trip_code,
        driver_name=data.driver_name,
        driver_phone=data.driver_phone,
        vehicle_plate=data.vehicle_plate,
        route_name=data.route_name,
        warehouse_id=data.warehouse_id or "1",
        warehouse_name=data.warehouse_name or "Kho Tổng Hà Nội",
        status="IN_TRANSIT",
        dispatched_at=now,
        estimated_delivery_time=est_delivery,
        note=data.note,
        created_by=current_user.username,
    )
    db.add(trip)
    db.flush()

    for idx, stop_data in enumerate(data.stops, start=1):
        stop = DeliveryStopEntity(
            trip_id=trip.id,
            stop_order=idx,
            order_id=stop_data.order_id,
            order_code=stop_data.order_code,
            dealer_id=stop_data.dealer_id,
            dealer_name=stop_data.dealer_name,
            delivery_point=stop_data.delivery_point,
            receiver_name=stop_data.receiver_name,
            receiver_phone=stop_data.receiver_phone,
            amount=stop_data.amount,
            status="IN_TRANSIT",
            delivery_status="Pending",
            estimated_arrival=stop_data.estimated_arrival,
            note=stop_data.note,
        )
        db.add(stop)

        # Cập nhật trạng thái đơn hàng sang SHIPPED nếu có
        if stop_data.order_code:
            order = db.query(OrderEntity).filter(OrderEntity.order_code == stop_data.order_code).first()
            if order and order.status in ["EXPORTED", "CONFIRMED", "APPROVED", "PICKING"]:
                order.status = "SHIPPED"

    db.commit()
    db.refresh(trip)
    return _format_trip_response(trip, now, db)


@router.patch("/trips/{trip_id}/status", response_model=DeliveryTripResponse)
def update_trip_status(
    trip_id: int,
    data: DeliveryTripStatusUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_MANAGE_ROLES)),
):
    """Cập nhật trạng thái chuyến giao hàng (IN_TRANSIT, DELIVERING, COMPLETED, CANCELLED)."""
    now = get_utc_now()
    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Không tìm thấy chuyến giao hàng này.")

    new_status = data.status.upper().strip()
    if new_status not in ["IN_TRANSIT", "DELIVERING", "COMPLETED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ. Cho phép: IN_TRANSIT, DELIVERING, COMPLETED, CANCELLED.")

    trip.status = new_status
    if data.note:
        trip.note = data.note
    if new_status == "COMPLETED":
        if not trip.completed_at:
            trip.completed_at = now
        for stop in trip.stops:
            if stop.status != "DELIVERED":
                stop.status = "DELIVERED"
                stop.delivery_status = "Delivered"
                if not stop.delivered_at:
                    stop.delivered_at = now
                if not stop.actual_delivery_time:
                    stop.actual_delivery_time = now
    elif new_status != "COMPLETED":
        trip.completed_at = None

    db.commit()
    db.refresh(trip)
    return _format_trip_response(trip, now, db)


@router.post("/upload-pod")
async def upload_pod_image(
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(require_roles(DELIVERY_STOP_UPDATE_ROLES)),
):
    """
    SCRUM-85: Upload tệp ảnh chứng từ / biên bản giao nhận có chữ ký (Proof of Delivery - POD).
    Chấp nhận các định dạng ảnh: .jpg, .jpeg, .png, .webp.
    Lưu trữ vào thư mục tĩnh /uploads/pod/ và trả về URL hợp lệ để lưu vào CSDL.
    """
    allowed_exts = {".png", ".jpg", ".jpeg", ".webp"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Định dạng tệp không hợp lệ. Hệ thống chỉ chấp nhận ảnh định dạng JPG, JPEG, PNG, WEBP."
        )

    # Thư mục uploads/pod (trong backend/uploads/pod khớp với app.mount("/uploads", ...))
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    pod_dir = os.path.join(backend_root, "uploads", "pod")
    os.makedirs(pod_dir, exist_ok=True)

    filename = f"pod_{uuid.uuid4().hex[:12]}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    filepath = os.path.join(pod_dir, filename)

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dung lượng tệp vượt quá giới hạn tối đa 10MB."
        )

    with open(filepath, "wb") as f:
        f.write(contents)

    return {
        "url": f"/uploads/pod/{filename}",
        "filename": filename,
        "status": "success",
        "message": "Tải lên ảnh chứng từ giao hàng thành công."
    }


@router.post("/stops/{stop_id}/confirm-delivery", response_model=DeliveryStopResponse)
def confirm_delivery_success(
    stop_id: int,
    data: DeliveryStopConfirmRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_STOP_UPDATE_ROLES)),
):
    """
    SCRUM-85: Xác nhận giao hàng thành công kèm ảnh chứng từ (POD).
    - Cập nhật điểm giao: status = DELIVERED, delivery_status = Delivered.
    - Lưu họ tên người nhận, thời gian thực tế, đường dẫn ảnh chứng từ pod_image_url.
    - Cập nhật Order liên quan sang DELIVERED để chính thức kích hoạt điều kiện mở khóa cho Kế toán phát hành hóa đơn ở Sprint 7.
    - Tự động hoàn tất chuyến giao nếu tất cả điểm giao đã kết thúc.
    """
    now = get_utc_now()
    stop = db.query(DeliveryStopEntity).filter(DeliveryStopEntity.id == stop_id).first()
    if not stop:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm giao hàng này.")

    actual_time = data.actual_delivery_time or now
    if actual_time.tzinfo is None:
        actual_time = actual_time.replace(tzinfo=timezone.utc)

    # 1. Cập nhật thông tin điểm giao
    stop.status = "DELIVERED"
    stop.delivery_status = "Delivered"
    stop.receiver_name = data.receiver_name.strip()
    stop.delivered_at = actual_time
    stop.actual_delivery_time = actual_time
    stop.pod_image_url = data.pod_image_url.strip() if data.pod_image_url else None
    stop.failed_reason = None
    stop.failure_reason = None
    stop.return_status = None
    if data.note:
        stop.note = data.note.strip()
    if data.items_detail is not None:
        stop.items_detail = json.dumps(data.items_detail, ensure_ascii=False)

    # 2. Cập nhật OrderEntity liên quan sang DELIVERED (Sprint 7 invoice trigger)
    if stop.order_code:
        order = db.query(OrderEntity).filter(OrderEntity.order_code == stop.order_code).first()
        if order:
            order.status = "DELIVERED"

    db.flush()

    # 3. Kiểm tra xem toàn bộ các điểm giao trong chuyến đã hoàn thành chưa
    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == stop.trip_id).first()
    if trip and trip.status in ["IN_TRANSIT", "DELIVERING"]:
        remaining = db.query(func.count(DeliveryStopEntity.id)).filter(
            DeliveryStopEntity.trip_id == trip.id,
            DeliveryStopEntity.status.in_(["PENDING", "IN_TRANSIT"])
        ).scalar() or 0
        if remaining == 0:
            trip.status = "COMPLETED"
            trip.completed_at = now

    db.commit()
    db.refresh(stop)
    return _format_stop_response(stop, db)


@router.post("/stops/{stop_id}/record-failure", response_model=DeliveryStopResponse)
def record_delivery_failure(
    stop_id: int,
    data: DeliveryStopFailureRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_STOP_UPDATE_ROLES)),
):
    """
    SCRUM-85: Ghi nhận giao hàng không thành công (giao thất bại).
    - Cập nhật điểm giao sang FAILED (delivery_status = Failed).
    - Lưu lý do giao thất bại (failure_reason).
    - Ghi nhận hàng chuyển sang trạng thái chờ hoàn kho / quay về kho (return_status = 'CHỜ_HOÀN_KHO').
    - Tuyệt đối không làm giảm hoặc âm tồn kho (hàng thuộc trách nhiệm tài xế trên chuyến quay về kho, chuẩn bị cho quy trình xử lý đổi trả ở Sprint 8).
    """
    now = get_utc_now()
    stop = db.query(DeliveryStopEntity).filter(DeliveryStopEntity.id == stop_id).first()
    if not stop:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm giao hàng này.")

    reason = data.failure_reason.strip()
    stop.status = "FAILED"
    stop.delivery_status = "Failed"
    stop.failed_reason = reason
    stop.failure_reason = reason
    stop.return_status = "CHỜ_HOÀN_KHO"
    if data.note:
        stop.note = data.note.strip()

    db.flush()

    # Kiểm tra xem toàn bộ các điểm giao trong chuyến đã hoàn thành chưa
    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == stop.trip_id).first()
    if trip and trip.status in ["IN_TRANSIT", "DELIVERING"]:
        remaining = db.query(func.count(DeliveryStopEntity.id)).filter(
            DeliveryStopEntity.trip_id == trip.id,
            DeliveryStopEntity.status.in_(["PENDING", "IN_TRANSIT"])
        ).scalar() or 0
        if remaining == 0:
            trip.status = "COMPLETED"
            trip.completed_at = now

    db.commit()
    db.refresh(stop)
    return _format_stop_response(stop, db)


@router.patch("/stops/{stop_id}/status", response_model=DeliveryStopResponse)
def update_stop_status(
    stop_id: int,
    data: DeliveryStopStatusUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(DELIVERY_STOP_UPDATE_ROLES)),
):
    """
    Cập nhật trạng thái điểm giao hàng (DELIVERED hoặc FAILED) - Tương thích ngược toàn diện.
    Quy tắc nghiệp vụ 1:
    - Hàng hóa khi đã xuất kho thuộc trách nhiệm của chuyến/tài xế.
    - Điểm giao FAILED tuyệt đối KHÔNG tự động cộng lại tồn kho khả dụng.
    """
    now = get_utc_now()
    stop = db.query(DeliveryStopEntity).filter(DeliveryStopEntity.id == stop_id).first()
    if not stop:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm giao này.")

    new_status = data.status.upper().strip()
    if new_status not in ["DELIVERED", "FAILED", "IN_TRANSIT", "PENDING"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ. Cho phép: DELIVERED, FAILED, IN_TRANSIT, PENDING.")

    stop.status = new_status
    stop.delivery_status = "Delivered" if new_status == "DELIVERED" else ("Failed" if new_status == "FAILED" else new_status)
    if data.note:
        stop.note = data.note
    if data.receiver_name:
        stop.receiver_name = data.receiver_name.strip()
    if data.pod_image_url:
        stop.pod_image_url = data.pod_image_url.strip()
    if data.items_detail is not None:
        stop.items_detail = json.dumps(data.items_detail, ensure_ascii=False)

    if new_status == "DELIVERED":
        actual_time = data.actual_delivery_time or now
        stop.delivered_at = actual_time
        stop.actual_delivery_time = actual_time
        stop.failed_reason = None
        stop.failure_reason = None
        stop.return_status = None
        # Cập nhật order tương ứng thành DELIVERED
        if stop.order_code:
            order = db.query(OrderEntity).filter(OrderEntity.order_code == stop.order_code).first()
            if order and order.status != "DELIVERED":
                order.status = "DELIVERED"
    elif new_status == "FAILED":
        fail_rs = data.failure_reason or data.failed_reason or "Khách hẹn lại / Không nhận hàng"
        stop.failed_reason = fail_rs
        stop.failure_reason = fail_rs
        stop.return_status = "CHỜ_HOÀN_KHO"

    db.flush()

    # Kiểm tra xem toàn bộ các điểm giao trong chuyến đã hoàn thành chưa
    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == stop.trip_id).first()
    if trip and trip.status in ["IN_TRANSIT", "DELIVERING"]:
        remaining = db.query(func.count(DeliveryStopEntity.id)).filter(
            DeliveryStopEntity.trip_id == trip.id,
            DeliveryStopEntity.status.in_(["PENDING", "IN_TRANSIT"])
        ).scalar() or 0
        if remaining == 0:
            trip.status = "COMPLETED"
            trip.completed_at = now

    db.commit()
    db.refresh(stop)
    return _format_stop_response(stop, db)

