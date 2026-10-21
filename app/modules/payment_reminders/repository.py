from __future__ import annotations
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy import case, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import DealerEntity, OrderEntity
from app.models.invoice import InvoiceEntity
from app.modules.payment_reminders.config import (
    MAX_SEND_ATTEMPTS,
    REMINDER_DAYS_BEFORE_DUE,
    TIME_ZONE,
)
from app.modules.payment_reminders.models import PaymentReminderLog

REMINDER_STATUSES = {"UNPAID", "PARTIAL", "PARTIALLY_PAID", "OVERDUE"}
REMINDER_TYPES = {"DUE_SOON_3D", "OVERDUE"}
CHANNELS = {"email", "in_app"}
SMTP_CONFIGURATION_ERROR = "RuntimeError: SMTP is not configured"


def _smtp_is_configured() -> bool:
    return bool(settings.MAIL_SERVER and settings.MAIL_USERNAME and settings.MAIL_PASSWORD)


def _invoice_due_date(invoice: InvoiceEntity) -> date:
    due_date = invoice.due_date
    if isinstance(due_date, datetime):
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)
        return due_date.astimezone(TIME_ZONE).date()
    return due_date


def _dealer_is_inactive(dealer: DealerEntity) -> bool:
    if dealer.locked_at is not None:
        return True
    return str(dealer.status or "").strip().lower() in {
        "locked",
        "inactive",
        "cancelled",
        "canceled",
        "suspended",
        "tạm khóa",
        "đã khóa",
        "ngừng hoạt động",
    }


def invoice_is_eligible(
    db: Session,
    invoice: InvoiceEntity,
    reminder_type: str,
    today: date,
    overdue_repeat_daily: bool,
) -> bool:
    if reminder_type not in REMINDER_TYPES:
        return False
    if str(invoice.status or "").upper() not in REMINDER_STATUSES:
        return False
    if max(
        0.0,
        float(invoice.total_amount or 0.0) - float(invoice.paid_amount or 0.0),
    ) <= 0:
        return False

    dealer = db.get(DealerEntity, invoice.dealer_id)
    if dealer is None or _dealer_is_inactive(dealer):
        return False
    if invoice.order_id is not None:
        order = db.get(OrderEntity, invoice.order_id)
        if order is not None and str(order.status or "").upper() in {
            "CANCELLED",
            "REJECTED",
        }:
            return False

    due_date = _invoice_due_date(invoice)
    if reminder_type == "DUE_SOON_3D":
        return due_date == today + timedelta(days=REMINDER_DAYS_BEFORE_DUE)
    if overdue_repeat_daily:
        return due_date < today
    return due_date == today - timedelta(days=1)


def select_due_invoices(
    db: Session,
    today: date,
    *,
    overdue_repeat_daily: bool = False,
) -> list[tuple[InvoiceEntity, str]]:
    invoices = (
        db.query(InvoiceEntity)
        .filter(func.upper(InvoiceEntity.status).in_(REMINDER_STATUSES))
        .all()
    )
    selected = []
    for invoice in invoices:
        reminder_type = None
        if invoice_is_eligible(db, invoice, "DUE_SOON_3D", today, overdue_repeat_daily):
            reminder_type = "DUE_SOON_3D"
        elif invoice_is_eligible(db, invoice, "OVERDUE", today, overdue_repeat_daily):
            reminder_type = "OVERDUE"
        if reminder_type:
            selected.append((invoice, reminder_type))
    return selected


