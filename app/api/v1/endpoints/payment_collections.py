# backend/app/api/v1/endpoints/payment_collections.py
"""
Phân hệ Thu tiền theo tuyến & Nộp tiền về công ty (Route Payment Collection & Reconciliation).
Nghiệp vụ cốt lõi:
1. Ghi nhận khoản thu ngay tại cửa hàng/đại lý kèm ảnh biên lai (COL-YYYYMMDD-XXXX).
2. Khoản thu ở trạng thái 'Chờ nộp' (PENDING_REMITTANCE) cho tới khi nhân viên nộp (SUBMITTED)
   và Kế toán xác nhận đã nhận tiền (CONFIRMED).
3. Bảng đối chiếu tiền thu theo từng nhân viên và từng ngày (Reconciliation) minh bạch, không tranh cãi.
"""
from datetime import datetime, timezone
import uuid
from typing import List, Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rbac import Role
from app.models.entities import UserEntity, OrderEntity, DealerEntity
from app.models.payment_collection import PaymentCollectionEntity
from app.schemas.auth import UserResponse
from app.schemas.payment_collection import (
    PaymentCollectionCreate,
    PaymentCollectionRemit,
    BulkPaymentCollectionRemit,
    PaymentCollectionConfirm,
    BulkPaymentCollectionConfirm,
    PaymentCollectionReject,
    PaymentCollectionResponse,
    PaymentCollectionListResponse,
    DailyReconciliationItem,
    DailyReconciliationResponse,
    ReconciliationSummary,
)
from app.services.audit_service import log_audit_event

router = APIRouter()


def _is_admin(user: UserResponse) -> bool:
    roles = [user.role] + (user.roles or [])
    return any(r in [Role.SYSTEM_ADMIN.value, "admin", "system_admin"] for r in roles)


def _is_accountant_or_admin(user: UserResponse) -> bool:
    roles = [user.role] + (user.roles or [])
    return any(r in [Role.ACCOUNTANT.value, Role.SYSTEM_ADMIN.value, "admin", "system_admin", "accountant"] for r in roles)


def _is_sales_or_manager_or_admin(user: UserResponse) -> bool:
    roles = [user.role] + (user.roles or [])
    return any(r in [Role.SALES.value, Role.SALES_MANAGER.value, Role.SYSTEM_ADMIN.value, "sales", "sales_manager", "admin"] for r in roles)


