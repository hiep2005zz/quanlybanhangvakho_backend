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
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
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

    # Các trường mở rộng phục vụ xuất hóa đơn bán hàng từ phiếu giao hàng
    source_type = Column(String(50), default="ORDER", nullable=False)  # 'ORDER' | 'DELIVERY_NOTES'
    issue_status = Column(String(30), default="ISSUED", nullable=False)  # 'DRAFT' | 'ISSUED' | 'CANCELLED' | 'ADJUSTED'
    template_code = Column(String(50), default="1/001", nullable=True)  # Mẫu số hóa đơn
    invoice_series = Column(String(20), default="C26TAA", nullable=True)  # Ký hiệu hóa đơn
    subtotal_amount = Column(Float, default=0.0, nullable=False)  # Tổng tiền trước thuế
    vat_amount = Column(Float, default=0.0, nullable=False)  # Tiền thuế GTGT
    discount_amount = Column(Float, default=0.0, nullable=False)  # Tổng chiết khấu
    issued_by = Column(Unicode(100), nullable=True)  # Người lập / Người phát hành
    delivery_dispatches_summary = Column(UnicodeText, nullable=True)  # Danh sách mã phiếu xuất kho nguồn
    currency = Column(String(10), default="VND", nullable=False)

    delivery_notes = relationship("InvoiceDeliveryNoteEntity", backref="invoice", cascade="all, delete-orphan")
    invoice_items = relationship("InvoiceItemEntity", backref="invoice", cascade="all, delete-orphan")
    adjustments = relationship("InvoiceAdjustmentEntity", backref="invoice", cascade="all, delete-orphan")

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


class InvoiceDeliveryNoteEntity(Base):
    """Bảng liên kết giữa Hóa đơn bán hàng và các Phiếu xuất kho / Phiếu giao hàng nguồn"""
    __tablename__ = "invoice_delivery_notes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    delivery_dispatch_id = Column(Integer, ForeignKey("order_dispatches.id"), nullable=False, unique=True, index=True)  # Khóa chặn xuất trùng phiếu!
    dispatch_code = Column(String(50), nullable=False, index=True)
    order_id = Column(Integer, nullable=True)
    order_code = Column(String(50), nullable=False)
    delivered_quantity = Column(Float, default=0.0, nullable=False)
    total_amount = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=get_utc_now)


class InvoiceItemEntity(Base):
    """Bảng chi tiết dòng hàng hóa của Hóa đơn bán hàng tính theo số lượng thực giao"""
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    product_code = Column(String(50), nullable=True)
    product_name = Column(Unicode(255), nullable=False)
    unit_name = Column(Unicode(50), default="Cái")
    quantity = Column(Float, default=1.0, nullable=False)  # Số lượng thực giao gộp
    unit_price = Column(Float, default=0.0, nullable=False)  # Đơn giá bán
    discount_amount = Column(Float, default=0.0, nullable=False)  # Chiết khấu
    subtotal = Column(Float, default=0.0, nullable=False)  # Thành tiền trước thuế
    vat_rate = Column(Float, default=0.1, nullable=False)  # Thuế suất (0.1 = 10%)
    vat_amount = Column(Float, default=0.0, nullable=False)  # Tiền thuế
    total_amount = Column(Float, default=0.0, nullable=False)  # Tổng tiền sau thuế
    created_at = Column(DateTime, default=get_utc_now)


class InvoiceAdjustmentEntity(Base):
    """Bảng lưu chứng từ điều chỉnh hóa đơn đã phát hành (Bất biến hóa đơn gốc)"""
    __tablename__ = "invoice_adjustments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    adjustment_code = Column(String(50), unique=True, index=True, nullable=False)  # DC-202610-0001
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    invoice_code = Column(String(50), nullable=False, index=True)
    adjustment_type = Column(String(30), nullable=False)  # 'INCREASE' (Tăng) | 'DECREASE' (Giảm) | 'INFO' (Thông tin)
    reason = Column(Unicode(500), nullable=False)
    difference_subtotal = Column(Float, default=0.0, nullable=False)  # Chênh lệch tiền trước thuế
    difference_vat = Column(Float, default=0.0, nullable=False)       # Chênh lệch tiền thuế
    difference_total = Column(Float, default=0.0, nullable=False)     # Tổng chênh lệch thanh toán
    details_json = Column(UnicodeText, nullable=True)
    created_by = Column(Unicode(100), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

