# backend/app/services/invoice_service.py
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc

from app.models.entities import DealerEntity, OrderEntity, OrderItemEntity
from app.models.invoice import InvoiceEntity


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sync_dealer_invoices_from_orders(dealer: DealerEntity, db: Session) -> None:
    """
    Tự động đồng bộ các đơn hàng đã phát hành của đại lý vào bảng invoices nếu chưa tồn tại.
    """
    now = get_utc_now()
    allowed_days = int(getattr(dealer, "overdue_days_allowed", getattr(dealer, "max_debt_days", 30)) or 30)

    # Lấy các đơn hàng không bị hủy của đại lý
    orders = db.query(OrderEntity).filter(
        OrderEntity.dealer_id == dealer.id,
        OrderEntity.status != "CANCELLED",
    ).all()

    for ord in orders:
        inv_code = f"HD-{ord.order_code}"
        existing = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == inv_code).first()
        if not existing:
            # Xác định ngày phát hành & hạn thanh toán
            order_created = ord.created_at or now
            if order_created.tzinfo is None:
                order_created = order_created.replace(tzinfo=timezone.utc)
            
            due_date = order_created + timedelta(days=allowed_days)
            total = float(ord.total_amount or 0.0)

            # Đơn hàng PAID thì paid_amount = total, còn lại là UNPAID
            if ord.status == "PAID":
                paid = total
                status = "PAID"
            elif ord.status in ("DRAFT", "PENDING_APPROVAL"):
                # Đơn chờ duyệt/nháp chưa phát hành hóa đơn chính thức
                continue
            else:
                paid = 0.0
                if due_date < now:
                    status = "OVERDUE"
                else:
                    status = "UNPAID"

            remaining = max(0.0, total - paid)

            # Lấy items
            items_data = []
            if ord.items_json:
                try:
                    raw_items = json.loads(ord.items_json)
                    if isinstance(raw_items, list):
                        items_data = raw_items
                except Exception:
                    pass

            if not items_data:
                db_items = db.query(OrderItemEntity).filter(OrderItemEntity.order_id == ord.id).all()
                for it in db_items:
                    items_data.append({
                        "product_id": it.product_id,
                        "product_name": it.product_name or f"Sản phẩm #{it.product_id}",
                        "quantity": it.quantity,
                        "unit_name": it.unit_name or "Cái",
                        "price": it.unit_price,
                        "total": it.quantity * it.unit_price,
                    })

            new_invoice = InvoiceEntity(
                invoice_code=inv_code,
                order_id=ord.id,
                order_code=ord.order_code,
                dealer_id=dealer.id,
                dealer_name=dealer.name,
                issue_date=order_created,
                due_date=due_date,
                total_amount=total,
                paid_amount=paid,
                remaining_debt=remaining,
                status=status,
                items_json=json.dumps(items_data, ensure_ascii=False),
                note=ord.note or f"Hóa đơn bán hàng theo đơn {ord.order_code}",
            )
            db.add(new_invoice)
    db.commit()


