# backend/app/api/v1/endpoints/goods_receipts.py
"""
API Lập phiếu nhập kho từ nhà cung cấp (Goods Receipt Note - GRN).

Yêu cầu & Luồng xử lý:
1. Tạo phiếu (DRAFT): Nhà cung cấp, số chứng từ tham chiếu, ngày nhập, kho nhận hàng, ghi chú.
2. Dòng hàng (Items): Nhập theo UOM bất kỳ -> tự động tính base_quantity = quantity * conversion_rate.
3. Quản lý lô: Kiểm tra cờ is_batch_managed của sản phẩm -> nếu True bắt buộc có batch_number & expiry_date.
4. Xác nhận phiếu (CONFIRMED): Thực thi trong 1 Database Transaction:
   - status -> CONFIRMED
   - Cộng tồn kho tổng và tồn kho theo lô
   - Ghi thẻ kho/sổ kho (InventoryLedger), InventoryTransaction, và Audit Log.
5. Tính bất biến: Khi đã CONFIRMED, CHẶN toàn bộ thao tác SỬA (PUT) hoặc XÓA (DELETE) -> trả về HTTP 400 Bad Request.
6. RBAC: Quyền ghi kho (inventory:write) chỉ cấp cho admin, warehouse_manager, warehouse.
   Nhân viên kinh doanh (sales) tuyệt đối KHÔNG được ghi kho.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import Permission
from app.models.goods_receipt import WarehouseEntity, UnitOfMeasureEntity
from app.schemas.auth import UserResponse
from app.schemas.goods_receipt import (
    GoodsReceiptCreateRequest,
    GoodsReceiptUpdateRequest,
    GoodsReceiptResponse,
    GoodsReceiptListResponse,
    WarehouseResponse,
    UnitOfMeasureResponse,
)
from app.services.goods_receipt_service import (
    create_goods_receipt,
    update_goods_receipt,
    confirm_goods_receipt,
    delete_goods_receipt,
    get_goods_receipt,
    list_goods_receipts,
)

router = APIRouter()


@router.get("/meta/warehouses", response_model=List[WarehouseResponse])
def get_warehouses_list(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """Lấy danh sách các kho hàng hoạt động trong hệ thống."""
    return db.query(WarehouseEntity).filter(WarehouseEntity.is_active == True).all()


@router.get("/meta/uoms", response_model=List[UnitOfMeasureResponse])
def get_units_of_measure_list(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """Lấy danh mục đơn vị tính (Units of Measure)."""
    return db.query(UnitOfMeasureEntity).all()


@router.post("", response_model=GoodsReceiptResponse, status_code=status.HTTP_201_CREATED)
def create_new_goods_receipt(
    payload: GoodsReceiptCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Tạo mới phiếu nhập kho (trạng thái DRAFT).
    Tuyệt đối KHÔNG cộng hay làm biến động số lượng tồn kho.
    """
    return create_goods_receipt(db, payload, current_user, request)


@router.get("", response_model=GoodsReceiptListResponse)
def get_all_goods_receipts(
    supplier_id: Optional[int] = Query(None, description="Lọc theo ID nhà cung cấp"),
    warehouse_id: Optional[int] = Query(None, description="Lọc theo ID kho nhận"),
    status: Optional[str] = Query(None, description="Lọc theo trạng thái (DRAFT, CONFIRMED)"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã phiếu, số chứng từ"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """Lấy danh sách phiếu nhập kho."""
    receipts, total = list_goods_receipts(
        db,
        supplier_id=supplier_id,
        warehouse_id=warehouse_id,
        status_filter=status,
        search=search,
        limit=limit,
        offset=offset,
    )
    return GoodsReceiptListResponse(items=receipts, total=total)


@router.get("/{receipt_id}", response_model=GoodsReceiptResponse)
def get_goods_receipt_detail(
    receipt_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """Xem chi tiết phiếu nhập kho."""
    return get_goods_receipt(db, receipt_id)


@router.put("/{receipt_id}", response_model=GoodsReceiptResponse)
def update_existing_goods_receipt(
    receipt_id: int,
    payload: GoodsReceiptUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Cập nhật nội dung phiếu nhập kho nháp (DRAFT).
    Tính bất biến: Khi phiếu đã ở trạng thái CONFIRMED, chặn toàn bộ thao tác SỬA (PUT).
    """
    return update_goods_receipt(db, receipt_id, payload, current_user, request)


@router.post("/{receipt_id}/confirm", response_model=GoodsReceiptResponse)
def confirm_goods_receipt_endpoint(
    receipt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Xác nhận phiếu nhập kho (chuyển sang CONFIRMED).
    Thực thi trong một Database Transaction duy nhất:
    - Cộng tồn kho tổng và tồn kho theo số lô
    - Ghi sổ nhật ký giao dịch kho (InventoryLedger), InventoryTransaction, Audit Log.
    """
    return confirm_goods_receipt(db, receipt_id, current_user, request)


@router.delete("/{receipt_id}")
def delete_goods_receipt_endpoint(
    receipt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Xóa phiếu nhập kho nháp (DRAFT).
    Tính bất biến: Khi phiếu đã ở trạng thái CONFIRMED, chặn toàn bộ thao tác XÓA (DELETE).
    """
    return delete_goods_receipt(db, receipt_id, current_user, request)
