from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_permission
from app.core.database import get_db
from app.core.rbac import Permission, has_permission
from app.schemas.auth import UserResponse
from app.schemas.product import (
    ProductItem,
    ProductListResponse,
    ProductFinancialSummary,
    PriceUpdateRequest,
    UnitUpdateRequest,
    UnitConversionItem,
    ProductCreateRequest,
    ProductUpdateRequest,
)
from app.services.audit_service import log_audit_event

router = APIRouter()

# Mock Products database with base_unit and units
RAW_PRODUCTS = [
    {
        "id": 1,
        "code": "SP001",
        "name": "Áo thun Polo Nam Cao Cấp",
        "category": "Thời trang",
        "stock": 120,
        "cost_price": 85000.0,
        "sell_price": 199000.0,
        "base_unit": "Cái",
        "units": [
            {"unit_name": "Lốc", "conversion_rate": 6.0},
            {"unit_name": "Thùng", "conversion_rate": 24.0},
        ],
    },
    {
        "id": 2,
        "code": "SP002",
        "name": "Quần Jeans Slimfit Co Giãn",
        "category": "Thời trang",
        "stock": 45,
        "cost_price": 160000.0,
        "sell_price": 380000.0,
        "base_unit": "Chiếc",
        "units": [
            {"unit_name": "Kiện", "conversion_rate": 10.0},
        ],
    },
    {
        "id": 3,
        "code": "SP003",
        "name": "Áo khoác Bomber Chống Nước",
        "category": "Thời trang",
        "stock": 30,
        "cost_price": 220000.0,
        "sell_price": 490000.0,
        "base_unit": "Chiếc",
        "units": [],
    },
    {
        "id": 4,
        "code": "SP004",
        "name": "Giày Sneaker Thể Thao",
        "category": "Giày dép",
        "stock": 65,
        "cost_price": 310000.0,
        "sell_price": 650000.0,
        "base_unit": "Đôi",
        "units": [
            {"unit_name": "Thùng", "conversion_rate": 12.0},
        ],
    },
    {
        "id": 5,
        "code": "SP005",
        "name": "Thắt lưng da bò nguyên tấm",
        "category": "Phụ kiện",
        "stock": 80,
        "cost_price": 95000.0,
        "sell_price": 250000.0,
        "base_unit": "Chiếc",
        "units": [
            {"unit_name": "Hộp", "conversion_rate": 5.0},
        ],
    },
]


def _find_product_in_raw(product_id: int):
    for p in RAW_PRODUCTS:
        if p["id"] == product_id:
            return p
    return None

def _get_product_transaction_count(product_id: int, db: Session) -> int:
    from app.api.v1.endpoints.orders import ORDERS_DB
    from app.api.v1.endpoints.inventory import INVENTORY_TRANSACTIONS
    from app.models.entities import OrderEntity, InventoryTransactionEntity, ProductEntity
    import json

    prod_code = None
    for p in RAW_PRODUCTS:
        if p.get("id") == product_id:
            prod_code = p.get("code")
            break
    if not prod_code and db:
        try:
            prod_obj = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
            if prod_obj:
                prod_code = prod_obj.code
        except Exception:
            pass

    order_ids = set()

    # 1. In-memory orders
    for order_key, order in ORDERS_DB.items():
        items = order.get("items", [])
        for item in items:
            item_pid = item.get("product_id")
            item_code = item.get("product_code") or item.get("code")
            if (item_pid is not None and str(item_pid) == str(product_id)) or (prod_code and item_code and item_code.upper() == prod_code.upper()):
                order_ids.add(order.get("order_code") or f"mem_{order_key}")
                break

    # 2. Database orders (hỗ trợ cả JSON dạng list và JSON dạng dict có key 'items')
    if db:
        try:
            for o in db.query(OrderEntity).all():
                if o.items_json:
                    try:
                        data = json.loads(o.items_json)
                        items_list = data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) else [])
                        for item in items_list:
                            item_pid = item.get("product_id")
                            item_code = item.get("product_code") or item.get("code")
                            if (item_pid is not None and str(item_pid) == str(product_id)) or (prod_code and item_code and item_code.upper() == prod_code.upper()):
                                order_ids.add(o.order_code or f"db_{o.id}")
                                break
                    except Exception:
                        pass
        except Exception:
            pass

    # 3. Inventory transactions (nhập kho, xuất kho, điều chỉnh tồn kho)
    mem_tx_count = 0
    for tx in INVENTORY_TRANSACTIONS:
        tx_pid = getattr(tx, "product_id", None) or (tx.get("product_id") if isinstance(tx, dict) else None)
        if tx_pid is not None and str(tx_pid) == str(product_id):
            mem_tx_count += 1

    db_tx_count = 0
    if db:
        try:
            db_tx_count = db.query(InventoryTransactionEntity).filter(InventoryTransactionEntity.product_id == product_id).count()
        except Exception:
            pass

    inventory_count = max(mem_tx_count, db_tx_count)

    return len(order_ids) + inventory_count


