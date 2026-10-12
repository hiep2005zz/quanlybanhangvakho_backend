import json
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
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
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
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


