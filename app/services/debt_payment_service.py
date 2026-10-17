# backend/app/services/debt_payment_service.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
from typing import List, Dict, Any, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func

from app.models.entities import DealerEntity, OrderEntity, OrderItemEntity, UserEntity
from app.models.invoice import InvoiceEntity
from app.models.debt_payment import (
    PaymentReceiptEntity,
    PaymentAllocationEntity,
    PaymentAdjustmentEntity,
)
from app.schemas.auth import UserResponse
from app.schemas.debt_payment import (
    CreatePaymentReceiptRequest,
    CreateAdjustmentRequest,
    FifoPreviewItem,
)
from app.services.audit_service import log_audit_event


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sync_all_dealers_invoices(db: Session) -> None:
    """
    Tự động đồng bộ các hoá đơn từ đơn hàng CSDL hiện có.
    Đảm bảo các đại lý có đầy đủ hoá đơn với các mốc ngày khác nhau để phục vụ kiểm thử FIFO và đối trừ.
    """
    now = get_utc_now()
    dealers = db.query(DealerEntity).all()

    for dealer in dealers:
        allowed_days = int(getattr(dealer, "overdue_days_allowed", getattr(dealer, "max_debt_days", 30)) or 30)

        # 1. Đồng bộ đơn hàng hiện có vào invoices
        orders = db.query(OrderEntity).filter(
            OrderEntity.dealer_id == dealer.id,
            OrderEntity.status != "CANCELLED",
        ).all()

        for ord in orders:
            inv_code = f"HD-{ord.order_code}"
            existing = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == inv_code).first()
            if not existing:
                order_created = ord.created_at or now
                if order_created.tzinfo is None:
                    order_created = order_created.replace(tzinfo=timezone.utc)
                due_date = order_created + timedelta(days=allowed_days)
                total = float(ord.total_amount or 0.0)

                if ord.status == "PAID":
                    paid = total
                    inv_status = "PAID"
                elif ord.status in ("DRAFT", "PENDING_APPROVAL"):
                    continue
                else:
                    paid = 0.0
                    inv_status = "OVERDUE" if due_date < now else "UNPAID"

                rem = max(0.0, total - paid)

                new_inv = InvoiceEntity(
                    invoice_code=inv_code,
                    order_id=ord.id,
                    order_code=ord.order_code,
                    dealer_id=dealer.id,
                    dealer_name=dealer.name,
                    issue_date=order_created,
                    due_date=due_date,
                    total_amount=total,
                    paid_amount=paid,
                    remaining_debt=rem,
                    status=inv_status,
                    note=ord.note or f"Hoá đơn theo đơn hàng {ord.order_code}",
                )
                db.add(new_inv)

        # 2. Khởi tạo an toàn cho Dealer 1 & Dealer 2 nếu chưa có hoá đơn nào
        # Giúp kế toán viên có thể thử nghiệm ngay việc đối trừ công nợ trên nhiều đại lý khác nhau
        dealer_inv_count = db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == dealer.id).count()
        if dealer_inv_count == 0 and dealer.code in ("DL001", "DL002"):
            if dealer.code == "DL001":
                # Đại Lý Phân Phối Miền Bắc: 3 hoá đơn (cũ nhất 20 ngày trước, 10 ngày trước, 2 ngày trước)
                samples = [
                    ("ORD-MB-001", now - timedelta(days=20), 5000000.0, 1500000.0, "PARTIALLY_PAID"),
                    ("ORD-MB-002", now - timedelta(days=10), 8000000.0, 0.0, "UNPAID"),
                    ("ORD-MB-003", now - timedelta(days=2), 4500000.0, 0.0, "UNPAID"),
                ]
            else:
                # Đại Lý Thời Trang Tân Bình: 2 hoá đơn (15 ngày trước, 3 ngày trước)
                samples = [
                    ("ORD-TB-001", now - timedelta(days=15), 6200000.0, 0.0, "UNPAID"),
                    ("ORD-TB-002", now - timedelta(days=3), 3800000.0, 0.0, "UNPAID"),
                ]

            for s_code, s_date, s_total, s_paid, s_status in samples:
                inv_code = f"HD-{s_code}"
                due_date = s_date + timedelta(days=allowed_days)
                rem = max(0.0, s_total - s_paid)

                # Tạo đơn hàng tương ứng nếu chưa có
                existing_ord = db.query(OrderEntity).filter(OrderEntity.order_code == s_code).first()
                if not existing_ord:
                    new_ord = OrderEntity(
                        order_code=s_code,
                        dealer_id=dealer.id,
                        dealer_name=dealer.name,
                        created_by="Hệ thống",
                        total_amount=s_total,
                        status="CONFIRMED" if s_paid < s_total else "PAID",
                        created_at=s_date,
                        note=f"Đơn hàng mẫu {s_code}",
                    )
                    db.add(new_ord)
                    db.flush()
                    ord_id = new_ord.id
                else:
                    ord_id = existing_ord.id

                new_inv = InvoiceEntity(
                    invoice_code=inv_code,
                    order_id=ord_id,
                    order_code=s_code,
                    dealer_id=dealer.id,
                    dealer_name=dealer.name,
                    issue_date=s_date,
                    due_date=due_date,
                    total_amount=s_total,
                    paid_amount=s_paid,
                    remaining_debt=rem,
                    status=s_status,
                    note=f"Hoá đơn bán hàng theo đơn {s_code}",
                )
                db.add(new_inv)

    db.commit()


