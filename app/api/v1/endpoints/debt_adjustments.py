# backend/app/api/v1/endpoints/debt_adjustments.py
from __future__ import annotations
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.models.entities import DealerEntity, OrderEntity, ProductEntity
from app.models.invoice import InvoiceEntity
from app.models.debt_adjustment import (
    DebtAdjustmentEntity,
    DebtAdjustmentItemEntity,
    ReturnOrderEntity,
    ReturnOrderItemEntity,
)
from app.schemas.auth import UserResponse
from app.schemas.debt_adjustment import (
    DebtAdjustmentCreate,
    DebtAdjustmentResponse,
    DebtAdjustmentPreviewResponse,
    PendingReturnOrderResponse,
    DebtAdjustmentListResponse,
)
from app.services.debt_adjustment_service import (
    get_pending_returns,
    preview_debt_adjustment_from_return,
    create_debt_adjustment,
    get_original_order_and_invoice,
)

router = APIRouter()

ACCOUNTANT_ROLES = ["accountant", "admin"]
VIEW_ROLES = ["accountant", "admin", "sales_manager", "customer"]


def _get_user_roles(user: UserResponse) -> List[str]:
    raw = user.get_roles() if hasattr(user, "get_roles") else (user.roles or [user.role])
    return [str(getattr(r, "value", r)).lower() for r in raw if r]


def _resolve_customer_dealer_id(current_user: UserResponse, db: Session) -> Optional[int]:
    """Tìm ID đại lý liên kết nếu tài khoản đăng nhập là Customer."""
    roles = _get_user_roles(current_user)
    if "customer" in roles and not any(r in ["admin", "sales_manager", "sales", "accountant"] for r in roles):
        conds = [DealerEntity.id == current_user.id]
        if current_user.email:
            conds.append(DealerEntity.email == current_user.email)
        if current_user.phone:
            conds.append(DealerEntity.phone == current_user.phone)
        if current_user.username:
            conds.append(DealerEntity.code == current_user.username)
        if current_user.full_name:
            conds.append(DealerEntity.name == current_user.full_name)

        dealer = db.query(DealerEntity).filter(or_(*conds)).first()
        return dealer.id if dealer else None
    return None


@router.get("/pending-returns", response_model=List[PendingReturnOrderResponse])
def list_pending_returns(
    dealer_id: Optional[int] = Query(None, description="Lọc theo ID đại lý"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ACCOUNTANT_ROLES)),
):
    """
    Kế toán công nợ: Lấy danh sách phiếu trả hàng đã xác nhận hoàn kho (CONFIRMED)
    nhưng chưa được lập chứng từ điều chỉnh giảm công nợ.
    """
    return get_pending_returns(db, dealer_id=dealer_id)


@router.get("/preview/{return_id}", response_model=DebtAdjustmentPreviewResponse)
@router.get("/preview-from-return/{return_id}", response_model=DebtAdjustmentPreviewResponse)
def preview_adjustment(
    return_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ACCOUNTANT_ROLES)),
):
    """
    Khởi tạo dữ liệu xem trước cho Kế toán công nợ:
    - Map đơn giá gốc từ Hóa đơn gốc (BẢO LƯU GIÁ BÁN LÚC XUẤT, BỎ QUA GIÁ HIỆN HÀNH)
    - Tính toán trước tổng tiền giảm dự kiến
    - Hiển thị số dư nợ và hạn mức khả dụng trước & sau điều chỉnh
    """
    return preview_debt_adjustment_from_return(return_id=return_id, db=db)


@router.post("", response_model=DebtAdjustmentResponse, status_code=status.HTTP_201_CREATED)
def post_debt_adjustment(
    data: DebtAdjustmentCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ACCOUNTANT_ROLES)),
):
    """
    Lập và Phê duyệt ghi sổ chứng từ điều chỉnh giảm công nợ khi trả hàng (Atomic Transaction).
    1. Tạo bản ghi DebtAdjustmentEntity (Bất biến).
    2. Hóa đơn gốc: GIỮ NGUYÊN total_amount, chỉ cập nhật remaining_debt.
    3. Giảm nợ đại lý tức thời, tăng hạn mức khả dụng.
    4. Ghi Sổ chi tiết công nợ S31-DN (Phát sinh Có).
    5. Ghi Audit Log bất biến (DEBT_ADJUSTMENT_CREATED).
    """
    return create_debt_adjustment(data=data, current_user=current_user, db=db)


