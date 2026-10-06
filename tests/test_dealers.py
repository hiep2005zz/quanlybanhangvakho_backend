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


def test_dealer_profile_management_by_accountant():
    import time

    token = get_token("ketoan")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Tra cứu bảng giá áp dụng theo nhóm khách hàng (/dealers/price-book-preview)
    res_preview = client.get("/api/v1/dealers/price-book-preview?customer_group=Đại+lý+cấp+1", headers=headers)
    assert res_preview.status_code == 200
    preview_data = res_preview.json()
    assert preview_data["customer_group"] == "Đại lý cấp 1"
    assert preview_data["applied_price_book"] is not None
    assert preview_data["applied_price_book"]["name"] is not None
    assert preview_data["applied_price_book"]["code"] is not None

    # 2. Khai báo hồ sơ đại lý mới đầy đủ các trường (Mã, Tên, MST, Nhóm KH, Khu vực, Người phụ trách, Trạng thái)
    code_1 = f"DL-ACC-{int(time.time())}"
    create_payload = {
        "code": code_1,
        "name": "Công ty TNHH Đại Lý An Phát",
        "tax_code": "0109887766",
        "customer_group": "Đại lý cấp 1",
        "region": "Hà Nội",
        "assigned_sale_id": 3,
        "phone": "0987654321",
        "email": "anphat@daily.com",
        "address": "Số 10 Nguyễn Trãi, Hà Nội",
        "status": "Đang hoạt động"
    }
    res_create = client.post("/api/v1/dealers", json=create_payload, headers=headers)
    assert res_create.status_code == 201
    created_dealer = res_create.json()
    assert created_dealer["code"] == code_1
    assert created_dealer["tax_code"] == "0109887766"
    assert created_dealer["customer_group"] == "Đại lý cấp 1"
    assert created_dealer["applied_price_book"] is not None
    assert created_dealer["applied_price_book"]["code"] is not None
    dealer_id = created_dealer["id"]

    # 3. Ràng buộc: Mã đại lý là duy nhất (Trùng mã đại lý phải bị từ chối 400)
    dup_payload = {
        "code": code_1,
        "name": "Đại Lý Trùng Mã",
        "tax_code": "0311223344",
        "customer_group": "Đại lý cấp 1",
        "region": "Đà Nẵng",
        "status": "Đang hoạt động"
    }
    res_dup = client.post("/api/v1/dealers", json=dup_payload, headers=headers)
    assert res_dup.status_code == 400
    assert "đã tồn tại" in res_dup.json()["detail"]

    # 4. Cập nhật hồ sơ đại lý (PUT /dealers/{id})
    update_payload = {
        "code": code_1,
        "name": "Công ty TNHH Đại Lý An Phát (Đã cập nhật)",
        "tax_code": "0109887799",
        "customer_group": "Đại lý cấp 1",
        "region": "Hà Nội",
        "assigned_sale_id": 3,
        "phone": "0987654321",
        "email": "anphat@daily.com",
        "address": "Số 12 Nguyễn Trãi, Hà Nội",
        "status": "Đang hoạt động"
    }
    res_update = client.put(f"/api/v1/dealers/{dealer_id}", json=update_payload, headers=headers)
    assert res_update.status_code == 200
    updated_dealer = res_update.json()
    assert updated_dealer["name"] == "Công ty TNHH Đại Lý An Phát (Đã cập nhật)"
    assert updated_dealer["tax_code"] == "0109887799"
    assert updated_dealer["customer_group"] == "Đại lý cấp 1"

    # 5. Ràng buộc: Đại lý đã phát sinh giao dịch thì không xoá được, chỉ ngừng giao dịch
    # Giả lập phát sinh 1 đơn hàng cho đại lý này
    from app.api.v1.endpoints.orders import ORDERS_DB
    dummy_order_id = f"ORD-TEST-{int(time.time())}"
    ORDERS_DB[dummy_order_id] = {
        "id": dummy_order_id,
        "order_number": dummy_order_id,
        "created_at": "2026-10-06T12:00:00",
        "customer_name": updated_dealer["name"],
        "dealer_id": dealer_id,
        "customer_phone": "0987654321",
        "customer_address": "Hà Nội",
        "warehouse_id": "WH01",
        "items": [],
        "total_amount": 1000000,
        "discount_amount": 0,
        "final_amount": 1000000,
        "status": "completed",
        "creator_id": 1,
        "creator_name": "Kế toán"
    }

    # Thử xóa khi đã phát sinh giao dịch -> Bị chặn 400
    res_del_blocked = client.delete(f"/api/v1/dealers/{dealer_id}", headers=headers)
    assert res_del_blocked.status_code == 400
    assert "không thể xóa" in res_del_blocked.json()["detail"] or "không thể xoá" in res_del_blocked.json()["detail"]
    assert "Ngừng giao dịch" in res_del_blocked.json()["detail"]

    # Chuyển trạng thái sang "Ngừng giao dịch" -> Thành công
    res_stop = client.patch(
        f"/api/v1/dealers/{dealer_id}/status",
        json={"status": "Ngừng giao dịch", "reason": "Dừng kinh doanh theo quyết định kế toán"},
        headers=headers
    )
    assert res_stop.status_code == 200
    assert res_stop.json()["status"] == "Ngừng giao dịch"

    # Dọn dẹp đơn hàng test và xóa đại lý sau khi xóa đơn
    del ORDERS_DB[dummy_order_id]

    # Khi không còn đơn hàng nào -> Xóa thành công
    res_del_success = client.delete(f"/api/v1/dealers/{dealer_id}", headers=headers)
    assert res_del_success.status_code == 200


