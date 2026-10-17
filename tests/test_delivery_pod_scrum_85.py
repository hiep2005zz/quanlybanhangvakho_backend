# backend/tests/test_delivery_pod_scrum_85.py
import pytest
import io
import json
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import DeliveryTripEntity, DeliveryStopEntity, OrderEntity
from app.core.security import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def warehouse_mgr_headers():
    token = create_access_token("warehouse_mgr", "warehouse_manager")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def warehouse_staff_headers():
    token = create_access_token("kho", "warehouse")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def driver_headers():
    token = create_access_token("taixe_01", "driver")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def customer_headers():
    token = create_access_token("muahang", "customer")
    return {"Authorization": f"Bearer {token}"}


def test_upload_pod_image_success_and_validation(client, warehouse_staff_headers, customer_headers):
    """
    SCRUM-85: Kiểm tra API POST /api/v1/deliveries/upload-pod
    - Nhân viên kho / Tài xế upload ảnh biên bản giao nhận có chữ ký (POD) hợp lệ (JPG/PNG).
    - Trả về status 200 kèm url ảnh tĩnh hợp lệ trong /uploads/pod/.
    - Chặn các file không phải ảnh (e.g. .txt, .exe) -> HTTP 400 Bad Request.
    - Chặn người dùng không có quyền (e.g. customer) -> HTTP 403 Forbidden.
    """
    # 1. Upload ảnh PNG hợp lệ
    png_data = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    res = client.post(
        "/api/v1/deliveries/upload-pod",
        headers=warehouse_staff_headers,
        files={"file": ("bien_ban_pod_test.png", png_data, "image/png")}
    )
    assert res.status_code == 200, f"Error: {res.text}"
    data = res.json()
    assert "url" in data
    assert data["url"].startswith("/uploads/pod/pod_")
    assert data["status"] == "success"

    # 2. Chặn file không phải ảnh
    txt_data = io.BytesIO(b"Hello world invalid file")
    res_invalid = client.post(
        "/api/v1/deliveries/upload-pod",
        headers=warehouse_staff_headers,
        files={"file": ("invalid_file.txt", txt_data, "text/plain")}
    )
    assert res_invalid.status_code == 400
    assert "Định dạng tệp không hợp lệ" in res_invalid.text

    # 3. Chặn quyền customer
    res_forbidden = client.post(
        "/api/v1/deliveries/upload-pod",
        headers=customer_headers,
        files={"file": ("bien_ban_pod_test.png", png_data, "image/png")}
    )
    assert res_forbidden.status_code == 403