@router.get("", response_model=DebtAdjustmentListResponse)
def list_debt_adjustments(
    dealer_id: Optional[int] = Query(None, description="Lọc theo ID đại lý"),
    from_date: Optional[str] = Query(None, description="Từ ngày (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Đến ngày (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Tìm theo mã chứng từ, mã phiếu trả, mã HĐ gốc"),
    status_filter: Optional[str] = Query("ALL", description="Lọc trạng thái: ALL, POSTED, CANCELLED"),
    page: int = Query(1, ge=1, description="Trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số dòng mỗi trang"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(VIEW_ROLES)),
):
    """
    Tra cứu danh sách chứng từ điều chỉnh giảm công nợ.
    Bộ lọc: Đại lý, ngày lập, mã chứng từ, mã hóa đơn gốc.
    """
    query = db.query(DebtAdjustmentEntity)

    # Bảo mật RLS cho Customer
    customer_dealer_id = _resolve_customer_dealer_id(current_user, db)
    if customer_dealer_id is not None:
        query = query.filter(DebtAdjustmentEntity.dealer_id == customer_dealer_id)
    elif dealer_id:
        query = query.filter(DebtAdjustmentEntity.dealer_id == dealer_id)

    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(DebtAdjustmentEntity.status == status_filter.upper())

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                DebtAdjustmentEntity.adjustment_code.ilike(term),
                DebtAdjustmentEntity.return_order_code.ilike(term),
                DebtAdjustmentEntity.original_invoice_code.ilike(term),
                DebtAdjustmentEntity.dealer_name.ilike(term),
                DebtAdjustmentEntity.reason.ilike(term),
            )
        )

    if from_date:
        try:
            from_dt = datetime.strptime(from_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=0, minute=0, second=0)
            query = query.filter(DebtAdjustmentEntity.adjustment_date >= from_dt)
        except Exception:
            pass

    if to_date:
        try:
            to_dt = datetime.strptime(to_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
            query = query.filter(DebtAdjustmentEntity.adjustment_date <= to_dt)
        except Exception:
            pass

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size

    adjustments = (
        query.order_by(desc(DebtAdjustmentEntity.adjustment_date), desc(DebtAdjustmentEntity.id))
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return DebtAdjustmentListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=adjustments,
    )


@router.get("/{adj_id}", response_model=DebtAdjustmentResponse)
def get_debt_adjustment_detail(
    adj_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(VIEW_ROLES)),
):
    """
    Xem chi tiết chứng từ điều chỉnh giảm công nợ kèm bảng mặt hàng đối chiếu.
    """
    adj = db.query(DebtAdjustmentEntity).filter(DebtAdjustmentEntity.id == adj_id).first()
    if not adj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy chứng từ điều chỉnh ID {adj_id}."
        )

    # RLS: Customer chỉ xem chứng từ của chính đại lý mình
    customer_dealer_id = _resolve_customer_dealer_id(current_user, db)
    if customer_dealer_id is not None and adj.dealer_id != customer_dealer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đại lý chỉ được phép xem chứng từ của chính mình."
        )

    return adj


