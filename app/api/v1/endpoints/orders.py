# backend/app/api/v1/endpoints/orders.py
from __future__ import annotations
"""
Order Management Endpoint:
AC 3: Kiểm tra nhân viên phụ trách của Đại lý đó.
Nếu tài khoản nhân viên đang ở trạng thái LOCKED, từ chối tạo đơn và báo lỗi:
"Đại lý này thuộc nhân viên đã bị khóa tài khoản, vui lòng bàn giao trước khi lên đơn".
"""
import json
from uuid import uuid4
from app.models.entities import DealerDeliveryPointEntity
from typing import List, Optional
from datetime import date, datetime, timezone
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_permission, require_roles, require_sales_or_admin_role
from app.core.database import get_db
from app.core.rbac import Permission, Role
from app.schemas.auth import UserResponse
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.models.dealer import DEALERS_DB, save_dealers_db, load_dealers_db
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import OrderEntity

from app.models.user import USERS_DB
from app.services.audit_service import log_audit_event
from app.api.v1.endpoints.discounts import resolve_best_discount

router = APIRouter()

class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    price: float = Field(..., ge=0)
    unit: Optional[str] = None
    unit_name: Optional[str] = None
    conversion_rate: Optional[float] = Field(None, gt=0)

SALES_ORDER_UNITS = {"Cái", "Hộp", "Thùng", "Bộ", "Đôi"}

class OrderCreate(BaseModel):
    dealer_id: int
    items: List[OrderItemCreate]
    note: Optional[str] = None
    delivery_point_id: Optional[int] = None
    delivery_point: Optional[str] = Field(default=None, max_length=500)
    desired_delivery_date: Optional[date] = None
    discount_percent: float = Field(default=0, ge=0, le=100)
    discount_rate: Optional[float] = Field(default=None, ge=0, le=100)

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
    subtotal_amount: Optional[float] = 0.0
    discount_percent: float = 0.0
    discount_rate: Optional[float] = 0.0
    discount_amount: float = 0.0
    dealer_status: Optional[str] = "Đang hoạt động"
    dealer_lock_reason: Optional[str] = None

class SalesOrderResponse(OrderResponse):
    subtotal_amount: float = 0
    discount_percent: float = 0
    discount_rate: Optional[float] = 0
    discount_amount: float = 0
    delivery_point: Optional[str] = None
    delivery_point_id: Optional[int] = None
    desired_delivery_date: Optional[str] = None
    note: Optional[str] = None
    items: List[dict] = Field(default_factory=list)

class InvoiceEditRequest(BaseModel):
    note: Optional[str] = None
    status: Optional[str] = None  # e.g. CANCELLED, EDITED
    reason: str = Field(..., min_length=2, max_length=255)

class DebtLimitUpdateRequest(BaseModel):
    credit_limit: float = Field(..., ge=0)
    max_debt_days: int = Field(default=30, ge=0)
    reason: str = Field(..., min_length=2, max_length=255)

# Orders created through the API are stored in the database and this process-local cache.
ORDERS_DB: dict[int, dict] = {}
NEXT_ORDER_ID = 1

def _allocate_order_id(db: Session) -> int:
    global NEXT_ORDER_ID
    max_stored_id = db.query(func.max(OrderEntity.id)).scalar() or 0
    order_id = max(
        NEXT_ORDER_ID,
        max(ORDERS_DB.keys(), default=0) + 1,
        max_stored_id + 1,
    )
    NEXT_ORDER_ID = order_id + 1
    return order_id

def _is_dealer_locked(d) -> bool:
    if not d:
        return False
    st = getattr(d, "status", "Đang hoạt động") or "Đang hoạt động"
    st_str = str(st).lower()
    return (
        st in ["Đã khóa", "LOCKED"]
        or "khóa" in st_str
        or "lock" in st_str
    )

class DealerCreditInfoResponse(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    customer_group: Optional[str] = None
    status: Optional[str] = "Đang hoạt động"
    credit_limit: float
    overdue_days_allowed: int
    max_debt_days: int
    current_debt: float
    remaining_credit: float
    max_debt_age: int
    is_overdue: bool
    is_over_limit: bool

def calculate_dealer_debt_summary(dealer, orders_db: dict) -> dict:
    """
    Tính toán chi tiết công nợ hiện tại, tuổi nợ và trạng thái nợ quá hạn của đại lý.
    """
    current_debt = 0.0
    max_debt_age = 0
    now = datetime.now(timezone.utc)
    for o in orders_db.values():
        if o.get("dealer_id") == dealer.id and o.get("status") not in ("PAID", "CANCELLED"):
            current_debt += float(o.get("total_amount", 0.0) or 0.0)
            created_at_val = o.get("created_at")
            if created_at_val:
                try:
                    if isinstance(created_at_val, str):
                        order_date = datetime.fromisoformat(created_at_val.replace("Z", "+00:00"))
                    elif isinstance(created_at_val, datetime):
                        order_date = created_at_val if created_at_val.tzinfo else created_at_val.replace(tzinfo=timezone.utc)
                    else:
                        order_date = now
                    days_old = max(0, (now - order_date).days)
                    if days_old > max_debt_age:
                        max_debt_age = days_old
                except Exception:
                    pass

    credit_limit = float(getattr(dealer, "credit_limit", 50000000.0) or 50000000.0)
    allowed_days = int(getattr(dealer, "overdue_days_allowed", getattr(dealer, "max_debt_days", 30)) or 30)
    remaining_credit = max(0.0, credit_limit - current_debt)
    is_overdue = max_debt_age > allowed_days
    is_over_limit = current_debt > credit_limit

    return {
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "phone": getattr(dealer, "phone", None),
        "address": getattr(dealer, "address", None),
        "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
        "status": getattr(dealer, "status", "Đang hoạt động"),
        "credit_limit": credit_limit,
        "overdue_days_allowed": allowed_days,
        "max_debt_days": allowed_days,
        "current_debt": current_debt,
        "remaining_credit": remaining_credit,
        "max_debt_age": max_debt_age,
        "is_overdue": is_overdue,
        "is_over_limit": is_over_limit,
    }

def _get_customer_dealer(current_user: UserResponse):
    customer_dealer = DEALERS_DB.get(current_user.id)
    if not customer_dealer:
        for d in DEALERS_DB.values():
            if (d.email and d.email == current_user.email) or \
               (d.phone and d.phone == current_user.phone) or \
               (d.code and d.code.lower() == current_user.username.lower()) or \
               (d.name and d.name.lower() == (current_user.full_name or "").lower()):
                customer_dealer = d
                break
    return customer_dealer

@router.get("/dealers")
def get_order_dealers(
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_WRITE.value))
):
    """Return dealers available for order entry, restricted to the assigned salesperson or customer."""
    load_dealers_db()
    dealers = list(DEALERS_DB.values())
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    if is_customer or current_user.role == "customer":
        customer_dealer = _get_customer_dealer(current_user)
        dealers = [customer_dealer] if customer_dealer else []
    elif current_user.role == "sales" or ("sales" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager"])):
        assigned_user = USERS_DB.get(current_user.username) or next((u for u in USERS_DB.values() if u.id == current_user.id), None)
        if not assigned_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy nhân viên kinh doanh hiện tại.",
            )
        dealers = [dealer for dealer in dealers if dealer.assigned_sale_id == assigned_user.id]

    res = []
    for dealer in sorted(dealers, key=lambda item: item.name.lower()):
        debt_info = calculate_dealer_debt_summary(dealer, ORDERS_DB)
        res.append({
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "phone": dealer.phone,
            "address": dealer.address,
            "status": getattr(dealer, "status", "Đang hoạt động"),
            "lock_reason": getattr(dealer, "lock_reason", None),
            "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
            "credit_limit": debt_info["credit_limit"],
            "overdue_days_allowed": debt_info["overdue_days_allowed"],
            "max_debt_days": debt_info["max_debt_days"],
            "current_debt": debt_info["current_debt"],
            "remaining_credit": debt_info["remaining_credit"],
            "max_debt_age": debt_info["max_debt_age"],
            "is_overdue": debt_info["is_overdue"],
            "is_over_limit": debt_info["is_over_limit"],
        })
    return res

