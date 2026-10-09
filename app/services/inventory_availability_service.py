# backend/app/services/inventory_availability_service.py
from __future__ import annotations
"""
SCRUM-56: Kiểm tra tồn kho khả dụng & Phân quyền (Available Stock Service).
Công thức: Tồn khả dụng = Tồn thực tế - Tồn đang giữ chỗ cho đơn khác.
Đảm bảo an toàn đồng thời (Concurrency) bằng Row-level locking (SELECT ... FOR UPDATE).
"""
import json
from typing import Optional, Tuple, List, Dict, Any
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.entities import WarehouseStockEntity, ProductEntity, DealerEntity, OrderEntity
from app.models.dealer import DEALERS_DB, load_dealers_db
from app.api.v1.endpoints.products import RAW_PRODUCTS, _find_product_in_raw

# Danh mục các kho chính trong hệ thống
DEFAULT_WAREHOUSES = {
    "WH01": "Kho Tổng Hà Nội",
    "WH02": "Kho Chi Nhánh TP. Hồ Chí Minh",
    "WH03": "Kho Chi Nhánh Đà Nẵng",
}


def resolve_dealer_warehouse(dealer: Any) -> Tuple[str, str]:
    """
    Xác định kho phục vụ riêng cho đại lý.
    1. Ưu tiên warehouse_id / warehouse_name được cấu hình trực tiếp trên đại lý.
    2. Fallback dựa theo khu vực (region) hoặc địa chỉ (address) của đại lý.
    """
    if not dealer:
        return ("WH01", "Kho Tổng Hà Nội")

    # Kiểm tra cấu hình trực tiếp
    w_id = getattr(dealer, "warehouse_id", None)
    w_name = getattr(dealer, "warehouse_name", None)
    if w_id and w_name:
        return (w_id, w_name)
    if w_id and w_id in DEFAULT_WAREHOUSES:
        return (w_id, DEFAULT_WAREHOUSES[w_id])

    # Tra cứu theo địa bàn / khu vực
    region_str = str(getattr(dealer, "region", "") or "").lower()
    address_str = str(getattr(dealer, "address", "") or "").lower()
    location_text = f"{region_str} {address_str}"

    if any(k in location_text for k in ["hồ chí minh", "tp. hcm", "tp.hcm", "hcm", "sài gòn", "bình dương", "đồng nai", "cần thơ", "miền nam", "tân bình"]):
        return ("WH02", "Kho Chi Nhánh TP. Hồ Chí Minh")
    elif any(k in location_text for k in ["đà nẵng", "huế", "quảng nam", "quảng ngãi", "bình định", "miền trung"]):
        return ("WH03", "Kho Chi Nhánh Đà Nẵng")
    else:
        # Hà Nội, Hải Phòng, Cầu Giấy, Miền Bắc, hoặc mặc định
        return ("WH01", "Kho Tổng Hà Nội")


def calculate_active_orders_reserved(db: Session, warehouse_id: str, product_id: int) -> int:
    """
    Tính tổng số lượng sản phẩm đang giữ chỗ cho các đơn hàng chưa hoàn thành (PENDING, PENDING_APPROVAL, CONFIRMED).
    """
    total_reserved = 0
    try:
        active_orders = db.query(OrderEntity).filter(
            OrderEntity.status.in_(["PENDING", "PENDING_APPROVAL", "CONFIRMED"])
        ).all()
        for ord in active_orders:
            if not ord.items_json:
                continue
            try:
                data = json.loads(ord.items_json)
                items_list = data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) else [])
                for it in items_list:
                    pid = it.get("product_id")
                    if pid == product_id:
                        total_reserved += int(it.get("base_quantity", it.get("quantity", 0)))
            except Exception:
                pass
    except Exception:
        pass
    return total_reserved


