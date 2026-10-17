# backend/app/api/v1/endpoints/inventory_reports.py
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_roles, require_permission
from app.core.rbac import Permission
from app.schemas.auth import UserResponse
from app.schemas.inventory_report import TurnoverReportResponse, TurnoverSummaryResponse
from app.services.inventory_turnover_service import (
    get_turnover_report,
    compute_turnover_summary,
    generate_turnover_excel_file,
    _resolve_warehouse_filter,
    _slugify_warehouse_name,
)

router = APIRouter()

ALLOWED_REPORT_ROLES = ["admin", "warehouse_manager", "warehouse", "sales_manager", "accountant"]


@router.get("/turnover-report/summary", response_model=TurnoverSummaryResponse)
def get_inventory_turnover_summary_endpoint(
    warehouse_id: Optional[str] = Query("all", description="Mã kho hoặc 'all'"),
    warehouse_code: Optional[str] = Query(None, description="Mã kho thay thế hoặc 'all'"),
    category: Optional[str] = Query("all", description="Tên ngành hàng hoặc 'all'"),
    days: Optional[int] = Query(30, ge=1, le=365, description="Chu kỳ số ngày"),
    status: Optional[str] = Query("all", description="Lọc theo trạng thái"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ALLOWED_REPORT_ROLES)),
):
    """
    Endpoint thống kê độc lập: 5 thẻ KPI hiệu suất và tỷ lệ phân bổ giá trị tồn theo Kho & Nhóm hàng.
    Chỉ cần gọi 1 lần khi load trang hoặc khi thay đổi bộ lọc kho/ngành hàng/chu kỳ ngày.
    """
    effective_wh = warehouse_code or warehouse_id or "all"
    summary, wh_breakdown, cat_breakdown, warehouses, categories = compute_turnover_summary(
        db=db,
        warehouse_id=effective_wh,
        category=category,
        days=days or 30,
        status_filter=status,
    )
    return TurnoverSummaryResponse(
        summary=summary,
        warehouse_breakdown=wh_breakdown,
        category_breakdown=cat_breakdown,
        warehouses=warehouses,
        categories=categories,
    )


@router.get("/turnover-report", response_model=TurnoverReportResponse)
def get_inventory_turnover_report_endpoint(
    warehouse_id: Optional[str] = Query("all", description="Mã kho hoặc 'all' cho tất cả kho"),
    warehouse_code: Optional[str] = Query(None, description="Mã kho thay thế hoặc 'all'"),
    category: Optional[str] = Query("all", description="Tên ngành hàng hoặc 'all'"),
    days: Optional[int] = Query(30, ge=1, le=365, description="Chu kỳ tính toán số ngày (mặc định 30)"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo mã SKU hoặc tên sản phẩm"),
    status: Optional[str] = Query("all", description="Lọc theo trạng thái luân chuyển ('all', 'stagnant', 'slow', 'normal', 'fast', 'out_of_stock')"),
    page: Optional[int] = Query(1, ge=1, description="Số trang (phân trang)"),
    page_size: Optional[int] = Query(20, ge=1, le=100, description="Kích thước trang"),
    include_summary: Optional[bool] = Query(True, description="Có tính kèm thẻ KPI và phân bổ không"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ALLOWED_REPORT_ROLES)),
):
    """
    Báo cáo Tồn kho & Vòng quay hàng hóa.
    - include_summary=True: Trả về đầy đủ summary + 20 dòng bảng trang hiện tại.
    - include_summary=False: Tối ưu phân trang SQL siêu tốc (chỉ truy vấn đúng 20 dòng với offset/limit, dưới 50ms).
    """
    effective_wh = warehouse_code or warehouse_id or "all"
    return get_turnover_report(
        db=db,
        warehouse_id=effective_wh,
        category=category,
        days=days or 30,
        search=search,
        status_filter=status,
        page=page or 1,
        page_size=page_size or 20,
        include_summary=include_summary if include_summary is not None else True,
    )


@router.get("/turnover-report/export")
def export_inventory_turnover_excel_endpoint(
    warehouse_id: Optional[str] = Query(None, description="Mã kho hoặc 'all'"),
    warehouse_code: Optional[str] = Query(None, description="Mã kho thay thế hoặc 'all'"),
    category: Optional[str] = Query("all", description="Tên ngành hàng hoặc 'all'"),
    days: Optional[int] = Query(30, ge=1, le=365, description="Chu kỳ số ngày"),
    search: Optional[str] = Query(None, description="Tìm kiếm SKU hoặc tên sản phẩm"),
    status: Optional[str] = Query("all", description="Lọc theo trạng thái"),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles(ALLOWED_REPORT_ROLES)),
):
    """
    Xuất dữ liệu báo cáo Tồn kho & Vòng quay hàng hóa ra file Excel (.xlsx).
    Định dạng chuẩn 2 sheet: Chi tiết SKU và Phân bổ theo Kho & Nhóm hàng.
    Tự động đặt tên file linh hoạt theo phạm vi kho được lọc và ngày xuất.
    """
    target_wh = warehouse_code or warehouse_id
    if not target_wh or target_wh.strip() == "":
        target_wh = "all"

    excel_stream = generate_turnover_excel_file(
        db=db,
        warehouse_id=target_wh,
        category=category,
        days=days or 30,
        search=search,
        status_filter=status,
        exported_by=current_user.full_name or current_user.username,
    )

    _, _, wh_label, *_ = _resolve_warehouse_filter(db, target_wh)
    wh_slug = _slugify_warehouse_name(wh_label if target_wh != "all" else "Tat_Ca")
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"Bao_Cao_Ton_Kho_Vong_Quay_{wh_slug}_{date_str}.xlsx"

    return StreamingResponse(
        excel_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
