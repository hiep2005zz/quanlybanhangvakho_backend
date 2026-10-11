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
from app.models.entities import DealerDeliveryPointEntity, DealerEntity, OrderEntity, ProductEntity
from typing import List, Optional, Union
from datetime import date, datetime, timezone, timedelta
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, status as http_status, Request
from sqlalchemy import func, case, or_, and_
from sqlalchemy.orm import Session
from app.db.init_db import _ensure_legacy_columns
from app.api.deps import get_current_user, require_permission, require_roles, require_sales_or_admin_role
from app.core.database import get_db
from app.core.rbac import Permission, Role
from app.schemas.auth import UserResponse
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.models.dealer import DEALERS_DB, save_dealers_db, load_dealers_db
from app.models.price_book import PriceBookEntity, PriceBookItemEntity

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
    region: Optional[str] = None
    dealer_lock_reason: Optional[str] = None
    cancel_reason: Optional[str] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[str] = None
    dealer_code: Optional[str] = None
    dealer_phone: Optional[str] = None
    dealer_address: Optional[str] = None
    applied_policy_code: Optional[str] = None
    applied_policy_name: Optional[str] = None
    delivery_point: Optional[str] = None
    delivery_point_id: Optional[int] = None
    desired_delivery_date: Optional[str] = None
    note: Optional[str] = None

class OrderListFilteredResponse(BaseModel):
    items: List[OrderResponse]
    total: int
    total_count: Optional[int] = None
    filtered_total_amount: float
    pending_count: int = 0
    confirmed_count: int = 0
    page: int = 1
    page_size: int = 20

class SalesOrderResponse(OrderResponse):
    subtotal_amount: float = 0
    discount_percent: float = 0
    discount_rate: Optional[float] = 0
    discount_amount: float = 0
    items: List[dict] = Field(default_factory=list)

class OrderListResponse(OrderListFilteredResponse):
    pass

class OrderCancelRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500, description="Lý do hủy đơn hàng")

class InvoiceEditRequest(BaseModel):
    note: Optional[str] = None
    status: Optional[str] = None  # e.g. CANCELLED, EDITED
    reason: str = Field(..., min_length=1, max_length=500)

class DebtLimitUpdateRequest(BaseModel):
    credit_limit: float = Field(..., ge=0)
    max_debt_days: int = Field(default=30, ge=0)
    reason: str = Field(..., min_length=2, max_length=255)

class ReorderRequest(BaseModel):
    product_id: Optional[int] = None
    create_immediate: Optional[bool] = False
    delivery_point_id: Optional[int] = None
    note: Optional[str] = None

