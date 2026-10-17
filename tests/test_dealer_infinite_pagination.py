import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token

client = TestClient(app)

@pytest.fixture
def admin_token():
    return create_access_token("admin", "admin")

def test_get_dealers_unpaginated_backward_compatibility(admin_token):
    """GET /dealers without parameters returns raw list of dealers."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/api/v1/dealers", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0

def test_get_dealers_pagination_page_10(admin_token):
    """GET /dealers?page=1&page_size=10 returns paginated dict with items and has_more."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/api/v1/dealers?page=1&page_size=10", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "has_more" in data
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["items"]) <= 10

    total = data["total"]
    if total > 10:
        assert data["has_more"] is True
        assert len(data["items"]) == 10

        # Page 2 test
        res_page_2 = client.get("/api/v1/dealers?page=2&page_size=10", headers=headers)
        assert res_page_2.status_code == 200
        data_2 = res_page_2.json()
        assert data_2["page"] == 2
        assert len(data_2["items"]) <= 10

        # No overlapping IDs
        ids_1 = {d["id"] for d in data["items"]}
        ids_2 = {d["id"] for d in data_2["items"]}
        assert len(ids_1.intersection(ids_2)) == 0

def test_get_dealers_pagination_with_search(admin_token):
    """GET /dealers?page=1&page_size=10&search=... filters by keyword."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/api/v1/dealers?page=1&page_size=10&search=DL", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    for item in data["items"]:
        match = "dl" in (item["code"] or "").lower() or "dl" in (item["name"] or "").lower()
        assert match
