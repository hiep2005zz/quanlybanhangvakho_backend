# backend/app/services/batch_suggestion_service.py
"""
SCRUM-81 (User Story S6-04): Gợi ý và quản lý lô xuất theo nguyên tắc hạn dùng gần nhất trước (FEFO).

Nghiệp vụ:
1. Gợi ý lô: Tìm các lô hàng của SKU tại kho đang còn hạn (HSD >= ngày hiện tại)
   và có tồn khả dụng >= số lượng cần xuất (quy đổi về ĐVT cơ sở).
   Sắp xếp theo HSD tăng dần (ASC) -> Lô đầu tiên là suggested_batch.
2. Kiểm tra lý do đổi lô: Nếu chọn lô khác lô gợi ý, bắt buộc nhập lý do (batch_change_reason không rỗng).
3. Xuất kho trừ tồn: Khóa bản ghi lô hàng (FOR UPDATE), kiểm tra số lượng tồn,
   trừ đúng tồn lô đã chọn, cập nhật tồn kho tổng và ghi thẻ kho (InventoryLedgerEntity).
"""
from __future__ import annotations
from datetime import datetime, timezone, date
from typing import List, Optional, Tuple, Dict, Any, Union
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.goods_receipt import ProductBatchEntity, InventoryLedgerEntity, WarehouseEntity
from app.models.entities import ProductEntity, OrderEntity, OrderItemEntity, WarehouseStockEntity, InventoryTransactionEntity
from app.schemas.warehouse import ProductBatchInfo
from app.schemas.auth import UserResponse


