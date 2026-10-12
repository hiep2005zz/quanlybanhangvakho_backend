# backend/app/api/v1/endpoints/inventory.py
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
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


@router.get("/thresholds", response_model=List[WarehouseThresholdItem])
def get_inventory_thresholds(
    warehouse_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Khai báo & Xem định mức tồn tối thiểu theo SKU và theo kho.
    Hỗ trợ lọc theo từng kho hoặc lấy tất cả các kho.
    """
    products_query = db.query(ProductEntity).all() if db else []
    seen_ids = set()
    all_prods = []
    for p in products_query:
        all_prods.append({
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "category": p.category or "Thời trang",
            "base_unit": p.base_unit or "Cái",
            "stock": p.stock or 0,
            "min_stock": p.min_stock if p.min_stock is not None else 10,
        })
        seen_ids.add(p.id)

    for rp in RAW_PRODUCTS:
        if rp["id"] not in seen_ids and rp.get("status") != "inactive":
            all_prods.append({
                "id": rp["id"],
                "code": rp["code"],
                "name": rp["name"],
                "category": rp.get("category", "Thời trang"),
                "base_unit": rp.get("base_unit", "Cái"),
                "stock": rp.get("stock", 0),
                "min_stock": 10,
            })
            seen_ids.add(rp["id"])

    warehouses_to_query = (
        {warehouse_id: DEFAULT_WAREHOUSES[warehouse_id]}
        if warehouse_id and warehouse_id in DEFAULT_WAREHOUSES
        else DEFAULT_WAREHOUSES
    )

    results: List[WarehouseThresholdItem] = []

    for w_id, w_name in warehouses_to_query.items():
        ws_records = (
            {ws.product_id: ws for ws in db.query(WarehouseStockEntity).filter(WarehouseStockEntity.warehouse_id == w_id).all()}
            if db else {}
        )

        for prod in all_prods:
            pid = prod["id"]
            ws = ws_records.get(pid)
            if ws:
                if w_id == "WH01" and (prod["stock"] or 0) > (ws.actual_stock or 0):
                    ws.actual_stock = prod["stock"]
                    try:
                        db.commit()
                    except Exception:
                        pass
                actual = ws.actual_stock or 0
                reserved = ws.reserved_stock or 0
                min_stk = ws.min_stock if ws.min_stock is not None else prod["min_stock"]
            else:
                base_stk = prod["stock"]
                if w_id == "WH01":
                    actual = max(10, base_stk)
                elif w_id == "WH02":
                    actual = max(10, int(base_stk * 0.7))
                elif w_id == "WH03":
                    actual = max(5, int(base_stk * 0.5))
                else:
                    actual = max(10, base_stk)
                reserved = 0
                min_stk = prod["min_stock"]

            avail = max(0, actual - reserved)
            is_low = actual <= min_stk
            shortage = max(0, min_stk - actual)

            results.append(WarehouseThresholdItem(
                warehouse_id=w_id,
                warehouse_name=w_name,
                product_id=pid,
                product_code=prod["code"],
                product_name=prod["name"],
                category=prod["category"],
                base_unit=prod["base_unit"],
                actual_stock=actual,
                reserved_stock=reserved,
                available_stock=avail,
                min_stock=min_stk,
                is_low_stock=is_low,
                shortage_quantity=shortage,
            ))

    return results


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


@router.get("/low-stock-alerts", response_model=LowStockAlertSummary)
def get_low_stock_alerts_summary(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_READ.value)),
):
    """
    Lấy tổng hợp cảnh báo hàng dưới định mức tồn tối thiểu để hiển thị nổi bật trên Dashboard kho.
    """
    threshold_items = get_inventory_thresholds(warehouse_id=None, db=db, current_user=current_user)
    low_stock_items = [it for it in threshold_items if it.is_low_stock]

    wh_stats = []
    for w_id, w_name in DEFAULT_WAREHOUSES.items():
        wh_items = [it for it in low_stock_items if it.warehouse_id == w_id]
        wh_stats.append({
            "warehouse_id": w_id,
            "warehouse_name": w_name,
            "low_stock_count": len(wh_items),
        })

    return LowStockAlertSummary(
        total_low_stock=len(low_stock_items),
        total_products=len(threshold_items),
        warehouses=wh_stats,
        items=low_stock_items,
    )


@router.get("/stock-book", response_model=StockBookResponse)
def get_stock_book(
    warehouse_id: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: Optional[str] = None,
    search: Optional[str] = None,
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
    threshold_items = get_inventory_thresholds(warehouse_id=warehouse_id, db=db, current_user=current_user)

    # Thu thập danh mục phân loại duy nhất
    all_categories = sorted(list(set(it.category for it in threshold_items if it.category)))

    # Tạo danh sách kho
    warehouses_info = [
        WarehouseInfo(warehouse_id=wid, warehouse_name=wname)
        for wid, wname in DEFAULT_WAREHOUSES.items()
    ]

    items: List[StockBookItem] = []
    search_term = (search or "").strip().lower()

    for it in threshold_items:
        # Lọc kho nếu người dùng chọn cụ thể
        if warehouse_id and warehouse_id != "all" and it.warehouse_id != warehouse_id:
            continue

        # Lọc danh mục / ngành hàng
        if category and category != "all" and it.category != category:
            continue

        # Xác định trạng thái nghiệp vụ
        if it.available_stock <= 0:
            status_label = "out_of_stock"
        elif it.is_low_stock:
            status_label = "low_stock"
        else:
            status_label = "available"

        # Lọc theo trạng thái
        if stock_status and stock_status != "all":
            if stock_status == "available" and it.available_stock <= 0:
                continue
            elif stock_status == "out_of_stock" and it.available_stock > 0:
                continue
            elif stock_status == "low_stock" and not it.is_low_stock:
                continue

        # Lọc tìm kiếm theo mã sản phẩm hoặc tên sản phẩm
        if search_term:
            match_code = search_term in it.product_code.lower()
            match_name = search_term in it.product_name.lower()
            if not (match_code or match_name):
                continue

        items.append(StockBookItem(
            warehouse_id=it.warehouse_id,
            warehouse_name=it.warehouse_name,
            product_id=it.product_id,
            product_code=it.product_code,
            product_name=it.product_name,
            category=it.category,
            base_unit=it.base_unit,
            actual_stock=it.actual_stock,
            reserved_stock=it.reserved_stock,
            available_stock=it.available_stock,
            min_stock=it.min_stock,
            is_low_stock=it.is_low_stock,
            status=status_label,
        ))

    summary = StockBookSummary(
        total_actual_stock=sum(it.actual_stock for it in items),
        total_reserved_stock=sum(it.reserved_stock for it in items),
        total_available_stock=sum(it.available_stock for it in items),
        total_skus=len(items),
    )

    return StockBookResponse(
        summary=summary,
        items=items,
        warehouses=warehouses_info,
        categories=all_categories,
    )


