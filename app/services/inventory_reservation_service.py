# backend/app/services/inventory_reservation_service.py
from __future__ import annotations
"""
Inventory Reservation Transaction Service
Tuân thủ nguyên tắc ACID & Concurrency Control:
- UC1: Duyệt đơn & Giữ chỗ tồn kho (Approve Order & Reserve Stock)
- UC2: Hủy đơn & Nhả tồn kho (Cancel Order & Release Stock)
- UC3: Quá hạn giữ chỗ & Tự động nhả tồn (Expired Reservation Cleanup)
- Deadlock Prevention: Khóa hàng theo thứ tự sku_id / product_id tăng dần.
- Row-Level Locking: WITH (UPDLOCK, ROWLOCK) / SELECT ... FOR UPDATE.
"""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import logging
from typing import List, Dict, Any, Optional, Union, Tuple
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.entities import (
    WarehouseStockEntity,
    ProductEntity,
    DealerEntity,
    OrderEntity,
    OrderItemEntity,
    AuditLogEntity,
)
from app.models.dealer import DEALERS_DB, load_dealers_db
from app.api.v1.endpoints.products import RAW_PRODUCTS, _find_product_in_raw
from app.services.inventory_availability_service import (
    resolve_dealer_warehouse,
    get_or_create_warehouse_stock,
)

logger = logging.getLogger(__name__)


# ==========================================
# CÁC NGOẠI LỆ NGHIỆP VỤ & HỆ THỐNG
# ==========================================

class InsufficientStockException(HTTPException):
    """Lỗi không đủ tồn khả dụng cho một SKU cụ thể."""
    def __init__(self, sku_code: str, available_qty: int, requested_qty: int):
        detail = (
            f"Không đủ tồn khả dụng cho SKU {sku_code}. "
            f"Khả dụng: {available_qty}, yêu cầu: {requested_qty}. "
            f"Hủy thao tác duyệt đơn."
        )
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        self.sku_code = sku_code
        self.available_qty = available_qty
        self.requested_qty = requested_qty


class SystemReservationError(Exception):
    """Lỗi hệ thống bất khả kháng khi thực hiện giao dịch giữ chỗ."""
    pass


class OrderNotFoundException(HTTPException):
    """Không tìm thấy đơn hàng."""
    def __init__(self, order_id_or_code: Union[int, str]):
        detail = f"Không tìm thấy đơn hàng có mã hoặc ID '{order_id_or_code}'."
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class OrderNotEligibleForApprovalException(HTTPException):
    """Đơn hàng không ở trạng thái hợp lệ để duyệt."""
    def __init__(self, current_status: str):
        detail = (
            f"Đơn hàng đang ở trạng thái '{current_status}', "
            f"không thể duyệt. Chỉ có thể duyệt đơn ở trạng thái 'PENDING_APPROVAL', 'PENDING' hoặc 'DRAFT'."
        )
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


# ==========================================
# TRANSACTION CONTEXT HELPER
# ==========================================

@contextmanager
def atomic_db_transaction(db: Session):
    """
    Context manager mở Transaction nguyên tử an toàn:
    - Nếu session chưa có transaction, dùng db.begin().
    - Nếu session đã có transaction đang mở, dùng savepoint (db.begin_nested()).
    - Tự động ROLLBACK khi có ngoại lệ, COMMIT khi thành công.
    """
    if db.in_transaction():
        tx = db.begin_nested()
    else:
        tx = db.begin()
    try:
        yield tx
        tx.commit()
    except Exception:
        tx.rollback()
        raise


def _lock_database_for_concurrency(db: Session):
    """SQL Server sử dụng row-level locking (UPDLOCK) trên từng bản ghi."""
    pass


# ==========================================
# HELPER TRÍCH XUẤT DÒNG HÀNG TỪ ĐƠN HÀNG
# ==========================================