@router.get("/{adj_id}/export-pdf")
def export_debt_adjustment_pdf(
    adj_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(VIEW_ROLES)),
):
    """
    Xuất/In Biên bản điều chỉnh giảm trừ công nợ A4 chuẩn kế toán (2 liên: Công ty & Đại lý ký nhận).
    Trả về dữ liệu có cấu trúc đầy đủ phục vụ in ấn trực tiếp hoặc tải PDF.
    """
    adj = db.query(DebtAdjustmentEntity).filter(DebtAdjustmentEntity.id == adj_id).first()
    if not adj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy chứng từ điều chỉnh ID {adj_id}."
        )

    customer_dealer_id = _resolve_customer_dealer_id(current_user, db)
    if customer_dealer_id is not None and adj.dealer_id != customer_dealer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đại lý chỉ được phép xem chứng từ của chính mình."
        )

    dealer = db.query(DealerEntity).filter(DealerEntity.id == adj.dealer_id).first()
    invoice = db.query(InvoiceEntity).filter(InvoiceEntity.id == adj.original_invoice_id).first()

    items_data = []
    for it in adj.items:
        items_data.append({
            "product_code": it.product_code,
            "product_name": it.product_name,
            "unit_name": it.unit_name or "Cái",
            "return_quantity": it.return_quantity,
            "original_unit_price": it.original_unit_price,
            "line_total": it.line_total,
        })

    return {
        "adjustment_code": adj.adjustment_code,
        "adjustment_date": adj.adjustment_date.isoformat(),
        "status": adj.status,
        "company": {
            "name": "CÔNG TY CỔ PHẦN PHÂN PHỐI & BÁN LẺ TOÀN QUỐC",
            "address": "Tầng 5, Tòa nhà Detech, Số 8 Tôn Thất Thuyết, Mỹ Đình 2, Nam Từ Liêm, Hà Nội",
            "tax_code": "0108999888",
            "phone": "1900 6868",
        },
        "dealer": {
            "id": adj.dealer_id,
            "code": dealer.code if dealer else "",
            "name": adj.dealer_name,
            "tax_code": getattr(dealer, "tax_code", "") if dealer else "",
            "phone": getattr(dealer, "phone", "") if dealer else "",
            "address": getattr(dealer, "address", "") if dealer else "",
        },
        "references": {
            "return_order_code": adj.return_order_code,
            "original_invoice_code": adj.original_invoice_code,
            "original_invoice_total": float(invoice.total_amount if invoice else 0.0),
            "invoice_remaining_debt_after": float(invoice.remaining_debt if invoice else 0.0),
        },
        "financials": {
            "subtotal_amount": adj.subtotal_amount,
            "tax_amount": adj.tax_amount,
            "total_adjusted_amount": adj.total_adjusted_amount,
        },
        "reason": adj.reason,
        "note": adj.note,
        "created_by_name": adj.created_by_name,
        "items": items_data,
    }


def _format_return_order_response(ro: ReturnOrderEntity, db: Session) -> Dict[str, Any]:
    invoice, _ = get_original_order_and_invoice(ro, db)
    inv_code = invoice.invoice_code if invoice else (ro.original_invoice_code or f"HD-{ro.order_code}")
    inv_id = invoice.id if invoice else ro.original_invoice_id

    items_list = []
    for it in ro.items:
        items_list.append({
            "id": it.id,
            "product_id": it.product_id,
            "product_code": it.product_code,
            "product_name": it.product_name,
            "unit_name": it.unit_name or "Cái",
            "quantity": it.quantity,
            "unit_price": float(it.unit_price or 0.0),
            "line_total": float(it.line_total or 0.0),
            "reason": it.reason,
        })
    total_qty = sum(it.quantity for it in ro.items)
    return {
        "id": ro.id,
        "return_code": ro.return_code,
        "order_id": ro.order_id,
        "order_code": ro.order_code,
        "original_invoice_id": inv_id,
        "original_invoice_code": inv_code,
        "dealer_id": ro.dealer_id,
        "dealer_name": ro.dealer_name,
        "status": ro.status,
        "reason": ro.reason,
        "note": ro.note,
        "subtotal_amount": float(ro.subtotal_amount or 0.0),
        "tax_amount": float(ro.tax_amount or 0.0),
        "total_amount": float(ro.total_amount or 0.0),
        "total_items_count": total_qty,
        "confirmed_at": ro.confirmed_at,
        "created_at": ro.created_at,
        "items": items_list,
    }


