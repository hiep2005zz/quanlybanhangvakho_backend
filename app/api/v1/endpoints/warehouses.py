# backend/app/api/v1/endpoints/warehouses.py
from __future__ import annotations
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.api.deps import require_roles, require_permission
from app.core.rbac import Permission
from app.schemas.auth import UserResponse
from app.models.goods_receipt import WarehouseEntity
from app.models.warehouse import WarehouseLocationEntity, LocationStockEntity, WarehouseZoneEntity, WarehouseRackEntity
from app.models.entities import ProductEntity, DealerEntity, OrderEntity
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseUpdate,
    WarehouseResponse,
    WarehouseLocationCreate,
    WarehouseLocationUpdate,
    WarehouseLocationResponse,
    AssignProductToLocationRequest,
    TransferLocationProductRequest,
    LocationProductStockItem,
    OrderPickingItemResponse,
    ProductPickingLocation,
    WarehouseZoneCreate,
    WarehouseZoneUpdate,
    WarehouseZoneResponse,
    WarehouseRackCreate,
    WarehouseRackUpdate,
    WarehouseRackResponse,
    WarehouseMasterDataResponse,
    MasterDataOptionItem,
)

router = APIRouter()

# Quyền quản lý cấu hình kho: Admin, Quản lý kho, Thủ kho
WAREHOUSE_MANAGE_ROLES = ["admin", "warehouse", "warehouse_manager"]
# Quyền xem kho & tra cứu soạn đơn: Toàn bộ nhân sự nghiệp vụ
WAREHOUSE_VIEW_ROLES = ["admin", "warehouse", "warehouse_manager", "sales", "sales_manager", "accountant"]


# ==========================================================
# 1. QUẢN LÝ KHO HÀNG (WAREHOUSES)
# ==========================================================

@router.get("", response_model=List[WarehouseResponse])
def get_warehouses(
    search: Optional[str] = Query(None, description="Tìm kiếm mã hoặc tên kho"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái"),
    is_active_only: Optional[bool] = Query(None, description="Chỉ lấy kho đang hoạt động"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    """
    Lấy danh sách các kho hàng kèm số liệu thống kê:
    - Số vị trí lưu trữ (locations_count)
    - Số loại mặt hàng lưu trong kho (total_products_count)
    - Tổng số lượng tồn hàng tại các vị trí (total_stock_quantity)
    """
    query = db.query(WarehouseEntity)

    if is_active_only is True:
        query = query.filter(WarehouseEntity.is_active == True)

    if status_filter and status_filter.strip() and status_filter.lower() != "all" and status_filter.lower() != "tất cả":
        query = query.filter(WarehouseEntity.status == status_filter.strip())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(WarehouseEntity.code).like(term) |
            func.lower(WarehouseEntity.name).like(term) |
            func.lower(WarehouseEntity.address).like(term) |
            func.lower(WarehouseEntity.manager_name).like(term)
        )

    warehouses = query.order_by(WarehouseEntity.id.asc()).all()

    # Tính toán thống kê vị trí và hàng hóa cho từng kho
    results: List[WarehouseResponse] = []
    for wh in warehouses:
        loc_count = db.query(WarehouseLocationEntity).filter(
            WarehouseLocationEntity.warehouse_id == wh.id
        ).count()

        prod_count = db.query(LocationStockEntity.product_id).filter(
            LocationStockEntity.warehouse_id == wh.id,
            LocationStockEntity.quantity > 0
        ).distinct().count()

        total_qty = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
            LocationStockEntity.warehouse_id == wh.id
        ).scalar() or 0

        item = WarehouseResponse(
            id=wh.id,
            code=wh.code,
            name=wh.name,
            address=wh.address,
            manager_name=wh.manager_name,
            phone=wh.phone,
            status=wh.status or ("Đang hoạt động" if wh.is_active else "Ngừng hoạt động"),
            is_active=bool(wh.is_active),
            created_at=wh.created_at,
            locations_count=loc_count,
            total_products_count=prod_count,
            total_stock_quantity=int(total_qty),
        )
        results.append(item)

    return results


@router.post("", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """
    Tạo mới một kho hàng trong hệ thống.
    Kiểm tra mã kho không được trùng lặp.
    """
    clean_code = payload.code.strip().upper()
    existing = db.query(WarehouseEntity).filter(
        func.lower(WarehouseEntity.code) == clean_code.lower()
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã kho hàng '{clean_code}' đã tồn tại trong hệ thống. Vui lòng chọn mã khác."
        )

    clean_status = payload.status or "Đang hoạt động"
    is_act = payload.is_active if payload.is_active is not None else (clean_status == "Đang hoạt động")

    new_wh = WarehouseEntity(
        code=clean_code,
        name=payload.name.strip(),
        address=payload.address.strip() if payload.address else None,
        manager_name=payload.manager_name.strip() if payload.manager_name else None,
        phone=payload.phone.strip() if payload.phone else None,
        status=clean_status,
        is_active=is_act,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_wh)
    db.commit()
    db.refresh(new_wh)

    return WarehouseResponse(
        id=new_wh.id,
        code=new_wh.code,
        name=new_wh.name,
        address=new_wh.address,
        manager_name=new_wh.manager_name,
        phone=new_wh.phone,
        status=new_wh.status,
        is_active=bool(new_wh.is_active),
        created_at=new_wh.created_at,
        locations_count=0,
        total_products_count=0,
        total_stock_quantity=0,
    )


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
def get_warehouse_detail(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    """Lấy thông tin chi tiết một kho hàng."""
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng với ID {warehouse_id}."
        )

    loc_count = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == wh.id
    ).count()

    prod_count = db.query(LocationStockEntity.product_id).filter(
        LocationStockEntity.warehouse_id == wh.id,
        LocationStockEntity.quantity > 0
    ).distinct().count()

    total_qty = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
        LocationStockEntity.warehouse_id == wh.id
    ).scalar() or 0

    return WarehouseResponse(
        id=wh.id,
        code=wh.code,
        name=wh.name,
        address=wh.address,
        manager_name=wh.manager_name,
        phone=wh.phone,
        status=wh.status or ("Đang hoạt động" if wh.is_active else "Ngừng hoạt động"),
        is_active=bool(wh.is_active),
        created_at=wh.created_at,
        locations_count=loc_count,
        total_products_count=prod_count,
        total_stock_quantity=int(total_qty),
    )


