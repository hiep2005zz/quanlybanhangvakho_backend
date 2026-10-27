import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import UserEntity
from app.core.security import create_access_token

client = TestClient(app)

@pytest.fixture
def warehouse_headers():
    db = SessionLocal()
    user = db.query(UserEntity).filter(UserEntity.username.in_(["admin", "kho", "warehouse"])).first()
    if not user:
        user = db.query(UserEntity).first()
    db.close()
    token = create_access_token(user.username, "admin", token_version=getattr(user, "token_version", 1))
    return {"Authorization": f"Bearer {token}"}

def test_eligible_picking_orders_pagination(warehouse_headers):
    # 1. Test page 1 with page_size=10
    resp1 = client.get("/api/v1/warehouses/picking/eligible-orders?page=1&page_size=10", headers=warehouse_headers)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert "pagination" in data1
    assert "items" in data1
    assert data1["pagination"]["page"] == 1
    assert data1["pagination"]["page_size"] == 10
    total_records = data1["pagination"]["total_records"]
    assert len(data1["items"]) <= 10
    if total_records > 0:
        assert len(data1["items"]) > 0

    # 2. If there are > 10 records, test page 2
    if total_records > 10:
        resp2 = client.get("/api/v1/warehouses/picking/eligible-orders?page=2&page_size=10", headers=warehouse_headers)
        assert resp2.status_code == 200, resp2.text
        data2 = resp2.json()
        assert data2["pagination"]["page"] == 2
        # Items on page 2 should not overlap with page 1
        codes_p1 = {it["order_code"] for it in data1["items"]}
        codes_p2 = {it["order_code"] for it in data2["items"]}
        assert codes_p1.isdisjoint(codes_p2), "Page 1 and Page 2 must not have overlapping orders"

    # 3. Test backward compatibility: unpaginated request without page parameter returns a list
    resp_unpaged = client.get("/api/v1/warehouses/picking/eligible-orders", headers=warehouse_headers)
    assert resp_unpaged.status_code == 200, resp_unpaged.text
    unpaged_data = resp_unpaged.json()
    assert isinstance(unpaged_data, list), "Without page param, response must be raw list"
    assert len(unpaged_data) == total_records
