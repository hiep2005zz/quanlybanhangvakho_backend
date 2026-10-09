# backend/tests/test_goods_receipts.py
"""
Test Suite cho tính năng: Lập phiếu nhập kho từ nhà cung cấp (Goods Receipt Note - GRN).

Bao phủ toàn diện các yêu cầu nghiệp vụ:
1. Lưu nháp (DRAFT): Tạo mới hoặc sửa nội dung, tuyệt đối KHÔNG cộng hay làm biến động số lượng tồn kho.
2. Quản lý lô: Kiểm tra cờ is_batch_managed của sản phẩm.
   - True: bắt buộc phải có batch_number và expiry_date.
   - False: cho phép để trống.
3. Quy đổi đơn vị tính (UOM): Tự động tra cứu conversion_rate và tính base_quantity = quantity * conversion_rate.
4. Xác nhận phiếu (CONFIRMED):
   - Chuyển trạng thái sang CONFIRMED trong một Database Transaction duy nhất.
   - Cộng tồn kho tổng theo base_quantity.
   - Cập nhật tồn kho theo lô trong ProductBatch.
   - Ghi nhật ký thẻ kho/sổ kho (InventoryLedger), InventoryTransaction và AuditLog.
5. Tính bất biến: Khi phiếu đã CONFIRMED, chặn toàn bộ thao tác SỬA (PUT) hoặc XÓA (DELETE) bằng HTTP 400 Bad Request.
6. RBAC Zero-Trust: Quyền ghi kho (inventory:write) chỉ cấp cho admin, warehouse_manager, warehouse.
   Nhân viên kinh doanh (sales) tuyệt đối KHÔNG được ghi kho (HTTP 403 Forbidden).
7. Hỗ trợ cả hai URL prefix: /api/v1/goods-receipts và /goods-receipts.
"""
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import ProductEntity, InventoryTransactionEntity, AuditLogEntity
from app.models.supplier import SupplierEntity
from app.models.goods_receipt import (
    WarehouseEntity,
    UnitOfMeasureEntity,
    GoodsReceiptNoteEntity,
    GoodsReceiptNoteItemEntity,
    ProductBatchEntity,
    InventoryLedgerEntity,
)

client = TestClient(app)


