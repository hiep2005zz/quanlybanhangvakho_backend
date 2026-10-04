from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.dealer import DEALERS_DB
from app.models.entities import DealerDeliveryPointEntity
from app.api.v1.endpoints.orders import ORDERS_DB
from app.schemas.delivery_point import (
    DeliveryPointCreate, DeliveryPointUpdate, DeliveryPointOut,
)

router = APIRouter(prefix="/{dealer_id}/delivery-points")
dealers_router = APIRouter()


@dealers_router.get("")
def list_dealers(user=Depends(get_current_user)):
    """Lấy danh sách các đại lý mà người dùng hiện tại có quyền truy cập."""
    roles = getattr(user, "roles", None) or [getattr(user, "role", None)]
    roles = {str(r).lower() for r in roles if r}
    if roles & {"admin", "sales_manager"}:
        dealers = list(DEALERS_DB.values())
    elif "sales" in roles:
        dealers = [d for d in DEALERS_DB.values() if d.assigned_sale_id == user.id]
        if not dealers:
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


def _check_access(db: Session, dealer_id: int, user):
    dealer = next((d for d in DEALERS_DB.values() if getattr(d, "id", None) == dealer_id), None)
    if not dealer:
        raise HTTPException(404, "Không tìm thấy đại lý")

    roles = getattr(user, "roles", None) or [getattr(user, "role", None)]
    roles = {str(r).lower() for r in roles if r}
    if roles & {"admin", "sales_manager"}:
        return dealer
    if "sales" in roles and dealer.assigned_sale_id == user.id:
        return dealer
    raise HTTPException(403, "Bạn không phụ trách đại lý này")


def _clear_default(db: Session, dealer_id: int):
    db.execute(
        update(DealerDeliveryPointEntity)
        .where(DealerDeliveryPointEntity.dealer_id == dealer_id,
               DealerDeliveryPointEntity.is_default == True)  # noqa: E712
        .values(is_default=False)
    )
    db.flush()


@router.get("", response_model=list[DeliveryPointOut])
def list_points(dealer_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _check_access(db, dealer_id, user)
    q = select(DealerDeliveryPointEntity).where(
        DealerDeliveryPointEntity.dealer_id == dealer_id,
        DealerDeliveryPointEntity.is_active == True,  # noqa: E712
    ).order_by(DealerDeliveryPointEntity.is_default.desc(), DealerDeliveryPointEntity.id)
    return db.scalars(q).all()


@router.post("", response_model=DeliveryPointOut, status_code=201)
def create_point(dealer_id: int, body: DeliveryPointCreate,
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    _check_access(db, dealer_id, user)
    has_any = db.scalars(select(DealerDeliveryPointEntity.id).where(
        DealerDeliveryPointEntity.dealer_id == dealer_id,
        DealerDeliveryPointEntity.is_active == True)).first()  # noqa: E712
    data = body.model_dump()
    if not has_any:
        data["is_default"] = True          # điểm đầu tiên tự là mặc định
    if data["is_default"]:
        _clear_default(db, dealer_id)
    point = DealerDeliveryPointEntity(dealer_id=dealer_id, **data)
    db.add(point)
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
    data = body.model_dump()
    if data["is_default"] and not point.is_default:
        _clear_default(db, dealer_id)
    for k, v in data.items():
        setattr(point, k, v)
    db.commit()
    db.refresh(point)
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
    db.commit()