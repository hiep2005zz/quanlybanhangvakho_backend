# backend/tests/test_delivery_in_transit_scrum_84.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import DeliveryTripEntity, DeliveryStopEntity, ProductEntity, WarehouseStockEntity
from app.core.security import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def warehouse_mgr_headers():
    token = create_access_token("warehouse_mgr", "warehouse_manager")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    token = create_access_token("admin", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def warehouse_staff_headers():
    token = create_access_token("kho", "warehouse")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def customer_headers():
    token = create_access_token("muahang", "customer")
    return {"Authorization": f"Bearer {token}"}


def test_in_transit_summary_endpoint_and_kpis(client, warehouse_mgr_headers, admin_headers):
    """
    SCRUM-84: Kiểm tra API GET /api/v1/deliveries/in-transit-summary
    - warehouse_mgr và admin truy cập thành công (200 OK).
    - Trả về đúng 4 chỉ số KPI: total_in_transit_value, active_trips_count, remaining_stops_count, overdue_trips_count.
    """
    # 1. Quản lý kho truy cập
    res_mgr = client.get("/api/v1/deliveries/in-transit-summary", headers=warehouse_mgr_headers)
    assert res_mgr.status_code == 200, f"Error: {res_mgr.text}"
    data_mgr = res_mgr.json()

    assert "total_in_transit_value" in data_mgr
    assert "active_trips_count" in data_mgr
    assert "remaining_stops_count" in data_mgr
    assert "overdue_trips_count" in data_mgr

    assert isinstance(data_mgr["total_in_transit_value"], (int, float))
    assert data_mgr["total_in_transit_value"] > 0
    assert data_mgr["active_trips_count"] >= 1
    assert data_mgr["remaining_stops_count"] >= 1
    assert data_mgr["overdue_trips_count"] >= 1

    # 2. Admin truy cập
    res_admin = client.get("/api/v1/deliveries/in-transit-summary", headers=admin_headers)
    assert res_admin.status_code == 200
    assert res_admin.json() == data_mgr


def test_in_transit_trips_list_and_overdue_flag(client, warehouse_mgr_headers):
    """
    SCRUM-84: Kiểm tra API GET /api/v1/deliveries/in-transit
    - Trả về danh sách chuyến đang giao kèm tính toán động: số điểm đã giao, số điểm còn lại, cờ is_overdue, remaining_value.
    - Bộ lọc is_overdue=true chỉ trả về các chuyến trễ hạn.
    - Bộ lọc search tìm kiếm chính xác theo mã hoặc tài xế.
    """
    # 1. Danh sách chuyến đang giao
    res = client.get("/api/v1/deliveries/in-transit?status=IN_TRANSIT", headers=warehouse_mgr_headers)
    assert res.status_code == 200
    trips = res.json()
    assert len(trips) >= 1

    first_trip = trips[0]
    assert "id" in first_trip
    assert "trip_code" in first_trip
    assert "driver_name" in first_trip
    assert "vehicle_plate" in first_trip
    assert "route_name" in first_trip
    assert "status" in first_trip
    assert "total_stops" in first_trip
    assert "delivered_stops" in first_trip
    assert "remaining_stops" in first_trip
    assert "total_trip_value" in first_trip
    assert "remaining_value" in first_trip
    assert "is_overdue" in first_trip
    assert "overdue_minutes" in first_trip
    assert "stops" in first_trip

    # 2. Kiểm tra bộ lọc chuyến trễ hạn is_overdue=true
    res_overdue = client.get("/api/v1/deliveries/in-transit?is_overdue=true", headers=warehouse_mgr_headers)
    assert res_overdue.status_code == 200
    overdue_trips = res_overdue.json()
    assert len(overdue_trips) >= 1
    for trip in overdue_trips:
        assert trip["is_overdue"] is True
        assert trip["overdue_minutes"] > 0

    # 3. Kiểm tra tìm kiếm theo từ khóa
    sample_code = first_trip["trip_code"]
    res_search = client.get(f"/api/v1/deliveries/in-transit?search={sample_code}", headers=warehouse_mgr_headers)
    assert res_search.status_code == 200
    found_trips = res_search.json()
    assert any(t["trip_code"] == sample_code for t in found_trips)


def test_business_rule_in_transit_value_deduction_on_delivery(client, warehouse_mgr_headers):
    """
    Quy tắc nghiệp vụ 2:
    - Chỉ tính giá trị của các điểm giao chưa hoàn thành (status !== 'DELIVERED').
    - Khi điểm giao thành công -> Trừ tiền điểm đó ra khỏi giá trị "hàng nằm trên đường".
    """
    # 1. Lấy summary ban đầu
    res_init = client.get("/api/v1/deliveries/in-transit-summary", headers=warehouse_mgr_headers)
    init_val = res_init.json()["total_in_transit_value"]

    # 2. Tạo một chuyến giao hàng mới riêng biệt để test biến động giá trị
    now = datetime.now(timezone.utc)
    create_payload = {
        "driver_name": "Tài xế Test Biến Động",
        "vehicle_plate": "30A-999.88",
        "route_name": "Tuyến Test Quy Tắc Nghiệp Vụ 2",
        "estimated_delivery_time": (now + timedelta(hours=3)).isoformat(),
        "stops": [
            {
                "order_code": "DH-TEST-BR2-A",
                "dealer_name": "Đại Lý Test A",
                "delivery_point": "Số 1 Tràng Tiền, Hà Nội",
                "amount": 10000000.0,
            },
            {
                "order_code": "DH-TEST-BR2-B",
                "dealer_name": "Đại Lý Test B",
                "delivery_point": "Số 2 Hàng Bài, Hà Nội",
                "amount": 15000000.0,
            }
        ]
    }
    res_create = client.post("/api/v1/deliveries/trips", headers=warehouse_mgr_headers, json=create_payload)
    assert res_create.status_code == 201
    created_trip = res_create.json()
    trip_id = created_trip["id"]
    stops = created_trip["stops"]
    assert len(stops) == 2

    # Giá trị in-transit sau khi thêm chuyến mới tăng thêm 25 triệu
    res_after_create = client.get("/api/v1/deliveries/in-transit-summary", headers=warehouse_mgr_headers)
    val_after_create = res_after_create.json()["total_in_transit_value"]
    assert val_after_create == init_val + 25000000.0

    # 3. Giao thành công điểm 1 (10 triệu)
    stop1_id = stops[0]["id"]
    res_deliv = client.patch(
        f"/api/v1/deliveries/stops/{stop1_id}/status",
        headers=warehouse_mgr_headers,
        json={"status": "DELIVERED", "note": "Khách đã nhận đủ và ký nhận"}
    )
    assert res_deliv.status_code == 200
    assert res_deliv.json()["status"] == "DELIVERED"

    # 4. Kiểm tra giá trị in-transit summary: phải giảm đúng 10 triệu
    res_after_deliv = client.get("/api/v1/deliveries/in-transit-summary", headers=warehouse_mgr_headers)
    val_after_deliv = res_after_deliv.json()["total_in_transit_value"]
    assert val_after_deliv == val_after_create - 10000000.0

    # 5. Kiểm tra chi tiết chuyến hàng: remaining_value còn 15 triệu, delivered_stops = 1, remaining_stops = 1
    res_trip = client.get(f"/api/v1/deliveries/trips/{trip_id}", headers=warehouse_mgr_headers)
    assert res_trip.status_code == 200
    trip_data = res_trip.json()
    assert trip_data["total_trip_value"] == 25000000.0
    assert trip_data["remaining_value"] == 15000000.0
    assert trip_data["delivered_stops"] == 1
    assert trip_data["remaining_stops"] == 1


def test_business_rule_custody_and_no_inventory_change_on_failed_delivery(client, warehouse_mgr_headers):
    """
    Quy tắc nghiệp vụ 1 & Ràng buộc toàn vẹn tồn kho:
    - Hàng hóa khi đã xuất kho thuộc trách nhiệm của chuyến/tài xế.
    - Điểm giao FAILED tuyệt đối KHÔNG tự động cộng ngược lại vào tồn kho khả dụng (available_quantity)
      hay tồn kho thực tế (physical_quantity).
    """
    db = SessionLocal()
    try:
        # Lấy một bản ghi tồn kho mẫu trước khi cập nhật điểm giao thất bại
        stock = db.query(WarehouseStockEntity).first()
        if not stock:
            # Nếu chưa có stock record, tạo 1 sản phẩm & stock mẫu
            prod = ProductEntity(code="SP-TEST-INV", name="Sản phẩm Test Tồn Kho", price=100000.0)
            db.add(prod)
            db.flush()
            stock = WarehouseStockEntity(warehouse_id=1, product_id=prod.id, physical_quantity=100, available_quantity=80)
            db.add(stock)
            db.commit()
            db.refresh(stock)

        stock_id = stock.id
        actual_before = stock.actual_stock
        reserved_before = stock.reserved_stock
    finally:
        db.close()

    # 1. Tạo một chuyến có điểm giao
    now = datetime.now(timezone.utc)
    create_payload = {
        "driver_name": "Tài xế Test Không Đổi Tồn",
        "vehicle_plate": "29D-777.66",
        "route_name": "Tuyến Test BR1 Custody",
        "estimated_delivery_time": (now + timedelta(hours=2)).isoformat(),
        "stops": [
            {
                "order_code": "DH-TEST-BR1-FAIL",
                "dealer_name": "Đại Lý Hẹn Lại",
                "delivery_point": "Số 99 Bà Triệu, Hà Nội",
                "amount": 8000000.0,
            }
        ]
    }
    res_create = client.post("/api/v1/deliveries/trips", headers=warehouse_mgr_headers, json=create_payload)
    assert res_create.status_code == 201
    created_stop = res_create.json()["stops"][0]
    stop_id = created_stop["id"]

    # 2. Đổi trạng thái điểm sang FAILED
    res_fail = client.patch(
        f"/api/v1/deliveries/stops/{stop_id}/status",
        headers=warehouse_mgr_headers,
        json={"status": "FAILED", "failed_reason": "Khách đóng cửa, hẹn giao lại sáng mai"}
    )
    assert res_fail.status_code == 200
    stop_data = res_fail.json()
    assert stop_data["status"] == "FAILED"
    assert stop_data["failed_reason"] == "Khách đóng cửa, hẹn giao lại sáng mai"

    # 3. Kiểm tra lại dữ liệu tồn kho: Tuyệt đối không thay đổi!
    db = SessionLocal()
    try:
        stock_after = db.query(WarehouseStockEntity).filter(WarehouseStockEntity.id == stock_id).first()
        assert stock_after.actual_stock == actual_before, "actual_stock bị thay đổi trái phép!"
        assert stock_after.reserved_stock == reserved_before, "reserved_stock bị thay đổi trái phép!"
    finally:
        db.close()


def test_rbac_authorization(client, warehouse_mgr_headers, admin_headers, warehouse_staff_headers, customer_headers):
    """
    Kiểm tra phân quyền:
    - warehouse_mgr, admin, warehouse truy cập thành công (200 OK).
    - customer bị chặn 403 Forbidden.
    """
    # Allowed
    assert client.get("/api/v1/deliveries/in-transit-summary", headers=warehouse_mgr_headers).status_code == 200
    assert client.get("/api/v1/deliveries/in-transit-summary", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/deliveries/in-transit-summary", headers=warehouse_staff_headers).status_code == 200

    # Denied
    res_cust = client.get("/api/v1/deliveries/in-transit-summary", headers=customer_headers)
    assert res_cust.status_code == 403


def test_driver_phone_validation(client, warehouse_mgr_headers):
    """
    Kiểm tra ràng buộc số điện thoại tài xế:
    - 422 nếu sai format di động VN (chuỗi vô hạn, thiếu/thừa số, sai đầu số).
    - 201 nếu hợp lệ (10 số, đầu 03, 05, 07, 08, 09) hoặc để trống.
    """
    now = datetime.now(timezone.utc)
    base_payload = {
        "driver_name": "Tài xế Test SĐT",
        "vehicle_plate": "29B-123.45",
        "route_name": "Tuyến Test Phone",
        "estimated_delivery_time": (now + timedelta(hours=3)).isoformat(),
        "stops": [
            {
                "order_code": "DH-TEST-PHONE",
                "dealer_name": "Đại Lý Phone Test",
                "delivery_point": "Hà Nội",
                "amount": 1000000.0,
            }
        ]
    }

    # 1. Quá dài / chuỗi vô hạn
    res_invalid_long = client.post(
        "/api/v1/deliveries/trips",
        headers=warehouse_mgr_headers,
        json={**base_payload, "driver_phone": "643516843521643516431"}
    )
    assert res_invalid_long.status_code == 422
    assert "Số điện thoại không đúng định dạng" in str(res_invalid_long.json())

    # 2. Sai đầu số
    res_invalid_prefix = client.post(
        "/api/v1/deliveries/trips",
        headers=warehouse_mgr_headers,
        json={**base_payload, "driver_phone": "0123456789"}
    )
    assert res_invalid_prefix.status_code == 422

    # 3. Thiếu số (9 số)
    res_invalid_len = client.post(
        "/api/v1/deliveries/trips",
        headers=warehouse_mgr_headers,
        json={**base_payload, "driver_phone": "098123456"}
    )
    assert res_invalid_len.status_code == 422

    # 4. Hợp lệ (10 số, đầu 09)
    res_valid = client.post(
        "/api/v1/deliveries/trips",
        headers=warehouse_mgr_headers,
        json={**base_payload, "driver_phone": "0981234567"}
    )
    assert res_valid.status_code == 201
    assert res_valid.json()["driver_phone"] == "0981234567"

    # 5. Để trống (optional)
    res_empty = client.post(
        "/api/v1/deliveries/trips",
        headers=warehouse_mgr_headers,
        json={**base_payload, "driver_phone": ""}
    )
    assert res_empty.status_code == 201

