from typing import Optional, List
from fastapi import APIRouter, Depends, Query

from app.api.deps import require_roles
from app.models.dealer import DEALERS_DB
from app.models.user import USERS_DB
from app.schemas.auth import UserResponse


router = APIRouter()


def get_sale_name(assigned_sale_id: Optional[int]) -> Optional[str]:
    """Lấy tên nhân viên kinh doanh phụ trách đại lý."""
    if not assigned_sale_id:
        return None

    for user in USERS_DB.values():
        if user.id == assigned_sale_id:
            return user.full_name

    return None


def get_region(address: Optional[str]) -> str:
    """
    Lấy khu vực từ địa chỉ hiện có.
    Ví dụ:
    '120 Cầu Giấy, Hà Nội' -> 'Hà Nội'
    """
    if not address:
        return "Chưa xác định"

    parts = [part.strip() for part in address.split(",") if part.strip()]

    if parts:
        return parts[-1]

    return "Chưa xác định"


@router.get("/search")
def search_dealers(
    keyword: Optional[str] = Query(
        default=None,
        description="Tìm theo mã đại lý, tên đại lý hoặc số điện thoại"
    ),
    region: Optional[str] = Query(
        default=None,
        description="Lọc theo khu vực"
    ),
    assigned_sale_id: Optional[int] = Query(
        default=None,
        description="Lọc theo người phụ trách"
    ),
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """
    Tìm kiếm và lọc danh sách đại lý.

    User Story:
    - Tìm theo mã
    - Tìm theo tên
    - Tìm theo số điện thoại
    - Lọc theo khu vực
    - Lọc theo người phụ trách
    """

    keyword_clean = keyword.strip().lower() if keyword else None
    region_clean = region.strip().lower() if region else None

    results = []

    for dealer in DEALERS_DB.values():

        # ==============================
        # 1. TÌM KIẾM THEO MÃ / TÊN / SĐT
        # ==============================
        if keyword_clean:
            searchable_values = [
                dealer.code or "",
                dealer.name or "",
                dealer.phone or "",
            ]

            matched = any(
                keyword_clean in value.lower()
                for value in searchable_values
            )

            if not matched:
                continue

        # ==============================
        # 2. LỌC THEO KHU VỰC
        # ==============================
        dealer_region = get_region(dealer.address)

        if region_clean:
            if region_clean not in dealer_region.lower():
                continue

        # ==============================
        # 3. LỌC THEO NGƯỜI PHỤ TRÁCH
        # ==============================
        if assigned_sale_id is not None:
            if dealer.assigned_sale_id != assigned_sale_id:
                continue

        results.append({
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "phone": dealer.phone,
            "email": dealer.email,
            "address": dealer.address,
            "region": dealer_region,
            "customer_group": getattr(dealer, "customer_group", "Dai_ly_cap_1"),
            "credit_limit": getattr(dealer, "credit_limit", 50000000.0),
            "assigned_sale_id": dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(
                dealer.assigned_sale_id
            ),
        })

    return {
        "items": results,
        "total": len(results),
    }


@router.get("")
def get_dealers(
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """Lấy toàn bộ danh sách đại lý và khách hàng kèm nhóm khách hàng."""
    results = []
    for dealer in DEALERS_DB.values():
        results.append({
            "id": dealer.id,
            "code": dealer.code,
            "name": dealer.name,
            "phone": dealer.phone,
            "email": dealer.email,
            "address": dealer.address,
            "region": get_region(dealer.address),
            "customer_group": getattr(dealer, "customer_group", "Dai_ly_cap_1"),
            "credit_limit": getattr(dealer, "credit_limit", 50000000.0),
            "assigned_sale_id": dealer.assigned_sale_id,
            "assigned_sale_name": get_sale_name(dealer.assigned_sale_id),
        })
    return results


@router.get("/filters")
def get_dealer_filters(
    current_user: UserResponse = Depends(
        require_roles([
            "admin",
            "sales_manager",
            "sales",
            "accountant",
        ])
    ),
):
    """
    Trả về dữ liệu dùng cho các bộ lọc trên frontend.
    """

    regions = set()
    sales = {}

    for dealer in DEALERS_DB.values():

        # Khu vực
        regions.add(get_region(dealer.address))

        # Nhân viên phụ trách
        if dealer.assigned_sale_id:
            sale_name = get_sale_name(dealer.assigned_sale_id)

            if sale_name:
                sales[dealer.assigned_sale_id] = sale_name

    return {
        "regions": sorted(regions),
        "sales": [
            {
                "id": sale_id,
                "name": sale_name,
            }
            for sale_id, sale_name in sorted(
                sales.items(),
                key=lambda item: item[1]
            )
        ],
    }