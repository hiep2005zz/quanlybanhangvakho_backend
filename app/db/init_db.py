# backend/app/db/init_db.py
import json
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import inspect, text
from app.core.database import engine, Base, SessionLocal
from app.models.entities import (
    UserEntity,
    ProductEntity,
    DealerEntity,
    InventoryTransactionEntity,
    OrderEntity,
    DealerDeliveryPointEntity,
    MasterDeliveryPointEntity,
    CategoryEntity,
    AuditLogEntity,
    WarehouseStockEntity,
    StockAuditEntity,
    StockTransferEntity,
    DeliveryTripEntity,
    DeliveryStopEntity,
    StockAdjustmentEntity,
    CreditPolicyConfigEntity,
    DealerPaymentEntity,
    OrderDispatchEntity,
    DriverEntity,
    VehicleEntity,
    DeliveryTripStopEntity,
    DeliveryTripDispatchEntity,
)
from app.models.payment_collection import PaymentCollectionEntity
from app.models.supplier import SupplierEntity
from app.models.goods_receipt import (
    WarehouseEntity,
    UnitOfMeasureEntity,
    GoodsReceiptNoteEntity,
    GoodsReceiptNoteItemEntity,
    ProductBatchEntity,
    InventoryLedgerEntity,
)
from app.models.warehouse import (
    WarehouseLocationEntity,
    LocationStockEntity,
    WarehouseZoneEntity,
    WarehouseRackEntity,
)
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.discount import DiscountPolicyEntity, DiscountTierEntity
from app.models.sales_target import SalesTargetEntity
from app.models.invoice import InvoiceEntity, InvoiceDeliveryNoteEntity, InvoiceItemEntity, InvoiceAdjustmentEntity
from app.models.debt_payment import (
    PaymentReceiptEntity,
    PaymentAllocationEntity,
    PaymentAdjustmentEntity,
)
from app.models.debt_reconciliation import DebtReconciliationEntity
from app.models.debt_adjustment import (
    DebtAdjustmentEntity,
    DebtAdjustmentItemEntity,
    ReturnOrderEntity,
    ReturnOrderItemEntity,
)
from app.models.receipt_voucher import ReceiptVoucherEntity
from app.core.security import get_password_hash
from app.core.rbac import Role


def _ensure_dealer_credit_limit_column(bind=engine):
    """Add the credit limit column to databases created before it was introduced."""
    columns = {column["name"] for column in inspect(bind).get_columns("dealers")}
    if "credit_limit" in columns:
        return

    with bind.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE dealers "
                "ADD credit_limit FLOAT NOT NULL DEFAULT 50000000.0"
            )
        )