@router.get("", response_model=ProductListResponse)
def get_products(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_READ.value))
):
    """
    Lấy danh sách sản phẩm.
    ÁP DỤNG AC 2 (Zero-Trust) & AC 3 (Bảo vệ dữ liệu nhạy cảm Giá vốn & Biên lợi nhuận):
    - Người dùng bắt buộc phải có quyền 'product:read'.
    - Dữ liệu 'cost_price' (giá vốn), 'profit_margin' (biên lợi nhuận), 'profit_per_unit' (lợi nhuận/đơn vị)
      CHỈ ĐƯỢC PHÉP TRẢ VỀ khi người dùng có quyền 'cost:read' (Vai trò: Quản lý kinh doanh hoặc Quản trị hệ thống).
    - Đối với Thủ kho, Nhân viên kinh doanh, Kế toán...: Server BÓC TÁCH & GỠ BỎ HOÀN TOÀN các trường này (None).
    """
    from app.models.entities import ProductEntity

    can_view_cost = current_user.can_view_cost or (Permission.COST_READ.value in current_user.permissions)
    
    # Đồng bộ từ DB nếu có
    db_products = db.query(ProductEntity).all()
    db_prod_map = {p.id: p for p in db_products}

    sanitized_items: list[ProductItem] = []
    total_stock = 0
    total_sell_val = 0.0
    total_cost_val = 0.0

    for p in RAW_PRODUCTS:
        db_p = db_prod_map.get(p["id"])
        base_unit = getattr(db_p, "base_unit", None) if db_p else None
        if not base_unit:
            base_unit = p.get("base_unit", "Cái")
        units_raw = getattr(db_p, "units", None) if db_p else None
        if not units_raw:
            units_raw = p.get("units", [])
        units_converted = [UnitConversionItem(unit_name=u["unit_name"], conversion_rate=float(u["conversion_rate"])) for u in units_raw]

        stock = p["stock"]
        sell_price = p["sell_price"]
        cost_price = p["cost_price"]

        total_stock += stock
        total_sell_val += sell_price * stock

        images = p.get("images") or []
        base_unit = p.get("base_unit") or "Cái"
        packaging_spec = p.get("packaging_specification")
        status_val = p.get("status") or "active"
        cat_name = db_p.category if (db_p and db_p.category) else p.get("category", "Chưa phân loại")
        cat_id = db_p.category_id if (db_p and db_p.category_id is not None) else p.get("category_id")
        p["category"] = cat_name
        p["category_id"] = cat_id

        if can_view_cost:
            profit_unit = sell_price - cost_price
            margin = round((profit_unit / sell_price) * 100, 2) if sell_price > 0 else 0.0
            total_cost_val += cost_price * stock
            sanitized_items.append(ProductItem(
                id=p["id"],
                code=p["code"],
                name=p["name"],
                category=cat_name,
                category_id=cat_id,
                stock=stock,
                sell_price=sell_price,
                base_unit=base_unit,
                units=units_converted,
                cost_price=cost_price,
                profit_margin=margin,
                profit_per_unit=profit_unit,
                images=images,
                packaging_specification=packaging_spec,
                status=status_val,
                transaction_count=_get_product_transaction_count(p["id"], db),
            ))
        else:
            # AC 3: Filter/strip bỏ hoàn toàn trường nhạy cảm trước khi gửi JSON về client
            sanitized_items.append(ProductItem(
                id=p["id"],
                code=p["code"],
                name=p["name"],
                category=cat_name,
                category_id=cat_id,
                stock=stock,
                sell_price=sell_price,
                base_unit=base_unit,
                units=units_converted,
                cost_price=None,
                profit_margin=None,
                profit_per_unit=None,
                images=images,
                packaging_specification=packaging_spec,
                status=status_val,
                transaction_count=_get_product_transaction_count(p["id"], db),
            ))
    if can_view_cost:
        gross_profit = total_sell_val - total_cost_val
        avg_margin = round((gross_profit / total_sell_val) * 100, 2) if total_sell_val > 0 else 0.0
        summary = ProductFinancialSummary(
            total_products=len(sanitized_items),
            total_stock=total_stock,
            total_sell_value=total_sell_val,
            total_cost_value=total_cost_val,
            total_gross_profit=gross_profit,
            average_margin_percent=avg_margin,
        )
    else:
        summary = ProductFinancialSummary(
            total_products=len(sanitized_items),
            total_stock=total_stock,
            total_sell_value=total_sell_val,
            total_cost_value=None,
            total_gross_profit=None,
            average_margin_percent=None,
        )

    return ProductListResponse(
        items=sanitized_items,
        total=len(sanitized_items),
        user_role=current_user.role,
        is_cost_price_visible=can_view_cost,
        summary=summary,
    )

