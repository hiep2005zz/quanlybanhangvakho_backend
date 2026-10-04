# backend/app/api/v1/endpoints/price_books.py
"""
Price Book Management Endpoints (Quản lý Bảng giá theo nhóm khách hàng).
Tuân thủ nghiêm ngặt Ma trận Phân quyền (RBAC) và Zero-Trust:
- CRUD và Cấu hình Bảng giá: CHỈ sales_manager và admin.
- Chặn truy cập (No Access - 403 Forbidden): warehouse, warehouse_manager, purchasing.
- Tra cứu/Áp giá đơn hàng: sales, customer, sales_manager, admin.
- Khóa chỉnh sửa (Block Update): Bảng giá đã phát sinh đơn hàng (is_locked == True) -> 400 Bad Request.
"""
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.api.deps import require_roles
from app.core.database import get_db
from app.core.rbac import Role
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import DealerEntity, ProductEntity, OrderEntity
from app.schemas.price_book import (
    PriceBookCreate,
    PriceBookResponse,
    PriceBookUpdate,
    PriceBookItemResponse,
    ResolvePriceResponse,
    OrderPriceValidationRequest,
    OrderPriceValidationResponse,
    OrderItemEvaluation
)
from app.schemas.auth import UserResponse

router = APIRouter()

# Nhóm vai trò được xem bảng giá (Read-only): admin, sales_manager, accountant
PRICE_BOOK_READERS = [
    Role.SYSTEM_ADMIN.value,
    Role.SALES_MANAGER.value,
    Role.ACCOUNTANT.value,
]

# Nhóm vai trò được cấu hình / chỉnh sửa bảng giá: CHỈ admin và sales_manager (accountant bị cấm Thêm/Sửa/Clone)
PRICE_BOOK_MANAGERS = [
    Role.SYSTEM_ADMIN.value,
    Role.SALES_MANAGER.value,
]

# Nhóm vai trò được phép áp dụng giá: sales, customer, sales_manager, admin
PRICE_RESOLVER_ROLES = [
    Role.SYSTEM_ADMIN.value,
    Role.SALES_MANAGER.value,
    Role.SALES.value,
    Role.CUSTOMER.value,
]

def get_utc_now():
    return datetime.now(timezone.utc)

def _build_price_book_response(pb: PriceBookEntity, db: Session) -> PriceBookResponse:
    db_items = db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == pb.id).all()
    response_items = []
    for item in db_items:
        prod = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).first()
        sale_val = item.sale_price if item.sale_price is not None else item.price
        floor_val = item.floor_price if item.floor_price is not None else item.min_price
        response_items.append(PriceBookItemResponse(
            id=item.id,
            product_id=item.product_id,
            product_code=prod.code if prod else None,
            product_name=prod.name if prod else None,
            sale_price=float(sale_val or 0.0),
            floor_price=float(floor_val or 0.0),
            price=float(sale_val or 0.0),
            min_price=float(floor_val or 0.0),
        ))

    return PriceBookResponse(
        id=pb.id,
        code=pb.code,
        name=pb.name,
        customer_group=pb.customer_group,
        valid_from=pb.valid_from,
        valid_to=pb.valid_to,
        status=pb.status or "ACTIVE",
        version=pb.version or 1,
        is_locked=bool(pb.is_locked),
        note=pb.note,
        created_by=pb.created_by or "admin",
        created_at=pb.created_at,
        items=response_items
    )


@router.get("", response_model=List[PriceBookResponse])
def get_price_books(
    customer_group: Optional[str] = None,
    status_filter: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    is_active_now: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_BOOK_READERS))
):
    """
    Lấy danh sách bảng giá (hỗ trợ lọc theo customer_group, status, search).
    Cho phép admin, sales_manager và accountant (Read-only).
    """
    query = db.query(PriceBookEntity)

    if customer_group:
        # Hỗ trợ cả key mã hóa và text hiển thị
        aliases = [customer_group]
        if customer_group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
            aliases = ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]
        elif customer_group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
            aliases = ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]
        elif customer_group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
            aliases = ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]
        query = query.filter(PriceBookEntity.customer_group.in_(aliases))

    effective_status = status_filter or status
    if effective_status:
        query = query.filter(PriceBookEntity.status == effective_status)

    if search:
        search_term = f"%{search.strip().lower()}%"
        query = query.filter(
            (PriceBookEntity.code.ilike(search_term)) |
            (PriceBookEntity.name.ilike(search_term))
        )

    if is_active_now:
        now = get_utc_now()
        query = query.filter(
            PriceBookEntity.status == "ACTIVE",
            PriceBookEntity.valid_from <= now,
            PriceBookEntity.valid_to >= now
        )

    query = query.order_by(PriceBookEntity.created_at.desc())
    price_books = query.all()

    return [_build_price_book_response(pb, db) for pb in price_books]


