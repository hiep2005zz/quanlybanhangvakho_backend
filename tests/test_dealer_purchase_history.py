from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import OrderEntity, DealerEntity
from app.models.dealer import DEALERS_DB
from app.api.v1.endpoints import orders as orders_endpoint

client = TestClient(app)
created_order_codes: set[str] = set()


@pytest.fixture(autouse=True)
def cleanup_history_test_orders():
    original_assignments = {
        dealer_id: dealer.assigned_sale_id
        for dealer_id, dealer in DEALERS_DB.items()
    }
    yield
    try:
        if created_order_codes:
            session = SessionLocal()
            try:
                session.query(OrderEntity).filter(
                    OrderEntity.order_code.in_(created_order_codes)
                ).delete(synchronize_session=False)
                session.commit()
            finally:
                session.close()
            for order_id, order in list(orders_endpoint.ORDERS_DB.items()):
                if order.get("order_code") in created_order_codes:
                    del orders_endpoint.ORDERS_DB[order_id]
            created_order_codes.clear()
    finally:
        for dealer_id, assigned_sale_id in original_assignments.items():
            if dealer_id in DEALERS_DB:
                DEALERS_DB[dealer_id].assigned_sale_id = assigned_sale_id


def get_token(username: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_dealer_purchase_history_assigned_sales():
    """
    SCRUM-52 / SCRUM-57:
    - Hiển thị các mặt hàng đại lý đã mua trong 3 tháng gần nhất kèm số lượng bình quân
    - Thêm nhanh cả nhóm hàng đã mua lần trước vào đơn mới
    - Chỉ hiện với đại lý mà nhân viên được phân công
    """
    sales_token = get_token("sales")
    headers = {"Authorization": f"Bearer {sales_token}"}

    # Đại lý 1 được phân công cho sales (id=3)
    DEALERS_DB[1].assigned_sale_id = 3
    db = SessionLocal()
    try:
        d1 = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if d1:
            d1.assigned_sale_id = 3
            db.commit()
    finally:
        db.close()

    # Tạo 2 đơn hàng cho Đại lý 1 với các sản phẩm khác nhau
    # Đơn 1: Mua SP 1 (số lượng 10), SP 2 (số lượng 4)
    res_ord1 = client.post(
        "/api/v1/orders/sales-entry",
        headers=headers,
        json={
            "dealer_id": 1,
            "delivery_point": "120 Cầu Giấy, Hà Nội",
            "desired_delivery_date": (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d"),
            "items": [
                {"product_id": 1, "quantity": 10, "price": 199000, "unit": "Cái"},
                {"product_id": 2, "quantity": 4, "price": 250000, "unit": "Chiếc"},
            ],
        },
    )
    assert res_ord1.status_code == 201, res_ord1.text
    ord1_code = res_ord1.json()["order_code"]
    created_order_codes.add(ord1_code)

    # Đơn 2: Mua SP 1 (số lượng 20), SP 3 (số lượng 6) (đơn mới nhất)
    res_ord2 = client.post(
        "/api/v1/orders/sales-entry",
        headers=headers,
        json={
            "dealer_id": 1,
            "delivery_point": "120 Cầu Giấy, Hà Nội",
            "desired_delivery_date": (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d"),
            "items": [
                {"product_id": 1, "quantity": 20, "price": 199000, "unit": "Cái"},
                {"product_id": 3, "quantity": 6, "price": 320000, "unit": "Bộ"},
            ],
        },
    )
    assert res_ord2.status_code == 201, res_ord2.text
    ord2_code = res_ord2.json()["order_code"]
    created_order_codes.add(ord2_code)

    # 1. Gọi API lấy lịch sử mua hàng của đại lý 1
    res_hist = client.get("/api/v1/orders/dealers/1/purchase-history", headers=headers)
    assert res_hist.status_code == 200, res_hist.text
    history_data = res_hist.json()

    assert history_data["dealer_id"] == 1
    assert history_data["has_history"] is True
    assert history_data["period"] == "3 tháng gần nhất"
    assert len(history_data["items"]) >= 3

    # Kiểm tra tính toán số lượng bình quân cho SP 1:
    # Mua 2 lần: lần 1 là 10, lần 2 là 20 -> Tổng 30 / 2 = 15.0 (hoặc nếu có các đơn trước đó thì tính trung bình chuẩn)
    item_sp1 = next((item for item in history_data["items"] if item["product_id"] == 1), None)
    assert item_sp1 is not None
    assert item_sp1["avg_quantity"] > 0
    assert "last_order_quantity" in item_sp1
    assert "last_purchased_date" in item_sp1

    # 2. Kiểm tra nhóm hàng đã mua lần trước (last_order_items)
    assert "last_order" in history_data and history_data["last_order"] is not None
    assert history_data["last_order"]["order_code"] == ord2_code
    last_items = history_data["last_order_items"]
    assert len(last_items) == 2
    last_pids = {it["product_id"]: it["quantity"] for it in last_items}
    assert 1 in last_pids and last_pids[1] == 20
    assert 3 in last_pids and last_pids[3] == 6

    # Kiểm tra alias route trên /api/v1/dealers/1/purchase-history
    res_alias = client.get("/api/v1/dealers/1/purchase-history", headers=headers)
    assert res_alias.status_code == 200
    assert res_alias.json()["dealer_id"] == 1


def test_dealer_purchase_history_forbidden_for_unassigned_sales():
    """
    Ràng buộc: Chỉ hiện với đại lý mà nhân viên được phân công.
    Nhân viên sales không được phân công đại lý 4 -> bị chặn với 403.
    """
    sales_token = get_token("sales")
    headers = {"Authorization": f"Bearer {sales_token}"}

    # Đảm bảo Đại lý 4 KHÔNG được phân công cho sales (id=3)
    DEALERS_DB[4].assigned_sale_id = 8
    db = SessionLocal()
    try:
        d4 = db.query(DealerEntity).filter(DealerEntity.id == 4).first()
        if d4:
            d4.assigned_sale_id = 8
            db.commit()
    finally:
        db.close()

    res = client.get("/api/v1/orders/dealers/4/purchase-history", headers=headers)
    assert res.status_code == 403
    assert "Chỉ hiển thị với đại lý mà nhân viên được phân công" in res.json()["detail"]


def test_dealer_purchase_history_allowed_for_manager_and_admin():
    """
    Quản lý kinh doanh (sales_manager) và admin có thể xem lịch sử mua hàng của mọi đại lý.
    """
    mgr_token = get_token("sales_manager")
    mgr_headers = {"Authorization": f"Bearer {mgr_token}"}

    res_mgr = client.get("/api/v1/orders/dealers/4/purchase-history", headers=mgr_headers)
    assert res_mgr.status_code == 200

    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    res_admin = client.get("/api/v1/orders/dealers/4/purchase-history", headers=admin_headers)
    assert res_admin.status_code == 200
