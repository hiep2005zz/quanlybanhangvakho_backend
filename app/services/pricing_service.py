# backend/app/services/pricing_service.py
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_, literal

from app.models.entities import ProductEntity, DealerEntity, InventoryTransactionEntity, OrderEntity
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.discount import DiscountPolicyEntity, DiscountTierEntity
from app.schemas.auth import UserResponse
from app.schemas.product import (
    ProductItem,
    ProductListResponse,
    ProductFinancialSummary,
    UnitConversionItem,
)

def get_customer_group_aliases(group: Optional[str]) -> list[str]:
    """
    Chuẩn hóa các biến thể của nhóm khách hàng (có dấu, không dấu, viết hoa, viết thường).
    """
    if not group:
        return ["dai_ly_cap_1", "Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1"]

    g = group.strip()
    g_lower = g.lower()
    if g_lower in ["cap_1", "dai_ly_cap_1", "đại lý cấp 1"]:
        return ["dai_ly_cap_1", "Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1"]
    if g_lower in ["cap_2", "dai_ly_cap_2", "đại lý cấp 2"]:
        return ["dai_ly_cap_2", "Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2"]
    if g_lower in ["retail", "khach_le", "khách lẻ"]:
        return ["khach_le", "Khach_le", "RETAIL", "Khách lẻ"]
    if g_lower in ["khách sỉ", "khach_si", "wholesale"]:
        return ["Khách sỉ", "khach_si", "Khach_si", "WHOLESALE"]
    return [g]


def resolve_customer_dealer(db: Session, current_user: UserResponse) -> Optional[Any]:
    """
    Xác định đối tượng Đại lý (Dealer) liên kết với tài khoản Customer đang đăng nhập.
    Tra cứu theo thứ tự:
    1. Cơ sở dữ liệu (DealerEntity) theo id, email, phone, code (username), full_name.
    2. Bộ nhớ/Cache DEALERS_DB theo id hoặc thông tin tương tự.
    3. Fallback mặc định về đại lý mẫu (ID 7 hoặc 1) nếu không tìm thấy.
    """
    # 1. Tra cứu trực tiếp từ bảng dealers trong CSDL
    dealer_filters = [DealerEntity.id == current_user.id]
    if current_user.email:
        dealer_filters.append(and_(DealerEntity.email.isnot(None), func.lower(DealerEntity.email) == current_user.email.lower()))
    if current_user.phone:
        dealer_filters.append(and_(DealerEntity.phone.isnot(None), DealerEntity.phone == current_user.phone))
    if current_user.username:
        dealer_filters.append(and_(DealerEntity.code.isnot(None), func.lower(DealerEntity.code) == current_user.username.lower()))
    if current_user.full_name:
        dealer_filters.append(and_(DealerEntity.name.isnot(None), func.lower(DealerEntity.name) == current_user.full_name.lower()))

    db_dealer = db.query(DealerEntity).filter(or_(*dealer_filters)).first()
    if db_dealer:
        return db_dealer

    # 2. Tra cứu từ DEALERS_DB
    from app.models.dealer import DEALERS_DB, load_dealers_db
    load_dealers_db()

    dealer = DEALERS_DB.get(current_user.id)
    if dealer:
        return dealer

    for d in DEALERS_DB.values():
        if (d.email and current_user.email and d.email.lower() == current_user.email.lower()) or \
           (d.phone and current_user.phone and d.phone == current_user.phone) or \
           (d.code and current_user.username and d.code.lower() == current_user.username.lower()) or \
           (d.name and current_user.full_name and d.name.lower() == current_user.full_name.lower()):
            return d

    # 3. Fallback mặc định
    if DEALERS_DB:
        return DEALERS_DB.get(7) or DEALERS_DB.get(1)
    return None