def get_dealer_debt_summary(dealer_id: int, db: Session) -> Dict[str, Any]:
    """
    Lấy thông tin tổng hợp công nợ của đại lý:
    - Tổng phát sinh phải thu (total_receivable)
    - Tổng đã thu (total_paid)
    - Tổng công nợ còn lại (total_remaining_debt)
    - Số hoá đơn còn nợ, số hoá đơn quá hạn
    """
    dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )

    # Đảm bảo hoá đơn đã được đồng bộ
    sync_all_dealers_invoices(db)

    now = get_utc_now()
    invoices = db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == dealer_id).all()

    total_receivable = 0.0
    total_paid = 0.0
    total_remaining_debt = 0.0
    open_invoices_count = 0
    overdue_invoices_count = 0

    for inv in invoices:
        tot = float(inv.total_amount or 0.0)
        paid = float(inv.paid_amount or 0.0)
        rem = max(0.0, tot - paid)
        inv.remaining_debt = rem

        total_receivable += tot
        total_paid += paid

        if rem > 0:
            total_remaining_debt += rem
            open_invoices_count += 1
            due = inv.due_date
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due < now:
                overdue_invoices_count += 1

    credit_limit = float(getattr(dealer, "credit_limit", 50000000.0) or 50000000.0)

    return {
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "phone": getattr(dealer, "phone", None),
        "email": getattr(dealer, "email", None),
        "address": getattr(dealer, "address", None),
        "credit_limit": credit_limit,
        "total_receivable": total_receivable,
        "total_paid": total_paid,
        "total_remaining_debt": total_remaining_debt,
        "open_invoices_count": open_invoices_count,
        "overdue_invoices_count": overdue_invoices_count,
        "is_overdue": overdue_invoices_count > 0,
        "is_over_limit": total_remaining_debt > credit_limit,
    }


