# -*- coding: utf-8 -*-
import sys
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from app.main import app

def get_auth_token(client: TestClient, username: str = "admin", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_warehouse_crud_and_validation():
    client = TestClient(app)
    admin_token = get_auth_token(client, "admin", "123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Danh sách kho
    res = client.get("/api/v1/warehouses", headers=admin_headers)
    assert res.status_code == 200
    warehouses = res.json()
    assert len(warehouses) >= 3
    hn_wh = next((w for w in warehouses if w["code"] == "KHO_HN"), None)
    assert hn_wh is not None
    assert hn_wh["locations_count"] >= 1

    # 2. Thêm mới kho hàng
    new_wh_code = "KHO_HP_TEST"
    create_res = client.post("/api/v1/warehouses", json={
        "code": new_wh_code,
        "name": "Kho Thử Nghiệm Hải Phòng",
        "address": "Số 10 Lê Hồng Phong, Ngô Quyền, Hải Phòng",
        "manager_name": "Vũ Văn Cảng",
        "phone": "0933123456",
        "status": "Đang hoạt động",
    }, headers=admin_headers)
    assert create_res.status_code == 201
    created_data = create_res.json()
    wh_id = created_data["id"]
    assert created_data["code"] == new_wh_code
    assert created_data["manager_name"] == "Vũ Văn Cảng"

    # 3. Chặn trùng mã kho (400 Bad Request)
    dup_res = client.post("/api/v1/warehouses", json={
        "code": new_wh_code,
        "name": "Kho Trùng Mã",
    }, headers=admin_headers)
    assert dup_res.status_code == 400
    assert "đã tồn tại" in dup_res.json()["detail"].lower()

    # 4. Cập nhật kho hàng
    update_res = client.put(f"/api/v1/warehouses/{wh_id}", json={
        "name": "Kho Thử Nghiệm Hải Phòng (Đã sửa)",
        "phone": "0988776655",
        "status": "Tạm dừng",
    }, headers=admin_headers)
    assert update_res.status_code == 200
    assert update_res.json()["name"] == "Kho Thử Nghiệm Hải Phòng (Đã sửa)"
    assert update_res.json()["phone"] == "0988776655"

    # 5. Xem chi tiết kho
    detail_res = client.get(f"/api/v1/warehouses/{wh_id}", headers=admin_headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == wh_id

    # 6. Xóa kho trống vừa tạo thành công
    del_res = client.delete(f"/api/v1/warehouses/{wh_id}", headers=admin_headers)
    assert del_res.status_code == 200


def test_locations_and_product_assignment():
    client = TestClient(app)
    admin_token = get_auth_token(client, "admin", "123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Danh sách vị trí của Kho 1
    loc_res = client.get("/api/v1/warehouses/1/locations", headers=admin_headers)
    assert loc_res.status_code == 200
    locs = loc_res.json()
    assert len(locs) >= 2

    # 2. Thêm vị trí mới
    new_loc_code = "HN-KC-D9-T1"
    create_loc_res = client.post("/api/v1/warehouses/1/locations", json={
        "location_code": new_loc_code,
        "location_name": "Khu C - Kệ phụ số 9",
        "zone": "Khu C",
        "aisle": "Dãy 9",
        "rack": "Tầng 1",
        "bin": "Ô 01",
        "max_capacity": 300,
        "status": "Đang sử dụng",
    }, headers=admin_headers)
    assert create_loc_res.status_code == 201
    loc_data = create_loc_res.json()
    loc_id = loc_data["id"]

    # 3. Chặn trùng mã vị trí trong cùng kho
    dup_loc = client.post("/api/v1/warehouses/1/locations", json={
        "location_code": new_loc_code,
    }, headers=admin_headers)
    assert dup_loc.status_code == 400
    assert "đã tồn tại" in dup_loc.json()["detail"].lower()

    # 4. Gán sản phẩm vào vị trí
    assign_res = client.post("/api/v1/warehouses/locations/assign-product", json={
        "location_id": loc_id,
        "product_id": 1,
        "quantity": 25,
    }, headers=admin_headers)
    assert assign_res.status_code == 200
    assert assign_res.json()["quantity"] == 25

    # 5. Tra cứu danh sách sản phẩm theo vị trí trong kho
    wh_prods_res = client.get("/api/v1/warehouses/1/products?location_id=" + str(loc_id), headers=admin_headers)
    assert wh_prods_res.status_code == 200
    matched = wh_prods_res.json()
    assert len(matched) >= 1
    assert matched[0]["quantity"] == 25

    # 6. Chuyển sản phẩm sang vị trí khác trong cùng kho
    dest_loc_id = locs[0]["id"]
    transfer_res = client.post("/api/v1/warehouses/locations/transfer-product", json={
        "from_location_id": loc_id,
        "to_location_id": dest_loc_id,
        "product_id": 1,
        "quantity": 10,
    }, headers=admin_headers)
    assert transfer_res.status_code == 200

    # 7. Chuyển vượt quá số lượng tồn tại vị trí nguồn -> 400 Bad Request
    over_transfer = client.post("/api/v1/warehouses/locations/transfer-product", json={
        "from_location_id": loc_id,
        "to_location_id": dest_loc_id,
        "product_id": 1,
        "quantity": 9999,
    }, headers=admin_headers)
    assert over_transfer.status_code == 400

    # 8. Chặn xóa vị trí khi còn hàng tồn
    del_loc_fail = client.delete(f"/api/v1/warehouses/locations/{loc_id}", headers=admin_headers)
    assert del_loc_fail.status_code == 400
    assert "sản phẩm" in del_loc_fail.json()["detail"].lower()

    # Giải phóng hàng về 0 để xóa vị trí
    client.post("/api/v1/warehouses/locations/assign-product", json={
        "location_id": loc_id,
        "product_id": 1,
        "quantity": 0,
    }, headers=admin_headers)

    del_loc_ok = client.delete(f"/api/v1/warehouses/locations/{loc_id}", headers=admin_headers)
    assert del_loc_ok.status_code == 200


def test_assign_default_warehouse_to_dealer():
    client = TestClient(app)
    admin_token = get_auth_token(client, "admin", "123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Gán kho 2 (Đà Nẵng) cho đại lý 1
    assign_res = client.put("/api/v1/warehouses/dealers/1/default-warehouse", json={
        "warehouse_id": "2",
    }, headers=admin_headers)
    assert assign_res.status_code == 200
    assert assign_res.json()["warehouse_id"] == 2

    # Tra cứu lại đại lý 1 qua API dealers để xác nhận
    dealer_res = client.get("/api/v1/dealers/search?keyword=DL001", headers=admin_headers)
    assert dealer_res.status_code == 200
    d_list = dealer_res.json()["items"]
    assert len(d_list) >= 1
    d1 = next((d for d in d_list if d["id"] == 1), None)
    assert d1 is not None
    assert str(d1.get("warehouse_id")) in ["2", "KHO_DN"]


def test_order_picking_locations_api():
    client = TestClient(app)
    admin_token = get_auth_token(client, "admin", "123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    from app.api.v1.endpoints.orders import ORDERS_DB
    ORDERS_DB[1] = {
        "id": 1,
        "order_code": "DH-2026-0001",
        "dealer_id": 1,
        "dealer_name": "Công Ty Cổ Phần Phân Phối Tổng Hợp Sao Mai Toàn Cầu",
        "created_by": "sales",
        "total_amount": 35800000.0,
        "status": "CONFIRMED",
        "items": [
            {"product_id": 1, "product_name": "Áo sơ mi nam công sở Oxford", "quantity": 10, "price": 250000.0},
        ]
    }

    # Tra cứu thông tin vị trí kệ soạn đơn cho DH-2026-0001
    picking_res = client.get("/api/v1/warehouses/orders/DH-2026-0001/picking-locations", headers=admin_headers)
    assert picking_res.status_code == 200
    picking_items = picking_res.json()
    assert len(picking_items) >= 1
    for item in picking_items:
        assert "product_name" in item
        assert "locations" in item
        assert "warehouse_name" in item
        assert len(item["locations"]) >= 1
        loc_sample = item["locations"][0]
        assert "location_code" in loc_sample
        assert "available_quantity" in loc_sample


def test_rbac_protection_on_warehouses():
    client = TestClient(app)
    sales_token = get_auth_token(client, "sales", "123")
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # Sales được phép XEM danh sách kho và vị trí để phục vụ bán hàng & soạn đơn
    view_res = client.get("/api/v1/warehouses", headers=sales_headers)
    assert view_res.status_code == 200

    # Sales KHÔNG ĐƯỢC PHÉP tạo hoặc xóa kho hàng (Bị chặn 403 Forbidden)
    create_res = client.post("/api/v1/warehouses", json={
        "code": "KHO_SALES_INVALID",
        "name": "Kho Không Có Quyền Tạo",
    }, headers=sales_headers)
    assert create_res.status_code == 403

    del_res = client.delete("/api/v1/warehouses/1", headers=sales_headers)
    assert del_res.status_code == 403
