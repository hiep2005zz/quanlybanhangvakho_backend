from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class CreditPolicyConfigResponse(BaseModel):
    id: int = 1
    over_limit_action: str = "REQUIRE_APPROVAL"  # BLOCK | REQUIRE_APPROVAL
    overdue_action: str = "BLOCK"                # BLOCK | REQUIRE_APPROVAL
    auto_eval_enabled: bool = True
    last_run_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class CreditPolicyConfigUpdate(BaseModel):
    over_limit_action: str = Field(..., pattern="^(BLOCK|REQUIRE_APPROVAL)$", description="Hành vi khi vượt hạn mức tiền: BLOCK hoặc REQUIRE_APPROVAL")
    overdue_action: str = Field(..., pattern="^(BLOCK|REQUIRE_APPROVAL)$", description="Hành vi khi quá hạn ngày nợ: BLOCK hoặc REQUIRE_APPROVAL")
    auto_eval_enabled: Optional[bool] = True

class DealerPaymentCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Số tiền thanh toán (VNĐ)")
    payment_method: Optional[str] = Field("Chuyển khoản", description="Phương thức: Chuyển khoản, Tiền mặt")
    reference_code: Optional[str] = Field(None, description="Mã giao dịch ngân hàng / Uỷ nhiệm chi")
    note: Optional[str] = Field(None, description="Ghi chú thanh toán")

class DealerPaymentResponse(BaseModel):
    id: int
    payment_code: str
    dealer_id: int
    dealer_name: str
    amount: float
    payment_method: str
    reference_code: Optional[str] = None
    note: Optional[str] = None
    recorded_by: str
    recorded_at: datetime
    credit_status: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)

class CreditEvaluationResult(BaseModel):
    dealer_id: int
    dealer_code: str
    dealer_name: str
    is_over_credit_limit: bool
    is_overdue: bool
    credit_warning_flag: bool
    credit_flag_reason: Optional[str] = None
    current_debt: float
    credit_limit: float
    max_debt_age: int
    overdue_days_allowed: int
    evaluated_at: datetime
