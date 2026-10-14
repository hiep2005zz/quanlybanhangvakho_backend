import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token

client = TestClient(app)

@pytest.fixture
def admin_token():
    return create_access_token("admin", "admin")

def test_get_products_pagination_page_size_10(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # 1. Fetch initial 10 products
    res_page_1 = client.get("/api/v1/products?page=1&page_size=10", headers=headers)
    assert res_page_1.status_code == 200
    data_1 = res_page_1.json()
    
    assert "items" in data_1
    assert "total" in data_1
    assert len(data_1["items"]) <= 10
    assert data_1["page"] == 1
    assert data_1["page_size"] == 10
    
    total = data_1["total"]
    if total > 10:
        assert data_1["has_more"] is True
        assert len(data_1["items"]) == 10
        
        # 2. Fetch next 10 products
        res_page_2 = client.get("/api/v1/products?page=2&page_size=10", headers=headers)
        assert res_page_2.status_code == 200
        data_2 = res_page_2.json()
        assert len(data_2["items"]) <= 10
        assert data_2["page"] == 2
        
        # Verify no overlap between page 1 and page 2
        ids_1 = {p["id"] for p in data_1["items"]}
        ids_2 = {p["id"] for p in data_2["items"]}
        assert len(ids_1.intersection(ids_2)) == 0

def test_get_products_pagination_with_skip(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    res = client.get("/api/v1/products?skip=0&limit=10", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) <= 10
    assert data["page"] == 1

def test_get_products_pagination_search(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Search for products with 'SP' or common term
    res = client.get("/api/v1/products?page=1&page_size=10&search=SP", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) <= 10
    for p in data["items"]:
        match = "sp" in p["code"].lower() or "sp" in p["name"].lower()
        assert match