def get_dealer_invoices_for_allocation(dealer_id: int, db: Session) -> List[Dict[str, Any]]:
    """
    Tiêu chí 2: Lấy danh sách toàn bộ hoá đơn của đại lý phục vụ Kế toán xem và đối trừ.
    Sắp xếp mặc định theo hoá đơn cũ nhất trước (issue_date ASC, due_date ASC, id ASC).
    """
    sync_all_dealers_invoices(db)
    now = get_utc_now()

    invoices = db.query(InvoiceEntity).filter(
        InvoiceEntity.dealer_id == dealer_id
    ).order_by(
        InvoiceEntity.issue_date.asc(),
        InvoiceEntity.due_date.asc(),
        InvoiceEntity.id.asc(),
    ).all()

    status_labels = {
        "UNPAID": "Chưa thanh toán",
        "PARTIAL": "Thanh toán một phần",
        "PARTIALLY_PAID": "Thanh toán một phần",
        "PAID": "Đã thanh toán",
        "OVERDUE": "Quá hạn",
    }

    results = []
    for inv in invoices:
        due = inv.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        issue = inv.issue_date
        if issue.tzinfo is None:
            issue = issue.replace(tzinfo=timezone.utc)

        days_diff = (due.date() - now.date()).days
        rem = max(0.0, float(inv.total_amount or 0.0) - float(inv.paid_amount or 0.0))

        # Xác định trạng thái hiển thị
        if rem <= 0:
            st = "PAID"
        elif float(inv.paid_amount or 0.0) > 0:
            st = "PARTIALLY_PAID"
        elif days_diff < 0:
            st = "OVERDUE"
        else:
            st = "UNPAID"

        results.append({
            "id": inv.id,
            "invoice_code": inv.invoice_code,
            "order_code": inv.order_code,
            "issue_date": issue.isoformat(),
            "due_date": due.isoformat(),
            "days_until_due": days_diff,
            "is_overdue": days_diff < 0 and rem > 0,
            "total_amount": float(inv.total_amount or 0.0),
            "paid_amount": float(inv.paid_amount or 0.0),
            "remaining_debt": rem,
            "remaining_amount": rem,
            "status": st,
            "status_label": status_labels.get(st, st),
            "items_count": len(inv.items) if hasattr(inv, "items") else 0,
            "note": inv.note,
        })

    return results


def preview_fifo_allocation(dealer_id: int, payment_amount: float, db: Session) -> List[FifoPreviewItem]:
    """
    Tiêu chí 2: Xem trước phân bổ tự động theo nguyên tắc hoá đơn cũ nhất trước (FIFO).
    """
    if payment_amount <= 0:
        return []

    sync_all_dealers_invoices(db)
    now = get_utc_now()

    # Lấy các hoá đơn còn nợ sắp xếp theo ngày phát hành cũ nhất
    open_invoices = db.query(InvoiceEntity).filter(
        InvoiceEntity.dealer_id == dealer_id,
        InvoiceEntity.remaining_debt > 0,
    ).order_by(
        InvoiceEntity.issue_date.asc(),
        InvoiceEntity.due_date.asc(),
        InvoiceEntity.id.asc(),
    ).all()

    remaining_payment = payment_amount
    preview_items: List[FifoPreviewItem] = []

    status_labels = {
        "UNPAID": "Chưa thanh toán",
        "PARTIAL": "Thanh toán một phần",
        "PARTIALLY_PAID": "Thanh toán một phần",
        "PAID": "Đã thanh toán",
        "OVERDUE": "Quá hạn",
    }

    for inv in open_invoices:
        if remaining_payment <= 0.0001:
            break

        cur_rem = float(inv.remaining_debt or 0.0)
        cur_paid = float(inv.paid_amount or 0.0)
        total = float(inv.total_amount or 0.0)

        alloc = min(remaining_payment, cur_rem)
        new_rem = max(0.0, cur_rem - alloc)
        new_paid = cur_paid + alloc

        if new_rem <= 0:
            new_status = "PAID"
        elif new_paid > 0:
            new_status = "PARTIALLY_PAID"
        else:
            new_status = inv.status

        issue_str = (inv.issue_date.isoformat() if inv.issue_date else now.isoformat())
        due_str = (inv.due_date.isoformat() if inv.due_date else now.isoformat())

        preview_items.append(FifoPreviewItem(
            invoice_id=inv.id,
            invoice_code=inv.invoice_code,
            issue_date=issue_str,
            due_date=due_str,
            total_amount=total,
            paid_before=cur_paid,
            remaining_before=cur_rem,
            allocated_amount=alloc,
            remaining_after=new_rem,
            new_status=new_status,
            new_status_label=status_labels.get(new_status, new_status),
        ))

        remaining_payment -= alloc

    return preview_items


