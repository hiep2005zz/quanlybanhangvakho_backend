import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.deps import get_current_user
from app.schemas.auth import UserResponse

router = APIRouter()

DISCOUNTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models", "discounts_data.json")

class DiscountTierSchema(BaseModel):
    id: Optional[Any] = None
    min_quantity: int = Field(..., ge=0)
    max_quantity: Optional[int] = None
    discount_percent: float = Field(..., ge=0, le=100)

class DiscountPolicyCreate(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    code: Optional[str] = None
    category: Optional[str] = "ALL"
    target_dealer_type: Optional[str] = "ALL"
    target_group: Optional[str] = "all"
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: Optional[bool] = True
    status: Optional[str] = "active"
    tiers: List[DiscountTierSchema] = []

class DiscountCalculateRequest(BaseModel):
    product_id: Optional[int] = None
    quantity: int = Field(..., gt=0)
    base_price: Optional[float] = None
    dealer_id: Optional[int] = None

class DiscountCalculateResponse(BaseModel):
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    base_price: float
    cost_price: Optional[float] = None
    applied_policy_name: Optional[str] = None
    applied_policy_code: Optional[str] = None
    applied_tier_label: Optional[str] = None
    discount_percent: float
    unit_discount_amount: float
    final_unit_price: float
    subtotal_before_discount: float
    total_discount_amount: float
    final_total_amount: float

_POLICIES_STORE: List[Dict[str, Any]] = []

def load_discount_policies() -> List[Dict[str, Any]]:
    global _POLICIES_STORE
    if os.path.exists(DISCOUNTS_FILE):
        try:
            with open(DISCOUNTS_FILE, "r", encoding="utf-8") as f:
                _POLICIES_STORE = json.load(f)
                return _POLICIES_STORE
        except Exception as e:
            print(f"Error loading {DISCOUNTS_FILE}: {e}")
    _POLICIES_STORE = [
        {
            "id": 1,
            "code": "CK-SL-001",
            "name": "Chiết khấu sản lượng toàn hệ thống",
            "title": "Tất cả sản phẩm",
            "category": "ALL",
            "target_dealer_type": "ALL",
            "target_group": "all",
            "description": "Giảm giá theo sản lượng đặt hàng áp dụng toàn hệ thống",
            "is_active": True,
            "status": "active",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
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
    save_discount_policies()
    return _POLICIES_STORE

def save_discount_policies():
    global _POLICIES_STORE
    try:
        os.makedirs(os.path.dirname(DISCOUNTS_FILE), exist_ok=True)
        with open(DISCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(_POLICIES_STORE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving {DISCOUNTS_FILE}: {e}")

load_discount_policies()

def resolve_best_discount(
    items: List[Dict[str, Any]],
    dealer: Optional[Any] = None,
    total_quantity: Optional[int] = None,
    subtotal_amount: Optional[float] = None
) -> Dict[str, Any]:
    """
    Quy tắc giá tốt nhất (Best Price Rule):
    Tìm chính sách chiết khấu mang lại số tiền giảm cao nhất cho khách hàng.
    """
    global _POLICIES_STORE
    if not _POLICIES_STORE:
        load_discount_policies()

    if total_quantity is None:
        total_quantity = sum(item.get("quantity", 0) for item in items)
    if subtotal_amount is None:
        subtotal_amount = sum(item.get("quantity", 0) * item.get("price", 0.0) for item in items)

    dealer_group = getattr(dealer, "customer_group", "") if dealer else ""
    dealer_group_str = str(dealer_group).lower()

    best_match = {
        "applied_policy_id": None,
        "applied_policy_code": None,
        "applied_policy_name": None,
        "applied_tier_label": None,
        "discount_percent": 0.0,
        "discount_amount": 0.0,
    }

    now_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for policy in _POLICIES_STORE:
        is_active = policy.get("is_active", True) and policy.get("status", "active") == "active"
        if not is_active:
            continue

        # Kiểm tra ngày hiệu lực
        start_date = policy.get("start_date")
        end_date = policy.get("end_date")
        if start_date and start_date > now_date_str:
            continue
        if end_date and end_date < now_date_str:
            continue

        # Kiểm tra đối tượng áp dụng
        target = str(policy.get("target_dealer_type") or policy.get("target_group") or "ALL").upper()
        if target not in ["ALL", "all", "ALL_DEALERS"]:
            if "CAP_1" in target or "TIER_1" in target:
                if "cấp 1" not in dealer_group_str and "cap_1" not in dealer_group_str and "cap 1" not in dealer_group_str:
                    continue
            elif "CAP_2" in target or "TIER_2" in target:
                if "cấp 2" not in dealer_group_str and "cap_2" not in dealer_group_str and "cap 2" not in dealer_group_str:
                    continue

        # Xác định số lượng áp dụng: theo sản phẩm cụ thể hay tổng đơn
        pol_title = policy.get("title") or policy.get("name") or ""
        pol_cat = policy.get("category") or "ALL"

        qty_to_check = total_quantity
        # Nếu chính sách gắn với 1 sản phẩm cụ thể
        if pol_cat != "ALL" and pol_title != "Tất cả sản phẩm":
            matched_qty = 0
            for it in items:
                prod_name = it.get("product_name") or ""
                prod_code = it.get("product_code") or ""
                if prod_code in pol_title or prod_name in pol_title:
                    matched_qty += it.get("quantity", 0)
            if matched_qty > 0:
                qty_to_check = matched_qty
            else:
                qty_to_check = 0

        # Kiểm tra các bậc
        for tier in policy.get("tiers", []):
            min_q = tier.get("min_quantity", 0)
            max_q = tier.get("max_quantity")
            tier_pct = float(tier.get("discount_percent", 0.0))

            if qty_to_check >= min_q and (max_q is None or qty_to_check <= max_q):
                tier_amount = round(subtotal_amount * tier_pct / 100.0, 2)
                # Best price rule: Ưu tiên chính sách đem lại mức giảm tiền cao nhất
                if tier_amount > best_match["discount_amount"] or (
                    tier_amount == best_match["discount_amount"] and tier_pct > best_match["discount_percent"]
                ):
                    best_match["applied_policy_id"] = policy.get("id")
                    best_match["applied_policy_code"] = policy.get("code")
                    best_match["applied_policy_name"] = policy.get("name") or policy.get("title")
                    best_match["applied_tier_label"] = f"Từ {min_q}" + (f" đến {max_q}" if max_q else "+") + f" sp: Giảm {tier_pct}%"
                    best_match["discount_percent"] = tier_pct
                    best_match["discount_amount"] = tier_amount

    return best_match

@router.get("", response_model=dict)
def get_discount_policies(
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_user: UserResponse = Depends(get_current_user)
):
    items = list(_POLICIES_STORE)
    if category and category != "ALL":
        items = [p for p in items if p.get("category") == category or p.get("category") == "ALL"]
    if is_active is not None:
        items = [p for p in items if p.get("is_active") == is_active or (is_active and p.get("status") == "active")]
    return {"items": items, "total": len(items)}

@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_discount_policy(
    data: DiscountPolicyCreate,
    current_user: UserResponse = Depends(get_current_user)
):
    global _POLICIES_STORE
    new_id = max([p.get("id", 0) for p in _POLICIES_STORE], default=0) + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    name = data.name or data.title or f"Chính sách chiết khấu #{new_id}"
    code = data.code or f"CK-SL-{new_id:03d}"
    new_policy = {
        "id": new_id,
        "code": code,
        "name": name,
        "title": data.title or name,
        "category": data.category or "ALL",
        "target_dealer_type": data.target_dealer_type or "ALL",
        "target_group": data.target_group or "all",
        "description": data.description,
        "start_date": data.start_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "end_date": data.end_date,
        "is_active": data.is_active if data.is_active is not None else True,
        "status": data.status or ("active" if data.is_active else "expired"),
        "tiers": [t.dict() for t in data.tiers],
        "created_by": current_user.username,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    _POLICIES_STORE.insert(0, new_policy)
    save_discount_policies()
    return new_policy

@router.put("/{policy_id}", response_model=dict)
def update_discount_policy(
    policy_id: int,
    data: DiscountPolicyCreate,
    current_user: UserResponse = Depends(get_current_user)
):
    policy = next((p for p in _POLICIES_STORE if p.get("id") == policy_id), None)
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    name = data.name or data.title or policy.get("name")
    policy["name"] = name
    policy["title"] = data.title or name
    if data.code:
        policy["code"] = data.code
    policy["category"] = data.category or policy.get("category", "ALL")
    policy["target_dealer_type"] = data.target_dealer_type or policy.get("target_dealer_type", "ALL")
    policy["target_group"] = data.target_group or policy.get("target_group", "all")
    policy["description"] = data.description
    if data.start_date:
        policy["start_date"] = data.start_date
    if data.end_date is not None:
        policy["end_date"] = data.end_date
    if data.is_active is not None:
        policy["is_active"] = data.is_active
        policy["status"] = "active" if data.is_active else "expired"
    if data.tiers:
        policy["tiers"] = [t.dict() for t in data.tiers]
    policy["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_discount_policies()
    return policy

@router.patch("/{policy_id}/toggle-status", response_model=dict)
def toggle_policy_status(
    policy_id: int,
    current_user: UserResponse = Depends(get_current_user)
):
    policy = next((p for p in _POLICIES_STORE if p.get("id") == policy_id), None)
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    policy["is_active"] = not policy.get("is_active", True)
    policy["status"] = "active" if policy["is_active"] else "expired"
    policy["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_discount_policies()
    return policy

@router.delete("/{policy_id}", response_model=dict)
def delete_discount_policy(
    policy_id: int,
    current_user: UserResponse = Depends(get_current_user)
):
    global _POLICIES_STORE
    policy = next((p for p in _POLICIES_STORE if p.get("id") == policy_id), None)
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    _POLICIES_STORE = [p for p in _POLICIES_STORE if p.get("id") != policy_id]
    save_discount_policies()
    return {"message": "Đã xóa chính sách chiết khấu", "deleted_id": policy_id}

@router.post("/calculate", response_model=DiscountCalculateResponse)
def calculate_discount(
    data: DiscountCalculateRequest,
    current_user: UserResponse = Depends(get_current_user)
):
    base_price = data.base_price or 100000.0
    items = [{
        "product_id": data.product_id or 1,
        "quantity": data.quantity,
        "price": base_price,
    }]
    best = resolve_best_discount(items=items, total_quantity=data.quantity, subtotal_amount=base_price * data.quantity)

    discount_pct = best["discount_percent"]
    unit_discount = base_price * (discount_pct / 100.0)
    final_unit_price = base_price - unit_discount
    subtotal = base_price * data.quantity
    total_discount = best["discount_amount"]
    final_total = subtotal - total_discount

    return DiscountCalculateResponse(
        product_id=data.product_id or 1,
        product_name=f"Sản phẩm #{data.product_id or 1}",
        quantity=data.quantity,
        base_price=base_price,
        cost_price=70000.0,
        applied_policy_name=best["applied_policy_name"],
        applied_policy_code=best["applied_policy_code"],
        applied_tier_label=best["applied_tier_label"],
        discount_percent=discount_pct,
        unit_discount_amount=unit_discount,
        final_unit_price=final_unit_price,
        subtotal_before_discount=subtotal,
        total_discount_amount=total_discount,
        final_total_amount=final_total,
    )
