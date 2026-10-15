# backend/app/services/batch_service.py
from __future__ import annotations
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.entities import ProductEntity, WarehouseStockEntity, InventoryTransactionEntity
from app.models.goods_receipt import ProductBatchEntity, WarehouseEntity, InventoryLedgerEntity
from app.schemas.auth import UserResponse
from app.schemas.batch import (
    BatchCreateRequest,
    BatchResponse,
    BatchListResponse,
    BatchTrackingToggleRequest,
    BatchTrackingToggleResponse,
)
from app.services.audit_service import log_audit_event


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def calculate_expiry_status(
    expiry_date: Optional[datetime],
    warning_days: int = 30,
    reference_time: Optional[datetime] = None,
) -> Tuple[str, Optional[int]]:
    """
    Tiêu chí 3: Đánh giá trạng thái hạn sử dụng chuẩn xác múi giờ.
    So sánh mốc thời gian ngày (date) để tránh lệch giờ phút.
    - EXPIRED: expiry_date <= reference_date
    - NEAR_EXPIRY: reference_date < expiry_date <= reference_date + warning_days
    - SAFE: expiry_date > reference_date + warning_days
    """
    if not expiry_date:
        return "SAFE", None

    ref = reference_time or get_utc_now()
    ref_date = ref.date()
    exp_date = expiry_date.date()

    delta_days = (exp_date - ref_date).days

    if delta_days <= 0:
        return "EXPIRED", delta_days
    elif delta_days <= warning_days:
        return "NEAR_EXPIRY", delta_days
    else:
        return "SAFE", delta_days


def build_batch_response(
    batch: ProductBatchEntity,
    product: Optional[ProductEntity] = None,
    warehouse: Optional[WarehouseEntity] = None,
    warning_days: int = 30,
) -> BatchResponse:
    prod = product or batch.product
    wh = warehouse or batch.warehouse
    status_str, days_left = calculate_expiry_status(batch.expiry_date, warning_days=warning_days)

    return BatchResponse(
        id=batch.id,
        product_id=batch.product_id,
        product_code=prod.code if prod else f"PRD-{batch.product_id}",
        product_name=prod.name if prod else f"Sản phẩm #{batch.product_id}",
        base_unit=getattr(prod, "base_unit", "Cái") or "Cái",
        warehouse_id=batch.warehouse_id,
        warehouse_name=wh.name if wh else "Kho Tổng",
        batch_number=batch.batch_number,
        manufacturing_date=batch.manufacturing_date,
        expiry_date=batch.expiry_date,
        remaining_quantity=float(batch.quantity or 0.0),
        days_until_expiry=days_left,
        expiry_status=status_str,
        warning_threshold_days=warning_days,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


def toggle_sku_batch_tracking(
    db: Session,
    product_id: int,
    data: BatchTrackingToggleRequest,
    current_user: UserResponse,
) -> BatchTrackingToggleResponse:
    """
    Tiêu chí 1: Bật hoặc tắt quản lý lô theo từng SKU.
    Kiểm soát chặt chẽ điều kiện biên khi chuyển đổi trạng thái:
    - Bật (False -> True):
      Nếu SKU đang có tồn kho hiện hữu (stock > 0) mà chưa được phân bổ vào các lô:
      Bắt buộc cung cấp thông tin số lô khởi tạo để bao trọn số tồn kho hiện hữu.
    - Tắt (True -> False):
      Giữ nguyên lịch sử các lô cũ trong DB (không xóa), chỉ đổi cờ để các giao dịch sau
      không bắt buộc truyền lô.
    """
    product = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {product_id}.",
        )

    old_flag = bool(getattr(product, "is_batch_managed", False))
    new_flag = bool(data.is_batch_managed)

    if old_flag == new_flag:
        return BatchTrackingToggleResponse(
            product_id=product.id,
            product_code=product.code,
            product_name=product.name,
            is_batch_managed=new_flag,
            message=f"Sản phẩm '{product.name}' đã ở trạng thái {'bật' if new_flag else 'tắt'} quản lý lô.",
        )

    batch_created_resp: Optional[BatchResponse] = None
    now_utc = get_utc_now()

    # Trường hợp 1: Chuyển từ TẮT -> BẬT
    if new_flag:
        current_stock = float(product.stock or 0.0)
        # Tính tổng tồn kho hiện đã có trong các bản ghi lô
        existing_batch_qty = (
            db.query(func.coalesce(func.sum(ProductBatchEntity.quantity), 0.0))
            .filter(ProductBatchEntity.product_id == product.id)
            .scalar()
        )
        existing_batch_qty = float(existing_batch_qty or 0.0)
        unallocated_stock = current_stock - existing_batch_qty

        # Nếu sản phẩm có tồn kho mà chưa phân bổ vào lô
        if unallocated_stock > 0:
            if not data.initial_batch_number or not data.initial_batch_number.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Không thể bật quản lý lô: Sản phẩm '{product.name}' đang có tồn kho hiện hữu "
                        f"({unallocated_stock} {product.base_unit}) chưa được phân bổ vào lô. "
                        f"Vui lòng cung cấp số lô khởi tạo (initial_batch_number) và hạn sử dụng (initial_expiry_date) "
                        f"để phân bổ tồn kho ban đầu."
                    ),
                )
            if not data.initial_expiry_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Bắt buộc phải có hạn sử dụng (initial_expiry_date) khi khởi tạo lô cho tồn kho hiện hữu.",
                )
            if data.initial_manufacturing_date and data.initial_expiry_date <= data.initial_manufacturing_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Hạn sử dụng khởi tạo bắt buộc phải sau Ngày sản xuất khởi tạo.",
                )

            # Tạo lô khởi tạo cho phần tồn kho chưa phân bổ
            clean_batch = data.initial_batch_number.strip().upper()
            init_batch = ProductBatchEntity(
                product_id=product.id,
                warehouse_id=1,  # Kho mặc định
                batch_number=clean_batch,
                manufacturing_date=data.initial_manufacturing_date,
                expiry_date=data.initial_expiry_date,
                quantity=unallocated_stock,
                created_at=now_utc,
                updated_at=now_utc,
            )
            db.add(init_batch)
            db.flush()
            batch_created_resp = build_batch_response(init_batch, product=product)

        product.is_batch_managed = True

    # Trường hợp 2: Chuyển từ BẬT -> TẮT
    else:
        # Không xóa ProductBatchEntity để bảo toàn lịch sử truy vết
        product.is_batch_managed = False

    # Ghi Audit Log thao tác thay đổi cờ quản lý lô
    log_audit_event(
        db=db,
        user=current_user,
        action_type="PRODUCT_UPDATE",
        entity_type="Product",
        entity_id=product.code,
        old_val={"is_batch_managed": old_flag},
        new_val={"is_batch_managed": new_flag, "reason": data.reason or ""},
        reason=f"Thay đổi cấu hình quản lý lô: {'Bật' if new_flag else 'Tắt'}. {data.reason or ''}".strip(),
    )

    db.commit()
    db.refresh(product)

    msg = (
        f"Đã bật quản lý lô cho sản phẩm '{product.name}'."
        if new_flag
        else f"Đã tắt quản lý lô cho sản phẩm '{product.name}'. Lịch sử các lô cũ vẫn được bảo lưu."
    )

    return BatchTrackingToggleResponse(
        product_id=product.id,
        product_code=product.code,
        product_name=product.name,
        is_batch_managed=new_flag,
        message=msg,
        batch_created=batch_created_resp,
    )


