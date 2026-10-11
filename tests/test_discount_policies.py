import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.v1.endpoints.discounts import _POLICIES_STORE, load_discount_policies

client = TestClient(app)

def _login(username: str = "sales", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]

def _admin_login() -> str:
    res = client.post("/api/v1/auth/login", json={"username": "admin", "password": "123"})
    assert res.status_code == 200
    return res.json()["access_token"]

@pytest.fixture(autouse=True)
def reset_test_stock():
    from app.core.database import SessionLocal
    from app.models.entities import WarehouseStockEntity, ProductEntity
    db = SessionLocal()
    try:
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == 1
        ).first()
        if not ws:
            ws = WarehouseStockEntity(
                warehouse_id="WH01",
                warehouse_name="Kho Tổng Hà Nội",
                product_id=1,
                actual_stock=10000.0,
                reserved_stock=0.0
            )
            db.add(ws)
        else:
            ws.actual_stock = max(float(ws.actual_stock or 0), 10000.0)
            ws.reserved_stock = 0.0

        stocks = db.query(WarehouseStockEntity).filter(WarehouseStockEntity.product_id == 1).all()
        for s in stocks:
            s.actual_stock = max(float(s.actual_stock or 0), 10000.0)
            s.reserved_stock = 0.0

        p = db.query(ProductEntity).filter(ProductEntity.id == 1).first()
        if p:
            p.stock = max(float(p.stock or 0), 10000.0)
        db.commit()
    finally:
        db.close()
    yield

