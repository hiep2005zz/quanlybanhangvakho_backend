# backend/tests/test_audit_logs.py
"""
Test Suite cho User Story SCRUM-29:
Ghi và xem nhật ký thao tác trên tồn kho, giá, hạn mức công nợ và hoá đơn.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.models.dealer import DEALERS_DB
from app.api.v1.endpoints.orders import ORDERS_DB

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_audit_logs_requires_admin():
    """Chỉ có role Quản trị hệ thống (Admin) mới có quyền truy cập /api/v1/audit-logs."""
    sales_token = get_token("sales")
    kho_token = get_token("kho")
    admin_token = get_token("admin")

    # Sales bị 403 Forbidden
    res_sales = client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {sales_token}"})
    assert res_sales.status_code == 403

    # Kho bị 403 Forbidden
    res_kho = client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {kho_token}"})
    assert res_kho.status_code == 403

    # Admin vào thành công 200 OK
    res_admin = client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "items" in data
    assert "total" in data


def test_inventory_adjust_creates_audit_log():
    """Khi thao tác điều chỉnh kho, hệ thống tự động ghi nhật ký vào audit_logs."""
    admin_token = get_token("admin")
    kho_token = get_token("kho")

    # Thủ kho thực hiện kiểm kê / điều chỉnh tồn kho
    adjust_resp = client.post(
        "/api/v1/inventory/adjust",
        headers={"Authorization": f"Bearer {kho_token}"},
        json={"product_id": 1, "adjustment": 5, "reason": "Kiểm kê định kỳ phát hiện dư"}
    )
    assert adjust_resp.status_code == 200

    # Admin tra cứu audit-logs
    logs_resp = client.get(
        "/api/v1/audit-logs?entity_type=Product&action_type=INVENTORY_ADJUST",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()["items"]
    assert len(logs) > 0
    latest = logs[0]
    assert latest["action_type"] == "INVENTORY_ADJUST"
    assert latest["entity_type"] == "Product"
    assert latest["entity_id"] == "SP001"
    assert "Kiểm kê định kỳ phát hiện dư" in latest["reason"]


def test_price_change_creates_audit_log():
    """Khi cập nhật giá bán hoặc giá vốn, hệ thống tự động ghi nhật ký PRICE_CHANGE."""
    admin_token = get_token("admin")

    price_resp = client.put(
        "/api/v1/products/2/price",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"sell_price": 395000.0, "cost_price": 165000.0, "reason": "Tăng giá theo bảng giá quý 4"}
    )
    assert price_resp.status_code == 200

    # Tra cứu lịch sử riêng của sản phẩm SP002
    entity_resp = client.get(
        "/api/v1/audit-logs/entity/Product/SP002",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert entity_resp.status_code == 200
    items = entity_resp.json()
    assert len(items) > 0
    assert items[0]["action_type"] == "PRICE_CHANGE"
    assert "Tăng giá theo bảng giá quý 4" in items[0]["reason"]


def test_debt_limit_change_creates_audit_log():
    """Khi thay đổi hạn mức công nợ khách hàng, hệ thống ghi DEBT_LIMIT_CHANGE."""
    admin_token = get_token("admin")

    from app.models.dealer import DEALERS_DB
    cur_limit = getattr(DEALERS_DB.get(1), "credit_limit", 50000000.0)
    new_limit = 120000000.0 if cur_limit != 120000000.0 else 85000000.0

    debt_resp = client.put(
        "/api/v1/orders/dealers/1/debt-limit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"credit_limit": new_limit, "reason": "Nâng hạn mức tín dụng khách hàng VIP"}
    )
    assert debt_resp.status_code == 200

    # Tra cứu lịch sử riêng của CustomerDebt DL001
    entity_resp = client.get(
        "/api/v1/audit-logs/entity/CustomerDebt/DL001",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert entity_resp.status_code == 200
    items = entity_resp.json()
    assert len(items) > 0
    assert items[0]["action_type"] == "DEBT_LIMIT_CHANGE"
    assert items[0]["entity_id"] == "DL001"


def test_invoice_edit_creates_audit_log():
    """Khi sửa đổi hoặc hủy hóa đơn, hệ thống ghi INVOICE_EDIT."""
    admin_token = get_token("admin")

    create_resp = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 1, "price": 199000, "unit": "Cái"}],
        },
    )
    assert create_resp.status_code == 201
    order_code = create_resp.json()["order_code"]

    edit_resp = client.put(
        f"/api/v1/orders/{order_code}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "CANCELLED", "note": "Hủy theo yêu cầu khách hàng", "reason": "Khách hủy hợp đồng"}
    )
    assert edit_resp.status_code == 200

    entity_resp = client.get(
        f"/api/v1/audit-logs/entity/Invoice/{order_code}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert entity_resp.status_code == 200
    items = entity_resp.json()
    assert len(items) > 0
    assert items[0]["action_type"] == "INVOICE_EDIT"
    assert items[0]["entity_id"] == order_code


def test_no_op_change_does_not_create_audit_log():
    """Khi dữ liệu không đổi (old_values == new_values), hệ thống bỏ qua và không ghi log."""
    admin_token = get_token("admin")

    # Lấy số lượng log trước khi thao tác no-op
    init_resp = client.get(
        "/api/v1/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    init_total = init_resp.json()["total"]

    # 1. Update giá bán giữ nguyên giá cũ
    client.put(
        "/api/v1/products/2/price",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"sell_price": next(p for p in RAW_PRODUCTS if p["id"] == 2)["sell_price"], "cost_price": next(p for p in RAW_PRODUCTS if p["id"] == 2)["cost_price"], "reason": "Không thay đổi giá"}
    )

    # 2. Update hạn mức công nợ bằng chính hạn mức hiện tại
    from app.models.dealer import DEALERS_DB
    cur_limit = getattr(DEALERS_DB.get(1), "credit_limit", 100000000.0)
    client.put(
        "/api/v1/orders/dealers/1/debt-limit",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"credit_limit": cur_limit, "reason": "Giữ nguyên hạn mức"}
    )

    # 3. Update trạng thái hóa đơn đúng bằng trạng thái hiện tại (CANCELLED)
    order = next(
        (order for order in ORDERS_DB.values() if order["order_code"] == "ORD00001"),
        {"status": "CANCELLED", "note": None},
    )
    client.put(
        "/api/v1/orders/ORD00001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": order["status"], "note": order.get("note"), "reason": "Giữ nguyên trạng thái"}
    )

    # Kiểm tra tổng số log không tăng lên
    after_resp = client.get(
        "/api/v1/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    after_total = after_resp.json()["total"]
    assert after_total == init_total, f"Expected total logs {init_total}, got {after_total}"
def test_sales_manager_can_view_product_price_history():
    """
    Tiêu chí nghiệp vụ:
    Là Quản lý kinh doanh (Sales Manager), có quyền xem lịch sử thay đổi giá của một sản phẩm,
    hiển thị đầy đủ: giá cũ, giá mới, người sửa, thời điểm áp dụng.
    """
    sales_mgr_token = get_token("sales_manager")

    # 1. Quản lý kinh doanh thực hiện đổi giá sản phẩm SP001 (product_id = 1)
    new_sell_price = 185000.0
    price_change_resp = client.put(
        "/api/v1/products/1/price",
        headers={"Authorization": f"Bearer {sales_mgr_token}"},
        json={
            "sell_price": new_sell_price,
            "reason": "Điều chỉnh giá theo chính sách chiết khấu đại lý tháng 10"
        }
    )
    assert price_change_resp.status_code == 200, f"Update price failed: {price_change_resp.text}"

    # 2. Quản lý kinh doanh tra cứu lịch sử của sản phẩm SP001
    history_resp = client.get(
        "/api/v1/audit-logs/entity/Product/SP001",
        headers={"Authorization": f"Bearer {sales_mgr_token}"}
    )
    assert history_resp.status_code == 200, f"Get history failed: {history_resp.text}"
    logs = history_resp.json()
    assert len(logs) > 0, "Dữ liệu lịch sử sản phẩm không được rỗng"

    # 3. Kiểm tra bản ghi thay đổi giá mới nhất chứa đầy đủ tiêu chí
    latest_price_log = next((l for l in logs if l["action_type"] == "PRICE_CHANGE"), None)
    assert latest_price_log is not None, "Phải có bản ghi action_type là PRICE_CHANGE"

    # Tiêu chí: Người sửa
    assert latest_price_log["user_name"] is not None
    assert len(latest_price_log["user_name"]) > 0

    # Tiêu chí: Thời điểm áp dụng
    assert latest_price_log["created_at"] is not None

    # Tiêu chí: Giá cũ & Giá mới
    import json
    old_vals = json.loads(latest_price_log["old_values"])
    new_vals = json.loads(latest_price_log["new_values"])
    assert "sell_price" in old_vals, "Phải có giá cũ (old sell_price)"
    assert "sell_price" in new_vals, "Phải có giá mới (new sell_price)"
    assert new_vals["sell_price"] == new_sell_price

    # Tiêu chí: Lý do giải thích với đại lý
    assert "chính sách chiết khấu đại lý tháng 10" in latest_price_log["reason"]


def test_audit_logs_immutable_cannot_delete():
    """
    Tiêu chí bảo mật kiểm toán:
    Lịch sử không sửa và không xoá được.
    Mọi yêu cầu xóa (DELETE) 1 bản ghi hoặc toàn bộ nhật ký đều bị từ chối 405 Method Not Allowed.
    """
    admin_token = get_token("admin")
    sales_mgr_token = get_token("sales_manager")

    # 1. Thử xóa 1 bản ghi bằng Admin -> Bị từ chối 405
    del_single_admin = client.delete(
        "/api/v1/audit-logs/1",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert del_single_admin.status_code == 405
    assert "bất biến" in del_single_admin.json()["detail"]

    # 2. Thử xóa toàn bộ bằng Admin -> Bị từ chối 405
    del_all_admin = client.delete(
        "/api/v1/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert del_all_admin.status_code == 405
    assert "bất biến" in del_all_admin.json()["detail"]

    # 3. Thử xóa bằng Quản lý kinh doanh -> Bị từ chối 405
    del_single_sm = client.delete(
        "/api/v1/audit-logs/1",
        headers={"Authorization": f"Bearer {sales_mgr_token}"}
    )
    assert del_single_sm.status_code == 405
