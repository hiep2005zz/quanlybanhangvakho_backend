# backend/app/api/v1/endpoints/users.py
import re
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.deps import get_current_user, require_permission
from app.core.rbac import (
    Role,
    Permission,
    ROLE_DETAILS,
    is_warehouse_role,
    is_specific_warehouse,
    SPECIFIC_WAREHOUSES,
)
from app.core.security import get_password_hash
from app.models.user import USERS_DB, UserInDB, get_next_user_id, save_users_db, normalize_phone, is_phone_taken
from app.models.dealer import sync_dealer_for_user
from app.schemas.auth import UserResponse
from app.schemas.user import UserCreate, UserUpdate, UserItemResponse, UserListResponse, CustomerCreate, CustomerCreateResponse
from app.services.customer_account import generate_temporary_password, send_customer_credentials

router = APIRouter()

from datetime import datetime, timezone
from app.models.dealer import count_dealers_by_sale_id, get_dealers_by_sale_id, DEALERS_DB, save_dealers_db, load_dealers_db


# Ràng buộc TC-01: Chỉ sales hoặc sales_manager mới được tiếp nhận bàn giao đại lý
SALES_ROLES = {"sales", "sales_manager"}

def check_region_match(user_branch: str, dealer_address: str, source_user_branch: str = "") -> bool:
    """Toàn quốc, Trụ sở chính, cùng Kho/Chi nhánh với người nguồn được nhận; ngược lại phải khớp vùng miền."""
    ub = (user_branch or "").lower().strip()
    if "toàn quốc" in ub or "trụ sở" in ub:
        return True
    
    # Nếu người nhận cùng kho/chi nhánh với người nguồn (ví dụ cùng ở Kho Tổng Hà Nội)
    sb = (source_user_branch or "").lower().strip()
    if sb and ub and sb == ub:
        return True

    addr = (dealer_address or "").lower()
    # Khu vực Miền Bắc
    if ("miền bắc" in ub or "hà nội" in ub or "hải phòng" in ub) and any(x in addr for x in ["hà nội", "hải phòng", "bắc", "quảng ninh"]):
        return True
    # Khu vực Miền Trung
    if ("miền trung" in ub or "đà nẵng" in ub or "huế" in ub) and any(x in addr for x in ["đà nẵng", "huế", "quảng", "nghệ an", "trung"]):
        return True
    # Khu vực Miền Nam
    if ("miền nam" in ub or "hồ chí minh" in ub or "tp. hcm" in ub) and any(x in addr for x in ["hồ chí minh", "tp. hcm", "bình dương", "nam", "tân bình"]):
        return True
    return False



def _build_user_item(u: UserInDB) -> UserItemResponse:
    roles = u.get_roles()
    primary_role = roles[0] if roles else u.role
    role_info = ROLE_DETAILS.get(primary_role, {})
    role_titles = [ROLE_DETAILS.get(r, {}).get("title", r) for r in roles]

    handover_count = count_dealers_by_sale_id(u.id) if (not u.is_active or getattr(u, "status", "ACTIVE") == "LOCKED") else 0
    locked_at_str = u.locked_at.isoformat() if getattr(u, "locked_at", None) else None
    user_status = getattr(u, "status", "ACTIVE")
    if not u.is_active:
        user_status = "LOCKED"

    can_view_cost = any(ROLE_DETAILS.get(r, {}).get("can_view_cost", False) for r in roles)
    can_write_inventory = any(ROLE_DETAILS.get(r, {}).get("can_write_inventory", False) for r in roles)

    display_branch = getattr(u, "branch", None) or ("Chưa phân công" if u.role == Role.CUSTOMER.value else "Kho Tổng Hà Nội")
    return UserItemResponse(
        id=u.id,
        username=u.username,
        full_name=u.full_name,
        email=u.email,
        phone=getattr(u, "phone", None),
        role=primary_role,
        roles=roles,
        role_title=role_info.get("title", primary_role),
        role_titles=role_titles,
        branch=display_branch,
        is_active=u.is_active and user_status == "ACTIVE",
        status=user_status,
        lock_reason=getattr(u, "lock_reason", None),
        locked_at=locked_at_str,
        dealers_needing_handover=handover_count,
        can_view_cost=can_view_cost,
        can_write_inventory=can_write_inventory,
        badge_color=role_info.get("badge_color", "#64748b"),
        avatar_url=getattr(u, "avatar_url", None),
    )

