# backend/app/services/debt_reconciliation_service.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc, asc

from app.models.entities import DealerEntity, OrderEntity, UserEntity
from app.models.invoice import InvoiceEntity
from app.models.payment_collection import PaymentCollectionEntity
from app.models.debt_reconciliation import DebtReconciliationEntity
from app.services.invoice_service import sync_dealer_invoices_from_orders


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_date_to_utc(date_str: str, is_end_of_day: bool = False) -> datetime:
    """
    Parse chuỗi YYYY-MM-DD thành datetime UTC.
    Nếu is_end_of_day = True, đặt giờ thành 23:59:59.999999.
    """
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    if is_end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(tzinfo=timezone.utc)


def generate_reconciliation_code(db: Session, target_date: datetime) -> str:
    """
    Sinh mã biên bản đối chiếu dạng: BB-DC-YYYYMM-XXX
    Ví dụ: BB-DC-202610-001
    """
    prefix = f"BB-DC-{target_date.strftime('%Y%m')}-"
    last_rec = (
        db.query(DebtReconciliationEntity)
        .filter(DebtReconciliationEntity.code.like(f"{prefix}%"))
        .order_by(desc(DebtReconciliationEntity.id))
        .first()
    )
    if last_rec and last_rec.code.startswith(prefix):
        try:
            seq = int(last_rec.code[len(prefix):]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:03d}"


def calculate_reconciliation_ledger(
    dealer: DealerEntity,
    from_dt: datetime,
    to_dt: datetime,
    db: Session,
) -> Dict[str, Any]:
    """
    Tính toán chi tiết số dư đầu kỳ, phát sinh trong kỳ và số dư cuối kỳ cho Đại lý.
    Tự động đồng bộ các đơn hàng mới nhất vào hóa đơn trước khi tính.
    """
    # 1. Đồng bộ hóa đơn từ đơn hàng
    sync_dealer_invoices_from_orders(dealer, db)

    # 2. Tính số dư đầu kỳ:
    # Lấy toàn bộ hóa đơn trước from_dt
    prior_invoices = (
        db.query(InvoiceEntity)
        .filter(
            InvoiceEntity.dealer_id == dealer.id,
            InvoiceEntity.issue_date < from_dt,
        )
        .all()
    )
    prior_invoice_total = sum(float(inv.total_amount or 0.0) for inv in prior_invoices)

    # Lấy toàn bộ khoản thu trước from_dt
    prior_payments = (
        db.query(PaymentCollectionEntity)
        .filter(
            PaymentCollectionEntity.dealer_id == dealer.id,
            PaymentCollectionEntity.status == "CONFIRMED",
            PaymentCollectionEntity.collection_date < from_dt,
        )
        .all()
    )
    prior_payment_total = sum(float(p.amount or 0.0) for p in prior_payments)

    # Nếu prior_payments ít hơn paid_amount trên các invoice trước đó (do dữ liệu cũ không có PaymentCollection)
    prior_invoice_paid = sum(float(inv.paid_amount or 0.0) for inv in prior_invoices)
    prior_deducted = max(prior_payment_total, prior_invoice_paid)
    opening_balance = max(0.0, prior_invoice_total - prior_deducted)

    # 3. Thu thập các chứng từ phát sinh trong kỳ:
    # a. Hóa đơn mua hàng (Phát sinh tăng)
    period_invoices = (
        db.query(InvoiceEntity)
        .filter(
            InvoiceEntity.dealer_id == dealer.id,
            InvoiceEntity.issue_date >= from_dt,
            InvoiceEntity.issue_date <= to_dt,
        )
        .order_by(asc(InvoiceEntity.issue_date))
        .all()
    )

    # b. Phiếu thu tiền (Phát sinh giảm)
    period_payments = (
        db.query(PaymentCollectionEntity)
        .filter(
            PaymentCollectionEntity.dealer_id == dealer.id,
            PaymentCollectionEntity.status == "CONFIRMED",
            PaymentCollectionEntity.collection_date >= from_dt,
            PaymentCollectionEntity.collection_date <= to_dt,
        )
        .order_by(asc(PaymentCollectionEntity.collection_date))
        .all()
    )

    items: List[Dict[str, Any]] = []

    for inv in period_invoices:
        dt_str = inv.issue_date.strftime("%Y-%m-%d %H:%M") if inv.issue_date else ""
        items.append({
            "doc_type": "INVOICE",
            "doc_code": inv.invoice_code,
            "doc_date": dt_str,
            "timestamp": inv.issue_date.timestamp() if inv.issue_date else 0,
            "order_code": inv.order_code,
            "description": inv.note or f"Hóa đơn bán hàng theo đơn #{inv.order_code}",
            "increase_amount": float(inv.total_amount or 0.0),
            "decrease_amount": 0.0,
            "payment_method": None,
        })

    # Thu thập ID/mã đơn đã có phiếu thu để tránh trùng lặp
    paid_order_codes = set()
    for p in period_payments:
        dt_str = p.collection_date.strftime("%Y-%m-%d %H:%M") if p.collection_date else ""
        method_label = "Tiền mặt" if p.payment_method == "CASH" else "Chuyển khoản"
        if p.order_code:
            paid_order_codes.add(p.order_code)
        items.append({
            "doc_type": "PAYMENT",
            "doc_code": p.code,
            "doc_date": dt_str,
            "timestamp": p.collection_date.timestamp() if p.collection_date else 0,
            "order_code": p.order_code,
            "description": p.note or f"Thu tiền thanh toán ({method_label})",
            "increase_amount": 0.0,
            "decrease_amount": float(p.amount or 0.0),
            "payment_method": method_label,
        })

    # Trường hợp hóa đơn trong kỳ được thanh toán nhưng chưa có phiếu thu tương ứng
    for inv in period_invoices:
        paid = float(inv.paid_amount or 0.0)
        if paid > 0 and inv.order_code not in paid_order_codes:
            dt_str = inv.issue_date.strftime("%Y-%m-%d %H:%M") if inv.issue_date else ""
            items.append({
                "doc_type": "PAYMENT",
                "doc_code": f"PT-{inv.invoice_code}",
                "doc_date": dt_str,
                "timestamp": (inv.issue_date.timestamp() + 1) if inv.issue_date else 1,
                "order_code": inv.order_code,
                "description": f"Thanh toán cho hóa đơn {inv.invoice_code}",
                "increase_amount": 0.0,
                "decrease_amount": paid,
                "payment_method": "Chuyển khoản",
            })

    # Sắp xếp các chứng từ theo thứ tự thời gian phát sinh
    items.sort(key=lambda x: x.get("timestamp", 0))

    # Tính luỹ kế số dư sau từng chứng từ
    running = opening_balance
    total_increase = 0.0
    total_decrease = 0.0

    for item in items:
        inc = item["increase_amount"]
        dec = item["decrease_amount"]
        total_increase += inc
        total_decrease += dec
        running = running + inc - dec
        item["running_balance"] = round(running, 2)
        # Bỏ trường timestamp phụ trợ trước khi trả về
        item.pop("timestamp", None)

    closing_balance = round(opening_balance + total_increase - total_decrease, 2)

    # Nhãn hiển thị kỳ
    if from_dt.year == to_dt.year and from_dt.month == to_dt.month and from_dt.day == 1:
        # Nếu là tròn tháng
        period_label = f"Tháng {from_dt.month:02d}/{from_dt.year}"
    else:
        period_label = f"{from_dt.strftime('%d/%m/%Y')} - {to_dt.strftime('%d/%m/%Y')}"

    return {
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "dealer_phone": dealer.phone or "",
        "dealer_address": dealer.address or "",
        "from_date": from_dt.strftime("%Y-%m-%d"),
        "to_date": to_dt.strftime("%Y-%m-%d"),
        "period_label": period_label,
        "opening_balance": round(opening_balance, 2),
        "total_increase": round(total_increase, 2),
        "total_decrease": round(total_decrease, 2),
        "closing_balance": round(closing_balance, 2),
        "items": items,
    }