@router.post("/mock-return", response_model=PendingReturnOrderResponse, status_code=status.HTTP_201_CREATED)
def create_mock_confirmed_return(
    order_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ACCOUNTANT_ROLES)),
):
    """
    Tiện ích hỗ trợ Test: Tạo nhanh một Phiếu trả hàng đã xác nhận hoàn kho (CONFIRMED)
    dựa trên một Đơn hàng / Hóa đơn thực tế trong hệ thống để Kế toán lập giảm nợ ngay.
    """
    now = datetime.now(timezone.utc)
    target_order = None

    if order_id:
        target_order = db.query(OrderEntity).filter(OrderEntity.id == order_id).first()
    else:
        # Lấy danh sách các đơn hàng hợp lệ
        valid_orders = db.query(OrderEntity).filter(
            ~OrderEntity.status.in_(["CANCELLED", "DRAFT", "REJECTED"])
        ).order_by(desc(OrderEntity.id)).all()

        for cand in valid_orders:
            # Kiểm tra xem đơn này đã có phiếu trả hàng chưa lập giảm nợ hay không
            cand_returns = db.query(ReturnOrderEntity).filter(ReturnOrderEntity.order_id == cand.id).all()
            if not cand_returns:
                target_order = cand
                break
            # Nếu có phiếu trả hàng nhưng CHƯA lập debt_adjustment thì dùng lại
            unadjusted = [
                r for r in cand_returns
                if not db.query(DebtAdjustmentEntity).filter(DebtAdjustmentEntity.return_order_id == r.id).first()
            ]
            if unadjusted:
                return _format_return_order_response(unadjusted[0], db)

        # Nếu tất cả đơn hiện tại đều đã được trả và lập giảm nợ, lấy đơn mới nhất để tạo thêm phiếu trả
        if not target_order and valid_orders:
            target_order = valid_orders[0]

    if not target_order:
        raise HTTPException(status_code=400, detail="Chưa có đơn hàng nào trong hệ thống để tạo phiếu trả hàng.")

    order = target_order
    dealer = db.query(DealerEntity).filter(DealerEntity.id == order.dealer_id).first()
    if not dealer:
        dealer = DealerEntity(code=f"DL{order.dealer_id}", name=order.dealer_name)

    # Đảm bảo có hóa đơn
    from app.services.invoice_service import sync_dealer_invoices_from_orders
    sync_dealer_invoices_from_orders(dealer, db)
    inv_code = f"HD-{order.order_code}"
    invoice = db.query(InvoiceEntity).filter(InvoiceEntity.invoice_code == inv_code).first()

    today_str = now.strftime("%Y%m%d")
    import uuid
    uid_suffix = uuid.uuid4().hex[:4].upper()
    ret_code = f"TH-{today_str}-{order.id:03d}-{uid_suffix}"

    # Lấy mặt hàng của đơn
    from app.services.debt_adjustment_service import extract_original_sold_items_map
    items_map = extract_original_sold_items_map(invoice, order, db)

    ro_items = []
    subtotal = 0.0
    for pid, it_info in items_map.items():
        qty = max(1, min(2, it_info["sold_quantity"]))
        price = it_info["unit_price"]
        tot = qty * price
        subtotal += tot
        ro_items.append(
            ReturnOrderItemEntity(
                product_id=pid,
                product_code=it_info["product_code"],
                product_name=it_info["product_name"],
                unit_name=it_info["unit_name"],
                quantity=qty,
                unit_price=price,
                line_total=tot,
                reason="Hàng lỗi bao bì khi vận chuyển, đã kiểm nhận hoàn kho",
            )
        )

    ro = ReturnOrderEntity(
        return_code=ret_code,
        order_id=order.id,
        order_code=order.order_code,
        original_invoice_id=invoice.id if invoice else None,
        original_invoice_code=inv_code,
        dealer_id=order.dealer_id,
        dealer_name=order.dealer_name,
        warehouse_id="WH01",
        warehouse_name="Kho Tổng Hà Nội",
        status="CONFIRMED",
        reason="Hàng lỗi bao bì giao hàng, đại lý gửi trả lại đã nhập kho",
        note="Đã kiểm tra chất lượng và hoàn kho thành công",
        subtotal_amount=subtotal,
        tax_amount=0.0,
        total_amount=subtotal,
        created_by_id=current_user.id,
        created_by_name=current_user.full_name or current_user.username,
        confirmed_by_id=current_user.id,
        confirmed_by_name=current_user.full_name or current_user.username,
        confirmed_at=now,
        items=ro_items,
    )
    db.add(ro)
    db.commit()
    db.refresh(ro)
    return _format_return_order_response(ro, db)