def get_token(username: str, password: str = "123") -> str:
    """Helper lấy JWT access token qua endpoint đăng nhập."""
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Đăng nhập thất bại cho {username}: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture
def grn_test_setup():
    """Chuẩn bị dữ liệu mẫu (nhà cung cấp, kho, sản phẩm) cho test suite."""
    db = SessionLocal()
    p_normal = None
    p_batch = None
    ncc = None
    kho = None
    try:
        # 1. Đảm bảo có nhà cung cấp hoạt động
        ncc = db.query(SupplierEntity).filter(SupplierEntity.code == "NCC_TEST_GRN").first()
        if not ncc:
            ncc = SupplierEntity(
                code="NCC_TEST_GRN",
                name="Công Ty Cung Ứng Thực Phẩm Sạch Hà Nội",
                contact_person="Nguyễn Văn Đại",
                is_active=True,
            )
            db.add(ncc)
            db.commit()
            db.refresh(ncc)

        # 2. Đảm bảo có kho nhận hàng
        kho = db.query(WarehouseEntity).filter(WarehouseEntity.code == "KHO_TEST_GRN").first()
        if not kho:
            kho = WarehouseEntity(
                code="KHO_TEST_GRN",
                name="Kho Tổng GRN Test",
                address="Khu Công Nghiệp Đài Tư, Long Biên, Hà Nội",
                is_active=True,
            )
            db.add(kho)
            db.commit()
            db.refresh(kho)

        # 3. Sản phẩm thường (không quản lý lô)
        p_normal = db.query(ProductEntity).filter(ProductEntity.code == "SP_GRN_NORMAL").first()
        if not p_normal:
            p_normal = ProductEntity(
                code="SP_GRN_NORMAL",
                name="Nước Tăng Lực RedBull 250ml",
                category_id=1,
                sell_price=15000.0,
                cost_price=10000.0,
                stock=50,
                base_unit="Lon",
                units_json='[{"unit_name": "Lốc", "conversion_rate": 6.0}, {"unit_name": "Thùng", "conversion_rate": 24.0}]',
                is_batch_managed=False,
                status="active",
            )
            db.add(p_normal)
            db.commit()
            db.refresh(p_normal)
        else:
            p_normal.is_batch_managed = False
            p_normal.stock = 50
            p_normal.base_unit = "Lon"
            p_normal.units_json = '[{"unit_name": "Lốc", "conversion_rate": 6.0}, {"unit_name": "Thùng", "conversion_rate": 24.0}]'
            db.commit()

        # 4. Sản phẩm quản lý lô (is_batch_managed = True)
        p_batch = db.query(ProductEntity).filter(ProductEntity.code == "SP_GRN_BATCH").first()
        if not p_batch:
            p_batch = ProductEntity(
                code="SP_GRN_BATCH",
                name="Sữa Chua Lên Men Tự Nhiên Vinamilk 100g",
                category_id=1,
                sell_price=8000.0,
                cost_price=5000.0,
                stock=20,
                base_unit="Hộp",
                units_json='[{"unit_name": "Lốc", "conversion_rate": 4.0}, {"unit_name": "Thùng", "conversion_rate": 48.0}]',
                is_batch_managed=True,
                status="active",
            )
            db.add(p_batch)
            db.commit()
            db.refresh(p_batch)
        else:
            p_batch.is_batch_managed = True
            p_batch.stock = 20
            p_batch.base_unit = "Hộp"
            p_batch.units_json = '[{"unit_name": "Lốc", "conversion_rate": 4.0}, {"unit_name": "Thùng", "conversion_rate": 48.0}]'
            db.commit()

        yield {
            "supplier_id": ncc.id,
            "warehouse_id": kho.id,
            "product_normal_id": p_normal.id,
            "product_batch_id": p_batch.id,
        }
    finally:
        # Dọn dẹp dữ liệu test GRN
        prod_ids = [p.id for p in [p_normal, p_batch] if p is not None]
        if prod_ids:
            db.query(GoodsReceiptNoteItemEntity).filter(
                GoodsReceiptNoteItemEntity.product_id.in_(prod_ids)
            ).delete(synchronize_session=False)
            db.query(ProductBatchEntity).filter(
                ProductBatchEntity.product_id.in_(prod_ids)
            ).delete(synchronize_session=False)
            db.query(InventoryLedgerEntity).filter(
                InventoryLedgerEntity.product_id.in_(prod_ids)
            ).delete(synchronize_session=False)
        if ncc:
            db.query(GoodsReceiptNoteEntity).filter(
                GoodsReceiptNoteEntity.supplier_id == ncc.id
            ).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_create_draft_goods_receipt_does_not_change_stock(grn_test_setup):
    """
    Yêu cầu:
    Trạng thái DRAFT: Tạo mới hoặc sửa nội dung. Tuyệt đối KHÔNG cộng hay làm biến động số lượng tồn kho.
    """
    token = get_token("kho")
    headers = {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    p_norm = db.query(ProductEntity).filter(ProductEntity.id == grn_test_setup["product_normal_id"]).first()
    initial_stock = p_norm.stock
    db.close()

    payload = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-NCC-2026-001",
        "receipt_date": datetime.now(timezone.utc).isoformat(),
        "note": "Nhập hàng thử nghiệm phiếu nháp",
        "items": [
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Thùng",
                "quantity": 5.0,
                "unit_price": 240000.0,
                "note": "5 thùng RedBull",
            }
        ],
    }

    resp = client.post("/api/v1/goods-receipts", json=payload, headers=headers)
    assert resp.status_code == 201, f"Tạo phiếu thất bại: {resp.text}"
    grn_data = resp.json()

    assert grn_data["status"] == "DRAFT"
    assert grn_data["code"].startswith("GRN-")
    assert grn_data["total_items"] == 1
    # 5 Thùng x 24 = 120 lon
    assert grn_data["total_quantity"] == 120.0
    grn_id = grn_data["id"]

    # Kiểm tra tồn kho trong cơ sở dữ liệu KHÔNG được thay đổi
    db = SessionLocal()
    p_norm_after = db.query(ProductEntity).filter(ProductEntity.id == grn_test_setup["product_normal_id"]).first()
    assert p_norm_after.stock == initial_stock, "Lỗi: Phiếu DRAFT đã làm biến động tồn kho!"

    # Kiểm tra thẻ kho và lô hàng KHÔNG có bản ghi nào liên quan đến phiếu DRAFT
    ledgers_count = db.query(InventoryLedgerEntity).filter(InventoryLedgerEntity.receipt_note_id == grn_id).count()
    assert ledgers_count == 0, "Lỗi: Phiếu DRAFT không được ghi nhận thẻ kho!"
    db.close()

    # Thử cập nhật nội dung phiếu nháp
    update_payload = {
        "note": "Ghi chú đã được cập nhật khi còn nháp",
        "reference_number": "HD-NCC-2026-001-REV1",
    }
    put_resp = client.put(f"/api/v1/goods-receipts/{grn_id}", json=update_payload, headers=headers)
    assert put_resp.status_code == 200
    assert put_resp.json()["note"] == "Ghi chú đã được cập nhật khi còn nháp"
    assert put_resp.json()["reference_number"] == "HD-NCC-2026-001-REV1"


