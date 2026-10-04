# backend/app/api/v1/endpoints/orders.py
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
from app.api.deps import get_current_user, require_permission, require_roles
from app.core.database import get_db
from app.core.rbac import Permission
from app.schemas.auth import UserResponse
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.models.dealer import DEALERS_DB, save_dealers_db
from app.models.entities import OrderEntity
from app.models.user import USERS_DB
from app.services.audit_service import log_audit_event

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

class SalesOrderResponse(OrderResponse):
    subtotal_amount: float = 0
    discount_percent: float = 0
    discount_amount: float = 0
    delivery_point: Optional[str] = None
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

@router.get("/dealers")
def get_order_dealers(
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_WRITE.value))
):
    """Return dealers available for order entry, restricted to the assigned salesperson."""
    dealers = list(DEALERS_DB.values())
    if current_user.role == "sales":
        assigned_user = USERS_DB.get(current_user.username)
        if not assigned_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy nhân viên kinh doanh hiện tại.",
            )
        dealers = [dealer for dealer in dealers if dealer.assigned_sale_id == assigned_user.id]

    return [
        {
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "phone": dealer.phone,
            "address": dealer.address,
        }
        for dealer in sorted(dealers, key=lambda item: item.name.lower())
    ]

@router.get("", response_model=List[OrderResponse])
def get_orders(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value))
):
    """Lấy danh sách đơn hàng / hóa đơn."""
    orders_by_code = {
        order["order_code"]: OrderResponse(**order)
        for order in ORDERS_DB.values()
    }
    for order in db.query(OrderEntity).order_by(OrderEntity.id.desc()).all():
        if order.order_code not in orders_by_code:
            orders_by_code[order.order_code] = OrderResponse(
                id=order.id,
                order_code=order.order_code,
                dealer_id=order.dealer_id,
                dealer_name=order.dealer_name,
                created_by=order.created_by,
                assigned_sale_id=order.assigned_sale_id,
                assigned_sale_name=order.assigned_sale_name,
                total_amount=order.total_amount,
                status=order.status,
                created_at=order.created_at.isoformat() if order.created_at else "",
            )
    return sorted(orders_by_code.values(), key=lambda order: order.id, reverse=True)

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

    # 2. Kiểm tra trạng thái khóa giao dịch của chính Đại lý
    if getattr(dealer, "status", "ACTIVE") == "LOCKED":
        lock_msg = f"Đại lý này đang bị KHÓA giao dịch (Lý do: {dealer.lock_reason or 'Không có lý do'}). Không thể tạo đơn hàng mới!"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=lock_msg
        )

    # 3. Kiểm tra hạn mức công nợ và số ngày nợ tối đa
    current_debt = 0.0
    max_debt_age = 0
    now = datetime.now(timezone.utc)
    for o in ORDERS_DB.values():
        if o.get("dealer_id") == dealer.id and o.get("status") not in ("PAID", "CANCELLED"):
            current_debt += o.get("total_amount", 0.0)
            created_at_str = o.get("created_at")
            if created_at_str:
                try:
                    order_date = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    days_old = (now - order_date).days
                    if days_old > max_debt_age:
                        max_debt_age = days_old
                except Exception:
                    pass
    
    order_total = sum(item.quantity * item.price for item in data.items)
    
    over_limit = (current_debt + order_total) > getattr(dealer, "credit_limit", 50000000.0)
    over_days = max_debt_age > getattr(dealer, "max_debt_days", 30)
    
    if over_limit and over_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đại lý đã vượt hạn mức công nợ và có công nợ quá hạn. Không thể xuất hàng."
        )
    elif over_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đại lý đã vượt hạn mức công nợ. Không thể xuất hàng."
        )
    elif over_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Đại lý có công nợ quá hạn. Không thể xuất hàng."
        )

    # 4. Tạo đơn hàng và tính base_quantity
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
            "product_name": raw_p.get("name") if raw_p else (prod_entity.name if prod_entity else f"SP #{item.product_id}"),
            "quantity": item.quantity,
            "price": item.price,
            "unit": chosen_unit,
            "unit_name": chosen_unit,
            "conversion_rate": chosen_rate,
            "base_quantity": base_quantity,
        })

    if db:
        db.commit()

    subtotal_amount = total_amount
    discount_amount = round(subtotal_amount * data.discount_percent / 100, 2)
    total_amount = subtotal_amount - discount_amount
    order_id = _allocate_order_id(db)

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
        "subtotal_amount": subtotal_amount,
        "discount_percent": data.discount_percent,
        "discount_amount": discount_amount,
        "delivery_point": data.delivery_point,
        "desired_delivery_date": data.desired_delivery_date.isoformat() if data.desired_delivery_date else None,
        "note": data.note,
    }
    ORDERS_DB[order_id] = order_record

    return OrderResponse(**order_record)

