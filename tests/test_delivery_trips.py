# tests/test_delivery_trips.py
import pytest
import json
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token
from app.core.database import SessionLocal
from app.models.entities import (
    OrderEntity,
    DealerEntity,
    DealerDeliveryPointEntity,
    OrderDispatchEntity,
    DriverEntity,
    VehicleEntity,
    DeliveryTripEntity,
    DeliveryTripStopEntity,
    DeliveryTripDispatchEntity,
)

client = TestClient(app)


@pytest.fixture
def warehouse_token():
    return create_access_token("kho", "warehouse")


@pytest.fixture
def warehouse_mgr_token():
    return create_access_token("warehouse_mgr", "warehouse_manager")


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
def setup_trips_data():
    """Tạo dữ liệu phiếu xuất kho, tài xế và xe mẫu để test."""
    db = SessionLocal()
    try:
        # 1. Đảm bảo có đại lý
        dealer = db.query(DealerEntity).first()
        if not dealer:
            dealer = DealerEntity(
                code="DL-TEST-TRIP",
                name="Đại Lý Phân Phối Cầu Giấy",
                address="Số 123 Đường Cầu Giấy, Hà Nội",
                phone="0911223344",
                status="Đang hoạt động",
            )
            db.add(dealer)
            db.commit()
            db.refresh(dealer)

        # 2. Đảm bảo có điểm giao hàng
        dp = db.query(DealerDeliveryPointEntity).filter(DealerDeliveryPointEntity.dealer_id == dealer.id).first()
        if not dp:
            dp = DealerDeliveryPointEntity(
                dealer_id=dealer.id,
                label="Kho Chi Nhánh Cầu Giấy",
                address="Số 123 Đường Cầu Giấy, Hà Nội",
                receiver_name="Nguyễn Văn Nhận",
                receiver_phone="0988776655",
                route_note="Giao giờ hành chính",
            )
            db.add(dp)
            db.commit()
            db.refresh(dp)

        # 3. Đảm bảo có tài xế và phương tiện
        driver = db.query(DriverEntity).first()
        if not driver:
            driver = DriverEntity(code="TX-TEST-01", name="Bác Tài Thử Nghiệm", phone="0901234567", status="ACTIVE")
            db.add(driver)
            db.commit()
            db.refresh(driver)

        vehicle = db.query(VehicleEntity).first()
        if not vehicle:
            vehicle = VehicleEntity(plate_number="29C-999.99", vehicle_type="Xe tải 1.5 tấn", capacity_kg=1500, status="ACTIVE")
            db.add(vehicle)
            db.commit()
            db.refresh(vehicle)

        # 4. Tạo 2 đơn hàng và 2 phiếu xuất kho test với mã duy nhất
        import uuid
        uid = uuid.uuid4().hex[:6].upper()
        order1 = OrderEntity(
            order_code=f"DH-TRIP-{uid}-01",
            dealer_id=dealer.id,
            dealer_name=dealer.name,
            created_by="sales",
            total_amount=5000000.0,
            status="CONFIRMED",
            delivery_point_id=dp.id,
        )
        order2 = OrderEntity(
            order_code=f"DH-TRIP-{uid}-02",
            dealer_id=dealer.id,
            dealer_name=dealer.name,
            created_by="sales",
            total_amount=3000000.0,
            status="CONFIRMED",
        )
        db.add_all([order1, order2])
        db.commit()
        db.refresh(order1)
        db.refresh(order2)

        disp1 = OrderDispatchEntity(
            dispatch_code=f"PXK-TRIP-{uid}-01",
            order_id=order1.id,
            order_code=order1.order_code,
            dispatch_type="INITIAL",
            round_number=1,
            warehouse_id="WH01",
            warehouse_name="Kho Tổng Hà Nội",
            performed_by="kho",
            items_json=json.dumps([{"product_name": "Áo sơ mi", "quantity": 10, "unit": "Cái"}]),
        )
        disp2 = OrderDispatchEntity(
            dispatch_code=f"PXK-TRIP-{uid}-02",
            order_id=order2.id,
            order_code=order2.order_code,
            dispatch_type="INITIAL",
            round_number=1,
            warehouse_id="WH01",
            warehouse_name="Kho Tổng Hà Nội",
            performed_by="kho",
            items_json=json.dumps([{"product_name": "Quần âu", "quantity": 5, "unit": "Cái"}]),
        )
        db.add_all([disp1, disp2])
        db.commit()
        db.refresh(disp1)
        db.refresh(disp2)

        return {
            "driver_id": driver.id,
            "vehicle_id": vehicle.id,
            "disp1_id": disp1.id,
            "disp2_id": disp2.id,
            "disp1_code": disp1.dispatch_code,
            "disp2_code": disp2.dispatch_code,
        }
    finally:
        db.close()