def calculate_agency_debt_summary(dealer: DealerEntity, db: Session) -> Dict[str, Any]:
    """
    Tính toán 3 Khối hiển thị Tổng quan (Summary Cards) chuẩn chỉ theo yêu cầu:
    1. Tổng còn nợ: Tổng số tiền chưa thanh toán của tất cả hóa đơn đã phát hành của Đại lý.
    2. Hạn mức còn lại: Hạn mức công nợ được cấp - Tổng còn nợ - (Giá trị đơn hàng đang chờ duyệt/xử lý).
    3. Hạn thanh toán còn lại / Cảnh báo: Số ngày còn lại của hóa đơn sắp đến hạn nhất và số lượng hóa đơn đang quá hạn.
    """
    sync_dealer_invoices_from_orders(dealer, db)
    now = get_utc_now()

    invoices = db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == dealer.id).all()

    total_debt = 0.0
    overdue_count = 0
    nearest_due_days: Optional[int] = None
    nearest_due_invoice_code: Optional[str] = None
    nearest_due_date_str: Optional[str] = None

    open_invoices = []
    for inv in invoices:
        due = inv.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)

        # Cập nhật trạng thái tức thời
        rem = max(0.0, float(inv.total_amount or 0.0) - float(inv.paid_amount or 0.0))
        inv.remaining_debt = rem
        if rem <= 0:
            inv.status = "PAID"
        elif due < now:
            inv.status = "OVERDUE"
            overdue_count += 1
            total_debt += rem
            open_invoices.append((inv, due, rem))
        else:
            if float(inv.paid_amount or 0.0) > 0:
                inv.status = "PARTIAL"
            else:
                inv.status = "UNPAID"
            total_debt += rem
            open_invoices.append((inv, due, rem))

    # Tìm hóa đơn sắp đến hạn nhất trong số các hóa đơn chưa thanh toán
    future_invoices = [item for item in open_invoices if item[1] >= now]
    if future_invoices:
        # Sắp xếp theo ngày hạn sớm nhất
        future_invoices.sort(key=lambda x: x[1])
        nearest = future_invoices[0]
        days_diff = (nearest[1].date() - now.date()).days
        nearest_due_days = max(0, days_diff)
        nearest_due_invoice_code = nearest[0].invoice_code
        nearest_due_date_str = nearest[1].strftime("%d/%m/%Y")
    elif open_invoices:
        # Nếu tất cả các hóa đơn mở đều đã quá hạn
        open_invoices.sort(key=lambda x: x[1], reverse=True)
        nearest = open_invoices[0]
        days_diff = (nearest[1].date() - now.date()).days
        nearest_due_days = days_diff  # số âm thể hiện quá hạn
        nearest_due_invoice_code = nearest[0].invoice_code
        nearest_due_date_str = nearest[1].strftime("%d/%m/%Y")

    # Giá trị đơn hàng đang chờ duyệt/xử lý
    pending_orders = db.query(OrderEntity).filter(
        OrderEntity.dealer_id == dealer.id,
        OrderEntity.status.in_(["DRAFT", "PENDING", "PENDING_APPROVAL"]),
    ).all()
    pending_orders_amount = sum(float(o.total_amount or 0.0) for o in pending_orders)

    credit_limit = float(getattr(dealer, "credit_limit", 50000000.0) or 50000000.0)
    # Công thức: Hạn mức công nợ được cấp - Tổng còn nợ - Giá trị đơn hàng đang chờ duyệt
    remaining_credit = max(0.0, credit_limit - total_debt - pending_orders_amount)

    db.commit()

    return {
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "phone": getattr(dealer, "phone", None),
        "address": getattr(dealer, "address", None),
        "credit_limit": credit_limit,
        "total_debt": total_debt,
        "pending_orders_amount": pending_orders_amount,
        "remaining_credit": remaining_credit,
        "overdue_invoices_count": overdue_count,
        "nearest_due_days": nearest_due_days,
        "nearest_due_invoice_code": nearest_due_invoice_code,
        "nearest_due_date": nearest_due_date_str,
        "is_overdue": overdue_count > 0,
        "is_over_limit": total_debt > credit_limit,
    }


