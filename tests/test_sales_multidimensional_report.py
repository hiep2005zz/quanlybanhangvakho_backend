import time
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_sales_report_unauthorized():
    # 1. Không có token -> 401/403
    resp = client.get("/api/v1/reports/sales-multidimensional")
    assert resp.status_code in (401, 403)


def test_sales_report_warehouse_blocked():
    # 2. Nhân viên kho bị chặn quyền truy cập báo cáo kinh doanh
    kho_token = get_token("kho")
    resp = client.get(
        "/api/v1/reports/sales-multidimensional",
        headers={"Authorization": f"Bearer {kho_token}"}
    )
    assert resp.status_code == 403


def test_sales_report_salesperson_access_hides_profit():
    # 3. Nhân viên kinh doanh (sales) xem được doanh số nhưng BỊ ẨN giá vốn & biên lợi nhuận
    sales_token = get_token("sales")
    resp = client.get(
        "/api/v1/reports/sales-multidimensional?dimension=category",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_view_profit"] is False
    assert data["summary"]["can_view_profit"] is False
    assert data["summary"]["total_cost"] is None
    assert data["summary"]["total_gross_profit"] is None
    assert data["summary"]["average_gross_margin_pct"] is None

    # Kiểm tra các items
    for item in data["items"]:
        assert item["cost"] is None
        assert item["gross_profit"] is None
        assert item["gross_margin_pct"] is None
        assert "revenue" in item
        assert "quantity" in item


def test_sales_report_manager_and_admin_sees_profit():
    # 4. Quản lý kinh doanh (sales_manager) và Admin xem được đầy đủ giá vốn và biên lợi nhuận
    for username in ["admin", "sales_manager"]:
        token = get_token(username)
        t0 = time.time()
        resp = client.get(
            "/api/v1/reports/sales-multidimensional?dimension=category",
            headers={"Authorization": f"Bearer {token}"}
        )
        elapsed = time.time() - t0
        assert resp.status_code == 200, f"User {username} should have access, got {resp.status_code}"
        assert elapsed < 2.0, f"Query took {elapsed:.2f}s, must be under 2s"

        data = resp.json()
        assert data["can_view_profit"] is True
        assert data["summary"]["can_view_profit"] is True
        assert data["summary"]["total_cost"] is not None
        assert data["summary"]["total_gross_profit"] is not None
        assert data["summary"]["average_gross_margin_pct"] is not None


def test_sales_report_all_5_dimensions():
    # 5. Kiểm tra đầy đủ 5 chiều phân tích: salesperson, region, dealer, category, sku
    mgr_token = get_token("sales_manager")
    dimensions = ["salesperson", "region", "dealer", "category", "sku"]
    for dim in dimensions:
        resp = client.get(
            f"/api/v1/reports/sales-multidimensional?dimension={dim}&time_preset=this_month",
            headers={"Authorization": f"Bearer {mgr_token}"}
        )
        assert resp.status_code == 200, f"Failed for dimension {dim}: {resp.text}"
        data = resp.json()
        assert data["dimension"] == dim
        assert "summary" in data
        assert "items" in data
        assert isinstance(data["items"], list)