def create_payment_receipt(
    payload: CreatePaymentReceiptRequest,
    current_user: UserResponse,
    db: Session,
) -> PaymentReceiptEntity:
    """
    Triển khai toàn vẹn Tiêu chí 1, 2, 3, 4 trong 1 Database Transaction:
    1. Kiểm tra đại lý tồn tại và số tiền thu > 0.
    2. Kiểm tra Tiêu chí 4: Không cho thu vượt tổng công nợ còn lại (rollback + 400).
    3. Sinh mã phiếu thu duy nhất (PT-YYYYMMDD-XXX).
    4. Đối trừ hoá đơn theo AUTO_FIFO hoặc MANUAL.
    5. Cập nhật paid_amount, remaining_debt và chuyển trạng thái hoá đơn.
    6. Lưu chi tiết phân bổ vào payment_allocations.
    7. Ghi nhận Audit Log.
    """
    if payload.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số tiền thu phải lớn hơn 0."
        )

    dealer = db.query(DealerEntity).filter(DealerEntity.id == payload.dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {payload.dealer_id}."
        )

    # Đảm bảo hoá đơn được đồng bộ trước khi tính toán
    sync_all_dealers_invoices(db)

    # Lấy các hoá đơn còn nợ của đại lý để kiểm tra và đối trừ
    open_invoices = db.query(InvoiceEntity).filter(
        InvoiceEntity.dealer_id == dealer.id,
        InvoiceEntity.remaining_debt > 0,
    ).order_by(
        InvoiceEntity.issue_date.asc(),
        InvoiceEntity.due_date.asc(),
        InvoiceEntity.id.asc(),
    ).all()

    total_remaining_debt = sum(float(inv.remaining_debt or 0.0) for inv in open_invoices)

    # =========================================================================
    # TIÊU CHÍ 4: Không cho thu vượt tổng công nợ còn lại
    # =========================================================================
    if payload.amount > total_remaining_debt + 0.001:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số tiền thu không được vượt quá tổng công nợ còn lại của đại lý."
        )

    now_utc = get_utc_now()
    payment_time = payload.payment_date or now_utc
    if payment_time.tzinfo is None:
        payment_time = payment_time.replace(tzinfo=timezone.utc)

    collector = payload.collector_name.strip() if payload.collector_name and payload.collector_name.strip() else (
        current_user.full_name or current_user.username
    )

    # Sinh mã phiếu thu: PT-YYYYMMDD-XXX
    date_prefix = payment_time.strftime("%Y%m%d")
    today_count = db.query(func.count(PaymentReceiptEntity.id)).filter(
        PaymentReceiptEntity.receipt_code.like(f"PT-{date_prefix}-%")
    ).scalar() or 0
    seq = today_count + 1
    receipt_code = f"PT-{date_prefix}-{seq:03d}"

    # Đảm bảo mã là duy nhất nếu có trùng lặp
    while db.query(PaymentReceiptEntity).filter(PaymentReceiptEntity.receipt_code == receipt_code).first():
        seq += 1
        receipt_code = f"PT-{date_prefix}-{seq:03d}"

    # Sử dụng database transaction
    try:
        initial_status = "NO_HISTORY" if payload.save_history is False else "CONFIRMED"
        receipt = PaymentReceiptEntity(
            receipt_code=receipt_code,
            dealer_id=dealer.id,
            dealer_code=dealer.code,
            dealer_name=dealer.name,
            amount=float(payload.amount),
            payment_method=payload.payment_method or "Tiền mặt",
            payment_date=payment_time,
            collector_id=current_user.id,
            collector_name=collector,
            note=payload.note.strip() if payload.note else None,
            allocation_mode=payload.allocation_mode.upper(),
            status=initial_status,
        )
        db.add(receipt)
        db.flush()

        # =====================================================================
        # TIÊU CHÍ 2 & 3: Xử lý đối trừ hoá đơn
        # =====================================================================
        if payload.allocation_mode.upper() == "AUTO_FIFO":
            remaining_to_allocate = float(payload.amount)
            for inv in open_invoices:
                if remaining_to_allocate <= 0.0001:
                    break

                cur_rem = float(inv.remaining_debt or 0.0)
                alloc = min(remaining_to_allocate, cur_rem)

                if alloc > 0:
                    # Ghi nhận phân bổ
                    allocation_record = PaymentAllocationEntity(
                        payment_id=receipt.id,
                        invoice_id=inv.id,
                        allocated_amount=alloc,
                        current_allocated_amount=alloc,
                    )
                    db.add(allocation_record)

                    # Cập nhật hoá đơn
                    inv.paid_amount = float(inv.paid_amount or 0.0) + alloc
                    inv.remaining_debt = max(0.0, float(inv.total_amount or 0.0) - float(inv.paid_amount))

                    if inv.remaining_debt <= 0.001:
                        inv.remaining_debt = 0.0
                        inv.status = "PAID"
                    else:
                        inv.status = "PARTIALLY_PAID"

                    ord_obj = None
                    if inv.order_id:
                        ord_obj = db.query(OrderEntity).filter(OrderEntity.id == inv.order_id).first()
                    if not ord_obj and inv.order_code:
                        ord_obj = db.query(OrderEntity).filter(OrderEntity.order_code == inv.order_code).first()

                    if ord_obj:
                        if inv.remaining_debt == 0:
                            ord_obj.status = "PAID"
                        elif float(inv.paid_amount or 0.0) > 0 and ord_obj.status != "PAID":
                            ord_obj.status = "PARTIAL"

                    remaining_to_allocate -= alloc

        elif payload.allocation_mode.upper() == "MANUAL":
            if not payload.allocations:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Vui lòng chọn ít nhất một hoá đơn để phân bổ đối trừ thủ công."
                )

            total_allocated = sum(float(a.allocated_amount) for a in payload.allocations)
            if abs(total_allocated - float(payload.amount)) > 0.01:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Tổng số tiền phân bổ ({total_allocated:,.0f} đ) phải bằng đúng số tiền thu ({payload.amount:,.0f} đ)."
                )

            for alloc_item in payload.allocations:
                inv = db.query(InvoiceEntity).filter(
                    InvoiceEntity.id == alloc_item.invoice_id,
                    InvoiceEntity.dealer_id == dealer.id,
                ).first()

                if not inv:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Không tìm thấy hoá đơn ID {alloc_item.invoice_id} thuộc đại lý {dealer.name}."
                    )

                alloc_amt = float(alloc_item.allocated_amount)
                if alloc_amt <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Số tiền đối trừ vào hoá đơn {inv.invoice_code} phải lớn hơn 0."
                    )

                cur_rem = float(inv.remaining_debt or 0.0)
                if alloc_amt > cur_rem + 0.001:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Số tiền đối trừ vào hoá đơn {inv.invoice_code} ({alloc_amt:,.0f} đ) không được vượt quá số còn nợ ({cur_rem:,.0f} đ)."
                    )

                # Ghi nhận phân bổ
                allocation_record = PaymentAllocationEntity(
                    payment_id=receipt.id,
                    invoice_id=inv.id,
                    allocated_amount=alloc_amt,
                    current_allocated_amount=alloc_amt,
                )
                db.add(allocation_record)

                # Cập nhật hoá đơn
                inv.paid_amount = float(inv.paid_amount or 0.0) + alloc_amt
                inv.remaining_debt = max(0.0, float(inv.total_amount or 0.0) - float(inv.paid_amount))

                if inv.remaining_debt <= 0.001:
                    inv.remaining_debt = 0.0
                    inv.status = "PAID"
                else:
                    inv.status = "PARTIALLY_PAID"

                ord_obj = None
                if inv.order_id:
                    ord_obj = db.query(OrderEntity).filter(OrderEntity.id == inv.order_id).first()
                if not ord_obj and inv.order_code:
                    ord_obj = db.query(OrderEntity).filter(OrderEntity.order_code == inv.order_code).first()

                if ord_obj:
                    if inv.remaining_debt == 0:
                        ord_obj.status = "PAID"
                    elif float(inv.paid_amount or 0.0) > 0 and ord_obj.status != "PAID":
                        ord_obj.status = "PARTIAL"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Chế độ đối trừ '{payload.allocation_mode}' không hợp lệ. Chỉ chấp nhận AUTO_FIFO hoặc MANUAL."
            )

        # Ghi vết Audit Log
        log_audit_event(
            db=db,
            user=current_user,
            action_type="DEBT_PAYMENT_CONFIRM",
            entity_type="PaymentReceipt",
            entity_id=receipt_code,
            old_val={},
            new_val={
                "receipt_code": receipt_code,
                "dealer_id": dealer.id,
                "dealer_code": dealer.code,
                "amount": payload.amount,
                "payment_method": payload.payment_method,
                "allocation_mode": payload.allocation_mode,
                "collector_name": collector,
            },
            reason=f"Ghi nhận thanh toán {receipt_code} cho đại lý {dealer.name} ({payload.amount:,.0f} đ)",
        )

        db.commit()
        db.refresh(receipt)
        return receipt

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi hệ thống khi ghi nhận thanh toán: {str(e)}"
        )