def test_get_discount_policies():
    token = _login()
    res = client.get("/api/v1/discounts", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert any(p["code"] == "CK-SL-001" for p in data["items"])

def test_calculate_discount_api_101_qty():
    token = _login()
    res = client.post(
        "/api/v1/discounts/calculate",
        json={"product_id": 1, "quantity": 101, "base_price": 100000.0},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["discount_percent"] == 5.0
    assert data["applied_policy_code"] == "CK-SL-001"
    assert data["total_discount_amount"] == round(101 * 100000.0 * 0.05, 2)
    assert data["final_total_amount"] == 101 * 100000.0 - data["total_discount_amount"]

def test_auto_discount_on_create_order_101_qty():
    """
    Khi tạo đơn hàng với 101 sản phẩm và discount_percent = 0,
    Backend phải tự động tính và áp dụng 5% chiết khấu từ CK-SL-001.
    """
    token = _login("sales", "123")
    order_payload = {
        "dealer_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 101,
                "price": 200000.0,
                "unit": "Cái",
                "conversion_rate": 1.0
            }
        ],
        "discount_percent": 0.0,
        "note": "Đơn hàng thử nghiệm chiết khấu tự động sản lượng 101 sp",
    }
    res = client.post("/api/v1/orders", json=order_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["discount_percent"] == 5.0
    assert order["discount_amount"] == round(101 * 200000.0 * 0.05, 2)
    expected_total = 101 * 200000.0 - order["discount_amount"]
    assert order["total_amount"] == expected_total

def test_manual_override_discount_on_create_order():
    """
    Ràng buộc can thiệp thủ công: Nếu người dùng nhập 8%, hệ thống tôn trọng giá trị 8%
    và nếu vượt mức chính sách (5%), đơn hàng được chuyển sang PENDING_APPROVAL.
    """
    token = _login("sales", "123")
    order_payload = {
        "dealer_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 101,
                "price": 200000.0,
                "unit": "Cái",
                "conversion_rate": 1.0
            }
        ],
        "discount_percent": 8.0,
        "note": "Đơn hàng can thiệp thủ công chiết khấu 8%",
    }
    res = client.post("/api/v1/orders", json=order_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    order = res.json()
    assert order["discount_percent"] == 8.0
    assert order["discount_amount"] == round(101 * 200000.0 * 0.08, 2)
    assert order["requires_approval"] is True
    assert "vượt mức chính sách" in (order["approval_reason"] or "")

def test_sales_entry_order_auto_discount_101_qty():
    """
    Kiểm tra tạo đơn qua màn hình Sales Order Entry (/sales-entry) với 101 sản phẩm.
    """
    token = _login("sales", "123")
    order_payload = {
        "dealer_id": 1,
        "delivery_point": "Kho Sao Mai, Hà Nội",
        "desired_delivery_date": "2026-12-31",
        "discount_percent": 0.0,
        "items": [
            {
                "product_id": 1,
                "quantity": 101,
                "price": 200000.0,
                "unit": "Cái",
                "conversion_rate": 1.0
            }
        ],
        "note": "Đơn sales entry 101 sp",
    }
    res = client.post("/api/v1/orders/sales-entry", json=order_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201, res.text
    order = res.json()
    assert order["discount_percent"] == 5.0
    expected_discount = round(order["subtotal_amount"] * 0.05, 2)
    assert order["discount_amount"] == expected_discount
    assert order["total_amount"] == order["subtotal_amount"] - expected_discount

def test_crud_discount_policy_database_persistence():
    """
    Kiểm tra toàn diện CRUD và lưu trữ vào Cơ sở dữ liệu:
    1. Tạo chính sách mới với các bậc chiết khấu
    2. Xác thực lưu trực tiếp trong DB (DiscountPolicyEntity, DiscountTierEntity)
    3. Cập nhật chính sách
    4. Bật / tắt trạng thái (toggle-status)
    5. Xóa chính sách (cascade xóa các bậc liên kết)
    """
    from app.core.database import SessionLocal
    from app.models.discount import DiscountPolicyEntity, DiscountTierEntity

    token = _admin_login()

    # 1. CREATE
    payload = {
        "code": "CK-TEST-DB-01",
        "name": "Chính sách kiểm thử DB",
        "title": "Chính sách kiểm thử DB",
        "category": "Thời trang",
        "target_dealer_type": "ALL",
        "target_group": "all",
        "description": "Chính sách lưu trực tiếp vào CSDL",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "is_active": True,
        "tiers": [
            {"min_quantity": 50, "max_quantity": 99, "discount_percent": 3.0},
            {"min_quantity": 100, "max_quantity": 199, "discount_percent": 7.0},
        ],
    }
    res = client.post("/api/v1/discounts", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201, res.text
    created = res.json()
    policy_id = created["id"]
    assert created["code"] == "CK-TEST-DB-01"
    assert len(created["tiers"]) == 2

    # 2. XÁC THỰC TRỰC TIẾP TRONG DB
    db = SessionLocal()
    try:
        db_entity = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
        assert db_entity is not None
        assert db_entity.name == "Chính sách kiểm thử DB"
        assert db_entity.category == "Thời trang"
        assert len(db_entity.tiers) == 2
        assert db_entity.tiers[0].min_quantity == 50
        assert db_entity.tiers[0].discount_percent == 3.0
    finally:
        db.close()

    # 3. UPDATE
    update_payload = {
        "name": "Chính sách kiểm thử DB đã cập nhật",
        "category": "Thời trang",
        "tiers": [
            {"min_quantity": 30, "max_quantity": 80, "discount_percent": 4.0},
        ],
    }
    res_update = client.put(f"/api/v1/discounts/{policy_id}", json=update_payload, headers={"Authorization": f"Bearer {token}"})
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["name"] == "Chính sách kiểm thử DB đã cập nhật"
    assert len(updated["tiers"]) == 1

    # Kiểm tra DB sau update
    db = SessionLocal()
    try:
        db_entity = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
        assert db_entity.name == "Chính sách kiểm thử DB đã cập nhật"
        assert len(db_entity.tiers) == 1
        assert db_entity.tiers[0].discount_percent == 4.0
    finally:
        db.close()

    # 4. TOGGLE STATUS
    res_toggle = client.patch(f"/api/v1/discounts/{policy_id}/toggle-status", headers={"Authorization": f"Bearer {token}"})
    assert res_toggle.status_code == 200
    assert res_toggle.json()["is_active"] is False

    db = SessionLocal()
    try:
        db_entity = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
        assert db_entity.is_active is False
    finally:
        db.close()

    # 5. DELETE
    res_delete = client.delete(f"/api/v1/discounts/{policy_id}", headers={"Authorization": f"Bearer {token}"})
    assert res_delete.status_code == 200

    db = SessionLocal()
    try:
        db_entity = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.id == policy_id).first()
        assert db_entity is None
        # Đảm bảo tiers bị cascade xóa
        tiers = db.query(DiscountTierEntity).filter(DiscountTierEntity.policy_id == policy_id).all()
        assert len(tiers) == 0
    finally:
        db.close()

