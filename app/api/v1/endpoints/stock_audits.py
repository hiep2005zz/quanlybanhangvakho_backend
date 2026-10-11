# backend/app/api/v1/endpoints/stock_audits.py
from datetime import datetime, timezone
import json
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.api.deps import require_warehouse_manager, require_warehouse_read
from app.schemas.auth import UserResponse
from app.models.entities import (
    StockAuditEntity,
    WarehouseStockEntity,
    ProductEntity,
    CategoryEntity,
    InventoryTransactionEntity,
)
from app.api.v1.endpoints.inventory import INVENTORY_TRANSACTIONS, _find_product
from app.schemas.inventory import InventoryTransaction
from app.services.inventory_availability_service import (
    DEFAULT_WAREHOUSES,
    get_or_create_warehouse_stock,
)
from app.services.audit_service import log_audit_event
from app.schemas.stock_audit import (
    StockAuditCreate,
    StockAuditUpdate,
    StockAuditConfirm,
    StockAuditCancel,
    StockAuditResponse,
    StockAuditListResponse,
    StockAuditListItem,
    StockAuditItemResponse,
)

router = APIRouter()


def get_utc_now():
    return datetime.now(timezone.utc)


def get_all_descendant_category_ids(db: Session, cat_id: int) -> set:
    """Lấy danh sách ID nhóm hàng bao gồm nhóm chỉ định và toàn bộ các nhóm con đệ quy."""
    ids = {cat_id}
    children = db.query(CategoryEntity).filter(CategoryEntity.parent_id == cat_id).all()
    for child in children:
        ids.update(get_all_descendant_category_ids(db, child.id))
    return ids


STATUS_LABELS = {
    "IN_PROGRESS": "Đang kiểm kê",
    "CONFIRMED": "Hoàn tất",
    "CANCELLED": "Đã hủy",
}