class ReorderItemDetail(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    quantity: int
    price: float
    old_price: Optional[float] = None
    unit: str
    unit_name: Optional[str] = None
    conversion_rate: float
    base_unit: str
    stock: int
    available_units: List[dict] = Field(default_factory=list)
    price_note: Optional[str] = None

class ExcludedItemDetail(BaseModel):
    product_id: int
    product_code: Optional[str] = None
    product_name: str
    reason: str

class ReorderResponse(BaseModel):
    order_id: int
    order_code: str
    dealer_id: int
    dealer_name: str
    valid_items: List[ReorderItemDetail]
    excluded_items: List[ExcludedItemDetail]
    total_valid: int
    total_excluded: int
    can_reorder: bool
    message: str
    created_order: Optional[OrderResponse] = None

# Orders created through the API are stored in the database and this process-local cache.
ORDERS_DB: dict[int, dict] = {
    1: {
        "id": 1,
        "order_code": "DH-2026-0001",
        "dealer_id": 1,
        "dealer_name": "Công Ty Cổ Phần Phân Phối Tổng Hợp Sao Mai Toàn Cầu",
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Trần Bán Hàng",
        "total_amount": 35800000.0,
        "status": "PAID",
        "created_at": "2026-03-15T08:30:00Z",
        "items": [
            {"product_id": 1, "product_name": "Áo sơ mi nam công sở Oxford", "quantity": 100, "price": 250000.0},
            {"product_id": 2, "product_name": "Quần tây nam Slimfit", "quantity": 30, "price": 360000.0}
        ]
    },
    2: {
        "id": 2,
        "order_code": "DH-2026-0002",
        "dealer_id": 1,
        "dealer_name": "Công Ty Cổ Phần Phân Phối Tổng Hợp Sao Mai Toàn Cầu",
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Trần Bán Hàng",
        "total_amount": 18200000.0,
        "status": "PAID",
        "created_at": "2026-03-20T14:15:00Z",
        "items": [
            {"product_id": 3, "product_name": "Áo polo thể thao phối sọc", "quantity": 70, "price": 260000.0}
        ]
    },
    3: {
        "id": 3,
        "order_code": "DH-2026-0003",
        "dealer_id": 2,
        "dealer_name": "Đại Lý Thời Trang & Dệt May Tân Bình",
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Trần Bán Hàng",
        "total_amount": 22400000.0,
        "status": "PAID",
        "created_at": "2026-03-22T10:00:00Z",
        "items": [
            {"product_id": 1, "product_name": "Áo sơ mi nam công sở Oxford", "quantity": 50, "price": 250000.0},
            {"product_id": 4, "product_name": "Áo thun cotton trơn Premium", "quantity": 60, "price": 165000.0}
        ]
    },
    4: {
        "id": 4,
        "order_code": "DH-2026-0004",
        "dealer_id": 3,
        "dealer_name": "Hợp Tác Xã Thương Mại Dịch Vụ Cảng Biển Hải Phòng",
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Trần Bán Hàng",
        "total_amount": 14500000.0,
        "status": "PAID",
        "created_at": "2026-03-25T16:40:00Z",
        "items": [
            {"product_id": 5, "product_name": "Thắt lưng da bò nguyên tấm", "quantity": 60, "price": 240000.0}
        ]
    },
    5: {
        "id": 5,
        "order_code": "DH-2026-0005",
        "dealer_id": 4,
        "dealer_name": "Công Ty TNHH Bán Lẻ Đầu Tư & Xuất Nhập Khẩu An Phát Đà Nẵng",
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Trần Bán Hàng",
        "total_amount": 9800000.0,
        "status": "PAID",
        "created_at": "2026-03-28T11:20:00Z",
        "items": [
            {"product_id": 2, "product_name": "Quần tây nam Slimfit", "quantity": 25, "price": 390000.0}
        ]
    }
}
NEXT_ORDER_ID = 6

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
    # Tự động đồng bộ các đơn hàng từ SQL database vào orders_db nếu chưa có
    try:
        from app.core.database import SessionLocal
        from app.models.entities import OrderEntity
        with SessionLocal() as db_session:
            for db_order in db_session.query(OrderEntity).filter(OrderEntity.dealer_id == dealer.id).all():
                if db_order.id not in orders_db:
                    orders_db[db_order.id] = {
                        "id": db_order.id,
                        "order_code": db_order.order_code,
                        "dealer_id": db_order.dealer_id,
                        "dealer_name": db_order.dealer_name,
                        "total_amount": db_order.total_amount,
                        "status": db_order.status,
                        "created_at": db_order.created_at.isoformat() if db_order.created_at else now.isoformat(),
                    }
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    for o in orders_db.values():
        if o.get("dealer_id") == dealer.id and o.get("status") not in ("PAID", "CANCELLED"):
            current_debt += float(o.get("total_amount", 0.0) or 0.0)
            created_at_val = o.get("created_at")
            if created_at_val:
                try:
                    if isinstance(created_at_val, str):
                        s = created_at_val.replace("Z", "+00:00")
                        order_date = datetime.fromisoformat(s)
                        if not order_date.tzinfo:
                            order_date = order_date.replace(tzinfo=timezone.utc)
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

def validate_dealer_overdue_debt(dealer, orders_db: dict) -> dict:
    """
    Tiêu chí 3: Hard Block (Chặn tuyệt đối) khi đại lý có nợ quá hạn vượt số ngày cho phép.
    So sánh số ngày quá hạn thực tế với số ngày nợ cho phép cấu hình theo đại lý.
    Nếu phát hiện khoản nợ vượt quá số ngày cho phép: Bắt buộc chặn đứng luồng tạo đơn (HTTP 400).
    """
    debt_summary = calculate_dealer_debt_summary(dealer, orders_db)
    if debt_summary.get("is_overdue"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đại lý đang có khoản nợ quá hạn vượt mức cho phép. Tạm khóa quyền lên đơn mới.",
        )
    return debt_summary

def _get_customer_dealer(current_user: UserResponse):
    customer_dealer = DEALERS_DB.get(current_user.id)
    if not customer_dealer:
        for d in DEALERS_DB.values():
            if (d.email and current_user.email and d.email.lower() == current_user.email.lower()) or \
               (d.phone and current_user.phone and d.phone == current_user.phone) or \
               (d.code and current_user.username and d.code.lower() == current_user.username.lower()) or \
               (d.name and current_user.full_name and d.name.lower() == current_user.full_name.lower()):
                customer_dealer = d
                break
    if not customer_dealer:
        try:
            from app.core.database import SessionLocal
            from app.models.entities import DealerEntity
            from sqlalchemy import or_
            with SessionLocal() as db_session:
                conds = [DealerEntity.id == current_user.id]
                if current_user.email:
                    conds.append(DealerEntity.email == current_user.email)
                if current_user.phone:
                    conds.append(DealerEntity.phone == current_user.phone)
                if current_user.username:
                    conds.append(DealerEntity.code == current_user.username)
                db_d = db_session.query(DealerEntity).filter(or_(*conds)).first()
                if db_d:
                    from app.models.dealer import Dealer
                    customer_dealer = Dealer(
                        id=db_d.id,
                        code=db_d.code,
                        name=db_d.name,
                        phone=db_d.phone,
                        email=db_d.email,
                        address=db_d.address,
                        region=db_d.region,
                        assigned_sale_id=db_d.assigned_sale_id,
                        credit_limit=db_d.credit_limit,
                        max_debt_days=db_d.max_debt_days,
                        overdue_days_allowed=db_d.overdue_days_allowed or db_d.max_debt_days,
                        customer_group=db_d.customer_group,
                        status=db_d.status,
                    )
        except Exception:
            pass
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
            "assigned_sale_id": getattr(dealer, "assigned_sale_id", None),
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



class PurchaseHistoryItem(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    unit: str
    conversion_rate: float = 1.0
    price: float = 0.0
    total_quantity: float = 0.0
    order_count: int = 0
    avg_quantity: float = 0.0
    last_order_quantity: float = 0.0
    last_purchased_date: Optional[str] = None


class LastOrderItem(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    quantity: float
    price: float
    unit: str
    conversion_rate: float = 1.0


class LastOrderSummary(BaseModel):
    order_code: str
    created_at: str
    items: List[LastOrderItem] = Field(default_factory=list)


class DealerPurchaseHistoryResponse(BaseModel):
    dealer_id: int
    dealer_name: str
    period: str = "3 tháng gần nhất"
    has_history: bool = False
    total_orders_3_months: int = 0
    items: List[PurchaseHistoryItem] = Field(default_factory=list)
    last_order: Optional[LastOrderSummary] = None
    last_order_items: List[LastOrderItem] = Field(default_factory=list)


@router.get("/dealers/{dealer_id}/purchase-history", response_model=DealerPurchaseHistoryResponse)
def get_dealer_purchase_history(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    SCRUM-52 / SCRUM-57:
    Lấy lịch sử mua hàng của đại lý trong 3 tháng gần nhất kèm số lượng bình quân,
    và nhóm hàng đã mua ở đơn hàng lần trước.
    Ràng buộc: Chỉ hiện với đại lý mà nhân viên được phân công.
    """
    # 1. Tra cứu đại lý trong DB hoặc in-memory
    dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer:
        dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy đại lý.",
        )

    # 2. Kiểm tra quyền hạn: Chỉ hiện với đại lý mà nhân viên được phân công
    user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
    if "admin" not in user_roles and "sales_manager" not in user_roles:
        assigned_user = USERS_DB.get(current_user.username)
        current_uid = assigned_user.id if assigned_user else current_user.id
        dealer_assigned_id = getattr(dealer, "assigned_sale_id", None)
        if dealer_assigned_id != current_uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chỉ hiển thị với đại lý mà nhân viên được phân công.",
            )

    # 3. Lấy toàn bộ đơn hàng của đại lý (loại trừ đơn bị hủy)
    all_orders = []
    seen_codes = set()

    db_orders = db.query(OrderEntity).filter(
        OrderEntity.dealer_id == dealer_id,
        OrderEntity.status != "CANCELLED",
    ).all()
    for o in db_orders:
        if o.order_code not in seen_codes:
            seen_codes.add(o.order_code)
            all_orders.append(o)

    for o_dict in ORDERS_DB.values():
        if o_dict.get("dealer_id") == dealer_id and o_dict.get("status") != "CANCELLED":
            code = o_dict.get("order_code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                all_orders.append(o_dict)

    now = datetime.now(timezone.utc)
    three_months_ago = now - timedelta(days=90)

    def parse_created_at(o):
        val = getattr(o, "created_at", None) if hasattr(o, "created_at") else (o.get("created_at") if isinstance(o, dict) else None)
        if isinstance(val, datetime):
            return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
            except Exception:
                return now
        return now

    def get_order_items(o):
        if hasattr(o, "items_json") and o.items_json:
            try:
                parsed = json.loads(o.items_json)
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict):
                    return parsed.get("items", [])
            except Exception:
                pass
        if isinstance(o, dict):
            if "items" in o and isinstance(o["items"], list):
                return o["items"]
            if "items_json" in o and o["items_json"]:
                try:
                    parsed = json.loads(o["items_json"])
                    if isinstance(parsed, list):
                        return parsed
                    if isinstance(parsed, dict):
                        return parsed.get("items", [])
                except Exception:
                    pass
        return []

    all_orders.sort(key=parse_created_at, reverse=True)

    # 4. Xác định nhóm hàng đã mua lần trước (đơn hàng mới nhất)
    last_order_obj = all_orders[0] if all_orders else None
    last_order_summary = None
    last_order_items: List[LastOrderItem] = []

    product_map = {p.id: p for p in db.query(ProductEntity).all()}
    for rp in RAW_PRODUCTS:
        if rp.get("id") and rp["id"] not in product_map:
            product_map[rp["id"]] = type("RawProd", (), rp)()

    if last_order_obj:
        raw_items = get_order_items(last_order_obj)
        last_code = getattr(last_order_obj, "order_code", None) if hasattr(last_order_obj, "order_code") else last_order_obj.get("order_code", "")
        last_dt = parse_created_at(last_order_obj).strftime("%Y-%m-%d %H:%M")
        for it in raw_items:
            pid = it.get("product_id")
            if not pid:
                continue
            prod = product_map.get(pid)
            p_code = getattr(prod, "code", f"SP{pid:03d}") if prod else f"SP{pid:03d}"
            p_name = it.get("product_name") or (getattr(prod, "name", f"Sản phẩm #{pid}") if prod else f"Sản phẩm #{pid}")
            last_item = LastOrderItem(
                product_id=pid,
                product_code=p_code,
                product_name=p_name,
                quantity=float(it.get("quantity", 1)),
                price=float(it.get("price", 0.0)),
                unit=it.get("unit") or it.get("unit_name") or getattr(prod, "base_unit", "Cái"),
                conversion_rate=float(it.get("conversion_rate", 1.0)),
            )
            last_order_items.append(last_item)

        last_order_summary = LastOrderSummary(
            order_code=last_code,
            created_at=last_dt,
            items=last_order_items,
        )

    # 5. Thống kê các mặt hàng đại lý đã mua trong 3 tháng gần nhất kèm số lượng bình quân
    recent_orders = [o for o in all_orders if parse_created_at(o) >= three_months_ago]
    product_stats = {}
    last_order_pids = {it.product_id: it.quantity for it in last_order_items}

    for o in recent_orders:
        o_date_str = parse_created_at(o).strftime("%Y-%m-%d")
        items = get_order_items(o)
        for it in items:
            pid = it.get("product_id")
            if not pid:
                continue
            qty = float(it.get("quantity", 0))
            if pid not in product_stats:
                prod = product_map.get(pid)
                p_code = getattr(prod, "code", f"SP{pid:03d}") if prod else f"SP{pid:03d}"
                p_name = it.get("product_name") or (getattr(prod, "name", f"Sản phẩm #{pid}") if prod else f"Sản phẩm #{pid}")
                product_stats[pid] = {
                    "product_id": pid,
                    "product_code": p_code,
                    "product_name": p_name,
                    "unit": it.get("unit") or it.get("unit_name") or getattr(prod, "base_unit", "Cái"),
                    "conversion_rate": float(it.get("conversion_rate", 1.0)),
                    "price": float(it.get("price", 0.0)),
                    "total_quantity": qty,
                    "order_count": 1,
                    "last_purchased_date": o_date_str,
                    "last_order_quantity": last_order_pids.get(pid, qty),
                }
            else:
                stat = product_stats[pid]
                stat["total_quantity"] += qty
                stat["order_count"] += 1
                if not stat["last_purchased_date"]:
                    stat["last_purchased_date"] = o_date_str

    history_items = []
    for pid, stat in product_stats.items():
        avg = stat["total_quantity"] / stat["order_count"] if stat["order_count"] > 0 else 0
        history_items.append(
            PurchaseHistoryItem(
                product_id=stat["product_id"],
                product_code=stat["product_code"],
                product_name=stat["product_name"],
                unit=stat["unit"],
                conversion_rate=stat["conversion_rate"],
                price=stat["price"],
                total_quantity=round(stat["total_quantity"], 2),
                order_count=stat["order_count"],
                avg_quantity=round(avg, 1),
                last_order_quantity=round(stat["last_order_quantity"], 2),
                last_purchased_date=stat["last_purchased_date"],
            )
        )

    history_items.sort(key=lambda x: (x.order_count, x.total_quantity), reverse=True)

    return DealerPurchaseHistoryResponse(
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        period="3 tháng gần nhất",
        has_history=len(history_items) > 0,
        total_orders_3_months=len(recent_orders),
        items=history_items,
        last_order=last_order_summary,
        last_order_items=last_order_items,
    )


def _format_order_response(
    order: OrderEntity,
    db: Session,
    can_view_cost: bool = False,
    is_customer: bool = False,
    is_sales: bool = False,
) -> OrderResponse:
    load_dealers_db()
    from app.api.v1.endpoints.products import _find_product_in_raw

    items = []
    in_mem = ORDERS_DB.get(order.id)
    if not in_mem:
        in_mem = next((v for v in ORDERS_DB.values() if v.get("order_code") == order.order_code), {}) or {}

    delivery_point = in_mem.get("delivery_point") if in_mem else None
    desired_delivery_date = in_mem.get("desired_delivery_date") if in_mem else None
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
                if not delivery_point:
                    delivery_point = payload.get("delivery_point")
                if not desired_delivery_date:
                    desired_delivery_date = payload.get("desired_delivery_date")
                subtotal_amount = payload.get("subtotal_amount", order.total_amount)
                discount_percent = payload.get("discount_percent", 0.0)
                discount_amount = payload.get("discount_amount", 0.0)
        except Exception:
            pass

    for item in items:
        if isinstance(item, dict):
            if not item.get("product_name"):
                p = _find_product_in_raw(item.get("product_id"))
                if p:
                    item["product_name"] = p.get("name")
                    item.setdefault("product_code", p.get("code"))
                else:
                    item["product_name"] = f"SP #{item.get('product_id', '')}"
            # AC-5: Bảo mật thông tin: Không trả về cost_price hay profit cho vai trò sales
            if is_sales or not can_view_cost:
                for sensitive_key in ["cost_price", "cost", "profit", "profit_margin", "margin", "gross_profit", "base_price"]:
                    item.pop(sensitive_key, None)

    if not delivery_point and order.delivery_point_id:
        dp_obj = db.get(DealerDeliveryPointEntity, order.delivery_point_id)
        if dp_obj:
            delivery_point = f"{dp_obj.label} — {dp_obj.address}"

    d_obj = DEALERS_DB.get(order.dealer_id)
    if not delivery_point and d_obj and getattr(d_obj, "address", None):
        delivery_point = f"Địa chỉ đại lý — {d_obj.address}"

    # Lấy thông tin region của đại lý
    region_val = getattr(d_obj, "region", None) if d_obj else None
    if not region_val:
        db_dealer = db.get(DealerEntity, order.dealer_id)
        if db_dealer and db_dealer.region:
            region_val = db_dealer.region

    disc_rate = getattr(order, "discount_rate", 0.0) or discount_percent or 0.0
    disc_amt = getattr(order, "discount_amount", 0.0) or discount_amount or 0.0
    sub_amt = (order.total_amount or 0.0) + disc_amt
    items_parsed = None
    appr_reason = None
    req_appr = False
    appr_by = None
    appr_at = None
    if order.items_json:
        try:
            ij = json.loads(order.items_json)
            if isinstance(ij, dict):
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

    c_reason = getattr(order, "cancel_reason", None)
    c_by = getattr(order, "cancelled_by", None)
    c_at_val = getattr(order, "cancelled_at", None)
    c_at_str = c_at_val.isoformat() if c_at_val and hasattr(c_at_val, "isoformat") else (str(c_at_val) if c_at_val else None)

    in_mem = ORDERS_DB.get(order.id)
    if not in_mem:
        in_mem = next((v for v in ORDERS_DB.values() if v.get("order_code") == order.order_code), {}) or {}
    if in_mem:
        if not appr_reason:
            appr_reason = in_mem.get("approval_reason")
            req_appr = in_mem.get("requires_approval", req_appr)
            appr_by = in_mem.get("approved_by", appr_by)
            appr_at = in_mem.get("approved_at", appr_at)
        if not c_reason:
            c_reason = in_mem.get("cancel_reason")
        if not c_by:
            c_by = in_mem.get("cancelled_by")
        if not c_at_str:
            c_at_str = in_mem.get("cancelled_at")
        if not delivery_point:
            delivery_point = in_mem.get("delivery_point")
        if not desired_delivery_date:
            desired_delivery_date = in_mem.get("desired_delivery_date")

    if order.status in ("PENDING_APPROVAL", "PENDING"):
        req_appr = True
        if not appr_reason:
            if disc_rate > 0:
                appr_reason = f"Chiết khấu ({disc_rate}%) vượt hạn mức chính sách cần duyệt"
            else:
                appr_reason = "Bán dưới giá sàn cần quản lý duyệt"

    clean_items = items or items_parsed
    if (is_sales or not can_view_cost) and clean_items:
        clean_items = [
            {k: v for k, v in it.items() if k not in ("cost_price", "cost", "profit", "profit_margin", "margin", "gross_profit", "base_price")}
            if isinstance(it, dict) else it
            for it in clean_items
        ]

    # Map delivered_quantity & shortage_quantity
    ord_st = (order.status or "").upper()
    is_order_dispatched = ord_st in ("DELIVERED", "CLOSED", "COMPLETED", "EXPORTED", "SHIPPED")
    if in_mem and in_mem.get("items"):
        mem_items_by_pid = {it.get("product_id"): it for it in in_mem.get("items", []) if isinstance(it, dict)}
        for it in (clean_items or []):
            if isinstance(it, dict):
                pid = it.get("product_id")
                if pid in mem_items_by_pid:
                    m_it = mem_items_by_pid[pid]
                    if "delivered_quantity" in m_it and it.get("delivered_quantity") is None:
                        it["delivered_quantity"] = m_it["delivered_quantity"]
                    if "shortage_quantity" in m_it and it.get("shortage_quantity") is None:
                        it["shortage_quantity"] = m_it["shortage_quantity"]

    for it in (clean_items or []):
        if isinstance(it, dict):
            qty = it.get("quantity", 0)
            del_qty = it.get("delivered_quantity")
            if is_order_dispatched:
                if del_qty is None:
                    del_qty = qty
                shortage = max(0, int(qty) - int(del_qty))
            else:
                del_qty = None
                shortage = 0
            it["delivered_quantity"] = del_qty
            it["shortage_quantity"] = shortage

    dealer_status = getattr(d_obj, "status", "Đang hoạt động") if d_obj else "Đang hoạt động"
    dealer_lock_reason = None if is_customer else (getattr(d_obj, "lock_reason", None) if d_obj else None)

    created_at_str = order.created_at.isoformat() if hasattr(order.created_at, "isoformat") else str(order.created_at or "")
    desired_del_date_str = desired_delivery_date.isoformat() if hasattr(desired_delivery_date, "isoformat") else (str(desired_delivery_date) if desired_delivery_date else None)

    return OrderResponse(
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
        items=clean_items,
        created_at=created_at_str,
        dealer_status=dealer_status,
        dealer_lock_reason=dealer_lock_reason,
        region=region_val,
        cancel_reason=c_reason,
        cancelled_by=c_by,
        cancelled_at=c_at_str,
        delivery_point=delivery_point,
        delivery_point_id=order.delivery_point_id,
        desired_delivery_date=desired_del_date_str,
        note=order.note or (in_mem.get("note") if in_mem else None),
    )

_build_order_response = _format_order_response


@router.get("/sales-reps")
def get_sales_representatives(
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value))
):
    """Danh sách nhân viên kinh doanh cho bộ lọc."""
    reps = []
    seen_ids = set()
    for u in USERS_DB.values():
        u_roles = u.roles if hasattr(u, "roles") else [u.role]
        if any(r in u_roles for r in ["sales", "sales_manager"]) or u.role in ["sales", "sales_manager"]:
            if u.id not in seen_ids:
                seen_ids.add(u.id)
                reps.append({
                    "id": u.id,
                    "username": u.username,
                    "full_name": u.full_name or u.username,
                    "role": u.role,
                })
    return sorted(reps, key=lambda x: x["full_name"])


@router.get("/regions")
def get_order_regions(
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value)),
    db: Session = Depends(get_db)
):
    """Danh sách khu vực cho bộ lọc."""
    regions = set()
    for d in DEALERS_DB.values():
        if d.region:
            regions.add(d.region.strip())
    for r in db.query(DealerEntity.region).filter(DealerEntity.region.isnot(None)).all():
        if r[0]:
            regions.add(r[0].strip())
    return sorted(list(regions))


@router.get("", response_model=Union[OrderListFilteredResponse, List[OrderResponse]])
def get_orders(
    request: Request,
    status: Optional[str] = None,
    dealer_id: Optional[int] = None,
    sales_rep_id: Optional[int] = None,
    region: Optional[str] = None,
    start_date: Optional[Union[date, str]] = None,
    end_date: Optional[Union[date, str]] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    search: Optional[str] = None,
    format: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value))
):
    """
    Lấy danh sách đơn hàng / hóa đơn kèm bộ lọc đa tiêu chí và tổng hợp doanh thu.
    Áp dụng Zero-Trust Row-Level Security (RLS) cho vai trò sales.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else (current_user.roles or [current_user.role])
    if current_user.username == "muahang" or "purchasing" in user_roles or current_user.role == "purchasing":
        raise HTTPException(
            status_code=403,
            detail="Truy cập bị từ chối (403 Forbidden). Nhân viên mua hàng không có quyền truy cập đơn hàng bán."
        )

    allowed_roles = {
        Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value, Role.SALES.value, Role.CUSTOMER.value,
        "admin", "sales_manager", "sales", "customer", "agent"
    }
    if not any(r in allowed_roles for r in user_roles):
        raise HTTPException(
            status_code=403,
            detail="Chức năng chỉ dành cho Quản trị viên, Quản lý kinh doanh, Nhân viên kinh doanh và Đại lý."
        )

    # 1. AC: Tự động chạy migration an toàn bổ sung cột SQLite (tránh lỗi 500)
    try:
        _ensure_legacy_columns(db.get_bind())
    except Exception:
        pass

    load_dealers_db()

    # Kiểm tra tương thích với legacy tests & clients (khi không truyền query param nào hoặc request legacy format)
    is_legacy_request = (
        not request.query_params
        or format in ("legacy", "list")
        or request.query_params.get("format") in ("legacy", "list")
        or request.headers.get("x-legacy-response") == "true"
        or request.headers.get("x-format") == "list"
    )

    # Quyền & Roles
    is_admin = Role.SYSTEM_ADMIN.value in user_roles or current_user.role == Role.SYSTEM_ADMIN.value or "admin" in user_roles or current_user.role == "admin"
    is_sales_manager = Role.SALES_MANAGER.value in user_roles or current_user.role == Role.SALES_MANAGER.value or "sales_manager" in user_roles or current_user.role == "sales_manager"
    is_sales = ("sales" in user_roles or current_user.role == "sales" or Role.SALES.value in user_roles) and not (is_admin or is_sales_manager)
    is_customer = ("customer" in user_roles or current_user.role == "customer" or Role.CUSTOMER.value in user_roles) and not (is_admin or is_sales_manager or is_sales or "accountant" in user_roles)
    can_view_cost = is_admin or is_sales_manager or (hasattr(Permission, "COST_READ") and Permission.COST_READ.value in (current_user.permissions or []))

    # Đồng bộ các đơn in-memory nếu có vào DB để SQL query aggregation chính xác
    try:
        existing_db_codes = {r[0] for r in db.query(OrderEntity.order_code).all()}
        for o_id, mem_order in ORDERS_DB.items():
            ocode = mem_order.get("order_code")
            if ocode and ocode not in existing_db_codes:
                dt_str = mem_order.get("created_at")
                c_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00")) if dt_str else datetime.now(timezone.utc)
                db.add(OrderEntity(
                    id=mem_order.get("id", o_id),
                    order_code=ocode,
                    dealer_id=mem_order.get("dealer_id", 1),
                    dealer_name=mem_order.get("dealer_name", ""),
                    created_by=mem_order.get("created_by", ""),
                    assigned_sale_id=mem_order.get("assigned_sale_id"),
                    assigned_sale_name=mem_order.get("assigned_sale_name"),
                    total_amount=mem_order.get("total_amount", 0.0),
                    status=mem_order.get("status", "CONFIRMED"),
                    note=mem_order.get("note"),
                    delivery_point_id=mem_order.get("delivery_point_id"),
                    discount_rate=mem_order.get("discount_percent", 0.0),
                    discount_amount=mem_order.get("discount_amount", 0.0),
                    items_json=json.dumps(mem_order.get("items", []), ensure_ascii=False),
                    created_at=c_at,
                ))
                existing_db_codes.add(ocode)
        db.commit()
    except Exception:
        db.rollback()

    cur_page = max(1, page or 1)
    cur_page_size = max(1, min(page_size or 20, 100))

    # 2. Xử lý Row-Level Security (RLS)
    assigned_dealer_ids = set()
    if is_sales:
        assigned_user = USERS_DB.get(current_user.username) or next((u for u in USERS_DB.values() if u.id == current_user.id), None)
        target_sale_id = assigned_user.id if assigned_user else current_user.id
        assigned_ids = [d.id for d in DEALERS_DB.values() if getattr(d, "assigned_sale_id", None) == target_sale_id]
        sql_assigned_ids = [r[0] for r in db.query(DealerEntity.id).filter(DealerEntity.assigned_sale_id == target_sale_id).all()]
        assigned_dealer_ids = set(assigned_ids) | set(sql_assigned_ids)

        if not assigned_dealer_ids or (dealer_id is not None and dealer_id not in assigned_dealer_ids):
            if is_legacy_request:
                return []
            return OrderListFilteredResponse(
                items=[], total=0, total_count=0, page=cur_page, page_size=cur_page_size,
                filtered_total_amount=0.0, pending_count=0, confirmed_count=0
            )


    # 3. Xây dựng Query cơ sở
    query = db.query(OrderEntity)

    if is_sales:
        if dealer_id is not None:
            query = query.filter(OrderEntity.dealer_id == dealer_id)
        else:
            query = query.filter(OrderEntity.dealer_id.in_(list(assigned_dealer_ids)))
    elif is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        if customer_dealer:
            query = query.filter(OrderEntity.dealer_id == customer_dealer.id)
        else:
            if is_legacy_request:
                return []
            return OrderListFilteredResponse(
                items=[], total=0, total_count=0, page=cur_page, page_size=cur_page_size,
                filtered_total_amount=0.0, pending_count=0, confirmed_count=0
            )
    else:
        # Admin / Sales Manager / Accountant
        if dealer_id is not None:
            query = query.filter(OrderEntity.dealer_id == dealer_id)
        if sales_rep_id is not None:
            query = query.filter(OrderEntity.assigned_sale_id == sales_rep_id)

    # 4. Bộ lọc trạng thái
    if status and status.strip() and status.strip().upper() != "ALL":
        st = status.strip().upper()
        if st in ("PENDING_APPROVAL", "PENDING"):
            query = query.filter(OrderEntity.status.in_(["PENDING_APPROVAL", "PENDING"]))
        elif st == "CONFIRMED":
            query = query.filter(func.upper(OrderEntity.status) == "CONFIRMED")
        elif st == "COMPLETED":
            query = query.filter(OrderEntity.status.in_(["COMPLETED", "DELIVERED", "EXPORTED", "CLOSED"]))
        elif st == "CANCELLED":
            query = query.filter(func.upper(OrderEntity.status) == "CANCELLED")
        elif st == "REJECTED":
            query = query.filter(func.upper(OrderEntity.status) == "REJECTED")
        else:
            query = query.filter(func.upper(OrderEntity.status) == st)

    # 5. Bộ lọc khu vực (Region)
    if region and region.strip() and region.strip().upper() != "ALL":
        clean_reg = region.strip().lower()
        reg_db_ids = [d.id for d in DEALERS_DB.values() if d.region and clean_reg in d.region.lower()]
        reg_sql_ids = [r[0] for r in db.query(DealerEntity.id).filter(DealerEntity.region.ilike(f"%{clean_reg}%")).all()]
        matching_dealer_ids = list(set(reg_db_ids) | set(reg_sql_ids))
        if matching_dealer_ids:
            query = query.filter(OrderEntity.dealer_id.in_(matching_dealer_ids))
        else:
            query = query.filter(OrderEntity.id == -1)

    # 6. Bộ lọc khoảng thời gian
    parsed_start_date = None
    if start_date:
        try:
            if isinstance(start_date, (date, datetime)):
                parsed_start_date = start_date if isinstance(start_date, date) else start_date.date()
            elif str(start_date).strip():
                parsed_start_date = datetime.strptime(str(start_date).strip()[:10], "%Y-%m-%d").date()
        except Exception:
            pass

    parsed_end_date = None
    if end_date:
        try:
            if isinstance(end_date, (date, datetime)):
                parsed_end_date = end_date if isinstance(end_date, date) else end_date.date()
            elif str(end_date).strip():
                parsed_end_date = datetime.strptime(str(end_date).strip()[:10], "%Y-%m-%d").date()
        except Exception:
            pass

    if parsed_start_date:
        start_dt = datetime.combine(parsed_start_date, datetime.min.time())
        query = query.filter(OrderEntity.created_at >= start_dt)
    if parsed_end_date:
        end_dt = datetime.combine(parsed_end_date, datetime.max.time())
        query = query.filter(OrderEntity.created_at <= end_dt)

    # 7. Bộ lọc từ khóa tìm kiếm
    if search and search.strip():
        kw = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(OrderEntity.order_code).like(kw),
                func.lower(OrderEntity.dealer_name).like(kw),
                func.lower(OrderEntity.created_by).like(kw),
                func.lower(OrderEntity.assigned_sale_name).like(kw)
            )
        )

    # Nếu là legacy request không phân trang:
    if is_legacy_request:
        entities = query.order_by(OrderEntity.id.desc()).all()
        return [
            _format_order_response(rec, db, can_view_cost=can_view_cost, is_customer=is_customer, is_sales=is_sales)
            for rec in entities
        ]

    # 8. Tính toán Thống kê & Tổng tiền (Cả 4 KPI đều tính trên tập query đã áp dụng các bộ lọc)
    stats = query.with_entities(
        func.coalesce(func.sum(OrderEntity.total_amount), 0.0),
        func.count(OrderEntity.id),
        func.coalesce(func.sum(case((OrderEntity.status.in_(["PENDING_APPROVAL", "PENDING"]), 1), else_=0)), 0),
        func.coalesce(func.sum(case((OrderEntity.status == "CONFIRMED", 1), else_=0)), 0)
    ).first()

    filtered_total_amount = float(stats[0] or 0.0) if stats else 0.0
    total_count = int(stats[1] or 0) if stats else 0
    pending_count = int(stats[2] or 0) if stats else 0
    confirmed_count = int(stats[3] or 0) if stats else 0

    # 9. Sắp xếp (Ưu tiên PENDING_APPROVAL lên đầu, sau đó theo ID giảm dần)
    is_pending_expr = case((OrderEntity.status.in_(["PENDING_APPROVAL", "PENDING"]), 1), else_=0)
    items_query = query.order_by(is_pending_expr.desc(), OrderEntity.id.desc())

    # 10. Phân trang
    offset_val = (cur_page - 1) * cur_page_size
    records = items_query.offset(offset_val).limit(cur_page_size).all()

    items = [
        _format_order_response(rec, db, can_view_cost=can_view_cost, is_customer=is_customer, is_sales=is_sales)
        for rec in records
    ]

    return OrderListFilteredResponse(
        items=items,
        total=total_count,
        total_count=total_count,
        page=cur_page,
        page_size=cur_page_size,
        filtered_total_amount=round(filtered_total_amount, 2),
        pending_count=pending_count,
        confirmed_count=confirmed_count,
    )

@router.post("", response_model=OrderResponse, response_model_exclude_unset=True, status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_sales_or_admin_role)
):
    global NEXT_ORDER_ID
    load_dealers_db()

    # TIÊU CHÍ CHẤP NHẬN 3: BLOCK LOGIC ĐỘC LẬP CHO ROLE CUSTOMER
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = current_user.role == "customer" or current_user.role == "Customer" or (
        "customer" in [r.lower() for r in user_roles] and not any(r.lower() in ["admin", "sales_manager", "sales", "accountant"] for r in user_roles)
    )

    if current_user.role == 'Customer' or current_user.role == 'customer' or is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        # Ép cứng dealer_id / customer_id theo đúng tài khoản đại lý (Chống hacker gian lận đổi ID)
        if customer_dealer:
            data.dealer_id = customer_dealer.id
        else:
            data.dealer_id = current_user.id

    # 1. Tìm thông tin đại lý
    dealer = DEALERS_DB.get(data.dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {data.dealer_id}."
        )

    # Lỗi tạo đơn chéo / tài khoản đại lý bị khóa:
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = ("customer" in user_roles or "agent" in user_roles or current_user.role in ["customer", "agent"]) and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    if is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        if customer_dealer:
            if _is_dealer_locked(customer_dealer):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Đại lý '{customer_dealer.name}' hiện đang bị KHÓA giao dịch. Không thể tạo đơn hàng mới."
                )
            # Bắt buộc gán dealer_id theo đúng tài khoản đại lý đang đăng nhập (chống gian lận đặt hộ)
            data.dealer_id = customer_dealer.id
            dealer = customer_dealer
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
            data.dealer_id = dealer.id

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

    # 3. TIÊU CHÍ 3 (Hard Block): Kiểm tra nợ quá hạn trước khi kiểm tra tổng hạn mức công nợ (Tiêu chí 2)
    debt_summary = validate_dealer_overdue_debt(dealer, ORDERS_DB)
    current_debt = debt_summary["current_debt"]
    credit_limit = debt_summary["credit_limit"]
    overdue_days_allowed = debt_summary["overdue_days_allowed"]
    max_debt_age = debt_summary["max_debt_age"]
    is_overdue = debt_summary["is_overdue"]

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
                fl_val = pbi.floor_price if (pbi.floor_price is not None and pbi.floor_price > 0) else (pbi.min_price if (getattr(pbi, "min_price", None) is not None and pbi.min_price > 0) else None)
                if fl_val is not None and it.price < fl_val:
                    requires_approval = True
                    approval_reasons.append(
                        f"Bán dưới giá sàn: {cur_name} có đơn giá {it.price:,.0f} đ thấp hơn giá sàn {fl_val:,.0f} đ (Bảng giá: {pb_matched.name})"
                    )
                elif fl_val is None and listed_price is not None and listed_price > 0 and it.price < listed_price:
                    requires_approval = True
                    approval_reasons.append(
                        f"Bán dưới giá niêm yết: {cur_name} có đơn giá {it.price:,.0f} đ thấp hơn giá niêm yết {listed_price:,.0f} đ"
                    )
            else:
                if listed_price is not None and listed_price > 0 and it.price < listed_price:
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
    is_sales_or_admin = any(r in ["admin", "sales_manager", "sales"] for r in user_roles)
    is_customer = ("customer" in user_roles or "agent" in user_roles or current_user.role in ["customer", "agent"]) and not is_sales_or_admin
    if is_customer:
        requires_approval = True
        approval_reasons.append("Đơn hàng do Đại lý tự đặt từ Cổng Đại lý (Chờ nhân viên phụ trách xác nhận)")

    discount_amount = round(subtotal_amount * effective_pct / 100, 2)
    final_total_amount = subtotal_amount - discount_amount

    # TIÊU CHÍ 2 (Vượt hạn mức): Tổng đơn cộng công nợ hiện tại vượt hạn mức thì đơn bị đánh dấu cần duyệt
    is_admin = any(r.lower() == "admin" for r in user_roles) or (current_user.role and current_user.role.lower() == "admin")
    if not is_admin and ((current_debt + final_total_amount) > credit_limit or over_limit):
        requires_approval = True
        approval_reasons.append(
            f"Vượt hạn mức công nợ: Tổng nợ sau đơn ({current_debt + final_total_amount:,.0f} đ) vượt hạn mức cho phép ({credit_limit:,.0f} đ)"
        )

    order_id = _allocate_order_id(db)

    order_code = f"ORD{order_id:05d}"
    now_str = datetime.now(timezone.utc).isoformat()
    # Nếu là Customer (Đại lý tự đặt): Status BẮT BUỘC bị ép thành "PENDING_APPROVAL" (Chờ duyệt).
    # Nếu là Admin: Luôn tự động CONFIRMED (Admin có thẩm quyền tối cao, không cần cấp dưới duyệt đơn).
    # Nếu là Sales: Nếu vi phạm chính sách giá/hạn mức thì PENDING_APPROVAL, ngược lại CONFIRMED.
    if is_admin:
        requires_approval = False
        approval_reasons.clear()
        order_status = "CONFIRMED"
    else:
        order_status = "PENDING_APPROVAL" if (is_customer or requires_approval) else "CONFIRMED"
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
        "note": data.note,
        "dealer_status": getattr(dealer, "status", "Đang hoạt động"),
        "dealer_lock_reason": None if ("customer" in current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]) else getattr(dealer, "lock_reason", None),
    }
    if data.delivery_point is not None:
        order_record["delivery_point"] = delivery_point_str
    if data.desired_delivery_date is not None:
        order_record["desired_delivery_date"] = data.desired_delivery_date.isoformat()
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

    assigned_sale_id = dealer.assigned_sale_id or 3
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
    ALLOWED_ORDER_CREATORS = ["admin", "sales_manager", "sales", "customer", "agent"]
    if not any(r.lower() in ALLOWED_ORDER_CREATORS for r in user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Chức năng này chỉ dành cho Nhân viên kinh doanh, Quản trị hệ thống và Đại lý."
        )

    # AC 3: TIÊU CHÍ 3 (Hard Block) - Kiểm tra nợ quá hạn trước khi kiểm tra tổng hạn mức
    debt_summary = validate_dealer_overdue_debt(dealer, ORDERS_DB)
    current_debt = debt_summary["current_debt"]
    credit_limit = debt_summary["credit_limit"]
    overdue_days_allowed = debt_summary["overdue_days_allowed"]
    max_debt_age = debt_summary["max_debt_age"]
    is_overdue = debt_summary["is_overdue"]

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
    is_admin = any(r.lower() == "admin" for r in user_roles) or (current_user.role and current_user.role.lower() == "admin")
    is_sales_or_admin = any(r in ["admin", "sales_manager", "sales"] for r in user_roles)
    is_customer = ("customer" in user_roles or "agent" in user_roles or current_user.role in ["customer", "agent"]) and not is_sales_or_admin
    if is_customer:
        requires_approval = True
        approval_reasons.append("Đơn hàng do Đại lý tự đặt từ Cổng Đại lý (Chờ nhân viên phụ trách xác nhận)")

    discount_amount = round(subtotal_amount * effective_pct / 100, 2)
    final_total_amount = subtotal_amount - discount_amount

    # TIÊU CHÍ 2 (Vượt hạn mức): Tổng đơn cộng công nợ hiện tại vượt hạn mức thì đơn bị đánh dấu cần duyệt
    if not is_admin and (current_debt + final_total_amount) > credit_limit:
        requires_approval = True
        approval_reasons.append(
            f"Vượt hạn mức công nợ: Tổng nợ sau đơn ({current_debt + final_total_amount:,.0f} đ) vượt hạn mức cho phép ({credit_limit:,.0f} đ)"
        )

    if is_admin:
        requires_approval = False
        approval_reasons.clear()
        order_status = "CONFIRMED"
    else:
        order_status = "PENDING_APPROVAL" if (is_customer or requires_approval) else "CONFIRMED"
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
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = current_user.role == "customer" or current_user.role == "Customer" or (
        "customer" in [r.lower() for r in user_roles] and not any(r.lower() in ["admin", "sales_manager", "sales", "accountant"] for r in user_roles)
    )

    if ("purchasing" in [r.lower() for r in user_roles]) and not is_customer and current_user.username != "muahang":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Nhân viên mua hàng không có quyền truy cập đơn hàng bán."
        )

    load_dealers_db()
    order_record = next(
        (
            order
            for order in ORDERS_DB.values()
            if order.get("order_code", "").upper() == order_code.upper() or str(order.get("id")) == str(order_code)
        ),
        None,
    )
    entity = db.query(OrderEntity).filter(
        func.upper(OrderEntity.order_code) == order_code.upper()
    ).first()

    if entity is None and str(order_code).isdigit():
        entity = db.query(OrderEntity).filter(OrderEntity.id == int(order_code)).first()
        if entity is None and order_record is None:
            order_record = ORDERS_DB.get(int(order_code))

    if entity is None and order_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã {order_code}.",
        )

    # Data Isolation: Nếu là đại lý, chỉ được xem chi tiết đơn hàng của chính mình
    if is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        target_dealer_id = customer_dealer.id if customer_dealer else current_user.id
        chk_dealer_id = entity.dealer_id if entity else (order_record.get("dealer_id") if order_record else None)
        chk_created_by = entity.created_by if entity else (order_record.get("created_by") if order_record else None)
        if chk_dealer_id != target_dealer_id and (not chk_created_by or chk_created_by.lower() != current_user.username.lower()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Truy cập bị từ chối (403 Forbidden). Bạn chỉ được phép xem đơn hàng của chính mình."
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
                "cancel_reason": entity.cancel_reason,
                "cancelled_by": entity.cancelled_by,
                "cancelled_at": entity.cancelled_at.isoformat() if entity.cancelled_at else None,
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
            if entity.cancel_reason:
                order_record["cancel_reason"] = entity.cancel_reason
            if entity.cancelled_by:
                order_record["cancelled_by"] = entity.cancelled_by
            if entity.cancelled_at:
                order_record["cancelled_at"] = entity.cancelled_at.isoformat() if hasattr(entity.cancelled_at, "isoformat") else str(entity.cancelled_at)

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
                        "applied_policy_code",
                        "applied_policy_name",
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
            d_id = order_record.get("dealer_id")
            try:
                d_id = int(d_id)
            except (ValueError, TypeError):
                pass
            d = DEALERS_DB.get(d_id)
            if not d:
                d = next((dl for dl in DEALERS_DB.values() if str(dl.id) == str(order_record.get("dealer_id")) or dl.code == str(order_record.get("dealer_id"))), None)
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
    d_id = order_record.get("dealer_id")
    try:
        d_id = int(d_id)
    except (ValueError, TypeError):
        pass
    d = DEALERS_DB.get(d_id)
    if not d:
        d = next((dl for dl in DEALERS_DB.values() if str(dl.id) == str(order_record.get("dealer_id")) or dl.code == str(order_record.get("dealer_id"))), None)
    d_status = getattr(d, "status", "Đang hoạt động") if d else "Đang hoạt động"
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    if is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        ord_dealer_id = order_record.get("dealer_id")
        dealer_target_ids = {str(customer_dealer.id), str(getattr(customer_dealer, "code", "")).upper()} if customer_dealer else set()
        ord_dealer_name = (order_record.get("dealer_name") or "").strip().lower()
        cust_name = (getattr(customer_dealer, "name", "") or "").strip().lower() if customer_dealer else ""
        if not customer_dealer or (
            str(ord_dealer_id) not in dealer_target_ids and
            ord_dealer_name != cust_name
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xem đơn hàng của chính mình."
            )

    ord_st = (order_record.get("status") or "").upper()
    is_order_dispatched = ord_st in ("DELIVERED", "CLOSED", "COMPLETED", "EXPORTED", "SHIPPED")
    for item in items:
        is_dict = isinstance(item, dict)
        qty = item.get("quantity", 0) if is_dict else getattr(item, "quantity", 0)
        del_qty = item.get("delivered_quantity") if is_dict else getattr(item, "delivered_quantity", None)
        if is_order_dispatched:
            if del_qty is None:
                del_qty = qty
            shortage = max(0, int(qty) - int(del_qty))
        else:
            del_qty = None
            shortage = 0

        if is_dict:
            item["delivered_quantity"] = del_qty
            item["shortage_quantity"] = shortage
        else:
            setattr(item, "delivered_quantity", del_qty)
            setattr(item, "shortage_quantity", shortage)

    d_lock_reason = None if is_customer else (getattr(d, "lock_reason", None) if d else None)

    detail_response = dict(order_record)
    detail_response.update({
        "items": items,
        "subtotal_amount": subtotal_amount,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "applied_policy_code": order_record.get("applied_policy_code"),
        "applied_policy_name": order_record.get("applied_policy_name"),
        "dealer_status": d_status,
        "dealer_lock_reason": d_lock_reason,
        "dealer_code": getattr(d, "code", None) if d else None,
        "dealer_phone": getattr(d, "phone", None) if d else None,
        "dealer_address": getattr(d, "address", None) if d else None,
    })
    return SalesOrderResponse(**detail_response)

@router.get("/{order_code}/clone-data", response_model=SalesOrderResponse)
def get_order_clone_data(
    order_code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value)),
):
    """Lấy dữ liệu để sao chép đơn. Tính lại giá theo bảng sản phẩm (products.sell_price) / price_book hiện tại và discount hiện hành, bỏ SP inactive."""
    # 1. Load original order
    original_order = get_order_detail(order_code, db, current_user).dict()
    
    # 2. Get current dealer
    dealer_id = original_order.get("dealer_id")
    dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer:
        dealer = DEALERS_DB.get(dealer_id)
        if not dealer:
            dealer = next((dl for dl in DEALERS_DB.values() if str(dl.id) == str(dealer_id)), None)
        
    customer_group = getattr(dealer, "customer_group", "dai_ly_cap_1") if dealer else "dai_ly_cap_1"
    
    from app.api.v1.endpoints.price_books import get_utc_now
    from app.api.v1.endpoints.products import _find_product_in_raw
    
    new_items = []
    subtotal = 0.0
    now = get_utc_now()
    
    # Normalize group
    aliases = [customer_group]
    if str(customer_group).lower() in ["cap_1", "dai_ly_cap_1", "đại lý cấp 1"]:
        aliases = ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1", "dai_ly_cap_1"]
    elif str(customer_group).lower() in ["cap_2", "dai_ly_cap_2", "đại lý cấp 2"]:
        aliases = ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2", "dai_ly_cap_2"]
    elif str(customer_group).lower() in ["retail", "khach_le", "khách lẻ"]:
        aliases = ["Khach_le", "RETAIL", "Khách lẻ", "khach_le"]
        
    for item in original_order.get("items", []):
        product_id = item.get("product_id")
        sku = item.get("product_code") or item.get("code")
        
        # Check product entity & active status
        prod_entity = None
        if product_id:
            try:
                prod_entity = db.query(ProductEntity).filter(ProductEntity.id == int(product_id)).first()
            except Exception:
                pass
        if not prod_entity and sku:
            prod_entity = db.query(ProductEntity).filter(ProductEntity.code == sku).first()
            if prod_entity:
                product_id = prod_entity.id
                item["product_id"] = product_id

        raw_p = None
        if product_id:
            try:
                raw_p = _find_product_in_raw(int(product_id))
            except Exception:
                raw_p = _find_product_in_raw(product_id)
        if not raw_p and sku:
            for p in RAW_PRODUCTS:
                if p.get("code") == sku:
                    raw_p = p
                    break

        prod_status = raw_p.get("status") if raw_p else (getattr(prod_entity, "status", "active") if prod_entity else "active")
        
        if str(prod_status).lower() in ["inactive", "ngừng kinh doanh", "ngung_kinh_doanh"]:
            # Bỏ qua SP ngừng kinh doanh
            continue
            
        # Đảm bảo có product_name và product_code
        if prod_entity:
            item["product_name"] = prod_entity.name
            item["product_code"] = prod_entity.code
        elif raw_p:
            item["product_name"] = raw_p.get("name")
            item["product_code"] = raw_p.get("code")

        # 1. Bắt buộc truy vấn lại giá niêm yết hiện hành từ bảng sản phẩm (products.sell_price)
        current_sell_price = None
        if prod_entity and getattr(prod_entity, "sell_price", None) is not None and float(prod_entity.sell_price) > 0:
            current_sell_price = float(prod_entity.sell_price)
        elif raw_p and raw_p.get("sell_price") is not None and float(raw_p.get("sell_price")) > 0:
            current_sell_price = float(raw_p.get("sell_price"))
            
        # Fallback từ price_book nếu sản phẩm chưa có sell_price
        if current_sell_price is None:
            try:
                pb_item = db.query(PriceBookItemEntity).join(PriceBookEntity).filter(
                    PriceBookItemEntity.product_id == (prod_entity.id if prod_entity else product_id),
                    func.upper(PriceBookEntity.status) == "ACTIVE",
                    PriceBookEntity.customer_group.in_(aliases),
                    PriceBookEntity.valid_from <= now,
                    PriceBookEntity.valid_to >= now
                ).order_by(PriceBookEntity.id.desc()).first()
                
                if pb_item and pb_item.sale_price is not None and float(pb_item.sale_price) > 0:
                    current_sell_price = float(pb_item.sale_price)
            except Exception:
                pass

        if current_sell_price is None:
            current_sell_price = float(item.get("unit_price") or item.get("price") or 0)
                
        # Ghi đè unit_price và price mới nhất vào item trả về
        item["price"] = current_sell_price
        item["unit_price"] = current_sell_price
        qty = float(item.get("quantity", 0))
        item["subtotal"] = round(current_sell_price * qty, 2)
        subtotal += current_sell_price * qty
        new_items.append(item)
        
    original_order["items"] = new_items
    original_order["subtotal_amount"] = round(subtotal, 2)
    
    # 2. Áp dụng lại chính sách chiết khấu sản lượng (discounts) theo số lượng sản phẩm hiện có
    discount_amount = 0.0
    discount_percent = 0.0
    try:
        from app.api.v1.endpoints.discounts import resolve_best_discount
        total_quantity = sum(float(it.get("quantity", 0)) for it in new_items)
        discount_res = resolve_best_discount(
            items=new_items,
            dealer=dealer,
            total_quantity=total_quantity,
            subtotal_amount=subtotal,
            db=db,
        )
        discount_percent = float(discount_res.get("discount_percent", 0.0))
        discount_amount = float(discount_res.get("discount_amount", 0.0))
        if discount_percent > 0 and discount_amount == 0:
            discount_amount = round(subtotal * discount_percent / 100.0, 2)
        original_order["discount_percent"] = discount_percent
        original_order["discount_rate"] = discount_percent
        original_order["discount_amount"] = round(discount_amount, 2)
        original_order["applied_policy_code"] = discount_res.get("applied_policy_code")
        original_order["applied_policy_name"] = discount_res.get("applied_policy_name")
    except Exception:
        original_order["discount_percent"] = 0.0
        original_order["discount_rate"] = 0.0
        original_order["discount_amount"] = 0.0
        
    original_order["total_amount"] = max(0.0, round(subtotal - discount_amount, 2))
    original_order["status"] = "DRAFT" # Luôn ở trạng thái Nháp
    original_order["id"] = 0 # Xoá id cũ
    original_order["order_code"] = "" # Xoá mã đơn
    
    return SalesOrderResponse(**original_order)