@router.post("", response_model=PriceBookResponse, status_code=status.HTTP_201_CREATED)
def create_price_book(
    data: PriceBookCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_BOOK_MANAGERS))
):
    """
    Tạo mới bảng giá cùng danh sách sản phẩm chi tiết trong một Transaction duy nhất.
    Validate: code duy nhất, valid_to >= valid_from.
    """
    if db.query(PriceBookEntity).filter(PriceBookEntity.code == data.code).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã bảng giá đã tồn tại."
        )

    if data.valid_to < data.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngày kết thúc không được nhỏ hơn ngày bắt đầu."
        )

    try:
        new_pb = PriceBookEntity(
            code=data.code.strip(),
            name=data.name.strip(),
            customer_group=data.customer_group,
            valid_from=data.valid_from,
            valid_to=data.valid_to,
            status=data.status or "ACTIVE",
            version=data.version or 1,
            is_locked=False,
            note=data.note,
            created_by=current_user.username
        )
        db.add(new_pb)
        db.flush()

        for item in data.items:
            sale_val = item.sale_price if item.sale_price is not None else (item.price or 0.0)
            floor_val = item.floor_price if item.floor_price is not None else (item.min_price or 0.0)
            pb_item = PriceBookItemEntity(
                price_book_id=new_pb.id,
                product_id=item.product_id,
                sale_price=sale_val,
                floor_price=floor_val,
                price=sale_val,
                min_price=floor_val
            )
            db.add(pb_item)

        db.commit()
        db.refresh(new_pb)
    except Exception as ex:
        db.rollback()
        raise ex

    return _build_price_book_response(new_pb, db)


# Endpoint resolve-price phải nằm trước /{id} để không bị nuốt bởi path parameter
@router.get("/resolve-price", response_model=ResolvePriceResponse)
def resolve_price(
    product_id: int,
    customer_id: Optional[int] = None,
    customer_group: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_RESOLVER_ROLES))
):
    """
    Endpoint hỗ trợ luồng Đơn hàng (Order):
    Tự động tìm bảng giá đang hiệu lực (valid_from <= now <= valid_to) tương ứng
    với nhóm của khách hàng, trả về sale_price và floor_price.
    """
    # 1. Tìm thông tin khách hàng / đại lý
    group = customer_group or "dai_ly_cap_1"
    if customer_id is not None:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == customer_id).first()
        if dealer:
            group = getattr(dealer, "customer_group", None) or customer_group or "dai_ly_cap_1"
        else:
            from app.models.dealer import DEALERS_DB
            d_mem = DEALERS_DB.get(customer_id)
            if d_mem:
                group = getattr(d_mem, "customer_group", None) or customer_group or "dai_ly_cap_1"
            elif not customer_group:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Không tìm thấy khách hàng / đại lý với ID {customer_id}."
                )

    customer_group = group

    # 2. Chuẩn hóa nhóm khách hàng
    aliases = [customer_group]
    if customer_group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
        aliases = ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1", "dai_ly_cap_1"]
    elif customer_group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
        aliases = ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2", "dai_ly_cap_2"]
    elif customer_group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
        aliases = ["Khach_le", "RETAIL", "Khách lẻ", "khach_le"]

    now = get_utc_now()
    prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
    prod_code = prod.code if prod else None
    prod_name = prod.name if prod else None

    # Tìm chính xác dòng sản phẩm trong bảng giá đang hiệu lực của nhóm khách hàng
    item_match = db.query(PriceBookItemEntity, PriceBookEntity).join(
        PriceBookEntity, PriceBookItemEntity.price_book_id == PriceBookEntity.id
    ).filter(
        PriceBookEntity.customer_group.in_(aliases),
        PriceBookEntity.status == "ACTIVE",
        PriceBookEntity.valid_from <= now,
        PriceBookEntity.valid_to >= now,
        PriceBookItemEntity.product_id == product_id
    ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

    if item_match:
        pb_item, pb = item_match
    else:
        # Nếu chưa tìm thấy theo sản phẩm, kiểm tra xem nhóm khách hàng này có bảng giá hiệu lực nào không
        pb = db.query(PriceBookEntity).filter(
            PriceBookEntity.customer_group.in_(aliases),
            PriceBookEntity.status == "ACTIVE",
            PriceBookEntity.valid_from <= now,
            PriceBookEntity.valid_to >= now
        ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

        if not pb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy bảng giá đang hiệu lực cho nhóm khách hàng '{customer_group}'."
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sản phẩm {prod_code or product_id} không có trong bảng giá '{pb.name}'."
        )

    sale_val = pb_item.sale_price if pb_item.sale_price is not None else pb_item.price
    floor_val = pb_item.floor_price if pb_item.floor_price is not None else pb_item.min_price

    return ResolvePriceResponse(
        price_book_id=pb.id,
        price_book_code=pb.code,
        price_book_name=pb.name,
        customer_id=customer_id,
        customer_group=pb.customer_group,
        product_id=product_id,
        product_code=prod_code,
        product_name=prod_name,
        sale_price=float(sale_val or 0.0),
        floor_price=float(floor_val or 0.0)
    )


@router.get("/{id}", response_model=PriceBookResponse)
def get_price_book(
    id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_BOOK_READERS))
):
    """
    Lấy chi tiết bảng giá kèm danh sách sản phẩm.
    Cho phép admin, sales_manager và accountant (Read-only).
    """
    pb = db.query(PriceBookEntity).filter(PriceBookEntity.id == id).first()
    if not pb:
        raise HTTPException(status_code=404, detail="Không tìm thấy bảng giá.")

    return _build_price_book_response(pb, db)


