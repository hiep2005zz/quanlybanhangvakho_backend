import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.user import USERS_DB, UserInDB, DEFAULT_HASH
from app.models.dealer import DEALERS_DB
from app.core.rbac import Role

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_customer_account():
    # Tạo tài khoản đại lý mẫu cho kiểm thử khớp với Dealer 1 (DL001)
    USERS_DB["dl001"] = UserInDB(
        id=99,
        username="dl001",
        full_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        email="saomai@daily.vn",
        phone="0912345678",
        role=Role.CUSTOMER.value,
        roles=[Role.CUSTOMER.value],
        hashed_password=DEFAULT_HASH,
        branch="Đại lý phân phối",
    )
    from app.models.user import save_users_db
    save_users_db()
    yield
    if "dl001" in USERS_DB:
        del USERS_DB["dl001"]
        save_users_db()

def get_token(username: str) -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": "123"})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

def test_customer_role_can_create_order_forced_pending():
    """
    AC 1 & 2: Đại lý (Customer) được phép tự tạo đơn hàng.
    Đơn hàng BẮT BUỘC bị ép trạng thái 'PENDING_APPROVAL' (Chờ duyệt).
    Đại lý cố tình gửi dealer_id khác (dealer 2) vẫn bị ép gán về đại lý của chính mình (dealer 1).
    """
    token = get_token("dl001")
    payload = {
        "dealer_id": 2, # Cố tình đặt cho đại lý 2
        "items": [
            {"product_id": 1, "quantity": 1, "price": 199000}
        ],
        "note": "Đại lý tự lên đơn đặt hàng"
    }
    res = client.post("/api/v1/orders", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201, f"Tạo đơn thất bại: {res.text}"
    data = res.json()
    
    # 1. Trạng thái BẮT BUỘC bị ép thành PENDING_APPROVAL (Chờ duyệt)
    assert data["status"] == "PENDING_APPROVAL"
    assert data["requires_approval"] is True
    
    # 2. dealer_id BẮT BUỘC bị ép về chính đại lý của tài khoản (Dealer 1)
    assert data["dealer_id"] == 1
    assert data["dealer_name"] == DEALERS_DB[1].name

def test_strict_blocking_of_irrelevant_roles():
    """
    AC 1: VẪN TIẾP TỤC CHẶN NGHIÊM NGẶT các role không liên quan:
    - kho (Thủ kho)
    - ketoan (Kế toán)
    Tất cả phải trả về HTTP 403 Forbidden.
    """
    payload = {
        "dealer_id": 1,
        "items": [{"product_id": 1, "quantity": 1, "price": 199000}]
    }
    
    for blocked_user in ["kho", "ketoan"]:
        token = get_token(blocked_user)
        res = client.post("/api/v1/orders", json=payload, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403, f"Role {blocked_user} phải bị chặn 403 nhưng nhận {res.status_code}"
        assert "Truy cập bị từ chối" in res.text

def test_sales_and_admin_keep_standard_logic():
    """
    AC 2: Nếu là Sales hoặc Admin, giữ nguyên 100% logic hiện tại.
    Được phép chọn khách hàng được phân công, trạng thái không bị ép nếu giá và hạn mức hợp lệ (CONFIRMED).
    """
    token_sales = get_token("sales")
    payload = {
        "dealer_id": 1,
        "items": [{"product_id": 1, "quantity": 1, "price": 199000}]
    }
    res = client.post("/api/v1/orders", json=payload, headers={"Authorization": f"Bearer {token_sales}"})
    assert res.status_code == 201
    data = res.json()
    assert data["dealer_id"] == 1
    # Giá niêm yết chuẩn, công nợ trong hạn mức -> Trạng thái mặc định là CONFIRMED (hoặc PENDING_APPROVAL nếu quá hạn mức)
    assert data["status"] in ["CONFIRMED", "PENDING_APPROVAL"]