def _resolve_current_product_price(db: Session, dealer_id: int, product_id: int) -> tuple[float, Optional[float], Optional[str]]:
    """
    Quy tắc giá (Requirement 5):
    - Không lấy giá từ OrderDetail của đơn cũ.
    - Lấy giá hiện hành từ bảng giá có hiệu lực của nhóm khách hàng / đại lý nếu có.
    - Nếu không có bảng giá hoặc sản phẩm không nằm trong bảng giá, lấy giá niêm yết hiện tại của sản phẩm.
    """
    now = datetime.now(timezone.utc)
    dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first() if db else None
    if not dealer:
        dealer = DEALERS_DB.get(dealer_id)
    cust_group = getattr(dealer, "customer_group", None) or "dai_ly_cap_1"

    aliases = [cust_group]
    if cust_group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
        aliases = ["dai_ly_cap_1", "Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1"]
    elif cust_group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
        aliases = ["dai_ly_cap_2", "Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2"]
    elif cust_group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
        aliases = ["khach_le", "Khach_le", "RETAIL", "Khách lẻ"]

    item_match = None
    if db:
        item_match = db.query(PriceBookItemEntity, PriceBookEntity).join(
            PriceBookEntity, PriceBookItemEntity.price_book_id == PriceBookEntity.id
        ).filter(
            PriceBookEntity.customer_group.in_(aliases),
            PriceBookEntity.status == "ACTIVE",
            PriceBookEntity.valid_from <= now,
            PriceBookEntity.valid_to >= now,
            PriceBookItemEntity.product_id == product_id
        ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

    from app.api.v1.endpoints.products import _find_product_in_raw
    raw_p = _find_product_in_raw(product_id)
    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == product_id).first() if db else None
    listed_price = (prod_entity.sell_price if prod_entity and prod_entity.sell_price is not None else (raw_p.get("sell_price") if raw_p else 0.0)) or 0.0
    floor_price = (prod_entity.floor_price if prod_entity and prod_entity.floor_price is not None else (raw_p.get("floor_price") if raw_p else 0.0)) or 0.0

    if item_match:
        pbi, pb_matched = item_match
        sale_val = pbi.sale_price if (pbi.sale_price is not None and pbi.sale_price > 0) else (pbi.price if (pbi.price is not None and pbi.price > 0) else listed_price)
        floor_val = pbi.floor_price if (pbi.floor_price is not None and pbi.floor_price > 0) else (pbi.min_price if (pbi.min_price is not None and pbi.min_price > 0) else floor_price or sale_val)

        # Bắt buộc đồng bộ với giá niêm yết và giá sàn mới nhất từ sản phẩm nếu có
        # (trừ khi bảng giá có version > 1 được chỉ định riêng biệt)
        if listed_price > 0 and getattr(pb_matched, "version", 1) <= 1:
            sale_val = listed_price
            if floor_price > 0:
                floor_val = floor_price

        price_note = pb_matched.name or pb_matched.code
        return float(sale_val or listed_price), float(floor_val) if floor_val is not None else None, price_note

    return float(listed_price), float(floor_price or listed_price), "Giá niêm yết"


