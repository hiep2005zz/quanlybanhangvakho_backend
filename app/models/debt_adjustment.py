# backend/app/models/debt_adjustment.py
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


class DebtAdjustmentEntity(Base):
    """
    Bảng debt_adjustments: Chứng từ điều chỉnh giảm công nợ khi trả hàng (Credit Note).
    Lưu thông tin chứng từ điều chỉnh độc lập, liên kết chặt chẽ với Hóa đơn gốc và Phiếu trả hàng.
    """
    __tablename__ = "debt_adjustments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    adjustment_code = Column(String(50), unique=True, index=True, nullable=False)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    return_order_id = Column(Integer, nullable=True, index=True)
    return_order_code = Column(String(50), index=True, nullable=False)
    original_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    original_invoice_code = Column(String(50), index=True, nullable=False)
    adjustment_date = Column(DateTime, default=get_utc_now, nullable=False)
    subtotal_amount = Column(Float, default=0.0, nullable=False)
    tax_amount = Column(Float, default=0.0, nullable=False)
    total_adjusted_amount = Column(Float, default=0.0, nullable=False)
    status = Column(String(30), default="POSTED", nullable=False)  # POSTED (Đã ghi sổ) | CANCELLED
    reason = Column(Unicode(500), nullable=False)
    note = Column(Unicode(500), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_name = Column(Unicode(150), nullable=False)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    # Relationships
    items = relationship(
        "DebtAdjustmentItemEntity",
        back_populates="adjustment",
        cascade="all, delete-orphan",
        order_by="DebtAdjustmentItemEntity.id",
    )


class DebtAdjustmentItemEntity(Base):
    """
    Bảng debt_adjustment_items: Chi tiết mặt hàng điều chỉnh giảm nợ.
    Đảm bảo từng mặt hàng trả lại được định giá đúng theo hóa đơn gốc.
    """
    __tablename__ = "debt_adjustment_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    adjustment_id = Column(Integer, ForeignKey("debt_adjustments.id"), nullable=False, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    product_code = Column(String(50), nullable=False)
    product_name = Column(Unicode(255), nullable=False)
    unit_name = Column(Unicode(50), nullable=True)
    return_quantity = Column(Integer, default=1, nullable=False)
    original_unit_price = Column(Float, default=0.0, nullable=False)
    line_total = Column(Float, default=0.0, nullable=False)

    # Relationships
    adjustment = relationship("DebtAdjustmentEntity", back_populates="items")


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
    status = Column(String(30), default="CONFIRMED", nullable=False)  # CONFIRMED | COMPLETED | CANCELLED
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

    # Relationships
    return_order = relationship("ReturnOrderEntity", back_populates="items")
