import os
import json
import io
import math
import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.deps import require_roles, get_current_user
from app.core.database import get_db
from app.models.dealer import DEALERS_DB, Dealer, save_dealers_db, load_dealers_db, compute_dealer_unified_debt, parse_tx_date, normalize_dealer_status
from app.models.user import USERS_DB, load_users_db
from app.models.entities import OrderEntity, DealerEntity
from app.models.payment_collection import PaymentCollectionEntity
from app.schemas.auth import UserResponse
from app.services.audit_service import log_audit_event

logger = logging.getLogger(__name__)


router = APIRouter()


class DealerStatusUpdateRequest(BaseModel):
    status: str
    reason: Optional[str] = None


class DealerCreateRequest(BaseModel):
    code: Optional[str] = None
    name: str
    tax_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    region: Optional[str] = None
    assigned_sale_id: Optional[int] = None
    credit_limit: Optional[float] = 50000000.0
    overdue_days_allowed: Optional[int] = 30
    customer_group: Optional[str] = "Đại lý cấp 1"
    status: Optional[str] = "Đang hoạt động"
    transaction_count: Optional[int] = None
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None


class DealerUpdateRequest(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    tax_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    region: Optional[str] = None
    assigned_sale_id: Optional[int] = None
    credit_limit: Optional[float] = None
    max_debt_days: Optional[int] = None
    overdue_days_allowed: Optional[int] = None
    customer_group: Optional[str] = None
    status: Optional[str] = None
    transaction_count: Optional[int] = None
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None


class DealerCreditInfoResponse(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    customer_group: Optional[str] = None
    status: Optional[str] = "Đang hoạt động"
    credit_limit: float
    overdue_days_allowed: int
    max_debt_days: int
    current_debt: float
    remaining_credit: float
    max_debt_age: int
    is_overdue: bool
    is_over_limit: bool
    credit_warning_flag: Optional[bool] = False
    credit_flag_reason: Optional[str] = None
    last_credit_evaluated_at: Optional[Union[str, datetime]] = None
    over_limit_action: Optional[str] = "REQUIRE_APPROVAL"
    overdue_action: Optional[str] = "BLOCK"


class DebtAgingItem(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    region: Optional[str] = None
    assigned_sale_id: Optional[int] = None
    assigned_sale_name: Optional[str] = None
    credit_limit: float
    max_debt_days: int
    current_debt: float
    remaining_credit: float
    max_debt_age: int
    max_overdue_days: int
    debt_current: float
    debt_overdue_1_30: float
    debt_overdue_31_60: float
    debt_overdue_61_90: float
    debt_overdue_over_90: float
    total_overdue: float
    overdue_invoices_count: int
    total_invoices_count: int
    risk_level: str
    risk_level_label: str
    risk_score: float


class HighestRiskDealer(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    phone: Optional[str] = None
    assigned_sale_name: Optional[str] = None
    credit_limit: float
    current_debt: float
    total_overdue: float
    max_overdue_days: int
    max_debt_age: int
    debt_overdue_over_90: float
    risk_level_label: str
    reason: str


class DebtAgingSummary(BaseModel):
    total_debt: float
    total_overdue: float
    total_current: float
    total_overdue_1_30: float
    total_overdue_31_60: float
    total_overdue_61_90: float
    total_overdue_over_90: float
    total_dealers_count: int
    overdue_dealers_count: int
    highest_risk_dealer: Optional[HighestRiskDealer] = None


class DebtAgingReportResponse(BaseModel):
    summary: DebtAgingSummary
    items: List[DebtAgingItem]
    sales_list: List[Dict[str, Any]]


class CreateReceiptVoucherRequest(BaseModel):
    amount: float
    payment_method: Optional[str] = "Tiền mặt"
    reason: Optional[str] = "Thu tiền công nợ đại lý"
    order_code: Optional[str] = None
    payer_name: Optional[str] = None
    note: Optional[str] = None


class ReceiptVoucherResponse(BaseModel):
    id: int
    receipt_code: str
    dealer_id: int
    dealer_code: str
    dealer_name: str
    dealer_address: Optional[str] = None
    dealer_phone: Optional[str] = None
    dealer_tax_code: Optional[str] = None
    order_code: Optional[str] = None
    amount: float
    amount_in_words: str
    payment_method: str
    reason: str
    payer_name: str
    collector_name: str
    collector_role: str
    created_at: str
    note: Optional[str] = None


RECEIPT_VOUCHERS_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "models", "receipt_vouchers_data.json"
)
RECEIPT_VOUCHERS_DB: Dict[str, Dict[str, Any]] = {}


def save_receipt_vouchers_db():
    try:
        with open(RECEIPT_VOUCHERS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(RECEIPT_VOUCHERS_DB, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Error saving receipt vouchers db: {e}")


def load_receipt_vouchers_db():
    if os.path.exists(RECEIPT_VOUCHERS_JSON_PATH):
        try:
            with open(RECEIPT_VOUCHERS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                RECEIPT_VOUCHERS_DB.clear()
                RECEIPT_VOUCHERS_DB.update(data)
        except Exception as e:
            logger.warning(f"Error loading receipt vouchers db: {e}")


load_receipt_vouchers_db()


class DebtStatementItemResponse(BaseModel):
    id: Union[str, int]
    stt: int
    date: str
    doc_code: str
    doc_type: str
    doc_type_label: str
    description: str
    increase_amount: float
    decrease_amount: float
    running_balance: float
    payment_method: Optional[str] = None
    status: Optional[str] = None
    created_by: Optional[str] = None
    note: Optional[str] = None


class DebtStatementSummaryResponse(BaseModel):
    opening_balance: float
    total_increase: float
    total_decrease: float
    closing_balance: float
    closing_balance_words: str
    transactions_count: int


class DebtStatementFullResponse(BaseModel):
    dealer: Dict[str, Any]
    period: Dict[str, Optional[str]]
    summary: DebtStatementSummaryResponse
    transactions: List[DebtStatementItemResponse]


def number_to_vietnamese_words(n: float) -> str:
    n_int = int(round(n))
    if n_int == 0:
        return "Không đồng chẵn."
    if n_int < 0:
        return "Âm " + number_to_vietnamese_words(abs(n_int))

    digits = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
    units = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]

    def read_three(val: int, is_first: bool) -> str:
        h = val // 100
        t = (val % 100) // 10
        o = val % 10
        res = []
        if h > 0 or not is_first:
            res.append(digits[h] + " trăm")
            if t == 0 and o > 0:
                res.append("lẻ")
        if t > 1:
            res.append(digits[t] + " mươi")
            if o == 1:
                res.append("mốt")
            elif o == 5:
                res.append("lăm")
            elif o > 0:
                res.append(digits[o])
        elif t == 1:
            res.append("mười")
            if o == 5:
                res.append("lăm")
            elif o > 0:
                res.append(digits[o])
        elif o > 0:
            res.append(digits[o])
        return " ".join(res)

    groups = []
    temp = n_int
    while temp > 0:
        groups.append(temp % 1000)
        temp //= 1000

    words = []
    for i in range(len(groups) - 1, -1, -1):
        grp = groups[i]
        if grp > 0:
            grp_words = read_three(grp, i == len(groups) - 1)
            u = units[i]
            if u:
                words.append(f"{grp_words} {u}")
            else:
                words.append(grp_words)

    result_str = " ".join(words).strip()
    if not result_str:
        return "Không đồng chẵn."
    return result_str[0].upper() + result_str[1:] + " đồng chẵn."


def get_sale_name(assigned_sale_id: Optional[int]) -> Optional[str]:
    """Lấy tên nhân viên kinh doanh phụ trách đại lý."""
    if not assigned_sale_id:
        return None

    for user in USERS_DB.values():
        if user.id == assigned_sale_id:
            return user.full_name

    return None


def normalize_region_name(raw: Optional[str]) -> str:
    if not raw or not str(raw).strip():
        return "Chưa xác định"
    s = str(raw).strip()
    low = s.lower()
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
    return s


def get_region(address: Optional[str]) -> str:
    """
    Lấy khu vực từ địa chỉ hiện có và chuẩn hóa tên khu vực.
    Ví dụ: '120 Cầu Giấy, Hà Nội' -> 'Hà Nội'
    """
    if not address:
        return "Chưa xác định"

    parts = [part.strip() for part in address.split(",") if part.strip()]
    if parts:
        return normalize_region_name(parts[-1])

    return "Chưa xác định"


def get_applied_price_book_info(customer_group: Optional[str]) -> Optional[dict]:
    """Tìm bảng giá đang áp dụng cho nhóm khách hàng."""
    if not customer_group:
        return None
    try:
        from app.core.database import SessionLocal
        from app.models.price_book import PriceBookEntity, PriceBookItemEntity, get_utc_now
        db = SessionLocal()
        try:
            aliases = [customer_group]
            cg_low = customer_group.lower().replace("_", " ").strip()
            if "cấp 1" in cg_low or "cap 1" in cg_low or "cap_1" in cg_low:
                aliases = ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1", "dai_ly_cap_1"]
            elif "cấp 2" in cg_low or "cap 2" in cg_low or "cap_2" in cg_low:
                aliases = ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2", "dai_ly_cap_2"]
            elif "sỉ" in cg_low or "si" in cg_low:
                aliases = ["Khách sỉ", "khach_si", "Khach_si"]
            elif "lẻ" in cg_low or "le" in cg_low:
                aliases = ["Khach_le", "RETAIL", "Khách lẻ", "khach_le"]

            now = get_utc_now()
            now_naive = now.replace(tzinfo=None) if now.tzinfo else now
            candidates = db.query(PriceBookEntity).filter(
                PriceBookEntity.customer_group.in_(aliases),
                PriceBookEntity.status == "ACTIVE"
            ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).all()

            pb = None
            for cand in candidates:
                vf = cand.valid_from.replace(tzinfo=None) if cand.valid_from and cand.valid_from.tzinfo else cand.valid_from
                vt = cand.valid_to.replace(tzinfo=None) if cand.valid_to and cand.valid_to.tzinfo else cand.valid_to
                if vf and vt and vf <= now_naive <= vt:
                    pb = cand
                    break

            if not pb and candidates:
                pb = candidates[0]

            if pb:
                items_count = db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == pb.id).count()
                vf_check = pb.valid_from.replace(tzinfo=None) if pb.valid_from and pb.valid_from.tzinfo else pb.valid_from
                vt_check = pb.valid_to.replace(tzinfo=None) if pb.valid_to and pb.valid_to.tzinfo else pb.valid_to
                is_active = bool(vf_check and vt_check and vf_check <= now_naive <= vt_check)
                return {
                    "id": pb.id,
                    "code": pb.code,
                    "name": pb.name,
                    "customer_group": pb.customer_group,
                    "valid_from": pb.valid_from.isoformat() if pb.valid_from else None,
                    "valid_to": pb.valid_to.isoformat() if pb.valid_to else None,
                    "status": pb.status,
                    "is_active_now": is_active,
                    "items_count": items_count,
                    "note": pb.note,
                }
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Error fetching applied price book for group {customer_group}: {e}")
    return None


def compute_dealer_stats(current_user: Optional[UserResponse] = None) -> Dict[str, int]:
    """
    Tính toán 4 ô số liệu thống kê thực tế từ hệ thống:
    • Tổng số hồ sơ đại lý
    • Đang hoạt động
    • Ngừng giao dịch
    • Đã phát sinh giao dịch (không thể xóa)
    """
    load_dealers_db()
    from app.api.v1.endpoints.orders import ORDERS_DB

    total = 0
    active = 0
    stopped = 0
    paused = 0
    pending_activation = 0
    with_transactions = 0
    without_transactions = 0

    user_roles = current_user.get_roles() if current_user and hasattr(current_user, "get_roles") else ([current_user.role] if current_user else ["admin"])
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    assigned_sale_filter = None
    if current_user and not is_customer and "admin" not in user_roles and "sales_manager" not in user_roles and "accountant" not in user_roles:
        assigned_sale_filter = current_user.id

    for dealer in DEALERS_DB.values():
        if is_customer:
            if dealer.id != current_user.id and dealer.email != getattr(current_user, "email", None) and dealer.phone != getattr(current_user, "phone", None):
                continue
        elif assigned_sale_filter is not None and dealer.assigned_sale_id != assigned_sale_filter:
            continue

        total += 1
        st = getattr(dealer, "status", "Đang hoạt động") or "Đang hoạt động"
        if st == "Đang hoạt động":
            active += 1
        elif st == "Ngừng giao dịch":
            stopped += 1
        elif st == "Tạm ngừng":
            paused += 1
        elif st == "Chưa kích hoạt":
            pending_activation += 1
        else:
            active += 1

        manual_tx = getattr(dealer, "transaction_count", 0) or 0
        order_tx = sum(1 for o in ORDERS_DB.values() if o.get("dealer_id") == dealer.id)
        transaction_count = max(manual_tx, order_tx)

        if transaction_count > 0:
            with_transactions += 1
        else:
            without_transactions += 1

    return {
        "total": total,
        "active": active,
        "stopped": stopped,
        "paused": paused,
        "pending_activation": pending_activation,
        "with_transactions": with_transactions,
        "without_transactions": without_transactions,
    }


@router.get("/price-book-preview")
def preview_applied_price_book(
    customer_group: str = Query(..., description="Nhóm khách hàng cần tra cứu bảng giá áp dụng"),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant"])
    ),
):
    """
    Tra cứu thông tin bảng giá tự động áp dụng tương ứng với nhóm khách hàng.
    """
    pb_info = get_applied_price_book_info(customer_group)
    return {
        "customer_group": customer_group,
        "applied_price_book": pb_info,
    }


@router.get("/stats")
def get_dealer_stats(
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """
    Trả về 4 ô số liệu thống kê thực tế từ backend, không hard-code số lượng.
    """
    return compute_dealer_stats(current_user)

@router.get("/debt-aging-report", response_model=DebtAgingReportResponse)
def get_debt_aging_report(
    assigned_sale_id: Optional[int] = Query(None, description="Lọc theo nhân viên kinh doanh phụ trách"),
    risk_level: Optional[str] = Query(None, description="Lọc theo mức độ rủi ro (CRITICAL, HIGH, WARNING, SAFE)"),
    search: Optional[str] = Query(None, description="Tìm theo mã hoặc tên đại lý"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant"])
    ),
):
    """
    Báo cáo công nợ quá hạn theo tuổi nợ cho Kế toán công nợ:
    - Tổng hợp theo 5 nhóm tuổi nợ: Trong hạn, Quá hạn 1-30, 31-60, 61-90, >90 ngày
    - Tự động cấn trừ các khoản thu tiền đã khớp quỹ (PaymentCollection CONFIRMED) theo FIFO (First In First Out)
    - Phân tích và chỉ ra đại lý có rủi ro cao nhất cần xử lý trước
    - Lọc theo nhân viên phụ trách, mức độ rủi ro, tìm kiếm
    """
    from app.api.v1.endpoints.orders import ORDERS_DB
    load_dealers_db()
    load_users_db()
    now = datetime.now(timezone.utc)

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    if "admin" not in user_roles and "sales_manager" not in user_roles and "accountant" not in user_roles:
        # Sales thông thường chỉ xem đại lý của mình
        assigned_sale_id = current_user.id

    # Danh sách nhân viên phụ trách phục vụ bộ lọc
    sales_list = []
    seen_sale_ids = set()
    for u in USERS_DB.values():
        u_roles = getattr(u, 'roles', [getattr(u, 'role', '')])
        if any(r in u_roles for r in ['sales', 'sales_manager', 'admin']):
            if u.id not in seen_sale_ids:
                seen_sale_ids.add(u.id)
                sales_list.append({"id": u.id, "name": u.full_name})
    sales_list.sort(key=lambda s: s["name"])

    # Truy vấn tổng tiền đã thu được kế toán xác nhận (PaymentCollection CONFIRMED) theo từng đại lý
    confirmed_payments_map: Dict[int, float] = {}
    try:
        rows = (
            db.query(
                PaymentCollectionEntity.dealer_id,
                func.sum(PaymentCollectionEntity.amount)
            )
            .filter(PaymentCollectionEntity.status == "CONFIRMED")
            .group_by(PaymentCollectionEntity.dealer_id)
            .all()
        )
        for dealer_id, total_amt in rows:
            if dealer_id:
                confirmed_payments_map[dealer_id] = float(total_amt or 0.0)
    except Exception as ex:
        logger.warning(f"Error querying confirmed payments: {ex}")

    # Tính toán công nợ cho từng đại lý
    all_dealers_aging: List[DebtAgingItem] = []
    total_system_debt = 0.0
    total_system_overdue = 0.0
    total_system_current = 0.0
    total_system_1_30 = 0.0
    total_system_31_60 = 0.0
    total_system_61_90 = 0.0
    total_system_over_90 = 0.0
    system_overdue_dealers_count = 0

    for d in DEALERS_DB.values():
        credit_limit = float(getattr(d, "credit_limit", 50000000.0) or 50000000.0)
        allowed_days = int(getattr(d, "overdue_days_allowed", getattr(d, "max_debt_days", 30)) or 30)
        sale_name = get_sale_name(d.assigned_sale_id)
        total_paid = confirmed_payments_map.get(d.id, 0.0)

        # Quét và gom toàn bộ đơn hàng chưa thanh toán của đại lý này
        seen_order_codes = set()
        dealer_orders = []

        try:
            db_orders = db.query(OrderEntity).filter(OrderEntity.dealer_id == d.id).all()
            for o_ent in db_orders:
                st = (o_ent.status or "").upper()
                if st not in ("PAID", "CANCELLED", "REJECTED"):
                    c = o_ent.order_code
                    if c and c not in seen_order_codes:
                        seen_order_codes.add(c)
                        dt = o_ent.created_at or now
                        if isinstance(dt, datetime) and not dt.tzinfo:
                            dt = dt.replace(tzinfo=timezone.utc)
                        dealer_orders.append({
                            "order_code": c,
                            "amount": float(o_ent.total_amount or 0.0),
                            "created_at": dt,
                        })
        except Exception:
            pass

        for o in ORDERS_DB.values():
            if o.get("dealer_id") == d.id and str(o.get("status", "")).upper() not in ("PAID", "CANCELLED", "REJECTED"):
                c = o.get("order_code")
                if c and c not in seen_order_codes:
                    seen_order_codes.add(c)
                    created_at_val = o.get("created_at")
                    order_date = now
                    if created_at_val:
                        try:
                            if isinstance(created_at_val, str):
                                order_date = datetime.fromisoformat(created_at_val.replace("Z", "+00:00"))
                            elif isinstance(created_at_val, datetime):
                                order_date = created_at_val
                            if not order_date.tzinfo:
                                order_date = order_date.replace(tzinfo=timezone.utc)
                        except Exception:
                            pass
                    dealer_orders.append({
                        "order_code": c,
                        "amount": float(o.get("total_amount", 0.0) or 0.0),
                        "created_at": order_date,
                    })

        # Sắp xếp các đơn theo thứ tự ngày tăng dần (đơn cũ nhất lên đầu để cấn trừ nợ trước theo FIFO)
        dealer_orders.sort(key=lambda x: x["created_at"])

        # Cấn trừ số tiền đã thu (PaymentCollection CONFIRMED) theo FIFO
        remaining_payment = total_paid
        for ord_item in dealer_orders:
            if remaining_payment > 0:
                deduct = min(ord_item["amount"], remaining_payment)
                ord_item["amount"] -= deduct
                remaining_payment -= deduct

        debt_current = 0.0
        debt_overdue_1_30 = 0.0
        debt_overdue_31_60 = 0.0
        debt_overdue_61_90 = 0.0
        debt_overdue_over_90 = 0.0
        max_debt_age = 0
        max_overdue_days = 0
        overdue_invoices_count = 0
        total_invoices_count = 0

        for ord_item in dealer_orders:
            amt = ord_item["amount"]
            if amt <= 0.0001:
                continue

            total_invoices_count += 1
            days_old = max(0, (now - ord_item["created_at"]).days)
            if days_old > max_debt_age:
                max_debt_age = days_old

            if days_old <= allowed_days:
                debt_current += amt
            else:
                overdue_days = days_old - allowed_days
                if overdue_days > max_overdue_days:
                    max_overdue_days = overdue_days
                overdue_invoices_count += 1

                if 1 <= overdue_days <= 30:
                    debt_overdue_1_30 += amt
                elif 31 <= overdue_days <= 60:
                    debt_overdue_31_60 += amt
                elif 61 <= overdue_days <= 90:
                    debt_overdue_61_90 += amt
                else:
                    debt_overdue_over_90 += amt

        dealer_current_debt = debt_current + debt_overdue_1_30 + debt_overdue_31_60 + debt_overdue_61_90 + debt_overdue_over_90
        dealer_total_overdue = debt_overdue_1_30 + debt_overdue_31_60 + debt_overdue_61_90 + debt_overdue_over_90
        remaining_credit = max(0.0, credit_limit - dealer_current_debt)

        if dealer_total_overdue > 0:
            system_overdue_dealers_count += 1

        total_system_debt += dealer_current_debt
        total_system_overdue += dealer_total_overdue
        total_system_current += debt_current
        total_system_1_30 += debt_overdue_1_30
        total_system_31_60 += debt_overdue_31_60
        total_system_61_90 += debt_overdue_61_90
        total_system_over_90 += debt_overdue_over_90

        # Phân loại mức độ rủi ro & điểm rủi ro
        risk_score = (
            (debt_overdue_over_90 * 5.0) +
            (debt_overdue_61_90 * 3.0) +
            (debt_overdue_31_60 * 2.0) +
            (debt_overdue_1_30 * 1.0)
        )
        if dealer_current_debt > credit_limit:
            risk_score += (dealer_current_debt - credit_limit) * 2.0

        if debt_overdue_over_90 > 0 or (credit_limit > 0 and dealer_current_debt > credit_limit * 1.5):
            risk_level_code = "CRITICAL"
            risk_level_label = "Báo động đỏ"
        elif debt_overdue_61_90 > 0 or (credit_limit > 0 and dealer_current_debt > credit_limit):
            risk_level_code = "HIGH"
            risk_level_label = "Rủi ro cao"
        elif dealer_total_overdue > 0:
            risk_level_code = "WARNING"
            risk_level_label = "Cảnh báo"
        else:
            risk_level_code = "SAFE"
            risk_level_label = "An toàn"

        item = DebtAgingItem(
            dealer_id=d.id,
            dealer_code=d.code,
            dealer_name=d.name,
            phone=d.phone,
            address=d.address,
            region=getattr(d, "region", None) or get_region(d.address),
            assigned_sale_id=d.assigned_sale_id,
            assigned_sale_name=sale_name,
            credit_limit=credit_limit,
            max_debt_days=allowed_days,
            current_debt=dealer_current_debt,
            remaining_credit=remaining_credit,
            max_debt_age=max_debt_age,
            max_overdue_days=max_overdue_days,
            debt_current=debt_current,
            debt_overdue_1_30=debt_overdue_1_30,
            debt_overdue_31_60=debt_overdue_31_60,
            debt_overdue_61_90=debt_overdue_61_90,
            debt_overdue_over_90=debt_overdue_over_90,
            total_overdue=dealer_total_overdue,
            overdue_invoices_count=overdue_invoices_count,
            total_invoices_count=total_invoices_count,
            risk_level=risk_level_code,
            risk_level_label=risk_level_label,
            risk_score=risk_score,
        )
        all_dealers_aging.append(item)

    # Tìm đại lý rủi ro cao nhất
    overdue_dealers = [d for d in all_dealers_aging if d.total_overdue > 0]
    highest_risk_dealer = None
    if overdue_dealers:
        sorted_by_risk = sorted(
            overdue_dealers,
            key=lambda x: (x.risk_score, x.max_overdue_days, x.total_overdue),
            reverse=True
        )
        top = sorted_by_risk[0]
        reasons = []
        if top.debt_overdue_over_90 > 0:
            reasons.append(f"Có {top.debt_overdue_over_90:,.0f} đ nợ quá hạn >90 ngày")
        if top.max_overdue_days > 0:
            reasons.append(f"Tuổi nợ quá hạn cao nhất {top.max_overdue_days} ngày")
        if top.current_debt > top.credit_limit:
            reasons.append(f"Vượt hạn mức tín dụng {(top.current_debt - top.credit_limit):,.0f} đ")

        highest_risk_dealer = HighestRiskDealer(
            dealer_id=top.dealer_id,
            dealer_code=top.dealer_code,
            dealer_name=top.dealer_name,
            phone=top.phone,
            assigned_sale_name=top.assigned_sale_name,
            credit_limit=top.credit_limit,
            current_debt=top.current_debt,
            total_overdue=top.total_overdue,
            max_overdue_days=top.max_overdue_days,
            max_debt_age=top.max_debt_age,
            debt_overdue_over_90=top.debt_overdue_over_90,
            risk_level_label=top.risk_level_label,
            reason="; ".join(reasons) if reasons else "Nợ quá hạn cần xử lý gấp"
        )

    # Áp dụng bộ lọc
    filtered_items = all_dealers_aging
    if assigned_sale_id is not None:
        filtered_items = [it for it in filtered_items if it.assigned_sale_id == assigned_sale_id]
    if risk_level and risk_level != "all":
        filtered_items = [it for it in filtered_items if it.risk_level.lower() == risk_level.lower()]
    if search and search.strip():
        s = search.strip().lower()
        filtered_items = [
            it for it in filtered_items
            if s in it.dealer_code.lower() or s in it.dealer_name.lower() or (it.phone and s in it.phone)
        ]

    # Sắp xếp mặc định: Đại lý rủi ro nhất lên trước
    filtered_items.sort(
        key=lambda x: (x.risk_score, x.max_overdue_days, x.total_overdue),
        reverse=True
    )

    summary = DebtAgingSummary(
        total_debt=total_system_debt,
        total_overdue=total_system_overdue,
        total_current=total_system_current,
        total_overdue_1_30=total_system_1_30,
        total_overdue_31_60=total_system_31_60,
        total_overdue_61_90=total_system_61_90,
        total_overdue_over_90=total_system_over_90,
        total_dealers_count=len(all_dealers_aging),
        overdue_dealers_count=system_overdue_dealers_count,
        highest_risk_dealer=highest_risk_dealer,
    )

    return DebtAgingReportResponse(
        summary=summary,
        items=filtered_items,
        sales_list=sales_list,
    )


def prefetch_dealer_debt_context(db: Optional[Session] = None):
    """
    Prefetch SQL orders, receipts/payments, adjustments for accurate dealer debt calculation.
    """
    sql_orders_by_dealer = {}
    payments_by_dealer = {}  # {dealer_id: {"by_order": {order_code: amount}, "unallocated": amount}}
    adjustments_by_dealer = {}  # {dealer_id: {order_code: amount}}
    seen_receipt_codes = set()

    load_receipt_vouchers_db()

    if db:
        try:
            from app.models.entities import OrderEntity
            for ord_ent in db.query(OrderEntity).all():
                sql_orders_by_dealer.setdefault(ord_ent.dealer_id, []).append(ord_ent)
        except Exception as e:
            logger.warning(f"Error prefetching OrderEntity: {e}")

        try:
            from app.models.receipt_voucher import ReceiptVoucherEntity
            for rv in db.query(ReceiptVoucherEntity).all():
                seen_receipt_codes.add(rv.receipt_code)
                amt = float(rv.amount or 0.0)
                d_entry = payments_by_dealer.setdefault(rv.dealer_id, {"by_order": {}, "unallocated": 0.0})
                if rv.order_code and rv.order_code.strip():
                    oc = rv.order_code.strip()
                    d_entry["by_order"][oc] = d_entry["by_order"].get(oc, 0.0) + amt
                else:
                    d_entry["unallocated"] += amt
        except Exception as e:
            logger.warning(f"Error prefetching ReceiptVoucherEntity: {e}")

        try:
            from app.models.payment_collection import PaymentCollectionEntity
            for pc in db.query(PaymentCollectionEntity).filter(PaymentCollectionEntity.status == "CONFIRMED").all():
                amt = float(pc.amount or 0.0)
                d_entry = payments_by_dealer.setdefault(pc.dealer_id, {"by_order": {}, "unallocated": 0.0})
                if pc.order_code and pc.order_code.strip():
                    oc = pc.order_code.strip()
                    d_entry["by_order"][oc] = d_entry["by_order"].get(oc, 0.0) + amt
                else:
                    d_entry["unallocated"] += amt
        except Exception as e:
            logger.warning(f"Error prefetching PaymentCollectionEntity: {e}")

        try:
            from app.models.debt_adjustment import DebtAdjustmentEntity
            for adj in db.query(DebtAdjustmentEntity).filter(DebtAdjustmentEntity.status == "POSTED").all():
                amt = float(adj.total_adjusted_amount or 0.0)
                ord_c = (adj.original_invoice_code or "").replace("HD-", "").strip()
                d_adj = adjustments_by_dealer.setdefault(adj.dealer_id, {})
                d_adj[ord_c] = d_adj.get(ord_c, 0.0) + amt
        except Exception as e:
            logger.warning(f"Error prefetching DebtAdjustmentEntity: {e}")

    # Đồng bộ thêm các phiếu thu từ RECEIPT_VOUCHERS_DB in-memory/JSON (nếu chưa có trong SQL)
    for v_code, v in RECEIPT_VOUCHERS_DB.items():
        if v_code in seen_receipt_codes:
            continue
        d_id = v.get("dealer_id")
        if not d_id:
            continue
        amt = float(v.get("amount", 0.0) or 0.0)
        d_entry = payments_by_dealer.setdefault(d_id, {"by_order": {}, "unallocated": 0.0})
        oc = v.get("order_code")
        if oc and oc.strip():
            oc_clean = oc.strip()
            d_entry["by_order"][oc_clean] = d_entry["by_order"].get(oc_clean, 0.0) + amt
        else:
            d_entry["unallocated"] += amt

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return sql_orders_by_dealer, payments_by_dealer, adjustments_by_dealer, now


@router.get("/search")
def search_dealers(
    keyword: Optional[str] = Query(
        default=None,
        description="Tìm nhanh theo mã đại lý, tên đại lý, MST hoặc số điện thoại"
    ),
    region: Optional[str] = Query(
        default=None,
        description="Lọc theo khu vực"
    ),
    customer_group: Optional[str] = Query(
        default=None,
        description="Lọc theo nhóm khách hàng (Đại lý cấp 1, Đại lý cấp 2, Khách sỉ, Khách lẻ...)"
    ),
    assigned_sale_id: Optional[int] = Query(
        default=None,
        description="Lọc theo người phụ trách"
    ),
    status: Optional[str] = Query(
        default=None,
        description="Lọc theo trạng thái (Đang hoạt động, Tạm ngừng...)"
    ),
    has_transactions: Optional[bool] = Query(
        default=None,
        description="Lọc theo đại lý đã phát sinh giao dịch hay chưa"
    ),
    credit_status: Optional[str] = Query(
        default=None,
        description="Lọc theo tình trạng công nợ (flagged, over_limit, overdue, safe)"
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="Trang hiện tại (phân trang)"
    ),
    page_size: int = Query(
        default=10,
        ge=1,
        description="Số lượng bản ghi mỗi trang"
    ),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """
    Tìm kiếm và lọc danh sách đại lý cho nhân viên kinh doanh:
    • Tìm nhanh theo mã, tên, mã số thuế, số điện thoại
    • Lọc theo khu vực, nhóm khách hàng, người phụ trách, trạng thái
    • Hỗ trợ phân trang và thống kê trực tiếp
    """
    load_dealers_db()

    keyword_clean = keyword.strip().lower() if keyword and isinstance(keyword, str) else None
    region_clean = region.strip().lower() if region and isinstance(region, str) else None
    group_clean = customer_group.strip().lower() if customer_group and isinstance(customer_group, str) else None
    status_clean = status.strip().lower() if status and isinstance(status, str) else None
    assigned_sale_id = assigned_sale_id if isinstance(assigned_sale_id, int) else None
    has_transactions = has_transactions if isinstance(has_transactions, bool) else None

    if not isinstance(page, int):
        page = 1
    if not isinstance(page_size, int) or page_size <= 0:
        page_size = 10

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    sql_orders_by_dealer, payments_by_dealer, adjustments_by_dealer, now = prefetch_dealer_debt_context(db)

    if is_customer:
        customer_dealer = DEALERS_DB.get(current_user.id)
        if not customer_dealer:
            for d in DEALERS_DB.values():
                if (d.email and d.email == current_user.email) or \
                   (d.phone and d.phone == current_user.phone) or \
                   (d.code and d.code.lower() == current_user.username.lower()) or \
                   (d.name and d.name.lower() == (current_user.full_name or "").lower()):
                    customer_dealer = d
                    break
        if not customer_dealer:
            return {
                "items": [],
                "dealers": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "stats": compute_dealer_stats(current_user),
            }

        dealer_region = customer_dealer.region or get_region(customer_dealer.address)
        c_limit = float(getattr(customer_dealer, "credit_limit", 50000000.0) or 50000000.0)
        c_days = int(getattr(customer_dealer, "max_debt_days", 30) or 30)
        
        cust_orders = get_dealer_unified_orders(
            customer_dealer.id,
            dealer=customer_dealer,
            db=db,
            preloaded_sql_orders=sql_orders_by_dealer.get(customer_dealer.id)
        )
        d_pay = payments_by_dealer.get(customer_dealer.id, {"by_order": {}, "unallocated": 0.0})
        d_adj = adjustments_by_dealer.get(customer_dealer.id, {})
        current_debt, max_debt_age = compute_dealer_unified_debt(
            customer_dealer.id,
            cust_orders,
            d_pay["by_order"],
            d_pay["unallocated"],
            d_adj,
            now,
        )

        debt_status = "Bình thường"
        if current_debt > c_limit:
            debt_status = "Vượt hạn mức"
        elif current_debt > 0 and max_debt_age > c_days:
            debt_status = "Quá hạn thanh toán"
        elif current_debt > 0:
            debt_status = "Trong hạn mức"

        applied_pb = get_applied_price_book_info(getattr(customer_dealer, "customer_group", "Đại lý cấp 1"))
        single_item = {
            "id": customer_dealer.id,
            "code": customer_dealer.code,
            "name": customer_dealer.name,
            "tax_code": getattr(customer_dealer, "tax_code", None),
            "phone": customer_dealer.phone,
            "email": customer_dealer.email,
            "address": customer_dealer.address,
            "region": dealer_region,
            "credit_limit": c_limit,
            "max_debt_days": c_days,
            "current_debt": current_debt,
            "debt_status": debt_status,
            "assigned_sale_id": customer_dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(customer_dealer.assigned_sale_id),
            "customer_group": getattr(customer_dealer, "customer_group", "Đại lý cấp 1"),
            "status": normalize_dealer_status(getattr(customer_dealer, "status", "Đang hoạt động")),
            "lock_reason": None,
            "locked_at": None,
            "locked_by": None,
            "transaction_count": len(cust_orders),
            "has_transactions": len(cust_orders) > 0,
            "applied_price_book": applied_pb,
            "warehouse_id": getattr(customer_dealer, "warehouse_id", None),
            "warehouse_name": getattr(customer_dealer, "warehouse_name", None),
        }
        return {
            "items": [single_item],
            "dealers": [single_item],
            "total": 1,
            "page": 1,
            "page_size": page_size,
            "total_pages": 1,
            "stats": compute_dealer_stats(current_user),
        }


    if "admin" not in user_roles and "sales_manager" not in user_roles and "accountant" not in user_roles:
        # Sales chỉ thấy đại lý của mình
        assigned_sale_id = current_user.id

    # Chuẩn hóa keyword dạng chữ số để hỗ trợ tìm số điện thoại khi có khoảng cách hoặc dấu chấm
    clean_kw_digits = "".join(ch for ch in keyword_clean if ch.isdigit()) if keyword_clean else ""
    clean_kw_normalized = keyword_clean.replace("-", "").replace(" ", "").replace("_", "") if keyword_clean else ""

    results = []
    dealers_data_changed = False

    for dealer in DEALERS_DB.values():
        # Tính toán công nợ hiện tại và tuổi nợ từ toàn bộ đơn hàng chuẩn hóa (SQL + In-memory + Phiếu thu + Giảm trừ nợ)
        dealer_orders = get_dealer_unified_orders(
            dealer.id,
            dealer=dealer,
            db=db,
            preloaded_sql_orders=sql_orders_by_dealer.get(dealer.id)
        )
        d_pay = payments_by_dealer.get(dealer.id, {"by_order": {}, "unallocated": 0.0})
        d_adj = adjustments_by_dealer.get(dealer.id, {})
        current_debt, max_debt_age = compute_dealer_unified_debt(
            dealer.id,
            dealer_orders,
            d_pay["by_order"],
            d_pay["unallocated"],
            d_adj,
            now,
        )
        
        c_limit = float(getattr(dealer, "credit_limit", 50000000.0) or 50000000.0)
        c_days = int(getattr(dealer, "overdue_days_allowed", getattr(dealer, "max_debt_days", 30)) or 30)
        is_over_l = current_debt > c_limit
        is_over_d = (current_debt > 0 and max_debt_age >= c_days) if (c_days is not None and c_days > 0) else (current_debt > 0)
        if is_over_l and is_over_d:
            debt_status = "Vượt hạn mức & Quá hạn"
        elif is_over_l:
            debt_status = "Vượt hạn mức"
        elif is_over_d:
            debt_status = "Quá hạn thanh toán"
        elif current_debt > 0:
            debt_status = "Trong hạn mức" 
        else:
            debt_status = "Bình thường" 

        # ==========================================
        # 1. TÌM KIẾM NHANH THEO MÃ / TÊN / MST / SĐT
        # ==========================================
        if keyword_clean:
            code_str = (dealer.code or "").lower()
            code_normalized = code_str.replace("-", "").replace(" ", "").replace("_", "")
            name_str = (dealer.name or "").lower()
            tax_str = (getattr(dealer, "tax_code", "") or "").lower()
            phone_str = (dealer.phone or "").lower()
            phone_digits = "".join(ch for ch in phone_str if ch.isdigit())

            matched_code = (keyword_clean in code_str) or (clean_kw_normalized and clean_kw_normalized in code_normalized)
            matched_name = keyword_clean in name_str
            matched_tax = keyword_clean in tax_str
            matched_phone = keyword_clean in phone_str
            if not matched_phone and clean_kw_digits and len(clean_kw_digits) >= 3:
                matched_phone = clean_kw_digits in phone_digits

            if not (matched_code or matched_name or matched_tax or matched_phone):
                continue

        # ==========================================
        # 2. LỌC THEO KHU VỰC
        # ==========================================
        dealer_region = dealer.region or get_region(dealer.address)

        if region_clean:
            if region_clean not in dealer_region.lower():
                continue

        # ==========================================
        # 3. LỌC THEO NHÓM KHÁCH HÀNG
        # ==========================================
        dealer_group = getattr(dealer, "customer_group", "") or ""
        if group_clean:
            def _normalize_group(g: str) -> str:
                g_low = g.lower().replace("_", " ").strip()
                if "sỉ" in g_low or "si" in g_low:
                    return "khách sỉ"
                if "cấp 1" in g_low or "cap 1" in g_low:
                    return "đại lý cấp 1"
                if "cấp 2" in g_low or "cap 2" in g_low:
                    return "đại lý cấp 2"
                if "lẻ" in g_low or "le" in g_low:
                    return "khách lẻ"
                return g_low

            if _normalize_group(group_clean) != _normalize_group(dealer_group) and group_clean not in dealer_group.lower():
                continue

        # ==========================================
        # 4. LỌC THEO NGƯỜI PHỤ TRÁCH
        # ==========================================
        if assigned_sale_id is not None:
            if dealer.assigned_sale_id != assigned_sale_id:
                continue

        # ==========================================
        # 5. LỌC THEO TRẠNG THÁI
        # ==========================================
        dealer_status = getattr(dealer, "status", "") or ""
        if status_clean:
            if status_clean not in dealer_status.lower():
                continue

        # Đếm số lượng giao dịch đã phát sinh và tra cứu bảng giá áp dụng
        transaction_count = len(dealer_orders)
        applied_pb = get_applied_price_book_info(getattr(dealer, "customer_group", "Đại lý cấp 1"))

        # ==========================================
        # 6. LỌC THEO PHÁT SINH GIAO DỊCH
        # ==========================================
        # 6. LỌC THEO PHÁT SINH GIAO DỊCH
        # ==========================================
        if has_transactions is not None:
            if has_transactions and transaction_count <= 0:
                continue
            if not has_transactions and transaction_count > 0:
                continue

        # ==========================================
        # 7. LỌC THEO CỜ CẢNH BÁO TÍN DỤNG (S7-04)
        # ==========================================
        # Tự động tính toán theo công nợ thực tế thay vì phụ thuộc cờ lưu tĩnh
        is_over_l = bool(current_debt > c_limit) if (c_limit is not None and c_limit > 0) else False
        is_over_d = bool(current_debt > 0 and max_debt_age >= c_days) if (c_days is not None and c_days > 0) else (current_debt > 0 if c_days == 0 else False)
        has_flag = is_over_l or is_over_d or bool(getattr(dealer, "credit_warning_flag", False))

        reasons = []
        if is_over_l:
            reasons.append(f"Vượt hạn mức ({current_debt:,.0f} đ > {c_limit:,.0f} đ)")
        if is_over_d:
            reasons.append(f"Quá hạn thanh toán ({max_debt_age} ngày >= {c_days} ngày)")
        flag_reason = " | ".join(reasons) if reasons else getattr(dealer, "credit_flag_reason", None)
        last_eval = getattr(dealer, "last_credit_evaluated_at", None)

        if (getattr(dealer, "is_over_credit_limit", None) != is_over_l or
            getattr(dealer, "is_overdue", None) != is_over_d or
            getattr(dealer, "credit_warning_flag", None) != has_flag or
            getattr(dealer, "credit_flag_reason", None) != flag_reason):
            dealer.is_over_credit_limit = is_over_l
            dealer.is_overdue = is_over_d
            dealer.credit_warning_flag = has_flag
            dealer.credit_flag_reason = flag_reason
            dealers_data_changed = True
            try:
                from app.core.database import SessionLocal
                from app.models.entities import DealerEntity
                with SessionLocal() as db_session:
                    db_d = db_session.query(DealerEntity).filter(DealerEntity.id == dealer.id).first()
                    if db_d:
                        db_d.is_over_credit_limit = is_over_l
                        db_d.is_overdue = is_over_d
                        db_d.credit_warning_flag = has_flag
                        db_d.credit_flag_reason = flag_reason
                        db_session.commit()
            except Exception:
                pass

        if credit_status:
            cs = credit_status.lower().strip()
            if cs in ("flagged", "warning", "canh_bao"):
                if not has_flag:
                    continue
            elif cs in ("over_limit", "vuot_han_muc"):
                if not is_over_l:
                    continue
            elif cs in ("overdue", "qua_han"):
                if not is_over_d:
                    continue
            elif cs in ("safe", "an_toan", "trong_han_muc"):
                if has_flag:
                    continue

        results.append({
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "tax_code": getattr(dealer, "tax_code", None),
            "phone": dealer.phone,
            "email": dealer.email,
            "address": dealer.address,
            "region": dealer_region,
            "credit_limit": c_limit,
            "max_debt_days": c_days,
            "current_debt": current_debt,
            "debt_status": debt_status,
            "is_over_credit_limit": is_over_l,
            "is_overdue": is_over_d,
            "credit_warning_flag": has_flag,
            "credit_flag_reason": flag_reason,
            "last_credit_evaluated_at": last_eval.isoformat() if hasattr(last_eval, "isoformat") else last_eval,
            "assigned_sale_id": dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
            "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
            "status": normalize_dealer_status(getattr(dealer, "status", "Đang hoạt động")),
            "lock_reason": getattr(dealer, "lock_reason", None),
            "locked_at": getattr(dealer, "locked_at", None),
            "locked_by": getattr(dealer, "locked_by", None),
            "transaction_count": transaction_count,
            "has_transactions": transaction_count > 0,
            "applied_price_book": applied_pb,
            "warehouse_id": getattr(dealer, "warehouse_id", None),
            "warehouse_name": getattr(dealer, "warehouse_name", None),
        })

    if dealers_data_changed:
        save_dealers_db()

    # Sắp xếp đại lý mới nhất lên đầu (id giảm dần) để khi tạo mới hiển thị ngay lập tức ở trang 1
    results.sort(key=lambda d: d["id"], reverse=True)

    # Phân trang
    total_count = len(results)
    total_pages = max(1, math.ceil(total_count / page_size))
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paged_items = results[start_idx:end_idx]

    return {
        "items": paged_items,
        "dealers": paged_items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "stats": compute_dealer_stats(current_user),
    }


def parse_tx_date(val) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        val_clean = val.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(val_clean)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt_obj = datetime.strptime(val[:19], fmt)
                return dt_obj.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def get_dealer_unified_orders(
    dealer_id: int,
    dealer: Any = None,
    db: Optional[Session] = None,
    preloaded_sql_orders: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Hàm chuẩn hóa duy nhất lấy toàn bộ đơn hàng của đại lý:
    1. SQL Database (OrderEntity) - nguồn lưu trữ bền vững.
    2. Cache ORDERS_DB in-memory (các đơn vừa tạo chưa đồng bộ hoặc chỉ có trong RAM).
    3. Mock transactions nếu transaction_count được cấu hình lớn hơn số đơn thực tế.
    """
    if not dealer:
        load_dealers_db()
        dealer = DEALERS_DB.get(dealer_id)

    orders_seen = set()
    orders: List[Dict[str, Any]] = []

    # 1. Đọc từ cơ sở dữ liệu SQL
    sql_orders = preloaded_sql_orders
    if sql_orders is None and db:
        try:
            from app.models.entities import OrderEntity
            sql_orders = db.query(OrderEntity).filter(OrderEntity.dealer_id == dealer_id).all()
        except Exception as e:
            logger.warning(f"Error querying OrderEntity for dealer {dealer_id}: {e}")

    if sql_orders:
        for ord_ent in sql_orders:
            orders_seen.add(ord_ent.order_code)
            dt = parse_tx_date(ord_ent.created_at)
            amt = float(ord_ent.total_amount or 0.0)
            orders.append({
                "id": ord_ent.id,
                "order_code": ord_ent.order_code,
                "created_at": dt.isoformat(),
                "dt_obj": dt,
                "total_amount": amt,
                "status": ord_ent.status or "CONFIRMED",
                "created_by": getattr(ord_ent, "created_by", "Hệ thống bán hàng"),
                "items_count": 1,
                "assigned_sale_name": get_sale_name(dealer.assigned_sale_id) if dealer else "Trần Bán Hàng",
            })

    # 2. Đọc từ in-memory ORDERS_DB
    try:
        from app.api.v1.endpoints.orders import ORDERS_DB
        for ord_id, o in ORDERS_DB.items():
            if o.get("dealer_id") == dealer_id:
                code = o.get("order_code") or f"DH-{ord_id:04d}"
                if code in orders_seen:
                    continue
                orders_seen.add(code)
                dt = parse_tx_date(o.get("created_at"))
                amt = float(o.get("total_amount", 0.0) or 0.0)
                items_list = o.get("items") or []
                orders.append({
                    "id": o.get("id") or ord_id,
                    "order_code": code,
                    "created_at": dt.isoformat(),
                    "dt_obj": dt,
                    "total_amount": amt,
                    "status": o.get("status") or "CONFIRMED",
                    "created_by": o.get("created_by") or "Hệ thống bán hàng",
                    "items_count": len(items_list) if isinstance(items_list, list) else 1,
                    "assigned_sale_name": o.get("assigned_sale_name") or (get_sale_name(dealer.assigned_sale_id) if dealer else "Trần Bán Hàng"),
                })
    except Exception as e:
        logger.warning(f"Error reading ORDERS_DB for dealer {dealer_id}: {e}")

    orders.sort(key=lambda x: x["dt_obj"], reverse=True)
    return orders


@router.get("/{dealer_id}/transactions")
def get_dealer_transactions(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """
    Xem danh sách chi tiết các giao dịch / đơn hàng đã phát sinh của đại lý.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý ID {dealer_id}."
        )

    user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
    roles = {str(r).lower() for r in user_roles if r}
    is_customer = "customer" in roles and not any(r in ["admin", "sales_manager", "sales", "accountant"] for r in roles)
    if is_customer:
        is_own = (current_user.id == dealer_id) or \
                 (getattr(current_user, "username", "").lower() == getattr(dealer, "code", "").lower()) or \
                 (getattr(current_user, "email", None) and getattr(dealer, "email", None) and current_user.email.lower() == dealer.email.lower()) or \
                 (getattr(current_user, "phone", None) and getattr(dealer, "phone", None) and current_user.phone == dealer.phone)
        if not is_own:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xem giao dịch của chính mình."
            )

    tx_items = get_dealer_unified_orders(dealer_id=dealer_id, dealer=dealer, db=db)

    # Tính toán tổng tiền, đã thanh toán và chưa thanh toán
    total_amount = sum(t["total_amount"] for t in tx_items if t.get("status") not in ("CANCELLED", "REJECTED"))
    paid_amount = sum(t["total_amount"] for t in tx_items if t.get("status") == "PAID")
    unpaid_amount = sum(t["total_amount"] for t in tx_items if t.get("status") not in ("PAID", "CANCELLED", "REJECTED"))

    return {
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "total": len(tx_items),
        "total_transactions": len(tx_items),
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "unpaid_amount": unpaid_amount,
        "transactions": tx_items,
        "items": tx_items,
    }


@router.post("/{dealer_id}/receipt-vouchers", response_model=ReceiptVoucherResponse, status_code=status.HTTP_201_CREATED)
def create_dealer_receipt_voucher(
    dealer_id: int,
    payload: CreateReceiptVoucherRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "accountant",
        ])
    ),
):
    """
    Kế toán công nợ: Lập và ghi nhận phiếu thu tiền từ đại lý.
    Tự động sinh mã phiếu thu duy nhất, tính số tiền bằng chữ và ghi vết Audit Log.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý ID {dealer_id}."
        )

    if payload.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Số tiền thu phải lớn hơn 0."
        )

    now = datetime.now(timezone.utc)
    ts_code = now.strftime("%Y%m%d%H%M%S")
    rand_seq = len(RECEIPT_VOUCHERS_DB) + 1
    receipt_code = f"PT-{dealer.code}-{ts_code}-{rand_seq:02d}"

    words = number_to_vietnamese_words(payload.amount)
    payer = payload.payer_name.strip() if payload.payer_name and payload.payer_name.strip() else f"Đại diện {dealer.name}"
    collector = current_user.full_name or current_user.username
    collector_role_name = (
        "Kế toán công nợ"
        if current_user.role == "accountant"
        else ("Quản trị viên" if current_user.role == "admin" else "Quản lý kinh doanh")
    )

    voucher_data = {
        "id": rand_seq,
        "receipt_code": receipt_code,
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "dealer_address": dealer.address or "—",
        "dealer_phone": dealer.phone or "—",
        "dealer_tax_code": dealer.tax_code or "—",
        "order_code": payload.order_code.strip() if payload.order_code else None,
        "amount": float(payload.amount),
        "amount_in_words": words,
        "payment_method": payload.payment_method or "Tiền mặt",
        "reason": payload.reason.strip() if payload.reason else "Thu tiền thanh toán công nợ",
        "payer_name": payer,
        "collector_name": collector,
        "collector_role": collector_role_name,
        "created_at": now.isoformat(),
        "note": payload.note.strip() if payload.note else None,
    }

    RECEIPT_VOUCHERS_DB[receipt_code] = voucher_data
    save_receipt_vouchers_db()

    # 1. Lưu bản ghi vào SQL Server (ReceiptVoucherEntity)
    try:
        from app.models.receipt_voucher import ReceiptVoucherEntity
        rv_entity = ReceiptVoucherEntity(
            receipt_code=receipt_code,
            dealer_id=dealer.id,
            dealer_code=dealer.code,
            dealer_name=dealer.name,
            dealer_address=dealer.address,
            dealer_phone=dealer.phone,
            dealer_tax_code=dealer.tax_code,
            order_code=payload.order_code.strip() if payload.order_code else None,
            amount=float(payload.amount),
            amount_in_words=words,
            payment_method=payload.payment_method or "Tiền mặt",
            reason=voucher_data["reason"],
            payer_name=payer,
            collector_name=collector,
            collector_role=collector_role_name,
            created_at=now,
            note=voucher_data["note"],
        )
        db.add(rv_entity)
        db.commit()

        # 2. Tự động đồng bộ số dư công nợ trên SQL Server
        from app.services.invoice_service import resync_all_debts_sql
        resync_all_debts_sql(db)
    except Exception as dberr:
        logger.warning(f"Notice: SQL Server receipt voucher save notice: {dberr}")

    # Ghi Audit Log vết thao tác
    log_audit_event(
        db=db,
        user=current_user,
        action_type="DEBT_PAYMENT",
        entity_type="ReceiptVoucher",
        entity_id=receipt_code,
        old_val={},
        new_val={
            "receipt_code": receipt_code,
            "dealer_id": dealer.id,
            "dealer_code": dealer.code,
            "amount": payload.amount,
            "payment_method": payload.payment_method,
            "reason": voucher_data["reason"],
            "collector": collector,
        },
        reason=f"Kế toán lập phiếu thu {receipt_code} cho đại lý {dealer.name} ({payload.amount:,.0f} đ)",
    )

    return voucher_data


@router.get("/{dealer_id}/receipt-vouchers", response_model=List[ReceiptVoucherResponse])
def get_dealer_receipt_vouchers(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """Lấy danh sách các phiếu thu đã ghi nhận của đại lý từ SQL Server."""
    user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
    roles = {str(r).lower() for r in user_roles if r}
    is_customer = "customer" in roles and not any(r in ["admin", "sales_manager", "sales", "accountant"] for r in roles)
    if is_customer:
        load_dealers_db()
        dealer = DEALERS_DB.get(dealer_id)
        is_own = (current_user.id == dealer_id) or (dealer and (
                 (getattr(current_user, "username", "").lower() == getattr(dealer, "code", "").lower()) or \
                 (getattr(current_user, "email", None) and getattr(dealer, "email", None) and current_user.email.lower() == dealer.email.lower()) or \
                 (getattr(current_user, "phone", None) and getattr(dealer, "phone", None) and current_user.phone == dealer.phone)
        ))
        if not is_own:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xem phiếu thu của chính mình."
            )

    try:
        from app.models.receipt_voucher import ReceiptVoucherEntity
        sql_vouchers = db.query(ReceiptVoucherEntity).filter(
            ReceiptVoucherEntity.dealer_id == dealer_id
        ).order_by(ReceiptVoucherEntity.created_at.desc()).all()
        if sql_vouchers:
            return [
                {
                    "id": v.id,
                    "receipt_code": v.receipt_code,
                    "dealer_id": v.dealer_id,
                    "dealer_code": v.dealer_code,
                    "dealer_name": v.dealer_name,
                    "dealer_address": v.dealer_address or "—",
                    "dealer_phone": v.dealer_phone or "—",
                    "dealer_tax_code": v.dealer_tax_code or "—",
                    "order_code": v.order_code,
                    "amount": float(v.amount),
                    "amount_in_words": v.amount_in_words or "",
                    "payment_method": v.payment_method or "Tiền mặt",
                    "reason": v.reason or "",
                    "payer_name": v.payer_name or "",
                    "collector_name": v.collector_name or "",
                    "collector_role": v.collector_role or "",
                    "created_at": v.created_at.isoformat() if v.created_at else "",
                    "note": v.note,
                }
                for v in sql_vouchers
            ]
    except Exception:
        pass

    res = [v for v in RECEIPT_VOUCHERS_DB.values() if v.get("dealer_id") == dealer_id]
    res.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return res


@router.get("/receipt-vouchers/{receipt_code}", response_model=ReceiptVoucherResponse)
def get_receipt_voucher_by_code(
    receipt_code: str,
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """Xem chi tiết phiếu thu theo mã phiếu."""
    v = RECEIPT_VOUCHERS_DB.get(receipt_code)
    if not v:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu thu có mã {receipt_code}."
        )
    return v


@router.get("/filters")
def get_dealer_filters(
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """
    Trả về dữ liệu dùng cho các bộ lọc trên frontend:
    Khu vực, Người phụ trách, Nhóm khách hàng, Trạng thái.
    Đồng bộ trực tiếp từ phân quyền người dùng (RBAC).
    """
    load_users_db()
    load_dealers_db()

    regions = set()
    sales = {}
    customer_groups = set()
    statuses = set()

    for dealer in DEALERS_DB.values():
        # Khu vực
        reg = dealer.region or get_region(dealer.address)
        if reg and reg != "Chưa xác định":
            regions.add(reg)

        # Nhóm khách hàng
        grp = getattr(dealer, "customer_group", None)
        if grp:
            customer_groups.add(grp)

        # Trạng thái
        st = getattr(dealer, "status", None)
        if st:
            statuses.add(st)

    # Danh sách nhân viên kinh doanh thực tế phụ trách tuyến (ĐỒNG BỘ TỪ PHÂN QUYỀN RBAC):
    # Chỉ lấy tài khoản đang hoạt động (ACTIVE) và thực sự có vai trò 'sales' (Nhân viên kinh doanh).
    # Tuyệt đối không lấy vai trò quản lý (sales_manager), Admin hay các phòng ban khác.
    for u in USERS_DB.values():
        u_roles = u.get_roles() if hasattr(u, "get_roles") else [u.role]
        user_status = getattr(u, "status", "ACTIVE")
        if "sales" in u_roles and u.is_active and user_status == "ACTIVE":
            sales[u.id] = u.full_name

    # Đảm bảo có các khu vực phổ biến
    default_regions = [
        "Hà Nội",
        "TP. Hồ Chí Minh",
        "Đà Nẵng",
        "Hải Phòng",
        "Cần Thơ",
        "Bắc Ninh",
        "Bình Dương",
        "Đồng Nai",
        "Quảng Ninh",
        "Thái Nguyên",
        "Hải Dương",
        "Nam Định",
        "Nghệ An",
        "Khánh Hòa",
        "Lâm Đồng",
    ]
    for dr in default_regions:
        regions.add(dr)

    # Đảm bảo có các nhóm mặc định phổ biến
    default_groups = ["Đại lý cấp 1", "Đại lý cấp 2", "Khách sỉ", "Khách lẻ", "dai_ly_cap_1"]
    for dg in default_groups:
        customer_groups.add(dg)

    # Đảm bảo có các trạng thái mặc định
    default_statuses = ["Đang hoạt động", "Tạm ngừng", "Ngừng giao dịch", "Chưa kích hoạt"]
    for ds in default_statuses:
        statuses.add(ds)

    return {
        "regions": sorted(regions),
        "sales": [
            {
                "id": sale_id,
                "name": sale_name,
            }
            for sale_id, sale_name in sorted(
                sales.items(),
                key=lambda item: item[1]
            )
        ],
        "customer_groups": sorted(customer_groups),
        "statuses": sorted(statuses),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_dealer(
    payload: DealerCreateRequest,
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """
    Tạo mới đại lý / khách hàng vào tuyến phụ trách.
    Yêu cầu quyền: admin, sales_manager, sales, accountant.
    """
    name_clean = payload.name.strip()
    if not name_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tên đại lý / khách hàng không được để trống",
        )

    load_dealers_db()

    # Kiểm tra trùng tên đại lý / khách hàng (không phân biệt hoa thường)
    for d in DEALERS_DB.values():
        if d.name and d.name.strip().lower() == name_clean.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tên đại lý / khách hàng '{name_clean}' đã tồn tại trên hệ thống. Vui lòng đặt tên khác.",
            )

    # Sinh mã nếu chưa có
    new_id = max(DEALERS_DB.keys(), default=0) + 1
    code_clean = payload.code.strip().upper() if payload.code and payload.code.strip() else f"DL{new_id:03d}"

    # Kiểm tra trùng mã (Mã đại lý là duy nhất)
    clean_code_norm = code_clean.replace("-", "").replace(" ", "").replace("_", "")
    for d in DEALERS_DB.values():
        d_norm = d.code.upper().replace("-", "").replace(" ", "").replace("_", "")
        if d.code.upper() == code_clean or d_norm == clean_code_norm:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Mã đại lý '{code_clean}' đã tồn tại trên hệ thống. Mã đại lý phải là duy nhất.",
            )

    # Kiểm tra trùng số điện thoại (nếu có cung cấp)
    phone_clean = payload.phone.strip() if payload.phone else None
    if phone_clean:
        norm_phone = phone_clean.replace(" ", "").replace(".", "").replace("-", "")
        if norm_phone.startswith("+84"):
            norm_phone = "0" + norm_phone[3:]
        elif norm_phone.startswith("84") and len(norm_phone) == 11:
            norm_phone = "0" + norm_phone[2:]
        for d in DEALERS_DB.values():
            if d.phone:
                dp = d.phone.strip().replace(" ", "").replace(".", "").replace("-", "")
                if dp.startswith("+84"):
                    dp = "0" + dp[3:]
                elif dp.startswith("84") and len(dp) == 11:
                    dp = "0" + dp[2:]
                if dp == norm_phone:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Số điện thoại '{phone_clean}' đã được sử dụng cho một đại lý/khách hàng khác ({d.name}).",
                    )

    assigned_sale = payload.assigned_sale_id
    if assigned_sale is None and current_user.role == "sales":
        assigned_sale = current_user.id

    if assigned_sale is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bắt buộc phải chỉ định nhân viên kinh doanh (Sales) phụ trách đại lý / khách hàng.",
        )

    load_users_db()
    sale_user = next((u for u in USERS_DB.values() if u.id == assigned_sale), None)
    if not sale_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nhân viên phụ trách với ID {assigned_sale} không tồn tại trên hệ thống.",
        )
    sale_roles = sale_user.get_roles() if hasattr(sale_user, "get_roles") else [sale_user.role]
    if "sales" not in sale_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Người dùng '{sale_user.full_name}' có vai trò là {sale_user.role}, không phải Nhân viên kinh doanh ('sales'). Vui lòng chọn đúng nhân viên bán hàng được phân quyền.",
        )
    if not sale_user.is_active or getattr(sale_user, "status", "ACTIVE") != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tài khoản của nhân viên kinh doanh '{sale_user.full_name}' đang bị khóa hoặc ngừng hoạt động.",
        )

    reg = payload.region.strip() if payload.region and payload.region.strip() else get_region(payload.address)

    tx_cnt = 0
    if payload.transaction_count is not None:
        if payload.transaction_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Số lượng giao dịch phải lớn hơn hoặc bằng 0.",
            )
        tx_cnt = payload.transaction_count

    new_dealer = Dealer(
        id=new_id,
        code=code_clean,
        name=name_clean,
        tax_code=payload.tax_code.strip() if payload.tax_code and payload.tax_code.strip() else None,
        phone=payload.phone.strip() if payload.phone else None,
        email=payload.email.strip() if payload.email else None,
        address=payload.address.strip() if payload.address else None,
        region=reg,
        assigned_sale_id=assigned_sale,
        credit_limit=payload.credit_limit or 50000000.0,
        max_debt_days=payload.overdue_days_allowed or 30,
        overdue_days_allowed=payload.overdue_days_allowed or 30,
        customer_group=payload.customer_group or "Đại lý cấp 1",
        status=payload.status or "Đang hoạt động",
        transaction_count=tx_cnt,
        warehouse_id=payload.warehouse_id,
        warehouse_name=payload.warehouse_name,
    )

    DEALERS_DB[new_id] = new_dealer
    save_dealers_db()

    if new_dealer.address:
        try:
            from app.core.database import SessionLocal
            from app.models.entities import DealerDeliveryPointEntity
            from app.api.v1.endpoints.delivery_points import _sync_to_master
            import re
            db_s = SessionLocal()
            try:
                clean_p = (new_dealer.phone or "0987654321").replace(" ", "").replace(".", "").replace("-", "")
                if not re.fullmatch(r"^\d{10}$", clean_p):
                    clean_p = "0987654321"
                dp = DealerDeliveryPointEntity(
                    dealer_id=new_id,
                    label="Địa chỉ đăng ký đại lý",
                    address=new_dealer.address,
                    receiver_name=new_dealer.name,
                    receiver_phone=clean_p,
                    route_note="Địa chỉ chính thức của đại lý",
                    is_default=True,
                    is_active=True,
                )
                db_s.add(dp)
                _sync_to_master(db_s, dp.label, dp.address, dp.receiver_name, dp.receiver_phone, dp.route_note)
                db_s.commit()
            finally:
                db_s.close()
        except Exception:
            pass

    applied_pb = get_applied_price_book_info(new_dealer.customer_group)

    return {
        "id": new_dealer.id,
        "code": new_dealer.code,
        "name": new_dealer.name,
        "tax_code": getattr(new_dealer, "tax_code", None),
        "phone": new_dealer.phone,
        "email": new_dealer.email,
        "address": new_dealer.address,
        "region": reg,
        "credit_limit": new_dealer.credit_limit,
        "assigned_sale_id": new_dealer.assigned_sale_id,
        "assigned_sale_name": get_sale_name(new_dealer.assigned_sale_id),
        "customer_group": new_dealer.customer_group,
        "status": new_dealer.status,
        "transaction_count": tx_cnt,
        "has_transactions": tx_cnt > 0,
        "applied_price_book": applied_pb,
        "warehouse_id": new_dealer.warehouse_id,
        "warehouse_name": new_dealer.warehouse_name,
    }


@router.get("/{dealer_id}/credit-info", response_model=DealerCreditInfoResponse)
def get_dealer_credit_info_detail(
    dealer_id: int,
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "sales", "customer"]))
):
    """
    AC 4 & AC 1: API lấy thông tin công nợ chi tiết của Đại lý phục vụ kiểm soát khi lên đơn.
    Bảo vệ quyền truy cập: Chỉ cho phép Nhân viên kinh doanh (Sales), Quản trị hệ thống (Admin).
    """
    from app.api.v1.endpoints.orders import ORDERS_DB, calculate_dealer_debt_summary
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )
    if is_customer:
        customer_dealer = DEALERS_DB.get(current_user.id)
        if not customer_dealer:
            for d in DEALERS_DB.values():
                if (d.email and current_user.email and d.email.lower() == current_user.email.lower()) or \
                   (d.phone and current_user.phone and d.phone == current_user.phone) or \
                   (d.code and current_user.username and d.code.lower() == current_user.username.lower()) or \
                   (d.name and current_user.full_name and d.name.lower() == current_user.full_name.lower()):
                    customer_dealer = d
                    break
        if not customer_dealer or customer_dealer.id != dealer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xem thông tin công nợ của chính mình."
            )
    return calculate_dealer_debt_summary(dealer, ORDERS_DB)


@router.put("/{dealer_id}")
def update_dealer_profile(
    dealer_id: int,
    payload: DealerUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """
    Cập nhật toàn diện hồ sơ đại lý (Mã đại lý, tên, MST, nhóm KH, khu vực, người phụ trách, trạng thái).
    Quyền: admin, sales_manager, accountant, sales (phụ trách).
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    if "admin" not in user_roles and "sales_manager" not in user_roles and "accountant" not in user_roles:
        if dealer.assigned_sale_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn chỉ có quyền chỉnh sửa hồ sơ đại lý do bạn trực tiếp phụ trách."
            )

    # Kiểm tra mã đại lý là duy nhất nếu thay đổi mã
    if payload.code and payload.code.strip():
        new_code = payload.code.strip().upper()
        new_code_norm = new_code.replace("-", "").replace(" ", "").replace("_", "")
        if new_code != dealer.code.upper():
            for d in DEALERS_DB.values():
                d_norm = d.code.upper().replace("-", "").replace(" ", "").replace("_", "")
                if d.id != dealer_id and (d.code.upper() == new_code or d_norm == new_code_norm):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Mã đại lý '{new_code}' đã tồn tại trên hệ thống. Mã đại lý phải là duy nhất.",
                    )
            dealer.code = new_code

    old_info = {"code": dealer.code, "name": dealer.name, "status": dealer.status}

    if payload.name is not None and payload.name.strip():
        dealer.name = payload.name.strip()
    if payload.tax_code is not None:
        dealer.tax_code = payload.tax_code.strip() if payload.tax_code.strip() else None
    if payload.phone is not None:
        dealer.phone = payload.phone.strip() if payload.phone.strip() else None
    if payload.email is not None:
        dealer.email = payload.email.strip() if payload.email.strip() else None
    if payload.address is not None:
        dealer.address = payload.address.strip() if payload.address.strip() else None
        if dealer.address:
            try:
                from app.models.entities import DealerDeliveryPointEntity
                from app.api.v1.endpoints.delivery_points import _sync_to_master
                import re
                clean_p = (dealer.phone or "0987654321").replace(" ", "").replace(".", "").replace("-", "")
                if not re.fullmatch(r"^\d{10}$", clean_p):
                    clean_p = "0987654321"
                dp = db.query(DealerDeliveryPointEntity).filter(
                    DealerDeliveryPointEntity.dealer_id == dealer.id,
                    DealerDeliveryPointEntity.is_default == True,
                    DealerDeliveryPointEntity.is_active == True,
                ).first()
                if dp:
                    dp.address = dealer.address
                    if not dp.receiver_name:
                        dp.receiver_name = dealer.name
                    if not dp.receiver_phone:
                        dp.receiver_phone = clean_p
                    _sync_to_master(db, dp.label, dp.address, dp.receiver_name, dp.receiver_phone, dp.route_note)
                else:
                    dp = DealerDeliveryPointEntity(
                        dealer_id=dealer.id,
                        label="Địa chỉ đăng ký đại lý",
                        address=dealer.address,
                        receiver_name=dealer.name,
                        receiver_phone=clean_p,
                        route_note="Địa chỉ chính thức của đại lý",
                        is_default=True,
                        is_active=True,
                    )
                    db.add(dp)
                    _sync_to_master(db, dp.label, dp.address, dp.receiver_name, dp.receiver_phone, dp.route_note)
                db.commit()
            except Exception as sync_err:
                print(f"Lỗi đồng bộ điểm giao hàng khi cập nhật hồ sơ đại lý: {sync_err}")
    if payload.region is not None and payload.region.strip():
        dealer.region = payload.region.strip()
    if payload.assigned_sale_id is not None:
        dealer.assigned_sale_id = payload.assigned_sale_id
    if payload.customer_group is not None and payload.customer_group.strip():
        dealer.customer_group = payload.customer_group.strip()
    if payload.status is not None and payload.status.strip():
        valid_statuses = ["Đang hoạt động", "Tạm ngừng", "Ngừng giao dịch", "Chưa kích hoạt"]
        if payload.status.strip() in valid_statuses:
            dealer.status = payload.status.strip()
    if payload.credit_limit is not None:
        dealer.credit_limit = payload.credit_limit
    if payload.overdue_days_allowed is not None:
        dealer.overdue_days_allowed = payload.overdue_days_allowed
        dealer.max_debt_days = payload.overdue_days_allowed
    elif payload.max_debt_days is not None:
        dealer.max_debt_days = payload.max_debt_days
        dealer.overdue_days_allowed = payload.max_debt_days
    if payload.transaction_count is not None:
        if payload.transaction_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Số lượng giao dịch phải lớn hơn hoặc bằng 0.",
            )
        dealer.transaction_count = payload.transaction_count
    if payload.warehouse_id is not None:
        dealer.warehouse_id = payload.warehouse_id
    if payload.warehouse_name is not None:
        dealer.warehouse_name = payload.warehouse_name

    save_dealers_db()

    # Đồng bộ vào database DealerEntity nếu có
    try:
        from app.models.entities import DealerEntity
        db_dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer.id).first()
        if db_dealer:
            if payload.name: db_dealer.name = dealer.name
            if payload.phone: db_dealer.phone = dealer.phone
            if payload.email: db_dealer.email = dealer.email
            if payload.address: db_dealer.address = dealer.address
            if payload.warehouse_id is not None: db_dealer.warehouse_id = dealer.warehouse_id
            if payload.warehouse_name is not None: db_dealer.warehouse_name = dealer.warehouse_name
            db.commit()
    except Exception:
        pass

    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="DEALER_PROFILE_UPDATE",
            entity_type="Dealer",
            entity_id=dealer.code,
            old_val=old_info,
            new_val={
                "code": dealer.code,
                "name": dealer.name,
                "tax_code": getattr(dealer, "tax_code", None),
                "customer_group": dealer.customer_group,
                "status": dealer.status,
                "transaction_count": getattr(dealer, "transaction_count", 0),
                "warehouse_id": getattr(dealer, "warehouse_id", None),
                "warehouse_name": getattr(dealer, "warehouse_name", None),
            },
            reason="Cập nhật hồ sơ đại lý",
            request=request,
        )
    except Exception as e:
        print(f"Lỗi ghi audit log DEALER_PROFILE_UPDATE: {e}")

    from app.api.v1.endpoints.orders import ORDERS_DB
    order_tx = sum(1 for o in ORDERS_DB.values() if o.get("dealer_id") == dealer.id)
    manual_tx = getattr(dealer, "transaction_count", 0) or 0
    tx_count = max(manual_tx, order_tx)
    return {
        "id": dealer.id,
        "code": dealer.code,
        "name": dealer.name,
        "tax_code": getattr(dealer, "tax_code", None),
        "phone": dealer.phone,
        "email": dealer.email,
        "address": dealer.address,
        "region": dealer.region or get_region(dealer.address),
        "assigned_sale_id": dealer.assigned_sale_id,
        "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
        "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
        "status": dealer.status,
        "transaction_count": tx_count,
        "has_transactions": tx_count > 0,
        "applied_price_book": get_applied_price_book_info(dealer.customer_group),
        "warehouse_id": getattr(dealer, "warehouse_id", None),
        "warehouse_name": getattr(dealer, "warehouse_name", None),
    }


