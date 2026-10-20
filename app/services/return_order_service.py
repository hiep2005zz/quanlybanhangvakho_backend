# backend/app/services/return_order_service.py
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_, and_

from app.models.entities import (
    OrderEntity,
    OrderItemEntity,
    DealerEntity,
    ProductEntity,
    WarehouseStockEntity,
    InventoryTransactionEntity,
    AuditLogEntity,
    UserEntity,
)
from app.models.goods_receipt import (
    WarehouseEntity,
    ProductBatchEntity,
    InventoryLedgerEntity,
)
from app.models.return_order import ReturnOrderEntity, ReturnOrderItemEntity
from app.schemas.auth import UserResponse
from app.schemas.return_order import (
    ReturnOrderCreate,
    ReturnOrderItemCreate,
    ReturnOrderResponse,
    ReturnOrderItemResponse,
    ReturnOrderListResponse,
    OrderReturnableInfoResponse,
    ReturnableItemInfo,
)


def get_utc_now():
    return datetime.now(timezone.utc)


def _safe_user_id(db: Session, user_id: Optional[int]) -> Optional[int]:
    """Trả về user_id nếu tồn tại trong database bảng users, ngược lại trả về None để tránh lỗi khóa ngoại (FK) trên SQL Server."""
    if not user_id:
        return None
    exists = db.query(UserEntity.id).filter(UserEntity.id == user_id).first()
    return user_id if exists else None


def is_reason_damaged(reason: str) -> bool:
    """Kiểm tra xem lý do trả hàng có phải là hàng lỗi / hư hỏng / hết hạn không."""
    if not reason:
        return False
    lower = reason.lower().strip()
    damaged_phrases = [
        "hàng lỗi", "hang loi", "hư hỏng", "hu hong", "hết hạn", "het han",
        "kém chất lượng", "kem chat luong", "móp méo", "mop meo", "bị vỡ", "bi vo",
        "bị bể", "bi be", "bị rách", "bi rach", "hỏng hóc", "hong hoc",
        "lỗi kỹ thuật", "loi ky thuat", "lỗi sản xuất", "loi san xuat"
    ]
    if any(p in lower for p in damaged_phrases):
        return True

    words = set(re.findall(r"\b\w+\b", lower))
    damaged_words = {"lỗi", "loi", "hỏng", "hong", "defect", "defective", "damage", "damaged", "expire", "expired", "vỡ", "bể", "móp", "rách"}
    return bool(words & damaged_words)


def generate_return_order_code(db: Session) -> str:
    """Tự động sinh mã phiếu trả hàng duy nhất dạng RMA00001, RMA00002..."""
    last_order = (
        db.query(ReturnOrderEntity)
        .order_by(desc(ReturnOrderEntity.id))
        .first()
    )
    if not last_order:
        return "RMA00001"
    
    # Tìm số từ mã cũ
    match = re.search(r"(\d+)$", last_order.code or "")
    if match:
        next_num = int(match.group(1)) + 1
        return f"RMA{next_num:05d}"
    return f"RMA{last_order.id + 1:05d}"


def get_damaged_warehouse(db: Session) -> WarehouseEntity:
    """Lấy hoặc tự động tạo Kho hàng lỗi / chờ xử lý (DAMAGED WAREHOUSE)."""
    wh = (
        db.query(WarehouseEntity)
        .filter(
            (WarehouseEntity.warehouse_type == "DAMAGED") |
            (WarehouseEntity.code == "KHO_LOI")
        )
        .first()
    )
    if not wh:
        wh = WarehouseEntity(
            code="KHO_LOI",
            name="Kho Hàng Lỗi & Chờ Xử Lý",
            warehouse_type="DAMAGED",
            status="Đang hoạt động",
            is_active=True,
            address="Khu vực lưu trữ hàng lỗi / cách ly kỹ thuật",
            manager_name="Thủ Kho Hàng Lỗi",
            phone="0900000000",
        )
        db.add(wh)
        db.flush()
    return wh


def get_default_good_warehouse(db: Session) -> WarehouseEntity:
    """Lấy kho hàng tốt / kho xuất chuẩn mặc định."""
    wh = (
        db.query(WarehouseEntity)
        .filter(
            WarehouseEntity.is_active == True,
            WarehouseEntity.code != "KHO_LOI",
            or_(WarehouseEntity.warehouse_type == "DEFAULT", WarehouseEntity.warehouse_type == None)
        )
        .order_by(WarehouseEntity.id.asc())
        .first()
    )
    if not wh:
        wh = db.query(WarehouseEntity).first()
    return wh


