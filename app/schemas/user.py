import re
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

class UserCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)
    username: Optional[str] = None
    password: str = Field(..., min_length=3, max_length=128)
    role: Optional[str] = None
    roles: Optional[List[str]] = None
    branch: Optional[str] = "Kho Tổng Hà Nội"
    phone: Optional[str] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    roles: Optional[List[str]] = None
    branch: Optional[str] = None
    is_active: Optional[bool] = None
    status: Optional[str] = None  # ACTIVE | LOCKED
    lock_reason: Optional[str] = None

class CustomerCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)
    phone: str = Field(..., min_length=9, max_length=20)
    username: Optional[str] = None
    role: Optional[str] = None
    roles: Optional[List[str]] = None
    branch: Optional[str] = None

    @field_validator('full_name')
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("Vui lòng nhập Họ và tên nhân viên.")
        
        # Kiểm tra không được chứa chữ số
        if any(char.isdigit() for char in name):
            raise ValueError("Họ và tên chỉ được chứa chữ cái, không được chứa chữ số.")
            
        # Regex kiểm tra họ và tên hợp lệ (chữ cái tiếng Việt có dấu, chữ cái tiếng Anh, khoảng trắng)
        # Hỗ trợ đầy đủ bộ ký tự Unicode tiếng Việt
        vietnamese_name_pattern = r'^[a-zA-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠàáâãèéêìíòóôõùúăđĩũơƯĂẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼỀỀỂưăạảấầẩẫậắằẳẵặẹẻẽềềểỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪễệỉịọỏốồổỗộớờởỡợụủứừỬỮỰỲỴÝỶỸửữựỳỵỷỹ\s\.\'\-]+$'
        if not re.match(vietnamese_name_pattern, name):
            raise ValueError("Họ và tên chỉ được chứa chữ cái (tiếng Việt hoặc tiếng Anh), không được chứa số hoặc ký tự đặc biệt lạ.")
            
        return name

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        # Chuẩn hóa: bỏ khoảng trắng, dấu gạch ngang, dấu chấm, dấu ngoặc
        cleaned = re.sub(r'[\s\.\-\(\)]', '', v.strip())
        # Chuyển đổi +84 thành 0
        if cleaned.startswith('+84'):
            cleaned = '0' + cleaned[3:]
        elif cleaned.startswith('84') and len(cleaned) == 11:
            cleaned = '0' + cleaned[2:]
            
        # Ràng buộc số điện thoại Việt Nam chuẩn: 10 chữ số, bắt đầu bằng 03, 05, 07, 08, 09
        # (hoặc đầu số bàn 11 chữ số 02x)
        vn_phone_pattern = r'^(0[3|5|7|8|9][0-9]{8}|02[0-9]{9})$'
        if not re.match(vn_phone_pattern, cleaned):
            raise ValueError(
                "Số điện thoại không đúng định dạng Việt Nam. Vui lòng nhập số hợp lệ gồm 10 chữ số bắt đầu bằng 03, 05, 07, 08, 09 (ví dụ: 0912345678 hoặc +84912345678)."
            )
        return cleaned

class CustomerCreateResponse(BaseModel):
    user: "UserItemResponse"
    email_sent: bool
    message: str

class UserItemResponse(BaseModel):
    id: int
    username: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str
    roles: List[str] = []
    role_title: str
    role_titles: List[str] = []
    branch: str
    is_active: bool
    status: str = "ACTIVE"
    lock_reason: Optional[str] = None
    locked_at: Optional[str] = None
    dealers_needing_handover: int = 0
    can_view_cost: bool = False
    can_write_inventory: bool = False
    badge_color: str = "#64748b"
    avatar_url: Optional[str] = None

class UserListResponse(BaseModel):
    users: List[UserItemResponse]
    total: int
