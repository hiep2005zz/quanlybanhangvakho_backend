from datetime import datetime, timezone
from typing import List, Optional, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, Query
from app.api.deps import get_current_user
from app.schemas.auth import UserResponse

router = APIRouter()

class DiscountTierSchema(BaseModel):
    id: Optional[int] = None
    min_quantity: int
    max_quantity: Optional[int] = None
    discount_percent: float

class DiscountPolicyCreate(BaseModel):
    name: str
    category: Optional[str] = "ALL"
    target_dealer_type: Optional[str] = "ALL"
    description: Optional[str] = None
    is_active: Optional[bool] = True
    tiers: List[DiscountTierSchema] = []

class DiscountPolicyResponse(BaseModel):
    id: int
    code: str
    name: str
    category: str
    target_dealer_type: str
    description: Optional[str] = None
    is_active: bool
    tiers: List[DiscountTierSchema]
    created_by: str
    created_at: str
    updated_at: str

class DiscountCalculateRequest(BaseModel):
    product_id: int
    quantity: int

class DiscountCalculateResponse(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    base_price: float
    cost_price: Optional[float] = None
    applied_policy_name: Optional[str] = None
    applied_tier_label: Optional[str] = None
    discount_percent: float
    unit_discount_amount: float
    final_unit_price: float
    subtotal_before_discount: float
    total_discount_amount: float
    final_total_amount: float

# Bộ nhớ tạm in-memory cho các chính sách chiết khấu
_POLICIES_STORE: List[dict] = [
    {
        "id": 1,
        "code": "CK-SL-001",
        "name": "Chiết khấu sản lượng toàn hệ thống",
        "category": "ALL",
        "target_dealer_type": "ALL",
        "description": "Áp dụng theo mốc sản lượng đặt hàng",
        "is_active": True,
        "tiers": [
            {"id": 1, "min_quantity": 100, "max_quantity": 499, "discount_percent": 5.0},
            {"id": 2, "min_quantity": 500, "max_quantity": 999, "discount_percent": 10.0},
            {"id": 3, "min_quantity": 1000, "max_quantity": None, "discount_percent": 15.0},
        ],
        "created_by": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
]

@router.get("", response_model=dict)
def get_discount_policies(
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_user: UserResponse = Depends(get_current_user)
):
    items = _POLICIES_STORE
    if category and category != "ALL":
        items = [p for p in items if p.get("category") == category or p.get("category") == "ALL"]
    if is_active is not None:
        items = [p for p in items if p.get("is_active") == is_active]
    return {"items": items, "total": len(items)}

@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_discount_policy(
    data: DiscountPolicyCreate,
    current_user: UserResponse = Depends(get_current_user)
):
    new_id = max([p["id"] for p in _POLICIES_STORE], default=0) + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    new_policy = {
        "id": new_id,
        "code": f"CK-SL-{new_id:03d}",
        "name": data.name,
        "category": data.category or "ALL",
        "target_dealer_type": data.target_dealer_type or "ALL",
        "description": data.description,
        "is_active": data.is_active if data.is_active is not None else True,
        "tiers": [t.dict() for t in data.tiers],
        "created_by": current_user.username,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    _POLICIES_STORE.insert(0, new_policy)
    return new_policy

@router.put("/{policy_id}", response_model=dict)
def update_discount_policy(
    policy_id: int,
    data: DiscountPolicyCreate,
    current_user: UserResponse = Depends(get_current_user)
):
    policy = next((p for p in _POLICIES_STORE if p["id"] == policy_id), None)
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    policy["name"] = data.name
    policy["category"] = data.category or "ALL"
    policy["target_dealer_type"] = data.target_dealer_type or "ALL"
    policy["description"] = data.description
    policy["is_active"] = data.is_active if data.is_active is not None else policy["is_active"]
    policy["tiers"] = [t.dict() for t in data.tiers]
    policy["updated_at"] = datetime.now(timezone.utc).isoformat()
    return policy

@router.patch("/{policy_id}/toggle-status", response_model=dict)
def toggle_policy_status(
    policy_id: int,
    current_user: UserResponse = Depends(get_current_user)
):
    policy = next((p for p in _POLICIES_STORE if p["id"] == policy_id), None)
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    policy["is_active"] = not policy["is_active"]
    policy["updated_at"] = datetime.now(timezone.utc).isoformat()
    return policy

@router.delete("/{policy_id}", response_model=dict)
def delete_discount_policy(
    policy_id: int,
    current_user: UserResponse = Depends(get_current_user)
):
    global _POLICIES_STORE
    policy = next((p for p in _POLICIES_STORE if p["id"] == policy_id), None)
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    _POLICIES_STORE = [p for p in _POLICIES_STORE if p["id"] != policy_id]
    return {"message": "Đã xóa chính sách chiết khấu", "deleted_id": policy_id}

@router.post("/calculate", response_model=DiscountCalculateResponse)
def calculate_discount(
    data: DiscountCalculateRequest,
    current_user: UserResponse = Depends(get_current_user)
):
    base_price = 100000.0
    discount_pct = 0.0
    applied_name = None
    applied_tier = None

    for policy in _POLICIES_STORE:
        if not policy.get("is_active"):
            continue
        for tier in policy.get("tiers", []):
            min_q = tier.get("min_quantity", 0)
            max_q = tier.get("max_quantity")
            if data.quantity >= min_q and (max_q is None or data.quantity <= max_q):
                if tier.get("discount_percent", 0) > discount_pct:
                    discount_pct = tier["discount_percent"]
                    applied_name = policy["name"]
                    applied_tier = f"Từ {min_q} sp: Giảm {discount_pct}%"

    unit_discount = base_price * (discount_pct / 100.0)
    final_unit_price = base_price - unit_discount
    subtotal = base_price * data.quantity
    total_discount = unit_discount * data.quantity
    final_total = subtotal - total_discount

    return DiscountCalculateResponse(
        product_id=data.product_id,
        product_name=f"Sản phẩm #{data.product_id}",
        quantity=data.quantity,
        base_price=base_price,
        cost_price=70000.0,
        applied_policy_name=applied_name,
        applied_tier_label=applied_tier,
        discount_percent=discount_pct,
        unit_discount_amount=unit_discount,
        final_unit_price=final_unit_price,
        subtotal_before_discount=subtotal,
        total_discount_amount=total_discount,
        final_total_amount=final_total,
    )
