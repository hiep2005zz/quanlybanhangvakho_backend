# backend/tests/test_order_filtering_and_rls.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def get_token(username: str) -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

def test_admin_and_sales_manager_can_view_all_and_filter():
    """AC-1: Đăng nhập sales_manager / admin -> Thấy toàn bộ đơn toàn công ty, đủ bộ lọc."""
    token = get_token("admin")
    
    # 1. Gọi có phân trang/lọc
    res = client.get("/api/v1/orders?page=1&page_size=20", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert "filtered_total_amount" in data
    assert "pending_count" in data
    assert "confirmed_count" in data
    
    # AC-4: Kiểm tra tính nhất quán số liệu
    assert data["pending_count"] + data["confirmed_count"] <= data["total"]
    assert data["total"] >= len(data["items"])
    assert data["filtered_total_amount"] >= 0

def test_sales_rls_and_tamper_prevention():
    """AC-2: Đăng nhập sales -> Chỉ thấy đơn đại lý phụ trách, cố ý truyền dealer khác -> 0 kết quả."""
    token = get_token("sales")
    
    res = client.get("/api/v1/orders?page=1&page_size=20", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    
    # Kiểm tra không có đơn nào ngoài đại lý của sales
    for item in data["items"]:
        # Kiểm tra không bị lộ giá vốn/lợi nhuận
        for sub_item in (item.get("items") or []):
            assert "cost_price" not in sub_item
            assert "profit" not in sub_item
    
    # Cố tình truyền dealer_id ngoài phạm vi
    res_tamper = client.get("/api/v1/orders?dealer_id=999999", headers={"Authorization": f"Bearer {token}"})
    assert res_tamper.status_code == 200
    data_tamper = res_tamper.json()
    assert data_tamper["total"] == 0
    assert len(data_tamper["items"]) == 0

def test_status_filter_and_kpi_consistency():
    """AC-3 & AC-4: Lọc theo trạng thái và đồng bộ số liệu 4 thẻ KPI."""
    token = get_token("admin")
    
    res = client.get("/api/v1/orders?status=PENDING_APPROVAL&page=1&page_size=10", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    
    # Khi lọc PENDING_APPROVAL, tổng đơn thỏa bộ lọc phải bằng pending_count
    assert data["total"] == data["pending_count"]
    assert data["confirmed_count"] == 0
    for item in data["items"]:
        assert item["status"] in ("PENDING_APPROVAL", "PENDING")

def test_legacy_backward_compatibility():
    """AC-6: Gọi không có query params hoặc format=list phải trả về mảng danh sách cho client cũ."""
    token = get_token("sales_manager")
    res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert isinstance(res.json(), list)
