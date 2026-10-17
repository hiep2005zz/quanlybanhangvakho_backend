# backend/app/models/invoice.py
from __future__ import annotations
from datetime import datetime, timezone
import json
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Unicode,
    UnicodeText,
    ForeignKey,
)
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class InvoiceEntity(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_code = Column(String(50), unique=True, index=True, nullable=False)
    order_id = Column(Integer, nullable=True, index=True)
    order_code = Column(String(50), nullable=True, index=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    issue_date = Column(DateTime, default=get_utc_now, nullable=False)
    due_date = Column(DateTime, nullable=False)
    total_amount = Column(Float, default=0.0, nullable=False)
    paid_amount = Column(Float, default=0.0, nullable=False)
    remaining_debt = Column(Float, default=0.0, nullable=False)
    status = Column(String(50), default="UNPAID", nullable=False)  # UNPAID, PARTIAL, PAID, OVERDUE
    items_json = Column(UnicodeText, nullable=True)
    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    @property
    def items(self):
        if self.items_json:
            try:
                res = json.loads(self.items_json)
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        return []

    @items.setter
    def items(self, val):
        self.items_json = json.dumps(val or [], ensure_ascii=False)
