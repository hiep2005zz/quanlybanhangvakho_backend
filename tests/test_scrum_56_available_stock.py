# backend/tests/test_scrum_56_available_stock.py
import pytest
import concurrent.futures
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import WarehouseStockEntity, ProductEntity, DealerEntity, OrderEntity

client = TestClient(app)


def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


@pytest.fixture(scope="module")
def auth_tokens():
    return {
        "admin": get_token("admin"),
        "sales_manager": get_token("sales_manager"),
        "sales": get_token("sales"),
        "kho": get_token("kho"),
        "warehouse_mgr": get_token("warehouse_mgr"),
        "ketoan": get_token("ketoan"),
    }


def test_ac5_authorization_sales_only(auth_tokens):
    """
    AC 5: Phân quyền (Authorization - BẮT BUỘC):
    Các API kiểm tra tồn khả dụng chỉ được phép truy cập bởi người dùng có vai trò là
    Nhân viên kinh doanh (Role: sales, sales_manager, admin).
    Các role khác (kho, warehouse_mgr, ketoan) bị chặn 403 Forbidden.
    """
    sales_tok = auth_tokens["sales"]
    sm_tok = auth_tokens["sales_manager"]
    admin_tok = auth_tokens["admin"]
    kho_tok = auth_tokens["kho"]
    wm_tok = auth_tokens["warehouse_mgr"]
    ketoan_tok = auth_tokens["ketoan"]

    # 1. Sales, Sales Manager, Admin được phép truy cập
    for tok, rname in [(sales_tok, "sales"), (sm_tok, "sales_manager"), (admin_tok, "admin")]:
        res = client.get(
            "/api/v1/inventory/available-stock?dealer_id=1&product_id=1",
            headers={"Authorization": f"Bearer {tok}"}
        )
        assert res.status_code == 200, f"Role {rname} should be allowed (got {res.status_code})"
        assert "available_stock" in res.json()

        res_sum = client.get(
            "/api/v1/inventory/dealer-stock-summary/1",
            headers={"Authorization": f"Bearer {tok}"}
        )
        assert res_sum.status_code == 200, f"Role {rname} summary should be allowed"

    # 2. Thủ kho, Quản lý kho, Kế toán bị CHẶN 403 Forbidden
    for tok, rname in [(kho_tok, "warehouse"), (wm_tok, "warehouse_manager"), (ketoan_tok, "accountant")]:
        res_blocked = client.get(
            "/api/v1/inventory/available-stock?dealer_id=1&product_id=1",
            headers={"Authorization": f"Bearer {tok}"}
        )
        assert res_blocked.status_code == 403, f"Role {rname} must be blocked with 403 (got {res_blocked.status_code})"

        res_sum_blocked = client.get(
            "/api/v1/inventory/dealer-stock-summary/1",
            headers={"Authorization": f"Bearer {tok}"}
        )
        assert res_sum_blocked.status_code == 403, f"Role {rname} summary must be blocked with 403"


def test_ac1_serving_warehouse_per_dealer(auth_tokens):
    """
    AC 1: Trên giao diện đặt hàng, hiển thị số lượng tồn khả dụng của KHO PHỤC VỤ RIÊNG cho đại lý đó.
    - Đại lý Miền Bắc (Hà Nội, DL001) -> Kho Tổng Hà Nội (WH01)
    - Đại lý Miền Nam (TP. HCM, Tân Bình, DL002) -> Kho Chi Nhánh TP. Hồ Chí Minh (WH02)
    """
    sales_tok = auth_tokens["sales"]

    # Đại lý 1: Miền Bắc
    res_d1 = client.get(
        "/api/v1/inventory/available-stock?dealer_id=1&product_id=1",
        headers={"Authorization": f"Bearer {sales_tok}"}
    )
    assert res_d1.status_code == 200
    data_d1 = res_d1.json()
    assert data_d1["warehouse_id"] == "WH01"
    assert "Hà Nội" in data_d1["warehouse_name"]

    # Đại lý 2: Tân Bình - TP. HCM
    res_d2 = client.get(
        "/api/v1/inventory/available-stock?dealer_id=2&product_id=1",
        headers={"Authorization": f"Bearer {sales_tok}"}
    )
    assert res_d2.status_code == 200
    data_d2 = res_d2.json()
    assert data_d2["warehouse_id"] == "WH02"
    assert "Hồ Chí Minh" in data_d2["warehouse_name"]