def _generate_audit_code(db: Session) -> str:
    """Sinh mã phiếu kiểm kê duy nhất theo định dạng PKK-YYYYMMDD-XXXX."""
    today_str = datetime.now().strftime("%Y%m%d")
    prefix = f"PKK-{today_str}-"
    # Tìm mã lớn nhất trong ngày
    latest = (
        db.query(StockAuditEntity.code)
        .filter(StockAuditEntity.code.like(f"{prefix}%"))
        .order_by(StockAuditEntity.code.desc())
        .first()
    )
    if latest and latest[0]:
        try:
            seq = int(latest[0].split("-")[-1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def _format_audit_item_response(itm: dict, current_stock: Optional[int] = None) -> StockAuditItemResponse:
    sys_stock = itm.get("system_stock", 0)
    act_stock = itm.get("actual_stock")
    disc = act_stock - sys_stock if act_stock is not None else None

    if act_stock is None:
        result_status = "PENDING"
        result_label = "Chưa kiểm"
    elif disc == 0:
        result_status = "MATCH"
        result_label = "Khớp"
    elif disc < 0:
        result_status = "DEFICIT"
        result_label = f"Thiếu {abs(disc)} {itm.get('base_unit', 'Cái')}"
    else:
        result_status = "SURPLUS"
        result_label = f"Thừa {disc} {itm.get('base_unit', 'Cái')}"

    return StockAuditItemResponse(
        product_id=itm["product_id"],
        product_code=itm["product_code"],
        product_name=itm["product_name"],
        category_name=itm.get("category_name"),
        base_unit=itm.get("base_unit", "Cái"),
        system_stock=sys_stock,
        actual_stock=act_stock,
        discrepancy=disc,
        result_status=result_status,
        result_label=result_label,
        reason=itm.get("reason"),
        reserved_stock=itm.get("reserved_stock", 0),
        current_actual_stock=current_stock if current_stock is not None else sys_stock,
    )


def _format_audit_response(audit: StockAuditEntity, db: Session = None) -> StockAuditResponse:
    items_raw = []
    if audit.items_json:
        try:
            items_raw = json.loads(audit.items_json)
        except Exception:
            items_raw = []

    # Map tồn hiện tại ở kho nếu có db session
    current_stocks_map = {}
    if db and audit.warehouse_id:
        p_ids = [it.get("product_id") for it in items_raw if it.get("product_id")]
        if p_ids:
            records = (
                db.query(WarehouseStockEntity)
                .filter(
                    WarehouseStockEntity.warehouse_id == audit.warehouse_id,
                    WarehouseStockEntity.product_id.in_(p_ids),
                )
                .all()
            )
            for r in records:
                current_stocks_map[r.product_id] = r.actual_stock or 0

    items_formatted = [
        _format_audit_item_response(it, current_stocks_map.get(it.get("product_id")))
        for it in items_raw
    ]

    status_str = audit.status or "IN_PROGRESS"
    return StockAuditResponse(
        id=audit.id,
        code=audit.code,
        warehouse_id=audit.warehouse_id or "",
        warehouse_name=audit.warehouse_name or "",
        category_id=audit.category_id,
        category_name=audit.category_name,
        scope_type=audit.scope_type or "WAREHOUSE",
        status=status_str,
        status_label=STATUS_LABELS.get(status_str, status_str),
        note=audit.note,
        total_items=audit.total_items or len(items_formatted),
        discrepancy_items_count=audit.discrepancy_items_count or 0,
        total_discrepancy_qty=audit.total_discrepancy_qty or 0,
        items=items_formatted,
        created_by=audit.created_by or "",
        confirmed_by=audit.confirmed_by,
        confirmed_at=audit.confirmed_at,
        cancelled_by=audit.cancelled_by,
        cancelled_at=audit.cancelled_at,
        created_at=audit.created_at or get_utc_now(),
        updated_at=audit.updated_at,
    )


@router.get("/warehouses")
def get_audit_warehouses(
    current_user: UserResponse = Depends(require_warehouse_read),
):
    """Lấy danh sách các kho hỗ trợ kiểm kê."""
    return [
        {"id": w_id, "name": w_name}
        for w_id, w_name in DEFAULT_WAREHOUSES.items()
    ]


@router.get("/preview-products")
def preview_audit_products(
    warehouse_id: str = Query(..., description="Mã kho cần kiểm kê"),
    scope_type: str = Query("WAREHOUSE", description="Phạm vi: WAREHOUSE hoặc CATEGORY"),
    category_id: Optional[int] = Query(None, description="ID nhóm hàng nếu scope_type == CATEGORY"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_read),
):
    """
    Xem trước danh sách sản phẩm và số lượng tồn chốt dự kiến theo phạm vi kiểm kê.
    """
    if warehouse_id not in DEFAULT_WAREHOUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kho '{warehouse_id}' không hợp lệ trong hệ thống."
        )

    warehouse_name = DEFAULT_WAREHOUSES[warehouse_id]

    query = db.query(ProductEntity).filter(ProductEntity.status == "active")
    if scope_type == "CATEGORY":
        if not category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng chọn nhóm hàng khi kiểm kê theo phạm vi Nhóm hàng."
            )
        cat = db.query(CategoryEntity).filter(CategoryEntity.id == category_id).first()
        if not cat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy nhóm hàng có ID {category_id}."
            )
        cat_ids = get_all_descendant_category_ids(db, category_id)
        cat_names = {c.name for c in db.query(CategoryEntity).filter(CategoryEntity.id.in_(cat_ids)).all()}
        query = query.filter((ProductEntity.category_id.in_(cat_ids)) | (ProductEntity.category.in_(cat_names)))

    products = query.order_by(ProductEntity.id.asc()).all()

    items = []
    for prod in products:
        stock_rec = get_or_create_warehouse_stock(db, warehouse_id, warehouse_name, prod.id, for_update=False)
        items.append({
            "product_id": prod.id,
            "product_code": prod.code,
            "product_name": prod.name,
            "category_name": prod.category,
            "base_unit": prod.base_unit or "Cái",
            "system_stock": stock_rec.actual_stock or 0,
            "reserved_stock": stock_rec.reserved_stock or 0,
            "available_stock": stock_rec.available_stock,
        })

    return {
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "scope_type": scope_type,
        "category_id": category_id,
        "total_items": len(items),
        "items": items,
    }