def _check_product_reorder_status(db: Session, product_id: int) -> tuple[bool, Optional[str], Optional[dict]]:
    """
    Quy tắc trạng thái sản phẩm (Requirement 6):
    - Kiểm tra sản phẩm có tồn tại và đang kinh doanh hay không.
    - Trả về (is_active, reason, product_info)
    """
    from app.api.v1.endpoints.products import _find_product_in_raw
    raw_p = _find_product_in_raw(product_id)
    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == product_id).first() if db else None

    if not raw_p and not prod_entity:
        return False, "Sản phẩm không còn tồn tại", None

    # Xác định trạng thái
    status_val = "active"
    if prod_entity and prod_entity.status is not None:
        status_val = prod_entity.status
    if raw_p and raw_p.get("status") is not None:
        if raw_p.get("status") == "inactive" or status_val == "inactive":
            status_val = "inactive"
        elif not prod_entity:
            status_val = raw_p.get("status")

    prod_code = prod_entity.code if prod_entity else (raw_p.get("code") if raw_p else f"SP#{product_id}")
    prod_name = prod_entity.name if prod_entity else (raw_p.get("name") if raw_p else f"SP #{product_id}")

    status_str = str(status_val).strip().lower()
    if status_str in ["inactive", "ngừng kinh doanh", "ngừng giao dịch", "disabled", "tam_ngung"] or status_str != "active":
        return False, "Sản phẩm đã ngừng kinh doanh", {
            "id": product_id,
            "code": prod_code,
            "name": prod_name,
        }

    base_unit = (prod_entity.base_unit if prod_entity and prod_entity.base_unit else (raw_p.get("base_unit") if raw_p else "Cái")) or "Cái"
    units_list = (prod_entity.units if prod_entity and prod_entity.units else (raw_p.get("units") if raw_p else [])) or []
    stock = (prod_entity.stock if prod_entity and prod_entity.stock is not None else (raw_p.get("stock", 0) if raw_p else 0)) or 0

    return True, None, {
        "id": product_id,
        "code": prod_code,
        "name": prod_name,
        "base_unit": base_unit,
        "units": units_list,
        "stock": stock,
    }


