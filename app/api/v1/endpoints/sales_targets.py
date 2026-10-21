# backend/app/api/v1/endpoints/sales_targets.py
from __future__ import annotations
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
                    "delivery_point_id": getattr(o, "delivery_point_id", None),
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
                "delivery_point_id": o_dict.get("delivery_point_id"),
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


def get_dealers_assigned_sales_map(db: Session) -> Dict[int, int]:
    """Map dealer_id -> assigned_sale_id (nhân viên phụ trách đại lý)."""
    s_map: Dict[int, int] = {}
    try:
        db_dealers = db.query(DealerEntity).all()
        for d in db_dealers:
            sale_id = getattr(d, "assigned_sale_id", None) or getattr(d, "assigned_salesperson_id", None)
            if sale_id:
                s_map[d.id] = sale_id
    except Exception:
        pass

    for d_id, d_data in DEALERS_DB.items():
        if d_id not in s_map:
            sale_id = getattr(d_data, "assigned_sale_id", None) if not isinstance(d_data, dict) else (d_data.get("assigned_sale_id") or d_data.get("assigned_salesperson_id"))
            if sale_id:
                s_map[d_id] = sale_id

    return s_map


def get_delivery_point_region_map(db: Session) -> Dict[int, str]:
    """Map delivery_point_id -> normalized region name from delivery point address."""
    dp_map: Dict[int, str] = {}
    try:
        from app.models.entities import DealerDeliveryPointEntity
        dps = db.query(DealerDeliveryPointEntity).filter(DealerDeliveryPointEntity.is_active == True).all()
        for dp in dps:
            addr = dp.address or ""
            for r in ["Hà Nội", "Hải Phòng", "Đà Nẵng", "TP. HCM", "TP.HCM", "Cần Thơ", "Bình Dương", "Đồng Nai", "Nghệ An"]:
                if r.lower() in addr.lower():
                    dp_map[dp.id] = normalize_region_name(r)
                    break
    except Exception:
        pass
    return dp_map


def get_month_return_orders(db: Session, month_year: str) -> List[Dict[str, Any]]:
    """Tổng hợp đơn trả hàng hợp lệ phát sinh trong tháng để khấu trừ KPI."""
    ret_list = []
    try:
        from app.models.return_order import ReturnOrderEntity
        db_returns = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.status != "CANCELLED").all()
        for ro in db_returns:
            ro_date = ro.confirmed_at or ro.created_at
            dt_str = None
            if isinstance(ro_date, datetime):
                dt_str = ro_date.strftime("%Y-%m-%d")
            elif isinstance(ro_date, str):
                dt_str = ro_date[:10]

            if dt_str and dt_str.startswith(month_year):
                total_refund = 0.0
                if hasattr(ro, "items") and ro.items:
                    for it in ro.items:
                        r_qty = float(getattr(it, "return_quantity", 0) or 0)
                        r_uprice = float(getattr(it, "unit_price", 0.0) or 0.0)
                        r_amount = float(getattr(it, "refund_amount", None) or (r_qty * r_uprice))
                        total_refund += r_amount
                elif hasattr(ro, "total_refund_amount") and ro.total_refund_amount:
                    total_refund = float(ro.total_refund_amount)

                ret_list.append({
                    "return_code": ro.return_code,
                    "order_code": ro.order_code,
                    "dealer_id": ro.dealer_id,
                    "refund_amount": total_refund,
                    "date_str": dt_str,
                })
    except Exception as ex:
        print(f"Warning query returns for sales targets: {ex}")
    return ret_list


