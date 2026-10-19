# backend/app/api/v1/endpoints/return_orders.py
from __future__ import annotations
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_permission
from app.core.rbac import Permission, Role, WAREHOUSE_ROLES
from app.schemas.auth import UserResponse
from app.models.entities import OrderEntity, DealerEntity
from app.schemas.return_order import (
    ReturnOrderCreate,
    ReturnOrderResponse,
    ReturnOrderListResponse,
    ReturnOrderConfirmRequest,
    ReturnOrderCancelRequest,
    OrderReturnableInfoResponse,
)
from app.services.return_order_service import (
    create_return_order,
    confirm_return_receipt,
    cancel_return_order,
    get_return_order_by_id,
    list_return_orders,
    get_order_returnable_info,
    to_response_dto,
)

router = APIRouter()


@router.get("/eligible-orders", response_model=List[dict])
def get_eligible_orders(
    search: Optional[str] = None,
    dealer_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Quy tắc 1: Cho phép tìm kiếm và chọn các Đơn hàng gốc đã giao/hoàn thành/xác nhận
    để lập phiếu trả hàng.
    """
    # Các trạng thái đơn hàng đủ điều kiện trả
    eligible_statuses = ["DELIVERED", "COMPLETED", "CONFIRMED", "EXPORTED", "CLOSED"]
    
    query = db.query(OrderEntity).filter(OrderEntity.status.in_(eligible_statuses))

    if dealer_id:
        query = query.filter(OrderEntity.dealer_id == dealer_id)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            (OrderEntity.order_code.ilike(term)) |
            (OrderEntity.dealer_name.ilike(term))
        )

    # Nếu người dùng có vai trò Customer (Đại lý), chỉ xem đơn của chính họ
    user_roles = current_user.roles or [current_user.role]
    if Role.CUSTOMER.value in user_roles and len(user_roles) == 1:
        dealer = db.query(DealerEntity).filter(
            (DealerEntity.phone == current_user.phone) |
            (DealerEntity.name == current_user.full_name)
        ).first()
        if dealer:
            query = query.filter(OrderEntity.dealer_id == dealer.id)

    orders = query.order_by(OrderEntity.id.desc()).limit(50).all()

    return [
        {
            "id": o.id,
            "order_code": o.order_code,
            "dealer_id": o.dealer_id,
            "dealer_name": o.dealer_name,
            "status": o.status,
            "total_amount": float(o.total_amount or 0.0),
            "created_at": o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else "",
            "items_count": len(o.items_rel) if o.items_rel else 0,
        }
        for o in orders
    ]


@router.get("/order-returnable-items/{order_id}", response_model=OrderReturnableInfoResponse)
def get_order_returnable_items_endpoint(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Hiển thị danh sách các dòng hàng của đơn gốc kèm số lượng còn được phép trả,
    thông tin lô xuất gốc và cấu hình điều hướng kho hàng lỗi.
    """
    return get_order_returnable_info(db, order_id)


@router.post("", response_model=ReturnOrderResponse, status_code=status.HTTP_201_CREATED)
def create_return_order_endpoint(
    data: ReturnOrderCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Quy tắc 1, 2, 3: Nhân viên kinh doanh tạo Phiếu trả hàng.
    - Lưu ở trạng thái PENDING_WAREHOUSE (Chờ kho xác nhận).
    - TUYỆT ĐỐI CHƯA cộng tồn kho vật lý ở bước này.
    """
    user_roles = current_user.roles or [current_user.role]
    # Kiểm tra phân quyền: Sales, Sales Manager, Admin, Warehouse có quyền tạo
    allowed_roles = {
        Role.SYSTEM_ADMIN.value,
        Role.SALES.value,
        Role.SALES_MANAGER.value,
        Role.WAREHOUSE_MANAGER.value,
        Role.WAREHOUSE_STAFF.value,
    }
    if not any(r in allowed_roles for r in user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền lập phiếu trả hàng đại lý."
        )

    ro = create_return_order(db=db, data=data, current_user=current_user)
    return to_response_dto(ro)


@router.get("", response_model=ReturnOrderListResponse)
def list_return_orders_endpoint(
    status: Optional[str] = Query(None, description="Bộ lọc trạng thái: ALL | PENDING_WAREHOUSE | COMPLETED | CANCELLED"),
    search: Optional[str] = Query(None, description="Tìm theo mã phiếu, mã đơn, đại lý"),
    dealer_id: Optional[int] = Query(None, description="Lọc theo đại lý"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Danh sách phiếu trả hàng có phân trang và bộ lọc."""
    items, total = list_return_orders(
        db=db,
        status_filter=status,
        search=search,
        dealer_id=dealer_id,
        page=page,
        page_size=page_size,
    )
    return ReturnOrderListResponse(
        items=[to_response_dto(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{return_order_id}", response_model=ReturnOrderResponse)
def get_return_order_detail_endpoint(
    return_order_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Chi tiết một phiếu trả hàng."""
    ro = get_return_order_by_id(db, return_order_id)
    return to_response_dto(ro)


@router.post("/{return_order_id}/confirm-receipt", response_model=ReturnOrderResponse)
def confirm_return_receipt_endpoint(
    return_order_id: int,
    payload: Optional[ReturnOrderConfirmRequest] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Quy tắc 4: Quản lý kho xác thực nhận hàng ('Confirm Receipt')
    - Cung cấp nút 'Xác nhận nhận hàng & Nhập kho' dành cho Quản lý kho / Thủ kho.
    - Chuyển trạng thái sang COMPLETED.
    - CỘNG TỒN KHO vào đúng Kho đích (Kho thường hoặc Kho lỗi) và đúng Mã Lô trong Database Transaction.
    - Ghi Thẻ kho và Nhật ký giao dịch kho.
    """
    user_roles = current_user.roles or [current_user.role]
    # BẮT BUỘC kiểm tra vai trò Quản lý kho hoặc Admin (Zero-Trust)
    warehouse_allowed = {
        Role.SYSTEM_ADMIN.value,
        Role.WAREHOUSE_MANAGER.value,
    }
    if not any(r in warehouse_allowed for r in user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản lý kho hoặc Quản trị viên mới có quyền xác nhận nhận hàng và cập nhật tồn kho."
        )

    note_text = payload.note if payload else None
    ro = confirm_return_receipt(
        db=db,
        return_order_id=return_order_id,
        current_user=current_user,
        note=note_text,
    )
    return to_response_dto(ro)


@router.post("/{return_order_id}/cancel", response_model=ReturnOrderResponse)
def cancel_return_order_endpoint(
    return_order_id: int,
    payload: ReturnOrderCancelRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Hủy phiếu trả hàng khi chưa nhập kho (PENDING_WAREHOUSE)."""
    user_roles = current_user.roles or [current_user.role]
    allowed_roles = {
        Role.SYSTEM_ADMIN.value,
        Role.SALES_MANAGER.value,
        Role.SALES.value,
        Role.WAREHOUSE_MANAGER.value,
        Role.WAREHOUSE_STAFF.value,
    }
    if not any(r in allowed_roles for r in user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền hủy phiếu trả hàng."
        )

    ro = cancel_return_order(
        db=db,
        return_order_id=return_order_id,
        current_user=current_user,
        reason=payload.reason,
    )
    return to_response_dto(ro)
