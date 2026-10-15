import json
import time
from datetime import timezone
from datetime import datetime
import math
from typing import List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from app.api.deps import require_permission, require_sales_role, require_roles
from app.core.database import get_db
from app.core.rbac import Permission, Role
from app.schemas.auth import UserResponse
from app.schemas.inventory import (
    StockAdjustRequest,
    StockReceiptRequest,
    StockIssueRequest,
    StockUpdateRequest,
    InventoryResponse,
    InventoryTransaction,
    AvailableStockResponse,
    DealerStockSummaryResponse,
    WarehouseInfo,
    WarehouseThresholdItem,
    WarehouseThresholdPageResponse,
    UpdateThresholdRequest,
    LowStockAlertSummary,
    StockBookItem,
    StockBookSummary,
    StockBookResponse,
)
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.models.entities import ProductEntity, WarehouseStockEntity
from app.services.audit_service import log_audit_event
from app.services.inventory_availability_service import (
    get_available_stock_info,
    get_dealer_stock_summary,
    DEFAULT_WAREHOUSES,
    sync_missing_warehouse_stocks,
)

router = APIRouter()

# In-memory transactions log
INVENTORY_TRANSACTIONS: List[InventoryTransaction] = []



def _find_product(product_id: int):
    for p in RAW_PRODUCTS:
        if p["id"] == product_id:
            return p
    return None


@router.get("/transactions", response_model=List[InventoryTransaction])
def get_inventory_transactions(
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value))
):
    """
    Xem lịch sử biến động kho.
    Yêu cầu quyền 'inventory:read'.
    """
    return INVENTORY_TRANSACTIONS


@router.get("/available-stock", response_model=AvailableStockResponse)
def get_available_stock_endpoint(
    dealer_id: int,
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_sales_role)
):
    """
    SCRUM-56 / AC 1, AC 2, AC 5:
    Lấy thông tin tồn khả dụng của sản phẩm tại kho phục vụ riêng cho đại lý.
    Chỉ cho phép Nhân viên kinh doanh (Role: sales, sales_manager, admin).
    """
    return get_available_stock_info(db, dealer_id, product_id)


@router.get("/dealer-stock-summary/{dealer_id}", response_model=DealerStockSummaryResponse)
def get_dealer_stock_summary_endpoint(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_sales_role)
):
    """
    SCRUM-56 / AC 1, AC 2, AC 5:
    Lấy danh sách tồn khả dụng của tất cả sản phẩm tại kho phục vụ riêng cho đại lý.
    Chỉ cho phép Nhân viên kinh doanh (Role: sales, sales_manager, admin).
    """
    return get_dealer_stock_summary(db, dealer_id)



def _resolve_unit_and_rate(product: dict, prod_entity, unit_name: Optional[str], conversion_rate: Optional[float]):
    base_unit = "Cái"
    units_list = []
    if product:
        base_unit = product.get("base_unit", "Cái")
        units_list = product.get("units", [])
    elif prod_entity:
        base_unit = prod_entity.base_unit or "Cái"
        units_list = prod_entity.units or []

    chosen_unit = unit_name or base_unit
    chosen_rate = conversion_rate

    if chosen_rate is None or chosen_rate <= 0:
        if chosen_unit == base_unit:
            chosen_rate = 1.0
        else:
            matched = next((u for u in units_list if u.get("unit_name") == chosen_unit), None)
            chosen_rate = float(matched.get("conversion_rate", 1.0)) if matched else 1.0

    return chosen_unit, chosen_rate, base_unit


@router.post("/adjust", response_model=InventoryResponse)
def adjust_stock(
    data: StockAdjustRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Điều chỉnh tồn kho (Kiểm kê, cân đối kho).
    AC 4: CHẶN TUYỆT ĐỐI NHÂN VIÊN KINH DOANH (Sales).
    Nếu Sales gửi request -> require_permission bắn lỗi 403 Forbidden ngay tại Backend.
    """
    from app.models.entities import ProductEntity, InventoryTransactionEntity

    product = _find_product(data.product_id)
    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == data.product_id).first() if db else None

    if not product and not prod_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {data.product_id}."
        )

    prod_status = product.get("status") if product else (prod_entity.status if prod_entity else "active")
    prod_code = product["code"] if product else prod_entity.code
    if prod_status == "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sản phẩm '{prod_code}' đã ngừng kinh doanh, không thể thao tác kho."
        )

    chosen_unit, chosen_rate, base_unit = _resolve_unit_and_rate(product, prod_entity, data.unit_name, data.conversion_rate)
    base_adjustment = int(round(data.adjustment * chosen_rate))

    if abs(data.adjustment) > 10_000_000 or abs(base_adjustment) > 100_000_000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số lượng điều chỉnh quá lớn (tối đa 10.000.000 đơn vị). Vui lòng nhập số lượng hợp lý."
        )

    previous_stock = product["stock"] if product else prod_entity.stock
    new_stock = previous_stock + base_adjustment
    if new_stock < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Số lượng điều chỉnh khiến tồn kho âm ({new_stock}). Không thể thực hiện."
        )

    if product:
        product["stock"] = new_stock
    if prod_entity:
        prod_entity.stock = new_stock

    prod_name = product["name"] if product else prod_entity.name
    prod_code = product["code"] if product else prod_entity.code

    tx = InventoryTransaction(
        id=len(INVENTORY_TRANSACTIONS) + 1,
        product_id=data.product_id,
        product_name=prod_name,
        type="adjust",
        quantity=data.adjustment,
        previous_stock=previous_stock,
        new_stock=new_stock,
        unit_name=chosen_unit,
        conversion_rate=chosen_rate,
        base_quantity=base_adjustment,
        performed_by=current_user.username,
        user_role=current_user.role,
        reason=data.reason,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    INVENTORY_TRANSACTIONS.insert(0, tx)

    # Lưu vào database nếu có session
    if db:
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == data.product_id
        ).first()
        if ws:
            ws.actual_stock = max(0, (ws.actual_stock or 0) + int(base_adjustment))
            if ws.quantity_on_hand is not None:
                ws.quantity_on_hand = max(0, ws.quantity_on_hand + int(base_adjustment))
            else:
                ws.quantity_on_hand = ws.actual_stock
            ws.updated_at = datetime.now(timezone.utc)

        db_tx = InventoryTransactionEntity(
            product_id=data.product_id,
            product_name=prod_name,
            type="adjust",
            quantity=data.adjustment,
            previous_stock=previous_stock,
            new_stock=new_stock,
            unit_name=chosen_unit,
            conversion_rate=chosen_rate,
            base_quantity=base_adjustment,
            performed_by=current_user.username,
            user_role=current_user.role,
            reason=data.reason,
        )
        db.add(db_tx)
        db.commit()

    # Ghi nhật ký thao tác kiểm kê / điều chỉnh tồn kho (đầy đủ các trường quy đổi trước và sau)
    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVENTORY_ADJUST",
        entity_type="Product",
        entity_id=prod_code,
        old_val={"stock": previous_stock, "unit_name": base_unit, "conversion_rate": 1.0, "base_quantity": 0},
        new_val={"stock": new_stock, "unit_name": chosen_unit, "conversion_rate": chosen_rate, "base_quantity": base_adjustment},
        reason=data.reason,
        request=request,
    )

    return InventoryResponse(
        status="success",
        message=f"Đã điều chỉnh tồn kho sản phẩm '{prod_name}' thành công từ {previous_stock} sang {new_stock}.",
        product_id=data.product_id,
        current_stock=new_stock,
        transaction=tx,
    )


@router.post("/receipt", response_model=InventoryResponse)
def create_stock_receipt(
    data: StockReceiptRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Lập phiếu nhập kho hàng hóa.
    AC 4: Yêu cầu quyền 'inventory:write'. Sales không có quyền -> 403 Forbidden.
    """
    from app.models.entities import ProductEntity, InventoryTransactionEntity

    product = _find_product(data.product_id)
    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == data.product_id).first() if db else None

    if not product and not prod_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {data.product_id}."
        )

    prod_status = product.get("status") if product else (prod_entity.status if prod_entity else "active")
    prod_code = product["code"] if product else prod_entity.code
    if prod_status == "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sản phẩm '{prod_code}' đã ngừng kinh doanh, không thể thao tác kho."
        )

    chosen_unit, chosen_rate, base_unit = _resolve_unit_and_rate(product, prod_entity, data.unit_name, data.conversion_rate)
    base_qty = int(round(data.quantity * chosen_rate))

    if abs(data.quantity) > 10_000_000 or abs(base_qty) > 100_000_000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số lượng nhập kho quá lớn (tối đa 10.000.000 đơn vị). Vui lòng nhập số lượng hợp lý."
        )

    previous_stock = product["stock"] if product else prod_entity.stock
    new_stock = previous_stock + base_qty

    if product:
        product["stock"] = new_stock
    if prod_entity:
        prod_entity.stock = new_stock

    prod_name = product["name"] if product else prod_entity.name
    prod_code = product["code"] if product else prod_entity.code

    unit_info_str = f" ({data.quantity} {chosen_unit} = {base_qty} {base_unit})" if chosen_unit != base_unit else ""
    reason_str = f"Nhập kho từ NCC: {data.supplier}. {data.note or ''}{unit_info_str}".strip()

    tx = InventoryTransaction(
        id=len(INVENTORY_TRANSACTIONS) + 1,
        product_id=data.product_id,
        product_name=prod_name,
        type="receipt",
        quantity=data.quantity,
        previous_stock=previous_stock,
        new_stock=new_stock,
        unit_name=chosen_unit,
        conversion_rate=chosen_rate,
        base_quantity=base_qty,
        performed_by=current_user.username,
        user_role=current_user.role,
        reason=reason_str,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    INVENTORY_TRANSACTIONS.insert(0, tx)

    if db:
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == data.product_id
        ).first()
        if ws:
            ws.actual_stock = (ws.actual_stock or 0) + int(base_qty)
            if ws.quantity_on_hand is not None:
                ws.quantity_on_hand += int(base_qty)
            else:
                ws.quantity_on_hand = ws.actual_stock
            ws.updated_at = datetime.now(timezone.utc)

        db_tx = InventoryTransactionEntity(
            product_id=data.product_id,
            product_name=prod_name,
            type="receipt",
            quantity=data.quantity,
            previous_stock=previous_stock,
            new_stock=new_stock,
            unit_name=chosen_unit,
            conversion_rate=chosen_rate,
            base_quantity=base_qty,
            performed_by=current_user.username,
            user_role=current_user.role,
            reason=reason_str,
        )
        db.add(db_tx)
        db.commit()

    # Ghi nhật ký nhập kho (đầy đủ các trường quy đổi trước và sau)
    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVENTORY_ADJUST",
        entity_type="Product",
        entity_id=prod_code,
        old_val={"stock": previous_stock, "unit_name": base_unit, "conversion_rate": 1.0, "base_quantity": 0},
        new_val={"stock": new_stock, "unit_name": chosen_unit, "conversion_rate": chosen_rate, "base_quantity": base_qty},
        reason=reason_str,
        request=request,
    )

    return InventoryResponse(
        status="success",
        message=f"Nhập kho thành công {data.quantity} {chosen_unit} ({base_qty} {base_unit}) sản phẩm '{prod_name}'. Tồn kho hiện tại: {new_stock} {base_unit}.",
        product_id=data.product_id,
        current_stock=new_stock,
        transaction=tx,
    )


