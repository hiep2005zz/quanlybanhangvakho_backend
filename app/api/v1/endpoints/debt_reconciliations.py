# backend/app/api/v1/endpoints/debt_reconciliations.py
from __future__ import annotations
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.entities import DealerEntity, UserEntity
from app.models.debt_reconciliation import DebtReconciliationEntity
from app.schemas.auth import UserResponse
from app.schemas.debt_reconciliation import (
    DebtReconciliationPreviewRequest,
    DebtReconciliationCreateRequest,
    DebtDisputeRequest,
    DebtResolveRequest,
    DebtReconciliationResponse,
    DebtReconciliationListResponse,
)
from app.services.debt_reconciliation_service import (
    parse_date_to_utc,
    calculate_reconciliation_ledger,
    create_debt_reconciliation,
    to_response_dict,
    get_utc_now,
)
from app.api.v1.endpoints.agency_debts import _resolve_authenticated_dealer

router = APIRouter()


def _is_customer_user(current_user: UserResponse) -> bool:
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    return "customer" in [r.lower() for r in user_roles] and not any(
        r.lower() in ["admin", "sales_manager", "sales", "accountant"] for r in user_roles
    )


def _check_accountant_or_admin(current_user: UserResponse) -> None:
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    has_perm = any(r.lower() in ["admin", "accountant", "sales_manager"] for r in user_roles)
    if not has_perm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Kế toán công nợ, Quản lý kinh doanh hoặc Quản trị viên mới có quyền thực hiện thao tác này.",
        )


@router.post("/preview")
def preview_debt_reconciliation_endpoint(
    payload: DebtReconciliationPreviewRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Tính toán xem trước (Preview) số liệu đối chiếu cho một đại lý trong khoảng thời gian nhất định (chưa lưu vào DB).
    """
    _check_accountant_or_admin(current_user)

    dealer = db.query(DealerEntity).filter(DealerEntity.id == payload.dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý với ID {payload.dealer_id}.",
        )

    from_dt = parse_date_to_utc(payload.from_date, is_end_of_day=False)
    to_dt = parse_date_to_utc(payload.to_date, is_end_of_day=True)

    if from_dt > to_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngày bắt đầu kỳ đối chiếu không được lớn hơn ngày kết thúc.",
        )

    data = calculate_reconciliation_ledger(dealer, from_dt, to_dt, db)
    return data


@router.post("", response_model=DebtReconciliationResponse)
def create_debt_reconciliation_endpoint(
    payload: DebtReconciliationCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Tạo mới Biên bản đối chiếu công nợ chính thức (ở trạng thái DRAFT hoặc SENT).
    """
    _check_accountant_or_admin(current_user)

    dealer = db.query(DealerEntity).filter(DealerEntity.id == payload.dealer_id).first()
    if not dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy đại lý với ID {payload.dealer_id}.",
        )

    from_dt = parse_date_to_utc(payload.from_date, is_end_of_day=False)
    to_dt = parse_date_to_utc(payload.to_date, is_end_of_day=True)

    if from_dt > to_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngày bắt đầu kỳ đối chiếu không được lớn hơn ngày kết thúc.",
        )

    # Lấy UserEntity của người tạo
    user_entity = db.query(UserEntity).filter(UserEntity.id == current_user.id).first()

    rec = create_debt_reconciliation(
        dealer=dealer,
        from_date_str=payload.from_date,
        to_date_str=payload.to_date,
        created_by_user=user_entity,
        note=payload.note,
        send_immediately=payload.send_immediately,
        db=db,
    )

    return to_response_dict(rec)