def get_order_returnable_info(db: Session, order_id: int) -> OrderReturnableInfoResponse:
    """
    Lấy thông tin đơn hàng và danh sách dòng sản phẩm kèm số lượng còn được phép trả.
    Công thức: Số lượng được phép trả = Số lượng đã giao - Tổng số lượng đã trả ở các phiếu trước (PENDING_WAREHOUSE + COMPLETED).
    """
    order = db.query(OrderEntity).filter(OrderEntity.id == order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đơn hàng ID {order_id}."
        )

    # 1. Tính tổng số lượng đã tạo phiếu trả hàng ở các phiếu trước
    # Chỉ tính phiếu ở trạng thái PENDING_WAREHOUSE hoặc COMPLETED
    prev_returns = (
        db.query(
            ReturnOrderItemEntity.order_item_id,
            ReturnOrderItemEntity.product_id,
            func.sum(ReturnOrderItemEntity.return_quantity).label("total_returned")
        )
        .join(ReturnOrderEntity, ReturnOrderItemEntity.return_order_id == ReturnOrderEntity.id)
        .filter(
            ReturnOrderEntity.order_id == order.id,
            ReturnOrderEntity.status.in_(["PENDING_WAREHOUSE", "COMPLETED"])
        )
        .group_by(ReturnOrderItemEntity.order_item_id, ReturnOrderItemEntity.product_id)
        .all()
    )

    returned_map_by_item_id = {}
    returned_map_by_prod_id = {}
    for r in prev_returns:
        if r.order_item_id:
            returned_map_by_item_id[r.order_item_id] = int(r.total_returned or 0)
        returned_map_by_prod_id[r.product_id] = returned_map_by_prod_id.get(r.product_id, 0) + int(r.total_returned or 0)

    # 2. Đọc các items từ Order
    items_info: List[ReturnableItemInfo] = []
    
    # Ưu tiên đọc từ relationship items_rel
    db_items = order.items_rel
    if db_items and len(db_items) > 0:
        for it in db_items:
            prod = it.product_rel or db.query(ProductEntity).filter(ProductEntity.id == it.product_id).first()
            is_batch_managed = bool(prod.is_batch_managed) if prod else False
            
            # Số lượng đã giao (mặc định lấy theo quantity của đơn nếu đơn đã xác nhận/giao)
            delivered_qty = it.quantity or 1
            already_returned = returned_map_by_item_id.get(it.id, returned_map_by_prod_id.get(it.product_id, 0))
            remaining_qty = max(0, delivered_qty - already_returned)

            # Lấy thông tin lô nếu SKU quản lý lô
            available_batches = []
            selected_batch_id = it.selected_batch_id or it.suggested_batch_id
            selected_batch_num = None
            if is_batch_managed:
                if selected_batch_id:
                    batch_ent = db.query(ProductBatchEntity).filter(ProductBatchEntity.id == selected_batch_id).first()
                    if batch_ent:
                        selected_batch_num = batch_ent.batch_number
                        available_batches.append({
                            "id": batch_ent.id,
                            "batch_number": batch_ent.batch_number,
                            "expiry_date": batch_ent.expiry_date.strftime("%d/%m/%Y") if batch_ent.expiry_date else None,
                            "quantity": batch_ent.quantity,
                            "is_order_batch": True,
                        })
                # Lấy thêm các lô hiện có của sản phẩm này để người dùng linh hoạt chọn nếu cần
                all_prod_batches = (
                    db.query(ProductBatchEntity)
                    .filter(ProductBatchEntity.product_id == it.product_id)
                    .all()
                )
                for b in all_prod_batches:
                    if not any(ab["id"] == b.id for ab in available_batches):
                        available_batches.append({
                            "id": b.id,
                            "batch_number": b.batch_number,
                            "expiry_date": b.expiry_date.strftime("%d/%m/%Y") if b.expiry_date else None,
                            "quantity": b.quantity,
                            "is_order_batch": False,
                        })

            items_info.append(ReturnableItemInfo(
                order_item_id=it.id,
                product_id=it.product_id,
                product_code=prod.code if prod else None,
                product_name=it.product_name or (prod.name if prod else "Sản phẩm"),
                unit_name=it.unit_name or (prod.base_unit if prod else "Cái"),
                unit_price=float(it.unit_price or 0.0),
                original_quantity=it.quantity or 1,
                delivered_quantity=delivered_qty,
                already_returned_quantity=already_returned,
                remaining_quantity=remaining_qty,
                is_batch_managed=is_batch_managed,
                selected_batch_id=selected_batch_id,
                selected_batch_number=selected_batch_num,
                available_batches=available_batches,
            ))
    elif order.items_json:
        # Dự phòng đọc từ JSON nếu items_rel rỗng
        import json
        try:
            raw_items = json.loads(order.items_json)
            for idx, raw in enumerate(raw_items):
                pid = raw.get("product_id") or raw.get("id")
                prod = db.query(ProductEntity).filter(ProductEntity.id == pid).first()
                is_batch_managed = bool(prod.is_batch_managed) if prod else False
                orig_qty = int(raw.get("quantity", 1))
                del_qty = int(raw.get("delivered_quantity", orig_qty))
                already_ret = returned_map_by_prod_id.get(pid, 0)
                rem_qty = max(0, del_qty - already_ret)

                available_batches = []
                if is_batch_managed and prod:
                    p_batches = db.query(ProductBatchEntity).filter(ProductBatchEntity.product_id == prod.id).all()
                    for b in p_batches:
                        available_batches.append({
                            "id": b.id,
                            "batch_number": b.batch_number,
                            "expiry_date": b.expiry_date.strftime("%d/%m/%Y") if b.expiry_date else None,
                            "quantity": b.quantity,
                            "is_order_batch": False,
                        })

                items_info.append(ReturnableItemInfo(
                    order_item_id=idx + 1,
                    product_id=pid,
                    product_code=raw.get("product_code") or (prod.code if prod else ""),
                    product_name=raw.get("product_name") or (prod.name if prod else ""),
                    unit_name=raw.get("unit") or raw.get("unit_name") or (prod.base_unit if prod else "Cái"),
                    unit_price=float(raw.get("price") or raw.get("unit_price") or 0.0),
                    original_quantity=orig_qty,
                    delivered_quantity=del_qty,
                    already_returned_quantity=already_ret,
                    remaining_quantity=rem_qty,
                    is_batch_managed=is_batch_managed,
                    selected_batch_id=raw.get("selected_batch_id"),
                    selected_batch_number=raw.get("batch_number"),
                    available_batches=available_batches,
                ))
        except Exception:
            pass

    damaged_wh = get_damaged_warehouse(db)
    good_wh = get_default_good_warehouse(db)

    return OrderReturnableInfoResponse(
        order_id=order.id,
        order_code=order.order_code,
        dealer_id=order.dealer_id,
        dealer_name=order.dealer_name,
        order_status=order.status,
        order_created_at=order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "",
        items=items_info,
        damaged_warehouse={
            "id": damaged_wh.id,
            "code": damaged_wh.code,
            "name": damaged_wh.name,
            "warehouse_type": "DAMAGED",
        },
        default_warehouse={
            "id": good_wh.id if good_wh else 1,
            "code": good_wh.code if good_wh else "KHO_HN",
            "name": good_wh.name if good_wh else "Kho Tổng",
            "warehouse_type": "DEFAULT",
        },
    )


