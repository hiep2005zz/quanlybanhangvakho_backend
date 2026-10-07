# backend/app/api/v1/endpoints/audit_logs.py
"""
Audit Logs API Endpoints:
- GET /api/v1/audit-logs: Xem danh sách nhật ký thao tác (Chỉ Admin).
  Hỗ trợ lọc theo user_id, entity_type, from_date, to_date, entity_id, phân trang (page, page_size).
- GET /api/v1/audit-logs/entity/{entity_type}/{entity_id}: Xem nhanh lịch sử riêng của 1 thực thể.
"""
from typing import Optional, List
from datetime import datetime, timezone
import math
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.api.deps import require_roles, require_permission, get_current_user
from app.core.database import get_db
from app.core.rbac import Role, Permission
from app.schemas.auth import UserResponse
from app.schemas.audit import AuditLogItem, AuditLogListResponse
from app.models.entities import AuditLogEntity, UserEntity
from app.models.user import USERS_DB, load_users_db
from app.services.audit_service import MEMORY_AUDIT_LOGS, ALLOWED_ACTION_TYPES, SYSTEM_AUDIT_ACTION_TYPES

router = APIRouter()


def _format_datetime(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


def _get_user_avatar(user_id: Optional[int], user_name: Optional[str], db: Optional[Session] = None) -> Optional[str]:
    """Tìm avatar_url của người dùng theo user_id hoặc user_name để hiển thị trong nhật ký."""
    try:
        load_users_db()
        for u in USERS_DB.values():
            if user_id is not None and u.id == user_id:
                if getattr(u, "avatar_url", None):
                    return u.avatar_url
            if user_name:
                uname = user_name.strip().lower()
                if u.username.lower() == uname or (u.full_name and u.full_name.strip().lower() == uname):
                    if getattr(u, "avatar_url", None):
                        return u.avatar_url
    except Exception:
        pass

    if db is not None:
        try:
            user_rec = None
            if user_id is not None:
                user_rec = db.query(UserEntity).filter(UserEntity.id == user_id).first()
            if not user_rec and user_name:
                user_rec = db.query(UserEntity).filter(
                    (UserEntity.username.ilike(user_name.strip())) |
                    (UserEntity.full_name.ilike(user_name.strip()))
                ).first()
            if user_rec and getattr(user_rec, "avatar_url", None):
                return user_rec.avatar_url
        except Exception:
            pass

    return None


@router.get("", response_model=AuditLogListResponse)
def get_audit_logs(
    user_id: Optional[int] = Query(None, description="Lọc theo ID người thực hiện"),
    entity_type: Optional[str] = Query(None, description="Lọc theo loại đối tượng (Inventory, ProductPrice, CustomerDebt, Invoice)"),
    entity_id: Optional[str] = Query(None, description="Lọc theo mã/ID đối tượng"),
    action_type: Optional[str] = Query(None, description="Lọc theo hành động"),
    from_date: Optional[str] = Query(None, description="Từ ngày (YYYY-MM-DD hoặc ISO string)"),
    to_date: Optional[str] = Query(None, description="Đến ngày (YYYY-MM-DD hoặc ISO string)"),
    page: int = Query(1, ge=1, description="Trang hiện tại"),
    page_size: int = Query(20, ge=1, le=100, description="Số bản ghi trên mỗi trang"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value])),
):
    """
    Xem danh sách nhật ký thao tác:
    - Phân quyền: Chỉ cho phép role 'Quản trị hệ thống' (Admin).
    - Giới hạn: CHỈ hiển thị các thao tác trên Tồn kho, Giá bán, Hạn mức công nợ và Hoá đơn.
    - Hỗ trợ lọc theo: user_id, entity_type, entity_id, action_type, from_date, to_date.
    - Phân trang: page, page_size, sắp xếp created_at DESC.
    """
    # 1. Truy vấn từ Database
    try:
        # Luôn lọc chặt chẽ chỉ lấy các action_type thuộc 4 nhóm nghiệp vụ
        query = db.query(AuditLogEntity).filter(AuditLogEntity.action_type.in_(SYSTEM_AUDIT_ACTION_TYPES))

        if user_id is not None:
            query = query.filter(AuditLogEntity.user_id == user_id)

        if entity_type and entity_type.strip() and entity_type.strip().lower() != "all":
            clean_type = entity_type.strip()
            if clean_type == "Inventory":
                # Tồn kho: lọc theo entity_type='Inventory' hoặc entity_type='Product' có hành động liên quan kho
                query = query.filter(
                    (AuditLogEntity.entity_type == "Inventory") |
                    ((AuditLogEntity.entity_type == "Product") & (AuditLogEntity.action_type.in_(["INVENTORY_ADJUST", "STOCK_RECEIPT", "STOCK_ISSUE"])))
                )
            elif clean_type == "ProductPrice":
                # Giá sản phẩm: lọc theo entity_type in ['ProductPrice', 'PriceBook'] hoặc 'Product' có action_type='PRICE_CHANGE'
                query = query.filter(
                    (AuditLogEntity.entity_type.in_(["ProductPrice", "PriceBook"])) |
                    ((AuditLogEntity.entity_type == "Product") & (AuditLogEntity.action_type == "PRICE_CHANGE"))
                )
            elif clean_type in ["CustomerDebt", "Debt"]:
                # Hạn mức công nợ: lọc theo CustomerDebt hoặc Dealer có action_type='DEBT_LIMIT_CHANGE'
                query = query.filter(
                    (AuditLogEntity.entity_type.in_(["CustomerDebt", "DealerDebtLimit"])) |
                    ((AuditLogEntity.entity_type == "Dealer") & (AuditLogEntity.action_type == "DEBT_LIMIT_CHANGE"))
                )
            elif clean_type in ["Invoice", "Order"]:
                # Hóa đơn / đơn hàng
                query = query.filter(AuditLogEntity.entity_type.in_(["Invoice", "Order"]))
            else:
                query = query.filter(AuditLogEntity.entity_type == clean_type)

        if entity_id and entity_id.strip():
            query = query.filter(AuditLogEntity.entity_id.ilike(f"%{entity_id.strip()}%"))

        if action_type and action_type.strip() and action_type.strip().lower() != "all":
            query = query.filter(AuditLogEntity.action_type == action_type.strip())

        if from_date and from_date.strip():
            try:
                dt_from = datetime.fromisoformat(from_date.strip().replace("Z", "+00:00"))
                query = query.filter(AuditLogEntity.created_at >= dt_from)
            except Exception:
                pass

        if to_date and to_date.strip():
            try:
                dt_to = datetime.fromisoformat(to_date.strip().replace("Z", "+00:00"))
                # Nếu chỉ truyền date YYYY-MM-DD thì đẩy tới 23:59:59
                if len(to_date.strip()) == 10:
                    dt_to = dt_to.replace(hour=23, minute=59, second=59)
                query = query.filter(AuditLogEntity.created_at <= dt_to)
            except Exception:
                pass

        total = query.count()
        offset = (page - 1) * page_size
        records = query.order_by(desc(AuditLogEntity.created_at)).offset(offset).limit(page_size).all()

        items = [
            AuditLogItem(
                id=r.id,
                user_id=r.user_id,
                user_name=r.user_name,
                user_avatar=_get_user_avatar(r.user_id, r.user_name, db),
                action_type=r.action_type,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                old_values=r.old_values,
                new_values=r.new_values,
                reason=r.reason,
                ip_address=r.ip_address,
                created_at=_format_datetime(r.created_at),
            )
            for r in records
        ]

        total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

        return AuditLogListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    except Exception as e:
        print(f"Audit log DB query fallback to memory: {e}")
        # Fallback query từ MEMORY_AUDIT_LOGS: chỉ lấy các bản ghi thuộc 4 nhóm nghiệp vụ
        filtered = [m for m in MEMORY_AUDIT_LOGS if m.get("action_type") in SYSTEM_AUDIT_ACTION_TYPES]

        if user_id is not None:
            filtered = [m for m in filtered if m.get("user_id") == user_id]

        if entity_type and entity_type.strip() and entity_type.strip().lower() != "all":
            clean_type = entity_type.strip()
            if clean_type == "Inventory":
                filtered = [
                    m for m in filtered
                    if m.get("entity_type") == "Inventory"
                    or (m.get("entity_type") == "Product" and m.get("action_type") in ["INVENTORY_ADJUST", "STOCK_RECEIPT", "STOCK_ISSUE"])
                ]
            elif clean_type == "ProductPrice":
                filtered = [
                    m for m in filtered
                    if m.get("entity_type") in ["ProductPrice", "PriceBook"]
                    or (m.get("entity_type") == "Product" and m.get("action_type") == "PRICE_CHANGE")
                ]
            elif clean_type in ["CustomerDebt", "Debt"]:
                filtered = [
                    m for m in filtered
                    if m.get("entity_type") in ["CustomerDebt", "DealerDebtLimit"]
                    or (m.get("entity_type") == "Dealer" and m.get("action_type") == "DEBT_LIMIT_CHANGE")
                ]
            elif clean_type in ["Invoice", "Order"]:
                filtered = [m for m in filtered if m.get("entity_type") in ["Invoice", "Order"]]
            else:
                filtered = [m for m in filtered if m.get("entity_type") == clean_type]

        if entity_id and entity_id.strip():
            q_id = entity_id.strip().lower()
            filtered = [m for m in filtered if q_id in str(m.get("entity_id", "")).lower()]

        if action_type and action_type.strip() and action_type.strip().lower() != "all":
            filtered = [m for m in filtered if m.get("action_type") == action_type.strip()]

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        page_records = filtered[start:end]

        items = [
            AuditLogItem(
                id=m["id"],
                user_id=m.get("user_id"),
                user_name=m.get("user_name"),
                user_avatar=_get_user_avatar(m.get("user_id"), m.get("user_name"), db),
                action_type=m["action_type"],
                entity_type=m["entity_type"],
                entity_id=str(m["entity_id"]),
                old_values=m.get("old_values"),
                new_values=m.get("new_values"),
                reason=m.get("reason"),
                ip_address=m.get("ip_address"),
                created_at=str(m.get("created_at")),
            )
            for m in page_records
        ]

        return AuditLogListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=max(1, math.ceil(total / page_size)) if total > 0 else 1,
        )