def get_payment_receipts_list(
    dealer_id: Optional[int],
    db: Session,
) -> List[Dict[str, Any]]:
    """
    Lấy danh sách các khoản thanh toán / phiếu thu nợ đã ghi nhận.
    """
    query = db.query(PaymentReceiptEntity).filter(PaymentReceiptEntity.status != "NO_HISTORY")
    if dealer_id:
        query = query.filter(PaymentReceiptEntity.dealer_id == dealer_id)

    receipts = query.order_by(desc(PaymentReceiptEntity.created_at)).all()
    results = []

    for r in receipts:
        alloc_list = []
        for a in r.allocations:
            inv = a.invoice
            alloc_list.append({
                "id": a.id,
                "payment_id": a.payment_id,
                "invoice_id": a.invoice_id,
                "invoice_code": inv.invoice_code if inv else None,
                "allocated_amount": float(a.allocated_amount or 0.0),
                "current_allocated_amount": float(a.current_allocated_amount or 0.0),
                "invoice_total": float(inv.total_amount or 0.0) if inv else None,
                "invoice_paid": float(inv.paid_amount or 0.0) if inv else None,
                "invoice_remaining": float(inv.remaining_debt or 0.0) if inv else None,
                "invoice_status": inv.status if inv else None,
                "created_at": a.created_at,
            })

        adj_list = []
        for adj in r.adjustments:
            adj_list.append({
                "id": adj.id,
                "adjustment_code": adj.adjustment_code,
                "payment_id": adj.payment_id,
                "adjusted_by_id": adj.adjusted_by_id,
                "adjusted_by_name": adj.adjusted_by_name,
                "adjustment_amount": float(adj.adjustment_amount or 0.0),
                "reason": adj.reason,
                "details_json": adj.details_json,
                "created_at": adj.created_at,
            })

        results.append({
            "id": r.id,
            "receipt_code": r.receipt_code,
            "dealer_id": r.dealer_id,
            "dealer_code": r.dealer_code,
            "dealer_name": r.dealer_name,
            "amount": float(r.amount or 0.0),
            "payment_method": r.payment_method,
            "payment_date": r.payment_date,
            "collector_id": r.collector_id,
            "collector_name": r.collector_name,
            "note": r.note,
            "allocation_mode": r.allocation_mode,
            "status": r.status,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "allocations": alloc_list,
            "adjustments": adj_list,
        })

    return results