def test_batch_managed_validation(grn_test_setup):
    """
    Yêu cầu:
    Quản lý lô: Kiểm tra cờ is_batch_managed của sản phẩm.
    - True: bắt buộc phải có batch_number và expiry_date.
    - False: cho phép để trống.
    """
    token = get_token("warehouse_mgr")
    headers = {"Authorization": f"Bearer {token}"}

    # Case 1: Sản phẩm is_batch_managed=True nhưng thiếu batch_number -> Bị chặn 400 Bad Request
    payload_missing_batch = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-NCC-BATCH-01",
        "items": [
            {
                "product_id": grn_test_setup["product_batch_id"],
                "unit_name": "Hộp",
                "quantity": 10.0,
                "expiry_date": "2027-12-31T00:00:00Z",
                # batch_number bị bỏ trống
            }
        ],
    }
    resp1 = client.post("/api/v1/goods-receipts", json=payload_missing_batch, headers=headers)
    assert resp1.status_code == 400
    assert "is_batch_managed=True" in resp1.json()["detail"]
    assert "batch_number" in resp1.json()["detail"]

    # Case 2: Sản phẩm is_batch_managed=True nhưng thiếu expiry_date -> Bị chặn 400 Bad Request
    payload_missing_expiry = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-NCC-BATCH-02",
        "items": [
            {
                "product_id": grn_test_setup["product_batch_id"],
                "unit_name": "Hộp",
                "quantity": 10.0,
                "batch_number": "LOT-2026-001",
                # expiry_date bị bỏ trống
            }
        ],
    }
    resp2 = client.post("/api/v1/goods-receipts", json=payload_missing_expiry, headers=headers)
    assert resp2.status_code == 400
    assert "expiry_date" in resp2.json()["detail"]

    # Case 3: Sản phẩm is_batch_managed=False, không truyền batch_number & expiry_date -> Hợp lệ 201 Created
    payload_non_batch_valid = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-NCC-NONBATCH",
        "items": [
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Lon",
                "quantity": 10.0,
            }
        ],
    }
    resp3 = client.post("/api/v1/goods-receipts", json=payload_non_batch_valid, headers=headers)
    assert resp3.status_code == 201