@router.put("/{id}", response_model=PriceBookResponse)
def update_price_book(
    id: int,
    data: PriceBookUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_BOOK_MANAGERS))
):
    """
    Cập nhật thông tin bảng giá.
    BẮT BUỘC: Kiểm tra nếu is_locked == True thì trả về lỗi 400 Bad Request
    kèm thông báo "Bảng giá đã phát sinh đơn, không thể sửa".
    """
    pb = db.query(PriceBookEntity).filter(PriceBookEntity.id == id).first()
    if not pb:
        raise HTTPException(status_code=404, detail="Không tìm thấy bảng giá.")

    # TIÊU CHÍ BẮT BUỘC: Bảng giá đã phát sinh đơn hàng thì KHÔNG sửa (Block Update)
    if pb.is_locked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bảng giá đã phát sinh đơn, không thể sửa. Bảng giá đã phát sinh đơn hàng, không được phép chỉnh sửa."
        )

    # Validate thời gian
    v_from = data.valid_from if data.valid_from is not None else pb.valid_from
    v_to = data.valid_to if data.valid_to is not None else pb.valid_to
    if v_to < v_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngày kết thúc không được nhỏ hơn ngày bắt đầu."
        )

    if data.name is not None:
        pb.name = data.name.strip()
    if data.customer_group is not None:
        pb.customer_group = data.customer_group
    if data.valid_from is not None:
        pb.valid_from = data.valid_from
    if data.valid_to is not None:
        pb.valid_to = data.valid_to
    if data.status is not None:
        pb.status = data.status
    if data.note is not None:
        pb.note = data.note

    if data.items is not None:
        # Xóa items cũ và thêm items mới
        db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == id).delete()
        for item in data.items:
            sale_val = item.sale_price if item.sale_price is not None else (item.price or 0.0)
            floor_val = item.floor_price if item.floor_price is not None else (item.min_price or 0.0)
            pb_item = PriceBookItemEntity(
                price_book_id=pb.id,
                product_id=item.product_id,
                sale_price=sale_val,
                floor_price=floor_val,
                price=sale_val,
                min_price=floor_val
            )
            db.add(pb_item)

    db.commit()
    db.refresh(pb)

    return _build_price_book_response(pb, db)


@router.post("/{id}/clone", response_model=PriceBookResponse, status_code=status.HTTP_201_CREATED)
def clone_price_book(
    id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_BOOK_MANAGERS))
):
    """
    Tạo phiên bản mới từ bảng giá cũ (tăng version lên 1, sao chép toàn bộ dòng sản phẩm sang bản ghi mới).
    Phiên bản mới khởi tạo với is_locked = False.
    """
    pb = db.query(PriceBookEntity).filter(PriceBookEntity.id == id).first()
    if not pb:
        raise HTTPException(status_code=404, detail="Không tìm thấy bảng giá để nhân bản.")

    # 1. Tính toán version mới
    new_version = (pb.version or 1) + 1

    # 2. Sinh mã mới duy nhất
    base_code = pb.code
    if "-v" in base_code.lower():
        base_code = base_code.rsplit("-", 1)[0]
    new_code = f"{base_code}-v{new_version}"

    # Đảm bảo mã không trùng
    suffix = 1
    final_code = new_code
    while db.query(PriceBookEntity).filter(PriceBookEntity.code == final_code).first():
        final_code = f"{new_code}-{suffix}"
        suffix += 1

    # 3. Tạo entity mới
    new_pb = PriceBookEntity(
        code=final_code,
        name=f"{pb.name} (v{new_version})",
        customer_group=pb.customer_group,
        valid_from=pb.valid_from,
        valid_to=pb.valid_to,
        status="ACTIVE",
        version=new_version,
        parent_id=pb.id,
        is_locked=False,
        note=pb.note,
        created_by=current_user.username
    )
    db.add(new_pb)
    db.flush()

    # 4. Sao chép toàn bộ sản phẩm
    old_items = db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == pb.id).all()
    for item in old_items:
        sale_val = item.sale_price if item.sale_price is not None else item.price
        floor_val = item.floor_price if item.floor_price is not None else item.min_price
        new_item = PriceBookItemEntity(
            price_book_id=new_pb.id,
            product_id=item.product_id,
            sale_price=sale_val,
            floor_price=floor_val,
            price=sale_val,
            min_price=floor_val
        )
        db.add(new_item)

    db.commit()
    db.refresh(new_pb)

    return _build_price_book_response(new_pb, db)


