# backend/tests/test_accountant_order_permission.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def get_token(username: str) -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

def test_accountant_blocked_from_create_order():
    """AC 2: Kế toán (Accountant) bị CHẶN hoàn toàn khi gọi POST /orders (HTTP 403 Forbidden)."""
    token = get_token("ketoan")
    payload = {
        "dealer_id": 1,
        "items": [
            {"product_id": 1, "quantity": 1, "price": 100000}
        ],
        "delivery_point": "Kho A",
        "desired_delivery_date": "2026-10-15"
    }
    res = client.post("/api/v1/orders", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403, f"Expected 403 Forbidden but got {res.status_code}: {res.text}"
    assert "Truy cập bị từ chối" in res.text

def test_accountant_blocked_from_create_sales_entry_order():
    """AC 2: Kế toán (Accountant) bị CHẶN hoàn toàn khi gọi POST /orders/sales-entry (HTTP 403 Forbidden)."""
    token = get_token("ketoan")
    payload = {
        "dealer_id": 1,
        "items": [
            {"product_id": 1, "quantity": 1, "price": 100000}
        ],
        "delivery_point": "Kho A",
        "desired_delivery_date": "2026-10-15"
    }
    res = client.post("/api/v1/orders/sales-entry", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403, f"Expected 403 Forbidden but got {res.status_code}: {res.text}"
    assert "Truy cập bị từ chối" in res.text

def test_accountant_can_read_orders_list():
    """AC 3: Kế toán (Accountant) vẫn XEM được danh sách đơn hàng bình thường (Read-only)."""
    token = get_token("ketoan")
    res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, f"Expected 200 OK but got {res.status_code}: {res.text}"
    assert isinstance(res.json(), list)

def test_sales_and_admin_can_access_create_order():
    """Sales và Admin không bị chặn 403 khi gọi API tạo đơn."""
    token_sales = get_token("sales")
    token_admin = get_token("admin")

    res_sales = client.post("/api/v1/orders", json={}, headers={"Authorization": f"Bearer {token_sales}"})
    assert res_sales.status_code != 403, f"Sales should not be 403 Forbidden, got {res_sales.status_code}"

    res_admin = client.post("/api/v1/orders", json={}, headers={"Authorization": f"Bearer {token_admin}"})
    assert res_admin.status_code != 403, f"Admin should not be 403 Forbidden, got {res_admin.status_code}"
