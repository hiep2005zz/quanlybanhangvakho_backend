from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.database import get_db
from app.models.dealer import DEALERS_DB, Dealer, save_dealers_db, load_dealers_db
from app.models.user import USERS_DB, load_users_db
from app.schemas.auth import UserResponse
from app.services.audit_service import log_audit_event


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
    customer_group: Optional[str] = "Đại lý cấp 1"
    status: Optional[str] = "Đang hoạt động"
    transaction_count: Optional[int] = None


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
    customer_group: Optional[str] = None
    status: Optional[str] = None
    transaction_count: Optional[int] = None


def get_sale_name(assigned_sale_id: Optional[int]) -> Optional[str]:
    """Lấy tên nhân viên kinh doanh phụ trách đại lý."""
    if not assigned_sale_id:
        return None

    for user in USERS_DB.values():
        if user.id == assigned_sale_id:
            return user.full_name

    return None


def get_region(address: Optional[str]) -> str:
    """
    Lấy khu vực từ địa chỉ hiện có.
    Ví dụ: '120 Cầu Giấy, Hà Nội' -> 'Hà Nội'
    """
    if not address:
        return "Chưa xác định"

    parts = [part.strip() for part in address.split(",") if part.strip()]
    if parts:
        return parts[-1]

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
        print(f"Lỗi lấy bảng giá áp dụng: {e}")
    return None


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