def create_return_order(
    db: Session,
    data: ReturnOrderCreate,
    current_user: UserResponse,
) -> ReturnOrderEntity:
    """
    Quy tắc 1, 2, 3: Khởi tạo Phiếu trả hàng từ Đơn gốc.
    - Kiểm tra đơn gốc tồn tại.
    - Kiểm tra số lượng trả từng dòng hàng:
      Số lượng trả <= Số lượng đã giao - Tổng số lượng đã trả ở các phiếu trước (PENDING_WAREHOUSE + COMPLETED).
    - Kiểm tra Lô (nếu SKU quản lý lô: bắt buộc chọn lô).
    - Phân loại kho nhận: Hàng lỗi -> Kho lỗi (KHO_LOI), Hàng tốt -> Kho chuẩn.
    - Lưu ở trạng thái 'PENDING_WAREHOUSE' (Chờ kho xác nhận). TUYỆT ĐỐI CHƯA cộng tồn kho.
    """
    order = db.query(OrderEntity).filter(OrderEntity.id == data.order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Đơn hàng ID {data.order_id} không tồn tại trên hệ thống."
        )

    if not data.items or len(data.items) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng chọn ít nhất một sản phẩm cần trả hàng."
        )

    # Lấy thông tin các phiếu trả trước đó của đơn hàng này để validate số lượng
    prev_returns = (
        db.query(
            ReturnOrderItemEntity.order_item_id,
            ReturnOrderItemEntity.product_id,
            func.sum(ReturnOrderItemEntity.return_quantity).label("total_returned")
        )
        .join(ReturnOrderEntity, ReturnOrderItemEntity.return_order_id == ReturnOrderEntity.id)
        .filter(
            ReturnOrderEntity.order_id == order.id,
            ReturnOrderEntity.status.in_(["PENDING_WAREHOUSE", "COMPLETED"])
        )
        .group_by(ReturnOrderItemEntity.order_item_id, ReturnOrderItemEntity.product_id)
        .all()
    )

    returned_map_by_item_id = {}
    returned_map_by_prod_id = {}
    for r in prev_returns:
        if r.order_item_id:
            returned_map_by_item_id[r.order_item_id] = int(r.total_returned or 0)
        returned_map_by_prod_id[r.product_id] = returned_map_by_prod_id.get(r.product_id, 0) + int(r.total_returned or 0)

    # Đọc danh sách items của order gốc để lấy đơn giá và số lượng xuất
    order_items_map = {}
    if order.items_rel:
        for oi in order.items_rel:
            order_items_map[oi.id] = oi
            order_items_map[f"prod_{oi.product_id}"] = oi
    
    damaged_wh = get_damaged_warehouse(db)
    good_wh = get_default_good_warehouse(db)

    new_code = generate_return_order_code(db)
    now_utc = get_utc_now()

    return_order = ReturnOrderEntity(
        code=new_code,
        order_id=order.id,
        order_code=order.order_code,
        dealer_id=order.dealer_id,
        dealer_name=order.dealer_name,
        status="PENDING_WAREHOUSE",  # Quy tắc 4: Ban đầu ở trạng thái Chờ kho xác nhận
        total_items=0,
        total_quantity=0,
        total_refund_amount=0.0,
        note=data.note,
        created_by_id=_safe_user_id(db, current_user.id),
        created_by=current_user.full_name or current_user.username,
        created_at=now_utc,
    )
    db.add(return_order)
    db.flush()

    total_qty = 0
    total_refund = 0.0
    item_entities: List[ReturnOrderItemEntity] = []

    for req_item in data.items:
        if req_item.return_quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Số lượng trả của sản phẩm ID {req_item.product_id} phải lớn hơn 0."
            )

        if not req_item.return_reason or not req_item.return_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Vui lòng nhập/chọn lý do trả hàng bắt buộc cho từng dòng hàng."
            )

        product = db.query(ProductEntity).filter(ProductEntity.id == req_item.product_id).first()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy thông tin sản phẩm ID {req_item.product_id}."
            )

        # Tìm item gốc trên đơn hàng
        orig_order_item = None
        if req_item.order_item_id and req_item.order_item_id in order_items_map:
            orig_order_item = order_items_map[req_item.order_item_id]
        elif f"prod_{req_item.product_id}" in order_items_map:
            orig_order_item = order_items_map[f"prod_{req_item.product_id}"]

        # Tính số lượng đã giao và số lượng còn lại được phép trả
        delivered_qty = orig_order_item.quantity if orig_order_item else 1
        already_returned = (
            returned_map_by_item_id.get(req_item.order_item_id, 0)
            if req_item.order_item_id
            else returned_map_by_prod_id.get(req_item.product_id, 0)
        )
        remaining_allowable_qty = max(0, delivered_qty - already_returned)

        # QUY TẮC 2: Kiểm tra giới hạn số lượng trả (Strict Quantity Validation)
        if req_item.return_quantity > remaining_allowable_qty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Số lượng trả ({req_item.return_quantity}) của sản phẩm '{product.name}' "
                    f"vượt quá giới hạn cho phép trả lại ({remaining_allowable_qty}). "
                    f"(Đã giao: {delivered_qty}, Đã lập phiếu trước: {already_returned})."
                )
            )

        # Lấy đơn giá thực tế trên đơn hàng gốc để tính thành tiền trả lại
        unit_price = float(orig_order_item.unit_price) if orig_order_item else float(product.sell_price or 0.0)
        line_refund = round(req_item.return_quantity * unit_price, 2)

        # QUY TẮC 3: Kiểm tra Quản lý Lô (Lot/Batch Tracking)
        batch_id = req_item.batch_id
        batch_number = req_item.batch_number
        if product.is_batch_managed:
            if not batch_number and not batch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Sản phẩm '{product.name}' được quản lý theo Lô/Hạn sử dụng. "
                        f"Bắt buộc người dùng phải chọn/nhập Mã Lô đã xuất trên đơn gốc."
                    )
                )

            # Nếu có batch_id mà chưa có batch_number
            if batch_id and not batch_number:
                b_ent = db.query(ProductBatchEntity).filter(ProductBatchEntity.id == batch_id).first()
                if b_ent:
                    batch_number = b_ent.batch_number

        # QUY TẮC 3: Phân loại Kho nhận lại (Good vs. Defective/Damaged Warehouse Routing)
        is_damaged = is_reason_damaged(req_item.return_reason)
        if is_damaged:
            target_warehouse = damaged_wh
        else:
            target_wh_id = req_item.target_warehouse_id
            if target_wh_id:
                target_warehouse = db.query(WarehouseEntity).filter(WarehouseEntity.id == target_wh_id).first() or good_wh
            else:
                target_warehouse = good_wh

        item_ent = ReturnOrderItemEntity(
            return_order_id=return_order.id,
            order_item_id=orig_order_item.id if orig_order_item else req_item.order_item_id,
            product_id=product.id,
            product_code=product.code,
            product_name=product.name,
            unit_name=orig_order_item.unit_name if (orig_order_item and orig_order_item.unit_name) else (product.base_unit or "Cái"),
            unit_price=unit_price,
            original_quantity=delivered_qty,
            return_quantity=req_item.return_quantity,
            refund_amount=line_refund,
            return_reason=req_item.return_reason.strip(),
            is_damaged=is_damaged,
            target_warehouse_id=target_warehouse.id if target_warehouse else None,
            target_warehouse_code=target_warehouse.code if target_warehouse else None,
            target_warehouse_name=target_warehouse.name if target_warehouse else None,
            batch_id=batch_id,
            batch_number=batch_number,
            note=req_item.note,
            created_at=now_utc,
        )
        db.add(item_ent)
        item_entities.append(item_ent)

        total_qty += req_item.return_quantity
        total_refund += line_refund

    # Cập nhật tổng số lượng và thành tiền của phiếu trả
    return_order.total_items = len(item_entities)
    return_order.total_quantity = total_qty
    return_order.total_refund_amount = round(total_refund, 2)

    # Ghi Audit Log khởi tạo phiếu trả
    try:
        audit = AuditLogEntity(
            user_id=_safe_user_id(db, current_user.id),
            user_name=current_user.full_name or current_user.username,
            action_type="RETURN_ORDER_CREATE",
            entity_type="ReturnOrder",
            entity_id=return_order.code,
            new_values=f'{{"code": "{return_order.code}", "order_code": "{order.order_code}", "total_quantity": {total_qty}, "total_refund": {total_refund}}}',
            reason=f"Tạo phiếu trả hàng từ đại lý {order.dealer_name} theo đơn {order.order_code}",
            created_at=now_utc,
        )
        db.add(audit)
    except Exception:
        pass

    db.commit()
    db.refresh(return_order)
    return return_order


