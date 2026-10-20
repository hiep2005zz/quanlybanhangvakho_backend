from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import json

from app.core.database import get_db
from app.models.entities import OrderEntity, DealerEntity, ProductEntity, CategoryEntity, UserEntity
from app.api.v1.endpoints.auth import get_current_user, UserResponse
from app.api.v1.endpoints.orders import ORDERS_DB
from app.models.dealer import DEALERS_DB, load_dealers_db
from app.models.user import USERS_DB, load_users_db

router = APIRouter()


class SalesReportItem(BaseModel):
    id: str
    code: Optional[str] = None
    name: str
    sub_text: Optional[str] = None
    quantity: int
    revenue: float
    previous_quantity: Optional[int] = 0
    previous_revenue: Optional[float] = 0.0
    growth_rate: Optional[float] = 0.0
    # Các chỉ số lợi nhuận - chỉ trả về cho Admin / Sales Manager
    cost: Optional[float] = None
    gross_profit: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    # Đồng bộ Chỉ tiêu KPI và Hàng trả lại
    target_amount: Optional[float] = None
    target_achievement_rate: Optional[float] = None
    return_quantity: Optional[int] = 0
    return_amount: Optional[float] = 0.0


class SalesReportSummary(BaseModel):
    total_quantity: int
    total_revenue: float
    previous_total_revenue: float
    total_growth_rate: float
    total_orders_count: int
    can_view_profit: bool
    total_cost: Optional[float] = None
    total_gross_profit: Optional[float] = None
    average_gross_margin_pct: Optional[float] = None
    total_target_amount: Optional[float] = None
    total_target_achievement_rate: Optional[float] = None
    total_return_quantity: Optional[int] = 0
    total_return_amount: Optional[float] = 0.0


class SalesMultidimensionalResponse(BaseModel):
    dimension: str
    time_preset: str
    start_date: str
    end_date: str
    previous_start_date: str
    previous_end_date: str
    summary: SalesReportSummary
    items: List[SalesReportItem]
    can_view_profit: bool


def normalize_region_name(raw: Optional[str]) -> str:
    if not raw or not str(raw).strip() or str(raw).strip() == "Chưa phân vùng":
        return "Chưa phân vùng"
    clean = str(raw).strip()
    low = clean.lower()
    if low in ["ha noi", "hanoi", "hn"]:
        return "Hà Nội"
    if low in ["tp hcm", "tp.hcm", "tphcm", "ho chi minh", "hcm", "tp. hcm", "sai gon", "saigon", "tp ho chi minh"]:
        return "TP. HCM"
    if low in ["da nang", "danang"]:
        return "Đà Nẵng"
    if low in ["hai phong", "haiphong"]:
        return "Hải Phòng"
    if low in ["can tho", "cantho"]:
        return "Cần Thơ"
    return clean


def check_sales_report_access(current_user: UserResponse) -> bool:
    """
    Kiểm tra quyền truy cập báo cáo doanh số:
    - Admin, Sales Manager: Toàn quyền xem và xem được Biên lợi nhuận gộp.
    - Sales (Nhân viên kinh doanh): Xem được Doanh số & Sản lượng, KHÔNG xem được Biên lợi nhuận.
    - Kho (Warehouse), Khách hàng (Customer): Không có quyền truy cập (403 Forbidden).
    """
    roles = set()
    if current_user.role:
        roles.add(current_user.role.lower())
    for r in getattr(current_user, "roles", []) or []:
        roles.add(str(r).lower())

    # Khách hàng hoặc kho bị chặn
    if "customer" in roles or "kho" in roles or "warehouse" in roles or "warehouse_manager" in roles:
        if "admin" not in roles and "sales_manager" not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền truy cập Báo cáo doanh số."
            )

    allowed = {"admin", "sales_manager", "sales", "agent", "accountant"}
    if not (roles & allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập Báo cáo doanh số."
        )

    # Quyền xem giá vốn & biên lợi nhuận
    can_view_profit = bool(
        "admin" in roles
        or "sales_manager" in roles
        or getattr(current_user, "can_view_cost", False)
    )
    return can_view_profit


def safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Truy xuất an toàn thuộc tính dù obj là dict, Pydantic model hay SQLAlchemy entity."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_date_range(time_preset: str, start_date_str: Optional[str], end_date_str: Optional[str]):
    now = datetime.now(timezone.utc)
    
    if time_preset == "today":
        start_date = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        end_date = now
        prev_end_date = start_date
        prev_start_date = start_date - timedelta(days=1)
    elif time_preset == "last_month":
        if now.month == 1:
            start_date = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
            end_date = datetime(now.year, 1, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
            prev_start_date = datetime(now.year - 1, 11, 1, tzinfo=timezone.utc)
            prev_end_date = start_date - timedelta(microseconds=1)
        else:
            start_date = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)
            end_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
            if now.month - 1 == 1:
                prev_start_date = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
            else:
                prev_start_date = datetime(now.year, now.month - 2, 1, tzinfo=timezone.utc)
            prev_end_date = start_date - timedelta(microseconds=1)
    elif time_preset == "this_quarter":
        quarter = (now.month - 1) // 3 + 1
        q_start_month = 3 * (quarter - 1) + 1
        start_date = datetime(now.year, q_start_month, 1, tzinfo=timezone.utc)
        end_date = now
        delta = end_date - start_date
        prev_end_date = start_date - timedelta(microseconds=1)
        prev_start_date = prev_end_date - delta
    elif time_preset == "custom" and start_date_str and end_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
            if not start_date.tzinfo:
                start_date = start_date.replace(tzinfo=timezone.utc)
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            if not end_date.tzinfo:
                end_date = end_date.replace(tzinfo=timezone.utc)
            # Kết thúc ngày nếu không có giờ
            if end_date.hour == 0 and end_date.minute == 0:
                end_date = end_date.replace(hour=23, minute=59, second=59)
            delta = end_date - start_date
            prev_end_date = start_date - timedelta(seconds=1)
            prev_start_date = prev_end_date - delta
        except Exception:
            start_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
            end_date = now
            delta = end_date - start_date
            prev_end_date = start_date - timedelta(seconds=1)
            prev_start_date = prev_end_date - delta
    else:  # default 'this_month'
        time_preset = "this_month"
        start_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        end_date = now
        # Cùng kỳ tháng trước
        days_in = (end_date - start_date).days
        if now.month == 1:
            prev_month_start = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
        else:
            prev_month_start = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)
        prev_start_date = prev_month_start
        prev_end_date = prev_month_start + timedelta(days=days_in)

    return start_date, end_date, prev_start_date, prev_end_date, time_preset


