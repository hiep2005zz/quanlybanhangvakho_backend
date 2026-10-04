# backend/app/services/audit_service.py
"""
Audit Log Service:
Cung cấp helper ghi vết (log_audit_event) và truy vấn nhật ký thao tác
cho User Story SCRUM-29: Ghi và xem nhật ký thao tác trên tồn kho, giá, hạn mức công nợ và hoá đơn.
"""
from datetime import datetime, timezone
import json
from typing import Optional, Any, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import desc
from fastapi import Request

from app.models.entities import AuditLogEntity
from app.models.user import USERS_DB

# In-memory fallback if SQL engine has temporary issues or testing
MEMORY_AUDIT_LOGS: List[dict] = []
_MEMORY_AUDIT_ID = 1


def _to_json_str(val: Any) -> Optional[str]:
    """Helper chuyển đổi Python object / dict sang JSON string chuẩn UTF-8."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False, default=str)
    except Exception:
        return str(val)


def log_audit_event(
    db: Optional[Session],
    user: Any,
    action_type: str,
    entity_type: str,
    entity_id: Any,
    old_val: Any = None,
    new_val: Any = None,
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
    request: Optional[Request] = None,
) -> Optional[AuditLogEntity]:
    """
    Ghi nhật ký thao tác vào bảng audit_logs:
    - db: SQLAlchemy Session (nếu có, có thể fallback lưu in-memory)
    - user: UserResponse hoặc UserInDB hoặc dict (chứa id / username / full_name)
    - action_type: 'INVENTORY_ADJUST', 'PRICE_CHANGE', 'DEBT_LIMIT_CHANGE', 'INVOICE_EDIT', etc.
    - entity_type: 'Product', 'CustomerDebt', 'Invoice', etc.
    - entity_id: ID / Code của bản ghi bị tác động
    - old_val: Dữ liệu hoặc giá trị trước khi sửa (dict, số, chuỗi...)
    - new_val: Dữ liệu hoặc giá trị sau khi sửa (dict, số, chuỗi...)
    - reason: Lý do điều chỉnh
    - ip_address: IP thực hiện
    """
    global _MEMORY_AUDIT_ID

    # 0. Chỉ kích hoạt ghi log ở các API biến động tài sản:
    # - Nhập / Xuất / Điều chỉnh tồn kho: INVENTORY_ADJUST, STOCK_RECEIPT, STOCK_ISSUE
    # - Thay đổi giá bán, giá vốn: PRICE_CHANGE
    # - Thay đổi hạn mức công nợ khách hàng: DEBT_LIMIT_CHANGE
    # - Thay đổi trạng thái / hủy hóa đơn: INVOICE_EDIT, INVOICE_CANCEL
    ALLOWED_ACTION_TYPES = {
        "INVENTORY_ADJUST",
        "STOCK_RECEIPT",
        "STOCK_ISSUE",
        "PRICE_CHANGE",
        "DEBT_LIMIT_CHANGE",
        "INVOICE_EDIT",
        "INVOICE_CANCEL",
        "DEALER_STATUS_CHANGE",
        "DEALER_ASSIGNMENT",
    }
    if action_type not in ALLOWED_ACTION_TYPES:
        return None

    # Bóc tách thông tin user
    user_id = None
    user_name = None

    if user:
        if hasattr(user, "username"):
            user_name = getattr(user, "full_name", None) or getattr(user, "username")
            # Tìm ID nếu UserResponse không mang theo id
            u_in_db = USERS_DB.get(user.username.lower())
            user_id = getattr(user, "id", None) or (u_in_db.id if u_in_db else None)
        elif isinstance(user, dict):
            user_id = user.get("id")
            user_name = user.get("full_name") or user.get("username")
        elif isinstance(user, str):
            user_name = user
            u_in_db = USERS_DB.get(user.lower())
            user_id = u_in_db.id if u_in_db else None

    # Lấy client ip nếu truyền request
    if not ip_address and request:
        try:
            ip_address = request.client.host if request.client else None
        except Exception:
            pass

    # Chuẩn hóa đối tượng để so sánh tránh log rác (No-op change)
    if isinstance(old_val, dict) and isinstance(new_val, dict):
        # Nếu new_val chỉ chứa các trường thay đổi (partial update),
        # ta so sánh new_val với các trường tương ứng trong old_val
        if set(new_val.keys()).issubset(set(old_val.keys())):
            old_subset = {k: old_val.get(k) for k in new_val.keys()}
            if old_subset == new_val:
                return None
        elif old_val == new_val:
            return None
    elif old_val is not None and new_val is not None:
        if old_val == new_val:
            return None

    old_json = _to_json_str(old_val)
    new_json = _to_json_str(new_val)

    # Loại bỏ log rác (No-op change): nếu old_values == new_values thì bỏ qua, không ghi vào database
    if old_json is not None and new_json is not None and old_json == new_json:
        return None

    str_entity_id = str(entity_id)

    # 1. Thử ghi vào Database SQL
    audit_record = None
    if db is not None:
        try:
            audit_record = AuditLogEntity(
                user_id=user_id,
                user_name=user_name,
                action_type=action_type,
                entity_type=entity_type,
                entity_id=str_entity_id,
                old_values=old_json,
                new_values=new_json,
                reason=reason,
                ip_address=ip_address,
                created_at=datetime.now(timezone.utc),
            )
            db.add(audit_record)
            db.commit()
            db.refresh(audit_record)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"Warning: Failed to save audit log to DB: {e}")
            audit_record = None

    # 2. Luôn đồng bộ vào memory cache để dự phòng hoặc query nhanh
    mem_entry = {
        "id": audit_record.id if audit_record else _MEMORY_AUDIT_ID,
        "user_id": user_id,
        "user_name": user_name or "Hệ thống",
        "action_type": action_type,
        "entity_type": entity_type,
        "entity_id": str_entity_id,
        "old_values": old_json,
        "new_values": new_json,
        "reason": reason,
        "ip_address": ip_address,
        "created_at": audit_record.created_at.isoformat() if audit_record else datetime.now(timezone.utc).isoformat(),
    }
    _MEMORY_AUDIT_ID += 1
    MEMORY_AUDIT_LOGS.insert(0, mem_entry)

    return audit_record