def confirm_return_receipt(
    db: Session,
    return_order_id: int,
    current_user: UserResponse,
    note: Optional[str] = None,
) -> ReturnOrderEntity:
    """
    Quy tắc 4: Quản lý kho xác thực nhận hàng (Confirm Receipt)
    - Chỉ được thao tác bởi Quản lý kho / Thủ kho / Admin.
    - Chuyển trạng thái phiếu sang COMPLETED.
    - Lưu confirmed_by, confirmed_at.
    - CỘNG TỒN KHO:
      + Tồn theo kho (WarehouseStockEntity) đúng Kho đích (Kho thường hoặc Kho lỗi).
      + Tồn tổng sản phẩm (ProductEntity.stock).
      + Tồn theo Lô (ProductBatchEntity) nếu SKU quản lý lô.
      + Ghi Thẻ kho (InventoryLedgerEntity).
      + Ghi Lịch sử giao dịch kho (InventoryTransactionEntity).
    TẤT CẢ PHẢI NẰM TRONG DATABASE TRANSACTION (ACID).
    """
    return_order = (
        db.query(ReturnOrderEntity)
        .filter(ReturnOrderEntity.id == return_order_id)
        .first()
    )
    if not return_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu trả hàng ID {return_order_id}."
        )

    if return_order.status != "PENDING_WAREHOUSE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu trả hàng {return_order.code} đang ở trạng thái '{return_order.status}', không thể xác nhận nhận hàng."
        )

    now_utc = get_utc_now()
    user_display_name = current_user.full_name or current_user.username

    # Bắt đầu thực thi các cập nhật kho trong transaction
    try:
        # 1. Chuyển trạng thái phiếu sang COMPLETED
        return_order.status = "COMPLETED"
        return_order.confirmed_by = user_display_name
        return_order.confirmed_by_id = _safe_user_id(db, current_user.id)
        return_order.confirmed_at = now_utc
        if note:
            return_order.note = ((return_order.note + " | ") if return_order.note else "") + f"Xác nhận kho: {note}"

        # 2. Xử lý từng dòng hàng để cộng tồn kho
        for item in return_order.items:
            prod = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).with_for_update().first()
            if not prod:
                continue

            target_wid = item.target_warehouse_id
            target_wcode = item.target_warehouse_code or ("KHO_LOI" if item.is_damaged else "KHO_HN")
            target_wname = item.target_warehouse_name or ("Kho Hàng Lỗi & Chờ Xử Lý" if item.is_damaged else "Kho Tổng")

            # A. Cập nhật tồn kho theo kho (WarehouseStockEntity)
            # Map target_wid tương thích với hệ thống (WH01, WH02, WH03 hoặc code/id)
            alt_wh_codes = [target_wcode]
            if target_wid:
                alt_wh_codes.append(str(target_wid))
                alt_wh_codes.append(f"WH{target_wid:02d}")
            if target_wcode == "KHO_HN":
                alt_wh_codes.append("WH01")
            elif target_wcode == "KHO_HCM":
                alt_wh_codes.append("WH02")
            elif target_wcode == "KHO_DN":
                alt_wh_codes.append("WH03")

            ws = (
                db.query(WarehouseStockEntity)
                .filter(
                    WarehouseStockEntity.product_id == prod.id,
                    or_(
                        WarehouseStockEntity.warehouse_id.in_(alt_wh_codes),
                        WarehouseStockEntity.warehouse_name == target_wname,
                    )
                )
                .with_for_update()
                .first()
            )

            prev_ws_stock = ws.actual_stock if ws else 0

            if ws:
                ws.actual_stock = (ws.actual_stock or 0) + item.return_quantity
                if ws.quantity_on_hand is not None:
                    ws.quantity_on_hand += item.return_quantity
                else:
                    ws.quantity_on_hand = ws.actual_stock
                ws.updated_at = now_utc
            else:
                # Tạo bản ghi tồn kho mới tại kho đích
                ws = WarehouseStockEntity(
                    warehouse_id=target_wcode,
                    warehouse_name=target_wname,
                    product_id=prod.id,
                    actual_stock=item.return_quantity,
                    quantity_on_hand=item.return_quantity,
                    reserved_stock=0,
                    reserved_quantity=0,
                    min_stock=prod.min_stock or 10,
                    created_at=now_utc,
                    updated_at=now_utc,
                )
                db.add(ws)

            new_ws_stock = (prev_ws_stock or 0) + item.return_quantity

            # B. Cập nhật tồn kho tổng sản phẩm (ProductEntity.stock)
            # Lưu ý: Hàng lỗi nhập kho lỗi vẫn cộng tồn vật lý tại kho lỗi (để quản lý xử lý kỹ thuật/đổi trả)
            prev_prod_stock = prod.stock or 0
            prod.stock = (prod.stock or 0) + item.return_quantity

            # C. Xử lý tồn theo Lô (ProductBatchEntity) nếu SKU quản lý lô
            batch_num_clean = item.batch_number.strip() if item.batch_number else None
            batch_entity = None
            if batch_num_clean:
                # Tìm lô hàng tại kho đích
                batch_entity = (
                    db.query(ProductBatchEntity)
                    .filter(
                        ProductBatchEntity.product_id == prod.id,
                        ProductBatchEntity.warehouse_id == (target_wid or 1),
                        ProductBatchEntity.batch_number == batch_num_clean,
                    )
                    .with_for_update()
                    .first()
                )

                if batch_entity:
                    batch_entity.quantity = float(batch_entity.quantity or 0.0) + float(item.return_quantity)
                    batch_entity.updated_at = now_utc
                else:
                    # Tra cứu ngày hết hạn từ lô gốc của đơn nếu có
                    exp_date = None
                    orig_batch = (
                        db.query(ProductBatchEntity)
                        .filter(
                            ProductBatchEntity.product_id == prod.id,
                            ProductBatchEntity.batch_number == batch_num_clean,
                        )
                        .first()
                    )
                    if orig_batch:
                        exp_date = orig_batch.expiry_date

                    batch_entity = ProductBatchEntity(
                        product_id=prod.id,
                        warehouse_id=target_wid or 1,
                        batch_number=batch_num_clean,
                        quantity=float(item.return_quantity),
                        reserved_quantity=0.0,
                        expiry_date=exp_date,
                        created_at=now_utc,
                        updated_at=now_utc,
                    )
                    db.add(batch_entity)

            # D. Ghi Thẻ kho / Sổ kho (InventoryLedgerEntity)
            ledger_entry = InventoryLedgerEntity(
                product_id=prod.id,
                warehouse_id=target_wid or 1,
                reference_code=return_order.code,
                transaction_type="RECEIPT",
                quantity=float(item.return_quantity),
                previous_stock=float(prev_prod_stock),
                new_stock=float(prod.stock),
                batch_number=batch_num_clean,
                performed_by=user_display_name,
                note=(
                    f"Nhập trả hàng từ ĐL {return_order.dealer_name} "
                    f"({target_wname}) - Lý do: {item.return_reason}"
                ),
                created_at=now_utc,
            )
            db.add(ledger_entry)

            # E. Ghi Lịch sử giao dịch kho (InventoryTransactionEntity)
            inv_tx = InventoryTransactionEntity(
                product_id=prod.id,
                product_name=prod.name,
                type="receipt",
                quantity=item.return_quantity,
                previous_stock=prev_prod_stock,
                new_stock=prod.stock,
                performed_by=user_display_name,
                user_role=current_user.role,
                reason=(
                    f"Nhập lại kho theo Phiếu trả hàng {return_order.code} từ ĐL {return_order.dealer_name}. "
                    f"Kho đích: {target_wname}. Lý do trả: {item.return_reason}"
                ),
                unit_name=item.unit_name,
                conversion_rate=1.0,
                base_quantity=float(item.return_quantity),
                batch_number=batch_num_clean,
                created_at=now_utc,
            )
            db.add(inv_tx)

        # 3. Ghi Audit Log xác nhận nhập kho thành công
        try:
            audit = AuditLogEntity(
                user_id=_safe_user_id(db, current_user.id),
                user_name=user_display_name,
                action_type="RETURN_ORDER_CONFIRM",
                entity_type="ReturnOrder",
                entity_id=return_order.code,
                new_values=f'{{"status": "COMPLETED", "confirmed_by": "{user_display_name}", "total_quantity": {return_order.total_quantity}}}',
                reason=f"Thủ kho xác nhận nhận hàng và cộng tồn kho cho phiếu {return_order.code}",
                created_at=now_utc,
            )
            db.add(audit)
        except Exception:
            pass

        # Commit Transaction
        db.commit()
        db.refresh(return_order)
        return return_order

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi xác nhận nhận hàng và cập nhật tồn kho: {str(e)}"
        )


