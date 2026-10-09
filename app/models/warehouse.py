# backend/app/models/warehouse.py
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
from app.models.goods_receipt import WarehouseEntity


def get_utc_now():
    return datetime.now(timezone.utc)


class WarehouseLocationEntity(Base):
    """
    Khai báo vị trí lưu trữ trong kho (Khu vực / Dãy kệ / Tầng kệ / Ô chứa).
    """
    __tablename__ = "warehouse_locations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    location_code = Column(String(50), nullable=False, index=True)  # vd: KE-A1-T1-O1, KHU-A
    location_name = Column(Unicode(255), nullable=True)            # vd: Khu A - Dãy A1 - Tầng 1
    zone = Column(Unicode(50), nullable=True)                      # Khu vực (vd: Khu A, Khu Hàng Nhẹ)
    aisle = Column(Unicode(50), nullable=True)                     # Dãy kệ (vd: Dãy A1, Dãy 02)
    rack = Column(Unicode(50), nullable=True)                      # Tầng/Kệ (vd: Tầng 1, Kệ 02)
    bin = Column(Unicode(50), nullable=True)                       # Ô chứa hàng (vd: Ô 01, Hộc B)
    max_capacity = Column(Float, default=1000.0, nullable=True)   # Sức chứa tối đa (số lượng hoặc kg)
    is_active = Column(Boolean, default=True, nullable=False)
    status = Column(Unicode(50), default="Đang sử dụng", nullable=True)  # Đang sử dụng | Tạm ngưng | Đầy
    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id], backref="locations")
    stocks = relationship("LocationStockEntity", back_populates="location", cascade="all, delete-orphan")


class LocationStockEntity(Base):
    """
    Số lượng hàng hóa của từng sản phẩm được gán tại từng vị trí lưu kho.
    Hỗ trợ 1 sản phẩm nằm ở nhiều vị trí hoặc 1 vị trí chứa nhiều sản phẩm.
    """
    __tablename__ = "warehouse_location_stocks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    location_id = Column(Integer, ForeignKey("warehouse_locations.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    quantity = Column(Integer, default=0, nullable=False)  # Số lượng tồn tại vị trí này
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id])
    location = relationship("WarehouseLocationEntity", back_populates="stocks")
    product = relationship("ProductEntity", foreign_keys=[product_id])
