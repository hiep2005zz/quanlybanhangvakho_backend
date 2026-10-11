import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def test_get_warehouses_list():
    resp = client.get("/api/v1/inventory/warehouses")
    assert resp.status_code == 200
    warehouses = resp.json()
    assert len(warehouses) >= 3
    wh_ids = [w["warehouse_id"] for w in warehouses]
    assert "WH01" in wh_ids
    assert "WH02" in wh_ids
    assert "WH03" in wh_ids


def test_get_thresholds_requires_permission():
    # Không có token
    resp = client.get("/api/v1/inventory/thresholds")
    assert resp.status_code in [401, 403]

    token = get_token("admin")
    resp = client.get("/api/v1/inventory/thresholds", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) > 0
    first = items[0]
    assert "warehouse_id" in first
    assert "product_id" in first
    assert "min_stock" in first
    assert "is_low_stock" in first
    assert "shortage_quantity" in first


def test_update_threshold_and_alerts():
    token = get_token("admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Đặt min_stock cao hơn tồn kho thực tế để kích hoạt cảnh báo
    update_payload = {
        "warehouse_id": "WH01",
        "product_id": 1,
        "min_stock": 500,  # Tồn thực tế của SP001 khoảng 100-120 < 500
    }
    resp = client.put("/api/v1/inventory/thresholds", json=update_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["warehouse_id"] == "WH01"
    assert data["product_id"] == 1
    assert data["min_stock"] == 500
    assert data["is_low_stock"] is True
    assert data["shortage_quantity"] > 0

    # Kiểm tra endpoint low-stock-alerts
    alerts_resp = client.get("/api/v1/inventory/low-stock-alerts", headers=headers)
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert alerts["total_low_stock"] >= 1
    low_item = next((it for it in alerts["items"] if it["product_id"] == 1 and it["warehouse_id"] == "WH01"), None)
    assert low_item is not None
    assert low_item["is_low_stock"] is True

    # Đặt lại min_stock nhỏ để không còn cảnh báo
    update_payload_reset = {
        "warehouse_id": "WH01",
        "product_id": 1,
        "min_stock": 5,
    }
    resp_reset = client.put("/api/v1/inventory/thresholds", json=update_payload_reset, headers=headers)
    assert resp_reset.status_code == 200
    assert resp_reset.json()["is_low_stock"] is False


def test_warehouse_staff_forbidden_and_warehouse_manager_allowed():
    """
    Quy định phân quyền:
    - Thủ kho (Role 'kho' / 'warehouse') KHÔNG ĐƯỢC PHÉP cấu hình định mức tồn (403 Forbidden).
    - Quản lý kho (Role 'warehouse_mgr' / 'warehouse_manager') ĐƯỢC PHÉP cấu hình định mức tồn (200 OK).
    """
    kho_token = get_token("kho")
    wm_token = get_token("warehouse_mgr")

    payload = {
        "warehouse_id": "WH01",
        "product_id": 1,
        "min_stock": 25,
    }

    # 1. Thủ kho gửi yêu cầu cập nhật định mức -> Bị chặn 403 Forbidden
    res_kho = client.put(
        "/api/v1/inventory/thresholds",
        json=payload,
        headers={"Authorization": f"Bearer {kho_token}"}
    )
    assert res_kho.status_code == 403, f"Thủ kho phải bị chặn 403 nhưng nhận {res_kho.status_code}"

    # 2. Quản lý kho gửi yêu cầu cập nhật định mức -> Thành công 200 OK
    res_wm = client.put(
        "/api/v1/inventory/thresholds",
        json=payload,
        headers={"Authorization": f"Bearer {wm_token}"}
    )
    assert res_wm.status_code == 200, f"Quản lý kho phải được phép nhưng nhận {res_wm.status_code}"
    assert res_wm.json()["min_stock"] == 25