def cancel_return_order(
    db: Session,
    return_order_id: int,
    current_user: UserResponse,
    reason: str,
) -> ReturnOrderEntity:
    """Hủy phiếu trả hàng khi còn ở trạng thái PENDING_WAREHOUSE."""
    return_order = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.id == return_order_id).first()
    if not return_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu trả hàng ID {return_order_id}."
        )

    if return_order.status != "PENDING_WAREHOUSE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể hủy phiếu trả hàng đang ở trạng thái '{return_order.status}'."
        )

    now_utc = get_utc_now()
    return_order.status = "CANCELLED"
    return_order.cancelled_by = current_user.full_name or current_user.username
    return_order.cancelled_by_id = _safe_user_id(db, current_user.id)
    return_order.cancelled_at = now_utc
    return_order.cancel_reason = reason.strip()

    try:
        audit = AuditLogEntity(
            user_id=_safe_user_id(db, current_user.id),
            user_name=current_user.full_name or current_user.username,
            action_type="RETURN_ORDER_CANCEL",
            entity_type="ReturnOrder",
            entity_id=return_order.code,
            reason=f"Hủy phiếu trả hàng: {reason}",
            created_at=now_utc,
        )
        db.add(audit)
    except Exception:
        pass

    db.commit()
    db.refresh(return_order)
    return return_order


