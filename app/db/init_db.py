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
        },
        "dealers": {
            "max_debt_days": ("INTEGER DEFAULT 30", "INT DEFAULT 30"),
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
        },
        "categories": {
            "code": ("VARCHAR(50) DEFAULT ''", "NVARCHAR(50) DEFAULT ''"),
            "product_count": ("INTEGER DEFAULT 0", "INT DEFAULT 0"),
            "status": ("VARCHAR(50) DEFAULT 'Đang hoạt động'", "NVARCHAR(50) DEFAULT N'Đang hoạt động'"),
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
                        conn.execute(text("ALTER TABLE price_book_items ADD COLUMN sale_price REAL DEFAULT 0.0;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (price_book_items.sale_price): {ex}")
                if "floor_price" not in pbi_cols:
                    try:
                        conn.execute(text("ALTER TABLE price_book_items ADD COLUMN floor_price REAL DEFAULT 0.0;"))
                        conn.commit()
                    except Exception as ex:
                        print(f"SQLite migration notice (price_book_items.floor_price): {ex}")
        else:
            for sql_statement in [
                "IF COL_LENGTH('users', 'avatar_url') IS NULL ALTER TABLE users ADD avatar_url NVARCHAR(500);",
                "IF COL_LENGTH('products', 'base_unit') IS NULL ALTER TABLE products ADD base_unit NVARCHAR(50) DEFAULT N'Cái';",
                "IF COL_LENGTH('products', 'units_json') IS NULL ALTER TABLE products ADD units_json NVARCHAR(MAX);",
                "IF COL_LENGTH('inventory_transactions', 'unit_name') IS NULL ALTER TABLE inventory_transactions ADD unit_name NVARCHAR(50) DEFAULT N'Cái';",
                "IF COL_LENGTH('inventory_transactions', 'conversion_rate') IS NULL ALTER TABLE inventory_transactions ADD conversion_rate FLOAT DEFAULT 1.0;",
                "IF COL_LENGTH('inventory_transactions', 'base_quantity') IS NULL ALTER TABLE inventory_transactions ADD base_quantity FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('dealers', 'customer_group') IS NULL ALTER TABLE dealers ADD customer_group NVARCHAR(100) DEFAULT N'Đại lý cấp 1';",
                "IF COL_LENGTH('dealers', 'status') IS NULL ALTER TABLE dealers ADD status NVARCHAR(50) DEFAULT N'Đang hoạt động';",
                "IF COL_LENGTH('dealers', 'region') IS NULL ALTER TABLE dealers ADD region NVARCHAR(100);",
                "IF COL_LENGTH('dealers', 'lock_reason') IS NULL ALTER TABLE dealers ADD lock_reason NVARCHAR(500);",
                "IF COL_LENGTH('dealers', 'locked_at') IS NULL ALTER TABLE dealers ADD locked_at DATETIME;",
                "IF COL_LENGTH('dealers', 'locked_by') IS NULL ALTER TABLE dealers ADD locked_by VARCHAR(50);",
                "IF COL_LENGTH('price_books', 'version') IS NULL ALTER TABLE price_books ADD version INT DEFAULT 1;",
                "IF COL_LENGTH('price_books', 'is_locked') IS NULL ALTER TABLE price_books ADD is_locked BIT DEFAULT 0;",
                "IF COL_LENGTH('price_book_items', 'sale_price') IS NULL ALTER TABLE price_book_items ADD sale_price FLOAT DEFAULT 0.0;",
                "IF COL_LENGTH('price_book_items', 'floor_price') IS NULL ALTER TABLE price_book_items ADD floor_price FLOAT DEFAULT 0.0;",
            ]:
                try:
                    conn.execute(text(sql_statement))
                    conn.commit()
                except Exception as ex:
                    print(f"Migration notice: {ex}")
    db = SessionLocal()
    try:
        # 1. Seed Users nếu bảng đang trống
        if db.query(UserEntity).count() == 0:
            print("Seeding initial users...")
            # Kiểm tra xem có file users_data.json để migrate dữ liệu cũ không
            json_path = os.path.join(os.path.dirname(__file__), "..", "models", "users_data.json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, u in data.items():
                        user_obj = UserEntity(
                            id=u.get("id"),
                            username=u.get("username"),
                            full_name=u.get("full_name"),
                            email=u.get("email"),
                            phone=u.get("phone"),
                            role=u.get("role"),
                            roles_json=json.dumps(u.get("roles", [u.get("role")])),
                            hashed_password=u.get("hashed_password"),
                            branch=u.get("branch", "Kho Tổng Hà Nội"),
                            is_active=u.get("is_active", True),
                            status=u.get("status", "ACTIVE"),
                            lock_reason=u.get("lock_reason"),
                            failed_attempts=u.get("failed_attempts", 0),
                            token_version=u.get("token_version", 1),
                        )
                        db.add(user_obj)
            db.commit()
            print("Users seeded successfully.")

        # 1.5 Seed Categories nếu chưa có
        if db.query(CategoryEntity).count() == 0:
            print("Seeding initial categories into SQL Server...")
            initial_categories = [
                CategoryEntity(id=1, name="Thời trang", parent_id=None),
                CategoryEntity(id=2, name="Giày dép", parent_id=None),
                CategoryEntity(id=3, name="Phụ kiện", parent_id=None),
                CategoryEntity(id=4, name="Áo Nam", parent_id=1),
                CategoryEntity(id=5, name="Quần Nam", parent_id=1),
                CategoryEntity(id=6, name="Áo Thun", parent_id=4),
            ]
            db.add_all(initial_categories)
            db.commit()
            print("Categories seeded successfully.")

        # 2. Seed Products nếu chưa có
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

        # 3. Seed Dealers nếu chưa có
        if db.query(DealerEntity).count() == 0:
            print("Seeding initial dealers...")
            initial_dealers = [
                DealerEntity(id=1, code="DL001", name="Đại Lý Phân Phối Miền Bắc - Sao Mai", phone="0912345678", email="saomai@daily.vn", address="120 Cầu Giấy, Hà Nội", region="Hà Nội", assigned_sale_id=3, customer_group="dai_ly_cap_1", status="Đang hoạt động"),
                DealerEntity(id=2, code="DL002", name="Đại Lý Thời Trang Tân Bình", phone="0987654321", email="tanbinh@daily.vn", address="45 Lý Thường Kiệt, TP. HCM", region="TP. HCM", assigned_sale_id=3, customer_group="dai_ly_cap_2", status="Đang hoạt động"),
                DealerEntity(id=3, code="DL003", name="Đại Lý Tổng Hợp Hải Phòng", phone="0934567890", email="haiphong@daily.vn", address="88 Lạch Tray, Hải Phòng", region="Hải Phòng", assigned_sale_id=3, customer_group="dai_ly_cap_2", status="Đang hoạt động"),
                DealerEntity(id=4, code="DL004", name="Công Ty TNHH Bán Lẻ An Phát", phone="0945678901", email="anphat@daily.vn", address="66 Nguyễn Huệ, Đà Nẵng", region="Đà Nẵng", assigned_sale_id=8, customer_group="khach_le", status="Tạm ngừng"),
                DealerEntity(id=5, code="DL005", name="Khách Mua Lẻ Trực Tiếp", phone="0911223344", email="khachle@gmail.com", address="Số 10 Tràng Thi, Hoàn Kiếm, Hà Nội", region="Hà Nội", assigned_sale_id=8, customer_group="khach_le", status="Đang hoạt động"),
            ]
            db.add_all(initial_dealers)
            db.commit()
            print("Dealers seeded successfully.")

        # 4. Seed Master Delivery Points nếu chưa có
        if db.query(MasterDeliveryPointEntity).count() == 0:
            print("Seeding master delivery points...")
            existing_dealer_points = db.query(DealerDeliveryPointEntity).all()
            seen_pts = set()
            master_seeds = []
            for dp in existing_dealer_points:
                key = (dp.label.strip().lower(), dp.address.strip().lower())
                if key not in seen_pts:
                    seen_pts.add(key)
                    master_seeds.append(
                        MasterDeliveryPointEntity(
                            label=dp.label,
                            address=dp.address,
                            receiver_name=dp.receiver_name or "",
                            receiver_phone=dp.receiver_phone or "",
                            route_note=dp.route_note or "",
                            is_active=True,
                        )
                    )
            if master_seeds:
                db.add_all(master_seeds)
                db.commit()
                print(f"Seeded {len(master_seeds)} master delivery points.")

        # 5. Seed Price Books nếu chưa có
        if db.query(PriceBookEntity).count() == 0:
            print("Seeding initial price books into Database...")
            from datetime import timedelta, timezone, datetime
            now_dt = datetime.now(timezone.utc)
            
            # Lấy các sản phẩm có sẵn
            prods = db.query(ProductEntity).all()
            
            pb1 = PriceBookEntity(
                code="BG-DL1",
                name="Bảng giá Đại lý cấp 1",
                customer_group="Dai_ly_cap_1",
                valid_from=now_dt - timedelta(days=5),
                valid_to=now_dt + timedelta(days=90),
                status="ACTIVE",
                version=1,
                is_locked=False,
                note="Bảng giá chuẩn dành cho Đại lý cấp 1 toàn quốc",
                created_by="admin"
            )
            db.add(pb1)
            db.flush()

            for p in prods[:4]:
                sale_p = round(p.sell_price * 0.85, -3) if p.sell_price else 100000.0
                floor_p = round(p.sell_price * 0.75, -3) if p.sell_price else 80000.0
                db.add(PriceBookItemEntity(
                    price_book_id=pb1.id,
                    product_id=p.id,
                    sale_price=sale_p,
                    floor_price=floor_p,
                    price=sale_p,
                    min_price=floor_p
                ))

            pb2 = PriceBookEntity(
                code="BG-LE",
                name="Bảng giá Khách lẻ",
                customer_group="Khach_le",
                valid_from=now_dt - timedelta(days=30),
                valid_to=now_dt + timedelta(days=180),
                status="ACTIVE",
                version=1,
                is_locked=False,
                note="Bảng giá niêm yết bán lẻ",
                created_by="admin"
            )
            db.add(pb2)
            db.flush()

            for p in prods[:4]:
                sale_p = p.sell_price or 120000.0
                floor_p = round((p.sell_price or 120000.0) * 0.9, -3)
                db.add(PriceBookItemEntity(
                    price_book_id=pb2.id,
                    product_id=p.id,
                    sale_price=sale_p,
                    floor_price=floor_p,
                    price=sale_p,
                    min_price=floor_p
                ))

            pb3 = PriceBookEntity(
                code="BG-DL2-OLD",
                name="Bảng giá Đại lý cấp 2 (Đã khóa)",
                customer_group="Dai_ly_cap_2",
                valid_from=now_dt - timedelta(days=90),
                valid_to=now_dt - timedelta(days=5),
                status="EXPIRED",
                version=1,
                is_locked=True,
                note="Bảng giá đã phát sinh đơn hàng, không được phép chỉnh sửa",
                created_by="admin"
            )
            db.add(pb3)
            db.flush()

            for p in prods[:3]:
                sale_p = round((p.sell_price or 150000.0) * 0.9, -3)
                floor_p = round((p.sell_price or 150000.0) * 0.8, -3)
                db.add(PriceBookItemEntity(
                    price_book_id=pb3.id,
                    product_id=p.id,
                    sale_price=sale_p,
                    floor_price=floor_p,
                    price=sale_p,
                    min_price=floor_p
                ))

            db.commit()
            print("Price books seeded successfully.")

        # 6. Seed Suppliers nếu chưa có
        if db.query(SupplierEntity).count() == 0:
            print("Seeding initial suppliers...")
            initial_suppliers = [
                SupplierEntity(id=1, code="NCC001", name="Công ty Cổ phần Nước Giải Khát Sabeco", tax_code="0300588569", contact_person="Nguyễn Văn Cung", payment_terms="Gối đầu 30 ngày", is_active=True),
                SupplierEntity(id=2, code="NCC002", name="Công ty TNHH May Mặc An Phước", tax_code="0301438927", contact_person="Trần Thị May", payment_terms="Thanh toán ngay khi giao", is_active=True),
            ]
            db.add_all(initial_suppliers)
            db.commit()
            print("Suppliers seeded successfully.")

        # 7. Seed Warehouses nếu chưa có
        if db.query(WarehouseEntity).count() == 0:
            print("Seeding initial warehouses...")
            initial_warehouses = [
                WarehouseEntity(id=1, code="KHO_HN", name="Kho Tổng Hà Nội", address="Lô CN1 KCN Từ Liêm, Bắc Từ Liêm, Hà Nội", is_active=True),
                WarehouseEntity(id=2, code="KHO_DN", name="Kho Chi Nhánh Đà Nẵng", address="KCN Hòa Khánh, Liên Chiểu, Đà Nẵng", is_active=True),
                WarehouseEntity(id=3, code="KHO_HCM", name="Kho Chi Nhánh TP. Hồ Chí Minh", address="Khu chế xuất Tân Thuận, Quận 7, TP. HCM", is_active=True),
            ]
            db.add_all(initial_warehouses)
            db.commit()
            print("Warehouses seeded successfully.")

        # 8. Seed Units of Measure nếu chưa có
        if db.query(UnitOfMeasureEntity).count() == 0:
            print("Seeding initial units of measure...")
            initial_uoms = [
                UnitOfMeasureEntity(id=1, code="CAI", name="Cái", description="Đơn vị cơ sở cái"),
                UnitOfMeasureEntity(id=2, code="LON", name="Lon", description="Đơn vị cơ sở lon"),
                UnitOfMeasureEntity(id=3, code="LOC", name="Lốc", description="Quy cách lốc 6 cái/lon"),
                UnitOfMeasureEntity(id=4, code="THUNG", name="Thùng", description="Quy cách thùng 24 cái/lon"),
                UnitOfMeasureEntity(id=5, code="CHIEC", name="Chiếc", description="Đơn vị chiếc"),
                UnitOfMeasureEntity(id=6, code="KIEN", name="Kiện", description="Quy cách kiện 10 chiếc"),
                UnitOfMeasureEntity(id=7, code="HOP", name="Hộp", description="Đơn vị hộp"),
                UnitOfMeasureEntity(id=8, code="KG", name="Kg", description="Đơn vị kilogam"),
            ]
            db.add_all(initial_uoms)
            db.commit()
            print("Units of measure seeded successfully.")

        # 9. Seed Discount Policies nếu chưa có
        if db.query(DiscountPolicyEntity).count() == 0:
            print("Seeding initial discount policies...")
            default_policy = DiscountPolicyEntity(
                code="CK-SL-001",
                name="Chiết khấu sản lượng toàn hệ thống",
                title="Tất cả sản phẩm",
                category="ALL",
                target_dealer_type="ALL",
                target_group="all",
                description="Giảm giá theo sản lượng đặt hàng áp dụng toàn hệ thống",
                start_date="2026-01-01",
                end_date="2026-12-31",
                is_active=True,
                status="active",
                created_by="admin",
            )
            db.add(default_policy)
            db.flush()

            tier1 = DiscountTierEntity(
                policy_id=default_policy.id,
                min_quantity=100,
                max_quantity=499,
                discount_percent=5.0,
            )
            tier2 = DiscountTierEntity(
                policy_id=default_policy.id,
                min_quantity=500,
                max_quantity=999,
                discount_percent=10.0,
            )
            tier3 = DiscountTierEntity(
                policy_id=default_policy.id,
                min_quantity=1000,
                max_quantity=None,
                discount_percent=15.0,
            )
            db.add_all([tier1, tier2, tier3])

            policy_cap1 = DiscountPolicyEntity(
                code="CK-CAP1",
                name="Chính sách chiết khấu - Đại lý Cấp 1",
                title="Chính sách chiết khấu - Đại lý Cấp 1",
                category="ALL",
                target_dealer_type="agent_tier_1",
                target_group="agent_tier_1",
                description="Áp dụng cho đơn hàng đạt mốc sản lượng",
                start_date="2026-10-01",
                end_date="2026-12-31",
                is_active=True,
                status="active",
                created_by="admin",
            )
            db.add(policy_cap1)
            db.flush()

            t1 = DiscountTierEntity(policy_id=policy_cap1.id, min_quantity=100, max_quantity=499, discount_percent=5.0)
            t2 = DiscountTierEntity(policy_id=policy_cap1.id, min_quantity=500, max_quantity=999, discount_percent=8.0)
            t3 = DiscountTierEntity(policy_id=policy_cap1.id, min_quantity=1000, max_quantity=None, discount_percent=12.0)
            db.add_all([t1, t2, t3])

            db.commit()
            print("Discount policies seeded successfully.")

        from app.models.dealer import load_dealers_db
        load_dealers_db()


    except Exception as e:
        db.rollback()
        print(f"Error during init_db: {e}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
    init_db()
