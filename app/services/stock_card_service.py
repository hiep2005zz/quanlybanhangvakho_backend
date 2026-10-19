# backend/app/services/stock_card_service.py
from datetime import datetime, timezone
import json
import re
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from fastapi import HTTPException, status

from app.models.entities import (
    ProductEntity,
    WarehouseStockEntity,
    StockTransferEntity,
    StockAuditEntity,
    OrderEntity,
    OrderItemEntity,
    InventoryTransactionEntity,
)
from app.models.goods_receipt import (
    WarehouseEntity,
    GoodsReceiptNoteEntity,
    GoodsReceiptNoteItemEntity,
    InventoryLedgerEntity,
)
from app.models.supplier import SupplierEntity
from app.schemas.inventory import (
    StockCardResponse,
    StockCardSummary,
    StockCardEntry,
    StockCardVoucherInfo,
    WarehouseInfo,
)
from app.services.inventory_availability_service import DEFAULT_WAREHOUSES

# Map of standard warehouse IDs to names
STANDARD_WAREHOUSES = [
    {"warehouse_id": "WH01", "warehouse_name": "Kho Tổng Hà Nội"},
    {"warehouse_id": "WH02", "warehouse_name": "Kho Chi Nhánh TP.HCM"},
    {"warehouse_id": "WH03", "warehouse_name": "Kho Chi Nhánh Đà Nẵng"},
]


