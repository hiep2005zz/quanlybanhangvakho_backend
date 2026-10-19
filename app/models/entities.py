# backend/app/models/entities.py
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
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
from sqlalchemy.orm import relationship, synonym
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
    created_at = Column(DateTime, default=get_utc_now)

    @property
    def roles(self) -> list[str]:
        if self.roles_json:
            try:
                res = json.loads(self.roles_json)
                if isinstance(res, str):
                    try:
                        inner = json.loads(res)
                        if isinstance(inner, list):
                            return [str(x) for x in inner]
                    except Exception:
                        pass
                    return [res]
                elif isinstance(res, list):
                    cleaned = []
                    for item in res:
                        if isinstance(item, str) and (item.startswith("[") or item.startswith('"[')):
                            try:
                                inner = json.loads(item)
                                if isinstance(inner, str):
                                    inner = json.loads(inner)
                                if isinstance(inner, list):
                                    cleaned.extend([str(x) for x in inner])
                                    continue
                                elif isinstance(inner, str):
                                    cleaned.append(str(inner))
                                    continue
                            except Exception:
                                pass
                        cleaned.append(str(item))
                    return cleaned
            except Exception:
                pass
        return [self.role] if self.role else []

    @roles.setter
    def roles(self, val: list[str]):
        self.roles_json = json.dumps(val or [])

    def get_roles(self) -> list[str]:
        return self.roles

    avatar_url = Column(Unicode(500), nullable=True)


class RoleEntity(Base):
    __tablename__ = "roles"

    code = Column(String(50), primary_key=True, index=True)
    name = Column(Unicode(100), nullable=False)
    description = Column(Unicode(255), nullable=True)
    user_count = Column(Integer, default=0)
    status = Column(Unicode(50), default="Đang hoạt động")
    permissions_json = Column(UnicodeText, nullable=True)  # JSON array string

    @property
    def permissions(self) -> list[str]:
        if self.permissions_json:
            try:
                return json.loads(self.permissions_json)
            except Exception:
                pass
        return []

    @permissions.setter
    def permissions(self, val: list[str]):
        self.permissions_json = json.dumps(val or [])


