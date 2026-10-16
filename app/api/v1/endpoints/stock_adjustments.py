# backend/app/api/v1/endpoints/stock_adjustments.py
"""
Phân hệ Lập & Phê duyệt Phiếu điều chỉnh tồn kho (Stock Adjustment with Approval Workflow).
Nghiệp vụ cốt lõi:
1. Xử lý minh bạch các nghiệp vụ Hàng hỏng (damaged), Hàng mất (lost), Hàng hết hạn phải huỷ (expired).
2. Bắt buộc chọn lý do cụ thể và ghi rõ giá trị tổn thất (total_loss_value > 0).
3. Quy trình phê duyệt chặt chẽ (Four-Eyes Principle):
   - Chỉ Quản lý kho (warehouse_manager) hoặc Quản trị viên (admin) được duyệt.
   - Người lập và người duyệt TUYỆT ĐỐI KHÔNG ĐƯỢC LÀ MỘT.
4. Mọi phiếu điều chỉnh khi duyệt đều được ghi vào Thẻ kho (InventoryLedgerEntity).
   - Ràng buộc phân quyền: Chỉ Quản lý kho và Admin được nhìn thấy các bản ghi điều chỉnh tổn thất này trên thẻ kho.
"""
from datetime import datetime, timezone
import json
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc

from app.api.deps import require_permission, get_current_user
from app.core.database import get_db
from app.core.rbac import Permission, Role
from app.models.entities import (
    ProductEntity,
    WarehouseStockEntity,
    StockAdjustmentEntity,
    InventoryTransactionEntity,
    AuditLogEntity,
    UserEntity,
)
from app.models.goods_receipt import InventoryLedgerEntity, WarehouseEntity
from app.schemas.auth import UserResponse
from app.schemas.stock_adjustment import (
    StockAdjustmentCreate,
    StockAdjustmentApprove,
    StockAdjustmentReject,
    StockAdjustmentResponse,
    StockAdjustmentItemResponse,
    StockAdjustmentListResponse,
)
from app.services.audit_service import log_audit_event

router = APIRouter()

# Danh mục ánh xạ tên kho chuẩn
DEFAULT_WAREHOUSES = {
    "WH01": "Kho Tổng Hà Nội",
    "WH02": "Kho Chi Nhánh TP. Hồ Chí Minh",
    "WH03": "Kho Chi Nhánh Đà Nẵng",
}

WAREHOUSE_NUMERIC_MAP = {
    "WH01": 1,
    "WH02": 3,
    "WH03": 2,
    "KHO_HN": 1,
    "KHO_HCM": 3,
    "KHO_DN": 2,
    "1": 1,
    "2": 2,
    "3": 3,
}

WAREHOUSE_CODE_ALIASES = {
    "KHO_HN": ["WH01", "KHO_HN"],
    "WH01": ["WH01", "KHO_HN"],
    "KHO_HCM": ["WH02", "KHO_HCM"],
    "WH02": ["WH02", "KHO_HCM"],
    "KHO_DN": ["WH03", "KHO_DN"],
    "WH03": ["WH03", "KHO_DN"],
}

REASON_LABELS = {
    "damaged": "Hàng hỏng",
    "lost": "Hàng mất",
    "expired": "Hàng hết hạn phải huỷ",
    "other": "Lý do khác",
}

STATUS_LABELS = {
    "PENDING": "Chờ duyệt",
    "APPROVED": "Đã duyệt",
    "REJECTED": "Đã từ chối",
}


def _is_manager_or_admin(user: UserResponse) -> bool:
    """Kiểm tra người dùng có quyền Quản lý kho hoặc Quản trị hệ thống."""
    user_roles = [user.role]
    if hasattr(user, "roles") and user.roles:
        user_roles.extend(user.roles)
    return any(r in [Role.SYSTEM_ADMIN.value, Role.WAREHOUSE_MANAGER.value, "admin", "warehouse_manager"] for r in user_roles)


