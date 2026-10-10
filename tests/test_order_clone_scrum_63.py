import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import ProductEntity

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_order_clone_updates_current_selling_price_scrum_63():
    token = get_token("sales_manager")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Update SP001 current price to 185,000 đ
    price_res = client.put(
        "/api/v1/products/1/price",
        headers=headers,
        json={"sell_price": 185000.0, "reason": "Cập nhật giá niêm yết SCRUM-63"}
    )
    assert price_res.status_code == 200

    # 2. Create an order with old price 199,000 đ
    create_res = client.post(
        "/api/v1/orders",
        headers=headers,
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 2, "price": 199000.0}
            ],
            "delivery_point": "Kho Tổng Hà Nội",
            "desired_delivery_date": "2026-10-30"
        }
    )
    assert create_res.status_code in (200, 201)
    order_data = create_res.json()
    order_code = order_data["order_code"]
    order_id = order_data["id"]

    # 3. Call clone-data endpoint with order_code
    clone_res = client.get(f"/api/v1/orders/{order_code}/clone-data", headers=headers)
    assert clone_res.status_code == 200, f"Failed to get clone data: {clone_res.text}"
    clone_data = clone_res.json()

    # Verify unit_price is 185,000 đ instead of 199,000 đ
    assert len(clone_data["items"]) == 1
    cloned_item = clone_data["items"][0]
    assert cloned_item["unit_price"] == 185000.0
    assert cloned_item["price"] == 185000.0
    assert cloned_item["quantity"] == 2
    assert cloned_item["subtotal"] == 370000.0
    assert clone_data["subtotal_amount"] == 370000.0
    assert clone_data["status"] == "DRAFT"

    # 4. Call clone-data endpoint with order_id
    clone_res_id = client.get(f"/api/v1/orders/{order_id}/clone-data", headers=headers)
    assert clone_res_id.status_code == 200
    assert clone_res_id.json()["items"][0]["unit_price"] == 185000.0
