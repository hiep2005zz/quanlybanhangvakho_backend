# backend/app/schemas/auth.py
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: Optional[int] = None
    username: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    phone_number: Optional[str] = None
    role: str
    roles: List[str] = []
    role_titles: List[str] = []
    permissions: List[str] = []
    role_title: Optional[str] = None
    branch: Optional[str] = None
    warehouse_name: Optional[str] = None
    territory_name: Optional[str] = None
    can_view_cost: bool = False
    can_write_inventory: bool = False
    avatar_url: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900  # Thời hạn hiệu lực tính theo giây (15 phút)
    user: UserResponse
    remaining_attempts: Optional[int] = None

class MessageResponse(BaseModel):
    message: str
    status: str = "success"

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: Optional[str] = None

class ChangePasswordResponse(BaseModel):
    status: str = "success"
    message: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    user: UserResponse

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)

class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32)
    new_password: str = Field(min_length=8, max_length=128)

class RoleInfoItem(BaseModel):
    role: str
    title: str
    badge_color: str
    description: str
    can_view_cost: bool
    can_write_inventory: bool
    permissions: List[str]

class RoleMatrixResponse(BaseModel):
    roles: List[RoleInfoItem]
    total_roles: int
