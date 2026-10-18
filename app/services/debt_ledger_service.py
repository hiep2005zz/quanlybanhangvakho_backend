import io
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.models.entities import DealerEntity, OrderEntity, UserEntity
from app.schemas.debt_ledger import (
    AgeingBuckets,
    DealerDebtAgeingItem,
    DebtLedgerSummary,
    DebtLedgerResponse,
)

def compute_debt_ledger_data(
    db: Session,
    region: Optional[str] = None,
    assigned_sale_id: Optional[int] = None,
    overdue_only: bool = False,
    search: Optional[str] = None,
) -> DebtLedgerResponse:
    """
    Computes debt ledger per dealer with 4 ageing buckets:
    - 0-30 days
    - 31-60 days
    - 61-90 days
    - > 90 days
    Directly calculated from real database orders and dealers.
    """
    # 1. Fetch dealers
    dealers_query = db.query(DealerEntity)
    if region and region.strip():
        dealers_query = dealers_query.filter(DealerEntity.region == region.strip())
    if assigned_sale_id:
        dealers_query = dealers_query.filter(DealerEntity.assigned_sale_id == assigned_sale_id)
    if search and search.strip():
        s = f"%{search.strip().lower()}%"
        dealers_query = dealers_query.filter(
            (DealerEntity.name.ilike(s)) | (DealerEntity.code.ilike(s)) | (DealerEntity.phone.ilike(s))
        )
    dealers = dealers_query.all()

    # Cache users for sale staff names
    sales_users = {u.id: (u.full_name or u.username) for u in db.query(UserEntity).all()}

    # 2. Fetch all relevant orders
    # Active orders that count towards receivable: exclude CANCELLED, DRAFT, REJECTED
    orders = db.query(OrderEntity).filter(
        ~OrderEntity.status.in_(["CANCELLED", "DRAFT", "REJECTED"])
    ).all()

    # Map orders by dealer_id
    dealer_orders_map = {}
    for o in orders:
        if o.dealer_id not in dealer_orders_map:
            dealer_orders_map[o.dealer_id] = []
        dealer_orders_map[o.dealer_id].append(o)

    now = datetime.now(timezone.utc)
    items: List[DealerDebtAgeingItem] = []

    for d in dealers:
        d_orders = dealer_orders_map.get(d.id, [])
        total_receivable = 0.0
        total_paid = 0.0
        remaining_debt = 0.0

        b_0_30 = 0.0
        b_31_60 = 0.0
        b_61_90 = 0.0
        b_over_90 = 0.0
        oldest_days = 0

        # Map debt adjustments của đại lý này để giảm trừ trực tiếp vào bucket tuổi nợ của hóa đơn/đơn hàng gốc
        d_adjustments: dict[str, float] = {}
        try:
            from app.models.debt_adjustment import DebtAdjustmentEntity
            for adj in db.query(DebtAdjustmentEntity).filter(
                DebtAdjustmentEntity.dealer_id == d.id,
                DebtAdjustmentEntity.status == "POSTED",
            ).all():
                c = adj.original_invoice_code.replace("HD-", "").strip()
                d_adjustments[c] = d_adjustments.get(c, 0.0) + float(adj.total_adjusted_amount or 0.0)
        except Exception:
            pass

        # Map các khoản thanh toán thực tế từ SQL Server (receipt_vouchers & payment_collections)
        d_payments: dict[str, float] = {}
        try:
            from app.models.receipt_voucher import ReceiptVoucherEntity
            for rv in db.query(ReceiptVoucherEntity).filter(ReceiptVoucherEntity.dealer_id == d.id).all():
                if rv.order_code:
                    d_payments[rv.order_code] = d_payments.get(rv.order_code, 0.0) + float(rv.amount or 0.0)
        except Exception:
            pass
        try:
            from app.models.payment_collection import PaymentCollectionEntity
            for pc in db.query(PaymentCollectionEntity).filter(
                PaymentCollectionEntity.dealer_id == d.id,
                PaymentCollectionEntity.status == "CONFIRMED"
            ).all():
                if pc.order_code:
                    d_payments[pc.order_code] = d_payments.get(pc.order_code, 0.0) + float(pc.amount or 0.0)
        except Exception:
            pass

        for o in d_orders:
            amt = float(o.total_amount or 0.0)
            total_receivable += amt

            paid_offset = d_payments.get(o.order_code, 0.0)
            adj_offset = d_adjustments.get(o.order_code, 0.0)
            if o.status == "PAID" and paid_offset == 0:
                paid_offset = max(0.0, amt - adj_offset)

            # Outstanding debt order sau khi trừ thanh toán và giảm nợ từ trả hàng
            effective_rem = max(0.0, amt - paid_offset - adj_offset)
            total_paid += min(amt, paid_offset + adj_offset)
            remaining_debt += effective_rem

            if effective_rem <= 0:
                continue

            # Calculate age in days from order created_at (or due date)
            created_dt = o.created_at
            if created_dt:
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_days = max(0, (now - created_dt).days)
            else:
                age_days = 0

            if age_days > oldest_days:
                oldest_days = age_days

            # Ageing bucket placement (áp dụng số tiền nợ thực tế sau giảm trừ)
            if age_days <= 30:
                b_0_30 += effective_rem
            elif age_days <= 60:
                b_31_60 += effective_rem
            elif age_days <= 90:
                b_61_90 += effective_rem
            else:
                b_over_90 += effective_rem

        # Overdue condition:
        # If dealer has debt that exceeded allowed credit terms (max_debt_days or overdue_days_allowed)
        # Or has debt past 30 days
        allowed_grace = (d.max_debt_days or 30) + (d.overdue_days_allowed or 0)
        is_overdue = (remaining_debt > 0 and oldest_days > allowed_grace) or ((b_31_60 + b_61_90 + b_over_90) > 0)

        # Apply overdue_only filter if requested
        if overdue_only and not is_overdue:
            continue

        sale_name = sales_users.get(d.assigned_sale_id, "Chưa phân công") if d.assigned_sale_id else "Chưa phân công"

        item = DealerDebtAgeingItem(
            dealer_id=d.id,
            dealer_code=d.code,
            dealer_name=d.name,
            region=d.region or "Toàn quốc",
            assigned_sale_id=d.assigned_sale_id,
            assigned_sale_name=sale_name,
            phone=d.phone or "",
            credit_limit=float(d.credit_limit or 0.0),
            max_debt_days=d.max_debt_days or 30,
            overdue_days_allowed=d.overdue_days_allowed or 0,
            total_receivable=total_receivable,
            total_paid=total_paid,
            remaining_debt=remaining_debt,
            oldest_debt_days=oldest_days,
            is_overdue=is_overdue,
            ageing=AgeingBuckets(
                bucket_0_30=b_0_30,
                bucket_31_60=b_31_60,
                bucket_61_90=b_61_90,
                bucket_over_90=b_over_90,
            ),
        )
        items.append(item)

    # Sort: put highest debt and overdue items first
    items.sort(key=lambda x: (not x.is_overdue, -x.remaining_debt, -x.oldest_debt_days))

    # Summary
    summary = DebtLedgerSummary(
        total_dealers=len(items),
        total_receivable=sum(x.total_receivable for x in items),
        total_paid=sum(x.total_paid for x in items),
        total_remaining_debt=sum(x.remaining_debt for x in items),
        total_bucket_0_30=sum(x.ageing.bucket_0_30 for x in items),
        total_bucket_31_60=sum(x.ageing.bucket_31_60 for x in items),
        total_bucket_61_90=sum(x.ageing.bucket_61_90 for x in items),
        total_bucket_over_90=sum(x.ageing.bucket_over_90 for x in items),
    )

    return DebtLedgerResponse(items=items, summary=summary)