def get_order_line_items(db: Session, order: OrderEntity) -> List[Dict[str, Any]]:
    """
    Trích xuất danh sách dòng hàng của đơn hàng:
    Ưu tiên bảng `order_items`. Nếu chưa có, fallback đọc từ `order.items_json`.
    """
    rel_items = db.query(OrderItemEntity).filter(OrderItemEntity.order_id == order.id).all()
    if rel_items:
        items = []
        for ri in rel_items:
            items.append({
                "id": ri.id,
                "product_id": ri.product_id,
                "sku_id": ri.sku_id or (f"SP{ri.product_id:03d}"),
                "product_name": ri.product_name or f"SP #{ri.product_id}",
                "quantity": ri.quantity,
                "base_quantity": ri.base_quantity or ri.quantity,
                "unit_price": ri.unit_price,
                "is_reserved": ri.is_reserved,
            })
        return items

    # Fallback trích xuất từ items_json
    if not order.items_json:
        return []

    try:
        data = json.loads(order.items_json)
        raw_list = data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) else [])
    except Exception:
        return []

    items = []
    for it in raw_list:
        pid = it.get("product_id")
        if not pid:
            continue
        qty = int(it.get("quantity", 1))
        conv = float(it.get("conversion_rate", 1.0) or 1.0)
        base_qty = int(it.get("base_quantity", round(qty * conv)))
        sku = it.get("sku_id") or it.get("product_code") or f"SP{pid:03d}"
        pname = it.get("product_name") or f"SP #{pid}"
        items.append({
            "id": None,
            "product_id": pid,
            "sku_id": sku,
            "product_name": pname,
            "quantity": qty,
            "base_quantity": base_qty,
            "unit_price": float(it.get("price", it.get("unit_price", 0.0)) or 0.0),
            "is_reserved": bool(it.get("is_reserved", False)),
        })
    return items


# ==========================================
# UC1: DUYỆT ĐƠN & GIỮ CHỖ TỒN KHO
# ==========================================

