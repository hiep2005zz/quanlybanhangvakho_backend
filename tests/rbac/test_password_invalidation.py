"""
Kiểm thử tính năng vô hiệu hóa phiên khi đổi mật khẩu (Password Change Invalidation)
và đảm bảo tính độc lập giữa các phiên/tài khoản.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_password_change_invalidates_old_token_only_for_that_user():
    """
    1. Đăng nhập user 'kho' -> lấy token_kho_1
    2. Đăng nhập user 'admin' -> lấy token_admin_1
    3. User 'kho' đổi mật khẩu thành công -> nhận token_kho_2
    4. token_kho_1 phải bị 401 Unauthorized do token_version cũ
    5. token_kho_2 phải truy cập bình thường (200 OK)
    6. token_admin_1 của tài khoản khác KHÔNG bị ảnh hưởng, vẫn truy cập bình thường (200 OK)
    7. Trả lại mật khẩu gốc cho user 'kho' để không ảnh hưởng dữ liệu mẫu
    """
    # 1. Đăng nhập 'kho'
    resp_kho_1 = client.post("/api/v1/auth/login", json={"username": "kho", "password": "123"})
    assert resp_kho_1.status_code == 200
    token_kho_1 = resp_kho_1.json()["access_token"]

    # 2. Đăng nhập 'admin'
    resp_admin_1 = client.post("/api/v1/auth/login", json={"username": "admin", "password": "123"})
    assert resp_admin_1.status_code == 200
    token_admin_1 = resp_admin_1.json()["access_token"]

    # Cả 2 đều truy cập được auth/me
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_kho_1}"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_admin_1}"}).status_code == 200

    # 3. User 'kho' thực hiện đổi mật khẩu
    change_resp = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token_kho_1}"},
        json={
            "current_password": "123",
            "new_password": "NewSecretPassword123!",
            "confirm_password": "NewSecretPassword123!"
        }
    )
    assert change_resp.status_code == 200
    token_kho_2 = change_resp.json()["access_token"]
    assert token_kho_2 != token_kho_1

    # 4. Token cũ của user 'kho' (token_kho_1) lập tức bị 401 Unauthorized
    resp_revoked = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_kho_1}"})
    assert resp_revoked.status_code == 401
    assert "thu hồi" in resp_revoked.json()["detail"].lower()

    # 5. Token mới của user 'kho' (token_kho_2) hoạt động bình thường
    resp_valid = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_kho_2}"})
    assert resp_valid.status_code == 200
    assert resp_valid.json()["username"] == "kho"

    # 6. QUAN TRỌNG: Token của user 'admin' (token_admin_1) KHÔNG bị văng / không bị ảnh hưởng!
    resp_admin_check = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_admin_1}"})
    assert resp_admin_check.status_code == 200
    assert resp_admin_check.json()["username"] == "admin"

    # 7. Trả lại mật khẩu cũ cho user 'kho' trực tiếp trong USERS_DB
    from app.models.user import USERS_DB, DEFAULT_HASH
    USERS_DB["kho"].hashed_password = DEFAULT_HASH
    USERS_DB["kho"].token_version = 1