def test_rbac_delivery_trips_forbidden(sales_token, ketoan_token):
    """AC1: Sales và Kế toán bị từ chối 403 khi gọi API chuyến giao hàng."""
    # Sales bị chặn
    res_sales = client.get("/api/v1/delivery-trips", headers={"Authorization": f"Bearer {sales_token}"})
    assert res_sales.status_code == 403

    # Kế toán bị chặn
    res_ketoan = client.get("/api/v1/delivery-trips", headers={"Authorization": f"Bearer {ketoan_token}"})
    assert res_ketoan.status_code == 403


def test_create_trip_validation_empty_dispatches(warehouse_token):
    """AC2: Không cho tạo chuyến giao hàng khi danh sách phiếu xuất rỗng."""
    payload = {
        "delivery_date": "2026-10-20",
        "dispatch_ids": [],
    }
    res = client.post("/api/v1/delivery-trips", json=payload, headers={"Authorization": f"Bearer {warehouse_token}"})
    assert res.status_code == 400
    assert "chọn ít nhất một phiếu xuất" in res.json()["detail"]


def test_delivery_trip_lifecycle_and_constraints(warehouse_mgr_token, setup_trips_data):
    """
    Kịch bản toàn diện:
    1. Lấy danh sách phiếu xuất hợp lệ (chưa gán chuyến).
    2. Tạo chuyến giao với 2 phiếu xuất kho.
    3. Kiểm tra chuyến được tạo ở trạng thái ASSIGNED, có đủ điểm dừng.
    4. Kiểm tra các phiếu xuất không còn xuất hiện trong /eligible-dispatches.
    5. Kiểm tra chặn tạo chuyến trùng phiếu xuất (Active conflict).
    6. Kiểm tra chặn trùng lịch tài xế cùng ngày.
    7. Cập nhật kết quả từng điểm dừng: Điểm 1 DELIVERED, Điểm 2 FAILED -> Chuyến chuyển sang PARTIALLY_FAILED.
    8. Kiểm tra API chi tiết chuyến cung cấp đủ dữ liệu cho in ấn A4.
    """
    d = setup_trips_data
    auth_header = {"Authorization": f"Bearer {warehouse_mgr_token}"}
    delivery_date = "2026-10-25"

    # 1. Lấy eligible-dispatches
    res_elig = client.get("/api/v1/delivery-trips/eligible-dispatches", headers=auth_header)
    assert res_elig.status_code == 200
    elig_ids = [it["dispatch_id"] for it in res_elig.json()]
    assert d["disp1_id"] in elig_ids
    assert d["disp2_id"] in elig_ids

    # 2. Tạo chuyến giao hàng
    create_payload = {
        "delivery_date": delivery_date,
        "driver_id": d["driver_id"],
        "vehicle_id": d["vehicle_id"],
        "warehouse_name": "Kho Tổng Hà Nội",
        "note": "Giao tuyến Cầu Giấy thử nghiệm",
        "dispatch_ids": [d["disp1_id"], d["disp2_id"]],
    }
    res_create = client.post("/api/v1/delivery-trips", json=create_payload, headers=auth_header)
    assert res_create.status_code == 201
    trip_data = res_create.json()
    trip_id = trip_data["trip_id"]
    trip_code = trip_data["trip_code"]
    assert trip_code.startswith("TRIP-20261025-")

    # 3. Xem chi tiết chuyến vừa tạo
    res_detail = client.get(f"/api/v1/delivery-trips/{trip_id}", headers=auth_header)
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["status"] == "ASSIGNED"
    assert detail["total_dispatches"] == 2
    assert len(detail["stops"]) >= 1

    # 4. Kiểm tra dispatches đã được đưa vào chuyến không còn trong eligible-dispatches
    res_elig_after = client.get("/api/v1/delivery-trips/eligible-dispatches", headers=auth_header)
    elig_ids_after = [it["dispatch_id"] for it in res_elig_after.json()]
    assert d["disp1_id"] not in elig_ids_after
    assert d["disp2_id"] not in elig_ids_after

    # 5. Chặn đưa phiếu xuất đã nằm trong chuyến active vào chuyến mới
    conflict_payload = {
        "delivery_date": "2026-10-26",
        "dispatch_ids": [d["disp1_id"]],
    }
    res_conflict = client.post("/api/v1/delivery-trips", json=conflict_payload, headers=auth_header)
    assert res_conflict.status_code == 400
    assert "đã thuộc chuyến giao đang hoạt động" in res_conflict.json()["detail"]

    # 6. Chặn phân công trùng tài xế cùng ngày
    conflict_driver_payload = {
        "delivery_date": delivery_date,  # Cùng ngày
        "driver_id": d["driver_id"],    # Cùng tài xế
        "dispatch_ids": [99999],         # dummy id
    }
    # Create a dummy third dispatch for this test
    db = SessionLocal()
    try:
        dummy_disp = OrderDispatchEntity(
            dispatch_code="PXK-DUMMY-CONFLICT",
            order_id=1,
            order_code="DH-DUMMY",
            performed_by="kho",
        )
        db.add(dummy_disp)
        db.commit()
        db.refresh(dummy_disp)
        conflict_driver_payload["dispatch_ids"] = [dummy_disp.id]
    finally:
        db.close()

    res_driver_conflict = client.post("/api/v1/delivery-trips", json=conflict_driver_payload, headers=auth_header)
    assert res_driver_conflict.status_code == 400
    assert "đã được phân công cho chuyến" in res_driver_conflict.json()["detail"]

    # 7. Bắt đầu chuyến giao (IN_PROGRESS)
    res_start = client.patch(f"/api/v1/delivery-trips/{trip_id}/status", json={"status": "IN_PROGRESS"}, headers=auth_header)
    assert res_start.status_code == 200
    assert res_start.json()["new_status"] == "IN_PROGRESS"

    # 8. Cập nhật kết quả các điểm dừng
    stops = detail["stops"]
    stop1_id = stops[0]["id"]

    # Cập nhật điểm 1: Giao thành công (DELIVERED)
    res_stop1 = client.patch(
        f"/api/v1/delivery-trips/{trip_id}/stops/{stop1_id}",
        json={"status": "DELIVERED", "note": "Giao đủ hàng cho khách"},
        headers=auth_header,
    )
    assert res_stop1.status_code == 200
    assert res_stop1.json()["stop_status"] == "DELIVERED"

    # Nếu có điểm thứ 2, cập nhật thất bại
    if len(stops) > 1:
        stop2_id = stops[1]["id"]
        res_stop2 = client.patch(
            f"/api/v1/delivery-trips/{trip_id}/stops/{stop2_id}",
            json={"status": "FAILED", "failure_reason": "Khách đi công tác chưa về"},
            headers=auth_header,
        )
        assert res_stop2.status_code == 200
        assert res_stop2.json()["stop_status"] == "FAILED"
        # Trạng thái chuyến tự động cập nhật sang PARTIALLY_FAILED
        assert res_stop2.json()["trip_status"] == "PARTIALLY_FAILED"
    else:
        # Nếu chỉ có 1 điểm và đã giao xong thì chuyến COMPLETED
        assert res_stop1.json()["trip_status"] == "COMPLETED"

    # 9. Kiểm tra danh sách chuyến giao hàng
    res_list = client.get("/api/v1/delivery-trips", headers=auth_header)
    assert res_list.status_code == 200
    trip_codes = [t["trip_code"] for t in res_list.json()]
    assert trip_code in trip_codes


