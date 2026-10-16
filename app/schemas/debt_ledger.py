from typing import List, Optional
from pydantic import BaseModel

class AgeingBuckets(BaseModel):
    bucket_0_30: float = 0.0
    bucket_31_60: float = 0.0
    bucket_61_90: float = 0.0
    bucket_over_90: float = 0.0

class DealerDebtAgeingItem(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    region: Optional[str] = None
    assigned_sale_id: Optional[int] = None
    assigned_sale_name: Optional[str] = None
    phone: Optional[str] = None
    credit_limit: float = 0.0
    max_debt_days: int = 30
    overdue_days_allowed: int = 0
    total_receivable: float = 0.0  # Tổng phát sinh phải thu
    total_paid: float = 0.0        # Tổng đã thu
    remaining_debt: float = 0.0    # Tổng còn nợ
    oldest_debt_days: int = 0      # Số ngày nợ lâu nhất
    is_overdue: bool = False       # Có nợ quá hạn không
    ageing: AgeingBuckets          # 4 nhóm tuổi nợ

class DebtLedgerSummary(BaseModel):
    total_dealers: int = 0
    total_receivable: float = 0.0
    total_paid: float = 0.0
    total_remaining_debt: float = 0.0
    total_bucket_0_30: float = 0.0
    total_bucket_31_60: float = 0.0
    total_bucket_61_90: float = 0.0
    total_bucket_over_90: float = 0.0

class DebtLedgerResponse(BaseModel):
    items: List[DealerDebtAgeingItem]
    summary: DebtLedgerSummary
