# backend/tests/rbac/test_user_management.py
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.user import USERS_DB

client = TestClient(app)

def get_token(username: str = "admin", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_admin_can_list_users():
    """AC: Admin có quyền xem danh sách người dùng hệ thống."""
    token = get_token("admin", "123")
    res = client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "users" in data
    assert data["total"] >= 7
    usernames = [u["username"] for u in data["users"]]
    assert "admin" in usernames
    assert "sales" in usernames
    assert "kho" in usernames


@pytest.mark.parametrize("non_admin", ["sales", "kho", "ketoan", "muahang", "warehouse_mgr", "sales_manager"])
def test_non_admin_forbidden_from_user_management(non_admin: str):
    """AC: Zero-Trust / Default Deny: Tất cả các vai trò không phải Admin bị chặn 403."""
    token = get_token(non_admin, "123")

    # Thử xem danh sách
    res_get = client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
    assert res_get.status_code == 403

    # Thử tạo người dùng mới
    res_post = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": "Test Hack",
            "email": f"hack_{non_admin}@congty.vn",
            "password": "Password123@",
            "role": "admin",
            "branch": "Kho Tổng Hà Nội"
        }
    )
    assert res_post.status_code == 403


def test_admin_create_new_user_and_immediate_login():
    """
    AC: Admin tạo người dùng mới với Họ tên, Email, Mật khẩu, Vai trò, Kho/Địa bàn.
    Dữ liệu lưu vào database và tài khoản có thể đăng nhập được ngay với đúng quyền.
    """
    admin_token = get_token("admin", "123")
    new_user_payload = {
        "full_name": "Hoàng Văn Sales Mới",
        "username": "sales_moi",
        "email": "sales_moi@congty.vn",
        "password": "Password123@",
        "role": "sales",
        "branch": "Kho Chi Nhánh Đà Nẵng"
    }

    res_create = client.post("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"}, json=new_user_payload)
    assert res_create.status_code == 201
    created_data = res_create.json()
    assert created_data["username"] == "sales_moi"
    assert created_data["role"] == "sales"
    assert created_data["branch"] == "Kho Chi Nhánh Đà Nẵng"
    assert created_data["can_view_cost"] is False
    assert created_data["can_write_inventory"] is False

    # Đăng nhập ngay bằng username
    res_login_uname = client.post("/api/v1/auth/login", json={"username": "sales_moi", "password": "Password123@"})
    assert res_login_uname.status_code == 200
    user_info = res_login_uname.json()["user"]
    assert user_info["role"] == "sales"
    assert user_info["branch"] == "Kho Chi Nhánh Đà Nẵng"

    # Đăng nhập ngay bằng email
    res_login_email = client.post("/api/v1/auth/login", json={"username": "sales_moi@congty.vn", "password": "Password123@"})
    assert res_login_email.status_code == 200


def test_admin_can_create_another_admin():
    """AC: Tài khoản Admin có quyền tạo thêm tài khoản Admin khác."""
    admin_token = get_token("admin", "123")
    payload = {
        "full_name": "Trần Phó Quản Trị",
        "username": "admin2",
        "email": "admin2@congty.vn",
        "password": "AdminPassword123@",
        "role": "admin",
        "branch": "Toàn quốc"
    }

    res = client.post("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"}, json=payload)
    assert res.status_code == 201
    assert res.json()["role"] == "admin"

    # Admin mới đăng nhập thành công và có toàn quyền
    login_res = client.post("/api/v1/auth/login", json={"username": "admin2", "password": "AdminPassword123@"})
    assert login_res.status_code == 200
    assert login_res.json()["user"]["role"] == "admin"


def test_admin_cannot_self_delete():
    """AC: Admin KHÔNG được tự xóa tài khoản Admin của chính mình."""
    admin_token = get_token("admin", "123")
    res = client.delete("/api/v1/users/admin", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 400
    assert "Không được tự xóa tài khoản Admin của chính mình" in res.json()["detail"]


def test_admin_cannot_self_demote():
    """AC: Admin KHÔNG được tự hạ quyền Admin của chính mình."""
    admin_token = get_token("admin", "123")
    res = client.put(
        "/api/v1/users/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "sales"}
    )
    assert res.status_code == 400
    assert "Không được tự hạ quyền Admin của chính mình" in res.json()["detail"]


def test_admin_cannot_self_deactivate():
    """AC: Admin KHÔNG được tự khóa tài khoản của chính mình."""
    admin_token = get_token("admin", "123")
    res = client.put(
        "/api/v1/users/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False}
    )
    assert res.status_code == 400
    assert "Không được tự khóa tài khoản Admin của chính mình" in res.json()["detail"]


def test_admin_can_delete_other_user():
    """AC: Admin có quyền xóa tài khoản nhân viên khác."""
    admin_token = get_token("admin", "123")
    # Tạo user tạm để xóa
    client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "full_name": "Tài khoản xóa thử",
            "username": "user_to_delete",
            "email": "delete_me@congty.vn",
            "password": "123",
            "role": "sales",
            "branch": "Kho Tổng Hà Nội"
        }
    )
    assert "user_to_delete" in USERS_DB

    # Xóa user
    del_res = client.delete("/api/v1/users/user_to_delete", headers={"Authorization": f"Bearer {admin_token}"})
    assert del_res.status_code == 200
    assert "user_to_delete" not in USERS_DB