def get_current_date() -> datetime:
    """Trả về mốc bắt đầu ngày hiện tại theo UTC."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def resolve_warehouse_id_int(db: Session, warehouse_id_or_code: Union[int, str, None]) -> Optional[int]:
    """Quy đổi mã kho (WH01, KHO_HN...) hoặc ID kho sang ID integer của bảng warehouses."""
    if warehouse_id_or_code is None:
        return None
    if isinstance(warehouse_id_or_code, int):
        return warehouse_id_or_code
    if isinstance(warehouse_id_or_code, str):
        if warehouse_id_or_code.isdigit():
            return int(warehouse_id_or_code)
        # Ánh xạ chuẩn theo DEFAULT_WAREHOUSES
        mapping = {
            "WH01": 1,
            "WH02": 3,
            "WH03": 2,
            "KHO_HN": 1,
            "KHO_HCM": 3,
            "KHO_DN": 2,
        }
        if warehouse_id_or_code.upper() in mapping:
            return mapping[warehouse_id_or_code.upper()]
        wh = db.query(WarehouseEntity).filter(
            (WarehouseEntity.code == warehouse_id_or_code) |
            (WarehouseEntity.name == warehouse_id_or_code)
        ).first()
        if wh:
            return wh.id
        return 1
    return 1


def check_warehouse_user_permission(current_user: UserResponse, warehouse: Optional[WarehouseEntity]):
    """
    Kiểm tra phân quyền ở tầng Server:
    Chỉ cho phép Nhân viên kho và Quản lý kho thao tác trên kho được phân công.
    Admin có quyền toàn quyền trên tất cả các kho.
    """
    user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
    user_roles_lower = {str(r).lower() for r in user_roles}
    if current_user.role:
        user_roles_lower.add(str(current_user.role).lower())

    if "admin" in user_roles_lower:
        return

    # Kiểm tra vai trò kho
    is_warehouse_staff = bool(user_roles_lower & {"warehouse", "warehouse_manager"})
    if not is_warehouse_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối (403 Forbidden). Bạn không có quyền thao tác xuất kho."
        )

    # Kiểm tra kho được phân công
    if not warehouse:
        return

    user_branch = (getattr(current_user, "branch", None) or getattr(current_user, "warehouse_name", None) or "").strip().lower()
    if not user_branch:
        return  # Không cấu hình giới hạn chi nhánh

    wh_name = (warehouse.name or "").strip().lower()
    wh_code = (warehouse.code or "").strip().lower()

    # Kiểm tra khớp chi nhánh
    match = (
        user_branch in wh_name or
        wh_name in user_branch or
        user_branch in wh_code or
        wh_code in user_branch or
        ("hà nội" in user_branch and ("hà nội" in wh_name or "hn" in wh_code)) or
        ("đà nẵng" in user_branch and ("đà nẵng" in wh_name or "dn" in wh_code)) or
        (("hồ chí minh" in user_branch or "hcm" in user_branch) and ("hồ chí minh" in wh_name or "hcm" in wh_code))
    )
    if not match:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bạn được phân công làm việc tại '{getattr(current_user, 'branch', '')}', không có quyền thao tác trên '{warehouse.name}'."
        )


def get_fefo_batch_suggestions(
    db: Session,
    product_id: int,
    warehouse_id: int,
    required_base_qty: float,
) -> Tuple[Optional[ProductBatchInfo], List[ProductBatchInfo]]:
    """
    Tiêu chí 1: Truy vấn trực tiếp từ CSDL các lô của SKU tại kho:
    - Đang còn hạn sử dụng (expiry_date >= ngày hiện tại).
    - Sắp xếp theo expiry_date ASC (FEFO - hạn dùng gần nhất xếp đầu).
    - suggested_batch: Lô đầu tiên có available_quantity >= required_base_qty.
    - available_batches: Danh sách các lô còn hạn và còn tồn > 0.
    """
    today_start = get_current_date()

    # Truy vấn toàn bộ lô còn hạn và còn tồn > 0 của sản phẩm tại kho
    batches = (
        db.query(ProductBatchEntity)
        .filter(
            ProductBatchEntity.product_id == product_id,
            ProductBatchEntity.warehouse_id == warehouse_id,
            ProductBatchEntity.expiry_date >= today_start,
            ProductBatchEntity.quantity > 0,
        )
        .order_by(ProductBatchEntity.expiry_date.asc(), ProductBatchEntity.id.asc())
        .all()
    )

    available_batch_infos: List[ProductBatchInfo] = []
    suggested_batch_info: Optional[ProductBatchInfo] = None

    for b in batches:
        avail = b.available_quantity
        if avail <= 0:
            continue

        info = ProductBatchInfo(
            id=b.id,
            batch_number=b.batch_number,
            manufacturing_date=b.manufacturing_date.strftime("%d/%m/%Y") if b.manufacturing_date else None,
            expiry_date=b.expiry_date.strftime("%d/%m/%Y") if b.expiry_date else None,
            quantity=float(b.quantity),
            reserved_quantity=float(b.reserved_quantity or 0.0),
            available_quantity=float(avail),
        )
        available_batch_infos.append(info)

        # Lô gợi ý mặc định: Lô đầu tiên theo FEFO có tồn khả dụng >= số lượng cần xuất
        if suggested_batch_info is None and avail >= required_base_qty:
            suggested_batch_info = info

    # Nếu không có lô đơn lẻ nào đủ toàn bộ số lượng, gợi ý lô có HSD gần nhất còn tồn khả dụng lớn nhất hoặc đầu tiên
    if suggested_batch_info is None and available_batch_infos:
        suggested_batch_info = available_batch_infos[0]

    return suggested_batch_info, available_batch_infos


def validate_batch_change_reason(
    selected_batch_id: Optional[int],
    suggested_batch_id: Optional[int],
    batch_change_reason: Optional[str],
) -> Optional[str]:
    """
    Tiêu chí 2:
    - Nếu giữ nguyên lô gợi ý: lý do là tùy chọn.
    - Nếu đổi sang lô khác: bắt buộc nhập lý do (không được để trống hoặc chỉ có khoảng trắng).
    """
    if not selected_batch_id:
        return None

    # Nếu có lô gợi ý và thủ kho chọn lô khác
    if suggested_batch_id and int(selected_batch_id) != int(suggested_batch_id):
        reason = (batch_change_reason or "").strip()
        if not reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng nhập lý do khi chọn lô khác với lô hệ thống gợi ý."
            )
        return reason

    return (batch_change_reason or "").strip() or None


def execute_batch_deduction(
    db: Session,
    product: ProductEntity,
    warehouse_id: int,
    base_qty: float,
    selected_batch_id: Optional[int],
    suggested_batch_id: Optional[int],
    batch_change_reason: Optional[str],
    reference_code: str,
    performed_by: str,
    note: Optional[str] = None,
) -> ProductBatchEntity:
    """
    Tiêu chí 3: Trừ tồn lô đã chọn trong Database Transaction với Row-level lock.
    - FOR UPDATE trên bản ghi lô hàng.
    - Kiểm tra số lượng tồn >= số lượng xuất.
    - Trừ tồn lô (quantity -= base_qty).
    - Ghi thẻ kho InventoryLedgerEntity.
    """
    if not selected_batch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng chọn lô hàng xuất kho cho sản phẩm quản lý theo lô."
        )

    # 1. Row-level Lock trên bản ghi lô hàng được chọn
    batch = (
        db.query(ProductBatchEntity)
        .filter(
            ProductBatchEntity.id == selected_batch_id,
            ProductBatchEntity.product_id == product.id,
            ProductBatchEntity.warehouse_id == warehouse_id,
        )
        .with_for_update()
        .first()
    )

    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lô hàng ID {selected_batch_id} của sản phẩm '{product.name}' tại kho."
        )

    # 2. Kiểm tra tồn khả dụng / tồn thực tế của lô
    if batch.quantity < base_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lô hàng '{batch.batch_number}' không đủ số lượng tồn để xuất. Tồn hiện tại: {batch.quantity}, yêu cầu xuất: {base_qty}."
        )

    prev_batch_stock = float(batch.quantity)
    batch.quantity = round(batch.quantity - base_qty, 4)
    batch.updated_at = datetime.now(timezone.utc)

    # 3. Ghi thẻ kho (InventoryLedgerEntity)
    ledger_entry = InventoryLedgerEntity(
        product_id=product.id,
        warehouse_id=warehouse_id,
        reference_code=reference_code,
        transaction_type="ISSUE",
        quantity=-base_qty,
        previous_stock=prev_batch_stock,
        new_stock=float(batch.quantity),
        batch_number=batch.batch_number,
        selected_batch_id=batch.id,
        suggested_batch_id=suggested_batch_id,
        batch_change_reason=batch_change_reason,
        performed_by=performed_by,
        note=note or f"Xuất kho từ lô {batch.batch_number}" + (f" (Lý do đổi lô: {batch_change_reason})" if batch_change_reason else ""),
        created_at=datetime.now(timezone.utc),
    )
    db.add(ledger_entry)

    return batch
