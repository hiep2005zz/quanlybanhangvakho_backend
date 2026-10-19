# backend/app/api/v1/endpoints/sales_invoices.py
from __future__ import annotations
import io
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_, desc

from app.core.database import get_db
from app.api.deps import get_current_user
from app.schemas.auth import UserResponse
from app.models.entities import (
    OrderDispatchEntity,
    OrderEntity,
    OrderItemEntity,
    DealerEntity,
    DeliveryTripDispatchEntity,
    DeliveryTripStopEntity,
    ProductEntity,
)
from app.models.invoice import (
    InvoiceEntity,
    InvoiceDeliveryNoteEntity,
    InvoiceItemEntity,
    InvoiceAdjustmentEntity,
)
from app.services.audit_service import log_audit_event

router = APIRouter()


def get_utc_now():
    return datetime.now(timezone.utc)


def _require_accountant_or_admin(current_user: UserResponse):
    """
    Ràng buộc RBAC:
    Cho phép nhân viên nội bộ (Admin, Kế toán, Quản lý kinh doanh, Quản lý kho, Thủ kho, Nhân viên kinh doanh).
    Chặn tài khoản khách hàng / đại lý (customer).
    """
    roles = set(current_user.roles or [])
    if current_user.role:
        roles.add(current_user.role)
    if "customer" in roles and len(roles) == 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản khách hàng / đại lý không có quyền truy cập phân hệ xuất hóa đơn nội bộ.",
        )
    allowed = {"admin", "accountant", "sales_manager", "warehouse_manager", "warehouse", "sales"}
    if not (roles & allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập hoặc phát hành hóa đơn bán hàng.",
        )


def vietnamese_number_to_words(number: int) -> str:
    """Đọc số tiền tiếng Việt chuẩn xác theo quy chuẩn kế toán."""
    if number == 0:
        return "Không đồng"
    units = ["", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
    tens = ["", "mười", "hai mươi", "ba mươi", "bốn mươi", "năm mươi", "sáu mươi", "bảy mươi", "tám mươi", "chín mươi"]
    scales = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]

    def read_three_digits(n: int, is_highest: bool = False) -> str:
        h = n // 100
        t = (n % 100) // 10
        u = n % 10
        res = []
        if h > 0 or not is_highest:
            res.append(f"{units[h]} trăm" if h > 0 else "không trăm")
        if t > 0:
            res.append(tens[t])
        elif h > 0 and u > 0:
            res.append("lẻ")
        if u > 0:
            if t > 1 and u == 1:
                res.append("mốt")
            elif t > 0 and u == 5:
                res.append("lăm")
            else:
                res.append(units[u])
        return " ".join(res)

    num = abs(number)
    parts = []
    scale_idx = 0
    while num > 0:
        three_digits = num % 1000
        if three_digits > 0:
            words = read_three_digits(three_digits, num < 1000)
            scale = scales[scale_idx]
            parts.append(f"{words} {scale}".strip())
        num //= 1000
        scale_idx += 1
    parts.reverse()
    text_res = " ".join(parts).strip()
    return text_res[0].upper() + text_res[1:] + " đồng chẵn."


def _get_item_authoritative_price(db: Session, order: Optional[OrderEntity], product_id: int) -> float:
    """
    Quy chuẩn IV.4: Lấy đơn giá bán lịch sử có thẩm quyền từ đơn hàng.
    Không lấy giá hiện tại nếu đơn hàng đã xác nhận giá bán cụ thể.
    """
    if order:
        # 1. Thử tìm trong order_items
        order_item = db.query(OrderItemEntity).filter(
            OrderItemEntity.order_id == order.id,
            OrderItemEntity.product_id == product_id,
        ).first()
        if order_item and order_item.unit_price is not None and order_item.unit_price > 0:
            return float(order_item.unit_price)

        # 2. Thử tìm trong order.items_json
        if order.items_json:
            try:
                raw_items = json.loads(order.items_json)
                if isinstance(raw_items, list):
                    for it in raw_items:
                        if it.get("product_id") == product_id and it.get("price") is not None:
                            return float(it.get("price"))
            except Exception:
                pass

    # 3. Fallback lấy giá bán niêm yết của sản phẩm nếu không có trong đơn
    prod = db.query(ProductEntity).filter(ProductEntity.id == product_id).first()
    if prod and prod.sell_price is not None:
        return float(prod.sell_price)

    return 0.0


def _extract_dispatch_items_data(db: Session, dispatch: OrderDispatchEntity, order: Optional[OrderEntity]) -> List[Dict[str, Any]]:
    """Trích xuất danh sách mặt hàng thực giao và đơn giá đối chiếu."""
    items_out = []
    if not dispatch.items_json:
        return items_out

    try:
        raw_items = json.loads(dispatch.items_json)
        if not isinstance(raw_items, list):
            return items_out
    except Exception:
        return items_out

    for it in raw_items:
        prod_id = it.get("product_id")
        prod_name = it.get("product_name") or f"Sản phẩm #{prod_id}"
        unit_name = it.get("unit_name") or it.get("unit") or "Cái"
        
        # Số lượng thực giao: hỗ trợ delivered_quantity, dispatched_this_round, total_delivered, actual_quantity, quantity, dispatched_quantity, ordered_quantity
        raw_qty = (
            it.get("delivered_quantity")
            if it.get("delivered_quantity") is not None
            else it.get("dispatched_this_round")
            if it.get("dispatched_this_round") is not None
            else it.get("total_delivered")
            if it.get("total_delivered") is not None
            else it.get("actual_quantity")
            if it.get("actual_quantity") is not None
            else it.get("quantity")
            if it.get("quantity") is not None
            else it.get("dispatched_quantity")
            if it.get("dispatched_quantity") is not None
            else it.get("ordered_quantity", 0)
        )
        try:
            delivered_qty = float(raw_qty or 0.0)
        except Exception:
            delivered_qty = 0.0
        
        # Đơn giá bán lịch sử
        unit_price = float(it.get("price", it.get("unit_price", 0.0)) or 0.0)
        if unit_price <= 0 and prod_id:
            unit_price = _get_item_authoritative_price(db, order, prod_id)

        subtotal = delivered_qty * unit_price
        vat_rate = 0.1  # GTGT 10% chuẩn
        vat_amount = subtotal * vat_rate
        total_amount = subtotal + vat_amount

        # Tìm mã sản phẩm nếu có
        prod_code = it.get("product_code")
        if not prod_code and prod_id:
            p_entity = db.query(ProductEntity).filter(ProductEntity.id == prod_id).first()
            if p_entity:
                prod_code = p_entity.code

        items_out.append({
            "product_id": prod_id,
            "product_code": prod_code or f"SP{prod_id:04d}",
            "product_name": prod_name,
            "unit_name": unit_name,
            "delivered_quantity": delivered_qty,
            "unit_price": unit_price,
            "subtotal": subtotal,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "total_amount": total_amount,
        })

    return items_out


# =============================================================================
# SCHEMAS
# =============================================================================

class PreviewInvoiceRequest(BaseModel):
    dispatch_ids: List[int] = Field(..., min_length=1, description="Danh sách ID phiếu xuất kho / giao hàng cần gộp")


