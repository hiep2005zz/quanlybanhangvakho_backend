import time
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_executive_metrics_unauthorized():
    # 1. Không có token -> 401/403
    resp = client.get("/api/v1/dashboard/executive-metrics")
    assert resp.status_code in (401, 403)


def test_executive_metrics_admin_and_sales_manager_authorized():
    # 2. Admin và Sales Manager truy cập thành công
    admin_token = get_token("admin")
    # Khởi tạo kết nối DB (warm-up connection pool)
    client.get("/api/v1/dashboard/executive-metrics", headers={"Authorization": f"Bearer {admin_token}"})

    for username in ["admin", "sales_manager"]:
        token = get_token(username)
        start_time = time.time()
        resp = client.get(
            "/api/v1/dashboard/executive-metrics",
            headers={"Authorization": f"Bearer {token}"}
        )
        elapsed = time.time() - start_time

        assert resp.status_code == 200, f"User {username} should have access, got {resp.status_code}: {resp.text}"
        assert elapsed < 6.0, f"Dashboard metrics took {elapsed:.2f}s, must be under 6s!"

        data = resp.json()
        assert "kpis" in data
        assert "daily_revenue" in data
        assert "revenue_by_region" in data
        assert "top_overdue_dealers" in data
        assert "updated_at" in data

        kpis = data["kpis"]
        assert "revenue_today" in kpis
        assert "revenue_this_month" in kpis
        assert "pending_approval_orders_count" in kpis
        assert "pending_export_orders_count" in kpis
        assert "total_overdue_debt" in kpis
        assert "low_stock_skus_count" in kpis

        # Kiểm tra danh sách doanh số ngày trong tháng
        assert len(data["daily_revenue"]) >= 28

        # Kiểm tra danh sách vùng miền
        assert len(data["revenue_by_region"]) >= 1


def test_executive_metrics_warehouse_blocked():
    # 3. Nhân viên kho không được phép truy cập Dashboard điều hành kinh doanh
    kho_token = get_token("kho")
    resp = client.get(
        "/api/v1/dashboard/executive-metrics",
        headers={"Authorization": f"Bearer {kho_token}"}
    )
    assert resp.status_code == 403
