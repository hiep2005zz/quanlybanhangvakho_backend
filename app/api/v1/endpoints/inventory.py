# backend/app/api/v1/endpoints/inventory.py
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import Permission
from app.schemas.auth import UserResponse
from app.schemas.inventory import (
    StockAdjustRequest,
    StockReceiptRequest,
    StockIssueRequest,
    StockUpdateRequest,
    InventoryResponse,
    InventoryTransaction,
)
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.services.audit_service import log_audit_event

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

