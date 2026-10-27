import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def test_get_stock_book_unauthorized():
    resp = client.get("/api/v1/inventory/stock-book")
    assert resp.status_code in [401, 403]


def test_get_stock_book_structure():
    token = get_token("admin")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/v1/inventory/stock-book", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert "summary" in data
    assert "items" in data
    assert "warehouses" in data
    assert "categories" in data

    summary = data["summary"]
    assert "total_actual_stock" in summary
    assert "total_reserved_stock" in summary
    assert "total_available_stock" in summary
    assert "total_skus" in summary
    assert summary["total_skus"] == len(data["items"])

    # Kiểm tra cấu trúc từng item
    assert len(data["items"]) > 0
    item = data["items"][0]
    assert "warehouse_id" in item
    assert "warehouse_name" in item
    assert "product_code" in item
    assert "product_name" in item
    assert "actual_stock" in item
    assert "reserved_stock" in item
    assert "available_stock" in item
    assert "status" in item

    # Kiểm tra logic tính toán tồn khả dụng: available_stock = max(0, actual_stock - reserved_stock)
    for it in data["items"]:
        expected_avail = max(0, it["actual_stock"] - it["reserved_stock"])
        assert it["available_stock"] == expected_avail


def test_get_stock_book_filters():
    token = get_token("admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Lọc theo warehouse_id WH01
    resp = client.get("/api/v1/inventory/stock-book?warehouse_id=WH01", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for it in data["items"]:
        assert it["warehouse_id"] == "WH01"

    # 2. Tìm kiếm theo mã SKU 'SP001'
    resp = client.get("/api/v1/inventory/stock-book?search=SP001", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) > 0
    for it in data["items"]:
        assert "SP001" in it["product_code"] or "SP001" in it["product_name"]

    # 3. Lọc theo trạng thái 'available'
    resp = client.get("/api/v1/inventory/stock-book?stock_status=available", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for it in data["items"]:
        assert it["available_stock"] > 0


def test_purchasing_role_forbidden_stock_book():
    from app.core.security import create_access_token
    token = create_access_token(subject="muahang", role="purchasing", token_version=1)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/v1/inventory/stock-book", headers=headers)
    assert resp.status_code == 403, f"Purchasing user should be forbidden (403), got {resp.status_code}: {resp.text}"