@router.patch("/{dealer_id}/status")
@router.put("/{dealer_id}/status")
def update_dealer_status(
    dealer_id: int,
    payload: DealerStatusUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """
    Cập nhật trạng thái đại lý / khách hàng ('Đang hoạt động' <-> 'Ngừng giao dịch' / 'Tạm ngừng').
    Quyền: admin, sales_manager, accountant, hoặc nhân viên kinh doanh (sales) phụ trách đại lý.
    Tự động ghi vết vào bảng audit_logs với action_type='DEALER_STATUS_CHANGE'.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý / khách hàng có ID {dealer_id}."
        )

    # Ràng buộc phân quyền Zero-Trust: Sales chỉ được đổi trạng thái đại lý do mình phụ trách
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    if "admin" not in user_roles and "sales_manager" not in user_roles and "accountant" not in user_roles:
        if dealer.assigned_sale_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn chỉ có quyền thay đổi trạng thái của đại lý/khách hàng do bạn trực tiếp phụ trách."
            )

    new_status = payload.status.strip()
    if new_status not in ["Đang hoạt động", "Tạm ngừng", "Ngừng giao dịch", "Chưa kích hoạt", "Đã khóa"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trạng thái chỉ có thể là 'Đang hoạt động', 'Ngừng giao dịch', 'Tạm ngừng', 'Chưa kích hoạt' hoặc 'Đã khóa'."
        )

    clean_reason = payload.reason.strip() if payload.reason and payload.reason.strip() else None
    utc_now_str = datetime.now(timezone.utc).isoformat()

    if new_status == "Đã khóa":
        if not clean_reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lý do khóa giao dịch là bắt buộc khi chuyển sang trạng thái 'Đã khóa'."
            )
        dealer.lock_reason = clean_reason
        dealer.locked_at = utc_now_str
        dealer.locked_by = current_user.username
    elif new_status == "Đang hoạt động":
        dealer.lock_reason = None
        dealer.locked_at = None
        dealer.locked_by = None
    else:
        if clean_reason:
            dealer.lock_reason = clean_reason

    old_status = getattr(dealer, "status", "Đang hoạt động")
    dealer.status = new_status
    save_dealers_db()

    # Ghi nhật ký thao tác kiểm toán hệ thống
    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="DEALER_STATUS_CHANGE",
            entity_type="Dealer",
            entity_id=dealer.code,
            old_val={"status": old_status, "name": dealer.name},
            new_val={
                "status": dealer.status,
                "lock_reason": getattr(dealer, "lock_reason", None),
                "locked_at": getattr(dealer, "locked_at", None),
                "locked_by": getattr(dealer, "locked_by", None),
            },
            reason=clean_reason or payload.reason or f"Chuyển trạng thái sang '{new_status}'",
            request=request,
        )
    except Exception as e:
        print(f"Lỗi ghi nhật ký audit log thay đổi trạng thái đại lý: {e}")

    return {
        "id": dealer.id,
        "code": dealer.code,
        "name": dealer.name,
        "phone": dealer.phone,
        "email": dealer.email,
        "address": dealer.address,
        "region": dealer.region or get_region(dealer.address),
        "credit_limit": getattr(dealer, "credit_limit", 50000000.0),
        "assigned_sale_id": dealer.assigned_sale_id,
        "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
        "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
        "status": dealer.status,
        "lock_reason": getattr(dealer, "lock_reason", None),
        "locked_at": getattr(dealer, "locked_at", None),
        "locked_by": getattr(dealer, "locked_by", None),
    }


class DealerAssignRequest(BaseModel):
    assigned_sale_id: int
    reason: Optional[str] = None


@router.put("/{dealer_id}/assign")
@router.patch("/{dealer_id}/assign")
def assign_dealer(
    dealer_id: int,
    payload: DealerAssignRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager"])
    ),
):
    """
    Phân công 1 đại lý cho nhân viên kinh doanh khác.
    """
    load_dealers_db()
    load_users_db()
    
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý có ID {dealer_id}."
        )

    new_sale_id = payload.assigned_sale_id
    new_sale_user = next((u for u in USERS_DB.values() if u.id == new_sale_id), None)
    if not new_sale_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nhân viên ID {new_sale_id} không tồn tại."
        )
        
    sale_roles = new_sale_user.get_roles() if hasattr(new_sale_user, "get_roles") else [new_sale_user.role]
    if "sales" not in sale_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Người dùng '{new_sale_user.full_name}' không phải là nhân viên kinh doanh (sales)."
        )

    old_sale_id = dealer.assigned_sale_id
    if old_sale_id == new_sale_id:
        return {"message": "Nhân viên này đang phụ trách đại lý, không có sự thay đổi."}

    old_sale_name = get_sale_name(old_sale_id) or "Chưa phân công"
    dealer.assigned_sale_id = new_sale_id
    save_dealers_db()

    # Ghi log lịch sử phân công
    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="DEALER_ASSIGNMENT",
            entity_type="Dealer",
            entity_id=dealer.code,
            old_val={"assigned_sale_id": old_sale_id, "assigned_sale_name": old_sale_name},
            new_val={"assigned_sale_id": new_sale_id, "assigned_sale_name": new_sale_user.full_name},
            reason=(payload.reason or "").strip() or "Chuyển giao đại lý",
            request=request,
        )
    except Exception as e:
        print(f"Lỗi ghi audit log DEALER_ASSIGNMENT: {e}")

    return {
        "id": dealer.id,
        "code": dealer.code,
        "name": dealer.name,
        "assigned_sale_id": dealer.assigned_sale_id,
        "assigned_sale_name": new_sale_user.full_name,
        "message": "Phân công thành công"
    }


class BulkAssignRequest(BaseModel):
    dealer_ids: List[int]
    new_sale_id: int
    reason: Optional[str] = None


@router.post("/bulk-assign")
def bulk_assign_dealers(
    payload: BulkAssignRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager"])
    ),
):
    """
    Chuyển giao hàng loạt đại lý sang nhân viên mới.
    """
    load_dealers_db()
    load_users_db()

    new_sale_id = payload.new_sale_id
    new_sale_user = next((u for u in USERS_DB.values() if u.id == new_sale_id), None)
    if not new_sale_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nhân viên ID {new_sale_id} không tồn tại."
        )

    sale_roles = new_sale_user.get_roles() if hasattr(new_sale_user, "get_roles") else [new_sale_user.role]
    if "sales" not in sale_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Người dùng '{new_sale_user.full_name}' không phải là nhân viên kinh doanh (sales)."
        )

    assigned_count = 0
    errors = []

    # Bắt đầu xử lý (DB in-memory thì modify trực tiếp, ghi DB SQL bên trong save_dealers_db)
    # Tuy nhiên vì save_dealers_db ghi toàn bộ, ta chỉ gọi save_dealers_db 1 lần sau khi đổi hết
    try:
        for did in payload.dealer_ids:
            dealer = DEALERS_DB.get(did)
            if not dealer:
                errors.append(f"Không tìm thấy đại lý ID {did}")
                continue
                
            old_sale_id = dealer.assigned_sale_id
            if old_sale_id == new_sale_id:
                continue
                
            old_sale_name = get_sale_name(old_sale_id) or "Chưa phân công"
            dealer.assigned_sale_id = new_sale_id
            
            # Ghi log lịch sử cho từng đại lý
            try:
                log_audit_event(
                    db=db,
                    user=current_user,
                    action_type="DEALER_ASSIGNMENT",
                    entity_type="Dealer",
                    entity_id=dealer.code,
                    old_val={"assigned_sale_id": old_sale_id, "assigned_sale_name": old_sale_name},
                    new_val={"assigned_sale_id": new_sale_id, "assigned_sale_name": new_sale_user.full_name},
                    reason=(payload.reason or "").strip() or "Chuyển giao hàng loạt",
                    request=request,
                )
            except Exception as e:
                print(f"Lỗi ghi audit log bulk DEALER_ASSIGNMENT cho {did}: {e}")
                
            assigned_count += 1

        if assigned_count > 0:
            save_dealers_db()

    except Exception as ex:
        # In-memory rollback strategy is tricky without full deepcopy, but normally simple properties won't throw halfway.
        # Fallback reload from DB to cancel unsaved changes
        load_dealers_db()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi chuyển giao: {ex}"
        )

    return {
        "message": f"Đã chuyển giao thành công {assigned_count} đại lý cho nhân viên {new_sale_user.full_name}." if assigned_count > 0 else "Các đại lý đã chọn đều đã do nhân viên này phụ trách.",
        "assigned_count": assigned_count,
        "errors": errors if errors else None
    }


@router.delete("/{dealer_id}")
def delete_dealer(
    dealer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "accountant"])),
):
    """
    Xóa đại lý/khách hàng khỏi hệ thống (Chỉ khi CHƯA phát sinh giao dịch).
    Đại lý đã phát sinh giao dịch thì không xoá được, chỉ ngừng giao dịch.
    """
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý ID {dealer_id}."
        )

    # Kiểm tra xem có giao dịch (đơn hàng) phát sinh không
    total_orders = 0
    try:
        from app.models.entities import OrderEntity
        total_orders = db.query(OrderEntity).filter(OrderEntity.dealer_id == dealer_id).count()
    except Exception:
        pass

    try:
        from app.api.v1.endpoints.orders import ORDERS_DB
        orders_db_cnt = sum(1 for o in ORDERS_DB.values() if o.get("dealer_id") == dealer_id)
        total_orders = max(total_orders, orders_db_cnt)
    except Exception:
        pass

    manual_tx = getattr(dealer, "transaction_count", 0) or 0
    total_orders = max(total_orders, manual_tx)

    if total_orders > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đại lý '{dealer.name}' ({dealer.code}) đã phát sinh {total_orders} giao dịch trong hệ thống, không thể xóa. Theo quy định kiểm toán và kế toán, chỉ được phép chuyển sang trạng thái 'Ngừng giao dịch'.",
        )

    # 1. Xóa các điểm giao hàng của đại lý nếu có
    try:
        from app.models.entities import DealerEntity, DealerDeliveryPointEntity
        db.query(DealerDeliveryPointEntity).filter(DealerDeliveryPointEntity.dealer_id == dealer_id).delete(synchronize_session=False)
        # 2. Xóa trong SQL
        db.query(DealerEntity).filter(DealerEntity.id == dealer_id).delete(synchronize_session=False)
        db.commit()
    except Exception as db_del_err:
        db.rollback()
        logger.warning(f"Database deletion bypassed: {db_del_err}")

    # Ghi log thao tác
    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="DEALER_DELETE",
            entity_type="Dealer",
            entity_id=dealer.code,
            old_val={"id": dealer.id, "code": dealer.code, "name": dealer.name},
            new_val=None,
            reason="Xóa đại lý khỏi hệ thống",
            request=request,
        )
    except Exception as e:
        print(f"Lỗi ghi log DEALER_DELETE: {e}")

    # Xóa khỏi DEALERS_DB in-memory và sync file json
    del DEALERS_DB[dealer_id]
    save_dealers_db()

    return {"status": "success", "message": f"Đã xóa đại lý {dealer.name} ({dealer.code}) thành công."}


@router.get("")
@router.get("/")
def get_dealers(
    search: Optional[str] = Query(default=None, description="Tìm nhanh theo mã đại lý, tên hoặc số điện thoại"),
    keyword: Optional[str] = Query(default=None, description="Tương đương search"),
    page: Optional[int] = Query(default=None, ge=1, description="Trang hiện tại (phân trang)"),
    page_size: Optional[int] = Query(default=None, ge=1, description="Số lượng mỗi trang (mặc định 10)"),
    limit: Optional[int] = Query(default=None, ge=1, description="Số lượng bản ghi (tương đương page_size)"),
    skip: Optional[int] = Query(default=None, ge=0, description="Bỏ qua số bản ghi"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "sales", "accountant", "customer", "warehouse_manager", "warehouse"]))
):
    """Lấy danh sách đại lý (hỗ trợ tìm kiếm, phân trang cuộn vô tận hoặc trả về toàn bộ nếu không truyền param)."""
    load_dealers_db()
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    search_query = (search or keyword or "").strip()
    is_paginated = page is not None or page_size is not None or limit is not None or skip is not None

    sql_orders_by_dealer, payments_by_dealer, adjustments_by_dealer, now = prefetch_dealer_debt_context(db)

    if is_customer:
        customer_dealer = DEALERS_DB.get(current_user.id)
        if not customer_dealer:
            for d in DEALERS_DB.values():
                if (d.email and d.email == current_user.email) or \
                   (d.phone and d.phone == current_user.phone) or \
                   (d.code and d.code.lower() == current_user.username.lower()) or \
                   (d.name and d.name.lower() == (current_user.full_name or "").lower()):
                    customer_dealer = d
                    break
        if not customer_dealer:
            if is_paginated:
                return {"items": [], "dealers": [], "total": 0, "page": page or 1, "page_size": page_size or 10, "has_more": False}
            return []

        dealer_region = customer_dealer.region or get_region(customer_dealer.address)
        c_limit = float(getattr(customer_dealer, "credit_limit", 50000000.0) or 50000000.0)
        c_days = int(getattr(customer_dealer, "max_debt_days", 30) or 30)

        cust_orders = get_dealer_unified_orders(
            customer_dealer.id,
            dealer=customer_dealer,
            db=db,
            preloaded_sql_orders=sql_orders_by_dealer.get(customer_dealer.id)
        )
        d_pay = payments_by_dealer.get(customer_dealer.id, {"by_order": {}, "unallocated": 0.0})
        d_adj = adjustments_by_dealer.get(customer_dealer.id, {})
        current_debt, max_debt_age = compute_dealer_unified_debt(
            customer_dealer.id,
            cust_orders,
            d_pay["by_order"],
            d_pay["unallocated"],
            d_adj,
            now,
        )

        debt_status = "Bình thường"
        if current_debt > c_limit:
            debt_status = "Vượt hạn mức"
        elif current_debt > 0 and max_debt_age > c_days:
            debt_status = "Quá hạn thanh toán"
        elif current_debt > 0:
            debt_status = "Trong hạn mức"
        
        single_item = {
            "id": customer_dealer.id,
            "code": customer_dealer.code,
            "name": customer_dealer.name,
            "phone": customer_dealer.phone,
            "email": customer_dealer.email,
            "address": customer_dealer.address,
            "region": dealer_region,
            "assigned_sale_id": customer_dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(customer_dealer.assigned_sale_id),
            "credit_limit": c_limit,
            "max_debt_days": c_days,
            "current_debt": current_debt,
            "debt_status": debt_status,
            "customer_group": getattr(customer_dealer, "customer_group", "Đại lý cấp 1"),
            "status": normalize_dealer_status(getattr(customer_dealer, "status", "Đang hoạt động")),
            "lock_reason": None,
            "locked_at": None,
            "locked_by": None,
            "transaction_count": len(cust_orders),
            "has_transactions": len(cust_orders) > 0,
            "applied_price_book": get_applied_price_book_info(getattr(customer_dealer, "customer_group", "Đại lý cấp 1")),
            "warehouse_id": getattr(customer_dealer, "warehouse_id", None),
            "warehouse_name": getattr(customer_dealer, "warehouse_name", None),
        }

        if search_query:
            clean_kw = search_query.lower()
            match = clean_kw in (single_item.get("code") or "").lower() or \
                    clean_kw in (single_item.get("name") or "").lower() or \
                    clean_kw in (single_item.get("phone") or "").lower()
            if not match:
                if is_paginated:
                    return {"items": [], "dealers": [], "total": 0, "page": page or 1, "page_size": page_size or 10, "has_more": False}
                return []

        if is_paginated:
            return {"items": [single_item], "dealers": [single_item], "total": 1, "page": page or 1, "page_size": page_size or 10, "has_more": False}
        return [single_item]

    res = []
    for d in DEALERS_DB.values():
        dealer_orders = get_dealer_unified_orders(
            d.id,
            dealer=d,
            db=db,
            preloaded_sql_orders=sql_orders_by_dealer.get(d.id)
        )
        d_pay = payments_by_dealer.get(d.id, {"by_order": {}, "unallocated": 0.0})
        d_adj = adjustments_by_dealer.get(d.id, {})
        current_debt, max_debt_age = compute_dealer_unified_debt(
            d.id,
            dealer_orders,
            d_pay["by_order"],
            d_pay["unallocated"],
            d_adj,
            now,
        )

        c_limit = getattr(d, "credit_limit", 50000000.0)
        c_days = getattr(d, "max_debt_days", 30)
        if current_debt > c_limit and max_debt_age > c_days:
            debt_status = "Vượt hạn mức & Quá hạn"
        elif current_debt > c_limit:
            debt_status = "Vượt hạn mức"
        elif current_debt > 0 and max_debt_age > c_days:
            debt_status = "Quá hạn thanh toán"
        elif current_debt > 0:
            debt_status = "Trong hạn mức"
        else:
            debt_status = "Bình thường"

        tx_count = len(dealer_orders)
        applied_pb = get_applied_price_book_info(getattr(d, "customer_group", "Đại lý cấp 1"))
        res.append({
            "id": d.id,
            "code": d.code,
            "name": d.name,
            "tax_code": getattr(d, "tax_code", None),
            "phone": d.phone,
            "email": d.email,
            "address": d.address,
            "region": getattr(d, "region", None) or get_region(d.address),
            "assigned_sale_id": d.assigned_sale_id,
            "assigned_sale_name": get_sale_name(d.assigned_sale_id),
            "credit_limit": getattr(d, "credit_limit", 50000000.0),
            "max_debt_days": getattr(d, "max_debt_days", 30),
            "current_debt": current_debt,
            "debt_status": debt_status,
            "customer_group": getattr(d, "customer_group", "Đại lý cấp 1"),
            "status": normalize_dealer_status(getattr(d, "status", "Đang hoạt động")),
            "lock_reason": getattr(d, "lock_reason", None),
            "locked_at": getattr(d, "locked_at", None),
            "locked_by": getattr(d, "locked_by", None),
            "transaction_count": tx_count,
            "has_transactions": tx_count > 0,
            "applied_price_book": applied_pb,
            "warehouse_id": getattr(d, "warehouse_id", None),
            "warehouse_name": getattr(d, "warehouse_name", None),
        })

    # Lọc theo từ khóa tìm kiếm nếu có
    if search_query:
        clean_kw = search_query.lower()
        clean_digits = "".join(ch for ch in clean_kw if ch.isdigit())
        filtered = []
        for d in res:
            code_match = clean_kw in (d.get("code") or "").lower()
            name_match = clean_kw in (d.get("name") or "").lower()
            tax_match = clean_kw in (d.get("tax_code") or "").lower()
            phone_str = d.get("phone") or ""
            phone_match = clean_kw in phone_str.lower()
            if not phone_match and clean_digits and len(clean_digits) >= 3:
                phone_match = clean_digits in "".join(ch for ch in phone_str if ch.isdigit())
            if code_match or name_match or tax_match or phone_match:
                filtered.append(d)
        res = filtered

    # Sắp xếp đại lý mới nhất lên đầu
    res.sort(key=lambda d: d.get("id") or 0, reverse=True)
    total_count = len(res)

    if is_paginated:
        ps = limit or page_size or 10
        if skip is not None:
            start_idx = skip
            p = (skip // ps) + 1
        else:
            p = page or 1
            start_idx = (p - 1) * ps
        end_idx = start_idx + ps
        paged_items = res[start_idx:end_idx]
        has_more = end_idx < total_count
        return {
            "items": paged_items,
            "dealers": paged_items,
            "total": total_count,
            "page": p,
            "page_size": ps,
            "has_more": has_more,
        }

    return res


@router.get("/{dealer_id}/purchase-history")
def get_dealer_purchase_history_alias(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    from app.api.v1.endpoints.orders import get_dealer_purchase_history
    return get_dealer_purchase_history(dealer_id=dealer_id, db=db, current_user=current_user)


# Credit Policy & Payment aliases under /api/v1/dealers
from app.schemas.credit_policy import (
    CreditPolicyConfigResponse,
    CreditPolicyConfigUpdate,
    DealerPaymentCreate,
    DealerPaymentResponse,
)
from app.services.credit_policy_service import (
    get_credit_policy_config,
    update_credit_policy_config,
    evaluate_dealer_credit_flags,
    evaluate_all_dealers_credit_flags,
    record_dealer_payment,
)

@router.get("/credit-policy/config", response_model=CreditPolicyConfigResponse)
def get_dealer_credit_policy_config(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["accountant", "admin", "sales_manager"]))
):
    return get_credit_policy_config(db)

@router.put("/credit-policy/config", response_model=CreditPolicyConfigResponse)
def update_dealer_credit_policy_config(
    payload: CreditPolicyConfigUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["accountant", "sales_manager", "admin"]))
):
    return update_credit_policy_config(db=db, data=payload, user=current_user, request=request)

@router.post("/credit-policy/evaluate")
def evaluate_dealers_credit_policy(
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["accountant", "sales_manager", "admin"]))
):
    results = evaluate_all_dealers_credit_flags(db=db, user=current_user, request=request)
    return {
        "status": "success",
        "message": f"Đã hoàn tất đánh giá công nợ cho {len(results)} đại lý.",
        "dealers_evaluated": results,
    }

@router.post("/{dealer_id}/credit-policy/evaluate")
def evaluate_single_dealer_credit_policy(
    dealer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["accountant", "sales_manager", "admin"]))
):
    res = evaluate_dealer_credit_flags(dealer_id=dealer_id, db=db, user=current_user, reason_prefix="Đánh giá theo yêu cầu", request=request)
    return {
        "status": "success",
        "message": f"Đã đánh giá công nợ cho đại lý {res.get('dealer_name')}.",
        "result": res,
    }

@router.post("/{dealer_id}/payments", response_model=DealerPaymentResponse)
def record_dealer_payment_endpoint(
    dealer_id: int,
    payload: DealerPaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["accountant", "admin"]))
):
    return record_dealer_payment(
        dealer_id=dealer_id,
        data=payload,
        db=db,
        user=current_user,
        request=request
    )


@router.get("/debt-ledger")
def get_dealer_debt_ledger_endpoint(
    region: Optional[str] = Query(None, description="Lọc theo khu vực"),
    assigned_sale_id: Optional[int] = Query(None, description="Lọc theo nhân viên phụ trách"),
    overdue_only: bool = Query(False, description="Lọc riêng các đại lý có nợ quá hạn"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã, tên, SĐT đại lý"),
    ageing_bucket: Optional[str] = Query(None, description="Lọc theo nhóm tuổi nợ (all, 0-30, 31-60, 61-90, over_90, has_debt, settled, overdue)"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "accountant"])),
):
    """
    SCRUM-91: Báo cáo Sổ công nợ theo đại lý với phân tích tuổi nợ (0-30, 31-60, 61-90, >90 ngày).
    Chỉ cho phép Kế toán (accountant), Quản lý kinh doanh (sales_manager) và Admin truy cập.
    """
    from app.services.debt_ledger_service import compute_debt_ledger_data
    return compute_debt_ledger_data(
        db=db,
        region=region,
        assigned_sale_id=assigned_sale_id,
        overdue_only=overdue_only,
        search=search,
        ageing_bucket=ageing_bucket,
    )


@router.get("/debt-ledger/export")
def export_dealer_debt_ledger_endpoint(
    region: Optional[str] = Query(None, description="Lọc theo khu vực"),
    assigned_sale_id: Optional[int] = Query(None, description="Lọc theo nhân viên phụ trách"),
    overdue_only: bool = Query(False, description="Lọc riêng các đại lý có nợ quá hạn"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã, tên, SĐT đại lý"),
    ageing_bucket: Optional[str] = Query(None, description="Lọc theo nhóm tuổi nợ (all, 0-30, 31-60, 61-90, over_90, has_debt, settled, overdue)"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "accountant"])),
):
    """
    SCRUM-91: Xuất file Excel báo cáo Sổ công nợ và Phân tích tuổi nợ theo đúng bộ lọc.
    """
    from fastapi.responses import Response
    from app.services.debt_ledger_service import compute_debt_ledger_data, export_debt_ledger_excel

    data = compute_debt_ledger_data(
        db=db,
        region=region,
        assigned_sale_id=assigned_sale_id,
        overdue_only=overdue_only,
        search=search,
        ageing_bucket=ageing_bucket,
    )
    excel_bytes = export_debt_ledger_excel(data)

    filename = f"So_Cong_No_Va_Tuoi_No_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def calculate_debt_statement(
    dealer_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Tính toán Sổ chi tiết công nợ đại lý (Mẫu S31-DN):
    - Thu thập toàn bộ hóa đơn/đơn hàng (Phát sinh Tăng/Nợ)
    - Thu thập toàn bộ phiếu thu tiền/thanh toán (Phát sinh Giảm/Có)
    - Xác định Số dư đầu kỳ (trước from_date)
    - Liệt kê theo thứ tự thời gian tăng dần trong kỳ kèm số dư nợ lũy kế
    - Tính tổng phát sinh và Số dư cuối kỳ
    """
    now = datetime.now(timezone.utc)
    load_dealers_db()
    load_receipt_vouchers_db()
    dealer = DEALERS_DB.get(dealer_id) or (DEALERS_DB.get(int(dealer_id)) if str(dealer_id).isdigit() else None)
    if not dealer and db:
        try:
            from app.models.entities import DealerEntity
            ent = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
            if ent:
                dealer = Dealer(
                    id=ent.id,
                    code=ent.code,
                    name=ent.name,
                    tax_code=getattr(ent, "tax_code", None),
                    phone=ent.phone,
                    email=ent.email,
                    address=ent.address,
                    region=getattr(ent, "region", None),
                    assigned_sale_id=ent.assigned_sale_id,
                    credit_limit=float(ent.credit_limit) if getattr(ent, "credit_limit", None) is not None else 50000000.0,
                    max_debt_days=int(ent.max_debt_days) if getattr(ent, "max_debt_days", None) is not None else 30,
                    overdue_days_allowed=int(getattr(ent, "overdue_days_allowed", None) or getattr(ent, "max_debt_days", None) or 30),
                    customer_group=getattr(ent, "customer_group", "Đại lý cấp 1"),
                    status=getattr(ent, "status", "Đang hoạt động"),
                )
                DEALERS_DB[ent.id] = dealer
        except Exception as e:
            logger.warning(f"Error querying fallback DealerEntity: {e}")

    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý ID {dealer_id}."
        )

    from_dt: Optional[datetime] = None
    to_dt: Optional[datetime] = None
    if from_date:
        try:
            from_dt = datetime.strptime(from_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=0, minute=0, second=0)
        except Exception:
            pass
    if to_date:
        try:
            to_dt = datetime.strptime(to_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
        except Exception:
            pass

    all_raw_txs: List[Dict[str, Any]] = []

    # 1. Thu thập toàn bộ đơn hàng từ nguồn chuẩn hóa (SQL Database + In-memory + Fallback mock)
    unified_orders = get_dealer_unified_orders(dealer_id=dealer_id, dealer=dealer, db=db)
    for ord_info in unified_orders:
        if ord_info.get("status") in ("CANCELLED", "REJECTED"):
            continue
        dt = ord_info["dt_obj"]
        code = ord_info["order_code"]
        amt = float(ord_info["total_amount"] or 0.0)
        st = ord_info.get("status") or "CONFIRMED"
        all_raw_txs.append({
            "id": f"ORD-{ord_info['id']}",
            "date": dt.isoformat(),
            "dt_obj": dt,
            "doc_code": code,
            "doc_type": "ORDER",
            "doc_type_label": "Hóa đơn / Xuất bán",
            "description": f"Xuất bán hàng theo đơn hàng {code}",
            "increase_amount": amt,
            "decrease_amount": 0.0,
            "payment_method": "Công nợ đại lý",
            "status": st,
            "created_by": ord_info.get("created_by") or "Hệ thống bán hàng",
            "note": None,
        })

    # 2. Toàn bộ Phiếu thu tiền thực tế từ SQL Server (ReceiptVoucherEntity + PaymentCollectionEntity)
    existing_receipt_order_codes = set()
    loaded_from_sql = False
    if db:
        try:
            from app.models.receipt_voucher import ReceiptVoucherEntity
            sql_vouchers = db.query(ReceiptVoucherEntity).filter(ReceiptVoucherEntity.dealer_id == dealer_id).all()
            for v in sql_vouchers:
                loaded_from_sql = True
                dt = parse_tx_date(v.created_at or now)
                amt = float(v.amount or 0.0)
                oc = v.order_code
                if oc:
                    existing_receipt_order_codes.add(oc.strip())
                all_raw_txs.append({
                    "id": f"VOUCHER-{v.receipt_code}",
                    "date": dt.isoformat(),
                    "dt_obj": dt,
                    "doc_code": v.receipt_code,
                    "doc_type": "RECEIPT_VOUCHER",
                    "doc_type_label": "Phiếu thu tiền",
                    "description": v.reason or f"Thu tiền thanh toán công nợ theo phiếu {v.receipt_code}",
                    "increase_amount": 0.0,
                    "decrease_amount": amt,
                    "payment_method": v.payment_method or "Tiền mặt",
                    "status": "Đã thu tiền",
                    "created_by": v.collector_name or "Kế toán công nợ",
                    "note": v.note or (f"Người nộp: {v.payer_name}" if v.payer_name else None),
                })
        except Exception as e:
            logger.warning(f"Error querying ReceiptVoucherEntity: {e}")

    if not loaded_from_sql:
        for v_code, v in RECEIPT_VOUCHERS_DB.items():
            if v.get("dealer_id") == dealer_id:
                dt = parse_tx_date(v.get("created_at") or now)
                amt = float(v.get("amount", 0.0) or 0.0)
                oc = v.get("order_code")
                if oc:
                    existing_receipt_order_codes.add(oc.strip())
                all_raw_txs.append({
                    "id": f"VOUCHER-{v_code}",
                    "date": dt.isoformat(),
                    "dt_obj": dt,
                    "doc_code": v.get("receipt_code") or v_code,
                    "doc_type": "RECEIPT_VOUCHER",
                    "doc_type_label": "Phiếu thu tiền",
                    "description": v.get("reason") or f"Thu tiền thanh toán công nợ theo phiếu {v_code}",
                    "increase_amount": 0.0,
                    "decrease_amount": amt,
                    "payment_method": v.get("payment_method") or "Tiền mặt",
                    "status": "Đã thu tiền",
                    "created_by": v.get("collector_name") or "Kế toán công nợ",
                    "note": v.get("note") or (f"Người nộp: {v.get('payer_name')}" if v.get('payer_name') else None),
                })

    # 2.1 Bổ sung khoản thu từ PaymentCollectionEntity đã xác nhận (Thu tiền theo tuyến)
    if db:
        try:
            from app.models.payment_collection import PaymentCollectionEntity
            confirmed_pcols = db.query(PaymentCollectionEntity).filter(
                PaymentCollectionEntity.dealer_id == dealer_id,
                PaymentCollectionEntity.status == "CONFIRMED"
            ).all()
            for pc in confirmed_pcols:
                if pc.order_code and pc.order_code.strip() in existing_receipt_order_codes:
                    continue
                dt = parse_tx_date(pc.collection_date or now)
                amt = float(pc.amount or 0.0)
                if pc.order_code:
                    existing_receipt_order_codes.add(pc.order_code.strip())
                all_raw_txs.append({
                    "id": f"PC-{pc.code}",
                    "date": dt.isoformat(),
                    "dt_obj": dt,
                    "doc_code": pc.code,
                    "doc_type": "RECEIPT_VOUCHER",
                    "doc_type_label": "Thu tiền theo tuyến",
                    "description": f"Thu tiền nộp về công ty ({pc.order_code or 'Thanh toán nợ'})",
                    "increase_amount": 0.0,
                    "decrease_amount": amt,
                    "payment_method": pc.payment_method or "Tiền mặt",
                    "status": "Đã thu tiền",
                    "created_by": pc.confirmed_by or pc.sales_name or "Kế toán công nợ",
                    "note": pc.note,
                })
        except Exception:
            pass

    # 3. Thu thập toàn bộ Chứng từ điều chỉnh giảm công nợ do trả hàng (DEBT_ADJUSTMENT)
    adjusted_by_order: Dict[str, float] = {}
    if db:
        try:
            from app.models.debt_adjustment import DebtAdjustmentEntity
            adjustments = db.query(DebtAdjustmentEntity).filter(
                DebtAdjustmentEntity.dealer_id == dealer_id,
                DebtAdjustmentEntity.status == "POSTED",
            ).all()
            for adj in adjustments:
                adj_dt = parse_tx_date(adj.adjustment_date or now)
                adj_amt = float(adj.total_adjusted_amount or 0.0)
                # Map với order code nếu có
                ord_c = (adj.original_invoice_code or "").replace("HD-", "").strip()
                if ord_c:
                    adjusted_by_order[ord_c] = adjusted_by_order.get(ord_c, 0.0) + adj_amt

                all_raw_txs.append({
                    "id": f"ADJ-{adj.id}",
                    "date": adj_dt.isoformat(),
                    "dt_obj": adj_dt,
                    "doc_code": adj.adjustment_code,
                    "doc_type": "DEBT_ADJUSTMENT",
                    "doc_type_label": "Giảm trừ công nợ",
                    "description": f"Giảm trừ công nợ do trả hàng theo Phiếu trả {adj.return_order_code or ''} (HĐ gốc: {adj.original_invoice_code or ''})",
                    "increase_amount": 0.0,
                    "decrease_amount": adj_amt,
                    "payment_method": "Giảm trừ trả hàng",
                    "status": "Đã ghi sổ",
                    "created_by": adj.created_by_name or "Kế toán công nợ",
                    "note": adj.reason or adj.note,
                })
        except Exception:
            pass

    # 4. Tự động ghi nhận nghiệp vụ Thu tiền (Phát sinh Giảm) cho các đơn hàng đã thanh toán (PAID)
    # nếu chưa có phiếu thu trong RECEIPT_VOUCHERS_DB để đối trừ công nợ về 0 (đã trừ đi phần điều chỉnh giảm nợ)
    for tx in list(all_raw_txs):
        if tx.get("doc_type") == "ORDER" and tx.get("status") == "PAID":
            code = tx.get("doc_code")
            if code not in existing_receipt_order_codes:
                dt = tx["dt_obj"]
                amt = tx["increase_amount"]
                adjusted_offset = adjusted_by_order.get(code, 0.0)
                remaining_pay_amt = max(0.0, amt - adjusted_offset)
                if remaining_pay_amt > 0:
                    all_raw_txs.append({
                        "id": f"PAY-{code}",
                        "date": dt.isoformat(),
                        "dt_obj": dt,
                        "doc_code": f"PT-{code}",
                        "doc_type": "RECEIPT_VOUCHER",
                        "doc_type_label": "Phiếu thu tiền",
                        "description": f"Thu tiền thanh toán đơn hàng {code} (Đã thanh toán)",
                        "increase_amount": 0.0,
                        "decrease_amount": remaining_pay_amt,
                        "payment_method": "Chuyển khoản / Tiền mặt",
                        "status": "Đã thu tiền",
                        "created_by": "Kế toán công nợ",
                        "note": f"Thanh toán hoàn tất đơn hàng {code}",
                    })

    # Sắp xếp toàn bộ giao dịch theo thứ tự thời gian tăng dần, ưu tiên hóa đơn bán trước phiếu thu/giảm nợ trong cùng thời điểm
    all_raw_txs.sort(key=lambda x: (x["dt_obj"], 0 if x["doc_type"] == "ORDER" else 1))

    # Phân loại: Trước kỳ (tính vào Số dư đầu kỳ) và Trong kỳ
    prior_txs = []
    period_txs = []

    for tx in all_raw_txs:
        tx_dt = tx["dt_obj"]
        if from_dt and tx_dt < from_dt:
            prior_txs.append(tx)
        elif to_dt and tx_dt > to_dt:
            continue
        else:
            period_txs.append(tx)

    opening_balance = sum(t["increase_amount"] - t["decrease_amount"] for t in prior_txs)

    running_balance = opening_balance
    final_period_items = []
    for idx, tx in enumerate(period_txs, start=1):
        running_balance += (tx["increase_amount"] - tx["decrease_amount"])
        final_period_items.append({
            "id": tx["id"],
            "stt": idx,
            "date": tx["date"],
            "doc_code": tx["doc_code"],
            "doc_type": tx["doc_type"],
            "doc_type_label": tx["doc_type_label"],
            "description": tx["description"],
            "increase_amount": tx["increase_amount"],
            "decrease_amount": tx["decrease_amount"],
            "running_balance": running_balance,
            "payment_method": tx["payment_method"],
            "status": tx["status"],
            "created_by": tx["created_by"],
            "note": tx["note"],
        })

    total_increase = sum(t["increase_amount"] for t in final_period_items)
    total_decrease = sum(t["decrease_amount"] for t in final_period_items)
    closing_balance = running_balance

    dealer_info = {
        "id": dealer.id,
        "code": dealer.code,
        "name": dealer.name,
        "tax_code": getattr(dealer, "tax_code", None),
        "phone": getattr(dealer, "phone", None),
        "email": getattr(dealer, "email", None),
        "address": getattr(dealer, "address", None),
        "region": getattr(dealer, "region", None),
        "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
        "credit_limit": float(getattr(dealer, "credit_limit", 50000000.0) or 50000000.0),
        "overdue_days_allowed": int(getattr(dealer, "overdue_days_allowed", 30) or 30),
        "assigned_sale_id": dealer.assigned_sale_id,
        "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
        "status": normalize_dealer_status(getattr(dealer, "status", "Đang hoạt động")),
    }

    return {
        "dealer": dealer_info,
        "period": {
            "from_date": from_date if from_date else (period_txs[0]["date"][:10] if period_txs else None),
            "to_date": to_date if to_date else (period_txs[-1]["date"][:10] if period_txs else None),
        },
        "summary": {
            "opening_balance": opening_balance,
            "total_increase": total_increase,
            "total_decrease": total_decrease,
            "closing_balance": closing_balance,
            "closing_balance_words": number_to_vietnamese_words(closing_balance),
            "transactions_count": len(final_period_items),
        },
        "transactions": final_period_items,
    }


def generate_debt_statement_excel(statement_data: dict) -> io.BytesIO:
    """
    Xuất file Excel Sổ chi tiết công nợ đại lý chuẩn nghiệp vụ Kế toán Việt Nam (Mẫu S31-DN).
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    dealer = statement_data["dealer"]
    period = statement_data["period"]
    summary = statement_data["summary"]
    transactions = statement_data["transactions"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SoChiTietCongNo"
    ws.views.sheetView[0].showGridLines = True

    # Khổ giấy in ngang A4 và vừa chiều ngang 1 trang
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = None

    # Chiều cao các dòng tiêu đề
    ws.row_dimensions[4].height = 28
    ws.row_dimensions[11].height = 26

    # Bảng màu
    color_teal = "0F766E"       # Xanh ngọc đậm header
    color_opening = "F1F5F9"    # Xám nhạt số dư đầu kỳ
    color_subtotal = "FEF3C7"   # Vàng nhạt tổng phát sinh
    color_closing = "DCFCE7"    # Xanh lá nhạt số dư cuối kỳ
    color_white = "FFFFFF"

    font_company = Font(name="Segoe UI", size=11, bold=True, color="1E293B")
    font_sub = Font(name="Segoe UI", size=9.5, italic=True, color="475569")
    font_title = Font(name="Segoe UI", size=16, bold=True, color="0F172A")
    font_bold_header = Font(name="Segoe UI", size=10, bold=True, color=color_white)
    font_bold = Font(name="Segoe UI", size=10, bold=True, color="0F172A")
    font_regular = Font(name="Segoe UI", size=10, color="0F172A")
    font_money_inc = Font(name="Segoe UI", size=10, color="B91C1C", bold=True)
    font_money_dec = Font(name="Segoe UI", size=10, color="15803D", bold=True)

    fill_header = PatternFill(start_color=color_teal, end_color=color_teal, fill_type="solid")
    fill_opening = PatternFill(start_color=color_opening, end_color=color_opening, fill_type="solid")
    fill_total = PatternFill(start_color=color_subtotal, end_color=color_subtotal, fill_type="solid")
    fill_closing = PatternFill(start_color=color_closing, end_color=color_closing, fill_type="solid")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    thick_bottom = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='medium', color='0F172A')
    )

    # 1. Thông tin đơn vị phát hành
    ws["A1"] = "HỆ THỐNG PHÂN PHỐI & BÁN HÀNG VÀ QUẢN LÝ KHO TTCS"
    ws["A1"].font = font_company
    ws["A2"] = "Bộ phận Kế toán Tài chính — Quản lý Công nợ Đại lý"
    ws["A2"].font = font_sub

    ws["I1"] = "Mẫu số S31-DN"
    ws["I1"].font = Font(name="Segoe UI", size=10, bold=True, color="1E293B")
    ws["I1"].alignment = Alignment(horizontal="right")
    ws["I2"] = "(Ban hành theo TT số 200/2014/TT-BTC)"
    ws["I2"].font = font_sub
    ws["I2"].alignment = Alignment(horizontal="right")

    # 2. Tiêu đề sổ
    ws["A4"] = "SỔ CHI TIẾT CÔNG NỢ ĐẠI LÝ"
    ws["A4"].font = font_title
    ws["A4"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A4:I4")

    from_str = period.get("from_date") or "Đầu kỳ"
    to_str = period.get("to_date") or "Hiện tại"
    ws["A5"] = f"Kỳ đối chiếu công nợ: Từ ngày {from_str} đến ngày {to_str}"
    ws["A5"].font = font_sub
    ws["A5"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A5:I5")

    # 3. Thông tin đại lý
    ws["A7"] = "Đại lý / Khách hàng:"
    ws["A7"].font = font_bold
    ws["B7"] = f"{dealer.get('name', '')} (Mã: {dealer.get('code', '')})"
    ws["B7"].font = font_bold
    ws.merge_cells("B7:E7")

    ws["F7"] = "Mã số thuế:"
    ws["F7"].font = font_bold
    ws["G7"] = dealer.get('tax_code') or "—"
    ws.merge_cells("G7:I7")

    ws["A8"] = "Địa chỉ liên hệ:"
    ws["A8"].font = font_regular
    ws["B8"] = dealer.get('address') or "—"
    ws.merge_cells("B8:E8")

    ws["F8"] = "Số điện thoại:"
    ws["F8"].font = font_regular
    ws["G8"] = dealer.get('phone') or "—"
    ws.merge_cells("G8:I8")

    ws["A9"] = "Hạn mức tín dụng:"
    ws["A9"].font = font_regular
    credit_limit = dealer.get('credit_limit', 0.0) or 0.0
    ws["B9"] = f"{credit_limit:,.0f} đ"
    ws.merge_cells("B9:E9")

    ws["F9"] = "Thời hạn nợ cho phép:"
    ws["F9"].font = font_regular
    ws["G9"] = f"{dealer.get('overdue_days_allowed') or 30} ngày"
    ws.merge_cells("G9:I9")

    # 4. Tiêu đề các cột dữ liệu
    headers = [
        ("STT", 6, "center"),
        ("Ngày chứng từ", 16, "center"),
        ("Số chứng từ", 18, "center"),
        ("Loại chứng từ", 18, "center"),
        ("Diễn giải nghiệp vụ kinh tế", 38, "left"),
        ("Phát sinh Tăng (Nợ)", 18, "right"),
        ("Phát sinh Giảm (Có)", 18, "right"),
        ("Số dư nợ luỹ kế", 20, "right"),
        ("Ghi chú / Trạng thái", 18, "center"),
    ]

    header_row = 11
    for col_idx, (h_title, col_width, align) in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=h_title)
        cell.font = font_bold_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = col_width

    # Row 12: Dòng Số dư đầu kỳ
    row_idx = 12
    ws.cell(row=row_idx, column=1, value="")
    ws.cell(row=row_idx, column=2, value="")
    ws.cell(row=row_idx, column=3, value="")
    ws.cell(row=row_idx, column=4, value="SỐ DƯ ĐẦU KỲ")
    ws.cell(row=row_idx, column=5, value="Số dư công nợ chuyển sang từ kỳ trước")
    ws.cell(row=row_idx, column=6, value=None)
    ws.cell(row=row_idx, column=7, value=None)
    ws.cell(row=row_idx, column=8, value=summary["opening_balance"])
    ws.cell(row=row_idx, column=9, value="Đầu kỳ")

    for col in range(1, 10):
        c = ws.cell(row=row_idx, column=col)
        c.font = font_bold
        c.fill = fill_opening
        c.border = thin_border
        if col == 8:
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal="right")
        elif col in (1, 2, 3, 4, 9):
            c.alignment = Alignment(horizontal="center")

    # Các dòng phát sinh chi tiết trong kỳ
    for idx, tx in enumerate(transactions, start=1):
        row_idx += 1
        ws.cell(row=row_idx, column=1, value=idx).alignment = Alignment(horizontal="center")
        raw_dt = tx.get("date") or ""
        formatted_dt = raw_dt[:10]
        if "T" in raw_dt:
            parts = raw_dt.split("T")
            time_part = parts[1][:5]
            formatted_dt = f"{parts[0]} {time_part}"
        ws.cell(row=row_idx, column=2, value=formatted_dt).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=3, value=tx.get("doc_code", "")).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=4, value=tx.get("doc_type_label", "")).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=5, value=tx.get("description", "")).alignment = Alignment(horizontal="left", wrap_text=True)

        inc_cell = ws.cell(row=row_idx, column=6, value=tx.get("increase_amount") or None)
        inc_cell.number_format = '#,##0'
        inc_cell.alignment = Alignment(horizontal="right")
        if tx.get("increase_amount"):
            inc_cell.font = font_money_inc

        dec_cell = ws.cell(row=row_idx, column=7, value=tx.get("decrease_amount") or None)
        dec_cell.number_format = '#,##0'
        dec_cell.alignment = Alignment(horizontal="right")
        if tx.get("decrease_amount"):
            dec_cell.font = font_money_dec

        bal_cell = ws.cell(row=row_idx, column=8, value=tx.get("running_balance", 0.0))
        bal_cell.number_format = '#,##0'
        bal_cell.alignment = Alignment(horizontal="right")
        bal_cell.font = font_bold

        note_val = tx.get("payment_method") or tx.get("status") or ""
        ws.cell(row=row_idx, column=9, value=note_val).alignment = Alignment(horizontal="center")

        for col in range(1, 10):
            ws.cell(row=row_idx, column=col).border = thin_border

    # Dòng Cộng phát sinh trong kỳ
    row_idx += 1
    ws.cell(row=row_idx, column=1, value="")
    ws.cell(row=row_idx, column=2, value="")
    ws.cell(row=row_idx, column=3, value="")
    ws.cell(row=row_idx, column=4, value="CỘNG PHÁT SINH")
    ws.cell(row=row_idx, column=5, value="Tổng cộng phát sinh trong kỳ")
    c_inc = ws.cell(row=row_idx, column=6, value=summary["total_increase"])
    c_inc.number_format = '#,##0'
    c_inc.alignment = Alignment(horizontal="right")
    c_dec = ws.cell(row=row_idx, column=7, value=summary["total_decrease"])
    c_dec.number_format = '#,##0'
    c_dec.alignment = Alignment(horizontal="right")
    ws.cell(row=row_idx, column=8, value="")
    ws.cell(row=row_idx, column=9, value="")
    for col in range(1, 10):
        c = ws.cell(row=row_idx, column=col)
        c.font = font_bold
        c.fill = fill_total
        c.border = thin_border
        if col in (4,):
            c.alignment = Alignment(horizontal="center")

    # Dòng Số dư cuối kỳ
    row_idx += 1
    ws.cell(row=row_idx, column=1, value="")
    ws.cell(row=row_idx, column=2, value="")
    ws.cell(row=row_idx, column=3, value="")
    ws.cell(row=row_idx, column=4, value="SỐ DƯ CUỐI KỲ")
    ws.cell(row=row_idx, column=5, value="Số dư nợ còn lại đại lý phải thanh toán")
    ws.cell(row=row_idx, column=6, value="")
    ws.cell(row=row_idx, column=7, value="")
    c_close = ws.cell(row=row_idx, column=8, value=summary["closing_balance"])
    c_close.number_format = '#,##0'
    c_close.alignment = Alignment(horizontal="right")
    c_close.font = Font(name="Segoe UI", size=11, bold=True, color="B91C1C")
    ws.cell(row=row_idx, column=9, value="Chốt kỳ")
    for col in range(1, 10):
        c = ws.cell(row=row_idx, column=col)
        c.font = font_bold
        c.fill = fill_closing
        c.border = thick_bottom
        if col in (4, 9):
            c.alignment = Alignment(horizontal="center")

    # Dòng số tiền bằng chữ
    row_idx += 2
    ws.cell(row=row_idx, column=1, value=f"Số dư nợ cuối kỳ bằng chữ: {summary.get('closing_balance_words', '')}")
    ws.cell(row=row_idx, column=1).font = Font(name="Segoe UI", size=10, italic=True, bold=True, color="1E293B")
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=9)

    # Dòng cam kết đối chiếu
    row_idx += 1
    ws.cell(row=row_idx, column=1, value="Hai bên xác nhận đã đối chiếu chính xác các nghiệp vụ kinh tế và số dư công nợ tính đến ngày lập biên bản.")
    ws.cell(row=row_idx, column=1).font = font_sub
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=9)

    # Khung chữ ký 4 bên
    row_idx += 2
    ws.cell(row=row_idx, column=1, value="NGƯỜI LẬP BIỂU").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=1).font = font_bold
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=2)

    ws.cell(row=row_idx, column=3, value="KẾ TOÁN CÔNG NỢ").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=3).font = font_bold
    ws.merge_cells(start_row=row_idx, start_column=3, end_row=row_idx, end_column=5)

    ws.cell(row=row_idx, column=6, value="GIÁM ĐỐC / KTT").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=6).font = font_bold
    ws.merge_cells(start_row=row_idx, start_column=6, end_row=row_idx, end_column=7)

    ws.cell(row=row_idx, column=8, value="ĐẠI DIỆN ĐẠI LÝ").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=8).font = font_bold
    ws.merge_cells(start_row=row_idx, start_column=8, end_row=row_idx, end_column=9)

    row_idx += 1
    ws.cell(row=row_idx, column=1, value="(Ký, ghi rõ họ tên)").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=1).font = font_sub
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=2)

    ws.cell(row=row_idx, column=3, value="(Ký, ghi rõ họ tên)").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=3).font = font_sub
    ws.merge_cells(start_row=row_idx, start_column=3, end_row=row_idx, end_column=5)

    ws.cell(row=row_idx, column=6, value="(Ký, họ tên, đóng dấu)").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=6).font = font_sub
    ws.merge_cells(start_row=row_idx, start_column=6, end_row=row_idx, end_column=7)

    ws.cell(row=row_idx, column=8, value="(Ký, họ tên, đóng dấu)").alignment = Alignment(horizontal="center")
    ws.cell(row=row_idx, column=8).font = font_sub
    ws.merge_cells(start_row=row_idx, start_column=8, end_row=row_idx, end_column=9)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


@router.get("/{dealer_id}/debt-statement", response_model=DebtStatementFullResponse)
def get_dealer_debt_statement(
    dealer_id: int,
    from_date: Optional[str] = Query(None, description="Lọc từ ngày (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Lọc đến ngày (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """
    Kế toán công nợ: Xuất và xem Sổ chi tiết công nợ của đại lý (Mẫu S31-DN).
    Hỗ trợ lọc khoảng thời gian, tính số dư đầu kỳ, toàn bộ phát sinh tăng giảm và số dư lũy kế.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id) or (DEALERS_DB.get(int(dealer_id)) if str(dealer_id).isdigit() else None)
    if not dealer and db:
        try:
            from app.models.entities import DealerEntity
            ent = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
            if ent:
                dealer = Dealer(
                    id=ent.id,
                    code=ent.code,
                    name=ent.name,
                    tax_code=getattr(ent, "tax_code", None),
                    phone=ent.phone,
                    email=ent.email,
                    address=ent.address,
                    region=getattr(ent, "region", None),
                    assigned_sale_id=ent.assigned_sale_id,
                    credit_limit=float(ent.credit_limit) if getattr(ent, "credit_limit", None) is not None else 50000000.0,
                    max_debt_days=int(ent.max_debt_days) if getattr(ent, "max_debt_days", None) is not None else 30,
                    overdue_days_allowed=int(getattr(ent, "overdue_days_allowed", None) or getattr(ent, "max_debt_days", None) or 30),
                    customer_group=getattr(ent, "customer_group", "Đại lý cấp 1"),
                    status=getattr(ent, "status", "Đang hoạt động"),
                )
                DEALERS_DB[ent.id] = dealer
        except Exception as e:
            logger.warning(f"Error querying fallback DealerEntity: {e}")

    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý ID {dealer_id}."
        )

    if is_customer:
        customer_dealer = DEALERS_DB.get(current_user.id)
        if not customer_dealer:
            for d in DEALERS_DB.values():
                if (d.email and current_user.email and d.email.lower() == current_user.email.lower()) or \
                   (d.phone and current_user.phone and d.phone == current_user.phone) or \
                   (d.code and current_user.username and d.code.lower() == current_user.username.lower()) or \
                   (d.name and current_user.full_name and d.name.lower() == current_user.full_name.lower()):
                    customer_dealer = d
                    break
        if not customer_dealer or customer_dealer.id != dealer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xem sổ chi tiết công nợ của chính mình."
            )

    return calculate_debt_statement(
        dealer_id=dealer_id,
        from_date=from_date,
        to_date=to_date,
        db=db,
    )


