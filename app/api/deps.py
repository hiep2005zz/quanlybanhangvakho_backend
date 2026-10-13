# backend/app/api/deps.py
from typing import Optional, List, Callable
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core.security import decode_access_token, is_token_revoked
from app.core.rbac import (
    has_permission,
    has_roles_permission,
    get_role_permissions,
    get_roles_permissions,
    ROLE_DETAILS,
    Permission
)
from app.models.user import USERS_DB, UserInDB
from app.schemas.auth import UserResponse

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_current_token(token: Optional[str] = Depends(oauth2_scheme)) -> str:
    """Lấy token xác thực thô từ header Bearer (Zero-Trust: Default Deny nếu thiếu token)."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token

def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> UserResponse:
    """
    Xác thực token JWT của người dùng:
    - Kiểm tra tính hợp lệ và thời hạn token
    - Kiểm tra trạng thái thu hồi (blacklist / logout)
    - Kiểm tra token_version (thu hồi khi đổi mật khẩu)
    - Bóc tách vai trò (Role) và nạp danh sách quyền hạn (Permissions)
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Kiểm tra xem token đã bị hủy (đăng xuất) hay chưa
    if is_token_revoked(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã bị thu hồi hoặc đã đăng xuất. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    username: str = payload.get("sub", "")
    user: Optional[UserInDB] = USERS_DB.get(username)
    if not user or not user.is_active or getattr(user, "status", "ACTIVE") == "LOCKED":
        lock_msg = "Tài khoản đã bị khóa. Vui lòng liên hệ với quản lý." if user else "Tài khoản không tồn tại."
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=lock_msg,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Kiểm tra token_version: Nếu mật khẩu đã đổi hoặc tài khoản bị khóa, token_version trong DB sẽ tăng lên,
    # các phiên cũ có token_version nhỏ hơn sẽ bị thu hồi ngay lập tức
    token_version = payload.get("token_version", 1)
    user_token_version = getattr(user, "token_version", 1)
    if token_version != user_token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã bị thu hồi do đổi mật khẩu hoặc cập nhật trạng thái tài khoản. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    roles = user.get_roles()
    primary_role = roles[0] if roles else user.role
    role_info = ROLE_DETAILS.get(primary_role, {})
    role_titles = [ROLE_DETAILS.get(r, {}).get("title", r) for r in roles]
    permissions = get_roles_permissions(roles)
    
    can_view_cost = any(ROLE_DETAILS.get(r, {}).get("can_view_cost", False) for r in roles)
    can_write_inventory = any(ROLE_DETAILS.get(r, {}).get("can_write_inventory", False) for r in roles)
    
    branch = getattr(user, "branch", "Kho Tổng Hà Nội")
    warehouse_name = branch if ("kho" in branch.lower() or "toàn quốc" in branch.lower()) else branch
    territory_name = branch if ("khu vực" in branch.lower() or "toàn quốc" in branch.lower() or "miền" in branch.lower()) else branch
    phone_val = getattr(user, "phone", None)
    email_val = getattr(user, "email", None)
    user_id = getattr(user, "id", None)
    
    return UserResponse(
        id=user_id,
        username=user.username,
        full_name=user.full_name,
        email=email_val,
        phone=phone_val,
        phone_number=phone_val,
        role=primary_role,
        roles=roles,
        role_titles=role_titles,
        permissions=permissions,
        role_title=role_info.get("title", primary_role),
        branch=branch,
        warehouse_name=warehouse_name,
        territory_name=territory_name,
        can_view_cost=can_view_cost,
        can_write_inventory=can_write_inventory,
        avatar_url=getattr(user, "avatar_url", None),
    )


def get_current_user_for_refresh(token: Optional[str] = Depends(oauth2_scheme)) -> UserResponse:
    """
    Xác thực token khi gọi API làm mới phiên (/auth/refresh):
    Cho phép một khoảng thời gian ân hạn (grace period leeway=1800s / 30 phút)
    để token hết hạn trong thời gian người dùng tạm nghỉ / chuyển tab vẫn có thể làm mới an toàn,
    miễn là token chưa bị thu hồi (đăng xuất) và token_version vẫn khớp.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if is_token_revoked(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã bị thu hồi hoặc đã đăng xuất. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    payload = decode_access_token(token, allow_expired_grace=True, grace_seconds=1800)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    username: str = payload.get("sub", "")
    user: Optional[UserInDB] = USERS_DB.get(username)
    if not user or not user.is_active or getattr(user, "status", "ACTIVE") == "LOCKED":
        lock_msg = "Tài khoản đã bị khóa. Vui lòng liên hệ với quản lý." if user else "Tài khoản không tồn tại."
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=lock_msg,
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_version = payload.get("token_version", 1)
    user_token_version = getattr(user, "token_version", 1)
    if token_version != user_token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã bị thu hồi do đổi mật khẩu hoặc cập nhật trạng thái tài khoản. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    roles = user.get_roles()
    primary_role = roles[0] if roles else user.role
    role_info = ROLE_DETAILS.get(primary_role, {})
    role_titles = [ROLE_DETAILS.get(r, {}).get("title", r) for r in roles]
    permissions = get_roles_permissions(roles)
    
    can_view_cost = any(ROLE_DETAILS.get(r, {}).get("can_view_cost", False) for r in roles)
    can_write_inventory = any(ROLE_DETAILS.get(r, {}).get("can_write_inventory", False) for r in roles)
    
    branch = getattr(user, "branch", "Kho Tổng Hà Nội")
    warehouse_name = branch if ("kho" in branch.lower() or "toàn quốc" in branch.lower()) else branch
    territory_name = branch if ("khu vực" in branch.lower() or "toàn quốc" in branch.lower() or "miền" in branch.lower()) else branch
    phone_val = getattr(user, "phone", None)
    email_val = getattr(user, "email", None)
    user_id = getattr(user, "id", None)
    
    return UserResponse(
        id=user_id,
        username=user.username,
        full_name=user.full_name,
        email=email_val,
        phone=phone_val,
        phone_number=getattr(user, "phone_number", phone_val),
        role=primary_role,
        roles=roles,
        role_titles=role_titles,
        permissions=permissions,
        role_title=role_info.get("title", primary_role),
        branch=branch,
        warehouse_name=warehouse_name,
        territory_name=territory_name,
        can_view_cost=can_view_cost,
        can_write_inventory=can_write_inventory,
        avatar_url=getattr(user, "avatar_url", None),
    )


def require_permission(permission: str) -> Callable[[UserResponse], UserResponse]:
    """
    AC 2 - Zero-Trust / Default Deny Guard:
    Bảo vệ endpoint bằng cách kiểm tra quyền hạn cụ thể.
    Kiểm tra trên toàn bộ danh sách vai trò người dùng (roles / permissions).
    Nếu user không có quyền -> Chặn ngay lập tức với HTTP 403 Forbidden.
    """
    def permission_checker(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
        user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
        if not has_roles_permission(user_roles, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Truy cập bị từ chối (403 Forbidden). Bạn thuộc vai trò '{', '.join(user_roles)}' và không có quyền '{permission}' để thực hiện thao tác này (Chính sách Zero-Trust)."
            )
        return current_user
    return permission_checker


def require_roles(allowed_roles: List[str]) -> Callable[[UserResponse], UserResponse]:
    """
    Guard kiểm tra vai trò người dùng trong danh sách cho phép.
    Nếu người dùng có ít nhất 1 vai trò trong allowed_roles -> Cho phép.
    """
    def role_checker(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
        user_roles = current_user.roles or ([current_user.role] if current_user.role else [])
        if not any(r in allowed_roles for r in user_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Truy cập bị từ chối (403 Forbidden). Chức năng này chỉ dành cho các vai trò: {', '.join(allowed_roles)}."
            )
        return current_user
    return role_checker
