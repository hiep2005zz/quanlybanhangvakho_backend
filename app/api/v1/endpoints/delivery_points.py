import re
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.dealer import DEALERS_DB
from app.models.entities import DealerDeliveryPointEntity, MasterDeliveryPointEntity
from app.api.v1.endpoints.orders import ORDERS_DB
from app.schemas.delivery_point import (
    DeliveryPointCreate, DeliveryPointUpdate, DeliveryPointOut,
)

router = APIRouter(prefix="/{dealer_id}/delivery-points")
dealers_router = APIRouter()


def _sync_to_master(db: Session, label: str, address: str, receiver_name: str = "", receiver_phone: str = "", route_note: str = ""):
    lbl_clean = label.strip()
    addr_clean = address.strip()
    existing = db.scalars(
        select(MasterDeliveryPointEntity).where(
            MasterDeliveryPointEntity.label == lbl_clean,
            MasterDeliveryPointEntity.address == addr_clean,
        )
    ).first()
    if existing:
        if not existing.is_active:
            existing.is_active = True
        if receiver_name and not existing.receiver_name:
            existing.receiver_name = receiver_name
        if receiver_phone and not existing.receiver_phone:
            existing.receiver_phone = receiver_phone
        if route_note and not existing.route_note:
            existing.route_note = route_note
    else:
        new_master = MasterDeliveryPointEntity(
            label=lbl_clean,
            address=addr_clean,
            receiver_name=receiver_name or "",
            receiver_phone=receiver_phone or "",
            route_note=route_note or "",
            is_active=True,
        )
        db.add(new_master)
    db.flush()


@dealers_router.get("")
def list_dealers(user=Depends(get_current_user)):
    """Lấy danh sách các đại lý mà người dùng hiện tại có quyền truy cập."""
    roles = getattr(user, "roles", None) or [getattr(user, "role", None)]
    roles = {str(r).lower() for r in roles if r}
    if roles & {"admin", "sales_manager", "sales"}:
        dealers = list(DEALERS_DB.values())
    else:
        dealers = list(DEALERS_DB.values())

    return [
        {
            "id": d.id,
            "code": d.code,
            "name": d.name,
            "phone": d.phone,
            "email": d.email,
            "address": d.address,
            "assigned_sale_id": d.assigned_sale_id,
        }
        for d in dealers
    ]


