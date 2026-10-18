# backend/tests/test_sales_targets.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_sales_targets_unauthorized():
    # 1. Không có token -> 401 hoặc 403
    resp = client.get("/api/v1/sales-targets")
    assert resp.status_code in (401, 403)


def test_sales_targets_warehouse_blocked():
    # 2. Nhân viên kho không có quyền truy cập chỉ tiêu doanh số
    kho_token = get_token("kho")
    resp = client.get(
        "/api/v1/sales-targets",
        headers={"Authorization": f"Bearer {kho_token}"}
    )
    assert resp.status_code == 403


def test_sales_targets_salesperson_isolated():
    # 3. Nhân viên sale chỉ thấy chỉ tiêu của chính mình, can_manage = False
    sales_token = get_token("sales")
    resp = client.get(
        "/api/v1/sales-targets?month_year=2026-10",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_manage"] is False
    # Tất cả items loại salesperson phải là của chính nhân viên sale (id = 3)
    for it in data["items"]:
        if it["target_type"] == "salesperson":
            assert it["employee_id"] == 3


def test_sales_targets_manager_and_admin_full_access():
    # 4. Quản lý kinh doanh và Admin có can_manage = True và xem được tất cả
    for user in ["sales_manager", "admin"]:
        token = get_token(user)
        resp = client.get(
            "/api/v1/sales-targets?month_year=2026-10",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["can_manage"] is True
        assert "summary" in data
        assert data["summary"]["total_count"] >= 1


def test_create_and_delete_sales_target():
    # 5. Tạo mới chỉ tiêu cho nhân viên và xóa
    mgr_token = get_token("sales_manager")
    create_payload = {
        "target_type": "salesperson",
        "employee_id": 1,
        "employee_name": "Đào Ngọc Hiệp",
        "month_year": "2026-11",
        "target_amount": 75000000.0,
        "note": "Chỉ tiêu tháng 11 thử nghiệm"
    }
    resp = client.post(
        "/api/v1/sales-targets",
        json=create_payload,
        headers={"Authorization": f"Bearer {mgr_token}"}
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["target_amount"] == 75000000.0
    assert created["is_locked"] is False
    target_id = created["id"]

    # Xóa chỉ tiêu vừa tạo
    del_resp = client.delete(
        f"/api/v1/sales-targets/{target_id}",
        headers={"Authorization": f"Bearer {mgr_token}"}
    )
    assert del_resp.status_code == 200


def test_update_locked_target_enforces_reason_and_audit():
    # 6. Kỳ đã qua (2026-09) bị khóa: Sửa không có lý do -> 400; có lý do -> thành công và lưu audit log
    mgr_token = get_token("sales_manager")
    
    # Target id 2 là tháng 2026-09 (đã qua kỳ chốt sổ)
    # Lần 1: Cố tình không gửi lý do
    fail_resp = client.put(
        "/api/v1/sales-targets/2",
        json={"target_amount": 55000000.0, "reason": ""},
        headers={"Authorization": f"Bearer {mgr_token}"}
    )
    assert fail_resp.status_code == 400
    assert "lý do điều chỉnh" in fail_resp.json()["detail"].lower()

    # Lần 2: Gửi kèm lý do hợp lệ
    success_resp = client.put(
        "/api/v1/sales-targets/2",
        json={
            "target_amount": 55000000.0,
            "reason": "Điều chỉnh theo quyết định tăng chỉ tiêu bổ sung hợp đồng lớn"
        },
        headers={"Authorization": f"Bearer {mgr_token}"}
    )
    assert success_resp.status_code == 200
    updated = success_resp.json()
    assert updated["target_amount"] == 55000000.0
    assert updated["is_locked"] is True
    assert len(updated["audit_logs"]) >= 1
    last_log = updated["audit_logs"][-1]
    assert "tăng chỉ tiêu" in last_log["reason"]
    assert last_log["new_target_amount"] == 55000000.0


def test_salesperson_my_progress():
    # 7. Endpoint cá nhân /my-progress cho nhân viên sale
    sales_token = get_token("sales")
    resp = client.get(
        "/api/v1/sales-targets/my-progress",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "progress_pct" in data
    assert "remaining_amount" in data
    assert "actual_amount" in data
    assert data["month_year"] == "2026-10"
