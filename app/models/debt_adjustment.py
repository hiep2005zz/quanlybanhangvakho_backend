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


# Import ReturnOrderEntity and ReturnOrderItemEntity from app.models.return_order to avoid duplicate table definitions
from app.models.return_order import ReturnOrderEntity, ReturnOrderItemEntity
