import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.api.deps import require_permission, get_current_user, require_roles
from app.core.database import get_db
from app.core.rbac import Permission, Role
from app.models.entities import CategoryEntity, ProductEntity, OrderEntity, OrderItemEntity
from app.schemas.auth import UserResponse
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse, CategoryTreeResponse

router = APIRouter()

def build_category_tree(categories: List[CategoryEntity], parent_id: Optional[int] = None) -> List[CategoryTreeResponse]:
    tree = []
    for cat in categories:
        if cat.parent_id == parent_id:
            node = CategoryTreeResponse(
                id=cat.id,
                name=cat.name,
                parent_id=cat.parent_id,
                description=cat.description,
                sub_categories=build_category_tree(categories, cat.id)
            )
            tree.append(node)
    return tree

@router.get("/tree", response_model=List[CategoryTreeResponse])
def get_categories_tree(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user)
):
    categories = db.query(CategoryEntity).all()
    return build_category_tree(categories)

@router.get("", response_model=List[CategoryResponse])
def get_categories(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user)
):
    return db.query(CategoryEntity).all()

@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    if data.parent_id:
        parent = db.query(CategoryEntity).filter(CategoryEntity.id == data.parent_id).first()
        if not parent:
            raise HTTPException(status_code=400, detail="Danh mục cha không tồn tại.")
            
    existing = db.query(CategoryEntity).filter(CategoryEntity.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Tên danh mục đã tồn tại.")

    cat = CategoryEntity(**data.dict())
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat

@router.put("/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    data: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    cat = db.query(CategoryEntity).filter(CategoryEntity.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Không tìm thấy danh mục.")
        
    if data.parent_id and data.parent_id == category_id:
        raise HTTPException(status_code=400, detail="Không thể chọn chính nó làm danh mục cha.")

    if data.parent_id:
        parent = db.query(CategoryEntity).filter(CategoryEntity.id == data.parent_id).first()
        if not parent:
            raise HTTPException(status_code=400, detail="Danh mục cha không tồn tại.")
            
    existing = db.query(CategoryEntity).filter(CategoryEntity.name == data.name, CategoryEntity.id != category_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Tên danh mục đã tồn tại.")

    for key, value in data.dict().items():
        setattr(cat, key, value)
        
    db.commit()
    db.refresh(cat)
    return cat

@router.delete("/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    """
    Ràng buộc an toàn khi xóa danh mục: Nếu danh mục đó vẫn chứa sản phẩm HOẶC vẫn còn các nhóm con, tuyệt đối KHÔNG CHO PHÉP XÓA.
    """
    cat = db.query(CategoryEntity).filter(CategoryEntity.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Không tìm thấy danh mục.")
        
    # Check sub-categories
    sub_cats = db.query(CategoryEntity).filter(CategoryEntity.parent_id == category_id).count()
    if sub_cats > 0:
        raise HTTPException(status_code=400, detail="Không thể xóa danh mục vì vẫn còn các nhóm con.")
        
    # Check products
    products_count = db.query(ProductEntity).filter(ProductEntity.category_id == category_id).count()
    if products_count > 0:
        raise HTTPException(status_code=400, detail="Không thể xóa danh mục vì vẫn còn sản phẩm thuộc nhóm này.")
        
    # Also check RAW_PRODUCTS mock data for consistency since products.py uses it
    from app.api.v1.endpoints.products import RAW_PRODUCTS
    for p in RAW_PRODUCTS:
        if p.get("category_id") == category_id:
            raise HTTPException(status_code=400, detail="Không thể xóa danh mục vì vẫn còn sản phẩm thuộc nhóm này trong Mock Data.")
            
    db.delete(cat)
    db.commit()
    return {"status": "success", "message": "Xóa danh mục thành công"}

@router.get("/sales-report")
def get_category_sales_report(
    period: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles([Role.SYSTEM_ADMIN.value, Role.SALES_MANAGER.value]))
):
    """
    Báo cáo doanh số theo ngành hàng kết nối trực tiếp từ CSDL.
    Hỗ trợ lọc theo thời gian: 'today', 'month', 'quarter', 'all'.
    Tổng hợp doanh số lũy kế từ các nhóm con lên nhóm cha (hierarchical rollup).
    Dữ liệu được truy vấn thực tế từ OrderEntity và OrderItemEntity trong CSDL, loại trừ đơn hủy/từ chối/chờ duyệt.
    """
    categories = db.query(CategoryEntity).all()
    cat_by_id = {cat.id: cat for cat in categories}
    cat_by_name = {cat.name.strip().lower(): cat.id for cat in categories}

    # 1. Xây dựng bản đồ ánh xạ sản phẩm -> ngành hàng (prod_to_cat) từ CSDL
    prod_to_cat: dict[int, Optional[int]] = {}
    db_products = db.query(ProductEntity).all()
    for pe in db_products:
        if pe.category_id and pe.category_id in cat_by_id:
            prod_to_cat[pe.id] = pe.category_id
        elif pe.category and pe.category.strip().lower() in cat_by_name:
            prod_to_cat[pe.id] = cat_by_name[pe.category.strip().lower()]
        else:
            prod_to_cat[pe.id] = None

    from app.api.v1.endpoints.products import RAW_PRODUCTS
    for p in RAW_PRODUCTS:
        pid = p.get("id")
        if pid not in prod_to_cat or prod_to_cat[pid] is None:
            if p.get("category_id") and p.get("category_id") in cat_by_id:
                prod_to_cat[pid] = p["category_id"]
            elif p.get("category") and p.get("category").strip().lower() in cat_by_name:
                prod_to_cat[pid] = cat_by_name[p.get("category").strip().lower()]

    # 2. Truy vấn đơn hàng từ CSDL
    INVALID_STATUSES = {"CANCELLED", "REJECTED", "EXPIRED", "DRAFT", "PENDING_APPROVAL"}
    seen_codes = set()
    orders_to_process = []

    db_orders = db.query(OrderEntity).all()
    all_order_ids = [o.id for o in db_orders]
    
    # Lấy các chi tiết đơn hàng từ bảng order_items
    db_items = db.query(OrderItemEntity).filter(OrderItemEntity.order_id.in_(all_order_ids)).all() if all_order_ids else []
    items_by_order: dict[int, list[dict]] = {}
    for oi in db_items:
        items_by_order.setdefault(oi.order_id, []).append({
            "product_id": oi.product_id,
            "quantity": oi.base_quantity if (oi.base_quantity and oi.base_quantity > 0) else (oi.quantity or 1),
            "price": oi.unit_price or 0.0
        })

    for o in db_orders:
        st = (o.status or "").upper()
        if st in INVALID_STATUSES:
            continue
        code = (o.order_code or f"DB_{o.id}").strip().upper()
        if code not in seen_codes:
            seen_codes.add(code)
            items = items_by_order.get(o.id, [])
            if not items and o.items_json:
                try:
                    parsed = json.loads(o.items_json)
                    if isinstance(parsed, dict) and "items" in parsed:
                        items = parsed["items"]
                    elif isinstance(parsed, list):
                        items = parsed
                except Exception:
                    items = []

            # Tính tỷ lệ chiết khấu nếu có
            discount_pct = float(o.discount_rate or 0.0)
            if discount_pct <= 0 and o.discount_amount and o.total_amount:
                sub = float(o.total_amount) + float(o.discount_amount)
                if sub > 0:
                    discount_pct = (float(o.discount_amount) / sub) * 100.0

            orders_to_process.append({
                "id": o.id,
                "order_code": o.order_code,
                "created_at": o.created_at,
                "discount_rate": discount_pct,
                "items": items
            })

    # Hỗ trợ cache bộ nhớ ORDERS_DB nếu có đơn chưa sync vào DB (bảo đảm tương thích ngược)
    try:
        from app.api.v1.endpoints.orders import ORDERS_DB
        for oid, o in ORDERS_DB.items():
            st = (o.get("status") or "").upper()
            if st in INVALID_STATUSES:
                continue
            code = (o.get("order_code") or f"MEM_{oid}").strip().upper()
            if code not in seen_codes:
                seen_codes.add(code)
                created_at_val = o.get("created_at")
                if isinstance(created_at_val, str):
                    try:
                        created_at_val = datetime.fromisoformat(created_at_val.replace("Z", "+00:00"))
                    except Exception:
                        created_at_val = None
                orders_to_process.append({
                    "id": o.get("id", oid),
                    "order_code": o.get("order_code"),
                    "created_at": created_at_val,
                    "discount_rate": float(o.get("discount_percent") or o.get("discount_rate") or 0.0),
                    "items": o.get("items", [])
                })
    except Exception:
        pass

    # 3. Lọc theo khoảng thời gian (period) với chuẩn múi giờ GMT+7
    vn_tz = timezone(timedelta(hours=7))
    now_vn = datetime.now(vn_tz)

    def match_period(dt_val: Optional[Union[datetime, str]]) -> bool:
        if not period or period == "all" or not dt_val:
            return True
        if isinstance(dt_val, str):
            try:
                dt_val = datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
            except Exception:
                return True
        if not isinstance(dt_val, datetime):
            return True

        if dt_val.tzinfo:
            local_dt = dt_val.astimezone(vn_tz)
        else:
            local_dt = dt_val

        if period == "today":
            return local_dt.date() == now_vn.date()
        elif period == "month":
            return (local_dt.year, local_dt.month) == (now_vn.year, now_vn.month)
        elif period == "quarter":
            current_q = (now_vn.month - 1) // 3 + 1
            ord_q = (local_dt.month - 1) // 3 + 1
            return local_dt.year == now_vn.year and ord_q == current_q
        return True

    filtered_orders = [ord_obj for ord_obj in orders_to_process if match_period(ord_obj.get("created_at"))]

    # 4. Tính toán doanh số trực tiếp cho từng ngành hàng
    direct_sales = {cat.id: 0.0 for cat in categories}
    direct_sales[None] = 0.0

    for ord_obj in filtered_orders:
        discount_rate = float(ord_obj.get("discount_rate", 0.0) or 0.0)
        multiplier = max(0.0, 1.0 - (discount_rate / 100.0))

        for item in ord_obj.get("items", []):
            pid = item.get("product_id")
            qty = float(item.get("quantity", 0) or 0)
            price = float(item.get("price", item.get("unit_price", 0.0)) or 0.0)
            amount = qty * price * multiplier

            cid = prod_to_cat.get(pid)
            if cid in direct_sales:
                direct_sales[cid] += amount
            else:
                direct_sales[None] += amount

    # 5. Phân tầng lũy kế từ nhóm con lên nhóm cha (Hierarchical aggregation)
    def get_descendants_sales(cat_id: int) -> float:
        total = direct_sales.get(cat_id, 0.0)
        for cat in categories:
            if cat.parent_id == cat_id:
                total += get_descendants_sales(cat.id)
        return total

    report = []
    for cat in categories:
        report.append({
            "id": cat.id,
            "name": cat.name,
            "parent_id": cat.parent_id,
            "direct_sales": round(direct_sales.get(cat.id, 0.0), 2),
            "total_sales": round(get_descendants_sales(cat.id), 2)
        })

    return report