def calculate_actual_revenue(
    target_type: str,
    employee_id: Optional[int],
    region: Optional[str],
    month_year: str,
    orders: List[Dict[str, Any]],
    dealers_region_map: Dict[int, str],
    dealers_sale_map: Optional[Dict[int, int]] = None,
    dp_region_map: Optional[Dict[int, str]] = None,
    returns: Optional[List[Dict[str, Any]]] = None,
) -> float:
    """Tính doanh số thực tế thuần (Net Revenue) trong tháng của nhân viên hoặc khu vực."""
    total = 0.0
    orders_by_code: Dict[str, Dict[str, Any]] = {}
    dealers_sale_map = dealers_sale_map or {}
    dp_region_map = dp_region_map or {}
    returns = returns or []

    for o in orders:
        if o["status"] == "CANCELLED":
            continue

        # Kiểm tra tháng của đơn hàng
        odate = o.get("date_str") or ""
        if not odate.startswith(month_year):
            continue

        orders_by_code[o["order_code"]] = o
        amount = float(o.get("total_amount") or 0.0)

        # 1. Nhân viên phụ trách: ưu tiên của đơn hàng, fallback sang nhân viên phụ trách đại lý
        d_id = o.get("dealer_id")
        assigned_sale = o.get("assigned_sale_id") or dealers_sale_map.get(d_id)

        # 2. Khu vực: ưu tiên theo điểm giao hàng thực tế, fallback sang khu vực đăng ký đại lý
        dp_id = o.get("delivery_point_id")
        d_region = dp_region_map.get(dp_id) or dealers_region_map.get(d_id, "")

        if target_type == "salesperson":
            if employee_id and assigned_sale == employee_id:
                total += amount
        elif target_type == "region":
            if region:
                target_reg = normalize_region_name(region).lower()
                norm_d_reg = normalize_region_name(d_region).lower()
                if norm_d_reg and (target_reg in norm_d_reg or norm_d_reg in target_reg):
                    total += amount

    # 3. Khấu trừ hàng trả lại & hoàn tiền (RMA / Returns)
    for ro in returns:
        r_amount = float(ro.get("refund_amount") or 0.0)
        orig_order = orders_by_code.get(ro.get("order_code"))

        assigned_sale = None
        d_region = ""
        if orig_order:
            d_id = orig_order.get("dealer_id")
            assigned_sale = orig_order.get("assigned_sale_id") or dealers_sale_map.get(d_id)
            dp_id = orig_order.get("delivery_point_id")
            d_region = dp_region_map.get(dp_id) or dealers_region_map.get(d_id, "")
        else:
            d_id = ro.get("dealer_id")
            assigned_sale = dealers_sale_map.get(d_id)
            d_region = dealers_region_map.get(d_id, "")

        if target_type == "salesperson":
            if employee_id and assigned_sale == employee_id:
                total -= r_amount
        elif target_type == "region":
            if region:
                target_reg = normalize_region_name(region).lower()
                norm_d_reg = normalize_region_name(d_region).lower()
                if norm_d_reg and (target_reg in norm_d_reg or norm_d_reg in target_reg):
                    total -= r_amount

    return max(0.0, total)


