# backend/app/models/debt_reconciliation.py
from __future__ import annotations
from datetime import datetime, timezone
import json
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Unicode,
    UnicodeText,
    ForeignKey,
)
from app.core.database import Base


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DebtReconciliationEntity(Base):
    """
    Thực thể Biên bản đối chiếu công nợ định kỳ giữa Doanh nghiệp và Đại lý.
    Trạng thái:
    - DRAFT: Bản nháp của kế toán
    - SENT: Đã gửi cho đại lý, chờ xác nhận
    - CONFIRMED: Đại lý đã ký duyệt xác nhận khớp
    - DISPUTED: Đại lý báo chênh lệch số liệu
    - CANCELLED: Đã hủy
    """
    __tablename__ = "debt_reconciliations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)

    # Đại lý đối chiếu
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_code = Column(String(50), nullable=False)
    dealer_name = Column(Unicode(255), nullable=False)
    dealer_phone = Column(String(50), nullable=True)
    dealer_address = Column(Unicode(500), nullable=True)

    # Kỳ đối chiếu
    from_date = Column(DateTime, nullable=False, index=True)
    to_date = Column(DateTime, nullable=False, index=True)
    period_label = Column(Unicode(100), nullable=False)  # Ví dụ: "Tháng 10/2026" hoặc "01/10/2026 - 31/10/2026"

    # Số liệu tài chính
    opening_balance = Column(Float, default=0.0, nullable=False)  # Dư nợ đầu kỳ
    total_increase = Column(Float, default=0.0, nullable=False)   # Phát sinh tăng (Giao hàng/Hóa đơn)
    total_decrease = Column(Float, default=0.0, nullable=False)   # Phát sinh giảm (Thanh toán/Thu tiền)
    closing_balance = Column(Float, default=0.0, nullable=False)  # Dư nợ cuối kỳ

    # Trạng thái
    status = Column(String(30), default="DRAFT", nullable=False, index=True)

    # Snapshot chi tiết các chứng từ trong kỳ (Lưu JSON để đóng băng số liệu)
    # Gồm: invoices (hóa đơn phát sinh tăng), payments (phiếu thu phát sinh giảm)
    details_json = Column(UnicodeText, nullable=True)

    # Phản hồi từ đại lý (nếu báo lệch)
    dealer_reported_balance = Column(Float, nullable=True)  # Số dư nợ theo đại lý
    dealer_dispute_reason = Column(UnicodeText, nullable=True)  # Lý do lệch
    disputed_at = Column(DateTime, nullable=True)

    # Xác nhận từ đại lý (nếu đồng ý)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by_name = Column(Unicode(150), nullable=True)
    confirmed_ip = Column(String(50), nullable=True)

    # Thông tin người lập & giải trình
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_name = Column(Unicode(150), nullable=True)
    note = Column(UnicodeText, nullable=True)
    resolution_note = Column(UnicodeText, nullable=True)  # Ghi chú xử lý chênh lệch của kế toán

    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    @property
    def details(self):
        if self.details_json:
            try:
                res = json.loads(self.details_json)
                if isinstance(res, dict):
                    return res
            except Exception:
                pass
        return {"invoices": [], "payments": []}

    @details.setter
    def details(self, val):
        self.details_json = json.dumps(val or {"invoices": [], "payments": []}, ensure_ascii=False)
