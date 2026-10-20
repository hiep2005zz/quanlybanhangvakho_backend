# backend/app/models/return_order.py
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
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
    original_invoice_code = Column(String(50), nullable=True, default="", index=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    warehouse_id = Column(String(50), nullable=True)
    warehouse_name = Column(Unicode(255), nullable=True)
    status = Column(String(30), default="CONFIRMED", nullable=False)  # PENDING_WAREHOUSE | CONFIRMED | COMPLETED | CANCELLED
    reason = Column(Unicode(500), nullable=True, default="")
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
    def cancelled_by(self):
        return getattr(self, "cancelled_by_name", None) or getattr(self, "_cancelled_by", None)

    @cancelled_by.setter
    def cancelled_by(self, val):
        self._cancelled_by = val
        if hasattr(self, "cancelled_by_name"):
            try:
                self.cancelled_by_name = val
            except Exception:
                pass

    @property
    def cancelled_by_id(self):
        return getattr(self, "_cancelled_by_id", None)

    @cancelled_by_id.setter
    def cancelled_by_id(self, val):
        self._cancelled_by_id = val

    @property
    def cancelled_at(self):
        return getattr(self, "_cancelled_at", None)

    @cancelled_at.setter
    def cancelled_at(self, val):
        self._cancelled_at = val

    @property
    def cancel_reason(self):
        return getattr(self, "_cancel_reason", None)

    @cancel_reason.setter
    def cancel_reason(self, val):
        self._cancel_reason = val

    @property
    def total_refund_amount(self):
        return self.total_amount

    @total_refund_amount.setter
    def total_refund_amount(self, val):
        self.total_amount = val

    @property
    def total_items(self):
        return len(self.items) if self.items is not None and len(self.items) > 0 else getattr(self, "_total_items", 0)

    @total_items.setter
    def total_items(self, val):
        self._total_items = val

    @property
    def total_quantity(self):
        if self.items:
            return sum((getattr(it, "return_quantity", None) or getattr(it, "quantity", None) or 0) for it in self.items)
        return getattr(self, "_total_quantity", 0)

    @total_quantity.setter
    def total_quantity(self, val):
        self._total_quantity = val

    def __init__(self, **kwargs):
        if "code" in kwargs and "return_code" not in kwargs:
            kwargs["return_code"] = kwargs["code"]
        if "return_code" in kwargs and "code" not in kwargs:
            kwargs["code"] = kwargs["return_code"]
        if "created_by" in kwargs and "created_by_name" not in kwargs:
            kwargs["created_by_name"] = kwargs["created_by"]
        if "created_by_name" in kwargs and "created_by" not in kwargs:
            kwargs["created_by"] = kwargs["created_by_name"]
        if "confirmed_by" in kwargs and "confirmed_by_name" not in kwargs:
            kwargs["confirmed_by_name"] = kwargs["confirmed_by"]
        if "total_refund_amount" in kwargs and "total_amount" not in kwargs:
            kwargs["total_amount"] = kwargs["total_refund_amount"]
        if "total_amount" in kwargs and "total_refund_amount" not in kwargs:
            kwargs["total_refund_amount"] = kwargs["total_amount"]
        if "total_items" in kwargs:
            self._total_items = kwargs.pop("total_items")
        if "total_quantity" in kwargs:
            self._total_quantity = kwargs.pop("total_quantity")
        if "cancelled_by" in kwargs:
            self._cancelled_by = kwargs.pop("cancelled_by")
        if "cancelled_at" in kwargs:
            self._cancelled_at = kwargs.pop("cancelled_at")
        if "cancel_reason" in kwargs:
            self._cancel_reason = kwargs.pop("cancel_reason")
        if "cancelled_by_id" in kwargs:
            self._cancelled_by_id = kwargs.pop("cancelled_by_id")
        if "original_invoice_code" not in kwargs or kwargs["original_invoice_code"] is None:
            kwargs["original_invoice_code"] = ""
        if "reason" not in kwargs or kwargs["reason"] is None:
            kwargs["reason"] = kwargs.get("note", "") or ""
        super().__init__(**kwargs)

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
    order_item_id = Column(Integer, nullable=True, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    product_code = Column(String(50), nullable=True)
    product_name = Column(Unicode(255), nullable=False)
    unit_name = Column(Unicode(50), default="Cái", nullable=True)
    unit_price = Column(Float, default=0.0, nullable=False)
    original_quantity = Column(Integer, default=0, nullable=True)
    return_quantity = Column(Integer, default=1, nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    refund_amount = Column(Float, default=0.0, nullable=False)
    line_total = Column(Float, default=0.0, nullable=False)
    return_reason = Column(Unicode(255), nullable=True)
    reason = Column(Unicode(255), nullable=True)
    is_damaged = Column(Boolean, default=False, nullable=False)

    target_warehouse_id = Column(Integer, nullable=True)
    target_warehouse_code = Column(String(50), nullable=True)
    target_warehouse_name = Column(Unicode(255), nullable=True)

    batch_id = Column(Integer, nullable=True)
    batch_number = Column(String(100), nullable=True)

    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    def __init__(self, **kwargs):
        if "return_quantity" in kwargs and "quantity" not in kwargs:
            kwargs["quantity"] = kwargs["return_quantity"]
        elif "quantity" in kwargs and "return_quantity" not in kwargs:
            kwargs["return_quantity"] = kwargs["quantity"]
        if "refund_amount" in kwargs and "line_total" not in kwargs:
            kwargs["line_total"] = kwargs["refund_amount"]
        elif "line_total" in kwargs and "refund_amount" not in kwargs:
            kwargs["refund_amount"] = kwargs["line_total"]
        if "return_reason" in kwargs and "reason" not in kwargs:
            kwargs["reason"] = kwargs["return_reason"]
        elif "reason" in kwargs and "return_reason" not in kwargs:
            kwargs["return_reason"] = kwargs["reason"]
        if "original_quantity" not in kwargs:
            kwargs["original_quantity"] = kwargs.get("return_quantity", kwargs.get("quantity", 0))
        super().__init__(**kwargs)

    # Relationships
    return_order = relationship("ReturnOrderEntity", back_populates="items")
