import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_dealer_search_and_filters():
    token = get_token("sales_manager")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test search without query (should return all)
    res = client.get("/api/v1/dealers/search", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1

    # 2. Test search with keyword
    res = client.get("/api/v1/dealers/search?keyword=Sao+Mai", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert any("Sao Mai" in item["name"] for item in data["items"])

    # 3. Test get filters
    res = client.get("/api/v1/dealers/filters", headers=headers)
    assert res.status_code == 200
    filters = res.json()
    assert "regions" in filters
    assert "sales" in filters

    # 4. Test unauthorized role (warehouse)
    kho_token = get_token("kho")
    kho_headers = {"Authorization": f"Bearer {kho_token}"}
    res = client.get("/api/v1/dealers/search", headers=kho_headers)
    assert res.status_code == 403
