# backend/tests/test_reorder.py
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import ProductEntity, DealerEntity, OrderEntity
from app.models.dealer import DEALERS_DB
from app.api.v1.endpoints.orders import ORDERS_DB
from app.api.v1.endpoints.products import RAW_PRODUCTS

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def setup_test_data():
    db = SessionLocal()
    try:
        # Đảm bảo SP1, SP2, SP3, SP4 tồn tại trong SQLite DB
        # SP1: active, sell_price = 199.000
        p1 = db.query(ProductEntity).filter(ProductEntity.id == 1).first()
        if not p1:
            p1 = ProductEntity(id=1, code="SP001", name="Áo thun Polo Nam Cao Cấp", status="active", sell_price=199000.0, stock=100)
            db.add(p1)
        else:
            p1.status = "active"
            p1.sell_price = 199000.0

        # SP2: active, sell_price = 380.000
        p2 = db.query(ProductEntity).filter(ProductEntity.id == 2).first()
        if not p2:
            p2 = ProductEntity(id=2, code="SP002", name="Quần Jeans Slimfit Co Giãn", status="active", sell_price=380000.0, stock=50)
            db.add(p2)
        else:
            p2.status = "active"
            p2.sell_price = 380000.0

        # SP3: active, sell_price = 490.000
        p3 = db.query(ProductEntity).filter(ProductEntity.id == 3).first()
        if not p3:
            p3 = ProductEntity(id=3, code="SP003", name="Áo khoác Bomber Chống Nước", status="active", sell_price=490000.0, stock=40)
            db.add(p3)
        else:
            p3.status = "active"
            p3.sell_price = 490000.0

        # SP4: active, sell_price = 650.000
        p4 = db.query(ProductEntity).filter(ProductEntity.id == 4).first()
        if not p4:
            p4 = ProductEntity(id=4, code="SP004", name="Giày Sneaker Thể Thao", status="active", sell_price=650000.0, stock=60)
            db.add(p4)
        else:
            p4.status = "active"
            p4.sell_price = 650000.0

        for p in RAW_PRODUCTS:
            if p.get("id") in [1, 2, 3, 4]:
                p["status"] = "active"

        # Đảm bảo Đại lý 1 và Đại lý 2 tồn tại
        d1 = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if not d1:
            d1 = DealerEntity(id=1, code="DL001", name="Đại Lý Phân Phối Miền Bắc - Sao Mai", customer_group="Dai_ly_cap_1", status="Đang hoạt động")
            db.add(d1)
        else:
            d1.status = "Đang hoạt động"

        d2 = db.query(DealerEntity).filter(DealerEntity.id == 2).first()
        if not d2:
            d2 = DealerEntity(id=2, code="DL002", name="Đại Lý Thời Trang Tân Bình", customer_group="Dai_ly_cap_2", status="Đang hoạt động")
            db.add(d2)
        else:
            d2.status = "Đang hoạt động"

        # Đảm bảo tồn kho WH01 dồi dào để không bị chặn bởi tồn giữ chỗ của các đơn test khác
        from app.models.entities import WarehouseStockEntity
        for pid in [1, 2, 3, 4]:
            ws = db.query(WarehouseStockEntity).filter(
                WarehouseStockEntity.warehouse_id == "WH01",
                WarehouseStockEntity.product_id == pid
            ).first()
            if not ws:
                ws = WarehouseStockEntity(warehouse_id="WH01", warehouse_name="Kho Tổng Hà Nội", product_id=pid, actual_stock=1000)
                db.add(ws)
            else:
                ws.actual_stock = 1000

        db.commit()

        # Đồng bộ RAW_PRODUCTS
        for p in RAW_PRODUCTS:
            if p["id"] in [1, 2, 3, 4]:
                p["status"] = "active"
    finally:
        db.close()


