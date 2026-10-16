from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.entities import (
    DealerEntity,
    CreditPolicyConfigEntity,
    DealerPaymentEntity,
    OrderEntity,
)
from app.models.dealer import DEALERS_DB, Dealer, save_dealers_db, load_dealers_db
from app.schemas.credit_policy import (
    CreditPolicyConfigResponse,
    CreditPolicyConfigUpdate,
    DealerPaymentCreate,
)
from app.schemas.auth import UserResponse
from app.services.audit_service import log_audit_event

CONFIG_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "credit_policy_config.json")

def _load_fallback_config() -> dict:
    if os.path.exists(CONFIG_JSON_PATH):
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "id": 1,
        "over_limit_action": "REQUIRE_APPROVAL",
        "overdue_action": "BLOCK",
        "auto_eval_enabled": True,
        "last_run_at": None,
        "updated_at": None,
        "updated_by": "Hệ thống",
    }

def _save_fallback_config(data: dict):
    try:
        with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving fallback credit policy config: {e}")

def get_credit_policy_config(db: Session) -> CreditPolicyConfigResponse:
    """
    Lấy cấu hình chính sách cảnh báo & xử lý vi phạm công nợ hiện hành (S7-04).
    """
    try:
        entity = db.query(CreditPolicyConfigEntity).filter(CreditPolicyConfigEntity.id == 1).first()
        if not entity:
            fallback = _load_fallback_config()
            entity = CreditPolicyConfigEntity(
                id=1,
                over_limit_action=fallback.get("over_limit_action", "REQUIRE_APPROVAL"),
                overdue_action=fallback.get("overdue_action", "BLOCK"),
                auto_eval_enabled=fallback.get("auto_eval_enabled", True),
                updated_by=fallback.get("updated_by", "Hệ thống"),
            )
            db.add(entity)
            db.commit()
            db.refresh(entity)
        return CreditPolicyConfigResponse(
            id=entity.id,
            over_limit_action=entity.over_limit_action,
            overdue_action=entity.overdue_action,
            auto_eval_enabled=entity.auto_eval_enabled,
            last_run_at=entity.last_run_at,
            updated_at=entity.updated_at,
            updated_by=entity.updated_by,
        )
    except Exception as e:
        print(f"Error getting credit policy config from DB: {e}")
        fallback = _load_fallback_config()
        return CreditPolicyConfigResponse(**fallback)

def update_credit_policy_config(
    db: Session,
    data: CreditPolicyConfigUpdate,
    user: UserResponse,
    request=None
) -> CreditPolicyConfigResponse:
    """
    Cập nhật cấu hình chính sách xử lý công nợ (BLOCK / REQUIRE_APPROVAL) và ghi nhật ký Audit Log.
    """
    old_config = get_credit_policy_config(db)
    entity = db.query(CreditPolicyConfigEntity).filter(CreditPolicyConfigEntity.id == 1).first()
    now = datetime.now(timezone.utc)
    if not entity:
        entity = CreditPolicyConfigEntity(id=1)
        db.add(entity)

    entity.over_limit_action = data.over_limit_action
    entity.overdue_action = data.overdue_action
    if data.auto_eval_enabled is not None:
        entity.auto_eval_enabled = data.auto_eval_enabled
    entity.updated_at = now
    entity.updated_by = user.full_name or user.username
    db.commit()
    db.refresh(entity)

    # Cập nhật file fallback
    saved_dict = {
        "id": entity.id,
        "over_limit_action": entity.over_limit_action,
        "overdue_action": entity.overdue_action,
        "auto_eval_enabled": entity.auto_eval_enabled,
        "last_run_at": entity.last_run_at.isoformat() if entity.last_run_at else None,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
        "updated_by": entity.updated_by,
    }
    _save_fallback_config(saved_dict)

    # Ghi Audit Log (S2-04)
    log_audit_event(
        db=db,
        user=user,
        action_type="CREDIT_POLICY_CONFIG_CHANGE",
        entity_type="CreditPolicy",
        entity_id="GLOBAL_POLICY",
        old_val={"over_limit_action": old_config.over_limit_action, "overdue_action": old_config.overdue_action},
        new_val={"over_limit_action": entity.over_limit_action, "overdue_action": entity.overdue_action},
        reason="Cập nhật chính sách tự động cảnh báo và khoá bán công nợ (S7-04)",
        request=request,
    )

    return CreditPolicyConfigResponse(
        id=entity.id,
        over_limit_action=entity.over_limit_action,
        overdue_action=entity.overdue_action,
        auto_eval_enabled=entity.auto_eval_enabled,
        last_run_at=entity.last_run_at,
        updated_at=entity.updated_at,
        updated_by=entity.updated_by,
    )

