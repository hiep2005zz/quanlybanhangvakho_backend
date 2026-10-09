# backend/tests/test_credit_control.py
from datetime import datetime, timezone, timedelta, date
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.v1.endpoints.orders import ORDERS_DB
from app.models.dealer import DEALERS_DB
from app.models.entities import OrderEntity
from app.core.database import SessionLocal

client = TestClient(app)

def get_token(username: str) -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200
    return res.json()["access_token"]

def test_credit_info_authorization_sales_and_admin_allowed():
    token_sales = get_token("sales")
    token_admin = get_token("admin")

    res_sales = client.get("/api/v1/dealers/1/credit-info", headers={"Authorization": f"Bearer {token_sales}"})
    assert res_sales.status_code == 200
    data_sales = res_sales.json()
    assert data_sales["dealer_id"] == 1
    assert "credit_limit" in data_sales
    assert "overdue_days_allowed" in data_sales
    assert "current_debt" in data_sales
    assert "remaining_credit" in data_sales
    assert "is_overdue" in data_sales
    assert "is_over_limit" in data_sales

    res_admin = client.get("/api/v1/dealers/1/credit-info", headers={"Authorization": f"Bearer {token_admin}"})
    assert res_admin.status_code == 200

def test_credit_info_authorization_other_roles_blocked():
    token_kho = get_token("kho")
    token_ketoan = get_token("ketoan")

    res_kho = client.get("/api/v1/dealers/1/credit-info", headers={"Authorization": f"Bearer {token_kho}"})
    assert res_kho.status_code == 403

    res_ketoan = client.get("/api/v1/dealers/1/credit-info", headers={"Authorization": f"Bearer {token_ketoan}"})
    assert res_ketoan.status_code == 403

def test_orders_dealers_endpoint_contains_debt_metrics():
    token_sales = get_token("sales")
    res = client.get("/api/v1/orders/dealers", headers={"Authorization": f"Bearer {token_sales}"})
    assert res.status_code == 200
    dealers = res.json()
    assert len(dealers) > 0
    first = dealers[0]
    assert "credit_limit" in first
    assert "overdue_days_allowed" in first
    assert "current_debt" in first
    assert "remaining_credit" in first
    assert "max_debt_age" in first
    assert "is_overdue" in first
    assert "is_over_limit" in first

def test_over_credit_limit_marks_order_as_pending_approval():
    token_sales = get_token("sales")
    # Đặt đơn hàng giá trị cao (150 triệu VNĐ vượt hạn mức 100 triệu của DL001)
    res = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {token_sales}"},
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 1, "price": 150000000.0}],
        },
    )
    assert res.status_code == 201
    order_data = res.json()
    assert order_data["status"] == "PENDING_APPROVAL"
    assert order_data["requires_approval"] is True
    assert "Vượt hạn mức công nợ" in (order_data["approval_reason"] or "")

def test_overdue_debt_completely_blocks_order_creation():
    token_sales = get_token("sales")
    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=45)).isoformat()
    # Giả lập 1 đơn nợ quá hạn 45 ngày (> 30 ngày cho phép)
    ORDERS_DB[8888] = {
        "id": 8888,
        "order_code": "ORD8888-OVERDUE",
        "dealer_id": 1,
        "total_amount": 10000000.0,
        "status": "CONFIRMED",
        "created_at": old_date,
    }

    try:
        res = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {token_sales}"},
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 1, "price": 100000.0}],
            },
        )
        assert res.status_code == 400
        assert "quá hạn" in res.json()["detail"].lower()
    finally:
        if 8888 in ORDERS_DB:
            del ORDERS_DB[8888]
