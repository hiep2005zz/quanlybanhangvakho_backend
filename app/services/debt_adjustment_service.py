# backend/app/services/debt_adjustment_service.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from fastapi import HTTPException, status

from app.models.entities import DealerEntity, OrderEntity, OrderItemEntity, ProductEntity
from app.models.invoice import InvoiceEntity
from app.models.debt_adjustment import (
    DebtAdjustmentEntity,
    DebtAdjustmentItemEntity,
    ReturnOrderEntity,
    ReturnOrderItemEntity,
)
from app.models.dealer import DEALERS_DB, load_dealers_db
from app.services.audit_service import log_audit_event
from app.services.invoice_service import sync_dealer_invoices_from_orders, calculate_agency_debt_summary


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_debt_adjustment_code(db: Session) -> str:
    """
    Sinh mã chứng từ điều chỉnh giảm công nợ duy nhất: DCG-YYYYMMDD-XXX
    Ví dụ: DCG-20261017-001
    """
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"DCG-{today_str}-"
    last_adj = (
        db.query(DebtAdjustmentEntity)
        .filter(DebtAdjustmentEntity.adjustment_code.like(f"{prefix}%"))
        .order_by(desc(DebtAdjustmentEntity.id))
        .first()
    )
    if last_adj and last_adj.adjustment_code.startswith(prefix):
        try:
            seq = int(last_adj.adjustment_code[len(prefix):]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:03d}"


def get_original_order_and_invoice(
    return_order: ReturnOrderEntity,
    db: Session,
) -> Tuple[Optional[InvoiceEntity], Optional[OrderEntity]]:
    """
    Truy xuất Hóa đơn gốc và Đơn hàng gốc liên kết với phiếu trả hàng.
    """
    invoice: Optional[InvoiceEntity] = None
    order: Optional[OrderEntity] = None

    if return_order.original_invoice_id:
        invoice = db.query(InvoiceEntity).filter(InvoiceEntity.id == return_order.original_invoice_id).first()
    elif return_order.original_invoice_code:
        invoice = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == return_order.original_invoice_code).first()

    if return_order.order_id:
        order = db.query(OrderEntity).filter(OrderEntity.id == return_order.order_id).first()
    elif return_order.order_code:
        order = db.query(OrderEntity).filter(OrderEntity.order_code == return_order.order_code).first()

    if not invoice and order:
        # Tìm hoặc đồng bộ hóa đơn từ đơn hàng
        inv_code = f"HD-{order.order_code}"
        invoice = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == inv_code).first()
        if not invoice and return_order.dealer_id:
            dealer = db.query(DealerEntity).filter(DealerEntity.id == return_order.dealer_id).first()
            if dealer:
                sync_dealer_invoices_from_orders(dealer, db)
                invoice = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == inv_code).first()

    if not order and invoice and invoice.order_code:
        order = db.query(OrderEntity).filter(OrderEntity.order_code == invoice.order_code).first()

    return invoice, order


def extract_original_sold_items_map(
    invoice: Optional[InvoiceEntity],
    order: Optional[OrderEntity],
    db: Session,
) -> Dict[int, Dict[str, Any]]:
    """
    Trích xuất bản đồ giá bán và số lượng bán gốc theo product_id từ Hóa đơn gốc / Đơn hàng gốc.
    ĐẢM BẢO TUYỆT ĐỐI lấy giá xuất bán trên Hóa đơn gốc, không lấy giá hiện hành.
    """
    items_map: Dict[int, Dict[str, Any]] = {}

    # 1. Ưu tiên đọc từ items_json của Hóa đơn gốc
    if invoice and invoice.items_json:
        try:
            inv_items = json.loads(invoice.items_json)
            if isinstance(inv_items, list):
                for item in inv_items:
                    pid = item.get("product_id")
                    if pid:
                        items_map[pid] = {
                            "product_id": pid,
                            "product_code": item.get("product_code") or f"SP{pid:03d}",
                            "product_name": item.get("product_name") or f"Sản phẩm #{pid}",
                            "unit_name": item.get("unit_name") or "Cái",
                            "sold_quantity": int(item.get("quantity") or 0),
                            "unit_price": float(item.get("price") or item.get("unit_price") or 0.0),
                        }
        except Exception:
            pass

    # 2. Bổ sung từ OrderItemEntity nếu thiếu
    if order and not items_map:
        db_items = db.query(OrderItemEntity).filter(OrderItemEntity.order_id == order.id).all()
        for it in db_items:
            p_code = getattr(it, "sku_id", None)
            prod = None
            if not p_code:
                prod = db.query(ProductEntity).filter(ProductEntity.id == it.product_id).first()
                p_code = getattr(prod, "code", None) or f"SP{it.product_id:03d}"
            p_name = it.product_name or (prod.name if prod else f"Sản phẩm #{it.product_id}")
            items_map[it.product_id] = {
                "product_id": it.product_id,
                "product_code": p_code,
                "product_name": p_name,
                "unit_name": it.unit_name or "Cái",
                "sold_quantity": int(it.quantity or 0),
                "unit_price": float(it.unit_price or 0.0),
            }

    # 3. Bổ sung từ order.items_json nếu có
    if order and order.items_json and not items_map:
        try:
            ord_items = json.loads(order.items_json)
            if isinstance(ord_items, list):
                for item in ord_items:
                    pid = item.get("product_id")
                    if pid:
                        items_map[pid] = {
                            "product_id": pid,
                            "product_code": item.get("product_code") or f"SP{pid:03d}",
                            "product_name": item.get("product_name") or f"Sản phẩm #{pid}",
                            "unit_name": item.get("unit_name") or "Cái",
                            "sold_quantity": int(item.get("quantity") or 0),
                            "unit_price": float(item.get("price") or item.get("unit_price") or 0.0),
                        }
        except Exception:
            pass

    return items_map