@router.put("/{warehouse_id}", response_model=WarehouseResponse)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """Cập nhật thông tin kho hàng."""
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng với ID {warehouse_id}."
        )

    if payload.name is not None:
        wh.name = payload.name.strip()
    if payload.address is not None:
        wh.address = payload.address.strip()
    if payload.manager_name is not None:
        wh.manager_name = payload.manager_name.strip()
    if payload.phone is not None:
        wh.phone = payload.phone.strip()
    if payload.status is not None:
        wh.status = payload.status.strip()
        wh.is_active = (wh.status == "Đang hoạt động")
    if payload.is_active is not None:
        wh.is_active = payload.is_active
        wh.status = "Đang hoạt động" if wh.is_active else "Ngừng hoạt động"

    db.commit()
    db.refresh(wh)

    loc_count = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == wh.id
    ).count()

    prod_count = db.query(LocationStockEntity.product_id).filter(
        LocationStockEntity.warehouse_id == wh.id,
        LocationStockEntity.quantity > 0
    ).distinct().count()

    total_qty = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
        LocationStockEntity.warehouse_id == wh.id
    ).scalar() or 0

    return WarehouseResponse(
        id=wh.id,
        code=wh.code,
        name=wh.name,
        address=wh.address,
        manager_name=wh.manager_name,
        phone=wh.phone,
        status=wh.status,
        is_active=bool(wh.is_active),
        created_at=wh.created_at,
        locations_count=loc_count,
        total_products_count=prod_count,
        total_stock_quantity=int(total_qty),
    )


@router.delete("/{warehouse_id}")
def delete_warehouse(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin"])),
):
    """
    Xóa kho hàng hoặc chuyển sang ngừng hoạt động.
    Bảo vệ dữ liệu nghiêm ngặt:
    - Nếu kho còn hàng tồn tại các vị trí -> CHẶN XÓA (400 Bad Request)
    - Nếu kho có đại lý đang gán phục vụ -> CHẶN XÓA (400 Bad Request)
    - Ưu tiên chuyển sang trạng thái 'Ngừng hoạt động'.
    """
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng với ID {warehouse_id}."
        )

    # 1. Kiểm tra tồn hàng tại các vị trí
    stock_sum = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
        LocationStockEntity.warehouse_id == warehouse_id
    ).scalar() or 0

    if stock_sum > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể xóa kho '{wh.name}' vì đang còn {stock_sum} sản phẩm lưu trữ tại các vị trí kệ. Hãy chuyển hết hàng hoặc chuyển kho sang trạng thái 'Ngừng hoạt động'."
        )

    # 2. Kiểm tra đại lý đang gán kho này
    assigned_dealers = db.query(DealerEntity).filter(
        (DealerEntity.warehouse_id == str(warehouse_id)) | (DealerEntity.warehouse_id == wh.code)
    ).count()
    if assigned_dealers > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kho '{wh.name}' đang được gán làm kho phục vụ mặc định cho {assigned_dealers} đại lý. Vui lòng thay đổi kho phục vụ của các đại lý này trước khi xóa."
        )

    # Xóa các vị trí trống của kho nếu có
    db.query(LocationStockEntity).filter(LocationStockEntity.warehouse_id == warehouse_id).delete()
    db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.warehouse_id == warehouse_id).delete()
    db.delete(wh)
    db.commit()

    return {"message": f"Đã xóa thành công kho '{wh.name}' (Mã: {wh.code})."}


# ==========================================================
# 2. QUẢN LÝ VỊ TRÍ LƯU TRỮ TRONG KHO (LOCATIONS)
# ==========================================================