@router.get("", response_model=UserListResponse)
def list_users(
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Lấy danh sách người dùng trong hệ thống (Chỉ dành cho Quản trị viên - Zero-Trust).
    """
    load_dealers_db()
    users = [_build_user_item(u) for u in sorted(USERS_DB.values(), key=lambda x: x.id)]
    return UserListResponse(users=users, total=len(users))

@router.post("", response_model=UserItemResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    data: UserCreate,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Tạo người dùng mới trong hệ thống:
    - Hỗ trợ gán nhiều vai trò cùng lúc (roles: List[str]).
    - RÀNG BUỘC KHO: Người dùng có vai trò Kho (Thủ kho hoặc Quản lý kho) bắt buộc phải gắn với ít nhất 1 kho cụ thể.
    """
    valid_roles = [r.value for r in Role]
    
    # Chuẩn hóa danh sách roles
    chosen_roles: List[str] = []
    if data.roles:
        chosen_roles = [r.strip() for r in data.roles if r and r.strip()]
    elif data.role:
        chosen_roles = [data.role.strip()]

    if not chosen_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng chọn ít nhất một vai trò cho người dùng."
        )

    # Validate các vai trò phải hợp lệ trong 7 vai trò hệ thống
    for r in chosen_roles:
        if r not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Vai trò '{r}' không hợp lệ. Vui lòng chọn trong các vai trò hệ thống."
            )

    primary_role = chosen_roles[0]
    assigned_branch = (data.branch or "Kho Tổng Hà Nội").strip()

    # RÀNG BUỘC KHO: Người dùng có vai trò Kho phải gắn với ít nhất 1 kho cụ thể
    if is_warehouse_role(chosen_roles):
        if not is_specific_warehouse(assigned_branch):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Người dùng có vai trò Kho bắt buộc phải gắn với ít nhất 1 kho cụ thể (Ví dụ: {', '.join(SPECIFIC_WAREHOUSES)})."
            )

    # Chuẩn hóa username
    raw_username = (data.username or data.email.split("@")[0]).strip().lower()
    clean_username = re.sub(r'[^a-zA-Z0-9_\-\.]', '', raw_username)
    if not clean_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tên đăng nhập không hợp lệ."
        )

    if clean_username in USERS_DB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tên đăng nhập '{clean_username}' đã tồn tại trong hệ thống."
        )

    clean_email = data.email.strip().lower()
    for existing in USERS_DB.values():
        if existing.email and existing.email.strip().lower() == clean_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Email '{clean_email}' đã được sử dụng bởi người dùng khác."
            )

    clean_phone = normalize_phone(getattr(data, "phone", None))
    if clean_phone and is_phone_taken(clean_phone):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Số điện thoại này đã có trên hệ thống vui lòng đổi số khác"
        )

    new_user = UserInDB(
        id=get_next_user_id(),
        username=clean_username,
        full_name=data.full_name.strip(),
        email=clean_email,
        phone=clean_phone if clean_phone else (data.phone.strip() if getattr(data, "phone", None) else None),
        role=primary_role,
        roles=chosen_roles,
        hashed_password=get_password_hash(data.password),
        branch=assigned_branch,
        is_active=True,
    )
    USERS_DB[clean_username] = new_user
    save_users_db()
    
    # Sync dealer if role is customer
    is_customer = Role.CUSTOMER.value in chosen_roles
    sync_dealer_for_user(
        user_id=new_user.id,
        full_name=new_user.full_name,
        email=new_user.email,
        phone=getattr(data, "phone", None) or getattr(new_user, "phone", None),
        is_customer=is_customer
    )

    return _build_user_item(new_user)