@router.post("/issue", response_model=InventoryResponse)
def create_stock_issue(
    data: StockIssueRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Lập phiếu xuất kho hàng hóa.
    AC 4: Yêu cầu quyền 'inventory:write'. Sales không có quyền -> 403 Forbidden.
    """
    from app.models.entities import ProductEntity, InventoryTransactionEntity

    product = _find_product(data.product_id)
    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == data.product_id).first() if db else None

    if not product and not prod_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {data.product_id}."
        )

    prod_status = product.get("status") if product else (prod_entity.status if prod_entity else "active")
    prod_code = product["code"] if product else prod_entity.code
    if prod_status == "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sản phẩm '{prod_code}' đã ngừng kinh doanh, không thể thao tác kho."
        )

    chosen_unit, chosen_rate, base_unit = _resolve_unit_and_rate(product, prod_entity, data.unit_name, data.conversion_rate)
    base_qty = int(round(data.quantity * chosen_rate))

    if abs(data.quantity) > 10_000_000 or abs(base_qty) > 100_000_000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số lượng xuất kho quá lớn (tối đa 10.000.000 đơn vị). Vui lòng nhập số lượng hợp lý."
        )

    previous_stock = product["stock"] if product else prod_entity.stock
    if previous_stock < base_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không đủ tồn kho để xuất. Tồn hiện tại: {previous_stock} {base_unit}, yêu cầu xuất: {data.quantity} {chosen_unit} (= {base_qty} {base_unit})."
        )

    new_stock = previous_stock - base_qty
    if product:
        product["stock"] = new_stock
    if prod_entity:
        prod_entity.stock = new_stock

    prod_name = product["name"] if product else prod_entity.name
    prod_code = product["code"] if product else prod_entity.code

    unit_info_str = f" ({data.quantity} {chosen_unit} = {base_qty} {base_unit})" if chosen_unit != base_unit else ""
    reason_str = f"Xuất kho tới: {data.destination}. {data.note or ''}{unit_info_str}".strip()

    tx = InventoryTransaction(
        id=len(INVENTORY_TRANSACTIONS) + 1,
        product_id=data.product_id,
        product_name=prod_name,
        type="issue",
        quantity=-data.quantity,
        previous_stock=previous_stock,
        new_stock=new_stock,
        unit_name=chosen_unit,
        conversion_rate=chosen_rate,
        base_quantity=-base_qty,
        performed_by=current_user.username,
        user_role=current_user.role,
        reason=reason_str,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    INVENTORY_TRANSACTIONS.insert(0, tx)

    if db:
        # SCRUM-81: Xử lý trừ tồn lô và kiểm tra lý do đổi lô nếu sản phẩm quản lý theo lô
        from app.models.goods_receipt import WarehouseEntity
        from app.services.batch_suggestion_service import (
            resolve_warehouse_id_int,
            validate_batch_change_reason,
            execute_batch_deduction,
            check_warehouse_user_permission,
        )

        target_wh_id_int = resolve_warehouse_id_int(db, data.warehouse_id or "WH01")
        wh_entity = db.query(WarehouseEntity).filter(WarehouseEntity.id == target_wh_id_int).first() if target_wh_id_int else None
        check_warehouse_user_permission(current_user, wh_entity)

        is_batch_managed = bool(prod_entity.is_batch_managed) if prod_entity else False
        if is_batch_managed or data.selected_batch_id:
            # Tiêu chí 2: Validate lý do đổi lô nếu chọn lô khác với lô gợi ý
            clean_reason = validate_batch_change_reason(
                selected_batch_id=data.selected_batch_id,
                suggested_batch_id=data.suggested_batch_id,
                batch_change_reason=data.batch_change_reason,
            )
            # Tiêu chí 3: Mở lock và trừ đúng tồn lô đã chọn trong DB transaction
            execute_batch_deduction(
                db=db,
                product=prod_entity,
                warehouse_id=target_wh_id_int or 1,
                base_qty=base_qty,
                selected_batch_id=data.selected_batch_id,
                suggested_batch_id=data.suggested_batch_id,
                batch_change_reason=clean_reason,
                reference_code=f"XK-{int(time.time())}",
                performed_by=current_user.username,
                note=reason_str,
            )

        target_wh_code = wh_entity.code if wh_entity else (str(data.warehouse_id) if data.warehouse_id else "WH01")
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == target_wh_code,
            WarehouseStockEntity.product_id == data.product_id
        ).first()
        if ws:
            ws.actual_stock = max(0, (ws.actual_stock or 0) - int(base_qty))
            if ws.quantity_on_hand is not None:
                ws.quantity_on_hand = max(0, ws.quantity_on_hand - int(base_qty))
            else:
                ws.quantity_on_hand = ws.actual_stock
            ws.updated_at = datetime.now(timezone.utc)

        db_tx = InventoryTransactionEntity(
            product_id=data.product_id,
            product_name=prod_name,
            type="issue",
            quantity=-data.quantity,
            previous_stock=previous_stock,
            new_stock=new_stock,
            unit_name=chosen_unit,
            conversion_rate=chosen_rate,
            base_quantity=-base_qty,
            performed_by=current_user.username,
            user_role=current_user.role,
            reason=reason_str,
        )
        db.add(db_tx)
        db.commit()

    # Ghi nhật ký xuất kho (đầy đủ các trường quy đổi trước và sau)
    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVENTORY_ADJUST",
        entity_type="Product",
        entity_id=prod_code,
        old_val={"stock": previous_stock, "unit_name": base_unit, "conversion_rate": 1.0, "base_quantity": 0},
        new_val={"stock": new_stock, "unit_name": chosen_unit, "conversion_rate": chosen_rate, "base_quantity": -base_qty},
        reason=reason_str,
        request=request,
    )

    return InventoryResponse(
        status="success",
        message=f"Xuất kho thành công {data.quantity} {chosen_unit} ({base_qty} {base_unit}) sản phẩm '{prod_name}'. Tồn kho còn lại: {new_stock} {base_unit}.",
        product_id=data.product_id,
        current_stock=new_stock,
        transaction=tx,
    )


@router.put("/{product_id}/stock", response_model=InventoryResponse)
def update_stock_quantity(
    product_id: int,
    data: StockUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Cập nhật trực tiếp số lượng tồn kho (PUT).
    AC 4: Chặn các thao tác PUT đối với Nhân viên kinh doanh -> 403 Forbidden.
    """
    product = _find_product(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {product_id}."
        )

    previous_stock = product["stock"]
    product["stock"] = data.new_stock

    tx = InventoryTransaction(
        id=len(INVENTORY_TRANSACTIONS) + 1,
        product_id=product["id"],
        product_name=product["name"],
        type="stock_count",
        quantity=data.new_stock - previous_stock,
        previous_stock=previous_stock,
        new_stock=data.new_stock,
        performed_by=current_user.username,
        user_role=current_user.role,
        reason=f"Cập nhật trực tiếp tồn kho: {data.reason}",
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    INVENTORY_TRANSACTIONS.insert(0, tx)

    # Ghi nhật ký kiểm kê trực tiếp
    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVENTORY_ADJUST",
        entity_type="Product",
        entity_id=product["code"],
        old_val={"stock": previous_stock},
        new_val={"stock": data.new_stock},
        reason=data.reason,
        request=request,
    )

    return InventoryResponse(
        status="success",
        message=f"Đã cập nhật số lượng tồn kho sản phẩm '{product['name']}' thành {data.new_stock}.",
        product_id=product["id"],
        current_stock=data.new_stock,
        transaction=tx,
    )


@router.delete("/{product_id}/stock", response_model=InventoryResponse)
def clear_stock_quantity(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Xóa/Reset tồn kho sản phẩm về 0 (DELETE).
    AC 4: Chặn các thao tác DELETE đối với Nhân viên kinh doanh -> 403 Forbidden.
    """
    product = _find_product(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {product_id}."
        )

    previous_stock = product["stock"]
    product["stock"] = 0

    tx = InventoryTransaction(
        id=len(INVENTORY_TRANSACTIONS) + 1,
        product_id=product["id"],
        product_name=product["name"],
        type="adjust",
        quantity=-previous_stock,
        previous_stock=previous_stock,
        new_stock=0,
        performed_by=current_user.username,
        user_role=current_user.role,
        reason="Thao tác DELETE: Đặt lại tồn kho về 0",
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    INVENTORY_TRANSACTIONS.insert(0, tx)

    # Ghi nhật ký reset kho
    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVENTORY_ADJUST",
        entity_type="Product",
        entity_id=product["code"],
        old_val={"stock": previous_stock},
        new_val={"stock": 0},
        reason="Thao tác DELETE: Đặt lại tồn kho về 0",
        request=request,
    )

    return InventoryResponse(
        status="success",
        message=f"Đã reset tồn kho của '{product['name']}' về 0.",
        product_id=product["id"],
        current_stock=0,
        transaction=tx,
    )


@router.get("/warehouses", response_model=List[WarehouseInfo])
def get_warehouses_list():
    """Lấy danh sách các kho hoạt động trong hệ thống."""
    return [
        WarehouseInfo(warehouse_id=w_id, warehouse_name=w_name)
        for w_id, w_name in DEFAULT_WAREHOUSES.items()
    ]


# Caches phục vụ tốc độ đọc cấu hình định mức tồn (<5ms)
_THRESHOLDS_CACHE: dict = {}
_LAST_SYNC_THRESHOLDS: float = 0.0


@router.get("/thresholds", response_model=Union[WarehouseThresholdPageResponse, List[WarehouseThresholdItem]])
def get_inventory_thresholds(
    warehouse_id: Optional[str] = None,
    search: Optional[str] = None,
    filter_mode: Optional[str] = None,
    page: Optional[int] = None,
    page_size: int = 20,
    only_low_stock: Optional[bool] = Query(False),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Khai báo & Xem định mức tồn tối thiểu theo SKU và theo kho.
    Tối ưu hóa Server-side SQL + Bộ nhớ đệm (Cache) giúp chuyển trang tức thì (<5ms) ngay cả với 26.000 bản ghi.
    """
    global _LAST_SYNC_THRESHOLDS, _THRESHOLDS_CACHE
    now = time.time()

    # 1. Đồng bộ bản ghi kho định kỳ (tối đa 1 lần mỗi 5 phút)
    if db and (now - _LAST_SYNC_THRESHOLDS > 300):
        try:
            sync_missing_warehouse_stocks(db)
            _LAST_SYNC_THRESHOLDS = now
        except Exception as sync_ex:
            print(f"Warning syncing warehouse stocks: {sync_ex}")

    wh_filter = (warehouse_id or "ALL").strip()
    search_term = (search or "").strip()
    f_mode = (filter_mode or ("LOW_ONLY" if only_low_stock else "ALL")).strip()

    if db:
        effective_min = func.coalesce(WarehouseStockEntity.min_stock, ProductEntity.min_stock, 10)

        base_query = (
            db.query(
                WarehouseStockEntity.warehouse_id,
                WarehouseStockEntity.warehouse_name,
                ProductEntity.id.label("product_id"),
                ProductEntity.code.label("product_code"),
                ProductEntity.name.label("product_name"),
                ProductEntity.category,
                ProductEntity.base_unit,
                WarehouseStockEntity.actual_stock,
                WarehouseStockEntity.reserved_stock,
                effective_min.label("min_stock"),
            )
            .join(ProductEntity, WarehouseStockEntity.product_id == ProductEntity.id)
            .filter(ProductEntity.status != "inactive")
        )

        # Lọc theo kho
        if wh_filter != "ALL" and wh_filter != "all":
            base_query = base_query.filter(WarehouseStockEntity.warehouse_id == wh_filter)
        else:
            base_query = base_query.filter(WarehouseStockEntity.warehouse_id.in_(list(DEFAULT_WAREHOUSES.keys())))

        # Lọc theo trạng thái
        if f_mode == "LOW_ONLY":
            base_query = base_query.filter(WarehouseStockEntity.actual_stock <= effective_min)

        # Lọc theo từ khóa tìm kiếm
        if search_term:
            search_pat = f"%{search_term}%"
            base_query = base_query.filter(
                or_(
                    ProductEntity.code.ilike(search_pat),
                    ProductEntity.name.ilike(search_pat),
                    WarehouseStockEntity.warehouse_name.ilike(search_pat),
                )
            )

        # 2. Cache thống kê KPI tổng quan (60 giây)
        cache_key = f"{wh_filter}_{f_mode}_{search_term}"
        cached_stats = _THRESHOLDS_CACHE.get(cache_key)

        if cached_stats and (now - cached_stats["timestamp"] < 60):
            total_filtered, total_count, low_count, safe_count, active_warehouses = cached_stats["data"]
        else:
            total_filtered = base_query.count()

            # Thống kê trên toàn bộ kho hiện tại
            scope_query = (
                db.query(WarehouseStockEntity.warehouse_id, WarehouseStockEntity.actual_stock, effective_min)
                .join(ProductEntity, WarehouseStockEntity.product_id == ProductEntity.id)
                .filter(ProductEntity.status != "inactive")
            )
            if wh_filter != "ALL" and wh_filter != "all":
                scope_query = scope_query.filter(WarehouseStockEntity.warehouse_id == wh_filter)
            else:
                scope_query = scope_query.filter(WarehouseStockEntity.warehouse_id.in_(list(DEFAULT_WAREHOUSES.keys())))

            total_count = scope_query.count()
            low_count = scope_query.filter(WarehouseStockEntity.actual_stock <= effective_min).count()
            safe_count = max(0, total_count - low_count)
            active_warehouses = (
                1 if (wh_filter != "ALL" and wh_filter != "all")
                else len(DEFAULT_WAREHOUSES)
            )

            _THRESHOLDS_CACHE[cache_key] = {
                "timestamp": now,
                "data": (total_filtered, total_count, low_count, safe_count, active_warehouses),
            }

        # 3. Phân trang Server-side SQL (chỉ lấy đúng 20 bản ghi!)
        if page is not None:
            p = max(1, page)
            ps = max(1, min(page_size, 100))
            total_pages = max(1, math.ceil(total_filtered / ps)) if total_filtered > 0 else 1
            paged_rows = (
                base_query
                .order_by(ProductEntity.code.asc(), WarehouseStockEntity.warehouse_id.asc())
                .offset((p - 1) * ps)
                .limit(ps)
                .all()
            )
            items = [
                WarehouseThresholdItem(
                    warehouse_id=r.warehouse_id,
                    warehouse_name=r.warehouse_name,
                    product_id=r.product_id,
                    product_code=r.product_code,
                    product_name=r.product_name,
                    category=r.category or "Thời trang",
                    base_unit=r.base_unit or "Cái",
                    actual_stock=r.actual_stock or 0,
                    reserved_stock=r.reserved_stock or 0,
                    available_stock=max(0, (r.actual_stock or 0) - (r.reserved_stock or 0)),
                    min_stock=r.min_stock or 10,
                    is_low_stock=((r.actual_stock or 0) <= (r.min_stock or 10)),
                    shortage_quantity=max(0, (r.min_stock or 10) - (r.actual_stock or 0)),
                )
                for r in paged_rows
            ]

            return WarehouseThresholdPageResponse(
                items=items,
                total=total_filtered,
                page=p,
                page_size=ps,
                total_pages=total_pages,
                total_count=total_count,
                low_count=low_count,
                safe_count=safe_count,
                active_warehouses=active_warehouses,
            )

        # Nếu không truyền page: trả về List[WarehouseThresholdItem]
        rows = (
            base_query
            .order_by(ProductEntity.code.asc(), WarehouseStockEntity.warehouse_id.asc())
            .all()
        )
        return [
            WarehouseThresholdItem(
                warehouse_id=r.warehouse_id,
                warehouse_name=r.warehouse_name,
                product_id=r.product_id,
                product_code=r.product_code,
                product_name=r.product_name,
                category=r.category or "Thời trang",
                base_unit=r.base_unit or "Cái",
                actual_stock=r.actual_stock or 0,
                reserved_stock=r.reserved_stock or 0,
                available_stock=max(0, (r.actual_stock or 0) - (r.reserved_stock or 0)),
                min_stock=r.min_stock or 10,
                is_low_stock=((r.actual_stock or 0) <= (r.min_stock or 10)),
                shortage_quantity=max(0, (r.min_stock or 10) - (r.actual_stock or 0)),
            )
            for r in rows
        ]


@router.put("/thresholds", response_model=WarehouseThresholdItem)
def update_inventory_threshold(
    data: UpdateThresholdRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.WAREHOUSE_MANAGER.value])),
):
    """
    Cập nhật định mức tồn tối thiểu (min_stock) cho sản phẩm theo kho.
    Ghi vết vào bảng audit_logs với action_type='MIN_STOCK_UPDATE'.
    """
    if data.warehouse_id not in DEFAULT_WAREHOUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã kho '{data.warehouse_id}' không hợp lệ trong hệ thống."
        )

    w_name = DEFAULT_WAREHOUSES[data.warehouse_id]

    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == data.product_id).first() if db else None
    raw_p = _find_product(data.product_id)
    if not prod_entity and not raw_p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {data.product_id}."
        )

    prod_code = prod_entity.code if prod_entity else raw_p["code"]
    prod_name = prod_entity.name if prod_entity else raw_p["name"]
    prod_cat = (prod_entity.category if prod_entity else raw_p.get("category")) or "Thời trang"
    base_unit = (prod_entity.base_unit if prod_entity else raw_p.get("base_unit")) or "Cái"

    ws = None
    old_min_stock = 10
    if db:
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == data.warehouse_id,
            WarehouseStockEntity.product_id == data.product_id
        ).first()

        if not ws:
            base_stk = prod_entity.stock if prod_entity else raw_p.get("stock", 100)
            if data.warehouse_id == "WH01":
                actual = max(10, base_stk)
            elif data.warehouse_id == "WH02":
                actual = max(10, int(base_stk * 0.7))
            elif data.warehouse_id == "WH03":
                actual = max(5, int(base_stk * 0.5))
            else:
                actual = max(10, base_stk)
            ws = WarehouseStockEntity(
                warehouse_id=data.warehouse_id,
                warehouse_name=w_name,
                product_id=data.product_id,
                actual_stock=actual,
                reserved_stock=0,
                min_stock=data.min_stock,
            )
            db.add(ws)
        else:
            old_min_stock = ws.min_stock
            ws.min_stock = data.min_stock

        if prod_entity:
            prod_entity.min_stock = data.min_stock
        db.commit()
        _LOW_STOCK_CACHE.clear()
        _STOCK_BOOK_CACHE.clear()
        _THRESHOLDS_CACHE.clear()

    log_audit_event(
        db=db,
        user=current_user,
        action_type="MIN_STOCK_UPDATE",
        entity_type="WarehouseStock",
        entity_id=f"{data.warehouse_id}:{prod_code}",
        old_val={"warehouse_id": data.warehouse_id, "product_code": prod_code, "min_stock": old_min_stock},
        new_val={"warehouse_id": data.warehouse_id, "product_code": prod_code, "min_stock": data.min_stock},
        reason=f"Cập nhật định mức tồn tối thiểu kho {w_name} cho SP {prod_name}: {old_min_stock} -> {data.min_stock}",
        request=request,
    )

    actual = ws.actual_stock if ws else (prod_entity.stock if prod_entity else raw_p.get("stock", 0))
    reserved = ws.reserved_stock if ws else 0
    avail = max(0, actual - reserved)

    return WarehouseThresholdItem(
        warehouse_id=data.warehouse_id,
        warehouse_name=w_name,
        product_id=data.product_id,
        product_code=prod_code,
        product_name=prod_name,
        category=prod_cat,
        base_unit=base_unit,
        actual_stock=actual,
        reserved_stock=reserved,
        available_stock=avail,
        min_stock=data.min_stock,
        is_low_stock=(actual <= data.min_stock),
        shortage_quantity=max(0, data.min_stock - actual),
    )