def test_uom_conversion_rate_calculation(grn_test_setup):
    """
    Yêu cầu:
    Dòng hàng (Items): Cho phép nhập theo đơn vị tính bất kỳ (UOM).
    Hệ thống bắt buộc tự động tra cứu tỷ lệ quy đổi (conversion_rate) và tính ra số lượng đơn vị cơ sở:
    base_quantity = quantity * conversion_rate để ghi nhận dữ liệu lưu trữ/tồn kho.
    """
    token = get_token("kho")
    headers = {"Authorization": f"Bearer {token}"}

    # SP_GRN_NORMAL có:
    # Base unit: "Lon"
    # Lốc: conversion_rate = 6.0
    # Thùng: conversion_rate = 24.0
    payload = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-UOM-CONVERT-01",
        "items": [
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Thùng",
                "quantity": 2.5,
                "unit_price": 240000.0,
            },
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Lốc",
                "quantity": 10.0,
                "unit_price": 60000.0,
            },
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Lon",
                "quantity": 8.0,
                "unit_price": 10000.0,
            },
        ],
    }

    resp = client.post("/api/v1/goods-receipts", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()

    items = data["items"]
    assert len(items) == 3

    # Dòng 1: 2.5 Thùng x 24 = 60 Lon
    item1 = items[0]
    assert item1["unit_name"] == "Thùng"
    assert item1["conversion_rate"] == 24.0
    assert item1["base_quantity"] == 60.0

    # Dòng 2: 10 Lốc x 6 = 60 Lon
    item2 = items[1]
    assert item2["unit_name"] == "Lốc"
    assert item2["conversion_rate"] == 6.0
    assert item2["base_quantity"] == 60.0

    # Dòng 3: 8 Lon x 1 = 8 Lon
    item3 = items[2]
    assert item3["unit_name"] == "Lon"
    assert item3["conversion_rate"] == 1.0
    assert item3["base_quantity"] == 8.0

    # Tổng số lượng quy về đơn vị cơ sở: 60 + 60 + 8 = 128 Lon
    assert data["total_quantity"] == 128.0


def test_confirm_goods_receipt_increases_stock_and_lot_and_ledger(grn_test_setup):
    """
    Yêu cầu:
    CONFIRMED: Khi người dùng bấm xác nhận, thực thi trong một Database Transaction duy nhất:
    - Chuyển trạng thái sang CONFIRMED.
    - Cộng tồn kho theo base_quantity vào kho tương ứng (cả tồn tổng và tồn theo số lô).
    - Ghi nhật ký thẻ kho/sổ kho (InventoryLedger), InventoryTransaction, và AuditLog.
    """
    token = get_token("kho")
    headers = {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    p_norm = db.query(ProductEntity).filter(ProductEntity.id == grn_test_setup["product_normal_id"]).first()
    p_batch = db.query(ProductEntity).filter(ProductEntity.id == grn_test_setup["product_batch_id"]).first()
    norm_start_stock = p_norm.stock
    batch_start_stock = p_batch.stock
    db.close()

    batch_code = "LOT-VINAMILK-2026A"
    batch_exp = "2027-06-30T00:00:00Z"

    payload = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-CONFIRM-TRANSACTION-01",
        "note": "Nhập kho chính thức xác nhận",
        "items": [
            # SP thường: 2 Thùng x 24 = 48 Lon
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Thùng",
                "quantity": 2.0,
                "unit_price": 240000.0,
            },
            # SP theo lô: 1 Thùng x 48 = 48 Hộp
            {
                "product_id": grn_test_setup["product_batch_id"],
                "unit_name": "Thùng",
                "quantity": 1.0,
                "unit_price": 200000.0,
                "batch_number": batch_code,
                "expiry_date": batch_exp,
            },
        ],
    }

    # 1. Tạo phiếu nháp
    create_resp = client.post("/api/v1/goods-receipts", json=payload, headers=headers)
    assert create_resp.status_code == 201
    grn_id = create_resp.json()["id"]

    # 2. Bấm xác nhận phiếu
    confirm_resp = client.post(f"/api/v1/goods-receipts/{grn_id}/confirm", headers=headers)
    assert confirm_resp.status_code == 200
    confirmed_data = confirm_resp.json()

    assert confirmed_data["status"] == "CONFIRMED"
    assert confirmed_data["confirmed_by"] is not None
    assert confirmed_data["confirmed_at"] is not None

    # 3. Kiểm tra biến động tồn kho thực tế trong DB
    db = SessionLocal()
    p_norm_after = db.query(ProductEntity).filter(ProductEntity.id == grn_test_setup["product_normal_id"]).first()
    p_batch_after = db.query(ProductEntity).filter(ProductEntity.id == grn_test_setup["product_batch_id"]).first()

    assert p_norm_after.stock == norm_start_stock + 48, f"Tồn kho SP thường phải tăng 48: {p_norm_after.stock}"
    assert p_batch_after.stock == batch_start_stock + 48, f"Tồn kho SP lô phải tăng 48: {p_batch_after.stock}"

    # 4. Kiểm tra tồn kho theo lô trong bảng ProductBatchEntity
    lot_rec = (
        db.query(ProductBatchEntity)
        .filter(
            ProductBatchEntity.product_id == grn_test_setup["product_batch_id"],
            ProductBatchEntity.batch_number == batch_code,
            ProductBatchEntity.warehouse_id == grn_test_setup["warehouse_id"],
        )
        .first()
    )
    assert lot_rec is not None, "Không tìm thấy bản ghi số lô trong ProductBatchEntity!"
    assert lot_rec.quantity == 48.0

    # 5. Kiểm tra ghi nhận thẻ kho (InventoryLedgerEntity)
    ledgers = (
        db.query(InventoryLedgerEntity)
        .filter(InventoryLedgerEntity.receipt_note_id == grn_id)
        .all()
    )
    assert len(ledgers) == 2, f"Thẻ kho phải có 2 dòng tương ứng 2 items, thực tế: {len(ledgers)}"
    for lg in ledgers:
        assert lg.transaction_type == "RECEIPT"
        assert lg.reference_code == confirmed_data["code"]
        assert lg.quantity == 48.0
        assert lg.new_stock == lg.previous_stock + 48.0

    # 6. Kiểm tra ghi nhận InventoryTransactionEntity
    tx_list = (
        db.query(InventoryTransactionEntity)
        .filter(InventoryTransactionEntity.reason.contains(confirmed_data["code"]))
        .all()
    )
    assert len(tx_list) == 2

    # 7. Kiểm tra Audit Log
    audit = (
        db.query(AuditLogEntity)
        .filter(
            AuditLogEntity.action_type == "GOODS_RECEIPT_CONFIRM",
            AuditLogEntity.entity_id == confirmed_data["code"],
        )
        .first()
    )
    assert audit is not None
    db.close()