def get_pending_returns(db: Session, dealer_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Lấy danh sách các Phiếu trả hàng đã xác nhận hoàn kho (status = CONFIRMED)
    nhưng chưa được lập chứng từ điều chỉnh giảm công nợ (hoặc chứng từ cũ đã bị hủy).
    """
    # Lấy các return_order_id đã có chứng từ POSTED
    posted_return_ids = {
        row[0]
        for row in db.query(DebtAdjustmentEntity.return_order_id)
        .filter(DebtAdjustmentEntity.status == "POSTED", DebtAdjustmentEntity.return_order_id.isnot(None))
        .all()
    }

    query = db.query(ReturnOrderEntity).filter(
        ReturnOrderEntity.status.in_(["CONFIRMED", "COMPLETED", "PENDING_CREDIT", "APPROVED"]),
        ~ReturnOrderEntity.id.in_(posted_return_ids),
    )

    if dealer_id:
        query = query.filter(ReturnOrderEntity.dealer_id == dealer_id)

    return_orders = query.order_by(desc(ReturnOrderEntity.confirmed_at), desc(ReturnOrderEntity.id)).all()

    results = []
    for ro in return_orders:
        invoice, order = get_original_order_and_invoice(ro, db)
        inv_code = invoice.invoice_code if invoice else (ro.original_invoice_code or f"HD-{ro.order_code}")
        inv_id = invoice.id if invoice else ro.original_invoice_id

        items_list = []
        for it in ro.items:
            items_list.append({
                "id": it.id,
                "product_id": it.product_id,
                "product_code": it.product_code,
                "product_name": it.product_name,
                "unit_name": it.unit_name,
                "quantity": it.quantity,
                "unit_price": it.unit_price,
                "line_total": it.line_total,
                "reason": it.reason,
            })

        total_qty = sum(it.quantity for it in ro.items)
        results.append({
            "id": ro.id,
            "return_code": ro.return_code,
            "order_id": ro.order_id,
            "order_code": ro.order_code,
            "original_invoice_id": inv_id,
            "original_invoice_code": inv_code,
            "dealer_id": ro.dealer_id,
            "dealer_name": ro.dealer_name,
            "status": ro.status,
            "reason": ro.reason,
            "note": ro.note,
            "subtotal_amount": float(ro.subtotal_amount or 0.0),
            "tax_amount": float(ro.tax_amount or 0.0),
            "total_amount": float(ro.total_amount or 0.0),
            "total_items_count": total_qty,
            "confirmed_at": ro.confirmed_at,
            "created_at": ro.created_at,
            "items": items_list,
        })

    return results


def preview_debt_adjustment_from_return(return_id: int, db: Session) -> Dict[str, Any]:
    """
    Khởi tạo dữ liệu xem trước cho Kế toán công nợ:
    1. Kiểm tra Phiếu trả hàng
    2. Đối chiếu Đơn giá gốc từ Hóa đơn gốc
    3. Tính toán trước Số nợ giảm và Hạn mức khả dụng sau điều chỉnh
    """
    return_order = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.id == return_id).first()
    if not return_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Phiếu trả hàng ID {return_id}."
        )

    # Kiểm tra xem đã lập chứng từ chưa (Idempotency preview check)
    existing_adj = (
        db.query(DebtAdjustmentEntity)
        .filter(DebtAdjustmentEntity.return_order_id == return_id, DebtAdjustmentEntity.status == "POSTED")
        .first()
    )
    if existing_adj:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu trả hàng {return_order.return_code} đã được lập chứng từ giảm nợ (Mã: {existing_adj.adjustment_code})."
        )

    dealer = db.query(DealerEntity).filter(DealerEntity.id == return_order.dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Đại lý ID {return_order.dealer_id}."
        )

    invoice, order = get_original_order_and_invoice(return_order, db)
    if not invoice:
        # Tự động đồng bộ hóa đơn nếu chưa có
        sync_dealer_invoices_from_orders(dealer, db)
        invoice, order = get_original_order_and_invoice(return_order, db)

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Hóa đơn gốc cho Phiếu trả {return_order.return_code} (Đơn hàng: {return_order.order_code})."
        )

    # Trích xuất bản đồ giá gốc
    sold_items_map = extract_original_sold_items_map(invoice, order, db)

    # Tính toán từng mặt hàng trả theo đơn giá gốc
    subtotal = 0.0
    preview_items = []

    for ro_item in return_order.items:
        pid = ro_item.product_id
        sold_info = sold_items_map.get(pid)

        # Lấy đơn giá xuất bán ban đầu, KHÔNG dùng giá niêm yết hiện hành
        if sold_info and sold_info.get("unit_price", 0.0) > 0:
            orig_price = float(sold_info["unit_price"])
            orig_sold_qty = int(sold_info.get("sold_quantity", 0))
        elif ro_item.unit_price > 0:
            orig_price = float(ro_item.unit_price)
            orig_sold_qty = ro_item.quantity
        else:
            # Fallback tra cứu sản phẩm
            prod = db.query(ProductEntity).filter(ProductEntity.id == pid).first()
            orig_price = float(prod.price if prod else 0.0)
            orig_sold_qty = ro_item.quantity

        line_tot = float(ro_item.quantity) * orig_price
        subtotal += line_tot

        preview_items.append({
            "product_id": pid,
            "product_code": ro_item.product_code or (sold_info.get("product_code") if sold_info else f"SP{pid:03d}"),
            "product_name": ro_item.product_name or (sold_info.get("product_name") if sold_info else f"Sản phẩm #{pid}"),
            "unit_name": ro_item.unit_name or (sold_info.get("unit_name") if sold_info else "Cái"),
            "return_quantity": ro_item.quantity,
            "original_sold_quantity": orig_sold_qty,
            "original_unit_price": orig_price,
            "line_total": line_tot,
        })

    tax_amount = float(return_order.tax_amount or 0.0)
    total_adjusted = subtotal + tax_amount

    # Lấy thông tin công nợ hiện tại của đại lý
    debt_summary = calculate_agency_debt_summary(dealer, db)
    current_debt = float(debt_summary["total_debt"])
    credit_limit = float(debt_summary["credit_limit"])
    rem_credit = float(debt_summary["remaining_credit"])

    inv_rem_debt = float(invoice.remaining_debt if invoice.remaining_debt is not None else invoice.total_amount)
    estimated_inv_rem_debt = max(0.0, inv_rem_debt - total_adjusted)
    estimated_dealer_debt = max(0.0, current_debt - total_adjusted)
    estimated_rem_credit = max(0.0, credit_limit - estimated_dealer_debt)

    return {
        "return_order_id": return_order.id,
        "return_order_code": return_order.return_code,
        "original_invoice_id": invoice.id,
        "original_invoice_code": invoice.invoice_code,
        "order_id": order.id if order else return_order.order_id,
        "order_code": return_order.order_code,
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "current_debt": current_debt,
        "credit_limit": credit_limit,
        "remaining_credit": rem_credit,
        "invoice_total_amount": float(invoice.total_amount or 0.0),
        "invoice_remaining_debt": inv_rem_debt,
        "subtotal_amount": subtotal,
        "tax_amount": tax_amount,
        "total_adjusted_amount": total_adjusted,
        "estimated_remaining_debt": estimated_inv_rem_debt,
        "estimated_dealer_debt": estimated_dealer_debt,
        "estimated_remaining_credit": estimated_rem_credit,
        "reason": return_order.reason or "Hàng trả lại nhập kho",
        "items": preview_items,
    }


def create_debt_adjustment(
    data: Any,
    current_user: Any,
    db: Session,
) -> DebtAdjustmentEntity:
    """
    Giao dịch ACID: Lập và Phê duyệt ghi sổ chứng từ điều chỉnh giảm công nợ khi trả hàng.
    Quy tắc nghiệp vụ:
    1. Bảo toàn Hóa đơn gốc (Immutability):
       - Hóa đơn gốc KHÔNG bị xóa dòng hoặc sửa total_amount gốc.
       - Chỉ cập nhật remaining_debt: remaining_debt_mới = max(0, remaining_debt_cũ - total_adjusted).
       - Nếu remaining_debt == 0, chuyển status = 'PAID'.
    2. Đơn giá xuất bán gốc:
       - Bắt buộc áp dụng đơn giá tại thời điểm bán, bỏ qua giá niêm yết hiện hành.
    3. Cập nhật Công nợ & Hạn mức tức thời.
    4. Ngăn chặn lập trùng (Idempotency):
       - Một phiếu trả hàng chỉ được lập duy nhất một chứng từ giảm nợ POSTED.
    5. Ghi Audit Log bất biến: DEBT_ADJUSTMENT_CREATED.
    """
    return_id = data.return_order_id
    now = get_utc_now()

    # 1. Kiểm tra tồn tại và trạng thái Phiếu trả hàng
    return_order = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.id == return_id).first()
    if not return_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Phiếu trả hàng ID {return_id}."
        )

    # 2. Kiểm tra Idempotency: Chưa có chứng từ POSTED nào cho phiếu này
    existing_adj = (
        db.query(DebtAdjustmentEntity)
        .filter(DebtAdjustmentEntity.return_order_id == return_id, DebtAdjustmentEntity.status == "POSTED")
        .first()
    )
    if existing_adj:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu trả hàng {return_order.return_code} đã được lập chứng từ giảm nợ trước đó (Mã: {existing_adj.adjustment_code})."
        )

    # 3. Lấy Đại lý, Hóa đơn gốc & Đơn hàng gốc
    dealer = db.query(DealerEntity).filter(DealerEntity.id == return_order.dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Đại lý ID {return_order.dealer_id}."
        )

    invoice, order = get_original_order_and_invoice(return_order, db)
    if not invoice:
        sync_dealer_invoices_from_orders(dealer, db)
        invoice, order = get_original_order_and_invoice(return_order, db)

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Hóa đơn gốc cho Phiếu trả hàng {return_order.return_code}."
        )

    # 4. Trích xuất đơn giá xuất bán gốc và số lượng đã bán ban đầu
    sold_items_map = extract_original_sold_items_map(invoice, order, db)

    # 5. Xây dựng danh sách chi tiết chứng từ điều chỉnh (DebtAdjustmentItemEntity)
    items_to_create = []
    subtotal_amount = 0.0

    # Nếu request có gửi kèm danh sách items tuỳ chỉnh
    requested_items_dict = {it.product_id: it for it in (data.items or [])}

    for ro_item in return_order.items:
        pid = ro_item.product_id
        sold_info = sold_items_map.get(pid)

        # Số lượng thực trả
        if pid in requested_items_dict:
            ret_qty = requested_items_dict[pid].return_quantity
        else:
            ret_qty = ro_item.quantity

        if ret_qty <= 0:
            continue

        # Kiểm tra không vượt quá số lượng đã bán ban đầu (nếu có thông tin)
        if sold_info and sold_info.get("sold_quantity"):
            sold_qty = sold_info["sold_quantity"]
            if ret_qty > sold_qty:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Số lượng trả cho '{ro_item.product_name}' ({ret_qty}) vượt quá số lượng xuất bán ban đầu ({sold_qty})."
                )

        # Lấy đơn giá gốc
        if sold_info and sold_info.get("unit_price", 0.0) > 0:
            unit_price = float(sold_info["unit_price"])
        elif ro_item.unit_price > 0:
            unit_price = float(ro_item.unit_price)
        else:
            prod = db.query(ProductEntity).filter(ProductEntity.id == pid).first()
            unit_price = float(prod.price if prod else 0.0)

        line_tot = float(ret_qty) * unit_price
        subtotal_amount += line_tot

        item_entity = DebtAdjustmentItemEntity(
            product_id=pid,
            product_code=ro_item.product_code or (sold_info.get("product_code") if sold_info else f"SP{pid:03d}"),
            product_name=ro_item.product_name or (sold_info.get("product_name") if sold_info else f"Sản phẩm #{pid}"),
            unit_name=ro_item.unit_name or (sold_info.get("unit_name") if sold_info else "Cái"),
            return_quantity=ret_qty,
            original_unit_price=unit_price,
            line_total=line_tot,
        )
        items_to_create.append(item_entity)

    if not items_to_create:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không có mặt hàng nào hợp lệ để lập chứng từ điều chỉnh giảm công nợ."
        )

    tax_amount = float(return_order.tax_amount or 0.0)
    total_adjusted_amount = subtotal_amount + tax_amount

    if total_adjusted_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tổng số tiền giảm trừ công nợ phải lớn hơn 0."
        )

    # 6. Sinh mã chứng từ DCG-YYYYMMDD-XXX
    adjustment_code = generate_debt_adjustment_code(db)
    user_name = getattr(current_user, "full_name", None) or getattr(current_user, "username", "Kế toán công nợ")
    user_id = getattr(current_user, "id", None)
    adj_date = data.adjustment_date or now

    # 7. Khởi tạo thực thể DebtAdjustmentEntity (Bất biến)
    adjustment = DebtAdjustmentEntity(
        adjustment_code=adjustment_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        return_order_id=return_order.id,
        return_order_code=return_order.return_code,
        original_invoice_id=invoice.id,
        original_invoice_code=invoice.invoice_code,
        adjustment_date=adj_date,
        subtotal_amount=subtotal_amount,
        tax_amount=tax_amount,
        total_adjusted_amount=total_adjusted_amount,
        status="POSTED",
        reason=data.reason.strip(),
        note=data.note.strip() if data.note else None,
        created_by_id=user_id,
        created_by_name=user_name,
        items=items_to_create,
    )
    db.add(adjustment)
    db.flush()

    # 8. Cập nhật Hóa đơn gốc (BẢO TOÀN TỔNG TIỀN GỐC total_amount, CHỈ GIẢM remaining_debt)
    old_rem_debt = float(invoice.remaining_debt if invoice.remaining_debt is not None else invoice.total_amount)
    new_rem_debt = max(0.0, old_rem_debt - total_adjusted_amount)
    invoice.remaining_debt = new_rem_debt

    if new_rem_debt <= 0:
        invoice.status = "PAID"
        invoice.paid_amount = float(invoice.total_amount or 0.0)
        paid_note = f"Đã thanh toán hoàn tất (đã đối trừ qua chứng từ giảm nợ {adjustment_code})"
        invoice.note = f"{invoice.note} - {paid_note}" if invoice.note else paid_note
    else:
        if float(invoice.paid_amount or 0.0) > 0:
            invoice.status = "PARTIAL"
        adj_note = f"Đã giảm trừ {total_adjusted_amount:,.0f} đ theo chứng từ {adjustment_code}"
        invoice.note = f"{invoice.note} - {adj_note}" if invoice.note else adj_note

    invoice.updated_at = now

    # 9. Cập nhật Đơn hàng gốc nếu có
    if order:
        if new_rem_debt <= 0:
            order.status = "PAID"
        ord_adj_note = f"Giảm trừ trả hàng theo {adjustment_code}"
        order.note = f"{order.note} | {ord_adj_note}" if order.note else ord_adj_note
        order.updated_at = now

    # 10. Cập nhật Phiếu trả hàng sang trạng thái COMPLETED
    return_order.status = "COMPLETED"
    return_order.updated_at = now

    # 11. Đồng bộ DEALERS_DB trong bộ nhớ (nếu có)
    load_dealers_db()

    # 12. Ghi Audit Log bất biến (DEBT_ADJUSTMENT_CREATED)
    log_audit_event(
        db=db,
        user=current_user,
        action_type="DEBT_ADJUSTMENT_CREATED",
        entity_type="DebtAdjustment",
        entity_id=adjustment.id,
        old_val={
            "invoice_code": invoice.invoice_code,
            "invoice_remaining_debt_old": old_rem_debt,
            "return_order_code": return_order.return_code,
        },
        new_val={
            "adjustment_code": adjustment.adjustment_code,
            "total_adjusted_amount": total_adjusted_amount,
            "invoice_remaining_debt_new": new_rem_debt,
            "dealer_name": dealer.name,
            "status": "POSTED",
        },
        reason=f"Lập chứng từ giảm trừ công nợ do trả hàng: {data.reason}",
    )

    db.commit()
    db.refresh(adjustment)
    return adjustment
