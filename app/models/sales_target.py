# backend/app/models/sales_target.py
import json
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Unicode, UnicodeText
from app.core.database import Base

def get_utc_now():
    return datetime.now(timezone.utc)

class SalesTargetEntity(Base):
    __tablename__ = "sales_targets"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    target_type = Column(String(20), default="salesperson", nullable=False)  # 'salesperson' | 'region'
    employee_id = Column(Integer, nullable=True, index=True)
    employee_name = Column(Unicode(255), nullable=True)
    region = Column(Unicode(100), nullable=True, index=True)
    month_year = Column(String(7), nullable=False, index=True)  # YYYY-MM, e.g. "2026-10"
    target_amount = Column(Float, default=0.0, nullable=False)
    is_locked = Column(Boolean, default=False, nullable=False)
    note = Column(UnicodeText, nullable=True)
    created_by = Column(Unicode(100), default="admin", nullable=False)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)
    audit_logs_json = Column(UnicodeText, nullable=True)

    @property
    def audit_logs(self) -> List[Dict[str, Any]]:
        if self.audit_logs_json:
            try:
                res = json.loads(self.audit_logs_json)
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        return []

    @audit_logs.setter
    def audit_logs(self, val: List[Dict[str, Any]]):
        self.audit_logs_json = json.dumps(val or [], ensure_ascii=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target_type": self.target_type,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "region": self.region,
            "month_year": self.month_year,
            "target_amount": float(self.target_amount or 0.0),
            "is_locked": bool(self.is_locked),
            "note": self.note,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "audit_logs": self.audit_logs,
        }

# File JSON dự phòng đồng bộ dữ liệu
TARGETS_FILE = os.path.join(os.path.dirname(__file__), "sales_targets_data.json")

def load_sales_targets_db() -> Dict[int, Dict[str, Any]]:
    if not os.path.exists(TARGETS_FILE):
        return {}
    try:
        with open(TARGETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except Exception:
        return {}

def save_sales_targets_db(targets: Dict[int, Dict[str, Any]]) -> None:
    try:
        with open(TARGETS_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in targets.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving sales targets file: {e}")

SALES_TARGETS_DB: Dict[int, Dict[str, Any]] = load_sales_targets_db()

# Khởi tạo dữ liệu mẫu nếu chưa có
if not SALES_TARGETS_DB:
    sample_targets = {
        1: {
            "id": 1,
            "target_type": "salesperson",
            "employee_id": 3,
            "employee_name": "Trần Bán Hàng",
            "region": "Khu vực Miền Bắc",
            "month_year": "2026-10",
            "target_amount": 50000000.0,
            "is_locked": False,
            "note": "Chỉ tiêu bán hàng tháng 10 cho Trần Bán Hàng",
            "created_by": "sales_manager",
            "created_at": "2026-10-01T08:00:00Z",
            "updated_at": "2026-10-01T08:00:00Z",
            "audit_logs": []
        },
        2: {
            "id": 2,
            "target_type": "salesperson",
            "employee_id": 3,
            "employee_name": "Trần Bán Hàng",
            "region": "Khu vực Miền Bắc",
            "month_year": "2026-09",
            "target_amount": 45000000.0,
            "is_locked": True,
            "note": "Chỉ tiêu tháng 9 đã qua kỳ chốt sổ",
            "created_by": "sales_manager",
            "created_at": "2026-09-01T08:00:00Z",
            "updated_at": "2026-09-01T08:00:00Z",
            "audit_logs": []
        },
        3: {
            "id": 3,
            "target_type": "region",
            "employee_id": None,
            "employee_name": None,
            "region": "Hà Nội",
            "month_year": "2026-10",
            "target_amount": 150000000.0,
            "is_locked": False,
            "note": "Chỉ tiêu khu vực Hà Nội",
            "created_by": "sales_manager",
            "created_at": "2026-10-01T08:00:00Z",
            "updated_at": "2026-10-01T08:00:00Z",
            "audit_logs": []
        },
        4: {
            "id": 4,
            "target_type": "region",
            "employee_id": None,
            "employee_name": None,
            "region": "TP. HCM",
            "month_year": "2026-10",
            "target_amount": 200000000.0,
            "is_locked": False,
            "note": "Chỉ tiêu khu vực TP. HCM",
            "created_by": "sales_manager",
            "created_at": "2026-10-01T08:00:00Z",
            "updated_at": "2026-10-01T08:00:00Z",
            "audit_logs": []
        }
    }
    SALES_TARGETS_DB.update(sample_targets)
    save_sales_targets_db(SALES_TARGETS_DB)
