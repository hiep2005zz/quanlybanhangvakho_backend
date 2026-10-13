# backend/tests/test_product_deletion.py
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.v1.endpoints.products import RAW_PRODUCTS
from app.api.v1.endpoints.orders import ORDERS_DB
from app.api.v1.endpoints.inventory import INVENTORY_TRANSACTIONS
from app.core.database import SessionLocal
from app.models.entities import ProductEntity, OrderEntity, InventoryTransactionEntity

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_product_with_transactions_cannot_be_deleted():
    """
    Khi sản phẩm đã phát sinh giao dịch (đơn hàng hoặc chứng từ kho) thì không thể xóa:
    - Yêu cầu DELETE /api/v1/products/{id} phải bị chặn với mã lỗi HTTP 400 Bad Request.
    - Trả về thông báo hướng dẫn chuyển sang trạng thái 'Ngừng kinh doanh'.
    - Cho phép cập nhật trạng thái sản phẩm sang 'inactive' (Ngừng kinh doanh).
    """
    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    wh_token = get_token("kho")
    wh_headers = {"Authorization": f"Bearer {wh_token}"}

    # 1. Tạo sản phẩm mới chưa có giao dịch
    create_payload = {
        "code": "SP_TX_BLOCK_01",
        "name": "Sản Phẩm Thử Nghiệm Giao Dịch",
        "category": "Thời trang",
        "base_unit": "Cái",
        "sell_price": 250000.0,
        "cost_price": 150000.0,
        "status": "active"
    }
    create_res = client.post("/api/v1/products", json=create_payload, headers=admin_headers)
    assert create_res.status_code == 201, create_res.text
    prod = create_res.json()
    prod_id = prod["id"]

    try:
        # 2. Phát sinh giao dịch nhập kho cho sản phẩm này
        receipt_payload = {
            "product_id": prod_id,
            "quantity": 10,
            "unit_name": "Cái",
            "conversion_rate": 1.0,
            "supplier": "Công ty TNHH Cung Ứng May Mặc",
            "note": "Nhập kho đợt 1 kiểm thử xóa sản phẩm"
        }
        receipt_res = client.post("/api/v1/inventory/receipt", json=receipt_payload, headers=wh_headers)
        assert receipt_res.status_code == 200, receipt_res.text

        # 3. Kiểm tra danh sách sản phẩm: transaction_count > 0
        get_res = client.get("/api/v1/products", headers=admin_headers)
        assert get_res.status_code == 200
        prods = get_res.json()["items"]
        target = next((p for p in prods if p["id"] == prod_id), None)
        assert target is not None
        assert target["transaction_count"] > 0

        # 4. Thử XÓA sản phẩm đã có giao dịch -> BẮT BUỘC BỊ CHẶN HTTP 400
        del_res = client.delete(f"/api/v1/products/{prod_id}", headers=admin_headers)
        assert del_res.status_code == 400, del_res.text
        detail_msg = del_res.json().get("detail", "")
        assert "không thể xóa" in detail_msg
        assert "Ngừng kinh doanh" in detail_msg

        # 5. Xác nhận sản phẩm vẫn còn tồn tại trong hệ thống
        get_after = client.get("/api/v1/products", headers=admin_headers)
        still_exists = any(p["id"] == prod_id for p in get_after.json()["items"])
        assert still_exists is True

        # 6. Cho phép chuyển trạng thái sang "inactive" (Ngừng kinh doanh)
        update_res = client.put(
            f"/api/v1/products/{prod_id}",
            json={"status": "inactive"},
            headers=admin_headers
        )
        assert update_res.status_code == 200
        assert update_res.json()["status"] == "inactive"

    finally:
        # Dọn dẹp dữ liệu test
        # Xóa các giao dịch inventory phát sinh cho test
        for tx in list(INVENTORY_TRANSACTIONS):
            tx_pid = getattr(tx, "product_id", None) or (tx.get("product_id") if isinstance(tx, dict) else None)
            if tx_pid == prod_id:
                INVENTORY_TRANSACTIONS.remove(tx)

        db = SessionLocal()
        try:
            db.query(InventoryTransactionEntity).filter(InventoryTransactionEntity.product_id == prod_id).delete()
            db.query(ProductEntity).filter(ProductEntity.id == prod_id).delete()
            db.commit()
        finally:
            db.close()

        for idx, p in enumerate(list(RAW_PRODUCTS)):
            if p["id"] == prod_id:
                RAW_PRODUCTS.pop(idx)
                break


def test_product_without_transactions_can_be_deleted():
    """
    Sản phẩm chưa phát sinh bất kỳ giao dịch nào (đơn hàng hoặc chứng từ kho):
    - Cho phép xóa vĩnh viễn khỏi hệ thống (HTTP 200 OK).
    """
    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Tạo sản phẩm mới hoàn toàn chưa có giao dịch
    create_payload = {
        "code": "SP_NO_TX_CAN_DEL",
        "name": "Sản Phẩm Chưa Giao Dịch",
        "category": "Phụ kiện",
        "base_unit": "Chiếc",
        "sell_price": 99000.0,
        "cost_price": 50000.0,
        "status": "active"
    }
    create_res = client.post("/api/v1/products", json=create_payload, headers=admin_headers)
    assert create_res.status_code == 201, create_res.text
    prod = create_res.json()
    prod_id = prod["id"]

    try:
        # 2. Kiểm tra transaction_count == 0
        get_res = client.get("/api/v1/products", headers=admin_headers)
        prods = get_res.json()["items"]
        target = next((p for p in prods if p["id"] == prod_id), None)
        assert target is not None
        assert target["transaction_count"] == 0

        # 3. Xóa sản phẩm khi chưa có giao dịch -> Thành công HTTP 200
        del_res = client.delete(f"/api/v1/products/{prod_id}", headers=admin_headers)
        assert del_res.status_code == 200, del_res.text
        assert "thành công" in del_res.json().get("message", "")

        # 4. Xác nhận sản phẩm không còn trong danh sách
        get_after = client.get("/api/v1/products", headers=admin_headers)
        still_exists = any(p["id"] == prod_id for p in get_after.json()["items"])
        assert still_exists is False

    finally:
        db = SessionLocal()
        try:
            db.query(ProductEntity).filter(ProductEntity.id == prod_id).delete()
            db.commit()
        finally:
            db.close()
        for idx, p in enumerate(list(RAW_PRODUCTS)):
            if p["id"] == prod_id:
                RAW_PRODUCTS.pop(idx)
                break