def get_or_create_warehouse_stock(
    db: Session,
    warehouse_id: str,
    warehouse_name: str,
    product_id: int,
    for_update: bool = False
) -> WarehouseStockEntity:
    """
    Lấy bản ghi tồn kho của sản phẩm tại kho chỉ định.
    Nếu chưa có, tự động khởi tạo dựa theo tồn kho của sản phẩm.
    Hỗ trợ Row-Level Locking với with_for_update().
    """
    query = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == warehouse_id,
        WarehouseStockEntity.product_id == product_id
    )
    if for_update:
        query = query.with_for_update()

    stock_record = query.first()
    if stock_record:
        return stock_record

    # Tìm thông tin sản phẩm để lấy số lượng tồn kho ban đầu
    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
    raw_p = _find_product_in_raw(product_id)

    if raw_p and raw_p.get("stock", 0) > 0:
        base_stock = raw_p["stock"]
    elif prod_entity and prod_entity.stock is not None and prod_entity.stock > 0:
        base_stock = prod_entity.stock
    else:
        base_stock = 100

    # Phân bổ tồn kho theo kho phục vụ
    if warehouse_id == "WH01":
        actual = max(10, base_stock)
    elif warehouse_id == "WH02":
        actual = max(10, int(base_stock * 0.7))
    elif warehouse_id == "WH03":
        actual = max(5, int(base_stock * 0.5))
    else:
        actual = max(10, base_stock)

    reserved = 0

    new_stock = WarehouseStockEntity(
        warehouse_id=warehouse_id,
        warehouse_name=warehouse_name,
        product_id=product_id,
        actual_stock=actual,
        reserved_stock=reserved,
    )
    db.add(new_stock)
    try:
        db.flush()
    except Exception:
        db.rollback()
        # Thử truy vấn lại nếu một process khác đã tạo trong lúc này
        existing = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == warehouse_id,
            WarehouseStockEntity.product_id == product_id
        )
        if for_update:
            existing = existing.with_for_update()
        stock_record = existing.first()
        if stock_record:
            return stock_record
        raise

    return new_stock


def get_available_stock_info(db: Session, dealer_id: int, product_id: int) -> Dict[str, Any]:
    """
    AC 1 & AC 2:
    Lấy thông tin tồn khả dụng của sản phẩm tại kho phục vụ riêng cho đại lý:
    Tồn khả dụng = Tồn thực tế - Tồn đang giữ chỗ cho đơn khác.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        dealer_entity = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
        dealer = dealer_entity

    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )

    warehouse_id, warehouse_name = resolve_dealer_warehouse(dealer)

    prod_entity = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
    raw_p = _find_product_in_raw(product_id)
    if not prod_entity and not raw_p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {product_id}."
        )

    code = prod_entity.code if prod_entity else raw_p["code"]
    name = prod_entity.name if prod_entity else raw_p["name"]
    base_unit = (prod_entity.base_unit if prod_entity else raw_p.get("base_unit")) or "Cái"

    stock_record = get_or_create_warehouse_stock(db, warehouse_id, warehouse_name, product_id, for_update=False)

    actual = stock_record.actual_stock or 0
    reserved = stock_record.reserved_stock or 0
    available = max(0, actual - reserved)

    return {
        "product_id": product_id,
        "product_code": code,
        "product_name": name,
        "dealer_id": dealer_id,
        "dealer_name": getattr(dealer, "name", ""),
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "actual_stock": actual,
        "reserved_stock": reserved,
        "available_stock": available,
        "base_unit": base_unit,
    }


def get_dealer_stock_summary(db: Session, dealer_id: int) -> Dict[str, Any]:
    """
    AC 1:
    Lấy danh sách tồn khả dụng của tất cả sản phẩm tại kho phục vụ riêng cho đại lý.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()

    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )

    warehouse_id, warehouse_name = resolve_dealer_warehouse(dealer)

    # Lấy danh sách sản phẩm
    db_products = db.query(ProductEntity).filter(ProductEntity.status == "active").all()
    product_map = {}
    for p in db_products:
        product_map[p.id] = {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "base_unit": p.base_unit or "Cái",
            "stock": p.stock or 0,
        }

    # Bổ sung từ RAW_PRODUCTS nếu thiếu
    for rp in RAW_PRODUCTS:
        if rp["id"] not in product_map and rp.get("status", "active") != "inactive":
            product_map[rp["id"]] = {
                "id": rp["id"],
                "code": rp["code"],
                "name": rp["name"],
                "base_unit": rp.get("base_unit", "Cái"),
                "stock": rp.get("stock", 0),
            }

    items_summary = []
    for pid, pdata in product_map.items():
        stock_record = get_or_create_warehouse_stock(db, warehouse_id, warehouse_name, pid, for_update=False)
        actual = stock_record.actual_stock or 0
        reserved = stock_record.reserved_stock or 0
        available = max(0, actual - reserved)

        items_summary.append({
            "product_id": pid,
            "product_code": pdata["code"],
            "product_name": pdata["name"],
            "base_unit": pdata["base_unit"],
            "actual_stock": actual,
            "reserved_stock": reserved,
            "available_stock": available,
        })

    return {
        "dealer_id": dealer_id,
        "dealer_name": getattr(dealer, "name", ""),
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "items": items_summary,
    }