def export_debt_ledger_excel(data: DebtLedgerResponse) -> bytes:
    """
    Generates a professional Excel (.xlsx) file for the debt ledger and ageing report.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "So Cong No & Tuoi No"

    # Styling definitions
    title_font = Font(name="Segoe UI", size=15, bold=True, color="1E3A8A")
    subtitle_font = Font(name="Segoe UI", size=10, italic=True, color="4B5563")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=10)
    summary_font = Font(name="Segoe UI", size=10, bold=True)
    overdue_font = Font(name="Segoe UI", size=10, bold=True, color="DC2626")

    header_fill = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    bucket_header_fill = PatternFill(start_color="B45309", end_color="B45309", fill_type="solid") # Amber
    summary_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid") # Light yellow

    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )
    summary_border = Border(
        left=Side(style="thin", color="9CA3AF"),
        right=Side(style="thin", color="9CA3AF"),
        top=Side(style="medium", color="4B5563"),
        bottom=Side(style="double", color="111827"),
    )

    # Title block
    ws.merge_cells("A1:K1")
    ws["A1"] = "BÁO CÁO SỔ CÔNG NỢ THEO ĐẠI LÝ VÀ PHÂN TÍCH TUỔI NỢ"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:K2")
    ws["A2"] = f"Ngày xuất báo cáo: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Hệ thống Quản trị Bán hàng & Phân phối"
    ws["A2"].font = subtitle_font
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # Headers
    headers = [
        ("STT", 6),
        ("Mã ĐL", 12),
        ("Tên Đại Lý", 30),
        ("Khu Vực", 16),
        ("Phụ Trách", 20),
        ("Tổng Phải Thu (đ)", 18),
        ("Tổng Đã Thu (đ)", 18),
        ("Tổng Còn Nợ (đ)", 18),
        ("0 - 30 Ngày (đ)", 16),
        ("31 - 60 Ngày (đ)", 16),
        ("61 - 90 Ngày (đ)", 16),
        ("> 90 Ngày (đ)", 16),
        ("Trạng Thái Nợ", 16),
    ]

    header_row = 4
    ws.row_dimensions[header_row].height = 28

    for col_idx, (header_text, col_width) in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.value = header_text
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        # Highlighting ageing buckets with distinct color
        if col_idx in [9, 10, 11, 12]:
            cell.fill = bucket_header_fill
        else:
            cell.fill = header_fill
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = col_width

    # Data Rows
    current_row = 5
    for idx, item in enumerate(data.items, start=1):
        ws.row_dimensions[current_row].height = 22
        status_text = "Quá hạn" if item.is_overdue else "Trong hạn"

        row_values = [
            (idx, Alignment(horizontal="center")),
            (item.dealer_code, Alignment(horizontal="center")),
            (item.dealer_name, Alignment(horizontal="left")),
            (item.region, Alignment(horizontal="center")),
            (item.assigned_sale_name, Alignment(horizontal="left")),
            (item.total_receivable, Alignment(horizontal="right")),
            (item.total_paid, Alignment(horizontal="right")),
            (item.remaining_debt, Alignment(horizontal="right")),
            (item.ageing.bucket_0_30, Alignment(horizontal="right")),
            (item.ageing.bucket_31_60, Alignment(horizontal="right")),
            (item.ageing.bucket_61_90, Alignment(horizontal="right")),
            (item.ageing.bucket_over_90, Alignment(horizontal="right")),
            (status_text, Alignment(horizontal="center")),
        ]

        for col_idx, (val, align) in enumerate(row_values, start=1):
            cell = ws.cell(row=current_row, column=col_idx)
            cell.value = val
            cell.alignment = align
            cell.border = thin_border
            cell.font = data_font

            # Money formatting
            if col_idx in [6, 7, 8, 9, 10, 11, 12]:
                cell.number_format = "#,##0"

            # Color overdue cells
            if col_idx == 13 and item.is_overdue:
                cell.font = overdue_font
            if col_idx in [10, 11, 12] and val > 0:
                cell.font = overdue_font

        current_row += 1

    # Summary Row
    ws.row_dimensions[current_row].height = 26
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=5)
    summary_label_cell = ws.cell(row=current_row, column=1)
    summary_label_cell.value = f"TỔNG CỘNG ({data.summary.total_dealers} Đại lý)"
    summary_label_cell.font = summary_font
    summary_label_cell.alignment = Alignment(horizontal="center", vertical="center")

    summary_values = [
        (6, data.summary.total_receivable),
        (7, data.summary.total_paid),
        (8, data.summary.total_remaining_debt),
        (9, data.summary.total_bucket_0_30),
        (10, data.summary.total_bucket_31_60),
        (11, data.summary.total_bucket_61_90),
        (12, data.summary.total_bucket_over_90),
    ]

    for col in range(1, 14):
        c = ws.cell(row=current_row, column=col)
        c.fill = summary_fill
        c.border = summary_border

    for col_idx, sum_val in summary_values:
        cell = ws.cell(row=current_row, column=col_idx)
        cell.value = sum_val
        cell.font = summary_font
        cell.number_format = "#,##0"
        cell.alignment = Alignment(horizontal="right", vertical="center")

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