# Caches phục vụ tốc độ đọc cảnh báo tồn kho và sổ kho tức thì (<5ms)
_LOW_STOCK_CACHE: dict = {}
_LAST_SYNC_LOW_STOCK: float = 0.0


@router.get("/low-stock-alerts", response_model=LowStockAlertSummary)
def get_low_stock_alerts_summary(
    page: Optional[int] = Query(None, ge=1, description="Số trang (bắt đầu từ 1)"),
    page_size: Optional[int] = Query(None, ge=1, le=100, description="Số lượng bản ghi mỗi trang (mặc định 20 nếu có page)"),
    warehouse_id: Optional[str] = Query(None, description="Lọc theo mã kho"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã SKU hoặc tên sản phẩm"),
    severity: Optional[str] = Query(None, description="Mức độ khẩn: OUT_OF_STOCK hoặc NEAR_DEPLETED"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Lấy tổng hợp cảnh báo hàng dưới định mức tồn tối thiểu để hiển thị nổi bật trên Dashboard kho.
    Tối ưu hóa Server-side SQL + Bộ nhớ đệm (Cache) giúp chuyển trang tức thì (<5ms) ngay cả với hàng chục ngàn sản phẩm.
    """
    global _LAST_SYNC_LOW_STOCK, _LOW_STOCK_CACHE
    now = time.time()

    # 1. Đồng bộ bản ghi kho định kỳ (tối đa 1 lần mỗi 5 phút)
    if db and (now - _LAST_SYNC_LOW_STOCK > 300):
        try:
            sync_missing_warehouse_stocks(db)
            _LAST_SYNC_LOW_STOCK = now
        except Exception as sync_ex:
            print(f"Warning syncing warehouse stocks: {sync_ex}")

    actual_page_size = page_size if page_size and page_size > 0 else 20
    search_term = (search or "").strip()
    wh_filter = (warehouse_id or "ALL").strip()
    sev_filter = (severity or "ALL").strip()

    if db:
        effective_min = func.coalesce(WarehouseStockEntity.min_stock, ProductEntity.min_stock, 10)

        # Base query cho các bản ghi bị dưới định mức tồn
        base_query = (
            db.query(
                WarehouseStockEntity.warehouse_id,
                WarehouseStockEntity.warehouse_name,
                ProductEntity.id.label("product_id"),
                ProductEntity.code.label("product_code"),
                ProductEntity.name.label("product_name"),
                ProductEntity.category,
                ProductEntity.base_unit,
                WarehouseStockEntity.actual_stock,
                WarehouseStockEntity.reserved_stock,
                effective_min.label("min_stock"),
            )
            .join(ProductEntity, WarehouseStockEntity.product_id == ProductEntity.id)
            .filter(ProductEntity.status != "inactive")
            .filter(WarehouseStockEntity.actual_stock <= effective_min)
        )

        # Lọc theo kho
        if wh_filter != "ALL":
            base_query = base_query.filter(WarehouseStockEntity.warehouse_id == wh_filter)
        else:
            base_query = base_query.filter(WarehouseStockEntity.warehouse_id.in_(list(DEFAULT_WAREHOUSES.keys())))

        # Lọc theo mức độ khẩn
        if sev_filter == "OUT_OF_STOCK":
            base_query = base_query.filter(WarehouseStockEntity.actual_stock <= 0)
        elif sev_filter == "NEAR_DEPLETED":
            base_query = base_query.filter(WarehouseStockEntity.actual_stock > 0)

        # Lọc theo tìm kiếm
        if search_term:
            search_pat = f"%{search_term}%"
            base_query = base_query.filter(
                or_(
                    ProductEntity.code.ilike(search_pat),
                    ProductEntity.name.ilike(search_pat),
                    WarehouseStockEntity.warehouse_name.ilike(search_pat),
                )
            )

        # 2. Cache thống kê KPI tổng quan (60 giây)
        cache_key = f"{wh_filter}_{sev_filter}_{search_term}"
        cached_stats = _LOW_STOCK_CACHE.get(cache_key)

        if cached_stats and (now - cached_stats["timestamp"] < 60):
            total_filtered, total_low_stock, total_prods, out_of_stock_count, near_depleted_count, wh_stats = cached_stats["data"]
        else:
            total_filtered = base_query.count()

            # Thống kê tổng thể toàn hệ thống
            all_low_query = (
                db.query(WarehouseStockEntity.warehouse_id, ProductEntity.id)
                .join(ProductEntity, WarehouseStockEntity.product_id == ProductEntity.id)
                .filter(ProductEntity.status != "inactive")
                .filter(WarehouseStockEntity.warehouse_id.in_(list(DEFAULT_WAREHOUSES.keys())))
                .filter(WarehouseStockEntity.actual_stock <= effective_min)
            )
            total_low_stock = all_low_query.with_entities(func.count(func.distinct(ProductEntity.id))).scalar() or 0
            total_prods = db.query(func.count(ProductEntity.id)).filter(ProductEntity.status != "inactive").scalar() or 0
            out_of_stock_count = all_low_query.filter(WarehouseStockEntity.actual_stock <= 0).count()
            total_all_alerts = all_low_query.count()
            near_depleted_count = max(0, total_all_alerts - out_of_stock_count)

            # Thống kê theo từng kho
            wh_counts = (
                all_low_query.with_entities(
                    WarehouseStockEntity.warehouse_id,
                    func.count(WarehouseStockEntity.id),
                )
                .group_by(WarehouseStockEntity.warehouse_id)
                .all()
            )
            wh_map = {wid: cnt for wid, cnt in wh_counts}
            wh_stats = [
                {
                    "warehouse_id": wid,
                    "warehouse_name": wname,
                    "low_stock_count": wh_map.get(wid, 0),
                }
                for wid, wname in DEFAULT_WAREHOUSES.items()
            ]

            _LOW_STOCK_CACHE[cache_key] = {
                "timestamp": now,
                "data": (total_filtered, total_low_stock, total_prods, out_of_stock_count, near_depleted_count, wh_stats),
            }

        # 3. Phân trang Server-side SQL (chỉ lấy đúng 20 bản ghi!)
        if page is not None:
            total_pages = max(1, math.ceil(total_filtered / actual_page_size))
            paged_rows = (
                base_query
                .order_by(ProductEntity.code.asc(), WarehouseStockEntity.warehouse_id.asc())
                .offset((page - 1) * actual_page_size)
                .limit(actual_page_size)
                .all()
            )
            items = [
                WarehouseThresholdItem(
                    warehouse_id=r.warehouse_id,
                    warehouse_name=r.warehouse_name,
                    product_id=r.product_id,
                    product_code=r.product_code,
                    product_name=r.product_name,
                    category=r.category or "Thời trang",
                    base_unit=r.base_unit or "Cái",
                    actual_stock=r.actual_stock or 0,
                    reserved_stock=r.reserved_stock or 0,
                    available_stock=max(0, (r.actual_stock or 0) - (r.reserved_stock or 0)),
                    min_stock=r.min_stock or 10,
                    is_low_stock=True,
                    shortage_quantity=max(0, (r.min_stock or 10) - (r.actual_stock or 0)),
                )
                for r in paged_rows
            ]

            return LowStockAlertSummary(
                total_low_stock=total_low_stock,
                total_alerts=total_filtered,
                total_products=total_prods,
                out_of_stock_count=out_of_stock_count,
                near_depleted_count=near_depleted_count,
                warehouses=wh_stats,
                items=items,
                page=page,
                page_size=actual_page_size,
                total_pages=total_pages,
            )

        # Nếu không truyền page (lấy tất cả)
        rows = (
            base_query
            .order_by(ProductEntity.code.asc(), WarehouseStockEntity.warehouse_id.asc())
            .all()
        )
        items = [
            WarehouseThresholdItem(
                warehouse_id=r.warehouse_id,
                warehouse_name=r.warehouse_name,
                product_id=r.product_id,
                product_code=r.product_code,
                product_name=r.product_name,
                category=r.category or "Thời trang",
                base_unit=r.base_unit or "Cái",
                actual_stock=r.actual_stock or 0,
                reserved_stock=r.reserved_stock or 0,
                available_stock=max(0, (r.actual_stock or 0) - (r.reserved_stock or 0)),
                min_stock=r.min_stock or 10,
                is_low_stock=True,
                shortage_quantity=max(0, (r.min_stock or 10) - (r.actual_stock or 0)),
            )
            for r in rows
        ]
        return LowStockAlertSummary(
            total_low_stock=total_low_stock,
            total_alerts=len(items),
            total_products=total_prods,
            out_of_stock_count=out_of_stock_count,
            near_depleted_count=near_depleted_count,
            warehouses=wh_stats,
            items=items,
        )

    # Fallback nếu không có DB
    threshold_items = get_inventory_thresholds(warehouse_id=None, db=db, current_user=current_user)
    low_stock_items = [it for it in threshold_items if it.is_low_stock]
    unique_pids = set(it.product_id for it in low_stock_items)
    return LowStockAlertSummary(
        total_low_stock=len(unique_pids),
        total_alerts=len(low_stock_items),
        total_products=len(threshold_items),
        warehouses=[],
        items=low_stock_items,
    )


# Caches phục vụ tốc độ đọc sổ tồn kho tức thì (<5ms)
_STOCK_BOOK_CACHE: dict = {}
_CATEGORIES_CACHE: dict = {"timestamp": 0.0, "data": []}
_LAST_SYNC_WAREHOUSE_STOCKS: float = 0.0


@router.get("/stock-book", response_model=StockBookResponse)
def get_stock_book(
    warehouse_id: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: Optional[str] = None,
    search: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Sổ tồn kho chi tiết theo từng SKU và từng kho.
    Hỗ trợ nhân viên kho tra cứu nhanh 3 chỉ số then chốt:
    - Tồn thực tế (actual_stock)
    - Tồn đang giữ chỗ (reserved_stock)
    - Tồn khả dụng bán được (available_stock = actual_stock - reserved_stock)
    Bộ lọc: Kho, Nhóm hàng/Ngành hàng, Trạng thái tồn (Tất cả, Còn hàng bán, Hết hàng bán, Dưới định mức), Tìm kiếm SKU/Tên SP.
    """
    global _LAST_SYNC_WAREHOUSE_STOCKS, _CATEGORIES_CACHE, _STOCK_BOOK_CACHE
    now = time.time()

    warehouses_info = [
        WarehouseInfo(warehouse_id=wid, warehouse_name=wname)
        for wid, wname in DEFAULT_WAREHOUSES.items()
    ]

    # 1. Đồng bộ bản ghi kho (chạy tối đa 1 lần mỗi 5 phút để tránh nghẽn truy vấn khi người dùng lướt chuyển trang)
    if db and (now - _LAST_SYNC_WAREHOUSE_STOCKS > 300):
        try:
            sync_missing_warehouse_stocks(db)
            _LAST_SYNC_WAREHOUSE_STOCKS = now
        except Exception as sync_ex:
            print(f"Warning syncing warehouse stocks: {sync_ex}")

    # 2. Xây dựng truy vấn cơ sở nối giữa bảng WarehouseStockEntity và ProductEntity (Server-side SQL)
    if db:
        base_query = (
            db.query(
                WarehouseStockEntity.warehouse_id,
                WarehouseStockEntity.warehouse_name,
                ProductEntity.id.label("product_id"),
                ProductEntity.code.label("product_code"),
                ProductEntity.name.label("product_name"),
                ProductEntity.category,
                ProductEntity.base_unit,
                WarehouseStockEntity.actual_stock,
                WarehouseStockEntity.reserved_stock,
                WarehouseStockEntity.min_stock,
                ProductEntity.min_stock.label("prod_min_stock"),
            )
            .join(ProductEntity, WarehouseStockEntity.product_id == ProductEntity.id)
            .filter(ProductEntity.status != "inactive")
        )

        # Lọc theo kho
        if warehouse_id and warehouse_id != "all":
            base_query = base_query.filter(WarehouseStockEntity.warehouse_id == warehouse_id)
        else:
            base_query = base_query.filter(WarehouseStockEntity.warehouse_id.in_(list(DEFAULT_WAREHOUSES.keys())))

        # Lọc theo ngành hàng / danh mục
        if category and category != "all":
            base_query = base_query.filter(ProductEntity.category == category)

        # Lọc tìm kiếm theo mã SKU hoặc tên sản phẩm
        search_term = (search or "").strip()
        if search_term:
            search_pattern = f"%{search_term}%"
            base_query = base_query.filter(
                or_(
                    ProductEntity.code.ilike(search_pattern),
                    ProductEntity.name.ilike(search_pattern),
                )
            )

        # Lọc theo trạng thái tồn kho
        if stock_status and stock_status != "all":
            avail_expr = func.coalesce(WarehouseStockEntity.actual_stock, 0) - func.coalesce(WarehouseStockEntity.reserved_stock, 0)
            effective_min = func.coalesce(WarehouseStockEntity.min_stock, ProductEntity.min_stock, 10)
            if stock_status == "available":
                base_query = base_query.filter(avail_expr > 0)
            elif stock_status == "out_of_stock":
                base_query = base_query.filter(avail_expr <= 0)
            elif stock_status == "low_stock":
                base_query = base_query.filter(WarehouseStockEntity.actual_stock <= effective_min)

        # 3. Tính toán tổng hợp (Summary) có Cache ngắn hạn (60s) theo bộ lọc
        cache_key = f"{warehouse_id or 'all'}_{category or 'all'}_{stock_status or 'all'}_{search_term}"
        cached_summary = _STOCK_BOOK_CACHE.get(cache_key)

        if cached_summary and (now - cached_summary["timestamp"] < 60):
            sum_actual, sum_reserved, sum_available, total_count = cached_summary["data"]
        else:
            summary_res = base_query.with_entities(
                func.coalesce(func.sum(WarehouseStockEntity.actual_stock), 0),
                func.coalesce(func.sum(WarehouseStockEntity.reserved_stock), 0),
                func.count(WarehouseStockEntity.id),
            ).first()

            sum_actual = int(summary_res[0]) if summary_res else 0
            sum_reserved = int(summary_res[1]) if summary_res else 0
            sum_available = max(0, sum_actual - sum_reserved)
            total_count = int(summary_res[2]) if summary_res else 0
            _STOCK_BOOK_CACHE[cache_key] = {
                "timestamp": now,
                "data": (sum_actual, sum_reserved, sum_available, total_count),
            }

        # 4. Danh mục ngành hàng có Cache (5 phút)
        if now - _CATEGORIES_CACHE["timestamp"] < 300 and _CATEGORIES_CACHE["data"]:
            all_categories = _CATEGORIES_CACHE["data"]
        else:
            all_categories = [
                c[0] for c in db.query(ProductEntity.category)
                .distinct()
                .filter(ProductEntity.category != None)
                .order_by(ProductEntity.category)
                .all()
            ]
            _CATEGORIES_CACHE = {"timestamp": now, "data": all_categories}

        # 5. Phân trang SQL trực tiếp (LIMIT và OFFSET cực nhanh qua Index)
        ordered_query = base_query.order_by(ProductEntity.code.asc(), WarehouseStockEntity.warehouse_id.asc())
        if page is not None and page_size is not None and page > 0 and page_size > 0:
            paginated_rows = ordered_query.offset((page - 1) * page_size).limit(page_size).all()
        else:
            paginated_rows = ordered_query.all()

        # 5. Khởi tạo danh sách items (chỉ đúng số bản ghi của trang đó)
        items: List[StockBookItem] = []
        for r in paginated_rows:
            act = r.actual_stock or 0
            res = r.reserved_stock or 0
            avail = max(0, act - res)
            min_stk = r.min_stock if r.min_stock is not None else (r.prod_min_stock if r.prod_min_stock is not None else 10)
            is_low = act <= min_stk

            if avail <= 0:
                status_label = "out_of_stock"
            elif is_low:
                status_label = "low_stock"
            else:
                status_label = "available"

            items.append(StockBookItem(
                warehouse_id=r.warehouse_id,
                warehouse_name=r.warehouse_name,
                product_id=r.product_id,
                product_code=r.product_code,
                product_name=r.product_name,
                category=r.category or "Thời trang",
                base_unit=r.base_unit or "Cái",
                actual_stock=act,
                reserved_stock=res,
                available_stock=avail,
                min_stock=min_stk,
                is_low_stock=is_low,
                status=status_label,
            ))

        summary = StockBookSummary(
            total_actual_stock=sum_actual,
            total_reserved_stock=sum_reserved,
            total_available_stock=sum_available,
            total_skus=total_count,
        )

        return StockBookResponse(
            summary=summary,
            items=items,
            warehouses=warehouses_info,
            categories=all_categories,
            total=total_count,
            page=page,
            page_size=page_size,
        )

    # Fallback khi không có DB
    return StockBookResponse(
        summary=StockBookSummary(
            total_actual_stock=0,
            total_reserved_stock=0,
            total_available_stock=0,
            total_skus=0,
        ),
        items=[],
        warehouses=warehouses_info,
        categories=[],
        total=0,
        page=page,
        page_size=page_size,
    )


# ==========================================
# QUẢN LÝ CHUYỂN KHO NỘI BỘ (STOCK TRANSFERS)
# ==========================================

from app.models.entities import StockTransferEntity
from app.schemas.stock_transfer import (
    StockTransferCreateRequest,
    StockTransferReceiveRequest,
    StockTransferResponse,
    StockTransferItemDetail,
)


def _format_stock_transfer_response(transfer: StockTransferEntity) -> StockTransferResponse:
    items_list = []
    if transfer.items_json:
        try:
            raw_items = json.loads(transfer.items_json)
            for it in raw_items:
                items_list.append(StockTransferItemDetail(**it))
        except Exception:
            pass
    return StockTransferResponse(
        id=transfer.id,
        code=transfer.code,
        from_warehouse_id=transfer.from_warehouse_id,
        from_warehouse_name=transfer.from_warehouse_name,
        to_warehouse_id=transfer.to_warehouse_id,
        to_warehouse_name=transfer.to_warehouse_name,
        status=transfer.status,
        note=transfer.note,
        total_items=transfer.total_items or len(items_list),
        total_transfer_qty=transfer.total_transfer_qty or 0,
        total_received_qty=transfer.total_received_qty or 0,
        discrepancy_qty=transfer.discrepancy_qty or 0,
        discrepancy_reason=transfer.discrepancy_reason,
        items=items_list,
        created_by=transfer.created_by,
        dispatched_by=transfer.dispatched_by,
        dispatched_at=transfer.dispatched_at,
        received_by=transfer.received_by,
        received_at=transfer.received_at,
        cancelled_by=transfer.cancelled_by,
        cancelled_at=transfer.cancelled_at,
        created_at=transfer.created_at,
        updated_at=transfer.updated_at,
    )


@router.post("/transfers", response_model=StockTransferResponse)
def create_stock_transfer(
    data: StockTransferCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Lập phiếu chuyển kho nội bộ và điều chuyển hàng từ kho xuất.
    Ràng buộc:
    - Kho gửi và kho nhận phải khác nhau.
    - Cả 2 kho phải tồn tại trong hệ thống.
    - Kiểm tra tồn khả dụng tại kho gửi (available_stock >= transfer_qty).
    - Hàng đang chuyển được ghi nhận là IN_TRANSIT, trừ tồn kho đi, CHƯA cộng vào kho đến.
    """
    if data.from_warehouse_id == data.to_warehouse_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kho chuyển đi và kho tiếp nhận không được trùng nhau."
        )

    if data.from_warehouse_id not in DEFAULT_WAREHOUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã kho đi '{data.from_warehouse_id}' không hợp lệ."
        )

    if data.to_warehouse_id not in DEFAULT_WAREHOUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã kho đến '{data.to_warehouse_id}' không hợp lệ."
        )

    from_name = DEFAULT_WAREHOUSES[data.from_warehouse_id]
    to_name = DEFAULT_WAREHOUSES[data.to_warehouse_id]

    items_detail = []
    total_transfer_qty = 0

    # Kiểm tra từng mặt hàng
    for item in data.items:
        prod_entity = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).first() if db else None
        raw_p = _find_product(item.product_id)
        if not prod_entity and not raw_p:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy sản phẩm có ID {item.product_id}."
            )
        prod_code = prod_entity.code if prod_entity else raw_p["code"]
        prod_name = prod_entity.name if prod_entity else raw_p["name"]
        prod_cat = prod_entity.category if prod_entity else raw_p.get("category", "")
        base_unit = prod_entity.base_unit if prod_entity else raw_p.get("base_unit", "Cái")

        # Kiểm tra tồn kho đi
        ws_from = None
        if db:
            ws_from = db.query(WarehouseStockEntity).filter(
                WarehouseStockEntity.warehouse_id == data.from_warehouse_id,
                WarehouseStockEntity.product_id == item.product_id
            ).first()

        avail_stock = ws_from.available_stock if ws_from else (prod_entity.stock if prod_entity else raw_p.get("stock", 0))
        if avail_stock < item.transfer_qty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{prod_name}' ({prod_code}) tại kho '{from_name}' chỉ còn {avail_stock} tồn khả dụng, không đủ để chuyển {item.transfer_qty}."
            )

        # Trừ tồn thực tế tại kho đi
        if db:
            if not ws_from:
                base_stk = prod_entity.stock if prod_entity else raw_p.get("stock", 100)
                ws_from = WarehouseStockEntity(
                    warehouse_id=data.from_warehouse_id,
                    warehouse_name=from_name,
                    product_id=item.product_id,
                    actual_stock=base_stk,
                    reserved_stock=0,
                    min_stock=10,
                )
                db.add(ws_from)
            ws_from.actual_stock = max(0, ws_from.actual_stock - item.transfer_qty)

        total_transfer_qty += item.transfer_qty
        items_detail.append({
            "product_id": item.product_id,
            "product_code": prod_code,
            "product_name": prod_name,
            "category": prod_cat,
            "unit": base_unit,
            "transfer_qty": item.transfer_qty,
            "received_qty": None,
            "discrepancy_qty": 0,
            "reason": None,
        })

    # Sinh mã phiếu
    date_str = datetime.now().strftime("%Y%m%d")
    count_today = 0
    if db:
        count_today = db.query(StockTransferEntity).filter(
            StockTransferEntity.code.like(f"CK-{date_str}-%")
        ).count()
    transfer_code = f"CK-{date_str}-{(count_today + 1):03d}"

    now = datetime.now(timezone.utc)
    transfer = StockTransferEntity(
        code=transfer_code,
        from_warehouse_id=data.from_warehouse_id,
        from_warehouse_name=from_name,
        to_warehouse_id=data.to_warehouse_id,
        to_warehouse_name=to_name,
        status="IN_TRANSIT",
        note=data.note,
        total_items=len(items_detail),
        total_transfer_qty=total_transfer_qty,
        total_received_qty=0,
        discrepancy_qty=0,
        discrepancy_reason=None,
        items_json=json.dumps(items_detail, ensure_ascii=False),
        created_by=current_user.username,
        dispatched_by=current_user.username,
        dispatched_at=now,
        created_at=now,
        updated_at=now,
    )

    if db:
        db.add(transfer)
        db.commit()
        db.refresh(transfer)

    # Ghi Audit Log
    log_audit_event(
        db=db,
        user=current_user,
        action_type="STOCK_TRANSFER_DISPATCH",
        entity_type="StockTransfer",
        entity_id=transfer_code,
        old_val={"from": from_name, "to": to_name, "status": "DRAFT"},
        new_val={"status": "IN_TRANSIT", "items": items_detail, "total_transfer_qty": total_transfer_qty},
        reason=f"Xuất chuyển kho nội bộ từ {from_name} sang {to_name}",
        request=request,
    )

    return _format_stock_transfer_response(transfer)


@router.get("/transfers", response_model=List[StockTransferResponse])
def get_stock_transfers(
    status_filter: Optional[str] = Query(None, alias="status"),
    warehouse_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Lấy danh sách phiếu chuyển kho nội bộ.
    """
    if not db:
        return []
    query = db.query(StockTransferEntity)
    if status_filter:
        query = query.filter(StockTransferEntity.status == status_filter.upper())
    if warehouse_id:
        query = query.filter(
            (StockTransferEntity.from_warehouse_id == warehouse_id) |
            (StockTransferEntity.to_warehouse_id == warehouse_id)
        )
    transfers = query.order_by(StockTransferEntity.id.desc()).all()
    return [_format_stock_transfer_response(t) for t in transfers]


@router.get("/transfers/{code}", response_model=StockTransferResponse)
def get_stock_transfer_detail(
    code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Xem chi tiết phiếu chuyển kho nội bộ.
    """
    transfer = db.query(StockTransferEntity).filter(StockTransferEntity.code == code).first() if db else None
    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu chuyển kho có mã '{code}'."
        )
    return _format_stock_transfer_response(transfer)


@router.post("/transfers/{code}/receive", response_model=StockTransferResponse)
def receive_stock_transfer(
    code: str,
    data: StockTransferReceiveRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Thủ kho đến xác nhận nhận hàng chuyển kho.
    Ràng buộc:
    - Phiếu phải ở trạng thái IN_TRANSIT.
    - Kho đến xác nhận nhận đủ thì tồn mới được cộng vào kho đến.
    - Nếu có chênh lệch (thực nhận < chuyển đi), bắt buộc phải có lý do chênh lệch.
    """
    transfer = db.query(StockTransferEntity).filter(StockTransferEntity.code == code).first() if db else None
    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu chuyển kho '{code}'."
        )

    if transfer.status != "IN_TRANSIT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu chuyển kho '{code}' đang ở trạng thái '{transfer.status}', không thể tiếp nhận."
        )

    items_list = json.loads(transfer.items_json) if transfer.items_json else []
    receive_map = {item.product_id: item for item in data.items}

    total_received_qty = 0
    total_discrepancy = 0
    updated_items = []

    for item in items_list:
        pid = item["product_id"]
        rx_item = receive_map.get(pid)
        if not rx_item:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Thiếu thông tin số lượng thực nhận cho sản phẩm '{item['product_name']}'."
            )

        if rx_item.received_qty < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Số lượng nhận cho sản phẩm '{item['product_name']}' không được âm."
            )

        transfer_qty = item["transfer_qty"]
        received_qty = rx_item.received_qty
        discrepancy = transfer_qty - received_qty

        if discrepancy != 0:
            total_discrepancy += abs(discrepancy)

        # Cộng tồn vào kho đến đúng bằng số lượng thực nhận
        if db:
            ws_to = db.query(WarehouseStockEntity).filter(
                WarehouseStockEntity.warehouse_id == transfer.to_warehouse_id,
                WarehouseStockEntity.product_id == pid
            ).first()
            if not ws_to:
                ws_to = WarehouseStockEntity(
                    warehouse_id=transfer.to_warehouse_id,
                    warehouse_name=transfer.to_warehouse_name,
                    product_id=pid,
                    actual_stock=received_qty,
                    reserved_stock=0,
                    min_stock=10,
                )
                db.add(ws_to)
            else:
                ws_to.actual_stock += received_qty

        total_received_qty += received_qty
        item["received_qty"] = received_qty
        item["discrepancy_qty"] = discrepancy
        item["reason"] = rx_item.reason
        updated_items.append(item)

    # RÀNG BUỘC NGHIỆP VỤ: Nếu có chênh lệch, BẮT BUỘC phải nhập lý do!
    if total_discrepancy > 0:
        if not data.discrepancy_reason or not data.discrepancy_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Phát hiện chênh lệch {total_discrepancy} sản phẩm khi nhận hàng. Bắt buộc phải nhập lý do chênh lệch."
            )

    now = datetime.now(timezone.utc)
    transfer.status = "COMPLETED"
    transfer.total_received_qty = total_received_qty
    transfer.discrepancy_qty = total_discrepancy
    transfer.discrepancy_reason = data.discrepancy_reason.strip() if data.discrepancy_reason else None
    transfer.received_by = current_user.username
    transfer.received_at = now
    transfer.items_json = json.dumps(updated_items, ensure_ascii=False)
    transfer.updated_at = now

    if db:
        db.commit()
        db.refresh(transfer)

    # Ghi audit log
    log_audit_event(
        db=db,
        user=current_user,
        action_type="STOCK_TRANSFER_RECEIVE",
        entity_type="StockTransfer",
        entity_id=code,
        old_val={"status": "IN_TRANSIT"},
        new_val={
            "status": "COMPLETED",
            "total_received_qty": total_received_qty,
            "discrepancy_qty": total_discrepancy,
            "discrepancy_reason": transfer.discrepancy_reason
        },
        reason=f"Kho {transfer.to_warehouse_name} xác nhận nhập hàng từ phiếu {code}" + (f" (Chênh lệch: {transfer.discrepancy_reason})" if total_discrepancy > 0 else ""),
        request=request,
    )

    return _format_stock_transfer_response(transfer)


