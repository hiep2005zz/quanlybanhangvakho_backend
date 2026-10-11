# backend/tests/test_order_filtering_and_revenue.py
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.dealer import DEALERS_DB
from app.models.entities import OrderEntity, DealerEntity
import json

client = TestClient(app)

def get_token(username: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def setup_orders_data():
    """Tạo dữ liệu đơn hàng mẫu phục vụ kiểm thử lọc đa tiêu chí và RLS."""
    session = SessionLocal()
    # Dealer 1: DL001, Hà Nội, assigned_sale_id = 3 (user: sales)
    # Dealer 2: DL002, TP. HCM, assigned_sale_id = 3 (user: sales)
    # Dealer 4: DL004, Đà Nẵng, assigned_sale_id = 8 (user: sales2 / Nguyễn Văn A)
    now = datetime.now(timezone.utc)
    d1 = now - timedelta(days=60)
    d2 = now - timedelta(days=55)
    d3 = now - timedelta(days=50)

    DEALERS_DB[1].assigned_sale_id = 3
    DEALERS_DB[1].region = "Hà Nội"
    DEALERS_DB[2].assigned_sale_id = 3
    DEALERS_DB[2].region = "TP. HCM"
    DEALERS_DB[4].assigned_sale_id = 8
    DEALERS_DB[4].region = "Đà Nẵng"

    # Đảm bảo trong DB DealerEntity cũng cập nhật
    db_d1 = session.get(DealerEntity, 1)
    if db_d1:
        db_d1.assigned_sale_id = 3
        db_d1.region = "Hà Nội"
    db_d2 = session.get(DealerEntity, 2)
    if db_d2:
        db_d2.assigned_sale_id = 3
        db_d2.region = "TP. HCM"
    db_d4 = session.get(DealerEntity, 4)
    if db_d4:
        db_d4.assigned_sale_id = 8
        db_d4.region = "Đà Nẵng"
    session.commit()

    test_orders = [
        OrderEntity(
            order_code="TEST_ORD_01",
            dealer_id=1,
            dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
            created_by="sales",
            assigned_sale_id=3,
            assigned_sale_name="Trần Bán Hàng",
            total_amount=1000000.0,
            status="CONFIRMED",
            created_at=d1,
            items_json=json.dumps([
                {"product_id": 1, "product_name": "SP1", "quantity": 10, "price": 100000.0, "cost_price": 70000.0, "profit": 30000.0}
            ])
        ),
        OrderEntity(
            order_code="TEST_ORD_02",
            dealer_id=1,
            dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
            created_by="sales",
            assigned_sale_id=3,
            assigned_sale_name="Trần Bán Hàng",
            total_amount=2000000.0,
            status="PENDING_APPROVAL",
            created_at=d2,
            items_json=json.dumps([
                {"product_id": 2, "product_name": "SP2", "quantity": 20, "price": 100000.0, "cost_price": 60000.0}
            ])
        ),
        OrderEntity(
            order_code="TEST_ORD_03",
            dealer_id=2,
            dealer_name="Đại Lý Thời Trang Tân Bình",
            created_by="sales",
            assigned_sale_id=3,
            assigned_sale_name="Trần Bán Hàng",
            total_amount=3000000.0,
            status="CONFIRMED",
            created_at=d3,
            items_json=json.dumps([
                {"product_id": 1, "product_name": "SP1", "quantity": 30, "price": 100000.0}
            ])
        ),
        OrderEntity(
            order_code="TEST_ORD_04",
            dealer_id=4,
            dealer_name="Công Ty TNHH Bán Lẻ An Phát",
            created_by="admin",
            assigned_sale_id=8,
            assigned_sale_name="Nguyễn Văn A",
            total_amount=4000000.0,
            status="CONFIRMED",
            created_at=d2,
            items_json=json.dumps([
                {"product_id": 1, "product_name": "SP1", "quantity": 40, "price": 100000.0, "cost_price": 80000.0}
            ])
        ),
    ]

    for o in test_orders:
        session.add(o)
    session.commit()
    created_codes = [o.order_code for o in test_orders]
    session.close()

    yield created_codes

    # Dọn dẹp sau kiểm thử
    cleanup_session = SessionLocal()
    cleanup_session.query(OrderEntity).filter(OrderEntity.order_code.in_(created_codes)).delete(synchronize_session=False)
    cleanup_session.commit()
    cleanup_session.close()


def test_ac1_sales_manager_sees_all_orders(setup_orders_data):
    """AC-1: sales_manager đăng nhập thấy được toàn bộ đơn hàng của tất cả đại lý và tất cả nhân viên."""
    sm_token = get_token("sales_manager")
    headers = {"Authorization": f"Bearer {sm_token}"}

    res = client.get("/api/v1/orders?page=1&page_size=50", headers=headers)
    assert res.status_code == 200
    data = res.json()

    assert "items" in data
    assert "total" in data
    assert "filtered_total_amount" in data
    assert data["total"] >= 4

    returned_codes = {o["order_code"] for o in data["items"]}
    for code in ["TEST_ORD_01", "TEST_ORD_02", "TEST_ORD_03", "TEST_ORD_04"]:
        assert code in returned_codes


def test_ac2_sales_only_sees_assigned_dealers(setup_orders_data):
    """AC-2: sales đăng nhập chỉ thấy các đơn hàng thuộc đại lý được phân công. Gửi dealer_id của người khác trả về rỗng."""
    sales_token = get_token("sales")
    headers = {"Authorization": f"Bearer {sales_token}"}

    # 1. Xem danh sách không truyền dealer_id: Chỉ thấy Dealer 1 và Dealer 2, KHÔNG thấy Dealer 4
    res = client.get("/api/v1/orders?page=1&page_size=50", headers=headers)
    assert res.status_code == 200
    data = res.json()
    returned_codes = {o["order_code"] for o in data["items"]}

    assert "TEST_ORD_01" in returned_codes
    assert "TEST_ORD_02" in returned_codes
    assert "TEST_ORD_03" in returned_codes
    assert "TEST_ORD_04" not in returned_codes  # Thuộc Dealer 4 của sale id=8

    # 2. Truyền dealer_id = 4 (thuộc nhân viên khác): Phải trả về danh sách rỗng
    res_forbidden_dealer = client.get("/api/v1/orders?dealer_id=4", headers=headers)
    assert res_forbidden_dealer.status_code == 200
    data_forbidden = res_forbidden_dealer.json()
    assert data_forbidden["total"] == 0
    assert len(data_forbidden["items"]) == 0
    assert data_forbidden["filtered_total_amount"] == 0.0


def test_ac3_multi_criteria_filtering(setup_orders_data):
    """AC-3: Kết hợp lọc theo trạng thái + khoảng ngày + khu vực trả về đúng bản ghi thỏa mãn đồng thời."""
    sm_token = get_token("sales_manager")
    headers = {"Authorization": f"Bearer {sm_token}"}

    today = date.today()
    start_d = today - timedelta(days=62)
    end_d = today - timedelta(days=58)

    # Lọc: status=CONFIRMED + region=Hà Nội + khoảng ngày [today-12, today-4]
    # Khớp duy nhất TEST_ORD_01 (Dealer 1 ở Hà Nội, 10 ngày trước, CONFIRMED, 1.000.000 đ)
    # TEST_ORD_02 bị loại vì status là PENDING_APPROVAL
    # TEST_ORD_03 bị loại vì ở TP. HCM và ngày d3 (-1 ngày ngoài range)
    # TEST_ORD_04 bị loại vì ở Đà Nẵng
    url = f"/api/v1/orders?status=CONFIRMED&region=H%C3%A0+N%E1%BB%99i&start_date={start_d.isoformat()}&end_date={end_d.isoformat()}"
    res = client.get(url, headers=headers)
    assert res.status_code == 200
    data = res.json()

    codes = [o["order_code"] for o in data["items"]]
    assert "TEST_ORD_01" in codes
    assert "TEST_ORD_02" not in codes
    assert "TEST_ORD_03" not in codes
    assert "TEST_ORD_04" not in codes
    assert data["filtered_total_amount"] == 1000000.0


def test_ac4_aggregation_across_pagination(setup_orders_data):
    """AC-4: Giá trị filtered_total_amount phản ánh đúng tổng tiền của tất cả đơn hàng thỏa bộ lọc, không đổi khi đổi trang."""
    sm_token = get_token("sales_manager")
    headers = {"Authorization": f"Bearer {sm_token}"}

    # Lọc đơn có status=CONFIRMED: có ít nhất TEST_ORD_01 (1tr), TEST_ORD_03 (3tr), TEST_ORD_04 (4tr) = 8tr
    # Xem trang 1 với page_size = 1
    res_p1 = client.get("/api/v1/orders?status=CONFIRMED&page=1&page_size=1", headers=headers)
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert len(data_p1["items"]) == 1
    total_amount_p1 = data_p1["filtered_total_amount"]
    total_count_p1 = data_p1["total"]

    # Xem trang 2 với page_size = 1
    res_p2 = client.get("/api/v1/orders?status=CONFIRMED&page=2&page_size=1", headers=headers)
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert len(data_p2["items"]) == 1
    total_amount_p2 = data_p2["filtered_total_amount"]
    total_count_p2 = data_p2["total"]

    # filtered_total_amount và total PHẢI GIỮ NGUYÊN giữa các trang
    assert total_amount_p1 == total_amount_p2
    assert total_count_p1 == total_count_p2
    assert total_amount_p1 >= 8000000.0
    # Đơn hàng ở trang 1 khác đơn hàng ở trang 2
    assert data_p1["items"][0]["order_code"] != data_p2["items"][0]["order_code"]


def test_ac5_security_no_cost_or_profit_for_sales(setup_orders_data):
    """AC-5: Không có trường dữ liệu giá vốn hay lợi nhuận trong payload trả về cho vai trò sales."""
    sales_token = get_token("sales")
    headers = {"Authorization": f"Bearer {sales_token}"}

    res = client.get("/api/v1/orders?page=1&page_size=50", headers=headers)
    assert res.status_code == 200
    data = res.json()

    for order in data["items"]:
        # Kiểm tra ở cấp độ order
        assert "cost_price" not in order
        assert "profit" not in order
        assert "margin" not in order
        # Kiểm tra trong từng item của đơn
        if order.get("items"):
            for item in order["items"]:
                assert "cost_price" not in item
                assert "profit" not in item
                assert "margin" not in item
                assert "gross_profit" not in item
