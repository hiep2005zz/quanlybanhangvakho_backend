# backend/app/api/v1/endpoints/sales_targets.py
import json
import calendar
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import OrderEntity, DealerEntity, UserEntity
from app.models.sales_target import (
    SalesTargetEntity,
    SALES_TARGETS_DB,
    save_sales_targets_db
)
from app.api.v1.endpoints.auth import get_current_user, UserResponse
from app.api.v1.endpoints.orders import ORDERS_DB
from app.api.v1.endpoints.dealers import normalize_region_name
from app.models.dealer import DEALERS_DB
from app.models.user import USERS_DB

router = APIRouter()

# Thời gian chuẩn của hệ thống
CURRENT_MONTH_YEAR = "2026-10"


class SalesTargetAuditLog(BaseModel):
    modified_at: str
    modified_by_id: int
    modified_by_name: str
    old_target_amount: float
    new_target_amount: float
    reason: str


class SalesTargetCreate(BaseModel):
    target_type: str = Field(..., description="'salesperson' hoặc 'region'")
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    region: Optional[str] = None
    month_year: str = Field(..., description="Định dạng YYYY-MM, vd: '2026-10'")
    target_amount: float = Field(..., ge=0, description="Chỉ tiêu doanh số (VNĐ)")
    note: Optional[str] = None


class SalesTargetUpdate(BaseModel):
    target_amount: float = Field(..., ge=0, description="Chỉ tiêu doanh số mới (VNĐ)")
    note: Optional[str] = None
    reason: Optional[str] = Field(None, description="Bắt buộc khi sửa chỉ tiêu của kỳ đã qua (bị khóa)")


class SalesTargetItem(BaseModel):
    id: int
    target_type: str
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    region: Optional[str] = None
    month_year: str
    target_amount: float
    actual_amount: float
    progress_pct: float
    remaining_amount: float
    projected_amount: Optional[float] = None
    is_locked: bool
    note: Optional[str] = None
    created_by: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    audit_logs: List[SalesTargetAuditLog] = []


class SalesTargetSummary(BaseModel):
    month_year: str
    total_target_amount: float
    total_actual_amount: float
    average_progress_pct: float
    completed_count: int
    total_count: int


class SalesTargetListResponse(BaseModel):
    summary: SalesTargetSummary
    items: List[SalesTargetItem]
    can_manage: bool
    current_month_year: str = CURRENT_MONTH_YEAR


def check_sales_target_access(current_user: UserResponse) -> tuple[bool, bool]:
    """
    Trả về (can_access, can_manage).
    - can_access: admin, sales_manager, sales
    - can_manage: admin, sales_manager
    """
    roles = set(current_user.roles or [])
    if current_user.role:
        roles.add(current_user.role)

    allowed = {"admin", "sales_manager", "sales"}
    if not (roles & allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập module Chỉ tiêu doanh số."
        )

    can_manage = bool("admin" in roles or "sales_manager" in roles)
    return True, can_manage


def get_all_consolidated_orders(db: Session) -> List[Dict[str, Any]]:
    """Tổng hợp danh sách đơn hàng từ CSDL và ORDERS_DB."""
    orders_list = []
    seen_codes = set()

    # 1. Từ DB
    try:
        db_orders = db.query(OrderEntity).all()
        for o in db_orders:
            if o.order_code and o.order_code not in seen_codes:
                seen_codes.add(o.order_code)
                dt_str = None
                if o.created_at:
                    if isinstance(o.created_at, datetime):
                        dt_str = o.created_at.strftime("%Y-%m-%d")
                    elif isinstance(o.created_at, str):
                        dt_str = o.created_at[:10]

                orders_list.append({
                    "order_code": o.order_code,
                    "dealer_id": o.dealer_id,
                    "assigned_sale_id": o.assigned_sale_id,
                    "total_amount": float(o.total_amount or 0.0),
                    "status": (o.status or "PENDING").upper(),
                    "date_str": dt_str or "2026-10-01",
                })
    except Exception as ex:
        print(f"Warning read orders from db: {ex}")

    # 2. Từ ORDERS_DB
    for o_dict in ORDERS_DB.values():
        code = o_dict.get("order_code") or f"DH-{o_dict.get('id')}"
        if code not in seen_codes:
            seen_codes.add(code)
            c_val = o_dict.get("created_at")
            dt_str = None
            if isinstance(c_val, datetime):
                dt_str = c_val.strftime("%Y-%m-%d")
            elif isinstance(c_val, str):
                dt_str = c_val[:10]

            orders_list.append({
                "order_code": code,
                "dealer_id": o_dict.get("dealer_id"),
                "assigned_sale_id": o_dict.get("assigned_sale_id"),
                "total_amount": float(o_dict.get("total_amount") or 0.0),
                "status": (o_dict.get("status") or "PENDING").upper(),
                "date_str": dt_str or "2026-10-01",
            })

    return orders_list