def test_confirmed_goods_receipt_is_immutable(grn_test_setup):
    """
    Yêu cầu:
    Tính bất biến: Khi phiếu đã ở trạng thái CONFIRMED, chặn toàn bộ thao tác SỬA (PUT) hoặc XÓA (DELETE).
    Nếu người dùng cố tình thao tác, trả về lỗi HTTP 400 Bad Request kèm thông báo rõ ràng
    (không được sửa trực tiếp, phải lập phiếu điều chỉnh).
    """
    token = get_token("admin")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-IMMUTABLE-01",
        "items": [
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Lon",
                "quantity": 10.0,
            }
        ],
    }

    # 1. Tạo và xác nhận phiếu
    res_create = client.post("/api/v1/goods-receipts", json=payload, headers=headers)
    assert res_create.status_code == 201
    grn_id = res_create.json()["id"]

    res_confirm = client.post(f"/api/v1/goods-receipts/{grn_id}/confirm", headers=headers)
    assert res_confirm.status_code == 200

    # 2. Cố tình SỬA (PUT) phiếu đã CONFIRMED -> Bị chặn 400 Bad Request
    put_res = client.put(
        f"/api/v1/goods-receipts/{grn_id}",
        json={"note": "Cố tình sửa nội dung phiếu đã xác nhận"},
        headers=headers,
    )
    assert put_res.status_code == 400
    assert "Không thể chỉnh sửa phiếu nhập kho đã xác nhận" in put_res.json()["detail"]
    assert "phiếu điều chỉnh" in put_res.json()["detail"]

    # 3. Cố tình XÓA (DELETE) phiếu đã CONFIRMED -> Bị chặn 400 Bad Request
    del_res = client.delete(f"/api/v1/goods-receipts/{grn_id}", headers=headers)
    assert del_res.status_code == 400
    assert "Không thể xóa phiếu nhập kho đã xác nhận" in del_res.json()["detail"]
    assert "phiếu điều chỉnh" in del_res.json()["detail"]

    # 4. Cố tình XÁC NHẬN lại phiếu đã CONFIRMED -> Bị chặn 400 Bad Request
    reconfirm_res = client.post(f"/api/v1/goods-receipts/{grn_id}/confirm", headers=headers)
    assert reconfirm_res.status_code == 400
    assert "đã được xác nhận trước đó" in reconfirm_res.json()["detail"]