@router.get("/{dealer_id}/debt-statement/export-excel")
def export_dealer_debt_statement_excel(
    dealer_id: int,
    from_date: Optional[str] = Query(None, description="Lọc từ ngày (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Lọc đến ngày (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
            "customer",
        ])
    ),
):
    """
    Kế toán công nợ: Xuất file Excel (.xlsx) Sổ chi tiết công nợ đại lý gửi đối chiếu số dư.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in user_roles and not any(r in user_roles for r in ["admin", "sales_manager", "sales", "accountant"])

    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id) or (DEALERS_DB.get(int(dealer_id)) if str(dealer_id).isdigit() else None)
    if not dealer and db:
        try:
            from app.models.entities import DealerEntity
            ent = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
            if ent:
                dealer = Dealer(
                    id=ent.id,
                    code=ent.code,
                    name=ent.name,
                    tax_code=getattr(ent, "tax_code", None),
                    phone=ent.phone,
                    email=ent.email,
                    address=ent.address,
                    region=getattr(ent, "region", None),
                    assigned_sale_id=ent.assigned_sale_id,
                    credit_limit=float(ent.credit_limit) if getattr(ent, "credit_limit", None) is not None else 50000000.0,
                    max_debt_days=int(ent.max_debt_days) if getattr(ent, "max_debt_days", None) is not None else 30,
                    overdue_days_allowed=int(getattr(ent, "overdue_days_allowed", None) or getattr(ent, "max_debt_days", None) or 30),
                    customer_group=getattr(ent, "customer_group", "Đại lý cấp 1"),
                    status=getattr(ent, "status", "Đang hoạt động"),
                )
                DEALERS_DB[ent.id] = dealer
        except Exception as e:
            logger.warning(f"Error querying fallback DealerEntity: {e}")

    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý ID {dealer_id}."
        )

    if is_customer:
        customer_dealer = DEALERS_DB.get(current_user.id)
        if not customer_dealer:
            for d in DEALERS_DB.values():
                if (d.email and current_user.email and d.email.lower() == current_user.email.lower()) or \
                   (d.phone and current_user.phone and d.phone == current_user.phone) or \
                   (d.code and current_user.username and d.code.lower() == current_user.username.lower()) or \
                   (d.name and current_user.full_name and d.name.lower() == current_user.full_name.lower()):
                    customer_dealer = d
                    break
        if not customer_dealer or customer_dealer.id != dealer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản đại lý chỉ được phép xuất sổ chi tiết công nợ của chính mình."
            )

    try:
        statement_data = calculate_debt_statement(
            dealer_id=dealer_id,
            from_date=from_date,
            to_date=to_date,
            db=db,
        )
        excel_stream = generate_debt_statement_excel(statement_data)
    except Exception as e:
        logger.error(f"Error generating Excel statement for dealer {dealer_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Không thể khởi tạo file Excel: {str(e)}"
        )

    import urllib.parse
    import re
    date_suffix = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw_code = dealer.code or f"DL{dealer.id}"
    safe_code = re.sub(r'[^a-zA-Z0-9_-]', '_', str(raw_code))
    filename = f"So_Chi_Tiet_Cong_No_{safe_code}_{date_suffix}.xlsx"
    encoded_filename = urllib.parse.quote(filename)

    return Response(
        content=excel_stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post("/sync-debt")
@router.post("/{dealer_id}/sync-debt")
def sync_dealer_debt_endpoint(
    dealer_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "accountant",
        ])
    ),
):
    """
    Kế toán công nợ / Admin: Yêu cầu đồng bộ lại toàn bộ dữ liệu công nợ trên SQL Server.
    Gọi trực tiếp Stored Procedure / logic đồng bộ SQL Server chuẩn hóa hóa đơn, phiếu thu, nợ còn lại.
    """
    from app.services.invoice_service import resync_all_debts_sql
    result = resync_all_debts_sql(db)
    return {
        "status": "success",
        "message": "Đã đồng bộ lại toàn bộ dữ liệu công nợ thành công trên SQL Server.",
        "details": result,
    }


