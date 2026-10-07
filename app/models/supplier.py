# backend/app/models/supplier.py
"""
Bảng nhà cung cấp (suppliers).

Lưu ý:
- Tên, người liên hệ, điều khoản dùng Unicode (NVARCHAR) để lưu đúng tiếng Việt trên SQL Server.
- Mã số thuế là duy nhất nhưng được phép để trống: dùng unique index CÓ ĐIỀU KIỆN
  (chỉ áp dụng khi tax_code khác NULL), vì SQL Server coi nhiều giá trị NULL là trùng nhau.
- Không xoá nhà cung cấp, chỉ ngừng giao dịch (is_active = False) để phiếu nhập
  sau này vẫn truy nguyên được nguồn hàng.
"""
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Unicode, func, text

from app.core.database import Base


class SupplierEntity(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(30), nullable=False, unique=True, index=True)
    name = Column(Unicode(255), nullable=False)
    tax_code = Column(String(14), nullable=True)
    contact_person = Column(Unicode(255), nullable=True)
    payment_terms = Column(Unicode(255), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    inactive_reason = Column(Unicode(255), nullable=True)
    deactivated_at = Column(DateTime, nullable=True)
    deactivated_by = Column(String(100), nullable=True)

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index(
            "uq_suppliers_tax_code",
            "tax_code",
            unique=True,
            mssql_where=text("tax_code IS NOT NULL"),
        ),
    )