def _is_admin(user: UserResponse) -> bool:
    """Kiểm tra người dùng có quyền Quản trị hệ thống (Admin)."""
    user_roles = [user.role]
    if hasattr(user, "roles") and user.roles:
        user_roles.extend(user.roles)
    return any(r in [Role.SYSTEM_ADMIN.value, "admin", "system_admin"] for r in user_roles)


def _format_adjustment_response(
    adj: StockAdjustmentEntity,
    current_user: Optional[UserResponse] = None,
    db: Optional[Session] = None,
) -> StockAdjustmentResponse:
    """Chuyển đổi entity sang response schema."""
    raw_items = []
    if adj.items_json:
        try:
            raw_items = json.loads(adj.items_json)
        except Exception:
            raw_items = []

    parsed_items: List[StockAdjustmentItemResponse] = []
    for it in raw_items:
        prod_id = it.get("product_id")
        current_actual_stock = None
        if db and prod_id:
            ws = db.query(WarehouseStockEntity).filter(
                WarehouseStockEntity.warehouse_id == adj.warehouse_id,
                WarehouseStockEntity.product_id == prod_id
            ).first()
            if ws:
                current_actual_stock = ws.actual_stock

        parsed_items.append(
            StockAdjustmentItemResponse(
                product_id=it.get("product_id", 0),
                product_code=it.get("product_code", ""),
                product_name=it.get("product_name", ""),
                category=it.get("category"),
                base_unit=it.get("base_unit", "Cái"),
                quantity=it.get("quantity", 0),
                cost_price=float(it.get("cost_price", 0.0)),
                loss_value=float(it.get("loss_value", 0.0)),
                batch_number=it.get("batch_number"),
                note=it.get("note"),
                current_actual_stock=current_actual_stock,
            )
        )

    # Tính cờ can_approve: Người dùng là Quản lý kho / Admin VÀ (KHÔNG PHẢI người lập phiếu HOẶC là Admin)
    can_approve = False
    if current_user:
        is_mgr = _is_manager_or_admin(current_user)
        is_adm = _is_admin(current_user)
        is_creator = False
        if adj.created_by_id is not None and current_user.id is not None and str(adj.created_by_id) == str(current_user.id):
            is_creator = True
        elif adj.created_by and current_user.username and adj.created_by.strip().lower() == current_user.username.strip().lower():
            is_creator = True
        elif adj.created_by and current_user.full_name and adj.created_by.strip().lower() == current_user.full_name.strip().lower():
            is_creator = True

        # Four-Eyes áp dụng cho Quản lý kho, nhưng Admin tối cao được phép tự phê duyệt phiếu của chính mình
        can_approve = is_mgr and (not is_creator or is_adm) and (adj.status == "PENDING")

    return StockAdjustmentResponse(
        id=adj.id,
        code=adj.code,
        warehouse_id=adj.warehouse_id,
        warehouse_name=adj.warehouse_name,
        reason_type=adj.reason_type,
        reason_label=REASON_LABELS.get(adj.reason_type, adj.reason_type),
        reason_note=adj.reason_note,
        total_loss_value=float(adj.total_loss_value or 0.0),
        total_items=adj.total_items or len(parsed_items),
        total_quantity=adj.total_quantity or sum(p.quantity for p in parsed_items),
        status=adj.status,
        status_label=STATUS_LABELS.get(adj.status, adj.status),
        items=parsed_items,
        created_by_id=adj.created_by_id,
        created_by=adj.created_by,
        created_by_role=adj.created_by_role,
        approved_by_id=adj.approved_by_id,
        approved_by=adj.approved_by,
        approved_at=adj.approved_at,
        rejected_by_id=adj.rejected_by_id,
        rejected_by=adj.rejected_by,
        rejected_at=adj.rejected_at,
        rejection_reason=adj.rejection_reason,
        can_approve=can_approve,
        created_at=adj.created_at,
        updated_at=adj.updated_at,
    )


