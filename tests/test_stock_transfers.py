import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import WarehouseStockEntity, StockTransferEntity

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


def test_stock_transfers_workflow():
    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Chuẩn bị tồn kho ban đầu cho WH01 và WH02
    with SessionLocal() as db:
        ws_wh01 = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == 1
        ).first()
        if not ws_wh01:
            ws_wh01 = WarehouseStockEntity(
                warehouse_id="WH01",
                warehouse_name="Kho Tổng Hà Nội",
                product_id=1,
                actual_stock=100,
                reserved_stock=0,
                min_stock=10
            )
            db.add(ws_wh01)
        else:
            ws_wh01.actual_stock = 100
            ws_wh01.reserved_stock = 0

        ws_wh02 = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH02",
            WarehouseStockEntity.product_id == 1
        ).first()
        if not ws_wh02:
            ws_wh02 = WarehouseStockEntity(
                warehouse_id="WH02",
                warehouse_name="Kho Chi Nhánh TP. Hồ Chí Minh",
                product_id=1,
                actual_stock=20,
                reserved_stock=0,
                min_stock=10
            )
            db.add(ws_wh02)
        else:
            ws_wh02.actual_stock = 20
        db.commit()

    # 2. Kiểm tra chặn tạo phiếu cùng kho
    same_warehouse_payload = {
        "from_warehouse_id": "WH01",
        "to_warehouse_id": "WH01",
        "items": [{"product_id": 1, "transfer_qty": 10}],
        "note": "Chuyển cùng kho"
    }
    resp = client.post("/api/v1/inventory/transfers", json=same_warehouse_payload, headers=admin_headers)
    assert resp.status_code == 400
    assert "trùng nhau" in resp.json()["detail"]

    # 3. Kiểm tra chặn khi tồn khả dụng không đủ
    over_stock_payload = {
        "from_warehouse_id": "WH01",
        "to_warehouse_id": "WH02",
        "items": [{"product_id": 1, "transfer_qty": 99999}],
        "note": "Vượt tồn"
    }
    resp = client.post("/api/v1/inventory/transfers", json=over_stock_payload, headers=admin_headers)
    assert resp.status_code == 400
    assert "không đủ để chuyển" in resp.json()["detail"]

    # 4. Tạo phiếu chuyển kho hợp lệ (chuyển 15 sản phẩm từ WH01 sang WH02)
    valid_payload = {
        "from_warehouse_id": "WH01",
        "to_warehouse_id": "WH02",
        "items": [{"product_id": 1, "transfer_qty": 15}],
        "note": "Điều chuyển hàng phục vụ chi nhánh HCM"
    }
    resp = client.post("/api/v1/inventory/transfers", json=valid_payload, headers=admin_headers)
    assert resp.status_code == 200
    transfer_data = resp.json()
    transfer_code = transfer_data["code"]
    assert transfer_data["status"] == "IN_TRANSIT"
    assert transfer_data["total_transfer_qty"] == 15

    # 5. Kiểm tra: Tồn kho đi (WH01) ĐÃ GIẢM từ 100 -> 85
    # NHƯNG tồn kho đến (WH02) CHƯA ĐƯỢC CỘNG (vẫn là 20)!
    with SessionLocal() as db:
        ws_wh01_after = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == 1
        ).first()
        ws_wh02_after = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH02",
            WarehouseStockEntity.product_id == 1
        ).first()
        assert ws_wh01_after.actual_stock == 85
        assert ws_wh02_after.actual_stock == 20  # Hàng đang trên đường, chưa cộng vào kho đến!

    # 6. Kiểm tra xem chi tiết phiếu
    detail_resp = client.get(f"/api/v1/inventory/transfers/{transfer_code}", headers=admin_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "IN_TRANSIT"

    # 7. Kho đến nhận thiếu mà KHÔNG nhập lý do -> Bị từ chối (400)
    rx_payload_no_reason = {
        "items": [{"product_id": 1, "received_qty": 12}],  # Gửi 15 nhưng chỉ nhận 12 (thiếu 3)
        "discrepancy_reason": ""  # Bỏ trống lý do
    }
    rx_fail_resp = client.post(f"/api/v1/inventory/transfers/{transfer_code}/receive", json=rx_payload_no_reason, headers=admin_headers)
    assert rx_fail_resp.status_code == 400
    assert "Bắt buộc phải nhập lý do chênh lệch" in rx_fail_resp.json()["detail"]

    # 8. Kho đến nhận thiếu CÓ nhập lý do -> Thành công!
    rx_payload_with_reason = {
        "items": [{"product_id": 1, "received_qty": 12, "reason": "3 hộp bị ướt hỏng do mưa"}],
        "discrepancy_reason": "Thùng hàng bị ngấm nước mưa làm hỏng 3 hộp trên xe vận chuyển"
    }
    rx_success_resp = client.post(f"/api/v1/inventory/transfers/{transfer_code}/receive", json=rx_payload_with_reason, headers=admin_headers)
    assert rx_success_resp.status_code == 200
    completed_data = rx_success_resp.json()
    assert completed_data["status"] == "COMPLETED"
    assert completed_data["total_received_qty"] == 12
    assert completed_data["discrepancy_qty"] == 3
    assert "ngấm nước mưa" in completed_data["discrepancy_reason"]

    # 9. Kiểm tra: Tồn kho đến (WH02) chỉ tăng đúng số lượng thực nhận (20 + 12 = 32)
    with SessionLocal() as db:
        ws_wh02_final = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH02",
            WarehouseStockEntity.product_id == 1
        ).first()
        assert ws_wh02_final.actual_stock == 32


def test_cancel_stock_transfer():
    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Tạo phiếu chuyển 5 sản phẩm
    payload = {
        "from_warehouse_id": "WH01",
        "to_warehouse_id": "WH03",
        "items": [{"product_id": 1, "transfer_qty": 5}],
        "note": "Phiếu test hủy"
    }
    resp = client.post("/api/v1/inventory/transfers", json=payload, headers=admin_headers)
    assert resp.status_code == 200
    code = resp.json()["code"]

    # Lấy tồn kho trước khi hủy
    with SessionLocal() as db:
        ws_before = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == 1
        ).first()
        stock_before_cancel = ws_before.actual_stock

    # Hủy phiếu chuyển kho
    cancel_resp = client.post(f"/api/v1/inventory/transfers/{code}/cancel?cancel_reason=Kho+đích+hết+nhu+cầu", headers=admin_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Tồn kho đi được hoàn trả +5
    with SessionLocal() as db:
        ws_after = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == 1
        ).first()
        assert ws_after.actual_stock == stock_before_cancel + 5


def test_stock_transfers_rbac():
    sales_token = get_token("sales")
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    payload = {
        "from_warehouse_id": "WH01",
        "to_warehouse_id": "WH02",
        "items": [{"product_id": 1, "transfer_qty": 5}]
    }
    # Sales không có quyền ghi kho -> 403 Forbidden
    resp = client.post("/api/v1/inventory/transfers", json=payload, headers=sales_headers)
    assert resp.status_code == 403