def approve_order_and_reserve_stock(
    db: Session,
    order_identifier: Union[int, str],
    approved_by: str,
    reservation_hours: int = 24,
) -> OrderEntity:
    """
    UC1: Duyệt đơn & Giữ chỗ tồn kho (Approve Order & Reserve Stock)
    
    Quy trình giao dịch:
    1. Kiểm tra đơn hàng đang chờ duyệt.
    2. Xác định kho phục vụ của đại lý.
    3. Sắp xếp toàn bộ SKU tăng dần theo sku_id / product_id để chống Deadlock.
    4. Row-level lock (UPDLOCK, ROWLOCK / FOR UPDATE) trên từng bản ghi tồn kho.
    5. Kiểm tra tồn khả dụng:
       available_qty = quantity_on_hand - reserved_quantity
       Nếu available_qty < item.quantity -> ROLLBACK, raise InsufficientStockException.
    6. Nếu tất cả SKU đủ hàng:
       - Tăng reserved_quantity và reserved_stock.
       - Cập nhật is_reserved = True cho từng dòng hàng.
       - Cập nhật order.status = 'APPROVED'.
       - Gán order.reservation_expires_at = NOW() + timedelta(hours=reservation_hours).
    7. Commit transaction.
    """
    # 1. Tìm đơn hàng
    order = db.query(OrderEntity).filter(
        (OrderEntity.order_code == str(order_identifier)) | (OrderEntity.id == (int(order_identifier) if str(order_identifier).isdigit() else -1))
    ).first()

    if not order:
        raise OrderNotFoundException(order_identifier)

    # Kiểm tra trạng thái hợp lệ
    cur_status = (order.status or "").upper()
    if cur_status not in ("PENDING_APPROVAL", "PENDING", "DRAFT", "REVISION_REQUIRED"):
        # Xử lý idempotent: Nếu đơn hàng đã được duyệt thành công trước đó (CONFIRMED hoặc APPROVED), trả về trực tiếp đơn hàng
        if cur_status in ("CONFIRMED", "APPROVED") and (order.approval_status == "APPROVED" or getattr(order, "requires_approval", False) is False):
            return order
        raise OrderNotEligibleForApprovalException(cur_status)

    # Xác định đại lý & kho phụ trách
    dealer = None
    if order.dealer_id:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == order.dealer_id).first()
        if not dealer:
            load_dealers_db()
            dealer = DEALERS_DB.get(order.dealer_id)
    warehouse_id, warehouse_name = resolve_dealer_warehouse(dealer)

    # Trích xuất danh sách dòng hàng
    line_items = get_order_line_items(db, order)
    if not line_items:
        # Nếu đơn không có dòng hàng, chỉ cập nhật trạng thái
        with atomic_db_transaction(db):
            order.status = "CONFIRMED"
            order.requires_approval = False
            order.approval_status = "APPROVED"
            order.approved_by = approved_by
            order.approved_at = datetime.now(timezone.utc)
            order.reservation_expires_at = datetime.now(timezone.utc) + timedelta(hours=reservation_hours)
        return order

    # 3. Sắp xếp các dòng hàng theo product_id tăng dần để TRÁNH DEADLOCK
    sorted_items = sorted(line_items, key=lambda x: int(x["product_id"]))

    try:
        with atomic_db_transaction(db):
            _lock_database_for_concurrency(db)

            # Danh sách bản ghi đã khóa và số lượng cần giữ chỗ
            reservations_to_apply: List[Tuple[WarehouseStockEntity, int, Dict[str, Any]]] = []

            # 4 & 5. Khóa dòng và kiểm tra tồn khả dụng
            for item in sorted_items:
                pid = int(item["product_id"])
                req_qty = int(item["base_quantity"])
                sku_code = str(item.get("sku_id") or f"SP{pid:03d}")

                # Khóa dòng bản ghi tồn kho (Row-Level Lock)
                stock_record = get_or_create_warehouse_stock(
                    db=db,
                    warehouse_id=warehouse_id,
                    warehouse_name=warehouse_name,
                    product_id=pid,
                    for_update=True,
                )

                # Đồng bộ giá trị giữa quantity_on_hand và actual_stock
                if stock_record.quantity_on_hand is None or (stock_record.quantity_on_hand == 0 and (stock_record.actual_stock or 0) > 0):
                    stock_record.quantity_on_hand = stock_record.actual_stock or 0
                if stock_record.reserved_quantity is None:
                    stock_record.reserved_quantity = stock_record.reserved_stock or 0

                on_hand = stock_record.quantity_on_hand
                reserved = stock_record.reserved_quantity
                available = max(0, on_hand - reserved)

                if available < req_qty:
                    # Ràng buộc nghiệp vụ: Bắt buộc ROLLBACK và trả về lỗi chuẩn
                    raise InsufficientStockException(
                        sku_code=sku_code,
                        available_qty=available,
                        requested_qty=req_qty,
                    )

                reservations_to_apply.append((stock_record, req_qty, item))

            # 6. Cập nhật giữ chỗ (khi tất cả SKU đều đủ hàng)
            now_utc = datetime.now(timezone.utc)
            expires_at = now_utc + timedelta(hours=reservation_hours)

            for stock_rec, req_qty, item_data in reservations_to_apply:
                # Tăng tồn giữ chỗ
                new_reserved = (stock_rec.reserved_quantity or 0) + req_qty
                
                # Check Constraint validation: 0 <= reserved <= on_hand
                assert new_reserved >= 0, "reserved_quantity không được âm"
                assert new_reserved <= (stock_rec.quantity_on_hand or 0), "reserved_quantity không được vượt quá quantity_on_hand"

                stock_rec.reserved_quantity = new_reserved
                stock_rec.reserved_stock = new_reserved
                stock_rec.updated_at = now_utc

                # Đánh dấu is_reserved = True trong bảng order_items nếu có
                if item_data.get("id"):
                    db_item = db.query(OrderItemEntity).filter(OrderItemEntity.id == item_data["id"]).first()
                    if db_item:
                        db_item.is_reserved = True
                else:
                    # Tạo hoặc cập nhật dòng trong order_items
                    existing_oi = db.query(OrderItemEntity).filter(
                        OrderItemEntity.order_id == order.id,
                        OrderItemEntity.product_id == item_data["product_id"]
                    ).first()
                    if existing_oi:
                        existing_oi.is_reserved = True
                    else:
                        new_oi = OrderItemEntity(
                            order_id=order.id,
                            order_code=order.order_code,
                            product_id=item_data["product_id"],
                            sku_id=item_data.get("sku_id"),
                            product_name=item_data.get("product_name"),
                            quantity=item_data.get("quantity", 1),
                            base_quantity=req_qty,
                            unit_price=item_data.get("unit_price", 0.0),
                            is_reserved=True,
                        )
                        db.add(new_oi)

            # Cập nhật is_reserved = True trong items_json
            if order.items_json:
                try:
                    js_data = json.loads(order.items_json)
                    if isinstance(js_data, list):
                        for el in js_data:
                            el["is_reserved"] = True
                        order.items_json = json.dumps(js_data, ensure_ascii=False)
                    elif isinstance(js_data, dict):
                        js_items = js_data.get("items", [])
                        for el in js_items:
                            el["is_reserved"] = True
                        js_data["items"] = js_items
                        js_data["requires_approval"] = False
                        js_data["approval_status"] = "APPROVED"
                        js_data["approved_by"] = approved_by
                        js_data["approved_at"] = now_utc.isoformat()
                        order.items_json = json.dumps(js_data, ensure_ascii=False)
                except Exception as ex:
                    logger.warning(f"Could not update items_json is_reserved: {ex}")

            # Cập nhật trạng thái đơn hàng
            order.status = "CONFIRMED"
            order.requires_approval = False
            order.approval_status = "APPROVED"
            order.approved_by = approved_by
            order.approved_at = now_utc
            order.reservation_expires_at = expires_at

            # Làm sạch thông báo chờ xác nhận khi đơn đã duyệt thành công
            if order.approval_reason:
                parts = [p.strip() for p in order.approval_reason.split("|") if p.strip()]
                remaining = [
                    p for p in parts
                    if not any(k in p.lower() for k in ["chờ nhân viên phụ trách xác nhận", "đại lý tự đặt", "cổng đại lý", "chờ nhân viên"])
                ]
                order.approval_reason = " | ".join(remaining) if remaining else None

    except InsufficientStockException as ise:
        # Ném lại ngoại lệ nghiệp vụ (Transaction tự động rollback bởi context manager)
        raise ise
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"System error reserving stock for order {order_identifier}: {exc}", exc_info=True)
        raise SystemReservationError(f"Lỗi hệ thống khi giữ chỗ tồn: {str(exc)}")

    return order


