from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.api.v1.endpoints import orders as orders_endpoint
from app.models.dealer import DEALERS_DB
from app.models.entities import OrderEntity

client = TestClient(app)
created_order_codes: set[str] = set()


@pytest.fixture(autouse=True)
def cleanup_created_orders():
    original_assignments = {
        dealer_id: dealer.assigned_sale_id
        for dealer_id, dealer in DEALERS_DB.items()
    }
    yield
    try:
        if created_order_codes:
            session = SessionLocal()
            try:
                session.query(OrderEntity).filter(
                    OrderEntity.order_code.in_(created_order_codes)
                ).delete(synchronize_session=False)
                session.commit()
            finally:
                session.close()
            for order_id, order in list(orders_endpoint.ORDERS_DB.items()):
                if order["order_code"] in created_order_codes:
                    del orders_endpoint.ORDERS_DB[order_id]
            orders_endpoint.NEXT_ORDER_ID = max(
                orders_endpoint.NEXT_ORDER_ID,
                max(orders_endpoint.ORDERS_DB.keys(), default=0) + 1,
            )
            created_order_codes.clear()
    finally:
        for dealer_id, assigned_sale_id in original_assignments.items():
            DEALERS_DB[dealer_id].assigned_sale_id = assigned_sale_id


def get_token(username: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_sales_order_dealer_list_only_returns_assigned_dealers():
    DEALERS_DB[1].assigned_sale_id = 3
    DEALERS_DB[2].assigned_sale_id = 3
    DEALERS_DB[3].assigned_sale_id = 3
    DEALERS_DB[4].assigned_sale_id = 2
    headers = {"Authorization": f"Bearer {get_token('sales')}"}

    response = client.get("/api/v1/orders/dealers", headers=headers)

    assert response.status_code == 200
    assert {dealer["id"] for dealer in response.json()} == {1, 2, 3}


def test_order_write_is_required_for_order_dealer_list():
    headers = {"Authorization": f"Bearer {get_token('kho')}"}

    response = client.get("/api/v1/orders/dealers", headers=headers)

    assert response.status_code == 403


def test_sales_cannot_create_order_for_unassigned_dealer():
    DEALERS_DB[4].assigned_sale_id = 2
    headers = {"Authorization": f"Bearer {get_token('sales')}"}

    response = client.post(
        "/api/v1/orders/sales-entry",
        headers=headers,
        json={
            "dealer_id": 4,
            "delivery_point": "66 Nguyễn Huệ, Đà Nẵng",
            "desired_delivery_date": (date.today() + timedelta(days=8)).isoformat(),
            "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        },
    )

    assert response.status_code == 403
    legacy_response = client.post(
        "/api/v1/orders",
        headers=headers,
        json={
            "dealer_id": 4,
            "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        },
    )
    assert legacy_response.status_code == 201
    created_order_codes.add(legacy_response.json()["order_code"])


def test_order_creation_keeps_delivery_unit_and_discount_totals(monkeypatch):
    DEALERS_DB[1].assigned_sale_id = 3
    headers = {"Authorization": f"Bearer {get_token('sales')}"}

    response = client.post(
        "/api/v1/orders/sales-entry",
        headers=headers,
        json={
            "dealer_id": 1,
            "delivery_point": "120 Cầu Giấy, Hà Nội",
            "desired_delivery_date": (date.today() + timedelta(days=8)).isoformat(),
            "discount_percent": 10,
            "items": [
                {"product_id": 1, "quantity": 2, "price": 1, "unit": "Thùng", "conversion_rate": 24},
                {"product_id": 2, "quantity": 1, "price": 1, "unit": "Chiếc", "conversion_rate": 1},
            ],
        },
    )

    assert response.status_code == 201
    result = response.json()
    created_order_codes.add(result["order_code"])
    assert result["subtotal_amount"] == 778000
    assert result["discount_amount"] == 77800
    assert result["total_amount"] == 700200
    assert result["delivery_point"] == "120 Cầu Giấy, Hà Nội"
    assert result["desired_delivery_date"] == (date.today() + timedelta(days=8)).isoformat()
    assert [item["unit"] for item in result["items"]] == ["Thùng", "Chiếc"]
    assert [item["conversion_rate"] for item in result["items"]] == [24, 1]
    assert [item["base_quantity"] for item in result["items"]] == [48, 1]
    assert [item["product_name"] for item in result["items"]] == [
        "Áo thun Polo Nam Cao Cấp",
        "Quần Jeans Slimfit Co Giãn",
    ]
    from app.api.v1.endpoints.products import RAW_PRODUCTS
    monkeypatch.setitem(RAW_PRODUCTS[0], "name", "Tên sản phẩm đã cập nhật")
    detail_response = client.get(
        f"/api/v1/orders/{result['order_code']}",
        headers=headers,
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["subtotal_amount"] == 778000
    assert [item["product_name"] for item in detail_response.json()["items"]] == [
        "Áo thun Polo Nam Cao Cấp",
        "Quần Jeans Slimfit Co Giãn",
    ]
    session = SessionLocal()
    try:
        stored_order = session.query(OrderEntity).filter_by(order_code=result["order_code"]).first()
        assert stored_order is not None
        assert '"delivery_point": "120 Cầu Giấy, Hà Nội"' in stored_order.items_json
        listed_orders = client.get("/api/v1/orders", headers=headers)
        assert listed_orders.status_code == 200
        assert any(order["order_code"] == result["order_code"] for order in listed_orders.json())
        cancel_response = client.put(
            f"/api/v1/orders/{result['order_code']}",
            headers=headers,
            json={"status": "CANCELLED", "reason": "Đại lý yêu cầu hủy đơn"},
        )
        assert cancel_response.status_code == 200
        stored_order = session.query(OrderEntity).filter_by(order_code=result["order_code"]).first()
        session.refresh(stored_order)
        assert stored_order.status == "CANCELLED"
        listed_orders = client.get("/api/v1/orders", headers=headers)
        listed_order = next(
            order for order in listed_orders.json()
            if order["order_code"] == result["order_code"]
        )
        assert listed_order["status"] == "CANCELLED"
    finally:
        session.close()


def test_sales_entry_rejects_forged_conversion_rate():
    DEALERS_DB[1].assigned_sale_id = 3
    headers = {"Authorization": "Bearer " + get_token("sales")}
    response = client.post(
        "/api/v1/orders/sales-entry",
        headers=headers,
        json={
            "dealer_id": 1,
            "delivery_point": "120 Cầu Giấy, Hà Nội",
            "desired_delivery_date": (date.today() + timedelta(days=1)).isoformat(),
            "items": [
                {"product_id": 1, "quantity": 1, "price": 199000, "unit": "Thùng", "conversion_rate": 1},
            ],
        },
    )
    assert response.status_code == 400


def test_sales_entry_rejects_delivery_date_in_the_past():
    DEALERS_DB[1].assigned_sale_id = 3
    headers = {"Authorization": "Bearer " + get_token("sales")}
    response = client.post(
        "/api/v1/orders/sales-entry",
        headers=headers,
        json={
            "dealer_id": 1,
            "delivery_point": "120 Cầu Giấy, Hà Nội",
            "desired_delivery_date": (date.today() - timedelta(days=1)).isoformat(),
            "items": [{"product_id": 1, "quantity": 1, "price": 199000}],
        },
    )
    assert response.status_code == 400


def test_existing_order_payload_still_works_without_new_fields():
    DEALERS_DB[1].assigned_sale_id = 3
    headers = {"Authorization": f"Bearer {get_token('sales')}"}

    response = client.post(
        "/api/v1/orders",
        headers=headers,
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 2, "price": 199000}],
        },
    )

    assert response.status_code == 201
    order_code = response.json()["order_code"]
    created_order_codes.add(order_code)
    assert response.json()["total_amount"] == 398000
    assert "delivery_point" not in response.json()
    detail_response = client.get(f"/api/v1/orders/{order_code}", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["items"][0]["product_name"] == "Áo thun Polo Nam Cao Cấp"
