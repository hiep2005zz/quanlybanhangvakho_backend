# backend/app/models/discount.py
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Unicode, UnicodeText
from sqlalchemy.orm import relationship
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class DiscountPolicyEntity(Base):
    __tablename__ = "discount_policies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(Unicode(255), nullable=False)
    title = Column(Unicode(255), nullable=True)
    category = Column(String(100), default="ALL")
    target_dealer_type = Column(String(100), default="ALL")
    target_group = Column(String(100), default="all")
    description = Column(UnicodeText, nullable=True)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    status = Column(String(50), default="active")
    created_by = Column(Unicode(100), nullable=True, default="admin")
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    tiers = relationship(
        "DiscountTierEntity",
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="DiscountTierEntity.min_quantity",
        lazy="joined",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "title": self.title or self.name,
            "category": self.category or "ALL",
            "target_dealer_type": self.target_dealer_type or "ALL",
            "target_group": self.target_group or "all",
            "description": self.description or "",
            "start_date": self.start_date or "",
            "end_date": self.end_date,
            "is_active": self.is_active,
            "status": self.status or ("active" if self.is_active else "expired"),
            "tiers": [t.to_dict() for t in self.tiers] if self.tiers else [],
            "created_by": self.created_by or "admin",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DiscountTierEntity(Base):
    __tablename__ = "discount_tiers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    policy_id = Column(Integer, ForeignKey("discount_policies.id", ondelete="CASCADE"), nullable=False, index=True)
    min_quantity = Column(Integer, nullable=False, default=0)
    max_quantity = Column(Integer, nullable=True)
    discount_percent = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=get_utc_now)

    policy = relationship("DiscountPolicyEntity", back_populates="tiers")

    def to_dict(self):
        return {
            "id": self.id,
            "min_quantity": self.min_quantity,
            "max_quantity": self.max_quantity,
            "discount_percent": self.discount_percent,
        }