# ==========================================
# UC2: HỦY ĐƠN & NHẢ TỒN KHO
# ==========================================

def cancel_order_and_release_stock(
    db: Session,
    order_identifier: Union[int, str],
    cancelled_by: str,
    reason: str,
) -> OrderEntity:
    """
    UC2: Luồng Hủy đơn & Nhả tồn kho (Cancel Order & Release Stock)
    
    Quy trình giao dịch:
    1. Kiểm tra đơn hàng hợp lệ.
    2. Sắp xếp toàn bộ SKU tăng dần để chống Deadlock.
    3. Row-level lock trên từng bản ghi tồn kho.
    4. Giảm reserved_quantity cho các dòng có is_reserved = True.
    5. Cập nhật is_reserved = False.
    6. Cập nhật order.status = 'CANCELLED'.
    7. Commit transaction.
    """
    order = db.query(OrderEntity).filter(
        (OrderEntity.order_code == str(order_identifier)) | (OrderEntity.id == (int(order_identifier) if str(order_identifier).isdigit() else -1))
    ).first()

    if not order:
        raise OrderNotFoundException(order_identifier)

    cur_status = (order.status or "").upper()

    dealer = None
    if order.dealer_id:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == order.dealer_id).first()
        if not dealer:
            load_dealers_db()
            dealer = DEALERS_DB.get(order.dealer_id)
    warehouse_id, warehouse_name = resolve_dealer_warehouse(dealer)

    line_items = get_order_line_items(db, order)
    sorted_items = sorted(line_items, key=lambda x: int(x["product_id"])) if line_items else []

    try:
        with atomic_db_transaction(db):
            _lock_database_for_concurrency(db)
            now_utc = datetime.now(timezone.utc)

            for item in sorted_items:
                pid = int(item["product_id"])
                req_qty = int(item["base_quantity"])
                is_res = item.get("is_reserved", False) or (cur_status in ("APPROVED", "CONFIRMED", "PENDING", "PENDING_APPROVAL", "DRAFT", "CANCELLED"))

                if is_res and req_qty > 0:
                    stock_rec = db.query(WarehouseStockEntity).filter(
                        WarehouseStockEntity.warehouse_id == warehouse_id,
                        WarehouseStockEntity.product_id == pid
                    ).with_for_update().first()

                    if stock_rec:
                        cur_res = stock_rec.reserved_stock if stock_rec.reserved_stock is not None else (stock_rec.reserved_quantity or 0)
                        new_res = max(0, cur_res - req_qty)
                        stock_rec.reserved_quantity = new_res
                        stock_rec.reserved_stock = new_res
                        stock_rec.updated_at = now_utc

                # Cập nhật is_reserved = False trong DB
                if item.get("id"):
                    db_item = db.query(OrderItemEntity).filter(OrderItemEntity.id == item["id"]).first()
                    if db_item:
                        db_item.is_reserved = False
                else:
                    db_item = db.query(OrderItemEntity).filter(
                        OrderItemEntity.order_id == order.id,
                        OrderItemEntity.product_id == pid
                    ).first()
                    if db_item:
                        db_item.is_reserved = False

            # Cập nhật is_reserved = False trong items_json
            if order.items_json:
                try:
                    js_data = json.loads(order.items_json)
                    if isinstance(js_data, list):
                        for el in js_data:
                            el["is_reserved"] = False
                        order.items_json = json.dumps(js_data, ensure_ascii=False)
                    elif isinstance(js_data, dict):
                        js_items = js_data.get("items", [])
                        for el in js_items:
                            el["is_reserved"] = False
                        js_data["items"] = js_items
                        order.items_json = json.dumps(js_data, ensure_ascii=False)
                except Exception:
                    pass

            order.status = "CANCELLED"
            order.cancel_reason = reason
            order.cancelled_by = cancelled_by
            order.cancelled_at = now_utc

    except Exception as exc:
        logger.error(f"System error cancelling order {order_identifier}: {exc}", exc_info=True)
        raise SystemReservationError(f"Lỗi hệ thống khi hủy đơn và nhả tồn: {str(exc)}")

    return order


