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
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    region: Optional[str] = None
    assigned_sale_id: Optional[int] = None
    credit_limit: Optional[float] = 50000000.0
    customer_group: Optional[str] = "Đại lý cấp 1"
    status: Optional[str] = "Đang hoạt động"


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

    for dealer in DEALERS_DB.values():
        # ==========================================
        # 1. TÌM KIẾM NHANH THEO MÃ / TÊN / SĐT
        # ==========================================
        if keyword_clean:
            code_str = (dealer.code or "").lower()
            name_str = (dealer.name or "").lower()
            phone_str = (dealer.phone or "").lower()
            phone_digits = "".join(ch for ch in phone_str if ch.isdigit())

            matched_code = keyword_clean in code_str
            matched_name = keyword_clean in name_str
            matched_phone = keyword_clean in phone_str
            if not matched_phone and clean_kw_digits and len(clean_kw_digits) >= 3:
                matched_phone = clean_kw_digits in phone_digits

            if not (matched_code or matched_name or matched_phone):
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
            if group_clean not in dealer_group.lower():
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

        results.append({
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "phone": dealer.phone,
            "email": dealer.email,
            "address": dealer.address,
            "region": dealer_region,
            "credit_limit": getattr(dealer, "credit_limit", 50000000.0),
            "assigned_sale_id": dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
            "customer_group": getattr(dealer, "customer_group", "Đại lý cấp 1"),
            "status": getattr(dealer, "status", "Đang hoạt động"),
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
    default_groups = ["Đại lý cấp 1", "Đại lý cấp 2", "Khách sỉ", "Khách lẻ"]
    for dg in default_groups:
        customer_groups.add(dg)

    # Đảm bảo có các trạng thái mặc định
    default_statuses = ["Đang hoạt động", "Tạm ngừng"]
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
        ])
    ),
):
    """
    Tạo mới đại lý / khách hàng vào tuyến phụ trách.
    Yêu cầu quyền: admin, sales_manager, sales.
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

    # Kiểm tra trùng mã
    for d in DEALERS_DB.values():
        if d.code.upper() == code_clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Mã đại lý '{code_clean}' đã tồn tại trên hệ thống",
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

    new_dealer = Dealer(
        id=new_id,
        code=code_clean,
        name=name_clean,
        phone=payload.phone.strip() if payload.phone else None,
        email=payload.email.strip() if payload.email else None,
        address=payload.address.strip() if payload.address else None,
        region=reg,
        assigned_sale_id=assigned_sale,
        credit_limit=payload.credit_limit or 50000000.0,
        customer_group=payload.customer_group or "Đại lý cấp 1",
        status=payload.status or "Đang hoạt động",
    )

    DEALERS_DB[new_id] = new_dealer
    save_dealers_db()

    return {
        "id": new_dealer.id,
        "code": new_dealer.code,
        "name": new_dealer.name,
        "phone": new_dealer.phone,
        "email": new_dealer.email,
        "address": new_dealer.address,
        "region": reg,
        "credit_limit": new_dealer.credit_limit,
        "assigned_sale_id": new_dealer.assigned_sale_id,
        "assigned_sale_name": get_sale_name(new_dealer.assigned_sale_id),
        "customer_group": new_dealer.customer_group,
        "status": new_dealer.status,
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
        ])
    ),
):
    """
    Cập nhật trạng thái đại lý / khách hàng ('Đang hoạt động' <-> 'Tạm ngừng').
    Quyền: admin, sales_manager, hoặc nhân viên kinh doanh (sales) phụ trách đại lý.
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
    if "admin" not in user_roles and "sales_manager" not in user_roles:
        if dealer.assigned_sale_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn chỉ có quyền thay đổi trạng thái của đại lý/khách hàng do bạn trực tiếp phụ trách."
            )

    new_status = payload.status.strip()
    if new_status not in ["Đang hoạt động", "Tạm ngừng"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trạng thái chỉ có thể là 'Đang hoạt động' hoặc 'Tạm ngừng'."
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