@router.get("/dealers/{dealer_id}/credit-info", response_model=DealerCreditInfoResponse)
def get_order_dealer_credit_info(
    dealer_id: int,
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "sales", "customer"]))
):
    """
    AC 4 & AC 1: API lấy thông tin công nợ chi tiết của Đại lý phục vụ kiểm soát khi lên đơn.
    Bảo vệ quyền truy cập: Chỉ cho phép Nhân viên kinh doanh (Sales), Quản trị hệ thống (Admin).
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )
    if is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        if not customer_dealer or customer_dealer.id != dealer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xem thông tin công nợ của chính mình."
            )
    return calculate_dealer_debt_summary(dealer, ORDERS_DB)

@router.get("", response_model=List[OrderResponse])
def get_orders(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value))
):
    """Lấy danh sách đơn hàng / hóa đơn."""
    load_dealers_db()
    orders_by_code: dict[str, OrderResponse] = {}
    for order in ORDERS_DB.values():
        try:
            orders_by_code[order["order_code"]] = OrderResponse(**order)
        except Exception:
            pass

    for order in db.query(OrderEntity).order_by(OrderEntity.id.desc()).all():
        items = []
        delivery_point = None
        desired_delivery_date = None
        subtotal_amount = order.total_amount
        discount_percent = 0.0
        discount_amount = 0.0

        if order.items_json:
            try:
                payload = json.loads(order.items_json)
                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    items = payload.get("items", [])
                    delivery_point = payload.get("delivery_point")
                    desired_delivery_date = payload.get("desired_delivery_date")
                    subtotal_amount = payload.get("subtotal_amount", order.total_amount)
                    discount_percent = payload.get("discount_percent", 0.0)
                    discount_amount = payload.get("discount_amount", 0.0)
            except Exception:
                pass

        from app.api.v1.endpoints.products import _find_product_in_raw
        for item in items:
            if isinstance(item, dict) and not item.get("product_name"):
                p = _find_product_in_raw(item.get("product_id"))
                if p:
                    item["product_name"] = p.get("name")
                    item.setdefault("product_code", p.get("code"))
                else:
                    item["product_name"] = f"SP #{item.get('product_id', '')}"

        if not delivery_point and order.delivery_point_id:
            dp_obj = db.get(DealerDeliveryPointEntity, order.delivery_point_id)
            if dp_obj:
                delivery_point = f"{dp_obj.label} — {dp_obj.address}"

        if not delivery_point:
            d_obj = DEALERS_DB.get(order.dealer_id)
            if d_obj and getattr(d_obj, "address", None):
                delivery_point = f"Địa chỉ đại lý — {d_obj.address}"

        if order.order_code not in orders_by_code:
            disc_rate = getattr(order, "discount_rate", 0.0) or 0.0
            disc_amt = getattr(order, "discount_amount", 0.0) or 0.0
            sub_amt = (order.total_amount or 0.0) + disc_amt
            items_parsed = None
            appr_reason = None
            req_appr = False
            appr_by = None
            appr_at = None
            if order.items_json:
                try:
                    ij = json.loads(order.items_json)
                    items_parsed = ij.get("items", [])
                    disc_rate = ij.get("discount_percent", ij.get("discount_rate", disc_rate))
                    disc_amt = ij.get("discount_amount", disc_amt)
                    sub_amt = ij.get("subtotal_amount", sub_amt)
                    appr_reason = ij.get("approval_reason")
                    req_appr = ij.get("requires_approval", False)
                    appr_by = ij.get("approved_by")
                    appr_at = ij.get("approved_at")
                except Exception:
                    pass

            in_mem = ORDERS_DB.get(order.id, {})
            if not appr_reason and in_mem:
                appr_reason = in_mem.get("approval_reason")
                req_appr = in_mem.get("requires_approval", req_appr)
                appr_by = in_mem.get("approved_by", appr_by)
                appr_at = in_mem.get("approved_at", appr_at)

            if order.status in ("PENDING_APPROVAL", "PENDING"):
                req_appr = True
                if not appr_reason:
                    if disc_rate > 0:
                        appr_reason = f"Chiết khấu ({disc_rate}%) vượt hạn mức chính sách cần duyệt"
                    else:
                        appr_reason = "Bán dưới giá sàn cần quản lý duyệt"

            resp_item = OrderResponse(
                id=order.id,
                order_code=order.order_code,
                dealer_id=order.dealer_id,
                dealer_name=order.dealer_name,
                created_by=order.created_by,
                assigned_sale_id=order.assigned_sale_id,
                assigned_sale_name=order.assigned_sale_name,
                total_amount=order.total_amount,
                subtotal_amount=sub_amt,
                discount_percent=disc_rate,
                discount_rate=disc_rate,
                discount_amount=disc_amt,
                status=order.status,
                requires_approval=req_appr,
                approval_reason=appr_reason,
                approved_by=appr_by,
                approved_at=appr_at,
                items=items_parsed or items,
                created_at=order.created_at.isoformat() if order.created_at else "",
            )
            orders_by_code[order.order_code] = resp_item

            ORDERS_DB[order.id] = {
                "id": order.id,
                "order_code": order.order_code,
                "dealer_id": order.dealer_id,
                "dealer_name": order.dealer_name,
                "created_by": order.created_by,
                "assigned_sale_id": order.assigned_sale_id,
                "assigned_sale_name": order.assigned_sale_name,
                "total_amount": order.total_amount,
                "status": order.status,
                "requires_approval": req_appr,
                "approval_reason": appr_reason,
                "approved_by": appr_by,
                "approved_at": appr_at,
                "created_at": order.created_at.isoformat() if order.created_at else "",
                "items": items,
                "delivery_point_id": order.delivery_point_id,
                "delivery_point": delivery_point,
                "desired_delivery_date": desired_delivery_date,
                "note": order.note,
                "subtotal_amount": subtotal_amount,
                "discount_percent": discount_percent,
                "discount_amount": discount_amount,
            }
        else:
            existing = orders_by_code[order.order_code]
            if (not existing.items or len(existing.items) == 0) and items:
                existing.items = items

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    for resp in orders_by_code.values():
        d = DEALERS_DB.get(resp.dealer_id)
        if d:
            resp.dealer_status = getattr(d, "status", "Đang hoạt động")
            resp.dealer_lock_reason = None if is_customer else getattr(d, "lock_reason", None)
        else:
            resp.dealer_status = "Đang hoạt động"
            resp.dealer_lock_reason = None

    def _order_sort_key(order: OrderResponse):
        is_pending = 1 if getattr(order, "status", "") in ("PENDING_APPROVAL", "PENDING") else 0
        return (is_pending, getattr(order, "id", 0))

    return sorted(orders_by_code.values(), key=_order_sort_key, reverse=True)

@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_sales_or_admin_role)
):
    global NEXT_ORDER_ID
    load_dealers_db()

    # 1. Tìm thông tin đại lý
    dealer = DEALERS_DB.get(data.dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {data.dealer_id}."
        )

    # Lỗi tạo đơn chéo / tài khoản đại lý bị khóa:
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    if is_customer or current_user.role == "customer":
        customer_dealer = _get_customer_dealer(current_user)
        if customer_dealer:
            if _is_dealer_locked(customer_dealer):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Đại lý '{customer_dealer.name}' hiện đang bị KHÓA giao dịch. Không thể tạo đơn hàng mới."
                )
            if dealer.id != customer_dealer.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tài khoản đại lý chỉ được phép tạo đơn hàng cho chính mình."
                )
        else:
            is_own = (dealer.id == current_user.id) or \
                     (dealer.email and dealer.email == current_user.email) or \
                     (dealer.phone and dealer.phone == current_user.phone) or \
                     (dealer.code and dealer.code.lower() == current_user.username.lower())
            if not is_own:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tài khoản đại lý chỉ được phép tạo đơn hàng cho chính mình."
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

    # 1. AC 3: Kiểm tra nhân viên phụ trách của đại lý
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

    # 2. Kiểm tra trạng thái khóa giao dịch của chính Đại lý (TRỤ CỘT 2)
    if _is_dealer_locked(dealer):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đại lý '{dealer.name}' hiện đang bị KHÓA giao dịch. Không thể tạo đơn hàng mới."
        )

    # 3. Kiểm tra hạn mức công nợ và số ngày nợ tối đa
    debt_summary = calculate_dealer_debt_summary(dealer, ORDERS_DB)
    current_debt = debt_summary["current_debt"]
    credit_limit = debt_summary["credit_limit"]
    overdue_days_allowed = debt_summary["overdue_days_allowed"]
    max_debt_age = debt_summary["max_debt_age"]
    is_overdue = debt_summary["is_overdue"]

    # TIÊU CHÍ 3 (Khoản nợ quá hạn - Block hoàn toàn):
    # Đại lý có khoản quá hạn quá số ngày cho phép thì chặn tạo đơn hoàn toàn
    if is_overdue:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đại lý '{dealer.name}' có khoản nợ quá hạn ({max_debt_age} ngày, vượt quá {overdue_days_allowed} ngày cho phép). Hệ thống chặn tạo đơn hàng mới."
        )

    order_total = sum(item.quantity * item.price for item in data.items)
    over_limit = (current_debt + order_total) > credit_limit

    # 4. Tạo đơn hàng và tính base_quantity
    from app.api.v1.endpoints.products import RAW_PRODUCTS, _find_product_in_raw
    from app.models.entities import ProductEntity

    processed_items = []
    total_amount = 0.0

    for item in data.items:
        # Tìm thông tin sản phẩm để lấy đơn vị cơ sở và hệ số quy đổi mặc định nếu chưa truyền
        raw_p = _find_product_in_raw(item.product_id)
        prod_entity = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).first() if db else None

        prod_status = raw_p.get("status") if raw_p else (prod_entity.status if prod_entity else "active")
        prod_code = raw_p.get("code") if raw_p else (prod_entity.code if prod_entity else f"SP#{item.product_id}")
        if prod_status == "inactive":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{prod_code}' đã ngừng kinh doanh, không thể tạo đơn hàng."
            )

        base_unit = "Cái"
        units_list = []
        if raw_p:
            base_unit = raw_p.get("base_unit", "Cái")
            units_list = raw_p.get("units", [])
        elif prod_entity:
            base_unit = prod_entity.base_unit or "Cái"
            units_list = prod_entity.units or []

        chosen_unit = item.unit or item.unit_name or base_unit
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
            "product_code": prod_code,
            "product_name": raw_p.get("name") if raw_p else (prod_entity.name if prod_entity else f"SP #{item.product_id}"),
            "quantity": item.quantity,
            "price": item.price,
            "unit": chosen_unit,
            "unit_name": chosen_unit,
            "conversion_rate": chosen_rate,
            "base_quantity": base_quantity,
        })

    # SCRUM-56 / AC 3 & AC 4: Kiểm tra tồn khả dụng & giữ chỗ tồn kho (Database Row-Level Locking)
    from app.services.inventory_availability_service import validate_and_reserve_order_stock
    validate_and_reserve_order_stock(
        db=db,
        dealer_id=dealer.id,
        items=processed_items,
    )

    # AC: Kiểm tra bảng giá áp dụng cho nhóm khách hàng của Đại lý
    cust_group = getattr(dealer, "customer_group", None) or "dai_ly_cap_1"
    aliases = [cust_group]
    if cust_group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
        aliases = ["dai_ly_cap_1", "Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1"]
    elif cust_group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
        aliases = ["dai_ly_cap_2", "Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2"]
    elif cust_group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
        aliases = ["khach_le", "Khach_le", "RETAIL", "Khách lẻ"]

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

            prod_entity = db.query(ProductEntity).filter(ProductEntity.id == it.product_id).first()
            raw_p = _find_product_in_raw(it.product_id)
            listed_price = prod_entity.sell_price if prod_entity and prod_entity.sell_price is not None else (raw_p.get("sell_price") if raw_p else None)
            cur_name = prod_entity.name if prod_entity else (raw_p.get("name") if raw_p else f"SP #{it.product_id}")

            if item_match:
                pbi, pb_matched = item_match
                pb_matched.is_locked = True
                fl_val = pbi.floor_price if pbi.floor_price is not None else pbi.min_price
                if fl_val is not None and it.price < fl_val:
                    requires_approval = True
                    approval_reasons.append(
                        f"Bán dưới giá sàn: {cur_name} có đơn giá {it.price:,.0f} đ thấp hơn giá sàn {fl_val:,.0f} đ (Bảng giá: {pb_matched.name})"
                    )
                elif fl_val is None and listed_price is not None and it.price < listed_price:
                    requires_approval = True
                    approval_reasons.append(
                        f"Bán dưới giá niêm yết: {cur_name} có đơn giá {it.price:,.0f} đ thấp hơn giá niêm yết {listed_price:,.0f} đ"
                    )
            else:
                if listed_price is not None and it.price < listed_price:
                    requires_approval = True
                    approval_reasons.append(
                        f"Bán dưới giá niêm yết: {cur_name} có đơn giá {it.price:,.0f} đ thấp hơn giá niêm yết {listed_price:,.0f} đ"
                    )

    if db:
        db.commit()

    subtotal_amount = total_amount
    total_quantity = sum(item.get("quantity", 0) for item in processed_items)
    best_disc = resolve_best_discount(
        items=processed_items,
        dealer=dealer,
        total_quantity=total_quantity,
        subtotal_amount=subtotal_amount,
    )
    best_pct = float(best_disc["discount_percent"])

    req_discount = data.discount_rate if data.discount_rate is not None and data.discount_percent == 0 else data.discount_percent

    if req_discount == 0:
        effective_pct = best_pct
    else:
        effective_pct = req_discount
        if best_pct > 0 and effective_pct > best_pct:
            requires_approval = True
            approval_reasons.append(
                f"Chiết khấu thủ công ({effective_pct}%) vượt mức chính sách {best_disc.get('applied_policy_code', '')} ({best_pct}%)"
            )

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    if is_customer or current_user.role == "customer" or "customer" in user_roles:
        requires_approval = True
        approval_reasons.append("Đơn hàng do Đại lý tạo từ cổng đặt hàng (Cần quản lý duyệt)")

    discount_amount = round(subtotal_amount * effective_pct / 100, 2)
    final_total_amount = subtotal_amount - discount_amount

    # TIÊU CHÍ 2 (Vượt hạn mức): Tổng đơn cộng công nợ hiện tại vượt hạn mức thì đơn bị đánh dấu cần duyệt
    if (current_debt + final_total_amount) > credit_limit or over_limit:
        requires_approval = True
        approval_reasons.append(
            f"Vượt hạn mức công nợ: Tổng nợ sau đơn ({current_debt + final_total_amount:,.0f} đ) vượt hạn mức cho phép ({credit_limit:,.0f} đ)"
        )

    order_id = _allocate_order_id(db)

    order_code = f"ORD{order_id:05d}"
    now_str = datetime.now(timezone.utc).isoformat()
    order_status = "PENDING_APPROVAL" if requires_approval else "CONFIRMED"
    approval_reason = " | ".join(approval_reasons) if approval_reasons else None

    delivery_point_str = data.delivery_point
    if selected_delivery_point_id and not delivery_point_str:
        dp_obj = db.get(DealerDeliveryPointEntity, selected_delivery_point_id)
        if dp_obj:
            delivery_point_str = f"{dp_obj.label} — {dp_obj.address}"
            if dp_obj.receiver_name:
                delivery_point_str += f" ({dp_obj.receiver_name}{(' - ' + dp_obj.receiver_phone) if dp_obj.receiver_phone else ''})"

    # Lưu bản ghi vào SQL Database
    if db:
        try:
            db_order = OrderEntity(
                id=order_id,
                order_code=order_code,
                dealer_id=dealer.id,
                dealer_name=dealer.name,
                created_by=current_user.username,
                assigned_sale_id=assigned_sale_id,
                assigned_sale_name=assigned_user.full_name if assigned_user else None,
                total_amount=final_total_amount,
                status=order_status,
                note=data.note,
                delivery_point_id=selected_delivery_point_id,
                discount_rate=effective_pct,
                discount_amount=discount_amount,
                requires_approval=requires_approval,
                approval_status="PENDING_APPROVAL" if requires_approval else "NORMAL",
                approval_reason=approval_reason,
                items_json=json.dumps({
                    "items": processed_items,
                    "delivery_point": delivery_point_str,
                    "delivery_point_id": selected_delivery_point_id,
                    "desired_delivery_date": data.desired_delivery_date.isoformat() if data.desired_delivery_date else None,
                    "subtotal_amount": subtotal_amount,
                    "discount_percent": effective_pct,
                    "discount_rate": effective_pct,
                    "discount_amount": discount_amount,
                    "applied_policy_code": best_disc.get("applied_policy_code"),
                    "applied_policy_name": best_disc.get("applied_policy_name"),
                    "requires_approval": requires_approval,
                    "approval_reason": approval_reason,
                }, ensure_ascii=False),
                created_at=datetime.fromisoformat(now_str),
            )
            db.add(db_order)
            db.commit()
        except Exception as db_err:
            print(f"Lưu đơn hàng vào DB SQL thất bại (dự phòng in-memory): {db_err}")

    order_record = {
        "id": order_id,
        "order_code": order_code,
        "delivery_point_id": selected_delivery_point_id,
        "dealer_id": dealer.id,
        "dealer_name": dealer.name,
        "created_by": current_user.username,
        "assigned_sale_id": assigned_sale_id,
        "assigned_sale_name": assigned_user.full_name if assigned_user else None,
        "total_amount": final_total_amount,
        "status": order_status,
        "requires_approval": requires_approval,
        "approval_reason": approval_reason,
        "items": processed_items,
        "created_at": now_str,
        "subtotal_amount": subtotal_amount,
        "discount_percent": effective_pct,
        "discount_rate": effective_pct,
        "discount_amount": discount_amount,
        "delivery_point": delivery_point_str,
        "desired_delivery_date": data.desired_delivery_date.isoformat() if data.desired_delivery_date else None,
        "note": data.note,
        "dealer_status": getattr(dealer, "status", "Đang hoạt động"),
        "dealer_lock_reason": None if ("customer" in current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]) else getattr(dealer, "lock_reason", None),
    }
    ORDERS_DB[order_id] = order_record

    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVOICE_CREATE",
        entity_type="Invoice",
        entity_id=order_code,
        old_val=None,
        new_val={
            "order_code": order_code,
            "total_amount": final_total_amount,
            "dealer_name": dealer.name,
            "items_count": len(processed_items),
            "status": order_status,
        },
        reason=f"Tạo đơn hàng/hóa đơn cho đại lý {dealer.name}",
        request=request,
    )

    return OrderResponse(**order_record)

@router.post("/sales-entry", response_model=SalesOrderResponse, status_code=status.HTTP_201_CREATED)
def create_sales_entry_order(
    data: OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_sales_or_admin_role),
):
    """Create and persist orders from the sales-entry workflow."""
    if not data.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đơn hàng phải có ít nhất một dòng sản phẩm.",
        )
    if not data.delivery_point or not data.delivery_point.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng chọn điểm giao hàng.",
        )
    if not data.desired_delivery_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng chọn ngày giao mong muốn.",
        )
    if data.desired_delivery_date < date.today():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngày giao mong muốn không được ở quá khứ.",
        )

    load_dealers_db()

    dealer = DEALERS_DB.get(data.dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {data.dealer_id}.",
        )

    # Lỗi tạo đơn chéo / tài khoản đại lý bị khóa:
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    if is_customer or current_user.role == "customer":
        customer_dealer = _get_customer_dealer(current_user)
        if customer_dealer:
            if _is_dealer_locked(customer_dealer):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Đại lý '{customer_dealer.name}' hiện đang bị KHÓA giao dịch. Không thể tạo đơn hàng mới."
                )
            if dealer.id != customer_dealer.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tài khoản đại lý chỉ được phép tạo đơn hàng cho chính mình."
                )
        else:
            is_own = (dealer.id == current_user.id) or \
                     (dealer.email and dealer.email == current_user.email) or \
                     (dealer.phone and dealer.phone == current_user.phone) or \
                     (dealer.code and dealer.code.lower() == current_user.username.lower())
            if not is_own:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tài khoản đại lý chỉ được phép tạo đơn hàng cho chính mình."
                )

    assigned_sale_id = dealer.assigned_sale_id
    assigned_user = next((user for user in USERS_DB.values() if user.id == assigned_sale_id), None)
    if current_user.role == "sales":
        current_salesperson = USERS_DB.get(current_user.username)
        if not current_salesperson or assigned_sale_id != current_salesperson.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn chỉ được tạo đơn hàng cho đại lý được phân công.",
            )
    if assigned_user and (not assigned_user.is_active or getattr(assigned_user, "status", "ACTIVE") == "LOCKED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đại lý này thuộc nhân viên đã bị khóa tài khoản, vui lòng bàn giao trước khi lên đơn",
        )

    # Kiểm tra trạng thái khóa giao dịch của Đại lý (TRỤ CỘT 2)
    if _is_dealer_locked(dealer):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đại lý '{dealer.name}' hiện đang bị KHÓA giao dịch. Không thể tạo đơn hàng mới."
        )

    # AC 4: Kiểm tra phân quyền truy cập tính năng tạo đơn bán hàng
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    if not any(r in ["admin", "sales_manager", "sales"] for r in user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Chức năng này chỉ dành cho Nhân viên kinh doanh (Sales / Sale Executive) và Quản trị hệ thống (Admin)."
        )

    # AC 3: Kiểm tra nợ quá hạn (Block hoàn toàn)
    debt_summary = calculate_dealer_debt_summary(dealer, ORDERS_DB)
    current_debt = debt_summary["current_debt"]
    credit_limit = debt_summary["credit_limit"]
    overdue_days_allowed = debt_summary["overdue_days_allowed"]
    max_debt_age = debt_summary["max_debt_age"]
    is_overdue = debt_summary["is_overdue"]

    if is_overdue:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đại lý '{dealer.name}' có khoản nợ quá hạn ({max_debt_age} ngày, vượt quá {overdue_days_allowed} ngày cho phép). Hệ thống chặn tạo đơn hàng mới."
        )

    product_by_id = {product["id"]: product for product in RAW_PRODUCTS}
    from app.models.entities import ProductEntity

    priced_items: list[dict] = []
    products_by_id = {
        product.id: product
        for product in db.query(ProductEntity).filter(
            ProductEntity.id.in_([item.product_id for item in data.items])
        ).all()
    }
    for item in data.items:
        product = product_by_id.get(item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy sản phẩm có ID {item.product_id}.",
            )
        entity = products_by_id.get(item.product_id)

        prod_status = product.get("status") if product else (entity.status if entity else "active")
        if prod_status == "inactive":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{product['code']}' đã ngừng kinh doanh, không thể tạo đơn hàng."
            )

        base_unit = (entity.base_unit if entity and entity.base_unit else product.get("base_unit")) or "Cái"
        configured_units = entity.units if entity and entity.units else product.get("units", [])
        unit_rates = {unit: 1.0 for unit in SALES_ORDER_UNITS}
        unit_rates[base_unit] = 1.0
        for configured_unit in configured_units:
            unit_name = configured_unit.get("unit_name")
            conversion_rate = configured_unit.get("conversion_rate")
            if unit_name and isinstance(conversion_rate, (int, float)) and conversion_rate > 0:
                unit_rates[unit_name] = float(conversion_rate)

        selected_unit = item.unit or item.unit_name or base_unit
        expected_rate = unit_rates.get(selected_unit)
        if expected_rate is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Đơn vị tính '{selected_unit}' không hợp lệ cho sản phẩm {product['code']}.",
            )
        if item.conversion_rate is not None and item.conversion_rate != expected_rate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Hệ số quy đổi của đơn vị '{selected_unit}' không hợp lệ.",
            )

        priced_items.append({
            "product_id": product["id"],
            "product_code": product["code"],
            "product_name": product["name"],
            "quantity": item.quantity,
            "price": product["sell_price"],
            "unit": selected_unit,
            "unit_name": selected_unit,
            "conversion_rate": expected_rate,
            "base_quantity": int(round(item.quantity * expected_rate)),
        })

    # SCRUM-56 / AC 3 & AC 4: Kiểm tra tồn khả dụng & giữ chỗ tồn kho (Database Row-Level Locking)
    from app.services.inventory_availability_service import validate_and_reserve_order_stock
    validate_and_reserve_order_stock(
        db=db,
        dealer_id=dealer.id,
        items=priced_items,
    )
    for pi in priced_items:
        raw_p = product_by_id.get(pi["product_id"])
        if raw_p:
            raw_p["stock"] = max(0, raw_p.get("stock", 0) - pi["base_quantity"])
        entity_p = products_by_id.get(pi["product_id"])
        if entity_p:
            entity_p.stock = max(0, (entity_p.stock or 0) - pi["base_quantity"])

    delivery_point = data.delivery_point.strip()
    selected_dp_id = data.delivery_point_id
    if selected_dp_id and not delivery_point:
        dp_obj = db.get(DealerDeliveryPointEntity, selected_dp_id)
        if dp_obj:
            delivery_point = f"{dp_obj.label} — {dp_obj.address}"
            if dp_obj.receiver_name:
                delivery_point += f" ({dp_obj.receiver_name}{(' - ' + dp_obj.receiver_phone) if dp_obj.receiver_phone else ''})"

    subtotal_amount = sum(item["quantity"] * item["price"] for item in priced_items)
    total_quantity = sum(item["quantity"] for item in priced_items)

    best_disc = resolve_best_discount(
        items=priced_items,
        dealer=dealer,
        total_quantity=total_quantity,
        subtotal_amount=subtotal_amount,
    )
    best_pct = float(best_disc["discount_percent"])

    req_discount = data.discount_rate if data.discount_rate is not None and data.discount_percent == 0 else data.discount_percent

    requires_approval = False
    approval_reasons = []

    if req_discount == 0:
        effective_pct = best_pct
    else:
        effective_pct = req_discount
        if best_pct > 0 and effective_pct > best_pct:
            requires_approval = True
            approval_reasons.append(
                f"Chiết khấu thủ công ({effective_pct}%) vượt mức chính sách {best_disc.get('applied_policy_code', '')} ({best_pct}%)"
            )

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    if is_customer or current_user.role == "customer" or "customer" in user_roles:
        requires_approval = True
        approval_reasons.append("Đơn hàng do Đại lý tạo từ cổng đặt hàng (Cần quản lý duyệt)")

    discount_amount = round(subtotal_amount * effective_pct / 100, 2)
    final_total_amount = subtotal_amount - discount_amount

    # TIÊU CHÍ 2 (Vượt hạn mức): Tổng đơn cộng công nợ hiện tại vượt hạn mức thì đơn bị đánh dấu cần duyệt
    if (current_debt + final_total_amount) > credit_limit:
        requires_approval = True
        approval_reasons.append(
            f"Vượt hạn mức công nợ: Tổng nợ sau đơn ({current_debt + final_total_amount:,.0f} đ) vượt hạn mức cho phép ({credit_limit:,.0f} đ)"
        )

    order_status = "PENDING_APPROVAL" if requires_approval else "CONFIRMED"
    approval_reason = " | ".join(approval_reasons) if approval_reasons else None

    created_at = datetime.now(timezone.utc)
    db_order = OrderEntity(
        order_code=f"PENDING-{uuid4().hex}",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        created_by=current_user.username,
        assigned_sale_id=assigned_sale_id,
        assigned_sale_name=assigned_user.full_name if assigned_user else None,
        total_amount=final_total_amount,
        discount_rate=effective_pct,
        discount_amount=discount_amount,
        status=order_status,
        requires_approval=requires_approval,
        approval_status="PENDING_APPROVAL" if requires_approval else "NORMAL",
        approval_reason=approval_reason,
        note=data.note,
        delivery_point_id=selected_dp_id,
        items_json=json.dumps({
            "items": priced_items,
            "delivery_point": delivery_point,
            "delivery_point_id": selected_dp_id,
            "desired_delivery_date": data.desired_delivery_date.isoformat(),
            "subtotal_amount": subtotal_amount,
            "discount_percent": effective_pct,
            "discount_rate": effective_pct,
            "discount_amount": discount_amount,
            "applied_policy_code": best_disc.get("applied_policy_code"),
            "applied_policy_name": best_disc.get("applied_policy_name"),
            "requires_approval": requires_approval,
            "approval_reason": approval_reason,
        }, ensure_ascii=False),
        created_at=created_at,
    )
    db.add(db_order)
    db.flush()

    order_code = f"ORD{db_order.id:05d}"
    code_exists = (
        order_code in (order.get("order_code") for order in ORDERS_DB.values())
        or db.query(OrderEntity.id).filter(OrderEntity.order_code == order_code).first() is not None
    )
    if code_exists:
        order_code = f"{order_code}-{uuid4().hex[:8]}"
    db_order.order_code = order_code
    db.commit()

    order_record = {
        "id": db_order.id,
        "order_code": order_code,
        "dealer_id": dealer.id,
        "dealer_name": dealer.name,
        "created_by": current_user.username,
        "assigned_sale_id": assigned_sale_id,
        "assigned_sale_name": assigned_user.full_name if assigned_user else None,
        "total_amount": final_total_amount,
        "status": order_status,
        "requires_approval": requires_approval,
        "approval_reason": approval_reason,
        "created_at": created_at.isoformat(),
        "subtotal_amount": subtotal_amount,
        "discount_percent": effective_pct,
        "discount_rate": effective_pct,
        "discount_amount": discount_amount,
        "delivery_point_id": selected_dp_id,
        "delivery_point": delivery_point,
        "desired_delivery_date": data.desired_delivery_date.isoformat(),
        "note": data.note,
        "items": priced_items,
        "dealer_status": getattr(dealer, "status", "Đang hoạt động"),
        "dealer_lock_reason": None if ("customer" in current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]) else getattr(dealer, "lock_reason", None),
    }
    ORDERS_DB[db_order.id] = order_record

    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVOICE_CREATE",
        entity_type="Invoice",
        entity_id=order_code,
        old_val=None,
        new_val={
            "order_code": order_code,
            "total_amount": final_total_amount,
            "dealer_name": dealer.name,
            "items_count": len(priced_items),
            "status": order_status,
        },
        reason=f"Tạo đơn bán hàng cho đại lý {dealer.name}",
        request=request,
    )

    return SalesOrderResponse(**order_record)

@router.get("/{order_code}", response_model=SalesOrderResponse)
def get_order_detail(
    order_code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value)),
):
    """Return one order with its persisted line items and delivery details."""
    load_dealers_db()
    order_record = next(
        (
            order
            for order in ORDERS_DB.values()
            if order["order_code"].upper() == order_code.upper()
        ),
        None,
    )
    entity = db.query(OrderEntity).filter(
        func.upper(OrderEntity.order_code) == order_code.upper()
    ).first()

    if entity is None and order_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã {order_code}.",
        )

    if entity is not None:
        if order_record is None:
            order_record = {
                "id": entity.id,
                "order_code": entity.order_code,
                "dealer_id": entity.dealer_id,
                "dealer_name": entity.dealer_name,
                "created_by": entity.created_by,
                "assigned_sale_id": entity.assigned_sale_id,
                "assigned_sale_name": entity.assigned_sale_name,
                "total_amount": entity.total_amount,
                "status": entity.status,
                "created_at": entity.created_at.isoformat() if entity.created_at else "",
                "note": entity.note,
                "delivery_point_id": entity.delivery_point_id,
            }
        else:
            if entity.created_by:
                order_record["created_by"] = entity.created_by
            if entity.assigned_sale_name:
                order_record["assigned_sale_name"] = entity.assigned_sale_name
            if entity.status:
                order_record["status"] = entity.status
            if entity.total_amount is not None:
                order_record["total_amount"] = entity.total_amount

        if entity.items_json:
            try:
                stored_details = json.loads(entity.items_json)
                if isinstance(stored_details, list):
                    if not order_record.get("items"):
                        order_record["items"] = stored_details
                elif isinstance(stored_details, dict):
                    if not order_record.get("items"):
                        order_record["items"] = stored_details.get("items", [])
                    for field in (
                        "subtotal_amount",
                        "discount_percent",
                        "discount_rate",
                        "discount_amount",
                        "delivery_point",
                        "desired_delivery_date",
                        "requires_approval",
                        "approval_reason",
                    ):
                        if field in stored_details and (order_record.get(field) is None or order_record.get(field) == ""):
                            order_record[field] = stored_details[field]
                    if "discount_rate" not in order_record and "discount_percent" in order_record:
                        order_record["discount_rate"] = order_record["discount_percent"]
                    if "note" in stored_details and not order_record.get("note"):
                        order_record["note"] = stored_details["note"]
            except Exception:
                pass

    # Đảm bảo delivery_point có giá trị hiển thị rõ ràng
    if not order_record.get("delivery_point"):
        if order_record.get("delivery_point_id"):
            dp_obj = db.get(DealerDeliveryPointEntity, order_record["delivery_point_id"])
            if dp_obj:
                order_record["delivery_point"] = f"{dp_obj.label} — {dp_obj.address}"
        if not order_record.get("delivery_point"):
            d = DEALERS_DB.get(order_record.get("dealer_id"))
            if d and getattr(d, "address", None):
                order_record["delivery_point"] = f"Địa chỉ đại lý — {d.address}"

    items = order_record.get("items") or []
    from app.api.v1.endpoints.products import _find_product_in_raw

    for item in items:
        if not isinstance(item, dict) or item.get("product_name"):
            continue
        product = _find_product_in_raw(item.get("product_id"))
        if product:
            item["product_name"] = product.get("name")
            item.setdefault("product_code", product.get("code"))
        else:
            item["product_name"] = f"SP #{item.get('product_id', '')}"

    subtotal_amount = order_record.get(
        "subtotal_amount",
        sum(
            float(item.get("price", 0)) * float(item.get("quantity", 0))
            for item in items
            if isinstance(item, dict)
        ),
    )
    discount_percent = order_record.get("discount_percent", 0)
    discount_amount = order_record.get(
        "discount_amount",
        round(subtotal_amount * discount_percent / 100, 2),
    )
    d = DEALERS_DB.get(order_record.get("dealer_id"))
    d_status = getattr(d, "status", "Đang hoạt động") if d else "Đang hoạt động"
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    d_lock_reason = None if is_customer else (getattr(d, "lock_reason", None) if d else None)

    detail_response = dict(order_record)
    detail_response.update({
        "items": items,
        "subtotal_amount": subtotal_amount,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "dealer_status": d_status,
        "dealer_lock_reason": d_lock_reason,
    })
    return SalesOrderResponse(**detail_response)


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

    db_order = db.query(OrderEntity).filter(
        (OrderEntity.order_code == target["order_code"]) | (OrderEntity.id == target.get("id"))
    ).first()
    if db_order:
        db_order.status = "CONFIRMED"
        try:
            p_dict = json.loads(db_order.items_json) if db_order.items_json else {}
            p_dict["requires_approval"] = False
            p_dict["approved_by"] = current_user.username
            p_dict["approved_at"] = target["approved_at"]
            db_order.items_json = json.dumps(p_dict, ensure_ascii=False)
        except Exception:
            pass
        db.commit()

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


class OrderRejectRequest(BaseModel):
    reason: Optional[str] = "Từ chối duyệt đơn hàng bán dưới giá sàn"


@router.post("/{order_code}/reject", response_model=OrderResponse)
def reject_order(
    order_code: str,
    request: Request,
    payload: Optional[OrderRejectRequest] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    """
    Từ chối đơn hàng: CHỈ sales_manager hoặc admin.
    Chuyển đơn từ PENDING_APPROVAL sang REJECTED.
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
    reject_reason = (payload.reason if payload and payload.reason else "Từ chối duyệt đơn hàng bán dưới giá sàn")
    target["status"] = "REJECTED"
    target["requires_approval"] = False
    target["approval_reason"] = f"Bị từ chối bởi {current_user.username}: {reject_reason}"

    # Hoàn trả tồn kho nếu đơn đã trừ tồn kho lúc tạo
    if old_status == "PENDING_APPROVAL":
        from app.api.v1.endpoints.products import _find_product_in_raw
        from app.models.entities import ProductEntity
        for it in target.get("items", []):
            p_id = it.get("product_id")
            base_qty = it.get("base_quantity", it.get("quantity", 0))
            raw_p = _find_product_in_raw(p_id)
            if raw_p:
                raw_p["stock"] = raw_p.get("stock", 0) + base_qty
            if db:
                pe = db.query(ProductEntity).filter(ProductEntity.id == p_id).first()
                if pe:
                    pe.stock = (pe.stock or 0) + base_qty
        if db:
            db_order = db.query(OrderEntity).filter(
                (OrderEntity.order_code == target["order_code"]) | (OrderEntity.id == target.get("id"))
            ).first()
            if db_order:
                db_order.status = "REJECTED"
                try:
                    p_dict = json.loads(db_order.items_json) if db_order.items_json else {}
                    p_dict["requires_approval"] = False
                    p_dict["approval_reason"] = target["approval_reason"]
                    db_order.items_json = json.dumps(p_dict, ensure_ascii=False)
                except Exception:
                    pass
                from app.services.inventory_availability_service import release_order_stock
                release_order_stock(db, db_order)
            db.commit()

    log_audit_event(
        db=db,
        user=current_user,
        action_type="ORDER_REJECT",
        entity_type="Order",
        entity_id=target["order_code"],
        old_val={"status": old_status},
        new_val={"status": "REJECTED"},
        reason=reject_reason,
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

    persisted_order = db.query(OrderEntity).filter(
        func.upper(OrderEntity.order_code) == order_code.upper()
    ).first()
    if not target and persisted_order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy hóa đơn/đơn hàng có mã {order_code}."
        )
    if not target and persisted_order is not None:
        target = {
            "order_code": persisted_order.order_code,
            "status": persisted_order.status,
            "note": persisted_order.note,
        }

    old_val = {"status": target["status"], "note": target.get("note")}
    new_val = {}
    new_status = data.status.upper() if data.status else target["status"]
    new_note = data.note if data.note is not None else target.get("note")
    if new_status != target["status"]:
        new_val["status"] = new_status
    if new_note != target.get("note"):
        new_val["note"] = new_note

    if not new_val:
        return {
            "status": "success",
            "message": "Đơn hàng không thay đổi.",
            "order": target,
        }

    target.update(new_val)
    if persisted_order is not None:
        if "status" in new_val:
            persisted_order.status = new_val["status"]
        if "note" in new_val:
            persisted_order.note = new_val["note"]
        db.commit()

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

    # Kiểm tra nếu đại lý của đơn hàng đang bị khóa -> Đơn đang dở vẫn xử lý được nhưng có cảnh báo
    dealer = DEALERS_DB.get(target.get("dealer_id"))
    warning_message = None
    if dealer and getattr(dealer, "status", "ACTIVE") == "LOCKED":
        warning_message = f"CẢNH BÁO: Đại lý '{dealer.name}' hiện đang bị KHÓA giao dịch (Lý do: {dealer.lock_reason or 'Không rõ'}). Vui lòng lưu ý khi xử lý công nợ và hoàn tất đơn dở dang này!"

    return {
        "status": "success",
        "message": f"Đã cập nhật hóa đơn {order_code} thành công.",
        "warning": warning_message,
        "order": target
    }


