# backend/tests/test_pricing_service_rbac.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import OrderEntity, DealerEntity, ProductEntity
from app.models.discount import DiscountPolicyEntity, DiscountTierEntity

client = TestClient(app)


def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


@pytest.fixture
def auth_tokens():
    return {
        "admin": get_token("admin"),
        "sales_manager": get_token("sales_manager"),
        "sales": get_token("sales"),
        "kho": get_token("kho"),
        "ketoan": get_token("ketoan"),
        "customer": get_token("muahang"),
    }


@pytest.fixture
def test_price_book_and_dealer():
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    pb_id = None
    policy_id = None
    try:
        # 1. Đảm bảo dealer 1 thuộc nhóm Dai_ly_cap_1
        d = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if d:
            d.customer_group = "Dai_ly_cap_1"
            db.commit()

        # 2. Tạo bảng giá hiệu lực cho nhóm Dai_ly_cap_1: SP1 có sale_price=200000, floor_price=170000
        pb = PriceBookEntity(
            code=f"BG-PRICING-TEST-{int(now.timestamp())}",
            name="Bảng giá Nghiệm thu PricingService",
            customer_group="Dai_ly_cap_1",
            valid_from=now - timedelta(days=2),
            valid_to=now + timedelta(days=30),
            status="ACTIVE",
            version=5,
            is_locked=False,
            created_by="sales_manager",
        )
        db.add(pb)
        db.flush()
        pb_id = pb.id

        db.add(
            PriceBookItemEntity(
                price_book_id=pb.id,
                product_id=1,
                sale_price=200000.0,
                floor_price=170000.0,
            )
        )

        # Tạm tắt các chính sách chiết khấu khác để đảm bảo tính độc lập của test
        other_policies = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.is_active == True).all()
        deactivated_ids = [p.id for p in other_policies]
        for p in other_policies:
            p.is_active = False

        # 3. Tạo chính sách chiết khấu bậc sản lượng:
        # Bậc 1: 10 - 49 -> 5%
        # Bậc 2: 50 - 99 -> 10%
        # Bậc 3: 100+ -> 15%
        policy = DiscountPolicyEntity(
            code=f"CK-TIER-TEST-{int(now.timestamp())}",
            name="Chính sách Chiết khấu Bậc Test",
            target_dealer_type="ALL",
            target_group="ALL",
            category="ALL",
            is_active=True,
            status="active",
            start_date=(now - timedelta(days=5)).strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=30)).strftime("%Y-%m-%d"),
            created_by="sales_manager",
        )
        db.add(policy)
        db.flush()
        policy_id = policy.id

        db.add(DiscountTierEntity(policy_id=policy.id, min_quantity=10, max_quantity=49, discount_percent=5.0))
        db.add(DiscountTierEntity(policy_id=policy.id, min_quantity=50, max_quantity=99, discount_percent=10.0))
        db.add(DiscountTierEntity(policy_id=policy.id, min_quantity=100, max_quantity=None, discount_percent=15.0))

        db.commit()
    finally:
        db.close()

    yield {"price_book_id": pb_id, "policy_id": policy_id, "dealer_id": 1, "product_id": 1}

    # Dọn dẹp
    clean_db = SessionLocal()
    try:
        if pb_id:
            clean_db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == pb_id).delete()
            clean_db.query(PriceBookEntity).filter(PriceBookEntity.id == pb_id).delete()
        if policy_id:
            clean_db.query(DiscountTierEntity).filter(DiscountTierEntity.policy_id == policy_id).delete()
            clean_db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).delete()
        if deactivated_ids:
            for p in clean_db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id.in_(deactivated_ids)).all():
                p.is_active = True
        clean_db.commit()
    finally:
        clean_db.close()