# -----------------------------------------------------------------------------
# 1. TẠO PHIẾU ĐIỀU CHỈNH TỒN KHO (PENDING APPROVAL)
# -----------------------------------------------------------------------------
@router.post("", response_model=StockAdjustmentResponse, status_code=status.HTTP_201_CREATED)
def create_stock_adjustment(
    payload: StockAdjustmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Lập phiếu điều chỉnh tồn kho (Hàng hỏng, Hàng mất, Hết hạn phải huỷ).
    Yêu cầu quyền 'inventory:write' (Thủ kho, Quản lý kho, Admin).
    Tiêu chí bắt buộc:
    - Bắt buộc chọn lý do (reason_type).
    - Bắt buộc nhập chi tiết nguyên nhân (reason_note).
    - Bắt buộc ghi rõ giá trị tổn thất (total_loss_value > 0).
    - Trạng thái khởi tạo: PENDING (Chưa trừ tồn kho thực tế cho tới khi được Quản lý kho phê duyệt).
    """
    # 1. Kiểm tra lý do bắt buộc
    if not payload.reason_type or payload.reason_type not in REASON_LABELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lý do điều chỉnh không hợp lệ. Vui lòng chọn một trong các lý do: {', '.join(REASON_LABELS.keys())}."
        )

    if not payload.reason_note or len(payload.reason_note.strip()) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bắt buộc nhập chi tiết lý do và nguyên nhân tổn thất (tối thiểu 2 ký tự)."
        )

    # 2. Xác định tên kho
    warehouse_name = DEFAULT_WAREHOUSES.get(payload.warehouse_id)
    if not warehouse_name:
        wh_filter = [WarehouseEntity.code == payload.warehouse_id]
        if str(payload.warehouse_id).isdigit():
            wh_filter.append(WarehouseEntity.id == int(payload.warehouse_id))
        wh_entity = db.query(WarehouseEntity).filter(or_(*wh_filter)).first()
        warehouse_name = wh_entity.name if wh_entity else f"Kho {payload.warehouse_id}"

    # 3. Kiểm tra danh sách mặt hàng và tồn kho
    if not payload.items or len(payload.items) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiếu điều chỉnh phải có ít nhất 1 mặt hàng."
        )

    processed_items = []
    calculated_total_loss = 0.0
    total_qty = 0

    for item in payload.items:
        if item.quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Số lượng điều chỉnh giảm phải lớn hơn 0."
            )

        prod = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).first()
        if not prod:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy sản phẩm có ID {item.product_id}."
            )

        if prod.status == "inactive":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{prod.name}' (Mã: {prod.code}) đã ngừng kinh doanh."
            )

        # Kiểm tra tồn kho thực tế trong kho chỉ định
        target_wh_ids = WAREHOUSE_CODE_ALIASES.get(payload.warehouse_id, [payload.warehouse_id])
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id.in_(target_wh_ids),
            WarehouseStockEntity.product_id == item.product_id
        ).first()

        actual_stock = ws.actual_stock if ws else 0
        if actual_stock < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tồn thực tế trong kho '{warehouse_name}' của sản phẩm '{prod.name}' chỉ còn {actual_stock} {prod.base_unit}, không đủ để điều chỉnh giảm {item.quantity} {prod.base_unit}."
            )

        # Đơn giá vốn và giá trị tổn thất
        cost_price = item.cost_price if (item.cost_price is not None and item.cost_price >= 0) else float(prod.cost_price or 0.0)
        item_loss_value = item.loss_value if (item.loss_value is not None and item.loss_value >= 0) else (item.quantity * cost_price)

        calculated_total_loss += item_loss_value
        total_qty += item.quantity

        processed_items.append({
            "product_id": prod.id,
            "product_code": prod.code,
            "product_name": prod.name,
            "category": prod.category,
            "base_unit": prod.base_unit,
            "quantity": item.quantity,
            "cost_price": cost_price,
            "loss_value": item_loss_value,
            "batch_number": item.batch_number,
            "note": item.note,
        })

    final_total_loss = payload.total_loss_value if (payload.total_loss_value is not None and payload.total_loss_value > 0) else calculated_total_loss

    # Tiêu chí bắt buộc: Phải ghi rõ giá trị tổn thất > 0
    if final_total_loss <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bắt buộc ghi rõ giá trị tổn thất (> 0 VNĐ) để xử lý hàng hỏng, hàng mất minh bạch có người chịu trách nhiệm."
        )

    # 4. Sinh mã phiếu duy nhất
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    unique_suffix = uuid.uuid4().hex[:6].upper()
    code = f"ADJ-{payload.warehouse_id}-{date_str}-{unique_suffix}"

    # 5. Lưu phiếu ở trạng thái PENDING
    adjustment = StockAdjustmentEntity(
        code=code,
        warehouse_id=payload.warehouse_id,
        warehouse_name=warehouse_name,
        reason_type=payload.reason_type,
        reason_note=payload.reason_note.strip(),
        total_loss_value=float(final_total_loss),
        total_items=len(processed_items),
        total_quantity=total_qty,
        status="PENDING",
        items_json=json.dumps(processed_items, ensure_ascii=False),
        created_by_id=current_user.id,
        created_by=current_user.full_name or current_user.username,
        created_by_role=current_user.role,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db.add(adjustment)
    db.commit()
    db.refresh(adjustment)

    # Ghi nhật ký kiểm toán (Audit Log)
    log_audit_event(
        db=db,
        user=current_user,
        action_type="STOCK_ADJUSTMENT_CREATED",
        entity_type="StockAdjustment",
        entity_id=adjustment.code,
        old_val=None,
        new_val={
            "warehouse_id": payload.warehouse_id,
            "reason_type": payload.reason_type,
            "reason_note": payload.reason_note,
            "total_loss_value": final_total_loss,
            "total_quantity": total_qty,
            "status": "PENDING",
            "items_count": len(processed_items),
        },
        reason=f"Lập phiếu điều chỉnh tổn kho [{REASON_LABELS.get(payload.reason_type)}]. Giá trị tổn thất: {final_total_loss:,.0f} đ. Chờ quản lý phê duyệt.",
        request=request,
    )

    return _format_adjustment_response(adjustment, current_user, db)


# -----------------------------------------------------------------------------
# 2. DANH SÁCH PHIẾU ĐIỀU CHỈNH TỒN KHO
# -----------------------------------------------------------------------------
@router.get("", response_model=StockAdjustmentListResponse)
def get_stock_adjustments(
    warehouse_id: Optional[str] = Query(None, description="Lọc theo mã kho"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái: PENDING | APPROVED | REJECTED"),
    reason_type: Optional[str] = Query(None, description="Lọc theo lý do: damaged | lost | expired | other"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã phiếu, ghi chú, người lập"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value))
):
    """Lấy danh sách các phiếu điều chỉnh tồn kho."""
    query = db.query(StockAdjustmentEntity)

    if warehouse_id and warehouse_id != "all":
        query = query.filter(StockAdjustmentEntity.warehouse_id == warehouse_id)

    if status_filter and status_filter != "all":
        query = query.filter(StockAdjustmentEntity.status == status_filter.upper())

    if reason_type and reason_type != "all":
        query = query.filter(StockAdjustmentEntity.reason_type == reason_type)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                StockAdjustmentEntity.code.ilike(term),
                StockAdjustmentEntity.reason_note.ilike(term),
                StockAdjustmentEntity.created_by.ilike(term),
                StockAdjustmentEntity.approved_by.ilike(term),
            )
        )

    total = query.count()
    records = query.order_by(desc(StockAdjustmentEntity.id)).offset(offset).limit(limit).all()

    # Thống kê nhanh
    base_stat = db.query(StockAdjustmentEntity)
    if warehouse_id and warehouse_id != "all":
        base_stat = base_stat.filter(StockAdjustmentEntity.warehouse_id == warehouse_id)

    pending_count = base_stat.filter(StockAdjustmentEntity.status == "PENDING").count()
    approved_count = base_stat.filter(StockAdjustmentEntity.status == "APPROVED").count()
    rejected_count = base_stat.filter(StockAdjustmentEntity.status == "REJECTED").count()

    total_loss_sum = sum(
        float(r.total_loss_value or 0.0)
        for r in base_stat.filter(StockAdjustmentEntity.status.in_(["PENDING", "APPROVED"])).all()
    )

    items = [_format_adjustment_response(r, current_user, db) for r in records]

    return StockAdjustmentListResponse(
        items=items,
        total=total,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        total_loss_value_sum=total_loss_sum,
    )


# -----------------------------------------------------------------------------
# 3. CHI TIẾT PHIẾU ĐIỀU CHỈNH
# -----------------------------------------------------------------------------
@router.get("/{adjustment_id}", response_model=StockAdjustmentResponse)
def get_stock_adjustment_detail(
    adjustment_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value))
):
    """Xem chi tiết phiếu điều chỉnh tồn kho."""
    adj = db.query(StockAdjustmentEntity).filter(StockAdjustmentEntity.id == adjustment_id).first()
    if not adj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu điều chỉnh có ID {adjustment_id}."
        )

    return _format_adjustment_response(adj, current_user, db)


# -----------------------------------------------------------------------------
# 4. PHÊ DUYỆT PHIẾU ĐIỀU CHỈNH (FOUR-EYES PRINCIPLE & CẬP NHẬT KHO + THẺ KHO)
# -----------------------------------------------------------------------------
@router.post("/{adjustment_id}/approve", response_model=StockAdjustmentResponse)
def approve_stock_adjustment(
    adjustment_id: int,
    payload: Optional[StockAdjustmentApprove] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Phê duyệt phiếu điều chỉnh tồn kho.
    YÊU CẦU NGHIÊM NGẶT:
    1. Chỉ Quản lý kho (Role: warehouse_manager) hoặc Quản trị viên (Role: admin) mới có quyền duyệt.
       Thủ kho thông thường (warehouse) hoặc Sales -> 403 Forbidden.
    2. NGUYÊN TẮC BỐN MẮT (FOUR-EYES PRINCIPLE):
       Người lập và người duyệt TUYỆT ĐỐI KHÔNG ĐƯỢC LÀ MỘT.
       Nếu người lập phiếu (kể cả là Quản lý kho) tự duyệt phiếu của chính mình -> Báo lỗi 400 Bad Request.
    3. CÂN ĐỐI TỒN KHO & GHI THẺ KHO:
       - Trừ tồn thực tế trong kho tương ứng.
       - Ghi Thẻ kho (InventoryLedgerEntity) với reference_code = mã phiếu điều chỉnh.
       - Ghi InventoryTransactionEntity và AuditLogEntity.
    """
    # 1. Kiểm tra vai trò duyệt: Chỉ Quản lý kho hoặc Admin
    if not _is_manager_or_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản lý kho (Warehouse Manager) hoặc Quản trị viên (Admin) mới có quyền phê duyệt phiếu điều chỉnh tồn kho."
        )

    # 2. Tìm phiếu điều chỉnh
    adj = db.query(StockAdjustmentEntity).filter(StockAdjustmentEntity.id == adjustment_id).first()
    if not adj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu điều chỉnh có ID {adjustment_id}."
        )

    # 3. Kiểm tra trạng thái phiếu
    if adj.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu điều chỉnh '{adj.code}' đã ở trạng thái '{STATUS_LABELS.get(adj.status, adj.status)}', không thể phê duyệt lại."
        )

    # 4. RÀNG BUỘC CỐT LÕI: FOUR-EYES PRINCIPLE (Người lập và người duyệt không được là một, NGOẠI TRỪ Admin tối cao)
    is_adm = _is_admin(current_user)
    is_same_user_id = (adj.created_by_id is not None and current_user.id is not None and str(adj.created_by_id) == str(current_user.id))
    is_same_username = (adj.created_by and current_user.username and adj.created_by.strip().lower() == current_user.username.strip().lower())
    is_same_fullname = (adj.created_by and current_user.full_name and adj.created_by.strip().lower() == current_user.full_name.strip().lower())

    if (is_same_user_id or is_same_username or is_same_fullname) and not is_adm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quy chuẩn phê duyệt độc lập (Four-eyes principle): Quản lý kho không thể tự duyệt phiếu do chính mình lập. Vui lòng để một Quản lý kho khác hoặc Quản trị viên phê duyệt phiếu này."
        )

    # 5. Phân tích danh sách hàng để cân đối trừ kho
    raw_items = []
    if adj.items_json:
        try:
            raw_items = json.loads(adj.items_json)
        except Exception:
            raw_items = []

    if not raw_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiếu điều chỉnh không có danh sách mặt hàng hợp lệ."
        )

    # Xác định ID số nguyên cho kho để liên kết Thẻ kho (InventoryLedgerEntity)
    numeric_wh_id = WAREHOUSE_NUMERIC_MAP.get(adj.warehouse_id)
    if not numeric_wh_id:
        wh_filter = [WarehouseEntity.code == adj.warehouse_id]
        if str(adj.warehouse_id).isdigit():
            wh_filter.append(WarehouseEntity.id == int(adj.warehouse_id))
        wh_row = db.query(WarehouseEntity).filter(or_(*wh_filter)).first()
        numeric_wh_id = wh_row.id if wh_row else 1

    reason_display = REASON_LABELS.get(adj.reason_type, adj.reason_type)
    approval_note = payload.note.strip() if (payload and payload.note) else ""
    now_utc = datetime.now(timezone.utc)

    # Thực hiện trừ tồn kho & ghi thẻ kho trong Transaction
    for it in raw_items:
        prod_id = it.get("product_id")
        qty = int(it.get("quantity", 0))
        loss_val = float(it.get("loss_value", 0.0))

        prod = db.query(ProductEntity).filter(ProductEntity.id == prod_id).first()
        if not prod:
            continue

        target_wh_ids = WAREHOUSE_CODE_ALIASES.get(adj.warehouse_id, [adj.warehouse_id])
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id.in_(target_wh_ids),
            WarehouseStockEntity.product_id == prod_id
        ).first()

        prev_actual = ws.actual_stock if ws else prod.stock
        new_actual = max(0, prev_actual - qty)

        # Cập nhật tồn kho theo kho
        if ws:
            ws.actual_stock = new_actual
            if ws.quantity_on_hand is not None:
                ws.quantity_on_hand = max(0, (ws.quantity_on_hand or 0) - qty)
            else:
                ws.quantity_on_hand = new_actual
            ws.updated_at = now_utc

        # Cập nhật tổng tồn trên sản phẩm
        prod.stock = max(0, (prod.stock or 0) - qty)

        # GHI THẺ KHO (InventoryLedgerEntity)
        ledger_entry = InventoryLedgerEntity(
            product_id=prod.id,
            warehouse_id=numeric_wh_id,
            reference_code=adj.code,
            transaction_type="ADJUST",
            quantity=-float(qty),
            previous_stock=float(prev_actual),
            new_stock=float(new_actual),
            batch_number=it.get("batch_number"),
            performed_by=current_user.full_name or current_user.username,
            note=f"Điều chỉnh tổn thất [{reason_display}]: {adj.reason_note}. Tổn thất: {loss_val:,.0f} đ",
            created_at=now_utc,
        )
        db.add(ledger_entry)

        # Ghi nhật ký biến động kho (InventoryTransactionEntity)
        inv_tx = InventoryTransactionEntity(
            product_id=prod.id,
            product_name=prod.name,
            type="adjust",
            quantity=-qty,
            previous_stock=prev_actual,
            new_stock=new_actual,
            unit_name=prod.base_unit,
            conversion_rate=1.0,
            base_quantity=-qty,
            performed_by=current_user.username,
            user_role=current_user.role,
            reason=f"Phiếu điều chỉnh {adj.code} [{reason_display}]: {adj.reason_note}. Tổn thất: {loss_val:,.0f} đ",
        )
        db.add(inv_tx)

    # 6. Cập nhật trạng thái phiếu sang APPROVED
    adj.status = "APPROVED"
    adj.approved_by_id = current_user.id
    adj.approved_by = current_user.full_name or current_user.username
    adj.approved_at = now_utc
    adj.updated_at = now_utc

    db.commit()
    db.refresh(adj)

    # Ghi nhật ký kiểm toán (Audit Log)
    log_audit_event(
        db=db,
        user=current_user,
        action_type="STOCK_ADJUSTMENT_APPROVED",
        entity_type="StockAdjustment",
        entity_id=adj.code,
        old_val={"status": "PENDING"},
        new_val={
            "status": "APPROVED",
            "approved_by": adj.approved_by,
            "approved_at": str(now_utc),
            "approval_note": approval_note,
        },
        reason=f"Phê duyệt phiếu điều chỉnh tồn kho {adj.code} (Người lập: {adj.created_by}, Người duyệt: {adj.approved_by}). Tổng giá trị tổn thất: {adj.total_loss_value:,.0f} đ.",
        request=request,
    )

    return _format_adjustment_response(adj, current_user, db)