@router.post("/{order_id}/reorder", response_model=ReorderResponse)
def reorder_order(
    order_id: str,
    payload: Optional[ReorderRequest] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_WRITE.value))
):
    """
    Đặt lại đơn hàng cũ (Reorder):
    Hỗ trợ:
    - Đặt lại toàn bộ một đơn hàng cũ (payload.product_id is None).
    - Đặt lại riêng một dòng sản phẩm trong đơn hàng cũ (payload.product_id is not None).
    - Kiểm tra quyền Đại lý, chặn Đại lý reorder đơn của Đại lý khác.
    - Loại bỏ sản phẩm ngừng kinh doanh (status != 'active') hoặc không còn tồn tại.
    - Lấy giá hiện hành của hệ thống, không dùng giá cũ.
    - Giữ nguyên số lượng của đơn cũ.
    - Trả về danh sách hợp lệ và danh sách bị loại kèm lý do chi tiết.
    - Tùy chọn create_immediate=True để tạo luôn đơn hàng mới vào DB.
    """
    if payload is None:
        payload = ReorderRequest()

    load_dealers_db()

    # 1. Tra cứu đơn hàng cũ (theo ID số hoặc mã đơn)
    target_order_record = None
    for o in ORDERS_DB.values():
        if str(o.get("id")) == str(order_id) or str(o.get("order_code", "")).upper() == str(order_id).upper():
            target_order_record = o
            break

    entity = None
    if db:
        try:
            num_id = int(order_id)
            entity = db.query(OrderEntity).filter(OrderEntity.id == num_id).first()
        except (ValueError, TypeError):
            pass
        if not entity:
            entity = db.query(OrderEntity).filter(func.upper(OrderEntity.order_code) == str(order_id).upper()).first()

    if not entity and not target_order_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng '{order_id}'."
        )

    order_db_id = entity.id if entity else target_order_record["id"]
    order_code_val = entity.order_code if entity else target_order_record["order_code"]
    dealer_id = entity.dealer_id if entity else target_order_record["dealer_id"]
    dealer_name = entity.dealer_name if entity else target_order_record["dealer_name"]
    delivery_point_id = entity.delivery_point_id if entity else target_order_record.get("delivery_point_id")

    # 2. Phân quyền Đại lý (Case 7): Chặn Đại lý reorder đơn của Đại lý khác
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    if is_customer or current_user.role == "customer":
        customer_dealer = _get_customer_dealer(current_user)
        if customer_dealer:
            if _is_dealer_locked(customer_dealer):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Đại lý '{customer_dealer.name}' hiện đang bị KHÓA giao dịch. Không thể đặt lại đơn hàng."
                )
            if dealer_id != customer_dealer.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tài khoản đại lý chỉ được phép đặt lại đơn hàng của chính mình."
                )
        else:
            d_obj = DEALERS_DB.get(dealer_id)
            is_own = (dealer_id == current_user.id) or \
                     (d_obj and d_obj.email and d_obj.email == current_user.email) or \
                     (d_obj and d_obj.phone and d_obj.phone == current_user.phone) or \
                     (d_obj and d_obj.code and d_obj.code.lower() == current_user.username.lower())
            if not is_own:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tài khoản đại lý chỉ được phép đặt lại đơn hàng của chính mình."
                )

    # 3. Lấy danh sách sản phẩm trong đơn cũ
    items = []
    if entity and entity.items_json:
        try:
            stored_details = json.loads(entity.items_json)
            if isinstance(stored_details, list):
                items = stored_details
            elif isinstance(stored_details, dict):
                items = stored_details.get("items", [])
        except Exception:
            pass
    if not items and target_order_record and target_order_record.get("items"):
        items = target_order_record.get("items", [])

    # Nếu đơn hàng cũ không có dòng sản phẩm nào (Case 9)
    if not items:
        return ReorderResponse(
            order_id=order_db_id,
            order_code=order_code_val,
            dealer_id=dealer_id,
            dealer_name=dealer_name,
            valid_items=[],
            excluded_items=[],
            total_valid=0,
            total_excluded=0,
            can_reorder=False,
            message="Đơn hàng cũ không có sản phẩm nào để đặt lại.",
        )

    # Nếu đặt lại một dòng hàng cụ thể (Requirement 4)
    if payload.product_id is not None:
        matched_items = [it for it in items if it.get("product_id") == payload.product_id]
        if not matched_items:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy dòng sản phẩm có ID {payload.product_id} trong đơn hàng {order_code_val}."
            )
        items = matched_items

    # 4. Kiểm tra từng sản phẩm
    valid_items: list[ReorderItemDetail] = []
    excluded_items: list[ExcludedItemDetail] = []

    for item in items:
        pid = item.get("product_id")
        old_qty = int(item.get("quantity", 1))
        old_price = float(item.get("price", 0.0))
        item_unit = item.get("unit") or item.get("unit_name") or "Cái"
        item_rate = float(item.get("conversion_rate", 1.0))
        item_name = item.get("product_name") or f"SP #{pid}"
        item_code = item.get("product_code")

        is_active, exc_reason, prod_info = _check_product_reorder_status(db, pid)
        if not is_active:
            excluded_items.append(ExcludedItemDetail(
                product_id=pid,
                product_code=prod_info["code"] if prod_info else item_code,
                product_name=prod_info["name"] if prod_info else item_name,
                reason=exc_reason or "Sản phẩm đã ngừng kinh doanh"
            ))
            continue

        # Lấy giá hiện hành (Requirement 5)
        current_price, current_floor, price_note = _resolve_current_product_price(db, dealer_id, pid)

        base_unit = prod_info.get("base_unit", "Cái")
        raw_units = prod_info.get("units", [])
        avail_units = [{"unit_name": base_unit, "conversion_rate": 1.0, "is_base": True}]
        for u in raw_units:
            if u.get("unit_name") != base_unit:
                avail_units.append({
                    "unit_name": u.get("unit_name"),
                    "conversion_rate": float(u.get("conversion_rate", 1.0)),
                    "is_base": False
                })

        valid_items.append(ReorderItemDetail(
            product_id=pid,
            product_code=prod_info["code"],
            product_name=prod_info["name"],
            quantity=old_qty,
            price=current_price,
            old_price=old_price,
            unit=item_unit,
            unit_name=item_unit,
            conversion_rate=item_rate,
            base_unit=base_unit,
            stock=prod_info.get("stock", 0),
            available_units=avail_units,
            price_note=price_note
        ))

    total_valid = len(valid_items)
    total_excluded = len(excluded_items)
    can_reorder = total_valid > 0

    if not can_reorder:
        if payload.product_id is not None and total_excluded > 0:
            message = f"Sản phẩm {excluded_items[0].product_name} hiện đã ngừng kinh doanh và không thể đặt lại."
        else:
            message = "Tất cả sản phẩm trong đơn hàng này hiện đã ngừng kinh doanh. Không thể đặt lại đơn."
    else:
        if total_excluded > 0:
            message = "Đặt lại đơn thành công một phần. Một số sản phẩm đã bị loại do ngừng kinh doanh hoặc không còn tồn tại."
        else:
            message = "Đặt lại đơn thành công."

    created_order_resp = None
    if payload.create_immediate and can_reorder:
        new_order_data = OrderCreate(
            dealer_id=dealer_id,
            items=[
                OrderItemCreate(
                    product_id=it.product_id,
                    quantity=it.quantity,
                    price=it.price,
                    unit=it.unit,
                    unit_name=it.unit_name,
                    conversion_rate=it.conversion_rate,
                )
                for it in valid_items
            ],
            delivery_point_id=payload.delivery_point_id or delivery_point_id,
            note=payload.note or f"Đặt lại từ đơn {order_code_val}",
        )
        created_order_resp = create_order(
            data=new_order_data,
            request=request,
            db=db,
            current_user=current_user,
        )

    return ReorderResponse(
        order_id=order_db_id,
        order_code=order_code_val,
        dealer_id=dealer_id,
        dealer_name=dealer_name,
        valid_items=valid_items,
        excluded_items=excluded_items,
        total_valid=total_valid,
        total_excluded=total_excluded,
        can_reorder=can_reorder,
        message=message,
        created_order=created_order_resp,
    )


