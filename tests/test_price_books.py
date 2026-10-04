# backend/tests/test_price_books.py
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import ProductEntity, DealerEntity

client = TestClient(app)

def get_auth_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

@pytest.fixture
def auth_tokens():
    return {
        "admin": get_auth_token("admin"),
        "sales_manager": get_auth_token("sales_manager"),
        "sales": get_auth_token("sales"),
        "kho": get_auth_token("kho"),
        "warehouse_mgr": get_auth_token("warehouse_mgr"),
        "muahang": get_auth_token("muahang"),
        "ketoan": get_auth_token("ketoan"),
    }

def test_rbac_price_books_access(auth_tokens):
    """
    Ma trận phân quyền (RBAC):
    - admin, sales_manager: Toàn quyền CRUD bảng giá.
    - accountant: Quyền Xem (Read-only) danh sách bảng giá để đối soát; CẤM Thêm, Sửa, Xóa, Clone.
    - warehouse, warehouse_manager, purchasing, sales: Bị chặn (403 Forbidden) truy cập cấu hình bảng giá.
    """
    # 1. Admin được phép truy cập
    res_admin = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['admin']}"})
    assert res_admin.status_code == 200

    # 2. Sales Manager được phép truy cập
    res_sm = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['sales_manager']}"})
    assert res_sm.status_code == 200

    # 3. Accountant (ketoan) được phép XEM danh sách (200 OK)
    res_kt = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['ketoan']}"})
    assert res_kt.status_code == 200

    # Accountant bị cấm THÊM bảng giá (403 Forbidden)
    res_kt_post = client.post("/api/v1/price-books", json={"code": "BG-KT-DENIED", "name": "Test", "customer_group": "Khach_le", "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T00:00:00Z"}, headers={"Authorization": f"Bearer {auth_tokens['ketoan']}"})
    assert res_kt_post.status_code == 403

    # Accountant bị cấm SỬA bảng giá (403 Forbidden)
    res_kt_put = client.put("/api/v1/price-books/1", json={"name": "Sửa thử"}, headers={"Authorization": f"Bearer {auth_tokens['ketoan']}"})
    assert res_kt_put.status_code == 403

    # Accountant bị cấm CLONE bảng giá (403 Forbidden)
    res_kt_clone = client.post("/api/v1/price-books/1/clone", headers={"Authorization": f"Bearer {auth_tokens['ketoan']}"})
    assert res_kt_clone.status_code == 403

    # 4. Sales bị chặn khỏi API cấu hình bảng giá (403 Forbidden)
    res_sales = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['sales']}"})
    assert res_sales.status_code == 403

    # 5. Warehouse (kho) bị chặn 403 Forbidden
    res_kho = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['kho']}"})
    assert res_kho.status_code == 403

    # 6. Warehouse Manager bị chặn 403 Forbidden
    res_wm = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['warehouse_mgr']}"})
    assert res_wm.status_code == 403

    # 7. Purchasing (muahang) bị chặn 403 Forbidden
    res_mh = client.get("/api/v1/price-books", headers={"Authorization": f"Bearer {auth_tokens['muahang']}"})
    assert res_mh.status_code == 403