def get_payment_receipt_detail(receipt_id: int, db: Session) -> Dict[str, Any]:
    """
    Lấy chi tiết một phiếu thu và toàn bộ phân bổ hoá đơn.
    """
    receipt = db.query(PaymentReceiptEntity).filter(PaymentReceiptEntity.id == receipt_id).first()
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu thu ID {receipt_id}."
        )

    alloc_list = []
    for a in receipt.allocations:
        inv = a.invoice
        alloc_list.append({
            "id": a.id,
            "payment_id": a.payment_id,
            "invoice_id": a.invoice_id,
            "invoice_code": inv.invoice_code if inv else None,
            "allocated_amount": float(a.allocated_amount or 0.0),
            "current_allocated_amount": float(a.current_allocated_amount or 0.0),
            "invoice_total": float(inv.total_amount or 0.0) if inv else None,
            "invoice_paid": float(inv.paid_amount or 0.0) if inv else None,
            "invoice_remaining": float(inv.remaining_debt or 0.0) if inv else None,
            "invoice_status": inv.status if inv else None,
            "created_at": a.created_at,
        })

    adj_list = []
    for adj in receipt.adjustments:
        adj_list.append({
            "id": adj.id,
            "adjustment_code": adj.adjustment_code,
            "payment_id": adj.payment_id,
            "adjusted_by_id": adj.adjusted_by_id,
            "adjusted_by_name": adj.adjusted_by_name,
            "adjustment_amount": float(adj.adjustment_amount or 0.0),
            "reason": adj.reason,
            "details_json": adj.details_json,
            "created_at": adj.created_at,
        })

    return {
        "id": receipt.id,
        "receipt_code": receipt.receipt_code,
        "dealer_id": receipt.dealer_id,
        "dealer_code": receipt.dealer_code,
        "dealer_name": receipt.dealer_name,
        "amount": float(receipt.amount or 0.0),
        "payment_method": receipt.payment_method,
        "payment_date": receipt.payment_date,
        "collector_id": receipt.collector_id,
        "collector_name": receipt.collector_name,
        "note": receipt.note,
        "allocation_mode": receipt.allocation_mode,
        "status": receipt.status,
        "created_at": receipt.created_at,
        "updated_at": receipt.updated_at,
        "allocations": alloc_list,
        "adjustments": adj_list,
    }


