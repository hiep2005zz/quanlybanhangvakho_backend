# backend/app/api/v1/endpoints/orders.py
"""
Order Management Endpoint:
AC 3: Kiểm tra nhân viên phụ trách của Đại lý đó.
Nếu tài khoản nhân viên đang ở trạng thái LOCKED, từ chối tạo đơn và báo lỗi:
"Đại lý này thuộc nhân viên đã bị khóa tài khoản, vui lòng bàn giao trước khi lên đơn".
"""

from fastapi import HTTPException
from app.models.entities import DealerDeliveryPointEntity
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_permission
from app.core.database import get_db
from app.core.rbac import Permission
from app.schemas.auth import UserResponse
from app.models.dealer import DEALERS_DB, save_dealers_db
from app.models.user import USERS_DB
from app.services.audit_service import log_audit_event

router = APIRouter()

class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    price: float = Field(..., ge=0)
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

class OrderCreate(BaseModel):
    dealer_id: int
    items: List[OrderItemCreate]
    note: Optional[str] = None
    delivery_point_id: int | None = None

class OrderResponse(BaseModel):
    id: int
    order_code: str
    dealer_id: int
    dealer_name: str
    created_by: str
    assigned_sale_id: Optional[int] = None
    assigned_sale_name: Optional[str] = None
    total_amount: float
    status: str
    items: Optional[List[dict]] = None
    created_at: str

class InvoiceEditRequest(BaseModel):
    note: Optional[str] = None
    status: Optional[str] = None  # e.g. CANCELLED, EDITED
    reason: str = Field(..., min_length=2, max_length=255)

class DebtLimitUpdateRequest(BaseModel):
    credit_limit: float = Field(..., ge=0)
    reason: str = Field(..., min_length=2, max_length=255)

# Mock orders storage
ORDERS_DB: dict[int, dict] = {
    1: {
        "id": 1,
        "order_code": "ORD00001",
        "dealer_id": 1,
        "dealer_name": "Đại Lý Phân Phối Miền Bắc - Sao Mai",
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Trần Bán Hàng",
        "total_amount": 1990000.0,
        "status": "CONFIRMED",
        "created_at": "2026-09-28T09:00:00Z",
    }
}
NEXT_ORDER_ID = 2

@router.get("", response_model=List[OrderResponse])
def get_orders(
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value))
):
    """Lấy danh sách đơn hàng / hóa đơn."""
    return [OrderResponse(**o) for o in sorted(ORDERS_DB.values(), key=lambda x: x["id"], reverse=True)]