@router.post("/validate-order-items", response_model=OrderPriceValidationResponse)
def validate_order_items(
    data: OrderPriceValidationRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(PRICE_RESOLVER_ROLES))
):
    """
    Kiểm tra áp giá đơn hàng:
    - Tìm bảng giá hiệu lực theo nhóm khách hàng của đại lý.
    - Nếu đơn giá bán thực tế < floor_price: trả về requires_approval = True để đơn
      chuyển sang trạng thái chờ Quản lý kinh doanh duyệt.
    """
    dealer = db.query(DealerEntity).filter(DealerEntity.id == data.dealer_id).first()
    if not dealer:
        from app.models.dealer import DEALERS_DB
        dealer = DEALERS_DB.get(data.dealer_id)
        if not dealer:
            raise HTTPException(status_code=404, detail="Không tìm thấy đại lý.")

    customer_group = getattr(dealer, "customer_group", None) or "dai_ly_cap_1"

    aliases = [customer_group]
    if customer_group in ["CAP_1", "Dai_ly_cap_1", "Đại lý cấp 1", "dai_ly_cap_1"]:
        aliases = ["Dai_ly_cap_1", "CAP_1", "Đại lý cấp 1", "dai_ly_cap_1"]
    elif customer_group in ["CAP_2", "Dai_ly_cap_2", "Đại lý cấp 2", "dai_ly_cap_2"]:
        aliases = ["Dai_ly_cap_2", "CAP_2", "Đại lý cấp 2", "dai_ly_cap_2"]
    elif customer_group in ["RETAIL", "Khach_le", "Khách lẻ", "khach_le"]:
        aliases = ["Khach_le", "RETAIL", "Khách lẻ", "khach_le"]

    now = get_utc_now()
    pb = db.query(PriceBookEntity).filter(
        PriceBookEntity.customer_group.in_(aliases),
        PriceBookEntity.status == "ACTIVE",
        PriceBookEntity.valid_from <= now,
        PriceBookEntity.valid_to >= now
    ).order_by(PriceBookEntity.version.desc(), PriceBookEntity.created_at.desc()).first()

    if not pb:
        raise HTTPException(
            status_code=400,
            detail=f"Không tìm thấy bảng giá đang hiệu lực cho nhóm khách hàng '{customer_group}'."
        )

    items_eval = []
    requires_approval = False
    reasons = []

    db_items = db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == pb.id).all()
    pb_item_map = {item.product_id: item for item in db_items}

    for order_item in data.items:
        prod = db.query(ProductEntity).filter(ProductEntity.id == order_item.product_id).first()
        prod_code = prod.code if prod else None

        pb_item = pb_item_map.get(order_item.product_id)
        if not pb_item:
            items_eval.append(OrderItemEvaluation(
                product_id=order_item.product_id,
                product_code=prod_code,
                actual_price=order_item.price,
                book_price=0.0,
                min_price=0.0,
                sale_price=None,
                floor_price=None,
                is_below_min=True
            ))
            requires_approval = True
            reasons.append(f"Sản phẩm {prod_code} không có trong bảng giá.")
        else:
            floor_val = pb_item.floor_price if pb_item.floor_price is not None else pb_item.min_price
            sale_val = pb_item.sale_price if pb_item.sale_price is not None else pb_item.price

            is_below_floor = floor_val is not None and order_item.price < floor_val
            if is_below_floor:
                requires_approval = True
                reasons.append(
                    f"Sản phẩm {prod_code} có giá bán {order_item.price:,.0f} thấp hơn giá sàn ({floor_val:,.0f})."
                )

            items_eval.append(OrderItemEvaluation(
                product_id=order_item.product_id,
                product_code=prod_code,
                actual_price=order_item.price,
                book_price=sale_val or 0.0,
                min_price=floor_val or 0.0,
                sale_price=sale_val,
                floor_price=floor_val,
                is_below_min=is_below_floor
            ))

    approval_status = "PENDING_APPROVAL" if requires_approval else "NORMAL"
    approval_reason = " ".join(reasons) if requires_approval else None

    return OrderPriceValidationResponse(
        price_book_id=pb.id,
        price_book_code=pb.code,
        requires_approval=requires_approval,
        approval_status=approval_status,
        approval_reason=approval_reason,
        items_evaluation=items_eval
    )
