# backend/app/api/v1/endpoints/batches.py
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import Permission
from app.schemas.auth import UserResponse
from app.schemas.batch import (
    BatchCreateRequest,
    BatchResponse,
    BatchListResponse,
    BatchTrackingToggleRequest,
    BatchTrackingToggleResponse,
)
from app.services.batch_service import (
    toggle_sku_batch_tracking,
    create_product_batch,
    list_batches,
)

router = APIRouter()


@router.get("", response_model=BatchListResponse)
def get_batches_list(
    product_id: Optional[int] = Query(None, description="Lọc theo ID sản phẩm"),
    warehouse_id: Optional[int] = Query(None, description="Lọc theo ID kho"),
    status: Optional[str] = Query("ALL", description="Lọc trạng thái: ALL, EXPIRED, NEAR_EXPIRY, SAFE"),
    warning_days: int = Query(30, ge=1, le=365, description="Ngưỡng cảnh báo cận hạn (số ngày)"),
    search: Optional[str] = Query(None, description="Tìm theo số lô, tên sản phẩm hoặc mã SKU"),
    page: int = Query(1, ge=1, description="Số trang (bắt đầu từ 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Số lượng mục trên mỗi trang"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Tiêu chí 3: Danh sách các lô hàng kèm thông tin hạn sử dụng và cảnh báo cận hạn.
    Khai báo ngưỡng cảnh báo động thông qua query parameter 'warning_days'.
    Hỗ trợ phân trang: page, page_size (mặc định 10 mục/trang).
    """
    return list_batches(
        db=db,
        product_id=product_id,
        warehouse_id=warehouse_id,
        status_filter=status,
        warning_days=warning_days,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def create_batch(
    data: BatchCreateRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Tiêu chí 2: Khai báo và quản lý thông tin của từng lô hàng gắn với SKU.
    4 thuộc tính: batch_number, manufacturing_date, expiry_date, remaining_quantity.
    Ràng buộc: expiry_date > manufacturing_date và remaining_quantity >= 0.
    """
    return create_product_batch(db=db, data=data, current_user=current_user)


@router.patch("/product/{product_id}/tracking", response_model=BatchTrackingToggleResponse)
def toggle_product_batch_tracking(
    product_id: int,
    data: BatchTrackingToggleRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Tiêu chí 1: Bật hoặc tắt quản lý lô theo từng SKU.
    Kiểm soát điều kiện biên chặt chẽ:
    - Bật khi có tồn kho: yêu cầu chỉ định lô khởi tạo (initial_batch_number, initial_expiry_date).
    - Tắt: bảo lưu lịch sử các lô cũ trong cơ sở dữ liệu.
    """
    return toggle_sku_batch_tracking(
        db=db,
        product_id=product_id,
        data=data,
        current_user=current_user,
    )


@router.get("/alerts", response_model=BatchListResponse)
def get_batch_expiry_alerts(
    warehouse_id: Optional[int] = Query(None, description="Lọc theo kho hàng"),
    warning_days: int = Query(30, ge=1, le=365, description="Ngưỡng số ngày cảnh báo trước hạn"),
    page: int = Query(1, ge=1, description="Số trang (bắt đầu từ 1)"),
    page_size: int = Query(50, ge=1, le=200, description="Số lượng mục trên mỗi trang"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Tiêu chí 3: Truy vấn nhanh danh sách các lô sắp hết hạn hoặc đã quá hạn còn tồn kho
    (remaining_quantity > 0) để nhân viên kho không giao nhầm hàng cho đại lý.
    """
    return list_batches(
        db=db,
        warehouse_id=warehouse_id,
        status_filter=None,  # list_batches sẽ trả về đầy đủ items và stats
        warning_days=warning_days,
        search=None,
        page=page,
        page_size=page_size,
    )
