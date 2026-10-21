# backend/tests/test_warehouse_wave_picking.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def get_token(username: str = "kho", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


@pytest.fixture(scope="module")
def admin_token():
    return get_token("admin", "123")


@pytest.fixture(scope="module")
def warehouse_token():
    return get_token("kho", "123")


@pytest.fixture(scope="module")
def non_warehouse_token():
    # Sử dụng tài khoản mua hàng / đại lý không có quyền ghi kho
    return get_token("muahang", "123")



def test_eligible_picking_orders_warehouse_access(warehouse_token):
    """Tiêu chí 2: Nhân viên kho xem được danh sách đơn hàng đã duyệt để gộp soạn."""
    res = client.get(
        "/api/v1/warehouses/picking/eligible-orders",
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    for ord_item in data:
        assert ord_item["status"] in ("CONFIRMED", "APPROVED", "PICKING")
        assert "order_code" in ord_item
        assert "total_items" in ord_item


def test_batch_picking_preview_multi_orders_consolidation_and_pick_path(warehouse_token):
    """
    Tiêu chí 1 & 2:
    - Gộp nhiều đơn thành một lượt soạn (Wave / Batch Picking)
    - Phiếu liệt kê dòng hàng kèm vị trí lưu (Khu, Dãy, Kệ, Ô)
    - Sắp xếp theo đường đi trong kho (Pick Path routing: Zone -> Aisle -> Rack -> Bin)
    """
    res_orders = client.get(
        "/api/v1/warehouses/picking/eligible-orders",
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    orders = res_orders.json()
    assert len(orders) >= 1, "Cần có ít nhất 1 đơn hợp lệ trong DB để test"
    
    selected_codes = [o["order_code"] for o in orders[:2]]

    payload = {"order_codes": selected_codes}
    res = client.post(
        "/api/v1/warehouses/picking/batch-preview",
        json=payload,
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["batch_code"].startswith("PICK-")
    assert len(data["selected_order_codes"]) == len(selected_codes)
    assert len(data["route_items"]) > 0

    route_items = data["route_items"]
    # Kiểm tra thứ tự tuần tự của bước nhặt (Step 1, Step 2, ...)
    for idx, item in enumerate(route_items, start=1):
        assert item["step_number"] == idx
        assert "product_code" in item
        assert "total_ordered_quantity" in item
        assert item["total_ordered_quantity"] > 0
        assert "location_code" in item
        assert "order_breakdown" in item
        breakdown_sum = sum(b["quantity"] for b in item["order_breakdown"])
        assert breakdown_sum == item["total_ordered_quantity"]


def test_batch_picking_complete_success_with_actual_qty(warehouse_token):
    """
    Tiêu chí 3: Đánh dấu đã soạn từng dòng, ghi số lượng thực soạn.
    Hoàn tất thành công khi soạn đủ số lượng.
    """
    res_orders = client.get(
        "/api/v1/warehouses/picking/eligible-orders",
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    orders = res_orders.json()
    selected_code = orders[0]["order_code"]

    res_prev = client.post(
        "/api/v1/warehouses/picking/batch-preview",
        json={"order_codes": [selected_code]},
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    preview = res_prev.json()
    batch_code = preview["batch_code"]

    items_to_pick = []
    for it in preview["route_items"]:
        items_to_pick.append({
            "product_id": it["product_id"],
            "product_code": it["product_code"],
            "ordered_quantity": it["total_ordered_quantity"],
            "picked_quantity": it["total_ordered_quantity"],  # Soạn đủ 100%
            "location_code": it["location_code"],
        })

    complete_payload = {
        "batch_code": batch_code,
        "order_codes": [selected_code],
        "items": items_to_pick,
        "picker_name": "Nguyễn Văn Kho",
        "notes": "Soạn hàng đầy đủ và đóng gói theo chuẩn"
    }

    res_complete = client.post(
        "/api/v1/warehouses/picking/batch-complete",
        json=complete_payload,
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    assert res_complete.status_code == 200
    resp_data = res_complete.json()
    assert resp_data["success"] is True
    assert resp_data["total_orders_updated"] >= 1
    assert resp_data["shortage_items_count"] == 0


def test_batch_picking_shortage_enforces_mandatory_reason(warehouse_token):
    """
    Tiêu chí 4: Soạn thiếu BẮT BUỘC nhập lý do.
    Nếu picked_quantity < ordered_quantity mà không có lý do -> Chặn 400.
    Nếu có lý do hợp lệ -> Cho phép hoàn tất và ghi nhận thiếu.
    """
    res_orders = client.get(
        "/api/v1/warehouses/picking/eligible-orders",
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    orders = res_orders.json()
    selected_code = orders[0]["order_code"]

    res_prev = client.post(
        "/api/v1/warehouses/picking/batch-preview",
        json={"order_codes": [selected_code]},
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    preview = res_prev.json()
    batch_code = preview["batch_code"]

    target_item = preview["route_items"][0]
    ordered_q = target_item["total_ordered_quantity"]
    picked_q = max(0, ordered_q - 1)  # Cố tình soạn thiếu 1 món

    # TRƯỜNG HỢP 1: Soạn thiếu nhưng KHÔNG nhập lý do (hoặc rỗng) -> BỊ CHẶN 400
    bad_items = [{
        "product_id": target_item["product_id"],
        "product_code": target_item["product_code"],
        "ordered_quantity": ordered_q,
        "picked_quantity": picked_q,
        "shortage_reason": "   ",  # Rỗng
        "location_code": target_item["location_code"],
    }]

    res_blocked = client.post(
        "/api/v1/warehouses/picking/batch-complete",
        json={
            "batch_code": batch_code,
            "order_codes": [selected_code],
            "items": bad_items,
            "picker_name": "Nguyễn Văn Kho"
        },
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    assert res_blocked.status_code == 400
    assert "bắt buộc phải nhập lý do thiếu hàng" in res_blocked.json()["detail"].lower()

    # TRƯỜNG HỢP 2: Soạn thiếu và CÓ nhập lý do rõ ràng -> THÀNH CÔNG
    valid_items = [{
        "product_id": target_item["product_id"],
        "product_code": target_item["product_code"],
        "ordered_quantity": ordered_q,
        "picked_quantity": picked_q,
        "shortage_reason": "Hàng tại ô kệ bị rách bao bì, chờ nhập thêm từ kho tổng",
        "location_code": target_item["location_code"],
    }]

    res_success = client.post(
        "/api/v1/warehouses/picking/batch-complete",
        json={
            "batch_code": batch_code,
            "order_codes": [selected_code],
            "items": valid_items,
            "picker_name": "Nguyễn Văn Kho"
        },
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    assert res_success.status_code == 200
    res_json = res_success.json()
    assert res_json["success"] is True
    assert res_json["shortage_items_count"] == 1


def test_batch_picking_rbac_unauthorized_user(non_warehouse_token):
    """Bảo mật RBAC: Khách hàng / Đại lý không được phép thực hiện chức năng kho."""
    res = client.post(
        "/api/v1/warehouses/picking/batch-complete",
        json={
            "batch_code": "PICK-TEST",
            "order_codes": ["ORD00003"],
            "items": [{
                "product_id": 1,
                "product_code": "SP001",
                "ordered_quantity": 1,
                "picked_quantity": 1
            }]
        },
        headers={"Authorization": f"Bearer {non_warehouse_token}"}
    )
    assert res.status_code == 403


def test_batch_picking_rejects_over_picking(warehouse_token):
    """Ràng buộc nghiệp vụ kho: Không được phép soạn vượt quá số lượng đơn yêu cầu."""
    res_orders = client.get(
        "/api/v1/warehouses/picking/eligible-orders",
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    orders = res_orders.json()
    selected_code = orders[0]["order_code"]

    res_prev = client.post(
        "/api/v1/warehouses/picking/batch-preview",
        json={"order_codes": [selected_code]},
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    preview = res_prev.json()
    batch_code = preview["batch_code"]
    target_item = preview["route_items"][0]

    # Soạn vượt quá số lượng đơn (vd: ordered = 1, picked = 2)
    res_over = client.post(
        "/api/v1/warehouses/picking/batch-complete",
        json={
            "batch_code": batch_code,
            "order_codes": [selected_code],
            "items": [{
                "product_id": target_item["product_id"],
                "product_code": target_item["product_code"],
                "ordered_quantity": target_item["total_ordered_quantity"],
                "picked_quantity": target_item["total_ordered_quantity"] + 1,
                "location_code": target_item["location_code"],
            }],
            "picker_name": "Nguyễn Văn Kho"
        },
        headers={"Authorization": f"Bearer {warehouse_token}"}
    )
    assert res_over.status_code == 400
    assert "không được vượt quá số lượng yêu cầu" in res_over.json()["detail"]