@router.post("/sales-entry", response_model=SalesOrderResponse, status_code=status.HTTP_201_CREATED)
def create_sales_entry_order(
    data: OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_WRITE.value)),
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

    dealer = DEALERS_DB.get(data.dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {data.dealer_id}.",
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

    delivery_point = data.delivery_point.strip()
    subtotal_amount = sum(item["quantity"] * item["price"] for item in priced_items)
    discount_amount = round(subtotal_amount * data.discount_percent / 100, 2)
    total_amount = subtotal_amount - discount_amount
    created_at = datetime.now(timezone.utc)
    db_order = OrderEntity(
        order_code=f"PENDING-{uuid4().hex}",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        created_by=current_user.username,
        assigned_sale_id=assigned_sale_id,
        assigned_sale_name=assigned_user.full_name if assigned_user else None,
        total_amount=total_amount,
        status="CONFIRMED",
        note=data.note,
        items_json=json.dumps({
            "items": priced_items,
            "delivery_point": delivery_point,
            "desired_delivery_date": data.desired_delivery_date.isoformat(),
            "subtotal_amount": subtotal_amount,
            "discount_percent": data.discount_percent,
            "discount_amount": discount_amount,
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
        "total_amount": total_amount,
        "status": "CONFIRMED",
        "created_at": created_at.isoformat(),
        "subtotal_amount": subtotal_amount,
        "discount_percent": data.discount_percent,
        "discount_amount": discount_amount,
        "delivery_point": delivery_point,
        "desired_delivery_date": data.desired_delivery_date.isoformat(),
        "note": data.note,
        "items": priced_items,
    }
    return SalesOrderResponse(**order_record)

@router.get("/{order_code}", response_model=SalesOrderResponse)
def get_order_detail(
    order_code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.ORDER_READ.value)),
):
    """Return one order with its persisted line items and delivery details."""
    order_record = next(
        (
            order
            for order in ORDERS_DB.values()
            if order["order_code"].upper() == order_code.upper()
        ),
        None,
    )
    if order_record is None:
        entity = db.query(OrderEntity).filter(
            func.upper(OrderEntity.order_code) == order_code.upper()
        ).first()
        if entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy đơn hàng có mã {order_code}.",
            )

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
        }
        try:
            stored_details = json.loads(entity.items_json) if entity.items_json else {}
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Dữ liệu chi tiết đơn hàng {order_code} không hợp lệ.",
            ) from error
        if isinstance(stored_details, list):
            order_record["items"] = stored_details
            stored_details = {}
        elif isinstance(stored_details, dict):
            order_record["items"] = stored_details.get("items", [])
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Dữ liệu chi tiết đơn hàng {order_code} không hợp lệ.",
            )
        for field in (
            "subtotal_amount",
            "discount_percent",
            "discount_amount",
            "delivery_point",
            "desired_delivery_date",
        ):
            if field in stored_details:
                order_record[field] = stored_details[field]

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
    detail_response = dict(order_record)
    detail_response.update({
        "items": items,
        "subtotal_amount": subtotal_amount,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
    })
    return SalesOrderResponse(**detail_response)


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

    persisted_order = None
    if not target:
        persisted_order = db.query(OrderEntity).filter(
            func.upper(OrderEntity.order_code) == order_code.upper()
        ).first()
        if persisted_order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy hóa đơn/đơn hàng có mã {order_code}."
            )
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
        persisted_order.status = new_status
        persisted_order.note = new_note
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