def create_debt_reconciliation(
    dealer: DealerEntity,
    from_date_str: str,
    to_date_str: str,
    created_by_user: Optional[UserEntity],
    note: Optional[str],
    send_immediately: bool,
    db: Session,
) -> DebtReconciliationEntity:
    """
    Khởi tạo và lưu trữ Biên bản đối chiếu công nợ mới dạng Snapshot.
    """
    from_dt = parse_date_to_utc(from_date_str, is_end_of_day=False)
    to_dt = parse_date_to_utc(to_date_str, is_end_of_day=True)

    ledger_data = calculate_reconciliation_ledger(dealer, from_dt, to_dt, db)
    code = generate_reconciliation_code(db, to_dt)
    init_status = "SENT" if send_immediately else "DRAFT"

    rec = DebtReconciliationEntity(
        code=code,
        dealer_id=dealer.id,
        dealer_code=dealer.code,
        dealer_name=dealer.name,
        dealer_phone=dealer.phone,
        dealer_address=dealer.address,
        from_date=from_dt,
        to_date=to_dt,
        period_label=ledger_data["period_label"],
        opening_balance=ledger_data["opening_balance"],
        total_increase=ledger_data["total_increase"],
        total_decrease=ledger_data["total_decrease"],
        closing_balance=ledger_data["closing_balance"],
        status=init_status,
        details=ledger_data["items"],
        created_by_id=created_by_user.id if created_by_user else None,
        created_by_name=created_by_user.full_name or created_by_user.username if created_by_user else "Hệ thống",
        note=note,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def to_response_dict(rec: DebtReconciliationEntity) -> Dict[str, Any]:
    """
    Chuyển đổi thực thể thành Dict chuẩn theo Pydantic schema.
    """
    return {
        "id": rec.id,
        "code": rec.code,
        "dealer_id": rec.dealer_id,
        "dealer_code": rec.dealer_code,
        "dealer_name": rec.dealer_name,
        "dealer_phone": rec.dealer_phone,
        "dealer_address": rec.dealer_address,
        "from_date": rec.from_date.strftime("%Y-%m-%d") if rec.from_date else "",
        "to_date": rec.to_date.strftime("%Y-%m-%d") if rec.to_date else "",
        "period_label": rec.period_label,
        "opening_balance": rec.opening_balance,
        "total_increase": rec.total_increase,
        "total_decrease": rec.total_decrease,
        "closing_balance": rec.closing_balance,
        "status": rec.status,
        "items": rec.details if isinstance(rec.details, list) else rec.details.get("invoices", []) + rec.details.get("payments", []),
        "dealer_reported_balance": rec.dealer_reported_balance,
        "dealer_dispute_reason": rec.dealer_dispute_reason,
        "disputed_at": rec.disputed_at.strftime("%Y-%m-%d %H:%M") if rec.disputed_at else None,
        "confirmed_at": rec.confirmed_at.strftime("%Y-%m-%d %H:%M") if rec.confirmed_at else None,
        "confirmed_by_name": rec.confirmed_by_name,
        "created_by_name": rec.created_by_name,
        "note": rec.note,
        "resolution_note": rec.resolution_note,
        "created_at": rec.created_at.strftime("%Y-%m-%d %H:%M") if rec.created_at else "",
    }