@router.post("/{order_code}/approve", response_model=OrderResponse)
def approve_order(
    order_code: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user)
):
    """
    Quyền duyệt đơn hàng:
    - sales_manager và admin: Toàn quyền duyệt mọi đơn hàng.
    - sales: Được phép duyệt đơn hàng của đại lý do mình phụ trách (hoặc đơn do đại lý tự đặt).
      (Chặn sales tự duyệt đơn bán dưới giá sàn do chính sales tạo ra).
    - Các vai trò khác (kho, kế toán, mua hàng, customer): Bị chặn 403 Forbidden.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_admin_or_sm = any(r.lower() in ["admin", "sales_manager"] for r in user_roles)
    is_sales = any(r.lower() == "sales" for r in user_roles)

    if not (is_admin_or_sm or is_sales):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Bạn không có quyền duyệt đơn hàng."
        )

    target = None
    for o in ORDERS_DB.values():
        if o["order_code"].upper() == order_code.upper() or str(o.get("id")) == str(order_code):
            target = o
            break

    db_order = db.query(OrderEntity).filter(
        (func.upper(OrderEntity.order_code) == order_code.upper()) | (OrderEntity.id == (target["id"] if target else -1)) | (str(OrderEntity.id) == str(order_code))
    ).first()

    if not target and not db_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã hoặc ID {order_code}."
        )

    assigned_id = target.get("assigned_sale_id") if target else (db_order.assigned_sale_id if db_order else None)
    created_by = target.get("created_by") if target else (db_order.created_by if db_order else "")
    approval_reason = (target.get("approval_reason") if target else (db_order.approval_reason if db_order else "")) or ""
    is_floor_price_order = "giá sàn" in approval_reason.lower()

    if is_sales and not is_admin_or_sm:
        # 1. Tuyệt đối không cho sales duyệt đơn do Admin hoặc Quản lý cấp trên tạo
        creator_user = USERS_DB.get(created_by.lower()) if created_by else None
        creator_roles = creator_user.get_roles() if creator_user else []
        is_creator_admin_or_sm = any(r.lower() in ["admin", "sales_manager"] for r in creator_roles) or created_by.lower() in ["admin", "sales_manager"]
        if is_creator_admin_or_sm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Nhân viên kinh doanh không được phép duyệt đơn hàng do Quản trị viên (Admin) hoặc Quản lý kinh doanh tạo."
            )

        # 2. Nhân viên bán hàng CHỈ ĐƯỢC DUYỆT đại lý do mình phụ trách
        if not assigned_id or assigned_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn chỉ có quyền duyệt đơn hàng của đại lý do mình phụ trách."
            )

        # 3. Nếu là đơn do chính sales tự tạo bán dưới giá sàn -> Không được tự duyệt
        if is_floor_price_order and created_by.lower() == current_user.username.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Nhân viên kinh doanh không được phép tự duyệt đơn hàng bán dưới giá sàn của chính mình."
            )

    old_status = target.get("status") if target else db_order.status
    now_iso = datetime.now(timezone.utc).isoformat()

    if target:
        target["status"] = "CONFIRMED"
        target["requires_approval"] = False
        target["approved_by"] = current_user.username
        target["approved_at"] = now_iso

    if db_order:
        db_order.status = "CONFIRMED"
        db_order.requires_approval = False
        try:
            p_dict = json.loads(db_order.items_json) if db_order.items_json else {}
            p_dict["requires_approval"] = False
            p_dict["approved_by"] = current_user.username
            p_dict["approved_at"] = now_iso
            db_order.items_json = json.dumps(p_dict, ensure_ascii=False)
        except Exception:
            pass
        db.commit()

    if not target and db_order:
        target = {
            "id": db_order.id,
            "order_code": db_order.order_code,
            "dealer_id": db_order.dealer_id,
            "dealer_name": db_order.dealer_name,
            "created_by": db_order.created_by,
            "assigned_sale_id": db_order.assigned_sale_id,
            "assigned_sale_name": db_order.assigned_sale_name,
            "total_amount": db_order.total_amount,
            "status": "CONFIRMED",
            "requires_approval": False,
            "approved_by": current_user.username,
            "approved_at": now_iso,
        }
        ORDERS_DB[db_order.id] = target

    log_audit_event(
        db=db,
        user=current_user,
        action_type="ORDER_APPROVE",
        entity_type="Order",
        entity_id=target["order_code"],
        old_val={"status": old_status},
        new_val={"status": "CONFIRMED"},
        reason="Duyệt và xác nhận đơn hàng",
        request=request,
    )

    return OrderResponse(**target)


class OrderRejectRequest(BaseModel):
    reason: Optional[str] = "Từ chối duyệt đơn hàng"


@router.post("/{order_code}/reject", response_model=OrderResponse)
def reject_order(
    order_code: str,
    request: Request,
    payload: Optional[OrderRejectRequest] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user)
):
    """
    Từ chối đơn hàng: sales_manager, admin hoặc nhân viên sales phụ trách đại lý.
    Chuyển đơn từ PENDING_APPROVAL sang REJECTED.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_admin_or_sm = any(r.lower() in ["admin", "sales_manager"] for r in user_roles)
    is_sales = any(r.lower() == "sales" for r in user_roles)

    if not (is_admin_or_sm or is_sales):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Bạn không có quyền từ chối đơn hàng."
        )

    target = None
    for o in ORDERS_DB.values():
        if o["order_code"].upper() == order_code.upper() or str(o.get("id")) == str(order_code):
            target = o
            break

    db_order = db.query(OrderEntity).filter(
        (func.upper(OrderEntity.order_code) == order_code.upper()) | (OrderEntity.id == (target["id"] if target else -1)) | (str(OrderEntity.id) == str(order_code))
    ).first()

    if not target and not db_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã hoặc ID {order_code}."
        )

    assigned_id = target.get("assigned_sale_id") if target else (db_order.assigned_sale_id if db_order else None)
    created_by = target.get("created_by") if target else (db_order.created_by if db_order else "")

    if is_sales and not is_admin_or_sm:
        # 1. Tuyệt đối không cho sales thao tác trên đơn do Admin hoặc Quản lý tạo
        creator_user = USERS_DB.get(created_by.lower()) if created_by else None
        creator_roles = creator_user.get_roles() if creator_user else []
        is_creator_admin_or_sm = any(r.lower() in ["admin", "sales_manager"] for r in creator_roles) or created_by.lower() in ["admin", "sales_manager"]
        if is_creator_admin_or_sm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Nhân viên kinh doanh không được phép từ chối đơn hàng do Quản trị viên (Admin) hoặc Quản lý kinh doanh tạo."
            )

        # 2. Chỉ được từ chối đơn của đại lý do mình phụ trách
        if not assigned_id or assigned_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn chỉ có quyền từ chối đơn hàng của đại lý do mình phụ trách."
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


