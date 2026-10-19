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
    Index,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class ReturnOrderEntity(Base):
    """
    Bảng Phiếu trả hàng từ đại lý (Sales Return / RMA)
    Quy trình 2 bước:
      - Nhân viên kinh doanh tạo phiếu -> status = 'PENDING_WAREHOUSE' (Chưa cộng tồn kho)
      - Quản lý kho / Thủ kho xác nhận nhận hàng -> status = 'COMPLETED' (Cộng tồn kho)
      - Có thể hủy phiếu khi còn ở PENDING_WAREHOUSE -> status = 'CANCELLED'
    """
    __tablename__ = "return_orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)  # Ví dụ: RMA00001
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    order_code = Column(String(50), nullable=False, index=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    status = Column(String(30), default="PENDING_WAREHOUSE", nullable=False, index=True)
    # Trạng thái: PENDING_WAREHOUSE (Chờ kho xác nhận) | COMPLETED (Đã nhập kho) | CANCELLED (Đã hủy)

    total_items = Column(Integer, default=0, nullable=False)
    total_quantity = Column(Integer, default=0, nullable=False)
    total_refund_amount = Column(Float, default=0.0, nullable=False)
    note = Column(UnicodeText, nullable=True)

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Unicode(100), nullable=False)
    created_at = Column(DateTime, default=get_utc_now, index=True)

    confirmed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_by = Column(Unicode(100), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)

    cancelled_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_by = Column(Unicode(100), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(UnicodeText, nullable=True)

    # Relationships
    order = relationship("OrderEntity", foreign_keys=[order_id])
    dealer = relationship("DealerEntity", foreign_keys=[dealer_id])
    items = relationship("ReturnOrderItemEntity", back_populates="return_order", cascade="all, delete-orphan")


class ReturnOrderItemEntity(Base):
    """
    Bảng Chi tiết dòng hàng trong Phiếu trả hàng.
    """
    __tablename__ = "return_order_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    return_order_id = Column(Integer, ForeignKey("return_orders.id"), nullable=False, index=True)
    order_item_id = Column(Integer, nullable=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    product_code = Column(String(50), nullable=True, index=True)
    product_name = Column(Unicode(255), nullable=False)
    unit_name = Column(Unicode(50), default="Cái", nullable=False)
    unit_price = Column(Float, default=0.0, nullable=False)
    original_quantity = Column(Integer, default=0, nullable=False)  # Số lượng xuất trên đơn gốc
    return_quantity = Column(Integer, nullable=False, default=1)     # Số lượng trả lần này
    refund_amount = Column(Float, default=0.0, nullable=False)       # = return_quantity * unit_price
    return_reason = Column(Unicode(255), nullable=False)             # Lý do trả hàng (bắt buộc)
    is_damaged = Column(Boolean, default=False, nullable=False)      # Cờ phân loại hàng lỗi/hư hỏng

    # Điều hướng kho nhận lại:
    target_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
    target_warehouse_code = Column(String(50), nullable=True)
    target_warehouse_name = Column(Unicode(255), nullable=True)

    # Quản lý Lô nếu SKU có quản lý lô:
    batch_id = Column(Integer, ForeignKey("product_batches.id"), nullable=True)
    batch_number = Column(String(100), nullable=True)

    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    # Relationships
    return_order = relationship("ReturnOrderEntity", back_populates="items")
    product = relationship("ProductEntity", foreign_keys=[product_id])
    target_warehouse = relationship("WarehouseEntity", foreign_keys=[target_warehouse_id])
    batch = relationship("ProductBatchEntity", foreign_keys=[batch_id])
