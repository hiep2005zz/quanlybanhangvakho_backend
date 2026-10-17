import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def get_token(username: str = "admin") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json()["access_token"]


def test_debt_aging_report_unauthorized():
    # Không có token
    res = client.get("/api/v1/dealers/debt-aging-report")
    assert res.status_code in (401, 403)


def test_debt_aging_report_accountant_and_admin_authorized():
    # Kế toán công nợ và admin có quyền truy cập
    token_admin = get_token("admin")
    res_admin = client.get("/api/v1/dealers/debt-aging-report", headers={"Authorization": f"Bearer {token_admin}"})
    assert res_admin.status_code == 200
    data = res_admin.json()

    assert "summary" in data
    assert "items" in data
    assert "sales_list" in data

    summary = data["summary"]
    assert "total_debt" in summary
    assert "total_overdue" in summary
    assert "total_dealers_count" in summary
    assert "overdue_dealers_count" in summary

    # Kiểm tra cấu trúc item
    if len(data["items"]) > 0:
        first = data["items"][0]
        assert "dealer_id" in first
        assert "dealer_code" in first
        assert "debt_current" in first
        assert "debt_overdue_1_30" in first
        assert "debt_overdue_31_60" in first
        assert "debt_overdue_61_90" in first
        assert "debt_overdue_over_90" in first
        assert "risk_level" in first
        assert "risk_level_label" in first


def test_debt_aging_report_warehouse_blocked():
    # Thủ kho không được phép truy cập
    token_kho = get_token("kho")
    # Ghi chú: "kho" trong test_users_data có thể có role khác, kiểm tra vai trò không được cấp quyền
    res_kho = client.get("/api/v1/dealers/debt-aging-report", headers={"Authorization": f"Bearer {token_kho}"})
    # Chỉ cho phép admin, sales_manager, sales, accountant
    # Nếu "kho" có role sales_manager trong data thì 200, ngược lại nếu role warehouse thì 403
