# backend/app/models/debt_payment.py
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


class PaymentReceiptEntity(Base):
    """
    S7-02: Bảng lưu khoản thanh toán / phiếu thu nợ từ đại lý.
    Tiêu chí 1: Mã phiếu thu tự sinh duy nhất (PT-YYYYMMDD-XXX), đại lý, số tiền,
    hình thức thu (Tiền mặt, Chuyển khoản, Khác), ngày thu, người thu, ghi chú,
    chế độ đối trừ (AUTO_FIFO, MANUAL), trạng thái (CONFIRMED, ADJUSTED).
    """
    __tablename__ = "payment_receipts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    receipt_code = Column(String(50), unique=True, index=True, nullable=False)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_code = Column(String(50), nullable=True)
    dealer_name = Column(Unicode(255), nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    payment_method = Column(Unicode(50), default="Tiền mặt", nullable=False)  # Tiền mặt, Chuyển khoản, Khác
    payment_date = Column(DateTime, nullable=False, default=get_utc_now)
    collector_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    collector_name = Column(Unicode(150), nullable=False)
    note = Column(UnicodeText, nullable=True)
    allocation_mode = Column(String(20), default="AUTO_FIFO", nullable=False)  # AUTO_FIFO | MANUAL
    status = Column(String(20), default="CONFIRMED", nullable=False)  # CONFIRMED | ADJUSTED
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    # Quan hệ
    dealer = relationship("DealerEntity", foreign_keys=[dealer_id])
    collector = relationship("UserEntity", foreign_keys=[collector_id])
    allocations = relationship("PaymentAllocationEntity", back_populates="receipt", cascade="all, delete-orphan")
    adjustments = relationship("PaymentAdjustmentEntity", back_populates="receipt", cascade="all, delete-orphan")


class PaymentAllocationEntity(Base):
    """
    S7-02: Bảng đối trừ chi tiết vào từng hoá đơn.
    Tiêu chí 3: Lưu chi tiết số tiền đã đối trừ vào từng hoá đơn trong bảng liên kết.
    """
    __tablename__ = "payment_allocations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    payment_id = Column(Integer, ForeignKey("payment_receipts.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    allocated_amount = Column(Float, nullable=False, default=0.0)
    current_allocated_amount = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=get_utc_now)

    # Quan hệ
    receipt = relationship("PaymentReceiptEntity", back_populates="allocations")
    invoice = relationship("InvoiceEntity", foreign_keys=[invoice_id])


class PaymentAdjustmentEntity(Base):
    """
    S7-02: Bảng lưu vết bút toán điều chỉnh phiếu thu.
    Tiêu chí 5: Đã ghi nhận thì không xoá, chỉ lập bút toán điều chỉnh.
    """
    __tablename__ = "payment_adjustments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    adjustment_code = Column(String(50), unique=True, index=True, nullable=False)
    payment_id = Column(Integer, ForeignKey("payment_receipts.id"), nullable=False, index=True)
    adjusted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    adjusted_by_name = Column(Unicode(150), nullable=False)
    adjustment_amount = Column(Float, nullable=False, default=0.0)
    reason = Column(UnicodeText, nullable=False)  # Bắt buộc có lý do điều chỉnh
    details_json = Column(UnicodeText, nullable=True)  # Chi tiết điều chỉnh cho từng invoice
    created_at = Column(DateTime, default=get_utc_now)

    # Quan hệ
    receipt = relationship("PaymentReceiptEntity", back_populates="adjustments")
