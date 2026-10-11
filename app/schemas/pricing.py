# backend/app/schemas/pricing.py
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class CalculateLineItemRequest(BaseModel):
    dealer_id: Optional[int] = None
    customer_group: Optional[str] = None
    product_id: int
    quantity: int = Field(default=1, gt=0)
    unit_price_override: Optional[float] = Field(default=None, ge=0)


class CalculateLineItemResponse(BaseModel):
    product_id: int
    product_code: str
    product_name: str
    quantity: int
    unit_price: float
    book_price: float
    floor_price: float
    is_below_floor: bool
    requires_approval: bool
    approval_reason: Optional[str] = None
    discount_rate: float
    discount_amount: float
    applied_policy_code: Optional[str] = None
    applied_policy_name: Optional[str] = None
    line_total: float


class ValidatePriceFloorItem(BaseModel):
    product_id: int
    quantity: int = Field(default=1, gt=0)
    price: float = Field(..., ge=0)


class ValidatePriceFloorRequest(BaseModel):
    dealer_id: Optional[int] = None
    customer_group: Optional[str] = None
    items: List[ValidatePriceFloorItem]


class PriceFloorEvaluationItem(BaseModel):
    product_id: int
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    unit_price: float
    book_price: float = 0.0
    floor_price: float = 0.0
    is_below_floor: bool = False
    quantity: int = 1
    error: Optional[str] = None


class ValidatePriceFloorResponse(BaseModel):
    requires_approval: bool
    approval_reasons: List[str] = []
    approval_reason: Optional[str] = None
    items_evaluation: List[PriceFloorEvaluationItem] = []
