# backend/app/api/v1/endpoints/auth.py
import re
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.deps import get_current_user, get_current_user_for_refresh, get_current_token
from app.core.config import settings
from app.core.security import create_access_token, revoke_token, verify_password, get_password_hash
from app.models.user import USERS_DB, save_users_db, load_users_db
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    UserResponse,
    MessageResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    RoleMatrixResponse,
    RoleInfoItem,
)
from app.core.rbac import ROLE_DETAILS, get_role_permissions, Role
from app.services.auth_service import authenticate_user, reset_failed_attempts
from app.services.password_reset import password_reset_service

router = APIRouter()

@router.post("/login", response_model=TokenResponse)
def login(login_data: LoginRequest):
    token_resp, error = authenticate_user(login_data.username, login_data.password)
    if error:
        raise error
    return token_resp

@router.post("/refresh", response_model=TokenResponse)
def refresh_session(current_user: UserResponse = Depends(get_current_user_for_refresh)):
    """
    Cơ chế tự động làm mới phiên (Silent Refresh / Sliding Expiration).
    Gia hạn thêm thời gian hiệu lực cho người dùng khi còn hoạt động hoặc thao tác.
    """
    user_db = USERS_DB.get(current_user.username)
    token_version = getattr(user_db, "token_version", 1) if user_db else 1
    new_access_token = create_access_token(
        subject=current_user.username,
        role=current_user.role,
        token_version=token_version
    )
    return TokenResponse(
        access_token=new_access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=current_user
    )

@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    data: ChangePasswordRequest,
    current_user: UserResponse = Depends(get_current_user)
):
    """
    Đổi mật khẩu tài khoản đang đăng nhập:
    - Bắt buộc nhập mật khẩu hiện tại
    - Mật khẩu mới tối thiểu 8 ký tự, có cả chữ và số
    - Thu hồi các phiên đăng nhập khác bằng cách tăng token_version
    """
    current_pwd = data.current_password.strip() if data.current_password else ""
    if not current_pwd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng nhập mật khẩu hiện tại."
        )

    user_db = USERS_DB.get(current_user.username)
    if not user_db or not user_db.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Người dùng không hợp lệ hoặc đã bị khóa."
        )

    if not verify_password(current_pwd, user_db.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu hiện tại không chính xác."
        )

    new_pwd = data.new_password.strip() if data.new_password else ""
    if len(new_pwd) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải có tối thiểu 8 ký tự."
        )

    if not re.search(r'[A-Za-z]', new_pwd) or not re.search(r'\d', new_pwd):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải chứa cả chữ cái và chữ số."
        )

    if current_pwd == new_pwd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới không được trùng với mật khẩu hiện tại."
        )

    if data.confirm_password is not None and data.confirm_password.strip() != new_pwd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Xác nhận mật khẩu mới không khớp."
        )

    user_db.hashed_password = get_password_hash(new_pwd)
    user_db.token_version = getattr(user_db, "token_version", 1) + 1
    save_users_db()
    reset_failed_attempts(user_db.username)

    new_token = create_access_token(
        subject=user_db.username,
        role=user_db.role,
        token_version=user_db.token_version
    )

    return ChangePasswordResponse(
        status="success",
        message="Đổi mật khẩu thành công! Tất cả các phiên đăng nhập khác đã được thu hồi an toàn.",
        access_token=new_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=current_user
    )

@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest) -> MessageResponse:
    """
    Yêu cầu đặt lại mật khẩu khi quên qua email:
    - Nhận liên kết đặt lại mật khẩu có hiệu lực 30 phút
    - Kiểm tra email/username có tồn tại và khớp với tài khoản trước khi gửi email
    """
    msg = password_reset_service.request_reset(str(payload.email))
    return MessageResponse(message=msg)

@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    """
    Đặt lại mật khẩu mới qua token:
    - Liên kết chỉ dùng được 1 lần
    - Tự động mã hóa mật khẩu mới và hủy các phiên cũ
    """
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải có tối thiểu 8 ký tự."
        )
    msg = password_reset_service.reset_password(payload.token, payload.new_password)
    return MessageResponse(message=msg)

@router.post("/logout", response_model=MessageResponse)
def logout(
    token: str = Depends(get_current_token),
    _: UserResponse = Depends(get_current_user)
):
    """
    Hủy / thu hồi hiệu lực của token hiện tại ngay lập tức phía server (Blacklist).
    Token này sẽ không thể sử dụng để gọi bất kỳ API nào khác nữa.
    """
    revoke_token(token)
    return MessageResponse(
        message="Đăng xuất thành công. Phiên làm việc đã bị thu hồi ngay lập tức trên máy chủ."
    )

@router.get("/me", response_model=UserResponse)
def get_me(current_user: UserResponse = Depends(get_current_user)):
    return current_user

@router.get("/roles-matrix", response_model=RoleMatrixResponse)
def get_roles_matrix():
    """
    Khai báo ma trận phân quyền 7 vai trò nghiệp vụ (AC 1).
    Endpoint công khai cung cấp thông tin quyền hạn và mô tả chi tiết của từng vai trò.
    """
    items = []
    for role_enum in Role:
        role_key = role_enum.value
        if role_key == Role.CUSTOMER.value:
            continue
        info = ROLE_DETAILS.get(role_key, {})
        items.append(
            RoleInfoItem(
                role=role_key,
                title=info.get("title", role_key),
                badge_color=info.get("badge_color", "#64748b"),
                description=info.get("description", ""),
                can_view_cost=info.get("can_view_cost", False),
                can_write_inventory=info.get("can_write_inventory", False),
                permissions=get_role_permissions(role_key),
            )
        )
    return RoleMatrixResponse(roles=items, total_roles=len(items))

@router.get("/admin-contact")
def get_admin_contact():
    """
    Lấy email liên hệ chính thức của Quản trị viên hệ thống (Admin).
    Dữ liệu lấy trực tiếp từ tài khoản Admin trong hệ thống hoặc cấu hình mail hệ thống.
    """
    admin_user = USERS_DB.get("admin")
    admin_email = (admin_user.email if admin_user and admin_user.email else None) or settings.MAIL_FROM or "daongochiep645@gmail.com"
    admin_name = admin_user.full_name if admin_user else "Nguyễn Quản Trị"
    return {"admin_email": admin_email, "admin_name": admin_name}
