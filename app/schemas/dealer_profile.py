from typing import Optional

from pydantic import BaseModel, Field


class DealerProfileCreate(BaseModel):
    code: Optional[str] = None
    name: str = Field(min_length=2)
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None

    tax_id: Optional[str] = None
    tax_code: Optional[str] = None
    region: str = "Chưa xác định"

    assigned_sale_id: Optional[int] = None
    assigned_sale_name: Optional[str] = None

    customer_group: str = "Đại lý cấp 1"
    status: str = "Đang hoạt động"


class DealerProfileUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None

    tax_id: Optional[str] = None
    tax_code: Optional[str] = None
    region: Optional[str] = None

    assigned_sale_id: Optional[int] = None

    customer_group: Optional[str] = None
    status: Optional[str] = None


class DealerProfileResponse(BaseModel):
    id: int
    code: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None

    tax_id: Optional[str] = None
    tax_code: Optional[str] = None
    region: str

    assigned_sale_id: Optional[int] = None
    assigned_sale_name: Optional[str] = None

    customer_group: str
    status: str
    price_list: Optional[str] = None