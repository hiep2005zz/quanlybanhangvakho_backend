# backend/app/api/v1/endpoints/agency_debts.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.entities import DealerEntity
from app.schemas.auth import UserResponse
from app.services.invoice_service import (
    calculate_agency_debt_summary,
    get_agency_invoices_list,
    get_invoice_detail,
    generate_invoice_xml,
    generate_invoice_pdf,
)

router = APIRouter()


def _resolve_authenticated_dealer(current_user: UserResponse, db: Session, target_dealer_id: Optional[int] = None) -> DealerEntity:
    """
    Ràng buộc bảo mật Zero-Trust:
    - Nếu người dùng có vai trò Đại lý (Customer), BẮT BUỘC chỉ trả về đại lý gắn liền với tài khoản của họ.
    - Quản trị viên/Kế toán có thể xem theo dealer_id được truyền lên.
    """
    user_roles = current_user.get_roles() if hasattr(current_user, "get_roles") else [current_user.role]
    is_customer = "customer" in [r.lower() for r in user_roles] and not any(
        r.lower() in ["admin", "sales_manager", "sales", "accountant"] for r in user_roles
    )

    if is_customer:
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
        if not dealer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy hồ sơ đại lý liên kết với tài khoản của bạn.",
            )
        return dealer

    # Dành cho Quản trị viên / Kế toán / Sales
    allowed_staff = {"admin", "sales_manager", "sales", "accountant"}
    if not (set([r.lower() for r in user_roles]) & allowed_staff):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập thông tin công nợ đại lý.",
        )

    if target_dealer_id is not None:
        dealer = db.query(DealerEntity).filter(DealerEntity.id == target_dealer_id).first()
        if not dealer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy đại lý có ID {target_dealer_id}.",
            )
        return dealer

    # Mặc định lấy đại lý đầu tiên nếu là admin tra cứu chung
    first_dealer = db.query(DealerEntity).first()
    if not first_dealer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hệ thống chưa có dữ liệu đại lý nào.",
        )
    return first_dealer


@router.get("/summary")
def get_agency_debt_summary_endpoint(
    dealer_id: Optional[int] = Query(None, description="ID đại lý (Chỉ dùng cho Quản trị viên/Kế toán)"),
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    1. Khối hiển thị Tổng quan (Summary Cards):
    - Tổng còn nợ: Tổng số tiền chưa thanh toán của tất cả hóa đơn đã phát hành.
    - Hạn mức còn lại: Hạn mức công nợ được cấp - Tổng còn nợ - (Giá trị đơn hàng đang chờ duyệt/xử lý nếu có).
    - Hạn thanh toán còn lại / Cảnh báo: Số ngày còn lại của hóa đơn sắp đến hạn nhất và số lượng hóa đơn đang quá hạn.
    """
    dealer = _resolve_authenticated_dealer(current_user, db, dealer_id)
    return calculate_agency_debt_summary(dealer, db)


@router.get("/invoices")
def get_agency_invoices_endpoint(
    status: Optional[str] = Query("ALL", description="Lọc trạng thái: ALL, UNPAID, PARTIAL, PAID, OVERDUE"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã hóa đơn hoặc mã đơn hàng"),
    page: int = Query(1, ge=1, description="Số thứ tự trang"),
    page_size: int = Query(10, ge=1, le=100, description="Kích thước trang"),
    dealer_id: Optional[int] = Query(None, description="ID đại lý (Chỉ dùng cho Quản trị viên/Kế toán)"),
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    2. Danh sách hóa đơn đính kèm (Invoice List Table):
    - Mã hóa đơn, Mã đơn hàng gốc, Ngày phát hành, Hạn thanh toán, Tổng tiền, Đã thanh toán, Còn nợ, Trạng thái.
    - Bộ lọc trạng thái, tìm kiếm và phân trang.
    """
    dealer = _resolve_authenticated_dealer(current_user, db, dealer_id)
    return get_agency_invoices_list(
        dealer=dealer,
        db=db,
        status_filter=status,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/invoices/{invoice_code}")
def get_agency_invoice_detail_endpoint(
    invoice_code: str,
    dealer_id: Optional[int] = Query(None, description="ID đại lý (Chỉ dùng cho Quản trị viên/Kế toán)"),
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    3. Xem hóa đơn: Lấy toàn bộ thông tin chi tiết hóa đơn phục vụ Modal xem và in ấn.
    """
    dealer = _resolve_authenticated_dealer(current_user, db, dealer_id)
    detail = get_invoice_detail(dealer.id, invoice_code, db)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy hóa đơn #{invoice_code} của đại lý {dealer.name}.",
        )
    return detail


@router.get("/invoices/{invoice_code}/download")
def download_agency_invoice_endpoint(
    invoice_code: str,
    format: Optional[str] = Query("xml", description="Định dạng tải về: xml hoặc json"),
    dealer_id: Optional[int] = Query(None, description="ID đại lý (Chỉ dùng cho Quản trị viên/Kế toán)"),
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Tải lại file hóa đơn đã phát hành về máy (hỗ trợ XML hóa đơn điện tử hoặc JSON chứng từ).
    """
    dealer = _resolve_authenticated_dealer(current_user, db, dealer_id)
    detail = get_invoice_detail(dealer.id, invoice_code, db)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy hóa đơn #{invoice_code}.",
        )

    fmt = (format or "pdf").lower()
    if fmt == "json":
        import json
        content = json.dumps(detail, ensure_ascii=False, indent=2).encode("utf-8")
        media_type = "application/json"
        filename = f"{invoice_code}.json"
    elif fmt == "xml":
        content = generate_invoice_xml(detail).encode("utf-8")
        media_type = "application/xml"
        filename = f"{invoice_code}.xml"
    else:
        # Mặc định xuất file PDF chuẩn A4 chất lượng cao
        content = generate_invoice_pdf(detail)
        media_type = "application/pdf"
        filename = f"{invoice_code}.pdf"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": media_type,
        },
    )
