# backend/app/db/init_db.py
import json
import os
from sqlalchemy import text
from app.core.database import engine, Base, SessionLocal
from app.models.entities import (
    UserEntity,
    ProductEntity,
    DealerEntity,
    InventoryTransactionEntity,
    OrderEntity,
    CategoryEntity,
    AuditLogEntity,
)
from app.core.security import get_password_hash
from app.core.rbac import Role

def init_db():
    print("Initializing Database tables in Microsoft SQL Server...")
    # Tạo các bảng nếu chưa có
    Base.metadata.create_all(bind=engine)
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
                    conn.execute(text("ALTER TABLE inventory_transactions ADD COLUMN base_quantity INTEGER DEFAULT 0;"))
                    conn.commit()
                except Exception as ex:
                    print(f"SQLite migration notice (tx.base_quantity): {ex}")
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
            print("Seeding initial users into SQL Server...")
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
            print("Seeding initial products into SQL Server...")
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
            print("Seeding initial dealers into SQL Server...")
            initial_dealers = [
                DealerEntity(id=1, code="DL001", name="Đại Lý Phân Phối Miền Bắc - Sao Mai", phone="0912345678", email="saomai@daily.vn", address="120 Cầu Giấy, Hà Nội", region="Hà Nội", assigned_sale_id=3, customer_group="Đại lý cấp 1", status="Đang hoạt động"),
                DealerEntity(id=2, code="DL002", name="Đại Lý Thời Trang Tân Bình", phone="0987654321", email="tanbinh@daily.vn", address="45 Lý Thường Kiệt, TP. HCM", region="TP. HCM", assigned_sale_id=3, customer_group="Đại lý cấp 2", status="Đang hoạt động"),
                DealerEntity(id=3, code="DL003", name="Đại Lý Tổng Hợp Hải Phòng", phone="0934567890", email="haiphong@daily.vn", address="88 Lạch Tray, Hải Phòng", region="Hải Phòng", assigned_sale_id=3, customer_group="Khách sỉ", status="Đang hoạt động"),
                DealerEntity(id=4, code="DL004", name="Công Ty TNHH Bán Lẻ An Phát", phone="0945678901", email="anphat@daily.vn", address="66 Nguyễn Huệ, Đà Nẵng", region="Đà Nẵng", assigned_sale_id=None, customer_group="Khách lẻ", status="Tạm ngừng"),
            ]
            db.add_all(initial_dealers)
            db.commit()
            print("Dealers seeded successfully.")

    except Exception as e:
        db.rollback()
        print(f"Error during init_db: {e}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
