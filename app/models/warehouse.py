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


class WarehouseZoneEntity(Base):
    """
    Khai báo danh mục Khu vực trong kho (Master Data: Zone).
    Ví dụ: Khu A - Thời trang, Khu B - Quần âu, Khu Hàng Nặng, Khu Đóng Gói...
    """
    __tablename__ = "warehouse_zones"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    zone_code = Column(String(50), nullable=False)   # vd: KHU-A, ZONE-01
    zone_name = Column(Unicode(100), nullable=False) # vd: Khu A - Thời trang nam
    description = Column(UnicodeText, nullable=True) # Mô tả phạm vi / loại hàng
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id])


class WarehouseRackEntity(Base):
    """
    Khai báo danh mục Kệ / Dãy kệ / Tầng trong kho (Master Data: Rack / Aisle).
    Ví dụ: Dãy A1, Dãy 02, Tầng 1, Tầng 2, Kệ 01...
    """
    __tablename__ = "warehouse_racks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    zone_id = Column(Integer, ForeignKey("warehouse_zones.id", ondelete="SET NULL"), nullable=True, index=True)
    rack_code = Column(String(50), nullable=False)  # vd: DAY-A1, KE-01, TANG-1
    rack_name = Column(Unicode(100), nullable=False) # vd: Dãy A1, Tầng 1, Kệ 01
    rack_type = Column(String(50), default="rack", nullable=False) # 'aisle' (Dãy), 'rack' (Tầng/Kệ), 'bin' (Ô)
    max_capacity = Column(Float, default=1000.0, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    warehouse = relationship("WarehouseEntity", foreign_keys=[warehouse_id])
    zone = relationship("WarehouseZoneEntity", foreign_keys=[zone_id])


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

