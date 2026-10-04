# backend/app/api/v1/endpoints/orders.py
"""
Order Management Endpoint:
AC 3: Kiểm tra nhân viên phụ trách của Đại lý đó.
Nếu tài khoản nhân viên đang ở trạng thái LOCKED, từ chối tạo đơn và báo lỗi:
"Đại lý này thuộc nhân viên đã bị khóa tài khoản, vui lòng bàn giao trước khi lên đơn".
"""
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_permission, require_roles
from app.core.database import get_db
from app.core.rbac import Permission, Role
from app.schemas.auth import UserResponse
from app.models.dealer import DEALERS_DB, save_dealers_db
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
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
    requires_approval: Optional[bool] = False
    approval_reason: Optional[str] = None
    items: Optional[List[dict]] = None
    created_at: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None

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

    # AC: Kiểm tra bảng giá áp dụng cho nhóm khách hàng của Đại lý
    cust_group = getattr(dealer, "customer_group", None) or "Dai_ly_cap_1"
    aliases = [cust_group]
    if cust_group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1"]:
        aliases = ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1"]
    elif cust_group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2"]:
        aliases = ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2"]
    elif cust_group in ["RETAIL", "Khach_le", "Khách lẻ"]:
        aliases = ["Khach_le", "RETAIL", "Khách lẻ"]

    requires_approval = False
    approval_reasons = []
    now_dt = datetime.now(timezone.utc)
    if db:
        # 1. Tìm và khóa các bảng giá đang hiệu lực của nhóm khách hàng
        active_pbs = db.query(PriceBookEntity).filter(
            PriceBookEntity.customer_group.in_(aliases),
            PriceBookEntity.status == "ACTIVE",
            PriceBookEntity.valid_from <= now_dt,
            PriceBookEntity.valid_to >= now_dt
        ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).all()

        for pb in active_pbs:
            pb.is_locked = True

        # 2. Kiểm tra đơn giá thực tế của từng sản phẩm so với floor_price
        for it in data.items:
            # Tra cứu chính xác dòng sản phẩm trong bảng giá hiệu lực
            item_match = db.query(PriceBookItemEntity, PriceBookEntity).join(
                PriceBookEntity, PriceBookItemEntity.price_book_id == PriceBookEntity.id
            ).filter(
                PriceBookEntity.customer_group.in_(aliases),
                PriceBookEntity.status == "ACTIVE",
                PriceBookEntity.valid_from <= now_dt,
                PriceBookEntity.valid_to >= now_dt,
                PriceBookItemEntity.product_id == it.product_id
            ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

            if item_match:
                pbi, pb_matched = item_match
                pb_matched.is_locked = True
                fl_val = pbi.floor_price if pbi.floor_price is not None else pbi.min_price
                if fl_val is not None and it.price < fl_val:
                    requires_approval = True
                    prod_name = next((pi["product_name"] for pi in processed_items if pi["product_id"] == it.product_id), f"SP #{it.product_id}")
                    approval_reasons.append(
                        f"{prod_name}: Đơn giá bán {it.price:,.0f} đ thấp hơn giá sàn {fl_val:,.0f} đ (Bảng giá: {pb_matched.name})"
                    )

    if db:
        db.commit()

    order_id = NEXT_ORDER_ID
    NEXT_ORDER_ID += 1
    order_code = f"ORD{order_id:05d}"
    now_str = datetime.now(timezone.utc).isoformat()
    order_status = "PENDING_APPROVAL" if requires_approval else "CONFIRMED"
    approval_reason = " | ".join(approval_reasons) if approval_reasons else None

    order_record = {
        "id": order_id,
        "order_code": order_code,
        "dealer_id": dealer.id,
        "dealer_name": dealer.name,
        "created_by": current_user.username,
        "assigned_sale_id": assigned_sale_id,
        "assigned_sale_name": assigned_user.full_name if assigned_user else None,
        "total_amount": total_amount,
        "status": order_status,
        "requires_approval": requires_approval,
        "approval_reason": approval_reason,
        "items": processed_items,
        "created_at": now_str,
    }
    ORDERS_DB[order_id] = order_record

    return OrderResponse(**order_record)


@router.post("/{order_code}/approve", response_model=OrderResponse)
def approve_order(
    order_code: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    """
    Quyền duyệt đơn khi bán dưới giá sàn: CHỈ sales_manager hoặc admin.
    Chuyển đơn từ PENDING_APPROVAL sang CONFIRMED.
    Hỗ trợ tra cứu theo cả order_code (ORD00001) lẫn order id (1).
    """
    target = None
    for o in ORDERS_DB.values():
        if o["order_code"].upper() == order_code.upper() or str(o.get("id")) == str(order_code):
            target = o
            break

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã hoặc ID {order_code}."
        )

    old_status = target.get("status")
    target["status"] = "CONFIRMED"
    target["requires_approval"] = False
    target["approved_by"] = current_user.username
    target["approved_at"] = datetime.now(timezone.utc).isoformat()

    log_audit_event(
        db=db,
        user=current_user,
        action_type="ORDER_APPROVE",
        entity_type="Order",
        entity_id=target["order_code"],
        old_val={"status": old_status},
        new_val={"status": "CONFIRMED"},
        reason="Duyệt đơn hàng bán dưới giá sàn",
        request=request,
    )

    return OrderResponse(**target)


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