from app.core.database import SessionLocal
from app.models.entities import CategoryEntity

@router.put("/{product_id}/stock")
@router.patch("/{product_id}/stock")
def update_product_stock(
    product_id: int,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.INVENTORY_WRITE.value))
):
    """
    Cập nhật số lượng tồn kho sản phẩm.
    """
    for p in RAW_PRODUCTS:
        if p["id"] == product_id:
            old_stock = p["stock"]
            if "stock" in payload:
                new_stock = int(payload["stock"])
                p["stock"] = new_stock
                log_audit_event(
                    db=db,
                    user=current_user,
                    action_type="INVENTORY_ADJUST",
                    entity_type="Product",
                    entity_id=p["code"],
                    old_val={"stock": old_stock},
                    new_val={"stock": new_stock},
                    reason=payload.get("reason", "Cập nhật tồn kho sản phẩm"),
                    request=request,
                )
            return {"status": "success", "message": "Cập nhật tồn kho thành công", "product": p}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy sản phẩm")


@router.put("/{product_id}", response_model=ProductItem)
@router.patch("/{product_id}", response_model=ProductItem)
def update_product_details(
    product_id: int,
    payload: ProductUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Cập nhật chi tiết sản phẩm từ Product Drawer (4 khối chức năng).
    """
    target = None
    for p in RAW_PRODUCTS:
        if p["id"] == product_id:
            target = p
            break

    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy sản phẩm")

    can_view_cost = current_user.can_view_cost or (Permission.COST_READ.value in current_user.permissions)
    old_data = dict(target)

    # Cập nhật các trường
    if payload.code is not None and payload.code.strip():
        clean_sku = payload.code.strip().upper()
        # Kiểm tra trùng SKU nếu đổi mã
        for other in RAW_PRODUCTS:
            if other["id"] != product_id and other["code"].upper() == clean_sku:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Mã SKU '{clean_sku}' đã được sản phẩm khác sử dụng."
                )
        target["code"] = clean_sku

    if payload.name is not None and payload.name.strip():
        target["name"] = payload.name.strip()

    if payload.category is not None:
        target["category"] = payload.category.strip()

    if payload.category_id is not None:
        target["category_id"] = payload.category_id

    if payload.base_unit is not None:
        target["base_unit"] = payload.base_unit

    if payload.packaging_specification is not None:
        target["packaging_specification"] = payload.packaging_specification

    if payload.sell_price is not None:
        target["sell_price"] = payload.sell_price

    if payload.cost_price is not None and can_view_cost:
        target["cost_price"] = payload.cost_price

    if payload.images is not None:
        target["images"] = payload.images

    if payload.status is not None:
        target["status"] = payload.status

    # Đồng bộ lưu vào SQLite
    try:
        from app.models.entities import ProductEntity
        db_prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
        if db_prod:
            db_prod.code = target["code"]
            db_prod.name = target["name"]
            db_prod.category = target["category"]
            if target.get("category_id"):
                db_prod.category_id = target["category_id"]
            db_prod.sell_price = target["sell_price"]
            if can_view_cost and target.get("cost_price") is not None:
                db_prod.cost_price = target["cost_price"]
            db.commit()
    except Exception as db_err:
        db.rollback()
        print(f"Warning: update product to DB: {db_err}")

    # Ghi audit log
    log_audit_event(
        db=db,
        user=current_user,
        action_type="PRODUCT_UPDATE",
        entity_type="Product",
        entity_id=target["code"],
        old_val=old_data,
        new_val=target,
        reason=f"Cập nhật thông tin sản phẩm {target['name']}",
        request=request,
    )

    cost = target.get("cost_price", 0.0)
    sell = target.get("sell_price", 0.0)
    profit_unit = sell - cost if can_view_cost else None
    margin = round((profit_unit / sell) * 100, 2) if (can_view_cost and sell > 0) else None

    return ProductItem(
        id=target["id"],
        code=target["code"],
        name=target["name"],
        category=target["category"],
        category_id=target.get("category_id"),
        base_unit=target.get("base_unit", "Cái"),
        packaging_specification=target.get("packaging_specification"),
        images=target.get("images", []),
        status=target.get("status", "active"),
        stock=target.get("stock", 0),
        sell_price=sell,
        cost_price=cost if can_view_cost else None,
        profit_margin=margin,
        profit_per_unit=profit_unit,
        transaction_count=_get_product_transaction_count(target["id"], db)
    )

from app.core.rbac import Role
from app.api.deps import require_roles

@router.put("/{product_id}/category")
@router.patch("/{product_id}/category")
def update_product_category(
    product_id: int,
    payload: dict,
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    """
    Đổi nhóm hàng (category_id) của sản phẩm dành cho Admin và Sales Manager.
    """
    if "category_id" not in payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Thiếu category_id")
    
    cat_id = payload["category_id"]
    if cat_id is not None:
        cat_id = int(cat_id)
        
    db = SessionLocal()
    try:
        category_name = "Chưa phân loại"
        if cat_id is not None:
            category = db.query(CategoryEntity).filter(CategoryEntity.id == cat_id).first()
            if not category:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Danh mục không tồn tại")
            category_name = category.name
            
        # 1. Update in SQLite Database
        from app.models.entities import ProductEntity
        db_product = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
        if db_product:
            db_product.category_id = cat_id
            db_product.category = category_name
            db.commit()
            
        # 2. Update in-memory RAW_PRODUCTS (to keep legacy endpoints in sync)
        for p in RAW_PRODUCTS:
            if p["id"] == product_id:
                p["category_id"] = cat_id
                p["category"] = category_name
                return {"status": "success", "message": "Cập nhật ngành hàng thành công", "product": p}
                
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy sản phẩm")
    finally:
        db.close()

@router.put("/{product_id}/price")
def update_product_price(
    product_id: int,
    data: PriceUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Thay đổi giá bán niêm yết hoặc giá vốn nhập kho.
    Ghi vết vào bảng audit_logs với action_type='PRICE_CHANGE'.
    """
    for p in RAW_PRODUCTS:
        if p["id"] == product_id:
            old_val = {}
            new_val = {}
            if data.sell_price is not None and data.sell_price != p["sell_price"]:
                old_val["sell_price"] = p["sell_price"]
                p["sell_price"] = data.sell_price
                new_val["sell_price"] = data.sell_price
            if data.cost_price is not None and data.cost_price != p["cost_price"]:
                old_val["cost_price"] = p["cost_price"]
                p["cost_price"] = data.cost_price
                new_val["cost_price"] = data.cost_price

            if not new_val:
                return {
                    "status": "success",
                    "message": "Giá sản phẩm không thay đổi.",
                    "product": p,
                }

            # Đồng bộ thay đổi vào DB nếu tồn tại bản ghi ProductEntity
            from app.models.entities import ProductEntity
            try:
                db_p = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
                if db_p:
                    if "sell_price" in new_val:
                        db_p.sell_price = data.sell_price
                    if "cost_price" in new_val:
                        db_p.cost_price = data.cost_price
                    db.commit()
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                print(f"Warning syncing product price to DB: {e}")

            # Cập nhật margin và profit_per_unit trong RAW_PRODUCTS
            if p.get("cost_price") and p.get("sell_price") and p["sell_price"] > 0:
                p["profit_per_unit"] = p["sell_price"] - p["cost_price"]
                p["profit_margin"] = round(((p["sell_price"] - p["cost_price"]) / p["sell_price"]) * 100, 1)

            log_audit_event(
                db=db,
                user=current_user,
                action_type="PRICE_CHANGE",
                entity_type="Product",
                entity_id=p["code"],
                old_val=old_val,
                new_val=new_val,
                reason=data.reason,
                request=request,
            )

            return {
                "status": "success",
                "message": f"Đã cập nhật giá cho sản phẩm {p['name']}.",
                "product": p
            }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy sản phẩm")


@router.get("/check-sku")
def check_sku_availability(
    sku: str,
    exclude_id: int | None = None,
    current_user: UserResponse = Depends(get_current_user)
):
    """
    Kiểm tra tính duy nhất của mã SKU realtime toàn hệ thống.
    """
    clean_sku = sku.strip().upper()
    if not clean_sku:
        return {"available": False, "message": "Mã SKU không được để trống"}

    for p in RAW_PRODUCTS:
        if p["code"].upper() == clean_sku:
            if exclude_id is not None and p["id"] == exclude_id:
                continue
            return {"available": False, "message": f"Mã SKU '{clean_sku}' đã tồn tại trên hệ thống"}

    return {"available": True, "message": "Mã SKU hợp lệ và chưa được sử dụng"}


@router.post("", response_model=ProductItem, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Khai báo sản phẩm mới (Product Form 4 khối chức năng).
    Bao gồm kiểm tra SKU duy nhất, lưu trữ thông tin cơ bản, quy cách & giá.
    """
    clean_sku = payload.code.strip().upper()
    if not clean_sku:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mã SKU không được để trống")

    # Kiểm tra trùng SKU
    for p in RAW_PRODUCTS:
        if p["code"].upper() == clean_sku:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Mã SKU '{clean_sku}' đã tồn tại trong hệ thống. Vui lòng chọn mã khác."
            )

    can_view_cost = current_user.can_view_cost or (Permission.COST_READ.value in current_user.permissions)
    cost = payload.cost_price if (can_view_cost and payload.cost_price is not None) else 0.0

    new_id = max([p["id"] for p in RAW_PRODUCTS], default=0) + 1
    new_product_dict = {
        "id": new_id,
        "code": clean_sku,
        "name": payload.name.strip(),
        "category": payload.category or "Thời trang",
        "category_id": payload.category_id,
        "base_unit": payload.base_unit or "Cái",
        "packaging_specification": payload.packaging_specification or "",
        "images": payload.images or [],
        "status": payload.status or "active",
        "stock": 0,
        "cost_price": cost,
        "sell_price": payload.sell_price,
    }

    # Lưu vào in-memory RAW_PRODUCTS
    RAW_PRODUCTS.append(new_product_dict)

    # Đồng bộ lưu vào SQLite ProductEntity
    try:
        from app.models.entities import ProductEntity
        db_prod = ProductEntity(
            id=new_id,
            code=clean_sku,
            name=payload.name.strip(),
            category=payload.category or "Thời trang",
            category_id=payload.category_id,
            stock=0,
            cost_price=cost,
            sell_price=payload.sell_price,
        )
        db.add(db_prod)
        db.commit()
    except Exception as db_err:
        db.rollback()
        # Non-fatal nếu trùng ID trên database, vẫn giữ in-memory
        print(f"Warning: sync product to DB: {db_err}")

    # Ghi audit log
    log_audit_event(
        db=db,
        user=current_user,
        action_type="PRODUCT_CREATE",
        entity_type="Product",
        entity_id=clean_sku,
        old_val=None,
        new_val=new_product_dict,
        reason=f"Khai báo sản phẩm mới: {payload.name.strip()}",
        request=request,
    )

    profit_unit = payload.sell_price - cost if can_view_cost else None
    margin = round((profit_unit / payload.sell_price) * 100, 2) if (can_view_cost and payload.sell_price > 0) else None

    return ProductItem(
        id=new_id,
        code=clean_sku,
        name=payload.name.strip(),
        category=payload.category or "Thời trang",
        category_id=payload.category_id,
        base_unit=payload.base_unit or "Cái",
        packaging_specification=payload.packaging_specification,
        images=payload.images or [],
        status=payload.status or "active",
        stock=0,
        sell_price=payload.sell_price,
        cost_price=cost if can_view_cost else None,
        profit_margin=margin,
        profit_per_unit=profit_unit,
        transaction_count=0
    )


@router.delete("/{product_id}", status_code=status.HTTP_200_OK)
def delete_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Xóa sản phẩm (chỉ cho phép nếu sản phẩm chưa phát sinh giao dịch).
    """
    transaction_count = _get_product_transaction_count(product_id, db)
    if transaction_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sản phẩm đã phát sinh giao dịch, không thể xóa. Vui lòng chuyển sang trạng thái Ngừng kinh doanh."
        )

    target = None
    for idx, p in enumerate(RAW_PRODUCTS):
        if p["id"] == product_id:
            target = p
            RAW_PRODUCTS.pop(idx)
            break

    db_prod = None
    if db:
        from app.models.entities import ProductEntity
        db_prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()

    if not target and not db_prod:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy sản phẩm")

    prod_code = target["code"] if target else db_prod.code
    prod_name = target["name"] if target else db_prod.name
    deleted_val = target if target else {
        "id": db_prod.id,
        "code": db_prod.code,
        "name": db_prod.name,
        "category": db_prod.category,
        "stock": db_prod.stock,
        "cost_price": db_prod.cost_price,
        "sell_price": db_prod.sell_price,
    }

    if db_prod:
        try:
            db.delete(db_prod)
            db.commit()
        except Exception as db_err:
            db.rollback()
            print(f"Warning: delete product from DB: {db_err}")

    log_audit_event(
        db=db,
        user=current_user,
        action_type="PRODUCT_DELETE",
        entity_type="Product",
        entity_id=prod_code,
        old_val=deleted_val,
        new_val=None,
        reason=f"Xóa vĩnh viễn sản phẩm {prod_name}",
        request=request,
    )

    return {"status": "success", "message": f"Đã xóa sản phẩm {prod_code} thành công"}


@router.put("/{product_id}/units")
@router.patch("/{product_id}/units")
def update_product_units(
    product_id: int,
    data: UnitUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.WAREHOUSE_MANAGER.value]))
):
    """
    Tiêu chí 1: Khai báo đa đơn vị tính:
    - Mỗi SKU có 1 Đơn vị tính cơ sở (Base unit, ví dụ: Lon, Cái) và có thể khai báo thêm nhiều Đơn vị quy đổi kèm hệ số quy đổi về đơn vị cơ sở (ví dụ: Lốc = 6, Thùng = 24).
    - Hệ số quy đổi bắt buộc > 0.
    - Lưu vào DB và đồng bộ in-memory RAW_PRODUCTS.
    """
    from app.models.entities import ProductEntity

    # Validate hệ số quy đổi bắt buộc > 0
    if data.units is not None:
        for u in data.units:
            if u.conversion_rate <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Hệ số quy đổi của đơn vị '{u.unit_name}' phải lớn hơn 0 (conversion_rate > 0)"
                )

    raw_p = _find_product_in_raw(product_id)
    db_product = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()

    if not raw_p and not db_product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy sản phẩm")

    old_base_unit = raw_p.get("base_unit", "Cái") if raw_p else (db_product.base_unit or "Cái")
    old_units = raw_p.get("units", []) if raw_p else (db_product.units or [])

    new_base_unit = data.base_unit.strip() if data.base_unit and data.base_unit.strip() else old_base_unit
    new_units = [u.dict() for u in data.units] if data.units is not None else old_units

    # Cập nhật DB
    if db_product:
        db_product.base_unit = new_base_unit
        db_product.units = new_units
        db.commit()

    # Cập nhật in-memory
    if raw_p:
        raw_p["base_unit"] = new_base_unit
        raw_p["units"] = new_units

    # Audit log
    code_val = raw_p.get("code") if raw_p else db_product.code
    log_audit_event(
        db=db,
        user=current_user,
        action_type="UNIT_CONVERSION_CHANGE",
        entity_type="Product",
        entity_id=code_val,
        old_val={"base_unit": old_base_unit, "units": old_units},
        new_val={"base_unit": new_base_unit, "units": new_units},
        reason="Cập nhật đơn vị tính và hệ số quy đổi",
        request=request,
    )

    return {
        "status": "success",
        "message": f"Cập nhật đơn vị quy đổi thành công cho sản phẩm {code_val}",
        "product": {
            "id": product_id,
            "code": code_val,
            "name": raw_p.get("name") if raw_p else db_product.name,
            "base_unit": new_base_unit,
            "units": new_units
        }
    }