def create_payment_adjustment(
    receipt_id: int,
    payload: CreateAdjustmentRequest,
    current_user: UserResponse,
    db: Session,
) -> PaymentAdjustmentEntity:
    """
    TIÊU CHÍ 5: Đã ghi nhận thì không xoá, chỉ lập bút toán điều chỉnh.
    - BẮT BUỘC nhập lý do điều chỉnh (*).
    - Hoàn lại số tiền đã thu và khôi phục số còn nợ trên các hoá đơn liên quan.
    - Tự động cập nhật lại trạng thái hoá đơn (UNPAID / OVERDUE / PARTIALLY_PAID).
    - Chuyển trạng thái phiếu thu sang ADJUSTED.
    - Ghi nhận vào bảng payment_adjustments và Audit Log trong 1 Database Transaction.
    """
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lý do điều chỉnh là bắt buộc."
        )

    receipt = db.query(PaymentReceiptEntity).filter(PaymentReceiptEntity.id == receipt_id).first()
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu thu ID {receipt_id}."
        )

    if receipt.status == "ADJUSTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Khoản thanh toán này đã được lập bút toán điều chỉnh trước đó."
        )

    now_utc = get_utc_now()
    date_prefix = now_utc.strftime("%Y%m%d")
    today_count = db.query(func.count(PaymentAdjustmentEntity.id)).filter(
        PaymentAdjustmentEntity.adjustment_code.like(f"DC-{date_prefix}-%")
    ).scalar() or 0
    seq = today_count + 1
    adjustment_code = f"DC-{date_prefix}-{seq:03d}"

    while db.query(PaymentAdjustmentEntity).filter(PaymentAdjustmentEntity.adjustment_code == adjustment_code).first():
        seq += 1
        adjustment_code = f"DC-{date_prefix}-{seq:03d}"

    try:
        adjust_amount = 0.0
        revert_details = []

        # Hoàn lại số tiền đối trừ trên từng hoá đơn
        for alloc in receipt.allocations:
            cur_allocated = float(alloc.current_allocated_amount or 0.0)
            if cur_allocated > 0:
                inv = db.query(InvoiceEntity).filter(InvoiceEntity.id == alloc.invoice_id).first()
                if inv:
                    # Giảm số tiền đã thu trên hoá đơn
                    new_paid = max(0.0, float(inv.paid_amount or 0.0) - cur_allocated)
                    inv.paid_amount = new_paid
                    inv.remaining_debt = max(0.0, float(inv.total_amount or 0.0) - new_paid)

                    # Khôi phục trạng thái chuẩn
                    due = inv.due_date
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)

                    if new_paid == 0:
                        inv.status = "OVERDUE" if due < now_utc else "UNPAID"
                    elif inv.remaining_debt > 0.001:
                        inv.status = "PARTIALLY_PAID"
                    else:
                        inv.remaining_debt = 0.0
                        inv.status = "PAID"

                    ord_obj = None
                    if inv.order_id:
                        ord_obj = db.query(OrderEntity).filter(OrderEntity.id == inv.order_id).first()
                    if not ord_obj and inv.order_code:
                        ord_obj = db.query(OrderEntity).filter(OrderEntity.order_code == inv.order_code).first()

                    if ord_obj:
                        if new_paid == 0:
                            ord_obj.status = "CONFIRMED"
                        elif inv.remaining_debt > 0:
                            ord_obj.status = "PARTIAL"
                        else:
                            ord_obj.status = "PAID"

                    revert_details.append({
                        "invoice_id": inv.id,
                        "invoice_code": inv.invoice_code,
                        "reverted_amount": cur_allocated,
                        "new_paid_amount": inv.paid_amount,
                        "new_remaining_debt": inv.remaining_debt,
                        "new_status": inv.status,
                    })

                adjust_amount += cur_allocated
                alloc.current_allocated_amount = 0.0

        receipt.status = "ADJUSTED"

        user_name = current_user.full_name or current_user.username
        adjustment = PaymentAdjustmentEntity(
            adjustment_code=adjustment_code,
            payment_id=receipt.id,
            adjusted_by_id=current_user.id,
            adjusted_by_name=user_name,
            adjustment_amount=adjust_amount,
            reason=payload.reason.strip(),
            details_json=json.dumps(revert_details, ensure_ascii=False),
        )
        db.add(adjustment)

        # Ghi Audit Log
        log_audit_event(
            db=db,
            user=current_user,
            action_type="DEBT_PAYMENT_ADJUSTMENT",
            entity_type="PaymentReceipt",
            entity_id=receipt.receipt_code,
            old_val={"status": "CONFIRMED", "amount": receipt.amount},
            new_val={
                "status": "ADJUSTED",
                "adjustment_code": adjustment_code,
                "adjusted_amount": adjust_amount,
                "reason": payload.reason.strip(),
            },
            reason=f"Lập bút toán điều chỉnh {adjustment_code} cho phiếu thu {receipt.receipt_code}: {payload.reason.strip()}",
        )

        db.commit()
        db.refresh(adjustment)
        return adjustment

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi lập bút toán điều chỉnh: {str(e)}"
        )