def get_dealer_region_map(db: Session) -> Dict[int, str]:
    """Map dealer_id -> region."""
    d_map = {}
    try:
        db_dealers = db.query(DealerEntity).all()
        for d in db_dealers:
            raw_reg = getattr(d, "region", None)
            d_map[d.id] = normalize_region_name(raw_reg) if raw_reg else "Chưa phân vùng"
    except Exception:
        pass

    for d_id, d_data in DEALERS_DB.items():
        if d_id not in d_map:
            raw_reg = getattr(d_data, "region", None) if not isinstance(d_data, dict) else d_data.get("region")
            d_map[d_id] = normalize_region_name(raw_reg) if raw_reg else "Chưa phân vùng"

    return d_map


def calculate_actual_revenue(
    target_type: str,
    employee_id: Optional[int],
    region: Optional[str],
    month_year: str,
    orders: List[Dict[str, Any]],
    dealers_region_map: Dict[int, str],
) -> float:
    """Tính doanh số thực tế trong tháng của nhân viên hoặc khu vực."""
    total = 0.0
    for o in orders:
        if o["status"] == "CANCELLED":
            continue

        # Kiểm tra tháng của đơn hàng
        odate = o.get("date_str") or ""
        if not odate.startswith(month_year):
            continue

        amount = float(o.get("total_amount") or 0.0)

        if target_type == "salesperson":
            if employee_id and o.get("assigned_sale_id") == employee_id:
                total += amount
        elif target_type == "region":
            if region:
                target_reg = normalize_region_name(region).lower()
                d_id = o.get("dealer_id")
                d_region = dealers_region_map.get(d_id, "")
                norm_d_reg = normalize_region_name(d_region).lower()
                if norm_d_reg and (target_reg in norm_d_reg or norm_d_reg in target_reg):
                    total += amount

    return total


def calculate_projected(actual: float, month_year: str) -> Optional[float]:
    """Ước tính doanh số cuối tháng nếu đang ở tháng hiện tại."""
    if month_year != CURRENT_MONTH_YEAR:
        return None
    try:
        # Ngày hiện tại của hệ thống là 16
        day_of_month = 16
        year, month = map(int, month_year.split("-"))
        days_in_month = calendar.monthrange(year, month)[1]
        if day_of_month > 0:
            return round((actual / day_of_month) * days_in_month, 2)
    except Exception:
        pass
    return None