# ==============================================================================
# HÀNG LOẠT SẢN PHẨM TỪ EXCEL (Bulk Import / Preview / Upsert)
# ==============================================================================
from fastapi import UploadFile, File
from fastapi.responses import Response
from app.schemas.product_import import (
    ProductBulkPreviewResponse,
    ProductBulkConfirmRequest,
    ProductBulkConfirmResponse,
)
from app.services.product_import_service import ProductBulkImportService

@router.get("/import-template")
def download_product_import_template(
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Endpoint 1: GET /api/v1/products/import-template
    Tải tệp Excel mẫu chuẩn (.xlsx) chứa đầy đủ các cột: SKU, Tên sản phẩm, ĐVT, Giá bán, Danh mục...
    """
    content = ProductBulkImportService.generate_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Mau_Nhap_Danh_Muc_San_Pham.xlsx"}
    )

@router.post("/bulk-preview", response_model=ProductBulkPreviewResponse)
def bulk_preview_products(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Endpoint 2: POST /api/v1/products/bulk-preview
    Nhận UploadFile Excel, validate từng dòng dữ liệu, so khớp SKU trong DB:
    - Báo lỗi chi tiết theo từng dòng (thiếu dữ liệu bắt buộc, sai định dạng số/chuỗi, đơn vị không hợp lệ).
    - Đánh dấu trạng thái: "NEW" (Tạo mới), "UPDATE" (Cập nhật), "ERROR" (Lỗi).
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Định dạng tệp không được hỗ trợ. Vui lòng tải lên tệp Excel (.xlsx hoặc .xls)."
        )

    try:
        content = file.file.read()
        return ProductBulkImportService.parse_and_validate(content, db)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lỗi khi đọc file Excel: {str(e)}"
        )

@router.post("/bulk-confirm", response_model=ProductBulkConfirmResponse)
def bulk_confirm_products(
    request: ProductBulkConfirmRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.PRODUCT_WRITE.value))
):
    """
    Endpoint 3: POST /api/v1/products/bulk-confirm
    Lưu / cập nhật dữ liệu vào CSDL theo transaction, hỗ trợ tệp lên đến 5.000 dòng.
    Ghi vết vào Audit Log và đồng bộ in-memory store.
    """
    try:
        result = ProductBulkImportService.execute_upsert(request, db)

        # Ghi log kiểm toán nếu có thao tác thành công
        if result.total_processed > 0:
            log_audit_event(
                db=db,
                user=current_user,
                action_type="PRODUCT_BULK_IMPORT",
                entity_type="Product",
                entity_id=f"BULK_{result.total_processed}_ITEMS",
                old_val=None,
                new_val={
                    "total_processed": result.total_processed,
                    "created_count": result.created_count,
                    "updated_count": result.updated_count,
                    "failed_count": result.failed_count,
                },
                reason=f"Nhập hàng loạt sản phẩm từ Excel ({result.created_count} tạo mới, {result.updated_count} cập nhật)",
                request=req,
            )

        return result
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


