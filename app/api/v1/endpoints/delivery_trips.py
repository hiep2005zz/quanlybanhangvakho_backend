# backend/app/api/v1/endpoints/delivery_trips.py
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.database import get_db
from app.api.deps import get_current_user
from app.schemas.auth import UserResponse
from app.models.entities import (
    DriverEntity,
    VehicleEntity,
    DeliveryTripEntity,
    DeliveryTripStopEntity,
    DeliveryTripDispatchEntity,
    OrderDispatchEntity,
    OrderEntity,
    DealerEntity,
    DealerDeliveryPointEntity,
)
from app.services.audit_service import log_audit_event

router = APIRouter()


def get_utc_now():
    return datetime.now(timezone.utc)


def _require_warehouse_or_admin(current_user: UserResponse):
    """
    Ràng buộc RBAC: Chỉ Quản lý kho, Thủ kho và Quản trị viên mới được thao tác Chuyến giao hàng.
    """
    roles = set(current_user.roles or [])
    if current_user.role:
        roles.add(current_user.role)
    allowed = {"admin", "warehouse_manager", "warehouse"}
    if not (roles & allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập hoặc quản lý chuyến giao hàng (Yêu cầu vai trò Quản lý kho, Thủ kho hoặc Quản trị viên).",
        )


# =============================================================================
# SCHEMAS
# =============================================================================

class StopInput(BaseModel):
    stop_order: int = 1
    dealer_id: Optional[int] = None
    dealer_name: str
    delivery_point_id: Optional[int] = None
    address: str
    receiver_name: str
    receiver_phone: str
    route_note: Optional[str] = None
    dispatch_ids: List[int] = Field(default_factory=list)


class DeliveryTripCreate(BaseModel):
    delivery_date: str  # YYYY-MM-DD
    driver_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
    note: Optional[str] = None
    dispatch_ids: Optional[List[int]] = Field(default_factory=list)
    stops: Optional[List[StopInput]] = None


class DeliveryTripUpdate(BaseModel):
    delivery_date: Optional[str] = None
    driver_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
    note: Optional[str] = None


class AssignDriverVehicle(BaseModel):
    driver_id: int
    vehicle_id: int


class TripStatusUpdate(BaseModel):
    status: str  # IN_PROGRESS | COMPLETED | CANCELLED
    note: Optional[str] = None