@router.get("", response_model=SalesTargetListResponse)
def get_sales_targets(
    month_year: Optional[str] = Query(None, description="Tháng cần xem (YYYY-MM), mặc định tháng hiện tại"),
    target_type: Optional[str] = Query("all", description="all | salesperson | region"),
    search: Optional[str] = Query(None, description="Tìm theo tên nhân viên hoặc khu vực"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Lấy danh sách chỉ tiêu doanh số và tiến độ thực tế theo thời gian thực.
    - Quản lý & Admin: Xem toàn bộ đội ngũ và khu vực.
    - Nhân viên Sale: Chỉ xem được chỉ tiêu của bản thân.
    """
    _, can_manage = check_sales_target_access(current_user)

    target_month = month_year or CURRENT_MONTH_YEAR
    orders = get_all_consolidated_orders(db)
    dealers_map = get_dealer_region_map(db)

    # Đọc danh sách từ DB và SALES_TARGETS_DB
    all_targets: Dict[int, Dict[str, Any]] = {}

    # 1. Từ DB
    try:
        db_items = db.query(SalesTargetEntity).filter(SalesTargetEntity.month_year == target_month).all()
        for item in db_items:
            all_targets[item.id] = item.to_dict()
    except Exception:
        pass

    # 2. Bổ sung từ bộ nhớ SALES_TARGETS_DB
    for t_id, t_dict in SALES_TARGETS_DB.items():
        if t_dict.get("month_year") == target_month:
            if t_id not in all_targets:
                all_targets[t_id] = t_dict

    # Nếu chưa có bản ghi nào cho tháng này, kiểm tra tất cả các tháng khác
    if not all_targets and month_year is None:
        for t_id, t_dict in SALES_TARGETS_DB.items():
            all_targets[t_id] = t_dict

    items: List[SalesTargetItem] = []
    total_target = 0.0
    total_actual = 0.0
    completed_count = 0

    for t_id, t_data in sorted(all_targets.items(), key=lambda x: x[0]):
        # Bộ lọc loại chỉ tiêu
        t_type = t_data.get("target_type", "salesperson")
        if target_type and target_type != "all" and t_type != target_type:
            continue

        emp_id = t_data.get("employee_id")
        emp_name = t_data.get("employee_name") or ""
        reg = t_data.get("region") or ""

        # Nhân viên kinh doanh thường chỉ xem của chính mình
        if not can_manage:
            if t_type == "salesperson" and emp_id != current_user.id:
                continue

        # Bộ lọc tìm kiếm
        if search:
            s_lower = search.lower()
            if s_lower not in emp_name.lower() and s_lower not in reg.lower():
                continue

        m_year = t_data.get("month_year", target_month)
        tgt_amount = float(t_data.get("target_amount") or 0.0)

        # Tính toán thực tế
        actual = calculate_actual_revenue(t_type, emp_id, reg, m_year, orders, dealers_map)
        pct = round((actual / tgt_amount) * 100, 2) if tgt_amount > 0 else 0.0
        remaining = max(0.0, tgt_amount - actual)
        proj = calculate_projected(actual, m_year)

        # Xác định khóa kỳ: tự động khóa nếu tháng đã qua
        is_past_month = m_year < CURRENT_MONTH_YEAR
        is_locked = bool(t_data.get("is_locked", False) or is_past_month)

        if pct >= 100.0:
            completed_count += 1

        total_target += tgt_amount
        total_actual += actual

        raw_logs = t_data.get("audit_logs") or []
        parsed_logs = []
        for lg in raw_logs:
            parsed_logs.append(SalesTargetAuditLog(
                modified_at=lg.get("modified_at") or "",
                modified_by_id=lg.get("modified_by_id", 0),
                modified_by_name=lg.get("modified_by_name") or "",
                old_target_amount=float(lg.get("old_target_amount") or 0.0),
                new_target_amount=float(lg.get("new_target_amount") or 0.0),
                reason=lg.get("reason") or "",
            ))

        items.append(SalesTargetItem(
            id=t_id,
            target_type=t_type,
            employee_id=emp_id,
            employee_name=emp_name,
            region=reg,
            month_year=m_year,
            target_amount=tgt_amount,
            actual_amount=actual,
            progress_pct=pct,
            remaining_amount=remaining,
            projected_amount=proj,
            is_locked=is_locked,
            note=t_data.get("note"),
            created_by=t_data.get("created_by", "admin"),
            created_at=t_data.get("created_at"),
            updated_at=t_data.get("updated_at"),
            audit_logs=parsed_logs,
        ))

    total_count = len(items)
    avg_progress = round((total_actual / total_target) * 100, 2) if total_target > 0 else 0.0

    summary = SalesTargetSummary(
        month_year=target_month,
        total_target_amount=total_target,
        total_actual_amount=total_actual,
        average_progress_pct=avg_progress,
        completed_count=completed_count,
        total_count=total_count,
    )

    return SalesTargetListResponse(
        summary=summary,
        items=items,
        can_manage=can_manage,
        current_month_year=CURRENT_MONTH_YEAR,
    )


@router.get("/metadata")
def get_sales_targets_metadata(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Đồng bộ dữ liệu thời gian thực từ các module khác:
    - Danh sách Nhân viên (từ quản lý người dùng CSDL).
    - Danh sách Khu vực (từ danh bạ đại lý CSDL).
    """
    check_sales_target_access(current_user)

    # 1. Danh sách nhân viên (sales, sales_manager, admin) từ bảng users
    salespersons = []
    try:
        users = db.query(UserEntity).filter(UserEntity.is_active == True).all()
        for u in users:
            roles = set(json.loads(u.roles_json) if u.roles_json else [])
            if u.role:
                roles.add(u.role)
            if roles & {"sales", "sales_manager", "admin"}:
                role_label = "Nhân viên kinh doanh" if "sales" in roles else ("Quản lý kinh doanh" if "sales_manager" in roles else "Admin")
                salespersons.append({
                    "id": u.id,
                    "username": u.username,
                    "full_name": u.full_name or u.username,
                    "role": u.role or "sales",
                    "role_label": role_label,
                })
    except Exception as ex:
        print(f"Error querying users for sales targets: {ex}")

    if not salespersons:
        salespersons = [
            {"id": 3, "username": "sales_user", "full_name": "Trần Bán Hàng", "role": "sales", "role_label": "Nhân viên kinh doanh"},
            {"id": 2, "username": "sales_manager", "full_name": "Trưởng Phòng Kinh Doanh", "role": "sales_manager", "role_label": "Quản lý kinh doanh"},
            {"id": 1, "username": "admin", "full_name": "Đào Ngọc Hiệp", "role": "admin", "role_label": "Admin"},
        ]

    # 2. Danh sách khu vực duy nhất đồng bộ trực tiếp từ danh bạ đại lý (dealers)
    regions_set = set()
    try:
        dealers = db.query(DealerEntity).all()
        for d in dealers:
            if d.region and d.region.strip():
                regions_set.add(normalize_region_name(d.region.strip()))
    except Exception:
        pass
    for d_data in DEALERS_DB.values():
        r = getattr(d_data, "region", None) if not isinstance(d_data, dict) else d_data.get("region")
        if r and r.strip():
            regions_set.add(normalize_region_name(r.strip()))

    # Fallback dự phòng chỉ khi danh bạ đại lý hoàn toàn chưa có khu vực nào
    if not regions_set:
        regions_set = set(["Hà Nội", "Hải Phòng", "TP. HCM", "Đà Nẵng"])

    return {
        "salespersons": sorted(salespersons, key=lambda x: x["id"]),
        "regions": sorted(list(regions_set)),
    }


@router.get("/my-progress")
def get_my_sales_progress(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy thông tin tiến độ cá nhân của nhân viên bán hàng trong tháng hiện tại."""
    check_sales_target_access(current_user)

    orders = get_all_consolidated_orders(db)
    dealers_map = get_dealer_region_map(db)

    # Tìm chỉ tiêu tháng này của user
    my_target = None
    try:
        db_tgt = (
            db.query(SalesTargetEntity)
            .filter(
                SalesTargetEntity.target_type == "salesperson",
                SalesTargetEntity.employee_id == current_user.id,
                SalesTargetEntity.month_year == CURRENT_MONTH_YEAR,
            )
            .first()
        )
        if db_tgt:
            my_target = db_tgt.to_dict()
    except Exception:
        pass

    if not my_target:
        for t_data in SALES_TARGETS_DB.values():
            if (
                t_data.get("target_type") == "salesperson"
                and t_data.get("employee_id") == current_user.id
                and t_data.get("month_year") == CURRENT_MONTH_YEAR
            ):
                my_target = t_data
                break

    target_amt = float(my_target.get("target_amount") or 0.0) if my_target else 50000000.0
    actual = calculate_actual_revenue("salesperson", current_user.id, None, CURRENT_MONTH_YEAR, orders, dealers_map)
    pct = round((actual / target_amt) * 100, 2) if target_amt > 0 else 0.0
    proj = calculate_projected(actual, CURRENT_MONTH_YEAR)

    return {
        "employee_id": current_user.id,
        "employee_name": current_user.full_name or current_user.username,
        "month_year": CURRENT_MONTH_YEAR,
        "target_amount": target_amt,
        "actual_amount": actual,
        "progress_pct": pct,
        "remaining_amount": max(0.0, target_amt - actual),
        "projected_amount": proj,
        "is_locked": False,
        "days_left": 15,
    }


@router.post("", response_model=SalesTargetItem)
def create_sales_target(
    payload: SalesTargetCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Thiết lập chỉ tiêu mới cho nhân viên hoặc khu vực."""
    _, can_manage = check_sales_target_access(current_user)
    if not can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản lý kinh doanh hoặc Admin mới có quyền thiết lập chỉ tiêu."
        )

    if payload.target_type not in ["salesperson", "region"]:
        raise HTTPException(status_code=400, detail="Loại chỉ tiêu phải là 'salesperson' hoặc 'region'.")

    emp_name = payload.employee_name
    if payload.target_type == "salesperson":
        if not payload.employee_id:
            raise HTTPException(status_code=400, detail="Vui lòng chọn nhân viên kinh doanh.")
        if not emp_name:
            # Tra cứu tên nhân viên
            u = db.query(UserEntity).filter(UserEntity.id == payload.employee_id).first()
            if u:
                emp_name = u.full_name or u.username
            elif payload.employee_id in USERS_DB:
                u_obj = USERS_DB[payload.employee_id]
                emp_name = (getattr(u_obj, "full_name", None) or getattr(u_obj, "username", None)) if not isinstance(u_obj, dict) else (u_obj.get("full_name") or u_obj.get("username"))
            else:
                emp_name = f"Nhân viên #{payload.employee_id}"
    elif payload.target_type == "region":
        if not payload.region or not payload.region.strip():
            raise HTTPException(status_code=400, detail="Vui lòng nhập tên khu vực.")
        payload.region = normalize_region_name(payload.region.strip())

    # Kiểm tra trùng lặp
    for t in SALES_TARGETS_DB.values():
        if t.get("month_year") == payload.month_year and t.get("target_type") == payload.target_type:
            if payload.target_type == "salesperson" and t.get("employee_id") == payload.employee_id:
                raise HTTPException(status_code=400, detail=f"Chỉ tiêu tháng {payload.month_year} cho nhân viên này đã tồn tại.")
            if payload.target_type == "region" and (t.get("region") or "").strip().lower() == payload.region.strip().lower():
                raise HTTPException(status_code=400, detail=f"Chỉ tiêu tháng {payload.month_year} cho khu vực này đã tồn tại.")

    new_id = max(SALES_TARGETS_DB.keys(), default=0) + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    is_locked = payload.month_year < CURRENT_MONTH_YEAR

    new_target_dict = {
        "id": new_id,
        "target_type": payload.target_type,
        "employee_id": payload.employee_id,
        "employee_name": emp_name,
        "region": payload.region,
        "month_year": payload.month_year,
        "target_amount": float(payload.target_amount),
        "is_locked": is_locked,
        "note": payload.note,
        "created_by": current_user.username,
        "created_at": now_iso,
        "updated_at": now_iso,
        "audit_logs": [],
    }

    # Lưu vào bộ nhớ và file
    SALES_TARGETS_DB[new_id] = new_target_dict
    save_sales_targets_db(SALES_TARGETS_DB)

    # Lưu vào DB nếu có kết nối
    try:
        db_entity = SalesTargetEntity(
            target_type=payload.target_type,
            employee_id=payload.employee_id,
            employee_name=emp_name,
            region=payload.region,
            month_year=payload.month_year,
            target_amount=float(payload.target_amount),
            is_locked=is_locked,
            note=payload.note,
            created_by=current_user.username,
        )
        db.add(db_entity)
        db.commit()
    except Exception as ex:
        print(f"Warning saving sales target to db: {ex}")

    orders = get_all_consolidated_orders(db)
    dealers_map = get_dealer_region_map(db)
    actual = calculate_actual_revenue(payload.target_type, payload.employee_id, payload.region, payload.month_year, orders, dealers_map)
    pct = round((actual / payload.target_amount) * 100, 2) if payload.target_amount > 0 else 0.0

    return SalesTargetItem(
        id=new_id,
        target_type=payload.target_type,
        employee_id=payload.employee_id,
        employee_name=emp_name,
        region=payload.region,
        month_year=payload.month_year,
        target_amount=float(payload.target_amount),
        actual_amount=actual,
        progress_pct=pct,
        remaining_amount=max(0.0, float(payload.target_amount) - actual),
        projected_amount=calculate_projected(actual, payload.month_year),
        is_locked=is_locked,
        note=payload.note,
        created_by=current_user.username,
        created_at=now_iso,
        updated_at=now_iso,
        audit_logs=[],
    )


@router.put("/{target_id}", response_model=SalesTargetItem)
def update_sales_target(
    target_id: int,
    payload: SalesTargetUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Cập nhật chỉ tiêu doanh số.
    TIÊU CHÍ BẮT BUỘC: Nếu chỉ tiêu của kỳ đã qua (tháng < tháng hiện tại hoặc is_locked == True),
    bắt buộc phải có lý do điều chỉnh để ghi nhật ký audit log.
    """
    _, can_manage = check_sales_target_access(current_user)
    if not can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản lý kinh doanh hoặc Admin mới có quyền điều chỉnh chỉ tiêu."
        )

    target_data = SALES_TARGETS_DB.get(target_id)
    if not target_data:
        raise HTTPException(status_code=404, detail="Không tìm thấy chỉ tiêu cần điều chỉnh.")

    m_year = target_data.get("month_year", CURRENT_MONTH_YEAR)
    is_locked = bool(target_data.get("is_locked") or m_year < CURRENT_MONTH_YEAR)

    old_amount = float(target_data.get("target_amount") or 0.0)
    new_amount = float(payload.target_amount)

    # Kiểm tra lý do khi chỉ tiêu đã bị khóa
    if is_locked:
        if not payload.reason or len(payload.reason.strip()) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Chỉ tiêu của kỳ đã qua đã bị khoá. Sửa chỉ tiêu bắt buộc phải có lý do điều chỉnh để ghi nhật ký."
            )

        # Ghi nhật ký audit log
        audit_entry = {
            "modified_at": datetime.now(timezone.utc).isoformat(),
            "modified_by_id": current_user.id,
            "modified_by_name": current_user.full_name or current_user.username,
            "old_target_amount": old_amount,
            "new_target_amount": new_amount,
            "reason": payload.reason.strip(),
        }
        if "audit_logs" not in target_data:
            target_data["audit_logs"] = []
        target_data["audit_logs"].append(audit_entry)

    target_data["target_amount"] = new_amount
    if payload.note is not None:
        target_data["note"] = payload.note
    target_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    SALES_TARGETS_DB[target_id] = target_data
    save_sales_targets_db(SALES_TARGETS_DB)

    # Cập nhật DB
    try:
        db_entity = db.query(SalesTargetEntity).filter(SalesTargetEntity.id == target_id).first()
        if db_entity:
            db_entity.target_amount = new_amount
            db_entity.note = target_data.get("note")
            db_entity.audit_logs = target_data.get("audit_logs")
            db.commit()
    except Exception as ex:
        print(f"Warning update db sales target: {ex}")

    orders = get_all_consolidated_orders(db)
    dealers_map = get_dealer_region_map(db)
    actual = calculate_actual_revenue(
        target_data.get("target_type", "salesperson"),
        target_data.get("employee_id"),
        target_data.get("region"),
        m_year,
        orders,
        dealers_map
    )
    pct = round((actual / new_amount) * 100, 2) if new_amount > 0 else 0.0

    raw_logs = target_data.get("audit_logs") or []
    parsed_logs = [
        SalesTargetAuditLog(
            modified_at=lg.get("modified_at") or "",
            modified_by_id=lg.get("modified_by_id", 0),
            modified_by_name=lg.get("modified_by_name") or "",
            old_target_amount=float(lg.get("old_target_amount") or 0.0),
            new_target_amount=float(lg.get("new_target_amount") or 0.0),
            reason=lg.get("reason") or "",
        )
        for lg in raw_logs
    ]

    return SalesTargetItem(
        id=target_id,
        target_type=target_data.get("target_type", "salesperson"),
        employee_id=target_data.get("employee_id"),
        employee_name=target_data.get("employee_name"),
        region=target_data.get("region"),
        month_year=m_year,
        target_amount=new_amount,
        actual_amount=actual,
        progress_pct=pct,
        remaining_amount=max(0.0, new_amount - actual),
        projected_amount=calculate_projected(actual, m_year),
        is_locked=is_locked,
        note=target_data.get("note"),
        created_by=target_data.get("created_by", "admin"),
        created_at=target_data.get("created_at"),
        updated_at=target_data.get("updated_at"),
        audit_logs=parsed_logs,
    )


@router.delete("/{target_id}")
def delete_sales_target(
    target_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Xóa chỉ tiêu doanh số (chỉ áp dụng cho kỳ hiện tại chưa bị khóa)."""
    _, can_manage = check_sales_target_access(current_user)
    if not can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản lý kinh doanh hoặc Admin mới có quyền xóa chỉ tiêu."
        )

    target_data = SALES_TARGETS_DB.get(target_id)
    if not target_data:
        raise HTTPException(status_code=404, detail="Không tìm thấy chỉ tiêu cần xóa.")

    m_year = target_data.get("month_year", CURRENT_MONTH_YEAR)
    is_locked = bool(target_data.get("is_locked") or m_year < CURRENT_MONTH_YEAR)

    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể xóa chỉ tiêu của kỳ đã qua bị khoá. Vui lòng giữ nguyên lịch sử."
        )

    del SALES_TARGETS_DB[target_id]
    save_sales_targets_db(SALES_TARGETS_DB)

    try:
        db_entity = db.query(SalesTargetEntity).filter(SalesTargetEntity.id == target_id).first()
        if db_entity:
            db.delete(db_entity)
            db.commit()
    except Exception:
        pass

    return {"message": "Đã xóa chỉ tiêu thành công.", "id": target_id}