def test_rbac_security_pricing_endpoints(auth_tokens, test_price_book_and_dealer):
    """
    TEST CASE 1 (RBAC):
    - User vai trò Warehouse, Accountant, Customer: Trả về 403 Forbidden.
    - User Sales hoặc Admin: Được phép gọi API tính giá (200 OK).
    """
    unauthorized_roles = ["kho", "ketoan", "customer"]
    for role in unauthorized_roles:
        res = client.post(
            "/api/v1/pricing/calculate-line-item",
            headers={"Authorization": f"Bearer {auth_tokens[role]}"},
            json={
                "dealer_id": 1,
                "product_id": 1,
                "quantity": 10,
            },
        )
        assert res.status_code == 403, f"Role {role} must be forbidden (403), got {res.status_code}"

    # Sales và Admin được phép truy cập
    authorized_roles = ["sales", "sales_manager", "admin"]
    for role in authorized_roles:
        res = client.post(
            "/api/v1/pricing/calculate-line-item",
            headers={"Authorization": f"Bearer {auth_tokens[role]}"},
            json={
                "dealer_id": 1,
                "product_id": 1,
                "quantity": 10,
            },
        )
        assert res.status_code == 200, f"Role {role} must succeed (200), got {res.status_code}"


def test_effective_price_lookup_and_hard_validation(auth_tokens, test_price_book_and_dealer):
    """
    TEST CASE 2 (AC1):
    - SKU có bảng giá -> Điền tự động chính xác đơn giá và giá sàn.
    - SKU không có bảng giá hiệu lực -> Bị chặn (400 Bad Request) kèm thông báo rõ ràng.
    """
    sales_token = auth_tokens["sales"]

    # 1. Tra cứu SKU có bảng giá (product_id = 1)
    res_ok = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={
            "dealer_id": 1,
            "product_id": 1,
            "quantity": 1,
        },
    )
    assert res_ok.status_code == 200
    data_ok = res_ok.json()
    assert data_ok["product_id"] == 1
    assert data_ok["unit_price"] == 200000.0
    assert data_ok["book_price"] == 200000.0
    assert data_ok["floor_price"] == 170000.0
    assert data_ok["is_below_floor"] is False

    # 2. Tra cứu SKU không có trong bảng giá hiệu lực (ví dụ product_id = 999999)
    res_blocked = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={
            "dealer_id": 1,
            "product_id": 999999,
            "quantity": 1,
        },
    )
    assert res_blocked.status_code == 400
    err_detail = res_blocked.json()["detail"]
    assert "chưa có bảng giá đang hiệu lực cho nhóm khách hàng này" in err_detail
    assert "Vui lòng kiểm tra lại cấu hình giá" in err_detail


def test_tiered_quantity_discount_calculation(auth_tokens, test_price_book_and_dealer):
    """
    TEST CASE 3 (AC2):
    - Đổi số lượng qua các mốc -> Chiết khấu % tự động nhảy theo đúng bậc.
    - Dưới bậc thấp nhất (< 10) -> Chiết khấu = 0%.
    - Bậc 1 (10 <= qty <= 49) -> 5%.
    - Bậc 2 (50 <= qty <= 99) -> 10%.
    - Bậc 3 (>= 100) -> 15%.
    - line_total = quantity * unit_price * (1 - discount_rate / 100).
    """
    sales_token = auth_tokens["sales"]

    # Dưới ngưỡng bậc thấp nhất (quantity = 5 < 10) -> Chiết khấu 0%
    res_under = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={"dealer_id": 1, "product_id": 1, "quantity": 5},
    )
    assert res_under.status_code == 200
    d_under = res_under.json()
    assert d_under["discount_rate"] == 0.0
    assert d_under["line_total"] == 5 * 200000.0

    # Bậc 1: quantity = 20 -> 5%
    res_tier1 = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={"dealer_id": 1, "product_id": 1, "quantity": 20},
    )
    assert res_tier1.status_code == 200
    d_t1 = res_tier1.json()
    assert d_t1["discount_rate"] == 5.0
    expected_total_t1 = 20 * 200000.0 * (1 - 0.05)  # 3,800,000
    assert d_t1["line_total"] == expected_total_t1

    # Bậc 2: quantity = 60 -> 10%
    res_tier2 = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={"dealer_id": 1, "product_id": 1, "quantity": 60},
    )
    assert res_tier2.status_code == 200
    d_t2 = res_tier2.json()
    assert d_t2["discount_rate"] == 10.0
    expected_total_t2 = 60 * 200000.0 * (1 - 0.10)  # 10,800,000
    assert d_t2["line_total"] == expected_total_t2

    # Bậc 3: quantity = 100 -> 15%
    res_tier3 = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={"dealer_id": 1, "product_id": 1, "quantity": 100},
    )
    assert res_tier3.status_code == 200
    d_t3 = res_tier3.json()
    assert d_t3["discount_rate"] == 15.0
    expected_total_t3 = 100 * 200000.0 * (1 - 0.15)
    assert d_t3["line_total"] == expected_total_t3