def _ensure_legacy_columns(bind=engine):
    """Add columns introduced after existing SQLite or SQL Server databases were created."""
    additions = {
        "users": {
            "avatar_url": ("TEXT", "NVARCHAR(500)"),
        },
        "products": {
            "category_id": ("INTEGER NULL", "INT NULL"),
            "base_unit": ("VARCHAR(50) DEFAULT 'Cái'", "NVARCHAR(50) DEFAULT N'Cái'"),
            "units_json": ("TEXT", "NVARCHAR(MAX)"),
            "packaging_specification": ("VARCHAR(255)", "NVARCHAR(255)"),
            "images_json": ("TEXT", "NVARCHAR(MAX)"),
            "status": ("VARCHAR(50) DEFAULT 'active'", "NVARCHAR(50) DEFAULT 'active'"),
            "is_batch_managed": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "min_stock": ("INTEGER DEFAULT 10", "INT DEFAULT 10"),
            "floor_price": ("FLOAT NULL", "FLOAT NULL"),
        },
        "warehouse_stocks": {
            "min_stock": ("INTEGER DEFAULT 10", "INT DEFAULT 10"),
        },
        "dealers": {
            "max_debt_days": ("INTEGER DEFAULT 30", "INT DEFAULT 30"),
            "overdue_days_allowed": ("INTEGER DEFAULT 30", "INT DEFAULT 30"),
            "locked_by": ("VARCHAR(50)", "NVARCHAR(50)"),
            "tax_code": ("VARCHAR(50)", "NVARCHAR(50)"),
            "transaction_count": ("INTEGER DEFAULT 0", "INT DEFAULT 0"),
            "warehouse_id": ("VARCHAR(50)", "NVARCHAR(50)"),
            "warehouse_name": ("VARCHAR(255)", "NVARCHAR(255)"),
            "is_over_credit_limit": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "is_overdue": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "credit_warning_flag": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "credit_flag_reason": ("TEXT", "NVARCHAR(MAX)"),
            "last_credit_evaluated_at": ("DATETIME", "DATETIME"),
        },
        "inventory_transactions": {
            "unit_name": ("VARCHAR(50) DEFAULT 'Cái'", "NVARCHAR(50) DEFAULT N'Cái'"),
            "conversion_rate": ("FLOAT DEFAULT 1.0", "FLOAT DEFAULT 1.0"),
            "base_quantity": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "batch_number": ("VARCHAR(100) NULL", "NVARCHAR(100) NULL"),
            "selected_batch_id": ("INTEGER NULL", "INT NULL"),
            "suggested_batch_id": ("INTEGER NULL", "INT NULL"),
            "batch_change_reason": ("NVARCHAR(500) NULL", "NVARCHAR(500) NULL"),
        },
        "inventory_ledgers": {
            "selected_batch_id": ("INTEGER NULL", "INT NULL"),
            "suggested_batch_id": ("INTEGER NULL", "INT NULL"),
            "batch_change_reason": ("NVARCHAR(500) NULL", "NVARCHAR(500) NULL"),
        },
        "orders": {
            "delivery_point_id": ("INTEGER", "INT NULL"),
            "discount_rate": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "discount_amount": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "requires_approval": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "approval_status": ("VARCHAR(50) DEFAULT 'NORMAL'", "NVARCHAR(50) DEFAULT 'NORMAL'"),
            "approval_reason": ("NVARCHAR(500)", "NVARCHAR(500)"),
            "cancel_reason": ("NVARCHAR(500)", "NVARCHAR(500)"),
            "cancelled_by": ("VARCHAR(100)", "NVARCHAR(100)"),
            "cancelled_at": ("DATETIME", "DATETIME"),
        },
        "categories": {
            "code": ("VARCHAR(50) DEFAULT ''", "NVARCHAR(50) DEFAULT ''"),
            "parent_id": ("INTEGER NULL", "INT NULL"),
            "product_count": ("INTEGER DEFAULT 0", "INT DEFAULT 0"),
            "status": ("VARCHAR(50) DEFAULT 'Đang hoạt động'", "NVARCHAR(50) DEFAULT N'Đang hoạt động'"),
        },
        "warehouses": {
            "manager_name": ("VARCHAR(100)", "NVARCHAR(100)"),
            "phone": ("VARCHAR(50)", "NVARCHAR(50)"),
            "status": ("VARCHAR(50) DEFAULT 'Đang hoạt động'", "NVARCHAR(50) DEFAULT N'Đang hoạt động'"),
        },
        "stock_audits": {
            "discrepancy_items_count": ("INTEGER DEFAULT 0", "INT DEFAULT 0"),
            "cancelled_by": ("VARCHAR(100)", "NVARCHAR(100)"),
            "cancelled_at": ("DATETIME", "DATETIME"),
        },
        "discount_tiers": {
            "discount_amount": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "discount_type": ("VARCHAR(20) DEFAULT 'percent'", "NVARCHAR(20) DEFAULT 'percent'"),
        },
        "product_batches": {
            "manufacturing_date": ("DATETIME NULL", "DATETIME NULL"),
            "reserved_quantity": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
        },
        "goods_receipt_note_items": {
            "manufacturing_date": ("DATETIME NULL", "DATETIME NULL"),
        },
        "order_items": {
            "suggested_batch_id": ("INTEGER NULL", "INT NULL"),
            "selected_batch_id": ("INTEGER NULL", "INT NULL"),
            "batch_change_reason": ("NVARCHAR(500) NULL", "NVARCHAR(500) NULL"),
        },
        "delivery_trips": {
            "vehicle_plate": ("VARCHAR(50) DEFAULT ''", "NVARCHAR(50) DEFAULT ''"),
            "driver_phone": ("VARCHAR(50) NULL", "NVARCHAR(50) NULL"),
            "route_name": ("VARCHAR(255) NULL", "NVARCHAR(255) NULL"),
            "warehouse_id": ("VARCHAR(50) NULL", "NVARCHAR(50) NULL"),
            "warehouse_name": ("VARCHAR(255) NULL", "NVARCHAR(255) NULL"),
            "estimated_delivery_time": ("DATETIME NULL", "DATETIME NULL"),
            "dispatched_at": ("DATETIME NULL", "DATETIME NULL"),
            "completed_at": ("DATETIME NULL", "DATETIME NULL"),
            "note": ("TEXT NULL", "NVARCHAR(MAX) NULL"),
            "created_by": ("VARCHAR(100) DEFAULT ''", "NVARCHAR(100) DEFAULT ''"),
        },
        "delivery_stops": {
            "actual_delivery_time": ("DATETIME NULL", "DATETIME NULL"),
            "pod_image_url": ("VARCHAR(500) NULL", "NVARCHAR(500) NULL"),
            "delivery_status": ("VARCHAR(30) NULL", "NVARCHAR(30) NULL"),
            "failure_reason": ("TEXT NULL", "NVARCHAR(MAX) NULL"),
            "return_status": ("VARCHAR(50) NULL", "NVARCHAR(50) NULL"),
            "items_detail": ("TEXT NULL", "NVARCHAR(MAX) NULL"),
        },
        "return_orders": {
            "return_code": ("VARCHAR(50) NULL", "VARCHAR(50) NULL"),
            "original_invoice_id": ("INTEGER NULL", "INT NULL"),
            "original_invoice_code": ("VARCHAR(50) DEFAULT ''", "NVARCHAR(50) DEFAULT ''"),
            "warehouse_id": ("VARCHAR(50) NULL", "NVARCHAR(50) NULL"),
            "warehouse_name": ("VARCHAR(255) NULL", "NVARCHAR(255) NULL"),
            "reason": ("NVARCHAR(500) DEFAULT ''", "NVARCHAR(500) DEFAULT ''"),
            "subtotal_amount": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "tax_amount": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "total_amount": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "created_by_name": ("NVARCHAR(150) NULL", "NVARCHAR(150) NULL"),
            "confirmed_by_name": ("NVARCHAR(150) NULL", "NVARCHAR(150) NULL"),
            "cancelled_by_id": ("INTEGER NULL", "INT NULL"),
            "cancelled_by_name": ("NVARCHAR(150) NULL", "NVARCHAR(150) NULL"),
            "cancelled_at": ("DATETIME NULL", "DATETIME NULL"),
            "cancel_reason": ("NVARCHAR(500) NULL", "NVARCHAR(500) NULL"),
            "updated_at": ("DATETIME NULL", "DATETIME NULL"),
        },
        "return_order_items": {
            "order_item_id": ("INTEGER NULL", "INT NULL"),
            "original_quantity": ("INTEGER DEFAULT 0", "INT DEFAULT 0"),
            "return_quantity": ("INTEGER DEFAULT 1", "INT DEFAULT 1"),
            "refund_amount": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "return_reason": ("NVARCHAR(255) NULL", "NVARCHAR(255) NULL"),
            "is_damaged": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "target_warehouse_id": ("INTEGER NULL", "INT NULL"),
            "target_warehouse_code": ("VARCHAR(50) NULL", "VARCHAR(50) NULL"),
            "target_warehouse_name": ("NVARCHAR(255) NULL", "NVARCHAR(255) NULL"),
            "batch_id": ("INTEGER NULL", "INT NULL"),
            "batch_number": ("VARCHAR(100) NULL", "VARCHAR(100) NULL"),
            "note": ("TEXT NULL", "NVARCHAR(MAX) NULL"),
            "created_at": ("DATETIME NULL", "DATETIME NULL"),
            "quantity": ("INTEGER DEFAULT 1", "INT DEFAULT 1"),
            "line_total": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
            "reason": ("NVARCHAR(255) NULL", "NVARCHAR(255) NULL"),
        },
    }
    inspector = inspect(bind)
    dialect_name = bind.dialect.name
    for table_name, columns_to_add in additions.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, (sqlite_definition, sql_server_definition) in columns_to_add.items():
            if column_name in existing_columns:
                continue
            definition = sql_server_definition if dialect_name == "mssql" else sqlite_definition
            add_column = "ADD" if dialect_name == "mssql" else "ADD COLUMN"
            with bind.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE {table_name} {add_column} {column_name} {definition}")
                )

    try:
        updated_inspector = inspect(bind)
        if updated_inspector.has_table("return_orders"):
            cols = {c["name"] for c in updated_inspector.get_columns("return_orders")}
            with bind.begin() as connection:
                if bind.dialect.name == "mssql":
                    try:
                        if "code" in cols:
                            connection.execute(text("ALTER TABLE return_orders ALTER COLUMN code VARCHAR(50) NULL"))
                        if "created_by" in cols:
                            connection.execute(text("ALTER TABLE return_orders ALTER COLUMN created_by NVARCHAR(100) NULL"))
                        if "total_items" in cols:
                            connection.execute(text("ALTER TABLE return_orders ALTER COLUMN total_items INT NULL"))
                        if "total_quantity" in cols:
                            connection.execute(text("ALTER TABLE return_orders ALTER COLUMN total_quantity INT NULL"))
                        if "total_refund_amount" in cols:
                            connection.execute(text("ALTER TABLE return_orders ALTER COLUMN total_refund_amount FLOAT NULL"))
                    except Exception:
                        pass
                if "code" in cols and "return_code" in cols:
                    connection.execute(text("UPDATE return_orders SET return_code = code WHERE (return_code IS NULL OR return_code = '') AND code IS NOT NULL"))
                    connection.execute(text("UPDATE return_orders SET code = return_code WHERE (code IS NULL OR code = '') AND return_code IS NOT NULL"))
                if "total_refund_amount" in cols and "total_amount" in cols:
                    connection.execute(text("UPDATE return_orders SET total_amount = total_refund_amount WHERE (total_amount IS NULL OR total_amount = 0.0) AND total_refund_amount IS NOT NULL"))
                    connection.execute(text("UPDATE return_orders SET total_refund_amount = total_amount WHERE (total_refund_amount IS NULL OR total_refund_amount = 0.0) AND total_amount IS NOT NULL"))
                if "created_by" in cols and "created_by_name" in cols:
                    connection.execute(text("UPDATE return_orders SET created_by_name = created_by WHERE (created_by_name IS NULL OR created_by_name = '') AND created_by IS NOT NULL"))
                    connection.execute(text("UPDATE return_orders SET created_by = created_by_name WHERE (created_by IS NULL OR created_by = '') AND created_by_name IS NOT NULL"))
                if "confirmed_by" in cols and "confirmed_by_name" in cols:
                    connection.execute(text("UPDATE return_orders SET confirmed_by_name = confirmed_by WHERE (confirmed_by_name IS NULL OR confirmed_by_name = '') AND confirmed_by IS NOT NULL"))
                    connection.execute(text("UPDATE return_orders SET confirmed_by = confirmed_by_name WHERE (confirmed_by IS NULL OR confirmed_by = '') AND confirmed_by_name IS NOT NULL"))
                if "cancelled_by" in cols and "cancelled_by_name" in cols:
                    connection.execute(text("UPDATE return_orders SET cancelled_by_name = cancelled_by WHERE (cancelled_by_name IS NULL OR cancelled_by_name = '') AND cancelled_by IS NOT NULL"))
        if updated_inspector.has_table("return_order_items"):
            cols = {c["name"] for c in updated_inspector.get_columns("return_order_items")}
            with bind.begin() as connection:
                if "return_quantity" in cols and "quantity" in cols:
                    connection.execute(text("UPDATE return_order_items SET quantity = return_quantity WHERE (quantity IS NULL OR quantity = 0) AND return_quantity IS NOT NULL"))
                    connection.execute(text("UPDATE return_order_items SET return_quantity = quantity WHERE (return_quantity IS NULL OR return_quantity = 0) AND quantity IS NOT NULL"))
                if "refund_amount" in cols and "line_total" in cols:
                    connection.execute(text("UPDATE return_order_items SET line_total = refund_amount WHERE (line_total IS NULL OR line_total = 0.0) AND refund_amount IS NOT NULL"))
                    connection.execute(text("UPDATE return_order_items SET refund_amount = line_total WHERE (refund_amount IS NULL OR refund_amount = 0.0) AND line_total IS NOT NULL"))
                if "return_reason" in cols and "reason" in cols:
                    connection.execute(text("UPDATE return_order_items SET reason = return_reason WHERE (reason IS NULL OR reason = '') AND return_reason IS NOT NULL"))
                    connection.execute(text("UPDATE return_order_items SET return_reason = reason WHERE (return_reason IS NULL OR return_reason = '') AND reason IS NOT NULL"))
                if "original_quantity" in cols:
                    if "return_quantity" in cols:
                        connection.execute(text("UPDATE return_order_items SET original_quantity = return_quantity WHERE (original_quantity IS NULL OR original_quantity = 0) AND return_quantity IS NOT NULL"))
                    elif "quantity" in cols:
                        connection.execute(text("UPDATE return_order_items SET original_quantity = quantity WHERE (original_quantity IS NULL OR original_quantity = 0) AND quantity IS NOT NULL"))
    except Exception:
        pass