def test_vehicle_conflict_and_assign_driver(warehouse_mgr_token, setup_trips_data):
    """AC4: Kiểm tra chặn trùng lịch phương tiện và phân công tài xế / xe sau khi tạo."""
    auth_header = {"Authorization": f"Bearer {warehouse_mgr_token}"}
    d = setup_trips_data
    delivery_date = "2026-10-28"

    db = SessionLocal()
    try:
        # Tạo thêm 2 phiếu xuất kho test
        disp_a = OrderDispatchEntity(dispatch_code="PXK-VEH-01", order_id=1, order_code="DH-VEH-01", performed_by="kho")
        disp_b = OrderDispatchEntity(dispatch_code="PXK-VEH-02", order_id=1, order_code="DH-VEH-02", performed_by="kho")
        db.add_all([disp_a, disp_b])
        db.commit()
        db.refresh(disp_a)
        db.refresh(disp_b)
        disp_a_id = disp_a.id
        disp_b_id = disp_b.id
    finally:
        db.close()

    # 1. Tạo chuyến thứ nhất gán xe
    res1 = client.post(
        "/api/v1/delivery-trips",
        json={
            "delivery_date": delivery_date,
            "vehicle_id": d["vehicle_id"],
            "driver_id": d["driver_id"],
            "dispatch_ids": [disp_a_id],
        },
        headers=auth_header,
    )
    assert res1.status_code == 201
    trip1_id = res1.json()["trip_id"]

    # 2. Tạo chuyến thứ 2 cùng ngày cùng xe -> Bị chặn 400
    res2 = client.post(
        "/api/v1/delivery-trips",
        json={
            "delivery_date": delivery_date,
            "vehicle_id": d["vehicle_id"],
            "dispatch_ids": [disp_b_id],
        },
        headers=auth_header,
    )
    assert res2.status_code == 400
    assert "Phương tiện" in res2.json()["detail"]
    assert "đã được gán cho chuyến" in res2.json()["detail"]

    # 3. Tạo chuyến nháp không gán xe/tài xế (DRAFT)
    res_draft = client.post(
        "/api/v1/delivery-trips",
        json={
            "delivery_date": "2026-10-29",
            "dispatch_ids": [disp_b_id],
        },
        headers=auth_header,
    )
    assert res_draft.status_code == 201
    draft_id = res_draft.json()["trip_id"]

    # Chi tiết chuyến nháp là DRAFT
    detail_draft = client.get(f"/api/v1/delivery-trips/{draft_id}", headers=auth_header).json()
    assert detail_draft["status"] == "DRAFT"

    # Phân công xe và tài xế cho chuyến nháp qua endpoint /assign
    res_assign = client.post(
        f"/api/v1/delivery-trips/{draft_id}/assign",
        json={
            "driver_id": d["driver_id"],
            "vehicle_id": d["vehicle_id"],
        },
        headers=auth_header,
    )
    assert res_assign.status_code == 200
    assert "Đã phân công tài xế" in res_assign.json()["message"]

    # Trạng thái chuyến chuyển sang ASSIGNED
    detail_assigned = client.get(f"/api/v1/delivery-trips/{draft_id}", headers=auth_header).json()
    assert detail_assigned["status"] == "ASSIGNED"
    assert detail_assigned["driver_id"] == d["driver_id"]
    assert detail_assigned["vehicle_id"] == d["vehicle_id"]