@router.get("", response_model=StockAuditListResponse)
def list_stock_audits(
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã phiếu"),
    warehouse_id: Optional[str] = Query(None, description="Lọc theo kho"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái: IN_PROGRESS, CONFIRMED, CANCELLED"),
    scope_type: Optional[str] = Query(None, description="Lọc theo phạm vi"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_read),
):
    """
    Chức năng 1: Quản lý danh sách phiếu kiểm kê.
    Cho phép tìm kiếm, lọc theo kho, trạng thái, phạm vi và phân trang.
    """
    query = db.query(StockAuditEntity)

    if search:
        search_kw = f"%{search.strip()}%"
        query = query.filter(StockAuditEntity.code.ilike(search_kw))

    if warehouse_id:
        query = query.filter(StockAuditEntity.warehouse_id == warehouse_id)

    if status_filter:
        query = query.filter(StockAuditEntity.status == status_filter.upper())

    if scope_type:
        query = query.filter(StockAuditEntity.scope_type == scope_type.upper())

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)

    audits = (
        query.order_by(StockAuditEntity.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for a in audits:
        st = a.status or "IN_PROGRESS"
        items.append(
            StockAuditListItem(
                id=a.id,
                code=a.code,
                warehouse_id=a.warehouse_id or "",
                warehouse_name=a.warehouse_name or "",
                category_id=a.category_id,
                category_name=a.category_name,
                scope_type=a.scope_type or "WAREHOUSE",
                status=st,
                status_label=STATUS_LABELS.get(st, st),
                note=a.note,
                total_items=a.total_items or 0,
                discrepancy_items_count=a.discrepancy_items_count or 0,
                total_discrepancy_qty=a.total_discrepancy_qty or 0,
                created_by=a.created_by or "",
                confirmed_by=a.confirmed_by,
                confirmed_at=a.confirmed_at,
                cancelled_by=a.cancelled_by,
                cancelled_at=a.cancelled_at,
                created_at=a.created_at or get_utc_now(),
                updated_at=a.updated_at,
            )
        )

    return StockAuditListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{audit_id}", response_model=StockAuditResponse)
def get_stock_audit_detail(
    audit_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_read),
):
    """
    Chức năng 3 & 7: Xem chi tiết phiếu kiểm kê và kết quả chênh lệch.
    """
    audit = db.query(StockAuditEntity).filter(StockAuditEntity.id == audit_id).first()
    if not audit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu kiểm kê có ID {audit_id}."
        )
    return _format_audit_response(audit, db)


@router.post("", response_model=StockAuditResponse, status_code=status.HTTP_201_CREATED)
def create_stock_audit(
    data: StockAuditCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_manager),
):
    """
    Chức năng 2: Tạo phiếu kiểm kê.
    Chỉ Quản lý kho (Role: warehouse_manager) và Admin được phép tạo.
    Chốt số lượng tồn kho ban đầu của từng sản phẩm tại kho được chọn làm căn cứ.
    """
    if data.warehouse_id not in DEFAULT_WAREHOUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã kho '{data.warehouse_id}' không hợp lệ. Vui lòng chọn kho có thật trong hệ thống."
        )

    warehouse_name = DEFAULT_WAREHOUSES[data.warehouse_id]

    category_name = None
    if data.scope_type == "CATEGORY":
        if not data.category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng chọn nhóm hàng khi chọn phạm vi kiểm kê theo Nhóm hàng."
            )
        cat = db.query(CategoryEntity).filter(CategoryEntity.id == data.category_id).first()
        if not cat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy nhóm hàng có ID {data.category_id}."
            )
        category_name = cat.name

    # Lấy danh sách sản phẩm thuộc phạm vi
    prod_query = db.query(ProductEntity).filter(ProductEntity.status == "active")
    if data.scope_type == "CATEGORY" and data.category_id:
        cat_ids = get_all_descendant_category_ids(db, data.category_id)
        cat_names = {c.name for c in db.query(CategoryEntity).filter(CategoryEntity.id.in_(cat_ids)).all()}
        prod_query = prod_query.filter(
            (ProductEntity.category_id.in_(cat_ids)) | (ProductEntity.category.in_(cat_names))
        )

    if data.product_ids and len(data.product_ids) > 0:
        prod_query = prod_query.filter(ProductEntity.id.in_(data.product_ids))

    products = prod_query.order_by(ProductEntity.id.asc()).all()

    if not products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không tìm thấy sản phẩm nào phù hợp với phạm vi kiểm kê đã chọn."
        )

    # Chốt tồn kho làm căn cứ cho từng sản phẩm tại kho được chọn
    items = []
    for prod in products:
        stock_rec = get_or_create_warehouse_stock(
            db=db,
            warehouse_id=data.warehouse_id,
            warehouse_name=warehouse_name,
            product_id=prod.id,
            for_update=False,
        )
        sys_stock = stock_rec.actual_stock or 0
        res_stock = stock_rec.reserved_stock or 0

        items.append({
            "product_id": prod.id,
            "product_code": prod.code,
            "product_name": prod.name,
            "category_name": prod.category,
            "base_unit": prod.base_unit or "Cái",
            "system_stock": sys_stock,
            "actual_stock": None,
            "discrepancy": None,
            "reason": None,
            "reserved_stock": res_stock,
        })

    audit_code = _generate_audit_code(db)
    creator_name = current_user.full_name or current_user.username

    audit = StockAuditEntity(
        code=audit_code,
        warehouse_id=data.warehouse_id,
        warehouse_name=warehouse_name,
        category_id=data.category_id,
        category_name=category_name,
        scope_type=data.scope_type,
        status="IN_PROGRESS",
        note=data.note,
        total_items=len(items),
        discrepancy_items_count=0,
        total_discrepancy_qty=0,
        items_json=json.dumps(items, ensure_ascii=False),
        created_by=creator_name,
        created_at=get_utc_now(),
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)

    return _format_audit_response(audit, db)


