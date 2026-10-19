import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models.entities import DealerEntity, OrderEntity, CreditPolicyConfigEntity, DealerPaymentEntity
from app.models.dealer import DEALERS_DB, Dealer, save_dealers_db, load_dealers_db
from app.api.v1.endpoints.orders import ORDERS_DB
from app.services.credit_policy_service import (
    evaluate_dealer_credit_flags,
    evaluate_all_dealers_credit_flags,
    record_dealer_payment,
    get_credit_policy_config,
    update_credit_policy_config,
)
from app.schemas.credit_policy import CreditPolicyConfigUpdate, DealerPaymentCreate

client = TestClient(app)

@pytest.fixture
def auth_headers_admin():
    token = create_access_token(subject="admin", role="admin", token_version=1)
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def auth_headers_accountant():
    token = create_access_token(subject="ketoan", role="accountant", token_version=1)
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def auth_headers_sales():
    token = create_access_token(subject="sales", role="sales", token_version=1)
    return {"Authorization": f"Bearer {token}"}

def test_credit_policy_config_get_and_update(auth_headers_accountant):
    """Kiểm tra lấy và cập nhật cấu hình chính sách công nợ (S7-04 AC2) với RBAC."""
    headers = auth_headers_accountant
    # 1. GET config
    res = client.get("/api/v1/dealers/credit-policy/config", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "over_limit_action" in data
    assert "overdue_action" in data

    # 2. PUT config - đổi thành BLOCK cho over_limit và REQUIRE_APPROVAL cho overdue
    update_payload = {
        "over_limit_action": "BLOCK",
        "overdue_action": "REQUIRE_APPROVAL",
        "auto_eval_enabled": True
    }
    put_res = client.put("/api/v1/dealers/credit-policy/config", json=update_payload, headers=headers)
    assert put_res.status_code == 200
    updated_data = put_res.json()
    assert updated_data["over_limit_action"] == "BLOCK"
    assert updated_data["overdue_action"] == "REQUIRE_APPROVAL"

    # Reset lại mặc định: over_limit = REQUIRE_APPROVAL, overdue = BLOCK
    client.put("/api/v1/dealers/credit-policy/config", json={
        "over_limit_action": "REQUIRE_APPROVAL",
        "overdue_action": "BLOCK",
        "auto_eval_enabled": True
    }, headers=headers)

def test_evaluate_dealer_credit_flags_over_limit():
    """AC1: Đại lý vượt hạn mức tiền -> Tự động gắn cờ cảnh báo."""
    with SessionLocal() as db:
        dealer_id = 9001
        db_d = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
        if not db_d:
            db_d = DealerEntity(
                id=dealer_id,
                code="DL_TEST_LIMIT",
                name="Đại Lý Test Vượt Hạn Mức",
                credit_limit=10000000.0,
                max_debt_days=30,
                overdue_days_allowed=30,
                status="Đang hoạt động"
            )
            db.add(db_d)
            db.commit()

        test_dealer = Dealer(
            id=dealer_id,
            code="DL_TEST_LIMIT",
            name="Đại Lý Test Vượt Hạn Mức",
            credit_limit=10000000.0, # 10 triệu
            max_debt_days=30,
            overdue_days_allowed=30,
            status="Đang hoạt động"
        )
        DEALERS_DB[dealer_id] = test_dealer

        # Tạo đơn nợ 15 triệu (chưa quá hạn nhưng vượt hạn mức 10 triệu)
        now = datetime.now(timezone.utc)
        ORDERS_DB[90011] = {
            "id": 90011,
            "order_code": "ORD_TEST_90011",
            "dealer_id": dealer_id,
            "dealer_name": test_dealer.name,
            "total_amount": 15000000.0,
            "status": "CONFIRMED",
            "created_at": (now - timedelta(days=5)).isoformat(),
        }

        # Đánh giá lại
        res = evaluate_dealer_credit_flags(dealer_id=dealer_id, db=db)
        assert res["credit_warning_flag"] is True
        assert res["is_over_credit_limit"] is True
        assert res["is_overdue"] is False
        assert "Vượt hạn mức" in res["credit_flag_reason"]

        # Kiểm tra object dealer trong memory
        assert DEALERS_DB[dealer_id].credit_warning_flag is True
        assert DEALERS_DB[dealer_id].is_over_credit_limit is True

def test_evaluate_dealer_credit_flags_overdue():
    """AC1: Đại lý có khoản nợ quá số ngày cho phép -> Tự động gắn cờ."""
    with SessionLocal() as db:
        dealer_id = 9002
        db_d = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
        if not db_d:
            db_d = DealerEntity(
                id=dealer_id,
                code="DL_TEST_OVERDUE",
                name="Đại Lý Test Nợ Quá Hạn",
                credit_limit=50000000.0,
                max_debt_days=15,
                overdue_days_allowed=15,
                status="Đang hoạt động"
            )
            db.add(db_d)
            db.commit()

        test_dealer = Dealer(
            id=dealer_id,
            code="DL_TEST_OVERDUE",
            name="Đại Lý Test Nợ Quá Hạn",
            credit_limit=50000000.0, # 50 triệu
            max_debt_days=15,        # Cho nợ tối đa 15 ngày
            overdue_days_allowed=15,
            status="Đang hoạt động"
        )
        DEALERS_DB[dealer_id] = test_dealer

        # Tạo đơn nợ 5 triệu nhưng đã 25 ngày tuổi (> 15 ngày)
        now = datetime.now(timezone.utc)
        ORDERS_DB[90021] = {
            "id": 90021,
            "order_code": "ORD_TEST_90021",
            "dealer_id": dealer_id,
            "dealer_name": test_dealer.name,
            "total_amount": 5000000.0,
            "status": "CONFIRMED",
            "created_at": (now - timedelta(days=25)).isoformat(),
        }

        res = evaluate_dealer_credit_flags(dealer_id=dealer_id, db=db)
        assert res["credit_warning_flag"] is True
        assert res["is_overdue"] is True
        assert res["is_over_credit_limit"] is False
        assert "Quá hạn thanh toán" in res["credit_flag_reason"]

def test_payment_clears_credit_warning_flags(auth_headers_accountant):
    """AC3 & S7-02: Ghi nhận thu tiền đối trừ công nợ -> Tự động gỡ cờ ngay lập tức."""
    with SessionLocal() as db:
        dealer_id = 9003
        db_d = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
        if not db_d:
            db_d = DealerEntity(
                id=dealer_id,
                code="DL_TEST_PAY",
                name="Đại Lý Test Thanh Toán",
                credit_limit=20000000.0,
                max_debt_days=10,
                overdue_days_allowed=10,
                status="Đang hoạt động"
            )
            db.add(db_d)
            db.commit()

        test_dealer = Dealer(
            id=dealer_id,
            code="DL_TEST_PAY",
            name="Đại Lý Test Thanh Toán",
            credit_limit=20000000.0,
            max_debt_days=10,
            overdue_days_allowed=10,
            status="Đang hoạt động"
        )
        DEALERS_DB[dealer_id] = test_dealer

        # Tạo đơn nợ 12 triệu tuổi 20 ngày -> bị gắn cờ
        now = datetime.now(timezone.utc)
        ORDERS_DB[90031] = {
            "id": 90031,
            "order_code": "ORD_TEST_90031",
            "dealer_id": dealer_id,
            "dealer_name": test_dealer.name,
            "total_amount": 12000000.0,
            "status": "CONFIRMED",
            "created_at": (now - timedelta(days=20)).isoformat(),
        }

        # Đánh giá trước khi thu tiền
        pre_eval = evaluate_dealer_credit_flags(dealer_id=dealer_id, db=db)
        assert pre_eval["credit_warning_flag"] is True

        # Thu tiền 12 triệu qua endpoint API (S7-02)
        payment_payload = {
            "amount": 12000000.0,
            "payment_method": "Chuyển khoản",
            "reference_code": "UNC123456",
            "note": "Thanh toán dứt điểm đơn nợ quá hạn"
        }
        res = client.post(
            f"/api/v1/dealers/{dealer_id}/payments",
            json=payment_payload,
            headers=auth_headers_accountant,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["amount"] == 12000000.0
        assert data["credit_status"]["credit_warning_flag"] is False
        assert data["credit_status"]["is_overdue"] is False
        assert data["credit_status"]["current_debt"] == 0.0

        # Kiểm tra cờ trong DEALERS_DB đã được gỡ bỏ hoàn toàn
        assert DEALERS_DB[dealer_id].credit_warning_flag is False

def test_order_creation_blocked_or_require_approval_by_config(auth_headers_sales, auth_headers_accountant, auth_headers_admin):
    """AC2: Đơn mới bị Chặn (BLOCK) hoặc chuyển Cần duyệt (REQUIRE_APPROVAL) theo cấu hình."""
    with SessionLocal() as db:
        dealer_id = 9004
        db_d = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
        if not db_d:
            db_d = DealerEntity(
                id=dealer_id,
                code="DL_TEST_BLOCK",
                name="Đại Lý Test Chặn Đơn",
                credit_limit=50000000.0,
                max_debt_days=10,
                overdue_days_allowed=10,
                status="Đang hoạt động"
            )
            db.add(db_d)
            db.commit()

        test_dealer = Dealer(
            id=dealer_id,
            code="DL_TEST_BLOCK",
            name="Đại Lý Test Chặn Đơn",
            credit_limit=50000000.0,
            max_debt_days=10,
            overdue_days_allowed=10,
            status="Đang hoạt động"
        )
        DEALERS_DB[dealer_id] = test_dealer

    # 1. Cấu hình overdue_action = BLOCK
    client.put("/api/v1/dealers/credit-policy/config", json={
        "over_limit_action": "REQUIRE_APPROVAL",
        "overdue_action": "BLOCK",
        "auto_eval_enabled": True
    }, headers=auth_headers_accountant)

    # Có đơn nợ quá hạn
    now = datetime.now(timezone.utc)
    ORDERS_DB[90041] = {
        "id": 90041,
        "order_code": "ORD_TEST_90041",
        "dealer_id": dealer_id,
        "dealer_name": "Đại Lý Test Chặn Đơn",
        "total_amount": 5000000.0,
        "status": "CONFIRMED",
        "created_at": (now - timedelta(days=25)).isoformat(),
    }

    # Thử tạo đơn mới khi cấu hình là BLOCK -> Phải bị HTTP 400 Chặn tạo đơn
    order_payload = {
        "dealer_id": dealer_id,
        "items": [{"product_id": 1, "quantity": 2, "price": 100000}],
        "delivery_point": "Kho nhận hàng Hà Nội"
    }
    block_res = client.post("/api/v1/orders", json=order_payload, headers=auth_headers_sales)
    assert block_res.status_code == 400
    assert "quá hạn" in block_res.json()["detail"].lower()
    assert "chặn" in block_res.json()["detail"].lower()

    # 2. Đổi cấu hình overdue_action = REQUIRE_APPROVAL
    client.put("/api/v1/dealers/credit-policy/config", json={
        "over_limit_action": "REQUIRE_APPROVAL",
        "overdue_action": "REQUIRE_APPROVAL",
        "auto_eval_enabled": True
    }, headers=auth_headers_accountant)

    # Thử tạo đơn mới lại -> Không bị chặn, mà được chuyển sang Chờ duyệt (PENDING_APPROVAL)
    approval_res = client.post("/api/v1/orders", json=order_payload, headers=auth_headers_sales)
    assert approval_res.status_code == 201
    order_data = approval_res.json()
    assert order_data["status"] == "PENDING_APPROVAL"
    assert order_data["requires_approval"] is True
    assert "quá hạn" in order_data["approval_reason"].lower()

    # 3. Cấu hình over_limit_action = BLOCK
    client.put("/api/v1/dealers/credit-policy/config", json={
        "over_limit_action": "BLOCK",
        "overdue_action": "REQUIRE_APPROVAL",
        "auto_eval_enabled": True
    }, headers=auth_headers_accountant)

    # Đặt đại lý 9004 có nợ vượt hạn mức (nợ 5 triệu > hạn mức 1 triệu)
    with SessionLocal() as db:
        db_d = db.query(DealerEntity).filter(DealerEntity.id == dealer_id).first()
        if db_d:
            db_d.credit_limit = 1000000.0
            db.commit()
    DEALERS_DB[dealer_id].credit_limit = 1000000.0
    DEALERS_DB[dealer_id].is_over_credit_limit = True
    DEALERS_DB[dealer_id].credit_warning_flag = True
    DEALERS_DB[dealer_id].credit_flag_reason = "Vượt hạn mức tín dụng"

    # Tạo đơn mới (Sales khi cấu hình BLOCK) -> Bắt buộc bị HTTP 400 Chặn tạo đơn
    over_limit_res = client.post("/api/v1/orders", json=order_payload, headers=auth_headers_sales)
    assert over_limit_res.status_code == 400
    assert "vượt hạn mức" in over_limit_res.json()["detail"].lower()
    assert "chặn" in over_limit_res.json()["detail"].lower()

    # Ở trang admin: Admin được phép tạo đơn mà không bị chặn bởi cấu hình
    admin_res = client.post("/api/v1/orders", json=order_payload, headers=auth_headers_admin)
    assert admin_res.status_code == 201
    assert admin_res.json()["status"] == "CONFIRMED"

    # Reset lại cấu hình mặc định an toàn
    client.put("/api/v1/dealers/credit-policy/config", json={
        "over_limit_action": "REQUIRE_APPROVAL",
        "overdue_action": "BLOCK",
        "auto_eval_enabled": True
    }, headers=auth_headers_accountant)

def test_daily_cron_evaluates_all_dealers():
    """AC3: Tác vụ định kỳ đánh giá lại toàn bộ đại lý."""
    with SessionLocal() as db:
        results = evaluate_all_dealers_credit_flags(db=db)
        assert isinstance(results, list)
        assert len(results) > 0
        cfg = get_credit_policy_config(db)
        assert cfg.last_run_at is not None


def test_credit_policy_rbac_permissions():
    """
    Kiểm tra phân quyền Cảnh báo & khoá bán công nợ (S7-04):
    - Kế toán công nợ (accountant), Quản lý kinh doanh (sales_manager), Admin: Được phép truy cập & cấu hình.
    - Nhân viên kinh doanh (sales), Kho (warehouse), Mua hàng (purchasing): Bị chặn (403 Forbidden).
    """
    # 1. Các user được phép xem và cấu hình
    allowed_users = [
        ("ketoan", "accountant"),
        ("sales_manager", "sales_manager"),
        ("admin", "admin"),
    ]
    for uname, role in allowed_users:
        token = create_access_token(subject=uname, role=role, token_version=1)
        headers = {"Authorization": f"Bearer {token}"}
        # GET config
        res_get = client.get("/api/v1/dealers/credit-policy/config", headers=headers)
        assert res_get.status_code == 200, f"User {uname} ({role}) should be able to GET credit policy config"
        # PUT config
        res_put = client.put("/api/v1/dealers/credit-policy/config", json={
            "over_limit_action": "REQUIRE_APPROVAL",
            "overdue_action": "BLOCK",
            "auto_eval_enabled": True
        }, headers=headers)
        assert res_put.status_code == 200, f"User {uname} ({role}) should be able to PUT credit policy config"
        # POST evaluate
        res_eval = client.post("/api/v1/dealers/credit-policy/evaluate", headers=headers)
        assert res_eval.status_code == 200, f"User {uname} ({role}) should be able to POST evaluate credit policy"

    # 2. Các user bị chặn hoàn toàn
    forbidden_users = [
        ("sales", "sales"),
        ("kho", "warehouse"),
        ("warehouse_mgr", "warehouse_manager"),
        ("muahang", "purchasing"),
    ]
    for uname, role in forbidden_users:
        token = create_access_token(subject=uname, role=role, token_version=1)
        headers = {"Authorization": f"Bearer {token}"}
        # GET config -> 403
        res_get = client.get("/api/v1/dealers/credit-policy/config", headers=headers)
        assert res_get.status_code == 403, f"User {uname} ({role}) must NOT be allowed to GET config (expected 403, got {res_get.status_code})"
        # PUT config -> 403
        res_put = client.put("/api/v1/dealers/credit-policy/config", json={
            "over_limit_action": "BLOCK",
            "overdue_action": "BLOCK",
            "auto_eval_enabled": True
        }, headers=headers)
        assert res_put.status_code == 403, f"User {uname} ({role}) must NOT be allowed to PUT config (expected 403, got {res_put.status_code})"
        # POST evaluate -> 403
        res_eval = client.post("/api/v1/dealers/credit-policy/evaluate", headers=headers)
        assert res_eval.status_code == 403, f"User {uname} ({role}) must NOT be allowed to evaluate (expected 403, got {res_eval.status_code})"

    # 3. Thu tiền / đối trừ (S7-02): Chỉ accountant và admin
    token_sm = create_access_token(subject="sales_manager", role="sales_manager", token_version=1)
    res_pay_sm = client.post("/api/v1/dealers/9001/payments", json={"amount": 100000, "payment_method": "Tiền mặt"}, headers={"Authorization": f"Bearer {token_sm}"})
    assert res_pay_sm.status_code == 403, "Sales manager must NOT be allowed to record payments"

    # Reset lại cấu hình mặc định an toàn cho các test khác
    token_admin = create_access_token(subject="admin", role="admin", token_version=1)
    client.put("/api/v1/dealers/credit-policy/config", json={
        "over_limit_action": "REQUIRE_APPROVAL",
        "overdue_action": "BLOCK",
        "auto_eval_enabled": True
    }, headers={"Authorization": f"Bearer {token_admin}"})



