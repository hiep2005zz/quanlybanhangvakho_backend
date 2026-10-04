import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.dealer import DEALERS_DB, save_dealers_db

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_dealer_search_and_filters():
    token = get_token("sales")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Tra cứu danh sách không điều kiện
    res = client.get("/api/v1/dealers/search", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1

    # Kiểm tra mỗi đại lý trả về đầy đủ các trường
    sample = data["items"][0]
    for key in ["id", "code", "name", "phone", "region", "assigned_sale_name", "customer_group", "status"]:
        assert key in sample

    # 2. Tìm kiếm nhanh theo TÊN
    res = client.get("/api/v1/dealers/search?keyword=Sao+Mai", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    assert any("Sao Mai" in it["name"] for it in items)

    # 3. Tìm kiếm nhanh theo MÃ
    res = client.get("/api/v1/dealers/search?keyword=DL002", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["code"] == "DL002"

    # 4. Tìm kiếm nhanh theo SỐ ĐIỆN THOẠI (cả dạng thường và dạng có dấu cách)
    res = client.get("/api/v1/dealers/search?keyword=0912345678", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert any("0912345678" in it["phone"] for it in items)

    res_spaced = client.get("/api/v1/dealers/search?keyword=0912+345", headers=headers)
    assert res_spaced.status_code == 200
    items_spaced = res_spaced.json()["items"]
    assert any("0912345678" in it["phone"] for it in items_spaced)

    # 5. Lọc theo KHU VỰC
    res = client.get("/api/v1/dealers/search?region=Hà+Nội", headers=headers)
    assert res.status_code == 200
    for it in res.json()["items"]:
        assert "Hà Nội" in it["region"]

    # 6. Lọc theo NHÓM KHÁCH HÀNG
    res = client.get("/api/v1/dealers/search?customer_group=Khách+sỉ", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    for it in items:
        assert it["customer_group"] == "Khách sỉ"

    # 7. Lọc theo NGƯỜI PHỤ TRÁCH (assigned_sale_id)
    res = client.get("/api/v1/dealers/search?assigned_sale_id=3", headers=headers)
    assert res.status_code == 200
    for it in res.json()["items"]:
        assert it["assigned_sale_id"] == 3

    # 8. Lọc theo TRẠNG THÁI
    res = client.get("/api/v1/dealers/search?status=Đang+hoạt+động", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    for it in items:
        assert it["status"] == "Đang hoạt động"

    # 9. Lấy dữ liệu cấu hình bộ lọc (/filters)
    res = client.get("/api/v1/dealers/filters", headers=headers)
    assert res.status_code == 200
    filters = res.json()
    assert "regions" in filters and len(filters["regions"]) >= 1
    assert "sales" in filters and len(filters["sales"]) >= 1
    assert "customer_groups" in filters and "dai_ly_cap_1" in filters["customer_groups"]
    assert "statuses" in filters and "Đang hoạt động" in filters["statuses"]

    # 10. Tạo mới đại lý qua API POST
    import time
    unique_code = f"DL-T{int(time.time())}"
    new_dealer_payload = {
        "code": unique_code,
        "name": "Đại Lý Thử Nghiệm Hà Đông",
        "phone": "0988998877",
        "email": "hadong.test@daily.vn",
        "address": "45 Quang Trung, Hà Đông, Hà Nội",
        "region": "Hà Nội",
        "customer_group": "dai_ly_cap_1",
        "status": "Đang hoạt động"
    }
    create_res = client.post("/api/v1/dealers", json=new_dealer_payload, headers=headers)
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["code"] == unique_code
    assert created["name"] == "Đại Lý Thử Nghiệm Hà Đông"
    assert created["customer_group"] == "dai_ly_cap_1"
    assert created["status"] == "Đang hoạt động"

    # Tìm lại đại lý vừa tạo bằng keyword
    verify_res = client.get(f"/api/v1/dealers/search?keyword={unique_code}", headers=headers)
    assert verify_res.status_code == 200
    assert any(it["code"] == unique_code for it in verify_res.json()["items"])

    # 11. Kiểm tra phân quyền truy cập: role 'kho' (thủ kho) không có quyền tra cứu
    kho_token = get_token("kho")
    kho_headers = {"Authorization": f"Bearer {kho_token}"}
    res_kho = client.get("/api/v1/dealers/search", headers=kho_headers)
    assert res_kho.status_code == 403

    # 12. Kiểm thử cập nhật trạng thái đại lý (PATCH /dealers/{id}/status)
    # Tạm ngừng
    patch_pause = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Tạm ngừng", "reason": "Kiểm tra tính năng tạm ngừng"},
        headers=headers,
    )
    assert patch_pause.status_code == 200
    assert patch_pause.json()["status"] == "Tạm ngừng"

    # Kích hoạt lại
    patch_active = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Đang hoạt động", "reason": "Kích hoạt lại"},
        headers=headers,
    )
    assert patch_active.status_code == 200
    assert patch_active.json()["status"] == "Đang hoạt động"

    # Trạng thái không hợp lệ -> 400
    patch_invalid = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Trạng thái lạ"},
        headers=headers,
    )
    assert patch_invalid.status_code == 400

    # Thủ kho đổi trạng thái -> 403
    patch_kho = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Tạm ngừng"},
        headers=kho_headers,
    )
    assert patch_kho.status_code == 403

    # Dọn dẹp đại lý test
    if created["id"] in DEALERS_DB:
        del DEALERS_DB[created["id"]]
        save_dealers_db()
