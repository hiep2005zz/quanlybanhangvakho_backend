# backend/app/api/v1/endpoints/suppliers.py
"""
Quản lý danh mục nhà cung cấp.

- Khai báo: mã, tên, mã số thuế, người liên hệ, điều khoản thanh toán.
- Nhà cung cấp KHÔNG bị xoá: chỉ chuyển sang "ngừng giao dịch" (có thể mở lại).
  Vì vậy cố ý KHÔNG có endpoint DELETE, để phiếu nhập sau này luôn truy nguyên được nguồn hàng.
- Quyền được kiểm tra ở backend (Zero-Trust), không chỉ ẩn nút ở frontend.
"""
import re
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.database import SessionLocal
from app.core.rbac import Role
from app.models.supplier import SupplierEntity
from app.schemas.auth import UserResponse
from app.schemas.supplier import (
    SupplierCreate,
    SupplierDeactivateRequest,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdate,
)

router = APIRouter()

# Vai trò được xem / quản lý nhà cung cấp. Muốn mở rộng (ví dụ cho Nhân viên mua hàng)
# chỉ cần thêm vào danh sách này.
SUPPLIER_READ_ROLES = [
    Role.WAREHOUSE_STAFF.value,
    Role.WAREHOUSE_MANAGER.value,
    Role.SYSTEM_ADMIN.value,
]
SUPPLIER_WRITE_ROLES = [
    Role.WAREHOUSE_STAFF.value,
    Role.WAREHOUSE_MANAGER.value,
    Role.SYSTEM_ADMIN.value,
]

require_supplier_read = require_roles(SUPPLIER_READ_ROLES)
require_supplier_write = require_roles(SUPPLIER_WRITE_ROLES)

CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,29}$")
TAX_PATTERN = re.compile(r"^\d{10}(-?\d{3})?$")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _normalize_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not CODE_PATTERN.fullmatch(code):
        raise _bad_request(
            "Mã nhà cung cấp chỉ gồm chữ cái, chữ số, dấu gạch ngang hoặc gạch dưới, "
            "dài 2 đến 30 ký tự (ví dụ: NCC001)."
        )
    return code


def _normalize_name(value: str) -> str:
    name = " ".join((value or "").split())
    if len(name) < 2:
        raise _bad_request("Tên nhà cung cấp phải có ít nhất 2 ký tự.")
    return name


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    text_value = " ".join((value or "").split())
    return text_value or None


def _normalize_tax_code(value: Optional[str]) -> Optional[str]:
    tax = (value or "").strip().replace(" ", "")
    if not tax:
        return None
    if not TAX_PATTERN.fullmatch(tax):
        raise _bad_request(
            "Mã số thuế không hợp lệ. Nhập 10 chữ số, hoặc 13 chữ số cho đơn vị phụ thuộc "
            "(ví dụ: 0123456789 hoặc 0123456789-001)."
        )
    if len(tax) == 13:  # 13 chữ số liền nhau -> chuẩn hoá thành 10-3
        tax = f"{tax[:10]}-{tax[10:]}"
    return tax


def _get_or_404(db: Session, code: str) -> SupplierEntity:
    entity = db.query(SupplierEntity).filter(SupplierEntity.code == (code or "").strip().upper()).first()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy nhà cung cấp có mã '{code}'.",
        )
    return entity


def _ensure_tax_code_unique(db: Session, tax_code: Optional[str], exclude_id: Optional[int] = None) -> None:
    if not tax_code:
        return
    query = db.query(SupplierEntity).filter(SupplierEntity.tax_code == tax_code)
    if exclude_id is not None:
        query = query.filter(SupplierEntity.id != exclude_id)
    existing = query.first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Mã số thuế {tax_code} đã được dùng cho nhà cung cấp '{existing.name}' ({existing.code}).",
        )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dữ liệu bị trùng (mã hoặc mã số thuế đã tồn tại). Vui lòng kiểm tra lại.",
        )


def _supplier_has_receipts(db: Session, entity: SupplierEntity) -> bool:
    """Kiểm tra xem nhà cung cấp đã có phiếu nhập kho nào liên kết hay chưa."""
    from app.models.entities import InventoryTransactionEntity
    from app.api.v1.endpoints.inventory import INVENTORY_TRANSACTIONS

    like_name = f"%{entity.name}%"
    like_code = f"%{entity.code}%"
    has_db = db.query(InventoryTransactionEntity).filter(
        InventoryTransactionEntity.type == "receipt",
        or_(
            InventoryTransactionEntity.reason.ilike(like_name),
            InventoryTransactionEntity.reason.ilike(like_code)
        )
    ).first() is not None

    if has_db:
        return True

    for tx in INVENTORY_TRANSACTIONS:
        tx_type = getattr(tx, "type", None) or (tx.get("type") if isinstance(tx, dict) else None)
        tx_reason = getattr(tx, "reason", "") or (tx.get("reason", "") if isinstance(tx, dict) else "")
        if tx_type == "receipt" and (entity.name.lower() in tx_reason.lower() or entity.code.lower() in tx_reason.lower()):
            return True

    return False


class SupplierDetailResponse(SupplierResponse):
    has_receipts: bool = False


class SupplierListDetailResponse(BaseModel):
    items: List[SupplierDetailResponse]
    total: int
    active_count: int
    inactive_count: int


