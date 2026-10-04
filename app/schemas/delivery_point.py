from pydantic import BaseModel, Field


class DeliveryPointBase(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=500)
    receiver_name: str = Field(min_length=1, max_length=150)
    receiver_phone: str = Field(pattern=r"^[0-9+\-\s]{8,20}$")
    route_note: str | None = Field(default=None, max_length=500)
    is_default: bool = False


class DeliveryPointCreate(DeliveryPointBase):
    pass


class DeliveryPointUpdate(DeliveryPointBase):
    pass


class DeliveryPointOut(DeliveryPointBase):
    id: int
    dealer_id: int
    is_active: bool

    model_config = {"from_attributes": True}