@router.get("/sales-multidimensional", response_model=SalesMultidimensionalResponse)
def get_sales_multidimensional_report(
    dimension: str = Query("category", description="Chiều nhóm: salesperson | region | dealer | category | sku"),
    time_preset: str = Query("this_month", description="this_month | last_month | this_quarter | today | custom"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Báo cáo doanh số theo nhiều chiều dành cho Quản lý kinh doanh.
    Nhóm theo: Nhân viên, Khu vực, Đại lý, Nhóm ngành hàng, hoặc từng SKU.
    Tính toán Sản lượng, Doanh số, Lợi nhuận gộp & Biên lợi nhuận (chỉ Quản lý xem được).
    So sánh với cùng kỳ trước.
    """
    can_view_profit = check_sales_report_access(current_user)
    
    cur_start, cur_end, prev_start, prev_end, time_preset = parse_date_range(time_preset, start_date, end_date)

    # 1. Tra cứu thông tin sản phẩm (code, name, category, cost_price, sell_price)
    products_map: Dict[int, Dict[str, Any]] = {}
    try:
        db_prods = db.query(ProductEntity).all()
        for p in db_prods:
            products_map[p.id] = {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "category": p.category or "Khác",
                "category_id": p.category_id,
                "cost_price": float(p.cost_price or 0.0),
                "sell_price": float(p.sell_price or 0.0),
            }
    except Exception as ex:
        print(f"Warning query products: {ex}")

    # 2. Tra cứu đại lý (name, region, assigned_sale_id)
    dealers_map: Dict[int, Dict[str, Any]] = {}
    try:
        db_dealers = db.query(DealerEntity).all()
        for d in db_dealers:
            dealers_map[d.id] = {
                "id": d.id,
                "code": d.code,
                "name": d.name,
                "region": getattr(d, "region", None) or "Chưa phân vùng",
                "assigned_sale_id": d.assigned_sale_id,
            }
    except Exception as ex:
        print(f"Warning query dealers: {ex}")

    # Bổ sung từ DEALERS_DB
    for d_id, d_data in DEALERS_DB.items():
        if d_id not in dealers_map:
            dealers_map[d_id] = {
                "id": d_id,
                "code": safe_get(d_data, "code") or f"DL{d_id:03d}",
                "name": safe_get(d_data, "name") or "",
                "region": safe_get(d_data, "region") or "Chưa phân vùng",
                "assigned_sale_id": safe_get(d_data, "assigned_sale_id"),
            }

    # 3. Tra cứu nhân viên bán hàng
    sales_map: Dict[int, str] = {}
    try:
        db_users = db.query(UserEntity).all()
        for u in db_users:
            sales_map[u.id] = u.full_name or u.username
    except Exception as ex:
        print(f"Warning query users: {ex}")

    for u_id, u_data in USERS_DB.items():
        if u_id not in sales_map:
            sales_map[u_id] = safe_get(u_data, "full_name") or safe_get(u_data, "username") or ""

    # 4. Hợp nhất danh sách đơn hàng
    all_orders = []
    seen_codes = set()

    try:
        db_orders = db.query(OrderEntity).all()
        for o in db_orders:
            if o.order_code not in seen_codes:
                seen_codes.add(o.order_code)
                created_dt = o.created_at
                if isinstance(created_dt, str):
                    try:
                        created_dt = datetime.fromisoformat(created_dt.replace("Z", "+00:00"))
                    except Exception:
                        created_dt = cur_end
                if created_dt and not created_dt.tzinfo:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)

                items = []
                if getattr(o, "items_rel", None):
                    for it in o.items_rel:
                        items.append({
                            "product_id": it.product_id,
                            "product_name": it.product_name,
                            "quantity": int(it.quantity or 1),
                            "unit_price": float(it.unit_price or 0.0),
                        })
                elif o.items_json:
                    try:
                        parsed = json.loads(o.items_json)
                        if isinstance(parsed, list):
                            for it in parsed:
                                items.append({
                                    "product_id": it.get("product_id") or it.get("id"),
                                    "product_name": it.get("product_name") or it.get("name"),
                                    "quantity": int(it.get("quantity") or 1),
                                    "unit_price": float(it.get("unit_price") or it.get("price") or 0.0),
                                })
                    except Exception:
                        pass

                all_orders.append({
                    "order_code": o.order_code,
                    "dealer_id": o.dealer_id,
                    "dealer_name": o.dealer_name,
                    "assigned_sale_id": o.assigned_sale_id,
                    "assigned_sale_name": o.assigned_sale_name,
                    "total_amount": float(o.total_amount or 0.0),
                    "status": (o.status or "PENDING").upper(),
                    "created_at": created_dt or cur_end,
                    "items": items,
                })
    except Exception as ex:
        print(f"Warning reading db orders: {ex}")

    for o_dict in ORDERS_DB.values():
        code = safe_get(o_dict, "order_code")
        if code and code not in seen_codes:
            seen_codes.add(code)
            created_val = safe_get(o_dict, "created_at")
            if isinstance(created_val, datetime):
                dt = created_val
            elif isinstance(created_val, str):
                try:
                    dt = datetime.fromisoformat(created_val.replace("Z", "+00:00"))
                except Exception:
                    dt = cur_end
            else:
                dt = cur_end
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)

            raw_items = safe_get(o_dict, "items") or []
            normalized_items = []
            for it in raw_items:
                normalized_items.append({
                    "product_id": safe_get(it, "product_id") or safe_get(it, "id"),
                    "product_name": safe_get(it, "product_name") or safe_get(it, "name"),
                    "quantity": int(safe_get(it, "quantity") or 1),
                    "unit_price": float(safe_get(it, "unit_price") or safe_get(it, "price") or 0.0),
                })

            all_orders.append({
                "order_code": code,
                "dealer_id": safe_get(o_dict, "dealer_id"),
                "dealer_name": safe_get(o_dict, "dealer_name") or "",
                "assigned_sale_id": safe_get(o_dict, "assigned_sale_id"),
                "assigned_sale_name": safe_get(o_dict, "assigned_sale_name"),
                "total_amount": float(safe_get(o_dict, "total_amount") or 0.0),
                "status": (safe_get(o_dict, "status") or "PENDING").upper(),
                "created_at": dt,
                "items": normalized_items,
            })

    # Đơn hàng hợp lệ để tính doanh số: Không phải bị huỷ hoặc từ chối
    valid_statuses = {"PAID", "COMPLETED", "APPROVED", "DELIVERED", "DELIVERING", "PICKING", "EXPORTED", "CONFIRMED"}

    # 5. Phân loại đơn hàng vào Kỳ này vs Cùng kỳ trước
    current_period_orders = []
    previous_period_orders = []

    for o in all_orders:
        if o["status"] not in valid_statuses:
            continue
        c_at = o["created_at"]
        if cur_start <= c_at <= cur_end:
            current_period_orders.append(o)
        elif prev_start <= c_at <= prev_end:
            previous_period_orders.append(o)

    # Helper xác định Group Key & Name cho 1 item hoặc order
    def get_group_info(order: Dict[str, Any], item: Dict[str, Any]) -> tuple[str, Optional[str], str, Optional[str]]:
        """Trả về (id, code, name, sub_text) theo dimension"""
        p_id = item.get("product_id")
        p_info = products_map.get(p_id, {})
        d_id = order.get("dealer_id")
        d_info = dealers_map.get(d_id, {})

        if dimension == "salesperson":
            sale_id = order.get("assigned_sale_id") or d_info.get("assigned_sale_id") or 0
            sale_name = order.get("assigned_sale_name") or sales_map.get(sale_id) or "Chưa phân công"
            return str(sale_id), f"NV{sale_id:03d}" if sale_id else "N/A", sale_name, "Kinh doanh"

        elif dimension == "region":
            raw_reg = d_info.get("region") or "Chưa phân vùng"
            reg = normalize_region_name(raw_reg)
            return reg, None, reg, "Địa bàn kinh doanh"

        elif dimension == "dealer":
            d_name = order.get("dealer_name") or d_info.get("name") or (f"Đại lý #{d_id}" if d_id else "Đại lý vãng lai")
            d_code = d_info.get("code") or (f"DL{d_id:03d}" if isinstance(d_id, int) else (str(d_id) if d_id else "N/A"))
            return str(d_id or "0"), d_code, d_name, d_info.get("region") or ""

        elif dimension == "sku":
            p_code = p_info.get("code") or (f"SP{p_id:03d}" if isinstance(p_id, int) else (str(p_id) if p_id else "SP-KHAC"))
            p_name = item.get("product_name") or p_info.get("name") or (f"Sản phẩm #{p_id}" if p_id else "Sản phẩm khác")
            cat_name = p_info.get("category") or "Khác"
            return str(p_id or "0"), p_code, p_name, cat_name

        else:  # category
            cat_name = p_info.get("category") or "Khác"
            return cat_name, None, cat_name, "Ngành hàng"

    # 6. Truy vấn Chỉ tiêu doanh số (KPI) cho kỳ báo cáo
    targets_map: Dict[str, float] = {}
    try:
        from app.models.sales_target import SalesTargetEntity
        m_year = cur_start.strftime("%Y-%m")
        db_targets = db.query(SalesTargetEntity).filter(SalesTargetEntity.month_year == m_year).all()
        for t in db_targets:
            if t.target_type == "salesperson" and t.employee_id:
                targets_map[f"salesperson_{t.employee_id}"] = float(t.target_amount or 0.0)
            elif t.target_type == "region" and t.region:
                targets_map[f"region_{normalize_region_name(t.region)}"] = float(t.target_amount or 0.0)
    except Exception as ex:
        print(f"Warning query sales targets: {ex}")

    # 7. Gom nhóm tính toán số liệu kỳ này
    groups: Dict[str, Dict[str, Any]] = {}

    # Nếu chiều là nhân viên kinh doanh, khởi tạo trước toàn bộ nhân viên sales trong hệ thống
    if dimension == "salesperson":
        try:
            for u in db_users:
                u_role = (getattr(u, "role", "") or "").lower()
                u_roles = [str(r).lower() for r in (getattr(u, "roles", []) or [u_role])]
                if "sales" in u_roles or "sales_manager" in u_roles or "agent" in u_roles or u.id == 3:
                    g_id = str(u.id)
                    groups[g_id] = {
                        "id": g_id,
                        "code": f"NV{u.id:03d}",
                        "name": u.full_name or u.username,
                        "sub_text": "Kinh doanh",
                        "quantity": 0,
                        "revenue": 0.0,
                        "cost": 0.0,
                        "previous_quantity": 0,
                        "previous_revenue": 0.0,
                        "return_quantity": 0,
                        "return_amount": 0.0,
                    }
        except Exception:
            pass

    for o in current_period_orders:
        for it in o.get("items") or []:
            g_id, g_code, g_name, g_sub = get_group_info(o, it)
            if g_id not in groups:
                groups[g_id] = {
                    "id": g_id,
                    "code": g_code,
                    "name": g_name,
                    "sub_text": g_sub,
                    "quantity": 0,
                    "revenue": 0.0,
                    "cost": 0.0,
                    "previous_quantity": 0,
                    "previous_revenue": 0.0,
                    "return_quantity": 0,
                    "return_amount": 0.0,
                }
            
            qty = int(it.get("quantity") or 1)
            price = float(it.get("unit_price") or it.get("price") or 0.0)
            item_rev = qty * price
            p_info = products_map.get(it.get("product_id"), {})
            cost_price = p_info.get("cost_price", 0.0)
            item_cost = qty * cost_price

            groups[g_id]["quantity"] += qty
            groups[g_id]["revenue"] += item_rev
            groups[g_id]["cost"] += item_cost

    # 8. Gom nhóm số liệu cùng kỳ trước
    for o in previous_period_orders:
        for it in o.get("items") or []:
            g_id, g_code, g_name, g_sub = get_group_info(o, it)
            if g_id not in groups:
                groups[g_id] = {
                    "id": g_id,
                    "code": g_code,
                    "name": g_name,
                    "sub_text": g_sub,
                    "quantity": 0,
                    "revenue": 0.0,
                    "cost": 0.0,
                    "previous_quantity": 0,
                    "previous_revenue": 0.0,
                    "return_quantity": 0,
                    "return_amount": 0.0,
                }
            qty = int(it.get("quantity") or 1)
            price = float(it.get("unit_price") or it.get("price") or 0.0)
            groups[g_id]["previous_quantity"] += qty
            groups[g_id]["previous_revenue"] += (qty * price)

    # 9. Đồng bộ trừ hàng trả lại (RMA / Return Orders)
    total_system_return_qty = 0
    total_system_return_amount = 0.0
    try:
        from app.models.return_order import ReturnOrderEntity
        return_orders = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.status != "CANCELLED").all()
        for ro in return_orders:
            ro_date = ro.confirmed_at or ro.created_at
            if ro_date and not ro_date.tzinfo:
                ro_date = ro_date.replace(tzinfo=timezone.utc)

            is_cur = (cur_start <= ro_date <= cur_end) if ro_date else False
            is_prev = (prev_start <= ro_date <= prev_end) if ro_date else False
            if not (is_cur or is_prev):
                continue

            d_id = ro.dealer_id
            order_info = next((o for o in all_orders if o["order_code"] == ro.order_code), None)

            for r_it in ro.items:
                p_id = r_it.product_id
                r_qty = int(r_it.return_quantity or 0)
                r_amount = float(r_it.refund_amount or (r_qty * float(r_it.unit_price or 0.0)))
                p_info = products_map.get(p_id, {})
                c_price = float(p_info.get("cost_price", 0.0))
                r_cost = r_qty * c_price

                fake_order = {
                    "dealer_id": d_id,
                    "dealer_name": ro.dealer_name,
                    "assigned_sale_id": order_info.get("assigned_sale_id") if order_info else None,
                    "assigned_sale_name": order_info.get("assigned_sale_name") if order_info else None,
                }
                fake_item = {
                    "product_id": p_id,
                    "product_name": r_it.product_name,
                }
                g_id, g_code, g_name, g_sub = get_group_info(fake_order, fake_item)
                if g_id in groups:
                    if is_cur:
                        groups[g_id]["quantity"] = max(0, groups[g_id]["quantity"] - r_qty)
                        groups[g_id]["revenue"] = max(0.0, groups[g_id]["revenue"] - r_amount)
                        groups[g_id]["cost"] = max(0.0, groups[g_id]["cost"] - r_cost)
                        groups[g_id]["return_quantity"] = groups[g_id].get("return_quantity", 0) + r_qty
                        groups[g_id]["return_amount"] = groups[g_id].get("return_amount", 0.0) + r_amount
                        total_system_return_qty += r_qty
                        total_system_return_amount += r_amount
                    elif is_prev:
                        groups[g_id]["previous_quantity"] = max(0, groups[g_id]["previous_quantity"] - r_qty)
                        groups[g_id]["previous_revenue"] = max(0.0, groups[g_id]["previous_revenue"] - r_amount)
    except Exception as ex:
        print(f"Warning deducting returns in sales report: {ex}")

    # 10. Chuyển đổi thành danh sách kết quả & Lọc tìm kiếm
    items_list: List[SalesReportItem] = []
    
    total_qty = 0
    total_rev = 0.0
    total_cost = 0.0
    prev_total_rev = 0.0
    accumulated_target = 0.0
    has_any_target = False

    search_kw = search.strip().lower() if search else None

    for g in groups.values():
        rev = g["revenue"]
        prev_rev = g["previous_revenue"]
        cost = g["cost"]
        qty = g["quantity"]

        # Tra cứu KPI tương ứng
        target_amt = None
        achieve_rate = None
        if dimension == "salesperson":
            target_amt = targets_map.get(f"salesperson_{g['id']}")
        elif dimension == "region":
            target_amt = targets_map.get(f"region_{g['id']}")

        if target_amt is not None and target_amt > 0:
            achieve_rate = round((rev / target_amt) * 100, 1)
            accumulated_target += target_amt
            has_any_target = True

        # Nếu không có doanh số cả 2 kỳ và không phải là nhân viên sales (hoặc không có target) thì bỏ qua
        if rev == 0 and prev_rev == 0 and qty == 0:
            if dimension != "salesperson" and target_amt is None:
                continue

        # Lọc tìm kiếm
        if search_kw:
            name_match = search_kw in g["name"].lower()
            code_match = bool(g["code"] and search_kw in g["code"].lower())
            sub_match = bool(g["sub_text"] and search_kw in g["sub_text"].lower())
            if not (name_match or code_match or sub_match):
                continue

        total_qty += qty
        total_rev += rev
        total_cost += cost
        prev_total_rev += prev_rev

        # Tăng trưởng so với cùng kỳ
        if prev_rev > 0:
            growth = round(((rev - prev_rev) / prev_rev) * 100, 1)
        else:
            growth = 100.0 if rev > 0 else 0.0

        # Lợi nhuận gộp & biên lợi nhuận
        gross_profit = rev - cost
        margin_pct = round((gross_profit / rev) * 100, 1) if rev > 0 else 0.0

        item_data = SalesReportItem(
            id=g["id"],
            code=g["code"],
            name=g["name"],
            sub_text=g["sub_text"],
            quantity=qty,
            revenue=round(rev, 2),
            previous_quantity=g["previous_quantity"],
            previous_revenue=round(prev_rev, 2),
            growth_rate=growth,
            cost=round(cost, 2) if can_view_profit else None,
            gross_profit=round(gross_profit, 2) if can_view_profit else None,
            gross_margin_pct=margin_pct if can_view_profit else None,
            target_amount=round(target_amt, 2) if target_amt is not None else None,
            target_achievement_rate=achieve_rate,
            return_quantity=g.get("return_quantity", 0),
            return_amount=round(g.get("return_amount", 0.0), 2),
        )
        items_list.append(item_data)

    # Sắp xếp mặc định: Doanh số giảm dần
    items_list.sort(key=lambda x: x.revenue, reverse=True)

    # Tính tổng hợp Summary
    if prev_total_rev > 0:
        total_growth = round(((total_rev - prev_total_rev) / prev_total_rev) * 100, 1)
    else:
        total_growth = 100.0 if total_rev > 0 else 0.0

    total_gross_profit = total_rev - total_cost
    avg_margin = round((total_gross_profit / total_rev) * 100, 1) if total_rev > 0 else 0.0

    total_target_val = round(accumulated_target, 2) if has_any_target else None
    total_achieve_rate = round((total_rev / accumulated_target) * 100, 1) if (has_any_target and accumulated_target > 0) else None

    summary = SalesReportSummary(
        total_quantity=total_qty,
        total_revenue=round(total_rev, 2),
        previous_total_revenue=round(prev_total_rev, 2),
        total_growth_rate=total_growth,
        total_orders_count=len(current_period_orders),
        can_view_profit=can_view_profit,
        total_cost=round(total_cost, 2) if can_view_profit else None,
        total_gross_profit=round(total_gross_profit, 2) if can_view_profit else None,
        average_gross_margin_pct=avg_margin if can_view_profit else None,
        total_target_amount=total_target_val,
        total_target_achievement_rate=total_achieve_rate,
        total_return_quantity=total_system_return_qty,
        total_return_amount=round(total_system_return_amount, 2),
    )

    return SalesMultidimensionalResponse(
        dimension=dimension,
        time_preset=time_preset,
        start_date=cur_start.strftime("%Y-%m-%d"),
        end_date=cur_end.strftime("%Y-%m-%d"),
        previous_start_date=prev_start.strftime("%Y-%m-%d"),
        previous_end_date=prev_end.strftime("%Y-%m-%d"),
        summary=summary,
        items=items_list,
        can_view_profit=can_view_profit,
    )
