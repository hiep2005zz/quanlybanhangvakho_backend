# backend/app/models/dealer.py
from __future__ import annotations
"""
Data model and in-memory store for Dealers/Customers.
Stores assigned_sale_id pointing to users.id.
"""
from typing import Optional, List, Union, Dict, Any, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel

def parse_tx_date(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        val_clean = val.replace("Z", "+00:00")
        try:
            dt_obj = datetime.fromisoformat(val_clean)
            return dt_obj if dt_obj.tzinfo else dt_obj.replace(tzinfo=timezone.utc)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt_obj = datetime.strptime(val[:19], fmt)
                return dt_obj.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def compute_dealer_unified_debt(
    dealer_id: int,
    dealer_orders: List[Dict[str, Any]],
    payments_by_order: Dict[str, float],
    unallocated_payments: float,
    adjustments_by_order: Dict[str, float],
    now: Optional[datetime] = None,
) -> Tuple[float, int]:
    """
    Tính toán số dư công nợ thực tế và tuổi nợ tối đa của đại lý.
    Khớp chính xác với Sổ chi tiết công nợ (S31-DN), Sổ phân tích tuổi nợ và CSDL SQL Server:
    - Công nợ từng đơn = Tổng tiền - Đã thanh toán (phiếu thu + thu theo tuyến) - Giảm trừ do trả hàng
    - Đơn có trạng thái PAID mà chưa có phiếu thu thì coi như đã tất toán
    - Các khoản phiếu thu không gắn mã đơn được trừ lũy kế (FIFO) vào các đơn nợ cũ nhất
    - Tuổi nợ chỉ tính trên các đơn hàng THỰC SỰ CÒN NỢ (> 0)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    sorted_orders = sorted(
        dealer_orders,
        key=lambda x: x.get("dt_obj") or parse_tx_date(x.get("created_at"))
    )
    open_orders = []

    for o in sorted_orders:
        if o.get("status") in ("CANCELLED", "REJECTED", "DRAFT"):
            continue
        ord_code = o.get("order_code", "")
        amt = float(o.get("total_amount", 0.0) or 0.0)
        adj_offset = adjustments_by_order.get(ord_code, 0.0)
        paid_offset = payments_by_order.get(ord_code, 0.0)

        if o.get("status") == "PAID" and paid_offset == 0:
            paid_offset = max(0.0, amt - adj_offset)

        eff_rem = max(0.0, amt - paid_offset - adj_offset)
        if eff_rem > 0:
            dt_obj = o.get("dt_obj") or parse_tx_date(o.get("created_at"))
            open_orders.append({
                "order_code": ord_code,
                "amount": eff_rem,
                "dt_obj": dt_obj,
            })

    # Giảm trừ các khoản thanh toán chung (không gắn order_code cụ thể) theo FIFO
    rem_unallocated = float(unallocated_payments or 0.0)
    total_remaining = 0.0
    max_age = 0

    for item in open_orders:
        if rem_unallocated > 0:
            deduct = min(item["amount"], rem_unallocated)
            item["amount"] -= deduct
            rem_unallocated -= deduct

        if item["amount"] > 0:
            total_remaining += item["amount"]
            order_dt = item["dt_obj"]
            if order_dt.tzinfo is None:
                order_dt = order_dt.replace(tzinfo=timezone.utc)
            days_old = max(0, (now - order_dt).days)
            if days_old > max_age:
                max_age = days_old

    return round(total_remaining, 2), max_age


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
    overdue_days_allowed: int = 30          # Số ngày nợ quá hạn cho phép
    customer_group: Optional[str] = "Đại lý cấp 1"
    status: str = "Đang hoạt động"          # Đang hoạt động | Tạm ngừng | Đã khóa
    lock_reason: Optional[str] = None
    locked_at: Optional[Union[str, datetime]] = None
    locked_by: Optional[str] = None
    transaction_count: Optional[int] = 0
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
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
        assigned_sale_id=8,  # Nhân viên kinh doanh phụ trách: Nguyễn Văn A
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
        assigned_sale_id=8,  # Nhân viên kinh doanh phụ trách: Nguyễn Văn A
        credit_limit=20000000.0,
        customer_group="khach_le",
        status="Đang hoạt động",
    ),
    7: Dealer(
        id=7,
        code="DL007",
        name="Vũ Mua Hàng",
        phone="0911223377",
        email="muahang@congty.vn",
        address="12 Hàng Buồm, Hà Nội",
        region="Hà Nội",
        assigned_sale_id=None,
        credit_limit=50000000.0,
        customer_group="Đại lý cấp 1",
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
                if hasattr(db_dealer, "overdue_days_allowed"):
                    db_dealer.overdue_days_allowed = getattr(d, "overdue_days_allowed", getattr(d, "max_debt_days", 30))
                db_dealer.customer_group = getattr(d, "customer_group", getattr(db_dealer, "customer_group", "Đại lý cấp 1"))
                if hasattr(db_dealer, "transaction_count"):
                    db_dealer.transaction_count = getattr(d, "transaction_count", 0) or 0
                if hasattr(db_dealer, "status"):
                    db_dealer.status = getattr(d, "status", "Đang hoạt động")
                    db_dealer.lock_reason = getattr(d, "lock_reason", None)
                    locked_at_val = getattr(d, "locked_at", None)
                    if locked_at_val:
                        if isinstance(locked_at_val, str):
                            try:
                                db_dealer.locked_at = datetime.fromisoformat(locked_at_val.replace("Z", "+00:00"))
                            except Exception:
                                db_dealer.locked_at = None
                        elif isinstance(locked_at_val, datetime):
                            db_dealer.locked_at = locked_at_val
                    else:
                        db_dealer.locked_at = None
                    db_dealer.locked_by = getattr(d, "locked_by", None)
                if hasattr(db_dealer, "warehouse_id"):
                    db_dealer.warehouse_id = getattr(d, "warehouse_id", None)
                if hasattr(db_dealer, "warehouse_name"):
                    db_dealer.warehouse_name = getattr(d, "warehouse_name", None)

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
                        overdue_days_allowed=int(getattr(entity, "overdue_days_allowed", None) or getattr(entity, "max_debt_days", None) or 30),
                        customer_group=getattr(entity, "customer_group", None) or "Đại lý cấp 1",
                        status="Đang hoạt động" if ("ho?t" in str(getattr(entity, "status", "")) or "Ðang" in str(getattr(entity, "status", ""))) else (getattr(entity, "status", "Đang hoạt động") or "Đang hoạt động"),
                        lock_reason=getattr(entity, "lock_reason", None),
                        locked_at=entity.locked_at.isoformat() if hasattr(getattr(entity, "locked_at", None), "isoformat") else getattr(entity, "locked_at", None),
                        locked_by=getattr(entity, "locked_by", None),
                        transaction_count=int(getattr(entity, "transaction_count", 0) or 0),
                        warehouse_id=getattr(entity, "warehouse_id", None),
                        warehouse_name=getattr(entity, "warehouse_name", None),
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