@router.post("/transfers/{code}/cancel", response_model=StockTransferResponse)
def cancel_stock_transfer(
    code: str,
    request: Request,
    cancel_reason: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value)),
):
    """
    Hủy phiếu chuyển kho nội bộ khi hàng chưa được tiếp nhận.
    Hoàn trả lại số lượng đã xuất cho kho đi.
    """
    transfer = db.query(StockTransferEntity).filter(StockTransferEntity.code == code).first() if db else None
    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu chuyển kho '{code}'."
        )

    if transfer.status != "IN_TRANSIT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu chuyển kho '{code}' đang ở trạng thái '{transfer.status}', không thể hủy."
        )

    # Hoàn trả tồn kho đi
    items_list = json.loads(transfer.items_json) if transfer.items_json else []
    if db:
        for it in items_list:
            ws_from = db.query(WarehouseStockEntity).filter(
                WarehouseStockEntity.warehouse_id == transfer.from_warehouse_id,
                WarehouseStockEntity.product_id == it["product_id"]
            ).first()
            if ws_from:
                ws_from.actual_stock += it["transfer_qty"]

    now = datetime.now(timezone.utc)
    transfer.status = "CANCELLED"
    transfer.cancelled_by = current_user.username
    transfer.cancelled_at = now
    transfer.updated_at = now
    if cancel_reason:
        transfer.note = ((transfer.note or "") + f" [Hủy: {cancel_reason}]").strip()

    if db:
        db.commit()
        db.refresh(transfer)

    log_audit_event(
        db=db,
        user=current_user,
        action_type="STOCK_TRANSFER_CANCEL",
        entity_type="StockTransfer",
        entity_id=code,
        old_val={"status": "IN_TRANSIT"},
        new_val={"status": "CANCELLED"},
        reason=f"Hủy phiếu chuyển kho {code} và hoàn trả hàng cho {transfer.from_warehouse_name}",
        request=request,
    )

    return _format_stock_transfer_response(transfer)


