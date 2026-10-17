import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import UserEntity
from app.core.security import create_access_token

client = TestClient(app)


@pytest.fixture
def admin_user():
    db = SessionLocal()
    user = db.query(UserEntity).filter(UserEntity.username == "admin").first()
    db.close()
    return user


@pytest.fixture
def admin_headers(admin_user):
    token = create_access_token(admin_user.username, admin_user.role, token_version=admin_user.token_version)
    return {"Authorization": f"Bearer {token}"}


def test_payment_collections_pagination_backend(admin_headers):
    """
    Kiểm tra phân trang 10 dòng/trang cho cả danh sách khoản thu và bảng đối chiếu hàng ngày.
    """
    # 1. Kiểm tra endpoint danh sách khoản thu
    res = client.get("/api/v1/payment-collections?page=1&limit=10", headers=admin_headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "page" in data
    assert "page_size" in data
    assert "total_pages" in data
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["items"]) <= 10

    # 2. Kiểm tra endpoint bảng đối chiếu hàng ngày
    res_rec = client.get("/api/v1/payment-collections/daily-reconciliation?page=1&page_size=10", headers=admin_headers)
    assert res_rec.status_code == 200, res_rec.text
    rec_data = res_rec.json()
    assert "page" in rec_data
    assert "page_size" in rec_data
    assert "total_pages" in rec_data
    assert "total" in rec_data
    assert rec_data["page"] == 1
    assert rec_data["page_size"] == 10
    assert len(rec_data["items"]) <= 10