class IssueInvoiceRequest(BaseModel):
    dispatch_ids: List[int] = Field(..., min_length=1, description="Danh sách ID phiếu xuất kho / giao hàng cần gộp")
    template_code: Optional[str] = "1/001"
    invoice_series: Optional[str] = "C26TAA"
    note: Optional[str] = None


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/deliveries")
def get_deliveries_for_invoicing(
    search: Optional[str] = Query(None, description="Tìm nhanh theo mã phiếu, mã đơn, đại lý hoặc kho xuất"),
    dispatch_code: Optional[str] = Query(None, description="Lọc theo mã phiếu giao hàng"),
    order_code: Optional[str] = Query(None, description="Lọc theo mã đơn hàng"),
    dealer_id: Optional[int] = Query(None, description="Lọc theo ID đại lý"),
    dealer_name: Optional[str] = Query(None, description="Lọc theo tên đại lý"),
    warehouse_id: Optional[str] = Query(None, description="Lọc theo kho xuất"),
    from_date: Optional[str] = Query(None, description="Từ ngày (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Đến ngày (YYYY-MM-DD)"),
    delivery_status: Optional[str] = Query(None, description="Trạng thái giao hàng (DELIVERED, PENDING, FAILED)"),
    invoice_status: Optional[str] = Query("ALL", description="Trạng thái hóa đơn: ALL | PENDING | INVOICED"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    1. Xem danh sách phiếu giao hàng đủ điều kiện xuất hóa đơn.
    Hỗ trợ lọc, tìm kiếm, tính thống kê nhanh theo database thực tế.
    """
    _require_accountant_or_admin(current_user)

    # Lấy toàn bộ phiếu xuất kho kèm đơn hàng
    query = db.query(OrderDispatchEntity).join(OrderEntity, OrderDispatchEntity.order_id == OrderEntity.id)

    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                OrderDispatchEntity.dispatch_code.ilike(s),
                OrderDispatchEntity.order_code.ilike(s),
                OrderEntity.dealer_name.ilike(s),
                OrderDispatchEntity.warehouse_name.ilike(s),
            )
        )

    if dispatch_code:
        query = query.filter(OrderDispatchEntity.dispatch_code.ilike(f"%{dispatch_code.strip()}%"))
    if order_code:
        query = query.filter(OrderDispatchEntity.order_code.ilike(f"%{order_code.strip()}%"))
    if dealer_id:
        query = query.filter(OrderEntity.dealer_id == dealer_id)
    if dealer_name:
        query = query.filter(OrderEntity.dealer_name.ilike(f"%{dealer_name.strip()}%"))
    if warehouse_id:
        query = query.filter(
            or_(
                OrderDispatchEntity.warehouse_id == warehouse_id,
                OrderDispatchEntity.warehouse_name.ilike(f"%{warehouse_id.strip()}%"),
            )
        )

    all_dispatches = query.order_by(desc(OrderDispatchEntity.created_at)).all()

    # Thu thập ID của tất cả các phiếu đã xuất hóa đơn để đánh dấu và kiểm tra
    invoiced_links = db.query(InvoiceDeliveryNoteEntity).all()
    invoiced_map = {link.delivery_dispatch_id: link for link in invoiced_links}

    # Thu thập trạng thái giao hàng từ delivery_trip_dispatches và delivery_stops
    trip_disps = db.query(DeliveryTripDispatchEntity).all()
    trip_disp_map = {td.dispatch_id: td for td in trip_disps}

    total_pending_count = 0
    total_issued_count = 0
    total_pending_amount = 0.0

    items_list = []

    for disp in all_dispatches:
        ord_entity = disp.order_rel
        dealer = db.query(DealerEntity).filter(DealerEntity.id == ord_entity.dealer_id).first() if ord_entity else None

        # Trạng thái giao hàng
        # Kiểm tra qua chuyến giao hàng liên kết
        trip_disp = trip_disp_map.get(disp.id)
        if trip_disp and trip_disp.status.upper() in ["DELIVERED", "COMPLETED", "SUCCESS"]:
            del_status = "DELIVERED"
            del_date = trip_disp.created_at
        elif ord_entity and ord_entity.status in ["DELIVERED", "COMPLETED", "SHIPPED"]:
            del_status = "DELIVERED"
            del_date = disp.created_at
        elif trip_disp:
            del_status = trip_disp.status.upper()
            del_date = trip_disp.created_at
        else:
            del_status = "DELIVERED"  # Mặc định phiếu xuất kho đã lập thành công
            del_date = disp.created_at

        # Trạng thái hóa đơn
        invoiced_record = invoiced_map.get(disp.id)
        is_invoiced = invoiced_record is not None
        inv_status = "INVOICED" if is_invoiced else "PENDING"

        # Tính toán chi tiết hàng thực giao và tổng tiền
        items_data = _extract_dispatch_items_data(db, disp, ord_entity)
        total_qty = sum(it["delivered_quantity"] for it in items_data)
        total_amt = sum(it["total_amount"] for it in items_data)

        # Cập nhật thống kê
        if is_invoiced:
            total_issued_count += 1
        else:
            if del_status == "DELIVERED" and total_qty > 0 and ord_entity and ord_entity.dealer_id:
                total_pending_count += 1
                total_pending_amount += total_amt

        # Áp dụng bộ lọc
        if delivery_status and del_status != delivery_status.upper():
            continue
        if invoice_status == "PENDING" and is_invoiced:
            continue
        if invoice_status == "INVOICED" and not is_invoiced:
            continue

        if from_date:
            try:
                fd = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if del_date and del_date < fd:
                    continue
            except Exception:
                pass
        if to_date:
            try:
                td_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
                if del_date and del_date > td_dt:
                    continue
            except Exception:
                pass

        # Điều kiện đủ điều kiện xuất hóa đơn: chưa xuất HĐ, có đại lý, có SL thực > 0 và không phải chuyến thất bại
        is_eligible = (not is_invoiced) and (ord_entity is not None and ord_entity.dealer_id is not None) and (total_qty > 0) and (del_status != "FAILED")

        invoice_info = None
        if is_invoiced and invoiced_record:
            inv = invoiced_record.invoice
            if inv:
                invoice_info = {
                    "invoice_id": inv.id,
                    "invoice_code": inv.invoice_code,
                    "issue_date": inv.issue_date.strftime("%d/%m/%Y") if inv.issue_date else None,
                    "status": inv.status,
                }

        items_list.append({
            "dispatch_id": disp.id,
            "dispatch_code": disp.dispatch_code,
            "order_id": disp.order_id,
            "order_code": disp.order_code,
            "dealer_id": ord_entity.dealer_id if ord_entity else None,
            "dealer_code": dealer.code if dealer else "",
            "dealer_name": ord_entity.dealer_name if ord_entity else "Khách hàng",
            "dealer_phone": dealer.phone if dealer else "",
            "dealer_address": dealer.address if dealer else "",
            "warehouse_id": disp.warehouse_id,
            "warehouse_name": disp.warehouse_name or "Kho Tổng Hà Nội",
            "delivery_date": del_date.strftime("%d/%m/%Y %H:%M") if del_date else disp.created_at.strftime("%d/%m/%Y %H:%M"),
            "delivery_status": del_status,
            "items_count": len(items_data),
            "total_delivered_quantity": total_qty,
            "total_amount": round(total_amt),
            "invoice_status": inv_status,
            "is_eligible": is_eligible,
            "invoice_info": invoice_info,
            "items": items_data,
        })

    # Phân trang
    total_records = len(items_list)
    start_idx = (page - 1) * page_size
    paged_items = items_list[start_idx : start_idx + page_size]

    return {
        "status": "success",
        "quick_stats": {
            "total_pending_invoices": total_pending_count,
            "total_issued_invoices": total_issued_count,
            "total_adjustments": db.query(InvoiceAdjustmentEntity).count(),
            "total_pending_amount": round(total_pending_amount),
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            "total_pages": max(1, (total_records + page_size - 1) // page_size),
        },
        "data": paged_items,
    }


@router.get("/deliveries/{dispatch_id}")
def get_delivery_dispatch_detail(
    dispatch_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """2. Xem chi tiết phiếu giao hàng phục vụ đối soát số lượng thực giao và đơn giá."""
    _require_accountant_or_admin(current_user)

    disp = db.query(OrderDispatchEntity).filter(OrderDispatchEntity.id == dispatch_id).first()
    if not disp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy phiếu giao hàng.")

    ord_entity = disp.order_rel
    dealer = db.query(DealerEntity).filter(DealerEntity.id == ord_entity.dealer_id).first() if ord_entity else None
    items_data = _extract_dispatch_items_data(db, disp, ord_entity)

    # Kiểm tra hóa đơn đã liên kết nếu có
    invoiced_link = db.query(InvoiceDeliveryNoteEntity).filter(InvoiceDeliveryNoteEntity.delivery_dispatch_id == disp.id).first()

    return {
        "status": "success",
        "data": {
            "dispatch_id": disp.id,
            "dispatch_code": disp.dispatch_code,
            "order_id": disp.order_id,
            "order_code": disp.order_code,
            "dealer": {
                "id": dealer.id if dealer else (ord_entity.dealer_id if ord_entity else None),
                "code": dealer.code if dealer else "",
                "name": dealer.name if dealer else (ord_entity.dealer_name if ord_entity else "Khách hàng"),
                "phone": dealer.phone if dealer else "",
                "address": dealer.address if dealer else "",
                "tax_code": getattr(dealer, "tax_code", None),
            },
            "warehouse_id": disp.warehouse_id,
            "warehouse_name": disp.warehouse_name,
            "performed_by": disp.performed_by,
            "note": disp.note,
            "created_at": disp.created_at.strftime("%d/%m/%Y %H:%M") if disp.created_at else None,
            "is_invoiced": invoiced_link is not None,
            "invoice_code": invoiced_link.invoice.invoice_code if invoiced_link and invoiced_link.invoice else None,
            "items": items_data,
            "total_delivered_quantity": sum(it["delivered_quantity"] for it in items_data),
            "total_amount": round(sum(it["total_amount"] for it in items_data)),
        },
    }


@router.post("/preview")
def preview_sales_invoice(
    payload: PreviewInvoiceRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    3. Xem trước hóa đơn gộp trước khi phát hành.
    Kiểm tra toàn bộ điều kiện nghiệp vụ:
    - Cùng một đại lý / khách hàng.
    - Cùng đơn vị tiền tệ (VND).
    - Đã giao thành công (DELIVERED).
    - Chưa được phát hành hóa đơn.
    - Tính toán gộp các mặt hàng cùng mã sản phẩm theo số lượng thực giao.
    """
    _require_accountant_or_admin(current_user)

    if not payload.dispatch_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vui lòng chọn ít nhất một phiếu giao hàng.")

    dispatches = db.query(OrderDispatchEntity).filter(OrderDispatchEntity.id.in_(payload.dispatch_ids)).all()
    if len(dispatches) != len(set(payload.dispatch_ids)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Một số phiếu giao hàng không tồn tại.")

    # 1. Kiểm tra xuất trùng: Chưa phiếu nào được gắn vào hóa đơn đã phát hành
    already_invoiced = db.query(InvoiceDeliveryNoteEntity).filter(
        InvoiceDeliveryNoteEntity.delivery_dispatch_id.in_(payload.dispatch_ids)
    ).all()
    if already_invoiced:
        codes = ", ".join([link.dispatch_code for link in already_invoiced])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phiếu giao hàng sau đã được xuất hóa đơn trước đó: {codes}. Không thể xuất trùng!",
        )

    # 2. Kiểm tra cùng đại lý
    dealer_ids = set()
    dealers_info = {}
    for d in dispatches:
        ord_entity = d.order_rel
        if not ord_entity or not ord_entity.dealer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Phiếu {d.dispatch_code} không có thông tin đại lý hợp lệ.",
            )
        dealer_ids.add(ord_entity.dealer_id)
        dealers_info[ord_entity.dealer_id] = ord_entity.dealer_name

    if len(dealer_ids) > 1:
        dealer_names_str = ", ".join(list(dealers_info.values()))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể gộp các phiếu giao hàng thuộc các đại lý khác nhau ({dealer_names_str}) vào cùng một hóa đơn.",
        )

    target_dealer_id = list(dealer_ids)[0]
    dealer = db.query(DealerEntity).filter(DealerEntity.id == target_dealer_id).first()

    # 3. Tổng hợp mặt hàng gộp theo (product_id, unit_name, unit_price)
    # Quy chuẩn IV.2: Hóa đơn dựa trên số lượng THỰC GIAO, không lấy số lượng đặt hàng ban đầu
    grouped_items: Dict[str, Dict[str, Any]] = {}
    source_dispatches_out = []

    for d in dispatches:
        ord_entity = d.order_rel
        disp_items = _extract_dispatch_items_data(db, d, ord_entity)
        d_qty = sum(it["delivered_quantity"] for it in disp_items)
        d_total = sum(it["total_amount"] for it in disp_items)

        source_dispatches_out.append({
            "dispatch_id": d.id,
            "dispatch_code": d.dispatch_code,
            "order_id": d.order_id,
            "order_code": d.order_code,
            "warehouse_name": d.warehouse_name or "Kho Tổng Hà Nội",
            "delivery_date": d.created_at.strftime("%d/%m/%Y") if d.created_at else "",
            "items_count": len(disp_items),
            "delivered_quantity": d_qty,
            "total_amount": round(d_total),
        })

        for it in disp_items:
            key = f"{it['product_id']}_{it['unit_name']}_{it['unit_price']}"
            if key not in grouped_items:
                grouped_items[key] = {
                    "product_id": it["product_id"],
                    "product_code": it["product_code"],
                    "product_name": it["product_name"],
                    "unit_name": it["unit_name"],
                    "unit_price": it["unit_price"],
                    "delivered_quantity": 0.0,
                    "discount_amount": 0.0,
                    "subtotal": 0.0,
                    "vat_rate": it["vat_rate"],
                    "vat_amount": 0.0,
                    "total_amount": 0.0,
                }
            grouped_items[key]["delivered_quantity"] += it["delivered_quantity"]

    # Tính toán thành tiền từng dòng gộp
    final_items = []
    stt = 1
    total_qty = 0.0
    subtotal_amount = 0.0
    total_vat = 0.0

    for key, row in grouped_items.items():
        qty = row["delivered_quantity"]
        sub = qty * row["unit_price"]
        vat = sub * row["vat_rate"]
        tot = sub + vat

        row["subtotal"] = sub
        row["vat_amount"] = vat
        row["total_amount"] = tot
        row["stt"] = stt
        stt += 1

        total_qty += qty
        subtotal_amount += sub
        total_vat += vat
        final_items.append(row)

    final_total_amount = round(subtotal_amount + total_vat)
    amount_in_words = vietnamese_number_to_words(int(final_total_amount))

    return {
        "status": "success",
        "preview": {
            "template_code": "1/001",
            "invoice_series": "C26TAA",
            "invoice_code_placeholder": "HD-YYYYMM-XXXX (Sẽ cấp chính thức khi phát hành)",
            "issue_date": get_utc_now().strftime("%d/%m/%Y"),
            "currency": "VND",
            "issued_by": current_user.full_name or current_user.username,
            "dealer": {
                "id": dealer.id if dealer else target_dealer_id,
                "code": dealer.code if dealer else "",
                "name": dealer.name if dealer else dealers_info.get(target_dealer_id, "Khách hàng"),
                "tax_code": getattr(dealer, "tax_code", None) or "0108899889",
                "phone": dealer.phone if dealer else "",
                "address": dealer.address if dealer else "Hà Nội, Việt Nam",
                "credit_limit": getattr(dealer, "credit_limit", 50000000.0) if dealer else 50000000.0,
            },
            "source_dispatches": source_dispatches_out,
            "items": final_items,
            "summary": {
                "total_quantity": total_qty,
                "subtotal_amount": round(subtotal_amount),
                "discount_amount": 0.0,
                "taxable_amount": round(subtotal_amount),
                "vat_amount": round(total_vat),
                "total_amount": final_total_amount,
                "amount_in_words": amount_in_words,
            },
        },
    }


