import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import get_password_hash
from app.models.dealer import DEALERS_DB, Dealer, save_dealers_db, sync_dealer_for_user
from app.models.user import USERS_DB, UserInDB, save_users_db
from app.api.v1.endpoints.orders import ORDERS_DB

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

def test_dealer_order_tracking_isolated_and_shortage():
    """
    User Story:
    Là Đại lý, tôi muốn theo dõi trạng thái các đơn hàng của mình,
    để biết khi nào hàng tới mà không phải gọi cho nhân viên kinh doanh.
    - Danh sách đơn kèm trạng thái, ngày giao dự kiến, tổng tiền
    - Xem chi tiết dòng hàng và số lượng thực giao nếu giao thiếu
    - Chỉ thấy đơn của chính mình
    """
    # 1. Tạo 2 tài khoản đại lý (role: customer)
    cust1_username = "track_cust_01"
    cust2_username = "track_cust_02"
    USERS_DB[cust1_username] = UserInDB(
        id=8801,
        username=cust1_username,
        full_name="Đại Lý An Phú Track 1",
        email="track1@daily.vn",
        role="customer",
        roles=["customer"],
        hashed_password=get_password_hash("123"),
        is_active=True,
        branch="Hà Nội",
    )
    USERS_DB[cust2_username] = UserInDB(
        id=8802,
        username=cust2_username,
        full_name="Đại Lý Bình Minh Track 2",
        email="track2@daily.vn",
        role="customer",
        roles=["customer"],
        hashed_password=get_password_hash("123"),
        is_active=True,
        branch="TP. HCM",
    )
    save_users_db()

    # 2. Tạo 2 đại lý tương ứng
    dealer1 = Dealer(
        id=8801,
        code="DL_TRACK_01",
        name="Đại Lý An Phú Track 1",
        phone="0911000111",
        email="track1@daily.vn",
        address="123 Cầu Giấy, Hà Nội",
        customer_group="dai_ly_cap_1",
        status="Đang hoạt động",
        credit_limit=100000000.0,
        max_debt_days=30,
        assigned_sale_id=3,
    )
    dealer2 = Dealer(
        id=8802,
        code="DL_TRACK_02",
        name="Đại Lý Bình Minh Track 2",
        phone="0911000222",
        email="track2@daily.vn",
        address="456 Lê Lợi, TP. HCM",
        customer_group="dai_ly_cap_2",
        status="Đang hoạt động",
        credit_limit=100000000.0,
        max_debt_days=30,
        assigned_sale_id=3,
    )
    DEALERS_DB[dealer1.id] = dealer1
    DEALERS_DB[dealer2.id] = dealer2
    save_dealers_db()

    token_cust1 = get_token(cust1_username)
    headers_cust1 = {"Authorization": f"Bearer {token_cust1}"}

    token_cust2 = get_token(cust2_username)
    headers_cust2 = {"Authorization": f"Bearer {token_cust2}"}

    # 3. Tạo đơn hàng cho Đại lý 1 có giao thiếu (quantity=10, delivered_quantity=7)
    order1_code = "DH-TRACK-001"
    expected_delivery_date = (date.today() + timedelta(days=3)).isoformat()
    ORDERS_DB[9001] = {
        "id": 9001,
        "order_code": order1_code,
        "dealer_id": dealer1.id,
        "dealer_name": dealer1.name,
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Nguyen Van Sales",
        "total_amount": 5000000.0,
        "subtotal_amount": 5000000.0,
        "status": "EXPORTED",
        "delivery_point": "Kho trung tâm Cầu Giấy — 123 Cầu Giấy, Hà Nội",
        "desired_delivery_date": expected_delivery_date,
        "note": "Giao giờ hành chính",
        "created_at": "2026-10-10T10:00:00",
        "items": [
            {
                "product_id": 1,
                "product_code": "SP001",
                "product_name": "Sản phẩm A",
                "quantity": 10,
                "delivered_quantity": 7,
                "price": 500000.0,
                "unit": "Hộp",
            }
        ],
    }

    # 4. Tạo đơn hàng cho Đại lý 2
    order2_code = "DH-TRACK-002"
    ORDERS_DB[9002] = {
        "id": 9002,
        "order_code": order2_code,
        "dealer_id": dealer2.id,
        "dealer_name": dealer2.name,
        "created_by": "sales",
        "assigned_sale_id": 3,
        "assigned_sale_name": "Nguyen Van Sales",
        "total_amount": 2500000.0,
        "subtotal_amount": 2500000.0,
        "status": "DELIVERED",
        "delivery_point": "Chi nhánh miền Nam — 456 Lê Lợi, TP. HCM",
        "desired_delivery_date": expected_delivery_date,
        "note": "Hàng dễ vỡ",
        "created_at": "2026-10-10T11:00:00",
        "items": [
            {
                "product_id": 2,
                "product_code": "SP002",
                "product_name": "Sản phẩm B",
                "quantity": 5,
                "delivered_quantity": 5,
                "price": 500000.0,
                "unit": "Cái",
            }
        ],
    }

    # 5. Kiểm tra Đại lý 1 gọi API danh sách đơn hàng
    res1 = client.get("/api/v1/orders", headers=headers_cust1)
    assert res1.status_code == 200
    orders1 = res1.json()

    # Tiêu chí: Chỉ thấy đơn của chính mình
    assert any(o["order_code"] == order1_code for o in orders1), "Đại lý 1 phải thấy đơn hàng của chính mình"
    assert not any(o["order_code"] == order2_code for o in orders1), "Đại lý 1 TUYỆT ĐỐI KHÔNG được thấy đơn của Đại lý 2"

    # Tiêu chí: Đầy đủ trạng thái, ngày giao dự kiến, điểm giao hàng, tổng tiền
    target_order = next(o for o in orders1 if o["order_code"] == order1_code)
    assert target_order["status"] == "EXPORTED"
    assert target_order["desired_delivery_date"] == expected_delivery_date
    assert target_order["delivery_point"] == "Kho trung tâm Cầu Giấy — 123 Cầu Giấy, Hà Nội"
    assert target_order["total_amount"] == 5000000.0

    # 6. Kiểm tra Đại lý 1 xem chi tiết dòng hàng và số lượng thực giao / thiếu
    detail_res1 = client.get(f"/api/v1/orders/{order1_code}", headers=headers_cust1)
    assert detail_res1.status_code == 200
    detail_data1 = detail_res1.json()

    assert len(detail_data1["items"]) == 1
    item = detail_data1["items"][0]
    assert item["quantity"] == 10
    assert item["delivered_quantity"] == 7
    assert item["shortage_quantity"] == 3  # Giao thiếu 3 hộp

    # 7. Kiểm tra Đại lý 1 không thể xem chi tiết đơn hàng của Đại lý 2 (403 Forbidden)
    forbidden_res = client.get(f"/api/v1/orders/{order2_code}", headers=headers_cust1)
    assert forbidden_res.status_code == 403
    assert "chính mình" in forbidden_res.json()["detail"].lower()

    # 8. Kiểm tra Đại lý 2 gọi API danh sách đơn hàng
    res2 = client.get("/api/v1/orders", headers=headers_cust2)
    assert res2.status_code == 200
    orders2 = res2.json()

    assert any(o["order_code"] == order2_code for o in orders2)
    assert not any(o["order_code"] == order1_code for o in orders2)

    # 9. Kiểm tra Giao bổ sung:
    # 9a. Đại lý (customer) không được phép tự gọi API giao bổ sung -> 403 Forbidden
    cust_supp_res = client.post(f"/api/v1/orders/{order1_code}/supplementary-delivery", headers=headers_cust1, json={"note": "Test giao bù"})
    assert cust_supp_res.status_code == 403

    # 9b. Nhân viên (sales/admin) thực hiện giao bổ sung thành công
    token_admin = get_token("admin")
    headers_admin = {"Authorization": f"Bearer {token_admin}"}
    admin_supp_res = client.post(f"/api/v1/orders/{order1_code}/supplementary-delivery", headers=headers_admin, json={"note": "Giao bổ sung đợt 2 - hoàn tất"})
    assert admin_supp_res.status_code == 200
    supp_data = admin_supp_res.json()
    assert supp_data["items"][0]["delivered_quantity"] == 10
    assert supp_data["items"][0]["shortage_quantity"] == 0
    assert supp_data["status"] == "DELIVERED"