@router.put("/dealers/{dealer_id}/credit-limit")
@router.put("/dealers/{dealer_id}/debt-limit")
def update_customer_debt_limit(
    dealer_id: int,
    data: DebtLimitUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["accountant", "sales_manager", "admin"]))
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
    old_days = getattr(dealer, "max_debt_days", 30)
    if old_limit == data.credit_limit and old_days == data.max_debt_days:
        return {
            "status": "success",
            "message": "Hạn mức công nợ không thay đổi.",
            "dealer": dealer,
        }
    dealer.credit_limit = data.credit_limit
    dealer.max_debt_days = data.max_debt_days
    save_dealers_db()

    log_audit_event(
        db=db,
        user=current_user,
        action_type="DEBT_LIMIT_CHANGE",
        entity_type="CustomerDebt",
        entity_id=dealer.code,
        old_val={"credit_limit": old_limit, "max_debt_days": old_days, "customer_name": dealer.name},
        new_val={"credit_limit": data.credit_limit, "max_debt_days": data.max_debt_days},
        reason=data.reason,
        request=request,
    )

    return {
        "status": "success",
        "message": f"Đã cập nhật hạn mức công nợ cho {dealer.name} thành {data.credit_limit:,.0f} đ.",
        "dealer": dealer
    }
class DealerLockRequest(BaseModel):
    lock_reason: str = Field(..., min_length=1, max_length=500)


