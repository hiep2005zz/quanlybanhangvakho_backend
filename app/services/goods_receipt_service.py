# backend/app/services/goods_receipt_service.py
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from fastapi import HTTPException, status, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import ProductEntity, InventoryTransactionEntity, AuditLogEntity
from app.models.supplier import SupplierEntity
from app.models.goods_receipt import (
    WarehouseEntity,
    UnitOfMeasureEntity,
    GoodsReceiptNoteEntity,
    GoodsReceiptNoteItemEntity,
    ProductBatchEntity,
    InventoryLedgerEntity,
    get_utc_now,
)
from app.schemas.auth import UserResponse
from app.schemas.goods_receipt import (
    GoodsReceiptCreateRequest,
    GoodsReceiptUpdateRequest,
    GoodsReceiptResponse,
    GoodsReceiptItemResponse,
    GoodsReceiptItemCreate,
)
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.services.audit_service import log_audit_event


def generate_grn_code(db: Session) -> str:
    """Sinh mã phiếu nhập kho tự động duy nhất: GRN-YYYYMMDD-XXXX."""
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"GRN-{today_str}-"
    last_grn = (
        db.query(GoodsReceiptNoteEntity)
        .filter(GoodsReceiptNoteEntity.code.startswith(prefix))
        .order_by(GoodsReceiptNoteEntity.id.desc())
        .first()
    )
    if last_grn and last_grn.code:
        try:
            last_seq = int(last_grn.code.split("-")[-1])
            seq = last_seq + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def resolve_item_conversion(
    db: Session,
    product: ProductEntity,
    unit_name: Optional[str],
    uom_id: Optional[int],
    custom_rate: Optional[float],
) -> Tuple[Optional[int], str, float]:
    """
    Tự động tra cứu ĐVT và tỷ lệ quy đổi (conversion_rate) của sản phẩm.
    base_quantity = quantity * conversion_rate.
    """
    base_unit = product.base_unit or "Cái"
    resolved_unit_name = None
    resolved_uom_id = uom_id

    if uom_id:
        uom_rec = db.query(UnitOfMeasureEntity).filter(UnitOfMeasureEntity.id == uom_id).first()
        if uom_rec:
            resolved_unit_name = uom_rec.name

    if not resolved_unit_name and unit_name:
        resolved_unit_name = unit_name.strip()

    if not resolved_unit_name:
        resolved_unit_name = base_unit

    # Tra cứu conversion_rate
    if custom_rate and custom_rate > 0:
        conversion_rate = float(custom_rate)
    elif resolved_unit_name.lower() == base_unit.lower():
        conversion_rate = 1.0
    else:
        # Tra cứu từ cấu hình đơn vị quy đổi của sản phẩm
        units_list = product.units or []
        matched = next(
            (u for u in units_list if u.get("unit_name", "").lower() == resolved_unit_name.lower()),
            None,
        )
        if matched and float(matched.get("conversion_rate", 0)) > 0:
            conversion_rate = float(matched["conversion_rate"])
        else:
            conversion_rate = 1.0

    # Nếu chưa có uom_id, thử tìm trong bảng units_of_measure
    if not resolved_uom_id:
        uom_rec = (
            db.query(UnitOfMeasureEntity)
            .filter(func.lower(UnitOfMeasureEntity.name) == resolved_unit_name.lower())
            .first()
        )
        if uom_rec:
            resolved_uom_id = uom_rec.id

    return resolved_uom_id, resolved_unit_name, conversion_rate


def validate_batch_logic(
    product: ProductEntity,
    batch_number: Optional[str],
    expiry_date: Optional[datetime],
) -> None:
    """
    Quản lý lô: Kiểm tra cờ is_batch_managed của sản phẩm.
    Nếu True, bắt buộc phải có batch_number và expiry_date.
    Nếu False, cho phép để trống.
    """
    is_batch = bool(getattr(product, "is_batch_managed", False))
    if is_batch:
        if not batch_number or not batch_number.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{product.name}' (Mã: {product.code}) có cờ quản lý lô (is_batch_managed=True). Bắt buộc phải có số lô (batch_number).",
            )
        if not expiry_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{product.name}' (Mã: {product.code}) có cờ quản lý lô (is_batch_managed=True). Bắt buộc phải có hạn sử dụng (expiry_date).",
            )