def test_reorder_case_1_all_active():
    """Case 1: Đơn cũ có 3 sản phẩm, cả 3 còn kinh doanh => Đặt lại đủ 3 sản phẩm."""
    setup_test_data()
    admin_token = get_token("admin")

    # Tạo đơn cũ có 3 SP: 1, 2, 3 với số lượng tương ứng 2, 5, 1
    old_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 2, "price": 180000.0},
                {"product_id": 2, "quantity": 5, "price": 350000.0},
                {"product_id": 3, "quantity": 1, "price": 450000.0},
            ],
            "note": "Đơn cũ test case 1"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert old_order_res.status_code == 201
    old_order = old_order_res.json()
    order_code = old_order["order_code"]

    # Gọi API reorder
    reorder_res = client.post(
        f"/api/v1/orders/{order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is True
    assert data["total_valid"] == 3
    assert data["total_excluded"] == 0
    assert len(data["valid_items"]) == 3
    assert len(data["excluded_items"]) == 0

    # Kiểm tra số lượng giữ nguyên
    item_map = {it["product_id"]: it for it in data["valid_items"]}
    assert item_map[1]["quantity"] == 2
    assert item_map[2]["quantity"] == 5
    assert item_map[3]["quantity"] == 1


def test_reorder_case_2_one_inactive():
    """Case 2: Đơn cũ có 3 sản phẩm, 1 sản phẩm đã ngừng kinh doanh => Đặt lại 2 sản phẩm còn lại + thông báo sản phẩm bị loại."""
    setup_test_data()
    admin_token = get_token("admin")

    # Tạo đơn cũ có SP 1, 2, 3
    old_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 3, "price": 190000.0},
                {"product_id": 2, "quantity": 4, "price": 370000.0},
                {"product_id": 3, "quantity": 2, "price": 480000.0},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert old_order_res.status_code == 201
    order_code = old_order_res.json()["order_code"]

    # Chuyển SP 2 sang ngừng kinh doanh
    db = SessionLocal()
    try:
        p2 = db.query(ProductEntity).filter(ProductEntity.id == 2).first()
        if p2:
            p2.status = "inactive"
            db.commit()
    finally:
        db.close()
    for p in RAW_PRODUCTS:
        if p["id"] == 2:
            p["status"] = "inactive"

    # Gọi API reorder
    reorder_res = client.post(
        f"/api/v1/orders/{order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is True
    assert data["total_valid"] == 2
    assert data["total_excluded"] == 1
    assert len(data["valid_items"]) == 2
    assert len(data["excluded_items"]) == 1

    # SP 2 bị loại với lý do ngừng kinh doanh
    assert data["excluded_items"][0]["product_id"] == 2
    assert "ngừng kinh doanh" in data["excluded_items"][0]["reason"].lower()

    # Khôi phục lại trạng thái SP 2
    setup_test_data()


def test_reorder_case_3_all_inactive():
    """Case 3: Đơn cũ có 3 sản phẩm, cả 3 đã ngừng kinh doanh => Không tạo đơn mới + thông báo."""
    setup_test_data()
    admin_token = get_token("admin")

    old_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 1, "price": 190000.0},
                {"product_id": 2, "quantity": 1, "price": 370000.0},
                {"product_id": 3, "quantity": 1, "price": 480000.0},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert old_order_res.status_code == 201
    order_code = old_order_res.json()["order_code"]

    # Đánh dấu cả 3 SP ngừng kinh doanh
    db = SessionLocal()
    try:
        for pid in [1, 2, 3]:
            p = db.query(ProductEntity).filter(ProductEntity.id == pid).first()
            if p:
                p.status = "inactive"
        db.commit()
    finally:
        db.close()
    for p in RAW_PRODUCTS:
        if p["id"] in [1, 2, 3]:
            p["status"] = "inactive"

    # Gọi reorder
    reorder_res = client.post(
        f"/api/v1/orders/{order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is False
    assert data["total_valid"] == 0
    assert data["total_excluded"] == 3
    assert "Tất cả sản phẩm trong đơn hàng này hiện đã ngừng kinh doanh" in data["message"]

    setup_test_data()


def test_reorder_case_4_single_line_active():
    """Case 4: Đại lý đặt lại một dòng hàng đang kinh doanh => Chỉ thêm dòng đó với số lượng cũ và giá hiện tại."""
    setup_test_data()
    admin_token = get_token("admin")

    old_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 15, "price": 190000.0},
                {"product_id": 2, "quantity": 8, "price": 370000.0},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert old_order_res.status_code == 201
    order_code = old_order_res.json()["order_code"]

    # Đặt lại riêng dòng SP 1
    db = SessionLocal()
    from app.api.v1.endpoints.orders import _resolve_current_product_price
    expected_cur_price, _, _ = _resolve_current_product_price(db, 1, 1)
    db.close()

    reorder_res = client.post(
        f"/api/v1/orders/{order_code}/reorder",
        json={"product_id": 1},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is True
    assert data["total_valid"] == 1
    assert data["total_excluded"] == 0
    assert data["valid_items"][0]["product_id"] == 1
    assert data["valid_items"][0]["quantity"] == 15
    assert data["valid_items"][0]["price"] == expected_cur_price  # Giá hiện hành


def test_reorder_case_5_single_line_inactive():
    """Case 5: Đại lý đặt lại một dòng hàng đã ngừng kinh doanh => Không thêm + thông báo."""
    setup_test_data()
    admin_token = get_token("admin")

    old_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 10, "price": 190000.0},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert old_order_res.status_code == 201
    order_code = old_order_res.json()["order_code"]

    # Tắt kinh doanh SP 1
    db = SessionLocal()
    try:
        p1 = db.query(ProductEntity).filter(ProductEntity.id == 1).first()
        if p1:
            p1.status = "inactive"
            db.commit()
    finally:
        db.close()
    for p in RAW_PRODUCTS:
        if p["id"] == 1:
            p["status"] = "inactive"

    # Đặt lại riêng dòng SP 1
    reorder_res = client.post(
        f"/api/v1/orders/{order_code}/reorder",
        json={"product_id": 1},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is False
    assert data["total_valid"] == 0
    assert data["total_excluded"] == 1
    assert "hiện đã ngừng kinh doanh và không thể đặt lại" in data["message"]

    setup_test_data()


def test_reorder_case_6_price_rule_uses_current_price():
    """Case 6: Giá sản phẩm đã thay đổi so với đơn cũ => Đơn mới phải dùng giá hiện tại, không dùng giá cũ."""
    setup_test_data()
    admin_token = get_token("admin")

    # Đơn cũ: SP1 mua với giá cũ 100.000
    old_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [
                {"product_id": 1, "quantity": 10, "price": 100000.0},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert old_order_res.status_code == 201
    order_code = old_order_res.json()["order_code"]

    # Tạo bảng giá mới có hiệu lực với giá mới 250.000 (version cao nhất để ưu tiên)
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        pb = PriceBookEntity(
            code=f"BG-NEW-PRICE-{int(now.timestamp())}",
            name="Bảng giá Cập nhật Mới",
            customer_group="Dai_ly_cap_1",
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=30),
            status="ACTIVE",
            version=99999,
            created_by="admin"
        )
        db.add(pb)
        db.flush()
        db.add(PriceBookItemEntity(
            price_book_id=pb.id,
            product_id=1,
            sale_price=250000.0,
            floor_price=200000.0
        ))
        db.commit()
    finally:
        db.close()

    # Reorder
    reorder_res = client.post(
        f"/api/v1/orders/{order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is True
    # Giá mới PHẢI LÀ 250.000, không được là giá cũ 100.000
    assert data["valid_items"][0]["price"] == 250000.0
    assert data["valid_items"][0]["price"] != 100000.0
    assert data["valid_items"][0]["old_price"] == 100000.0
    assert data["valid_items"][0]["quantity"] == 10

    setup_test_data()


def test_reorder_case_7_dealer_cross_order_rejected():
    """Case 7: Đại lý cố reorder đơn của Đại lý khác => Backend từ chối 403 Forbidden."""
    setup_test_data()
    admin_token = get_token("admin")

    # Tạo đơn cho Dealer 1
    d1_order_res = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 2, "price": 190000.0}]
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert d1_order_res.status_code == 201
    d1_order_code = d1_order_res.json()["order_code"]

    # Đại lý test_customer_dealer (gắn với ID 999 hoặc dealer khác)
    cust_token = get_token("test_customer_dealer")

    # Cố tình reorder đơn của Dealer 1
    reorder_cross = client.post(
        f"/api/v1/orders/{d1_order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {cust_token}"}
    )
    assert reorder_cross.status_code == 403
    assert "chỉ được phép đặt lại đơn hàng của chính mình" in reorder_cross.json()["detail"]


