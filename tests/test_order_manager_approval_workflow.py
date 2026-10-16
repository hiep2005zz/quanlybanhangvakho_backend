import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.entities import OrderEntity, DealerEntity, AuditLogEntity

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]

def test_sales_manager_approval_workflow():
    sm_token = get_token("sales_manager")
    sales_token = get_token("sales")

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    created_pb_id = None
    try:
        # Đảm bảo đại lý 1 có nhóm và hạn mức
        d = db.query(DealerEntity).filter(DealerEntity.id == 1).first()
        if d:
            d.customer_group = "Dai_ly_cap_1"
            d.credit_limit = 10_000_000_000.0
            db.commit()

        # Tạo bảng giá có giá sàn
        pb = PriceBookEntity(
            code=f"BG-WF-{int(now.timestamp())}",
            name="Bảng giá Test Workflow Duyệt",
            customer_group="Dai_ly_cap_1",
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=30),
            status="ACTIVE",
            version=99,
            is_locked=False,
            created_by="sales_manager"
        )
        db.add(pb)
        db.flush()
        created_pb_id = pb.id

        db.add(PriceBookItemEntity(
            price_book_id=pb.id,
            product_id=1,
            sale_price=200000.0,
            floor_price=170000.0
        ))
        db.commit()
    finally:
        db.close()

    try:
        # 1. Sales tạo đơn bán dưới giá sàn (150.000 < 170.000)
        res_create = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 2, "price": 150000.0}],
                "note": "Đơn thử nghiệm duyệt"
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        assert res_create.status_code == 201
        order_data = res_create.json()
        assert order_data["status"] == "PENDING_APPROVAL"
        assert order_data["requires_approval"] is True
        assert "thấp hơn giá sàn" in order_data["approval_reason"]
        order_code = order_data["order_code"]

        # 2. Quản lý trả lại sửa (Return for edit) nhưng lý do ngắn < 5 ký tự -> 422
        res_return_short = client.post(
            f"/api/v1/orders/{order_code}/return-for-edit",
            json={"reason": "Sửa"},
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_return_short.status_code == 422

        # 3. Quản lý trả lại sửa với lý do hợp lệ >= 5 ký tự -> 200 OK
        return_msg = "Giá bán dưới sàn quá 10%, đề nghị điều chỉnh lại giá tối thiểu 170.000đ"
        res_return = client.post(
            f"/api/v1/orders/{order_code}/return-for-edit",
            json={"reason": return_msg},
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_return.status_code == 200
        ret_data = res_return.json()
        assert ret_data["status"] == "REVISION_REQUIRED"
        assert ret_data["requires_approval"] is True

        # 4. Kiểm tra lịch sử duyệt của đơn hàng
        res_hist = client.get(
            f"/api/v1/orders/{order_code}/approval-history",
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_hist.status_code == 200
        history = res_hist.json()
        assert len(history) >= 1
        return_event = next((h for h in history if h["action_type"] == "ORDER_RETURN_FOR_EDIT"), None)
        assert return_event is not None
        assert return_event["action_label"] == "Trả lại yêu cầu sửa"
        assert return_event["user_name"] in ("sales_manager", "Phạm Trưởng Phòng Sales")
        assert return_msg in return_event["reason"]

        # 5. Từ chối đơn nhưng lý do < 5 ký tự -> 422
        res_reject_short = client.post(
            f"/api/v1/orders/{order_code}/reject",
            json={"reason": "K"},
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_reject_short.status_code == 422

        # 6. Duyệt đơn (Approve) -> status chuyển thành CONFIRMED và giữ chỗ tồn kho
        res_approve = client.post(
            f"/api/v1/orders/{order_code}/approve",
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_approve.status_code == 200
        approved_data = res_approve.json()
        assert approved_data["status"] == "CONFIRMED"
        assert approved_data["requires_approval"] is False
        assert approved_data["approved_by"] == "sales_manager"

        # 7. Kiểm tra lại lịch sử duyệt đầy đủ
        res_hist2 = client.get(
            f"/api/v1/orders/{order_code}/approval-history",
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_hist2.status_code == 200
        history2 = res_hist2.json()
        approve_event = next((h for h in history2 if h["action_type"] == "ORDER_APPROVE"), None)
        assert approve_event is not None
        assert approve_event["action_label"] in ("Duyệt đơn hàng", "Phê duyệt đơn hàng")

        # 8. Tạo đơn khác và Từ chối (Reject) với lý do >= 5 ký tự
        res_create2 = client.post(
            "/api/v1/orders",
            json={
                "dealer_id": 1,
                "items": [{"product_id": 1, "quantity": 1, "price": 140000.0}],
            },
            headers={"Authorization": f"Bearer {sales_token}"}
        )
        order2_code = res_create2.json()["order_code"]
        reject_reason = "Không chấp nhận bán dưới giá sàn 170.000đ"
        res_reject = client.post(
            f"/api/v1/orders/{order2_code}/reject",
            json={"reason": reject_reason},
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_reject.status_code == 200
        assert res_reject.json()["status"] == "REJECTED"

        # Kiểm tra lịch sử từ chối
        res_hist3 = client.get(
            f"/api/v1/orders/{order2_code}/approval-history",
            headers={"Authorization": f"Bearer {sm_token}"}
        )
        assert res_hist3.status_code == 200
        reject_event = next((h for h in res_hist3.json() if h["action_type"] == "ORDER_REJECT"), None)
        assert reject_event is not None
        assert reject_event["action_label"] in ("Từ chối duyệt đơn", "Từ chối đơn hàng")
        assert reject_reason in reject_event["reason"]

    finally:
        clean_db = SessionLocal()
        try:
            d = clean_db.query(DealerEntity).filter(DealerEntity.id == 1).first()
            if d:
                d.credit_limit = 100_000_000.0
                clean_db.commit()
            from app.api.v1.endpoints.orders import DEALERS_DB
            if 1 in DEALERS_DB:
                DEALERS_DB[1].credit_limit = 100_000_000.0
            if created_pb_id:
                clean_db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == created_pb_id).delete()
                clean_db.query(PriceBookEntity).filter(PriceBookEntity.id == created_pb_id).delete()
                clean_db.commit()
        finally:
            clean_db.close()
