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

def test_delete_existing_point_4_cleanup(client):
    db = SessionLocal()
    pt4 = db.get(DealerDeliveryPointEntity, 4)
    print("PT4 BEFORE:", pt4.id if pt4 else None, "dealer_id:", pt4.dealer_id if pt4 else None)
    db.close()
    if pt4 and pt4.is_active:
        del_res = client.delete(f"/api/v1/dealers/{pt4.dealer_id}/delivery-points/4")
        print("DEL_RES:", del_res.status_code, del_res.text)
        assert del_res.status_code in [204, 404]
        list_res = client.get(f"/api/v1/dealers/{pt4.dealer_id}/delivery-points")
        ids = [p["id"] for p in list_res.json()]
        print("IDS AFTER:", ids)
        db2 = SessionLocal()
        pt4_after = db2.get(DealerDeliveryPointEntity, 4)
        print("PT4 AFTER IN DB:", pt4_after.is_active if pt4_after else "NONE")
        assert pt4_after is None or not pt4_after.is_active
        db2.close()


