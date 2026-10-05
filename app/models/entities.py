# backend/app/models/entities.py
from datetime import datetime, timezone
import json
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    Text,
    Unicode,
    UnicodeText,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from app.core.database import Base

def get_utc_now():
    return datetime.now(timezone.utc)

class UserEntity(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    full_name = Column(Unicode(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    role = Column(String(50), nullable=False)
    roles_json = Column(UnicodeText, nullable=True)  # JSON string lưu danh sách roles
    hashed_password = Column(String(255), nullable=False)
    branch = Column(Unicode(100), default="Kho Tổng Hà Nội")
    is_active = Column(Boolean, default=True)
    status = Column(String(20), default="ACTIVE")  # ACTIVE | LOCKED
    lock_reason = Column(Unicode(500), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    failed_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    token_version = Column(Integer, default=1)
    avatar_url = Column(Unicode(500), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    @property
    def roles(self) -> list[str]:
        if self.roles_json:
            try:
                res = json.loads(self.roles_json)
                if isinstance(res, str):
                    res = json.loads(res)
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        return [self.role] if self.role else []

    @roles.setter
    def roles(self, val: list[str]):
        self.roles_json = json.dumps(val or [])

    def get_roles(self) -> list[str]:
        r = self.roles
        if r and len(r) > 0:
            return r
        return [self.role] if self.role else []


class CategoryEntity(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(Unicode(255), nullable=False, unique=True)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    description = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    sub_categories = relationship("CategoryEntity", backref="parent", remote_side=[id])

class ProductEntity(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    category = Column(Unicode(100), default="Thời trang")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    stock = Column(Integer, default=0)
    cost_price = Column(Float, default=0.0)
    sell_price = Column(Float, default=0.0)
    base_unit = Column(Unicode(50), default="Cái")
    units_json = Column(UnicodeText, nullable=True)  # JSON string lưu danh sách đơn vị quy đổi [{"unit_name": "Thùng", "conversion_rate": 24}]
    created_at = Column(DateTime, default=get_utc_now)

    category_rel = relationship("CategoryEntity", backref="products")

    @property
    def units(self) -> list[dict]:
        if self.units_json:
            try:
                return json.loads(self.units_json)
            except Exception:
                pass
        return []

    @units.setter
    def units(self, val: list[dict]):
        self.units_json = json.dumps(val or [], ensure_ascii=False)


class DealerEntity(Base):
    __tablename__ = "dealers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(Unicode(500), nullable=True)
    region = Column(Unicode(100), nullable=True)
    assigned_sale_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    credit_limit = Column(Float, default=50000000.0)
    max_debt_days = Column(Integer, default=30)
    customer_group = Column(Unicode(100), default="Đại lý cấp 1")
    status = Column(Unicode(50), default="Đang hoạt động")
    lock_reason = Column(Unicode(500), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)



class InventoryTransactionEntity(Base):
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    product_name = Column(Unicode(255), nullable=False)
    type = Column(String(50), nullable=False)  # receipt, issue, adjust
    quantity = Column(Integer, nullable=False)
    previous_stock = Column(Integer, nullable=False)
    new_stock = Column(Integer, nullable=False)
    unit_name = Column(Unicode(50), nullable=True)
    conversion_rate = Column(Float, default=1.0)
    base_quantity = Column(Integer, nullable=True)
    performed_by = Column(Unicode(100), nullable=False)
    user_role = Column(String(50), nullable=False)
    reason = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)


class OrderEntity(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_code = Column(String(50), unique=True, index=True, nullable=False)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False)
    dealer_name = Column(Unicode(255), nullable=False)
    created_by = Column(Unicode(100), nullable=False)
    assigned_sale_id = Column(Integer, nullable=True)
    assigned_sale_name = Column(Unicode(255), nullable=True)
    total_amount = Column(Float, default=0.0)
    status = Column(String(50), default="PENDING")
    note = Column(UnicodeText, nullable=True)
    items_json = Column(UnicodeText, nullable=True)  # JSON order items
    created_at = Column(DateTime, default=get_utc_now)
    delivery_point_id = Column(Integer, ForeignKey("dealer_delivery_points.id"), nullable=True)

class AuditLogEntity(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    user_name = Column(Unicode(100), nullable=True)
    action_type = Column(String(50), nullable=False, index=True)  # INVENTORY_ADJUST, PRICE_CHANGE, DEBT_LIMIT_CHANGE, INVOICE_EDIT, etc.
    entity_type = Column(String(50), nullable=False, index=True)  # Product, CustomerDebt, Invoice, etc.
    entity_id = Column(String(100), nullable=False, index=True)   # Mã SP, ID khách, Mã hóa đơn
    old_values = Column(UnicodeText, nullable=True)               # JSON string giá trị cũ
    new_values = Column(UnicodeText, nullable=True)               # JSON string giá trị mới
    reason = Column(Unicode(255), nullable=True)                  # Lý do điều chỉnh
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=get_utc_now, index=True)

class DealerDeliveryPointEntity(Base):
    __tablename__ = "dealer_delivery_points"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False)
    label = Column(Unicode(100), nullable=False)
    address = Column(Unicode(500), nullable=False)
    receiver_name = Column(Unicode(150), nullable=False)
    receiver_phone = Column(String(20), nullable=False)
    route_note = Column(Unicode(500), nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=get_utc_now)


class MasterDeliveryPointEntity(Base):
    __tablename__ = "master_delivery_points"

    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(Unicode(100), nullable=False)
    address = Column(Unicode(500), nullable=False)
    receiver_name = Column(Unicode(150), nullable=True)
    receiver_phone = Column(String(50), nullable=True)
    route_note = Column(Unicode(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=get_utc_now)