# tests/test_warehouse_dispatch.py
import pytest
import json
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token
from app.core.database import SessionLocal
from app.models.entities import OrderEntity, OrderItemEntity, ProductEntity, WarehouseStockEntity, OrderDispatchEntity, InventoryTransactionEntity, DealerEntity

client = TestClient(app)

@pytest.fixture
def warehouse_token():
    return create_access_token("kho", "warehouse")

@pytest.fixture
def admin_token():
    return create_access_token("admin", "admin")

@pytest.fixture
def sales_token():
    return create_access_token("sales", "sales")

@pytest.fixture
def ketoan_token():
    return create_access_token("ketoan", "accountant")

@pytest.fixture
def test_order_setup():
    db = SessionLocal()
    try:
        # Đảm bảo có đại lý và sản phẩm
        dealer = db.query(DealerEntity).first()
        dealer_id = dealer.id if dealer else 1
        dealer_name = dealer.name if dealer else "Đại lý Hà Nội"

        # Tạo 2 sản phẩm thử nghiệm hoặc lấy từ DB
        p1 = db.query(ProductEntity).filter(ProductEntity.id == 1).first()
        p2 = db.query(ProductEntity).filter(ProductEntity.id == 2).first()
        pid1 = p1.id if p1 else 1
        pid2 = p2.id if p2 else 2

        # Cài đặt tồn kho cho 2 sản phẩm tại WH01
        ws1 = db.query(WarehouseStockEntity).filter(WarehouseStockEntity.warehouse_id == "WH01", WarehouseStockEntity.product_id == pid1).first()
        if not ws1:
            ws1 = WarehouseStockEntity(warehouse_id="WH01", warehouse_name="Kho Tổng Hà Nội", product_id=pid1, actual_stock=200, quantity_on_hand=200, reserved_stock=0, reserved_quantity=0)
            db.add(ws1)
        else:
            ws1.actual_stock = 200
            ws1.quantity_on_hand = 200
            ws1.reserved_stock = 0
            ws1.reserved_quantity = 0

        ws2 = db.query(WarehouseStockEntity).filter(WarehouseStockEntity.warehouse_id == "WH01", WarehouseStockEntity.product_id == pid2).first()
        if not ws2:
            ws2 = WarehouseStockEntity(warehouse_id="WH01", warehouse_name="Kho Tổng Hà Nội", product_id=pid2, actual_stock=100, quantity_on_hand=100, reserved_stock=0, reserved_quantity=0)
            db.add(ws2)
        else:
            ws2.actual_stock = 100
            ws2.quantity_on_hand = 100
            ws2.reserved_stock = 0
            ws2.reserved_quantity = 0

        # Tạo một đơn hàng CONFIRMED để xuất kho
        order_code = f"TEST-DISPATCH-{pid1}-{pid2}"
        existing = db.query(OrderEntity).filter(OrderEntity.order_code == order_code).first()
        if existing:
            db.query(OrderDispatchEntity).filter(OrderDispatchEntity.order_id == existing.id).delete()
            db.delete(existing)
            db.commit()

        items = [
            {"product_id": pid1, "product_name": "Sản phẩm A", "sku_id": "SP001", "quantity": 10, "delivered_quantity": 0, "shortage_quantity": 10, "price": 100000, "unit": "Cái"},
            {"product_id": pid2, "product_name": "Sản phẩm B", "sku_id": "SP002", "quantity": 5, "delivered_quantity": 0, "shortage_quantity": 5, "price": 200000, "unit": "Cái"},
        ]

        order = OrderEntity(
            order_code=order_code,
            dealer_id=dealer_id,
            dealer_name=dealer_name,
            created_by="sales",
            total_amount=2000000.0,
            status="CONFIRMED",
            items_json=json.dumps(items, ensure_ascii=False),
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        return {"order_code": order_code, "pid1": pid1, "pid2": pid2}
    finally:
        db.close()


def test_rbac_sales_and_customer_forbidden(sales_token, ketoan_token, test_order_setup):
    """Scenario 10: Người dùng không đủ quyền (sales, ketoan) bị chặn 403."""
    code = test_order_setup["order_code"]
    res = client.post(
        f"/api/v1/orders/{code}/confirm-dispatch",
        headers={"Authorization": f"Bearer {sales_token}"},
        json={"delivered_quantities": {str(test_order_setup["pid1"]): 5}},
    )
    assert res.status_code == 403

    res_ketoan = client.post(
        f"/api/v1/orders/{code}/confirm-dispatch",
        headers={"Authorization": f"Bearer {ketoan_token}"},
        json={"delivered_quantities": {str(test_order_setup["pid1"]): 5}},
    )
    assert res_ketoan.status_code == 403


def test_validation_negative_and_over_quantity(warehouse_token, test_order_setup):
    """Scenario 5 & 6: Chặn số âm và chặn xuất vượt số lượng đặt."""
    code = test_order_setup["order_code"]
    pid1 = str(test_order_setup["pid1"])

    # Số âm
    res_neg = client.post(
        f"/api/v1/orders/{code}/confirm-dispatch",
        headers={"Authorization": f"Bearer {warehouse_token}"},
        json={"delivered_quantities": {pid1: -1}},
    )
    assert res_neg.status_code == 400
    assert "không được âm" in res_neg.text

    # Vượt số lượng đặt (đặt 10, xuất 15)
    res_over = client.post(
        f"/api/v1/orders/{code}/confirm-dispatch",
        headers={"Authorization": f"Bearer {warehouse_token}"},
        json={"delivered_quantities": {pid1: 15}},
    )
    assert res_over.status_code == 400
    assert "vượt quá số lượng còn phải giao" in res_over.text


def test_order_not_found(warehouse_token):
    """Scenario 9: Đơn hàng không tồn tại trả về 404."""
    res = client.post(
        "/api/v1/orders/NON-EXISTENT-CODE-999/confirm-dispatch",
        headers={"Authorization": f"Bearer {warehouse_token}"},
        json={"delivered_quantities": {"1": 1}},
    )
    assert res.status_code == 404


def test_partial_dispatch_and_supplementary_delivery(warehouse_token, test_order_setup):
    """
    Scenario 2, 3, 4:
    - Xuất thiếu đợt 1 -> Ghi nhận nợ hàng, đơn PARTIALLY_DELIVERED.
    - Xuất bổ sung đợt 2 một phần -> Đơn vẫn PARTIALLY_DELIVERED.
    - Xuất bổ sung đợt 3 phần còn lại -> Đơn hoàn tất DELIVERED.
    """
    code = test_order_setup["order_code"]
    p1 = str(test_order_setup["pid1"])
    p2 = str(test_order_setup["pid2"])

    # Lượt 1: Đặt SP1: 10, SP2: 5. Xuất SP1: 6 (thiếu 4), SP2: 5 (đủ)
    res1 = client.post(
        f"/api/v1/orders/{code}/confirm-dispatch",
        headers={"Authorization": f"Bearer {warehouse_token}"},
        json={"delivered_quantities": {p1: 6, p2: 5}, "note": "Xuất thiếu 4 SP1 do đang xếp hàng"},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["order_status"] == "PARTIALLY_DELIVERED"
    assert data1["round_number"] == 1
    assert data1["dispatch_code"].startswith("PXK-")

    # Kiểm tra danh sách Chờ giao bổ sung có đơn này
    res_supp_list = client.get(
        "/api/v1/orders/warehouse/supplementary-pending",
        headers={"Authorization": f"Bearer {warehouse_token}"},
    )
    assert res_supp_list.status_code == 200
    supp_orders = res_supp_list.json()
    matching = [o for o in supp_orders if o["order_code"] == code]
    assert len(matching) == 1
    assert matching[0]["shortage_items_count"] == 1
    assert matching[0]["total_shortage_quantity"] == 4

    # Lượt 2: Giao bổ sung 2 SP1 (vẫn còn thiếu 2)
    res2 = client.post(
        f"/api/v1/orders/{code}/supplementary-delivery",
        headers={"Authorization": f"Bearer {warehouse_token}"},
        json={"delivered_quantities": {p1: 2}, "note": "Giao bổ sung đợt 2"},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["order_status"] == "PARTIALLY_DELIVERED"
    assert data2["round_number"] == 2

    # Lượt 3: Giao nốt 2 SP1 còn lại -> Đơn hoàn tất DELIVERED
    res3 = client.post(
        f"/api/v1/orders/{code}/supplementary-delivery",
        headers={"Authorization": f"Bearer {warehouse_token}"},
        json={"delivered_quantities": {p1: 2}, "note": "Giao bổ sung đợt 3 hoàn tất"},
    )
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["order_status"] == "DELIVERED"
    assert data3["round_number"] == 3

    # Kiểm tra lịch sử phiếu xuất (Scenario 14: Phiếu xuất bất biến và xem lại được 3 đợt)
    res_history = client.get(
        f"/api/v1/orders/{code}/dispatch-history",
        headers={"Authorization": f"Bearer {warehouse_token}"},
    )
    assert res_history.status_code == 200
    history = res_history.json()
    assert len(history) == 3
    assert history[0]["round_number"] == 1
    assert history[0]["dispatch_type"] == "INITIAL"
    assert history[1]["round_number"] == 2
    assert history[1]["dispatch_type"] == "SUPPLEMENTARY"
    assert history[2]["round_number"] == 3
    assert history[2]["dispatch_type"] == "SUPPLEMENTARY"