def get_active_price_book_for_dealer(db: Session, dealer: Any) -> Optional[PriceBookEntity]:
    """
    Xác định Bảng giá đang có hiệu lực (Active và thời gian thực nằm trong khoảng start_date/valid_from -> end_date/valid_to).
    - Ưu tiên 1: Bảng giá riêng được chỉ định trực tiếp cho Đại lý (nếu có cấu hình price_list)
    - Ưu tiên 2: Bảng giá có hiệu lực mới nhất theo Nhóm khách hàng (customer_group)
    """
    if not dealer:
        return None

    now_aware = datetime.now(timezone.utc)
    now_naive = now_aware.replace(tzinfo=None)

    date_condition = or_(
        and_(PriceBookEntity.valid_from <= now_aware, PriceBookEntity.valid_to >= now_aware),
        and_(PriceBookEntity.valid_from <= now_naive, PriceBookEntity.valid_to >= now_naive),
    )
    status_condition = or_(
        PriceBookEntity.status == "ACTIVE",
        PriceBookEntity.status == "active",
        func.upper(PriceBookEntity.status) == "ACTIVE",
    )

    # 1. Kiểm tra Bảng giá riêng lẻ được gán trực tiếp (theo DealerProfile hoặc thuộc tính price_list)
    specific_price_list = getattr(dealer, "price_list", None)
    if not specific_price_list and hasattr(dealer, "id"):
        try:
            from app.models.dealer_profile import DealerProfileEntity
            profile = db.query(DealerProfileEntity).filter(DealerProfileEntity.dealer_id == dealer.id).first()
            if profile and profile.price_list:
                specific_price_list = profile.price_list
        except Exception:
            pass

    if specific_price_list:
        sp_pb = (
            db.query(PriceBookEntity)
            .filter(
                or_(
                    PriceBookEntity.code == specific_price_list,
                    PriceBookEntity.name == specific_price_list,
                ),
                status_condition,
                date_condition,
            )
            .order_by(
                PriceBookEntity.version.desc(),
                PriceBookEntity.created_at.desc(),
                PriceBookEntity.id.desc(),
            )
            .first()
        )
        if sp_pb:
            return sp_pb

    # 2. Tra cứu Bảng giá đang có hiệu lực theo Nhóm khách hàng (customer_group)
    cust_group = getattr(dealer, "customer_group", None)
    if not cust_group and hasattr(dealer, "id"):
        try:
            from app.models.dealer_profile import DealerProfileEntity
            profile = db.query(DealerProfileEntity).filter(DealerProfileEntity.dealer_id == dealer.id).first()
            if profile and profile.customer_group:
                cust_group = profile.customer_group
        except Exception:
            pass

    cust_group = cust_group or "dai_ly_cap_1"
    aliases = get_customer_group_aliases(cust_group)

    active_pb = (
        db.query(PriceBookEntity)
        .filter(
            PriceBookEntity.customer_group.in_(aliases),
            status_condition,
            date_condition,
        )
        .order_by(
            PriceBookEntity.version.desc(),
            PriceBookEntity.created_at.desc(),
            PriceBookEntity.id.desc(),
        )
        .first()
    )
    return active_pb


def get_product_transaction_count(product_id: int, db: Session) -> int:
    """Đếm số giao dịch liên quan tới sản phẩm (orders + inventory transactions)."""
    if not db:
        return 0
    try:
        from app.api.v1.endpoints.products import _get_product_transaction_count
        return _get_product_transaction_count(product_id, db)
    except Exception:
        try:
            return db.query(InventoryTransactionEntity).filter(
                InventoryTransactionEntity.product_id == product_id
            ).count()
        except Exception:
            return 0