def build_goods_receipt_response(receipt: GoodsReceiptNoteEntity) -> GoodsReceiptResponse:
    """Chuyển entity sang schema response đầy đủ."""
    items_resp = []
    for it in receipt.items:
        prod_code = it.product.code if it.product else None
        prod_name = it.product.name if it.product else None
        items_resp.append(
            GoodsReceiptItemResponse(
                id=it.id,
                receipt_note_id=it.receipt_note_id,
                product_id=it.product_id,
                product_code=prod_code,
                product_name=prod_name,
                uom_id=it.uom_id,
                unit_name=it.unit_name,
                quantity=it.quantity,
                conversion_rate=it.conversion_rate,
                base_quantity=it.base_quantity,
                unit_price=it.unit_price,
                batch_number=it.batch_number,
                expiry_date=it.expiry_date,
                note=it.note,
            )
        )

    return GoodsReceiptResponse(
        id=receipt.id,
        code=receipt.code,
        supplier_id=receipt.supplier_id,
        supplier_code=receipt.supplier.code if receipt.supplier else None,
        supplier_name=receipt.supplier.name if receipt.supplier else None,
        reference_number=receipt.reference_number,
        receipt_date=receipt.receipt_date,
        warehouse_id=receipt.warehouse_id,
        warehouse_code=receipt.warehouse.code if receipt.warehouse else None,
        warehouse_name=receipt.warehouse.name if receipt.warehouse else None,
        status=receipt.status,
        note=receipt.note,
        total_items=receipt.total_items,
        total_quantity=receipt.total_quantity,
        total_amount=receipt.total_amount,
        created_by=receipt.created_by,
        confirmed_by=receipt.confirmed_by,
        confirmed_at=receipt.confirmed_at,
        created_at=receipt.created_at,
        updated_at=receipt.updated_at,
        items=items_resp,
    )


def create_goods_receipt(
    db: Session,
    data: GoodsReceiptCreateRequest,
    current_user: UserResponse,
    request: Optional[Request] = None,
) -> GoodsReceiptResponse:
    """
    Tạo mới phiếu nhập kho ở trạng thái DRAFT.
    Tuyệt đối KHÔNG cộng hay làm biến động số lượng tồn kho.
    """
    # 1. Kiểm tra nhà cung cấp
    supplier = db.query(SupplierEntity).filter(SupplierEntity.id == data.supplier_id).first()
    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không tìm thấy nhà cung cấp có ID {data.supplier_id}.",
        )
    if not supplier.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nhà cung cấp '{supplier.name}' đã ngừng giao dịch, không thể lập phiếu nhập.",
        )

    # 2. Kiểm tra kho nhận
    warehouse = db.query(WarehouseEntity).filter(WarehouseEntity.id == data.warehouse_id).first()
    if not warehouse:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không tìm thấy kho nhận hàng có ID {data.warehouse_id}.",
        )
    if not warehouse.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kho '{warehouse.name}' hiện không hoạt động.",
        )

    receipt_code = generate_grn_code(db)
    receipt_date = data.receipt_date or get_utc_now()

    receipt = GoodsReceiptNoteEntity(
        code=receipt_code,
        supplier_id=supplier.id,
        reference_number=data.reference_number.strip() if data.reference_number else None,
        receipt_date=receipt_date,
        warehouse_id=warehouse.id,
        status="DRAFT",
        note=data.note,
        created_by=current_user.full_name or current_user.username,
        created_at=get_utc_now(),
    )
    db.add(receipt)
    db.flush()

    total_qty = 0.0
    total_amt = 0.0

    # 3. Xử lý từng dòng hàng
    for item_in in data.items:
        prod = db.query(ProductEntity).filter(ProductEntity.id == item_in.product_id).first()
        if not prod:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Không tìm thấy sản phẩm có ID {item_in.product_id}.",
            )
        if prod.status == "inactive":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sản phẩm '{prod.code}' - {prod.name} đã ngừng kinh doanh, không thể nhập kho.",
            )

        # Quản lý lô
        validate_batch_logic(prod, item_in.batch_number, item_in.expiry_date)

        # Tra cứu tỷ lệ quy đổi và tính base_quantity
        uom_id, unit_name, rate = resolve_item_conversion(
            db, prod, item_in.unit_name, item_in.uom_id, item_in.conversion_rate
        )
        base_qty = round(item_in.quantity * rate, 4)

        u_price = float(item_in.unit_price or 0.0)
        line_amount = round(item_in.quantity * u_price, 2)
        total_qty += base_qty
        total_amt += line_amount

        item_entity = GoodsReceiptNoteItemEntity(
            receipt_note_id=receipt.id,
            product_id=prod.id,
            uom_id=uom_id,
            unit_name=unit_name,
            quantity=float(item_in.quantity),
            conversion_rate=rate,
            base_quantity=base_qty,
            unit_price=u_price,
            batch_number=item_in.batch_number.strip() if item_in.batch_number else None,
            expiry_date=item_in.expiry_date,
            note=item_in.note,
        )
        db.add(item_entity)

    receipt.total_items = len(data.items)
    receipt.total_quantity = total_qty
    receipt.total_amount = total_amt

    db.commit()
    db.refresh(receipt)

    return build_goods_receipt_response(receipt)