@router.get("", response_model=SupplierListDetailResponse)
def list_suppliers(
    search: Optional[str] = Query(None, max_length=100, description="Tìm theo mã, tên, MST, người liên hệ"),
    status_filter: str = Query("all", alias="status", description="all | active | inactive"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_read),
):
    """Danh sách nhà cung cấp (tìm kiếm + lọc trạng thái). Số đếm tính theo kết quả tìm kiếm."""
    if status_filter not in ("all", "active", "inactive"):
        raise _bad_request("Giá trị 'status' chỉ nhận: all, active, inactive.")

    query = db.query(SupplierEntity)
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(
                SupplierEntity.code.ilike(like),
                SupplierEntity.name.ilike(like),
                SupplierEntity.tax_code.ilike(like),
                SupplierEntity.contact_person.ilike(like),
            )
        )

    rows = query.order_by(SupplierEntity.name.asc()).all()
    active_count = sum(1 for r in rows if r.is_active)
    inactive_count = len(rows) - active_count

    if status_filter == "active":
        rows = [r for r in rows if r.is_active]
    elif status_filter == "inactive":
        rows = [r for r in rows if not r.is_active]

    detail_items = []
    for r in rows:
        item_dict = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        item_dict["has_receipts"] = _supplier_has_receipts(db, r)
        detail_items.append(SupplierDetailResponse(**item_dict))

    return SupplierListDetailResponse(
        items=detail_items,
        total=len(detail_items),
        active_count=active_count,
        inactive_count=inactive_count,
    )


@router.get("/{code}", response_model=SupplierDetailResponse)
def get_supplier(
    code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_read),
):
    entity = _get_or_404(db, code)
    item_dict = {c.name: getattr(entity, c.name) for c in entity.__table__.columns}
    item_dict["has_receipts"] = _supplier_has_receipts(db, entity)
    return SupplierDetailResponse(**item_dict)


@router.delete("/{code}")
def delete_supplier(
    code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_write),
):
    """
    Xóa nhà cung cấp.
    Nếu nhà cung cấp đã có phiếu nhập kho: CHẶN xóa (HTTP 400), chỉ cho phép ngừng giao dịch.
    """
    entity = _get_or_404(db, code)
    if _supplier_has_receipts(db, entity):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nhà cung cấp đã có phiếu nhập thì không xoá được, chỉ ngừng giao dịch."
        )

    db.delete(entity)
    _commit(db)
    return {"message": f"Đã xóa nhà cung cấp '{entity.name}' ({entity.code}) thành công."}


@router.post("", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(
    data: SupplierCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_write),
):
    code = _normalize_code(data.code)
    name = _normalize_name(data.name)
    tax_code = _normalize_tax_code(data.tax_code)

    if db.query(SupplierEntity).filter(SupplierEntity.code == code).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Mã nhà cung cấp '{code}' đã tồn tại.",
        )
    _ensure_tax_code_unique(db, tax_code)

    entity = SupplierEntity(
        code=code,
        name=name,
        tax_code=tax_code,
        contact_person=_normalize_optional_text(data.contact_person),
        payment_terms=_normalize_optional_text(data.payment_terms),
        is_active=True,
        created_by=current_user.username,
    )
    db.add(entity)
    _commit(db)
    db.refresh(entity)
    return entity


@router.put("/{code}", response_model=SupplierResponse)
def update_supplier(
    code: str,
    data: SupplierUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_write),
):
    """Sửa thông tin. Mã nhà cung cấp cố định, không đổi được."""
    entity = _get_or_404(db, code)

    name = _normalize_name(data.name)
    tax_code = _normalize_tax_code(data.tax_code)
    _ensure_tax_code_unique(db, tax_code, exclude_id=entity.id)

    entity.name = name
    entity.tax_code = tax_code
    entity.contact_person = _normalize_optional_text(data.contact_person)
    entity.payment_terms = _normalize_optional_text(data.payment_terms)
    _commit(db)
    db.refresh(entity)
    return entity


@router.post("/{code}/deactivate", response_model=SupplierResponse)
def deactivate_supplier(
    code: str,
    data: Optional[SupplierDeactivateRequest] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_write),
):
    """Ngừng giao dịch với nhà cung cấp (thay cho xoá). Có thể mở lại bằng /activate."""
    entity = _get_or_404(db, code)
    if not entity.is_active:
        raise _bad_request("Nhà cung cấp này đã ngừng giao dịch từ trước.")

    entity.is_active = False
    entity.inactive_reason = _normalize_optional_text(data.reason if data else None)
    entity.deactivated_at = datetime.now()
    entity.deactivated_by = current_user.username
    _commit(db)
    db.refresh(entity)
    return entity


@router.post("/{code}/activate", response_model=SupplierResponse)
def activate_supplier(
    code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_supplier_write),
):
    """Mở lại giao dịch với nhà cung cấp đã ngừng."""
    entity = _get_or_404(db, code)
    if entity.is_active:
        raise _bad_request("Nhà cung cấp này đang giao dịch bình thường.")

    entity.is_active = True
    entity.inactive_reason = None
    entity.deactivated_at = None
    entity.deactivated_by = None
    _commit(db)
    db.refresh(entity)
    return entity