@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_WRITE.value))
):
    global NEXT_ORDER_ID
    # 1. Tìm thông tin đại lý
    dealer = DEALERS_DB.get(data.dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {data.dealer_id}."
        )
        selected_delivery_point_id = None

    if data.delivery_point_id is not None:
        # Tìm điểm giao hàng theo ID
        dp = db.get(DealerDeliveryPointEntity, data.delivery_point_id)
        if not dp or not dp.is_active or dp.dealer_id != data.dealer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Điểm giao hàng không hợp lệ hoặc không thuộc đại lý này."
            )
        selected_delivery_point_id = dp.id
    else:
        # Nếu không chọn, tự động tìm điểm mặc định của đại lý
        from sqlalchemy import select
        dp = db.scalars(
            select(DealerDeliveryPointEntity).where(
                DealerDeliveryPointEntity.dealer_id == data.dealer_id,
                DealerDeliveryPointEntity.is_default == True,
                DealerDeliveryPointEntity.is_active == True
            )
        ).first()
        if dp:
            selected_delivery_point_id = dp.id

    # 2. AC 3: Kiểm tra nhân viên phụ trách của đại lý
    assigned_sale_id = dealer.assigned_sale_id
    assigned_user = None
    if assigned_sale_id:
        for u in USERS_DB.values():
            if u.id == assigned_sale_id:
                assigned_user = u
                break

    # Nếu nhân viên phụ trách bị KHÓA (LOCKED hoặc is_active = False):
    if assigned_user and (not assigned_user.is_active or getattr(assigned_user, "status", "ACTIVE") == "LOCKED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đại lý này thuộc nhân viên đã bị khóa tài khoản, vui lòng bàn giao trước khi lên đơn"
        )

    # 3. Tạo đơn hàng và tính base_quantity
    from app.api.v1.endpoints.products import RAW_PRODUCTS, _find_product_in_raw
    from app.models.entities import ProductEntity

    processed_items = []
    total_amount = 0.0

    for item in data.items:
        # Tìm thông tin sản phẩm để lấy đơn vị cơ sở và hệ số quy đổi mặc định nếu chưa truyền
        raw_p = _find_product_in_raw(item.product_id)
        prod_entity = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).first() if db else None

        base_unit = "Cái"
        units_list = []
        if raw_p:
            base_unit = raw_p.get("base_unit", "Cái")
            units_list = raw_p.get("units", [])
        elif prod_entity:
            base_unit = prod_entity.base_unit or "Cái"
            units_list = prod_entity.units or []

        chosen_unit = item.unit_name or base_unit
        chosen_rate = item.conversion_rate

        if chosen_rate is None or chosen_rate <= 0:
            if chosen_unit == base_unit:
                chosen_rate = 1.0
            else:
                matched = next((u for u in units_list if u.get("unit_name") == chosen_unit), None)
                chosen_rate = float(matched.get("conversion_rate", 1.0)) if matched else 1.0

        base_quantity = int(round(item.quantity * chosen_rate))
        total_amount += item.quantity * item.price

        # Cập nhật trừ tồn kho theo base_quantity nếu có sản phẩm
        if raw_p:
            raw_p["stock"] = max(0, raw_p.get("stock", 0) - base_quantity)
        if prod_entity:
            prod_entity.stock = max(0, (prod_entity.stock or 0) - base_quantity)

        processed_items.append({
            "product_id": item.product_id,
            "product_name": raw_p.get("name") if raw_p else (prod_entity.name if prod_entity else f"SP #{item.product_id}"),
            "quantity": item.quantity,
            "price": item.price,
            "unit_name": chosen_unit,
            "conversion_rate": chosen_rate,
            "base_quantity": base_quantity,
        })

    if db:
        db.commit()

    order_id = NEXT_ORDER_ID
    NEXT_ORDER_ID += 1
    order_code = f"ORD{order_id:05d}"
    now_str = datetime.now(timezone.utc).isoformat()

    order_record = {
        "id": order_id,
        "order_code": order_code,
        "delivery_point_id": selected_delivery_point_id,
        "dealer_id": dealer.id,
        "dealer_name": dealer.name,
        "created_by": current_user.username,
        "assigned_sale_id": assigned_sale_id,
        "assigned_sale_name": assigned_user.full_name if assigned_user else None,
        "total_amount": total_amount,
        "status": "CONFIRMED",
        "items": processed_items,
        "created_at": now_str,
    }
    ORDERS_DB[order_id] = order_record

    return OrderResponse(**order_record)


@router.put("/{order_code}")
def edit_or_cancel_invoice(
    order_code: str,
    data: InvoiceEditRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_WRITE.value))
):
    """
    Sửa đổi hoặc hủy hóa đơn/đơn hàng.
    Ghi vết vào bảng audit_logs với action_type='INVOICE_EDIT'.
    """
    target = None
    for o in ORDERS_DB.values():
        if o["order_code"].upper() == order_code.upper():
            target = o
            break

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy hóa đơn/đơn hàng có mã {order_code}."
        )

    old_val = {"status": target["status"], "note": target.get("note")}
    new_val = {}

    if data.status:
        target["status"] = data.status.upper()
        new_val["status"] = target["status"]
    if data.note is not None:
        target["note"] = data.note
        new_val["note"] = data.note

    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVOICE_EDIT",
        entity_type="Invoice",
        entity_id=order_code.upper(),
        old_val=old_val,
        new_val=new_val,
        reason=data.reason,
        request=request,
    )

    return {
        "status": "success",
        "message": f"Đã cập nhật hóa đơn {order_code} thành công.",
        "order": target
    }


@router.put("/dealers/{dealer_id}/debt-limit")
def update_customer_debt_limit(
    dealer_id: int,
    data: DebtLimitUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Cập nhật hạn mức công nợ khách hàng / đại lý.
    Ghi vết vào bảng audit_logs với action_type='DEBT_LIMIT_CHANGE'.
    """
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy khách hàng/đại lý có ID {dealer_id}."
        )

    old_limit = getattr(dealer, "credit_limit", 50000000.0)
    dealer.credit_limit = data.credit_limit
    save_dealers_db()

    log_audit_event(
        db=db,
        user=current_user,
        action_type="DEBT_LIMIT_CHANGE",
        entity_type="CustomerDebt",
        entity_id=dealer.code,
        old_val={"credit_limit": old_limit, "customer_name": dealer.name},
        new_val={"credit_limit": data.credit_limit},
        reason=data.reason,
        request=request,
    )

    return {
        "status": "success",
        "message": f"Đã cập nhật hạn mức công nợ cho {dealer.name} thành {data.credit_limit:,.0f} đ.",
        "dealer": dealer
    }