@router.get("/search")
def search_dealers(
    keyword: Optional[str] = Query(
        default=None,
        description="Tìm nhanh theo mã đại lý, tên đại lý hoặc số điện thoại"
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
    Tìm kiếm và lọc danh sách đại lý cho nhân viên kinh doanh:
    • Tìm nhanh theo mã, tên, số điện thoại
    • Lọc theo khu vực, nhóm khách hàng, người phụ trách, trạng thái
    """

    load_dealers_db()

    keyword_clean = keyword.strip().lower() if keyword else None
    region_clean = region.strip().lower() if region else None
    group_clean = customer_group.strip().lower() if customer_group else None
    status_clean = status.strip().lower() if status else None

    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    if "admin" not in user_roles and "sales_manager" not in user_roles and "accountant" not in user_roles:
        # Sales chỉ thấy đại lý của mình
        assigned_sale_id = current_user.id

    # Chuẩn hóa keyword dạng chữ số để hỗ trợ tìm số điện thoại khi có khoảng cách hoặc dấu chấm
    clean_kw_digits = "".join(ch for ch in keyword_clean if ch.isdigit()) if keyword_clean else ""

    results = []

    from app.api.v1.endpoints.orders import ORDERS_DB
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for dealer in DEALERS_DB.values():
        # Tính toán công nợ hiện tại và tuổi nợ
        current_debt = 0.0
        max_debt_age = 0
        for o in ORDERS_DB.values():
            if o.get("dealer_id") == dealer.id and o.get("status") not in ("PAID", "CANCELLED"):
                current_debt += o.get("total_amount", 0.0)
                created_at_str = o.get("created_at")
                if created_at_str:
                    try:
                        order_date = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        days_old = (now - order_date).days
                        if days_old > max_debt_age:
                            max_debt_age = days_old
                    except Exception:
                        pass
        
        c_limit = getattr(dealer, "credit_limit", 50000000.0)
        c_days = getattr(dealer, "max_debt_days", 30)
        if current_debt > c_limit and max_debt_age > c_days:
            debt_status = "Vượt hạn mức & Quá hạn"
        elif current_debt > c_limit:
            debt_status = "Vượt hạn mức"
        elif max_debt_age > c_days:
            debt_status = "Quá hạn thanh toán"
        else:
            debt_status = "Trong hạn mức" 
        # ==========================================
        # 1. TÌM KIẾM NHANH THEO MÃ / TÊN / MST / SĐT
        # ==========================================
        if keyword_clean:
            code_str = (dealer.code or "").lower()
            name_str = (dealer.name or "").lower()
            tax_str = (getattr(dealer, "tax_code", "") or "").lower()
            phone_str = (dealer.phone or "").lower()
            phone_digits = "".join(ch for ch in phone_str if ch.isdigit())

            matched_code = keyword_clean in code_str
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
        manual_tx = getattr(dealer, "transaction_count", 0) or 0
        order_tx = sum(1 for o in ORDERS_DB.values() if o.get("dealer_id") == dealer.id)
        transaction_count = max(manual_tx, order_tx)
        applied_pb = get_applied_price_book_info(getattr(dealer, "customer_group", "Đại lý cấp 1"))

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
            "assigned_sale_id": dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
            "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
            "status": getattr(dealer, "status", "Đang hoạt động"),
            "transaction_count": transaction_count,
            "has_transactions": transaction_count > 0,
            "applied_price_book": applied_pb,
        })

    return {
        "items": results,
        "total": len(results),
    }


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

    # Đảm bảo có các nhóm mặc định phổ biến
    default_groups = ["Đại lý cấp 1", "Đại lý cấp 2", "Khách sỉ", "Khách lẻ", "dai_ly_cap_1"]
    for dg in default_groups:
        customer_groups.add(dg)

    # Đảm bảo có các trạng thái mặc định
    default_statuses = ["Đang hoạt động", "Tạm ngừng", "Ngừng giao dịch"]
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

    # Sinh mã nếu chưa có
    new_id = max(DEALERS_DB.keys(), default=0) + 1
    code_clean = payload.code.strip().upper() if payload.code and payload.code.strip() else f"DL{new_id:03d}"

    # Kiểm tra trùng mã (Mã đại lý là duy nhất)
    for d in DEALERS_DB.values():
        if d.code.upper() == code_clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Mã đại lý '{code_clean}' đã tồn tại trên hệ thống. Mã đại lý phải là duy nhất.",
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
        customer_group=payload.customer_group or "Đại lý cấp 1",
        status=payload.status or "Đang hoạt động",
        transaction_count=tx_cnt,
    )

    DEALERS_DB[new_id] = new_dealer
    save_dealers_db()

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
    }


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
        if new_code != dealer.code.upper():
            for d in DEALERS_DB.values():
                if d.id != dealer_id and d.code.upper() == new_code:
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
    if payload.region is not None and payload.region.strip():
        dealer.region = payload.region.strip()
    if payload.assigned_sale_id is not None:
        dealer.assigned_sale_id = payload.assigned_sale_id
    if payload.customer_group is not None and payload.customer_group.strip():
        dealer.customer_group = payload.customer_group.strip()
    if payload.status is not None and payload.status.strip():
        valid_statuses = ["Đang hoạt động", "Tạm ngừng", "Ngừng giao dịch"]
        if payload.status.strip() in valid_statuses:
            dealer.status = payload.status.strip()
    if payload.credit_limit is not None:
        dealer.credit_limit = payload.credit_limit
    if payload.max_debt_days is not None:
        dealer.max_debt_days = payload.max_debt_days
    if payload.transaction_count is not None:
        if payload.transaction_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Số lượng giao dịch phải lớn hơn hoặc bằng 0.",
            )
        dealer.transaction_count = payload.transaction_count

    save_dealers_db()

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
    if new_status not in ["Đang hoạt động", "Tạm ngừng", "Ngừng giao dịch"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trạng thái chỉ có thể là 'Đang hoạt động', 'Ngừng giao dịch' hoặc 'Tạm ngừng'."
        )

    old_status = getattr(dealer, "status", "Đang hoạt động")
    if old_status == new_status:
        return {
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "status": dealer.status,
            "message": "Trạng thái không thay đổi",
        }

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
            new_val={"status": new_status},
            reason=payload.reason or f"Chuyển trạng thái sang '{new_status}'",
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

    old_sale_name = get_sale_name(old_sale_id)
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
            reason=payload.reason or "Chuyển giao đại lý",
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
                
            old_sale_name = get_sale_name(old_sale_id)
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
                    reason=payload.reason or "Chuyển giao hàng loạt",
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
        "message": f"Đã chuyển giao {assigned_count} đại lý cho {new_sale_user.full_name}.",
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
    from app.models.entities import OrderEntity, DealerEntity, DealerDeliveryPointEntity
    total_orders = db.query(OrderEntity).filter(OrderEntity.dealer_id == dealer_id).count()
    if total_orders == 0:
        from app.api.v1.endpoints.orders import ORDERS_DB
        total_orders = sum(1 for o in ORDERS_DB.values() if o.get("dealer_id") == dealer_id)

    manual_tx = getattr(dealer, "transaction_count", 0) or 0
    total_orders = max(total_orders, manual_tx)

    if total_orders > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Đại lý '{dealer.name}' ({dealer.code}) đã phát sinh {total_orders} giao dịch trong hệ thống, không thể xóa. Theo quy định kiểm toán và kế toán, chỉ được phép chuyển sang trạng thái 'Ngừng giao dịch'.",
        )

    # 1. Xóa các điểm giao hàng của đại lý nếu có
    db.query(DealerDeliveryPointEntity).filter(DealerDeliveryPointEntity.dealer_id == dealer_id).delete(synchronize_session=False)
    # 2. Xóa trong SQL
    db.query(DealerEntity).filter(DealerEntity.id == dealer_id).delete(synchronize_session=False)
    db.commit()

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
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "sales", "accountant"]))
):
    """Lấy danh sách tất cả đại lý."""
    load_dealers_db()
    res = []
    for d in DEALERS_DB.values():
        tx_count = sum(1 for o in ORDERS_DB.values() if o.get("dealer_id") == d.id)
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
            "customer_group": getattr(d, "customer_group", "Đại lý cấp 1"),
            "status": getattr(d, "status", "Đang hoạt động"),
            "lock_reason": getattr(d, "lock_reason", None),
            "transaction_count": tx_count,
            "has_transactions": tx_count > 0,
            "applied_price_book": applied_pb,
        })
    return res