@router.post("/customers", response_model=CustomerCreateResponse, status_code=status.HTTP_201_CREATED)
def create_customer(
    data: CustomerCreate,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Tạo tài khoản cho nhân viên kinh doanh / đại diện bán hàng (Role.CUSTOMER):
    - Tự động sinh mật khẩu tạm thời bảo mật cao.
    - Gửi thông tin đăng nhập qua Email đến nhân viên.
    - Lưu số điện thoại liên hệ để quản lý thuận tiện.
    """
    clean_email = data.email.strip().lower()
    clean_phone = normalize_phone(data.phone)
    if clean_phone and is_phone_taken(clean_phone):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Số điện thoại này đã có trên hệ thống vui lòng đổi số khác"
        )

    # Kiểm tra trùng email
    for existing in USERS_DB.values():
        if existing.email and existing.email.strip().lower() == clean_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Email '{clean_email}' đã được sử dụng trong hệ thống."
            )

    # Sinh hoặc kiểm tra username
    if data.username and data.username.strip():
        clean_username = data.username.strip().lower()
        if not re.match(r'^[a-zA-Z0-9_.-]+$', clean_username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tên đăng nhập chỉ được chứa chữ cái, số, dấu gạch dưới, gạch ngang hoặc chấm."
            )
        if clean_username in USERS_DB:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tên đăng nhập '{clean_username}' đã tồn tại."
            )
    else:
        prefix = re.sub(r'[^a-z0-9]', '', clean_email.split('@')[0].lower())
        if not prefix:
            prefix = "sales"
        clean_username = prefix
        counter = 1
        while clean_username in USERS_DB:
            clean_username = f"{prefix}{counter}"
            counter += 1

    temp_password = generate_temporary_password()

    # Xác định vai trò gán cho tài khoản
    chosen_roles = [Role.CUSTOMER.value]
    if data.roles:
        clean_roles = [r.strip() for r in data.roles if r and r.strip()]
        if clean_roles:
            chosen_roles = clean_roles
    elif data.role and data.role.strip():
        chosen_roles = [data.role.strip()]

    # Validate roles nếu truyền vào
    valid_roles = {r.value for r in Role}
    for r in chosen_roles:
        if r not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Vai trò '{r}' không hợp lệ. Vui lòng chọn trong các vai trò hệ thống."
            )

    # Nếu có vai trò chính thức khác customer, loại bỏ customer
    clean_official_roles = [r for r in chosen_roles if r != Role.CUSTOMER.value]
    if clean_official_roles:
        chosen_roles = clean_official_roles

    primary_role = chosen_roles[0]

    # Chi nhánh / Kho
    assigned_branch = (data.branch or "").strip()
    if not assigned_branch:
        if Role.CUSTOMER.value in chosen_roles:
            assigned_branch = "Chưa phân công"
        else:
            assigned_branch = "Kho Tổng Hà Nội"

    # Ràng buộc vai trò kho
    if is_warehouse_role(chosen_roles):
        if not is_specific_warehouse(assigned_branch):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Người dùng có vai trò Kho bắt buộc phải gắn với ít nhất 1 kho cụ thể (Ví dụ: {', '.join(SPECIFIC_WAREHOUSES)})."
            )

    new_user = UserInDB(
        id=get_next_user_id(),
        username=clean_username,
        full_name=data.full_name.strip(),
        email=clean_email,
        phone=clean_phone,
        role=primary_role,
        roles=chosen_roles,
        hashed_password=get_password_hash(temp_password),
        branch=assigned_branch,
        is_active=True,
    )
    USERS_DB[clean_username] = new_user
    save_users_db()

    # Sync dealer if role is customer
    is_customer = Role.CUSTOMER.value in chosen_roles
    sync_dealer_for_user(
        user_id=new_user.id,
        full_name=new_user.full_name,
        email=new_user.email,
        phone=new_user.phone,
        is_customer=is_customer
    )

    email_sent = send_customer_credentials(
        email=clean_email,
        full_name=new_user.full_name,
        username=clean_username,
        password=temp_password,
        phone=clean_phone,
    )

    msg = "Tạo tài khoản kinh doanh thành công!"
    if not email_sent:
        msg += " (Lưu ý: Không thể gửi email tự động, vui lòng bàn giao trực tiếp)."

    return CustomerCreateResponse(
        user=_build_user_item(new_user),
        email_sent=email_sent,
        message=msg
    )

@router.put("/{username}", response_model=UserItemResponse)
def update_user(
    username: str,
    data: UserUpdate,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Cập nhật thông tin người dùng:
    - BẢO VỆ ADMIN: Không thể tự thu hồi vai trò quản trị (Admin) của chính mình.
    - BẢO VỆ ADMIN: Không được tự khóa tài khoản Admin của chính mình.
    - RÀNG BUỘC KHO: Người dùng có vai trò Kho phải gắn với ít nhất 1 kho cụ thể.
    """
    target_username = username.strip().lower()
    user = USERS_DB.get(target_username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy người dùng '{username}'."
        )

    is_self = (current_user.username.strip().lower() == target_username)
    current_user_roles = user.get_roles()

    # Xác định danh sách roles mới (nếu có cập nhật)
    new_roles: Optional[List[str]] = None
    if data.roles is not None:
        new_roles = [r.strip() for r in data.roles if r and r.strip()]
        if not new_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Người dùng phải có ít nhất 1 vai trò."
            )
    elif data.role is not None:
        new_roles = [data.role.strip()]

    # Kiểm tra tính hợp lệ của các roles mới
    valid_roles = [r.value for r in Role]
    if new_roles is not None:
        for r in new_roles:
            if r not in valid_roles:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Vai trò '{r}' không hợp lệ."
                )

        # BẢO VỆ ADMIN: Không thể tự thu hồi vai trò quản trị của chính mình
        if is_self and Role.SYSTEM_ADMIN.value in current_user_roles and Role.SYSTEM_ADMIN.value not in new_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Không thể tự thu hồi vai trò quản trị của chính mình (Không được tự hạ quyền Admin của chính mình)."
            )

    # Xác định branch mới
    target_branch = data.branch.strip() if data.branch is not None else getattr(user, "branch", "")
    effective_roles = new_roles if new_roles is not None else current_user_roles

    # RÀNG BUỘC KHO: Người dùng có vai trò Kho bắt buộc phải gắn với ít nhất 1 kho cụ thể
    if is_warehouse_role(effective_roles):
        if not is_specific_warehouse(target_branch):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Người dùng có vai trò Kho bắt buộc phải gắn với ít nhất 1 kho cụ thể (Ví dụ: {', '.join(SPECIFIC_WAREHOUSES)})."
            )

    # Kiểm tra ràng buộc không được tự khóa tài khoản của chính mình
    wants_to_lock = (data.is_active is False) or (data.status == "LOCKED")
    if is_self and wants_to_lock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không được tự khóa tài khoản Admin của chính mình."
        )

    # AC 2: Bắt buộc ghi lý do khi khóa tài khoản
    if wants_to_lock:
        reason = (data.lock_reason or "").strip()
        if not reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bắt buộc phải nhập lý do khi khóa tài khoản (Ví dụ: Nghỉ việc, chuyển công tác...)."
            )
        user.is_active = False
        user.status = "LOCKED"
        user.lock_reason = reason
        user.locked_at = datetime.now(timezone.utc)
        # AC 1: Tự động tăng token_version để thu hồi ngay lập tức mọi phiên đang mở
        user.token_version = getattr(user, "token_version", 1) + 1
    elif data.is_active is True or data.status == "ACTIVE":
        # Mở khóa tài khoản
        user.is_active = True
        user.status = "ACTIVE"
        user.lock_reason = None
        user.locked_at = None

    if new_roles is not None:
        # Nếu đã gán bất kỳ vai trò chính thức nào, tự động loại bỏ nhãn tạm 'customer'
        clean_new_roles = [r for r in new_roles if r != Role.CUSTOMER.value]
        if clean_new_roles:
            user.roles = clean_new_roles
            user.role = clean_new_roles[0]
        else:
            user.roles = [Role.CUSTOMER.value]
            user.role = Role.CUSTOMER.value

    if data.full_name is not None and data.full_name.strip():
        user.full_name = data.full_name.strip()

    if data.email is not None and data.email.strip():
        clean_email = data.email.strip().lower()
        for uname, existing in USERS_DB.items():
            if uname != target_username and existing.email and existing.email.strip().lower() == clean_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Email '{clean_email}' đã được sử dụng bởi người dùng khác."
                )
        user.email = clean_email

    if data.phone is not None:
        raw_phone = data.phone.strip()
        clean_phone = normalize_phone(raw_phone)
        if clean_phone and is_phone_taken(clean_phone, exclude_username=target_username):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Số điện thoại này đã có trên hệ thống vui lòng đổi số khác"
            )
        user.phone = clean_phone if clean_phone else (raw_phone if raw_phone else None)

    if data.branch is not None:
        user.branch = data.branch.strip()

    if data.password is not None and len(data.password.strip()) >= 3:
        user.hashed_password = get_password_hash(data.password.strip())
        user.token_version = getattr(user, "token_version", 1) + 1

    save_users_db()

    # Đồng bộ sang SQL Server Database nếu có
    try:
        from app.core.database import SessionLocal
        from app.models.entities import UserEntity
        import json as _json
        if SessionLocal:
            db_session = SessionLocal()
            try:
                db_u = db_session.query(UserEntity).filter(UserEntity.username == target_username).first()
                if db_u:
                    if new_roles is not None:
                        db_u.role = user.role
                        db_u.roles = _json.dumps(user.roles)
                    if data.full_name is not None and data.full_name.strip():
                        db_u.full_name = user.full_name
                    if data.email is not None and data.email.strip():
                        db_u.email = user.email
                    if data.phone is not None:
                        db_u.phone = user.phone
                    if data.branch is not None:
                        db_u.branch = user.branch
                    if data.is_active is not None:
                        db_u.is_active = user.is_active
                    db_session.commit()
            finally:
                db_session.close()
    except Exception as e:
        print(f"Warning: could not sync user to DB: {e}")

    # Sync dealer if role is customer
    is_customer = Role.CUSTOMER.value in user.get_roles()
    sync_dealer_for_user(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        is_customer=is_customer
    )

    return _build_user_item(user)

