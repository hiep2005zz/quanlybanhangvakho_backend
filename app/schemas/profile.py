# backend/app/schemas/profile.py
import re
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

class UserProfileResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: str
    phone_number: Optional[str] = None
    phone: Optional[str] = None
    role: str
    roles: List[str] = []
    role_title: Optional[str] = None
    warehouse_name: Optional[str] = None
    territory_name: Optional[str] = None
    branch: Optional[str] = None
    avatar_url: Optional[str] = None

class ProfileAvatarResponse(BaseModel):
    status: str = "success"
    message: str
    avatar_url: str
    thumbnail_url: str
    user: UserProfileResponse

class UpdateProfileRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255, description="Họ và tên")
    phone_number: str = Field(..., min_length=1, max_length=20, description="Số điện thoại")

    # Ràng buộc bảo mật: Bỏ qua / Không nhận các trường khác (như username, email, role,...)
    model_config = {
        "extra": "ignore"
    }

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Họ và tên không được để trống.")
        return v_clean

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: str) -> str:
        v_clean = v.strip()
        vn_phone_pattern = r"^(0)(3[2-9]|5[25689]|7[06-9]|8[1-9]|9[0-9])[0-9]{7}$"
        if not re.match(vn_phone_pattern, v_clean):
            raise ValueError("Số điện thoại không đúng định dạng nhà mạng Việt Nam (yêu cầu 10 chữ số, bắt đầu bằng 03, 05, 07, 08, 09).")
        return v_clean