def resolve_canonical_warehouse(wid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Chuẩn hóa mã kho về dạng chuẩn WH01, WH02, WH03.
    Hỗ trợ cả dạng ID số (1, 2, 3) hoặc mã (KHO_HN, KHO_HCM, KHO_DN).
    """
    if not wid or wid.strip().lower() in ["", "all", "none", "null"]:
        return None, None

    clean = wid.strip().upper()
    if clean in ["WH01", "1", "KHO_HN"]:
        return "WH01", "Kho Tổng Hà Nội"
    elif clean in ["WH02", "3", "KHO_HCM"]:
        return "WH02", "Kho Chi Nhánh TP.HCM"
    elif clean in ["WH03", "2", "KHO_DN"]:
        return "WH03", "Kho Chi Nhánh Đà Nẵng"

    return clean, DEFAULT_WAREHOUSES.get(clean, f"Kho {clean}")


def parse_date(date_val: Any) -> Optional[datetime]:
    """Parse date string / datetime object sang datetime chuẩn."""
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return date_val
    s = str(date_val).strip()
    # Thử các định dạng phổ biến
    for fmt in [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def get_stock_card_for_product(
    db: Session,
    product_id: int,
    warehouse_filter: Optional[str] = None,
    from_date_str: Optional[str] = None,
    to_date_str: Optional[str] = None,
) -> StockCardResponse:
    """
    Lấy dữ liệu Thẻ kho (Stock Card) chi tiết của một sản phẩm.
    Đáp ứng chuẩn 3 tiêu chí:
    1. Toàn bộ phát sinh nhập, xuất, chuyển kho, điều chỉnh kiểm kê theo thứ tự thời gian.
    2. Mỗi dòng hiển thị tồn sau phát sinh (balance_stock) và liên kết chứng từ gốc.
    3. Lọc theo kho và khoảng thời gian.
    """
    # 1. Kiểm tra sản phẩm tồn tại
    product = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy sản phẩm có ID {product_id}."
        )

    canonical_wid, canonical_wname = resolve_canonical_warehouse(warehouse_filter)

    from_dt = parse_date(from_date_str)
    to_dt = parse_date(to_date_str)
    if to_dt and len(to_date_str.strip()) <= 10:
        # Nếu chỉ truyền ngày YYYY-MM-DD, set đến cuối ngày 23:59:59
        to_dt = to_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    # 2. Lấy danh sách kho hoạt động để trả về cho bộ lọc Frontend
    warehouses_list: List[WarehouseInfo] = []
    db_warehouses = db.query(WarehouseEntity).filter(WarehouseEntity.is_active == True).all()
    if db_warehouses:
        for w in db_warehouses:
            c_wid, c_wname = resolve_canonical_warehouse(str(w.id))
            warehouses_list.append(WarehouseInfo(warehouse_id=c_wid or str(w.id), warehouse_name=w.name or c_wname or f"Kho {w.id}"))
    else:
        for sw in STANDARD_WAREHOUSES:
            warehouses_list.append(WarehouseInfo(warehouse_id=sw["warehouse_id"], warehouse_name=sw["warehouse_name"]))

    # Deduplicate warehouses_list by warehouse_id
    seen_wids = set()
    unique_warehouses: List[WarehouseInfo] = []
    for w in warehouses_list:
        if w.warehouse_id not in seen_wids:
            seen_wids.add(w.warehouse_id)
            unique_warehouses.append(w)

    # 3. Thu thập toàn bộ các giao dịch phát sinh của sản phẩm
    raw_events: List[Dict[str, Any]] = []
    seen_keys = set()  # Dùng để tránh duplicate nếu 1 giao dịch được lưu ở nhiều bảng
    seen_voucher_codes = set()

    # A. Thu thập từ inventory_ledgers
    ledgers = db.query(InventoryLedgerEntity).filter(InventoryLedgerEntity.product_id == product_id).all()
    for lg in ledgers:
        c_wid, c_wname = resolve_canonical_warehouse(str(lg.warehouse_id))
        key = f"ledger_{lg.id}"
        seen_keys.add(f"{lg.reference_code}_{c_wid}_{round(lg.quantity, 2)}")
        if lg.reference_code:
            seen_voucher_codes.add(lg.reference_code)

        raw_events.append({
            "id": f"lg-{lg.id}",
            "created_at": lg.created_at or datetime.now(timezone.utc),
            "warehouse_id": c_wid or "WH01",
            "warehouse_name": c_wname or DEFAULT_WAREHOUSES.get(c_wid or "WH01", "Kho Tổng Hà Nội"),
            "transaction_type": (lg.transaction_type or "RECEIPT").upper(),
            "reference_code": lg.reference_code or f"REF-{lg.id}",
            "quantity": float(lg.quantity),
            "performed_by": lg.performed_by or "Hệ thống",
            "note": lg.note or f"Ghi sổ kho ({lg.transaction_type})",
        })

    # B. Thu thập từ goods_receipt_notes (nếu chưa có trong ledgers)
    grn_items = (
        db.query(GoodsReceiptNoteItemEntity, GoodsReceiptNoteEntity)
        .join(GoodsReceiptNoteEntity, GoodsReceiptNoteItemEntity.receipt_note_id == GoodsReceiptNoteEntity.id)
        .filter(
            GoodsReceiptNoteItemEntity.product_id == product_id,
            GoodsReceiptNoteEntity.status == "CONFIRMED"
        )
        .all()
    )
    for itm, grn in grn_items:
        c_wid, c_wname = resolve_canonical_warehouse(str(grn.warehouse_id))
        qty = float(itm.base_quantity if itm.base_quantity else itm.quantity)
        ref_key = f"{grn.code}_{c_wid}_{round(qty, 2)}"
        if ref_key not in seen_keys:
            seen_keys.add(ref_key)
            seen_voucher_codes.add(grn.code)
            raw_events.append({
                "id": f"grn-{grn.id}-{itm.id}",
                "created_at": grn.confirmed_at or grn.receipt_date or grn.created_at,
                "warehouse_id": c_wid or "WH01",
                "warehouse_name": c_wname or DEFAULT_WAREHOUSES.get(c_wid or "WH01", "Kho Tổng Hà Nội"),
                "transaction_type": "RECEIPT",
                "reference_code": grn.code,
                "quantity": qty,
                "performed_by": grn.confirmed_by or grn.created_by or "Thủ kho",
                "note": f"Nhập kho từ NCC theo phiếu {grn.code}" + (f" ({grn.note})" if grn.note else ""),
            })

    # C. Thu thập từ stock_transfers (Chuyển kho nội bộ)
    transfers = db.query(StockTransferEntity).filter(
        StockTransferEntity.status.in_(["IN_TRANSIT", "COMPLETED"])
    ).all()
    for tf in transfers:
        if not tf.items_json:
            continue
        try:
            items_arr = json.loads(tf.items_json)
        except Exception:
            continue
        for itm in items_arr:
            if itm.get("product_id") == product_id:
                t_qty = float(itm.get("transfer_qty", 0))
                rx_qty = float(itm.get("received_qty", t_qty)) if tf.status == "COMPLETED" else 0.0

                from_wid, from_wname = resolve_canonical_warehouse(tf.from_warehouse_id)
                to_wid, to_wname = resolve_canonical_warehouse(tf.to_warehouse_id)

                # Xuất từ kho đi
                out_key = f"{tf.code}_{from_wid}_{-round(t_qty, 2)}"
                if out_key not in seen_keys:
                    seen_keys.add(out_key)
                    seen_voucher_codes.add(tf.code)
                    raw_events.append({
                        "id": f"tf-out-{tf.id}-{product_id}",
                        "created_at": tf.dispatched_at or tf.created_at,
                        "warehouse_id": from_wid,
                        "warehouse_name": from_wname,
                        "transaction_type": "TRANSFER",
                        "reference_code": tf.code,
                        "quantity": -t_qty,
                        "performed_by": tf.dispatched_by or tf.created_by or "Thủ kho xuất",
                        "note": f"Xuất chuyển kho sang {to_wname}" + (f" - {tf.note}" if tf.note else ""),
                    })

                # Nhập vào kho đến (khi hoàn thành)
                if tf.status == "COMPLETED" and rx_qty > 0:
                    in_key = f"{tf.code}_{to_wid}_{round(rx_qty, 2)}"
                    if in_key not in seen_keys:
                        seen_keys.add(in_key)
                        seen_voucher_codes.add(tf.code)
                        raw_events.append({
                            "id": f"tf-in-{tf.id}-{product_id}",
                            "created_at": tf.received_at or tf.updated_at or tf.created_at,
                            "warehouse_id": to_wid,
                            "warehouse_name": to_wname,
                            "transaction_type": "TRANSFER",
                            "reference_code": tf.code,
                            "quantity": rx_qty,
                            "performed_by": tf.received_by or "Thủ kho nhận",
                            "note": f"Nhập chuyển kho từ {from_wname}" + (f" - {tf.discrepancy_reason}" if tf.discrepancy_reason else ""),
                        })

    # D. Thu thập từ stock_audits (Phiếu kiểm kê đã xác nhận cân đối)
    audits = db.query(StockAuditEntity).filter(StockAuditEntity.status == "CONFIRMED").all()
    for audit in audits:
        if not audit.items_json:
            continue
        try:
            items_arr = json.loads(audit.items_json)
        except Exception:
            continue
        for itm in items_arr:
            if itm.get("product_id") == product_id:
                disc = float(itm.get("discrepancy", 0))
                if disc != 0:
                    a_wid, a_wname = resolve_canonical_warehouse(audit.warehouse_id)
                    audit_key = f"{audit.code}_{a_wid}_{round(disc, 2)}"
                    if audit_key not in seen_keys:
                        seen_keys.add(audit_key)
                        seen_voucher_codes.add(audit.code)
                        raw_events.append({
                            "id": f"audit-{audit.id}-{product_id}",
                            "created_at": audit.confirmed_at or audit.updated_at or audit.created_at,
                            "warehouse_id": a_wid,
                            "warehouse_name": a_wname,
                            "transaction_type": "ADJUST",
                            "reference_code": audit.code,
                            "quantity": disc,
                            "performed_by": audit.confirmed_by or audit.created_by or "Quản lý kho",
                            "note": f"Cân đối tồn sau kiểm kê: {itm.get('reason') or audit.note or 'Điều chỉnh kiểm kê'}",
                        })

    # E. Thu thập từ orders (Xuất bán hàng)
    orders = (
        db.query(OrderItemEntity, OrderEntity)
        .join(OrderEntity, OrderItemEntity.order_id == OrderEntity.id)
        .filter(
            OrderItemEntity.product_id == product_id,
            OrderEntity.status.notin_(["CANCELLED", "DRAFT"])
        )
        .all()
    )
    for itm, ord_rec in orders:
        ord_code = ord_rec.order_code
        ord_qty = float(itm.quantity)
        ord_wid, ord_wname = resolve_canonical_warehouse(getattr(ord_rec, 'warehouse_id', 'WH01'))
        ord_key = f"{ord_code}_{ord_wid}_{-round(ord_qty, 2)}"
        if ord_key not in seen_keys:
            seen_keys.add(ord_key)
            seen_voucher_codes.add(ord_code)
            raw_events.append({
                "id": f"ord-{ord_rec.id}-{itm.id}",
                "created_at": ord_rec.created_at,
                "warehouse_id": ord_wid,
                "warehouse_name": ord_wname,
                "transaction_type": "ISSUE",
                "reference_code": ord_code,
                "quantity": -ord_qty,
                "performed_by": ord_rec.created_by or "Nhân viên bán hàng",
                "note": f"Xuất kho bán hàng theo đơn hàng {ord_code}",
            })

    # F. Thu thập từ inventory_transactions (các điều chỉnh / xuất / nhập phát sinh trực tiếp)
    inv_txs = db.query(InventoryTransactionEntity).filter(InventoryTransactionEntity.product_id == product_id).all()
    for tx in inv_txs:
        tx_type_str = (tx.type or "adjust").lower()
        if tx_type_str == "receipt":
            tx_type = "RECEIPT"
            tx_qty = float(abs(tx.quantity))
        elif tx_type_str == "issue":
            tx_type = "ISSUE"
            tx_qty = -float(abs(tx.quantity))
        else:
            tx_type = "ADJUST"
            tx_qty = float(tx.quantity)

        # Trích xuất mã chứng từ gốc nếu có trong lý do
        reason_str = tx.reason or ""
        ref_match = re.search(r'(PKK-\d+-\d+|GRN-\d+-\d+|CK-\d+-\d+|ORD\w+|DH\w+|PNK-\w+|PXK-\w+|DK-\w+)', reason_str)
        ref_code = ref_match.group(1) if ref_match else f"TX-{tx.id:04d}"

        # Bỏ qua nếu chứng từ này đã được lấy từ các bảng chuẩn ở trên
        if ref_code in seen_voucher_codes and not ref_code.startswith("TX-"):
            continue

        tx_key = f"{ref_code}_WH01_{round(tx_qty, 2)}"
        if tx_key not in seen_keys:
            seen_keys.add(tx_key)
            raw_events.append({
                "id": f"tx-{tx.id}",
                "created_at": tx.created_at,
                "warehouse_id": "WH01",
                "warehouse_name": DEFAULT_WAREHOUSES.get("WH01", "Kho Tổng Hà Nội"),
                "transaction_type": tx_type,
                "reference_code": ref_code,
                "quantity": tx_qty,
                "performed_by": tx.performed_by or "Thủ kho",
                "note": reason_str or f"Biến động kho ({tx.type})",
            })

    # Sắp xếp toàn bộ giao dịch theo thứ tự thời gian tăng dần (Chronological order)
    raw_events.sort(key=lambda x: parse_date(x["created_at"]) or datetime.min)

    # 4. Lọc theo kho nếu có chọn kho
    filtered_events = raw_events
    if canonical_wid:
        filtered_events = [e for e in raw_events if e["warehouse_id"] == canonical_wid]

    # Tính tổng tồn thực tế hiện tại trên hệ thống
    if canonical_wid:
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == canonical_wid,
            WarehouseStockEntity.product_id == product_id
        ).first()
        current_actual = int(ws.actual_stock or 0) if ws else 0
    else:
        current_actual = int(product.stock or 0)

    # 5. Khởi tạo running balance
    has_initial_entry = any(e["reference_code"].startswith("DK-") for e in filtered_events)
    sum_changes = sum(e["quantity"] for e in filtered_events)
    if has_initial_entry and sum_changes >= 0:
        running_balance = 0.0
    else:
        running_balance = max(0.0, float(current_actual) - sum_changes)

    opening_stock = running_balance
    period_entries: List[StockCardEntry] = []
    total_receipt = 0.0
    total_issue = 0.0


    for ev in filtered_events:
        ev_dt = parse_date(ev["created_at"])
        qty = ev["quantity"]
        prev_stk = running_balance
        running_balance += qty

        # Nếu giao dịch diễn ra TRƯỚC from_dt -> tính vào Tồn đầu kỳ
        if from_dt and ev_dt and ev_dt < from_dt:
            opening_stock = running_balance
            continue

        # Nếu giao dịch diễn ra SAU to_dt -> bỏ qua không hiển thị trong kỳ
        if to_dt and ev_dt and ev_dt > to_dt:
            continue

        # Giao dịch nằm trong khoảng thời gian lọc (hoặc không lọc ngày)
        r_qty = qty if qty > 0 else 0.0
        i_qty = abs(qty) if qty < 0 else 0.0
        total_receipt += r_qty
        total_issue += i_qty

        # Xác định nhãn nghiệp vụ và thông tin voucher gốc
        tx_type = ev["transaction_type"]
        if tx_type == "RECEIPT":
            tx_label = "Nhập kho"
        elif tx_type == "ISSUE":
            tx_label = "Xuất kho"
        elif tx_type == "TRANSFER":
            tx_label = "Chuyển kho (nhập)" if qty > 0 else "Chuyển kho (xuất)"
        else:
            tx_label = "Điều chỉnh / Kiểm kê"

        ref_code = ev["reference_code"]
        voucher_info = resolve_voucher_info(ref_code)

        created_at_str = ev_dt.strftime("%Y-%m-%d %H:%M:%S") if ev_dt else str(ev["created_at"])

        period_entries.append(
            StockCardEntry(
                id=ev["id"],
                created_at=created_at_str,
                transaction_type=tx_type,
                transaction_label=tx_label,
                reference_code=ref_code,
                voucher_info=voucher_info,
                warehouse_id=ev["warehouse_id"],
                warehouse_name=ev["warehouse_name"],
                quantity=qty,
                receipt_quantity=r_qty,
                issue_quantity=i_qty,
                previous_stock=prev_stk,
                balance_stock=running_balance,  # TỒN SAU PHÁT SINH
                performed_by=ev.get("performed_by"),
                note=ev.get("note"),
            )
        )

    # 6. Tính tổng tồn thực tế hiện tại trên hệ thống
    if canonical_wid:
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == canonical_wid,
            WarehouseStockEntity.product_id == product_id
        ).first()
        current_actual = (ws.actual_stock or 0) if ws else int(product.stock or 0)
    else:
        current_actual = int(product.stock or 0)

    # Tồn cuối kỳ
    closing_stock = opening_stock + total_receipt - total_issue
    if not from_dt and period_entries:
        closing_stock = running_balance

    summary = StockCardSummary(
        product_id=product.id,
        product_code=product.code,
        product_name=product.name,
        category=product.category,
        base_unit=product.base_unit or "Cái",
        current_actual_stock=current_actual,
        opening_stock=opening_stock,
        total_receipt=total_receipt,
        total_issue=total_issue,
        closing_stock=closing_stock,
        total_records=len(period_entries),
    )

    return StockCardResponse(
        summary=summary,
        entries=period_entries,
        warehouses=unique_warehouses,
    )


def resolve_voucher_info(ref_code: str) -> StockCardVoucherInfo:
    """Xác định loại chứng từ gốc và đường link truy nguyên chứng từ."""
    code = (ref_code or "").strip()
    if code.startswith("GRN-") or code.startswith("PNK-"):
        return StockCardVoucherInfo(
            voucher_type="grn",
            voucher_code=code,
            url=f"/goods-receipt?code={code}"
        )
    elif code.startswith("ORD") or code.startswith("DH"):
        return StockCardVoucherInfo(
            voucher_type="order",
            voucher_code=code,
            url=f"/orders?code={code}"
        )
    elif code.startswith("CK-"):
        return StockCardVoucherInfo(
            voucher_type="transfer",
            voucher_code=code,
            url=f"/stock-transfers?code={code}"
        )
    elif code.startswith("PKK-"):
        return StockCardVoucherInfo(
            voucher_type="audit",
            voucher_code=code,
            url=f"/stock-audits?code={code}"
        )
    elif code.startswith("DK-"):
        return StockCardVoucherInfo(
            voucher_type="initial",
            voucher_code=code,
            url=None
        )
    return StockCardVoucherInfo(
        voucher_type="adjustment",
        voucher_code=code,
        url=None
    )