def test_create_price_book_and_validation(auth_tokens):
    """
    Tiêu chí 1, 2, 3:
    - Tạo bảng giá theo nhóm khách hàng (Dai_ly_cap_1).
    - Có ngày bắt đầu và kết thúc (chặn valid_to < valid_from).
    - Lưu 2 thông số: sale_price và floor_price.
    """
    token = auth_tokens["sales_manager"]
    now = datetime.now(timezone.utc)

    # 1. Test validate valid_to < valid_from -> Phải trả về 400 Bad Request
    invalid_payload = {
        "code": f"BG-ERR-{int(now.timestamp())}",
        "name": "Bảng giá lỗi ngày",
        "customer_group": "Dai_ly_cap_1",
        "valid_from": now.isoformat(),
        "valid_to": (now - timedelta(days=1)).isoformat(), # Lỗi: kết thúc trước bắt đầu
        "items": []
    }
    res_err = client.post("/api/v1/price-books", json=invalid_payload, headers={"Authorization": f"Bearer {token}"})
    assert res_err.status_code == 400
    assert "Ngày kết thúc không được nhỏ hơn ngày bắt đầu" in res_err.json()["detail"]

    # 2. Test tạo bảng giá hợp lệ với danh sách sản phẩm (sale_price & floor_price)
    code = f"BG-TEST-{int(now.timestamp())}"
    valid_payload = {
        "code": code,
        "name": "Bảng giá Đại lý Cấp 1 Mùa Thu",
        "customer_group": "Dai_ly_cap_1",
        "valid_from": now.isoformat(),
        "valid_to": (now + timedelta(days=60)).isoformat(),
        "status": "ACTIVE",
        "note": "Ghi chú bảng giá test",
        "items": [
            {
                "product_id": 1,
                "sale_price": 180000.0,
                "floor_price": 160000.0
            },
            {
                "product_id": 2,
                "sale_price": 350000.0,
                "floor_price": 320000.0
            }
        ]
    }
    res = client.post("/api/v1/price-books", json=valid_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    data = res.json()
    assert data["code"] == code
    assert data["customer_group"] == "Dai_ly_cap_1"
    assert data["version"] == 1
    assert data["is_locked"] is False
    assert len(data["items"]) == 2
    assert data["items"][0]["sale_price"] == 180000.0
    assert data["items"][0]["floor_price"] == 160000.0

def test_block_update_when_locked(auth_tokens):
    """
    Tiêu chí 4:
    Bảng giá đã phát sinh đơn hàng (is_locked == True) thì KHÔNG sửa (Block Update),
    trả về 400 Bad Request kèm thông báo "Bảng giá đã phát sinh đơn hàng, không được phép chỉnh sửa".
    """
    token = auth_tokens["sales_manager"]
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    try:
        # Tạo 1 bảng giá đã bị khóa (is_locked = True)
        locked_pb = PriceBookEntity(
            code=f"BG-LOCKED-{int(now.timestamp())}",
            name="Bảng giá đã khóa giao dịch",
            customer_group="Dai_ly_cap_2",
            valid_from=now - timedelta(days=10),
            valid_to=now + timedelta(days=20),
            status="ACTIVE",
            version=1,
            is_locked=True,  # ĐÃ PHÁT SINH GIAO DỊCH
            created_by="sales_manager"
        )
        db.add(locked_pb)
        db.commit()
        db.refresh(locked_pb)
        pb_id = locked_pb.id
    finally:
        db.close()

    # Thử gọi PUT cập nhật -> Bắt buộc bị chặn với 400 Bad Request
    update_payload = {
        "name": "Cố tình sửa tên bảng giá đã khóa",
        "status": "INACTIVE"
    }
    res = client.put(f"/api/v1/price-books/{pb_id}", json=update_payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 400
    assert "Bảng giá đã phát sinh đơn hàng, không được phép chỉnh sửa" in res.json()["detail"]

def test_clone_price_book_versioning(auth_tokens):
    """
    Tiêu chí 4:
    Cho phép tạo phiên bản mới (Clone / Create New Version):
    - Tăng version lên 1 (version = old_version + 1).
    - Sao chép toàn bộ dòng sản phẩm sang bản ghi mới.
    - Bản ghi mới có is_locked = False.
    """
    token = auth_tokens["admin"]
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    try:
        orig_pb = PriceBookEntity(
            code=f"BG-ORIG-{int(now.timestamp())}",
            name="Bảng giá Gốc Cần Clone",
            customer_group="Khach_le",
            valid_from=now - timedelta(days=5),
            valid_to=now + timedelta(days=45),
            status="ACTIVE",
            version=1,
            is_locked=True,
            created_by="admin"
        )
        db.add(orig_pb)
        db.flush()

        db.add_all([
            PriceBookItemEntity(price_book_id=orig_pb.id, product_id=1, sale_price=200000.0, floor_price=175000.0),
            PriceBookItemEntity(price_book_id=orig_pb.id, product_id=2, sale_price=400000.0, floor_price=360000.0)
        ])
        db.commit()
        db.refresh(orig_pb)
        orig_id = orig_pb.id
    finally:
        db.close()

    # Gọi API Clone
    res = client.post(f"/api/v1/price-books/{orig_id}/clone", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    cloned = res.json()
    assert cloned["version"] == 2
    assert cloned["is_locked"] is False
    assert "(v2)" in cloned["name"]
    assert len(cloned["items"]) == 2
    assert cloned["items"][0]["sale_price"] == 200000.0
    assert cloned["items"][0]["floor_price"] == 175000.0

def test_resolve_price_endpoint(auth_tokens):
    """
    Endpoint hỗ trợ luồng Đơn hàng (Order):
    GET /api/v1/price-books/resolve-price?customer_id=...&product_id=...
    Tự động tìm bảng giá đang hiệu lực theo nhóm của khách hàng, trả về sale_price và floor_price.
    """
    sales_token = auth_tokens["sales"]
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    try:
        # Cập nhật dealer 1 thuộc nhóm Dai_ly_cap_1
        d = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if d:
            d.customer_group = "Dai_ly_cap_1"
            db.commit()

        # Tạo bảng giá hiệu lực cho nhóm Dai_ly_cap_1
        pb = PriceBookEntity(
            code=f"BG-AUTO-{int(now.timestamp())}",
            name="Bảng giá Tự Động Đại Lý 1",
            customer_group="Dai_ly_cap_1",
            valid_from=now - timedelta(days=2),
            valid_to=now + timedelta(days=30),
            status="ACTIVE",
            version=1,
            is_locked=False,
            created_by="sales_manager"
        )
        db.add(pb)
        db.flush()

        db.add(PriceBookItemEntity(
            price_book_id=pb.id,
            product_id=1,
            sale_price=170000.0,
            floor_price=150000.0
        ))
        db.commit()
    finally:
        db.close()

    # Nhân viên sales tra cứu giá áp dụng
    res = client.get(
        "/api/v1/price-books/resolve-price?customer_id=1&product_id=1",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["customer_id"] == 1
    assert data["product_id"] == 1
    assert data["sale_price"] == 170000.0
    assert data["floor_price"] == 150000.0

def test_order_creation_floor_price_and_lock(auth_tokens):
    """
    Tiêu chí 3: Bán dưới giá sàn sẽ phải qua duyệt (order chuyển sang PENDING_APPROVAL).
    Tiêu chí 4: Bảng giá đã phát sinh đơn hàng thì bị khóa (is_locked = True).
    Phân quyền: Sales_manager có quyền duyệt đơn.
    """
    sales_token = auth_tokens["sales"]
    sm_token = auth_tokens["sales_manager"]
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    try:
        d = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if d:
            d.customer_group = "Dai_ly_cap_1"
            db.commit()

        pb = PriceBookEntity(
            code=f"BG-FLOOR-{int(now.timestamp())}",
            name="Bảng giá Kiểm tra Giá Sàn",
            customer_group="Dai_ly_cap_1",
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=30),
            status="ACTIVE",
            version=1,
            is_locked=False,
            created_by="sales_manager"
        )
        db.add(pb)
        db.flush()

        db.add(PriceBookItemEntity(
            price_book_id=pb.id,
            product_id=1,
            sale_price=180000.0,
            floor_price=160000.0  # Giá sàn là 160.000đ
        ))
        db.commit()
        db.refresh(pb)
        pb_id = pb.id
    finally:
        db.close()

    # 1. Bán giá 140.000đ (< giá sàn 160.000đ) -> Phải chuyển sang PENDING_APPROVAL
    order_payload = {
        "dealer_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 2,
                "price": 140000.0  # DƯỚI GIÁ SÀN!
            }
        ],
        "note": "Bán chiết khấu đặc biệt dưới giá sàn"
    }
    res_order = client.post("/api/v1/orders", json=order_payload, headers={"Authorization": f"Bearer {sales_token}"})
    assert res_order.status_code == 201
    order_data = res_order.json()
    assert order_data["status"] == "PENDING_APPROVAL"
    order_code = order_data["order_code"]

    # 2. Kiểm tra cờ is_locked của bảng giá đã được tự động khóa thành True
    check_db = SessionLocal()
    try:
        recheck_pb = check_db.query(PriceBookEntity).filter(PriceBookEntity.id == pb_id).first()
        assert recheck_pb.is_locked is True
    finally:
        check_db.close()

    # 3. Quản lý kinh doanh (sales_manager) duyệt đơn
    res_approve = client.post(f"/api/v1/orders/{order_code}/approve", headers={"Authorization": f"Bearer {sm_token}"})
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "CONFIRMED"
