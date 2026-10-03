# backend/app/api/v1/endpoints/profile.py
"""
User Story SCRUM-27 (S2-02): Xem và cập nhật hồ sơ cá nhân.
- GET /api/v1/me (hoặc /api/v1/profile): Xem thông tin hồ sơ của tài khoản đang đăng nhập.
- PUT /api/v1/me (hoặc PATCH /api/v1/profile): Cập nhật họ tên và số điện thoại Việt Nam.
"""
import os
import io
import uuid
from typing import Optional
from PIL import Image, ImageOps
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rbac import ROLE_DETAILS
from app.models.entities import UserEntity
from app.models.user import USERS_DB, save_users_db
from app.schemas.auth import UserResponse
from app.schemas.profile import UserProfileResponse, UpdateProfileRequest, ProfileAvatarResponse
from app.services.audit_service import log_audit_event

router = APIRouter()


def _build_profile_response(username: str, db: Optional[Session] = None) -> UserProfileResponse:
    """Helper xây dựng đối tượng UserProfileResponse từ USERS_DB hoặc SQL Server DB."""
    user_in_mem = USERS_DB.get(username.lower())
    
    # Ưu tiên lấy thông tin mới nhất từ SQL Database nếu có
    db_user: Optional[UserEntity] = None
    if db is not None:
        try:
            db_user = db.query(UserEntity).filter(UserEntity.username == username).first()
        except Exception:
            db_user = None

    user_id = (user_in_mem.id if user_in_mem else None) or (db_user.id if db_user else 0)
    full_name = (user_in_mem.full_name if user_in_mem and user_in_mem.full_name else None) or (db_user.full_name if db_user else username)
    email = (user_in_mem.email if user_in_mem and user_in_mem.email else None) or (db_user.email if db_user else None)
    phone = (getattr(user_in_mem, "phone", None) if user_in_mem and getattr(user_in_mem, "phone", None) else None) or (db_user.phone if db_user else None)
    role = (user_in_mem.role if user_in_mem and user_in_mem.role else None) or (db_user.role if db_user else "sales")
    roles = (user_in_mem.get_roles() if user_in_mem else None) or (db_user.get_roles() if db_user else [role])
    branch = getattr(user_in_mem, "branch", None) or (db_user.branch if db_user else None) or "Kho Tổng Hà Nội"

    role_info = ROLE_DETAILS.get(role, {})
    role_title = role_info.get("title", role)

    # Phân loại kho / địa bàn
    warehouse_name = branch if ("kho" in branch.lower() or "toàn quốc" in branch.lower()) else branch
    territory_name = branch if ("khu vực" in branch.lower() or "toàn quốc" in branch.lower() or "miền" in branch.lower()) else branch

    avatar_url = (getattr(user_in_mem, "avatar_url", None) if user_in_mem else None) or (getattr(db_user, "avatar_url", None) if db_user else None)

    return UserProfileResponse(
        id=user_id,
        username=username,
        email=email,
        full_name=full_name,
        phone_number=phone,
        phone=phone,
        role=role,
        roles=roles,
        role_title=role_title,
        warehouse_name=warehouse_name,
        territory_name=territory_name,
        branch=branch,
        avatar_url=avatar_url,
    )


@router.get("", response_model=UserProfileResponse)
def get_my_profile(
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Xem thông tin hồ sơ cá nhân của tài khoản đang đăng nhập:
    - Trả về: id, username, email, full_name, phone_number, role, warehouse_name, territory_name.
    - Cho phép cả 7 vai trò truy cập (chỉ cần đăng nhập hợp lệ).
    """
    return _build_profile_response(current_user.username, db=db)


@router.put("", response_model=UserProfileResponse)
@router.patch("", response_model=UserProfileResponse)
def update_my_profile(
    data: UpdateProfileRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cập nhật thông tin hồ sơ cá nhân:
    - CHỈ cho phép cập nhật: full_name và phone_number.
    - Bắt buộc kiểm tra định dạng số điện thoại Việt Nam (10 chữ số, đúng đầu số).
    - RÀNG BUỘC BẢO MẬT: Tuyệt đối không thay đổi username, email, role, warehouse, territory.
    """
    username = current_user.username.lower()
    user_in_mem = USERS_DB.get(username)
    if not user_in_mem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài khoản người dùng."
        )

    old_full_name = user_in_mem.full_name
    old_phone = getattr(user_in_mem, "phone", None)

    # 1. Cập nhật trong bộ nhớ USERS_DB
    user_in_mem.full_name = data.full_name
    user_in_mem.phone = data.phone_number

    # 2. Cập nhật trong cơ sở dữ liệu SQL Server (nếu kết nối được)
    if db is not None:
        try:
            db_user = db.query(UserEntity).filter(UserEntity.username == current_user.username).first()
            if db_user:
                db_user.full_name = data.full_name
                db_user.phone = data.phone_number
                db.commit()
                db.refresh(db_user)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"Warning: Failed to update UserEntity directly in DB: {e}")

    # 3. Đồng bộ lại toàn bộ dữ liệu người dùng
    save_users_db()

    # 4. Ghi vết nhật ký thao tác (Audit Log)
    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="USER_UPDATE",
            entity_type="User",
            entity_id=current_user.username,
            old_val={"full_name": old_full_name, "phone": old_phone},
            new_val={"full_name": data.full_name, "phone": data.phone_number},
            reason="Cập nhật hồ sơ cá nhân",
            request=request,
        )
    except Exception as e:
        print(f"Audit log error: {e}")

    return _build_profile_response(current_user.username, db=db)


MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}


@router.post("/avatar", response_model=ProfileAvatarResponse)
async def upload_profile_avatar(
    request: Request,
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Tải lên ảnh đại diện cá nhân:
    - Chấp nhận JPG/PNG tối đa 2MB
    - Ảnh được cắt vuông (Square Crop: 400x400) và tạo bản thu nhỏ (Thumbnail: 120x120)
    - Cập nhật avatar_url vào hồ sơ người dùng hiện tại
    """
    # 1. Kiểm tra phần mở rộng và mime type
    filename = (file.filename or "").strip()
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS or file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Định dạng ảnh không hợp lệ. Hệ thống chỉ chấp nhận ảnh định dạng JPG hoặc PNG."
        )

    # 2. Đọc file và kiểm tra dung lượng tối đa 2MB
    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dung lượng ảnh vượt quá giới hạn cho phép (Tối đa 2MB)."
        )
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tệp ảnh tải lên rỗng hoặc không có dữ liệu."
        )

    # 3. Mở và xác thực nội dung ảnh bằng Pillow
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        # Mở lại sau verify
        img = Image.open(io.BytesIO(content))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tệp tải lên bị lỗi hoặc không phải là hình ảnh hợp lệ."
        )

    # Đảm bảo chuyển đổi RGB nếu định dạng là RGBA hoặc Palette
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    # 4. Cắt vuông (Square Center Crop) và tạo bản chuẩn (400x400) + Bản thu nhỏ (120x120)
    # Tự động crop tâm giữ tỷ lệ vuông 1:1
    width, height = img.size
    min_dim = min(width, height)
    left = (width - min_dim) // 2
    top = (height - min_dim) // 2
    right = left + min_dim
    bottom = top + min_dim

    square_img = img.crop((left, top, right, bottom))
    avatar_400 = square_img.resize((400, 400), Image.Resampling.LANCZOS)
    thumbnail_120 = square_img.resize((120, 120), Image.Resampling.LANCZOS)

    # 5. Lưu vào thư mục uploads/avatars (trong backend/uploads/avatars khớp với main.py)
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
    upload_root = os.path.join(backend_root, "uploads", "avatars")
    os.makedirs(upload_root, exist_ok=True)

    uname_clean = current_user.username.lower()

    # Dọn dẹp/xóa các ảnh avatar và thumbnail cũ của user để giải phóng dung lượng bộ nhớ
    try:
        prefix = f"{uname_clean}_"
        for existing_file in os.listdir(upload_root):
            if existing_file.startswith(prefix) and (existing_file.endswith("_square.png") or existing_file.endswith("_thumb.png")):
                try:
                    os.remove(os.path.join(upload_root, existing_file))
                except Exception as del_err:
                    print(f"Warning: Cannot delete old avatar file {existing_file}: {del_err}")
        # Đồng thời dọn dẹp trong thư mục phụ app/uploads/avatars nếu có
        legacy_root = os.path.join(backend_root, "app", "uploads", "avatars")
        if os.path.exists(legacy_root):
            for legacy_file in os.listdir(legacy_root):
                if legacy_file.startswith(prefix) and (legacy_file.endswith("_square.png") or legacy_file.endswith("_thumb.png")):
                    try:
                        os.remove(os.path.join(legacy_root, legacy_file))
                    except Exception:
                        pass
    except Exception as cleanup_err:
        print(f"Avatar cleanup error: {cleanup_err}")

    file_uuid = uuid.uuid4().hex[:12]
    avatar_filename = f"{uname_clean}_{file_uuid}_square.png"
    thumb_filename = f"{uname_clean}_{file_uuid}_thumb.png"

    avatar_path = os.path.join(upload_root, avatar_filename)
    thumb_path = os.path.join(upload_root, thumb_filename)

    avatar_400.save(avatar_path, format="PNG", optimize=True)
    thumbnail_120.save(thumb_path, format="PNG", optimize=True)

    avatar_url = f"/uploads/avatars/{avatar_filename}"
    thumb_url = f"/uploads/avatars/{thumb_filename}"

    # 6. Cập nhật vào DB và USERS_DB
    user_in_mem = USERS_DB.get(uname_clean)
    if user_in_mem:
        user_in_mem.avatar_url = avatar_url

    if db is not None:
        try:
            db_user = db.query(UserEntity).filter(UserEntity.username == current_user.username).first()
            if db_user:
                db_user.avatar_url = avatar_url
                db.commit()
                db.refresh(db_user)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"Warning: Failed to update avatar_url in SQL: {e}")

    save_users_db()

    # 7. Ghi vết nhật ký thao tác (Audit Log)
    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="USER_UPDATE",
            entity_type="User",
            entity_id=current_user.username,
            old_val={"avatar_url": getattr(user_in_mem, "avatar_url", None)},
            new_val={"avatar_url": avatar_url},
            reason="Tải lên ảnh đại diện mới",
            request=request,
        )
    except Exception as e:
        print(f"Audit log error: {e}")

    profile_resp = _build_profile_response(current_user.username, db=db)
    return ProfileAvatarResponse(
        status="success",
        message="Tải lên và cập nhật ảnh đại diện thành công",
        avatar_url=avatar_url,
        thumbnail_url=thumb_url,
        user=profile_resp,
    )
