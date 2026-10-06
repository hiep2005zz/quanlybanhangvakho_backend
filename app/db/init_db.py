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
)
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
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
        },
        "dealers": {
            "max_debt_days": ("INTEGER DEFAULT 30", "INT DEFAULT 30"),
            "locked_by": ("VARCHAR(50)", "NVARCHAR(50)"),
            "tax_code": ("VARCHAR(50)", "NVARCHAR(50)"),
            "transaction_count": ("INTEGER DEFAULT 0", "INT DEFAULT 0"),
                DealerEntity(id=4, code="DL004", name="Công Ty TNHH Bán Lẻ An Phát", phone="0945678901", email="anphat@daily.vn", address="66 Nguyễn Huệ, Đà Nẵng", region="Đà Nẵng", assigned_sale_id=None, customer_group="khach_le", status="Tạm ngừng"),
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
