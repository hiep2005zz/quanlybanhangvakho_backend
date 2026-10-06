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
<<<<<<< HEAD
                        transaction_count=int(getattr(entity, "transaction_count", 0) or 0),
=======
>>>>>>> origin/test
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