def test_confirm_delivery_success_with_pod_and_sprint7_invoice_trigger(client, warehouse_staff_headers):
    """
    SCRUM-85: Kiểm tra API POST /api/v1/deliveries/stops/{stop_id}/confirm-delivery
    - Ghi nhận người nhận, thời điểm giao thực tế, link ảnh biên bản bàn giao (POD).
    - Cập nhật điểm giao sang DELIVERED.
    - Cập nhật trạng thái Đơn hàng liên quan sang DELIVERED (kích hoạt mở khóa xuất hóa đơn ở Sprint 7).
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        # Tạo Order mẫu
        order = OrderEntity(
            order_code="DH-TEST-POD-001",
            dealer_id=1,
            dealer_name="Đại Lý Test POD",
            created_by="sale_admin",
            total_amount=25000000.0,
            status="SHIPPED",
            items_json=json.dumps([{"product_code": "SP001", "product_name": "Áo sơ mi nam Oxford", "quantity": 10, "unit": "Cái"}]),
        )
        db.add(order)
        db.flush()

        # Tạo Chuyến và Điểm giao mẫu
        trip = DeliveryTripEntity(
            trip_code=f"CGH-TEST-POD-{now.strftime('%Y%m%d%H%M%S')}",
            driver_name="Tài xế Nguyễn Văn A",
            vehicle_plate="29A-123.45",
            warehouse_id="1",
            warehouse_name="Kho Tổng Hà Nội",
            status="IN_TRANSIT",
            dispatched_at=now - timedelta(hours=1),
            estimated_delivery_time=now + timedelta(hours=2),
            created_by="kho",
        )
        db.add(trip)
        db.flush()

        stop = DeliveryStopEntity(
            trip_id=trip.id,
            stop_order=1,
            order_code=order.order_code,
            order_id=order.id,
            dealer_name=order.dealer_name,
            delivery_point="120 Cầu Giấy, Hà Nội",
            receiver_name="Người nhận dự kiến",
            receiver_phone="0987654321",
            amount=25000000.0,
            status="IN_TRANSIT",
            delivery_status="Pending",
        )
        db.add(stop)
        db.commit()
        db.refresh(stop)
        stop_id = stop.id
        order_id = order.id
        trip_id = trip.id
    finally:
        db.close()

    # Gọi API xác nhận giao thành công
    actual_time = (now - timedelta(minutes=15)).isoformat()
    pod_url = "/uploads/pod/pod_test_sample_01.png"
    payload = {
        "receiver_name": "Anh Nguyễn Văn Hùng (Chủ đại lý)",
        "actual_delivery_time": actual_time,
        "pod_image_url": pod_url,
        "items_detail": [
            {"product_code": "SP001", "product_name": "Áo sơ mi nam Oxford", "quantity": 10, "delivered_quantity": 10, "unit": "Cái"}
        ],
        "note": "Khách đã kiểm đủ hàng và ký biên bản nhận",
    }

    res = client.post(f"/api/v1/deliveries/stops/{stop_id}/confirm-delivery", headers=warehouse_staff_headers, json=payload)
    assert res.status_code == 200, f"Error: {res.text}"
    data = res.json()

    assert data["id"] == stop_id
    assert data["status"] == "DELIVERED"
    assert data["delivery_status"] == "Delivered"
    assert data["receiver_name"] == "Anh Nguyễn Văn Hùng (Chủ đại lý)"
    assert data["pod_image_url"] == pod_url
    assert data["actual_delivery_time"] is not None
    assert len(data["items_detail"]) == 1
    assert data["items_detail"][0]["delivered_quantity"] == 10

    # Kiểm tra DB: Đơn hàng liên quan đã được chuyển sang DELIVERED để phục vụ Sprint 7
    verify_db = SessionLocal()
    try:
        updated_order = verify_db.query(OrderEntity).filter(OrderEntity.id == order_id).first()
        assert updated_order is not None
        assert updated_order.status == "DELIVERED", f"Order status must be DELIVERED, got: {updated_order.status}"

        # Chuyến giao 1 điểm này toàn bộ đã xong -> Chuyến tự động chuyển COMPLETED
        updated_trip = verify_db.query(DeliveryTripEntity).filter(DeliveryTripEntity.id == trip_id).first()
        assert updated_trip.status == "COMPLETED"
    finally:
        verify_db.close()


def test_record_delivery_failure_with_reason_and_awaiting_return(client, warehouse_staff_headers):
    """
    SCRUM-85: Kiểm tra API POST /api/v1/deliveries/stops/{stop_id}/record-failure
    - Khi điểm giao không thành công: ghi nhận lý do thất bại.
    - Cập nhật điểm giao sang FAILED (delivery_status = 'Failed').
    - Ghi nhận hàng chuyển sang trạng thái chờ hoàn kho (return_status = 'CHỜ_HOÀN_KHO').
    - Chuẩn bị dữ liệu cho quy trình xử lý đổi trả ở Sprint 8 và không làm âm tồn kho.
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        trip = DeliveryTripEntity(
            trip_code=f"CGH-TEST-FAIL-{now.strftime('%Y%m%d%H%M%S')}",
            driver_name="Tài xế Trần Văn B",
            vehicle_plate="29B-456.78",
            warehouse_id="1",
            warehouse_name="Kho Tổng Hà Nội",
            status="IN_TRANSIT",
            dispatched_at=now - timedelta(hours=1),
            estimated_delivery_time=now + timedelta(hours=2),
            created_by="kho",
        )
        db.add(trip)
        db.flush()

        stop = DeliveryStopEntity(
            trip_id=trip.id,
            stop_order=1,
            order_code="DH-TEST-FAIL-001",
            dealer_name="Đại Lý Thất Bại Test",
            delivery_point="500 Quang Trung, Hà Đông, Hà Nội",
            amount=12000000.0,
            status="IN_TRANSIT",
            delivery_status="Pending",
        )
        db.add(stop)
        db.commit()
        db.refresh(stop)
        stop_id = stop.id
    finally:
        db.close()

    # Gọi API báo giao hàng thất bại
    payload = {
        "failure_reason": "Đại lý đóng cửa / Chưa đến giờ nhận hàng",
        "note": "Tài xế đã gọi 3 cuộc không nghe máy",
    }
    res = client.post(f"/api/v1/deliveries/stops/{stop_id}/record-failure", headers=warehouse_staff_headers, json=payload)
    assert res.status_code == 200, f"Error: {res.text}"
    data = res.json()

    assert data["id"] == stop_id
    assert data["status"] == "FAILED"
    assert data["delivery_status"] == "Failed"
    assert data["failure_reason"] == "Đại lý đóng cửa / Chưa đến giờ nhận hàng"
    assert data["return_status"] == "CHỜ_HOÀN_KHO"

    # Xác thực trong DB
    verify_db = SessionLocal()
    try:
        updated_stop = verify_db.query(DeliveryStopEntity).filter(DeliveryStopEntity.id == stop_id).first()
        assert updated_stop.status == "FAILED"
        assert updated_stop.return_status == "CHỜ_HOÀN_KHO"
        assert updated_stop.failure_reason == "Đại lý đóng cửa / Chưa đến giờ nhận hàng"
    finally:
        verify_db.close()