def validate_and_reserve_order_stock(
    db: Session,
    dealer_id: int,
    items: List[Dict[str, Any]],
    order_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    AC 3 & AC 4:
    - Chặn đặt hàng khi đặt vượt tồn khả dụng (Backend validation).
    - Hiển thị thông báo gợi ý số lượng tối đa còn có thể đặt được.
    - Xử lý Đồng thời (Race Condition): Sử dụng Database Transaction và Row-level locking
      (SELECT ... FOR UPDATE) để đảm bảo không bị âm tồn kho khi nhiều người cùng đặt.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )

    warehouse_id, warehouse_name = resolve_dealer_warehouse(dealer)

    # Đảm bảo Database Transaction Lock (Đối với SQLite: BEGIN IMMEDIATE; đối với Postgres/SQL Server: with_for_update)
    from sqlalchemy import text
    if db.bind and db.bind.dialect.name == "sqlite":
        try:
            db.connection().execute(text("BEGIN IMMEDIATE"))
        except Exception:
            pass

    # Sắp xếp theo product_id để tránh deadlock khi khóa nhiều hàng
    sorted_items = sorted(items, key=lambda x: x["product_id"])

    # Danh sách các bản ghi tồn kho đã khóa thành công
    locked_stocks: List[Tuple[WarehouseStockEntity, int, Dict[str, Any]]] = []

    for item in sorted_items:
        product_id = item["product_id"]
        quantity = int(item["quantity"])
        conversion_rate = float(item.get("conversion_rate", 1.0) or 1.0)
        base_quantity = int(round(quantity * conversion_rate))
        unit_name = item.get("unit_name") or item.get("unit") or "Cái"

        # BẮT BUỘC: Khóa hàng tồn kho (Row-Level Lock)
        stock_record = get_or_create_warehouse_stock(
            db=db,
            warehouse_id=warehouse_id,
            warehouse_name=warehouse_name,
            product_id=product_id,
            for_update=True
        )
        try:
            db.refresh(stock_record)
        except Exception:
            pass

        actual_stock = stock_record.actual_stock or 0
        reserved_stock = stock_record.reserved_stock or 0
        available_stock = max(0, actual_stock - reserved_stock)

        prod_name = item.get("product_name") or f"SP #{product_id}"
        prod_code = item.get("product_code") or f"SP{product_id:03d}"

        # Kiểm tra vượt tồn khả dụng:
        if base_quantity > available_stock:
            # Tính số lượng tối đa có thể đặt theo đơn vị đã chọn
            max_orderable = int(available_stock // conversion_rate) if conversion_rate > 0 else available_stock

            error_detail = {
                "message": (
                    f"Sản phẩm '{prod_name}' ({prod_code}) vượt quá tồn khả dụng tại {warehouse_name}. "
                    f"Tồn khả dụng hiện tại: {max_orderable} {unit_name} "
                    f"(Thực tế: {actual_stock}, Giữ chỗ: {reserved_stock}). "
                    f"Số lượng tối đa có thể đặt được là: {max_orderable} {unit_name}."
                ),
                "error_code": "INSUFFICIENT_AVAILABLE_STOCK",
                "product_id": product_id,
                "product_code": prod_code,
                "product_name": prod_name,
                "warehouse_id": warehouse_id,
                "warehouse_name": warehouse_name,
                "requested_quantity": quantity,
                "unit_name": unit_name,
                "conversion_rate": conversion_rate,
                "base_quantity": base_quantity,
                "actual_stock": actual_stock,
                "reserved_stock": reserved_stock,
                "available_stock": available_stock,
                "max_orderable": max_orderable,
            }
            # Rollback để nhả toàn bộ lock
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_detail["message"]
            )

        locked_stocks.append((stock_record, base_quantity, item))

    # Cập nhật tăng tồn giữ chỗ cho tất cả sản phẩm hợp lệ
    for stock_rec, base_qty, itm in locked_stocks:
        stock_rec.reserved_stock = (stock_rec.reserved_stock or 0) + base_qty

    db.flush()

    return {
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "reserved_items_count": len(locked_stocks),
    }


def release_order_stock(db: Session, order: OrderEntity) -> None:
    """
    Giải phóng tồn đang giữ chỗ khi đơn hàng bị HỦY (CANCELLED) hoặc TỪ CHỐI (REJECTED).
    """
    if not order or not order.items_json:
        return

    try:
        data = json.loads(order.items_json)
        items_list = data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) else [])
    except Exception:
        return

    dealer = DEALERS_DB.get(order.dealer_id) if order.dealer_id else None
    if not dealer and db:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == order.dealer_id).first()

    warehouse_id, warehouse_name = resolve_dealer_warehouse(dealer)

    for item in items_list:
        pid = item.get("product_id")
        base_qty = int(item.get("base_quantity", item.get("quantity", 0)))
        if not pid or base_qty <= 0:
            continue

        stock_record = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == warehouse_id,
            WarehouseStockEntity.product_id == pid
        ).with_for_update().first()

        if stock_record:
            stock_record.reserved_stock = max(0, (stock_record.reserved_stock or 0) - base_qty)

    db.flush()
