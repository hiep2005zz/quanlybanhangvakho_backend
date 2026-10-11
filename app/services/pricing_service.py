# backend/app/services/pricing_service.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_, literal

from app.models.entities import ProductEntity, DealerEntity, InventoryTransactionEntity, OrderEntity
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
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


def get_products_for_customer(db: Session, current_user: UserResponse) -> ProductListResponse:
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
        # Nếu sản phẩm CÓ trong PriceBookItems -> lấy sale_price / price từ PriceBookItems
        # Nếu sản phẩm KHÔNG CÓ -> coalesce fallback về ProductEntity.sell_price (Giá niêm yết gốc)
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
            .order_by(ProductEntity.id.asc())
        )
    else:
        # Nếu không có Bảng giá riêng nào: Fallback về 100% Giá niêm yết từ bảng Products
        effective_sell_price = func.coalesce(ProductEntity.sell_price, 0.0).label("effective_sell_price")
        effective_floor_price = func.coalesce(ProductEntity.floor_price, 0.0).label("effective_floor_price")

        query = (
            db.query(
                ProductEntity,
                effective_sell_price,
                effective_floor_price,
            )
            .order_by(ProductEntity.id.asc())
        )

    rows = query.all()

    # 3. Chuẩn hóa danh sách sản phẩm trả về tầng Schema/DTO (Bảo toàn tuyệt đối dữ liệu CSDL)
    items: list[ProductItem] = []
    total_stock = 0
    total_sell_val = 0.0

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
                images=db_p.images or [],
                status=db_p.status or "active",
                is_batch_managed=bool(db_p.is_batch_managed),
                transaction_count=get_product_transaction_count(db_p.id, db),
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

    return ProductListResponse(
        items=items,
        total=len(items),
        user_role=current_user.role,
        is_cost_price_visible=False,
        summary=summary,
    )