@router.get("/{warehouse_id}/locations", response_model=List[WarehouseLocationResponse])
def get_warehouse_locations(
    warehouse_id: int,
    zone: Optional[str] = Query(None, description="Lọc theo khu vực (Khu A, Khu B...)"),
    search: Optional[str] = Query(None, description="Tìm mã hoặc tên vị trí"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái vị trí"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    """
    Lấy danh sách các vị trí kệ / ô chứa trong một kho cụ thể.
    """
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng với ID {warehouse_id}."
        )

    query = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == warehouse_id
    )

    if zone and zone.strip() and zone.lower() != "all" and zone.lower() != "tất cả":
        query = query.filter(WarehouseLocationEntity.zone == zone.strip())

    if status_filter and status_filter.strip() and status_filter.lower() != "all":
        query = query.filter(WarehouseLocationEntity.status == status_filter.strip())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(WarehouseLocationEntity.location_code).like(term) |
            func.lower(WarehouseLocationEntity.location_name).like(term) |
            func.lower(WarehouseLocationEntity.zone).like(term) |
            func.lower(WarehouseLocationEntity.aisle).like(term) |
            func.lower(WarehouseLocationEntity.rack).like(term)
        )

    locations = query.order_by(WarehouseLocationEntity.location_code.asc()).all()

    results: List[WarehouseLocationResponse] = []
    for loc in locations:
        items_cnt = db.query(LocationStockEntity.product_id).filter(
            LocationStockEntity.location_id == loc.id,
            LocationStockEntity.quantity > 0
        ).count()

        total_qty = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
            LocationStockEntity.location_id == loc.id
        ).scalar() or 0

        results.append(WarehouseLocationResponse(
            id=loc.id,
            warehouse_id=wh.id,
            warehouse_code=wh.code,
            warehouse_name=wh.name,
            location_code=loc.location_code,
            location_name=loc.location_name,
            zone=loc.zone,
            aisle=loc.aisle,
            rack=loc.rack,
            bin=loc.bin,
            max_capacity=loc.max_capacity,
            is_active=bool(loc.is_active),
            status=loc.status or "Đang sử dụng",
            note=loc.note,
            created_at=loc.created_at,
            items_count=items_cnt,
            total_quantity=int(total_qty),
        ))

    return results


@router.post("/{warehouse_id}/locations", response_model=WarehouseLocationResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse_location(
    warehouse_id: int,
    payload: WarehouseLocationCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """
    Thêm mới một vị trí lưu trữ (khu vực, dãy, kệ, ô chứa) vào kho.
    Mã vị trí phải là duy nhất trong cùng một kho.
    """
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng với ID {warehouse_id}."
        )

    if not wh.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kho '{wh.name}' hiện đang ngừng hoạt động. Không thể khai báo thêm vị trí lưu trữ."
        )

    clean_code = payload.location_code.strip().upper()
    existing = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == warehouse_id,
        func.lower(WarehouseLocationEntity.location_code) == clean_code.lower()
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã vị trí '{clean_code}' đã tồn tại trong kho '{wh.name}'. Vui lòng đặt mã khác."
        )

    # Tự động tạo tên vị trí nếu chưa nhập
    loc_name = payload.location_name
    if not loc_name or not loc_name.strip():
        parts = [p for p in [payload.zone, payload.aisle, payload.rack, payload.bin] if p]
        loc_name = " - ".join(parts) if parts else clean_code

    new_loc = WarehouseLocationEntity(
        warehouse_id=warehouse_id,
        location_code=clean_code,
        location_name=loc_name.strip() if loc_name else None,
        zone=payload.zone.strip() if payload.zone else None,
        aisle=payload.aisle.strip() if payload.aisle else None,
        rack=payload.rack.strip() if payload.rack else None,
        bin=payload.bin.strip() if payload.bin else None,
        max_capacity=payload.max_capacity if payload.max_capacity is not None else 1000.0,
        status=payload.status or "Đang sử dụng",
        is_active=payload.is_active if payload.is_active is not None else True,
        note=payload.note.strip() if payload.note else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_loc)
    db.commit()
    db.refresh(new_loc)

    return WarehouseLocationResponse(
        id=new_loc.id,
        warehouse_id=wh.id,
        warehouse_code=wh.code,
        warehouse_name=wh.name,
        location_code=new_loc.location_code,
        location_name=new_loc.location_name,
        zone=new_loc.zone,
        aisle=new_loc.aisle,
        rack=new_loc.rack,
        bin=new_loc.bin,
        max_capacity=new_loc.max_capacity,
        is_active=bool(new_loc.is_active),
        status=new_loc.status,
        note=new_loc.note,
        created_at=new_loc.created_at,
        items_count=0,
        total_quantity=0,
    )


@router.put("/locations/{location_id}", response_model=WarehouseLocationResponse)
def update_warehouse_location(
    location_id: int,
    payload: WarehouseLocationUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """Cập nhật thông tin vị trí lưu trữ."""
    loc = db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.id == location_id).first()
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vị trí ID {location_id}."
        )

    if payload.location_code is not None:
        new_code = payload.location_code.strip().upper()
        if new_code.lower() != loc.location_code.lower():
            dup = db.query(WarehouseLocationEntity).filter(
                WarehouseLocationEntity.warehouse_id == loc.warehouse_id,
                func.lower(WarehouseLocationEntity.location_code) == new_code.lower(),
                WarehouseLocationEntity.id != loc.id
            ).first()
            if dup:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Mã vị trí '{new_code}' đã tồn tại trong kho này."
                )
            loc.location_code = new_code

    if payload.location_name is not None:
        loc.location_name = payload.location_name.strip()
    if payload.zone is not None:
        loc.zone = payload.zone.strip()
    if payload.aisle is not None:
        loc.aisle = payload.aisle.strip()
    if payload.rack is not None:
        loc.rack = payload.rack.strip()
    if payload.bin is not None:
        loc.bin = payload.bin.strip()
    if payload.max_capacity is not None:
        loc.max_capacity = payload.max_capacity
    if payload.status is not None:
        loc.status = payload.status.strip()
    if payload.is_active is not None:
        loc.is_active = payload.is_active
    if payload.note is not None:
        loc.note = payload.note.strip()

    loc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(loc)

    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == loc.warehouse_id).first()
    items_cnt = db.query(LocationStockEntity.product_id).filter(
        LocationStockEntity.location_id == loc.id,
        LocationStockEntity.quantity > 0
    ).count()

    total_qty = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
        LocationStockEntity.location_id == loc.id
    ).scalar() or 0

    return WarehouseLocationResponse(
        id=loc.id,
        warehouse_id=loc.warehouse_id,
        warehouse_code=wh.code if wh else None,
        warehouse_name=wh.name if wh else None,
        location_code=loc.location_code,
        location_name=loc.location_name,
        zone=loc.zone,
        aisle=loc.aisle,
        rack=loc.rack,
        bin=loc.bin,
        max_capacity=loc.max_capacity,
        is_active=bool(loc.is_active),
        status=loc.status,
        note=loc.note,
        created_at=loc.created_at,
        items_count=items_cnt,
        total_quantity=int(total_qty),
    )


@router.delete("/locations/{location_id}")
def delete_warehouse_location(
    location_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """
    Xóa vị trí lưu trữ.
    Nếu vị trí đang chứa sản phẩm có số lượng > 0 -> Ngăn xóa (400 Bad Request).
    """
    loc = db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.id == location_id).first()
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy vị trí ID {location_id}."
        )

    current_qty = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
        LocationStockEntity.location_id == location_id
    ).scalar() or 0

    if current_qty > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể xóa vị trí '{loc.location_code}' vì đang chứa {current_qty} sản phẩm. Hãy dọn hàng ra khỏi vị trí trước khi xóa."
        )

    db.query(LocationStockEntity).filter(LocationStockEntity.location_id == location_id).delete()
    db.delete(loc)
    db.commit()

    return {"message": f"Đã xóa thành công vị trí '{loc.location_code}'."}


# ==========================================================
# 3. GÁN SẢN PHẨM VÀO VỊ TRÍ (PRODUCT LOCATION STOCKS)
# ==========================================================

@router.get("/{warehouse_id}/products", response_model=List[LocationProductStockItem])
def get_warehouse_location_products(
    warehouse_id: int,
    product_id: Optional[int] = Query(None, description="Lọc theo mã ID sản phẩm"),
    location_id: Optional[int] = Query(None, description="Lọc theo vị trí"),
    search: Optional[str] = Query(None, description="Tìm theo tên/mã sản phẩm hoặc vị trí"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    """
    Lấy danh sách các sản phẩm và số lượng tương ứng tại từng vị trí trong kho.
    Hỗ trợ 1 sản phẩm nằm ở nhiều vị trí để nhân viên dễ dàng tìm hàng khi soạn đơn.
    """
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kho không tồn tại.")

    query = db.query(LocationStockEntity).filter(
        LocationStockEntity.warehouse_id == warehouse_id,
        LocationStockEntity.quantity > 0
    )

    if product_id:
        query = query.filter(LocationStockEntity.product_id == product_id)
    if location_id:
        query = query.filter(LocationStockEntity.location_id == location_id)

    stocks = query.all()

    # Thu thập ID sản phẩm để lấy thông tin tổng tồn
    pids = list({s.product_id for s in stocks})
    products_map = {p.id: p for p in db.query(ProductEntity).filter(ProductEntity.id.in_(pids)).all()} if pids else {}
    locs_map = {l.id: l for l in db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.warehouse_id == warehouse_id).all()}

    results: List[LocationProductStockItem] = []
    for s in stocks:
        prod = products_map.get(s.product_id)
        loc = locs_map.get(s.location_id)
        if not prod or not loc:
            continue

        # Tìm tổng tồn của sản phẩm tại kho này
        wh_total = db.query(func.coalesce(func.sum(LocationStockEntity.quantity), 0)).filter(
            LocationStockEntity.warehouse_id == warehouse_id,
            LocationStockEntity.product_id == s.product_id
        ).scalar() or 0

        # Nếu có từ khóa tìm kiếm
        if search and search.strip():
            term = search.strip().lower()
            text_match = (
                term in prod.code.lower() or
                term in prod.name.lower() or
                term in loc.location_code.lower() or
                (loc.location_name and term in loc.location_name.lower()) or
                (loc.zone and term in loc.zone.lower())
            )
            if not text_match:
                continue

        results.append(LocationProductStockItem(
            id=s.id,
            location_id=loc.id,
            location_code=loc.location_code,
            location_name=loc.location_name,
            zone=loc.zone,
            aisle=loc.aisle,
            rack=loc.rack,
            bin=loc.bin,
            product_id=prod.id,
            product_code=prod.code,
            product_name=prod.name,
            base_unit=prod.base_unit or "Cái",
            quantity=s.quantity,
            warehouse_total_stock=int(wh_total),
        ))

    # Sắp xếp theo tên sản phẩm rồi vị trí kệ
    results.sort(key=lambda x: (x.product_name, x.location_code))
    return results


@router.post("/locations/assign-product", response_model=Dict[str, Any])
def assign_product_to_location(
    payload: AssignProductToLocationRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """
    Gán hoặc điều chỉnh số lượng sản phẩm vào một vị trí lưu kho.
    Nếu quantity == 0: Xóa sản phẩm khỏi vị trí đó.
    """
    loc = db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.id == payload.location_id).first()
    if not loc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vị trí lưu kho không tồn tại.")

    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == loc.warehouse_id).first()
    if not wh or not wh.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kho hàng đang ngừng hoạt động.")

    prod = db.query(ProductEntity).filter(ProductEntity.id == payload.product_id).first()
    if not prod:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sản phẩm không tồn tại.")

    stock_record = db.query(LocationStockEntity).filter(
        LocationStockEntity.location_id == payload.location_id,
        LocationStockEntity.product_id == payload.product_id
    ).first()

    if payload.quantity <= 0:
        if stock_record:
            db.delete(stock_record)
            db.commit()
        return {
            "message": f"Đã giải phóng sản phẩm '{prod.name}' khỏi vị trí '{loc.location_code}'.",
            "quantity": 0,
        }

    if stock_record:
        stock_record.quantity = payload.quantity
        stock_record.updated_at = datetime.now(timezone.utc)
    else:
        stock_record = LocationStockEntity(
            warehouse_id=loc.warehouse_id,
            location_id=loc.id,
            product_id=prod.id,
            quantity=payload.quantity,
            created_at=datetime.now(timezone.utc),
        )
        db.add(stock_record)

    db.commit()

    return {
        "message": f"Đã gán thành công {payload.quantity} {prod.base_unit or 'Cái'} '{prod.name}' vào vị trí '{loc.location_code}'.",
        "location_code": loc.location_code,
        "product_name": prod.name,
        "quantity": payload.quantity,
    }


@router.post("/locations/transfer-product", response_model=Dict[str, Any])
def transfer_product_between_locations(
    payload: TransferLocationProductRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    """
    Chuyển một lượng hàng của sản phẩm từ vị trí này sang vị trí khác (cùng trong kho).
    """
    if payload.from_location_id == payload.to_location_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vị trí nguồn và đích phải khác nhau.")

    source_loc = db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.id == payload.from_location_id).first()
    dest_loc = db.query(WarehouseLocationEntity).filter(WarehouseLocationEntity.id == payload.to_location_id).first()

    if not source_loc or not dest_loc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy vị trí nguồn hoặc đích.")

    if source_loc.warehouse_id != dest_loc.warehouse_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chỉ hỗ trợ chuyển vị trí trong cùng một kho hàng.")

    source_stock = db.query(LocationStockEntity).filter(
        LocationStockEntity.location_id == payload.from_location_id,
        LocationStockEntity.product_id == payload.product_id
    ).first()

    if not source_stock or source_stock.quantity < payload.quantity:
        avail = source_stock.quantity if source_stock else 0
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Số lượng tại vị trí nguồn '{source_loc.location_code}' không đủ để chuyển (Hiện có: {avail}, yêu cầu chuyển: {payload.quantity})."
        )

    # Trừ nguồn
    source_stock.quantity -= payload.quantity
    source_stock.updated_at = datetime.now(timezone.utc)
    if source_stock.quantity == 0:
        db.delete(source_stock)

    # Cộng đích
    dest_stock = db.query(LocationStockEntity).filter(
        LocationStockEntity.location_id == payload.to_location_id,
        LocationStockEntity.product_id == payload.product_id
    ).first()

    if dest_stock:
        dest_stock.quantity += payload.quantity
        dest_stock.updated_at = datetime.now(timezone.utc)
    else:
        dest_stock = LocationStockEntity(
            warehouse_id=source_loc.warehouse_id,
            location_id=dest_loc.id,
            product_id=payload.product_id,
            quantity=payload.quantity,
            created_at=datetime.now(timezone.utc),
        )
        db.add(dest_stock)

    db.commit()

    return {
        "message": f"Đã chuyển thành công {payload.quantity} sản phẩm từ vị trí '{source_loc.location_code}' sang vị trí '{dest_loc.location_code}'.",
        "from_location": source_loc.location_code,
        "to_location": dest_loc.location_code,
        "quantity": payload.quantity,
    }


# ==========================================================
# 4. GÁN KHO PHỤC VỤ MẶC ĐỊNH CHO ĐẠI LÝ
# ==========================================================

@router.put("/dealers/{dealer_id}/default-warehouse", response_model=Dict[str, Any])
def assign_default_warehouse_to_dealer(
    dealer_id: int,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(["admin", "sales_manager", "sales"])),
):
    """
    Thiết lập kho phục vụ mặc định cho đại lý.
    Cập nhật cả bảng DealerEntity và file JSON lưu trữ để đồng bộ toàn diện.
    """
    target_warehouse_id = payload.get("warehouse_id")
    if not target_warehouse_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Thiếu thông tin warehouse_id.")

    # Tìm kho
    wh = None
    if str(target_warehouse_id).isdigit():
        wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == int(target_warehouse_id)).first()
    if not wh:
        wh = db.query(WarehouseEntity).filter(WarehouseEntity.code == str(target_warehouse_id).upper()).first()

    if not wh:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy kho hàng '{target_warehouse_id}'.")

    if not wh.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Kho '{wh.name}' đang ngừng hoạt động. Không thể gán cho đại lý.")

    # Cập nhật trong DB
    dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer:
        # Fallback check DEALERS_DB
        from app.models.dealer import DEALERS_DB, load_dealers_db, save_dealers_db
        load_dealers_db()
        if dealer_id not in DEALERS_DB:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy đại lý ID {dealer_id}.")
        mem_dealer = DEALERS_DB[dealer_id]
        mem_dealer.warehouse_id = str(wh.id)
        mem_dealer.warehouse_name = wh.name
        save_dealers_db()
    else:
        dealer.warehouse_id = str(wh.id)
        dealer.warehouse_name = wh.name
        db.commit()

        # Đồng bộ vào bộ nhớ DEALERS_DB
        from app.models.dealer import DEALERS_DB, load_dealers_db, save_dealers_db
        load_dealers_db()
        if dealer_id in DEALERS_DB:
            DEALERS_DB[dealer_id].warehouse_id = str(wh.id)
            DEALERS_DB[dealer_id].warehouse_name = wh.name
            save_dealers_db()

    return {
        "message": f"Đã gán kho phục vụ mặc định '{wh.name}' (Mã: {wh.code}) cho đại lý thành công.",
        "dealer_id": dealer_id,
        "warehouse_id": wh.id,
        "warehouse_code": wh.code,
        "warehouse_name": wh.name,
    }


# ==========================================================
# 5. TRA CỨU VỊ TRÍ PHỤC VỤ SOẠN ĐƠN (ORDER PICKING LOCATIONS)
# ==========================================================

@router.get("/orders/{order_code}/picking-locations", response_model=List[OrderPickingItemResponse])
def get_order_picking_locations(
    order_code: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    """
    Tích hợp thông tin vị trí vào màn hình soạn đơn:
    Khi nhân viên xem hoặc xử lý đơn hàng, trả về:
    - Kho xuất
    - Danh sách vị trí kệ hoặc khu vực lưu trữ
    - Số lượng khả dụng của từng sản phẩm tại từng vị trí
    """
    clean_code = order_code.strip()

    # Tìm đơn hàng trong DB hoặc in-memory ORDERS_DB
    ord_db = db.query(OrderEntity).filter(OrderEntity.order_code == clean_code).first()
    items_data = []
    dealer_id = None

    if ord_db:
        dealer_id = ord_db.dealer_id
        if ord_db.items_json:
            import json
            try:
                parsed = json.loads(ord_db.items_json)
                items_data = parsed if isinstance(parsed, list) else parsed.get("items", [])
            except Exception:
                pass
    else:
        from app.api.v1.endpoints.orders import ORDERS_DB
        found_ord = next((o for o in ORDERS_DB.values() if o.get("order_code") == clean_code or str(o.get("id")) == clean_code), None)
        if found_ord:
            dealer_id = found_ord.get("dealer_id")
            items_data = found_ord.get("items") or []

    if not items_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng '{order_code}' hoặc đơn hàng không có sản phẩm."
        )

    # Xác định kho xuất dựa trên đại lý của đơn
    from app.services.inventory_availability_service import resolve_dealer_warehouse
    from app.models.dealer import DEALERS_DB, load_dealers_db
    load_dealers_db()
    dealer_obj = DEALERS_DB.get(dealer_id) if dealer_id else None
    w_code, w_name = resolve_dealer_warehouse(dealer_obj)

    # Tìm WarehouseEntity tương ứng
    wh = db.query(WarehouseEntity).filter(
        (WarehouseEntity.code == w_code) | (WarehouseEntity.name == w_name) | (WarehouseEntity.id == 1)
    ).first()
    wh_id = wh.id if wh else 1
    wh_code = wh.code if wh else w_code
    wh_name = wh.name if wh else w_name

    results: List[OrderPickingItemResponse] = []

    for it in items_data:
        pid = it.get("product_id") or it.get("id")
        pname = it.get("product_name") or it.get("name") or "Sản phẩm"
        pcode = it.get("product_code") or f"SP{pid:03d}" if pid else "SP"
        qty = int(it.get("quantity") or 1)
        unit = it.get("unit") or it.get("unit_name") or "Cái"

        # Tra cứu các vị trí lưu sản phẩm này trong kho xuất
        loc_stocks = db.query(LocationStockEntity, WarehouseLocationEntity).join(
            WarehouseLocationEntity,
            LocationStockEntity.location_id == WarehouseLocationEntity.id
        ).filter(
            LocationStockEntity.warehouse_id == wh_id,
            LocationStockEntity.product_id == pid,
            LocationStockEntity.quantity > 0
        ).all()

        picking_locs: List[ProductPickingLocation] = []
        for stock, loc in loc_stocks:
            picking_locs.append(ProductPickingLocation(
                location_id=loc.id,
                location_code=loc.location_code,
                location_name=loc.location_name or loc.location_code,
                zone=loc.zone,
                aisle=loc.aisle,
                rack=loc.rack,
                bin=loc.bin,
                available_quantity=stock.quantity,
            ))

        # Nếu chưa được gán vị trí cụ thể trong kho này, tự động tra cứu vị trí chung hoặc đề xuất kệ trống
        if not picking_locs:
            any_loc = db.query(WarehouseLocationEntity).filter(
                WarehouseLocationEntity.warehouse_id == wh_id,
                WarehouseLocationEntity.is_active == True
            ).first()
            if any_loc:
                picking_locs.append(ProductPickingLocation(
                    location_id=any_loc.id,
                    location_code=any_loc.location_code,
                    location_name=f"{any_loc.location_name} (Đang chờ phân bổ)",
                    zone=any_loc.zone,
                    aisle=any_loc.aisle,
                    rack=any_loc.rack,
                    bin=any_loc.bin,
                    available_quantity=0,
                ))

        results.append(OrderPickingItemResponse(
            product_id=pid,
            product_code=pcode,
            product_name=pname,
            ordered_quantity=qty,
            unit_name=unit,
            warehouse_id=wh_id,
            warehouse_code=wh_code,
            warehouse_name=wh_name,
            locations=picking_locs,
        ))

    return results


# ==========================================================
# 5. MASTER DATA: QUẢN LÝ KHU VỰC (ZONES) & KỆ/DÃY (RACKS)
# ==========================================================

@router.get("/{warehouse_id}/master-data", response_model=WarehouseMasterDataResponse)
def get_warehouse_master_data(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    """
    Lấy toàn bộ dữ liệu cấu hình danh mục (Khu vực, Dãy kệ, Tầng/Kệ, Ô chứa)
    của một kho hàng để hiển thị thành các tùy chọn chọn nhanh (dropdown) khi tạo vị trí.
    """
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy kho hàng với ID {warehouse_id}."
        )

    # 1. Khu vực (Zones) từ bảng warehouse_zones
    db_zones = db.query(WarehouseZoneEntity).filter(
        WarehouseZoneEntity.warehouse_id == warehouse_id,
        WarehouseZoneEntity.is_active == True
    ).order_by(WarehouseZoneEntity.zone_code.asc()).all()

    zone_items: List[MasterDataOptionItem] = []
    seen_zones = set()
    for z in db_zones:
        zone_items.append(MasterDataOptionItem(id=z.id, code=z.zone_code, name=z.zone_name, type="zone"))
        seen_zones.add(z.zone_name.strip().lower())

    # Bổ sung các khu vực đã có trong locations nếu chưa có trong master table
    loc_zones = db.query(WarehouseLocationEntity.zone).filter(
        WarehouseLocationEntity.warehouse_id == warehouse_id,
        WarehouseLocationEntity.zone.isnot(None)
    ).distinct().all()
    for (lz,) in loc_zones:
        if lz and lz.strip() and lz.strip().lower() not in seen_zones:
            zone_items.append(MasterDataOptionItem(code=f"Z-{len(zone_items)+1}", name=lz.strip(), type="zone"))
            seen_zones.add(lz.strip().lower())

    # Mặc định gợi ý nếu kho hoàn toàn mới chưa có dữ liệu
    if not zone_items:
        default_zones = ["Khu A (Thời trang)", "Khu B (Hàng may mặc)", "Khu C (Hàng phụ kiện)", "Khu D (Lưu trữ chung)"]
        for idx, dz in enumerate(default_zones, start=1):
            zone_items.append(MasterDataOptionItem(code=f"KHU-{chr(64+idx)}", name=dz, type="zone"))

    # 2. Dãy kệ & Tầng/Kệ từ warehouse_racks
    db_racks = db.query(WarehouseRackEntity).filter(
        WarehouseRackEntity.warehouse_id == warehouse_id,
        WarehouseRackEntity.is_active == True
    ).order_by(WarehouseRackEntity.rack_code.asc()).all()

    aisle_items: List[MasterDataOptionItem] = []
    rack_items: List[MasterDataOptionItem] = []
    seen_aisles = set()
    seen_racks = set()

    for r in db_racks:
        item = MasterDataOptionItem(
            id=r.id,
            code=r.rack_code,
            name=r.rack_name,
            type=r.rack_type,
            zone_name=r.zone.zone_name if r.zone else None
        )
        if r.rack_type == "aisle":
            aisle_items.append(item)
            seen_aisles.add(r.rack_name.strip().lower())
        else:
            rack_items.append(item)
            seen_racks.add(r.rack_name.strip().lower())

    # Bổ sung từ locations thực tế
    loc_aisles = db.query(WarehouseLocationEntity.aisle).filter(
        WarehouseLocationEntity.warehouse_id == warehouse_id,
        WarehouseLocationEntity.aisle.isnot(None)
    ).distinct().all()
    for (la,) in loc_aisles:
        if la and la.strip() and la.strip().lower() not in seen_aisles:
            aisle_items.append(MasterDataOptionItem(code=f"A-{len(aisle_items)+1}", name=la.strip(), type="aisle"))
            seen_aisles.add(la.strip().lower())

    loc_racks = db.query(WarehouseLocationEntity.rack).filter(
        WarehouseLocationEntity.warehouse_id == warehouse_id,
        WarehouseLocationEntity.rack.isnot(None)
    ).distinct().all()
    for (lr,) in loc_racks:
        if lr and lr.strip() and lr.strip().lower() not in seen_racks:
            rack_items.append(MasterDataOptionItem(code=f"R-{len(rack_items)+1}", name=lr.strip(), type="rack"))
            seen_racks.add(lr.strip().lower())

    # Gợi ý mặc định nếu chưa có
    if not aisle_items:
        for a in ["Dãy 1", "Dãy 2", "Dãy 3", "Dãy A1", "Dãy B1"]:
            aisle_items.append(MasterDataOptionItem(code=a.replace(" ", "-"), name=a, type="aisle"))

    if not rack_items:
        for rk in ["Tầng 1", "Tầng 2", "Tầng 3", "Tầng 4", "Kệ 01", "Kệ 02"]:
            rack_items.append(MasterDataOptionItem(code=rk.replace(" ", "-"), name=rk, type="rack"))

    # 3. Ô chứa hàng (Bins)
    bin_items: List[MasterDataOptionItem] = []
    seen_bins = set()
    loc_bins = db.query(WarehouseLocationEntity.bin).filter(
        WarehouseLocationEntity.warehouse_id == warehouse_id,
        WarehouseLocationEntity.bin.isnot(None)
    ).distinct().all()
    for (lb,) in loc_bins:
        if lb and lb.strip() and lb.strip().lower() not in seen_bins:
            bin_items.append(MasterDataOptionItem(code=f"BIN-{len(bin_items)+1}", name=lb.strip(), type="bin"))
            seen_bins.add(lb.strip().lower())

    if not bin_items:
        for b in ["Ô 01", "Ô 02", "Ô 03", "Ô 04", "Ô 05", "Hộc A", "Hộc B"]:
            bin_items.append(MasterDataOptionItem(code=b.replace(" ", "-"), name=b, type="bin"))

    return WarehouseMasterDataResponse(
        zones=zone_items,
        aisles=aisle_items,
        racks=rack_items,
        bins=bin_items,
    )


# --- QUẢN LÝ KHU VỰC (ZONES) CRUD ---

@router.get("/{warehouse_id}/zones", response_model=List[WarehouseZoneResponse])
def get_warehouse_zones(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Không tìm thấy kho hàng.")

    zones = db.query(WarehouseZoneEntity).filter(
        WarehouseZoneEntity.warehouse_id == warehouse_id
    ).order_by(WarehouseZoneEntity.id.asc()).all()

    # Nếu chưa có bản ghi nào trong bảng warehouse_zones, tự động tạo từ danh sách locations hiện có hoặc mẫu mặc định
    if not zones:
        unique_zones = db.query(WarehouseLocationEntity.zone).filter(
            WarehouseLocationEntity.warehouse_id == warehouse_id,
            WarehouseLocationEntity.zone.isnot(None)
        ).distinct().all()
        created_zones = []
        for idx, (uz,) in enumerate(unique_zones, start=1):
            if uz and uz.strip():
                z_entity = WarehouseZoneEntity(
                    warehouse_id=warehouse_id,
                    zone_code=f"KHU-{chr(64+idx)}",
                    zone_name=uz.strip(),
                    description="Khu vực khởi tạo từ vị trí hiện có",
                    is_active=True,
                )
                db.add(z_entity)
                created_zones.append(z_entity)
        if not created_zones:
            default_samples = [
                ("KHU-A", "Khu A - Thời trang", "Khu vực trưng bày và lưu kho quần áo"),
                ("KHU-B", "Khu B - Hàng may mặc", "Khu lưu kho phụ kiện thời trang"),
                ("KHU-C", "Khu C - Hàng phụ kiện", "Khu vực hàng nhỏ, phụ kiện đóng gói"),
            ]
            for zc, zn, zd in default_samples:
                z_entity = WarehouseZoneEntity(
                    warehouse_id=warehouse_id,
                    zone_code=zc,
                    zone_name=zn,
                    description=zd,
                    is_active=True,
                )
                db.add(z_entity)
                created_zones.append(z_entity)
        db.commit()
        zones = db.query(WarehouseZoneEntity).filter(
            WarehouseZoneEntity.warehouse_id == warehouse_id
        ).order_by(WarehouseZoneEntity.id.asc()).all()

    results: List[WarehouseZoneResponse] = []
    for z in zones:
        loc_count = db.query(WarehouseLocationEntity).filter(
            WarehouseLocationEntity.warehouse_id == warehouse_id,
            WarehouseLocationEntity.zone == z.zone_name
        ).count()
        results.append(WarehouseZoneResponse(
            id=z.id,
            warehouse_id=z.warehouse_id,
            zone_code=z.zone_code,
            zone_name=z.zone_name,
            description=z.description,
            is_active=z.is_active,
            created_at=z.created_at,
            locations_count=loc_count,
        ))
    return results


@router.post("/{warehouse_id}/zones", response_model=WarehouseZoneResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse_zone(
    warehouse_id: int,
    payload: WarehouseZoneCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Không tìm thấy kho hàng.")

    clean_code = payload.zone_code.strip().upper()
    existing = db.query(WarehouseZoneEntity).filter(
        WarehouseZoneEntity.warehouse_id == warehouse_id,
        func.lower(WarehouseZoneEntity.zone_code) == clean_code.lower()
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã khu vực '{clean_code}' đã tồn tại trong kho này."
        )

    new_zone = WarehouseZoneEntity(
        warehouse_id=warehouse_id,
        zone_code=clean_code,
        zone_name=payload.zone_name.strip(),
        description=payload.description.strip() if payload.description else None,
        is_active=payload.is_active if payload.is_active is not None else True,
    )
    db.add(new_zone)
    db.commit()
    db.refresh(new_zone)

    return WarehouseZoneResponse(
        id=new_zone.id,
        warehouse_id=new_zone.warehouse_id,
        zone_code=new_zone.zone_code,
        zone_name=new_zone.zone_name,
        description=new_zone.description,
        is_active=new_zone.is_active,
        created_at=new_zone.created_at,
        locations_count=0,
    )


@router.put("/zones/{zone_id}", response_model=WarehouseZoneResponse)
def update_warehouse_zone(
    zone_id: int,
    payload: WarehouseZoneUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    zone = db.query(WarehouseZoneEntity).filter(WarehouseZoneEntity.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Không tìm thấy khu vực.")

    old_name = zone.zone_name

    if payload.zone_code is not None:
        clean_code = payload.zone_code.strip().upper()
        existing = db.query(WarehouseZoneEntity).filter(
            WarehouseZoneEntity.warehouse_id == zone.warehouse_id,
            WarehouseZoneEntity.id != zone.id,
            func.lower(WarehouseZoneEntity.zone_code) == clean_code.lower()
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Mã khu vực '{clean_code}' đã được sử dụng.")
        zone.zone_code = clean_code

    if payload.zone_name is not None and payload.zone_name.strip():
        new_name = payload.zone_name.strip()
        # Đồng bộ cập nhật tên khu vực trong các locations đang liên kết
        if old_name != new_name:
            db.query(WarehouseLocationEntity).filter(
                WarehouseLocationEntity.warehouse_id == zone.warehouse_id,
                WarehouseLocationEntity.zone == old_name
            ).update({"zone": new_name}, synchronize_session=False)
        zone.zone_name = new_name

    if payload.description is not None:
        zone.description = payload.description.strip() if payload.description else None

    if payload.is_active is not None:
        zone.is_active = payload.is_active

    db.commit()
    db.refresh(zone)

    loc_count = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == zone.warehouse_id,
        WarehouseLocationEntity.zone == zone.zone_name
    ).count()

    return WarehouseZoneResponse(
        id=zone.id,
        warehouse_id=zone.warehouse_id,
        zone_code=zone.zone_code,
        zone_name=zone.zone_name,
        description=zone.description,
        is_active=zone.is_active,
        created_at=zone.created_at,
        locations_count=loc_count,
    )


@router.delete("/zones/{zone_id}")
def delete_warehouse_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    zone = db.query(WarehouseZoneEntity).filter(WarehouseZoneEntity.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Không tìm thấy khu vực.")

    loc_count = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == zone.warehouse_id,
        WarehouseLocationEntity.zone == zone.zone_name
    ).count()

    if loc_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Khu vực '{zone.zone_name}' đang có {loc_count} vị trí kệ lưu kho sử dụng. Vui lòng chuyển các vị trí sang khu vực khác trước khi xóa."
        )

    db.delete(zone)
    db.commit()
    return {"message": f"Đã xóa khu vực '{zone.zone_name}' thành công."}


# --- QUẢN LÝ KỆ / DÃY (RACKS / AISLES) CRUD ---

@router.get("/{warehouse_id}/racks", response_model=List[WarehouseRackResponse])
def get_warehouse_racks(
    warehouse_id: int,
    rack_type: Optional[str] = Query(None, description="Lọc theo loại: aisle hoặc rack"),
    zone_id: Optional[int] = Query(None, description="Lọc theo khu vực"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_VIEW_ROLES)),
):
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Không tìm thấy kho hàng.")

    query = db.query(WarehouseRackEntity).filter(WarehouseRackEntity.warehouse_id == warehouse_id)
    if rack_type:
        query = query.filter(WarehouseRackEntity.rack_type == rack_type.strip())
    if zone_id:
        query = query.filter(WarehouseRackEntity.zone_id == zone_id)

    racks = query.order_by(WarehouseRackEntity.id.asc()).all()

    # Khởi tạo mặc định nếu chưa có
    if not racks and not rack_type and not zone_id:
        default_racks = [
            ("DAY-01", "Dãy 1", "aisle", 1000.0),
            ("DAY-02", "Dãy 2", "aisle", 1000.0),
            ("DAY-A1", "Dãy A1", "aisle", 1200.0),
            ("TANG-01", "Tầng 1", "rack", 500.0),
            ("TANG-02", "Tầng 2", "rack", 500.0),
            ("TANG-03", "Tầng 3", "rack", 500.0),
            ("KE-01", "Kệ 01", "rack", 800.0),
        ]
        created = []
        for rc, rn, rt, cap in default_racks:
            r_ent = WarehouseRackEntity(
                warehouse_id=warehouse_id,
                rack_code=rc,
                rack_name=rn,
                rack_type=rt,
                max_capacity=cap,
                is_active=True,
            )
            db.add(r_ent)
            created.append(r_ent)
        db.commit()
        racks = db.query(WarehouseRackEntity).filter(
            WarehouseRackEntity.warehouse_id == warehouse_id
        ).order_by(WarehouseRackEntity.id.asc()).all()

    results: List[WarehouseRackResponse] = []
    for r in racks:
        # Đếm số location đang dùng tên rack/aisle này
        loc_count = db.query(WarehouseLocationEntity).filter(
            WarehouseLocationEntity.warehouse_id == warehouse_id,
            (WarehouseLocationEntity.aisle == r.rack_name) | (WarehouseLocationEntity.rack == r.rack_name)
        ).count()

        results.append(WarehouseRackResponse(
            id=r.id,
            warehouse_id=r.warehouse_id,
            zone_id=r.zone_id,
            zone_name=r.zone.zone_name if r.zone else None,
            rack_code=r.rack_code,
            rack_name=r.rack_name,
            rack_type=r.rack_type,
            max_capacity=r.max_capacity,
            is_active=r.is_active,
            created_at=r.created_at,
            locations_count=loc_count,
        ))
    return results


@router.post("/{warehouse_id}/racks", response_model=WarehouseRackResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse_rack(
    warehouse_id: int,
    payload: WarehouseRackCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Không tìm thấy kho hàng.")

    clean_code = payload.rack_code.strip().upper()
    existing = db.query(WarehouseRackEntity).filter(
        WarehouseRackEntity.warehouse_id == warehouse_id,
        func.lower(WarehouseRackEntity.rack_code) == clean_code.lower()
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã kệ/dãy '{clean_code}' đã tồn tại trong kho này."
        )

    new_rack = WarehouseRackEntity(
        warehouse_id=warehouse_id,
        zone_id=payload.zone_id,
        rack_code=clean_code,
        rack_name=payload.rack_name.strip(),
        rack_type=payload.rack_type or "rack",
        max_capacity=payload.max_capacity if payload.max_capacity is not None else 1000.0,
        is_active=payload.is_active if payload.is_active is not None else True,
    )
    db.add(new_rack)
    db.commit()
    db.refresh(new_rack)

    return WarehouseRackResponse(
        id=new_rack.id,
        warehouse_id=new_rack.warehouse_id,
        zone_id=new_rack.zone_id,
        zone_name=new_rack.zone.zone_name if new_rack.zone else None,
        rack_code=new_rack.rack_code,
        rack_name=new_rack.rack_name,
        rack_type=new_rack.rack_type,
        max_capacity=new_rack.max_capacity,
        is_active=new_rack.is_active,
        created_at=new_rack.created_at,
        locations_count=0,
    )


@router.put("/racks/{rack_id}", response_model=WarehouseRackResponse)
def update_warehouse_rack(
    rack_id: int,
    payload: WarehouseRackUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    rack = db.query(WarehouseRackEntity).filter(WarehouseRackEntity.id == rack_id).first()
    if not rack:
        raise HTTPException(status_code=404, detail="Không tìm thấy kệ/dãy.")

    old_name = rack.rack_name

    if payload.rack_code is not None:
        clean_code = payload.rack_code.strip().upper()
        existing = db.query(WarehouseRackEntity).filter(
            WarehouseRackEntity.warehouse_id == rack.warehouse_id,
            WarehouseRackEntity.id != rack.id,
            func.lower(WarehouseRackEntity.rack_code) == clean_code.lower()
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Mã kệ '{clean_code}' đã được sử dụng.")
        rack.rack_code = clean_code

    if payload.rack_name is not None and payload.rack_name.strip():
        new_name = payload.rack_name.strip()
        # Đồng bộ cập nhật vị trí tương ứng
        if old_name != new_name:
            if rack.rack_type == "aisle":
                db.query(WarehouseLocationEntity).filter(
                    WarehouseLocationEntity.warehouse_id == rack.warehouse_id,
                    WarehouseLocationEntity.aisle == old_name
                ).update({"aisle": new_name}, synchronize_session=False)
            else:
                db.query(WarehouseLocationEntity).filter(
                    WarehouseLocationEntity.warehouse_id == rack.warehouse_id,
                    WarehouseLocationEntity.rack == old_name
                ).update({"rack": new_name}, synchronize_session=False)
        rack.rack_name = new_name

    if payload.rack_type is not None:
        rack.rack_type = payload.rack_type
    if payload.zone_id is not None:
        rack.zone_id = payload.zone_id if payload.zone_id > 0 else None
    if payload.max_capacity is not None:
        rack.max_capacity = payload.max_capacity
    if payload.is_active is not None:
        rack.is_active = payload.is_active

    db.commit()
    db.refresh(rack)

    loc_count = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == rack.warehouse_id,
        (WarehouseLocationEntity.aisle == rack.rack_name) | (WarehouseLocationEntity.rack == rack.rack_name)
    ).count()

    return WarehouseRackResponse(
        id=rack.id,
        warehouse_id=rack.warehouse_id,
        zone_id=rack.zone_id,
        zone_name=rack.zone.zone_name if rack.zone else None,
        rack_code=rack.rack_code,
        rack_name=rack.rack_name,
        rack_type=rack.rack_type,
        max_capacity=rack.max_capacity,
        is_active=rack.is_active,
        created_at=rack.created_at,
        locations_count=loc_count,
    )


@router.delete("/racks/{rack_id}")
def delete_warehouse_rack(
    rack_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(WAREHOUSE_MANAGE_ROLES)),
):
    rack = db.query(WarehouseRackEntity).filter(WarehouseRackEntity.id == rack_id).first()
    if not rack:
        raise HTTPException(status_code=404, detail="Không tìm thấy kệ/dãy.")

    loc_count = db.query(WarehouseLocationEntity).filter(
        WarehouseLocationEntity.warehouse_id == rack.warehouse_id,
        (WarehouseLocationEntity.aisle == rack.rack_name) | (WarehouseLocationEntity.rack == rack.rack_name)
    ).count()

    if loc_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Kệ/Dãy '{rack.rack_name}' đang được liên kết trong {loc_count} vị trí lưu kho. Vui lòng chuyển các vị trí trước khi xóa."
        )

    db.delete(rack)
    db.commit()
    return {"message": f"Đã xóa kệ/dãy '{rack.rack_name}' thành công."}