@router.get("/batches/suggest")
def suggest_batches_for_product(
    product_id: int = Query(..., description="ID sản phẩm (SKU)"),
    warehouse_id: Optional[str] = Query("WH01", description="Mã kho hoặc ID kho"),
    quantity: float = Query(1.0, description="Số lượng cần xuất"),
    conversion_rate: float = Query(1.0, description="Hệ số quy đổi sang đơn vị cơ sở"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    SCRUM-81: API truy vấn trực tiếp từ CSDL gợi ý lô theo FEFO (hạn dùng gần nhất xếp trước).
    Trả về:
    - suggested_batch: Lô đầu tiên có HSD gần nhất còn đủ số lượng.
    - available_batches: Toàn bộ danh sách lô còn hạn và còn tồn khả dụng của SKU tại kho.
    """
    from app.services.batch_suggestion_service import resolve_warehouse_id_int, get_fefo_batch_suggestions
    wh_id_int = resolve_warehouse_id_int(db, warehouse_id) or 1
    base_qty = float(quantity) * float(conversion_rate or 1.0)

    suggested, avail_list = get_fefo_batch_suggestions(
        db=db,
        product_id=product_id,
        warehouse_id=wh_id_int,
        required_base_qty=base_qty,
    )

    return {
        "product_id": product_id,
        "warehouse_id": wh_id_int,
        "required_base_qty": base_qty,
        "suggested_batch": suggested,
        "available_batches": avail_list,
    }



