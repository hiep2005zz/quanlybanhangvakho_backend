import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token

client = TestClient(app)

@pytest.fixture
def admin_token():
    return create_access_token("admin", "admin")

@pytest.fixture
def accountant_token():
    return create_access_token("ketoan", "accountant")

@pytest.fixture
def sales_token():
    return create_access_token("sale1", "sales")


def test_get_deliveries_default_page_size_10(admin_token):
    """GET /sales-invoices/deliveries defaults to page_size=10 and returns 10 items."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/api/v1/sales-invoices/deliveries", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "data" in data
    assert "pagination" in data
    pagination = data["pagination"]
    assert pagination["page"] == 1
    assert pagination["page_size"] == 10
    assert pagination["total_records"] >= 20
    assert len(data["data"]) == 10


def test_get_deliveries_page_2_loads_next_10(admin_token):
    """GET /sales-invoices/deliveries?page=2&page_size=10 loads next 10 items without duplicate IDs."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    res1 = client.get("/api/v1/sales-invoices/deliveries?page=1&page_size=10", headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()

    res2 = client.get("/api/v1/sales-invoices/deliveries?page=2&page_size=10", headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()

    assert data2["pagination"]["page"] == 2
    assert data2["pagination"]["page_size"] == 10
    assert len(data2["data"]) == 10

    ids1 = {d["dispatch_id"] for d in data1["data"]}
    ids2 = {d["dispatch_id"] for d in data2["data"]}
    # Đảm bảo không trùng lặp bản ghi giữa trang 1 và trang 2
    assert len(ids1.intersection(ids2)) == 0


def test_get_deliveries_accountant_access_granted(accountant_token):
    """Accountant role has full permission to read delivery dispatches for invoicing."""
    headers = {"Authorization": f"Bearer {accountant_token}"}
    res = client.get("/api/v1/sales-invoices/deliveries?page=1&page_size=10", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "data" in data
    assert len(data["data"]) == 10