@router.get("/entity/{entity_type}/{entity_id}", response_model=List[AuditLogItem])
def get_entity_audit_logs(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Xem nhanh lịch sử thay đổi riêng của 1 mặt hàng, hóa đơn hoặc hạn mức khách hàng.
    Mọi nhân viên có quyền xem nghiệp vụ tương ứng (hoặc Admin/Manager) đều có thể xem lịch sử đối tượng này.
    """
    try:
        matched_entity_types = [entity_type]
        if entity_type.lower() in ("dealer", "customerdebt", "dealerdebtlimit"):
            matched_entity_types = ["Dealer", "CustomerDebt", "DealerDebtLimit"]
        elif entity_type.lower() in ("product", "productprice", "pricebook"):
            matched_entity_types = ["Product", "ProductPrice", "PriceBook"]

        records = (
            db.query(AuditLogEntity)
            .filter(
                AuditLogEntity.entity_type.in_(matched_entity_types),
                AuditLogEntity.entity_id == str(entity_id),
                AuditLogEntity.action_type.in_(ALLOWED_ACTION_TYPES),
            )
            .order_by(desc(AuditLogEntity.created_at))
            .limit(100)
            .all()
        )

        return [
            AuditLogItem(
                id=r.id,
                user_id=r.user_id,
                user_name=r.user_name,
                user_avatar=_get_user_avatar(r.user_id, r.user_name, db),
                action_type=r.action_type,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                old_values=r.old_values,
                new_values=r.new_values,
                reason=r.reason,
                ip_address=r.ip_address,
                created_at=_format_datetime(r.created_at),
            )
            for r in records
        ]
    except Exception as e:
        print(f"Entity audit log DB error fallback: {e}")
        matched = [
            m for m in MEMORY_AUDIT_LOGS
            if m.get("action_type") in ALLOWED_ACTION_TYPES
            and m.get("entity_type") in matched_entity_types
            and str(m.get("entity_id")) == str(entity_id)
        ]
        return [
            AuditLogItem(
                id=m["id"],
                user_id=m.get("user_id"),
                user_name=m.get("user_name"),
                user_avatar=_get_user_avatar(m.get("user_id"), m.get("user_name"), db),
                action_type=m["action_type"],
                entity_type=m["entity_type"],
                entity_id=str(m["entity_id"]),
                old_values=m.get("old_values"),
                new_values=m.get("new_values"),
                reason=m.get("reason"),
                ip_address=m.get("ip_address"),
                created_at=str(m.get("created_at")),
            )
            for m in matched[:50]
        ]


@router.delete("/{log_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def delete_audit_log(
    log_id: int,
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Theo nguyên tắc toàn vẹn và bất biến (Audit Trail Immutability):
    Nhật ký thao tác (Audit log) là bất biến (Append-only), tuyệt đối không được phép chỉnh sửa hoặc xóa dưới bất kỳ hình thức nào.
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Nhật ký thao tác là dữ liệu bất biến (Append-only), nghiêm cấm chỉnh sửa và xóa để đảm bảo tính minh bạch và tuân thủ kiểm toán."
    )


@router.delete("", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def clear_all_audit_logs(
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Theo nguyên tắc toàn vẹn và bất biến (Audit Trail Immutability):
    Nhật ký thao tác (Audit log) là bất biến (Append-only), tuyệt đối không được phép chỉnh sửa hoặc xóa dưới bất kỳ hình thức nào.
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Nhật ký thao tác là dữ liệu bất biến (Append-only), nghiêm cấm chỉnh sửa và xóa để đảm bảo tính minh bạch và tuân thủ kiểm toán."
    )
