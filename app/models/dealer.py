# backend/app/models/dealer.py
from __future__ import annotations
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
    tax_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    region: Optional[str] = None
    assigned_sale_id: Optional[int] = None  # user id of the sales staff responsible
    credit_limit: float = 50000000.0        # Hạn mức công nợ mặc định (VNĐ)
    max_debt_days: int = 30                 # Số ngày nợ tối đa cho phép
    customer_group: Optional[str] = "Đại lý cấp 1"
    status: str = "ACTIVE"                  # ACTIVE | LOCKED
    lock_reason: Optional[str] = None
    locked_at: Optional[str] = None
    locked_by: Optional[str] = None
    transaction_count: Optional[int] = 0
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
        region="Hà Nội",
        assigned_sale_id=3,
        credit_limit=100000000.0,
        customer_group="dai_ly_cap_1",
        status="Đang hoạt động",
    ),
    2: Dealer(
        id=2,
        code="DL002",
        name="Đại Lý Thời Trang Tân Bình",
        phone="0987654321",
        email="tanbinh@daily.vn",
        address="45 Lý Thường Kiệt, TP. HCM",
        region="TP. HCM",
        assigned_sale_id=3,
        credit_limit=50000000.0,
        customer_group="dai_ly_cap_2",
        status="Đang hoạt động",
    ),
    3: Dealer(
        id=3,
        code="DL003",
        name="Đại Lý Tổng Hợp Hải Phòng",
        phone="0934567890",
        email="haiphong@daily.vn",
        address="88 Lạch Tray, Hải Phòng",
        region="Hải Phòng",
        assigned_sale_id=3,
        credit_limit=50000000.0,
        customer_group="Khách sỉ",
        status="Đang hoạt động",
    ),
    4: Dealer(
        id=4,
        code="DL004",
        name="Công Ty TNHH Bán Lẻ An Phát",
        phone="0945678901",
        email="anphat@daily.vn",
        address="66 Nguyễn Huệ, Đà Nẵng",
        region="Đà Nẵng",
        assigned_sale_id=None,  # Chưa chỉ định nhân viên kinh doanh phụ trách
        credit_limit=50000000.0,
        customer_group="khach_le",
        status="Tạm ngừng",
    ),
    5: Dealer(
        id=5,
        code="DL005",
        name="Khách Mua Lẻ Trực Tiếp",
        phone="0911223344",
        email="khachle@gmail.com",
        address="Số 10 Tràng Thi, Hoàn Kiếm, Hà Nội",
        region="Hà Nội",
        assigned_sale_id=3,
        credit_limit=20000000.0,
        customer_group="khach_le",
        status="Đang hoạt động",
    ),
}

import os
import json

DEALERS_JSON_PATH = os.path.join(os.path.dirname(__file__), "dealers_data.json")

def save_dealers_db():
    """Lưu DEALERS_DB vào cơ sở dữ liệu (và đồng bộ JSON dự phòng)."""
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
                db_dealer.tax_code = getattr(d, "tax_code", None)
                db_dealer.phone = d.phone
                db_dealer.email = d.email
                db_dealer.address = d.address
                db_dealer.region = d.region
                db_dealer.assigned_sale_id = d.assigned_sale_id
                db_dealer.credit_limit = getattr(d, "credit_limit", getattr(db_dealer, "credit_limit", 0))
                db_dealer.max_debt_days = getattr(d, "max_debt_days", getattr(db_dealer, "max_debt_days", 30))
                db_dealer.customer_group = getattr(d, "customer_group", getattr(db_dealer, "customer_group", "Đại lý cấp 1"))
                if hasattr(db_dealer, "transaction_count"):
                    db_dealer.transaction_count = getattr(d, "transaction_count", 0) or 0
                if hasattr(db_dealer, "status"):
                    db_dealer.status = getattr(d, "status", "ACTIVE")
                    db_dealer.lock_reason = getattr(d, "lock_reason", None)
                    db_dealer.locked_at = getattr(d, "locked_at", None)
                    db_dealer.locked_by = getattr(d, "locked_by", None)

            if DEALERS_DB:
                existing_ids = list(DEALERS_DB.keys())
                db.query(DealerEntity).filter(DealerEntity.id.not_in(existing_ids)).delete(synchronize_session=False)
            db.commit()
        except Exception as sql_err:
            db.rollback()
            print(f"Database save dealers note: {sql_err}")
        finally:
            db.close()

        # Đồng bộ ra JSON backup
        data = {str(k): v.model_dump(mode="json") for k, v in DEALERS_DB.items()}
        with open(DEALERS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving dealers db: {e}")

def load_dealers_db():
    """Nạp DEALERS_DB từ cơ sở dữ liệu (hoặc fallback sang JSON)."""
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
                        tax_code=getattr(entity, "tax_code", None),
                        phone=entity.phone,
                        email=entity.email,
                        address=entity.address,
                        region=getattr(entity, "region", None),
                        assigned_sale_id=entity.assigned_sale_id,
                        credit_limit=float(entity.credit_limit) if getattr(entity, "credit_limit", None) is not None else 50000000.0,
                        max_debt_days=int(entity.max_debt_days) if getattr(entity, "max_debt_days", None) is not None else 30,
                        customer_group=getattr(entity, "customer_group", None) or "Đại lý cấp 1",
                        status="Đang hoạt động" if ("ho?t" in str(getattr(entity, "status", "")) or "Ðang" in str(getattr(entity, "status", ""))) else (getattr(entity, "status", "Đang hoạt động") or "Đang hoạt động"),
                        lock_reason=getattr(entity, "lock_reason", None),
                        locked_at=getattr(entity, "locked_at", None),
                        locked_by=getattr(entity, "locked_by", None),
                        transaction_count=int(getattr(entity, "transaction_count", 0) or 0),
                    )
                    DEALERS_DB[entity.id] = d
                loaded_from_sql = True
        except Exception as sql_err:
            print(f"Database load dealers note: {sql_err}")
        finally:
            db.close()
    except Exception as e:
        print(f"Error connecting to database on dealer load: {e}")

    if not loaded_from_sql and os.path.exists(DEALERS_JSON_PATH):
        try:
            with open(DEALERS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    DEALERS_DB[int(k)] = Dealer(**v)
        except Exception as e:
            print(f"Error loading dealers db from json: {e}")

def count_dealers_by_sale_id(user_id: int) -> int:
    """Đếm số lượng đại lý/khách hàng do nhân viên phụ trách."""
    return sum(1 for d in DEALERS_DB.values() if d.assigned_sale_id == user_id)

def get_dealers_by_sale_id(user_id: int) -> List[Dealer]:
    """Lấy danh sách đại lý do nhân viên phụ trách."""
    return [d for d in DEALERS_DB.values() if d.assigned_sale_id == user_id]
def sync_dealer_for_user(user_id: int, full_name: str, email: Optional[str], phone: Optional[str], is_customer: bool):
    """Đồng bộ tài khoản User với danh sách Dealer (nếu là customer)."""
    if is_customer:
        if user_id not in DEALERS_DB:
            DEALERS_DB[user_id] = Dealer(
                id=user_id,
                code=f"DL{user_id:03d}",
                name=full_name or "Đại lý mới",
                email=email,
                phone=phone
            )
        else:
            dealer = DEALERS_DB[user_id]
            dealer.name = full_name or dealer.name
            if email: dealer.email = email
            if phone: dealer.phone = phone
        save_dealers_db()
    else:
        # Nếu không còn là customer nữa thì không cần thiết xóa, nhưng có thể khóa lại nếu muốn
        pass
