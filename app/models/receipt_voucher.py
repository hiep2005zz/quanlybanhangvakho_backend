# backend/app/models/receipt_voucher.py
from datetime import datetime, timezone
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
from sqlalchemy.orm import relationship
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class ReceiptVoucherEntity(Base):
    """
    Bảng receipt_vouchers: Lưu vết Phiếu thu tiền công nợ đại lý trên SQL Server.
    Đảm bảo dữ liệu thanh toán công nợ được lưu trữ tập trung, bền vững trong SQL Server.
    """
    __tablename__ = "receipt_vouchers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    receipt_code = Column(String(50), unique=True, index=True, nullable=False)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_code = Column(String(50), nullable=False)
    dealer_name = Column(Unicode(255), nullable=False)
    dealer_address = Column(Unicode(500), nullable=True)
    dealer_phone = Column(String(50), nullable=True)
    dealer_tax_code = Column(String(50), nullable=True)
    order_code = Column(String(50), nullable=True, index=True)
    amount = Column(Float, nullable=False, default=0.0)
    amount_in_words = Column(Unicode(500), nullable=True)
    payment_method = Column(Unicode(50), default="Tiền mặt", nullable=False)
    reason = Column(Unicode(500), nullable=True)
    payer_name = Column(Unicode(150), nullable=True)
    collector_name = Column(Unicode(150), nullable=True)
    collector_role = Column(Unicode(100), nullable=True)
    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    note = Column(UnicodeText, nullable=True)

    dealer = relationship("DealerEntity", foreign_keys=[dealer_id])