def get_agency_invoices_list(
    dealer: DealerEntity,
    db: Session,
    status_filter: Optional[str] = "ALL",
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """
    Lấy danh sách hóa đơn phân trang, tìm kiếm và lọc trạng thái cho đại lý.
    """
    sync_dealer_invoices_from_orders(dealer, db)
    now = get_utc_now()

    query = db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == dealer.id)

    # Cập nhật trạng thái động
    all_invoices = query.all()
    for inv in all_invoices:
        due = inv.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        rem = max(0.0, float(inv.total_amount or 0.0) - float(inv.paid_amount or 0.0))
        inv.remaining_debt = rem
        if rem <= 0:
            inv.status = "PAID"
        elif due < now:
            inv.status = "OVERDUE"
        elif float(inv.paid_amount or 0.0) > 0:
            inv.status = "PARTIAL"
        else:
            inv.status = "UNPAID"
    db.commit()

    # Re-query sau khi cập nhật
    query = db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == dealer.id)

    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(InvoiceEntity.status == status_filter.upper())

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                InvoiceEntity.invoice_code.ilike(term),
                InvoiceEntity.order_code.ilike(term),
                InvoiceEntity.note.ilike(term),
            )
        )

    total_count = query.count()
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    offset = max(0, (page - 1) * page_size)

    items = query.order_by(desc(InvoiceEntity.issue_date), desc(InvoiceEntity.id)).offset(offset).limit(page_size).all()

    status_labels = {
        "UNPAID": "Chưa thanh toán",
        "PARTIAL": "Thanh toán một phần",
        "PAID": "Đã thanh toán",
        "OVERDUE": "Quá hạn",
    }

    results = []
    for inv in items:
        due = inv.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        issue = inv.issue_date
        if issue.tzinfo is None:
            issue = issue.replace(tzinfo=timezone.utc)

        days_diff = (due.date() - now.date()).days

        results.append({
            "id": inv.id,
            "invoice_code": inv.invoice_code,
            "order_id": inv.order_id,
            "order_code": inv.order_code,
            "dealer_id": inv.dealer_id,
            "dealer_name": inv.dealer_name,
            "issue_date": issue.isoformat(),
            "due_date": due.isoformat(),
            "days_until_due": days_diff,
            "is_overdue": days_diff < 0 and inv.status != "PAID",
            "total_amount": inv.total_amount,
            "paid_amount": inv.paid_amount,
            "remaining_debt": inv.remaining_debt,
            "status": inv.status,
            "status_label": status_labels.get(inv.status, inv.status),
            "items_count": len(inv.items),
            "note": inv.note,
        })

    return {
        "items": results,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def get_invoice_detail(dealer_id: int, invoice_code: str, db: Session) -> Dict[str, Any]:
    """
    Lấy chi tiết toàn bộ thông tin hóa đơn phục vụ Xem và In ấn.
    """
    inv = db.query(InvoiceEntity).filter(
        InvoiceEntity.invoice_code == invoice_code,
        InvoiceEntity.dealer_id == dealer_id,
    ).first()

    if not inv:
        return None

    dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    now = get_utc_now()
    due = inv.due_date
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    issue = inv.issue_date
    if issue.tzinfo is None:
        issue = issue.replace(tzinfo=timezone.utc)

    days_diff = (due.date() - now.date()).days

    status_labels = {
        "UNPAID": "Chưa thanh toán",
        "PARTIAL": "Thanh toán một phần",
        "PAID": "Đã thanh toán",
        "OVERDUE": "Quá hạn",
    }

    return {
        "id": inv.id,
        "invoice_code": inv.invoice_code,
        "order_id": inv.order_id,
        "order_code": inv.order_code,
        "dealer": {
            "id": dealer.id if dealer else inv.dealer_id,
            "code": getattr(dealer, "code", "") if dealer else "",
            "name": getattr(dealer, "name", inv.dealer_name),
            "tax_code": getattr(dealer, "tax_code", "0109988776") or "0109988776",
            "phone": getattr(dealer, "phone", "") if dealer else "",
            "address": getattr(dealer, "address", "") if dealer else "",
            "email": getattr(dealer, "email", "") if dealer else "",
        },
        "seller": {
            "name": "CÔNG TY CỔ PHẦN PHÂN PHỐI & BÁN HÀNG TOÀN QUỐC",
            "tax_code": "0102345678",
            "address": "Tầng 8, Tòa nhà Văn phòng TechHub, Cầu Giấy, Hà Nội",
            "phone": "1900 6868",
            "bank_account": "19036886888688 - Techcombank Chi nhánh Thăng Long",
        },
        "issue_date": issue.isoformat(),
        "due_date": due.isoformat(),
        "days_until_due": days_diff,
        "total_amount": inv.total_amount,
        "paid_amount": inv.paid_amount,
        "remaining_debt": inv.remaining_debt,
        "status": inv.status,
        "status_label": status_labels.get(inv.status, inv.status),
        "items": inv.items,
        "note": inv.note,
    }


def generate_invoice_xml(detail: Dict[str, Any]) -> str:
    """
    Sinh nội dung file XML Hóa đơn điện tử chuẩn theo quy định Tổng cục Thuế.
    """
    items_xml = ""
    for idx, it in enumerate(detail.get("items", []), 1):
        price = float(it.get("price", 0.0) or 0.0)
        qty = float(it.get("quantity", 0.0) or 0.0)
        amount = float(it.get("total", price * qty) or 0.0)
        items_xml += f"""
        <HHDVu>
            <STT>{idx}</STT>
            <THHDon>{it.get('product_name', 'Hàng hóa')}</THHDon>
            <DVTinh>{it.get('unit_name', 'Cái')}</DVTinh>
            <SLuong>{qty}</SLuong>
            <DGia>{price}</DGia>
            <ThTien>{amount}</ThTien>
            <TSuat>10%</TSuat>
        </HHDVu>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<HDon>
    <DLHDon Id="HD_{detail.get('invoice_code')}">
        <TTChung>
            <PBan>2.0.0</PBan>
            <THDon>HÓA ĐƠN GIÁ TRỊ GIA TĂNG</THDon>
            <KHMSHDon>1</KHMSHDon>
            <KHHDon>C26TAA</KHHDon>
            <SHDon>{detail.get('invoice_code')}</SHDon>
            <NLap>{detail.get('issue_date')[:10]}</NLap>
            <DVTTe>VND</DVTTe>
            <TGia>1</TGia>
            <HTTToan>Chuyển khoản / Công nợ</HTTToan>
            <MDHDon>{detail.get('order_code')}</MDHDon>
        </TTChung>
        <NDHDon>
            <NBan>
                <Ten>{detail['seller']['name']}</Ten>
                <MST>{detail['seller']['tax_code']}</MST>
                <DChi>{detail['seller']['address']}</DChi>
                <SDThoai>{detail['seller']['phone']}</SDThoai>
                <STKNgHang>{detail['seller']['bank_account']}</STKNgHang>
            </NBan>
            <NMua>
                <Ten>{detail['dealer']['name']}</Ten>
                <MST>{detail['dealer'].get('tax_code', '')}</MST>
                <DChi>{detail['dealer'].get('address', '')}</DChi>
                <SDThoai>{detail['dealer'].get('phone', '')}</SDThoai>
            </NMua>
            <DSHHDVu>
                {items_xml}
            </DSHHDVu>
            <TToan>
                <TgTCThue>{detail.get('total_amount', 0.0)}</TgTCThue>
                <TgTThue>0</TgTThue>
                <TgTTTBSo>{detail.get('total_amount', 0.0)}</TgTTTBSo>
                <DaTToan>{detail.get('paid_amount', 0.0)}</DaTToan>
                <ConNo>{detail.get('remaining_debt', 0.0)}</ConNo>
                <HanTToan>{detail.get('due_date')[:10]}</HanTToan>
            </TToan>
        </NDHDon>
    </DLHDon>
</HDon>"""
    return xml.strip()