@router.put("/{audit_id}", response_model=StockAuditResponse)
def update_stock_audit_results(
    audit_id: int,
    data: StockAuditUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_manager),
):
    """
    Chức năng 3: Nhập kết quả kiểm kê thực tế và lý do chênh lệch (Lưu nháp / cập nhật).
    Chỉ cho phép sửa khi phiếu đang ở trạng thái 'IN_PROGRESS' (Đang kiểm kê).
    """
    audit = db.query(StockAuditEntity).filter(StockAuditEntity.id == audit_id).first()
    if not audit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu kiểm kê có ID {audit_id}."
        )

    if audit.status != "IN_PROGRESS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu kiểm kê '{audit.code}' đang ở trạng thái '{STATUS_LABELS.get(audit.status, audit.status)}', không thể chỉnh sửa."
        )

    try:
        current_items = json.loads(audit.items_json or "[]")
    except Exception:
        current_items = []

    # Map input items
    input_map = {item.product_id: item for item in data.items}

    discrepancy_count = 0
    total_disc_qty = 0

    for itm in current_items:
        pid = itm.get("product_id")
        if pid in input_map:
            inp = input_map[pid]
            if inp.actual_stock is not None:
                if inp.actual_stock < 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Số lượng thực tế của sản phẩm '{itm.get('product_name')}' không thể nhỏ hơn 0."
                    )
                itm["actual_stock"] = inp.actual_stock
                # Backend tự tính lại chênh lệch
                disc = inp.actual_stock - itm.get("system_stock", 0)
                itm["discrepancy"] = disc
                if disc != 0:
                    discrepancy_count += 1
                    total_disc_qty += disc
            else:
                itm["actual_stock"] = None
                itm["discrepancy"] = None

            if inp.reason is not None:
                itm["reason"] = inp.reason.strip() if inp.reason else None

    if data.note is not None:
        audit.note = data.note

    audit.items_json = json.dumps(current_items, ensure_ascii=False)
    audit.discrepancy_items_count = discrepancy_count
    audit.total_discrepancy_qty = total_disc_qty
    audit.updated_at = get_utc_now()

    db.commit()
    db.refresh(audit)

    return _format_audit_response(audit, db)