def test_reorder_case_8_product_deleted_or_not_found():
    """Case 8: Sản phẩm trong đơn cũ đã bị xóa / không còn tồn tại => Bị loại với lý do phù hợp."""
    setup_test_data()
    admin_token = get_token("admin")

    # Giả lập đơn hàng cũ có sản phẩm ID 999999 (không tồn tại trong hệ thống)
    fake_order_id = 88888
    fake_order_code = "ORD-TEST-NOT-FOUND"
    ORDERS_DB[fake_order_id] = {
        "id": fake_order_id,
        "order_code": fake_order_code,
        "dealer_id": 1,
        "dealer_name": "Đại Lý Phân Phối Miền Bắc - Sao Mai",
        "created_by": "admin",
        "total_amount": 100000.0,
        "status": "CONFIRMED",
        "items": [
            {"product_id": 999999, "product_name": "Sản phẩm Cũ Đã Xóa", "quantity": 3, "price": 100000.0},
            {"product_id": 1, "product_name": "Áo thun Polo", "quantity": 2, "price": 190000.0},
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    reorder_res = client.post(
        f"/api/v1/orders/{fake_order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is True
    assert data["total_valid"] == 1
    assert data["total_excluded"] == 1

    exc = data["excluded_items"][0]
    assert exc["product_id"] == 999999
    assert exc["reason"] == "Sản phẩm không còn tồn tại"


def test_reorder_case_9_empty_order_cannot_reorder():
    """Case 9: Đơn cũ không có sản phẩm hợp lệ => Không tạo đơn mới."""
    setup_test_data()
    admin_token = get_token("admin")

    fake_order_id = 77777
    fake_order_code = "ORD-TEST-EMPTY"
    ORDERS_DB[fake_order_id] = {
        "id": fake_order_id,
        "order_code": fake_order_code,
        "dealer_id": 1,
        "dealer_name": "Đại Lý Phân Phối Miền Bắc - Sao Mai",
        "created_by": "admin",
        "total_amount": 0.0,
        "status": "CONFIRMED",
        "items": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    reorder_res = client.post(
        f"/api/v1/orders/{fake_order_code}/reorder",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert reorder_res.status_code == 200
    data = reorder_res.json()
    assert data["can_reorder"] is False
    assert data["total_valid"] == 0