@router.get("/dealers")
def list_dealers(
    current_user: UserResponse = Depends(get_current_user)
):
    """
    Lấy danh sách toàn bộ đại lý kèm trạng thái giao dịch (ACTIVE / LOCKED),
    lý do khóa, người khóa, thời điểm khóa.
    """
    dealers_list = []
    for d in DEALERS_DB.values():
        sale_name = None
        if d.assigned_sale_id:
            for u in USERS_DB.values():
                if u.id == d.assigned_sale_id:
                    sale_name = u.full_name or u.username
                    break

        d_dict = d.model_dump(mode="json")
        d_dict["assigned_sale_name"] = sale_name
        dealers_list.append(d_dict)

    return {"dealers": sorted(dealers_list, key=lambda x: x["id"])}


@router.post("/dealers/{dealer_id}/lock")
def lock_dealer(
    dealer_id: int,
    data: DealerLockRequest,
    current_user: UserResponse = Depends(require_roles(["accountant", "admin"]))
):
    """
    Kế toán công nợ / Quản trị viên: Khóa giao dịch của đại lý có dấu hiệu mất khả năng thanh toán.
    Bắt buộc nhập lý do khóa.
    """
    if not data.lock_reason or not data.lock_reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bắt buộc phải nhập lý do khóa đại lý."
        )

    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="Không tìm thấy đại lý.")

    if getattr(dealer, "status", "ACTIVE") == "LOCKED":
        raise HTTPException(status_code=400, detail="Đại lý này đã ở trạng thái KHÓA trước đó.")

    dealer.status = "LOCKED"
    dealer.lock_reason = data.lock_reason.strip()
    dealer.locked_at = datetime.now(timezone.utc).isoformat()
    dealer.locked_by = current_user.username
    save_dealers_db()

    return {"message": "Đã khóa giao dịch đại lý thành công.", "dealer": dealer}


@router.post("/dealers/{dealer_id}/unlock")
def unlock_dealer(
    dealer_id: int,
    current_user: UserResponse = Depends(require_roles(["accountant", "admin"]))
):
    """
    Kế toán công nợ / Quản trị viên: Mở lại giao dịch cho đại lý khi đã xử lý xong công nợ.
    """
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="Không tìm thấy đại lý.")

    if getattr(dealer, "status", "ACTIVE") == "ACTIVE":
        raise HTTPException(status_code=400, detail="Đại lý đang hoạt động bình thường, không cần mở khóa.")

    dealer.status = "ACTIVE"
    dealer.lock_reason = None
    dealer.locked_at = None
    dealer.locked_by = None
    save_dealers_db()

    return {"message": "Đã mở khóa giao dịch đại lý thành công.", "dealer": dealer}