def test_ac2_available_stock_formula(auth_tokens):
    """
    AC 2: Công thức tính: Tồn khả dụng = Tồn thực tế - Tồn đang giữ chỗ cho đơn khác.
    """
    sales_tok = auth_tokens["sales"]

    # Setup sản phẩm kiểm thử riêng biệt
    db = SessionLocal()
    try:
        p = db.query(ProductEntity).filter(ProductEntity.code == "SP_AC2_TEST").first()
        if not p:
            p = ProductEntity(code="SP_AC2_TEST", name="SP Test Cong Thuc Ton", stock=50, sell_price=10000.0, status="active")
            db.add(p)
            db.commit()
            db.refresh(p)
        pid = p.id

        # Thiết lập tồn thực tế = 50, tồn giữ chỗ = 20 -> Tồn khả dụng phải là 30
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == pid
        ).first()
        if not ws:
            ws = WarehouseStockEntity(
                warehouse_id="WH01",
                warehouse_name="Kho Tổng Hà Nội",
                product_id=pid,
                actual_stock=50,
                reserved_stock=20
            )
            db.add(ws)
        else:
            ws.actual_stock = 50
            ws.reserved_stock = 20
        db.commit()
    finally:
        db.close()

    res = client.get(
        f"/api/v1/inventory/available-stock?dealer_id=1&product_id={pid}",
        headers={"Authorization": f"Bearer {sales_tok}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["actual_stock"] == 50
    assert data["reserved_stock"] == 20
    assert data["available_stock"] == 30  # 50 - 20 = 30


def test_ac3_block_order_when_exceeding_available_stock(auth_tokens):
    """
    AC 3: Khi đặt vượt tồn khả dụng: Chặn đặt hàng (validate ở cả Frontend & Backend)
    và hiển thị thông báo gợi ý số lượng tối đa còn có thể đặt được.
    """
    sales_tok = auth_tokens["sales"]

    db = SessionLocal()
    try:
        p = db.query(ProductEntity).filter(ProductEntity.code == "SP_AC3_LIMIT").first()
        if not p:
            p = ProductEntity(code="SP_AC3_LIMIT", name="SP Test Chan Vuot Ton", stock=15, sell_price=20000.0, status="active")
            db.add(p)
            db.commit()
            db.refresh(p)
        pid = p.id

        # Thực tế 15, giữ chỗ 10 -> Khả dụng còn 5
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == pid
        ).first()
        if not ws:
            ws = WarehouseStockEntity(
                warehouse_id="WH01",
                warehouse_name="Kho Tổng Hà Nội",
                product_id=pid,
                actual_stock=15,
                reserved_stock=10
            )
            db.add(ws)
        else:
            ws.actual_stock = 15
            ws.reserved_stock = 10
        db.commit()
    finally:
        db.close()

    # 1. Đặt 6 sản phẩm (vượt mức 5 khả dụng) -> Chặn 400 Bad Request
    res_fail = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [{"product_id": pid, "quantity": 6, "price": 20000.0}],
            "note": "Thu dat vuot ton"
        },
        headers={"Authorization": f"Bearer {sales_tok}"}
    )
    assert res_fail.status_code == 400
    err_detail = res_fail.json().get("detail", "")
    assert "vượt quá tồn khả dụng" in err_detail
    assert "tối đa có thể đặt được là: 5" in err_detail or "Tồn khả dụng hiện tại: 5" in err_detail

    # 2. Đặt hợp lệ 5 sản phẩm -> Thành công 201 Created
    res_ok = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [{"product_id": pid, "quantity": 5, "price": 20000.0}],
            "note": "Dat dung ton kha dung con lai"
        },
        headers={"Authorization": f"Bearer {sales_tok}"}
    )
    assert res_ok.status_code == 201


def test_ac4_concurrency_race_condition(auth_tokens):
    """
    AC 4: Xử lý Đồng thời (Race Condition):
    Đảm bảo tính chính xác tuyệt đối khi có 2 người cùng chốt đơn trên một SKU sắp hết hàng.
    Không được phép để xảy ra tình trạng âm tồn kho (Database Transaction + Row-level locking).
    """
    sales_tok = auth_tokens["sales"]

    db = SessionLocal()
    try:
        p = db.query(ProductEntity).filter(ProductEntity.code == "SP_AC4_RACE").first()
        if not p:
            p = ProductEntity(code="SP_AC4_RACE", name="SP Test Race Condition", stock=1, sell_price=15000.0, status="active")
            db.add(p)
            db.commit()
            db.refresh(p)
        pid = p.id

        # Thiết lập đúng 1 sản phẩm còn lại duy nhất
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == pid
        ).first()
        if not ws:
            ws = WarehouseStockEntity(
                warehouse_id="WH01",
                warehouse_name="Kho Tổng Hà Nội",
                product_id=pid,
                actual_stock=1,
                reserved_stock=0
            )
            db.add(ws)
        else:
            ws.actual_stock = 1
            ws.reserved_stock = 0
        db.commit()
    finally:
        db.close()

    def submit_order(order_index: int):
        c = TestClient(app)
        res = c.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": pid, "quantity": 1, "price": 15000.0}],
                "note": f"Concurrent order #{order_index}"
            },
            headers={"Authorization": f"Bearer {sales_tok}"}
        )
        return res.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit_order, i) for i in range(2)]
        codes = [f.result() for f in futures]

    # Một request thành công (201), request thứ hai phải bị chặn (400)
    sorted_codes = sorted(codes)
    assert sorted_codes == [201, 400], f"Expected exactly one 201 and one 400, got: {codes}"

    # Kiểm tra tồn kho sau chốt đơn: Tuyệt đối không được âm
    db = SessionLocal()
    try:
        ws_check = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == pid
        ).first()
        assert ws_check.actual_stock == 1
        assert ws_check.reserved_stock == 1
        assert ws_check.available_stock == 0  # Không bị âm!
    finally:
        db.close()