def get_return_order_by_id(db: Session, return_order_id: int) -> ReturnOrderEntity:
    """Lấy chi tiết một phiếu trả hàng."""
    ro = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.id == return_order_id).first()
    if not ro:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu trả hàng ID {return_order_id}."
        )
    return ro


def list_return_orders(
    db: Session,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    dealer_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[ReturnOrderEntity], int]:
    """Danh sách phiếu trả hàng có phân trang và bộ lọc."""
    query = db.query(ReturnOrderEntity)

    if status_filter and status_filter.upper() != "ALL":
        if status_filter.upper() == "COMPLETED":
            query = query.filter(ReturnOrderEntity.status.in_(["COMPLETED", "CONFIRMED"]))
        else:
            query = query.filter(ReturnOrderEntity.status == status_filter.upper())

    if dealer_id:
        query = query.filter(ReturnOrderEntity.dealer_id == dealer_id)

    if search and search.strip():
        term = f"%{search.strip()}%"
        # Dùng các mapped Column thực sự của entity để tránh lỗi 'property' object has no attribute 'ilike'
        code_col = getattr(ReturnOrderEntity, "return_code", getattr(ReturnOrderEntity, "code", None))
        created_by_col = getattr(ReturnOrderEntity, "created_by_name", getattr(ReturnOrderEntity, "created_by", None))
        
        search_clauses = [
            ReturnOrderEntity.order_code.ilike(term),
            ReturnOrderEntity.dealer_name.ilike(term),
        ]
        if code_col is not None and hasattr(code_col, "ilike"):
            search_clauses.append(code_col.ilike(term))
        if created_by_col is not None and hasattr(created_by_col, "ilike"):
            search_clauses.append(created_by_col.ilike(term))
            
        query = query.filter(or_(*search_clauses))

    total = query.count()
    items = (
        query.order_by(desc(ReturnOrderEntity.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def to_response_dto(ro: ReturnOrderEntity) -> ReturnOrderResponse:
    """Chuyển entity sang ReturnOrderResponse DTO an toàn, chống crash 500 do thiếu thuộc tính hoặc giá trị Null."""
    items_dto = []
    for item in (ro.items or []):
        try:
            r_qty = getattr(item, "return_quantity", None)
            if r_qty is None or r_qty <= 0:
                r_qty = getattr(item, "quantity", 1) or 1

            orig_qty = getattr(item, "original_quantity", None)
            if orig_qty is None or orig_qty == 0:
                orig_qty = r_qty

            ref_amt = getattr(item, "refund_amount", None)
            if ref_amt is None:
                ref_amt = getattr(item, "line_total", 0.0) or 0.0

            r_reason = (
                getattr(item, "return_reason", None)
                or getattr(item, "reason", None)
                or getattr(ro, "reason", None)
                or getattr(ro, "note", None)
                or "Trả hàng đại lý"
            )

            items_dto.append(
                ReturnOrderItemResponse(
                    id=item.id,
                    return_order_id=item.return_order_id,
                    order_item_id=getattr(item, "order_item_id", None),
                    product_id=item.product_id,
                    product_code=getattr(item, "product_code", None),
                    product_name=str(getattr(item, "product_name", "") or "Sản phẩm"),
                    unit_name=str(getattr(item, "unit_name", "") or "Cái"),
                    unit_price=float(getattr(item, "unit_price", 0.0) or 0.0),
                    original_quantity=int(orig_qty or 0),
                    return_quantity=int(r_qty or 1),
                    refund_amount=float(ref_amt or 0.0),
                    return_reason=str(r_reason),
                    is_damaged=bool(getattr(item, "is_damaged", False)),
                    target_warehouse_id=getattr(item, "target_warehouse_id", None),
                    target_warehouse_code=getattr(item, "target_warehouse_code", None),
                    target_warehouse_name=getattr(item, "target_warehouse_name", None),
                    batch_id=getattr(item, "batch_id", None),
                    batch_number=getattr(item, "batch_number", None),
                    note=getattr(item, "note", None),
                )
            )
        except Exception:
            continue

    code_val = (
        getattr(ro, "code", None)
        or getattr(ro, "return_code", None)
        or f"RMA{getattr(ro, 'id', 1):05d}"
    )
    order_id_val = getattr(ro, "order_id", None) or 0
    order_code_val = getattr(ro, "order_code", None) or ""
    dealer_id_val = getattr(ro, "dealer_id", None) or 0
    dealer_name_val = getattr(ro, "dealer_name", None) or "Đại lý"
    status_val = getattr(ro, "status", None) or "PENDING_WAREHOUSE"

    total_items_val = getattr(ro, "total_items", None)
    if total_items_val is None or total_items_val == 0:
        total_items_val = len(items_dto)

    total_qty_val = getattr(ro, "total_quantity", None)
    if total_qty_val is None or total_qty_val == 0:
        total_qty_val = sum(it.return_quantity for it in items_dto)

    refund_amt_val = getattr(ro, "total_refund_amount", None)
    if refund_amt_val is None or refund_amt_val == 0.0:
        refund_amt_val = getattr(ro, "total_amount", 0.0) or sum(it.refund_amount for it in items_dto)

    created_by_val = getattr(ro, "created_by", None) or getattr(ro, "created_by_name", None) or "Hệ thống"
    created_at_dt = getattr(ro, "created_at", None)
    created_at_str = created_at_dt.strftime("%d/%m/%Y %H:%M") if created_at_dt and hasattr(created_at_dt, "strftime") else ""

    confirmed_by_val = getattr(ro, "confirmed_by", None) or getattr(ro, "confirmed_by_name", None)
    confirmed_at_dt = getattr(ro, "confirmed_at", None)
    confirmed_at_str = confirmed_at_dt.strftime("%d/%m/%Y %H:%M") if confirmed_at_dt and hasattr(confirmed_at_dt, "strftime") else None

    cancelled_by_val = getattr(ro, "cancelled_by", None) or getattr(ro, "cancelled_by_name", None)
    cancelled_at_dt = getattr(ro, "cancelled_at", None)
    cancelled_at_str = cancelled_at_dt.strftime("%d/%m/%Y %H:%M") if cancelled_at_dt and hasattr(cancelled_at_dt, "strftime") else None
    cancel_reason_val = getattr(ro, "cancel_reason", None)

    return ReturnOrderResponse(
        id=ro.id,
        code=str(code_val),
        order_id=int(order_id_val),
        order_code=str(order_code_val),
        dealer_id=int(dealer_id_val),
        dealer_name=str(dealer_name_val),
        status=str(status_val),
        total_items=int(total_items_val or 0),
        total_quantity=int(total_qty_val or 0),
        total_refund_amount=float(refund_amt_val or 0.0),
        note=getattr(ro, "note", None) or getattr(ro, "reason", None),
        created_by=str(created_by_val),
        created_at=str(created_at_str),
        confirmed_by=confirmed_by_val,
        confirmed_at=confirmed_at_str,
        cancelled_by=cancelled_by_val,
        cancelled_at=cancelled_at_str,
        cancel_reason=cancel_reason_val,
        items=items_dto,
    )