def get_products_for_customer(
    db: Session,
    current_user: UserResponse,
    page: Optional[int] = None,
    limit: Optional[int] = None,
    search: Optional[str] = None,
    category: Optional[str] = None,
) -> ProductListResponse:
    """
    Lấy danh sách sản phẩm hiển thị cho Đại lý (Role: Customer) với logic Dynamic Pricing thời gian thực:
    
    1. Xác định Bảng giá:
       - Tìm Đại lý tương ứng của current_user.
       - Kiểm tra Bảng giá riêng hoặc Bảng giá theo Nhóm khách hàng đang ACTIVE và trong khoảng thời gian valid_from -> valid_to.
    
    2. Ghi đè giá động (On-the-fly Dynamic Pricing Override):
       - Thực hiện LEFT OUTER JOIN giữa bảng Products và PriceBookItems (của Bảng giá đã xác định).
       - Dùng func.coalesce() trực tiếp trong câu lệnh query SQLAlchemy:
         + Nếu sản phẩm CÓ trong PriceBookItems: Trả về sale_price (hoặc price) từ PriceBookItems.
         + Nếu sản phẩm KHÔNG CÓ trong PriceBookItems: Trả về Giá niêm yết (sell_price) mặc định từ Products.
       - Tương tự cho giá sàn (floor_price / min_price).
    
    3. Nguyên tắc bảo vệ dữ liệu & bảo mật (Zero-Trust):
       - TUYỆT ĐỐI KHÔNG thực hiện thao tác UPDATE giá vào CSDL bảng Products. Giá niêm yết gốc luôn được bảo toàn.
       - Giấu hoàn toàn các thông tin nhạy cảm (cost_price, profit_margin, profit_per_unit = None).
       - Thời gian thực (Real-time): Truy vấn trực tiếp, phản ánh ngay lập tức khi Sales/Admin đổi giá.
    """
    # Đồng bộ dữ liệu in-memory RAW_PRODUCTS nếu có sản phẩm chưa tồn tại trong bảng products DB
    from app.api.v1.endpoints.products import RAW_PRODUCTS
    if RAW_PRODUCTS:
        try:
            existing_db_ids = {p.id for p in db.query(ProductEntity.id).all()}
            missing_products = [p for p in RAW_PRODUCTS if p.get("id") not in existing_db_ids]
            if missing_products:
                for p in missing_products:
                    db_p = ProductEntity(
                        id=p.get("id"),
                        code=p.get("code"),
                        name=p.get("name"),
                        category=p.get("category", "Thời trang"),
                        category_id=p.get("category_id"),
                        stock=p.get("stock", 0),
                        sell_price=p.get("sell_price", 0.0),
                        floor_price=p.get("floor_price", 0.0),
                        cost_price=p.get("cost_price", 0.0),
                        base_unit=p.get("base_unit", "Cái"),
                        packaging_specification=p.get("packaging_specification") or "",
                        images_json=json.dumps(p.get("images") or [], ensure_ascii=False),
                        status=p.get("status", "active"),
                        is_batch_managed=bool(p.get("is_batch_managed", False)),
                    )
                    db.add(db_p)
                db.commit()
        except Exception:
            db.rollback()

    # 1. Xác định Đại lý và Bảng giá hiệu lực
    dealer = resolve_customer_dealer(db, current_user)
    active_pb = get_active_price_book_for_dealer(db, dealer) if dealer else None

    # 2. Xây dựng câu truy vấn SQLAlchemy sử dụng outerjoin và func.coalesce
    if active_pb is not None:
        effective_sell_price = func.coalesce(
            case((PriceBookItemEntity.sale_price > 0, PriceBookItemEntity.sale_price), else_=None),
            case((PriceBookItemEntity.price > 0, PriceBookItemEntity.price), else_=None),
            ProductEntity.sell_price,
            0.0
        ).label("effective_sell_price")

        effective_floor_price = func.coalesce(
            case((PriceBookItemEntity.floor_price > 0, PriceBookItemEntity.floor_price), else_=None),
            case((PriceBookItemEntity.min_price > 0, PriceBookItemEntity.min_price), else_=None),
            ProductEntity.floor_price,
            0.0
        ).label("effective_floor_price")

        query = (
            db.query(
                ProductEntity,
                effective_sell_price,
                effective_floor_price,
            )
            .outerjoin(
                PriceBookItemEntity,
                and_(
                    PriceBookItemEntity.product_id == ProductEntity.id,
                    PriceBookItemEntity.price_book_id == active_pb.id,
                )
            )
        )
    else:
        effective_sell_price = func.coalesce(ProductEntity.sell_price, 0.0).label("effective_sell_price")
        effective_floor_price = func.coalesce(ProductEntity.floor_price, 0.0).label("effective_floor_price")

        query = (
            db.query(
                ProductEntity,
                effective_sell_price,
                effective_floor_price,
            )
        )

    # Áp dụng bộ lọc tìm kiếm và ngành hàng
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(ProductEntity.name).like(term),
                func.lower(ProductEntity.code).like(term)
            )
        )

    if category and category.strip() and category.strip().upper() != "ALL":
        query = query.filter(ProductEntity.category == category.strip())

    # Lấy tổng số lượng sản phẩm thỏa mãn điều kiện trước khi phân trang (Total count)
    total = query.count()

    # Sắp xếp theo ID
    query = query.order_by(ProductEntity.id.asc())

    # Phân trang Server-Side (SQLAlchemy offset & limit)
    is_paginated = page is not None or limit is not None
    current_page = max(1, page) if page is not None else 1
    page_limit = max(1, limit) if limit is not None else (20 if is_paginated else (total if total > 0 else 20))
    total_pages = max(1, math.ceil(total / page_limit)) if total > 0 else 1

    if is_paginated:
        query = query.offset((current_page - 1) * page_limit).limit(page_limit)

    rows = query.all()

    # 3. Chuẩn hóa danh sách sản phẩm trả về tầng Schema/DTO (Bảo toàn tuyệt đối dữ liệu CSDL)
    items: list[ProductItem] = []
    total_stock = 0
    total_sell_val = 0.0

    try:
        from app.api.v1.endpoints.products import _get_all_product_transaction_counts
        tx_counts_map = _get_all_product_transaction_counts(db)
    except Exception:
        tx_counts_map = {}

    for db_p, eff_sell, eff_floor in rows:
        units_raw = getattr(db_p, "units", None) or []
        units_converted: list[UnitConversionItem] = []
        for u in units_raw:
            if isinstance(u, dict) and "unit_name" in u and "conversion_rate" in u:
                try:
                    units_converted.append(
                        UnitConversionItem(
                            unit_name=str(u["unit_name"]),
                            conversion_rate=float(u["conversion_rate"]),
                        )
                    )
                except (ValueError, TypeError):
                    pass

        stock = int(db_p.stock or 0)
        sell_price = float(eff_sell if eff_sell is not None else (db_p.sell_price or 0.0))
        floor_price = float(eff_floor if eff_floor is not None else (db_p.floor_price or 0.0))

        total_stock += stock
        total_sell_val += sell_price * stock

        db_images = getattr(db_p, "images", None) or []
        if not db_images and db_p:
            for raw_it in RAW_PRODUCTS:
                if raw_it.get("id") == db_p.id or raw_it.get("code") == db_p.code:
                    db_images = raw_it.get("images") or []
                    break

        items.append(
            ProductItem(
                id=db_p.id,
                code=db_p.code,
                name=db_p.name,
                category=db_p.category or "Chưa phân loại",
                category_id=db_p.category_id,
                stock=stock,
                sell_price=sell_price,
                floor_price=floor_price,
                base_unit=db_p.base_unit or "Cái",
                units=units_converted,
                packaging_specification=db_p.packaging_specification or "",
                images=db_images,
                status=db_p.status or "active",
                is_batch_managed=bool(db_p.is_batch_managed),
                transaction_count=tx_counts_map.get(db_p.id, 0),
                # AC 3 & Zero-Trust: Tuyệt đối giấu giá vốn và biên lợi nhuận đối với Customer
                cost_price=None,
                profit_margin=None,
                profit_per_unit=None,
            )
        )

    # 4. Tổng quan tài chính hiển thị cho Customer (không có giá vốn / lãi gộp)
    summary = ProductFinancialSummary(
        total_products=len(items),
        total_stock=total_stock,
        total_sell_value=total_sell_val,
        total_cost_value=None,
        total_gross_profit=None,
        average_margin_percent=None,
    )

    applied_name = active_pb.name if active_pb else None
    return ProductListResponse(
        items=items,
        data=items,
        total=total,
        page=current_page,
        limit=page_limit,
        total_pages=total_pages,
        user_role=current_user.role,
        is_cost_price_visible=False,
        summary=summary,
        applied_price_book=applied_name,
    )

