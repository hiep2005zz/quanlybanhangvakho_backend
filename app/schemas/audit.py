# backend/app/schemas/audit.py
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field

class AuditLogItem(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    action_type: str
    entity_type: str
    entity_id: str
    old_values: Optional[str] = None
    new_values: Optional[str] = None
    reason: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: str

class AuditLogListResponse(BaseModel):
    items: List[AuditLogItem]
    total: int
    page: int
    page_size: int
    total_pages: int

class AuditLogCreateManual(BaseModel):
    action_type: str
    entity_type: str
    entity_id: str
    old_values: Optional[Any] = None
    new_values: Optional[Any] = None
    reason: Optional[str] = None