@router.post("/{audit_id}/confirm", response_model=StockAuditResponse)
def confirm_stock_audit(
    audit_id: int,
    data: Optional[StockAuditConfirm] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_manager),
):
    """
    Chức năng 4: Xác nhận và điều chỉnh tồn kho.
    Quy tắc an toàn:
    1. Chỉ Quản lý kho (warehouse_manager) và Admin được phép xác nhận.
    2. Phiếu phải ở trạng thái IN_PROGRESS. Chặn tuyệt đối xác nhận hai lần.
    3. Tất cả sản phẩm bắt buộc phải có số lượng thực tế (>= 0).
    4. Bắt buộc nhập lý do đối với các dòng có chênh lệch.
    5. Điều chỉnh tồn kho:
       - Tồn kho được điều chỉnh dựa trên chênh lệch phát hiện: new_stock = current_stock + (actual_stock - system_stock)
         nhằm bảo đảm an toàn khi có giao dịch nhập/xuất phát sinh trong lúc kiểm kê.
       - Tồn kho sau điều chỉnh không được âm.
       - Ghi nhận đầy đủ vào inventory_transactions và audit_logs trong Database Transaction.
    """
    audit = (
        db.query(StockAuditEntity)
        .filter(StockAuditEntity.id == audit_id)
        .with_for_update()
        .first()
    )
    if not audit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu kiểm kê có ID {audit_id}."
        )

    # Chặn xác nhận 2 lần hoặc xác nhận phiếu đã hủy
    if audit.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu kiểm kê '{audit.code}' đã được xác nhận trước đó bởi {audit.confirmed_by}. Không thể xác nhận hai lần."
        )
    if audit.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu kiểm kê '{audit.code}' đã bị hủy, không thể xác nhận."
        )
    if audit.status != "IN_PROGRESS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu kiểm kê đang ở trạng thái '{audit.status}', không thể xác nhận."
        )

    try:
        items = json.loads(audit.items_json or "[]")
    except Exception:
        items = []

    # Cập nhật kết quả gửi kèm nếu có
    if data and data.items:
        input_map = {item.product_id: item for item in data.items}
        for itm in items:
            pid = itm.get("product_id")
            if pid in input_map:
                inp = input_map[pid]
                if inp.actual_stock is not None:
                    if inp.actual_stock < 0:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Số lượng thực tế của '{itm.get('product_name')}' không thể nhỏ hơn 0."
                        )
                    itm["actual_stock"] = inp.actual_stock
                    itm["discrepancy"] = inp.actual_stock - itm.get("system_stock", 0)
                if inp.reason is not None:
                    itm["reason"] = inp.reason.strip() if inp.reason else None

    # Kiểm tra tính đầy đủ và hợp lệ của dữ liệu trước khi điều chỉnh
    discrepancy_count = 0
    total_disc_qty = 0

    for itm in items:
        p_name = itm.get("product_name", f"SP #{itm.get('product_id')}")
        p_code = itm.get("product_code", "")
        actual = itm.get("actual_stock")
        sys_stock = itm.get("system_stock", 0)

        # 1. Bắt buộc có số lượng thực tế
        if actual is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{p_name}' ({p_code}) chưa được nhập số lượng thực tế. Vui lòng kiểm đếm đầy đủ trước khi xác nhận."
            )
        if actual < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Số lượng thực tế của '{p_name}' ({p_code}) không hợp lệ ({actual}). Không được nhập số âm."
            )

        # 2. Tự tính chênh lệch
        disc = actual - sys_stock
        itm["discrepancy"] = disc

        # 3. Bắt buộc nhập lý do đối với dòng có chênh lệch
        if disc != 0:
            reason = (itm.get("reason") or "").strip()
            if not reason:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bắt buộc nhập lý do chênh lệch đối với sản phẩm '{p_name}' ({p_code}) có độ lệch {disc:+d} {itm.get('base_unit', 'Cái')}."
                )
            discrepancy_count += 1
            total_disc_qty += disc

    # BẮT ĐẦU ĐIỀU CHỈNH TỒN KHO TRONG TRANSACTION
    warehouse_id = audit.warehouse_id
    warehouse_name = audit.warehouse_name or DEFAULT_WAREHOUSES.get(warehouse_id, "Kho Tổng Hà Nội")
    confirmer_name = current_user.full_name or current_user.username
    now_dt = get_utc_now()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    adjustments_log = []

    for itm in items:
        pid = itm.get("product_id")
        p_name = itm.get("product_name")
        p_code = itm.get("product_code")
        base_unit = itm.get("base_unit", "Cái")
        actual_counted = itm["actual_stock"]
        system_frozen = itm["system_stock"]
        adjustment = actual_counted - system_frozen
        reason = itm.get("reason") or "Kiểm kê kho khớp"

        # Khóa bản ghi tồn kho tại kho được kiểm kê
        stock_rec = (
            db.query(WarehouseStockEntity)
            .filter(
                WarehouseStockEntity.warehouse_id == warehouse_id,
                WarehouseStockEntity.product_id == pid,
            )
            .with_for_update()
            .first()
        )
        if not stock_rec:
            stock_rec = get_or_create_warehouse_stock(db, warehouse_id, warehouse_name, pid, for_update=True)

        current_actual = stock_rec.actual_stock or 0
        new_warehouse_actual = current_actual + adjustment

        if new_warehouse_actual < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Điều chỉnh khiến tồn kho của '{p_name}' tại {warehouse_name} bị âm ({new_warehouse_actual}). Không thể thực hiện."
            )

        # Cảnh báo xung đột hàng đang giữ chỗ nếu new_warehouse_actual < reserved_stock
        reserved = stock_rec.reserved_stock or 0
        is_reserved_conflict = new_warehouse_actual < reserved

        # Cập nhật tồn kho của kho
        stock_rec.actual_stock = new_warehouse_actual
        stock_rec.updated_at = now_dt

        # Cập nhật tồn kho tổng của ProductEntity
        prod_entity = (
            db.query(ProductEntity)
            .filter(ProductEntity.id == pid)
            .with_for_update()
            .first()
        )
        if prod_entity:
            prev_prod_stock = prod_entity.stock or 0
            new_prod_stock = max(0, prev_prod_stock + adjustment)
            prod_entity.stock = new_prod_stock

            raw_p = _find_product(pid)
            if raw_p:
                raw_p["stock"] = new_prod_stock

        # Ghi nhận biến động kho nếu có chênh lệch
        if adjustment != 0:
            tx_reason = f"Phiếu kiểm kê {audit.code}: {reason}"
            if is_reserved_conflict:
                tx_reason += f" [CẢNH BÁO: Tồn thực tế {new_warehouse_actual} nhỏ hơn tồn giữ chỗ {reserved}]"

            # Lưu vào Database
            db_tx = InventoryTransactionEntity(
                product_id=pid,
                product_name=p_name,
                type="adjust",
                quantity=adjustment,
                previous_stock=current_actual,
                new_stock=new_warehouse_actual,
                unit_name=base_unit,
                conversion_rate=1.0,
                base_quantity=adjustment,
                performed_by=confirmer_name,
                user_role=current_user.role,
                reason=tx_reason,
                created_at=now_dt,
            )
            db.add(db_tx)

            # In-memory transaction
            mem_tx = InventoryTransaction(
                id=len(INVENTORY_TRANSACTIONS) + 1,
                product_id=pid,
                product_name=p_name,
                type="adjust",
                quantity=adjustment,
                previous_stock=current_actual,
                new_stock=new_warehouse_actual,
                unit_name=base_unit,
                conversion_rate=1.0,
                base_quantity=adjustment,
                performed_by=confirmer_name,
                user_role=current_user.role,
                reason=tx_reason,
                created_at=now_str,
            )
            INVENTORY_TRANSACTIONS.insert(0, mem_tx)

            # Audit log
            log_audit_event(
                db=db,
                user=current_user,
                action_type="INVENTORY_ADJUST",
                entity_type="StockAudit",
                entity_id=audit.code,
                old_val={
                    "product_code": p_code,
                    "warehouse_id": warehouse_id,
                    "stock": current_actual,
                    "frozen_stock": system_frozen,
                },
                new_val={
                    "product_code": p_code,
                    "warehouse_id": warehouse_id,
                    "stock": new_warehouse_actual,
                    "adjustment": adjustment,
                    "counted_stock": actual_counted,
                },
                reason=reason,
                request=request,
            )

        adjustments_log.append({
            "product_id": pid,
            "product_code": p_code,
            "adjustment": adjustment,
            "previous_warehouse_stock": current_actual,
            "new_warehouse_stock": new_warehouse_actual,
        })

    # Cập nhật trạng thái phiếu kiểm kê sang Hoàn tất
    audit.status = "CONFIRMED"
    audit.confirmed_by = confirmer_name
    audit.confirmed_at = now_dt
    audit.updated_at = now_dt
    audit.items_json = json.dumps(items, ensure_ascii=False)
    audit.discrepancy_items_count = discrepancy_count
    audit.total_discrepancy_qty = total_disc_qty
    if data and data.note:
        audit.note = data.note

    db.commit()
    db.refresh(audit)

    return _format_audit_response(audit, db)


@router.post("/{audit_id}/cancel", response_model=StockAuditResponse)
def cancel_stock_audit(
    audit_id: int,
    data: Optional[StockAuditCancel] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_warehouse_manager),
):
    """
    Hủy phiếu kiểm kê khi phiếu chưa hoàn tất.
    Chỉ Quản lý kho và Admin được phép hủy.
    """
    audit = db.query(StockAuditEntity).filter(StockAuditEntity.id == audit_id).first()
    if not audit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu kiểm kê có ID {audit_id}."
        )

    if audit.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu kiểm kê '{audit.code}' đã hoàn tất, không thể hủy."
        )

    if audit.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu kiểm kê '{audit.code}' đã ở trạng thái hủy trước đó."
        )

    audit.status = "CANCELLED"
    audit.cancelled_by = current_user.full_name or current_user.username
    audit.cancelled_at = get_utc_now()
    audit.updated_at = get_utc_now()
    if data and data.reason:
        audit.note = f"{audit.note or ''} [Lý do hủy: {data.reason}]".strip()

    db.commit()
    db.refresh(audit)

    return _format_audit_response(audit, db)
