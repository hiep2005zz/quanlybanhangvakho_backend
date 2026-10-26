# backend/tests/test_inventory_reservation_transaction.py
from datetime import datetime, timezone, timedelta
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import (
    OrderEntity,
    OrderItemEntity,
    WarehouseStockEntity,
    ProductEntity,
    DealerEntity,
    AuditLogEntity,
)
from app.services.inventory_reservation_service import (
    approve_order_and_reserve_stock,
    cancel_order_and_release_stock,
    release_expired_reservations,
    InsufficientStockException,
    OrderNotEligibleForApprovalException,
    OrderNotFoundException,
)
from app.api.v1.endpoints.orders import ORDERS_DB


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


def get_token(client, username, password):
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_uc1_approve_order_and_reserve_stock_success(db, client):
    """
    UC1: Duyệt đơn thành công khi tồn khả dụng đủ:
    - Row-level lock thực thi
    - Tăng reserved_quantity
    - is_reserved = True
    - Trạng thái sang APPROVED
    - reservation_expires_at được thiết lập = now + 24h
    """
    # 1. Chuẩn bị sản phẩm và tồn kho
    p1 = db.query(ProductEntity).filter(ProductEntity.id == 1).first()
    assert p1 is not None, "Product 1 must exist"
    
    # Thiết lập tồn kho kho WH01 cho product 1
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    if not stock_rec:
        stock_rec = WarehouseStockEntity(
            warehouse_id="WH01",
            warehouse_name="Kho Tổng Hà Nội",
            product_id=1,
            actual_stock=100,
            quantity_on_hand=100,
            reserved_stock=10,
            reserved_quantity=10,
        )
        db.add(stock_rec)
        db.commit()
    else:
        stock_rec.actual_stock = 100
        stock_rec.quantity_on_hand = 100
        stock_rec.reserved_stock = 10
        stock_rec.reserved_quantity = 10
        db.commit()

    initial_reserved = stock_rec.reserved_quantity

    # 2. Tạo đơn hàng chờ duyệt
    order_code = f"TEST_RES_01_{int(datetime.now().timestamp())}"
    order = OrderEntity(
        order_code=order_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="tran_ban_hang",
        total_amount=500000.0,
        status="PENDING_APPROVAL",
        requires_approval=True,
        approval_reason="Chiết khấu vượt mức cần duyệt",
        items_json=json.dumps([
            {
                "product_id": 1,
                "sku_id": "SP001",
                "product_name": "Áo thun Polo Nam Cao Cấp",
                "quantity": 5,
                "base_quantity": 5,
                "price": 100000.0,
                "is_reserved": False,
            }
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # 3. Thực thi duyệt đơn và giữ chỗ qua Service
    approved_order = approve_order_and_reserve_stock(
        db=db,
        order_identifier=order.order_code,
        approved_by="quan_ly_ban_hang",
        reservation_hours=24,
    )

    # 4. Kiểm tra kết quả
    assert approved_order.status in ("APPROVED", "CONFIRMED")
    assert approved_order.requires_approval is False
    assert approved_order.reservation_expires_at is not None
    assert approved_order.reservation_expires_at > datetime.now(timezone.utc)

    # Kiểm tra tồn giữ chỗ trong kho
    db.refresh(stock_rec)
    expected_reserved = initial_reserved + 5
    assert stock_rec.reserved_quantity == expected_reserved
    assert stock_rec.reserved_stock == expected_reserved
    assert stock_rec.available == (stock_rec.quantity_on_hand - expected_reserved)

    # Kiểm tra dòng hàng is_reserved
    items_after = json.loads(approved_order.items_json)
    if isinstance(items_after, list):
        assert items_after[0]["is_reserved"] is True
    elif isinstance(items_after, dict):
        assert items_after["items"][0]["is_reserved"] is True


def test_uc1_approve_order_insufficient_stock_rollback(db):
    """
    UC1: Không đủ tồn khả dụng:
    - Bắt buộc ROLLBACK TRANSACTION
    - Trả về InsufficientStockException với format chuẩn:
      'Không đủ tồn khả dụng cho SKU [Mã_SKU]. Khả dụng: [available_qty], yêu cầu: [item.quantity]. Hủy thao tác duyệt đơn.'
    - Không làm thay đổi trạng thái đơn và không giữ chỗ
    """
    # 1. Cấu hình tồn kho chỉ còn khả dụng = 2
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 2
    ).first()
    if not stock_rec:
        stock_rec = WarehouseStockEntity(
            warehouse_id="WH01",
            warehouse_name="Kho Tổng Hà Nội",
            product_id=2,
            actual_stock=10,
            quantity_on_hand=10,
            reserved_stock=8,
            reserved_quantity=8,
        )
        db.add(stock_rec)
        db.commit()
    else:
        stock_rec.actual_stock = 10
        stock_rec.quantity_on_hand = 10
        stock_rec.reserved_stock = 8
        stock_rec.reserved_quantity = 8
        db.commit()

    # 2. Tạo đơn yêu cầu 10 chiếc (trong khi khả dụng chỉ có 2)
    order_code = f"TEST_RES_INSUFF_{int(datetime.now().timestamp())}"
    order = OrderEntity(
        order_code=order_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="tran_ban_hang",
        total_amount=1000000.0,
        status="PENDING_APPROVAL",
        requires_approval=True,
        approval_reason="Chờ duyệt",
        items_json=json.dumps([
            {
                "product_id": 2,
                "sku_id": "SP002",
                "product_name": "Quần Jeans Slimfit",
                "quantity": 10,
                "base_quantity": 10,
                "price": 100000.0,
                "is_reserved": False,
            }
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.commit()

    # 3. Duyệt đơn và kiểm tra bắt ngoại lệ
    with pytest.raises(InsufficientStockException) as exc_info:
        approve_order_and_reserve_stock(
            db=db,
            order_identifier=order_code,
            approved_by="quan_ly_ban_hang",
            reservation_hours=24,
        )

    # 4. Kiểm tra câu thông báo lỗi chi tiết theo đặc tả
    err_msg = str(exc_info.value.detail)
    assert "Không đủ tồn khả dụng cho SKU SP002" in err_msg
    assert "Khả dụng: 2" in err_msg
    assert "yêu cầu: 10" in err_msg
    assert "Hủy thao tác duyệt đơn" in err_msg

    # 5. Kiểm tra ROLLBACK: Đơn hàng vẫn ở trạng thái PENDING_APPROVAL, tồn kho không đổi
    db.refresh(order)
    assert order.status == "PENDING_APPROVAL"
    db.refresh(stock_rec)
    assert stock_rec.reserved_quantity == 8


def test_uc2_cancel_order_releases_reserved_stock(db):
    """
    UC2: Hủy đơn hàng đã APPROVED -> Nhả tồn kho giữ chỗ:
    - Giảm reserved_quantity
    - Đặt is_reserved = False
    - Chuyển status = CANCELLED
    """
    # 1. Cấu hình tồn kho
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    stock_rec.actual_stock = 100
    stock_rec.quantity_on_hand = 100
    stock_rec.reserved_stock = 25
    stock_rec.reserved_quantity = 25
    db.commit()

    initial_reserved = stock_rec.reserved_quantity

    # 2. Tạo đơn đã APPROVED và có is_reserved = True
    order_code = f"TEST_CANCEL_RES_{int(datetime.now().timestamp())}"
    order = OrderEntity(
        order_code=order_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="tran_ban_hang",
        total_amount=500000.0,
        status="APPROVED",
        requires_approval=False,
        reservation_expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        items_json=json.dumps([
            {
                "product_id": 1,
                "sku_id": "SP001",
                "product_name": "Áo thun Polo Nam Cao Cấp",
                "quantity": 5,
                "base_quantity": 5,
                "price": 100000.0,
                "is_reserved": True,
            }
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.commit()

    # 3. Thực thi hủy đơn và nhả tồn
    cancelled_order = cancel_order_and_release_stock(
        db=db,
        order_identifier=order_code,
        cancelled_by="tran_ban_hang",
        reason="Khách hàng đổi ý không lấy nữa",
    )

    # 4. Kiểm tra trạng thái đơn
    assert cancelled_order.status == "CANCELLED"
    assert cancelled_order.cancel_reason == "Khách hàng đổi ý không lấy nữa"

    # 5. Kiểm tra tồn kho giữ chỗ đã được nhả
    db.refresh(stock_rec)
    expected_reserved = initial_reserved - 5
    assert stock_rec.reserved_quantity == expected_reserved
    assert stock_rec.reserved_stock == expected_reserved


def test_uc3_expired_reservations_cleanup(db):
    """
    UC3: Quá hạn giữ chỗ & Tự động nhả tồn:
    - Tìm đơn APPROVED có reservation_expires_at < now
    - Nhả reserved_quantity
    - Chuyển status sang EXPIRED
    - Ghi log lý do tự động hoàn trả
    """
    # 0. Dọn dẹp trước các đơn đã hết hạn tồn đọng
    release_expired_reservations(db)
    db.commit()

    # 1. Cấu hình tồn kho
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    stock_rec.actual_stock = 100
    stock_rec.quantity_on_hand = 100
    stock_rec.reserved_stock = 30
    stock_rec.reserved_quantity = 30
    db.commit()

    initial_reserved = stock_rec.reserved_quantity

    # 2. Tạo đơn APPROVED nhưng đã quá hạn (hết hạn 2 giờ trước)
    order_code = f"TEST_EXPIRED_{int(datetime.now().timestamp())}"
    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    order = OrderEntity(
        order_code=order_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="tran_ban_hang",
        total_amount=400000.0,
        status="APPROVED",
        requires_approval=False,
        reservation_expires_at=past_time,
        items_json=json.dumps([
            {
                "product_id": 1,
                "sku_id": "SP001",
                "product_name": "Áo thun Polo Nam Cao Cấp",
                "quantity": 4,
                "base_quantity": 4,
                "price": 100000.0,
                "is_reserved": True,
            }
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    db.add(order)
    db.commit()

    # 3. Kích hoạt release_expired_reservations
    results = release_expired_reservations(db)

    # 4. Kiểm tra kết quả xử lý
    matched = [r for r in results if r["order_code"] == order_code]
    assert len(matched) == 1
    assert matched[0]["status"] == "EXPIRED"

    # Kiểm tra trạng thái đơn trong DB
    db.refresh(order)
    assert order.status == "EXPIRED"
    assert "quá hạn" in order.cancel_reason

    # Kiểm tra tồn giữ chỗ đã giảm 4
    db.refresh(stock_rec)
    expected_reserved = initial_reserved - 4
    assert stock_rec.reserved_quantity == expected_reserved
    assert stock_rec.reserved_stock == expected_reserved


def test_api_approve_endpoint_with_rbac(client, db):
    """
    Kiểm tra luồng API /api/v1/orders/{order_code}/approve:
    - Sales gọi -> 403 Forbidden
    - Sales Manager gọi -> 200 OK, đơn chuyển sang APPROVED, giữ chỗ thành công
    """
    sales_token = get_token(client, "sales", "123")
    manager_token = get_token(client, "sales_manager", "123")

    # Đảm bảo tồn kho đủ
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    stock_rec.actual_stock = 200
    stock_rec.quantity_on_hand = 200
    stock_rec.reserved_stock = 10
    stock_rec.reserved_quantity = 10
    db.commit()

    # Tạo đơn chờ duyệt
    order_code = f"API_APP_TEST_{int(datetime.now().timestamp())}"
    order = OrderEntity(
        order_code=order_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="tran_ban_hang",
        total_amount=200000.0,
        status="PENDING_APPROVAL",
        requires_approval=True,
        approval_reason="Chiết khấu cao",
        items_json=json.dumps([
            {
                "product_id": 1,
                "sku_id": "SP001",
                "product_name": "Áo thun Polo Nam Cao Cấp",
                "quantity": 2,
                "base_quantity": 2,
                "price": 100000.0,
            }
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.commit()

    # 1. Sales cố tình duyệt đơn -> 403
    res_sales = client.post(
        f"/api/v1/orders/{order_code}/approve",
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert res_sales.status_code == 403

    # 2. Sales Manager duyệt đơn -> 200 OK
    res_mgr = client.post(
        f"/api/v1/orders/{order_code}/approve",
        headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert res_mgr.status_code == 200
    data = res_mgr.json()
    assert data["status"] in ("APPROVED", "CONFIRMED")
    assert data["reservation_expires_at"] is not None


def test_criterion_1_approved_order_reserves_stock_and_blocks_further_orders(client, db):
    """
    Tiêu chí 1: Đơn chuyển sang Đã duyệt thì tồn tương ứng được giữ chỗ, không ai đặt được nữa
    - Cấu hình sản phẩm 1: tồn = 10, giữ chỗ = 0 (khả dụng = 10).
    - Đơn 1 đặt 8 chiếc, được duyệt (APPROVED) -> giữ chỗ = 8, khả dụng còn 2.
    - Đơn 2 đặt 5 chiếc (vượt quá 2 chiếc khả dụng còn lại) -> BỊ CHẶN NGAY LẬP TỨC (400 Bad Request).
    """
    sales_token = get_token(client, "sales", "123")
    sm_token = get_token(client, "sales_manager", "123")

    # 1. Cấu hình tồn kho: tổng 10 cái, chưa giữ chỗ
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    stock_rec.actual_stock = 10
    stock_rec.quantity_on_hand = 10
    stock_rec.reserved_stock = 0
    stock_rec.reserved_quantity = 0
    db.commit()

    # 2. Tạo Đơn 1 đặt 8 chiếc
    order1_code = f"CRIT1_ORD1_{int(datetime.now().timestamp())}"
    order1 = OrderEntity(
        order_code=order1_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="sales",
        total_amount=800000.0,
        status="PENDING_APPROVAL",
        requires_approval=True,
        approval_reason="Chờ duyệt",
        items_json=json.dumps([
            {
                "product_id": 1,
                "sku_id": "SP001",
                "product_name": "Áo thun Polo Nam Cao Cấp",
                "quantity": 8,
                "base_quantity": 8,
                "price": 100000.0,
                "is_reserved": False,
            }
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order1)
    db.commit()

    # Quản lý duyệt Đơn 1
    res_app = client.post(
        f"/api/v1/orders/{order1_code}/approve",
        headers={"Authorization": f"Bearer {sm_token}"}
    )
    assert res_app.status_code == 200
    assert res_app.json()["status"] in ("APPROVED", "CONFIRMED")

    # Kiểm tra tồn kho sau khi duyệt: reserved = 8, khả dụng chỉ còn 2
    db.refresh(stock_rec)
    assert stock_rec.reserved_quantity == 8
    assert stock_rec.available == 2

    # 3. Đơn 2 (người khác lên đơn) cố tình đặt 5 chiếc (vượt quá 2 khả dụng)
    res_order2 = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": 1,
            "items": [{"product_id": 1, "quantity": 5, "price": 100000.0}],
            "note": "Đơn số 2 cố đặt khi đã bị giữ chỗ"
        },
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    # BẮT BUỘC BỊ CHẶN: 400 Bad Request vì vượt quá tồn khả dụng
    assert res_order2.status_code == 400, f"Expected 400 blocked, got {res_order2.status_code}: {res_order2.text}"
    err_text = res_order2.text
    assert "vượt quá tồn khả dụng" in err_text or "Số lượng tối đa có thể đặt được là: 2" in err_text


def test_criterion_2_cancel_or_expired_automatically_releases_stock(client, db):
    """
    Tiêu chí 2: Huỷ đơn hoặc đơn quá hạn giữ chỗ thì tồn được nhả tự động
    - Khi hủy đơn đã giữ chỗ -> reserved_quantity giảm ngay lập tức, khả dụng hồi phục.
    - Khi đơn quá hạn (reservation_expires_at < now) -> cleanup chạy, tự động nhả tồn và chuyển EXPIRED.
    """
    # 0. Dọn dẹp trước các đơn đã hết hạn tồn đọng
    release_expired_reservations(db)
    db.commit()

    sales_token = get_token(client, "sales", "123")
    sm_token = get_token(client, "sales_manager", "123")

    # 1. Cấu hình tồn kho: tổng 20 cái, chưa giữ chỗ
    stock_rec = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    stock_rec.actual_stock = 20
    stock_rec.quantity_on_hand = 20
    stock_rec.reserved_stock = 0
    stock_rec.reserved_quantity = 0
    db.commit()

    # 2. Tạo đơn và duyệt giữ chỗ 7 chiếc -> reserved = 7
    ord_code = f"CRIT2_CANCEL_{int(datetime.now().timestamp())}"
    order = OrderEntity(
        order_code=ord_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="sales",
        total_amount=700000.0,
        status="PENDING_APPROVAL",
        requires_approval=True,
        items_json=json.dumps([
            {"product_id": 1, "sku_id": "SP001", "quantity": 7, "base_quantity": 7, "price": 100000.0}
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.commit()

    approve_order_and_reserve_stock(db, ord_code, "sales_manager", reservation_hours=24)
    db.commit()
    db.refresh(stock_rec)
    assert stock_rec.reserved_quantity == 7
    assert stock_rec.available == 13

    # Thao tác HỦY ĐƠN:
    res_cancel = client.post(
        f"/api/v1/orders/{ord_code}/cancel",
        json={"reason": "Khách hủy hợp đồng"},
        headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert res_cancel.status_code == 200

    # Kiểm tra tồn kho đã tự động nhả: reserved trở về 0, khả dụng trở về 20
    db.refresh(stock_rec)
    assert stock_rec.reserved_quantity == 0
    assert stock_rec.available == 20

    # 3. Kiểm tra đơn quá hạn giữ chỗ
    ord_exp_code = f"CRIT2_EXP_{int(datetime.now().timestamp())}"
    order_exp = OrderEntity(
        order_code=ord_exp_code,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="sales",
        total_amount=600000.0,
        status="APPROVED",
        requires_approval=False,
        reservation_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Đã quá hạn 1 tiếng
        items_json=json.dumps([
            {"product_id": 1, "sku_id": "SP001", "quantity": 6, "base_quantity": 6, "price": 100000.0, "is_reserved": True}
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(order_exp)
    stock_rec.reserved_stock = 6
    stock_rec.reserved_quantity = 6
    db.commit()

    # Kích hoạt dọn dẹp đơn quá hạn
    res_cleanup = client.post(
        "/api/v1/orders/cleanup-expired-reservations",
        headers={"Authorization": f"Bearer {sm_token}"}
    )
    assert res_cleanup.status_code == 200
    clean_data = res_cleanup.json()
    assert clean_data["expired_orders_count"] >= 1

    # Kiểm tra đơn chuyển sang EXPIRED và tồn kho đã được nhả
    db.refresh(order_exp)
    assert order_exp.status == "EXPIRED"
    db.refresh(stock_rec)
    assert stock_rec.reserved_quantity == 0
    assert stock_rec.available == 20


def test_criterion_3_atomic_transaction_multi_item_rollback(db):
    """
    Tiêu chí 3: Việc trừ tồn và ghi giữ chỗ nằm trong cùng một giao dịch, lỗi giữa chừng thì hoàn lại toàn bộ
    - Đơn hàng gồm 2 sản phẩm:
      + SP 1: Tồn kho dồi dào (100 cái, yêu cầu 5 cái) -> ĐỦ HÀNG
      + SP 2: Tồn kho chỉ có 1 cái (yêu cầu 10 cái) -> THIẾU HÀNG
    - Khi duyệt đơn: SP 1 kiểm tra trước thành công, đến SP 2 thiếu hàng -> Bị chặn và ROLLBACK toàn bộ.
    - Kết quả kiểm chứng ACID: SP 1 KHÔNG hề bị tăng giữ chỗ dở dang, trạng thái đơn không đổi.
    """
    # 1. Cấu hình tồn kho cho 2 sản phẩm
    stk1 = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 1
    ).first()
    stk1.actual_stock = 100
    stk1.quantity_on_hand = 100
    stk1.reserved_stock = 10
    stk1.reserved_quantity = 10

    stk2 = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == 2
    ).first()
    stk2.actual_stock = 5
    stk2.quantity_on_hand = 5
    stk2.reserved_stock = 4
    stk2.reserved_quantity = 4  # Khả dụng chỉ còn: 5 - 4 = 1 chiếc
    db.commit()

    init_res1 = stk1.reserved_quantity
    init_res2 = stk2.reserved_quantity

    # 2. Tạo đơn hàng 2 dòng
    ord_multi = f"CRIT3_ATOMIC_{int(datetime.now().timestamp())}"
    order = OrderEntity(
        order_code=ord_multi,
        dealer_id=1,
        dealer_name="Đại Lý Phân Phối Miền Bắc - Sao Mai",
        created_by="sales",
        total_amount=1500000.0,
        status="PENDING_APPROVAL",
        requires_approval=True,
        approval_reason="Đơn nhiều dòng kiểm thử atomic",
        items_json=json.dumps([
            {"product_id": 1, "sku_id": "SP001", "product_name": "Áo Polo", "quantity": 5, "base_quantity": 5, "price": 100000.0},
            {"product_id": 2, "sku_id": "SP002", "product_name": "Quần Jeans", "quantity": 10, "base_quantity": 10, "price": 100000.0},
        ], ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(order)
    db.commit()

    # 3. Thực thi duyệt đơn -> Phải ném ngoại lệ do SP 2 thiếu hàng
    with pytest.raises(InsufficientStockException) as exc_info:
        approve_order_and_reserve_stock(
            db=db,
            order_identifier=ord_multi,
            approved_by="sales_manager",
            reservation_hours=24,
        )

    assert "Không đủ tồn khả dụng cho SKU SP002" in str(exc_info.value.detail)

    # 4. Kiểm tra tính NGUYÊN TỬ (Atomic Rollback 100%):
    db.refresh(order)
    assert order.status == "PENDING_APPROVAL"  # Không bị đổi trạng thái

    db.refresh(stk1)
    assert stk1.reserved_quantity == init_res1  # SP 1 KHÔNG bị tăng dở dang
    assert stk1.reserved_stock == init_res1

    db.refresh(stk2)
    assert stk2.reserved_quantity == init_res2  # SP 2 giữ nguyên
    assert stk2.reserved_stock == init_res2

