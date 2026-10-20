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
from sqlalchemy.orm import relationship, validates
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
    code = Column(String(50), nullable=True, index=True)
    return_code = Column(String(50), nullable=True, index=True)
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
    subtotal_amount = Column(Float, default=0.0, nullable=True)
    tax_amount = Column(Float, default=0.0, nullable=True)
    total_amount = Column(Float, default=0.0, nullable=True)
    total_refund_amount = Column(Float, default=0.0, nullable=True)
    total_items = Column(Integer, default=0, nullable=True)
    total_quantity = Column(Integer, default=0, nullable=True)
    created_by = Column(Unicode(100), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_name = Column(Unicode(150), nullable=True)
    confirmed_by = Column(Unicode(100), nullable=True)
    confirmed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_by_name = Column(Unicode(150), nullable=True)
    confirmed_at = Column(DateTime, default=get_utc_now)
    cancelled_by_id = Column(Integer, nullable=True)
    cancelled_by = Column(Unicode(100), nullable=True)
    cancelled_by_name = Column(Unicode(150), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(Unicode(500), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    def __init__(self, **kwargs):
        val_code = kwargs.get("code") or kwargs.get("return_code")
        if val_code:
            kwargs["code"] = val_code
            kwargs["return_code"] = val_code

        val_creator = kwargs.get("created_by") or kwargs.get("created_by_name")
        if val_creator:
            kwargs["created_by"] = val_creator
            kwargs["created_by_name"] = val_creator

        val_confirmer = kwargs.get("confirmed_by") or kwargs.get("confirmed_by_name")
        if val_confirmer:
            kwargs["confirmed_by"] = val_confirmer
            kwargs["confirmed_by_name"] = val_confirmer

        val_canceller = kwargs.get("cancelled_by") or kwargs.get("cancelled_by_name")
        if val_canceller:
            kwargs["cancelled_by"] = val_canceller
            kwargs["cancelled_by_name"] = val_canceller

        val_amount = kwargs.get("total_refund_amount") if kwargs.get("total_refund_amount") is not None else kwargs.get("total_amount")
        if val_amount is not None:
            kwargs["total_refund_amount"] = val_amount
            kwargs["total_amount"] = val_amount
            if "subtotal_amount" not in kwargs:
                kwargs["subtotal_amount"] = val_amount

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


from sqlalchemy import event


@event.listens_for(ReturnOrderEntity, "before_insert")
@event.listens_for(ReturnOrderEntity, "before_update")
def sync_return_order_fields(mapper, connection, target):
    code_val = target.code or target.return_code
    if code_val:
        target.code = code_val
        target.return_code = code_val

    creator_val = target.created_by or target.created_by_name
    if creator_val:
        target.created_by = creator_val
        target.created_by_name = creator_val

    confirmer_val = target.confirmed_by or target.confirmed_by_name
    if confirmer_val:
        target.confirmed_by = confirmer_val
        target.confirmed_by_name = confirmer_val

    canceller_val = target.cancelled_by or target.cancelled_by_name
    if canceller_val:
        target.cancelled_by = canceller_val
        target.cancelled_by_name = canceller_val

    amt = target.total_refund_amount if target.total_refund_amount is not None else target.total_amount
    if amt is not None:
        target.total_refund_amount = amt
        target.total_amount = amt
        if target.subtotal_amount is None or target.subtotal_amount == 0.0:
            target.subtotal_amount = amt


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