class CategoryEntity(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), index=True, nullable=True)
    name = Column(Unicode(100), nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    description = Column(Unicode(255), nullable=True)
    product_count = Column(Integer, default=0)
    status = Column(Unicode(50), default="Đang hoạt động")
    created_at = Column(DateTime, default=get_utc_now)


class ProductEntity(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    category = Column(Unicode(100), default="Thời trang")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    stock = Column(Integer, default=0)
    cost_price = Column(Float, default=0.0)
    sell_price = Column(Float, default=0.0)
    floor_price = Column(Float, default=0.0, nullable=True)
    base_unit = Column(Unicode(50), default="Cái")
    units_json = Column(UnicodeText, nullable=True)
    packaging_specification = Column(Unicode(255), nullable=True)
    images_json = Column(UnicodeText, nullable=True)
    is_batch_managed = Column(Boolean, default=False)
    status = Column(String(50), default="active", index=True)
    min_stock = Column(Integer, default=10, nullable=True)
    created_at = Column(DateTime, default=get_utc_now, index=True)

    category_rel = relationship("CategoryEntity", backref="products")

    @property
    def units(self):
        if self.units_json:
            try:
                res = json.loads(self.units_json)
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        return []

    @units.setter
    def units(self, val):
        self.units_json = json.dumps(val or [], ensure_ascii=False)

    @property
    def images(self):
        if self.images_json:
            try:
                res = json.loads(self.images_json)
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        return []

    @images.setter
    def images(self, val):
        self.images_json = json.dumps(val or [], ensure_ascii=False)


class DealerEntity(Base):
    __tablename__ = "dealers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    tax_code = Column(String(50), nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(Unicode(500), nullable=True)
    region = Column(Unicode(100), nullable=True)
    assigned_sale_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    credit_limit = Column(Float, default=50000000.0)
    max_debt_days = Column(Integer, default=30)
    overdue_days_allowed = Column(Integer, default=30, nullable=True)
    customer_group = Column(Unicode(100), default="Đại lý cấp 1")
    status = Column(Unicode(50), default="Đang hoạt động")
    transaction_count = Column(Integer, default=0, nullable=True)
    lock_reason = Column(Unicode(500), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String(50), nullable=True)
    # Cờ cảnh báo tín dụng tự động (S7-04)
    is_over_credit_limit = Column(Boolean, default=False, nullable=False)
    is_overdue = Column(Boolean, default=False, nullable=False)
    credit_warning_flag = Column(Boolean, default=False, nullable=False)
    credit_flag_reason = Column(UnicodeText, nullable=True)
    last_credit_evaluated_at = Column(DateTime, nullable=True)
    warehouse_id = Column(String(50), nullable=True)
    warehouse_name = Column(Unicode(255), nullable=True)
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
    performed_by = Column(Unicode(100), nullable=False)
    user_role = Column(String(50), nullable=False)
    reason = Column(UnicodeText, nullable=True)
    unit_name = Column(Unicode(50), default="Cái")
    conversion_rate = Column(Float, default=1.0)
    base_quantity = Column(Float, default=0.0)
    # SCRUM-81: Lưu vết lô gợi ý, lô đã chọn và lý do đổi lô (FEFO)
    batch_number = Column(String(100), nullable=True)
    selected_batch_id = Column(Integer, nullable=True)
    suggested_batch_id = Column(Integer, nullable=True)
    batch_change_reason = Column(Unicode(500), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"                        # Nháp
    PENDING_APPROVAL = "PENDING_APPROVAL"  # Chờ duyệt
    APPROVED = "APPROVED"                  # Đã duyệt
    CONFIRMED = "CONFIRMED"                # Đã xác nhận
    PICKING = "PICKING"                    # Đang soạn hàng
    EXPORTED = "EXPORTED"                  # Đã xuất
    PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"  # Giao một phần (Còn nợ hàng)
    DELIVERED = "DELIVERED"                # Đã giao
    CLOSED = "CLOSED"                      # Đóng
    CANCELLED = "CANCELLED"                # Huỷ
    REJECTED = "REJECTED"                  # Bị từ chối
    EXPIRED = "EXPIRED"                    # Hết hạn giữ chỗ

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
    status = Column(String(50), default="PENDING", index=True)
    note = Column(UnicodeText, nullable=True)
    items_json = Column(UnicodeText, nullable=True)  # JSON order items
    created_at = Column(DateTime, default=get_utc_now, index=True)
    delivery_point_id = Column(Integer, ForeignKey("dealer_delivery_points.id"), nullable=True)
    discount_rate = Column(Float, default=0.0)
    discount_amount = Column(Float, default=0.0)
    requires_approval = Column(Boolean, default=False, nullable=True)
    approval_status = Column(String(50), default="NORMAL", nullable=True)
    approval_reason = Column(Unicode(500), nullable=True)
    cancel_reason = Column(Unicode(500), nullable=True)
    cancelled_by = Column(String(100), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    reservation_expires_at = Column(DateTime, nullable=True)

    # Synonym for customer_id to ensure full compatibility with dealer_id
    customer_id = synonym("dealer_id")


class OrderItemEntity(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    order_code = Column(String(50), nullable=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    sku_id = Column(String(50), nullable=True, index=True)
    product_name = Column(Unicode(255), nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    base_quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, default=0.0)
    unit_name = Column(Unicode(50), nullable=True)
    conversion_rate = Column(Float, default=1.0)
    is_reserved = Column(Boolean, default=False, nullable=False)
    # SCRUM-81: Gợi ý và chọn lô xuất kho (FEFO)
    suggested_batch_id = Column(Integer, ForeignKey("product_batches.id"), nullable=True, index=True)
    selected_batch_id = Column(Integer, ForeignKey("product_batches.id"), nullable=True, index=True)
    batch_change_reason = Column(Unicode(500), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    order_rel = relationship("OrderEntity", backref="items_rel")
    product_rel = relationship("ProductEntity", backref="order_items")
    suggested_batch_rel = relationship("ProductBatchEntity", foreign_keys=[suggested_batch_id])
    selected_batch_rel = relationship("ProductBatchEntity", foreign_keys=[selected_batch_id])


# Alias for OrderEntity
Order = OrderEntity

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


class WarehouseStockEntity(Base):
    __tablename__ = "warehouse_stocks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    warehouse_id = Column(String(50), nullable=False, index=True)
    warehouse_name = Column(Unicode(255), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    actual_stock = Column(Integer, default=0, nullable=False)
    reserved_stock = Column(Integer, default=0, nullable=False)
    quantity_on_hand = Column(Integer, default=0, nullable=True)
    reserved_quantity = Column(Integer, default=0, nullable=True)
    min_stock = Column(Integer, default=10, nullable=False)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    product_rel = relationship("ProductEntity", backref="warehouse_stocks")

    @property
    def on_hand(self) -> int:
        return self.actual_stock if self.actual_stock is not None else (self.quantity_on_hand or 0)

    @property
    def reserved(self) -> int:
        return self.reserved_stock if self.reserved_stock is not None else (self.reserved_quantity or 0)

    @property
    def available(self) -> int:
        return max(0, self.on_hand - self.reserved)

    @property
    def available_stock(self) -> int:
        return self.available

    @property
    def is_low_stock(self) -> bool:
        return self.on_hand <= (self.min_stock or 0)

    @property
    def shortage_quantity(self) -> int:
        return max(0, (self.min_stock or 0) - self.on_hand)

# Alias cho spec nghiệp vụ Tồn kho
InventoryEntity = WarehouseStockEntity

class StockAuditEntity(Base):
    __tablename__ = "stock_audits"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    warehouse_id = Column(String(50), nullable=True, index=True)
    warehouse_name = Column(Unicode(255), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    category_name = Column(Unicode(100), nullable=True)
    scope_type = Column(String(20), default="WAREHOUSE")  # WAREHOUSE | CATEGORY | ALL
    status = Column(String(20), default="DRAFT", nullable=False)  # DRAFT | CONFIRMED | CANCELLED
    note = Column(UnicodeText, nullable=True)
    total_items = Column(Integer, default=0)
    discrepancy_items_count = Column(Integer, default=0)
    total_discrepancy_qty = Column(Integer, default=0)
    items_json = Column(UnicodeText, nullable=True)
    created_by = Column(Unicode(100), nullable=False)
    confirmed_by = Column(Unicode(100), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    cancelled_by = Column(Unicode(100), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)


class StockTransferEntity(Base):
    __tablename__ = "stock_transfers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    from_warehouse_id = Column(String(50), nullable=False, index=True)
    from_warehouse_name = Column(Unicode(255), nullable=False)
    to_warehouse_id = Column(String(50), nullable=False, index=True)
    to_warehouse_name = Column(Unicode(255), nullable=False)
    status = Column(String(20), default="IN_TRANSIT", nullable=False)  # DRAFT | IN_TRANSIT | COMPLETED | CANCELLED
    note = Column(UnicodeText, nullable=True)
    total_items = Column(Integer, default=0)
    total_transfer_qty = Column(Integer, default=0)
    total_received_qty = Column(Integer, default=0)
    discrepancy_qty = Column(Integer, default=0)
    discrepancy_reason = Column(UnicodeText, nullable=True)
    items_json = Column(UnicodeText, nullable=True)
    created_by = Column(Unicode(100), nullable=False)
    dispatched_by = Column(Unicode(100), nullable=True)
    dispatched_at = Column(DateTime, nullable=True)
    received_by = Column(Unicode(100), nullable=True)
    received_at = Column(DateTime, nullable=True)
    cancelled_by = Column(Unicode(100), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)


class OrderDispatchEntity(Base):
    __tablename__ = "order_dispatches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    dispatch_code = Column(String(50), unique=True, index=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    order_code = Column(String(50), nullable=False, index=True)
    dispatch_type = Column(String(50), default="INITIAL", nullable=False)  # INITIAL | SUPPLEMENTARY
    round_number = Column(Integer, default=1, nullable=False)
    warehouse_id = Column(String(50), nullable=True)
    warehouse_name = Column(Unicode(255), nullable=True)
    performed_by = Column(Unicode(100), nullable=False)
    note = Column(UnicodeText, nullable=True)
    items_json = Column(UnicodeText, nullable=True)  # List of dispatched items
    created_at = Column(DateTime, default=get_utc_now)

    order_rel = relationship("OrderEntity", backref="dispatches_rel")


class DriverEntity(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(100), nullable=False)
    phone = Column(String(20), nullable=False)
    license_number = Column(String(50), nullable=True)
    status = Column(String(20), default="ACTIVE", nullable=False)  # ACTIVE | INACTIVE
    created_at = Column(DateTime, default=get_utc_now)


class VehicleEntity(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    plate_number = Column(String(20), unique=True, index=True, nullable=False)
    vehicle_type = Column(Unicode(50), default="Xe tải 1.5 tấn", nullable=False)
    capacity_kg = Column(Float, default=1500.0)
    status = Column(String(20), default="ACTIVE", nullable=False)  # ACTIVE | MAINTENANCE | INACTIVE
    created_at = Column(DateTime, default=get_utc_now)


class CreditPolicyConfigEntity(Base):
    """
    Cấu hình chính sách cảnh báo và khóa bán tự động khi đại lý vượt hạn mức hoặc quá hạn (S7-04).
    over_limit_action: 'BLOCK' (Chặn tạo đơn) | 'REQUIRE_APPROVAL' (Chuyển sang Cần duyệt)
    overdue_action: 'BLOCK' (Chặn tạo đơn) | 'REQUIRE_APPROVAL' (Chuyển sang Cần duyệt)
    """
    __tablename__ = "credit_policy_configs"

    id = Column(Integer, primary_key=True, default=1)
    over_limit_action = Column(String(50), default="REQUIRE_APPROVAL", nullable=False)
    overdue_action = Column(String(50), default="BLOCK", nullable=False)
    auto_eval_enabled = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)
    updated_by = Column(Unicode(100), nullable=True)


class DealerPaymentEntity(Base):
    """
    Ghi nhận thanh toán & đối trừ công nợ đại lý (S7-02, hỗ trợ S7-04 kích hoạt đánh giá lại).
    """
    __tablename__ = "dealer_payments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    payment_code = Column(String(50), unique=True, index=True, nullable=False)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    amount = Column(Float, nullable=False)
    payment_method = Column(Unicode(50), default="Chuyển khoản")  # Chuyển khoản | Tiền mặt
    reference_code = Column(String(100), nullable=True)           # Mã giao dịch / UNC
    note = Column(UnicodeText, nullable=True)
    recorded_by = Column(Unicode(100), nullable=False)
    recorded_at = Column(DateTime, default=get_utc_now)


class DeliveryTripEntity(Base):
    __tablename__ = "delivery_trips"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trip_code = Column(String(50), unique=True, index=True, nullable=False)
    delivery_date = Column(String(20), nullable=True, index=True)  # YYYY-MM-DD
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True, index=True)
    driver_name = Column(Unicode(150), nullable=True)
    driver_phone = Column(String(50), nullable=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True, index=True)
    vehicle_plate = Column(Unicode(50), nullable=True)
    vehicle_type = Column(Unicode(50), nullable=True)
    route_name = Column(Unicode(255), nullable=True)
    warehouse_id = Column(String(50), nullable=True)
    warehouse_name = Column(Unicode(255), nullable=True)
    status = Column(String(30), default="DRAFT", nullable=False, index=True)  # DRAFT | ASSIGNED | IN_PROGRESS | IN_TRANSIT | DELIVERING | COMPLETED | PARTIALLY_FAILED | CANCELLED
    dispatched_at = Column(DateTime, nullable=True)
    estimated_delivery_time = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    note = Column(UnicodeText, nullable=True)
    total_stops = Column(Integer, default=0)
    total_dispatches = Column(Integer, default=0)
    created_by = Column(Unicode(100), nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    stops = relationship("DeliveryStopEntity", backref="trip", cascade="all, delete-orphan", order_by="DeliveryStopEntity.stop_order")
    stops_rel = relationship("DeliveryTripStopEntity", backref="trip_rel", cascade="all, delete-orphan", order_by="DeliveryTripStopEntity.stop_order")
    dispatches_rel = relationship("DeliveryTripDispatchEntity", backref="trip_rel", cascade="all, delete-orphan")


class DeliveryTripStopEntity(Base):
    __tablename__ = "delivery_trip_stops"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trip_id = Column(Integer, ForeignKey("delivery_trips.id"), nullable=False, index=True)
    stop_order = Column(Integer, default=1, nullable=False)  # 1, 2, 3...
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=True)
    dealer_name = Column(Unicode(255), nullable=False)
    delivery_point_id = Column(Integer, nullable=True)
    address = Column(Unicode(500), nullable=False)
    receiver_name = Column(Unicode(150), nullable=False)
    receiver_phone = Column(String(50), nullable=False)
    route_note = Column(Unicode(500), nullable=True)
    status = Column(String(30), default="PENDING", nullable=False)  # PENDING | DELIVERED | FAILED
    delivered_at = Column(DateTime, nullable=True)
    failure_reason = Column(UnicodeText, nullable=True)
    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)

    dispatches_rel = relationship("DeliveryTripDispatchEntity", backref="stop_rel", cascade="all, delete-orphan")


class DeliveryTripDispatchEntity(Base):
    __tablename__ = "delivery_trip_dispatches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trip_id = Column(Integer, ForeignKey("delivery_trips.id"), nullable=False, index=True)
    stop_id = Column(Integer, ForeignKey("delivery_trip_stops.id"), nullable=False, index=True)
    dispatch_id = Column(Integer, ForeignKey("order_dispatches.id"), nullable=False, index=True)
    dispatch_code = Column(String(50), nullable=False, index=True)
    order_code = Column(String(50), nullable=False, index=True)
    items_summary = Column(UnicodeText, nullable=True)
    status = Column(String(30), default="PENDING", nullable=False)  # PENDING | DELIVERED | FAILED
    created_at = Column(DateTime, default=get_utc_now)


class DeliveryStopEntity(Base):
    __tablename__ = "delivery_stops"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trip_id = Column(Integer, ForeignKey("delivery_trips.id"), nullable=False, index=True)
    stop_order = Column(Integer, default=1, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    order_code = Column(String(50), nullable=False, index=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=True, index=True)
    dealer_name = Column(Unicode(255), nullable=False)
    delivery_point = Column(Unicode(500), nullable=False)
    receiver_name = Column(Unicode(150), nullable=True)
    receiver_phone = Column(String(50), nullable=True)
    amount = Column(Float, default=0.0, nullable=False)
    status = Column(String(30), default="PENDING", nullable=False, index=True)  # PENDING | IN_TRANSIT | DELIVERED | FAILED
    estimated_arrival = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    actual_delivery_time = Column(DateTime, nullable=True)
    failed_reason = Column(Unicode(255), nullable=True)
    failure_reason = Column(Unicode(500), nullable=True)
    pod_image_url = Column(Unicode(500), nullable=True)
    delivery_status = Column(String(30), nullable=True)
    return_status = Column(String(50), nullable=True)
    items_detail = Column(UnicodeText, nullable=True)
    note = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)


class StockAdjustmentEntity(Base):
    __tablename__ = "stock_adjustments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    warehouse_id = Column(String(50), nullable=False, index=True)
    warehouse_name = Column(Unicode(255), nullable=False)
    reason_type = Column(String(50), nullable=False)  # damaged, lost, expired, other
    reason_note = Column(UnicodeText, nullable=False)
    total_loss_value = Column(Float, default=0.0, nullable=False)
    total_items = Column(Integer, default=0)
    total_quantity = Column(Integer, default=0)
    status = Column(String(20), default="PENDING", nullable=False)  # PENDING | APPROVED | REJECTED
    items_json = Column(UnicodeText, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Unicode(100), nullable=False)
    created_by_role = Column(String(50), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by = Column(Unicode(100), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejected_by = Column(Unicode(100), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(UnicodeText, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

