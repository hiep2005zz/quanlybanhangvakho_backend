# backend/tests/test_unit_conversion.py
"""
Test Suite cho User Story: Đơn vị tính quy đổi (Unit Conversion)
Phạm vi 3 tiêu chí bắt buộc:
1. Khai báo đa đơn vị tính:
   - Mỗi SKU có 1 Đơn vị tính cơ sở (Base unit, ví dụ: Lon, Cái) và có thể khai báo thêm nhiều Đơn vị quy đổi
     kèm hệ số quy đổi về đơn vị cơ sở (ví dụ: Lốc = 6, Thùng = 24).
   - Hệ số quy đổi bắt buộc > 0. Hệ số <= 0 bị từ chối với lỗi 400.
2. Quy về đơn vị cơ sở khi ghi sổ:
   - Khi tạo Đơn hàng hoặc Phiếu nhập/xuất kho: Cho phép chọn đơn vị tính.
   - Nhập số lượng theo đơn vị đã chọn, hệ thống tự động tính ra base_quantity = quantity * conversion_rate
     để cập nhật vào tồn kho thực tế.
3. Không làm sai lệch giao dịch lịch sử:
   - Khi lưu đơn hàng hoặc phiếu kho, snapshot lại unit_name, conversion_rate và base_quantity tương ứng.
   - Khi người dùng sửa hệ số quy đổi của SKU trong tương lai, tất cả đơn hàng và phiếu kho cũ đã tạo trước đó
     giữ nguyên số liệu, tuyệt đối không bị tính lại.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_criterion_1_unit_declaration_and_validation():
    """
    Tiêu chí 1:
    - Khai báo đa đơn vị tính: ĐVT cơ sở và danh sách ĐVT quy đổi với hệ số quy đổi.
    - Hệ số quy đổi bắt buộc > 0: Nếu <= 0 phải trả về HTTP 400 Bad Request.
    """
    kho_mgr_token = get_token("warehouse_mgr")
    headers = {"Authorization": f"Bearer {kho_mgr_token}"}
    kho_staff_token = get_token("kho")
    headers_staff = {"Authorization": f"Bearer {kho_staff_token}"}

    # Lấy danh sách sản phẩm, chọn SP001
    res = client.get("/api/v1/products", headers=headers)
    assert res.status_code == 200
    products = res.json()["items"]
    sp1 = next(p for p in products if p["code"] == "SP001")
    product_id = sp1["id"]

    # 1. Cấu hình hợp lệ: Base unit là "Lon", các đơn vị quy đổi: Lốc (6), Thùng (24)
    payload_valid = {
        "base_unit": "Lon",
        "units": [
            {"unit_name": "Lốc", "conversion_rate": 6.0},
            {"unit_name": "Thùng", "conversion_rate": 24.0}
        ]
    }
    update_res = client.put(f"/api/v1/products/{product_id}/units", json=payload_valid, headers=headers)
    assert update_res.status_code == 200
    res_data = update_res.json()
    product_data = res_data["product"]
    assert product_data["base_unit"] == "Lon"
    assert len(product_data["units"]) == 2
    assert product_data["units"][0]["unit_name"] == "Lốc"
    assert product_data["units"][0]["conversion_rate"] == 6.0

    # 2. Thử nghiệm hệ số quy đổi <= 0 (bằng 0 hoặc âm) -> Bắt buộc bị chặn (Pydantic Field gt=0 hoặc endpoint validation -> 400 hoặc 422)
    payload_invalid_zero = {
        "base_unit": "Lon",
        "units": [
            {"unit_name": "LỗiZero", "conversion_rate": 0}
        ]
    }
    res_zero = client.put(f"/api/v1/products/{product_id}/units", json=payload_invalid_zero, headers=headers)
    assert res_zero.status_code in [400, 422]

    payload_invalid_negative = {
        "base_unit": "Lon",
        "units": [
            {"unit_name": "LỗiÂm", "conversion_rate": -5.0}
        ]
    }
    res_neg = client.put(f"/api/v1/products/{product_id}/units", json=payload_invalid_negative, headers=headers)
    assert res_neg.status_code in [400, 422]

    # 3. Phân quyền: Thủ kho (kho) không có quyền Cấu hình ĐVT quy đổi -> Bị từ chối HTTP 403 Forbidden
    res_forbidden = client.put(f"/api/v1/products/{product_id}/units", json=payload_valid, headers=headers_staff)
    assert res_forbidden.status_code == 403


def test_criterion_2_stock_conversion_on_receipt_and_issue():
    """
    Tiêu chí 2: Quy về đơn vị cơ sở khi ghi sổ.
    - Nhập kho: Nhập 2 Thùng (hệ số 24) -> tồn kho thực tế tăng 2 * 24 = 48 Lon.
    - Xuất kho: Xuất 1 Lốc (hệ số 6) -> tồn kho thực tế giảm 6 Lon.
    - Đơn hàng (Bán hàng): Tạo đơn hàng 1 Thùng (hệ số 24) -> tồn kho giảm 24 Lon.
    """
    kho_token = get_token("kho")
    headers = {"Authorization": f"Bearer {kho_token}"}

    # Lấy thông tin SP001
    res = client.get("/api/v1/products", headers=headers)
    assert res.status_code == 200
    sp1 = next(p for p in res.json()["items"] if p["code"] == "SP001")
    product_id = sp1["id"]
    initial_stock = sp1["stock"]

    # 1. Nhập kho: 2 Thùng (conversion_rate = 24.0) -> base_quantity = 48
    receipt_payload = {
        "product_id": product_id,
        "quantity": 2,
        "unit_name": "Thùng",
        "conversion_rate": 24.0,
        "supplier": "Nhà Cung Cấp Sabeco",
        "note": "Nhập kho theo thùng từ nhà cung cấp"
    }
    receipt_res = client.post("/api/v1/inventory/receipt", json=receipt_payload, headers=headers)
    assert receipt_res.status_code == 200
    tx_receipt = receipt_res.json()["transaction"]
    assert tx_receipt["unit_name"] == "Thùng"
    assert tx_receipt["conversion_rate"] == 24.0
    assert abs(tx_receipt["base_quantity"]) == 48
    assert tx_receipt["new_stock"] == initial_stock + 48

    # 2. Xuất kho: 1 Lốc (conversion_rate = 6.0) -> base_quantity = 6 (trừ kho 6 lon)
    issue_payload = {
        "product_id": product_id,
        "quantity": 1,
        "unit_name": "Lốc",
        "conversion_rate": 6.0,
        "destination": "Đại lý Hà Nội",
        "note": "Xuất mẫu thử nghiệm cho khách"
    }
    issue_res = client.post("/api/v1/inventory/issue", json=issue_payload, headers=headers)
    assert issue_res.status_code == 200
    tx_issue = issue_res.json()["transaction"]
    assert tx_issue["unit_name"] == "Lốc"
    assert tx_issue["conversion_rate"] == 6.0
    assert abs(tx_issue["base_quantity"]) == 6
    assert tx_issue["new_stock"] == initial_stock + 48 - 6

    # 3. Tạo Đơn hàng (Bán hàng) chọn ĐVT quy đổi: 1 Thùng (hệ số 24)
    sales_token = get_token("sales")
    sales_headers = {"Authorization": f"Bearer {sales_token}"}
    order_payload = {
        "dealer_id": 1,
        "note": "Đơn xuất sỉ theo thùng",
        "items": [
            {
                "product_id": product_id,
                "quantity": 1,
                "unit_name": "Thùng",
                "conversion_rate": 24.0,
                "price": 240000
            }
        ]
    }
    order_res = client.post("/api/v1/orders", json=order_payload, headers=sales_headers)
    assert order_res.status_code in [200, 201]
    order_data = order_res.json()
    item_saved = order_data["items"][0]
    assert item_saved["unit_name"] == "Thùng"
    assert item_saved["conversion_rate"] == 24.0
    assert item_saved["base_quantity"] == 24.0

    # Kiểm tra tồn kho sau khi bán 1 Thùng (24 lon)
    p_check = client.get("/api/v1/products", headers=headers).json()["items"]
    sp1_after = next(p for p in p_check if p["code"] == "SP001")
    assert sp1_after["stock"] == initial_stock + 48 - 6 - 24


def test_criterion_3_historical_snapshot_integrity():
    """
    Tiêu chí 3: Không làm sai lệch giao dịch lịch sử.
    - Tạo phiếu nhập kho và đơn hàng với ĐVT "Thùng" hệ số 24.
    - Sau đó, người dùng vào cập nhật lại hệ số của "Thùng" thành 30 (hoặc đổi quy cách đóng gói).
    - Các giao dịch lịch sử và đơn hàng cũ đã ghi sổ phải giữ nguyên số liệu snapshot
      (unit_name="Thùng", conversion_rate=24.0, base_quantity đúng lúc tạo), tuyệt đối không bị tính lại.
    """
    kho_mgr_token = get_token("warehouse_mgr")
    headers_mgr = {"Authorization": f"Bearer {kho_mgr_token}"}
    kho_token = get_token("kho")
    headers = {"Authorization": f"Bearer {kho_token}"}

    # SP002 cấu hình ban đầu
    res = client.get("/api/v1/products", headers=headers)
    sp2 = next(p for p in res.json()["items"] if p["code"] == "SP002")
    product_id = sp2["id"]

    # Đảm bảo SP002 có Thùng = 24
    setup_payload = {
        "base_unit": "Lon",
        "units": [
            {"unit_name": "Thùng", "conversion_rate": 24.0}
        ]
    }
    client.put(f"/api/v1/products/{product_id}/units", json=setup_payload, headers=headers_mgr)

    # 1. Tạo phiếu nhập 3 Thùng (hệ số 24 => base_quantity = 72)
    receipt_res = client.post("/api/v1/inventory/receipt", json={
        "product_id": product_id,
        "quantity": 3,
        "unit_name": "Thùng",
        "conversion_rate": 24.0,
        "supplier": "Công ty TNHH Bia Nước Giải Khát",
        "note": "Nhập kho lịch sử mốc 1"
    }, headers=headers)
    assert receipt_res.status_code == 200
    tx_id = receipt_res.json()["transaction"]["id"]

    # 2. Tạo đơn hàng 1 Thùng (hệ số 24 => base_quantity = 24)
    sales_token = get_token("sales")
    order_res = client.post("/api/v1/orders", json={
        "dealer_id": 1,
        "note": "Đơn hàng lịch sử mốc 1",
        "items": [
            {
                "product_id": product_id,
                "quantity": 1,
                "unit_name": "Thùng",
                "conversion_rate": 24.0,
                "price": 300000
            }
        ]
    }, headers={"Authorization": f"Bearer {sales_token}"})
    assert order_res.status_code in [200, 201]
    order_id = order_res.json()["id"]

    # 3. Thay đổi hệ số quy đổi của SP002 trong tương lai: Thùng = 30
    future_update_payload = {
        "base_unit": "Lon",
        "units": [
            {"unit_name": "Thùng", "conversion_rate": 30.0}
        ]
    }
    change_res = client.put(f"/api/v1/products/{product_id}/units", json=future_update_payload, headers=headers_mgr)
    assert change_res.status_code == 200
    assert change_res.json()["product"]["units"][0]["conversion_rate"] == 30.0

    # 4. Kiểm tra lịch sử giao dịch kho (Inventory Transactions) cũ
    tx_list_res = client.get("/api/v1/inventory/transactions", headers=headers)
    assert tx_list_res.status_code == 200
    tx_items = tx_list_res.json()
    old_tx = next((t for t in tx_items if t["id"] == tx_id), None)
    assert old_tx is not None
    # Snapshot cũ phải giữ nguyên vẹn 100%: Thùng, 24.0, 72.0 (không bị đổi thành 30 hay 90)
    assert old_tx["unit_name"] == "Thùng"
    assert old_tx["conversion_rate"] == 24.0
    assert old_tx["base_quantity"] == 72

    # 5. Kiểm tra đơn hàng cũ đã tạo
    orders_res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {sales_token}"})
    assert orders_res.status_code == 200
    orders = orders_res.json()
    old_order = next((o for o in orders if o["id"] == order_id), None)
    assert old_order is not None
    old_item = old_order["items"][0]
    # Snapshot đơn hàng cũ giữ nguyên: Thùng, 24.0, 24.0
    assert old_item["unit_name"] == "Thùng"
    assert old_item["conversion_rate"] == 24.0
    assert old_item["base_quantity"] == 24.0