@router.get("", response_model=DebtReconciliationListResponse)
def list_debt_reconciliations_endpoint(
    dealer_id: Optional[int] = Query(None, description="Lọc theo ID đại lý"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc theo trạng thái DRAFT/SENT/CONFIRMED/DISPUTED"),
    search: Optional[str] = Query(None, description="Tìm theo mã biên bản, tên đại lý"),
    from_date: Optional[str] = Query(None, description="Từ ngày"),
    to_date: Optional[str] = Query(None, description="Đến ngày"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lấy danh sách các biên bản đối chiếu công nợ kèm KPI tóm tắt.
    Nếu là tài khoản Đại lý (Customer), tự động giới hạn chỉ trả về biên bản của đại lý đó.
    """
    query = db.query(DebtReconciliationEntity)

    is_customer = _is_customer_user(current_user)
    if is_customer:
        dealer = _resolve_authenticated_dealer(current_user, db)
        query = query.filter(DebtReconciliationEntity.dealer_id == dealer.id)
        # Đại lý không nhìn thấy bản nháp (DRAFT) mà chỉ thấy SENT, CONFIRMED, DISPUTED
        query = query.filter(DebtReconciliationEntity.status != "DRAFT")
    elif dealer_id is not None:
        query = query.filter(DebtReconciliationEntity.dealer_id == dealer_id)

    if status_filter and status_filter.upper() != "ALL":
        query = query.filter(DebtReconciliationEntity.status == status_filter.upper())

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                DebtReconciliationEntity.code.ilike(term),
                DebtReconciliationEntity.dealer_name.ilike(term),
                DebtReconciliationEntity.dealer_code.ilike(term),
            )
        )

    if from_date:
        query = query.filter(DebtReconciliationEntity.to_date >= parse_date_to_utc(from_date, is_end_of_day=False))
    if to_date:
        query = query.filter(DebtReconciliationEntity.from_date <= parse_date_to_utc(to_date, is_end_of_day=True))

    total = query.count()
    items_db = (
        query.order_by(desc(DebtReconciliationEntity.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Tính KPI tổng kết
    # Dùng query base cùng phạm vi quyền
    kpi_query = db.query(DebtReconciliationEntity)
    if is_customer:
        dealer = _resolve_authenticated_dealer(current_user, db)
        kpi_query = kpi_query.filter(DebtReconciliationEntity.dealer_id == dealer.id, DebtReconciliationEntity.status != "DRAFT")
    elif dealer_id is not None:
        kpi_query = kpi_query.filter(DebtReconciliationEntity.dealer_id == dealer_id)

    all_recs = kpi_query.all()
    kpi_total = len(all_recs)
    kpi_pending = sum(1 for r in all_recs if r.status == "SENT")
    kpi_confirmed = sum(1 for r in all_recs if r.status == "CONFIRMED")
    kpi_disputed = sum(1 for r in all_recs if r.status == "DISPUTED")
    kpi_draft = sum(1 for r in all_recs if r.status == "DRAFT")

    return {
        "items": [to_response_dict(r) for r in items_db],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "kpi_summary": {
            "total_count": kpi_total,
            "pending_count": kpi_pending,
            "confirmed_count": kpi_confirmed,
            "disputed_count": kpi_disputed,
            "draft_count": kpi_draft,
        },
    }


@router.get("/{rec_id}", response_model=DebtReconciliationResponse)
def get_debt_reconciliation_detail_endpoint(
    rec_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lấy thông tin chi tiết một biên bản đối chiếu công nợ.
    """
    rec = db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.id == rec_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biên bản đối chiếu ID {rec_id}.",
        )

    # Ràng buộc Zero-Trust nếu là khách hàng (Đại lý)
    if _is_customer_user(current_user):
        dealer = _resolve_authenticated_dealer(current_user, db)
        if rec.dealer_id != dealer.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền truy cập biên bản đối chiếu của đại lý khác.",
            )

    return to_response_dict(rec)


@router.post("/{rec_id}/send", response_model=DebtReconciliationResponse)
def send_reconciliation_to_dealer_endpoint(
    rec_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Kế toán gửi biên bản đối chiếu sang Cổng đại lý để đại lý ký/xác nhận (Chuyển trạng thái từ DRAFT sang SENT).
    """
    _check_accountant_or_admin(current_user)

    rec = db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.id == rec_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biên bản đối chiếu ID {rec_id}.",
        )

    if rec.status not in ("DRAFT", "DISPUTED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Biên bản đang ở trạng thái '{rec.status}', không thể gửi lại.",
        )

    rec.status = "SENT"
    rec.updated_at = get_utc_now()
    db.commit()
    db.refresh(rec)
    return to_response_dict(rec)


@router.post("/{rec_id}/confirm", response_model=DebtReconciliationResponse)
def confirm_reconciliation_by_dealer_endpoint(
    rec_id: int,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Đại lý xác nhận số liệu biên bản đối chiếu là chính xác (Chuyển trạng thái sang CONFIRMED).
    """
    rec = db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.id == rec_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biên bản đối chiếu ID {rec_id}.",
        )

    if _is_customer_user(current_user):
        dealer = _resolve_authenticated_dealer(current_user, db)
        if rec.dealer_id != dealer.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền xác nhận biên bản của đại lý khác.",
            )

    if rec.status == "CONFIRMED":
        return to_response_dict(rec)

    if rec.status != "SENT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Biên bản chưa được gửi cho đại lý hoặc không ở trạng thái chờ xác nhận.",
        )

    client_ip = request.client.host if request.client else "unknown"

    rec.status = "CONFIRMED"
    rec.confirmed_at = get_utc_now()
    rec.confirmed_by_name = current_user.full_name or current_user.username
    rec.confirmed_ip = client_ip
    rec.updated_at = get_utc_now()

    db.commit()
    db.refresh(rec)
    return to_response_dict(rec)