def test_dealer_transaction_count_constraint():
    token = get_token("sales")
    headers = {"Authorization": f"Bearer {token}"}
    import time
    unique_code = f"TX{int(time.time()) % 100000:05d}"

    # 1. Tạo đại lý mới chưa có giao dịch
    res_create = client.post(
        "/api/v1/dealers",
        json={
            "code": unique_code,
            "name": "Đại Lý Kiểm Tra Giao Dịch",
            "customer_group": "dai_ly_cap_1",
            "region": "Hà Nội",
            "status": "Đang hoạt động",
        },
        headers=headers,
    )
    assert res_create.status_code == 201
    dealer_id = res_create.json()["id"]

    # 2. Cập nhật số lượng giao dịch âm -> Bị chặn với lỗi ràng buộc
    res_invalid_neg = client.put(
        f"/api/v1/dealers/{dealer_id}",
        json={"transaction_count": -2},
        headers=headers,
    )
    assert res_invalid_neg.status_code == 400
    assert "Số lượng giao dịch phải lớn hơn hoặc bằng 0" in res_invalid_neg.json()["detail"]

    # Cập nhật số lượng giao dịch = 0 (hợp lệ >= 0) -> Thành công
    res_valid_0 = client.put(
        f"/api/v1/dealers/{dealer_id}",
        json={"transaction_count": 0},
        headers=headers,
    )
    assert res_valid_0.status_code == 200
    assert res_valid_0.json()["transaction_count"] == 0

    # 3. Cập nhật số lượng giao dịch hợp lệ (> 0) -> Thành công
    res_valid = client.put(
        f"/api/v1/dealers/{dealer_id}",
        json={"transaction_count": 5},
        headers=headers,
    )
    assert res_valid.status_code == 200
    data_valid = res_valid.json()
    assert data_valid["transaction_count"] == 5
    assert data_valid["has_transactions"] is True

    # 4. Kiểm tra ràng buộc kiểm toán: đã phát sinh giao dịch (> 0) thì không xóa được
    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    res_del_blocked = client.delete(f"/api/v1/dealers/{dealer_id}", headers=admin_headers)
    assert res_del_blocked.status_code == 400
    assert "không thể xóa" in res_del_blocked.json()["detail"] or "không thể xoá" in res_del_blocked.json()["detail"]
    assert "Ngừng giao dịch" in res_del_blocked.json()["detail"]

    # 5. Có thể chuyển trạng thái sang "Ngừng giao dịch"
    res_stop = client.patch(
        f"/api/v1/dealers/{dealer_id}/status",
        json={"status": "Ngừng giao dịch"},
        headers=headers,
    )
    assert res_stop.status_code == 200
    assert res_stop.json()["status"] == "Ngừng giao dịch"

