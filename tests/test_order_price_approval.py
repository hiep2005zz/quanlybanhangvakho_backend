# backend/tests/test_order_price_approval.py
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import ProductEntity, DealerEntity

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

def test_order_creation_with_floor_price_and_approval():
    """
    Test luồng kết nối Bảng giá - Đơn hàng:
    1. Tra cứu giá tự động qua /api/v1/price-books/resolve-price
    2. Bán dưới giá sàn -> status = PENDING_APPROVAL, requires_approval = True
    3. Bán bằng hoặc trên giá sàn -> status = CONFIRMED, requires_approval = False
    4. Sales không có quyền tự duyệt đơn (403 Forbidden)
    5. Sales Manager và Admin có quyền duyệt đơn (/api/v1/orders/{id}/approve) -> status chuyển sang CONFIRMED
    """
    sales_token = get_token("sales")
    sm_token = get_token("sales_manager")
    admin_token = get_token("admin")
    kho_token = get_token("kho")

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    created_pb_id = None
    try:
        # Cập nhật dealer 1 thuộc nhóm Dai_ly_cap_1
        d = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if d:
            d.customer_group = "Dai_ly_cap_1"
            db.commit()

        # Tạo bảng giá hiệu lực cho nhóm Dai_ly_cap_1
        # SP 1: sale_price = 190.000, floor_price = 165.000
        pb = PriceBookEntity(
            code=f"BG-TEST-APPR-{int(now.timestamp())}",
            name="Bảng giá Test Kiểm Duyệt",
            customer_group="Dai_ly_cap_1",
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=30),
            status="ACTIVE",
            version=10,  # Version cao để ưu tiên
            is_locked=False,
            created_by="sales_manager"
        )
        db.add(pb)
        db.flush()
        created_pb_id = pb.id

        db.add(PriceBookItemEntity(
            price_book_id=pb.id,
            product_id=1,
            sale_price=190000.0,
            floor_price=165000.0
        ))
        db.commit()
    finally:
        db.close()

    try:
        # 1. Tra cứu giá qua resolve-price
        res_resolve = client.get(
            "/api/v1/price-books/resolve-price?customer_id=1&product_id=1",
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_resolve.status_code == 200
        resolve_data = res_resolve.json()
        assert resolve_data["sale_price"] == 190000.0
        assert resolve_data["floor_price"] == 165000.0

        # 2. Tạo đơn dưới giá sàn (bán 150.000 < floor 165.000)
        res_order_low = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 1, "price": 150000.0}],
                "note": "Đơn giá đặc biệt dưới giá sàn"
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_order_low.status_code == 201
        order_low_data = res_order_low.json()
        assert order_low_data["status"] == "PENDING_APPROVAL"
        assert order_low_data.get("requires_approval") is True
        assert "thấp hơn giá sàn" in (order_low_data.get("approval_reason") or "")
        order_id = order_low_data["id"]
        order_code = order_low_data["order_code"]

        # 3. Nhân viên sales cố tình tự duyệt đơn -> 403 Forbidden
        res_unauthorized_approve = client.post(
            f"/api/v1/orders/{order_code}/approve",
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_unauthorized_approve.status_code == 403

        # 4. Thủ kho không được duyệt đơn -> 403 Forbidden
        res_kho_approve = client.post(
            f"/api/v1/orders/{order_code}/approve",
            headers={"Authorization": f"Bearer {kho_token}"}
        )
        assert res_kho_approve.status_code == 403

        # 5. Sales manager duyệt đơn theo order_code -> 200 OK, status = CONFIRMED
        res_approve = client.post(
            f"/api/v1/orders/{order_code}/approve",
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_approve.status_code == 200
        approved_data = res_approve.json()
        assert approved_data["status"] == "CONFIRMED"
        assert approved_data.get("requires_approval") is False
        assert approved_data.get("approved_by") == "sales_manager"

        # 6. Tạo đơn thứ hai với giá hợp lệ (180.000 >= 165.000) -> status = CONFIRMED ngay lập tức
        res_order_ok = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 2, "price": 180000.0}],
                "note": "Đơn chuẩn giá"
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_order_ok.status_code == 201
        order_ok_data = res_order_ok.json()
        assert order_ok_data["status"] == "CONFIRMED"
        assert order_ok_data.get("requires_approval") is False

        # 7. Test duyệt đơn bằng ID số thay vì mã order_code
        res_order_low2 = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 1, "price": 140000.0}],
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_order_low2.status_code == 201
        order2_id = res_order_low2.json()["id"]
        res_admin_approve = client.post(
            f"/api/v1/orders/{order2_id}/approve",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res_admin_approve.status_code == 200
        assert res_admin_approve.json()["status"] == "CONFIRMED"

        # 8. Test từ chối duyệt đơn (reject)
        res_order_low3 = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 1, "price": 130000.0}],
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_order_low3.status_code == 201
        order3_code = res_order_low3.json()["order_code"]
        res_reject = client.post(
            f"/api/v1/orders/{order3_code}/reject",
            json={"reason": "Giá bán quá thấp, không chấp nhận"},
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_reject.status_code == 200
        assert res_reject.json()["status"] == "REJECTED"

        # 9. Test bán dưới giá niêm yết khi sản phẩm chưa có bảng giá riêng
        res_list_low = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 2, "quantity": 1, "price": 100000.0}],
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_list_low.status_code == 201
        list_low_data = res_list_low.json()
        assert list_low_data["status"] == "PENDING_APPROVAL"
        assert list_low_data.get("requires_approval") is True
        assert (
            "thấp hơn giá niêm yết" in (list_low_data.get("approval_reason") or "")
            or "thấp hơn giá sàn" in (list_low_data.get("approval_reason") or "")
        )

    finally:
        # Cleanup
        clean_db = SessionLocal()
        try:
            if created_pb_id:
                clean_db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == created_pb_id).delete()
                clean_db.query(PriceBookEntity).filter(PriceBookEntity.id == created_pb_id).delete()
                clean_db.commit()
        finally:
            clean_db.close()