class StopStatusUpdate(BaseModel):
    status: str  # DELIVERED | FAILED | PENDING
    failure_reason: Optional[str] = None
    note: Optional[str] = None
    delivered_at: Optional[str] = None


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/drivers")
def get_drivers(
    delivery_date: Optional[str] = Query(None, description="Ngày giao YYYY-MM-DD để kiểm tra bận"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy danh sách tài xế đang hoạt động và trạng thái bận lịch theo ngày."""
    _require_warehouse_or_admin(current_user)

    drivers = db.query(DriverEntity).filter(DriverEntity.status == "ACTIVE").all()

    # Tìm các tài xế đang bận chuyến active trong ngày
    busy_map: Dict[int, str] = {}
    if delivery_date:
        active_trips = db.query(DeliveryTripEntity).filter(
            DeliveryTripEntity.delivery_date == delivery_date,
            DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
            DeliveryTripEntity.driver_id.isnot(None),
        ).all()
        for t in active_trips:
            if t.driver_id:
                busy_map[t.driver_id] = t.trip_code

    results = []
    for d in drivers:
        results.append({
            "id": d.id,
            "code": d.code,
            "name": d.name,
            "phone": d.phone,
            "license_number": d.license_number,
            "status": d.status,
            "is_busy": d.id in busy_map,
            "current_trip_code": busy_map.get(d.id),
        })
    return results


@router.get("/vehicles")
def get_vehicles(
    delivery_date: Optional[str] = Query(None, description="Ngày giao YYYY-MM-DD để kiểm tra bận"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy danh sách phương tiện đang hoạt động và trạng thái bận lịch theo ngày."""
    _require_warehouse_or_admin(current_user)

    vehicles = db.query(VehicleEntity).filter(VehicleEntity.status == "ACTIVE").all()

    busy_map: Dict[int, str] = {}
    if delivery_date:
        active_trips = db.query(DeliveryTripEntity).filter(
            DeliveryTripEntity.delivery_date == delivery_date,
            DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
            DeliveryTripEntity.vehicle_id.isnot(None),
        ).all()
        for t in active_trips:
            if t.vehicle_id:
                busy_map[t.vehicle_id] = t.trip_code

    results = []
    for v in vehicles:
        results.append({
            "id": v.id,
            "plate_number": v.plate_number,
            "vehicle_type": v.vehicle_type,
            "capacity_kg": v.capacity_kg,
            "status": v.status,
            "is_busy": v.id in busy_map,
            "current_trip_code": busy_map.get(v.id),
        })
    return results


@router.get("/eligible-dispatches")
def get_eligible_dispatches(
    warehouse_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Lấy danh sách các phiếu xuất kho đủ điều kiện giao hàng:
    - Đã xác nhận xuất kho (có trong order_dispatches)
    - Chưa được đưa vào bất kỳ chuyến giao hàng nào đang hoạt động (DRAFT, ASSIGNED, IN_PROGRESS).
    """
    _require_warehouse_or_admin(current_user)

    # 1. Tìm các dispatch_id đã nằm trong chuyến active
    assigned_rows = db.query(DeliveryTripDispatchEntity.dispatch_id).join(
        DeliveryTripEntity, DeliveryTripDispatchEntity.trip_id == DeliveryTripEntity.id
    ).filter(
        DeliveryTripEntity.status.in_(["DRAFT", "ASSIGNED", "IN_PROGRESS"])
    ).all()
    assigned_ids = {r[0] for r in assigned_rows}

    # 2. Truy vấn phiếu xuất kho chưa được gán vào chuyến active
    query = db.query(OrderDispatchEntity)
    if assigned_ids:
        query = query.filter(~OrderDispatchEntity.id.in_(assigned_ids))
    if warehouse_id:
        query = query.filter(OrderDispatchEntity.warehouse_id == warehouse_id)

    dispatches = query.order_by(OrderDispatchEntity.id.desc()).all()

    results = []
    for d in dispatches:
        # Lấy thông tin order
        order = db.query(OrderEntity).filter(OrderEntity.id == d.order_id).first()
        dealer = None
        delivery_point = None

        if order:
            dealer = db.query(DealerEntity).filter(DealerEntity.id == order.dealer_id).first()
            if order.delivery_point_id:
                delivery_point = db.query(DealerDeliveryPointEntity).filter(
                    DealerDeliveryPointEntity.id == order.delivery_point_id
                ).first()

        # Xác định thông tin giao nhận
        receiver_name = ""
        receiver_phone = ""
        address = ""
        route_note = ""

        if delivery_point:
            receiver_name = delivery_point.receiver_name or ""
            receiver_phone = delivery_point.receiver_phone or ""
            address = delivery_point.address or ""
            route_note = delivery_point.route_note or ""
        elif dealer:
            receiver_name = dealer.name or ""
            receiver_phone = dealer.phone or ""
            address = dealer.address or ""
        elif order:
            receiver_name = order.dealer_name or ""

        # Tóm tắt mặt hàng
        items_summary = ""
        if d.items_json:
            try:
                items_data = json.loads(d.items_json)
                if isinstance(items_data, list):
                    item_strs = [
                        f"{it.get('product_name', it.get('name', 'SP'))} ({it.get('dispatched_quantity', it.get('quantity', 0))} {it.get('unit', 'Cái')})"
                        for it in items_data
                    ]
                    items_summary = ", ".join(item_strs)
            except Exception:
                pass

        results.append({
            "dispatch_id": d.id,
            "dispatch_code": d.dispatch_code,
            "order_id": d.order_id,
            "order_code": d.order_code,
            "dispatch_type": d.dispatch_type,
            "round_number": d.round_number,
            "warehouse_id": d.warehouse_id,
            "warehouse_name": d.warehouse_name or "Kho Tổng Hà Nội",
            "performed_by": d.performed_by,
            "created_at": d.created_at.isoformat() if hasattr(d.created_at, "isoformat") else str(d.created_at),
            "dealer_id": order.dealer_id if order else None,
            "dealer_name": order.dealer_name if order else (dealer.name if dealer else "Khách hàng"),
            "delivery_point_id": order.delivery_point_id if order else None,
            "receiver_name": receiver_name or (order.dealer_name if order else "Khách hàng"),
            "receiver_phone": receiver_phone or "—",
            "address": address or "Tại kho / Chưa có địa chỉ chi tiết",
            "route_note": route_note or (order.note if order else ""),
            "items_summary": items_summary,
        })

    return results


@router.get("")
def list_delivery_trips(
    status_filter: Optional[str] = Query(None, alias="status"),
    delivery_date: Optional[str] = Query(None),
    driver_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy danh sách các chuyến giao hàng với các bộ lọc."""
    _require_warehouse_or_admin(current_user)

    query = db.query(DeliveryTripEntity)

    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(DeliveryTripEntity.status == status_filter.upper())
    if delivery_date:
        query = query.filter(DeliveryTripEntity.delivery_date == delivery_date)
    if driver_id:
        query = query.filter(DeliveryTripEntity.driver_id == driver_id)
    if search:
        kw = f"%{search.strip().lower()}%"
        query = query.filter(
            (func.lower(DeliveryTripEntity.trip_code).like(kw))
            | (func.lower(DeliveryTripEntity.driver_name).like(kw))
            | (func.lower(DeliveryTripEntity.vehicle_plate).like(kw))
        )

    trips = query.order_by(DeliveryTripEntity.id.desc()).all()

    results = []
    for t in trips:
        results.append({
            "id": t.id,
            "trip_code": t.trip_code,
            "delivery_date": t.delivery_date,
            "driver_id": t.driver_id,
            "driver_name": t.driver_name,
            "driver_phone": t.driver_phone,
            "vehicle_id": t.vehicle_id,
            "vehicle_plate": t.vehicle_plate,
            "vehicle_type": t.vehicle_type,
            "warehouse_id": t.warehouse_id,
            "warehouse_name": t.warehouse_name,
            "status": t.status,
            "note": t.note,
            "total_stops": t.total_stops,
            "total_dispatches": t.total_dispatches,
            "created_by": t.created_by,
            "created_at": t.created_at.isoformat() if hasattr(t.created_at, "isoformat") else str(t.created_at),
            "updated_at": t.updated_at.isoformat() if hasattr(t.updated_at, "isoformat") else str(t.updated_at),
        })

    return results


@router.post("", status_code=status.HTTP_201_CREATED)
def create_delivery_trip(
    payload: DeliveryTripCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Tạo chuyến giao hàng mới:
    - Gom nhiều phiếu xuất vào một chuyến.
    - Gom các phiếu cùng điểm giao thành một điểm dừng nếu có.
    - Gán tài xế & phương tiện.
    - Kiểm tra không trùng lịch tài xế hoặc xe.
    """
    _require_warehouse_or_admin(current_user)

    # Thu thập tất cả dispatch_ids
    dispatch_ids = list(payload.dispatch_ids or [])
    if payload.stops:
        for st in payload.stops:
            dispatch_ids.extend(st.dispatch_ids or [])
    dispatch_ids = list(set(dispatch_ids))

    if not dispatch_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng chọn ít nhất một phiếu xuất kho để tạo chuyến giao hàng.",
        )

    # 1. Kiểm tra tranh chấp: Đảm bảo không phiếu xuất nào đã nằm trong chuyến active
    active_conflicts = db.query(
        DeliveryTripDispatchEntity.dispatch_code, DeliveryTripEntity.trip_code
    ).join(
        DeliveryTripEntity, DeliveryTripDispatchEntity.trip_id == DeliveryTripEntity.id
    ).filter(
        DeliveryTripDispatchEntity.dispatch_id.in_(dispatch_ids),
        DeliveryTripEntity.status.in_(["DRAFT", "ASSIGNED", "IN_PROGRESS"]),
    ).all()

    if active_conflicts:
        conflict_msg = ", ".join([f"{c[0]} (đang trong {c[1]})" for c in active_conflicts])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Các phiếu xuất sau đã thuộc chuyến giao đang hoạt động khác: {conflict_msg}",
        )

    # 2. Kiểm tra tài xế & phương tiện
    driver = None
    if payload.driver_id:
        driver = db.query(DriverEntity).filter(DriverEntity.id == payload.driver_id, DriverEntity.status == "ACTIVE").first()
        if not driver:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tài xế không tồn tại hoặc đã ngừng hoạt động.")

        # Kiểm tra trùng lịch tài xế
        conflict_driver_trip = db.query(DeliveryTripEntity).filter(
            DeliveryTripEntity.delivery_date == payload.delivery_date,
            DeliveryTripEntity.driver_id == payload.driver_id,
            DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
        ).first()
        if conflict_driver_trip:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tài xế {driver.name} đã được phân công cho chuyến {conflict_driver_trip.trip_code} vào ngày {payload.delivery_date}.",
            )

    vehicle = None
    if payload.vehicle_id:
        vehicle = db.query(VehicleEntity).filter(VehicleEntity.id == payload.vehicle_id, VehicleEntity.status == "ACTIVE").first()
        if not vehicle:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phương tiện không tồn tại hoặc đang bảo trì.")

        # Kiểm tra trùng lịch phương tiện
        conflict_vehicle_trip = db.query(DeliveryTripEntity).filter(
            DeliveryTripEntity.delivery_date == payload.delivery_date,
            DeliveryTripEntity.vehicle_id == payload.vehicle_id,
            DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
        ).first()
        if conflict_vehicle_trip:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Phương tiện {vehicle.plate_number} đã được gán cho chuyến {conflict_vehicle_trip.trip_code} vào ngày {payload.delivery_date}.",
            )

    # 3. Tạo mã chuyến duy nhất: TRIP-YYYYMMDD-XXXX
    date_compact = payload.delivery_date.replace("-", "")
    today_count = db.query(DeliveryTripEntity).filter(
        DeliveryTripEntity.delivery_date == payload.delivery_date
    ).count()
    trip_code = f"TRIP-{date_compact}-{today_count + 1:03d}"

    # Kiểm tra trùng code
    while db.query(DeliveryTripEntity).filter(DeliveryTripEntity.trip_code == trip_code).first():
        trip_code = f"TRIP-{date_compact}-{uuid.uuid4().hex[:4].upper()}"

    initial_status = "ASSIGNED" if (driver and vehicle) else "DRAFT"

    # Lấy thông tin các phiếu xuất
    dispatches_db = db.query(OrderDispatchEntity).filter(
        OrderDispatchEntity.id.in_(dispatch_ids)
    ).all()
    dispatch_map = {d.id: d for d in dispatches_db}

    # Nếu Frontend đã gửi cấu trúc stops chi tiết
    prepared_stops: List[Dict[str, Any]] = []

    if payload.stops and len(payload.stops) > 0:
        for st in payload.stops:
            st_dispatches = [dispatch_map[did] for did in st.dispatch_ids if did in dispatch_map]
            if st_dispatches:
                prepared_stops.append({
                    "stop_order": st.stop_order,
                    "dealer_id": st.dealer_id,
                    "dealer_name": st.dealer_name,
                    "delivery_point_id": st.delivery_point_id,
                    "address": st.address,
                    "receiver_name": st.receiver_name,
                    "receiver_phone": st.receiver_phone,
                    "route_note": st.route_note,
                    "dispatches": st_dispatches,
                })
    else:
        # Tự động gom các phiếu có cùng địa chỉ / điểm giao thành 1 điểm dừng
        grouped_by_address: Dict[str, Dict[str, Any]] = {}
        order_seq = 1

        for did in dispatch_ids:
            d = dispatch_map.get(did)
            if not d:
                continue

            order = db.query(OrderEntity).filter(OrderEntity.id == d.order_id).first()
            dealer = db.query(DealerEntity).filter(DealerEntity.id == order.dealer_id).first() if order else None
            delivery_point = None
            if order and order.delivery_point_id:
                delivery_point = db.query(DealerDeliveryPointEntity).filter(
                    DealerDeliveryPointEntity.id == order.delivery_point_id
                ).first()

            addr_key = (delivery_point.address if delivery_point else (dealer.address if dealer else "Kho")).strip().lower()

            if addr_key not in grouped_by_address:
                grouped_by_address[addr_key] = {
                    "stop_order": order_seq,
                    "dealer_id": order.dealer_id if order else None,
                    "dealer_name": order.dealer_name if order else (dealer.name if dealer else "Khách hàng"),
                    "delivery_point_id": order.delivery_point_id if order else None,
                    "address": delivery_point.address if delivery_point else (dealer.address if dealer else "Tại kho"),
                    "receiver_name": delivery_point.receiver_name if delivery_point else (dealer.name if dealer else "Khách hàng"),
                    "receiver_phone": delivery_point.receiver_phone if delivery_point else (dealer.phone if dealer else ""),
                    "route_note": delivery_point.route_note if delivery_point else (order.note if order else ""),
                    "dispatches": [],
                }
                order_seq += 1

            grouped_by_address[addr_key]["dispatches"].append(d)

        prepared_stops = list(grouped_by_address.values())

    # 4. Lưu dữ liệu nguyên khối (Atomic Transaction)
    trip = DeliveryTripEntity(
        trip_code=trip_code,
        delivery_date=payload.delivery_date,
        driver_id=driver.id if driver else None,
        driver_name=driver.name if driver else None,
        driver_phone=driver.phone if driver else None,
        vehicle_id=vehicle.id if vehicle else None,
        vehicle_plate=vehicle.plate_number if vehicle else None,
        vehicle_type=vehicle.vehicle_type if vehicle else None,
        warehouse_id=payload.warehouse_id or (dispatches_db[0].warehouse_id if dispatches_db else None),
        warehouse_name=payload.warehouse_name or (dispatches_db[0].warehouse_name if dispatches_db else "Kho Tổng Hà Nội"),
        status=initial_status,
        note=payload.note,
        total_stops=len(prepared_stops),
        total_dispatches=len(dispatch_ids),
        created_by=current_user.full_name or current_user.username,
        created_at=get_utc_now(),
        updated_at=get_utc_now(),
    )
    db.add(trip)
    db.flush()

    for idx, st_data in enumerate(prepared_stops, start=1):
        stop_entity = DeliveryTripStopEntity(
            trip_id=trip.id,
            stop_order=st_data.get("stop_order") or idx,
            dealer_id=st_data.get("dealer_id"),
            dealer_name=st_data.get("dealer_name") or "Khách hàng",
            delivery_point_id=st_data.get("delivery_point_id"),
            address=st_data.get("address") or "Tại kho",
            receiver_name=st_data.get("receiver_name") or "Khách hàng",
            receiver_phone=st_data.get("receiver_phone") or "—",
            route_note=st_data.get("route_note"),
            status="PENDING",
            created_at=get_utc_now(),
        )
        db.add(stop_entity)
        db.flush()

        for d in st_data.get("dispatches", []):
            items_summary = ""
            if d.items_json:
                try:
                    items_data = json.loads(d.items_json)
                    if isinstance(items_data, list):
                        item_strs = [
                            f"{it.get('product_name', 'SP')} x{it.get('dispatched_quantity', it.get('quantity', 0))}"
                            for it in items_data
                        ]
                        items_summary = ", ".join(item_strs)
                except Exception:
                    pass

            dt_dispatch = DeliveryTripDispatchEntity(
                trip_id=trip.id,
                stop_id=stop_entity.id,
                dispatch_id=d.id,
                dispatch_code=d.dispatch_code,
                order_code=d.order_code,
                items_summary=items_summary,
                status="PENDING",
                created_at=get_utc_now(),
            )
            db.add(dt_dispatch)

    db.commit()
    db.refresh(trip)

    # Ghi audit log
    try:
        log_audit_event(
            db=db,
            user_id=current_user.id,
            user_name=current_user.full_name or current_user.username,
            action_type="DELIVERY_TRIP_CREATE",
            entity_type="DeliveryTrip",
            entity_id=trip.trip_code,
            new_values={
                "trip_code": trip.trip_code,
                "delivery_date": trip.delivery_date,
                "driver": trip.driver_name,
                "vehicle": trip.vehicle_plate,
                "total_stops": trip.total_stops,
                "total_dispatches": trip.total_dispatches,
            },
            reason="Tạo chuyến giao hàng mới và phân tuyến",
        )
    except Exception:
        pass

    return {
        "status": "success",
        "message": f"Tạo thành công chuyến giao hàng {trip.trip_code} với {trip.total_stops} điểm dừng ({trip.total_dispatches} phiếu xuất).",
        "trip_id": trip.id,
        "trip_code": trip.trip_code,
    }


@router.get("/{trip_id}")
def get_delivery_trip_detail(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy chi tiết chuyến giao hàng kèm danh sách điểm dừng và phiếu xuất kho theo thứ tự."""
    _require_warehouse_or_admin(current_user)

    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy chuyến giao hàng.")

    stops_db = db.query(DeliveryTripStopEntity).filter(
        DeliveryTripStopEntity.trip_id == trip.id
    ).order_by(DeliveryTripStopEntity.stop_order.asc()).all()

    stops_res = []
    for st in stops_db:
        dispatches_db = db.query(DeliveryTripDispatchEntity).filter(
            DeliveryTripDispatchEntity.stop_id == st.id
        ).all()

        stops_res.append({
            "id": st.id,
            "stop_order": st.stop_order,
            "dealer_id": st.dealer_id,
            "dealer_name": st.dealer_name,
            "delivery_point_id": st.delivery_point_id,
            "address": st.address,
            "receiver_name": st.receiver_name,
            "receiver_phone": st.receiver_phone,
            "route_note": st.route_note,
            "status": st.status,
            "delivered_at": st.delivered_at.isoformat() if st.delivered_at else None,
            "failure_reason": st.failure_reason,
            "note": st.note,
            "dispatches": [
                {
                    "id": d.id,
                    "dispatch_id": d.dispatch_id,
                    "dispatch_code": d.dispatch_code,
                    "order_code": d.order_code,
                    "items_summary": d.items_summary,
                    "status": d.status,
                }
                for d in dispatches_db
            ],
        })

    return {
        "id": trip.id,
        "trip_code": trip.trip_code,
        "delivery_date": trip.delivery_date,
        "driver_id": trip.driver_id,
        "driver_name": trip.driver_name,
        "driver_phone": trip.driver_phone,
        "vehicle_id": trip.vehicle_id,
        "vehicle_plate": trip.vehicle_plate,
        "vehicle_type": trip.vehicle_type,
        "warehouse_id": trip.warehouse_id,
        "warehouse_name": trip.warehouse_name,
        "status": trip.status,
        "note": trip.note,
        "total_stops": trip.total_stops,
        "total_dispatches": trip.total_dispatches,
        "created_by": trip.created_by,
        "created_at": trip.created_at.isoformat() if hasattr(trip.created_at, "isoformat") else str(trip.created_at),
        "updated_at": trip.updated_at.isoformat() if hasattr(trip.updated_at, "isoformat") else str(trip.updated_at),
        "stops": stops_res,
    }


@router.put("/{trip_id}")
def update_delivery_trip(
    trip_id: int,
    payload: DeliveryTripUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Cập nhật thông tin chuyến giao hàng (chỉ khi chuyến ở trạng thái DRAFT hoặc ASSIGNED)."""
    _require_warehouse_or_admin(current_user)

    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy chuyến giao hàng.")

    if trip.status not in ["DRAFT", "ASSIGNED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể chỉnh sửa chuyến giao hàng đang ở trạng thái {trip.status}.",
        )

    # Cập nhật thông tin cơ bản
    if payload.delivery_date is not None:
        trip.delivery_date = payload.delivery_date
    if payload.note is not None:
        trip.note = payload.note
    if payload.warehouse_id is not None:
        trip.warehouse_id = payload.warehouse_id
    if payload.warehouse_name is not None:
        trip.warehouse_name = payload.warehouse_name

    # Cập nhật tài xế nếu có
    if payload.driver_id is not None:
        driver = db.query(DriverEntity).filter(DriverEntity.id == payload.driver_id, DriverEntity.status == "ACTIVE").first()
        if not driver:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tài xế không tồn tại hoặc đã ngừng hoạt động.")

        conflict = db.query(DeliveryTripEntity).filter(
            DeliveryTripEntity.id != trip.id,
            DeliveryTripEntity.delivery_date == trip.delivery_date,
            DeliveryTripEntity.driver_id == driver.id,
            DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
        ).first()
        if conflict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Tài xế {driver.name} đã bị trùng lịch với chuyến {conflict.trip_code}.")

        trip.driver_id = driver.id
        trip.driver_name = driver.name
        trip.driver_phone = driver.phone

    # Cập nhật phương tiện nếu có
    if payload.vehicle_id is not None:
        vehicle = db.query(VehicleEntity).filter(VehicleEntity.id == payload.vehicle_id, VehicleEntity.status == "ACTIVE").first()
        if not vehicle:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phương tiện không tồn tại hoặc đang bảo trì.")

        conflict = db.query(DeliveryTripEntity).filter(
            DeliveryTripEntity.id != trip.id,
            DeliveryTripEntity.delivery_date == trip.delivery_date,
            DeliveryTripEntity.vehicle_id == vehicle.id,
            DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
        ).first()
        if conflict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Phương tiện {vehicle.plate_number} đã bị trùng lịch với chuyến {conflict.trip_code}.")

        trip.vehicle_id = vehicle.id
        trip.vehicle_plate = vehicle.plate_number
        trip.vehicle_type = vehicle.vehicle_type

    if trip.driver_id and trip.vehicle_id and trip.status == "DRAFT":
        trip.status = "ASSIGNED"

    trip.updated_at = get_utc_now()
    db.commit()
    db.refresh(trip)

    return {"status": "success", "message": "Cập nhật chuyến giao hàng thành công."}


@router.post("/{trip_id}/assign")
def assign_driver_and_vehicle(
    trip_id: int,
    payload: AssignDriverVehicle,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Phân công tài xế và phương tiện cho chuyến giao hàng."""
    _require_warehouse_or_admin(current_user)

    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy chuyến giao hàng.")

    if trip.status not in ["DRAFT", "ASSIGNED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể phân công cho chuyến giao hàng đang ở trạng thái {trip.status}.",
        )

    driver = db.query(DriverEntity).filter(DriverEntity.id == payload.driver_id, DriverEntity.status == "ACTIVE").first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tài xế không tồn tại hoặc đã ngừng hoạt động.")

    conflict_driver = db.query(DeliveryTripEntity).filter(
        DeliveryTripEntity.id != trip.id,
        DeliveryTripEntity.delivery_date == trip.delivery_date,
        DeliveryTripEntity.driver_id == driver.id,
        DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
    ).first()
    if conflict_driver:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tài xế {driver.name} đã được phân công cho chuyến {conflict_driver.trip_code} vào ngày {trip.delivery_date}.",
        )

    vehicle = db.query(VehicleEntity).filter(VehicleEntity.id == payload.vehicle_id, VehicleEntity.status == "ACTIVE").first()
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phương tiện không tồn tại hoặc đang bảo trì.")

    conflict_vehicle = db.query(DeliveryTripEntity).filter(
        DeliveryTripEntity.id != trip.id,
        DeliveryTripEntity.delivery_date == trip.delivery_date,
        DeliveryTripEntity.vehicle_id == vehicle.id,
        DeliveryTripEntity.status.in_(["ASSIGNED", "IN_PROGRESS"]),
    ).first()
    if conflict_vehicle:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phương tiện {vehicle.plate_number} đã được gán cho chuyến {conflict_vehicle.trip_code} vào ngày {trip.delivery_date}.",
        )

    trip.driver_id = driver.id
    trip.driver_name = driver.name
    trip.driver_phone = driver.phone
    trip.vehicle_id = vehicle.id
    trip.vehicle_plate = vehicle.plate_number
    trip.vehicle_type = vehicle.vehicle_type
    trip.status = "ASSIGNED"
    trip.updated_at = get_utc_now()

    db.commit()
    db.refresh(trip)

    return {
        "status": "success",
        "message": f"Đã phân công tài xế {driver.name} và xe {vehicle.plate_number} cho chuyến {trip.trip_code}.",
    }


@router.patch("/{trip_id}/status")
def update_trip_status(
    trip_id: int,
    payload: TripStatusUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Cập nhật trạng thái chuyến giao hàng:
    - Bắt đầu giao (IN_PROGRESS)
    - Hoàn tất chuyến (COMPLETED)
    - Hủy chuyến (CANCELLED)
    """
    _require_warehouse_or_admin(current_user)

    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy chuyến giao hàng.")

    new_st = payload.status.upper()

    # Quy tắc: Không cho hủy nếu đã hoàn tất
    if trip.status == "COMPLETED" and new_st == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chuyến giao hàng đã hoàn thành, không thể hủy bỏ.",
        )

    if new_st == "IN_PROGRESS":
        if not trip.driver_id or not trip.vehicle_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cần phân công tài xế và phương tiện trước khi bắt đầu giao hàng.",
            )
        trip.status = "IN_PROGRESS"

    elif new_st == "COMPLETED":
        # Đánh dấu tất cả các điểm dừng còn lại là DELIVERED nếu người dùng xác nhận hoàn thành
        stops = db.query(DeliveryTripStopEntity).filter(DeliveryTripStopEntity.trip_id == trip.id).all()
        has_failed = False
        for st in stops:
            if st.status == "FAILED":
                has_failed = True
            elif st.status == "PENDING":
                st.status = "DELIVERED"
                st.delivered_at = get_utc_now()
                # Đồng bộ phiếu xuất
                db.query(DeliveryTripDispatchEntity).filter(DeliveryTripDispatchEntity.stop_id == st.id).update({"status": "DELIVERED"})

        trip.status = "PARTIALLY_FAILED" if has_failed else "COMPLETED"

    elif new_st == "CANCELLED":
        trip.status = "CANCELLED"

    else:
        trip.status = new_st

    if payload.note:
        trip.note = f"{trip.note or ''}\n[{new_st}]: {payload.note}".strip()

    trip.updated_at = get_utc_now()
    db.commit()
    db.refresh(trip)

    return {
        "status": "success",
        "message": f"Đã cập nhật trạng thái chuyến {trip.trip_code} sang {trip.status}.",
        "new_status": trip.status,
    }


@router.patch("/{trip_id}/stops/{stop_id}")
def update_trip_stop_result(
    trip_id: int,
    stop_id: int,
    payload: StopStatusUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Cập nhật kết quả giao hàng tại một điểm dừng (DELIVERED hoặc FAILED).
    Tự động cập nhật trạng thái chuyến dựa trên kết quả giao thực tế.
    """
    _require_warehouse_or_admin(current_user)

    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy chuyến giao hàng.")

    stop = db.query(DeliveryTripStopEntity).filter(
        DeliveryTripStopEntity.id == stop_id, DeliveryTripStopEntity.trip_id == trip.id
    ).first()
    if not stop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy điểm dừng trong chuyến này.")

    new_status = payload.status.upper()
    if new_status not in ["DELIVERED", "FAILED", "PENDING"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Trạng thái điểm dừng không hợp lệ.")

    stop.status = new_status
    if new_status == "DELIVERED":
        stop.delivered_at = get_utc_now()
        stop.failure_reason = None
    elif new_status == "FAILED":
        stop.failure_reason = payload.failure_reason or "Không liên lạc được người nhận / Khách hẹn giao lại"
    else:
        stop.delivered_at = None
        stop.failure_reason = None

    if payload.note:
        stop.note = payload.note

    # Cập nhật trạng thái các phiếu xuất thuộc điểm dừng này
    db.query(DeliveryTripDispatchEntity).filter(
        DeliveryTripDispatchEntity.stop_id == stop.id
    ).update({"status": new_status})

    # Tự động tính toán trạng thái toàn chuyến (Rule 7 & 8)
    all_stops = db.query(DeliveryTripStopEntity).filter(
        DeliveryTripStopEntity.trip_id == trip.id
    ).all()

    has_pending = any(s.status == "PENDING" for s in all_stops)
    has_failed = any(s.status == "FAILED" for s in all_stops)
    all_delivered = all(s.status == "DELIVERED" for s in all_stops)

    if not has_pending:
        if all_delivered:
            trip.status = "COMPLETED"
        elif has_failed:
            trip.status = "PARTIALLY_FAILED"
    else:
        # Nếu đang có ít nhất 1 điểm đã giao hoặc thất bại nhưng còn điểm pending thì chuyến là IN_PROGRESS
        if trip.status in ["DRAFT", "ASSIGNED"]:
            trip.status = "IN_PROGRESS"

    trip.updated_at = get_utc_now()
    db.commit()
    db.refresh(stop)
    db.refresh(trip)

    return {
        "status": "success",
        "message": f"Đã cập nhật điểm dừng #{stop.stop_order} ({stop.receiver_name}) sang trạng thái {stop.status}.",
        "stop_status": stop.status,
        "trip_status": trip.status,
    }


@router.delete("/{trip_id}", status_code=status.HTTP_200_OK)
def delete_draft_delivery_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Xóa chuyến giao hàng nháp (chỉ khi chuyến ở trạng thái DRAFT)."""
    _require_warehouse_or_admin(current_user)

    trip = db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy chuyến giao hàng.")

    if trip.status not in ["DRAFT", "CANCELLED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chỉ có thể xóa chuyến giao hàng ở trạng thái Nháp (DRAFT) hoặc Đã hủy (CANCELLED). Chuyến này đang là {trip.status}.",
        )

    trip_code = trip.trip_code
    db.delete(trip)
    db.commit()

    return {"status": "success", "message": f"Đã xóa chuyến giao hàng {trip_code}."}
