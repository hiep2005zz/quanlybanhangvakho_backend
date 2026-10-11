# backend/app/api/v1/endpoints/pricing.py
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_roles
from app.schemas.auth import UserResponse
from app.schemas.pricing import (
    CalculateLineItemRequest,
    CalculateLineItemResponse,
    ValidatePriceFloorRequest,
    ValidatePriceFloorResponse,
    PriceFloorEvaluationItem,
)
from app.services.pricing_service import PricingService

router = APIRouter()

# AC1 & RBAC: Chỉ tài khoản có vai trò Sales hoặc Admin mới được sử dụng tính năng tra cứu giá & tính toán
PRICING_ACCESS_ROLES = ["admin", "sales_manager", "sales"]


@router.get(
    "/effective-price",
    status_code=status.HTTP_200_OK,
)
def get_effective_price(
    product_id: int = Query(..., description="ID sản phẩm (SKU)"),
    dealer_id: Optional[int] = Query(None, description="ID đại lý"),
    customer_group: Optional[str] = Query(None, description="Mã nhóm khách hàng"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICING_ACCESS_ROLES)),
):
    """
    AC1: Tra cứu đơn giá và giá sàn theo Bảng giá hiệu lực.
    Bảo vệ RBAC: Chỉ Sales và Admin. Trả về 403 Forbidden với các vai trò khác.
    Chặn khi thiếu bảng giá: Ném lỗi 400 Bad Request nếu SKU chưa có bảng giá hiệu lực.
    """
    target = dealer_id if dealer_id is not None else customer_group
    return PricingService.get_effective_price(target, product_id, db)


@router.post(
    "/calculate-line-item",
    response_model=CalculateLineItemResponse,
    status_code=status.HTTP_200_OK,
)
def calculate_line_item(
    payload: CalculateLineItemRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICING_ACCESS_ROLES)),
):
    """
    AC1, AC2, AC3: Tự động tính giá, chiết khấu theo sản lượng và kiểm soát giá sàn.
    Bảo vệ RBAC: Chỉ Sales và Admin. Trả về 403 Forbidden với các vai trò khác.
    """
    target = payload.dealer_id if payload.dealer_id is not None else payload.customer_group
    result = PricingService.calculate_line_item(
        customer_group_or_dealer_id=target,
        product_id=payload.product_id,
        quantity=payload.quantity,
        override_price=payload.unit_price_override,
        db=db,
    )
    return CalculateLineItemResponse(**result)


@router.post(
    "/validate-floor",
    response_model=ValidatePriceFloorResponse,
    status_code=status.HTTP_200_OK,
)
def validate_floor(
    payload: ValidatePriceFloorRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICING_ACCESS_ROLES)),
):
    """
    AC3: Kiểm tra giá sàn cho danh sách dòng hàng của đơn hàng.
    Bảo vệ RBAC: Chỉ Sales và Admin. Trả về 403 Forbidden với các vai trò khác.
    """
    target = payload.dealer_id if payload.dealer_id is not None else payload.customer_group
    items_dict = [it.model_dump() for it in payload.items]
    requires_approval, reasons, evaluations = PricingService.validate_order_price_floor(
        items=items_dict,
        customer_group_or_dealer_id=target,
        db=db,
    )

    return ValidatePriceFloorResponse(
        requires_approval=requires_approval,
        approval_reasons=reasons,
        approval_reason=" | ".join(reasons) if reasons else None,
        items_evaluation=[PriceFloorEvaluationItem(**it) for it in evaluations],
    )