def evaluate_dealer_credit_flags(
    dealer_id: int,
    db: Session,
    user: Optional[UserResponse] = None,
    reason_prefix: str = "Tự động đánh giá",
    request=None
) -> dict:
    """
    AC1 & AC3: Đánh giá lại công nợ của 1 đại lý cụ thể:
    - Kiểm tra vượt hạn mức tiền: current_debt > credit_limit
    - Kiểm tra quá số ngày nợ cho phép: max_debt_age > overdue_days_allowed (hoặc max_debt_days)
    - Tự động gắn cờ cảnh báo nếu vi phạm 1 trong 2 điều kiện; gỡ cờ khi không còn vi phạm.
    - Ghi nhận Audit Log nếu có thay đổi trạng thái cờ.
    """
    from app.api.v1.endpoints.orders import ORDERS_DB, calculate_dealer_debt_summary
    dealer = DEALERS_DB.get(dealer_id)
    db_dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer and not db_dealer:
        load_dealers_db()
        dealer = DEALERS_DB.get(dealer_id)
        db_dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not db_dealer and not dealer:
        raise ValueError(f"Không tìm thấy đại lý có ID {dealer_id}")

    # Đồng bộ thông tin dealer object
    if not dealer and db_dealer:
        dealer = Dealer(
            id=db_dealer.id,
            code=db_dealer.code,
            name=db_dealer.name,
            credit_limit=db_dealer.credit_limit or 50000000.0,
            max_debt_days=db_dealer.max_debt_days or 30,
            overdue_days_allowed=db_dealer.overdue_days_allowed or db_dealer.max_debt_days or 30,
            status=db_dealer.status or "Đang hoạt động",
        )
        DEALERS_DB[dealer.id] = dealer

    debt_info = calculate_dealer_debt_summary(dealer, ORDERS_DB)
    current_debt = float(debt_info.get("current_debt", 0.0))
    credit_limit = float(debt_info.get("credit_limit", 50000000.0))
    max_debt_age = int(debt_info.get("max_debt_age", 0))
    allowed_days = int(debt_info.get("overdue_days_allowed", getattr(dealer, "max_debt_days", 30)) or 30)

    is_over_limit = current_debt > credit_limit
    is_overdue = max_debt_age > allowed_days
    has_flag = is_over_limit or is_overdue

    reasons: list[str] = []
    if is_over_limit:
        reasons.append(f"Vượt hạn mức công nợ: {current_debt:,.0f} đ > {credit_limit:,.0f} đ")
    if is_overdue:
        reasons.append(f"Quá hạn thanh toán: khoản nợ {max_debt_age} ngày > {allowed_days} ngày cho phép")
    flag_reason = " | ".join(reasons) if reasons else None

    old_flag = bool(getattr(dealer, "credit_warning_flag", False))
    old_reason = getattr(dealer, "credit_flag_reason", None)
    now = datetime.now(timezone.utc)

    # Cập nhật in-memory
    dealer.is_over_credit_limit = is_over_limit
    dealer.is_overdue = is_overdue
    dealer.credit_warning_flag = has_flag
    dealer.credit_flag_reason = flag_reason
    dealer.last_credit_evaluated_at = now
    DEALERS_DB[dealer.id] = dealer

    # Cập nhật DB
    if db_dealer:
        db_dealer.is_over_credit_limit = is_over_limit
        db_dealer.is_overdue = is_overdue
        db_dealer.credit_warning_flag = has_flag
        db_dealer.credit_flag_reason = flag_reason
        db_dealer.last_credit_evaluated_at = now
        db.commit()

    save_dealers_db()

    # Ghi nhận Audit Log nếu trạng thái gắn cờ thay đổi hoặc lý do thay đổi
    if old_flag != has_flag or (has_flag and old_reason != flag_reason):
        audit_action = "CREDIT_FLAG_CHANGE"
        log_reason = f"{reason_prefix}: Gắn cờ cảnh báo tín dụng ({flag_reason})" if has_flag else f"{reason_prefix}: Gỡ cờ cảnh báo do công nợ đã về mức an toàn"
        try:
            log_audit_event(
                db=db,
                user=user,
                action_type=audit_action,
                entity_type="CustomerDebt",
                entity_id=dealer.code,
                old_val={"credit_warning_flag": old_flag, "reason": old_reason},
                new_val={
                    "credit_warning_flag": has_flag,
                    "is_over_credit_limit": is_over_limit,
                    "is_overdue": is_overdue,
                    "reason": flag_reason,
                    "current_debt": current_debt,
                    "credit_limit": credit_limit,
                    "max_debt_age": max_debt_age,
                },
                reason=log_reason,
                request=request,
            )
        except Exception as audit_err:
            print(f"Error logging credit flag audit event: {audit_err}")

    return {
        "dealer_id": dealer.id,
        "dealer_code": dealer.code,
        "dealer_name": dealer.name,
        "is_over_credit_limit": is_over_limit,
        "is_overdue": is_overdue,
        "credit_warning_flag": has_flag,
        "credit_flag_reason": flag_reason,
        "current_debt": current_debt,
        "credit_limit": credit_limit,
        "max_debt_age": max_debt_age,
        "overdue_days_allowed": allowed_days,
        "evaluated_at": now.isoformat(),
    }

