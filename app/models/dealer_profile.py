from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class DealerProfileEntity(Base):
    """
    Thông tin mở rộng của đại lý/khách hàng.

    Tách riêng khỏi bảng dealers để không ảnh hưởng
    cấu trúc dealer cũ.
    """

    __tablename__ = "dealer_profiles"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True
    )

    dealer_id = Column(
        Integer,
        ForeignKey("dealers.id"),
        nullable=False,
        unique=True,
        index=True
    )

    tax_id = Column(
        String(50),
        nullable=True,
        index=True
    )

    customer_group = Column(
        String(100),
        nullable=False,
        default="Đại lý cấp 1"
    )

    region = Column(
        String(100),
        nullable=False,
        default="Chưa xác định"
    )

    status = Column(
        String(50),
        nullable=False,
        default="Đang hoạt động"
    )

    price_list = Column(
        String(150),
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=get_utc_now
    )

    updated_at = Column(
        DateTime,
        default=get_utc_now,
        onupdate=get_utc_now
    )