def test_sales_role_forbidden_from_goods_receipts(grn_test_setup):
    """
    Ràng buộc RBAC:
    Quyền ghi kho (inventory:write) CHỈ cấp cho admin, warehouse_manager, warehouse.
    Nhân viên kinh doanh (sales) TUYỆT ĐỐI KHÔNG được ghi kho -> HTTP 403 Forbidden.
    """
    sales_token = get_token("sales")
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    payload = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-SALES-ATTEMPT",
        "items": [
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Lon",
                "quantity": 10.0,
            }
        ],
    }

    # Sales cố tình tạo phiếu nhập kho -> 403 Forbidden
    resp = client.post("/api/v1/goods-receipts", json=payload, headers=sales_headers)
    assert resp.status_code == 403

    # Tạo trước 1 phiếu nháp bởi kho để test quyền xác nhận và sửa của sales
    kho_token = get_token("kho")
    kho_headers = {"Authorization": f"Bearer {kho_token}"}
    created = client.post("/api/v1/goods-receipts", json=payload, headers=kho_headers).json()
    grn_id = created["id"]

    # Sales cố tình sửa phiếu -> 403 Forbidden
    resp_put = client.put(f"/api/v1/goods-receipts/{grn_id}", json={"note": "hacked"}, headers=sales_headers)
    assert resp_put.status_code == 403

    # Sales cố tình xác nhận phiếu -> 403 Forbidden
    resp_confirm = client.post(f"/api/v1/goods-receipts/{grn_id}/confirm", headers=sales_headers)
    assert resp_confirm.status_code == 403

    # Sales cố tình xóa phiếu -> 403 Forbidden
    resp_delete = client.delete(f"/api/v1/goods-receipts/{grn_id}", headers=sales_headers)
    assert resp_delete.status_code == 403


def test_endpoints_without_api_v1_prefix(grn_test_setup):
    """
    Kiểm tra tính tương thích khi gọi trực tiếp đường dẫn theo đúng yêu cầu bài toán:
    - POST /goods-receipts
    - GET /goods-receipts/{id}
    - PUT /goods-receipts/{id}
    - POST /goods-receipts/{id}/confirm
    """
    token = get_token("kho")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "supplier_id": grn_test_setup["supplier_id"],
        "warehouse_id": grn_test_setup["warehouse_id"],
        "reference_number": "HD-DIRECT-PATH",
        "items": [
            {
                "product_id": grn_test_setup["product_normal_id"],
                "unit_name": "Lon",
                "quantity": 5.0,
            }
        ],
    }

    # 1. POST /goods-receipts
    res_create = client.post("/goods-receipts", json=payload, headers=headers)
    assert res_create.status_code == 201
    grn_id = res_create.json()["id"]

    # 2. GET /goods-receipts/{id}
    res_get = client.get(f"/goods-receipts/{grn_id}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["id"] == grn_id

    # 3. PUT /goods-receipts/{id}
    res_put = client.put(f"/goods-receipts/{grn_id}", json={"note": "Cập nhật qua direct route"}, headers=headers)
    assert res_put.status_code == 200
    assert res_put.json()["note"] == "Cập nhật qua direct route"

    # 4. POST /goods-receipts/{id}/confirm
    res_confirm = client.post(f"/goods-receipts/{grn_id}/confirm", headers=headers)
    assert res_confirm.status_code == 200
    assert res_confirm.json()["status"] == "CONFIRMED"
