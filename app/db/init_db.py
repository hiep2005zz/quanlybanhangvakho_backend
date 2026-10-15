# backend/app/db/init_db.py
import json
import os
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
)
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
            "base_unit": ("VARCHAR(50) DEFAULT 'Cái'", "NVARCHAR(50) DEFAULT N'Cái'"),
            "units_json": ("TEXT", "NVARCHAR(MAX)"),
            "packaging_specification": ("VARCHAR(255)", "NVARCHAR(255)"),
            "images_json": ("TEXT", "NVARCHAR(MAX)"),
            "status": ("VARCHAR(50) DEFAULT 'active'", "NVARCHAR(50) DEFAULT 'active'"),
            "is_batch_managed": ("BOOLEAN DEFAULT 0", "BIT DEFAULT 0"),
            "min_stock": ("INTEGER DEFAULT 10", "INT DEFAULT 10"),
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
        },
        "inventory_transactions": {
            "unit_name": ("VARCHAR(50) DEFAULT 'Cái'", "NVARCHAR(50) DEFAULT N'Cái'"),
            "conversion_rate": ("FLOAT DEFAULT 1.0", "FLOAT DEFAULT 1.0"),
            "base_quantity": ("FLOAT DEFAULT 0.0", "FLOAT DEFAULT 0.0"),
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
            definition = sqlite_definition if dialect_name == "sqlite" else sql_server_definition
            add_column = "ADD COLUMN" if dialect_name == "sqlite" else "ADD"
            with bind.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE {table_name} {add_column} {column_name} {definition}")
                )


def init_db():
    print(f"Initializing database tables using {engine.dialect.name}...")
    # Tạo các bảng nếu chưa có
    Base.metadata.create_all(bind=engine)
    _ensure_dealer_credit_limit_column(engine)
    _ensure_legacy_columns(engine)
    print("Tables created successfully.")
    # Tự động migrate thêm cột nếu bảng đã tồn tại từ trước
    is_sqlite = engine.url.drivername.startswith("sqlite")
    with engine.connect() as conn:
        if is_sqlite:
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
        else:
            for sql_statement in [
                "IF COL_LENGTH('users', 'avatar_url') IS NULL ALTER TABLE users ADD avatar_url NVARCHAR(500);",
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
                    password_hash=get_password_hash("admin123"),
                    full_name="Quản Trị Viên",
                    role=Role.ADMIN.value,
                    roles_json=json.dumps([Role.ADMIN.value]),
                    is_active=True,
                ),
                UserEntity(
                    id=2,
                    username="sales_manager",
                    password_hash=get_password_hash("manager123"),
                    full_name="Trưởng Phòng Kinh Doanh",
                    role=Role.SALES_MANAGER.value,
                    roles_json=json.dumps([Role.SALES_MANAGER.value]),
                    is_active=True,
                ),
                UserEntity(
                    id=3,
                    username="sales_user",
                    password_hash=get_password_hash("sales123"),
                    full_name="Nhân Viên Bán Hàng",
                    role=Role.SALES.value,
                    roles_json=json.dumps([Role.SALES.value]),
                    is_active=True,
                ),
                UserEntity(
                    id=4,
                    username="warehouse_user",
                    password_hash=get_password_hash("kho123"),
                    full_name="Thủ Kho",
                    role=Role.WAREHOUSE.value,
                    roles_json=json.dumps([Role.WAREHOUSE.value]),
                    is_active=True,
                ),
            ]
            db.add_all(initial_users)
            db.commit()
            print("Users seeded successfully.")

        # 2. Seed Categories nếu chưa có
        if db.query(CategoryEntity).count() == 0:
            print("Seeding initial categories...")
            initial_categories = [
                CategoryEntity(id=1, name="Thời trang", parent_id=None),
                CategoryEntity(id=2, name="Giày dép", parent_id=None),
                CategoryEntity(id=3, name="Phụ kiện", parent_id=None),
                CategoryEntity(id=7, name="Đồ điện tử", parent_id=None),
                CategoryEntity(id=4, name="Áo Nam", parent_id=1),
                CategoryEntity(id=5, name="Quần Nam", parent_id=1),
                CategoryEntity(id=6, name="Áo Thun", parent_id=4),
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
                DealerDeliveryPointEntity(dealer_id=1, name="Kho tổng Lê Lợi", address="123 Lê Lợi, Q1, TP. HCM", is_default=True),
                DealerDeliveryPointEntity(dealer_id=1, name="Chi nhánh Bình Thạnh", address="45 Xô Viết Nghệ Tĩnh, Bình Thạnh, TP. HCM", is_default=False),
                DealerDeliveryPointEntity(dealer_id=2, name="Cửa hàng Ba Đình", address="456 Hoàng Hoa Thám, Ba Đình, Hà Nội", is_default=True),
                DealerDeliveryPointEntity(dealer_id=3, name="Cơ sở Hải Châu", address="789 Nguyễn Văn Linh, Đà Nẵng", is_default=True),
            ]
            db.add_all(points)
            db.commit()
            print("Dealer delivery points seeded successfully.")

        # 6. Seed Suppliers nếu chưa có
        if db.query(SupplierEntity).count() == 0:
            print("Seeding initial suppliers...")
            initial_suppliers = [
                SupplierEntity(id=1, code="NCC001", name="Công ty Cổ phần May Việt Tiến", tax_code="0300123456", phone="02838640800", email="contact@viettien.com.vn", address="Số 7 Lê Minh Xuân, Phường 7, Tân Bình, TP. HCM", status="active"),
                SupplierEntity(id=2, code="NCC002", name="Tập đoàn Dệt May Phong Phú", tax_code="0300987654", phone="02838963533", email="info@phongphu.com.vn", address="48 Tăng Nhơn Phú, Tăng Nhơn Phú B, TP. Thủ Đức, TP. HCM", status="active"),
                SupplierEntity(id=3, code="NCC003", name="Công ty TNHH Giày Biti's Việt Nam", tax_code="0301112233", phone="02838753443", email="support@bitis.com.vn", address="22 Lý Chiêu Hoàng, Phường 10, Quận 6, TP. HCM", status="active"),
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
                UnitOfMeasureEntity(id=1, name="Cái", description="Đơn vị đếm cơ bản", is_active=True),
                UnitOfMeasureEntity(id=2, name="Hộp", description="Hộp đóng gói nhỏ", is_active=True),
                UnitOfMeasureEntity(id=3, name="Thùng", description="Thùng carton tiêu chuẩn", is_active=True),
                UnitOfMeasureEntity(id=4, name="Bộ", description="Bộ sản phẩm hoàn chỉnh", is_active=True),
                UnitOfMeasureEntity(id=5, name="Kiện", description="Kiện hàng lớn", is_active=True),
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
                    name="Chiết khấu số lượng Áo Thun Polo",
                    description="Ưu đãi bậc thang khi mua số lượng lớn cho mã SP001",
                    product_id=1,
                    is_active=True,
                    tiers=[
                        DiscountTierEntity(min_quantity=10, max_quantity=29, discount_percent=5.0),
                        DiscountTierEntity(min_quantity=30, max_quantity=99, discount_percent=10.0),
                        DiscountTierEntity(min_quantity=100, max_quantity=None, discount_percent=15.0),
                    ]
                ),
                DiscountPolicyEntity(
                    id=2,
                    name="Chiết khấu số lượng Quần Jeans Slimfit",
                    description="Ưu đãi bậc thang khi mua số lượng lớn cho mã SP002",
                    product_id=2,
                    is_active=True,
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

        print("Database initialization completed successfully.")
    except Exception as e:
        print(f"Error during seeding: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
