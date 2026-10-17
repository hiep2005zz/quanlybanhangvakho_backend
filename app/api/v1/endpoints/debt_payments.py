# backend/app/api/v1/endpoints/debt_payments.py
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.models.entities import DealerEntity
from app.schemas.auth import UserResponse
from app.schemas.debt_payment import (
    CreatePaymentReceiptRequest,
    PreviewFifoRequest,
    FifoPreviewItem,
    CreateAdjustmentRequest,
    PaymentReceiptResponse,
    PaymentAdjustmentResponse,
    DealerDebtSummaryResponse,
    InvoiceAllocationOption,
)
from app.services.debt_payment_service import (
    get_dealer_debt_summary,
    get_dealer_invoices_for_allocation,
    preview_fifo_allocation,
    create_payment_receipt,
    get_payment_receipts_list,
    get_payment_receipt_detail,
    create_payment_adjustment,
    sync_all_dealers_invoices,
)

router = APIRouter()


@router.get("/dealers", response_model=List[DealerDebtSummaryResponse])
def get_all_dealers_debt_summary_endpoint(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant"])
    ),
):
    """
    Lấy danh sách toàn bộ đại lý kèm thông tin công nợ tổng hợp (Tổng phải thu, Tổng đã thu, Tổng còn nợ).
    Dùng cho Kế toán lựa chọn đại lý trên màn hình ghi nhận thanh toán và đối trừ công nợ.
    """
    sync_all_dealers_invoices(db)
    dealers = db.query(DealerEntity).order_by(DealerEntity.name.asc()).all()
    results = []
    for d in dealers:
        summary = get_dealer_debt_summary(d.id, db)
        results.append(summary)
    return results


@router.get("/dealers/{dealer_id}/summary", response_model=DealerDebtSummaryResponse)
def get_single_dealer_debt_summary_endpoint(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant", "customer"])
    ),
):
    """
    Tiêu chí 1: Hiển thị ngay thông tin tổng hợp công nợ của đại lý khi chọn đại lý.
    """
    return get_dealer_debt_summary(dealer_id, db)


@router.get("/dealers/{dealer_id}/invoices", response_model=List[InvoiceAllocationOption])
def get_dealer_invoices_for_allocation_endpoint(
    dealer_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant", "customer"])
    ),
):
    """
    Tiêu chí 2: Lấy danh sách toàn bộ hoá đơn của đại lý phục vụ Kế toán xem và đối trừ.
    Hiển thị chính xác đại lý còn nợ hoá đơn nào, sắp xếp theo thứ tự hoá đơn cũ nhất xếp trước.
    """
    return get_dealer_invoices_for_allocation(dealer_id, db)


@router.post("/preview-fifo", response_model=List[FifoPreviewItem])
def preview_fifo_allocation_endpoint(
    payload: PreviewFifoRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "accountant"])
    ),
):
    """
    Tiêu chí 2: Xem trước phân bổ đối trừ tự động theo nguyên tắc hoá đơn cũ nhất trước (FIFO).
    """
    return preview_fifo_allocation(payload.dealer_id, payload.amount, db)


@router.post("", response_model=PaymentReceiptResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=PaymentReceiptResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_payment_receipt_endpoint(
    payload: CreatePaymentReceiptRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "accountant"])
    ),
):
    """
    TIÊU CHÍ 1, 2, 3, 4:
    Ghi nhận khoản thanh toán từ đại lý và thực hiện đối trừ vào công nợ theo FIFO hoặc Thủ công.
    - Không cho thu vượt tổng công nợ còn lại (Tiêu chí 4).
    - Cập nhật số tiền đã thu, số còn nợ và trạng thái hoá đơn (Tiêu chí 3).
    - Toàn bộ thao tác nằm trong 1 Database Transaction đảm bảo toàn vẹn dữ liệu.
    """
    receipt = create_payment_receipt(payload, current_user, db)
    # Trả về chi tiết đầy đủ
    return get_payment_receipt_detail(receipt.id, db)


@router.get("", response_model=List[PaymentReceiptResponse])
@router.get("/", response_model=List[PaymentReceiptResponse], include_in_schema=False)
def get_payment_receipts_endpoint(
    dealer_id: Optional[int] = Query(None, description="Lọc theo ID đại lý"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant", "customer"])
    ),
):
    """
    Lấy danh sách các khoản thanh toán / phiếu thu nợ đã ghi nhận trên hệ thống.
    """
    return get_payment_receipts_list(dealer_id, db)


@router.get("/{receipt_id}", response_model=PaymentReceiptResponse)
def get_payment_receipt_by_id_endpoint(
    receipt_id: int,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "sales", "accountant", "customer"])
    ),
):
    """
    Xem chi tiết phiếu thu nợ và chi tiết phân bổ hoá đơn.
    """
    return get_payment_receipt_detail(receipt_id, db)


@router.delete("/{receipt_id}")
def delete_payment_receipt_endpoint(
    receipt_id: int,
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "accountant"])
    ),
):
    """
    TIÊU CHÍ 5: CẤM XOÁ TUYỆT ĐỐI.
    Đã ghi nhận thì không xoá, chỉ lập bút toán điều chỉnh.
    """
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Khoản thanh toán đã ghi nhận không thể xoá theo quy định kế toán. Vui lòng lập bút toán điều chỉnh."
    )


@router.post("/{receipt_id}/adjust", response_model=PaymentAdjustmentResponse, status_code=status.HTTP_201_CREATED)
def create_payment_adjustment_endpoint(
    receipt_id: int,
    payload: CreateAdjustmentRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "accountant"])
    ),
):
    """
    TIÊU CHÍ 5: Lập bút toán điều chỉnh cho phiếu thu đã ghi nhận.
    - Bắt buộc nhập lý do điều chỉnh (*).
    - Hoàn trả số tiền đã đối trừ, khôi phục nợ và trạng thái ban đầu của các hoá đơn.
    - Chuyển trạng thái phiếu thu sang ADJUSTED.
    """
    adjustment = create_payment_adjustment(receipt_id, payload, current_user, db)
    return adjustment


@router.post("/sync-invoices")
def trigger_sync_invoices_endpoint(
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(
        require_roles(["admin", "sales_manager", "accountant"])
    ),
):
    """
    Kích hoạt đồng bộ hoá đơn từ đơn hàng và công nợ CSDL hiện có.
    """
    sync_all_dealers_invoices(db)
    return {"status": "ok", "message": "Đã đồng bộ dữ liệu hoá đơn đại lý thành công."}
