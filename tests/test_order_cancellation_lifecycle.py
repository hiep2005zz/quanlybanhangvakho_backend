# backend/tests/test_order_cancellation_lifecycle.py
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import OrderEntity, WarehouseStockEntity

client = TestClient(app)


def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


@pytest.fixture(scope="module")
def tokens():
    return {
        "admin": get_token("admin"),
        "sales_manager": get_token("sales_manager"),
        "sales": get_token("sales"),
        "kho": get_token("kho"),
        "warehouse_mgr": get_token("warehouse_mgr"),
        "ketoan": get_token("ketoan"),
        "muahang": get_token("muahang"),
    }


def create_test_order(token: str, dealer_id: int = 1, qty: int = 2) -> dict:
    res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": dealer_id,
            "items": [
                {"product_id": 1, "quantity": qty, "price": 200000}
            ],
            "delivery_point": "Kho Tổng Hà Nội",
            "desired_delivery_date": "2026-10-25"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code in (200, 201), f"Failed to create test order: {res.text}"
    return res.json()


def test_muahang_strictly_blocked_from_orders(tokens):
    """
    Nhóm Không Liên Quan (No Access): muahang
    Chặn và ẩn hoàn toàn toàn bộ UI & Backend API liên quan đến đơn hàng bán (403 Forbidden).
    """
    token = tokens["muahang"]
    # 1. Blocked from list
    res_list = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    assert res_list.status_code == 403, f"Expected 403 for muahang GET /orders, got {res_list.status_code}"

    # 2. Blocked from detail
    res_detail = client.get("/api/v1/orders/ORD00001", headers={"Authorization": f"Bearer {token}"})
    assert res_detail.status_code == 403, f"Expected 403 for muahang GET /orders/detail, got {res_detail.status_code}"

    # 3. Blocked from cancel
    res_cancel = client.post(
        "/api/v1/orders/ORD00001/cancel",
        json={"reason": "Mua hàng cố tình hủy"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_cancel.status_code == 403, f"Expected 403 for muahang cancel, got {res_cancel.status_code}"


def test_read_only_roles_can_view_orders(tokens):
    """
    Nhóm Chỉ Xem (Read-Only): kho, warehouse_mgr, ketoan
    Được hiển thị danh sách và chi tiết đơn hàng để theo dõi tiến độ.
    """
    for role_name in ["kho", "warehouse_mgr", "ketoan"]:
        token = tokens[role_name]
        res = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, f"Role {role_name} must be allowed to read orders list (got {res.status_code})"
        assert isinstance(res.json(), list)


def test_read_only_roles_blocked_from_cancelling(tokens):
    """
    Nhóm Chỉ Xem (Read-Only): kho, warehouse_mgr, ketoan
    Backend bắt buộc chặn API hủy đơn (trả về 403 Forbidden).
    """
    # Tạo đơn hàng bằng sales
    order = create_test_order(tokens["sales"])
    order_code = order["order_code"]

    for role_name in ["kho", "warehouse_mgr", "ketoan"]:
        token = tokens[role_name]
        res = client.post(
            f"/api/v1/orders/{order_code}/cancel",
            json={"reason": f"{role_name} cố tình hủy đơn"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 403, f"Role {role_name} must be blocked with 403 on cancel, got {res.status_code}"


def test_cancel_requires_reason(tokens):
    """
    Logic Hủy đơn 1: Bắt buộc người dùng phải nhập lý do hủy.
    """
    order = create_test_order(tokens["sales"])
    order_code = order["order_code"]

    # Thử gửi lý do rỗng
    res_empty = client.post(
        f"/api/v1/orders/{order_code}/cancel",
        json={"reason": "   "},
        headers={"Authorization": f"Bearer {tokens['sales']}"}
    )
    assert res_empty.status_code == 400, f"Expected 400 Bad Request for empty reason, got {res_empty.status_code}"
    assert "lý do" in res_empty.text.lower()


def test_cannot_cancel_exported_or_later(tokens):
    """
    Logic Hủy đơn 2: Đơn đã xuất kho (>= 'Đã xuất' / EXPORTED) thì không hủy được.
    Backend có logic guard ném lỗi HTTP 400.
    """
    order = create_test_order(tokens["sales"])
    order_code = order["order_code"]

    # Giả lập đơn hàng đã chuyển sang trạng thái EXPORTED
    db = SessionLocal()
    try:
        db_order = db.query(OrderEntity).filter(OrderEntity.order_code == order_code).first()
        assert db_order is not None
        db_order.status = "EXPORTED"
        db.commit()
    finally:
        db.close()

    # Thử hủy đơn hàng đã xuất kho
    res_cancel = client.post(
        f"/api/v1/orders/{order_code}/cancel",
        json={"reason": "Khách đổi ý muốn hủy đơn"},
        headers={"Authorization": f"Bearer {tokens['sales']}"}
    )
    assert res_cancel.status_code == 400, f"Expected 400 when cancelling EXPORTED order, got {res_cancel.status_code}"
    assert "xuất kho" in res_cancel.text.lower() or "trả hàng" in res_cancel.text.lower()


def test_successful_cancellation_releases_reserved_stock(tokens):
    """
    Thao tác hủy hợp lệ bởi nhóm Toàn Quyền (sales/sales_manager/admin):
    - Đổi trạng thái thành CANCELLED
    - Lưu lý do hủy, người hủy, thời gian hủy
    - Tự động nhả (giảm) số lượng tồn đang giữ chỗ (reserved_stock) trong Database Transaction
    """
    # 1. Kiểm tra reserved_stock trước khi tạo đơn
    db = SessionLocal()
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    init_reserved = stock_rec.reserved_stock if stock_rec else 0
    db.close()

    # 2. Tạo đơn đặt 3 cái
    order = create_test_order(tokens["sales"], qty=3)
    order_code = order["order_code"]

    # Kiểm tra reserved_stock tăng lên
    db = SessionLocal()
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    assert stock_rec is not None
    assert stock_rec.reserved_stock >= init_reserved + 3
    reserved_after_order = stock_rec.reserved_stock
    db.close()

    # 3. Hủy đơn hàng với lý do hợp lệ
    cancel_reason = "Khách hàng đổi địa chỉ nhận hàng và muốn đặt lại"
    res_cancel = client.post(
        f"/api/v1/orders/{order_code}/cancel",
        json={"reason": cancel_reason},
        headers={"Authorization": f"Bearer {tokens['sales']}"}
    )
    assert res_cancel.status_code == 200, f"Cancel failed: {res_cancel.text}"

    # 4. Kiểm tra reserved_stock đã được tự động nhả (giảm)
    db = SessionLocal()
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    assert stock_rec.reserved_stock == reserved_after_order - 3

    # Kiểm tra bản ghi đơn hàng trong cơ sở dữ liệu
    db_order = db.query(OrderEntity).filter(OrderEntity.order_code == order_code).first()
    assert db_order.status == "CANCELLED"
    assert db_order.cancel_reason == cancel_reason
    assert db_order.cancelled_by == "sales"
    assert db_order.cancelled_at is not None
    db.close()

    # 5. Gọi lại API xem chi tiết để đảm bảo thông tin hủy được trả về đầy đủ
    res_detail = client.get(
        f"/api/v1/orders/{order_code}",
        headers={"Authorization": f"Bearer {tokens['sales']}"}
    )
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert detail_data["status"] == "CANCELLED"
    assert detail_data["cancel_reason"] == cancel_reason
    assert detail_data["cancelled_by"] == "sales"
