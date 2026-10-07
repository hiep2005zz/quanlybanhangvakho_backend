import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.v1.endpoints.discounts import _POLICIES_STORE, load_discount_policies

client = TestClient(app)

def _login(username: str = "sales", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]

def _admin_login() -> str:
    res = client.post("/api/v1/auth/login", json={"username": "admin", "password": "123"})
    assert res.status_code == 200
    return res.json()["access_token"]

def test_get_discount_policies():
    token = _login()
    res = client.get("/api/v1/discounts", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert any(p["code"] == "CK-SL-001" for p in data["items"])

def test_calculate_discount_api_101_qty():
    token = _login()
    res = client.post(
        "/api/v1/discounts/calculate",
        json={"product_id": 1, "quantity": 101, "base_price": 100000.0},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["discount_percent"] == 5.0
    assert data["applied_policy_code"] == "CK-SL-001"
    assert data["total_discount_amount"] == round(101 * 100000.0 * 0.05, 2)
    assert data["final_total_amount"] == 101 * 100000.0 - data["total_discount_amount"]

def test_auto_discount_on_create_order_101_qty():
    """
    Khi tạo đơn hàng với 101 sản phẩm và discount_percent = 0,
    Backend phải tự động tính và áp dụng 5% chiết khấu từ CK-SL-001.
    """
    token = _login("sales", "123")
    order_payload = {
        "dealer_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 101,
                "price": 200000.0,
                "unit": "Cái",
                "conversion_rate": 1.0
            }
        ],
        "discount_percent": 0.0,
        "note": "Đơn hàng thử nghiệm chiết khấu tự động sản lượng 101 sp",
    }
    res = client.post("/api/v1/orders", json=order_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["discount_percent"] == 5.0
    assert order["discount_amount"] == round(101 * 200000.0 * 0.05, 2)
    expected_total = 101 * 200000.0 - order["discount_amount"]
    assert order["total_amount"] == expected_total

def test_manual_override_discount_on_create_order():
    """
    Ràng buộc can thiệp thủ công: Nếu người dùng nhập 8%, hệ thống tôn trọng giá trị 8%
    và nếu vượt mức chính sách (5%), đơn hàng được chuyển sang PENDING_APPROVAL.
    """
    token = _login("sales", "123")
    order_payload = {
        "dealer_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 101,
                "price": 200000.0,
                "unit": "Cái",
                "conversion_rate": 1.0
            }
        ],
        "discount_percent": 8.0,
        "note": "Đơn hàng can thiệp thủ công chiết khấu 8%",
    }
    res = client.post("/api/v1/orders", json=order_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    order = res.json()
    assert order["discount_percent"] == 8.0
    assert order["discount_amount"] == round(101 * 200000.0 * 0.08, 2)
    assert order["requires_approval"] is True
    assert "vượt mức chính sách" in (order["approval_reason"] or "")

def test_sales_entry_order_auto_discount_101_qty():
    """
    Kiểm tra tạo đơn qua màn hình Sales Order Entry (/sales-entry) với 101 sản phẩm.
    """
    token = _login("sales", "123")
    order_payload = {
        "dealer_id": 1,
        "delivery_point": "Kho Sao Mai, Hà Nội",
        "desired_delivery_date": "2026-12-31",
        "discount_percent": 0.0,
        "items": [
            {
                "product_id": 1,
                "quantity": 101,
                "price": 200000.0,
                "unit": "Cái",
                "conversion_rate": 1.0
            }
        ],
        "note": "Đơn sales entry 101 sp",
    }
    res = client.post("/api/v1/orders/sales-entry", json=order_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["discount_percent"] == 5.0
    expected_discount = round(order["subtotal_amount"] * 0.05, 2)
    assert order["discount_amount"] == expected_discount
    assert order["total_amount"] == order["subtotal_amount"] - expected_discount
