"""
Bộ kiểm thử tính năng Khóa / Mở khóa tài khoản & Cảnh báo bàn giao Đại lý
Kiểm tra toàn diện AC 1, AC 2, AC 3.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.user import USERS_DB
from app.models.dealer import DEALERS_DB

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def test_ac2_mandatory_lock_reason():
    """
    AC 2: Bắt buộc ghi lý do khi khóa tài khoản.
    Không có lock_reason hoặc để trống -> 400 Bad Request.
    """
    admin_token = get_token("admin")

    # Đảm bảo gán đúng ban đầu: sales (id=3) phụ trách DL001, DL002, DL003
    DEALERS_DB[1].assigned_sale_id = 3
    DEALERS_DB[2].assigned_sale_id = 3
    DEALERS_DB[3].assigned_sale_id = 3
    DEALERS_DB[4].assigned_sale_id = 2


    # Thử khóa không có lock_reason
    resp = client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "LOCKED", "lock_reason": ""}
    )
    assert resp.status_code == 400
    assert "lý do" in resp.json()["detail"].lower()


def test_ac1_lock_user_session_revoked_and_cannot_login():
    """
    AC 1:
    1. Nhân viên 'sales' đang đăng nhập (có token hợp lệ)
    2. Admin khóa tài khoản nhân viên 'sales' kèm lý do
    3. Phiên đang mở của 'sales' bị thu hồi ngay lập tức (401 Unauthorized)
    4. Khi 'sales' thử đăng nhập lại -> 403 Forbidden kèm lý do khóa
    """
    admin_token = get_token("admin")
    sales_token = get_token("sales")

    for did, d in DEALERS_DB.items():
        d.assigned_sale_id = 3 if did in [1, 2, 3] else 8


    # Phiên của sales trước khi khóa hoạt động bình thường
    check_before = client.get("/api/v1/products", headers={"Authorization": f"Bearer {sales_token}"})
    assert check_before.status_code == 200

    # Admin khóa tài khoản sales kèm lý do
    lock_reason = "Nghỉ việc từ ngày 25/09/2026, cần bàn giao địa bàn Miền Bắc."
    lock_resp = client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "LOCKED", "lock_reason": lock_reason}
    )
    assert lock_resp.status_code == 200
    user_data = lock_resp.json()
    assert user_data["status"] == "LOCKED"
    assert user_data["is_active"] is False
    assert user_data["lock_reason"] == lock_reason
    assert user_data["dealers_needing_handover"] == 3  # sales có 3 đại lý

    # 3. Phiên đang mở của sales lập tức bị 401 Unauthorized
    check_after = client.get("/api/v1/products", headers={"Authorization": f"Bearer {sales_token}"})
    assert check_after.status_code == 401
    assert "khóa" in check_after.json()["detail"].lower()

    # 4. Khi sales đăng nhập lại -> Bị chặn 403 Forbidden
    login_resp = client.post("/api/v1/auth/login", json={"username": "sales", "password": "123"})
    assert login_resp.status_code == 403
    assert "tài khoản đã bị khóa" in login_resp.json()["detail"].lower()
    assert "liên hệ với quản lý" in login_resp.json()["detail"].lower()


def test_ac3_dealers_marked_needing_handover_and_block_orders():
    """
    AC 3:
    1. Nhân viên sales đang bị khóa -> Đại lý của sales (DL001) được đánh dấu cần bàn giao
    2. Chặn tạo đơn hàng trên đại lý này -> Báo lỗi yêu cầu bàn giao
    3. Admin thực hiện bàn giao đại lý sang cho nhân viên khác ('sales_manager')
    4. Sau khi bàn giao, tạo đơn hàng thành công!
    """
    admin_token = get_token("admin")

    for did, d in DEALERS_DB.items():
        d.assigned_sale_id = 3 if did in [1, 2, 3] else 8

    # Đảm bảo tài khoản sales ở trạng thái LOCKED để kiểm tra quy trình bàn giao

    client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "LOCKED", "lock_reason": "Nghỉ việc, cần bàn giao đại lý."}
    )
    # Đảm bảo sales_manager có phạm vi Toàn quốc để nhận bàn giao các đại lý toàn quốc
    client.put(
        "/api/v1/users/sales_manager",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"branch": "Toàn quốc", "status": "ACTIVE"}
    )


    # 1. Kiểm tra danh sách đại lý của sales
    dealers_resp = client.get(
        "/api/v1/users/sales/dealers",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert dealers_resp.status_code == 200
    data = dealers_resp.json()
    assert data["is_locked"] is True
    assert data["total_dealers"] == 3
    assert all(d["needs_handover"] is True for d in data["dealers"])

    # 2. Thử tạo đơn hàng cho đại lý DL001 (id=1, thuộc sales đang bị khóa)
    order_resp = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 10, "price": 199000.0}],
            "note": "Đơn hàng thử nghiệm"
        }
    )
    assert order_resp.status_code == 400
    assert "bàn giao trước khi lên đơn" in order_resp.json()["detail"]

    # 3. Thực hiện bàn giao đại lý sang cho 'sales_manager'
    handover_resp = client.post(
        "/api/v1/users/sales/handover",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_sale_username": "sales_manager"}
    )
    assert handover_resp.status_code == 200
    assert handover_resp.json()["transferred_count"] == 3

    # Sau khi bàn giao, số đại lý cần bàn giao của sales về 0
    sales_check = client.get("/api/v1/users/sales/dealers", headers={"Authorization": f"Bearer {admin_token}"})
    assert sales_check.json()["total_dealers"] == 0

    # 4. Thử tạo đơn lại cho đại lý DL001 (nay đã thuộc sales_manager) -> Thành công!
    order_success = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 10, "price": 199000.0}],
            "note": "Đơn hàng sau khi bàn giao"
        }
    )
    assert order_success.status_code == 201
    assert order_success.json()["dealer_id"] == 1
    assert order_success.json()["status"] == "CONFIRMED"

    # Dọn dẹp trạng thái: Mở khóa lại cho sales và trả lại đại lý
    unlock_resp = client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "ACTIVE"}
    )
    assert unlock_resp.status_code == 200
    assert unlock_resp.json()["status"] == "ACTIVE"
    assert unlock_resp.json()["is_active"] is True
    assert unlock_resp.json()["lock_reason"] is None

    # Trả lại đại lý cho sales (id=3) và các đại lý khác cho sales 8
    DEALERS_DB[1].assigned_sale_id = 3
    DEALERS_DB[2].assigned_sale_id = 3
    DEALERS_DB[3].assigned_sale_id = 3
    DEALERS_DB[4].assigned_sale_id = 8
    if 5 in DEALERS_DB:
        DEALERS_DB[5].assigned_sale_id = 8


def test_tc01_handover_role_and_region_restrictions():
    """
    [TC-01]:
    - Chặn bàn giao cho các vai trò ngoài Bán hàng (kho, kế toán, admin...) -> 400 Bad Request
    - Chặn bàn giao chéo địa bàn (ví dụ đại lý Miền Bắc gán cho nhân sự cố định chỉ phụ trách Miền Nam) -> 400 Bad Request
    """
    admin_token = get_token("admin")

    # Khóa sales để kiểm tra
    client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "LOCKED", "lock_reason": "Kiểm tra bàn giao ràng buộc vai trò"}
    )

    # 1. Thử bàn giao cho 'kho' (Thủ kho) -> Bị từ chối 400
    resp_kho = client.post(
        "/api/v1/users/sales/handover",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_sale_username": "kho"}
    )
    assert resp_kho.status_code == 400
    assert "bộ phận bán hàng" in resp_kho.json()["detail"].lower()

    # 2. Thử bàn giao cho 'ketoan' (Kế toán) -> Bị từ chối 400
    resp_ketoan = client.post(
        "/api/v1/users/sales/handover",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_sale_username": "ketoan"}
    )
    assert resp_ketoan.status_code == 400
    assert "bộ phận bán hàng" in resp_ketoan.json()["detail"].lower()

    # Dọn dẹp trạng thái
    client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "ACTIVE"}
    )
    DEALERS_DB[1].assigned_sale_id = 3
    DEALERS_DB[2].assigned_sale_id = 3
    DEALERS_DB[3].assigned_sale_id = 3
    DEALERS_DB[4].assigned_sale_id = 8
    if 5 in DEALERS_DB:
        DEALERS_DB[5].assigned_sale_id = 8