ORDER_STATUS_RANKS = {
    "DRAFT": 1,
    "PENDING": 2,
    "PENDING_APPROVAL": 2,
    "CONFIRMED": 3,
    "APPROVED": 3,
    "PICKING": 4,
    "PREPARING": 4,
    "PACKING": 4,
    "EXPORTED": 5,      # >= 5: Đã xuất, Đã giao, Đóng -> CHẶN HỦY!
    "DISPATCHED": 5,
    "SHIPPED": 5,
    "DELIVERED": 6,
    "CLOSED": 7,
    "COMPLETED": 7,
    "CANCELLED": -1,
    "REJECTED": -1,
}


def _execute_order_cancellation(
    order_code: str,
    reason: str,
    current_user: UserResponse,
    request: Request,
    db: Session,
) -> dict:
    """
    Hàm xử lý hủy đơn hàng tập trung tuân thủ nghiêm ngặt các quy tắc:
    1. Authorization Guard: Chỉ admin, sales_manager, sales được phép hủy. Kho, kế toán, mua hàng bị 403.
    2. Lý do hủy bắt buộc.
    3. State Guard: Đơn đã xuất kho (>= 'EXPORTED') không được hủy (ném HTTP 400). Đơn đã hủy cũng chặn 400.
    4. Database Transaction: Đổi trạng thái sang CANCELLED, lưu lý do hủy, và tự động nhả (giảm) reserved_stock.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = current_user.role == "customer" or current_user.role == "Customer" or (
        "customer" in [r.lower() for r in user_roles] and not any(r.lower() in ["admin", "sales_manager", "sales", "accountant"] for r in user_roles)
    )

    if ("purchasing" in [r.lower() for r in user_roles]) and not is_customer and current_user.username != "muahang":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Nhân viên mua hàng không có quyền truy cập hay hủy đơn hàng bán."
        )

    ALLOWED_CANCEL_ROLES = {"admin", "sales_manager", "sales", "customer"}
    if not (any(r.lower() in ALLOWED_CANCEL_ROLES for r in user_roles) or current_user.username in ALLOWED_CANCEL_ROLES or is_customer):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Thao tác hủy đơn hàng chỉ dành cho các vai trò: Quản trị viên (admin), Quản lý kinh doanh (sales_manager), Nhân viên kinh doanh (sales) và Đại lý."
        )

    target = None
    for o in ORDERS_DB.values():
        if o["order_code"].upper() == order_code.upper() or str(o.get("id")) == str(order_code):
            target = o
            break

    persisted_order = db.query(OrderEntity).filter(
        (func.upper(OrderEntity.order_code) == order_code.upper()) | (OrderEntity.id == (target["id"] if target else -1))
    ).first()

    if not target and persisted_order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã {order_code}."
        )

    # Data Isolation & Quy tắc hủy đơn của Đại lý:
    if is_customer:
        customer_dealer = _get_customer_dealer(current_user)
        target_dealer_id = customer_dealer.id if customer_dealer else current_user.id
        chk_dealer_id = persisted_order.dealer_id if persisted_order else target.get("dealer_id")
        chk_created_by = persisted_order.created_by if persisted_order else target.get("created_by")
        if chk_dealer_id != target_dealer_id and (not chk_created_by or chk_created_by.lower() != current_user.username.lower()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Truy cập bị từ chối (403 Forbidden). Bạn chỉ có quyền hủy đơn hàng của chính mình."
            )

        cur_check_status = (persisted_order.status if persisted_order else target.get("status", "")).upper()
        if cur_check_status not in ("PENDING", "PENDING_APPROVAL", "DRAFT"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Đại lý chỉ được phép hủy đơn hàng khi đơn chưa được duyệt (trạng thái Chờ duyệt)."
            )

    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lý do hủy đơn hàng là bắt buộc. Vui lòng nhập lý do hủy."
        )

    cur_status = (persisted_order.status if persisted_order else target.get("status", "")).upper()
    if cur_status in ("CANCELLED", "REJECTED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đơn hàng đã ở trạng thái hủy hoặc từ chối, không thể hủy lại."
        )

    rank = ORDER_STATUS_RANKS.get(cur_status, 0)
    if rank >= 5:  # >= EXPORTED (Đã xuất)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đơn hàng đã ở trạng thái '{cur_status}' (từ 'Đã xuất' trở lên), không thể hủy đơn. Vui lòng thực hiện quy trình trả hàng."
        )

    old_status = cur_status
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    try:
        if persisted_order is not None:
            persisted_order.status = "CANCELLED"
            persisted_order.cancel_reason = clean_reason
            persisted_order.cancelled_by = current_user.username
            persisted_order.cancelled_at = now_utc

            # 1. Nhả (giảm) số lượng tồn đang giữ chỗ (reserved_stock)
            from app.services.inventory_availability_service import release_order_stock
            release_order_stock(db, persisted_order)

            # 2. Hoàn lại tồn kho cho ProductEntity và RAW_PRODUCTS
            if persisted_order.items_json:
                try:
                    items_data = json.loads(persisted_order.items_json)
                    items_list = items_data if isinstance(items_data, list) else (items_data.get("items", []) if isinstance(items_data, dict) else [])
                    from app.api.v1.endpoints.products import _find_product_in_raw
                    for it in items_list:
                        pid = it.get("product_id")
                        base_qty = int(it.get("base_quantity", it.get("quantity", 0)))
                        if pid and base_qty > 0:
                            pe = db.query(ProductEntity).filter(ProductEntity.id == pid).first()
                            if pe:
                                pe.stock = (pe.stock or 0) + base_qty
                            raw_p = _find_product_in_raw(pid)
                            if raw_p:
                                raw_p["stock"] = raw_p.get("stock", 0) + base_qty
                except Exception:
                    pass

            # 3. Ghi vết audit log
            log_audit_event(
                db=db,
                user=current_user,
                action_type="ORDER_CANCEL",
                entity_type="Order",
                entity_id=persisted_order.order_code,
                old_val={"status": old_status},
                new_val={"status": "CANCELLED", "cancel_reason": clean_reason},
                reason=clean_reason,
                request=request,
            )
            log_audit_event(
                db=db,
                user=current_user,
                action_type="INVOICE_EDIT",
                entity_type="Invoice",
                entity_id=persisted_order.order_code,
                old_val={"status": old_status},
                new_val={"status": "CANCELLED", "cancel_reason": clean_reason},
                reason=clean_reason,
                request=request,
            )

            db.commit()
            db.refresh(persisted_order)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi xử lý hủy đơn hàng và hoàn trả tồn kho: {str(e)}"
        )

    if target is not None:
        target["status"] = "CANCELLED"
        target["cancel_reason"] = clean_reason
        target["cancelled_by"] = current_user.username
        target["cancelled_at"] = now_iso
    elif persisted_order is not None:
        target = {
            "id": persisted_order.id,
            "order_code": persisted_order.order_code,
            "dealer_id": persisted_order.dealer_id,
            "dealer_name": persisted_order.dealer_name,
            "created_by": persisted_order.created_by,
            "assigned_sale_id": persisted_order.assigned_sale_id,
            "assigned_sale_name": persisted_order.assigned_sale_name,
            "total_amount": persisted_order.total_amount,
            "status": "CANCELLED",
            "cancel_reason": clean_reason,
            "cancelled_by": current_user.username,
            "cancelled_at": now_iso,
            "created_at": persisted_order.created_at.isoformat() if persisted_order.created_at else now_iso,
        }
        ORDERS_DB[persisted_order.id] = target

    return {
        "status": "success",
        "message": f"Đã hủy đơn hàng {order_code} thành công.",
        "order": target
    }


@router.post("/{order_code}/cancel")
def cancel_order(
    order_code: str,
    data: OrderCancelRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    API Hủy đơn hàng chuyên biệt:
    Yêu cầu bắt buộc lý do hủy, kiểm tra RBAC (sales, sales_manager, admin),
    chặn hủy nếu đơn >= 'Đã xuất', và thực thi Database Transaction đổi trạng thái + nhả tồn giữ chỗ.
    """
    return _execute_order_cancellation(
        order_code=order_code,
        reason=data.reason,
        current_user=current_user,
        request=request,
        db=db,
    )


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
    Nếu chuyển trạng thái sang CANCELLED, thực thi quy trình hủy chuẩn với Database Transaction và hoàn trả tồn kho.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = current_user.role == "customer" or current_user.role == "Customer" or (
        "customer" in [r.lower() for r in user_roles] and not any(r.lower() in ["admin", "sales_manager", "sales", "accountant"] for r in user_roles)
    )
    if is_customer and (not data.status or data.status.upper() != "CANCELLED"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Đại lý không có quyền chỉnh sửa đơn giá hoặc thông tin đơn hàng."
        )

    if data.status and data.status.upper() == "CANCELLED":
        return _execute_order_cancellation(
            order_code=order_code,
            reason=data.reason,
            current_user=current_user,
            request=request,
            db=db,
        )

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
    dealer.overdue_days_allowed = data.max_debt_days
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