# -----------------------------------------------------------------------------
# 5. TỪ CHỐI PHÊ DUYỆT PHIẾU ĐIỀU CHỈNH
# -----------------------------------------------------------------------------
@router.post("/{adjustment_id}/reject", response_model=StockAdjustmentResponse)
def reject_stock_adjustment(
    adjustment_id: int,
    payload: StockAdjustmentReject,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Từ chối phê duyệt phiếu điều chỉnh tồn kho.
    Yêu cầu:
    - Chỉ Quản lý kho hoặc Admin được từ chối.
    - Bắt buộc nhập lý do từ chối (reason).
    - Tồn kho và thẻ kho KHÔNG bị thay đổi.
    """
    if not _is_manager_or_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản lý kho hoặc Quản trị viên mới có quyền từ chối phê duyệt phiếu điều chỉnh tồn kho."
        )

    adj = db.query(StockAdjustmentEntity).filter(StockAdjustmentEntity.id == adjustment_id).first()
    if not adj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu điều chỉnh có ID {adjustment_id}."
        )

    if adj.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu điều chỉnh '{adj.code}' đã ở trạng thái '{STATUS_LABELS.get(adj.status, adj.status)}', không thể từ chối."
        )

    if not payload.reason or len(payload.reason.strip()) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bắt buộc nhập lý do từ chối phê duyệt (tối thiểu 2 ký tự)."
        )

    now_utc = datetime.now(timezone.utc)
    adj.status = "REJECTED"
    adj.rejected_by_id = current_user.id
    adj.rejected_by = current_user.full_name or current_user.username
    adj.rejected_at = now_utc
    adj.rejection_reason = payload.reason.strip()
    adj.updated_at = now_utc

    db.commit()
    db.refresh(adj)

    log_audit_event(
        db=db,
        user=current_user,
        action_type="STOCK_ADJUSTMENT_REJECTED",
        entity_type="StockAdjustment",
        entity_id=adj.code,
        old_val={"status": "PENDING"},
        new_val={"status": "REJECTED", "rejection_reason": adj.rejection_reason},
        reason=f"Từ chối phiếu điều chỉnh tồn kho {adj.code}. Lý do: {adj.rejection_reason}",
        request=request,
    )

    return _format_adjustment_response(adj, current_user, db)