@router.delete("/{username}")
def delete_user(
    username: str,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Xóa tài khoản người dùng:
    - BẢO VỆ ADMIN: Tuyệt đối không được tự xóa tài khoản Admin của chính mình!
    """
    target_username = username.strip().lower()
    if target_username not in USERS_DB:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy người dùng '{username}'."
        )

    # BẢO VỆ ADMIN: Chặn tự xóa chính mình
    if current_user.username.strip().lower() == target_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không được tự xóa tài khoản Admin của chính mình."
        )

    del USERS_DB[target_username]
    save_users_db()
    return {
        "status": "success",
        "message": f"Đã xóa thành công người dùng '{target_username}' khỏi hệ thống."
    }

@router.get("/{username}/dealers")
def get_user_dealers(
    username: str,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Lấy danh sách các đại lý do nhân viên này phụ trách để kiểm tra hoặc bàn giao.
    """
    target_username = username.strip().lower()
    user = USERS_DB.get(target_username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy người dùng '{username}'."
        )

    dealers = get_dealers_by_sale_id(user.id)
    is_locked = (not user.is_active) or (getattr(user, "status", "ACTIVE") == "LOCKED")
    return {
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "is_locked": is_locked,
        "lock_reason": getattr(user, "lock_reason", None),
        "total_dealers": len(dealers),
        "dealers": [
            {
                "id": d.id,
                "code": d.code,
                "name": d.name,
                "phone": d.phone,
                "email": d.email,
                "address": d.address,
                "assigned_sale_id": d.assigned_sale_id,
                "needs_handover": is_locked
            }
            for d in dealers
        ]
    }

class HandoverRequest(BaseModel):
    new_sale_username: str

@router.post("/{username}/handover")
def handover_dealers(
    username: str,
    data: HandoverRequest,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Bàn giao toàn bộ đại lý của nhân viên bị khóa sang cho nhân viên mới.
    """
    target_username = username.strip().lower()
    old_user = USERS_DB.get(target_username)
    if not old_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy nhân viên nguồn '{username}'."
        )

    new_uname = data.new_sale_username.strip().lower()
    new_user = USERS_DB.get(new_uname)
    if not new_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy nhân viên mới '{data.new_sale_username}'."
        )

    if not new_user.is_active or getattr(new_user, "status", "ACTIVE") == "LOCKED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nhân viên mới '{new_user.full_name}' cũng đang bị khóa tài khoản! Vui lòng chọn nhân viên đang hoạt động."
        )

    # TC-01: Ràng buộc 1 - Vai trò (Role): Chỉ sales hoặc sales_manager
    recipient_roles = set(new_user.get_roles())
    if not recipient_roles.intersection(SALES_ROLES):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chỉ có thể bàn giao đại lý cho nhân sự thuộc bộ phận Bán hàng / Kinh doanh (sales, sales_manager). Nhân viên '{new_user.full_name}' không hợp lệ."
        )

    # TC-01: Ràng buộc 2 - Địa bàn (Region): Khớp khu vực phụ trách
    user_dealers = [d for d in DEALERS_DB.values() if d.assigned_sale_id == old_user.id]
    for d in user_dealers:
        if not check_region_match(getattr(new_user, "branch", ""), d.address or "", getattr(old_user, "branch", "")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Địa bàn không phù hợp! Đại lý '{d.name}' ({d.address}) không thuộc khu vực phụ trách của '{new_user.full_name}' ({getattr(new_user, 'branch', '')})."
            )



    # Chuyển toàn bộ đại lý sang nhân viên mới
    transferred_count = 0
    for d in DEALERS_DB.values():
        if d.assigned_sale_id == old_user.id:
            d.assigned_sale_id = new_user.id
            transferred_count += 1

    # Lưu bền vững DEALERS_DB vào SQL Server và file JSON dự phòng
    save_dealers_db()

    return {

        "status": "success",
        "message": f"Đã bàn giao thành công {transferred_count} đại lý từ '{old_user.full_name}' sang '{new_user.full_name}'.",
        "transferred_count": transferred_count,
        "new_assigned_sale_id": new_user.id,
        "new_assigned_sale_name": new_user.full_name
    }