def create_product_batch(
    db: Session,
    data: BatchCreateRequest,
    current_user: UserResponse,
) -> BatchResponse:
    """
    Tiêu chí 2: Khởi tạo/quản lý thông tin từng lô hàng gắn với SKU.
    4 thuộc tính cốt lõi: batch_number, manufacturing_date, expiry_date, remaining_quantity.
    Validation: expiry_date > manufacturing_date, remaining_quantity >= 0.
    """
    product = db.query(ProductEntity).filter(ProductEntity.id == data.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {data.product_id}.",
        )

    warehouse = db.query(WarehouseEntity).filter(WarehouseEntity.id == data.warehouse_id).first()
    if not warehouse:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng có ID {data.warehouse_id}.",
        )

    if data.manufacturing_date and data.expiry_date <= data.manufacturing_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hạn sử dụng (expiry_date) bắt buộc phải sau Ngày sản xuất (manufacturing_date).",
        )

    if data.initial_quantity < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số lượng còn lại của lô không được âm (>= 0).",
        )

    clean_batch = data.batch_number.strip().upper()
    existing = (
        db.query(ProductBatchEntity)
        .filter(
            ProductBatchEntity.product_id == data.product_id,
            ProductBatchEntity.warehouse_id == data.warehouse_id,
            ProductBatchEntity.batch_number == clean_batch,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Số lô '{clean_batch}' đã tồn tại cho sản phẩm '{product.name}' tại kho '{warehouse.name}'.",
        )

    now_utc = get_utc_now()
    new_batch = ProductBatchEntity(
        product_id=data.product_id,
        warehouse_id=data.warehouse_id,
        batch_number=clean_batch,
        manufacturing_date=data.manufacturing_date,
        expiry_date=data.expiry_date,
        quantity=float(data.initial_quantity),
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(new_batch)

    # Nếu tạo lô có sẵn tồn kho ban đầu -> đồng bộ vào tồn kho tổng của SKU và kho
    if data.initial_quantity > 0:
        product.stock = (product.stock or 0) + int(data.initial_quantity)
        ws = (
            db.query(WarehouseStockEntity)
            .filter(
                WarehouseStockEntity.warehouse_id == "WH01",
                WarehouseStockEntity.product_id == data.product_id,
            )
            .first()
        )
        if ws:
            ws.actual_stock = (ws.actual_stock or 0) + int(data.initial_quantity)
            ws.quantity_on_hand = (ws.quantity_on_hand or 0) + int(data.initial_quantity)
            ws.updated_at = now_utc
        else:
            db.add(
                WarehouseStockEntity(
                    warehouse_id="WH01",
                    warehouse_name=warehouse.name,
                    product_id=data.product_id,
                    actual_stock=int(data.initial_quantity),
                    quantity_on_hand=int(data.initial_quantity),
                    min_stock=product.min_stock or 10,
                    created_at=now_utc,
                    updated_at=now_utc,
                )
            )

        # Ghi sổ kho
        db.add(
            InventoryLedgerEntity(
                product_id=data.product_id,
                warehouse_id=data.warehouse_id,
                reference_code=f"BATCH-INIT-{clean_batch}",
                transaction_type="RECEIPT",
                quantity=float(data.initial_quantity),
                previous_stock=float(product.stock - int(data.initial_quantity)),
                new_stock=float(product.stock),
                batch_number=clean_batch,
                performed_by=current_user.full_name or current_user.username,
                note=f"Khởi tạo số lô {clean_batch}. {data.note or ''}".strip(),
                created_at=now_utc,
            )
        )

    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVENTORY_ADJUST",
        entity_type="ProductBatch",
        entity_id=f"{product.code}-{clean_batch}",
        old_val={},
        new_val={
            "batch_number": clean_batch,
            "quantity": data.initial_quantity,
            "mfg_date": str(data.manufacturing_date),
            "exp_date": str(data.expiry_date),
        },
        reason=f"Tạo mới lô hàng {clean_batch}. {data.note or ''}".strip(),
    )

    db.commit()
    db.refresh(new_batch)
    return build_batch_response(new_batch, product=product, warehouse=warehouse)


def list_batches(
    db: Session,
    product_id: Optional[int] = None,
    warehouse_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    warning_days: int = 30,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> BatchListResponse:
    """
    Tiêu chí 3: Cung cấp danh sách lô kèm cảnh báo hết hạn theo ngưỡng.
    Tính toán trạng thái EXPIRED | NEAR_EXPIRY | SAFE theo warning_days.
    Hỗ trợ phân trang máy chủ: page, page_size (mặc định 10 lô/trang).
    """
    query = db.query(ProductBatchEntity)
    if product_id:
        query = query.filter(ProductBatchEntity.product_id == product_id)
    if warehouse_id:
        query = query.filter(ProductBatchEntity.warehouse_id == warehouse_id)

    raw_batches = query.all()

    items: List[BatchResponse] = []
    expired_cnt = 0
    near_expiry_cnt = 0
    safe_cnt = 0

    now_ref = get_utc_now()
    search_lower = search.strip().lower() if search and search.strip() else None

    for b in raw_batches:
        prod = b.product
        prod_name = prod.name if prod else ""
        prod_code = prod.code if prod else ""
        batch_no = b.batch_number or ""

        # Filter search
        if search_lower:
            if (
                search_lower not in batch_no.lower()
                and search_lower not in prod_name.lower()
                and search_lower not in prod_code.lower()
            ):
                continue

        st, days_left = calculate_expiry_status(b.expiry_date, warning_days=warning_days, reference_time=now_ref)
        if st == "EXPIRED":
            expired_cnt += 1
        elif st == "NEAR_EXPIRY":
            near_expiry_cnt += 1
        else:
            safe_cnt += 1

        if status_filter and status_filter.upper() != "ALL":
            if st != status_filter.upper():
                continue

        resp = build_batch_response(b, product=prod, warning_days=warning_days)
        items.append(resp)

    # Sắp xếp ưu tiên: Quá hạn & Gần hết hạn lên đầu theo expiry_date ASC
    items.sort(key=lambda x: (x.expiry_date or datetime.max, -x.remaining_quantity))

    total = len(items)
    safe_page_size = max(1, page_size)
    total_pages = max(1, math.ceil(total / safe_page_size))
    safe_page = max(1, min(page, total_pages)) if total > 0 else 1

    start_idx = (safe_page - 1) * safe_page_size
    end_idx = start_idx + safe_page_size
    paginated_items = items[start_idx:end_idx]

    return BatchListResponse(
        items=paginated_items,
        total=total,
        expired_count=expired_cnt,
        near_expiry_count=near_expiry_cnt,
        safe_count=safe_cnt,
        warning_threshold_days=warning_days,
        page=safe_page,
        page_size=safe_page_size,
        total_pages=total_pages,
    )


def validate_and_deduct_batch_for_issue(
    db: Session,
    product: ProductEntity,
    batch_number: Optional[str],
    quantity_to_deduct: float,
    warehouse_id: Optional[int],
    override_expired: bool,
    override_reason: Optional[str],
    current_user: UserResponse,
) -> Optional[ProductBatchEntity]:
    """
    Tiêu chí 2 & Tiêu chí 4:
    - Nếu SKU bật quản lý lô: Bắt buộc chọn lô.
    - Pessimistic Locking (with_for_update) chống Race Condition / âm kho.
    - Tiêu chí 4: Chặn xuất lô quá hạn (expiry_date <= now_date).
      Chỉ cho phép khi có cờ override_expired và vai trò là Warehouse Manager / Admin kèm lý do.
    """
    is_batch_managed = bool(getattr(product, "is_batch_managed", False))

    if not is_batch_managed:
        return None

    if not batch_number or not batch_number.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Sản phẩm '{product.name}' (Mã: {product.code}) đã bật cấu hình quản lý lô "
                f"(is_batch_managed=True). Bắt buộc phải chỉ định số lô (batch_number) khi xuất kho."
            ),
        )

    clean_batch = batch_number.strip().upper()
    query = db.query(ProductBatchEntity).filter(
        ProductBatchEntity.product_id == product.id,
        ProductBatchEntity.batch_number == clean_batch,
    )
    if warehouse_id:
        query = query.filter(ProductBatchEntity.warehouse_id == warehouse_id)

    # Khóa bi quan dòng dữ liệu để xử lý an toàn đồng thời (Concurrency Safety)
    batch = query.with_for_update().first()
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy số lô '{clean_batch}' của sản phẩm '{product.name}' trong kho.",
        )

    now_utc = get_utc_now()

    # Tiêu chí 4: Kiểm tra hạn sử dụng & Chặn xuất lô quá hạn
    st, days_left = calculate_expiry_status(batch.expiry_date, warning_days=0, reference_time=now_utc)
    if st == "EXPIRED":
        exp_str = batch.expiry_date.strftime("%d/%m/%Y") if batch.expiry_date else "N/A"
        if not override_expired:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"CHẶN XUẤT KHO: Lô hàng '{batch.batch_number}' của sản phẩm '{product.name}' "
                    f"đã quá hạn sử dụng vào ngày {exp_str}. Mặc định hệ thống chặn xuất để bảo vệ đại lý "
                    f"và người tiêu dùng. Cần sự phê duyệt ghi đè từ Quản lý kho kèm lý do nếu muốn xuất."
                ),
            )

        # Kiểm tra thẩm quyền người thực hiện ghi đè
        user_roles = current_user.roles if hasattr(current_user, "roles") and current_user.roles else [current_user.role]
        is_mgr = any(r in ["admin", "warehouse_manager"] for r in user_roles)
        if not is_mgr:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Từ chối truy cập: Chỉ Quản lý kho (Warehouse Manager) hoặc Quản trị viên (Admin) mới có quyền xác nhận ghi đè xuất lô hàng đã quá hạn.",
            )

        if not override_reason or len(override_reason.strip()) < 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bắt buộc phải nhập lý do xác nhận ghi đè khi xuất lô quá hạn (tối thiểu 5 ký tự).",
            )

        # Ghi vết Audit Log chuyên biệt cho sự kiện ghi đè xuất lô quá hạn
        log_audit_event(
            db=db,
            user=current_user,
            action_type="OVERRIDE_EXPIRED_BATCH_ISSUE",
            entity_type="ProductBatch",
            entity_id=f"{product.code}-{batch.batch_number}",
            old_val={
                "batch_number": batch.batch_number,
                "expiry_date": str(batch.expiry_date),
                "remaining_quantity": batch.quantity,
            },
            new_val={
                "deducted_quantity": quantity_to_deduct,
                "override_by": current_user.full_name or current_user.username,
                "override_role": current_user.role,
                "override_reason": override_reason.strip(),
            },
            reason=f"[GHI ĐÈ XUẤT LÔ QUÁ HẠN] {override_reason.strip()}",
        )

    # Tiêu chí 2: Kiểm tra số lượng tồn và trừ tồn an toàn
    if quantity_to_deduct <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số lượng xuất kho từ lô phải lớn hơn 0."
        )

    current_batch_qty = float(batch.quantity or 0.0)
    if current_batch_qty < quantity_to_deduct:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Lô hàng '{batch.batch_number}' không đủ số lượng để xuất "
                f"(Còn lại: {current_batch_qty} {product.base_unit}, Yêu cầu xuất: {quantity_to_deduct} {product.base_unit})."
            ),
        )

    batch.quantity = current_batch_qty - quantity_to_deduct
    batch.updated_at = now_utc

    return batch
