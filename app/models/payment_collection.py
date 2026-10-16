# backend/app/models/payment_collection.py
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


class PaymentCollectionEntity(Base):
    """
    Phân hệ Thu tiền theo tuyến & Nộp tiền về công ty (Route Payment Collection & Remittance).
    Giải quyết nghiệp vụ:
    1. Nhân viên kinh doanh ghi nhận khoản thu ngay tại cửa hàng/đại lý kèm ảnh biên lai.
    2. Khoản thu ở trạng thái 'Chờ nộp' (PENDING_REMITTANCE) cho tới khi nhân viên nộp (SUBMITTED)
       và Kế toán xác nhận đã nhận tiền (CONFIRMED).
    3. Cung cấp dữ liệu phục vụ Bảng đối chiếu tiền thu theo từng nhân viên và từng ngày (Reconciliation).
    """
    __tablename__ = "payment_collections"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)

    # Nhân viên kinh doanh thu tiền
    sales_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sales_name = Column(Unicode(150), nullable=False)

    # Đại lý / Cửa hàng nộp tiền
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    dealer_name = Column(Unicode(255), nullable=False)

    # Đơn hàng liên quan (nếu thu theo đơn hàng)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    order_code = Column(String(50), nullable=True)

    # Thông tin khoản thu
    amount = Column(Float, nullable=False, default=0.0)
    payment_method = Column(String(30), default="CASH", nullable=False)  # CASH (Tiền mặt) | BANK_TRANSFER (Chuyển khoản)
    receipt_image_url = Column(UnicodeText, nullable=True)  # Base64 string hoặc URL ảnh biên lai
    collection_date = Column(DateTime, nullable=False, default=get_utc_now)
    route_name = Column(Unicode(150), nullable=True)  # Tên tuyến bán hàng
    note = Column(UnicodeText, nullable=True)

    # Trạng thái luồng tiền: PENDING_REMITTANCE (Chờ nộp) -> SUBMITTED (Đã nộp) -> CONFIRMED (Đã xác nhận) | REJECTED (Từ chối)
    status = Column(String(30), default="PENDING_REMITTANCE", nullable=False)

    # Thông tin nộp tiền về công ty
    submitted_at = Column(DateTime, nullable=True)
    remittance_note = Column(UnicodeText, nullable=True)

    # Thông tin Kế toán đối chiếu & xác nhận đã nhận tiền
    confirmed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_by = Column(Unicode(150), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    confirmation_note = Column(UnicodeText, nullable=True)
    rejection_reason = Column(UnicodeText, nullable=True)

    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    # Quan hệ
    sales_rel = relationship("UserEntity", foreign_keys=[sales_id])
    dealer_rel = relationship("DealerEntity", foreign_keys=[dealer_id])
    order_rel = relationship("OrderEntity", foreign_keys=[order_id])
