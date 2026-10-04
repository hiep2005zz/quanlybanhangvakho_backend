import os
import sys
from datetime import datetime, timedelta, timezone

# Add the parent directory to sys.path so we can import 'app' modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.entities import ProductEntity
from app.models.price_book import PriceBookEntity, PriceBookItemEntity

def seed_price_books():
    db = SessionLocal()
    try:
        # Get some products to use for seeding items
        products = db.query(ProductEntity).limit(3).all()
        if len(products) < 3:
            print("Khong du 3 san pham, dang tao san pham mau...")
            dummy_products = [
                ProductEntity(code=f"SP_MOCK_{i}", name=f"Sản phẩm mẫu {i}", category="Khác", cost_price=100000, sell_price=150000)
                for i in range(3 - len(products))
            ]
            db.add_all(dummy_products)
            db.commit()
            for p in dummy_products:
                db.refresh(p)
            products.extend(dummy_products)
            
        p1 = products[0].id
        p2 = products[1].id
        p3 = products[2].id

        now = datetime.now(timezone.utc)

        # 1. Bảng giá Đại lý cấp 1 (Trạng thái: Hoạt động, Đang trong thời hạn)
        pb1 = PriceBookEntity(
            code=f"BG-DL1-{int(now.timestamp())}",
            name="Bảng giá Đại lý cấp 1 - Quý 4",
            customer_group="dai_ly_cap_1",
            valid_from=now - timedelta(days=5),
            valid_to=now + timedelta(days=90),
            status="ACTIVE",
            is_locked=False,
            version=1,
            created_by="admin",
            note="Bảng giá mặc định cho cấp 1"
        )
        db.add(pb1)
        db.flush()

        db.add_all([
            PriceBookItemEntity(price_book_id=pb1.id, product_id=p1, price=100000, min_price=95000),
            PriceBookItemEntity(price_book_id=pb1.id, product_id=p2, price=200000, min_price=190000),
            PriceBookItemEntity(price_book_id=pb1.id, product_id=p3, price=300000, min_price=280000),
        ])

        # 2. Bảng giá Khách lẻ (Trạng thái: Hoạt động)
        pb2 = PriceBookEntity(
            code=f"BG-LE-{int(now.timestamp())}",
            name="Bảng giá Khách lẻ chung",
            customer_group="khach_le",
            valid_from=now - timedelta(days=30),
            valid_to=now + timedelta(days=180),
            status="ACTIVE",
            is_locked=False,
            version=1,
            created_by="admin",
            note="Giá áp dụng bán lẻ toàn quốc"
        )
        db.add(pb2)
        db.flush()

        db.add_all([
            PriceBookItemEntity(price_book_id=pb2.id, product_id=p1, price=120000, min_price=110000),
            PriceBookItemEntity(price_book_id=pb2.id, product_id=p2, price=250000, min_price=230000),
        ])

        # 3. Bảng giá Đại lý cấp 2 (Trạng thái: Hết hạn, Cờ is_locked = true)
        pb3 = PriceBookEntity(
            code=f"BG-DL2-OLD-{int(now.timestamp())}",
            name="Bảng giá Cấp 2 - Mùa Hè",
            customer_group="dai_ly_cap_2",
            valid_from=now - timedelta(days=90),
            valid_to=now - timedelta(days=10),  # Đã hết hạn
            status="EXPIRED", # Đã hết hạn
            is_locked=True,  # Đã phát sinh đơn hàng
            version=1,
            created_by="admin",
            note="Đã hết hạn và có đơn hàng, không thể sửa"
        )
        db.add(pb3)
        db.flush()

        db.add_all([
            PriceBookItemEntity(price_book_id=pb3.id, product_id=p1, price=110000, min_price=105000),
            PriceBookItemEntity(price_book_id=pb3.id, product_id=p2, price=220000, min_price=210000),
            PriceBookItemEntity(price_book_id=pb3.id, product_id=p3, price=320000, min_price=310000),
        ])

        db.commit()
        print("Da tao thanh cong 3 bang gia (Dai ly cap 1, Khach le, Dai ly cap 2 khoa)!")
    
    except Exception as e:
        db.rollback()
        print(f"Loi khi seed du lieu: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_price_books()
