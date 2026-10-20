from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    Unicode,
    UnicodeText,
    UniqueConstraint,
)

from app.core.database import Base


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentReminderLog(Base):
    __tablename__ = "payment_reminder_logs"
    __table_args__ = (
        UniqueConstraint(
            "invoice_id",
            "reminder_type",
            "channel",
            "reminder_date",
            name="uq_payment_reminder_once_per_day",
        ),
        Index("ix_prl_status_date", "reminder_date", "status"),
        Index("ix_prl_agent", "agent_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer, nullable=False)
    agent_id = Column(Integer, nullable=True)
    reminder_type = Column(String(20), nullable=False)
    channel = Column(String(10), nullable=False)
    reminder_date = Column(Date, nullable=False)
    status = Column(String(10), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=get_utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_utc_now,
        onupdate=get_utc_now,
    )


class PaymentReminderNotification(Base):
    __tablename__ = "payment_reminder_notifications"
    __table_args__ = (
        UniqueConstraint(
            "invoice_id",
            "reminder_type",
            "reminder_date",
            name="uq_payment_reminder_notification_once_per_day",
        ),
        Index("ix_prn_agent_created", "agent_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=False)
    invoice_id = Column(Integer, nullable=False)
    reminder_type = Column(String(20), nullable=False)
    reminder_date = Column(Date, nullable=False)
    title = Column(Unicode(255), nullable=False)
    body = Column(UnicodeText, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=get_utc_now)
