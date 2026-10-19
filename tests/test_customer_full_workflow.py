import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.user import USERS_DB, UserInDB, DEFAULT_HASH, save_users_db
from app.core.rbac import Role

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_customer_account():
    USERS_DB["test_portal_cust"] = UserInDB(
        id=101,
        username="test_portal_cust",
        full_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        email="saomai@daily.vn",
        phone="0912345678",
        role=Role.CUSTOMER.value,
        roles=[Role.CUSTOMER.value],
        hashed_password=DEFAULT_HASH,
        branch="Đại lý phân phối",
    )
    save_users_db()
    yield
    USERS_DB.pop("test_portal_cust", None)
    save_users_db()

def test_full_customer_ordering_workflow():
    # 1. Login tài khoản test_portal_cust (role: customer, liên kết Dealer 1)
    login_resp = client.post("/api/v1/auth/login", json={"username": "test_portal_cust", "password": "123"})
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Lấy danh sách sản phẩm theo bảng giá của đại lý (Bảo mật: stripped cost_price)
    products_resp = client.get("/api/v1/products", headers=headers)
    assert products_resp.status_code == 200
    products_data = products_resp.json()
    products = products_data.get("items", products_data)
    assert len(products) > 0
    test_prod = products[0]
    # Kiểm tra bảo mật: Không lộ cost_price cho customer
    assert test_prod.get("cost_price") is None

    # 3. Lấy hạn mức công nợ của chính đại lý (Dealer ID 1)
    credit_resp = client.get("/api/v1/dealers/1/credit-info", headers=headers)
    assert credit_resp.status_code == 200, f"Credit info failed: {credit_resp.text}"
    credit_data = credit_resp.json()
    assert "remaining_credit" in credit_data
    assert "credit_limit" in credit_data

    # Thử lấy hạn mức của đại lý khác (ID: 2) -> Phải bị HTTP 403 Forbidden
    other_credit_resp = client.get("/api/v1/dealers/2/credit-info", headers=headers)
    assert other_credit_resp.status_code == 403

    # 4. Tạo đơn đặt hàng từ Customer Portal
    # Hacker cố tình gửi dealer_id = 2 (của người khác) và status = "CONFIRMED" (gian lận duyệt)
    order_payload = {
        "dealer_id": 2,
        "delivery_point": "Kho nhận hàng Đại lý Sao Mai",
        "desired_delivery_date": "2026-10-15",
        "note": "Đơn hàng tự đặt từ Cổng Đại Lý",
        "items": [
            {
                "product_id": test_prod["id"],
                "quantity": 2,
                "price": test_prod.get("sell_price") or 100000
            }
        ]
    }
    create_resp = client.post("/api/v1/orders", json=order_payload, headers=headers)
    assert create_resp.status_code in [200, 201], f"Create order failed: {create_resp.text}"
    created_order = create_resp.json()

    # Kiểm tra Security: Backend BẮT BUỘC ép dealer_id = 1 (của customer) và status = PENDING_APPROVAL
    assert created_order["dealer_id"] == 1, "Chống đổi ID thất bại! dealer_id phải bị ép về 1"
    assert created_order["status"] == "PENDING_APPROVAL", "Trạng thái phải bị ép cứng là PENDING_APPROVAL"
    order_code = created_order["order_code"]

    # 5. Customer xem danh sách đơn hàng -> Phải thấy đơn vừa tạo (Data Isolation)
    orders_resp = client.get("/api/v1/orders", headers=headers)
    assert orders_resp.status_code == 200
    my_orders = orders_resp.json()
    if isinstance(my_orders, dict) and "items" in my_orders:
        my_orders = my_orders["items"]
    found = any(o["order_code"] == order_code for o in my_orders)
    assert found is True
    # Tất cả đơn trả về đều phải thuộc dealer_id = 1
    for o in my_orders:
        assert o["dealer_id"] == 1

    # 6. Customer tự hủy đơn khi đơn chưa duyệt -> Phải thành công
    cancel_resp = client.post(
        f"/api/v1/orders/{order_code}/cancel",
        json={"reason": "Đại lý đổi ý không lấy hàng nữa"},
        headers=headers
    )
    assert cancel_resp.status_code == 200, f"Cancel order failed: {cancel_resp.text}"
    cancel_data = cancel_resp.json()
    cancelled_status = cancel_data.get("order", {}).get("status")
    assert cancelled_status == "CANCELLED"

    # Kiểm tra lại đơn đã chuyển sang CANCELLED
    detail_resp = client.get(f"/api/v1/orders/{order_code}", headers=headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "CANCELLED"
    print("\n>>> FULL CUSTOMER WORKFLOW TEST PASSED 100% <<<")


def test_customer_sales_entry_and_assigned_sales_approval():
    # 1. Login Customer
    login_cust = client.post("/api/v1/auth/login", json={"username": "test_portal_cust", "password": "123"})
    assert login_cust.status_code == 200
    cust_token = login_cust.json()["access_token"]
    cust_headers = {"Authorization": f"Bearer {cust_token}"}

    # 2. Đặt hàng qua /orders/sales-entry (đây là endpoint Cổng Đặt Hàng Đại Lý gọi khi có delivery_point)
    payload = {
        "dealer_id": 1,
        "delivery_point": "Chi nhánh giao hàng số 1",
        "desired_delivery_date": "2026-10-20",
        "note": "Đơn test duyệt bởi sales phụ trách",
        "items": [
            {
                "product_id": 1,
                "quantity": 1,
                "price": 200000
            }
        ]
    }
    create_resp = client.post("/api/v1/orders/sales-entry", json=payload, headers=cust_headers)
    assert create_resp.status_code in [200, 201], f"Sales-entry failed: {create_resp.text}"
    order_data = create_resp.json()
    order_code = order_data["order_code"]
    assert order_data["status"] == "PENDING_APPROVAL"
    assigned_sale_id = order_data.get("assigned_sale_id")

    # 3. Login nhân viên kinh doanh phụ trách (Trần Bán Hàng, username: sales, id: 3)
    login_sales = client.post("/api/v1/auth/login", json={"username": "sales", "password": "123"})
    assert login_sales.status_code == 200
    sales_token = login_sales.json()["access_token"]
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # 4. Nhân viên phụ trách duyệt đơn hàng của đại lý
    approve_resp = client.post(f"/api/v1/orders/{order_code}/approve", headers=sales_headers)
    assert approve_resp.status_code == 200, f"Sales approve failed: {approve_resp.text}"
    approved_order = approve_resp.json()
    assert approved_order["status"] == "CONFIRMED"
    assert approved_order["approved_by"] == "sales"

    print(f"\n>>> TEST CUSTOMER SALES-ENTRY & SALES APPROVAL ({order_code}) PASSED <<<")


def test_admin_created_order_never_needs_approval_and_sales_cannot_approve():
    # 1. Admin login
    admin_login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "123"})
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Admin tạo đơn hàng cho Đại lý 1
    order_payload = {
        "dealer_id": 1,
        "delivery_point": "Kho Admin chỉ định",
        "desired_delivery_date": "2026-10-25",
        "note": "Đơn do Admin tự tạo - Phải tự động CONFIRMED không cần duyệt",
        "items": [
            {
                "product_id": 1,
                "quantity": 2,
                "price": 180000
            }
        ]
    }
    create_resp = client.post("/api/v1/orders/sales-entry", json=order_payload, headers=admin_headers)
    assert create_resp.status_code in [200, 201], f"Admin create order failed: {create_resp.text}"
    order_data = create_resp.json()
    order_code = order_data["order_code"]

    # Đơn của Admin phải tự động CONFIRMED ngay lập tức, không bắt duyệt
    assert order_data["status"] == "CONFIRMED"
    assert order_data.get("requires_approval") is False

    # 3. Nhân viên bán hàng login (sales, id 3)
    sales_login = client.post("/api/v1/auth/login", json={"username": "sales", "password": "123"})
    assert sales_login.status_code == 200
    sales_token = sales_login.json()["access_token"]
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # 4. Nhân viên bán hàng KHÔNG ĐƯỢC PHÉP duyệt đơn do Admin tạo -> Phải bị HTTP 403 Forbidden
    approve_resp = client.post(f"/api/v1/orders/{order_code}/approve", headers=sales_headers)
    assert approve_resp.status_code == 403
    assert "Admin" in approve_resp.json()["detail"] or "Quản trị viên" in approve_resp.json()["detail"]

    # 5. Tương tự, sales không được từ chối đơn của Admin
    reject_resp = client.post(f"/api/v1/orders/{order_code}/reject", json={"reason": "Không thích"}, headers=sales_headers)
    assert reject_resp.status_code == 403

    # 6. Thử nghiệm: Đơn của đại lý không do sales phụ trách (giả lập dealer_id=4 có assigned_sale_id khác 3)
    from app.api.v1.endpoints.orders import ORDERS_DB
    test_id = 99999
    ORDERS_DB[test_id] = {
        "id": test_id,
        "order_code": "ORD-TEST-UNASSIGNED",
        "dealer_id": 4,
        "assigned_sale_id": 999,  # Đại lý do người khác phụ trách (không phải sales id 3)
        "created_by": "customer_other",
        "status": "PENDING_APPROVAL",
        "requires_approval": True,
        "total_amount": 100000,
        "final_total_amount": 100000,
        "delivery_point": "Kho Đà Nẵng",
        "items": []
    }
    # Sales (id 3) cố duyệt đơn của đại lý người khác phụ trách -> Bị 403 Forbidden
    unassigned_approve = client.post("/api/v1/orders/ORD-TEST-UNASSIGNED/approve", headers=sales_headers)
    assert unassigned_approve.status_code == 403
    assert "chỉ có quyền duyệt đơn hàng của đại lý do mình phụ trách" in unassigned_approve.json()["detail"]

    # Dọn dẹp
    ORDERS_DB.pop(test_id, None)
    print("\n>>> TEST ADMIN ORDER CONFIRMATION & SALES PERMISSION RESTRICTIONS PASSED <<<")


def test_create_customer_endpoint_no_500():
    login_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "123"})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    test_payload = {
        "full_name": "Nguyễn Văn Test Tạo Tài Khoản",
        "email": "test_create_account_no_500@congty.vn",
        "phone": "0987654311",
        "roles": ["sales_manager"],
        "branch": "Toàn quốc",
    }
    USERS_DB.pop("test_create_account_no_500", None)

    create_resp = client.post("/api/v1/users/customers", json=test_payload, headers=headers)
    assert create_resp.status_code == 201, f"Create customer failed: {create_resp.status_code} - {create_resp.text}"
    created_data = create_resp.json()
    assert "user" in created_data
    assert "temporary_password" in created_data
    created_username = created_data["user"]["username"]

    resend_resp = client.post(f"/api/v1/users/{created_username}/resend-credentials", headers=headers)
    assert resend_resp.status_code == 200, f"Resend credentials failed: {resend_resp.status_code} - {resend_resp.text}"
    resend_data = resend_resp.json()
    assert "temporary_password" in resend_data

    USERS_DB.pop(created_username, None)
    save_users_db()



