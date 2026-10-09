# backend/app/models/goods_receipt.py
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


class WarehouseEntity(Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    address = Column(Unicode(500), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=get_utc_now)


class UnitOfMeasureEntity(Base):
    __tablename__ = "units_of_measure"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(100), nullable=False)
    description = Column(Unicode(255), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)


class GoodsReceiptNoteEntity(Base):
    __tablename__ = "goods_receipt_notes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    reference_number = Column(Unicode(100), nullable=True)  # Số hóa đơn / phiếu giao hàng
    receipt_date = Column(DateTime, nullable=False, default=get_utc_now)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    status = Column(String(20), default="DRAFT", nullable=False)  # DRAFT, CONFIRMED, CANCELLED
    note = Column(UnicodeText, nullable=True)
    total_items = Column(Integer, default=0)
    total_quantity = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)
    created_by = Column(Unicode(100), nullable=True)
    confirmed_by = Column(Unicode(100), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    supplier = relationship("SupplierEntity", foreign_keys=[supplier_id])
    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id])
    items = relationship("GoodsReceiptNoteItemEntity", back_populates="receipt_note", cascade="all, delete-orphan")


class GoodsReceiptNoteItemEntity(Base):
    __tablename__ = "goods_receipt_note_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    receipt_note_id = Column(Integer, ForeignKey("goods_receipt_notes.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    uom_id = Column(Integer, ForeignKey("units_of_measure.id"), nullable=True)
    unit_name = Column(Unicode(50), nullable=False)  # Đơn vị tính lúc nhập (vd: Thùng, Lốc, Cái)
    quantity = Column(Float, nullable=False)  # Số lượng nhập theo ĐVT
    conversion_rate = Column(Float, default=1.0, nullable=False)  # Tỷ lệ quy đổi ra ĐVT cơ sở
    base_quantity = Column(Float, nullable=False)  # Số lượng theo ĐVT cơ sở = quantity * conversion_rate
    unit_price = Column(Float, default=0.0)
    batch_number = Column(String(100), nullable=True)  # Số lô (nếu sản phẩm quản lý lô)
    expiry_date = Column(DateTime, nullable=True)  # Hạn sử dụng (nếu quản lý lô)
    note = Column(UnicodeText, nullable=True)

    receipt_note = relationship("GoodsReceiptNoteEntity", back_populates="items")
    product = relationship("ProductEntity", foreign_keys=[product_id])
    uom = relationship("UnitOfMeasureEntity", foreign_keys=[uom_id])


class ProductBatchEntity(Base):
    __tablename__ = "product_batches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    batch_number = Column(String(100), nullable=False, index=True)
    expiry_date = Column(DateTime, nullable=True)
    quantity = Column(Float, default=0.0, nullable=False)  # Tồn kho theo lô
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    product = relationship("ProductEntity", foreign_keys=[product_id])
    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id])


class InventoryLedgerEntity(Base):
    __tablename__ = "inventory_ledgers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    receipt_note_id = Column(Integer, ForeignKey("goods_receipt_notes.id"), nullable=True)
    reference_code = Column(String(50), nullable=True)  # Mã chứng từ GRN
    transaction_type = Column(String(50), default="RECEIPT")  # RECEIPT, ISSUE, ADJUST
    quantity = Column(Float, nullable=False)  # Biến động tồn cơ sở (+ nhập, - xuất)
    previous_stock = Column(Float, default=0.0)
    new_stock = Column(Float, default=0.0)
    batch_number = Column(String(100), nullable=True)
    performed_by = Column(Unicode(100), nullable=True)
    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    product = relationship("ProductEntity", foreign_keys=[product_id])
    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id])