def test_user_can_have_multiple_roles():
    """Yêu cầu: Một người dùng có thể giữ nhiều vai trò cùng lúc và hợp nhất quyền hạn."""
    admin_token = get_token("admin", "123")
    payload = {
        "full_name": "Đa Năng Vừa Sales Vừa Kho",
        "username": "multi_role_user",
        "email": "multirole@congty.vn",
        "password": "Password123@",
        "roles": ["sales", "warehouse"],
        "branch": "Kho Tổng Hà Nội"
    }
    res_create = client.post("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"}, json=payload)
    assert res_create.status_code == 201
    created_data = res_create.json()
    assert set(created_data["roles"]) == {"sales", "warehouse"}
    assert created_data["can_write_inventory"] is True  # Có quyền kho nhờ vai trò warehouse

    # Đăng nhập và kiểm tra thông tin user trả về
    res_login = client.post("/api/v1/auth/login", json={"username": "multi_role_user", "password": "Password123@"})
    assert res_login.status_code == 200
    user_data = res_login.json()["user"]
    assert set(user_data["roles"]) == {"sales", "warehouse"}
    assert "inventory:write" in user_data["permissions"]
    assert "order:write" in user_data["permissions"]


def test_warehouse_role_must_be_assigned_to_specific_warehouse():
    """Yêu cầu: Người dùng vai trò Kho phải gắn với ít nhất 1 kho cụ thể."""
    admin_token = get_token("admin", "123")

    # Thử gán vai trò kho nhưng địa bàn là 'Toàn quốc' (không phải kho cụ thể) -> Phải bị từ chối 400
    payload_invalid = {
        "full_name": "Thủ Kho Sai Địa Bàn",
        "username": "kho_invalid_branch",
        "email": "kho_invalid@congty.vn",
        "password": "Password123@",
        "roles": ["warehouse"],
        "branch": "Toàn quốc"
    }
    res_fail = client.post("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"}, json=payload_invalid)
    assert res_fail.status_code == 400
    assert "bắt buộc phải gắn với ít nhất 1 kho cụ thể" in res_fail.json()["detail"]

    # Gán vai trò kho với kho cụ thể -> Thành công 201
    payload_valid = {
        "full_name": "Thủ Kho Đúng Kho",
        "username": "kho_valid_branch",
        "email": "kho_valid@congty.vn",
        "password": "Password123@",
        "roles": ["warehouse"],
        "branch": "Kho Chi Nhánh Đà Nẵng"
    }
    res_ok = client.post("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"}, json=payload_valid)
    assert res_ok.status_code == 201
    assert res_ok.json()["branch"] == "Kho Chi Nhánh Đà Nẵng"


def test_admin_cannot_revoke_own_admin_role_in_multi_roles():
    """Yêu cầu: Không thể tự thu hồi vai trò quản trị của chính mình (kể cả khi gửi mảng roles)."""
    admin_token = get_token("admin", "123")

    # Cố gắng cập nhật danh sách vai trò cho chính mình nhưng bỏ vai trò admin
    res_update = client.put(
        "/api/v1/users/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"roles": ["sales", "accountant"]}
    )
    assert res_update.status_code == 400
    assert "Không thể tự thu hồi vai trò quản trị của chính mình" in res_update.json()["detail"]

    # Nhưng có thể thêm vai trò khác cho chính mình mà vẫn giữ admin
    res_ok = client.put(
        "/api/v1/users/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"roles": ["admin", "sales_manager"]}
    )
    assert res_ok.status_code == 200
    assert "admin" in res_ok.json()["roles"]
    assert "sales_manager" in res_ok.json()["roles"]

    # Phục hồi tài khoản admin về duy nhất 1 vai trò admin để bảo đảm tính toàn vẹn hệ thống
    res_restore = client.put(
        "/api/v1/users/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"roles": ["admin"]}
    )
    assert res_restore.status_code == 200
    assert res_restore.json()["roles"] == ["admin"]


def test_unique_phone_number_in_user_management():
    """Kiểm tra không thể tạo hoặc cập nhật tài khoản với số điện thoại đã tồn tại trên tài khoản khác."""
    admin_token = get_token("admin", "123")
    # 1. Đặt phone cụ thể cho admin
    client.put(
        "/api/v1/users/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "0998887766"}
    )

    # 2. Cập nhật phone cho sales bằng chính số 0998887766 của admin -> 409
    res_dup = client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "0998887766"}
    )
    assert res_dup.status_code == 409
    assert "Số điện thoại này đã có trên hệ thống vui lòng đổi số khác" in res_dup.json()["detail"]

    # 3. Cập nhật phone cho sales bằng số điện thoại mới không trùng -> 200
    res_ok = client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "0975556677"}
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["phone"] == "0975556677"

    # 4. Cập nhật lại chính số 0975556677 cho sales -> 200 (không xung đột chính mình)
    res_self = client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "0975556677"}
    )
    assert res_self.status_code == 200

    # 5. Thử cập nhật cho kho bằng số 0975556677 của sales -> 409
    res_dup2 = client.put(
        "/api/v1/users/kho",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "0975556677"}
    )
    assert res_dup2.status_code == 409
    assert "Số điện thoại này đã có trên hệ thống vui lòng đổi số khác" in res_dup2.json()["detail"]

    # Dọn dẹp lại phone của sales
    client.put(
        "/api/v1/users/sales",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": ""}
    )