def calculate_projected(actual: float, month_year: str) -> Optional[float]:
    """Ước tính doanh số cuối tháng nếu đang ở tháng hiện tại theo ngày thực tế."""
    if month_year != CURRENT_MONTH_YEAR:
        return None
    try:
        now = datetime.now(timezone.utc)
        day_of_month = now.day
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
    dealers_sale_map = get_dealers_assigned_sales_map(db)
    dp_region_map = get_delivery_point_region_map(db)
    returns = get_month_return_orders(db, target_month)

    # Đọc danh sách từ DB và SALES_TARGETS_DB
    all_targets: Dict[int, Dict[str, Any]] = {}

    # 1. Từ DB
    try:
        db_items = db.query(SalesTargetEntity).filter(SalesTargetEntity.month_year == target_month).all()
        for item in db_items:
            item_dict = item.to_dict()
            all_targets[item.id] = item_dict
            SALES_TARGETS_DB[item.id] = item_dict
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
        actual = calculate_actual_revenue(t_type, emp_id, reg, m_year, orders, dealers_map, dealers_sale_map, dp_region_map, returns)
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

    # 1. Danh sách nhân viên (chỉ lấy sales, sales_manager, bỏ qua admin) từ bảng users
    salespersons = []
    try:
        users = db.query(UserEntity).filter(UserEntity.is_active == True).all()
        for u in users:
            # Loại bỏ tài khoản admin
            if (u.username or "").lower() == "admin" or (u.role or "").lower() == "admin":
                continue

            roles = set(json.loads(u.roles_json) if u.roles_json else [])
            if u.role:
                roles.add(u.role)
            
            # Chỉ lấy tài khoản có role kinh doanh và không phải admin
            if (roles & {"sales", "sales_manager"}) and "admin" not in roles:
                role_label = "Nhân viên kinh doanh" if "sales" in roles else "Quản lý kinh doanh"
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
    dealers_sale_map = get_dealers_assigned_sales_map(db)
    dp_region_map = get_delivery_point_region_map(db)
    returns = get_month_return_orders(db, CURRENT_MONTH_YEAR)

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
    actual = calculate_actual_revenue("salesperson", current_user.id, None, CURRENT_MONTH_YEAR, orders, dealers_map, dealers_sale_map, dp_region_map, returns)
    pct = round((actual / target_amt) * 100, 2) if target_amt > 0 else 0.0
    proj = calculate_projected(actual, CURRENT_MONTH_YEAR)

    now = datetime.now(timezone.utc)
    day_of_month = now.day
    year, month = map(int, CURRENT_MONTH_YEAR.split("-"))
    days_in_month = calendar.monthrange(year, month)[1]
    days_left = max(0, days_in_month - day_of_month)

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
        "days_left": days_left,
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

        # Kiểm tra không cho phép đặt chỉ tiêu cho tài khoản Admin
        target_u = db.query(UserEntity).filter(UserEntity.id == payload.employee_id).first()
        is_target_admin = False
        if target_u:
            u_roles = set(json.loads(target_u.roles_json) if target_u.roles_json else [])
            if (target_u.username or "").lower() == "admin" or (target_u.role or "").lower() == "admin" or "admin" in u_roles:
                is_target_admin = True
        elif payload.employee_id == 1:
            is_target_admin = True
        elif payload.employee_id in USERS_DB:
            u_obj = USERS_DB[payload.employee_id]
            u_r = getattr(u_obj, "role", None) if not isinstance(u_obj, dict) else u_obj.get("role")
            u_uname = getattr(u_obj, "username", None) if not isinstance(u_obj, dict) else u_obj.get("username")
            if (u_uname or "").lower() == "admin" or (u_r or "").lower() == "admin":
                is_target_admin = True

        if is_target_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Không thể thiết lập chỉ tiêu doanh số cho tài khoản Admin."
            )

        if not emp_name:
            # Tra cứu tên nhân viên
            u = target_u
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

    # Kiểm tra trùng lặp trong DB
    try:
        db_dups = db.query(SalesTargetEntity).filter(
            SalesTargetEntity.month_year == payload.month_year,
            SalesTargetEntity.target_type == payload.target_type
        ).all()
        for t in db_dups:
            if payload.target_type == "salesperson" and t.employee_id == payload.employee_id:
                raise HTTPException(status_code=400, detail=f"Chỉ tiêu tháng {payload.month_year} cho nhân viên này đã tồn tại.")
            if payload.target_type == "region" and (t.region or "").strip().lower() == payload.region.strip().lower():
                raise HTTPException(status_code=400, detail=f"Chỉ tiêu tháng {payload.month_year} cho khu vực này đã tồn tại.")
    except HTTPException:
        raise
    except Exception:
        pass

    # Kiểm tra trùng lặp trong SALES_TARGETS_DB
    for t in SALES_TARGETS_DB.values():
        if t.get("month_year") == payload.month_year and t.get("target_type") == payload.target_type:
            if payload.target_type == "salesperson" and t.get("employee_id") == payload.employee_id:
                raise HTTPException(status_code=400, detail=f"Chỉ tiêu tháng {payload.month_year} cho nhân viên này đã tồn tại.")
            if payload.target_type == "region" and (t.get("region") or "").strip().lower() == payload.region.strip().lower():
                raise HTTPException(status_code=400, detail=f"Chỉ tiêu tháng {payload.month_year} cho khu vực này đã tồn tại.")

    now_iso = datetime.now(timezone.utc).isoformat()
    is_locked = payload.month_year < CURRENT_MONTH_YEAR

    # Lưu vào DB nếu có kết nối
    new_id = None
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
        db.refresh(db_entity)
        new_id = db_entity.id
    except Exception as ex:
        print(f"Warning saving sales target to db: {ex}")

    if not new_id:
        new_id = max(SALES_TARGETS_DB.keys(), default=0) + 1

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

    orders = get_all_consolidated_orders(db)
    dealers_map = get_dealer_region_map(db)
    dealers_sale_map = get_dealers_assigned_sales_map(db)
    dp_region_map = get_delivery_point_region_map(db)
    returns = get_month_return_orders(db, payload.month_year)
    actual = calculate_actual_revenue(payload.target_type, payload.employee_id, payload.region, payload.month_year, orders, dealers_map, dealers_sale_map, dp_region_map, returns)
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

    db_entity = None
    try:
        db_entity = db.query(SalesTargetEntity).filter(SalesTargetEntity.id == target_id).first()
    except Exception as ex:
        print(f"Warning querying sales target in db: {ex}")

    if db_entity:
        target_data = db_entity.to_dict()
    else:
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
    if db_entity:
        try:
            db_entity.target_amount = new_amount
            db_entity.note = target_data.get("note")
            db_entity.audit_logs = target_data.get("audit_logs")
            db.commit()
        except Exception as ex:
            print(f"Warning update db sales target: {ex}")

    orders = get_all_consolidated_orders(db)
    dealers_map = get_dealer_region_map(db)
    dealers_sale_map = get_dealers_assigned_sales_map(db)
    dp_region_map = get_delivery_point_region_map(db)
    returns = get_month_return_orders(db, m_year)
    actual = calculate_actual_revenue(
        target_data.get("target_type", "salesperson"),
        target_data.get("employee_id"),
        target_data.get("region"),
        m_year,
        orders,
        dealers_map,
        dealers_sale_map,
        dp_region_map,
        returns
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

    db_entity = None
    try:
        db_entity = db.query(SalesTargetEntity).filter(SalesTargetEntity.id == target_id).first()
    except Exception as ex:
        print(f"Warning querying sales target in db: {ex}")

    if db_entity:
        target_data = db_entity.to_dict()
    else:
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

    if target_id in SALES_TARGETS_DB:
        del SALES_TARGETS_DB[target_id]
        save_sales_targets_db(SALES_TARGETS_DB)

    if db_entity:
        try:
            db.delete(db_entity)
            db.commit()
        except Exception as ex:
            print(f"Warning deleting sales target from db: {ex}")

    return {"message": "Đã xóa chỉ tiêu thành công.", "id": target_id}
