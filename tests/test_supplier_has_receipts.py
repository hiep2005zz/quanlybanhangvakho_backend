# backend/tests/test_supplier_has_receipts.py
"""
Kiểm tra tính năng: Khóa xóa nhà cung cấp khi đã có phiếu nhập kho (Goods Receipt Note).
- Nếu nhà cung cấp chưa có phiếu nhập: has_receipts = False, cho phép xóa.
- Nếu nhà cung cấp đã có phiếu nhập (qua goods_receipt_notes hoặc inventory_transactions):
  has_receipts = True, chặn xóa với HTTP 400 và yêu cầu chỉ ngừng giao dịch.
"""
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.supplier import SupplierEntity
from app.models.goods_receipt import WarehouseEntity, GoodsReceiptNoteEntity

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Đăng nhập thất bại: {resp.text}"
    return resp.json()["access_token"]


def test_supplier_without_receipts_can_be_deleted():
    token = get_token("admin", "123")
    headers = {"Authorization": f"Bearer {token}"}
    db = SessionLocal()

    code = "NCC_TEST_DEL_OK"
    tax = "9998881110"
    try:
        # Xóa tàn dư cũ nếu có
        existing = db.query(SupplierEntity).filter(SupplierEntity.code == code).first()
        if existing:
            db.delete(existing)
            db.commit()

        # Tạo mới NCC
        create_resp = client.post(
            "/api/v1/suppliers",
            headers=headers,
            json={
                "code": code,
                "name": "Nhà cung cấp test xóa",
                "tax_code": tax,
                "contact_person": "Nguyen Van Test",
            },
        )
        assert create_resp.status_code == 201, create_resp.text

        # Lấy thông tin chi tiết NCC -> has_receipts phải là False
        detail_resp = client.get(f"/api/v1/suppliers/{code}", headers=headers)
        assert detail_resp.status_code == 200
        assert detail_resp.json()["has_receipts"] is False

        # Xóa NCC -> thành công
        del_resp = client.delete(f"/api/v1/suppliers/{code}", headers=headers)
        assert del_resp.status_code == 200
        assert "thành công" in del_resp.json()["message"]
    finally:
        db.close()


def test_supplier_with_goods_receipt_cannot_be_deleted():
    token = get_token("admin", "123")
    headers = {"Authorization": f"Bearer {token}"}
    db = SessionLocal()

    code = "NCC_TEST_HAS_GRN"
    tax = "9998882220"
    grn_code = "GRN-TEST-SUP-01"
    try:
        # Chuẩn bị kho
        wh = db.query(WarehouseEntity).filter(WarehouseEntity.code == "WH_TEST_SUP").first()
        if not wh:
            wh = WarehouseEntity(code="WH_TEST_SUP", name="Kho Test NCC", is_active=True)
            db.add(wh)
            db.commit()
            db.refresh(wh)

        # Xóa tàn dư phiếu nhập cũ nếu có
        old_grn = db.query(GoodsReceiptNoteEntity).filter(GoodsReceiptNoteEntity.code == grn_code).first()
        if old_grn:
            db.delete(old_grn)
            db.commit()

        # Tạo mới hoặc lấy NCC
        sup = db.query(SupplierEntity).filter(SupplierEntity.code == code).first()
        if not sup:
            create_resp = client.post(
                "/api/v1/suppliers",
                headers=headers,
                json={
                    "code": code,
                    "name": "Nhà cung cấp có phiếu nhập",
                    "tax_code": tax,
                    "contact_person": "Tran Van Test",
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            sup = db.query(SupplierEntity).filter(SupplierEntity.code == code).first()

        # Ban đầu chưa có GRN -> has_receipts phải là False
        detail_before = client.get(f"/api/v1/suppliers/{code}", headers=headers)
        assert detail_before.status_code == 200
        assert detail_before.json()["has_receipts"] is False

        # Thêm 1 phiếu nhập kho liên kết với supplier_id
        grn = GoodsReceiptNoteEntity(
            code=grn_code,
            supplier_id=sup.id,
            warehouse_id=wh.id,
            status="CONFIRMED",
            receipt_date=datetime.now(timezone.utc),
            total_items=1,
            total_quantity=10,
            total_amount=100000,
        )
        db.add(grn)
        db.commit()

        # Kiểm tra lại chi tiết NCC -> has_receipts phải chuyển thành True
        detail_after = client.get(f"/api/v1/suppliers/{code}", headers=headers)
        assert detail_after.status_code == 200
        assert detail_after.json()["has_receipts"] is True

        # Danh sách tổng cũng phải có has_receipts = True
        list_resp = client.get("/api/v1/suppliers", headers=headers)
        assert list_resp.status_code == 200
        matching = [item for item in list_resp.json()["items"] if item["code"] == code]
        assert len(matching) == 1
        assert matching[0]["has_receipts"] is True

        # Khi lọc has_receipts=false -> NCC này không được xuất hiện
        no_receipts_resp = client.get("/api/v1/suppliers?has_receipts=false", headers=headers)
        assert no_receipts_resp.status_code == 200
        assert not any(item["code"] == code for item in no_receipts_resp.json()["items"])

        # Khi lọc has_receipts=true -> NCC này phải xuất hiện
        has_receipts_resp = client.get("/api/v1/suppliers?has_receipts=true", headers=headers)
        assert has_receipts_resp.status_code == 200
        assert any(item["code"] == code for item in has_receipts_resp.json()["items"])

        # Thử xóa NCC -> Bắt buộc bị chặn HTTP 400
        del_resp = client.delete(f"/api/v1/suppliers/{code}", headers=headers)
        assert del_resp.status_code == 400
        assert "không xoá được, chỉ ngừng giao dịch" in del_resp.json()["detail"]
    finally:
        # Dọn dẹp dữ liệu test
        clean_grn = db.query(GoodsReceiptNoteEntity).filter(GoodsReceiptNoteEntity.code == grn_code).first()
        if clean_grn:
            db.delete(clean_grn)
            db.commit()
        clean_sup = db.query(SupplierEntity).filter(SupplierEntity.code == code).first()
        if clean_sup:
            db.delete(clean_sup)
            db.commit()
        db.close()