def claim_reminder(
    db: Session,
    invoice_id: int,
    agent_id: int,
    reminder_type: str,
    channel: str,
    reminder_date: date,
) -> int | None:
    if reminder_type not in REMINDER_TYPES or channel not in CHANNELS:
        raise ValueError("Unsupported payment reminder type or channel")

    log = PaymentReminderLog(
        invoice_id=invoice_id,
        agent_id=agent_id,
        reminder_type=reminder_type,
        channel=channel,
        reminder_date=reminder_date,
        status="pending",
        attempts=0,
    )
    db.add(log)
    try:
        db.flush()
        db.commit()
        return log.id
    except IntegrityError as exc:
        db.rollback()

    existing = (
        db.query(PaymentReminderLog.id)
        .filter(
            PaymentReminderLog.invoice_id == invoice_id,
            PaymentReminderLog.reminder_type == reminder_type,
            PaymentReminderLog.channel == channel,
            PaymentReminderLog.reminder_date == reminder_date,
        )
        .scalar()
    )
    if existing is None:
        raise exc

    updated = (
        db.query(PaymentReminderLog)
        .filter(
            PaymentReminderLog.invoice_id == invoice_id,
            PaymentReminderLog.reminder_type == reminder_type,
            PaymentReminderLog.channel == channel,
            PaymentReminderLog.reminder_date == reminder_date,
            PaymentReminderLog.status == "failed",
            or_(
                PaymentReminderLog.attempts < MAX_SEND_ATTEMPTS,
                (
                    (PaymentReminderLog.channel == "email")
                    & (PaymentReminderLog.error_message.like(f"{SMTP_CONFIGURATION_ERROR}%"))
                    & _smtp_is_configured()
                ),
            ),
        )
        .update(
            {
                PaymentReminderLog.status: "pending",
                PaymentReminderLog.error_message: None,
                PaymentReminderLog.attempts: case(
                    (
                        (PaymentReminderLog.attempts >= MAX_SEND_ATTEMPTS)
                        & (PaymentReminderLog.channel == "email")
                        & (PaymentReminderLog.error_message.like(f"{SMTP_CONFIGURATION_ERROR}%"))
                        & _smtp_is_configured(),
                        0,
                    ),
                    else_=PaymentReminderLog.attempts,
                ),
                PaymentReminderLog.updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        return None

    db.commit()
    return (
        db.query(PaymentReminderLog.id)
        .filter(
            PaymentReminderLog.invoice_id == invoice_id,
            PaymentReminderLog.reminder_type == reminder_type,
            PaymentReminderLog.channel == channel,
            PaymentReminderLog.reminder_date == reminder_date,
        )
        .scalar()
    )


def failed_reminders(db: Session, as_of_date: date) -> list[PaymentReminderLog]:
    return (
        db.query(PaymentReminderLog)
        .filter(
            PaymentReminderLog.reminder_date < as_of_date,
            PaymentReminderLog.status == "failed",
            or_(
                PaymentReminderLog.attempts < MAX_SEND_ATTEMPTS,
                (
                    (PaymentReminderLog.channel == "email")
                    & (PaymentReminderLog.error_message.like(f"{SMTP_CONFIGURATION_ERROR}%"))
                    & _smtp_is_configured()
                ),
            ),
        )
        .all()
    )


def get_eligible_invoice(
    db: Session,
    invoice_id: int,
    reminder_type: str,
    today: date,
    overdue_repeat_daily: bool,
) -> InvoiceEntity | None:
    invoice = db.get(InvoiceEntity, invoice_id)
    if invoice is None or not invoice_is_eligible(
        db, invoice, reminder_type, today, overdue_repeat_daily
    ):
        return None
    return invoice


def begin_attempt(db: Session, log_id: int) -> bool:
    updated = (
        db.query(PaymentReminderLog)
        .filter(
            PaymentReminderLog.id == log_id,
            PaymentReminderLog.status == "pending",
            PaymentReminderLog.attempts < MAX_SEND_ATTEMPTS,
        )
        .update(
            {
                PaymentReminderLog.attempts: PaymentReminderLog.attempts + 1,
                PaymentReminderLog.updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return updated == 1


def set_log_status(
    db: Session,
    log_id: int,
    status: str,
    *,
    error_message: str | None = None,
    commit: bool = True,
) -> None:
    if status not in {"sent", "failed", "skipped"}:
        raise ValueError(f"Unsupported payment reminder status: {status}")
    values = {
        PaymentReminderLog.status: status,
        PaymentReminderLog.error_message: error_message,
        PaymentReminderLog.updated_at: datetime.now(timezone.utc),
    }
    if status == "sent":
        values[PaymentReminderLog.sent_at] = datetime.now(timezone.utc)
    db.query(PaymentReminderLog).filter(PaymentReminderLog.id == log_id).update(
        values, synchronize_session=False
    )
    if commit:
        db.commit()