class SupplementaryDeliveryRequest(BaseModel):
    note: Optional[str] = "Giao bổ sung đợt 2 - hoàn tất đơn hàng"
    delivered_quantities: Optional[dict[str, int]] = None


@router.post("/{order_code}/supplementary-delivery")
def execute_supplementary_delivery(
    order_code: str,
    request: Request,
    payload: Optional[SupplementaryDeliveryRequest] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Xử lý giao bổ sung cho đơn hàng bị giao thiếu (Phương án 1).
    Phân quyền: Nhân viên nội bộ (Admin, Sales Manager, Sales, Warehouse). Chặn vai trò customer.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    if "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "warehouse_manager", "warehouse_staff"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đại lý không có quyền thực hiện giao hàng bổ sung."
        )

    target = None
    for o in ORDERS_DB.values():
        if o["order_code"].upper() == order_code.upper() or str(o.get("id")) == str(order_code):
            target = o
            break

    db_order = db.query(OrderEntity).filter(
        (func.upper(OrderEntity.order_code) == order_code.upper()) | (OrderEntity.id == (int(order_code) if order_code.isdigit() else -1))
    ).first()

    if not target and not db_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng có mã {order_code}."
        )

    items = []
    if target and target.get("items"):
        items = target["items"]
    elif db_order and db_order.items_json:
        try:
            stored = json.loads(db_order.items_json)
            items = stored if isinstance(stored, list) else stored.get("items", [])
        except Exception:
            pass

    req_map = (payload.delivered_quantities if payload and payload.delivered_quantities else {})
    delivery_note = (payload.note if payload and payload.note else "Giao bổ sung đợt 2 - hoàn tất đơn hàng")

    for it in items:
        p_id = str(it.get("product_id"))
        qty = int(it.get("quantity", 0))
        cur_del = int(it.get("delivered_quantity", 0))
        if p_id in req_map:
            add_qty = int(req_map[p_id])
            it["delivered_quantity"] = min(qty, cur_del + add_qty)
        else:
            it["delivered_quantity"] = qty
        it["shortage_quantity"] = max(0, qty - int(it["delivered_quantity"]))

    if target:
        target["items"] = items
        target["status"] = "DELIVERED"
        if target.get("note"):
            target["note"] += f" | {delivery_note}"
        else:
            target["note"] = delivery_note

    if db_order:
        db_order.status = "DELIVERED"
        try:
            stored = json.loads(db_order.items_json) if db_order.items_json else {}
            if isinstance(stored, dict):
                stored["items"] = items
                db_order.items_json = json.dumps(stored, ensure_ascii=False)
            elif isinstance(stored, list):
                db_order.items_json = json.dumps(items, ensure_ascii=False)
        except Exception:
            pass
        if db_order.note:
            db_order.note += f" | {delivery_note}"
        else:
            db_order.note = delivery_note
        db.commit()

    log_audit_event(
        db=db,
        user=current_user,
        action_type="ORDER_SUPPLEMENTARY_DELIVERY",
        entity_type="Order",
        entity_id=order_code,
        old_val={"action": "shortage_delivery"},
        new_val={"items": items, "status": "DELIVERED"},
        reason=delivery_note,
        request=request,
    )

    if target:
        return target
    return get_order_detail(order_code=order_code, db=db, current_user=current_user)