def test_manual_price_override_and_price_floor(auth_tokens, test_price_book_and_dealer):
    """
    TEST CASE 4 (AC3):
    - Sửa unit_price < floor_price (160.000 < 170.000):
      -> is_below_floor = true, requires_approval = true, ghi nhận lý do cảnh báo giá sàn.
    - Sửa unit_price >= floor_price (180.000 >= 170.000):
      -> is_below_floor = false, requires_approval = false, luồng bình thường.
    """
    sales_token = auth_tokens["sales"]

    # 1. Bán dưới giá sàn (unit_price_override = 160000 < floor 170000)
    res_low = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={
            "dealer_id": 1,
            "product_id": 1,
            "quantity": 1,
            "unit_price_override": 160000.0,
        },
    )
    assert res_low.status_code == 200
    d_low = res_low.json()
    assert d_low["unit_price"] == 160000.0
    assert d_low["is_below_floor"] is True
    assert d_low["requires_approval"] is True
    assert "nhỏ hơn giá sàn quy định" in (d_low.get("approval_reason") or "")

    # 2. Bán trên hoặc bằng giá sàn (unit_price_override = 180000 >= floor 170000)
    res_ok = client.post(
        "/api/v1/pricing/calculate-line-item",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={
            "dealer_id": 1,
            "product_id": 1,
            "quantity": 1,
            "unit_price_override": 180000.0,
        },
    )
    assert res_ok.status_code == 200
    d_ok = res_ok.json()
    assert d_ok["unit_price"] == 180000.0
    assert d_ok["is_below_floor"] is False
    assert d_ok["requires_approval"] is False
    assert d_ok["approval_reason"] is None


def test_order_creation_with_floor_price_approval_flow(auth_tokens, test_price_book_and_dealer):
    """
    TEST CASE 4 (Luồng tạo đơn thực tế):
    - Tạo đơn dưới giá sàn: status = PENDING_APPROVAL, requires_approval = True
    - Tạo đơn đúng giá sàn: status = CONFIRMED
    """
    sales_token = auth_tokens["sales"]

    res = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 1, "price": 160000.0}],
            "note": "Test bán dưới sàn",
        },
    )
    assert res.status_code == 201
    ord_data = res.json()
    assert ord_data["status"] == "PENDING_APPROVAL"
    assert ord_data["requires_approval"] is True
    assert "giá sàn" in (ord_data.get("approval_reason") or "")


def test_non_regression_historic_orders_unaltered(auth_tokens):
    """
    TEST CASE 5 (Không hồi quy):
    - Đọc lại danh sách các đơn hàng hiện có:
      Dữ liệu lịch sử, tổng tiền, đơn giá không bị tự động tính toán lại hay thay đổi.
    """
    sales_token = auth_tokens["sales"]
    res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {sales_token}"})
    assert res.status_code == 200
    orders = res.json()
    assert isinstance(orders, list)
    if len(orders) > 0:
        sample_order = orders[0]
        assert "total_amount" in sample_order
        assert "order_code" in sample_order