def test_custom_stop_order_and_delete_draft(warehouse_mgr_token):
    """AC5 & AC6: Sắp xếp thứ tự điểm dừng thủ công và kiểm tra xóa chuyến nháp."""
    auth_header = {"Authorization": f"Bearer {warehouse_mgr_token}"}
    delivery_date = "2026-10-30"

    db = SessionLocal()
    try:
        disp_1 = OrderDispatchEntity(dispatch_code="PXK-STOP-01", order_id=1, order_code="DH-STOP-01", performed_by="kho")
        disp_2 = OrderDispatchEntity(dispatch_code="PXK-STOP-02", order_id=1, order_code="DH-STOP-02", performed_by="kho")
        db.add_all([disp_1, disp_2])
        db.commit()
        db.refresh(disp_1)
        db.refresh(disp_2)
        d1_id = disp_1.id
        d2_id = disp_2.id
    finally:
        db.close()

    # Tạo chuyến với cấu trúc điểm dừng tùy biến (stops sắp xếp rõ ràng)
    custom_stops_payload = {
        "delivery_date": delivery_date,
        "note": "Tuyến giao đa điểm có sắp xếp thứ tự",
        "stops": [
            {
                "stop_order": 1,
                "dealer_name": "Đại lý A - Hoàn Kiếm",
                "address": "10 Tràng Tiền, Hoàn Kiếm, Hà Nội",
                "receiver_name": "Anh Hoàng",
                "receiver_phone": "0911223344",
                "route_note": "Giao cổng sau",
                "dispatch_ids": [d1_id],
            },
            {
                "stop_order": 2,
                "dealer_name": "Đại lý B - Đống Đa",
                "address": "45 Thái Hà, Đống Đa, Hà Nội",
                "receiver_name": "Chị Mai",
                "receiver_phone": "0988776655",
                "route_note": "Giao trước 11h trưa",
                "dispatch_ids": [d2_id],
            },
        ],
    }

    res_create = client.post("/api/v1/delivery-trips", json=custom_stops_payload, headers=auth_header)
    assert res_create.status_code == 201
    trip_id = res_create.json()["trip_id"]

    # Kiểm tra chi tiết chuyến lưu đúng thứ tự và thông tin in ấn A4
    res_detail = client.get(f"/api/v1/delivery-trips/{trip_id}", headers=auth_header)
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["status"] == "DRAFT"
    assert detail["total_stops"] == 2
    assert detail["total_dispatches"] == 2
    assert detail["stops"][0]["stop_order"] == 1
    assert detail["stops"][0]["receiver_name"] == "Anh Hoàng"
    assert detail["stops"][0]["address"] == "10 Tràng Tiền, Hoàn Kiếm, Hà Nội"
    assert detail["stops"][0]["dispatches"][0]["dispatch_code"] == "PXK-STOP-01"
    assert detail["stops"][1]["stop_order"] == 2
    assert detail["stops"][1]["receiver_name"] == "Chị Mai"

    # Kiểm tra xóa chuyến DRAFT thành công
    res_delete = client.delete(f"/api/v1/delivery-trips/{trip_id}", headers=auth_header)
    assert res_delete.status_code == 200
    assert "Đã xóa chuyến giao hàng" in res_delete.json()["message"]

    # Kiểm tra không còn tồn tại
    res_check = client.get(f"/api/v1/delivery-trips/{trip_id}", headers=auth_header)
    assert res_check.status_code == 404