@router.post("/{rec_id}/dispute", response_model=DebtReconciliationResponse)
def dispute_reconciliation_by_dealer_endpoint(
    rec_id: int,
    payload: DebtDisputeRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Đại lý phản hồi số liệu bị lệch kèm lý do và số tiền theo sổ sách của đại lý (Chuyển trạng thái sang DISPUTED).
    """
    rec = db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.id == rec_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biên bản đối chiếu ID {rec_id}.",
        )

    if _is_customer_user(current_user):
        dealer = _resolve_authenticated_dealer(current_user, db)
        if rec.dealer_id != dealer.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền phản hồi biên bản của đại lý khác.",
            )

    if rec.status != "SENT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chỉ có thể báo chênh lệch đối với biên bản đang ở trạng thái chờ xác nhận.",
        )

    rec.status = "DISPUTED"
    rec.dealer_reported_balance = payload.dealer_reported_balance
    rec.dealer_dispute_reason = payload.reason
    rec.disputed_at = get_utc_now()
    rec.updated_at = get_utc_now()

    db.commit()
    db.refresh(rec)
    return to_response_dict(rec)


@router.post("/{rec_id}/resolve", response_model=DebtReconciliationResponse)
def resolve_dispute_endpoint(
    rec_id: int,
    payload: DebtResolveRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Kế toán ghi nhận giải quyết chênh lệch và cập nhật ghi chú giải trình.
    """
    _check_accountant_or_admin(current_user)

    rec = db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.id == rec_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biên bản đối chiếu ID {rec_id}.",
        )

    rec.resolution_note = payload.resolution_note
    rec.updated_at = get_utc_now()

    db.commit()
    db.refresh(rec)
    return to_response_dict(rec)


@router.delete("/{rec_id}")
def delete_reconciliation_endpoint(
    rec_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Kế toán hủy / xóa biên bản nháp hoặc biên bản chưa xác nhận.
    """
    _check_accountant_or_admin(current_user)

    rec = db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.id == rec_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biên bản đối chiếu ID {rec_id}.",
        )

    if rec.status == "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể xóa biên bản đã được Đại lý ký duyệt xác nhận.",
        )

    db.delete(rec)
    db.commit()
    return {"message": "Đã xóa biên bản đối chiếu thành công."}
