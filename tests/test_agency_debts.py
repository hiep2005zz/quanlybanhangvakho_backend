# backend/tests/test_agency_debts.py
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.user import USERS_DB, UserInDB, DEFAULT_HASH, save_users_db
from app.core.rbac import Role

client = TestClient(app)


@pytest.fixture
def customer_token():
    USERS_DB["test_debt_cust"] = UserInDB(
        id=101,
        username="test_debt_cust",
        full_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        email="saomai@daily.vn",
        phone="0912345678",
        role=Role.CUSTOMER.value,
        roles=[Role.CUSTOMER.value],
        hashed_password=DEFAULT_HASH,
        branch="Đại lý phân phối",
    )
    save_users_db()

    resp = client.post("/api/v1/auth/login", json={"username": "test_debt_cust", "password": "123"})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    yield token
    USERS_DB.pop("test_debt_cust", None)
    save_users_db()


def test_customer_debt_summary(customer_token):
    response = client.get(
        "/api/v1/agency-debts/summary",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "credit_limit" in data
    assert "total_debt" in data
    assert "remaining_credit" in data
    assert "overdue_invoices_count" in data
    assert "nearest_due_days" in data
    assert data["dealer_name"] == "Đại Lý Phân Phối Miền Bắc - Sao Mai"
    # Kiểm tra tính toán hạn mức còn lại
    expected_remaining = max(0.0, data["credit_limit"] - data["total_debt"] - data["pending_orders_amount"])
    assert abs(data["remaining_credit"] - expected_remaining) < 0.01


def test_customer_invoices_list(customer_token):
    response = client.get(
        "/api/v1/agency-debts/invoices?page=1&page_size=10",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "total_pages" in data

    if data["items"]:
        inv = data["items"][0]
        assert "invoice_code" in inv
        assert "order_code" in inv
        assert "issue_date" in inv
        assert "due_date" in inv
        assert "total_amount" in inv
        assert "paid_amount" in inv
        assert "remaining_debt" in inv
        assert "status" in inv
        assert "status_label" in inv


def test_customer_invoice_detail_and_download(customer_token):
    list_res = client.get(
        "/api/v1/agency-debts/invoices?page=1&page_size=5",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    if items:
        code = items[0]["invoice_code"]
        # Chi tiết
        detail_res = client.get(
            f"/api/v1/agency-debts/invoices/{code}",
            headers={"Authorization": f"Bearer {customer_token}"},
        )
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["invoice_code"] == code
        assert "seller" in detail
        assert "dealer" in detail
        assert "items" in detail

        # Download XML
        xml_res = client.get(
            f"/api/v1/agency-debts/invoices/{code}/download?format=xml",
            headers={"Authorization": f"Bearer {customer_token}"},
        )
        assert xml_res.status_code == 200
        assert "application/xml" in xml_res.headers.get("content-type", "")
        assert f"HD_{code}" in xml_res.text

        # Download JSON
        json_res = client.get(
            f"/api/v1/agency-debts/invoices/{code}/download?format=json",
            headers={"Authorization": f"Bearer {customer_token}"},
        )
        assert json_res.status_code == 200
        assert "application/json" in json_res.headers.get("content-type", "")

        # Download PDF
        pdf_res = client.get(
            f"/api/v1/agency-debts/invoices/{code}/download?format=pdf",
            headers={"Authorization": f"Bearer {customer_token}"},
        )
        assert pdf_res.status_code == 200
        assert "application/pdf" in pdf_res.headers.get("content-type", "")
        assert pdf_res.content[:4] == b"%PDF"
