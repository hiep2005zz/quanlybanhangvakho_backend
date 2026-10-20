from __future__ import annotations
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.api.v1.endpoints.agency_debts import _resolve_authenticated_dealer
from app.core.database import get_db
from app.models.invoice import InvoiceEntity
from app.modules.payment_reminders.models import PaymentReminderNotification
from app.modules.payment_reminders.scheduler import scheduler_status
from app.schemas.auth import UserResponse

router = APIRouter(
    dependencies=[Depends(require_roles(["customer"]))],
)


@router.get("/schedule")
def get_payment_reminder_schedule():
    return scheduler_status()


@router.get("")
def list_payment_reminders(
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dealer = _resolve_authenticated_dealer(current_user, db)
    notifications = (
        db.query(PaymentReminderNotification)
        .filter(PaymentReminderNotification.agent_id == dealer.id)
        .order_by(
            PaymentReminderNotification.created_at.desc(),
            PaymentReminderNotification.id.desc(),
        )
        .limit(50)
        .all()
    )
    result = []
    for notification in notifications:
        invoice = db.get(InvoiceEntity, notification.invoice_id)
        created_at = notification.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        result.append(
            {
                "id": notification.id,
                "invoice_id": notification.invoice_id,
                "invoice_code": invoice.invoice_code if invoice else None,
                "reminder_type": notification.reminder_type,
                "title": notification.title,
                "body": notification.body,
                "is_read": notification.is_read,
                "created_at": created_at.isoformat(),
            }
        )
    return {"items": result, "unread_count": sum(not item["is_read"] for item in result)}


@router.patch("/{notification_id}/read")
def mark_payment_reminder_read(
    notification_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dealer = _resolve_authenticated_dealer(current_user, db)
    notification = (
        db.query(PaymentReminderNotification)
        .filter(
            PaymentReminderNotification.id == notification_id,
            PaymentReminderNotification.agent_id == dealer.id,
        )
        .first()
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông báo.",
        )
    notification.is_read = True
    db.commit()
    return {"id": notification.id, "is_read": True}