# ==========================================
# UC3: QUÁ HẠN GIỮ CHỖ & TỰ ĐỘNG NHẢ TỒN
# ==========================================

def release_expired_reservations(db: Session) -> List[Dict[str, Any]]:
    """
    UC3: Luồng Quá hạn giữ chỗ & Tự động nhả tồn (Expired Reservation Cleanup)
    
    Tìm tất cả các đơn hàng thỏa mãn:
    status in ('APPROVED', 'CONFIRMED') AND reservation_expires_at IS NOT NULL AND reservation_expires_at < NOW()
    
    Với mỗi đơn hàng tìm thấy:
    - Thực hiện quy trình nhả tồn tương tự UC2 trong một Transaction độc lập cho từng đơn.
    - Cập nhật trạng thái đơn sang 'EXPIRED'.
    - Ghi log kiểm toán / note: "Đơn hàng quá hạn thanh toán/giữ chỗ, tự động hoàn trả tồn kho."
    """
    now_utc = datetime.now(timezone.utc)

    # Tìm các đơn hàng thỏa mãn điều kiện quá hạn
    expired_orders = db.query(OrderEntity).filter(
        OrderEntity.status.in_(["APPROVED", "CONFIRMED"]),
        OrderEntity.reservation_expires_at != None,
        OrderEntity.reservation_expires_at < now_utc
    ).all()

    released_results: List[Dict[str, Any]] = []

    for ord in expired_orders:
        ord_code = ord.order_code
        try:
            with atomic_db_transaction(db):
                _lock_database_for_concurrency(db)

                dealer = None
                if ord.dealer_id:
                    dealer = db.query(DealerEntity).filter(DealerEntity.id == ord.dealer_id).first()
                warehouse_id, _ = resolve_dealer_warehouse(dealer)

                line_items = get_order_line_items(db, ord)
                sorted_items = sorted(line_items, key=lambda x: int(x["product_id"])) if line_items else []

                for item in sorted_items:
                    pid = int(item["product_id"])
                    req_qty = int(item["base_quantity"])

                    if req_qty > 0:
                        stock_rec = db.query(WarehouseStockEntity).filter(
                            WarehouseStockEntity.warehouse_id == warehouse_id,
                            WarehouseStockEntity.product_id == pid
                        ).with_for_update().first()

                        if stock_rec:
                            cur_res = stock_rec.reserved_quantity if stock_rec.reserved_quantity is not None else (stock_rec.reserved_stock or 0)
                            new_res = max(0, cur_res - req_qty)
                            stock_rec.reserved_quantity = new_res
                            stock_rec.reserved_stock = new_res
                            stock_rec.updated_at = now_utc

                    if item.get("id"):
                        db_item = db.query(OrderItemEntity).filter(OrderItemEntity.id == item["id"]).first()
                        if db_item:
                            db_item.is_reserved = False
                    else:
                        db_item = db.query(OrderItemEntity).filter(
                            OrderItemEntity.order_id == ord.id,
                            OrderItemEntity.product_id == pid
                        ).first()
                        if db_item:
                            db_item.is_reserved = False

                # Cập nhật items_json
                if ord.items_json:
                    try:
                        js_data = json.loads(ord.items_json)
                        if isinstance(js_data, list):
                            for el in js_data:
                                el["is_reserved"] = False
                            ord.items_json = json.dumps(js_data, ensure_ascii=False)
                        elif isinstance(js_data, dict):
                            js_items = js_data.get("items", [])
                            for el in js_items:
                                el["is_reserved"] = False
                            js_data["items"] = js_items
                            ord.items_json = json.dumps(js_data, ensure_ascii=False)
                    except Exception:
                        pass

                clean_reason = "Đơn hàng quá hạn thanh toán/giữ chỗ, tự động hoàn trả tồn kho."
                ord.status = "EXPIRED"
                ord.cancel_reason = clean_reason
                ord.cancelled_by = "SYSTEM_EXPIRATION_CLEANUP"
                ord.cancelled_at = now_utc

                # Ghi nhận vào audit logs
                audit_entry = AuditLogEntity(
                    user_id=None,
                    user_name="SYSTEM_EXPIRATION_CLEANUP",
                    action_type="ORDER_EXPIRED_RELEASE",
                    entity_type="Order",
                    entity_id=ord_code,
                    old_values=json.dumps({"status": "APPROVED"}, ensure_ascii=False),
                    new_values=json.dumps({"status": "EXPIRED", "reason": clean_reason}, ensure_ascii=False),
                    reason=clean_reason,
                    created_at=now_utc,
                )
                db.add(audit_entry)

            released_results.append({
                "order_id": ord.id,
                "order_code": ord_code,
                "status": "EXPIRED",
                "message": "Đã tự động giải phóng tồn kho do quá hạn giữ chỗ."
            })
            logger.info(f"Successfully released expired reservation for order {ord_code}")

        except Exception as exc:
            logger.error(f"Failed to release expired reservation for order {ord_code}: {exc}", exc_info=True)

    return released_results
