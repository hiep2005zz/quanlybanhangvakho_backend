# backend/app/models/price_book.py
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Unicode, UnicodeText
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.entities import ProductEntity

def get_utc_now():
    return datetime.now(timezone.utc)

class PriceBookEntity(Base):
    __tablename__ = "price_books"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    customer_group = Column(String(50), nullable=False, index=True)
    valid_from = Column(DateTime, nullable=False, index=True)
    valid_to = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), default="ACTIVE")
    version = Column(Integer, default=1)
    parent_id = Column(Integer, ForeignKey("price_books.id"), nullable=True)
    is_locked = Column(Boolean, default=False)
    note = Column(UnicodeText, nullable=True)
    created_by = Column(Unicode(100), nullable=True, default="admin")
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    items = relationship("PriceBookItemEntity", backref="price_book", cascade="all, delete-orphan")


class PriceBookItemEntity(Base):
    __tablename__ = "price_book_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    price_book_id = Column(Integer, ForeignKey("price_books.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    price = Column(Float, nullable=True, default=0.0)
    min_price = Column(Float, nullable=True, default=0.0)
    sale_price = Column(Float, nullable=False, default=0.0)
    floor_price = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=get_utc_now)

    product = relationship("ProductEntity")
