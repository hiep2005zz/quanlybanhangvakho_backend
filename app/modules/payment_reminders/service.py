import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.entities import DealerEntity
from app.modules.payment_reminders import notifiers, repository
from app.modules.payment_reminders.config import (
    DRY_RUN,
    FEATURE_ENABLED,
    OVERDUE_REPEAT_DAILY,
    TIME_ZONE,
)

logger = logging.getLogger(__name__)
CHANNELS = ("in_app", "email")


def today_in_ict() -> date:
    return datetime.now(TIME_ZONE).date()


def _process_invoice_reminder(
    db: Session,
    invoice_id: int,
    reminder_type: str,
    reminder_date: date,
    summary: dict[str, int | bool],
    channels: tuple[str, ...] = CHANNELS,
) -> None:
    for channel in channels:
        invoice = repository.get_eligible_invoice(
            db, invoice_id, reminder_type, reminder_date, OVERDUE_REPEAT_DAILY
        )
        if invoice is None:
            logger.info(
                "Skipping %s reminder for invoice_id=%s: invoice is no longer eligible",
                reminder_type,
                invoice_id,
            )
            continue

        log_id = repository.claim_reminder(
            db,
            invoice.id,
            invoice.dealer_id,
            reminder_type,
            channel,
            reminder_date,
        )
        if log_id is None:
            continue

        dealer = db.get(DealerEntity, invoice.dealer_id)
        if channel == "email" and (dealer is None or not dealer.email or not dealer.email.strip()):
            repository.set_log_status(
                db, log_id, "skipped", error_message="Dealer email is not configured"
            )
            summary["skipped"] = int(summary["skipped"]) + 1
            continue

        if not repository.begin_attempt(db, log_id):
            continue

        try:
            if channel == "in_app":
                notifiers.create_in_app_notification(
                    db, invoice, reminder_type, reminder_date
                )
                repository.set_log_status(db, log_id, "sent", commit=False)
                db.commit()
            else:
                if dealer is None:
                    raise LookupError(f"Dealer {invoice.dealer_id} no longer exists")
                notifiers.send_email_reminder(dealer, invoice, reminder_type)
                repository.set_log_status(db, log_id, "sent")
            summary["sent"] = int(summary["sent"]) + 1
        except Exception as exc:
            db.rollback()
            error_message = f"{type(exc).__name__}: {exc}"[:1000]
            repository.set_log_status(
                db, log_id, "failed", error_message=error_message
            )
            logger.exception(
                "Payment reminder delivery failed: invoice_id=%s type=%s channel=%s",
                invoice_id,
                reminder_type,
                channel,
            )
            summary["failed"] = int(summary["failed"]) + 1


def run_daily_reminders(
    db: Session | None = None,
    *,
    today: date | None = None,
    dry_run: bool | None = None,
) -> dict[str, int | bool]:
    is_dry_run = DRY_RUN if dry_run is None else dry_run
    summary: dict[str, int | bool] = {
        "candidates": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": is_dry_run,
        "disabled": not FEATURE_ENABLED,
    }
    if not FEATURE_ENABLED:
        logger.info("Payment reminders are disabled")
        return summary

    owns_session = db is None
    session = db or SessionLocal()
    reminder_date = today or today_in_ict()
    try:
        failed_reminders = (
            [] if is_dry_run else repository.failed_reminders(session, reminder_date)
        )
        for log in failed_reminders:
            invoice = repository.get_eligible_invoice(
                session,
                log.invoice_id,
                log.reminder_type,
                log.reminder_date,
                OVERDUE_REPEAT_DAILY,
            )
            if invoice is None:
                continue
            _process_invoice_reminder(
                session,
                log.invoice_id,
                log.reminder_type,
                log.reminder_date,
                summary,
                channels=(log.channel,),
            )

        due_invoices = repository.select_due_invoices(
            session,
            reminder_date,
            overdue_repeat_daily=OVERDUE_REPEAT_DAILY,
        )
        summary["candidates"] = len(due_invoices)
        if is_dry_run:
            for invoice, reminder_type in due_invoices:
                logger.info(
                    "DRY_RUN candidate invoice=%s reminder_type=%s",
                    invoice.invoice_code,
                    reminder_type,
                )
            return summary

        for invoice, reminder_type in due_invoices:
            _process_invoice_reminder(
                session, invoice.id, reminder_type, reminder_date, summary
            )
        logger.info("Payment reminder run completed: %s", summary)
        return summary
    finally:
        if owns_session:
            session.close()
