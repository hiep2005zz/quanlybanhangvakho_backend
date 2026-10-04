from typing import Optional
import re
from pydantic import BaseModel, Field, field_validator


class DeliveryPointBase(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=500)
    receiver_name: Optional[str] = Field(default="", max_length=150)
    receiver_phone: Optional[str] = Field(default="", max_length=20)
    route_note: Optional[str] = Field(default="", max_length=500)
    is_default: bool = False

    @field_validator("receiver_name", "receiver_phone", "route_note", mode="before")
    @classmethod
    def clean_optional_strings(cls, v):
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("receiver_phone")
    @classmethod
    def validate_phone(cls, v):
        if not v:
            return ""
        if not re.match(r"^[0-9+\-\s]{8,20}$", v):
            raise ValueError("Số điện thoại nhận hàng phải từ 8 đến 20 ký tự (chữ số, dấu +, -)")
        return v


class DeliveryPointCreate(DeliveryPointBase):
    pass


class DeliveryPointUpdate(DeliveryPointBase):
    pass


class DeliveryPointOut(DeliveryPointBase):
    id: int
    dealer_id: int
    is_active: bool

    model_config = {"from_attributes": True}