class PricingService:
    @staticmethod
    def normalize_customer_group(customer_group: Optional[str]) -> list[str]:
        group = customer_group or "dai_ly_cap_1"
        if group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
            return ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1", "dai_ly_cap_1"]
        elif group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
            return ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2", "dai_ly_cap_2"]
        elif group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
            return ["Khach_le", "RETAIL", "Khách lẻ", "khach_le"]
        return [group]

    @staticmethod
    def resolve_customer_group_from_dealer(dealer_id_or_group: Any, db: Session) -> str:
        if isinstance(dealer_id_or_group, int) or (isinstance(dealer_id_or_group, str) and dealer_id_or_group.isdigit()):
            d_id = int(dealer_id_or_group)
            dealer = db.query(DealerEntity).filter(DealerEntity.id == d_id).first()
            if not dealer:
                from app.models.dealer import DEALERS_DB
                dealer = DEALERS_DB.get(d_id)
            if dealer:
                return getattr(dealer, "customer_group", None) or "dai_ly_cap_1"
        elif isinstance(dealer_id_or_group, str) and dealer_id_or_group:
            return dealer_id_or_group
        return "dai_ly_cap_1"

    @staticmethod
    def get_effective_price(
        customer_group_or_dealer_id: Any,
        product_id: int,
        db: Session
    ) -> Dict[str, Any]:
        """
        AC1: Tra cứu đơn giá và giá sàn theo Bảng giá hiệu lực.
        Điều kiện bảng giá: status == 'ACTIVE' VÀ valid_from <= TODAY <= valid_to.
        Chặn khi thiếu bảng giá (Hard Validation):
        Nếu SKU chưa có bảng giá hiệu lực: Raise HTTPException(400, "SKU [Mã_SKU] chưa có bảng giá đang hiệu lực cho nhóm khách hàng này. Vui lòng kiểm tra lại cấu hình giá.")
        """
        customer_group = PricingService.resolve_customer_group_from_dealer(customer_group_or_dealer_id, db)
        aliases = PricingService.normalize_customer_group(customer_group)
        now = datetime.now(timezone.utc)

        prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
        prod_code = prod.code if prod else f"SP{product_id:03d}"
        prod_name = prod.name if prod else f"Sản phẩm #{product_id}"

        # Tìm bảng giá hiệu lực và dòng sản phẩm
        item_match = db.query(PriceBookItemEntity, PriceBookEntity).join(
            PriceBookEntity, PriceBookItemEntity.price_book_id == PriceBookEntity.id
        ).filter(
            PriceBookEntity.customer_group.in_(aliases),
            PriceBookEntity.status == "ACTIVE",
            PriceBookEntity.valid_from <= now,
            PriceBookEntity.valid_to >= now,
            PriceBookItemEntity.product_id == product_id
        ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

        if not item_match:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SKU [{prod_code}] chưa có bảng giá đang hiệu lực cho nhóm khách hàng này. Vui lòng kiểm tra lại cấu hình giá."
            )

        pb_item, pb = item_match
        sale_val = pb_item.sale_price if pb_item.sale_price is not None else pb_item.price
        floor_val = pb_item.floor_price if pb_item.floor_price is not None else pb_item.min_price

        # Fallback về giá niêm yết của sản phẩm chỉ khi giá bảng giá chưa được đặt (bằng 0 hoặc None)
        if (sale_val is None or sale_val == 0) and prod and prod.sell_price:
            sale_val = prod.sell_price
        if (floor_val is None or floor_val == 0) and prod and prod.floor_price:
            floor_val = prod.floor_price

        return {
            "price_book_id": pb.id,
            "price_book_code": pb.code,
            "price_book_name": pb.name,
            "customer_group": pb.customer_group,
            "product_id": product_id,
            "product_code": prod_code,
            "product_name": prod_name,
            "sale_price": float(sale_val or 0.0),
            "floor_price": float(floor_val or 0.0),
            "unit_price": float(sale_val or 0.0),
        }

    @staticmethod
    def compute_tier_discount(
        product_id: int,
        quantity: int,
        customer_group: Optional[str] = None,
        base_price: float = 0.0,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        AC2: Tự động tính Chiết khấu theo sản lượng (Tiered Quantity Discount).
        Map với các bậc sản lượng quy định: min_qty <= quantity < max_qty (hoặc <= max_qty nếu là khoảng đóng).
        Nếu quantity dưới ngưỡng tối thiểu của bậc thấp nhất: Chiết khấu mặc định là 0.
        """
        if quantity <= 0:
            return {
                "discount_rate": 0.0,
                "discount_amount": 0.0,
                "unit_discount": 0.0,
                "applied_policy_code": None,
                "applied_policy_name": None,
                "applied_tier_label": None,
                "line_total": 0.0,
            }

        policies = []
        if db:
            entities = db.query(DiscountPolicyEntity).filter(
                DiscountPolicyEntity.is_active == True,
                DiscountPolicyEntity.status == "active"
            ).all()
            policies = [e.to_dict() for e in entities]

        if not policies:
            from app.api.v1.endpoints.discounts import load_discount_policies
            policies = load_discount_policies(db)

        best_rate = 0.0
        best_amount = 0.0
        applied_code = None
        applied_name = None
        applied_label = None

        prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first() if db else None
        prod_cat = prod.category if prod else None

        for policy in policies:
            if not policy.get("is_active"):
                continue

            start_date = policy.get("start_date")
            end_date = policy.get("end_date")
            today_str = datetime.now().strftime("%Y-%m-%d")
            if start_date and today_str < str(start_date):
                continue
            if end_date and today_str > str(end_date):
                continue

            p_cat = (policy.get("category") or "ALL").upper()
            if p_cat != "ALL" and prod_cat and p_cat != prod_cat.upper():
                continue

            t_grp = (policy.get("target_dealer_type") or policy.get("target_group") or "ALL").upper()
            if t_grp != "ALL" and customer_group:
                c_norm = customer_group.upper()
                if t_grp not in c_norm and c_norm not in t_grp:
                    continue

            tiers = policy.get("tiers", [])
            for tier in tiers:
                min_q = tier.get("min_quantity", 0)
                max_q = tier.get("max_quantity")
                disc_pct = float(tier.get("discount_percent", 0.0) or 0.0)
                disc_fixed = float(tier.get("discount_amount", 0.0) or 0.0)

                # Kiểm tra bậc sản lượng: min_qty <= quantity
                in_tier = False
                if max_q is not None and max_q > 0:
                    in_tier = (min_q <= quantity <= max_q)
                else:
                    in_tier = (quantity >= min_q)

                if in_tier and min_q > 0:
                    curr_rate = disc_pct
                    curr_amt = (quantity * base_price * (disc_pct / 100.0)) + (disc_fixed * quantity)
                    if curr_amt > best_amount:
                        best_amount = curr_amt
                        best_rate = curr_rate
                        applied_code = policy.get("code")
                        applied_name = policy.get("name")
                        applied_label = f"Từ {min_q} {f'đến {max_q}' if max_q else 'trở lên'}: giảm {disc_pct}%"

        line_subtotal = quantity * base_price
        line_discount = round(best_amount, 2)
        line_total = round(max(0.0, line_subtotal - line_discount), 2)

        return {
            "discount_rate": best_rate,
            "discount_amount": line_discount,
            "unit_discount": round(line_discount / quantity, 2) if quantity > 0 else 0.0,
            "applied_policy_code": applied_code,
            "applied_policy_name": applied_name,
            "applied_tier_label": applied_label,
            "line_total": line_total,
        }

    @staticmethod
    def calculate_line_item(
        customer_group_or_dealer_id: Any,
        product_id: int,
        quantity: int,
        override_price: Optional[float] = None,
        db: Session = None
    ) -> Dict[str, Any]:
        """
        AC1, AC2, AC3: Tổng hợp tính toán cho một dòng hàng:
        1. Lấy đơn giá theo bảng giá hiệu lực (AC1)
        2. Kiểm tra override_price & giá sàn floor_price (AC3)
        3. Tính chiết khấu theo sản lượng (AC2)
        """
        price_info = PricingService.get_effective_price(customer_group_or_dealer_id, product_id, db)
        book_price = price_info["sale_price"]
        floor_price = price_info["floor_price"]

        unit_price = float(override_price) if (override_price is not None and override_price >= 0) else book_price

        # Kiểm tra giá sàn (AC3)
        is_below_floor = bool(floor_price > 0 and unit_price < floor_price)
        approval_reason = None
        if is_below_floor:
            approval_reason = f"SKU [{price_info['product_code']}] có đơn giá bán nhỏ hơn giá sàn quy định ({floor_price:,.0f})."

        # Tính chiết khấu theo sản lượng (AC2)
        tier_calc = PricingService.compute_tier_discount(
            product_id=product_id,
            quantity=quantity,
            customer_group=price_info.get("customer_group"),
            base_price=unit_price,
            db=db
        )

        return {
            "product_id": product_id,
            "product_code": price_info["product_code"],
            "product_name": price_info["product_name"],
            "quantity": quantity,
            "unit_price": unit_price,
            "book_price": book_price,
            "floor_price": floor_price,
            "is_below_floor": is_below_floor,
            "requires_approval": is_below_floor,
            "approval_reason": approval_reason,
            "discount_rate": tier_calc["discount_rate"],
            "discount_amount": tier_calc["discount_amount"],
            "applied_policy_code": tier_calc["applied_policy_code"],
            "applied_policy_name": tier_calc["applied_policy_name"],
            "line_total": tier_calc["line_total"],
        }

    @staticmethod
    def validate_order_price_floor(
        items: List[Dict[str, Any]],
        customer_group_or_dealer_id: Any,
        db: Session
    ) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
        """
        AC3: Kiểm tra giá sàn cho danh sách dòng hàng của đơn hàng.
        """
        requires_approval = False
        reasons = []
        evaluations = []

        for item in items:
            p_id = item.get("product_id")
            actual_price = float(item.get("price") if item.get("price") is not None else (item.get("unit_price") or 0.0))
            qty = int(item.get("quantity") or 1)

            try:
                price_info = PricingService.get_effective_price(customer_group_or_dealer_id, p_id, db)
                floor_price = price_info["floor_price"]
                prod_code = price_info["product_code"]
                book_price = price_info["sale_price"]

                is_below = bool(floor_price > 0 and actual_price < floor_price)
                if is_below:
                    requires_approval = True
                    reason = f"SKU [{prod_code}] có đơn giá bán nhỏ hơn giá sàn quy định ({floor_price:,.0f})."
                    reasons.append(reason)

                evaluations.append({
                    "product_id": p_id,
                    "product_code": prod_code,
                    "product_name": price_info["product_name"],
                    "unit_price": actual_price,
                    "book_price": book_price,
                    "floor_price": floor_price,
                    "is_below_floor": is_below,
                    "quantity": qty,
                })
            except HTTPException as e:
                requires_approval = True
                reasons.append(e.detail)
                evaluations.append({
                    "product_id": p_id,
                    "unit_price": actual_price,
                    "floor_price": 0.0,
                    "is_below_floor": True,
                    "quantity": qty,
                    "error": e.detail,
                })

        return requires_approval, reasons, evaluations

    # Compatibility aliases with Customer dynamic pricing
    @staticmethod
    def get_products_for_customer(db: Session, current_user: UserResponse) -> ProductListResponse:
        return get_products_for_customer(db, current_user)

    @staticmethod
    def resolve_customer_dealer(current_user: UserResponse, db: Session = None):
        return resolve_customer_dealer(current_user, db)
