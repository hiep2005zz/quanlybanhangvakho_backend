# backend/app/models/dealer.py
"""
Data model and in-memory store for Dealers/Customers.
Stores assigned_sale_id pointing to users.id.
"""
from typing import Optional, List
from pydantic import BaseModel

class Dealer(BaseModel):
    id: int
    code: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    assigned_sale_id: Optional[int] = None  # user id of the sales staff responsible
    credit_limit: float = 50000000.0        # Hạn mức công nợ mặc định (VNĐ)
    customer_group: Optional[str] = "Dai_ly_cap_1"


# Initial seed data for dealers
# Sales user: id=3 (username: 'sales', full_name: 'Trần Bán Hàng')
DEALERS_DB: dict[int, Dealer] = {
    1: Dealer(
        id=1,
        code="DL001",
        name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        phone="0912345678",
        email="saomai@daily.vn",
        address="120 Cầu Giấy, Hà Nội",
        assigned_sale_id=3,
        customer_group="Dai_ly_cap_1",
    ),
    2: Dealer(
        id=2,
        code="DL002",
        name="Đại Lý Thời Trang Tân Bình",
        phone="0987654321",
        email="tanbinh@daily.vn",
        address="45 Lý Thường Kiệt, TP. HCM",
        assigned_sale_id=3,
        customer_group="Dai_ly_cap_2",
    ),
    3: Dealer(
        id=3,
        code="DL003",
        name="Đại Lý Tổng Hợp Hải Phòng",
        phone="0934567890",
        email="haiphong@daily.vn",
        address="88 Lạch Tray, Hải Phòng",
        assigned_sale_id=3,
        customer_group="Dai_ly_cap_1",
    ),
    4: Dealer(
        id=4,
        code="DL004",
        name="Công Ty TNHH Bán Lẻ An Phát",
        phone="0945678901",
        email="anphat@daily.vn",
        address="66 Nguyễn Huệ, Đà Nẵng",
        assigned_sale_id=2,  # id=2 is sales_manager
        customer_group="Dai_ly_cap_2",
    ),
    5: Dealer(
        id=5,
        code="DL005",
        name="Khách Mua Lẻ Trực Tiếp",
        phone="0911223344",
        email="khachle@gmail.com",
        address="Số 10 Tràng Thi, Hoàn Kiếm, Hà Nội",
        assigned_sale_id=3,
        customer_group="Khach_le",
    ),
}

import os
import json

DEALERS_JSON_PATH = os.path.join(os.path.dirname(__file__), "dealers_data.json")

def save_dealers_db():
    """Lưu DEALERS_DB vào cơ sở dữ liệu SQL Server (và đồng bộ JSON dự phòng)."""
    try:
        from app.core.database import SessionLocal
        from app.models.entities import DealerEntity

        db = SessionLocal()
        try:
            for k, d in DEALERS_DB.items():
                db_dealer = db.query(DealerEntity).filter(DealerEntity.id == d.id).first()
                if not db_dealer:
                    db_dealer = db.query(DealerEntity).filter(DealerEntity.code == d.code).first()
                if not db_dealer:
                    db_dealer = DealerEntity(id=d.id, code=d.code, name=d.name)
                    db.add(db_dealer)

                db_dealer.code = d.code
                db_dealer.name = d.name
                db_dealer.phone = d.phone
                db_dealer.email = d.email
                db_dealer.address = d.address
                db_dealer.assigned_sale_id = d.assigned_sale_id

            db.commit()
        except Exception as sql_err:
            db.rollback()
            print(f"SQL Server save dealers note: {sql_err}")
        finally:
            db.close()

        # Đồng bộ ra JSON backup
        data = {str(k): v.model_dump(mode="json") for k, v in DEALERS_DB.items()}
        with open(DEALERS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving dealers db: {e}")

def load_dealers_db():
    """Nạp DEALERS_DB từ SQL Server Database (hoặc fallback sang JSON)."""
    loaded_from_sql = False
    try:
        from app.core.database import SessionLocal
        from app.models.entities import DealerEntity

        db = SessionLocal()
        try:
            dealers_in_db = db.query(DealerEntity).all()
            if dealers_in_db:
                DEALERS_DB.clear()
                for entity in dealers_in_db:
                    d = Dealer(
                        id=entity.id,
                        code=entity.code,
                        name=entity.name,
                        phone=entity.phone,
                        email=entity.email,
                        address=entity.address,
                        assigned_sale_id=entity.assigned_sale_id,
                    )
                    DEALERS_DB[entity.id] = d
                loaded_from_sql = True
        except Exception as sql_err:
            print(f"SQL Server load dealers note: {sql_err}")
        finally:
            db.close()
    except Exception as e:
        print(f"Error connecting to SQL Server on dealer load: {e}")

    if not loaded_from_sql and os.path.exists(DEALERS_JSON_PATH):
        try:
            with open(DEALERS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    DEALERS_DB[int(k)] = Dealer(**v)
        except Exception as e:
            print(f"Error loading dealers db from json: {e}")

# Tự động nạp dữ liệu khi khởi động
load_dealers_db()

def count_dealers_by_sale_id(user_id: int) -> int:
    """Đếm số lượng đại lý/khách hàng do nhân viên phụ trách."""
    return sum(1 for d in DEALERS_DB.values() if d.assigned_sale_id == user_id)

def get_dealers_by_sale_id(user_id: int) -> List[Dealer]:
    """Lấy danh sách đại lý do nhân viên phụ trách."""
    return [d for d in DEALERS_DB.values() if d.assigned_sale_id == user_id]

