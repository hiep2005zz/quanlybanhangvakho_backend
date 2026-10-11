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
    from app.models.entities import WarehouseStockEntity
    from app.models.discount import DiscountPolicyEntity, DiscountTierEntity
    db = SessionLocal()
    try:
        stocks = db.query(WarehouseStockEntity).filter(WarehouseStockEntity.product_id == 1).all()
        for s in stocks:
            s.actual_stock = 1000.0
            s.reserved_stock = 0.0

        p = db.query(DiscountPolicyEntity).filter(DiscountPolicyEntity.code == "CK-SL-001").first()
        if not p:
            p = DiscountPolicyEntity(
                code="CK-SL-001",
                name="Chiết khấu sản lượng toàn hệ thống",
                title="Tất cả sản phẩm",
                category="ALL",
                target_dealer_type="ALL",
                target_group="all",
                is_active=True,
                status="active",
                start_date="2026-01-01",
                end_date="2026-12-31",
            )
            db.add(p)
            db.flush()
            t = DiscountTierEntity(policy_id=p.id, min_quantity=100, max_quantity=499, discount_percent=5.0)
            db.add(t)
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


def test_discount_policy_rbac_permissions():
    """
    Kiểm tra phân quyền RBAC:
    - Chỉ có admin và sales_manager mới được tạo, sửa, toggle, xóa chính sách chiết khấu.
    - Các vai trò khác (như sales, kho, ketoan) chỉ được xem (GET 200) và tính toán (calculate 200),
      nếu cố tình thao tác tạo/sửa/xóa sẽ bị chặn với mã 403 Forbidden.
    """
    sales_token = _login("sales", "123")
    sm_token = _login("sales_manager", "123")
    admin_token = _admin_login()

    # 1. Các vai trò khác (sales) CÓ THỂ XEM
    get_res = client.get("/api/v1/discounts", headers={"Authorization": f"Bearer {sales_token}"})
    assert get_res.status_code == 200

    # 2. Các vai trò khác (sales) CÓ THỂ TÍNH TOÁN
    calc_res = client.post(
        "/api/v1/discounts/calculate",
        json={"product_id": 1, "quantity": 10, "base_price": 50000.0},
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert calc_res.status_code == 200

    # 3. Các vai trò khác (sales) BỊ CHẶN (403) khi TẠO chính sách mới
    payload = {
        "code": "CK-FORBIDDEN-TEST",
        "name": "Chính sách thử quyền sales",
        "category": "ALL",
        "tiers": [{"min_quantity": 10, "discount_percent": 2.0}],
    }
    create_forbidden = client.post("/api/v1/discounts", json=payload, headers={"Authorization": f"Bearer {sales_token}"})
    assert create_forbidden.status_code == 403
    assert "Truy cập bị từ chối (403 Forbidden)" in create_forbidden.text

    # 4. Quản lý kinh doanh (sales_manager) CÓ THỂ TẠO chính sách mới (201)
    create_res = client.post("/api/v1/discounts", json=payload, headers={"Authorization": f"Bearer {sm_token}"})
    assert create_res.status_code == 201
    created_id = create_res.json()["id"]

    # 5. Các vai trò khác (sales) BỊ CHẶN (403) khi SỬA chính sách
    update_forbidden = client.put(
        f"/api/v1/discounts/{created_id}",
        json={"name": "Sửa bởi sales"},
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert update_forbidden.status_code == 403

    # 6. Các vai trò khác (sales) BỊ CHẶN (403) khi TOGGLE STATUS
    toggle_forbidden = client.patch(
        f"/api/v1/discounts/{created_id}/toggle-status",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert toggle_forbidden.status_code == 403

    # 7. Các vai trò khác (sales) BỊ CHẶN (403) khi XÓA chính sách
    delete_forbidden = client.delete(
        f"/api/v1/discounts/{created_id}",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert delete_forbidden.status_code == 403

    # 8. Quản lý kinh doanh (sales_manager) CÓ THỂ SỬA và TOGGLE STATUS
    update_res = client.put(
        f"/api/v1/discounts/{created_id}",
        json={"name": "Chính sách quản lý cập nhật"},
        headers={"Authorization": f"Bearer {sm_token}"}
    )
    assert update_res.status_code == 200

    toggle_res = client.patch(
        f"/api/v1/discounts/{created_id}/toggle-status",
        headers={"Authorization": f"Bearer {sm_token}"}
    )
    assert toggle_res.status_code == 200

    # 9. Admin (admin) CÓ THỂ XÓA chính sách
    del_res = client.delete(
        f"/api/v1/discounts/{created_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert del_res.status_code == 200


def test_category_discount_policy_application():
    """
    Kiểm tra chính sách chiết khấu áp dụng cho 1 nhóm hàng (Category):
    Tạo chính sách cho nhóm 'Áo Nam', khi đặt sản phẩm thuộc 'Áo Nam' (Product 1)
    với số lượng đủ bậc sẽ được hưởng chiết khấu chính sách.
    """
    admin_token = _admin_login()
    from app.core.database import SessionLocal
    from app.models.entities import ProductEntity
    _db = SessionLocal()
    p1 = _db.query(ProductEntity).filter(ProductEntity.id == 1).first()
    target_cat = p1.category if p1 and p1.category else "Thời trang"
    _db.close()

    payload = {
        "code": "CK-CAT-TEST",
        "name": f"Nhóm hàng: {target_cat}",
        "title": f"Nhóm hàng: {target_cat}",
        "category": target_cat,
        "target_dealer_type": "ALL",
        "is_active": True,
        "tiers": [
            {"min_quantity": 50, "max_quantity": 99, "discount_percent": 6.0},
            {"min_quantity": 100, "max_quantity": 499, "discount_percent": 12.0},
        ],
    }
    create_res = client.post("/api/v1/discounts", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_res.status_code == 201
    pol_id = create_res.json()["id"]

    try:
        # Product 1 (Áo thun Polo) thuộc Áo Nam
        calc_res = client.post(
            "/api/v1/discounts/calculate",
            json={"product_id": 1, "quantity": 105, "base_price": 100000.0},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert calc_res.status_code == 200
        data = calc_res.json()
        assert data["discount_percent"] == 12.0
        assert data["applied_policy_code"] == "CK-CAT-TEST"
    finally:
        client.delete(f"/api/v1/discounts/{pol_id}", headers={"Authorization": f"Bearer {admin_token}"})


def test_reject_overlapping_discount_tiers():
    """
    Kiểm tra hệ thống từ chối tạo chính sách khi các mốc sản lượng bị chồng chéo (overlap).
    Ví dụ: Mốc 1: 10-30 sp, Mốc 2: 25-40 sp -> phải trả về lỗi 400.
    """
    admin_token = _admin_login()
    payload = {
        "code": "CK-OVERLAP-TEST",
        "name": "Test chồng chéo số lượng",
        "title": "Test chồng chéo số lượng",
        "tiers": [
            {"min_quantity": 10, "max_quantity": 30, "discount_percent": 2.5},
            {"min_quantity": 25, "max_quantity": 40, "discount_percent": 5.0},
        ],
    }
    create_res = client.post("/api/v1/discounts", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_res.status_code == 400
    assert "chồng chéo số lượng" in create_res.json()["detail"]


def test_fixed_amount_per_unit_discount():
    """
    Kiểm tra chiết khấu tính theo số tiền trên đơn vị (đ/sản phẩm):
    Ví dụ: Mua từ 50 sản phẩm trở lên, giảm 10.000 đ/sp.
    """
    admin_token = _admin_login()
    payload = {
        "code": "CK-AMT-UNIT-01",
        "name": "Chiết khấu 10.000đ mỗi đơn vị",
        "title": "Tất cả sản phẩm",
        "category": "ALL",
        "is_active": True,
        "tiers": [
            {
                "min_quantity": 50,
                "max_quantity": 99,
                "discount_type": "amount",
                "discount_amount": 10000.0,
                "discount_percent": 0.0,
            },
            {
                "min_quantity": 100,
                "max_quantity": None,
                "discount_type": "amount",
                "discount_amount": 20000.0,
                "discount_percent": 0.0,
            },
        ],
    }
    create_res = client.post("/api/v1/discounts", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_res.status_code == 201, create_res.text
    pol_id = create_res.json()["id"]

    try:
        calc_res = client.post(
            "/api/v1/discounts/calculate",
            json={"product_id": 1, "quantity": 60, "base_price": 100000.0},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert calc_res.status_code == 200
        data = calc_res.json()
        assert data["applied_policy_code"] == "CK-AMT-UNIT-01"
        assert data["total_discount_amount"] == 60 * 10000.0
        assert data["unit_discount_amount"] == 10000.0
        assert data["final_total_amount"] == 60 * 100000.0 - 60 * 10000.0
    finally:
        client.delete(f"/api/v1/discounts/{pol_id}", headers={"Authorization": f"Bearer {admin_token}"})


def test_best_discount_rule_highest_benefit():
    """
    Quy tắc nghiệp vụ: Khi nhiều chính sách cùng áp dụng, hệ thống tự động
    chọn chính sách có lợi nhất cho khách hàng (mức tiền chiết khấu cao nhất).
    - Chính sách A: Giảm 5% (100 sp x 100.000đ x 5% = 500.000đ)
    - Chính sách B: Giảm 15.000đ/sp (100 sp x 15.000đ = 1.500.000đ)
    Hệ thống sẽ tự động chọn Chính sách B vì đem lại 1.500.000đ giảm giá cho khách.
    """
    admin_token = _admin_login()
    payload_b = {
        "code": "CK-BEST-RULE-B",
        "name": "Chính sách ưu đãi đặc biệt 15.000đ/sp",
        "title": "Tất cả sản phẩm",
        "category": "ALL",
        "is_active": True,
        "tiers": [
            {
                "min_quantity": 100,
                "max_quantity": None,
                "discount_type": "amount",
                "discount_amount": 15000.0,
                "discount_percent": 0.0,
            }
        ],
    }
    create_b = client.post("/api/v1/discounts", json=payload_b, headers={"Authorization": f"Bearer {admin_token}"})
    assert create_b.status_code == 201
    pol_id_b = create_b.json()["id"]

    try:
        calc_res = client.post(
            "/api/v1/discounts/calculate",
            json={"product_id": 1, "quantity": 100, "base_price": 100000.0},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert calc_res.status_code == 200
        data = calc_res.json()
        # So sánh giữa CK-SL-001 (5% = 500.000đ) và CK-BEST-RULE-B (1.500.000đ) -> Phải chọn CK-BEST-RULE-B
        assert data["applied_policy_code"] == "CK-BEST-RULE-B"
        assert data["total_discount_amount"] == 1500000.0
    finally:
        client.delete(f"/api/v1/discounts/{pol_id_b}", headers={"Authorization": f"Bearer {admin_token}"})





