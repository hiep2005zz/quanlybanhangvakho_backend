import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import DealerDeliveryPointEntity, MasterDeliveryPointEntity
from app.api.deps import get_current_user

class DummyUser:
    id = 1
    username = "admin"
    roles = ["admin"]
    role = "admin"
    status = "active"

@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_current_user, None)

def test_delete_dealer_delivery_point_persists(client):
    # 1. Tạo một điểm giao mới cho đại lý 1
    create_res = client.post(
        "/api/v1/dealers/1/delivery-points",
        json={
            "label": "Điểm giao test xóa",
            "address": "123 Đường Test, Hà Nội",
            "receiver_name": "Người Nhận Test",
            "receiver_phone": "0912345678",
            "route_note": "Ghi chú test",
            "is_default": False
        }
    )
    assert create_res.status_code == 201
    created_id = create_res.json()["id"]

    # 2. Kiểm tra xem điểm giao đã xuất hiện trong danh sách chưa
    list_res = client.get("/api/v1/dealers/1/delivery-points")
    assert list_res.status_code == 200
    ids = [p["id"] for p in list_res.json()]
    assert created_id in ids

    # 3. Gửi lệnh xóa (DELETE)
    del_res = client.delete(f"/api/v1/dealers/1/delivery-points/{created_id}")
    assert del_res.status_code == 204

    # 4. Kiểm tra lại GET: Điểm giao không còn xuất hiện trong danh sách đại lý
    list_res_after = client.get("/api/v1/dealers/1/delivery-points")
    assert list_res_after.status_code == 200
    ids_after = [p["id"] for p in list_res_after.json()]
    assert created_id not in ids_after, "Điểm giao đã xóa nhưng vẫn còn trong danh sách!"

    # 5. Kiểm tra trực tiếp trong DB: Entity đã bị xóa hoặc is_active = False
    db = SessionLocal()
    entity = db.get(DealerDeliveryPointEntity, created_id)
    assert entity is None or not entity.is_active, "Entity trong database không được xóa/vô hiệu hóa!"
    db.close()

def test_delete_master_delivery_point_does_not_resurrect(client):
    # 1. Tạo điểm giao trong danh mục chung
    create_res = client.post(
        "/api/v1/dealers/master-delivery-points",
        json={
            "label": "Master Point Test",
            "address": "456 Phố Cổ, Hà Nội",
            "receiver_name": "Master Receiver",
            "receiver_phone": "0987654321",
            "route_note": "Ghi chú master",
            "is_default": False
        }
    )
    assert create_res.status_code == 201
    created_master_id = create_res.json()["id"]

    # 2. Xóa khỏi danh mục chung
    del_res = client.delete(f"/api/v1/dealers/master-delivery-points/{created_master_id}")
    assert del_res.status_code == 204

    # 3. Gọi list-all-delivery-points nhiều lần: Không được tự động kích hoạt lại
    all_res = client.get("/api/v1/dealers/all-delivery-points")
    assert all_res.status_code == 200
    m_ids = [p["id"] for p in all_res.json()]
    assert created_master_id not in m_ids, "Điểm giao chung đã xóa nhưng vẫn bị tái sinh khi GET all-delivery-points!"

def test_cannot_delete_default_delivery_point(client):
    # 1. Điểm giao mặc định của đại lý không thể xóa -> trả về 400
    res_list = client.get("/api/v1/dealers/1/delivery-points")
    assert res_list.status_code == 200
    pts = res_list.json()
    default_pt = next((p for p in pts if p.get("is_default")), None)
    
    # Nếu chưa có điểm mặc định, tạo 1 điểm làm mặc định
    if not default_pt:
        c_res = client.post(
            "/api/v1/dealers/1/delivery-points",
            json={
                "label": "Kho mặc định test",
                "address": "999 Đường Mặc Định, Hà Nội",
                "receiver_name": "Người nhận 1",
                "receiver_phone": "0912345678",
                "is_default": True
            }
        )
        assert c_res.status_code == 201
        default_pt = c_res.json()

    # Thử xóa điểm mặc định -> phải trả về 400
    del_res = client.delete(f"/api/v1/dealers/1/delivery-points/{default_pt['id']}")
    assert del_res.status_code == 400
    assert "mặc định" in del_res.json()["detail"].lower()

    # Tạo điểm thứ 2 và gán làm mặc định mới
    c2_res = client.post(
        "/api/v1/dealers/1/delivery-points",
        json={
            "label": "Kho thay thế mặc định",
            "address": "888 Đường Mới, Hà Nội",
            "receiver_name": "Người nhận 2",
            "receiver_phone": "0987654321",
            "is_default": False
        }
    )
    assert c2_res.status_code == 201
    pt2_id = c2_res.json()["id"]

    set_def_res = client.post(f"/api/v1/dealers/1/delivery-points/{pt2_id}/set-default")
    assert set_def_res.status_code == 200

    # Lúc này điểm cũ không còn là mặc định -> có thể xóa thành công
    del_old_res = client.delete(f"/api/v1/dealers/1/delivery-points/{default_pt['id']}")
    assert del_old_res.status_code == 204



