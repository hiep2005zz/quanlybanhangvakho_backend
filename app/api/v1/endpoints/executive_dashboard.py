from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.entities import OrderEntity, DealerEntity, ProductEntity, WarehouseStockEntity
from app.api.v1.endpoints.auth import get_current_user, UserResponse
from app.api.v1.endpoints.orders import ORDERS_DB
from app.models.dealer import DEALERS_DB, load_dealers_db
from app.models.user import USERS_DB, load_users_db

router = APIRouter()


class KpiMetrics(BaseModel):
    revenue_today: float
    revenue_today_growth: float
    revenue_this_month: float
    revenue_month_growth: float
    pending_approval_orders_count: int
    pending_export_orders_count: int
    total_overdue_debt: float
    low_stock_skus_count: int


class DailyRevenueItem(BaseModel):
    date: str
    day: int
    revenue: float
    order_count: int


class RegionRevenueItem(BaseModel):
    region: str
    revenue: float
    percentage: float
    order_count: int


class OverdueDealerItem(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    phone: Optional[str] = None
    region: Optional[str] = None
    assigned_sale_name: Optional[str] = None
    credit_limit: float
    current_debt: float
    total_overdue: float
    max_overdue_days: int


class ExecutiveDashboardResponse(BaseModel):
    kpis: KpiMetrics
    daily_revenue: List[DailyRevenueItem]
    revenue_by_region: List[RegionRevenueItem]
    top_overdue_dealers: List[OverdueDealerItem]
    updated_at: str


def check_executive_access(current_user: UserResponse):
    user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
    if not any(r in ["admin", "sales_manager"] for r in user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị hệ thống và Quản lý kinh doanh mới có quyền truy cập Dashboard điều hành.",
        )


@router.get("/executive-metrics", response_model=ExecutiveDashboardResponse)
def get_executive_metrics(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Dashboard điều hành bán hàng và kho dành cho Quản lý kinh doanh và Admin.
    Tổng hợp toàn bộ chỉ số kinh doanh, tồn kho, công nợ, xu hướng doanh số trong 1 lần gọi (< 2 giây).
    """
    check_executive_access(current_user)

    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 1:
        last_month_start = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
        last_month_end = month_start
    else:
        last_month_start = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)
        last_month_end = month_start

    # 1. Thu thập đơn hàng hợp nhất từ DB và ORDERS_DB
    all_orders = []
    seen_codes = set()

    try:
        db_orders = db.query(OrderEntity).all()
        for o in db_orders:
            if o.order_code not in seen_codes:
                seen_codes.add(o.order_code)
                all_orders.append({
                    "id": o.id,
                    "order_code": o.order_code,
                    "dealer_id": o.dealer_id,
                    "dealer_name": o.dealer_name,
                    "total_amount": float(o.total_amount or 0.0),
                    "status": (o.status or "PENDING").upper(),
                    "requires_approval": getattr(o, "requires_approval", False),
                    "approval_status": getattr(o, "approval_status", "NORMAL"),
                    "created_at": o.created_at if isinstance(o.created_at, datetime) else (datetime.fromisoformat(str(o.created_at).replace("Z", "+00:00")) if o.created_at else now),
                })
    except Exception as ex:
        print(f"Warning reading db_orders: {ex}")

    for o_dict in ORDERS_DB.values():
        code = o_dict.get("order_code")
        if code and code not in seen_codes:
            seen_codes.add(code)
            created_val = o_dict.get("created_at")
            if isinstance(created_val, datetime):
                dt = created_val
            elif isinstance(created_val, str):
                try:
                    dt = datetime.fromisoformat(created_val.replace("Z", "+00:00"))
                except Exception:
                    dt = now
            else:
                dt = now
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)

            all_orders.append({
                "id": o_dict.get("id"),
                "order_code": code,
                "dealer_id": o_dict.get("dealer_id"),
                "dealer_name": o_dict.get("dealer_name", ""),
                "total_amount": float(o_dict.get("total_amount") or 0.0),
                "status": str(o_dict.get("status", "PENDING")).upper(),
                "requires_approval": o_dict.get("requires_approval", False),
                "approval_status": o_dict.get("approval_status", "NORMAL"),
                "created_at": dt,
            })

    # Lấy danh sách đại lý (vùng miền, hạn mức, nhân viên phụ trách)
    dealers_map: Dict[int, Any] = {}
    try:
        for d in db.query(DealerEntity).all():
            dealers_map[d.id] = d
    except Exception:
        pass
    for did, d_data in DEALERS_DB.items():
        if did not in dealers_map:
            dealers_map[did] = d_data

    # Map tên nhân viên kinh doanh
    sales_map: Dict[int, str] = {}
    try:
        from app.models.entities import UserEntity
        for u in db.query(UserEntity).all():
            sales_map[u.id] = u.full_name or u.username
    except Exception:
        pass
    for u in USERS_DB.values():
        uid = getattr(u, "id", None)
        if uid and uid not in sales_map:
            sales_map[uid] = getattr(u, "full_name", getattr(u, "username", "Sales"))

    # 2. Tính toán Doanh số & Đơn hàng
    revenue_today = 0.0
    revenue_yesterday = 0.0
    revenue_this_month = 0.0
    revenue_last_month = 0.0

    pending_approval_count = 0
    pending_export_count = 0

    # Phân bổ theo ngày trong tháng hiện tại (ngày 1 đến ngày cuối của tháng)
    import calendar
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    daily_stats: Dict[int, Dict[str, Any]] = {
        d: {"revenue": 0.0, "order_count": 0} for d in range(1, days_in_month + 1)
    }

    # Phân bổ theo vùng miền
    region_stats: Dict[str, Dict[str, Any]] = {}

    for o in all_orders:
        status_code = o["status"]
        amt = o["total_amount"]
        dt: datetime = o["created_at"]
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)

        # Đơn chờ duyệt
        is_pending = (
            status_code in ["PENDING", "PENDING_APPROVAL"]
            or o.get("approval_status") in ["PENDING", "PENDING_APPROVAL"]
            or (o.get("requires_approval") and status_code not in ["APPROVED", "CONFIRMED", "EXPORTED", "DELIVERED", "PAID", "CANCELLED", "REJECTED"])
        )
        if is_pending and status_code not in ["CANCELLED", "REJECTED"]:
            pending_approval_count += 1

        # Đơn chờ xuất kho
        if status_code in ["APPROVED", "CONFIRMED", "PICKING"]:
            pending_export_count += 1

        # Doanh số hợp lệ (các đơn không bị hủy)
        if status_code not in ["CANCELLED", "REJECTED"]:
            # Doanh số hôm nay
            if dt >= today_start:
                revenue_today += amt
            elif yesterday_start <= dt < today_start:
                revenue_yesterday += amt

            # Doanh số tháng này
            if dt >= month_start:
                revenue_this_month += amt
                if 1 <= dt.day <= days_in_month:
                    daily_stats[dt.day]["revenue"] += amt
                    daily_stats[dt.day]["order_count"] += 1

                # Khu vực đại lý
                d_obj = dealers_map.get(o["dealer_id"])
                reg = getattr(d_obj, "region", None) if d_obj else (d_obj.get("region") if isinstance(d_obj, dict) else None)
                region_name = reg or "Khác"
                if region_name not in region_stats:
                    region_stats[region_name] = {"revenue": 0.0, "order_count": 0}
                region_stats[region_name]["revenue"] += amt
                region_stats[region_name]["order_count"] += 1

            # Doanh số tháng trước
            if last_month_start <= dt < last_month_end:
                revenue_last_month += amt

    # Tỷ lệ tăng trưởng doanh số
    today_growth = round(((revenue_today - revenue_yesterday) / revenue_yesterday * 100), 1) if revenue_yesterday > 0 else (100.0 if revenue_today > 0 else 0.0)
    month_growth = round(((revenue_this_month - revenue_last_month) / revenue_last_month * 100), 1) if revenue_last_month > 0 else (100.0 if revenue_this_month > 0 else 0.0)

    # 3. Tính toán công nợ quá hạn và Top đại lý quá hạn
    dealer_overdue_list = []
    total_system_overdue_debt = 0.0

    for d_id, d in dealers_map.items():
        d_code = getattr(d, "code", None) or (d.get("code") if isinstance(d, dict) else f"DL{d_id:03d}")
        d_name = getattr(d, "name", None) or (d.get("name") if isinstance(d, dict) else f"Đại lý {d_id}")
        d_phone = getattr(d, "phone", None) or (d.get("phone") if isinstance(d, dict) else None)
        d_region = getattr(d, "region", None) or (d.get("region") if isinstance(d, dict) else None)
        d_sale_id = getattr(d, "assigned_sale_id", None) or (d.get("assigned_sale_id") if isinstance(d, dict) else None)
        d_limit = float(getattr(d, "credit_limit", 50000000.0) or (d.get("credit_limit", 50000000.0) if isinstance(d, dict) else 50000000.0))
        allowed_days = int(getattr(d, "overdue_days_allowed", getattr(d, "max_debt_days", 30)) or 30)

        sale_name = sales_map.get(d_sale_id, "Chưa phân công")

        dealer_orders = [o for o in all_orders if o["dealer_id"] == d_id and o["status"] not in ("PAID", "CANCELLED", "REJECTED")]
        dealer_debt = 0.0
        dealer_overdue = 0.0
        max_overdue_days = 0

        for o in dealer_orders:
            amt = o["total_amount"]
            dealer_debt += amt
            dt: datetime = o["created_at"]
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            days_old = max(0, (now - dt).days)
            if days_old > allowed_days:
                overdue_days = days_old - allowed_days
                dealer_overdue += amt
                if overdue_days > max_overdue_days:
                    max_overdue_days = overdue_days

        if dealer_overdue > 0:
            total_system_overdue_debt += dealer_overdue
            dealer_overdue_list.append(OverdueDealerItem(
                dealer_id=d_id,
                dealer_code=d_code,
                dealer_name=d_name,
                phone=d_phone,
                region=d_region,
                assigned_sale_name=sale_name,
                credit_limit=d_limit,
                current_debt=dealer_debt,
                total_overdue=dealer_overdue,
                max_overdue_days=max_overdue_days,
            ))

    # Xếp theo số tiền quá hạn giảm dần (Top 5)
    dealer_overdue_list.sort(key=lambda x: (x.total_overdue, x.max_overdue_days), reverse=True)
    top_overdue_dealers = dealer_overdue_list[:5]

    # 4. Tính toán số lượng SKU dưới định mức tồn
    low_stock_count = 0
    try:
        effective_min = func.coalesce(WarehouseStockEntity.min_stock, ProductEntity.min_stock, 10)
        res = db.query(WarehouseStockEntity).filter(WarehouseStockEntity.actual_stock <= effective_min).count()
        low_stock_count = res
    except Exception:
        # Fallback tính theo ProductEntity
        try:
            low_stock_count = db.query(ProductEntity).filter(ProductEntity.stock <= func.coalesce(ProductEntity.min_stock, 10)).count()
        except Exception:
            low_stock_count = 0

    # 5. Định dạng dữ liệu biểu đồ
    daily_revenue_list: List[DailyRevenueItem] = []
    for day in range(1, days_in_month + 1):
        d_str = f"{now.year}-{now.month:02d}-{day:02d}"
        daily_revenue_list.append(DailyRevenueItem(
            date=d_str,
            day=day,
            revenue=daily_stats[day]["revenue"],
            order_count=daily_stats[day]["order_count"],
        ))

    region_revenue_list: List[RegionRevenueItem] = []
    # Đảm bảo luôn có các miền chính dù chưa có đơn để biểu đồ cân đối
    default_regions = ["Miền Bắc", "Miền Trung", "Miền Nam"]
    for r in default_regions:
        if r not in region_stats:
            region_stats[r] = {"revenue": 0.0, "order_count": 0}

    total_region_rev = sum(v["revenue"] for v in region_stats.values())
    for r_name, r_data in region_stats.items():
        pct = round((r_data["revenue"] / total_region_rev * 100), 1) if total_region_rev > 0 else 0.0
        region_revenue_list.append(RegionRevenueItem(
            region=r_name,
            revenue=r_data["revenue"],
            percentage=pct,
            order_count=r_data["order_count"],
        ))
    region_revenue_list.sort(key=lambda x: x.revenue, reverse=True)

    return ExecutiveDashboardResponse(
        kpis=KpiMetrics(
            revenue_today=revenue_today,
            revenue_today_growth=today_growth,
            revenue_this_month=revenue_this_month,
            revenue_month_growth=month_growth,
            pending_approval_orders_count=pending_approval_count,
            pending_export_orders_count=pending_export_count,
            total_overdue_debt=total_system_overdue_debt,
            low_stock_skus_count=low_stock_count,
        ),
        daily_revenue=daily_revenue_list,
        revenue_by_region=region_revenue_list,
        top_overdue_dealers=top_overdue_dealers,
        updated_at=now.strftime("%H:%M:%S - %d/%m/%Y"),
    )
