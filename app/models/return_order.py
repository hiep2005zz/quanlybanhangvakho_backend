# backend/app/models/return_order.py
from __future__ import annotations
from datetime import datetime, timezone
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
from sqlalchemy.orm import relationship
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class ReturnOrderEntity(Base):
    """
    Bảng return_orders: Phiếu trả hàng / đổi trả (Sales Return).
    Ghi nhận hàng trả lại từ khách hàng / đại lý đã xác nhận nhập hoàn kho.
    """
    __tablename__ = "return_orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    return_code = Column(String(50), unique=True, index=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    order_code = Column(String(50), nullable=False, index=True)
    original_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    original_invoice_code = Column(String(50), nullable=False, index=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    warehouse_id = Column(String(50), nullable=True)
    warehouse_name = Column(Unicode(255), nullable=True)
    status = Column(String(30), default="CONFIRMED", nullable=False)  # PENDING_WAREHOUSE | CONFIRMED | COMPLETED | CANCELLED
    reason = Column(Unicode(500), nullable=False)
    note = Column(Unicode(500), nullable=True)
    subtotal_amount = Column(Float, default=0.0, nullable=False)
    tax_amount = Column(Float, default=0.0, nullable=False)
    total_amount = Column(Float, default=0.0, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_name = Column(Unicode(150), nullable=True)
    confirmed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_by_name = Column(Unicode(150), nullable=True)
    confirmed_at = Column(DateTime, default=get_utc_now)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    # Backward compatibility properties
    @property
    def code(self):
        return self.return_code

    @code.setter
    def code(self, val):
        self.return_code = val

    @property
    def created_by(self):
        return self.created_by_name

    @created_by.setter
    def created_by(self, val):
        self.created_by_name = val

    @property
    def confirmed_by(self):
        return self.confirmed_by_name

    @confirmed_by.setter
    def confirmed_by(self, val):
        self.confirmed_by_name = val

    @property
    def total_refund_amount(self):
        return self.total_amount

    @total_refund_amount.setter
    def total_refund_amount(self, val):
        self.total_amount = val

    @property
    def total_items(self):
        return len(self.items) if self.items else 0

    @property
    def total_quantity(self):
        return sum(it.quantity for it in self.items) if self.items else 0

    # Relationships
    items = relationship(
        "ReturnOrderItemEntity",
        back_populates="return_order",
        cascade="all, delete-orphan",
        order_by="ReturnOrderItemEntity.id",
    )


class ReturnOrderItemEntity(Base):
    """
    Bảng return_order_items: Chi tiết mặt hàng trong Phiếu trả hàng.
    """
    __tablename__ = "return_order_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    return_order_id = Column(Integer, ForeignKey("return_orders.id"), nullable=False, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    product_code = Column(String(50), nullable=False)
    product_name = Column(Unicode(255), nullable=False)
    unit_name = Column(Unicode(50), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Float, default=0.0, nullable=False)
    line_total = Column(Float, default=0.0, nullable=False)
    reason = Column(Unicode(255), nullable=True)

    # Backward compatibility properties
    @property
    def return_quantity(self):
        return self.quantity

    @return_quantity.setter
    def return_quantity(self, val):
        self.quantity = val

    @property
    def original_quantity(self):
        return self.quantity

    @original_quantity.setter
    def original_quantity(self, val):
        pass

    @property
    def refund_amount(self):
        return self.line_total

    @refund_amount.setter
    def refund_amount(self, val):
        self.line_total = val

    @property
    def return_reason(self):
        return self.reason

    @return_reason.setter
    def return_reason(self, val):
        self.reason = val

    # Relationships
    return_order = relationship("ReturnOrderEntity", back_populates="items")