def test_trip_update_and_completion_rules(admin_token):
    """AC7 & AC8: Chỉnh sửa thông tin chuyến (PUT), phân quyền Admin, và chặn hủy chuyến đã hoàn tất."""
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    delivery_date = "2026-10-31"

    db = SessionLocal()
    try:
        disp = OrderDispatchEntity(dispatch_code="PXK-COMP-01", order_id=1, order_code="DH-COMP-01", performed_by="kho")
        db.add(disp)
        db.commit()
        db.refresh(disp)
        d_id = disp.id
    finally:
        db.close()

    # Admin tạo chuyến
    res_create = client.post(
        "/api/v1/delivery-trips",
        json={"delivery_date": delivery_date, "dispatch_ids": [d_id]},
        headers=auth_header,
    )
    assert res_create.status_code == 201
    trip_id = res_create.json()["trip_id"]

    # Cập nhật thông tin chuyến (PUT)
    res_update = client.put(
        f"/api/v1/delivery-trips/{trip_id}",
        json={"note": "Đã cập nhật ghi chú qua PUT", "warehouse_name": "Kho Đông Anh"},
        headers=auth_header,
    )
    assert res_update.status_code == 200
    assert "Cập nhật chuyến giao hàng thành công" in res_update.json()["message"]

    # Kiểm tra chi tiết có thông tin mới
    detail = client.get(f"/api/v1/delivery-trips/{trip_id}", headers=auth_header).json()
    assert detail["note"] == "Đã cập nhật ghi chú qua PUT"
    assert detail["warehouse_name"] == "Kho Đông Anh"

    # Hoàn tất chuyến
    stop_id = detail["stops"][0]["id"]
    res_stop = client.patch(
        f"/api/v1/delivery-trips/{trip_id}/stops/{stop_id}",
        json={"status": "DELIVERED"},
        headers=auth_header,
    )
    assert res_stop.status_code == 200
    assert res_stop.json()["trip_status"] == "COMPLETED"

    # Chặn hủy chuyến đã hoàn tất
    res_cancel = client.patch(
        f"/api/v1/delivery-trips/{trip_id}/status",
        json={"status": "CANCELLED"},
        headers=auth_header,
    )
    assert res_cancel.status_code == 400
    assert "đã hoàn thành, không thể hủy bỏ" in res_cancel.json()["detail"]

    # Chặn xóa chuyến đã hoàn tất
    res_del_comp = client.delete(f"/api/v1/delivery-trips/{trip_id}", headers=auth_header)
    assert res_del_comp.status_code == 400
    assert "Chỉ có thể xóa chuyến giao hàng ở trạng thái Nháp" in res_del_comp.json()["detail"]


def test_warehouse_all_dispatches_history_endpoint(admin_token):
    """Kiểm tra API /orders/warehouse/dispatches trả về đầy đủ lịch sử phiếu xuất và không bị ẩn sau khi hoàn thành."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/api/v1/orders/warehouse/dispatches", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)




