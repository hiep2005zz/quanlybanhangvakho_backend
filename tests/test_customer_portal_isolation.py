import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.models.user import USERS_DB, UserInDB, DEFAULT_HASH
from app.models.dealer import DEALERS_DB
from app.core.rbac import Role

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_portal_test_accounts():
    USERS_DB["portal_cust1"] = UserInDB(
        id=101,
        username="portal_cust1",
        full_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        email="saomai@daily.vn",
        phone="0912345678",
        role=Role.CUSTOMER.value,
        roles=[Role.CUSTOMER.value],
        hashed_password=DEFAULT_HASH,
        branch="Đại lý phân phối",
    )
    USERS_DB["portal_cust2"] = UserInDB(
        id=102,
        username="portal_cust2",
        full_name="Đại Lý Thời Trang Tân Bình",
        email="tanbinh@daily.vn",
        phone="0987654321",
        role=Role.CUSTOMER.value,
        roles=[Role.CUSTOMER.value],
        hashed_password=DEFAULT_HASH,
        branch="Đại lý phân phối",
    )
    from app.models.user import save_users_db
    save_users_db()
    yield
    USERS_DB.pop("portal_cust1", None)
    USERS_DB.pop("portal_cust2", None)
    save_users_db()

def get_token(username: str) -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json()["access_token"]

def test_customer_data_isolation_on_get_orders():
    """
    Tiêu chí 2.2: Data Isolation GET /orders:
    Customer chỉ nhìn thấy đơn hàng của chính mình.
    """
    token1 = get_token("portal_cust1")
    token2 = get_token("portal_cust2")
    token_admin = get_token("admin")

    # Tạo đơn cho customer 1 (Dealer 1)
    res1 = client.post("/api/v1/orders", json={
        "dealer_id": 1,
        "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        "note": "Đơn của đại lý 1"
    }, headers={"Authorization": f"Bearer {token1}"})
    assert res1.status_code == 201
    code1 = res1.json()["order_code"]

    # Tạo đơn cho customer 2 (Dealer 2)
    res2 = client.post("/api/v1/orders", json={
        "dealer_id": 2,
        "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        "note": "Đơn của đại lý 2"
    }, headers={"Authorization": f"Bearer {token2}"})
    assert res2.status_code == 201
    code2 = res2.json()["order_code"]

    # Customer 1 get orders -> chỉ thấy code1, KHÔNG thấy code2
    orders1 = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token1}"}).json()
    codes_seen_by_1 = [o["order_code"] for o in orders1]
    assert code1 in codes_seen_by_1
    assert code2 not in codes_seen_by_1

    # Customer 2 get orders -> chỉ thấy code2, KHÔNG thấy code1
    orders2 = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token2}"}).json()
    codes_seen_by_2 = [o["order_code"] for o in orders2]
    assert code2 in codes_seen_by_2
    assert code1 not in codes_seen_by_2

    # Admin get orders -> thấy cả 2
    admin_orders = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token_admin}"}).json()
    admin_codes = [o["order_code"] for o in admin_orders]
    assert code1 in admin_codes
    assert code2 in admin_codes

def test_customer_blocked_from_forbidden_modules():
    """
    Tiêu chí 2.1: Bảo vệ route (403 Forbidden) cho module Kho, Báo cáo, Users, Duyệt đơn.
    """
    token = get_token("portal_cust1")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Quản lý kho: 403
    res_stock = client.post("/api/v1/inventory/adjust", json={
        "product_id": 1, "adjustment": 5, "reason": "Hack kho"
    }, headers=headers)
    assert res_stock.status_code == 403

    # 2. Quản lý người dùng: 403
    res_users = client.get("/api/v1/users", headers=headers)
    assert res_users.status_code == 403

    # 3. Duyệt đơn hàng: 403
    res_approve = client.post("/api/v1/orders/ORD00001/approve", headers=headers)
    assert res_approve.status_code == 403

    # 4. Sửa giá / sửa hóa đơn: 403
    res_edit = client.put("/api/v1/orders/ORD00001", json={"status": "CONFIRMED", "reason": "Sửa giá"}, headers=headers)
    assert res_edit.status_code == 403

def test_customer_cancellation_rules():
    """
    Tiêu chí 1.2 & 2: Đại lý chỉ được xem hoặc hủy đơn hàng của chính mình khi chưa duyệt.
    """
    token1 = get_token("portal_cust1")
    token2 = get_token("portal_cust2")
    token_manager = get_token("sales_manager")

    # Tạo đơn chờ duyệt cho customer 1
    res1 = client.post("/api/v1/orders", json={
        "dealer_id": 1,
        "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        "note": "Đơn test hủy"
    }, headers={"Authorization": f"Bearer {token1}"})
    assert res1.status_code == 201
    code = res1.json()["order_code"]

    # Customer 2 cố tình hủy đơn của customer 1 -> 403 Forbidden
    res_hack = client.post(f"/api/v1/orders/{code}/cancel", json={"reason": "Hủy trộm"}, headers={"Authorization": f"Bearer {token2}"})
    assert res_hack.status_code == 403

    # Customer 1 tự hủy đơn chưa duyệt của mình -> 200 OK
    res_cancel = client.post(f"/api/v1/orders/{code}/cancel", json={"reason": "Đại lý đổi ý không mua"}, headers={"Authorization": f"Bearer {token1}"})
    assert res_cancel.status_code == 200
    assert res_cancel.json()["order"]["status"] == "CANCELLED"

    # Tạo đơn khác và cho sales_manager duyệt
    res2 = client.post("/api/v1/orders", json={
        "dealer_id": 1,
        "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        "note": "Đơn đã duyệt"
    }, headers={"Authorization": f"Bearer {token1}"})
    code2 = res2.json()["order_code"]
    res_app = client.post(f"/api/v1/orders/{code2}/approve", headers={"Authorization": f"Bearer {token_manager}"})
    assert res_app.status_code == 200

    # Sau khi đơn đã duyệt, Customer cố tình hủy -> 400 Bad Request
    res_cancel_approved = client.post(f"/api/v1/orders/{code2}/cancel", json={"reason": "Hủy sau khi duyệt"}, headers={"Authorization": f"Bearer {token1}"})
    assert res_cancel_approved.status_code == 400
    assert "chưa được duyệt" in res_cancel_approved.text

def test_customer_price_book_isolation_and_cost_stripping():
    """
    Tiêu chí 2.3: Lọc Bảng giá và Ẩn giá vốn:
    Customer chỉ nhận các sản phẩm / mức giá áp dụng cho đại lý, không xem được giá vốn.
    """
    token = get_token("portal_cust1")
    res = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    assert len(items) > 0
    # Giá vốn và lợi nhuận BẮT BUỘC bị gỡ bỏ hoàn toàn (None)
    for p in items:
        assert p["cost_price"] is None
        assert p["profit_margin"] is None
        assert p["profit_per_unit"] is None
        assert p["sell_price"] is not None and p["sell_price"] > 0