def update_goods_receipt(
    db: Session,
    receipt_id: int,
    data: GoodsReceiptUpdateRequest,
    current_user: UserResponse,
    request: Optional[Request] = None,
) -> GoodsReceiptResponse:
    """
    Cập nhật nội dung phiếu nhập kho nháp (DRAFT).
    Tính bất biến: Khi phiếu đã ở trạng thái CONFIRMED, chặn toàn bộ thao tác SỬA (PUT).
    """
    receipt = (
        db.query(GoodsReceiptNoteEntity)
        .filter(GoodsReceiptNoteEntity.id == receipt_id)
        .first()
    )
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu nhập kho có ID {receipt_id}.",
        )

    # Ràng buộc bất biến: Chặn sửa nếu đã CONFIRMED
    if receipt.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể chỉnh sửa phiếu nhập kho đã xác nhận (CONFIRMED). Hệ thống không cho phép sửa trực tiếp, vui lòng lập phiếu điều chỉnh.",
        )

    if data.supplier_id is not None:
        supplier = db.query(SupplierEntity).filter(SupplierEntity.id == data.supplier_id).first()
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Không tìm thấy nhà cung cấp có ID {data.supplier_id}.",
            )
        if not supplier.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Nhà cung cấp '{supplier.name}' đã ngừng giao dịch.",
            )
        receipt.supplier_id = supplier.id

    if data.warehouse_id is not None:
        warehouse = db.query(WarehouseEntity).filter(WarehouseEntity.id == data.warehouse_id).first()
        if not warehouse:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Không tìm thấy kho có ID {data.warehouse_id}.",
            )
        if not warehouse.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Kho '{warehouse.name}' không hoạt động.",
            )
        receipt.warehouse_id = warehouse.id

    if data.reference_number is not None:
        receipt.reference_number = data.reference_number.strip() if data.reference_number else None
    if data.receipt_date is not None:
        receipt.receipt_date = data.receipt_date
    if data.note is not None:
        receipt.note = data.note

    # Cập nhật các dòng hàng nếu có truyền
    if data.items is not None:
        if len(data.items) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phiếu nhập kho bắt buộc phải có ít nhất 1 dòng hàng.",
            )

        # Xóa các dòng item cũ
        db.query(GoodsReceiptNoteItemEntity).filter(
            GoodsReceiptNoteItemEntity.receipt_note_id == receipt.id
        ).delete(synchronize_session=False)

        total_qty = 0.0
        total_amt = 0.0

        for item_in in data.items:
            prod = db.query(ProductEntity).filter(ProductEntity.id == item_in.product_id).first()
            if not prod:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Không tìm thấy sản phẩm có ID {item_in.product_id}.",
                )
            if prod.status == "inactive":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Sản phẩm '{prod.code}' - {prod.name} đã ngừng kinh doanh.",
                )

            validate_batch_logic(prod, item_in.batch_number, item_in.expiry_date)

            uom_id, unit_name, rate = resolve_item_conversion(
                db, prod, item_in.unit_name, item_in.uom_id, item_in.conversion_rate
            )
            base_qty = round(item_in.quantity * rate, 4)
            u_price = float(item_in.unit_price or 0.0)
            total_qty += base_qty
            total_amt += round(item_in.quantity * u_price, 2)

            new_item = GoodsReceiptNoteItemEntity(
                receipt_note_id=receipt.id,
                product_id=prod.id,
                uom_id=uom_id,
                unit_name=unit_name,
                quantity=float(item_in.quantity),
                conversion_rate=rate,
                base_quantity=base_qty,
                unit_price=u_price,
                batch_number=item_in.batch_number.strip() if item_in.batch_number else None,
                expiry_date=item_in.expiry_date,
                note=item_in.note,
            )
            db.add(new_item)

        receipt.total_items = len(data.items)
        receipt.total_quantity = total_qty
        receipt.total_amount = total_amt

    receipt.updated_at = get_utc_now()
    db.commit()
    db.refresh(receipt)

    return build_goods_receipt_response(receipt)