# -----------------------------------------------------------------------------
# 1. GHI NHẬN KHOẢN THU TẠI CỬA HÀNG KÈM ẢNH BIÊN LAI (TIÊU CHÍ 1)
# -----------------------------------------------------------------------------
@router.post("", response_model=PaymentCollectionResponse, status_code=status.HTTP_201_CREATED)
def create_payment_collection(
    payload: PaymentCollectionCreate,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Ghi nhận khoản thu ngay tại cửa hàng/đại lý kèm ảnh biên lai.
    - Áp dụng cho Nhân viên kinh doanh (Sales), Quản lý kinh doanh và Admin.
    - Trạng thái mặc định: PENDING_REMITTANCE (Chờ nộp).
    """
    if not _is_sales_or_manager_or_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Nhân viên kinh doanh, Quản lý bán hàng hoặc Quản trị viên mới có quyền lập phiếu thu tiền theo tuyến."
        )

    # 1. Kiểm tra đại lý
    dealer = db.query(DealerEntity).filter(DealerEntity.id == payload.dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý/cửa hàng có ID {payload.dealer_id}."
        )

    # 2. Kiểm tra đơn hàng nếu có
    order_code = None
    if payload.order_id:
        order = db.query(OrderEntity).filter(OrderEntity.id == payload.order_id).first()
        if order:
            order_code = order.order_code or f"ORD-{order.id}"

    # 3. Sinh mã chứng từ duy nhất theo ngày thu tiền: COL-YYYYMMDD-XXXX
    now_utc = datetime.now(timezone.utc)
    collection_time = payload.collection_date or now_utc
    date_str = collection_time.strftime("%Y%m%d")
    code = f"COL-{date_str}-{uuid.uuid4().hex[:6].upper()}"

    # 4. Tạo bản ghi khoản thu
    collection = PaymentCollectionEntity(
        code=code,
        sales_id=current_user.id,
        sales_name=current_user.full_name or current_user.username,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        order_id=payload.order_id,
        order_code=order_code,
        amount=float(payload.amount),
        payment_method=payload.payment_method or "CASH",
        receipt_image_url=payload.receipt_image_url,
        collection_date=collection_time,
        route_name=payload.route_name or dealer.route_name if hasattr(dealer, "route_name") else None,
        note=payload.note,
        status="PENDING_REMITTANCE",  # Chờ nộp
        created_at=now_utc,
    )
    db.add(collection)
    db.commit()
    db.refresh(collection)

    # 5. Ghi Audit Log
    try:
        log_audit_event(
            db=db,
            user_id=current_user.id,
            user_name=current_user.full_name or current_user.username,
            action_type="PAYMENT_COLLECTED",
            entity_type="PaymentCollection",
            entity_id=code,
            new_values={
                "code": code,
                "amount": collection.amount,
                "dealer_id": dealer.id,
                "dealer_name": dealer.name,
                "payment_method": collection.payment_method,
                "has_receipt": bool(collection.receipt_image_url),
                "status": collection.status,
            },
            reason=f"Thu tiền theo tuyến tại {dealer.name}: {collection.amount:,.0f} đ",
            ip_address=request.client.host if request and request.client else None,
        )
    except Exception as ex:
        print(f"Warning logging audit event: {ex}")

    return collection


# -----------------------------------------------------------------------------
# 2. DANH SÁCH KHOẢN THU (HỖ TRỢ LỌC THEO TUYẾN, NHÂN VIÊN, TRẠNG THÁI)
# -----------------------------------------------------------------------------
@router.get("", response_model=PaymentCollectionListResponse)
def get_payment_collections(
    sales_id: Optional[int] = Query(None, description="Lọc theo ID nhân viên"),
    dealer_id: Optional[int] = Query(None, description="Lọc theo ID đại lý"),
    status_filter: Optional[str] = Query(None, alias="status", description="PENDING_REMITTANCE, SUBMITTED, CONFIRMED, REJECTED"),
    from_date: Optional[str] = Query(None, description="Từ ngày (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Đến ngày (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Tìm theo mã phiếu, tên đại lý, ghi chú"),
    page: int = Query(1, ge=1, description="Số trang (1-based)"),
    limit: int = Query(10, ge=1, le=500, description="Số bản ghi mỗi trang (mặc định 10)"),
    offset: Optional[int] = Query(None, ge=0),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Tra cứu danh sách các khoản thu tiền theo tuyến.
    - Nhân viên kinh doanh thông thường chỉ xem được các khoản thu của chính mình.
    - Kế toán, Quản lý kinh doanh và Admin xem được toàn bộ.
    """
    query = db.query(PaymentCollectionEntity)

    # Phân quyền dòng (Row-level Security): Nhân viên kinh doanh chỉ thấy tiền do mình thu
    is_admin = _is_admin(current_user)
    is_accountant = any(r in [Role.ACCOUNTANT.value, "accountant"] for r in ([current_user.role] + (current_user.roles or [])))
    is_sales_mgr = any(r in [Role.SALES_MANAGER.value, "sales_manager"] for r in ([current_user.role] + (current_user.roles or [])))

    if not (is_admin or is_accountant or is_sales_mgr):
        query = query.filter(PaymentCollectionEntity.sales_id == current_user.id)
    elif sales_id:
        query = query.filter(PaymentCollectionEntity.sales_id == sales_id)

    if dealer_id:
        query = query.filter(PaymentCollectionEntity.dealer_id == dealer_id)

    if status_filter and status_filter != "all":
        query = query.filter(PaymentCollectionEntity.status == status_filter)

    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d")
            query = query.filter(PaymentCollectionEntity.collection_date >= fd)
        except Exception:
            pass

    if to_date:
        try:
            td = datetime.strptime(f"{to_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query = query.filter(PaymentCollectionEntity.collection_date <= td)
        except Exception:
            pass

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PaymentCollectionEntity.code.ilike(term),
                PaymentCollectionEntity.dealer_name.ilike(term),
                PaymentCollectionEntity.sales_name.ilike(term),
                PaymentCollectionEntity.order_code.ilike(term),
                PaymentCollectionEntity.note.ilike(term),
                PaymentCollectionEntity.route_name.ilike(term),
            )
        )

    # Thống kê tổng hợp số tiền
    all_matched = query.all()
    total = len(all_matched)
    total_amount = sum(c.amount for c in all_matched)
    total_pending_remittance = sum(c.amount for c in all_matched if c.status == "PENDING_REMITTANCE")
    total_submitted = sum(c.amount for c in all_matched if c.status == "SUBMITTED")
    total_confirmed = sum(c.amount for c in all_matched if c.status == "CONFIRMED")

    # Phân trang
    effective_offset = offset if offset is not None else (page - 1) * limit
    total_pages = max(1, (total + limit - 1) // limit) if limit > 0 else 1

    items = (
        query.order_by(desc(PaymentCollectionEntity.id))
        .offset(effective_offset)
        .limit(limit)
        .all()
    )

    return PaymentCollectionListResponse(
        items=items,
        total=total,
        total_amount=total_amount,
        total_pending_remittance=total_pending_remittance,
        total_submitted=total_submitted,
        total_confirmed=total_confirmed,
        page=page,
        page_size=limit,
        total_pages=total_pages,
    )


# -----------------------------------------------------------------------------
# 3. NỘP TIỀN VỀ CÔNG TY: CHUYỂN TRẠNG THÁI TỪ "CHỜ NỘP" SANG "ĐÃ NỘP" (TIÊU CHÍ 2)
# -----------------------------------------------------------------------------
@router.post("/{collection_id}/submit-remittance", response_model=PaymentCollectionResponse)
def submit_remittance(
    collection_id: int,
    payload: Optional[PaymentCollectionRemit] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Nhân viên kinh doanh nộp khoản tiền đã thu về công ty.
    Chuyển trạng thái từ PENDING_REMITTANCE sang SUBMITTED (Đã nộp, chờ kế toán đối chiếu).
    """
    col = db.query(PaymentCollectionEntity).filter(PaymentCollectionEntity.id == collection_id).first()
    if not col:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy phiếu thu ID {collection_id}.")

    # Kiểm tra quyền: Người thu hoặc Admin mới được nộp
    if not _is_admin(current_user) and col.sales_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn chỉ có quyền nộp khoản tiền do chính bạn thu thập."
        )

    if col.status not in ["PENDING_REMITTANCE", "REJECTED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Khoản thu '{col.code}' đang ở trạng thái '{col.status}', không thể nộp lại."
        )

    now_utc = datetime.now(timezone.utc)
    col.status = "SUBMITTED"
    col.submitted_at = now_utc
    if payload and payload.remittance_note:
        col.remittance_note = payload.remittance_note.strip()
    col.updated_at = now_utc

    db.commit()
    db.refresh(col)

    return col


@router.post("/bulk-submit-remittance", response_model=List[PaymentCollectionResponse])
def bulk_submit_remittance(
    payload: BulkPaymentCollectionRemit,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Nộp nhiều khoản thu theo lô về công ty cùng lúc.
    """
    cols = db.query(PaymentCollectionEntity).filter(
        PaymentCollectionEntity.id.in_(payload.collection_ids)
    ).all()

    if not cols:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy khoản thu nào.")

    now_utc = datetime.now(timezone.utc)
    updated = []
    for col in cols:
        if not _is_admin(current_user) and col.sales_id != current_user.id:
            continue
        if col.status in ["PENDING_REMITTANCE", "REJECTED"]:
            col.status = "SUBMITTED"
            col.submitted_at = now_utc
            if payload.remittance_note:
                col.remittance_note = payload.remittance_note.strip()
            col.updated_at = now_utc
            updated.append(col)

    db.commit()
    for col in updated:
        db.refresh(col)

    return updated


# -----------------------------------------------------------------------------
# 4. KẾ TOÁN XÁC NHẬN ĐÃ NHẬN TIỀN (CONFIRMED) HOẶC TỪ CHỐI (TIÊU CHÍ 2)
# -----------------------------------------------------------------------------
@router.post("/{collection_id}/confirm", response_model=PaymentCollectionResponse)
def confirm_payment_collection(
    collection_id: int,
    payload: Optional[PaymentCollectionConfirm] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Kế toán kiểm đếm và xác nhận đã nhận đủ tiền từ Nhân viên kinh doanh.
    RÀNG BUỘC PHÂN NHIỆM (SEGREGATION OF DUTIES):
    - Chỉ Kế toán (Role: accountant) hoặc Quản trị viên (Role: admin) mới có quyền duyệt.
    - Nhân viên kinh doanh TUYỆT ĐỐI KHÔNG ĐƯỢC TỰ DUYỆT khoản tiền do chính mình thu!
    """
    if not _is_accountant_or_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Kế toán (Accountant) hoặc Quản trị viên mới có quyền xác nhận đã nhận tiền."
        )

    col = db.query(PaymentCollectionEntity).filter(PaymentCollectionEntity.id == collection_id).first()
    if not col:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy phiếu thu ID {collection_id}.")

    # Kiểm tra phân nhiệm: Người thu không được tự xác nhận
    if not _is_admin(current_user) and col.sales_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nguyên tắc kiểm soát nội bộ: Nhân viên kinh doanh không được tự xác nhận khoản tiền do chính mình thu. Vui lòng để Kế toán kiểm đếm và xác nhận."
        )

    now_utc = datetime.now(timezone.utc)
    col.status = "CONFIRMED"
    col.confirmed_by_id = current_user.id
    col.confirmed_by = current_user.full_name or current_user.username
    col.confirmed_at = now_utc
    if payload and payload.confirmation_note:
        col.confirmation_note = payload.confirmation_note.strip()
    col.rejection_reason = None
    col.updated_at = now_utc

    db.commit()
    db.refresh(col)

    # Ghi Audit Log
    try:
        log_audit_event(
            db=db,
            user_id=current_user.id,
            user_name=current_user.full_name or current_user.username,
            action_type="PAYMENT_CONFIRMED",
            entity_type="PaymentCollection",
            entity_id=col.code,
            new_values={
                "code": col.code,
                "amount": col.amount,
                "sales_name": col.sales_name,
                "status": "CONFIRMED",
                "confirmed_by": col.confirmed_by,
            },
            reason=f"Kế toán xác nhận nhận đủ tiền: {col.amount:,.0f} đ từ {col.sales_name}",
            ip_address=request.client.host if request and request.client else None,
        )
    except Exception as ex:
        print(f"Warning audit: {ex}")

    return col


@router.post("/bulk-confirm", response_model=List[PaymentCollectionResponse])
def bulk_confirm_payment_collections(
    payload: BulkPaymentCollectionConfirm,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Kế toán xác nhận đối chiếu hàng loạt theo lô (Bulk Reconciliation).
    """
    if not _is_accountant_or_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Kế toán hoặc Admin mới có quyền xác nhận thu tiền."
        )

    cols = db.query(PaymentCollectionEntity).filter(
        PaymentCollectionEntity.id.in_(payload.collection_ids)
    ).all()

    now_utc = datetime.now(timezone.utc)
    confirmed_list = []
    for col in cols:
        # Kiểm tra tự duyệt
        if not _is_admin(current_user) and col.sales_id == current_user.id:
            continue
        col.status = "CONFIRMED"
        col.confirmed_by_id = current_user.id
        col.confirmed_by = current_user.full_name or current_user.username
        col.confirmed_at = now_utc
        if payload.confirmation_note:
            col.confirmation_note = payload.confirmation_note.strip()
        col.rejection_reason = None
        col.updated_at = now_utc
        confirmed_list.append(col)

    db.commit()
    for col in confirmed_list:
        db.refresh(col)

    return confirmed_list


@router.post("/{collection_id}/reject", response_model=PaymentCollectionResponse)
def reject_payment_collection(
    collection_id: int,
    payload: PaymentCollectionReject,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Kế toán từ chối / Báo sai lệch khoản tiền nộp (sai tiền mặt, thiếu ảnh biên lai...).
    Bắt buộc phải nhập lý do từ chối.
    """
    if not _is_accountant_or_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Kế toán hoặc Admin mới có quyền từ chối / báo lệch khoản thu."
        )

    col = db.query(PaymentCollectionEntity).filter(PaymentCollectionEntity.id == collection_id).first()
    if not col:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy phiếu thu ID {collection_id}.")

    now_utc = datetime.now(timezone.utc)
    col.status = "REJECTED"
    col.rejection_reason = payload.rejection_reason.strip()
    col.confirmed_by_id = current_user.id
    col.confirmed_by = current_user.full_name or current_user.username
    col.confirmed_at = now_utc
    col.updated_at = now_utc

    db.commit()
    db.refresh(col)

    return col


# -----------------------------------------------------------------------------
# 5. BẢNG ĐỐI CHIẾU TIỀN THU THEO TỪNG NHÂN VIÊN VÀ TỪNG NGÀY (TIÊU CHÍ 3)
# -----------------------------------------------------------------------------
@router.get("/daily-reconciliation", response_model=DailyReconciliationResponse)
def get_daily_reconciliation(
    sales_id: Optional[int] = Query(None, description="Lọc theo nhân viên kinh doanh"),
    from_date: Optional[str] = Query(None, description="Từ ngày (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Đến ngày (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Tìm theo tên NV, ngày, mã phiếu..."),
    balanced_only: Optional[bool] = Query(None, description="Chỉ lấy các ngày đã cân bằng 100%"),
    unbalanced_only: Optional[bool] = Query(None, description="Chỉ lấy các ngày còn lệch hoặc chờ nộp"),
    page: int = Query(1, ge=1, description="Số trang (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang (mặc định 10)"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Bảng đối chiếu tiền thu theo từng nhân viên và từng ngày (Daily Reconciliation).
    Phục vụ:
    - Nhân viên kinh doanh đối chiếu tiền đã thu trên tuyến với tiền đã nộp về công ty.
    - Kế toán đối chiếu và kiểm đếm dòng tiền mặt / chuyển khoản về quỹ hàng ngày.
    - Minh bạch, không tranh cãi, tự động chỉ ra khoản tiền còn giữ hoặc chênh lệch.
    - Hỗ trợ phân trang 10 dòng/trang và tìm kiếm, lọc tức thì.
    """
    query = db.query(PaymentCollectionEntity)

    # Phân quyền: Nhân viên chỉ thấy đối chiếu của chính mình
    is_admin = _is_admin(current_user)
    is_accountant = any(r in [Role.ACCOUNTANT.value, "accountant"] for r in ([current_user.role] + (current_user.roles or [])))
    is_sales_mgr = any(r in [Role.SALES_MANAGER.value, "sales_manager"] for r in ([current_user.role] + (current_user.roles or [])))

    if not (is_admin or is_accountant or is_sales_mgr):
        query = query.filter(PaymentCollectionEntity.sales_id == current_user.id)
    elif sales_id:
        query = query.filter(PaymentCollectionEntity.sales_id == sales_id)

    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d")
            query = query.filter(PaymentCollectionEntity.collection_date >= fd)
        except Exception:
            pass

    if to_date:
        try:
            td = datetime.strptime(f"{to_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query = query.filter(PaymentCollectionEntity.collection_date <= td)
        except Exception:
            pass

    records = query.order_by(desc(PaymentCollectionEntity.collection_date)).all()

    # Nhóm theo (Ngày YYYY-MM-DD, sales_id)
    grouped = defaultdict(list)
    for r in records:
        day_str = r.collection_date.strftime("%Y-%m-%d") if r.collection_date else "N/A"
        key = (day_str, r.sales_id, r.sales_name)
        grouped[key].append(r)

    items = []
    grand_collected = 0.0
    grand_pending = 0.0
    grand_submitted = 0.0
    grand_confirmed = 0.0
    grand_rejected = 0.0

    distinct_days = set()
    distinct_sales = set()

    for (day_str, s_id, s_name), col_list in grouped.items():
        day_total = sum(c.amount for c in col_list)
        day_pending = sum(c.amount for c in col_list if c.status == "PENDING_REMITTANCE")
        day_submitted = sum(c.amount for c in col_list if c.status == "SUBMITTED")
        day_confirmed = sum(c.amount for c in col_list if c.status == "CONFIRMED")
        day_rejected = sum(c.amount for c in col_list if c.status == "REJECTED")

        # Cân bằng khi toàn bộ các khoản đã được xác nhận (không còn pending, submitted hay rejected)
        is_balanced = (day_pending == 0.0 and day_submitted == 0.0 and day_rejected == 0.0 and day_confirmed > 0.0)

        # Bộ lọc trạng thái cân bằng
        if balanced_only is True and not is_balanced:
            continue
        if unbalanced_only is True and is_balanced:
            continue

        # Bộ lọc tìm kiếm
        if search and search.strip():
            term = search.strip().lower()
            matches_sales = term in (s_name or "").lower()
            matches_date = term in day_str.lower()
            matches_sub = any(
                term in (c.code or "").lower()
                or term in (c.dealer_name or "").lower()
                or term in (c.order_code or "").lower()
                or term in (c.note or "").lower()
                for c in col_list
            )
            if not (matches_sales or matches_date or matches_sub):
                continue

        distinct_days.add(day_str)
        distinct_sales.add(s_id)

        grand_collected += day_total
        grand_pending += day_pending
        grand_submitted += day_submitted
        grand_confirmed += day_confirmed
        grand_rejected += day_rejected

        items.append(
            DailyReconciliationItem(
                date=day_str,
                sales_id=s_id,
                sales_name=s_name,
                total_collected=day_total,
                total_pending_remittance=day_pending,
                total_submitted=day_submitted,
                total_confirmed=day_confirmed,
                total_rejected=day_rejected,
                collections_count=len(col_list),
                is_balanced=is_balanced,
                collections=col_list,
            )
        )

    # Sắp xếp ngày giảm dần
    items.sort(key=lambda x: (x.date, x.sales_name), reverse=True)

    total_records = len(items)
    total_pages = max(1, (total_records + page_size - 1) // page_size) if page_size > 0 else 1
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_items = items[start_idx:end_idx]

    summary = ReconciliationSummary(
        grand_total_collected=grand_collected,
        grand_total_pending_remittance=grand_pending,
        grand_total_submitted=grand_submitted,
        grand_total_confirmed=grand_confirmed,
        grand_total_rejected=grand_rejected,
        total_days=len(distinct_days),
        total_sales_staff=len(distinct_sales),
    )

    return DailyReconciliationResponse(
        summary=summary,
        items=paginated_items,
        total=total_records,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# -----------------------------------------------------------------------------
# 5. LẤY CHI TIẾT KHOẢN THU THEO ID
# -----------------------------------------------------------------------------
@router.get("/{collection_id}", response_model=PaymentCollectionResponse)
def get_payment_collection_detail(
    collection_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Lấy thông tin chi tiết của một khoản thu tiền theo ID.
    - Admin, Kế toán, Quản lý bán hàng có thể xem mọi khoản thu.
    - Nhân viên kinh doanh chỉ có thể xem khoản thu của chính mình.
    """
    col = db.query(PaymentCollectionEntity).filter(PaymentCollectionEntity.id == collection_id).first()
    if not col:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiếu thu ID {collection_id}."
        )

    roles = [current_user.role] + (current_user.roles or [])
    is_privileged = any(r in [Role.SYSTEM_ADMIN.value, Role.ACCOUNTANT.value, Role.SALES_MANAGER.value, "admin", "system_admin", "accountant", "sales_manager"] for r in roles)
    if not is_privileged and col.sales_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền xem khoản thu của nhân viên khác."
        )

    return col

