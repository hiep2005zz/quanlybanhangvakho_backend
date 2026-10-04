# backend/app/services/auth_service.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from fastapi import HTTPException, status
from app.core.config import settings
from app.core.security import verify_password, create_access_token
from app.models.user import USERS_DB, UserInDB, load_users_db
from app.schemas.auth import TokenResponse, UserResponse

# Global track for failed attempts by username (even if user doesn't exist in DB, to prevent enumeration)
FAILED_ATTEMPTS: dict[str, dict] = {}

def get_current_utc() -> datetime:
    return datetime.now(timezone.utc)

def reset_failed_attempts(username: str) -> None:
    uname = username.strip().lower()
    FAILED_ATTEMPTS[uname] = {"count": 0, "locked_until": None}

def authenticate_user(username: str, password: str) -> Tuple[Optional[TokenResponse], Optional[HTTPException]]:
    uname = username.strip().lower()
    now = get_current_utc()

    # Đồng bộ dữ liệu người dùng mới nhất từ file JSON (nếu có tài khoản mới được tạo từ endpoint)
    load_users_db()

    # Track attempts state
    attempt_record = FAILED_ATTEMPTS.get(uname, {"count": 0, "locked_until": None})

    # 1. Check if currently locked out
    if attempt_record["locked_until"] and attempt_record["locked_until"] > now:
        remaining_seconds = int((attempt_record["locked_until"] - now).total_seconds())
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        return None, HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": f"Tài khoản đã bị tạm khóa do nhập sai quá nhiều lần. Vui lòng thử lại sau {remaining_minutes} phút.",
                "lock_remaining_seconds": remaining_seconds
            }
        )

    # 2. Check user in database by username or email
    user: Optional[UserInDB] = USERS_DB.get(uname)
    if not user:
        for u in USERS_DB.values():
            if u.email and u.email.strip().lower() == uname:
                user = u
                break

    # Nếu tài khoản bị Quản trị viên khóa -> Trả về 403 Forbidden: luôn hiển thị thông báo chung bảo mật
    if user and (getattr(user, "status", "ACTIVE") == "LOCKED" or not user.is_active):
        return None, HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị khóa. Vui lòng liên hệ với quản lý."
        )


    # Verify password if user exists
    is_valid = False
    if user and user.is_active:
        is_valid = verify_password(password, user.hashed_password)

    if not is_valid:
        # Increment failed attempts
        attempt_record["count"] += 1
        current_count = attempt_record["count"]

        if current_count >= settings.MAX_FAILED_ATTEMPTS:
            # Lock for 15 minutes
            attempt_record["locked_until"] = now + timedelta(minutes=settings.LOCKOUT_MINUTES)
            attempt_record["count"] = 0  # reset count for next cycle
            FAILED_ATTEMPTS[uname] = attempt_record
            remaining_seconds = settings.LOCKOUT_MINUTES * 60
            return None, HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "message": "Tài khoản đã bị tạm khóa 15 phút do nhập sai 5 lần liên tiếp.",
                    "lock_remaining_seconds": remaining_seconds
                }
            )
        else:
            FAILED_ATTEMPTS[uname] = attempt_record
            remaining_tries = settings.MAX_FAILED_ATTEMPTS - current_count
            return None, HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "message": "Tên đăng nhập hoặc mật khẩu không chính xác.",
                    "remaining_attempts": remaining_tries
                }
            )

    # 3. Successful login -> reset failed attempts
    FAILED_ATTEMPTS[uname] = {"count": 0, "locked_until": None}

    access_token = create_access_token(
        subject=user.username,
        role=user.role,
        token_version=getattr(user, "token_version", 1)
    )
    from app.core.rbac import get_roles_permissions, ROLE_DETAILS
    roles = user.get_roles()
    primary_role = roles[0] if roles else user.role
    role_info = ROLE_DETAILS.get(primary_role, {})
    role_titles = [ROLE_DETAILS.get(r, {}).get("title", r) for r in roles]
    can_view_cost = any(ROLE_DETAILS.get(r, {}).get("can_view_cost", False) for r in roles)
    can_write_inventory = any(ROLE_DETAILS.get(r, {}).get("can_write_inventory", False) for r in roles)

    branch = getattr(user, "branch", "Kho Tổng Hà Nội")
    warehouse_name = branch if ("kho" in branch.lower() or "toàn quốc" in branch.lower()) else branch
    territory_name = branch if ("khu vực" in branch.lower() or "toàn quốc" in branch.lower() or "miền" in branch.lower()) else branch
    phone_val = getattr(user, "phone", None)
    email_val = getattr(user, "email", None)
    user_id = getattr(user, "id", None)

    token_resp = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse(
            id=user_id,
            username=user.username,
            full_name=user.full_name,
            email=email_val,
            phone=phone_val,
            phone_number=phone_val,
            role=primary_role,
            roles=roles,
            role_titles=role_titles,
            permissions=get_roles_permissions(roles),
            role_title=role_info.get("title", primary_role),
            branch=branch,
            warehouse_name=warehouse_name,
            territory_name=territory_name,
            can_view_cost=can_view_cost,
            can_write_inventory=can_write_inventory,
            avatar_url=getattr(user, "avatar_url", None),
        )
    )
    return token_resp, None