def confirm_goods_receipt(
    db: Session,
    receipt_id: int,
    current_user: UserResponse,
    request: Optional[Request] = None,
) -> GoodsReceiptResponse:
    """
    Xác nhận phiếu nhập kho (CONFIRMED).
    Thực thi trong một Database Transaction duy nhất:
    - Chuyển trạng thái sang CONFIRMED.
    - Cộng tồn kho theo base_quantity vào kho tương ứng (tồn tổng và tồn theo số lô).
    - Ghi nhật ký thẻ kho/sổ kho (InventoryLedger).
    - Ghi InventoryTransactionEntity & Audit Log.
    """
    receipt = (
        db.query(GoodsReceiptNoteEntity)
        .filter(GoodsReceiptNoteEntity.id == receipt_id)
        .first()
    )
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu nhập kho có ID {receipt_id}.",
        )

    if receipt.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiếu nhập kho này đã được xác nhận trước đó.",
        )

    if not receipt.items or len(receipt.items) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiếu nhập kho không có dòng hàng nào để xác nhận nhập kho.",
        )

    now_utc = get_utc_now()
    user_name_str = current_user.full_name or current_user.username

    # Thực thi cập nhật trong Transaction
    try:
        receipt.status = "CONFIRMED"
        receipt.confirmed_by = user_name_str
        receipt.confirmed_at = now_utc
        receipt.updated_at = now_utc

        for item in receipt.items:
            # 1. Cập nhật tồn kho tổng của sản phẩm
            prod = db.query(ProductEntity).filter(ProductEntity.id == item.product_id).first()
            if not prod:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Không tìm thấy sản phẩm có ID {item.product_id} trong cơ sở dữ liệu.",
                )

            prev_stock = prod.stock or 0
            # Cộng tồn theo base_quantity
            added_base_qty = int(round(item.base_quantity))
            prod.stock = prev_stock + added_base_qty
            new_stock = prod.stock

            # Đồng bộ bộ nhớ RAW_PRODUCTS nếu có
            for raw_p in RAW_PRODUCTS:
                if raw_p.get("id") == prod.id:
                    raw_p["stock"] = prod.stock

            # 2. Quản lý tồn theo số lô (ProductBatchEntity)
            if item.batch_number and item.batch_number.strip():
                clean_batch = item.batch_number.strip()
                batch_rec = (
                    db.query(ProductBatchEntity)
                    .filter(
                        ProductBatchEntity.product_id == prod.id,
                        ProductBatchEntity.warehouse_id == receipt.warehouse_id,
                        ProductBatchEntity.batch_number == clean_batch,
                    )
                    .first()
                )
                if batch_rec:
                    batch_rec.quantity += item.base_quantity
                    if item.expiry_date and not batch_rec.expiry_date:
                        batch_rec.expiry_date = item.expiry_date
                    batch_rec.updated_at = now_utc
                else:
                    new_batch = ProductBatchEntity(
                        product_id=prod.id,
                        warehouse_id=receipt.warehouse_id,
                        batch_number=clean_batch,
                        expiry_date=item.expiry_date,
                        quantity=item.base_quantity,
                        created_at=now_utc,
                        updated_at=now_utc,
                    )
                    db.add(new_batch)

            # 3. Ghi thẻ kho / sổ kho (InventoryLedgerEntity)
            ledger_entry = InventoryLedgerEntity(
                product_id=prod.id,
                warehouse_id=receipt.warehouse_id,
                receipt_note_id=receipt.id,
                reference_code=receipt.code,
                transaction_type="RECEIPT",
                quantity=item.base_quantity,
                previous_stock=float(prev_stock),
                new_stock=float(new_stock),
                batch_number=item.batch_number,
                performed_by=user_name_str,
                note=f"Nhập kho theo phiếu {receipt.code} (HĐ/Phiếu giao: {receipt.reference_number or 'N/A'})",
                created_at=now_utc,
            )
            db.add(ledger_entry)

            # 4. Ghi InventoryTransactionEntity (để đồng bộ lịch sử kho hiện có)
            inv_tx = InventoryTransactionEntity(
                product_id=prod.id,
                product_name=prod.name,
                type="receipt",
                quantity=int(item.quantity),
                previous_stock=prev_stock,
                new_stock=new_stock,
                performed_by=user_name_str,
                user_role=current_user.role,
                reason=f"Nhập kho từ NCC theo phiếu {receipt.code}",
                unit_name=item.unit_name,
                conversion_rate=item.conversion_rate,
                base_quantity=item.base_quantity,
                created_at=now_utc,
            )
            db.add(inv_tx)

        # 5. Ghi Audit Log cho hệ thống
        log_audit_event(
            db=db,
            user=current_user,
            action_type="GOODS_RECEIPT_CONFIRM",
            entity_type="GoodsReceiptNote",
            entity_id=receipt.code,
            old_val={"status": "DRAFT"},
            new_val={"status": "CONFIRMED", "total_quantity": receipt.total_quantity},
            reason=f"Xác nhận nhập kho phiếu {receipt.code} từ NCC",
            request=request,
        )

        db.commit()
        db.refresh(receipt)
    except Exception as ex:
        db.rollback()
        raise ex

    return build_goods_receipt_response(receipt)


