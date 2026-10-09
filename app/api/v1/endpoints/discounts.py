# backend/app/api/v1/endpoints/discounts.py
import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.models.discount import DiscountPolicyEntity, DiscountTierEntity
from app.models.entities import ProductEntity
from app.api.deps import get_current_user, require_roles
from app.schemas.auth import UserResponse

router = APIRouter()


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


def load_discount_policies(db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """Tải danh sách chính sách chiết khấu từ Database vào in-memory store."""
    global _POLICIES_STORE
    owns_session = False
    if db is None:
        try:
            db = SessionLocal()
            owns_session = True
        except Exception:
            pass

    if db is not None:
        try:
            entities = db.query(DiscountPolicyEntity).order_by(DiscountPolicyEntity.id.asc()).all()
            if entities:
                _POLICIES_STORE = [e.to_dict() for e in entities]
                return _POLICIES_STORE
        except Exception as e:
            print(f"Error loading discount policies from DB: {e}")
        finally:
            if owns_session:
                db.close()

    if not _POLICIES_STORE:
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
    return _POLICIES_STORE


def save_discount_policies():
    """Backward compatibility stub."""
    pass


# Initial load
try:
    load_discount_policies()
except Exception:
    pass


def resolve_best_discount(
    items: List[Dict[str, Any]],
    dealer: Optional[Any] = None,
    total_quantity: Optional[int] = None,
    subtotal_amount: Optional[float] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Quy tắc giá tốt nhất (Best Price Rule):
    Tìm chính sách chiết khấu mang lại số tiền giảm cao nhất cho khách hàng từ Database.
    """
    policies_to_check: List[Dict[str, Any]] = []

    if db is not None:
        try:
            entities = (
                db.query(DiscountPolicyEntity)
                .filter(DiscountPolicyEntity.is_active == True, DiscountPolicyEntity.status == "active")
                .order_by(DiscountPolicyEntity.id.asc())
                .all()
            )
            policies_to_check = [e.to_dict() for e in entities]
        except Exception:
            policies_to_check = []

    if not policies_to_check:
        try:
            with SessionLocal() as db_session:
                entities = (
                    db_session.query(DiscountPolicyEntity)
                    .filter(DiscountPolicyEntity.is_active == True, DiscountPolicyEntity.status == "active")
                    .order_by(DiscountPolicyEntity.id.asc())
                    .all()
                )
                policies_to_check = [e.to_dict() for e in entities]
        except Exception:
            pass

    if not policies_to_check:
        policies_to_check = [p for p in _POLICIES_STORE if p.get("is_active", True) and p.get("status") == "active"]

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

    for policy in policies_to_check:
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

        # Xác định số lượng và giá trị áp dụng
        pol_title = str(policy.get("title") or policy.get("name") or "")
        pol_cat = str(policy.get("category") or "ALL")

        qty_to_check = total_quantity
        subtotal_to_discount = subtotal_amount

        # 1. Trường hợp chính sách áp dụng cho 1 Nhóm hàng (Category)
        is_category_policy = (
            (pol_cat not in ["ALL", "all", "PRODUCT", "product"] and pol_cat != "")
            or "nhóm hàng:" in pol_title.lower()
            or "nhóm:" in pol_title.lower()
        )
        if is_category_policy and pol_cat not in ["ALL", "all"]:
            cat_name = pol_cat
            if "nhóm hàng:" in pol_title.lower():
                cat_name = pol_title.split(":", 1)[-1].strip()
            elif "nhóm:" in pol_title.lower():
                cat_name = pol_title.split(":", 1)[-1].strip()

            cat_qty = 0
            cat_subtotal = 0.0
            from app.api.v1.endpoints.products import RAW_PRODUCTS

            for it in items:
                it_cat = it.get("category")
                if not it_cat and db is not None:
                    p_id = it.get("product_id")
                    if p_id:
                        p_ent = db.query(ProductEntity).filter(ProductEntity.id == p_id).first()
                        if p_ent:
                            it_cat = p_ent.category
                if not it_cat:
                    p_id = it.get("product_id")
                    p_code = it.get("product_code")
                    for raw_p in RAW_PRODUCTS:
                        if (p_id and raw_p.get("id") == p_id) or (p_code and raw_p.get("code") == p_code):
                            it_cat = raw_p.get("category")
                            break

                if it_cat and (it_cat.lower().strip() == cat_name.lower().strip() or cat_name.lower().strip() in it_cat.lower().strip()):
                    it_qty = it.get("quantity", 0)
                    it_price = it.get("price", 0.0)
                    cat_qty += it_qty
                    cat_subtotal += it_qty * it_price

            if cat_qty > 0:
                qty_to_check = cat_qty
                subtotal_to_discount = cat_subtotal
            else:
                qty_to_check = 0

        # 2. Trường hợp chính sách áp dụng cho 1 Sản phẩm cụ thể
        elif pol_title and pol_title != "Tất cả sản phẩm" and "nhóm" not in pol_title.lower() and pol_cat != "ALL":
            matched_qty = 0
            matched_subtotal = 0.0
            for it in items:
                prod_name = it.get("product_name") or ""
                prod_code = it.get("product_code") or ""
                if (prod_code and prod_code in pol_title) or (prod_name and prod_name in pol_title):
                    it_qty = it.get("quantity", 0)
                    it_price = it.get("price", 0.0)
                    matched_qty += it_qty
                    matched_subtotal += it_qty * it_price
            if matched_qty > 0:
                qty_to_check = matched_qty
                subtotal_to_discount = matched_subtotal
            else:
                qty_to_check = 0

        # Kiểm tra các bậc
        for tier in policy.get("tiers", []):
            min_q = tier.get("min_quantity", 0)
            max_q = tier.get("max_quantity")
            tier_pct = float(tier.get("discount_percent", 0.0))

            if qty_to_check >= min_q and (max_q is None or qty_to_check <= max_q):
                tier_amount = round(subtotal_to_discount * tier_pct / 100.0, 2)
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
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    query = db.query(DiscountPolicyEntity)
    if category and category != "ALL":
        query = query.filter(
            (DiscountPolicyEntity.category == category) | (DiscountPolicyEntity.category == "ALL")
        )
    if is_active is not None:
        if is_active:
            query = query.filter(
                (DiscountPolicyEntity.is_active == True) & (DiscountPolicyEntity.status == "active")
            )
        else:
            query = query.filter(DiscountPolicyEntity.is_active == False)

    entities = query.order_by(DiscountPolicyEntity.id.asc()).all()
    items = [e.to_dict() for e in entities]
    load_discount_policies(db)
    return {"items": items, "total": len(items)}


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_discount_policy(
    data: DiscountPolicyCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager"])),
):
    count = db.query(DiscountPolicyEntity).count()
    code = data.code or f"CK-SL-{count + 1:03d}"
    name = data.name or data.title or f"Chính sách chiết khấu #{count + 1}"
    title = data.title or name
    start_date = data.start_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    is_active = data.is_active if data.is_active is not None else True
    status_val = data.status or ("active" if is_active else "expired")

    new_policy = DiscountPolicyEntity(
        code=code,
        name=name,
        title=title,
        category=data.category or "ALL",
        target_dealer_type=data.target_dealer_type or "ALL",
        target_group=data.target_group or "all",
        description=data.description,
        start_date=start_date,
        end_date=data.end_date,
        is_active=is_active,
        status=status_val,
        created_by=current_user.username,
    )
    db.add(new_policy)
    db.flush()

    for t in data.tiers:
        tier_entity = DiscountTierEntity(
            policy_id=new_policy.id,
            min_quantity=t.min_quantity,
            max_quantity=t.max_quantity,
            discount_percent=t.discount_percent,
        )
        db.add(tier_entity)

    db.commit()
    db.refresh(new_policy)
    load_discount_policies(db)
    return new_policy.to_dict()


@router.put("/{policy_id}", response_model=dict)
def update_discount_policy(
    policy_id: int,
    data: DiscountPolicyCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager"])),
):
    policy = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")

    if data.name or data.title:
        name = data.name or data.title
        policy.name = name
        policy.title = data.title or name
    if data.code:
        policy.code = data.code
    if data.category is not None:
        policy.category = data.category
    if data.target_dealer_type is not None:
        policy.target_dealer_type = data.target_dealer_type
    if data.target_group is not None:
        policy.target_group = data.target_group
    if data.description is not None:
        policy.description = data.description
    if data.start_date is not None:
        policy.start_date = data.start_date
    if data.end_date is not None:
        policy.end_date = data.end_date
    if data.is_active is not None:
        policy.is_active = data.is_active
        policy.status = "active" if data.is_active else "expired"
    if data.status is not None:
        policy.status = data.status

    if data.tiers is not None:
        db.query(DiscountTierEntity).filter(DiscountTierEntity.policy_id == policy_id).delete()
        for t in data.tiers:
            tier_entity = DiscountTierEntity(
                policy_id=policy.id,
                min_quantity=t.min_quantity,
                max_quantity=t.max_quantity,
                discount_percent=t.discount_percent,
            )
            db.add(tier_entity)

    policy.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(policy)
    load_discount_policies(db)
    return policy.to_dict()


@router.patch("/{policy_id}/toggle-status", response_model=dict)
def toggle_policy_status(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager"])),
):
    policy = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    policy.is_active = not policy.is_active
    policy.status = "active" if policy.is_active else "expired"
    policy.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(policy)
    load_discount_policies(db)
    return policy.to_dict()


@router.delete("/{policy_id}", response_model=dict)
def delete_discount_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager"])),
):
    policy = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Không tìm thấy chính sách chiết khấu.")
    db.delete(policy)
    db.commit()
    load_discount_policies(db)
    return {"message": "Đã xóa chính sách chiết khấu", "deleted_id": policy_id}


@router.post("/calculate", response_model=DiscountCalculateResponse)
def calculate_discount(
    data: DiscountCalculateRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    base_price = data.base_price or 100000.0
    items = [{
        "product_id": data.product_id or 1,
        "quantity": data.quantity,
        "price": base_price,
    }]
    best = resolve_best_discount(
        items=items,
        total_quantity=data.quantity,
        subtotal_amount=base_price * data.quantity,
        db=db,
    )

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