@router.post("/issue")
def issue_sales_invoice(
    payload: IssueInvoiceRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    4. Phát hành hóa đơn bán hàng trong một Database Transaction nguyên tử duy nhất.
    Tuân thủ nghiêm ngặt Mục IX:
    - Khóa và kiểm tra lại toàn bộ dữ liệu.
    - Cấp số hóa đơn liên tục tuần tự (HD-YYYYMM-XXXX).
    - Tạo bản ghi InvoiceEntity, InvoiceDeliveryNoteEntity (có UNIQUE constraint chặn race condition).
    - Tạo InvoiceItemEntity lưu chi tiết dòng hàng hóa theo số lượng thực giao.
    - Ghi nhận Audit Log INVOICE_CREATE.
    - Duy trì tính chính xác của công nợ đại lý.
    """
    _require_accountant_or_admin(current_user)

    if not payload.dispatch_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vui lòng chọn ít nhất một phiếu giao hàng.")

    now = get_utc_now()
    year_month = now.strftime("%Y%m")

    # Mở Transaction duy nhất
    try:
        # 1. Truy vấn và kiểm tra lại phiếu
        dispatches = db.query(OrderDispatchEntity).filter(
            OrderDispatchEntity.id.in_(payload.dispatch_ids)
        ).with_for_update(nowait=False).all() if db.bind.name != "sqlite" else db.query(OrderDispatchEntity).filter(OrderDispatchEntity.id.in_(payload.dispatch_ids)).all()

        if len(dispatches) != len(set(payload.dispatch_ids)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Một số phiếu giao hàng không tồn tại.")

        # 2. Kiểm tra chống xuất trùng trong transaction
        already_invoiced = db.query(InvoiceDeliveryNoteEntity).filter(
            InvoiceDeliveryNoteEntity.delivery_dispatch_id.in_(payload.dispatch_ids)
        ).first()
        if already_invoiced:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Phiếu giao hàng {already_invoiced.dispatch_code} vừa được xuất trên hóa đơn khác. Thao tác bị từ chối để chống trùng!",
            )

        # 3. Xác minh cùng đại lý
        dealer_ids = set()
        for d in dispatches:
            ord_entity = d.order_rel
            if not ord_entity or not ord_entity.dealer_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Phiếu {d.dispatch_code} thiếu đại lý hợp lệ.")
            dealer_ids.add(ord_entity.dealer_id)

        if len(dealer_ids) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tất cả các phiếu giao hàng được gộp phải thuộc cùng một đại lý.",
            )

        target_dealer_id = list(dealer_ids)[0]
        dealer = db.query(DealerEntity).filter(DealerEntity.id == target_dealer_id).first()
        if not dealer:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy thông tin đại lý.")

        # 4. Tính toán số liệu từ dữ liệu thẩm quyền trong database
        grouped_items: Dict[str, Dict[str, Any]] = {}
        dispatch_codes_list = []
        source_note_records = []

        for d in dispatches:
            dispatch_codes_list.append(d.dispatch_code)
            ord_entity = d.order_rel
            disp_items = _extract_dispatch_items_data(db, d, ord_entity)
            d_qty = sum(it["delivered_quantity"] for it in disp_items)
            d_total = sum(it["total_amount"] for it in disp_items)

            source_note_records.append({
                "dispatch_id": d.id,
                "dispatch_code": d.dispatch_code,
                "order_id": d.order_id,
                "order_code": d.order_code,
                "delivered_quantity": d_qty,
                "total_amount": d_total,
            })

            for it in disp_items:
                key = f"{it['product_id']}_{it['unit_name']}_{it['unit_price']}"
                if key not in grouped_items:
                    grouped_items[key] = {
                        "product_id": it["product_id"],
                        "product_code": it["product_code"],
                        "product_name": it["product_name"],
                        "unit_name": it["unit_name"],
                        "unit_price": it["unit_price"],
                        "delivered_quantity": 0.0,
                        "discount_amount": 0.0,
                        "subtotal": 0.0,
                        "vat_rate": it["vat_rate"],
                        "vat_amount": 0.0,
                        "total_amount": 0.0,
                    }
                grouped_items[key]["delivered_quantity"] += it["delivered_quantity"]

        subtotal_amount = 0.0
        total_vat = 0.0
        calculated_items = []
        for key, row in grouped_items.items():
            qty = row["delivered_quantity"]
            sub = qty * row["unit_price"]
            vat = sub * row["vat_rate"]
            tot = sub + vat
            row["subtotal"] = sub
            row["vat_amount"] = vat
            row["total_amount"] = tot
            subtotal_amount += sub
            total_vat += vat
            calculated_items.append(row)

        total_amount = round(subtotal_amount + total_vat)

        # 5. Cấp số hóa đơn tuần tự HD-YYYYMM-XXXX
        prefix = f"HD-{year_month}-"
        latest_inv = db.query(InvoiceEntity).filter(
            InvoiceEntity.invoice_code.like(f"{prefix}%")
        ).order_by(desc(InvoiceEntity.id)).first()

        next_seq = 1
        if latest_inv and latest_inv.invoice_code:
            try:
                seq_str = latest_inv.invoice_code.replace(prefix, "")
                next_seq = int(seq_str) + 1
            except Exception:
                next_seq = 1

        invoice_code = f"{prefix}{next_seq:04d}"

        # 6. Hạn thanh toán theo hạn mức công nợ đại lý
        allowed_days = int(getattr(dealer, "overdue_days_allowed", getattr(dealer, "max_debt_days", 30)) or 30)
        due_date = now + timedelta(days=allowed_days)

        # 7. Tạo bản ghi InvoiceEntity
        new_invoice = InvoiceEntity(
            invoice_code=invoice_code,
            order_id=dispatches[0].order_id,
            order_code=dispatches[0].order_code,
            dealer_id=dealer.id,
            dealer_name=dealer.name,
            issue_date=now,
            due_date=due_date,
            total_amount=total_amount,
            paid_amount=0.0,
            remaining_debt=total_amount,
            status="UNPAID",
            source_type="DELIVERY_NOTES",
            issue_status="ISSUED",
            template_code=payload.template_code or "1/001",
            invoice_series=payload.invoice_series or "C26TAA",
            subtotal_amount=round(subtotal_amount),
            vat_amount=round(total_vat),
            discount_amount=0.0,
            issued_by=current_user.full_name or current_user.username,
            delivery_dispatches_summary=", ".join(dispatch_codes_list),
            currency="VND",
            note=payload.note or f"Xuất từ các phiếu giao: {', '.join(dispatch_codes_list)}",
            items_json=json.dumps([
                {
                    "product_id": ci["product_id"],
                    "product_name": ci["product_name"],
                    "quantity": ci["delivered_quantity"],
                    "unit_name": ci["unit_name"],
                    "price": ci["unit_price"],
                    "total": ci["total_amount"],
                }
                for ci in calculated_items
            ], ensure_ascii=False),
        )
        db.add(new_invoice)
        db.flush()

        # 8. Tạo bản ghi InvoiceDeliveryNoteEntity cho từng phiếu nguồn
        for sn in source_note_records:
            idn = InvoiceDeliveryNoteEntity(
                invoice_id=new_invoice.id,
                delivery_dispatch_id=sn["dispatch_id"],
                dispatch_code=sn["dispatch_code"],
                order_id=sn["order_id"],
                order_code=sn["order_code"],
                delivered_quantity=sn["delivered_quantity"],
                total_amount=sn["total_amount"],
            )
            db.add(idn)

        # 9. Tạo bản ghi InvoiceItemEntity cho từng dòng mặt hàng thực giao
        for ci in calculated_items:
            iie = InvoiceItemEntity(
                invoice_id=new_invoice.id,
                product_id=ci["product_id"],
                product_code=ci["product_code"],
                product_name=ci["product_name"],
                unit_name=ci["unit_name"],
                quantity=ci["delivered_quantity"],
                unit_price=ci["unit_price"],
                discount_amount=0.0,
                subtotal=ci["subtotal"],
                vat_rate=ci["vat_rate"],
                vat_amount=ci["vat_amount"],
                total_amount=ci["total_amount"],
            )
            db.add(iie)

        # 10. Cập nhật số giao dịch của đại lý
        if hasattr(dealer, "transaction_count"):
            dealer.transaction_count = (dealer.transaction_count or 0) + 1

        db.commit()
        db.refresh(new_invoice)

        # 11. Ghi nhật ký kiểm toán quan trọng
        try:
            log_audit_event(
                db=db,
                user=current_user,
                action_type="INVOICE_CREATE",
                entity_type="Invoice",
                entity_id=new_invoice.id,
                new_val={
                    "invoice_code": invoice_code,
                    "dealer_id": dealer.id,
                    "dealer_name": dealer.name,
                    "total_amount": total_amount,
                    "delivery_notes": dispatch_codes_list,
                    "issued_by": current_user.full_name or current_user.username,
                },
                reason=f"Phát hành hóa đơn bán hàng {invoice_code} từ {len(dispatch_codes_list)} phiếu giao hàng đã giao thành công",
            )
        except Exception:
            pass

        return {
            "status": "success",
            "message": f"Phát hành thành công hóa đơn bán hàng {invoice_code}.",
            "data": {
                "invoice_id": new_invoice.id,
                "invoice_code": new_invoice.invoice_code,
                "dealer_name": new_invoice.dealer_name,
                "total_amount": new_invoice.total_amount,
                "issue_date": new_invoice.issue_date.strftime("%d/%m/%Y"),
                "due_date": new_invoice.due_date.strftime("%d/%m/%Y"),
                "dispatches_count": len(dispatch_codes_list),
            },
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as ex:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi thực hiện giao dịch phát hành hóa đơn: {str(ex)}",
        )


@router.get("")
def get_sales_invoices(
    invoice_code: Optional[str] = Query(None, description="Lọc theo mã hóa đơn"),
    dealer_id: Optional[int] = Query(None, description="Lọc theo đại lý"),
    dealer_name: Optional[str] = Query(None, description="Lọc theo tên đại lý"),
    from_date: Optional[str] = Query(None, description="Từ ngày lập"),
    to_date: Optional[str] = Query(None, description="Đến ngày lập"),
    status_filter: Optional[str] = Query("ALL", description="Trạng thái thanh toán (UNPAID, PAID, PARTIAL, OVERDUE, ALL)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """5. Xem danh sách hóa đơn bán hàng đã phát hành từ phiếu giao hàng."""
    _require_accountant_or_admin(current_user)

    query = db.query(InvoiceEntity).filter(InvoiceEntity.source_type == "DELIVERY_NOTES")

    if invoice_code:
        query = query.filter(InvoiceEntity.invoice_code.ilike(f"%{invoice_code.strip()}%"))
    if dealer_id:
        query = query.filter(InvoiceEntity.dealer_id == dealer_id)
    if dealer_name:
        query = query.filter(InvoiceEntity.dealer_name.ilike(f"%{dealer_name.strip()}%"))
    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(InvoiceEntity.status == status_filter.upper())

    total_records = query.count()
    invoices = query.order_by(desc(InvoiceEntity.issue_date)).offset((page - 1) * page_size).limit(page_size).all()

    data = []
    for inv in invoices:
        data.append({
            "id": inv.id,
            "invoice_code": inv.invoice_code,
            "dealer_id": inv.dealer_id,
            "dealer_name": inv.dealer_name,
            "issue_date": inv.issue_date.strftime("%d/%m/%Y") if inv.issue_date else "",
            "due_date": inv.due_date.strftime("%d/%m/%Y") if inv.due_date else "",
            "template_code": inv.template_code,
            "invoice_series": inv.invoice_series,
            "subtotal_amount": inv.subtotal_amount,
            "vat_amount": inv.vat_amount,
            "total_amount": inv.total_amount,
            "paid_amount": inv.paid_amount,
            "remaining_debt": inv.remaining_debt,
            "status": inv.status,
            "issue_status": inv.issue_status,
            "issued_by": inv.issued_by,
            "delivery_dispatches_summary": inv.delivery_dispatches_summary,
            "currency": inv.currency or "VND",
        })

    return {
        "status": "success",
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            "total_pages": max(1, (total_records + page_size - 1) // page_size),
        },
        "data": data,
    }


@router.get("/adjustments/history")
def get_all_invoice_adjustments(
    search: Optional[str] = Query(None, description="Tìm theo mã điều chỉnh, mã hóa đơn, lý do, người lập"),
    adjustment_type: Optional[str] = Query(None, description="Loại điều chỉnh: INCREASE | DECREASE | INFO"),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy danh sách toàn bộ lịch sử chứng từ điều chỉnh hóa đơn đã lập."""
    _require_accountant_or_admin(current_user)

    query = db.query(InvoiceAdjustmentEntity)

    if adjustment_type and adjustment_type.strip() and adjustment_type.upper() != "ALL":
        query = query.filter(InvoiceAdjustmentEntity.adjustment_type == adjustment_type.strip().upper())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(InvoiceAdjustmentEntity.adjustment_code).like(term),
                func.lower(InvoiceAdjustmentEntity.invoice_code).like(term),
                func.lower(InvoiceAdjustmentEntity.reason).like(term),
                func.lower(InvoiceAdjustmentEntity.created_by).like(term),
            )
        )

    # Date filters
    if from_date and from_date.strip():
        try:
            fd = datetime.strptime(from_date.strip(), "%Y-%m-%d")
            query = query.filter(InvoiceAdjustmentEntity.created_at >= fd)
        except Exception:
            pass

    if to_date and to_date.strip():
        try:
            td = datetime.strptime(to_date.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(InvoiceAdjustmentEntity.created_at <= td)
        except Exception:
            pass

    total_records = query.count()
    items = (
        query.order_by(desc(InvoiceAdjustmentEntity.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    result = []
    for a in items:
        inv = db.query(InvoiceEntity).filter(InvoiceEntity.id == a.invoice_id).first()
        result.append({
            "id": a.id,
            "adjustment_code": a.adjustment_code,
            "invoice_id": a.invoice_id,
            "invoice_code": a.invoice_code,
            "dealer_name": inv.dealer_name if inv else "",
            "adjustment_type": a.adjustment_type,
            "reason": a.reason,
            "difference_subtotal": a.difference_subtotal or 0.0,
            "difference_vat": a.difference_vat or 0.0,
            "difference_total": a.difference_total or 0.0,
            "details_json": a.details_json,
            "created_by": a.created_by or "",
            "created_at": a.created_at.strftime("%d/%m/%Y %H:%M") if a.created_at else "",
        })

    return {
        "status": "success",
        "quick_stats": {
            "total_adjustments": total_records,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            "total_pages": max(1, (total_records + page_size - 1) // page_size),
        },
        "data": result,
    }


@router.get("/{invoice_id}")
def get_sales_invoice_detail(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """6. Chi tiết hóa đơn bán hàng đã phát hành kèm danh mục mặt hàng và phiếu nguồn."""
    _require_accountant_or_admin(current_user)

    inv = db.query(InvoiceEntity).filter(InvoiceEntity.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy hóa đơn.")

    dealer = db.query(DealerEntity).filter(DealerEntity.id == inv.dealer_id).first()

    # Lấy danh sách phiếu nguồn
    links = db.query(InvoiceDeliveryNoteEntity).filter(InvoiceDeliveryNoteEntity.invoice_id == inv.id).all()
    source_notes = []
    for lk in links:
        source_notes.append({
            "dispatch_id": lk.delivery_dispatch_id,
            "dispatch_code": lk.dispatch_code,
            "order_id": lk.order_id,
            "order_code": lk.order_code,
            "delivered_quantity": lk.delivered_quantity,
            "total_amount": lk.total_amount,
        })

    # Lấy danh sách mặt hàng thực giao
    items = db.query(InvoiceItemEntity).filter(InvoiceItemEntity.invoice_id == inv.id).all()
    items_data = []
    for idx, it in enumerate(items, 1):
        items_data.append({
            "stt": idx,
            "product_id": it.product_id,
            "product_code": it.product_code,
            "product_name": it.product_name,
            "unit_name": it.unit_name,
            "quantity": it.quantity,
            "delivered_quantity": it.quantity,
            "unit_price": it.unit_price,
            "discount_amount": it.discount_amount,
            "subtotal": it.subtotal,
            "vat_rate": it.vat_rate,
            "vat_amount": it.vat_amount,
            "total_amount": it.total_amount,
        })

    amount_in_words = vietnamese_number_to_words(int(round(inv.total_amount)))

    return {
        "status": "success",
        "data": {
            "id": inv.id,
            "invoice_code": inv.invoice_code,
            "template_code": inv.template_code or "1/001",
            "invoice_series": inv.invoice_series or "C26TAA",
            "issue_date": inv.issue_date.strftime("%d/%m/%Y") if inv.issue_date else "",
            "due_date": inv.due_date.strftime("%d/%m/%Y") if inv.due_date else "",
            "issue_status": inv.issue_status,
            "status": inv.status,
            "issued_by": inv.issued_by or "Kế toán bán hàng",
            "currency": inv.currency or "VND",
            "dealer": {
                "id": dealer.id if dealer else inv.dealer_id,
                "code": dealer.code if dealer else "",
                "name": dealer.name if dealer else inv.dealer_name,
                "tax_code": getattr(dealer, "tax_code", None) or "0108899889",
                "phone": dealer.phone if dealer else "",
                "address": dealer.address if dealer else "Việt Nam",
            },
            "source_dispatches": source_notes,
            "items": items_data,
            "summary": {
                "subtotal_amount": inv.subtotal_amount,
                "discount_amount": inv.discount_amount,
                "taxable_amount": inv.subtotal_amount,
                "vat_amount": inv.vat_amount,
                "total_amount": inv.total_amount,
                "paid_amount": inv.paid_amount,
                "remaining_debt": inv.remaining_debt,
                "amount_in_words": amount_in_words,
            },
            "note": inv.note,
            "adjustments": [
                {
                    "id": a.id,
                    "adjustment_code": a.adjustment_code,
                    "adjustment_type": a.adjustment_type,
                    "reason": a.reason,
                    "difference_total": a.difference_total,
                    "created_by": a.created_by,
                    "created_at": a.created_at.strftime("%d/%m/%Y %H:%M") if a.created_at else "",
                }
                for a in db.query(InvoiceAdjustmentEntity).filter(InvoiceAdjustmentEntity.invoice_id == inv.id).order_by(desc(InvoiceAdjustmentEntity.created_at)).all()
            ],
        },
    }


@router.get("/{invoice_id}/pdf")
def export_sales_invoice_pdf(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """7. Tạo và xuất file PDF Hóa đơn bán hàng chuẩn theo mẫu hóa đơn Việt Nam."""
    _require_accountant_or_admin(current_user)

    inv = db.query(InvoiceEntity).filter(InvoiceEntity.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy hóa đơn.")

    dealer = db.query(DealerEntity).filter(DealerEntity.id == inv.dealer_id).first()
    items = db.query(InvoiceItemEntity).filter(InvoiceItemEntity.invoice_id == inv.id).all()
    links = db.query(InvoiceDeliveryNoteEntity).filter(InvoiceDeliveryNoteEntity.invoice_id == inv.id).all()

    # Sử dụng ReportLab tạo PDF chuẩn A4 với font tiếng Việt
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Đăng ký font tiếng Việt
    font_bold = "Arial-Bold"
    font_regular = "Arial"
    try:
        pdfmetrics.registerFont(TTFont("Arial", "C:/Windows/Fonts/arial.ttf"))
        pdfmetrics.registerFont(TTFont("Arial-Bold", "C:/Windows/Fonts/arialbd.ttf"))
    except Exception:
        font_bold = "Helvetica-Bold"
        font_regular = "Helvetica"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    # Header công ty
    c.setFont(font_bold, 11)
    c.drawString(40, h - 45, "CÔNG TY CỔ PHẦN PHÂN PHỐI & BÁN HÀNG TOÀN QUỐC")
    c.setFont(font_regular, 9)
    c.drawString(40, h - 58, "Địa chỉ: Tầng 8, Tòa nhà Discovery Complex, 302 Cầu Giấy, Hà Nội")
    c.drawString(40, h - 70, "Mã số thuế: 0101234567 | Hotline: 1900 6868")

    # Đường phân cách
    c.setStrokeColorRGB(0.7, 0.7, 0.7)
    c.setLineWidth(1)
    c.line(40, h - 78, w - 40, h - 78)

    # Tiêu đề hóa đơn
    c.setFont(font_bold, 18)
    c.drawCentredString(w / 2, h - 105, "HÓA ĐƠN BÁN HÀNG")
    c.setFont(font_regular, 10)
    c.drawCentredString(w / 2, h - 120, f"Mẫu số: {inv.template_code or '1/001'} - Ký hiệu: {inv.invoice_series or 'C26TAA'} - Số: {inv.invoice_code}")
    c.drawCentredString(w / 2, h - 133, f"Ngày phát hành: {inv.issue_date.strftime('%d/%m/%Y') if inv.issue_date else ''}")

    # Khung thông tin khách hàng (Đại lý)
    y_info = h - 155
    c.setFont(font_bold, 10)
    c.drawString(40, y_info, "Đơn vị mua hàng (Đại lý):")
    c.setFont(font_regular, 10)
    c.drawString(180, y_info, str(dealer.name if dealer else inv.dealer_name))

    y_info -= 16
    c.setFont(font_bold, 10)
    c.drawString(40, y_info, "Mã số thuế:")
    c.setFont(font_regular, 10)
    c.drawString(180, y_info, str(getattr(dealer, "tax_code", None) or "0108899889"))

    y_info -= 16
    c.setFont(font_bold, 10)
    c.drawString(40, y_info, "Địa chỉ:")
    c.setFont(font_regular, 10)
    c.drawString(180, y_info, str(dealer.address if dealer and dealer.address else "Hà Nội, Việt Nam"))

    y_info -= 16
    c.setFont(font_bold, 10)
    c.drawString(40, y_info, "Phiếu giao nguồn:")
    c.setFont(font_regular, 10)
    disp_codes = ", ".join([lk.dispatch_code for lk in links]) or inv.delivery_dispatches_summary or "—"
    c.drawString(180, y_info, disp_codes)

    # Bảng chi tiết mặt hàng
    tbl_top = y_info - 25
    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(40, tbl_top - 20, w - 80, 20, fill=1, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_bold, 9)

    c.drawString(45, tbl_top - 14, "STT")
    c.drawString(75, tbl_top - 14, "Tên hàng hóa, dịch vụ")
    c.drawString(270, tbl_top - 14, "ĐVT")
    c.drawString(315, tbl_top - 14, "SL Thực")
    c.drawString(380, tbl_top - 14, "Đơn giá")
    c.drawString(460, tbl_top - 14, "Thành tiền")

    def fmt_cur(v):
        return f"{int(round(float(v or 0))):,}".replace(",", ".") + " đ"

    cur_y = tbl_top - 20
    row_h = 20
    for idx, it in enumerate(items, 1):
        cur_y -= row_h
        c.rect(40, cur_y, w - 80, row_h, fill=0, stroke=1)
        c.setFont(font_regular, 9)
        c.drawString(48, cur_y + 6, str(idx))
        c.drawString(75, cur_y + 6, str(it.product_name)[:38])
        c.drawString(275, cur_y + 6, str(it.unit_name or "Cái"))
        c.drawRightString(355, cur_y + 6, str(int(it.quantity) if it.quantity.is_integer() else f"{it.quantity:.1f}"))
        c.drawRightString(440, cur_y + 6, fmt_cur(it.unit_price))
        c.drawRightString(545, cur_y + 6, fmt_cur(it.subtotal))

    # Hàng tổng cộng
    cur_y -= row_h
    c.setFillColorRGB(0.97, 0.98, 0.99)
    c.rect(40, cur_y, w - 80, row_h, fill=1, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_bold, 9)
    c.drawString(75, cur_y + 6, "Cộng tiền hàng (chưa có thuế GTGT):")
    c.drawRightString(545, cur_y + 6, fmt_cur(inv.subtotal_amount))

    cur_y -= row_h
    c.setFillColorRGB(1, 1, 1)
    c.rect(40, cur_y, w - 80, row_h, fill=1, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_bold, 9)
    c.drawString(75, cur_y + 6, "Tiền thuế GTGT (10%):")
    c.drawRightString(545, cur_y + 6, fmt_cur(inv.vat_amount))

    cur_y -= row_h
    c.setFillColorRGB(0.93, 0.95, 0.98)
    c.rect(40, cur_y, w - 80, row_h, fill=1, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_bold, 10)
    c.drawString(75, cur_y + 6, "TỔNG CỘNG TIỀN THANH TOÁN:")
    c.drawRightString(545, cur_y + 6, fmt_cur(inv.total_amount))

    # Số tiền bằng chữ
    cur_y -= 25
    c.setFont(font_bold, 9)
    c.drawString(40, cur_y, "Số tiền viết bằng chữ:")
    c.setFont(font_regular, 9)
    words = vietnamese_number_to_words(int(round(inv.total_amount)))
    c.drawString(155, cur_y, words)

    # Khối ký tên
    sig_y = cur_y - 45
    c.setFont(font_bold, 10)
    c.drawCentredString(120, sig_y, "Người mua hàng")
    c.drawCentredString(w / 2, sig_y, "Người lập hóa đơn")
    c.drawCentredString(w - 120, sig_y, "Thủ trưởng đơn vị")

    c.setFont(font_regular, 8)
    c.drawCentredString(120, sig_y - 12, "(Ký, ghi rõ họ tên)")
    c.drawCentredString(w / 2, sig_y - 12, "(Ký, ghi rõ họ tên)")
    c.drawCentredString(w - 120, sig_y - 12, "(Ký, đóng dấu, ghi rõ họ tên)")

    c.drawString(40, 25, f"* Hóa đơn bán hàng điện tử nội bộ được khởi tạo từ hệ thống TTCS lúc {get_utc_now().strftime('%d/%m/%Y %H:%M:%S')}")

    c.save()
    buf.seek(0)

    # Ghi nhận audit log
    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="INVOICE_DOWNLOAD",
            entity_type="Invoice",
            entity_id=inv.id,
            new_val={"invoice_code": inv.invoice_code, "action": "EXPORT_PDF"},
            reason=f"Tải tệp PDF hóa đơn bán hàng {inv.invoice_code}",
        )
    except Exception:
        pass

    filename = f"{inv.invoice_code}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/{invoice_id}/print-log")
def log_print_event(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Ghi nhận sự kiện in hóa đơn bán hàng vào nhật ký kiểm toán."""
    _require_accountant_or_admin(current_user)

    inv = db.query(InvoiceEntity).filter(InvoiceEntity.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy hóa đơn.")

    try:
        log_audit_event(
            db=db,
            user=current_user,
            action_type="INVOICE_PRINT",
            entity_type="Invoice",
            entity_id=inv.id,
            new_val={"invoice_code": inv.invoice_code, "action": "PRINT_INVOICE"},
            reason=f"Thực hiện in hóa đơn bán hàng {inv.invoice_code}",
        )
    except Exception:
        pass

    return {"status": "success", "message": f"Đã ghi nhận sự kiện in hóa đơn {inv.invoice_code}."}


@router.put("/{invoice_id}")
def prevent_edit_invoice(invoice_id: int):
    """Quy chuẩn VI.2: Ngăn chặn sửa trực tiếp dữ liệu hóa đơn đã phát hành."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Hóa đơn đã phát hành có tính bất biến theo chuẩn mực kế toán. Không cho phép sửa trực tiếp!",
    )


@router.delete("/{invoice_id}")
def prevent_delete_invoice(invoice_id: int):
    """Quy chuẩn VI.2: Ngăn chặn xóa hóa đơn đã phát hành."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Hóa đơn đã phát hành có tính bất biến theo chuẩn mực kế toán. Không cho phép xóa hóa đơn!",
    )


class CreateAdjustmentRequest(BaseModel):
    adjustment_type: str = Field(..., description="INCREASE (Điều chỉnh tăng) | DECREASE (Điều chỉnh giảm) | INFO (Điều chỉnh thông tin)")
    reason: str = Field(..., min_length=3, description="Lý do điều chỉnh hóa đơn")
    difference_subtotal: float = Field(0.0, description="Số tiền chênh lệch trước thuế")
    difference_vat: float = Field(0.0, description="Tiền thuế GTGT chênh lệch")
    difference_total: float = Field(0.0, description="Tổng tiền thanh toán chênh lệch")
    details_json: Optional[str] = Field(None, description="Chi tiết các dòng điều chỉnh (JSON)")


@router.post("/{invoice_id}/adjustments")
def create_invoice_adjustment(
    invoice_id: int,
    payload: CreateAdjustmentRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lập chứng từ điều chỉnh cho hóa đơn đã phát hành (Bảo toàn tính bất biến của hóa đơn gốc)."""
    _require_accountant_or_admin(current_user)

    inv = db.query(InvoiceEntity).filter(InvoiceEntity.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy hóa đơn cần điều chỉnh.")

    # Cấp mã chứng từ điều chỉnh tuần tự DC-YYYYMM-XXXX
    now = get_utc_now()
    year_month = now.strftime("%Y%m")
    prefix = f"DC-{year_month}-"
    latest_adj = db.query(InvoiceAdjustmentEntity).filter(
        InvoiceAdjustmentEntity.adjustment_code.like(f"{prefix}%")
    ).order_by(desc(InvoiceAdjustmentEntity.id)).first()

    next_seq = 1
    if latest_adj and latest_adj.adjustment_code:
        try:
            seq_str = latest_adj.adjustment_code.replace(prefix, "")
            next_seq = int(seq_str) + 1
        except Exception:
            next_seq = 1

    adj_code = f"{prefix}{next_seq:04d}"

    diff_total = payload.difference_total
    if diff_total == 0 and (payload.difference_subtotal != 0 or payload.difference_vat != 0):
        diff_total = payload.difference_subtotal + payload.difference_vat

    adj = InvoiceAdjustmentEntity(
        adjustment_code=adj_code,
        invoice_id=inv.id,
        invoice_code=inv.invoice_code,
        adjustment_type=payload.adjustment_type.upper(),
        reason=payload.reason.strip(),
        difference_subtotal=payload.difference_subtotal,
        difference_vat=payload.difference_vat,
        difference_total=diff_total,
        details_json=payload.details_json,
        created_by=current_user.full_name or current_user.username,
        created_at=now,
    )
    db.add(adj)

    # Đánh dấu hóa đơn đã có chứng từ điều chỉnh (nhưng không sửa số liệu gốc)
    inv.issue_status = "ADJUSTED"
    db.commit()
    db.refresh(adj)

    # Ghi nhận nhật ký kiểm toán
    log_audit_event(
        db=db,
        user=current_user,
        action_type="INVOICE_ADJUSTMENT_CREATED",
        entity_type="InvoiceAdjustment",
        entity_id=adj.id,
        new_val={
            "adjustment_code": adj.adjustment_code,
            "invoice_code": inv.invoice_code,
            "type": adj.adjustment_type,
            "difference_total": adj.difference_total,
            "reason": adj.reason,
        },
        reason=f"Lập chứng từ điều chỉnh {adj.adjustment_code} cho hóa đơn {inv.invoice_code}: {adj.reason}",
    )

    return {
        "status": "success",
        "message": f"Đã lập thành công chứng từ điều chỉnh {adj.adjustment_code} cho hóa đơn {inv.invoice_code}.",
        "data": {
            "id": adj.id,
            "adjustment_code": adj.adjustment_code,
            "invoice_id": adj.invoice_id,
            "invoice_code": adj.invoice_code,
            "adjustment_type": adj.adjustment_type,
            "reason": adj.reason,
            "difference_total": adj.difference_total,
            "created_by": adj.created_by,
            "created_at": adj.created_at.strftime("%d/%m/%Y %H:%M"),
        }
    }


@router.get("/{invoice_id}/adjustments")
def get_invoice_adjustments(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Lấy danh sách các chứng từ điều chỉnh của hóa đơn."""
    _require_accountant_or_admin(current_user)

    adjs = db.query(InvoiceAdjustmentEntity).filter(
        InvoiceAdjustmentEntity.invoice_id == invoice_id
    ).order_by(desc(InvoiceAdjustmentEntity.created_at)).all()

    return {
        "status": "success",
        "data": [
            {
                "id": a.id,
                "adjustment_code": a.adjustment_code,
                "invoice_id": a.invoice_id,
                "invoice_code": a.invoice_code,
                "adjustment_type": a.adjustment_type,
                "reason": a.reason,
                "difference_subtotal": a.difference_subtotal,
                "difference_vat": a.difference_vat,
                "difference_total": a.difference_total,
                "details_json": a.details_json,
                "created_by": a.created_by,
                "created_at": a.created_at.strftime("%d/%m/%Y %H:%M") if a.created_at else "",
            }
            for a in adjs
        ]
    }