def evaluate_all_dealers_credit_flags(
    db: Session,
    user: Optional[UserResponse] = None,
    request=None
) -> list[dict]:
    """
    AC3: Tác vụ định kỳ đánh giá lại toàn bộ đại lý đang hoạt động mỗi ngày 1 lần.
    """
    load_dealers_db()
    results = []
    dealers = list(DEALERS_DB.values())
    for d in dealers:
        try:
            res = evaluate_dealer_credit_flags(
                dealer_id=d.id,
                db=db,
                user=user,
                reason_prefix="Đánh giá định kỳ hàng ngày",
                request=request,
            )
            results.append(res)
        except Exception as e:
            print(f"Lỗi khi đánh giá đại lý {d.id} ({d.name}): {e}")

    # Cập nhật thời điểm chạy gần nhất trên config
    try:
        now = datetime.now(timezone.utc)
        cfg = db.query(CreditPolicyConfigEntity).filter(CreditPolicyConfigEntity.id == 1).first()
        if cfg:
            cfg.last_run_at = now
            db.commit()
            fallback = _load_fallback_config()
            fallback["last_run_at"] = now.isoformat()
            _save_fallback_config(fallback)
    except Exception as e:
        print(f"Lỗi cập nhật last_run_at: {e}")

    return results

def record_dealer_payment(
    dealer_id: int,
    data: DealerPaymentCreate,
    db: Session,
    user: UserResponse,
    request=None
) -> dict:
    """
    AC3 & S7-02: Ghi nhận thu tiền / thanh toán & đối trừ công nợ đại lý.
    Sau khi ghi nhận thanh toán thành công, NGAY LẬP TỨC kích hoạt đánh giá lại công nợ của đại lý:
    Nếu trả hết nợ quá hạn và hạ dư nợ xuống dưới hạn mức -> Tự động gỡ cờ ngay lập tức!
    """
    from app.api.v1.endpoints.orders import ORDERS_DB
    dealer = DEALERS_DB.get(dealer_id)
    db_dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer and not db_dealer:
        load_dealers_db()
        dealer = DEALERS_DB.get(dealer_id)
        db_dealer = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
    if not dealer and not db_dealer:
        raise ValueError(f"Không tìm thấy đại lý có ID {dealer_id}")

    dealer_name = dealer.name if dealer else db_dealer.name
    dealer_code = dealer.code if dealer else db_dealer.code

    now = datetime.now(timezone.utc)
    payment_code = f"TT{dealer_id}{int(now.timestamp())}"

    # Đối trừ các đơn hàng chưa thanh toán của đại lý theo FIFO (đơn cũ nhất trước)
    remaining_payment = float(data.amount)
    unpaid_db_orders = db.query(OrderEntity).filter(
        OrderEntity.dealer_id == dealer_id,
        OrderEntity.status.not_in(["PAID", "CANCELLED", "REJECTED"])
    ).order_by(OrderEntity.created_at.asc()).all()

    settled_orders = []
    for order in unpaid_db_orders:
        if remaining_payment <= 0:
            break
        order_amount = float(order.total_amount or 0.0)
        if order_amount <= remaining_payment:
            order.status = "PAID"
            remaining_payment -= order_amount
            settled_orders.append(order.order_code)
            if order.id in ORDERS_DB:
                ORDERS_DB[order.id]["status"] = "PAID"
        else:
            # Thanh toán một phần
            order.total_amount = round(order_amount - remaining_payment, 2)
            if order.id in ORDERS_DB:
                ORDERS_DB[order.id]["total_amount"] = order.total_amount
            remaining_payment = 0
            settled_orders.append(f"{order.order_code} (một phần)")
            break

    # Cũng đồng bộ ORDERS_DB trong bộ nhớ nếu có đơn nào chưa lưu trong DB
    for o_id, o in ORDERS_DB.items():
        if remaining_payment <= 0:
            break
        if o.get("dealer_id") == dealer_id and o.get("status") not in ("PAID", "CANCELLED", "REJECTED"):
            amt = float(o.get("total_amount", 0.0) or 0.0)
            if amt <= remaining_payment:
                o["status"] = "PAID"
                remaining_payment -= amt
                settled_orders.append(o.get("order_code", f"ORD#{o_id}"))

    # Tạo bản ghi thanh toán
    payment_record = DealerPaymentEntity(
        payment_code=payment_code,
        dealer_id=dealer_id,
        dealer_name=dealer_name,
        amount=data.amount,
        payment_method=data.payment_method or "Chuyển khoản",
        reference_code=data.reference_code,
        note=data.note,
        recorded_by=user.full_name or user.username,
        recorded_at=now,
    )
    db.add(payment_record)
    db.commit()
    db.refresh(payment_record)

    # Ghi nhận Audit Log (S2-04)
    settle_desc = f"Đối trừ các đơn: {', '.join(settled_orders)}" if settled_orders else "Ghi nhận thanh toán giảm nợ chung"
    log_audit_event(
        db=db,
        user=user,
        action_type="PAYMENT_RECORD",
        entity_type="CustomerDebt",
        entity_id=dealer_code,
        old_val={"action": "record_payment"},
        new_val={
            "payment_code": payment_code,
            "amount": data.amount,
            "payment_method": data.payment_method,
            "settled_orders": settled_orders,
        },
        reason=f"Thu tiền công nợ đại lý ({data.amount:,.0f} đ). {settle_desc}. Ghi chú: {data.note or 'Không'}",
        request=request,
    )

    # AC3: NGAY LẬP TỨC ĐÁNH GIÁ LẠI CÔNG NỢ ĐẠI LÝ SAU KHI THU TIỀN!
    eval_result = evaluate_dealer_credit_flags(
        dealer_id=dealer_id,
        db=db,
        user=user,
        reason_prefix="Ghi nhận thu tiền (S7-02)",
        request=request,
    )

    return {
        "id": payment_record.id,
        "payment_code": payment_record.payment_code,
        "dealer_id": payment_record.dealer_id,
        "dealer_name": payment_record.dealer_name,
        "amount": payment_record.amount,
        "payment_method": payment_record.payment_method,
        "reference_code": payment_record.reference_code,
        "note": payment_record.note,
        "recorded_by": payment_record.recorded_by,
        "recorded_at": payment_record.recorded_at.isoformat(),
        "credit_status": eval_result,
    }