@dealers_router.get("/all-delivery-points")
def list_all_delivery_points(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Lấy danh sách tất cả các điểm giao hàng hiện có trong danh mục chung."""
    # Tự động đồng bộ các điểm giao từ dealer_delivery_points vào master_delivery_points nếu chưa có
    existing_dealers_pts = db.scalars(select(DealerDeliveryPointEntity)).all()
    for dp in existing_dealers_pts:
        _sync_to_master(db, dp.label, dp.address, dp.receiver_name, dp.receiver_phone, dp.route_note)
    db.commit()

    q = select(MasterDeliveryPointEntity).where(
        MasterDeliveryPointEntity.is_active == True,  # noqa: E712
    ).order_by(MasterDeliveryPointEntity.id.desc())
    items = db.scalars(q).all()

    return [
        {
            "id": item.id,
            "label": item.label,
            "address": item.address,
            "receiver_name": item.receiver_name or "",
            "receiver_phone": item.receiver_phone or "",
            "route_note": item.route_note or "",
            "is_default": False,
            "is_active": item.is_active,
        }
        for item in items
    ]


@dealers_router.post("/master-delivery-points", status_code=201)
def create_master_point(body: DeliveryPointCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Tạo điểm giao hàng mới vào danh mục điểm giao chung."""
    phone_clean = (body.receiver_phone or "").strip()
    if not phone_clean:
        raise HTTPException(400, "Vui lòng nhập số điện thoại người nhận.")
    if not re.fullmatch(r"^\d{10}$", phone_clean):
        raise HTTPException(400, "Số điện thoại người nhận phải bao gồm đúng 10 chữ số.")
    _sync_to_master(db, body.label, body.address, body.receiver_name or "", phone_clean, body.route_note or "")
    db.commit()
    item = db.scalars(select(MasterDeliveryPointEntity).where(
        MasterDeliveryPointEntity.label == body.label.strip(),
        MasterDeliveryPointEntity.address == body.address.strip(),
        MasterDeliveryPointEntity.is_active == True,
    )).first()
    return {
        "id": item.id if item else 0,
        "label": body.label,
        "address": body.address,
        "receiver_name": body.receiver_name or "",
        "receiver_phone": body.receiver_phone or "",
        "route_note": body.route_note or "",
        "is_default": False,
        "is_active": True,
    }


@dealers_router.put("/master-delivery-points/{point_id}")
def update_master_point(point_id: int, body: DeliveryPointUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Cập nhật thông tin điểm giao trong danh mục điểm giao chung."""
    item = db.get(MasterDeliveryPointEntity, point_id)
    if not item or not item.is_active:
        raise HTTPException(404, "Không tìm thấy điểm giao trong danh mục")
    old_label = item.label
    old_address = item.address
    if body.receiver_phone is not None and body.receiver_phone.strip():
        phone_clean = re.sub(r"\D", "", body.receiver_phone.strip())
        if len(phone_clean) > 10:
            phone_clean = phone_clean[:10]
        body.receiver_phone = phone_clean
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(item, k):
            setattr(item, k, v)

    matching_dealer_points = db.scalars(
        select(DealerDeliveryPointEntity).where(
            DealerDeliveryPointEntity.label == old_label,
            DealerDeliveryPointEntity.address == old_address,
            DealerDeliveryPointEntity.is_active == True,
        )
    ).all()
    for dp in matching_dealer_points:
        dp.label = item.label
        dp.address = item.address
        if item.receiver_name:
            dp.receiver_name = item.receiver_name
        if item.receiver_phone:
            dp.receiver_phone = item.receiver_phone
        if item.route_note is not None:
            dp.route_note = item.route_note

    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "label": item.label,
        "address": item.address,
        "receiver_name": item.receiver_name or "",
        "receiver_phone": item.receiver_phone or "",
        "route_note": item.route_note or "",
        "is_default": False,
        "is_active": item.is_active,
    }


@dealers_router.delete("/master-delivery-points/{point_id}", status_code=204)
def delete_master_point(point_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Xóa điểm giao khỏi danh mục điểm giao chung."""
    item = db.get(MasterDeliveryPointEntity, point_id)
    if not item or not item.is_active:
        raise HTTPException(404, "Không tìm thấy điểm giao trong danh mục")
    item.is_active = False
    db.commit()


def _check_access(db: Session, dealer_id: int, user):
    dealer = next((d for d in DEALERS_DB.values() if getattr(d, "id", None) == dealer_id), None)
    if not dealer:
        raise HTTPException(404, "Không tìm thấy đại lý")

    roles = getattr(user, "roles", None) or [getattr(user, "role", None)]
    roles = {str(r).lower() for r in roles if r}
    # Cho phép các vai trò quản trị, nhân viên và đại lý (khách hàng) truy cập điểm giao
    if roles & {"admin", "sales_manager", "sales", "accountant", "warehouse", "warehouse_manager", "customer", "agent"}:
        return dealer
    # Mặc định cho phép nếu là chính đại lý đang đăng nhập
    if user.id == dealer_id or getattr(user, "username", "").lower() in [getattr(dealer, "code", "").lower(), "muahang"]:
        return dealer
    return dealer


def _clear_default(db: Session, dealer_id: int):
    db.execute(
        update(DealerDeliveryPointEntity)
        .where(DealerDeliveryPointEntity.dealer_id == dealer_id,
               DealerDeliveryPointEntity.is_default == True)  # noqa: E712
        .values(is_default=False)
    )
    db.flush()


@router.get("", response_model=List[DeliveryPointOut])
def list_points(dealer_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    dealer = _check_access(db, dealer_id, user)
    q = select(DealerDeliveryPointEntity).where(
        DealerDeliveryPointEntity.dealer_id == dealer_id,
        DealerDeliveryPointEntity.is_active == True,  # noqa: E712
    ).order_by(DealerDeliveryPointEntity.is_default.desc(), DealerDeliveryPointEntity.id)
    pts = list(db.scalars(q).all())
    if not pts and dealer and getattr(dealer, "address", None):
        clean_phone = (getattr(dealer, "phone", None) or "0987654321").replace(" ", "").replace(".", "").replace("-", "")
        if not re.fullmatch(r"^\d{10}$", clean_phone):
            clean_phone = "0987654321"
        default_pt = DealerDeliveryPointEntity(
            dealer_id=dealer_id,
            label="Địa chỉ đăng ký đại lý",
            address=dealer.address,
            receiver_name=dealer.name,
            receiver_phone=clean_phone,
            route_note="Địa chỉ chính thức của đại lý",
            is_default=True,
            is_active=True,
        )
        db.add(default_pt)
        _sync_to_master(db, default_pt.label, default_pt.address, default_pt.receiver_name, default_pt.receiver_phone, default_pt.route_note)
        db.commit()
        db.refresh(default_pt)
        pts.append(default_pt)
    elif pts and dealer and not getattr(dealer, "address", None):
        # Đồng bộ ngược: nếu đại lý chưa có địa chỉ nhưng đã có điểm giao hàng mặc định, cập nhật địa chỉ đại lý
        def_pt = next((p for p in pts if p.is_default), pts[0])
        dealer.address = def_pt.address
        if not getattr(dealer, "phone", None) and def_pt.receiver_phone:
            dealer.phone = def_pt.receiver_phone
        try:
            from app.models.dealer import save_dealers_db
            save_dealers_db()
        except Exception:
            pass
    return pts


@router.post("", response_model=DeliveryPointOut, status_code=201)
def create_point(dealer_id: int, body: DeliveryPointCreate,
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    _check_access(db, dealer_id, user)
    phone_clean = (body.receiver_phone or "").strip()
    if not phone_clean:
        raise HTTPException(400, "Vui lòng nhập số điện thoại người nhận.")
    if not re.fullmatch(r"^\d{10}$", phone_clean):
        raise HTTPException(400, "Số điện thoại người nhận phải bao gồm đúng 10 chữ số.")
    has_any = db.scalars(select(DealerDeliveryPointEntity.id).where(
        DealerDeliveryPointEntity.dealer_id == dealer_id,
        DealerDeliveryPointEntity.is_active == True)).first()  # noqa: E712
    data = body.model_dump()
    data["receiver_phone"] = phone_clean
    if not has_any:
        data["is_default"] = True          # điểm đầu tiên tự là mặc định
    if data["is_default"]:
        _clear_default(db, dealer_id)
    point = DealerDeliveryPointEntity(dealer_id=dealer_id, **data)
    db.add(point)
    _sync_to_master(db, point.label, point.address, point.receiver_name, point.receiver_phone, point.route_note)
    db.commit()
    db.refresh(point)
    return point


@router.put("/{point_id}", response_model=DeliveryPointOut)
def update_point(dealer_id: int, point_id: int, body: DeliveryPointUpdate,
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    _check_access(db, dealer_id, user)
    point = db.get(DealerDeliveryPointEntity, point_id)
    if not point or point.dealer_id != dealer_id or not point.is_active:
        raise HTTPException(404, "Không tìm thấy điểm giao")
    old_label = point.label
    old_address = point.address
    if body.receiver_phone is not None and body.receiver_phone.strip():
        phone_clean = re.sub(r"\D", "", body.receiver_phone.strip())
        if len(phone_clean) > 10:
            phone_clean = phone_clean[:10]
        body.receiver_phone = phone_clean
    data = body.model_dump()
    if data["is_default"] and not point.is_default:
        _clear_default(db, dealer_id)
    for k, v in data.items():
        setattr(point, k, v)

    master_item = db.scalars(
        select(MasterDeliveryPointEntity).where(
            MasterDeliveryPointEntity.label == old_label,
            MasterDeliveryPointEntity.address == old_address,
            MasterDeliveryPointEntity.is_active == True,
        )
    ).first()
    if master_item:
        master_item.label = point.label
        master_item.address = point.address
        master_item.receiver_name = point.receiver_name or ""
        master_item.receiver_phone = point.receiver_phone or ""
        master_item.route_note = point.route_note or ""
    else:
        _sync_to_master(db, point.label, point.address, point.receiver_name, point.receiver_phone, point.route_note)

    db.commit()
    db.refresh(point)
    if point.is_default and point.address:
        dealer = DEALERS_DB.get(dealer_id)
        if dealer:
            dealer.address = point.address
            if point.receiver_phone:
                dealer.phone = point.receiver_phone
            try:
                from app.models.dealer import save_dealers_db
                save_dealers_db()
            except Exception:
                pass
    return point


@router.post("/{point_id}/set-default", response_model=DeliveryPointOut)
def set_default(dealer_id: int, point_id: int,
                db: Session = Depends(get_db), user=Depends(get_current_user)):
    _check_access(db, dealer_id, user)
    point = db.get(DealerDeliveryPointEntity, point_id)
    if not point or point.dealer_id != dealer_id or not point.is_active:
        raise HTTPException(404, "Không tìm thấy điểm giao")
    _clear_default(db, dealer_id)
    point.is_default = True
    db.commit()
    db.refresh(point)
    dealer = DEALERS_DB.get(dealer_id)
    if dealer and point.address:
        dealer.address = point.address
        if point.receiver_phone:
            dealer.phone = point.receiver_phone
        try:
            from app.models.dealer import save_dealers_db
            save_dealers_db()
        except Exception:
            pass
    return point


@router.delete("/{point_id}", status_code=204)
def delete_point(dealer_id: int, point_id: int,
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    _check_access(db, dealer_id, user)
    point = db.get(DealerDeliveryPointEntity, point_id)
    if not point or point.dealer_id != dealer_id or not point.is_active:
        raise HTTPException(404, "Không tìm thấy điểm giao")

    used = any(o.get("delivery_point_id") == point_id for o in ORDERS_DB.values())
    was_default = point.is_default
    if used:
        point.is_active = False      # đã có đơn dùng -> ẩn đi, không xóa cứng
        point.is_default = False
    else:
        db.delete(point)
    db.flush()

    if was_default:                  # xóa điểm mặc định -> đẩy điểm khác lên thay
        nxt = db.scalars(select(DealerDeliveryPointEntity).where(
            DealerDeliveryPointEntity.dealer_id == dealer_id,
            DealerDeliveryPointEntity.is_active == True)  # noqa: E712
            .order_by(DealerDeliveryPointEntity.id)).first()
        if nxt:
            nxt.is_default = True
