# backend/tests/test_profile.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def restore_seed_users():
    from app.models.user import USERS_DB, save_users_db
    if "admin" in USERS_DB:
        USERS_DB["admin"].phone = "0822516998"
    if "sales_manager" in USERS_DB:
        USERS_DB["sales_manager"].phone = "0998473998"
    yield
    if "admin" in USERS_DB:
        USERS_DB["admin"].phone = "0822516998"
    if "sales_manager" in USERS_DB:
        USERS_DB["sales_manager"].phone = "0998473998"
    if "sales" in USERS_DB:
        USERS_DB["sales"].full_name = "Trần Bán Hàng"
        USERS_DB["sales"].phone = None
    if "kho" in USERS_DB:
        USERS_DB["kho"].full_name = "Lê Thủ Kho"
        USERS_DB["kho"].phone = None
    save_users_db()

def _login(username: str = "sales", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_get_profile_unauthorized():
    res = client.get("/api/v1/me")
    assert res.status_code == 401

    res_profile = client.get("/api/v1/profile")
    assert res_profile.status_code == 401


def test_get_profile_me():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}
    
    res = client.get("/api/v1/me", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "sales"
    assert "full_name" in data
    assert "role" in data
    assert "warehouse_name" in data
    assert "territory_name" in data


def test_get_profile_alias():
    token = _login("sales_manager", "123")
    headers = {"Authorization": f"Bearer {token}"}
    
    res = client.get("/api/v1/profile", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "sales_manager"
    assert data["role"] == "sales_manager"


def test_update_profile_valid():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "full_name": "Trần Bán Hàng Mới",
        "phone_number": "0971112233",
    }
    res = client.put("/api/v1/me", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["full_name"] == "Trần Bán Hàng Mới"
    assert data["phone_number"] == "0971112233"

    # GET lại để kiểm tra tính bền vững
    get_res = client.get("/api/v1/me", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["full_name"] == "Trần Bán Hàng Mới"
    assert get_res.json()["phone_number"] == "0971112233"


def test_update_profile_patch_alias():
    token = _login("kho", "123")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "full_name": "Lê Thủ Kho Chi Nhánh",
        "phone_number": "0389123456",
    }
    res = client.patch("/api/v1/profile", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["full_name"] == "Lê Thủ Kho Chi Nhánh"
    assert data["phone_number"] == "0389123456"


def test_update_profile_invalid_phone():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    # Đầu số không hợp lệ (01...)
    res1 = client.put("/api/v1/me", json={"full_name": "Test", "phone_number": "0123456789"}, headers=headers)
    assert res1.status_code in [400, 422]

    # Thiếu chữ số (9 số)
    res2 = client.put("/api/v1/me", json={"full_name": "Test", "phone_number": "098765432"}, headers=headers)
    assert res2.status_code in [400, 422]

    # Thừa chữ số (11 số)
    res3 = client.put("/api/v1/me", json={"full_name": "Test", "phone_number": "09876543210"}, headers=headers)
    assert res3.status_code in [400, 422]

    # Ký tự chữ
    res4 = client.put("/api/v1/me", json={"full_name": "Test", "phone_number": "09876abcde"}, headers=headers)
    assert res4.status_code in [400, 422]


def test_update_profile_empty_name():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.put("/api/v1/me", json={"full_name": "   ", "phone_number": "0971112233"}, headers=headers)
    assert res.status_code in [400, 422]


def test_update_profile_duplicate_phone_rejected():
    """Kiểm tra số điện thoại bị trùng với tài khoản khác thì không cho lưu và trả về 409."""
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    # Thử đổi sang số điện thoại của admin (0822516998)
    res = client.put(
        "/api/v1/me",
        json={"full_name": "Trần Bán Hàng", "phone_number": "0822516998"},
        headers=headers
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "Số điện thoại này đã có trên hệ thống vui lòng đổi số khác"

    # Thử đổi sang số điện thoại của sales_manager (0998473998)
    res2 = client.put(
        "/api/v1/me",
        json={"full_name": "Trần Bán Hàng", "phone_number": "0998473998"},
        headers=headers
    )
    assert res2.status_code == 409
    assert res2.json()["detail"] == "Số điện thoại này đã có trên hệ thống vui lòng đổi số khác"


def test_update_profile_same_user_phone_allowed():
    """Tài khoản tự cập nhật lại chính số điện thoại của mình thì được phép (không bị báo trùng)."""
    token = _login("admin", "123")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.put(
        "/api/v1/me",
        json={"full_name": "Đào Ngọc Hiệp", "phone_number": "0822516998"},
        headers=headers
    )
    assert res.status_code == 200
    assert res.json()["phone_number"] == "0822516998"


def test_update_profile_security_fields_ignored():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    # Cố tình gửi role admin, username khác, email khác
    malicious_payload = {
        "full_name": "Trần Bán Hàng An Toàn",
        "phone_number": "0972223344",
        "role": "admin",
        "roles": ["admin"],
        "username": "superadmin",
        "email": "hacked@domain.com",
        "warehouse_id": 999,
        "territory_id": 888,
    }
    res = client.put("/api/v1/me", json=malicious_payload, headers=headers)
    assert res.status_code == 200
    data = res.json()

    # role và username tuyệt đối KHÔNG được đổi
    assert data["role"] == "sales"
    assert data["username"] == "sales"
    assert data["full_name"] == "Trần Bán Hàng An Toàn"
    assert data["phone_number"] == "0972223344"


def test_all_seven_roles_can_access_profile():
    roles_users = ["admin", "sales_manager", "sales", "kho", "warehouse_mgr", "ketoan", "muahang"]
    for u in roles_users:
        token = _login(u, "123")
        headers = {"Authorization": f"Bearer {token}"}
        res = client.get("/api/v1/me", headers=headers)
        assert res.status_code == 200, f"Role user {u} cannot access /api/v1/me"
        data = res.json()
        assert data["username"] == u
        assert "role_title" in data


def test_upload_avatar_success():
    import io
    from PIL import Image

    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    # Tạo 1 ảnh hợp lệ kích thước chữ nhật 600x400
    img = Image.new("RGB", (600, 400), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    files = {"file": ("test_avatar.jpg", img_bytes, "image/jpeg")}
    res = client.post("/api/v1/me/avatar", files=files, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "avatar_url" in data
    assert "thumbnail_url" in data
    assert data["avatar_url"].endswith(".png")
    assert data["thumbnail_url"].endswith(".png")

    # Kiểm tra GET /api/v1/me đã có avatar_url
    res_me = client.get("/api/v1/me", headers=headers)
    assert res_me.status_code == 200
    assert res_me.json()["avatar_url"] == data["avatar_url"]


def test_upload_avatar_invalid_extension():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": ("document.pdf", b"%PDF-1.4...", "application/pdf")}
    res = client.post("/api/v1/me/avatar", files=files, headers=headers)
    assert res.status_code == 400
    assert "chỉ chấp nhận ảnh định dạng jpg hoặc png" in res.json()["detail"].lower()



def test_upload_avatar_oversized():
    token = _login("sales", "123")
    headers = {"Authorization": f"Bearer {token}"}

    # Giả lập file lớn hơn 2MB (2.5MB)
    oversized_data = b"X" * (int(2.5 * 1024 * 1024))
    files = {"file": ("big_image.jpg", oversized_data, "image/jpeg")}
    res = client.post("/api/v1/me/avatar", files=files, headers=headers)
    assert res.status_code == 400
    assert "vượt quá giới hạn cho phép (tối đa 2mb)" in res.json()["detail"].lower()


