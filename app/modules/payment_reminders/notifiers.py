import smtplib
import ssl
from datetime import date, datetime, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import DealerEntity
from app.models.invoice import InvoiceEntity
from app.modules.payment_reminders.models import PaymentReminderNotification
from app.modules.payment_reminders.config import REMINDER_DAYS_BEFORE_DUE
from app.modules.payment_reminders.config import TIME_ZONE

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _due_date_text(invoice: InvoiceEntity) -> str:
    due_date = invoice.due_date
    if isinstance(due_date, datetime):
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)
        due_date = due_date.astimezone(TIME_ZONE).date()
    return due_date.strftime("%d/%m/%Y")


def _reminder_content(
    invoice: InvoiceEntity,
    reminder_type: str,
) -> tuple[str, str]:
    due_date_text = _due_date_text(invoice)
    remaining_debt = max(
        0.0,
        float(invoice.total_amount or 0.0) - float(invoice.paid_amount or 0.0),
    )
    amount_text = f"{remaining_debt:,.0f}".replace(",", ".") + " đ"
    if reminder_type == "DUE_SOON_3D":
        title = f"Hóa đơn #{invoice.invoice_code} sắp đến hạn thanh toán"
        body = (
            f"Hóa đơn còn {REMINDER_DAYS_BEFORE_DUE} ngày đến hạn (hạn: {due_date_text}). "
            f"Số tiền còn nợ: {amount_text}."
        )
    else:
        title = f"Hóa đơn #{invoice.invoice_code} đã quá hạn thanh toán"
        body = (
            f"Hóa đơn đã quá hạn từ {due_date_text}. "
            f"Số tiền còn nợ: {amount_text}. Đơn hàng có thể bị khóa."
        )
    return title, body


def create_in_app_notification(
    db: Session,
    invoice: InvoiceEntity,
    reminder_type: str,
    reminder_date: date,
) -> None:
    title, body = _reminder_content(invoice, reminder_type)
    db.add(
        PaymentReminderNotification(
            agent_id=invoice.dealer_id,
            invoice_id=invoice.id,
            reminder_type=reminder_type,
            reminder_date=reminder_date,
            title=title,
            body=body,
            is_read=False,
        )
    )


def send_email_reminder(
    dealer: DealerEntity,
    invoice: InvoiceEntity,
    reminder_type: str,
) -> None:
    if not dealer.email or not dealer.email.strip():
        raise ValueError("Dealer email is not configured")
    if not settings.MAIL_SERVER or not settings.MAIL_USERNAME or not settings.MAIL_PASSWORD:
        raise RuntimeError("SMTP is not configured; payment reminder email was not sent")

    title, body = _reminder_content(invoice, reminder_type)
    template_name = (
        "due_soon_3d.html" if reminder_type == "DUE_SOON_3D" else "overdue.html"
    )
    template = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    payment_url = settings.FRONTEND_URL.rstrip("/") + "/agency-debts"
    values = {
        "invoice_code": escape(invoice.invoice_code),
        "due_date": escape(_due_date_text(invoice)),
        "remaining_debt": escape(
            f"{max(0.0, float(invoice.total_amount or 0.0) - float(invoice.paid_amount or 0.0)):,.0f}".replace(
                ",", "."
            )
            + " đ"
        ),
        "payment_url": escape(payment_url, quote=True),
        "title": escape(title),
        "message": escape(body),
    }
    html_body = template
    for key, value in values.items():
        html_body = html_body.replace("{{" + key + "}}", value)

    message = EmailMessage()
    message["Subject"] = title
    message["From"] = settings.MAIL_FROM or settings.MAIL_USERNAME
    message["To"] = dealer.email.strip()
    message.set_content(f"{body}\n\nXem hóa đơn: {payment_url}")
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT, timeout=15) as server:
        if settings.MAIL_TLS:
            server.starttls(context=ssl.create_default_context())
        server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
        server.send_message(message)