def _ensure_mssql_column_types(bind):
    if bind.dialect.name != "mssql":
        return
    with bind.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE dealers ALTER COLUMN status NVARCHAR(50) NULL;"))
            conn.execute(text("UPDATE dealers SET status = N'Đã khóa' WHERE status LIKE '%khóa%' OR status LIKE '%khoá%' OR status LIKE '%?%' OR LOWER(status) LIKE '%lock%';"))
            conn.execute(text("UPDATE dealers SET status = N'Đang hoạt động' WHERE status LIKE '%ho?t%' OR status LIKE '%Ðang%' OR status = 'ACTIVE';"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE drivers ALTER COLUMN status NVARCHAR(50) NOT NULL;"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE delivery_trips ALTER COLUMN driver_name NVARCHAR(150) NULL;"))
            conn.execute(text("ALTER TABLE delivery_trips ALTER COLUMN vehicle_plate NVARCHAR(50) NULL;"))
            conn.execute(text("ALTER TABLE delivery_trips ALTER COLUMN dispatched_at DATETIME NULL;"))
        except Exception:
            pass


def init_db():
    if engine.dialect.name != "mssql":
        raise RuntimeError(
            f"LỖI KHỞI TẠO CSDL: Toàn bộ hệ thống bắt buộc phải chạy Microsoft SQL Server (mssql). "
            f"Dialect hiện tại '{engine.dialect.name}' không được phép!"
        )

    print(f"Initializing database tables using Microsoft SQL Server ({engine.dialect.name})...")
    # Tạo các bảng nếu chưa có
    Base.metadata.create_all(bind=engine)
    _ensure_dealer_credit_limit_column(engine)
    _ensure_legacy_columns(engine)
    _ensure_mssql_column_types(engine)
    # Tự động migrate thêm cột và chỉ mục cho SQL Server
    with engine.connect() as conn:
        if False:
            # Kiểm tra và thêm cột cho SQLite
            user_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
            if "avatar_url" not in user_cols:
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url TEXT;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (avatar_url): {ex}")

            product_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(products)")).fetchall()]
            if "base_unit" not in product_cols:
                try:
                    conn.execute(text("ALTER TABLE products ADD COLUMN base_unit TEXT DEFAULT 'Cái';"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (base_unit): {ex}")
            if "units_json" not in product_cols:
                try:
                    conn.execute(text("ALTER TABLE products ADD COLUMN units_json TEXT;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (units_json): {ex}")
            if "min_stock" not in product_cols:
                try:
                    conn.execute(text("ALTER TABLE products ADD COLUMN min_stock INTEGER DEFAULT 10;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (products.min_stock): {ex}")

            ws_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(warehouse_stocks)")).fetchall()]
            if ws_cols:
                if "min_stock" not in ws_cols:
                    try:
                        conn.execute(text("ALTER TABLE warehouse_stocks ADD COLUMN min_stock INTEGER DEFAULT 10;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (warehouse_stocks.min_stock): {ex}")
                if "quantity_on_hand" not in ws_cols:
                    try:
                        conn.execute(text("ALTER TABLE warehouse_stocks ADD COLUMN quantity_on_hand INTEGER DEFAULT 0;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (warehouse_stocks.quantity_on_hand): {ex}")
                if "reserved_quantity" not in ws_cols:
                    try:
                        conn.execute(text("ALTER TABLE warehouse_stocks ADD COLUMN reserved_quantity INTEGER DEFAULT 0;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (warehouse_stocks.reserved_quantity): {ex}")
                try:
                    conn.execute(text("UPDATE warehouse_stocks SET quantity_on_hand = actual_stock WHERE (quantity_on_hand IS NULL OR quantity_on_hand = 0) AND actual_stock > 0;"))
                    conn.execute(text("UPDATE warehouse_stocks SET reserved_quantity = reserved_stock WHERE reserved_quantity IS NULL;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (warehouse_stocks sync): {ex}")

            order_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(orders)")).fetchall()]
            if order_cols and "reservation_expires_at" not in order_cols:
                try:
                    conn.execute(text("ALTER TABLE orders ADD COLUMN reservation_expires_at DATETIME;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (orders.reservation_expires_at): {ex}")

            dealer_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(dealers)")).fetchall()]
            if "customer_group" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN customer_group TEXT DEFAULT 'Đại lý cấp 1';"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (customer_group): {ex}")
            if "status" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN status VARCHAR(20) DEFAULT 'ACTIVE';"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (dealers.status): {ex}")
            if "region" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN region TEXT;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (region): {ex}")
            if "lock_reason" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN lock_reason TEXT;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (dealers.lock_reason): {ex}")
            if "locked_at" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN locked_at DATETIME;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (dealers.locked_at): {ex}")
            if "locked_by" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN locked_by VARCHAR(50);"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (dealers.locked_by): {ex}")
            if "max_debt_days" not in dealer_cols:
                try:
                    conn.execute(text("ALTER TABLE dealers ADD COLUMN max_debt_days INTEGER DEFAULT 30;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (dealers.max_debt_days): {ex}")

            tx_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(inventory_transactions)")).fetchall()]
            if "unit_name" not in tx_cols:
                try:
                    conn.execute(text("ALTER TABLE inventory_transactions ADD COLUMN unit_name TEXT DEFAULT 'Cái';"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (tx.unit_name): {ex}")
            if "conversion_rate" not in tx_cols:
                try:
                    conn.execute(text("ALTER TABLE inventory_transactions ADD COLUMN conversion_rate REAL DEFAULT 1.0;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (tx.conversion_rate): {ex}")
            if "base_quantity" not in tx_cols:
                try:
                    conn.execute(text("ALTER TABLE inventory_transactions ADD COLUMN base_quantity REAL DEFAULT 0.0;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (tx.base_quantity): {ex}")

            pb_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(price_books)")).fetchall()]
            if pb_cols:
                if "version" not in pb_cols:
                    try:
                        conn.execute(text("ALTER TABLE price_books ADD COLUMN version INTEGER DEFAULT 1;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (price_books.version): {ex}")
                if "is_locked" not in pb_cols:
                    try:
                        conn.execute(text("ALTER TABLE price_books ADD COLUMN is_locked INTEGER DEFAULT 0;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (price_books.is_locked): {ex}")

            pbi_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(price_book_items)")).fetchall()]
            if pbi_cols:
                if "sale_price" not in pbi_cols:
                    try:
                        conn.execute(text("ALTER TABLE price_book_items ADD COLUMN sale_price REAL;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (price_book_items.sale_price): {ex}")
                if "floor_price" not in pbi_cols:
                    try:
                        conn.execute(text("ALTER TABLE price_book_items ADD COLUMN floor_price REAL DEFAULT 0.0;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (price_book_items.floor_price): {ex}")

            if "floor_price" not in product_cols:
                try:
                    conn.execute(text("ALTER TABLE products ADD COLUMN floor_price REAL DEFAULT 0.0;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (products.floor_price): {ex}")

            # Migration bảng delivery_trips (SCRUM-84):
            dt_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(delivery_trips)")).fetchall()]
            if dt_cols:
                if "license_plate" in dt_cols and "vehicle_plate" in dt_cols:
                    conn.execute(text("UPDATE delivery_trips SET vehicle_plate = license_plate WHERE (vehicle_plate IS NULL OR vehicle_plate = '') AND license_plate IS NOT NULL;"))
                if "departure_time" in dt_cols and "dispatched_at" in dt_cols:
                    conn.execute(text("UPDATE delivery_trips SET dispatched_at = departure_time WHERE dispatched_at IS NULL AND departure_time IS NOT NULL;"))
                if "expected_completion_time" in dt_cols and "estimated_delivery_time" in dt_cols:
                    conn.execute(text("UPDATE delivery_trips SET estimated_delivery_time = expected_completion_time WHERE estimated_delivery_time IS NULL AND expected_completion_time IS NOT NULL;"))
                if "actual_completion_time" in dt_cols and "completed_at" in dt_cols:
                    conn.execute(text("UPDATE delivery_trips SET completed_at = actual_completion_time WHERE completed_at IS NULL AND actual_completion_time IS NOT NULL;"))
                conn.commit()

                # Xóa index của cột cũ trước khi drop column
                for idx_name in [
                    "ix_delivery_trips_expected_completion_time",
                    "ix_delivery_trips_departure_time",
                    "ix_delivery_trips_license_plate",
                    "ix_delivery_trips_actual_completion_time",
                ]:
                    try:
                        conn.execute(text(f"DROP INDEX IF EXISTS {idx_name};"))
                        conn.commit()
                    except Exception:
                        pass

                for old_c in ["license_plate", "departure_time", "expected_completion_time", "actual_completion_time"]:
                    if old_c in dt_cols:
                        try:
                            conn.execute(text(f"ALTER TABLE delivery_trips DROP COLUMN {old_c};"))
                            conn.commit()
                        except Exception as ex:
                            print(f"SQLite migration notice (delivery_trips DROP COLUMN {old_c}): {ex}")

                try:
                    conn.execute(text("DELETE FROM delivery_trips WHERE trip_code LIKE 'TRIP-DEMO-%' AND NOT EXISTS (SELECT 1 FROM delivery_stops WHERE delivery_stops.trip_id = delivery_trips.id);"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite cleanup notice (dummy delivery_trips): {ex}")

            # Migration bảng delivery_stops (SCRUM-85):
            ds_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(delivery_stops)")).fetchall()]
            if ds_cols:
                if "actual_delivery_time" not in ds_cols:
                    try:
                        conn.execute(text("ALTER TABLE delivery_stops ADD COLUMN actual_delivery_time DATETIME;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (delivery_stops.actual_delivery_time): {ex}")
                if "pod_image_url" not in ds_cols:
                    try:
                        conn.execute(text("ALTER TABLE delivery_stops ADD COLUMN pod_image_url TEXT;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (delivery_stops.pod_image_url): {ex}")
                if "delivery_status" not in ds_cols:
                    try:
                        conn.execute(text("ALTER TABLE delivery_stops ADD COLUMN delivery_status VARCHAR(30);"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (delivery_stops.delivery_status): {ex}")
                if "failure_reason" not in ds_cols:
                    try:
                        conn.execute(text("ALTER TABLE delivery_stops ADD COLUMN failure_reason TEXT;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (delivery_stops.failure_reason): {ex}")
                if "return_status" not in ds_cols:
                    try:
                        conn.execute(text("ALTER TABLE delivery_stops ADD COLUMN return_status VARCHAR(50);"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (delivery_stops.return_status): {ex}")
                if "items_detail" not in ds_cols:
                    try:
                        conn.execute(text("ALTER TABLE delivery_stops ADD COLUMN items_detail TEXT;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (delivery_stops.items_detail): {ex}")

                # Đồng bộ dữ liệu cũ nếu có
                try:
                    conn.execute(text("UPDATE delivery_stops SET actual_delivery_time = delivered_at WHERE actual_delivery_time IS NULL AND delivered_at IS NOT NULL;"))
                    conn.execute(text("UPDATE delivery_stops SET failure_reason = failed_reason WHERE failure_reason IS NULL AND failed_reason IS NOT NULL;"))
                    conn.execute(text("UPDATE delivery_stops SET delivery_status = status WHERE delivery_status IS NULL AND status IS NOT NULL;"))
                    conn.commit()
                except Exception:
                    pass
        else:
            for sql_statement in [
                "IF COL_LENGTH('users', 'avatar_url') IS NULL ALTER TABLE users ADD avatar_url NVARCHAR(500);",
                "IF COL_LENGTH('products', 'category_id') IS NULL ALTER TABLE products ADD category_id INT NULL;",
                "IF COL_LENGTH('products', 'base_unit') IS NULL ALTER TABLE products ADD base_unit NVARCHAR(50) DEFAULT N'Cái';",
                "IF COL_LENGTH('products', 'floor_price') IS NULL ALTER TABLE products ADD floor_price FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('products', 'units_json') IS NULL ALTER TABLE products ADD units_json NVARCHAR(MAX);",
                "IF COL_LENGTH('products', 'min_stock') IS NULL ALTER TABLE products ADD min_stock INT DEFAULT 10;",
                "IF COL_LENGTH('warehouse_stocks', 'min_stock') IS NULL ALTER TABLE warehouse_stocks ADD min_stock INT DEFAULT 10;",
                "IF COL_LENGTH('inventory_transactions', 'unit_name') IS NULL ALTER TABLE inventory_transactions ADD unit_name NVARCHAR(50) DEFAULT N'Cái';",
                "IF COL_LENGTH('inventory_transactions', 'conversion_rate') IS NULL ALTER TABLE inventory_transactions ADD conversion_rate FLOAT DEFAULT 1.0;",
                "IF COL_LENGTH('inventory_transactions', 'base_quantity') IS NULL ALTER TABLE inventory_transactions ADD base_quantity FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('dealers', 'customer_group') IS NULL ALTER TABLE dealers ADD customer_group NVARCHAR(100) DEFAULT N'Đại lý cấp 1';",
                "IF COL_LENGTH('dealers', 'status') IS NULL ALTER TABLE dealers ADD status VARCHAR(20) DEFAULT 'ACTIVE';",
                "IF COL_LENGTH('dealers', 'region') IS NULL ALTER TABLE dealers ADD region NVARCHAR(100);",
                "IF COL_LENGTH('dealers', 'lock_reason') IS NULL ALTER TABLE dealers ADD lock_reason NVARCHAR(500);",
                "IF COL_LENGTH('dealers', 'locked_at') IS NULL ALTER TABLE dealers ADD locked_at DATETIME;",
                "IF COL_LENGTH('dealers', 'locked_by') IS NULL ALTER TABLE dealers ADD locked_by VARCHAR(50);",
                "IF COL_LENGTH('dealers', 'max_debt_days') IS NULL ALTER TABLE dealers ADD max_debt_days INT DEFAULT 30;",
                "IF COL_LENGTH('price_books', 'version') IS NULL ALTER TABLE price_books ADD version INT DEFAULT 1;",
                "IF COL_LENGTH('price_books', 'is_locked') IS NULL ALTER TABLE price_books ADD is_locked BIT DEFAULT 0;",
                "IF COL_LENGTH('price_book_items', 'sale_price') IS NULL ALTER TABLE price_book_items ADD sale_price FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('price_book_items', 'floor_price') IS NULL ALTER TABLE price_book_items ADD floor_price FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('products', 'min_stock') IS NULL ALTER TABLE products ADD min_stock INT DEFAULT 10;",
                "IF COL_LENGTH('warehouse_stocks', 'min_stock') IS NULL ALTER TABLE warehouse_stocks ADD min_stock INT DEFAULT 10;",
                "IF COL_LENGTH('warehouse_stocks', 'quantity_on_hand') IS NULL ALTER TABLE warehouse_stocks ADD quantity_on_hand INT DEFAULT 0;",
                "IF COL_LENGTH('warehouse_stocks', 'reserved_quantity') IS NULL ALTER TABLE warehouse_stocks ADD reserved_quantity INT DEFAULT 0;",
                "IF COL_LENGTH('orders', 'reservation_expires_at') IS NULL ALTER TABLE orders ADD reservation_expires_at DATETIME;",
                "IF COL_LENGTH('discount_tiers', 'discount_amount') IS NULL ALTER TABLE discount_tiers ADD discount_amount FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('discount_tiers', 'discount_type') IS NULL ALTER TABLE discount_tiers ADD discount_type NVARCHAR(20) DEFAULT 'percent';",
                "UPDATE warehouse_stocks SET quantity_on_hand = actual_stock WHERE quantity_on_hand IS NULL OR quantity_on_hand = 0;",
                "UPDATE warehouse_stocks SET reserved_quantity = reserved_stock WHERE reserved_quantity IS NULL;",
                "IF COL_LENGTH('delivery_trips', 'license_plate') IS NOT NULL AND COL_LENGTH('delivery_trips', 'vehicle_plate') IS NOT NULL EXEC('UPDATE delivery_trips SET vehicle_plate = license_plate WHERE (vehicle_plate IS NULL OR vehicle_plate = '''') AND license_plate IS NOT NULL;');",
                "IF COL_LENGTH('delivery_trips', 'departure_time') IS NOT NULL AND COL_LENGTH('delivery_trips', 'dispatched_at') IS NOT NULL EXEC('UPDATE delivery_trips SET dispatched_at = departure_time WHERE dispatched_at IS NULL AND departure_time IS NOT NULL;');",
                "IF COL_LENGTH('delivery_trips', 'expected_completion_time') IS NOT NULL AND COL_LENGTH('delivery_trips', 'estimated_delivery_time') IS NOT NULL EXEC('UPDATE delivery_trips SET estimated_delivery_time = expected_completion_time WHERE estimated_delivery_time IS NULL AND expected_completion_time IS NOT NULL;');",
                "IF COL_LENGTH('delivery_trips', 'license_plate') IS NOT NULL ALTER TABLE delivery_trips DROP COLUMN license_plate;",
                "IF COL_LENGTH('delivery_trips', 'departure_time') IS NOT NULL ALTER TABLE delivery_trips DROP COLUMN departure_time;",
                "IF COL_LENGTH('delivery_trips', 'expected_completion_time') IS NOT NULL ALTER TABLE delivery_trips DROP COLUMN expected_completion_time;",
                "IF COL_LENGTH('delivery_trips', 'actual_completion_time') IS NOT NULL ALTER TABLE delivery_trips DROP COLUMN actual_completion_time;",
                "IF COL_LENGTH('delivery_stops', 'actual_delivery_time') IS NULL ALTER TABLE delivery_stops ADD actual_delivery_time DATETIME;",
                "IF COL_LENGTH('delivery_stops', 'pod_image_url') IS NULL ALTER TABLE delivery_stops ADD pod_image_url NVARCHAR(500);",
                "IF COL_LENGTH('delivery_stops', 'delivery_status') IS NULL ALTER TABLE delivery_stops ADD delivery_status VARCHAR(30);",
                "IF COL_LENGTH('delivery_stops', 'failure_reason') IS NULL ALTER TABLE delivery_stops ADD failure_reason NVARCHAR(MAX);",
                "IF COL_LENGTH('delivery_stops', 'return_status') IS NULL ALTER TABLE delivery_stops ADD return_status NVARCHAR(50);",
                "IF COL_LENGTH('delivery_stops', 'items_detail') IS NULL ALTER TABLE delivery_stops ADD items_detail NVARCHAR(MAX);",
                "IF COL_LENGTH('delivery_stops', 'actual_delivery_time') IS NOT NULL AND COL_LENGTH('delivery_stops', 'delivered_at') IS NOT NULL EXEC('UPDATE delivery_stops SET actual_delivery_time = delivered_at WHERE actual_delivery_time IS NULL AND delivered_at IS NOT NULL');",
                "IF COL_LENGTH('delivery_stops', 'failure_reason') IS NOT NULL AND COL_LENGTH('delivery_stops', 'failed_reason') IS NOT NULL EXEC('UPDATE delivery_stops SET failure_reason = failed_reason WHERE failure_reason IS NULL AND failed_reason IS NOT NULL');",
                "IF COL_LENGTH('delivery_stops', 'delivery_status') IS NOT NULL AND COL_LENGTH('delivery_stops', 'status') IS NOT NULL EXEC('UPDATE delivery_stops SET delivery_status = status WHERE delivery_status IS NULL AND status IS NOT NULL');",
                "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_warehouse_stocks_wh_stock_prod') CREATE NONCLUSTERED INDEX ix_warehouse_stocks_wh_stock_prod ON warehouse_stocks (warehouse_id, actual_stock) INCLUDE (product_id, reserved_stock, min_stock);",
                "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_warehouse_stocks_prod_wh') CREATE NONCLUSTERED INDEX ix_warehouse_stocks_prod_wh ON warehouse_stocks (product_id, warehouse_id) INCLUDE (actual_stock, reserved_stock, min_stock);",
                "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_inv_tx_prod_id') CREATE NONCLUSTERED INDEX ix_inv_tx_prod_id ON inventory_transactions (product_id);",
            ]:
                try:
                    conn.execute(text(sql_statement))
                    conn.commit()
                except Exception as ex:
                    print(f"SQL Server migration notice ({sql_statement[:35]}...): {ex}")

    db = SessionLocal()
    try:
        # 1. Seed Users nếu chưa có
        if db.query(UserEntity).count() == 0:
            print("Seeding initial users...")
            initial_users = [
                UserEntity(
                    id=1,
                    username="admin",
                    hashed_password=get_password_hash("123"),
                    full_name="Đào Ngọc Hiệp",
                    email="daongochiep645@gmail.com",
                    phone="0998887766",
                    role=Role.SYSTEM_ADMIN.value,
                    roles_json=json.dumps([Role.SYSTEM_ADMIN.value]),
                    is_active=True,
                ),
                UserEntity(
                    id=2,
                    username="sales_manager",
                    hashed_password=get_password_hash("123"),
                    full_name="Phạm Trưởng Phòng Sales",
                    email="sales.manager@congty.vn",
                    phone="0822516998",
                    role=Role.SALES_MANAGER.value,
                    roles_json=json.dumps([Role.SALES_MANAGER.value]),
                    is_active=True,
                ),
                UserEntity(
                    id=3,
                    username="sales",
                    hashed_password=get_password_hash("123"),
                    full_name="Trần Bán Hàng",
                    email="sales@congty.vn",
                    role=Role.SALES.value,
                    roles_json=json.dumps([Role.SALES.value]),
                    is_active=True,
                ),
                UserEntity(
                    id=4,
                    username="kho",
                    hashed_password=get_password_hash("123"),
                    full_name="Lê Thủ Kho",
                    email="kho@congty.vn",
                    role=Role.WAREHOUSE_STAFF.value,
                    roles_json=json.dumps([Role.WAREHOUSE_STAFF.value]),
                    is_active=True,
                ),
            ]
            db.add_all(initial_users)
            db.commit()
            print("Users seeded successfully.")

        # Đồng bộ và chuẩn hóa tài khoản Quản trị viên chính thức duy nhất: Đào Ngọc Hiệp
        try:
            admin_ent = db.query(UserEntity).filter(UserEntity.username == "admin").first()
            if admin_ent:
                admin_ent.full_name = "Đào Ngọc Hiệp"
                admin_ent.email = "daongochiep645@gmail.com"
                admin_ent.phone = "0998887766"
                admin_ent.role = Role.SYSTEM_ADMIN.value
                db.commit()

            # Dọn dẹp triệt để tài khoản quản trị ảo / trùng lặp trên SQL Server
            dup_admins = db.query(UserEntity).filter(
                (UserEntity.username.in_(["quanlybanhang", "admin_virtual", "admin_mock"])) |
                ((UserEntity.role == "admin") & (UserEntity.username != "admin"))
            ).all()
            for dup in dup_admins:
                db.delete(dup)
            if dup_admins:
                db.commit()
        except Exception as u_sync_err:
            print(f"Notice syncing admin user: {u_sync_err}")

        # 2. Seed Categories nếu chưa có
        if db.query(CategoryEntity).count() == 0:
            print("Seeding initial categories...")
            initial_categories = [
                CategoryEntity(id=1, code="CAT01", name="Thời trang", parent_id=None),
                CategoryEntity(id=2, code="CAT02", name="Giày dép", parent_id=None),
                CategoryEntity(id=3, code="CAT03", name="Phụ kiện", parent_id=None),
                CategoryEntity(id=7, code="CAT07", name="Đồ điện tử", parent_id=None),
                CategoryEntity(id=4, code="CAT04", name="Áo Nam", parent_id=1),
                CategoryEntity(id=5, code="CAT05", name="Quần Nam", parent_id=1),
                CategoryEntity(id=6, code="CAT06", name="Áo Thun", parent_id=4),
            ]
            db.add_all(initial_categories)
            db.commit()
            print("Categories seeded successfully.")

        # 3. Seed Products nếu chưa có
        if db.query(ProductEntity).count() == 0:
            print("Seeding initial products...")
            initial_products = [
                ProductEntity(id=1, code="SP001", name="Áo thun Polo Nam Cao Cấp", category="Thời trang", category_id=6, stock=120, cost_price=85000.0, sell_price=199000.0, base_unit="Cái", units_json=json.dumps([{"unit_name": "Lốc", "conversion_rate": 6.0}, {"unit_name": "Thùng", "conversion_rate": 24.0}], ensure_ascii=False)),
                ProductEntity(id=2, code="SP002", name="Quần Jeans Slimfit Co Giãn", category="Thời trang", category_id=5, stock=45, cost_price=160000.0, sell_price=380000.0, base_unit="Chiếc", units_json=json.dumps([{"unit_name": "Kiện", "conversion_rate": 10.0}], ensure_ascii=False)),
                ProductEntity(id=3, code="SP003", name="Áo khoác Bomber Chống Nước", category="Thời trang", category_id=4, stock=30, cost_price=220000.0, sell_price=490000.0, base_unit="Chiếc", units_json=json.dumps([], ensure_ascii=False)),
                ProductEntity(id=4, code="SP004", name="Giày Sneaker Thể Thao", category="Giày dép", category_id=2, stock=65, cost_price=310000.0, sell_price=650000.0, base_unit="Đôi", units_json=json.dumps([{"unit_name": "Thùng", "conversion_rate": 12.0}], ensure_ascii=False)),
                ProductEntity(id=5, code="SP005", name="Thắt lưng da bò nguyên tấm", category="Phụ kiện", category_id=3, stock=80, cost_price=95000.0, sell_price=250000.0, base_unit="Chiếc", units_json=json.dumps([{"unit_name": "Hộp", "conversion_rate": 5.0}], ensure_ascii=False)),
            ]
            db.add_all(initial_products)
            db.commit()
            print("Products seeded successfully.")

        # 4. Seed Dealers nếu chưa có
        if db.query(DealerEntity).count() == 0:
            print("Seeding initial dealers...")
            initial_dealers = [
                DealerEntity(
                    id=1,
                    code="DL001",
                    name="Đại Lý Toàn Thắng",
                    phone="0912345678",
                    address="123 Lê Lợi, Quận 1, TP. HCM",
                    debt=15000000.0,
                    credit_limit=50000000.0,
                    customer_group="Đại lý cấp 1",
                    status="ACTIVE",
                    region="Miền Nam",
                    assigned_sale_id=3,
                    assigned_sale_name="Nhân Viên Bán Hàng",
                ),
                DealerEntity(
                    id=2,
                    code="DL002",
                    name="Đại Lý Minh Khang",
                    phone="0987654321",
                    address="456 Hoàng Hoa Thám, Ba Đình, Hà Nội",
                    debt=42000000.0,
                    credit_limit=60000000.0,
                    customer_group="Đại lý cấp 2",
                    status="ACTIVE",
                    region="Miền Bắc",
                    assigned_sale_id=3,
                    assigned_sale_name="Nhân Viên Bán Hàng",
                ),
                DealerEntity(
                    id=3,
                    code="DL003",
                    name="Cửa Hàng Thời Trang Hạnh Phúc",
                    phone="0905123456",
                    address="789 Nguyễn Văn Linh, Hải Châu, Đà Nẵng",
                    debt=0.0,
                    credit_limit=30000000.0,
                    customer_group="Khách lẻ thân thiết",
                    status="ACTIVE",
                    region="Miền Trung",
                    assigned_sale_id=2,
                    assigned_sale_name="Trưởng Phòng Kinh Doanh",
                ),
            ]
            db.add_all(initial_dealers)
            db.commit()
            print("Dealers seeded successfully.")

        # 5. Seed Dealer Delivery Points nếu chưa có
        if db.query(DealerDeliveryPointEntity).count() == 0:
            print("Seeding initial dealer delivery points...")
            points = [
                DealerDeliveryPointEntity(dealer_id=1, label="Kho tổng Lê Lợi", address="123 Lê Lợi, Q1, TP. HCM", receiver_name="Đại Lý Toàn Thắng", receiver_phone="0912345678", is_default=True),
                DealerDeliveryPointEntity(dealer_id=1, label="Chi nhánh Bình Thạnh", address="45 Xô Viết Nghệ Tĩnh, Bình Thạnh, TP. HCM", receiver_name="Đại Lý Toàn Thắng", receiver_phone="0912345678", is_default=False),
                DealerDeliveryPointEntity(dealer_id=2, label="Cửa hàng Ba Đình", address="456 Hoàng Hoa Thám, Ba Đình, Hà Nội", receiver_name="Đại Lý Minh Khang", receiver_phone="0987654321", is_default=True),
                DealerDeliveryPointEntity(dealer_id=3, label="Cơ sở Hải Châu", address="789 Nguyễn Văn Linh, Đà Nẵng", receiver_name="Cửa Hàng Hạnh Phúc", receiver_phone="0905123456", is_default=True),
            ]
            db.add_all(points)
            db.commit()
            print("Dealer delivery points seeded successfully.")

        # 6. Seed Suppliers nếu chưa có
        if db.query(SupplierEntity).count() == 0:
            print("Seeding initial suppliers...")
            initial_suppliers = [
                SupplierEntity(id=1, code="NCC001", name="Công ty Cổ phần May Việt Tiến", tax_code="0300123456", contact_person="Nguyễn Văn Việt", payment_terms="Thanh toán 30 ngày", is_active=True),
                SupplierEntity(id=2, code="NCC002", name="Tập đoàn Dệt May Phong Phú", tax_code="0300987654", contact_person="Trần Phong Phú", payment_terms="Thanh toán ngay", is_active=True),
                SupplierEntity(id=3, code="NCC003", name="Công ty TNHH Giày Biti's Việt Nam", tax_code="0301112233", contact_person="Lê Bitis", payment_terms="Thanh toán 45 ngày", is_active=True),
            ]
            db.add_all(initial_suppliers)
            db.commit()
            print("Suppliers seeded successfully.")

        # 7. Seed Warehouses nếu chưa có
        if db.query(WarehouseEntity).count() == 0:
            print("Seeding initial warehouses...")
            initial_warehouses = [
                WarehouseEntity(id=1, code="KHO_HN", name="Kho Tổng Hà Nội", address="Lô CN1 KCN Từ Liêm, Bắc Từ Liêm, Hà Nội", manager_name="Nguyễn Văn Kho", phone="0901234567", status="Đang hoạt động", is_active=True),
                WarehouseEntity(id=2, code="KHO_DN", name="Kho Chi Nhánh Đà Nẵng", address="KCN Hòa Khánh, Liên Chiểu, Đà Nẵng", manager_name="Lê Thị Vận Chuyển", phone="0912345678", status="Đang hoạt động", is_active=True),
                WarehouseEntity(id=3, code="KHO_HCM", name="Kho Chi Nhánh TP. Hồ Chí Minh", address="Khu chế xuất Tân Thuận, Quận 7, TP. HCM", manager_name="Phạm Văn Thủ Kho", phone="0987654321", status="Đang hoạt động", is_active=True),
            ]
            db.add_all(initial_warehouses)
            db.commit()
            print("Warehouses seeded successfully.")
        else:
            # Cập nhật thông tin người quản lý và số điện thoại nếu còn thiếu
            wh1 = db.query(WarehouseEntity).filter(WarehouseEntity.id == 1).first()
            if wh1 and not wh1.manager_name:
                wh1.manager_name = "Nguyễn Văn Kho"
                wh1.phone = "0901234567"
                wh1.status = "Đang hoạt động"
            wh2 = db.query(WarehouseEntity).filter(WarehouseEntity.id == 2).first()
            if wh2 and not wh2.manager_name:
                wh2.manager_name = "Lê Thị Vận Chuyển"
                wh2.phone = "0912345678"
                wh2.status = "Đang hoạt động"
            wh3 = db.query(WarehouseEntity).filter(WarehouseEntity.id == 3).first()
            if wh3 and not wh3.manager_name:
                wh3.manager_name = "Phạm Văn Thủ Kho"
                wh3.phone = "0987654321"
                wh3.status = "Đang hoạt động"
            db.commit()

        # 7.1 Seed Warehouse Zones (Khu vực lưu kho) nếu chưa có
        if db.query(WarehouseZoneEntity).count() == 0:
            print("Seeding initial warehouse zones...")
            initial_zones = [
                WarehouseZoneEntity(id=1, warehouse_id=1, zone_code="KHU-A", zone_name="Khu A", description="Khu vực lưu trữ thời trang, áo sơ mi, áo polo", is_active=True),
                WarehouseZoneEntity(id=2, warehouse_id=1, zone_code="KHU-B", zone_name="Khu B", description="Khu vực lưu trữ quần âu, quần jeans, kaki", is_active=True),
                WarehouseZoneEntity(id=3, warehouse_id=1, zone_code="KHU-C", zone_name="Khu C", description="Khu vực lưu trữ phụ kiện, áo khoác, đồ thể thao", is_active=True),
                WarehouseZoneEntity(id=4, warehouse_id=2, zone_code="KHU-MT", zone_name="Khu Miền Trung", description="Khu trung chuyển hàng miền Trung", is_active=True),
                WarehouseZoneEntity(id=5, warehouse_id=3, zone_code="KHU-MN", zone_name="Khu Nam Bộ", description="Khu phân phối thị trường miền Nam", is_active=True),
            ]
            db.add_all(initial_zones)
            db.commit()
            print("Warehouse zones seeded successfully.")

        # 7.2 Seed Warehouse Racks (Dãy & Tầng/Kệ theo từng Khu) nếu chưa có
        if db.query(WarehouseRackEntity).count() == 0:
            print("Seeding initial warehouse racks...")
            initial_racks = []
            for z_code, z_name, z_id in [("KA", "Khu A", 1), ("KB", "Khu B", 2), ("KC", "Khu C", 3)]:
                for a_idx in [1, 2, 3]:
                    for t_idx in [1, 2]:
                        initial_racks.append(
                            WarehouseRackEntity(
                                warehouse_id=1,
                                rack_code=f"{z_code}-D{a_idx}-T{t_idx}",
                                rack_name=f"Dãy {a_idx} - Tầng {t_idx}",
                                rack_type="rack",
                                zone_id=z_id,
                                max_capacity=500.0,
                                is_active=True
                            )
                        )
            initial_racks.extend([
                WarehouseRackEntity(warehouse_id=2, rack_code="DN-D1", rack_name="Dãy 1", rack_type="aisle", max_capacity=1000.0, is_active=True),
                WarehouseRackEntity(warehouse_id=2, rack_code="DN-T1", rack_name="Tầng 1", rack_type="rack", max_capacity=600.0, is_active=True),
                WarehouseRackEntity(warehouse_id=3, rack_code="HCM-D1", rack_name="Dãy 1", rack_type="aisle", max_capacity=2000.0, is_active=True),
                WarehouseRackEntity(warehouse_id=3, rack_code="HCM-T1", rack_name="Tầng 1", rack_type="rack", max_capacity=1000.0, is_active=True),
            ])
            db.add_all(initial_racks)
            db.commit()
            print("Warehouse racks seeded successfully.")

        # 7.3 Seed Warehouse Locations (Khu vực / Dãy / Tầng / Ô chứa đồng nhất) nếu chưa có
        if db.query(WarehouseLocationEntity).count() == 0:
            print("Seeding initial warehouse locations...")
            initial_locs = []
            loc_id_counter = 1
            for z_letter, z_name in [("A", "Khu A"), ("B", "Khu B"), ("C", "Khu C")]:
                for a_idx in [1, 2, 3]:
                    for t_idx in [1, 2]:
                        bin_str = f"Ô 0{t_idx}"
                        initial_locs.append(
                            WarehouseLocationEntity(
                                id=loc_id_counter,
                                warehouse_id=1,
                                location_code=f"HN-K{z_letter}-D{a_idx}-T{t_idx}",
                                location_name=f"{z_name} - Dãy {a_idx} - Tầng {t_idx} - {bin_str}",
                                zone=z_name,
                                aisle=f"Dãy {a_idx}",
                                rack=f"Tầng {t_idx}",
                                bin=bin_str,
                                max_capacity=500.0,
                                is_active=True,
                                status="Đang sử dụng",
                                note="",
                            )
                        )
                        loc_id_counter += 1
            initial_locs.append(WarehouseLocationEntity(id=loc_id_counter, warehouse_id=2, location_code="DN-K1-D1-T1", location_name="Khu 1 - Dãy 1 - Tầng 1 - Ô 01", zone="Khu Miền Trung", aisle="Dãy 1", rack="Tầng 1", bin="Ô 01", max_capacity=600.0, is_active=True, status="Đang sử dụng", note=""))
            loc_id_counter += 1
            initial_locs.append(WarehouseLocationEntity(id=loc_id_counter, warehouse_id=3, location_code="HCM-KA-D1-T1", location_name="Khu A - Dãy 1 - Tầng 1 - Ô 01", zone="Khu Nam Bộ", aisle="Dãy 1", rack="Tầng 1", bin="Ô 01", max_capacity=1000.0, is_active=True, status="Đang sử dụng", note=""))
            db.add_all(initial_locs)
            db.commit()
            print("Warehouse locations seeded successfully.")

        # 7.4 Seed Location Stocks (Mỗi vị trí có 2 sản phẩm khác nhau) nếu chưa có
        if db.query(LocationStockEntity).count() == 0:
            print("Seeding initial location stocks...")
            pairs = [
                (1, 2, 45, 35), (1, 3, 30, 40), (2, 4, 35, 25),
                (3, 5, 40, 30), (1, 4, 35, 30), (2, 5, 40, 25),
                (2, 1, 50, 30), (2, 3, 45, 35), (2, 4, 40, 35),
                (2, 5, 45, 30), (3, 1, 35, 35), (4, 2, 40, 30),
                (3, 5, 40, 35), (4, 1, 35, 30), (5, 2, 45, 25),
                (3, 4, 30, 40), (5, 1, 45, 25), (4, 3, 40, 35),
            ]
            initial_stocks = []
            for l_id in range(1, 19):
                p1_id, p2_id, q1, q2 = pairs[(l_id - 1) % len(pairs)]
                initial_stocks.append(LocationStockEntity(warehouse_id=1, location_id=l_id, product_id=p1_id, quantity=q1))
                initial_stocks.append(LocationStockEntity(warehouse_id=1, location_id=l_id, product_id=p2_id, quantity=q2))
            initial_stocks.append(LocationStockEntity(warehouse_id=3, location_id=20, product_id=3, quantity=40))
            db.add_all(initial_stocks)
            db.commit()
            print("Location stocks seeded successfully.")

        # 8. Seed Units of Measure nếu chưa có
        if db.query(UnitOfMeasureEntity).count() == 0:
            print("Seeding initial units of measure...")
            initial_uoms = [
                UnitOfMeasureEntity(id=1, code="CAI", name="Cái", description="Đơn vị đếm cơ bản"),
                UnitOfMeasureEntity(id=2, code="HOP", name="Hộp", description="Hộp đóng gói nhỏ"),
                UnitOfMeasureEntity(id=3, code="THUNG", name="Thùng", description="Thùng carton tiêu chuẩn"),
                UnitOfMeasureEntity(id=4, code="BO", name="Bộ", description="Bộ sản phẩm hoàn chỉnh"),
                UnitOfMeasureEntity(id=5, code="KIEN", name="Kiện", description="Kiện hàng lớn"),
            ]
            db.add_all(initial_uoms)
            db.commit()
            print("Units of measure seeded successfully.")

        # 9. Seed Inventory Ledger nếu chưa có
        if db.query(InventoryLedgerEntity).count() == 0:
            print("Seeding initial inventory ledger...")
            ledgers = [
                InventoryLedgerEntity(
                    warehouse_id=1,
                    product_id=1,
                    reference_code="DK-001",
                    transaction_type="RECEIPT",
                    quantity=120,
                    previous_stock=0,
                    new_stock=120,
                    note="Tồn đầu kỳ hệ thống",
                ),
                InventoryLedgerEntity(
                    warehouse_id=1,
                    product_id=2,
                    reference_code="DK-002",
                    transaction_type="RECEIPT",
                    quantity=45,
                    previous_stock=0,
                    new_stock=45,
                    note="Tồn đầu kỳ hệ thống",
                ),
                InventoryLedgerEntity(
                    warehouse_id=1,
                    product_id=3,
                    reference_code="DK-003",
                    transaction_type="RECEIPT",
                    quantity=30,
                    previous_stock=0,
                    new_stock=30,
                    note="Tồn đầu kỳ hệ thống",
                ),
            ]
            db.add_all(ledgers)
            db.commit()
            print("Inventory ledger seeded successfully.")

        # 10. Seed Discount Policies nếu chưa có
        if db.query(DiscountPolicyEntity).count() == 0:
            print("Seeding initial volume discount policies...")
            policies = [
                DiscountPolicyEntity(
                    id=1,
                    code="CK-SL-001",
                    name="Chiết khấu số lượng Áo Thun Polo",
                    title="Ưu đãi bậc thang khi mua số lượng lớn cho mã SP001",
                    description="Ưu đãi bậc thang khi mua số lượng lớn cho mã SP001",
                    category="ALL",
                    is_active=True,
                    status="active",
                    tiers=[
                        DiscountTierEntity(min_quantity=10, max_quantity=29, discount_percent=5.0),
                        DiscountTierEntity(min_quantity=30, max_quantity=99, discount_percent=10.0),
                        DiscountTierEntity(min_quantity=100, max_quantity=None, discount_percent=15.0),
                    ]
                ),
                DiscountPolicyEntity(
                    id=2,
                    code="CK-SL-002",
                    name="Chiết khấu số lượng Quần Jeans Slimfit",
                    title="Ưu đãi bậc thang khi mua số lượng lớn cho mã SP002",
                    description="Ưu đãi bậc thang khi mua số lượng lớn cho mã SP002",
                    category="ALL",
                    is_active=True,
                    status="active",
                    tiers=[
                        DiscountTierEntity(min_quantity=20, max_quantity=49, discount_percent=8.0),
                        DiscountTierEntity(min_quantity=50, max_quantity=None, discount_percent=12.0),
                    ]
                )
            ]
            db.add_all(policies)
            db.commit()
            print("Discount policies seeded successfully.")

        from app.models.dealer import load_dealers_db
        load_dealers_db()
        from app.api.v1.endpoints.discounts import load_discount_policies
        load_discount_policies(db)

        # 10. Đồng bộ tồn kho tổng của ProductEntity từ tổng tồn thực tế của các chi nhánh kho (warehouse_stocks)
        try:
            db.execute(text("UPDATE products SET status = 'active' WHERE status IS NULL OR status = ''"))
            db.commit()
            from sqlalchemy import func
            DEFAULT_WAREHOUSES = {
                "WH01": "Kho Tổng Hà Nội",
                "WH02": "Kho Chi Nhánh TP.HCM",
                "WH03": "Kho Chi Nhánh Đà Nẵng",
            }
            existing_ws = {(ws.product_id, ws.warehouse_id) for ws in db.query(WarehouseStockEntity).all()}
            prods = db.query(ProductEntity).all()
            for p in prods:
                base_stk = p.stock or 100
                for wid, wname in DEFAULT_WAREHOUSES.items():
                    if (p.id, wid) not in existing_ws:
                        if wid == "WH01":
                            act = max(10, base_stk)
                        elif wid == "WH02":
                            act = max(10, int(base_stk * 0.7))
                        elif wid == "WH03":
                            act = max(5, int(base_stk * 0.5))
                        else:
                            act = max(10, base_stk)
                        ws_new = WarehouseStockEntity(
                            warehouse_id=wid,
                            warehouse_name=wname,
                            product_id=p.id,
                            actual_stock=act,
                            reserved_stock=0,
                            min_stock=p.min_stock or 10,
                        )
                        db.add(ws_new)
                        existing_ws.add((p.id, wid))
            db.flush()

            # Đảm bảo actual_stock trong warehouse_stocks >= tổng tồn trên các kệ + 50 (cho tồn chưa phân bổ)
            loc_sums = db.query(
                LocationStockEntity.warehouse_id,
                LocationStockEntity.product_id,
                func.sum(LocationStockEntity.quantity)
            ).group_by(LocationStockEntity.warehouse_id, LocationStockEntity.product_id).all()
            for wid_int, pid, total_loc_qty in loc_sums:
                target_wid = "WH01" if wid_int == 1 else ("WH03" if wid_int == 2 else ("WH02" if wid_int == 3 else f"WH{wid_int:02d}"))
                ws_rec = db.query(WarehouseStockEntity).filter(
                    WarehouseStockEntity.warehouse_id == target_wid,
                    WarehouseStockEntity.product_id == pid
                ).first()
                if ws_rec:
                    needed = int(total_loc_qty) + 50
                    if (ws_rec.actual_stock or 0) < needed:
                        ws_rec.actual_stock = needed
            db.flush()

            wh_rows = db.query(WarehouseStockEntity.product_id, func.sum(WarehouseStockEntity.actual_stock)).group_by(WarehouseStockEntity.product_id).all()
            wh_map = {pid: int(val) for pid, val in wh_rows if val is not None}
            for p in prods:
                if p.id in wh_map and wh_map[p.id] is not None:
                    if p.stock != wh_map[p.id]:
                        p.stock = wh_map[p.id]
            db.commit()
            print("Product stocks synchronized from warehouses successfully.")
        except Exception as sync_err:
            print(f"Error syncing product stocks from warehouses: {sync_err}")

        # 10.5. Seed initial orders if empty
        if db.query(OrderEntity).count() == 0:
            print("Seeding initial orders...")
            from app.api.v1.endpoints.orders import ORDERS_DB
            for o_id, mem_order in ORDERS_DB.items():
                ocode = mem_order.get("order_code")
                dt_str = mem_order.get("created_at")
                c_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00")) if dt_str else datetime.now(timezone.utc)
                db.add(OrderEntity(
                    order_code=ocode,
                    dealer_id=mem_order.get("dealer_id", 1),
                    dealer_name=mem_order.get("dealer_name", ""),
                    created_by=mem_order.get("created_by", "sales"),
                    assigned_sale_id=mem_order.get("assigned_sale_id"),
                    assigned_sale_name=mem_order.get("assigned_sale_name"),
                    total_amount=mem_order.get("total_amount", 0.0),
                    status=mem_order.get("status", "CONFIRMED"),
                    note=mem_order.get("note"),
                    delivery_point_id=mem_order.get("delivery_point_id"),
                    discount_rate=mem_order.get("discount_percent", 0.0),
                    discount_amount=mem_order.get("discount_amount", 0.0),
                    requires_approval=mem_order.get("requires_approval", False),
                    approval_reason=mem_order.get("approval_reason"),
                    items_json=json.dumps(mem_order.get("items", []), ensure_ascii=False),
                    created_at=c_at,
                ))
            db.commit()
            print("Initial orders seeded successfully.")

        # 11. Seed sample confirmed Return Order (Phiếu trả hàng hoàn kho) nếu chưa có
        try:
            if db.query(ReturnOrderEntity).count() == 0:
                print("Seeding initial confirmed return order...")
                # Tìm đơn hàng của đại lý 1 hoặc 2
                sample_order = (
                    db.query(OrderEntity)
                    .filter(~OrderEntity.status.in_(["CANCELLED", "DRAFT", "REJECTED"]))
                    .order_by(OrderEntity.id.asc())
                    .first()
                )
                if sample_order:
                    sample_dealer = db.query(DealerEntity).filter(DealerEntity.id == sample_order.dealer_id).first()
                    if sample_dealer:
                        from app.services.invoice_service import sync_dealer_invoices_from_orders
                        sync_dealer_invoices_from_orders(sample_dealer, db)
                    inv_code = f"HD-{sample_order.order_code}"
                    sample_inv = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == inv_code).first()

                    # Lấy sản phẩm từ đơn hàng
                    from app.services.debt_adjustment_service import extract_original_sold_items_map
                    items_map = extract_original_sold_items_map(sample_inv, sample_order, db)
                    ret_items = []
                    subtotal = 0.0
                    for pid, item_info in list(items_map.items())[:2]:
                        qty = max(1, min(2, item_info.get("sold_quantity", 1)))
                        uprice = float(item_info.get("unit_price", 0.0))
                        ltot = qty * uprice
                        subtotal += ltot
                        ret_items.append(
                            ReturnOrderItemEntity(
                                product_id=pid,
                                product_code=item_info["product_code"],
                                product_name=item_info["product_name"],
                                unit_name=item_info["unit_name"],
                                quantity=qty,
                                return_quantity=qty,
                                original_quantity=qty,
                                unit_price=uprice,
                                refund_amount=ltot,
                                line_total=ltot,
                                reason="Hàng giao sai quy cách theo phản ánh của đại lý",
                                return_reason="Hàng giao sai quy cách theo phản ánh của đại lý",
                            )
                        )

                    if ret_items:
                        now_utc = datetime.now(timezone.utc)
                        today_s = now_utc.strftime("%Y%m%d")
                        ret_order = ReturnOrderEntity(
                            return_code=f"TH-{today_s}-001",
                            order_id=sample_order.id,
                            order_code=sample_order.order_code,
                            original_invoice_id=sample_inv.id if sample_inv else None,
                            original_invoice_code=inv_code,
                            dealer_id=sample_order.dealer_id,
                            dealer_name=sample_order.dealer_name,
                            warehouse_id="WH01",
                            warehouse_name="Kho Tổng Hà Nội",
                            status="CONFIRMED",
                            reason="Khách trả lại do sai quy cách sản phẩm, kho đã kiểm nhận hoàn kho",
                            note="Phiếu trả hàng đã hoàn tất thủ tục nhập kho",
                            subtotal_amount=subtotal,
                            tax_amount=0.0,
                            total_amount=subtotal,
                            created_by_id=1,
                            created_by_name="Quản trị viên",
                            confirmed_by_id=1,
                            confirmed_by_name="Quản trị viên",
                            confirmed_at=now_utc,
                            items=ret_items,
                        )
                        db.add(ret_order)
                        db.commit()
                        print(f"Sample return order {ret_order.return_code} seeded successfully.")
        except Exception as ro_err:
            print(f"Notice: Return order seed skipped: {ro_err}")

        # 12. Seed Receipt Vouchers (Phiếu thu tiền đại lý) trên SQL Server nếu chưa có
        try:
            if db.query(ReceiptVoucherEntity).count() == 0:
                print("Seeding initial receipt vouchers into SQL Server...")
                from app.models.dealer import RECEIPT_VOUCHERS_JSON_PATH
                if os.path.exists(RECEIPT_VOUCHERS_JSON_PATH):
                    with open(RECEIPT_VOUCHERS_JSON_PATH, "r", encoding="utf-8") as rf:
                        v_data = json.load(rf)
                        for r_code, v in v_data.items():
                            c_dt_str = v.get("created_at")
                            c_dt = datetime.fromisoformat(c_dt_str.replace("Z", "+00:00")) if c_dt_str else datetime.now(timezone.utc)
                            db.add(ReceiptVoucherEntity(
                                receipt_code=r_code,
                                dealer_id=v.get("dealer_id", 1),
                                dealer_code=v.get("dealer_code", "DL001"),
                                dealer_name=v.get("dealer_name", ""),
                                dealer_address=v.get("dealer_address"),
                                dealer_phone=v.get("dealer_phone"),
                                dealer_tax_code=v.get("dealer_tax_code"),
                                order_code=v.get("order_code"),
                                amount=float(v.get("amount", 0.0) or 0.0),
                                amount_in_words=v.get("amount_in_words"),
                                payment_method=v.get("payment_method", "Tiền mặt"),
                                reason=v.get("reason"),
                                payer_name=v.get("payer_name"),
                                collector_name=v.get("collector_name"),
                                collector_role=v.get("collector_role"),
                                created_at=c_dt,
                                note=v.get("note"),
                            ))
                        db.commit()
                        print("Initial receipt vouchers seeded into SQL Server successfully.")
        except Exception as rv_err:
            print(f"Notice: Receipt vouchers seed notice: {rv_err}")

        # S7-02: Tự động đồng bộ hoá đơn từ đơn hàng và công nợ CSDL hiện có
        try:
            from app.services.debt_payment_service import sync_all_dealers_invoices
            sync_all_dealers_invoices(db)
            print("Dealer invoices synchronized successfully.")
        except Exception as inv_err:
            print(f"Notice on syncing invoices: {inv_err}")

        # 13. Tự động đồng bộ chuẩn hóa dữ liệu công nợ trên SQL Server
        try:
            from app.services.invoice_service import resync_all_debts_sql
            resync_all_debts_sql(db)
            print("All debt data synchronized on SQL Server successfully.")
        except Exception as sync_err:
            print(f"Notice: SQL Server debt resync notice: {sync_err}")

        print("Database initialization completed successfully.")
    except Exception as e:
        safe_msg = str(e).encode("ascii", errors="backslashreplace").decode("ascii")
        print(f"Error during seeding: {safe_msg}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