def delete_goods_receipt(
    db: Session,
    receipt_id: int,
    current_user: UserResponse,
    request: Optional[Request] = None,
) -> dict:
    """
    Xóa phiếu nhập kho nháp (DRAFT).
    Tính bất biến: Khi phiếu đã ở trạng thái CONFIRMED, chặn toàn bộ thao tác XÓA (DELETE).
    """
    receipt = (
        db.query(GoodsReceiptNoteEntity)
        .filter(GoodsReceiptNoteEntity.id == receipt_id)
        .first()
    )
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu nhập kho có ID {receipt_id}.",
        )

    if receipt.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể xóa phiếu nhập kho đã xác nhận (CONFIRMED). Hệ thống không cho phép xóa trực tiếp, vui lòng lập phiếu điều chỉnh.",
        )

    code = receipt.code
    db.delete(receipt)
    db.commit()

    return {"status": "success", "message": f"Đã xóa phiếu nhập kho nháp {code}."}


def get_goods_receipt(db: Session, receipt_id: int) -> GoodsReceiptResponse:
    """Xem chi tiết phiếu nhập kho."""
    receipt = (
        db.query(GoodsReceiptNoteEntity)
        .filter(GoodsReceiptNoteEntity.id == receipt_id)
        .first()
    )
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu nhập kho có ID {receipt_id}.",
        )
    return build_goods_receipt_response(receipt)


def list_goods_receipts(
    db: Session,
    supplier_id: Optional[int] = None,
    warehouse_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[GoodsReceiptResponse], int]:
    """Lấy danh sách phiếu nhập kho có lọc và phân trang."""
    query = db.query(GoodsReceiptNoteEntity)

    if supplier_id:
        query = query.filter(GoodsReceiptNoteEntity.supplier_id == supplier_id)
    if warehouse_id:
        query = query.filter(GoodsReceiptNoteEntity.warehouse_id == warehouse_id)
    if status_filter:
        query = query.filter(GoodsReceiptNoteEntity.status == status_filter.upper())
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            (GoodsReceiptNoteEntity.code.ilike(s))
            | (GoodsReceiptNoteEntity.reference_number.ilike(s))
            | (GoodsReceiptNoteEntity.note.ilike(s))
        )

    total = query.count()
    receipts = query.order_by(GoodsReceiptNoteEntity.id.desc()).offset(offset).limit(limit).all()
    return [build_goods_receipt_response(r) for r in receipts], total
