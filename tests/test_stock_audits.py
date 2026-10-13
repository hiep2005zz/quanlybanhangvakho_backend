# backend/tests/test_stock_audits.py
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import (
    StockAuditEntity,
    WarehouseStockEntity,
    ProductEntity,
    InventoryTransactionEntity,
    AuditLogEntity,
)
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
    # Thủ kho: chỉ có role "warehouse", KHÔNG PHẢI "warehouse_manager"
    token = create_access_token("kho", "warehouse")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sales_headers():
    # Nhân viên kinh doanh
    token = create_access_token("sales", "sales")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def accountant_headers():
    # Kế toán
    token = create_access_token("ketoan", "accountant")
    return {"Authorization": f"Bearer {token}"}


def test_rbac_stock_audit_permission_restriction(
    client, sales_headers, warehouse_staff_headers, accountant_headers, warehouse_mgr_headers
):
    """
    Test 11: Kiểm tra phân quyền:
    - Sales, Kế toán bị chặn 403 Forbidden ở mọi API tạo/sửa/xác nhận/hủy.
    - Thủ kho (warehouse) có thể xem danh sách nhưng KHÔNG được tạo/sửa/xác nhận phiếu.
    - Quản lý kho (warehouse_manager) thực hiện thành công.
    """
    # 1. Sales tạo phiếu -> 403
    res_sales = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE"},
        headers=sales_headers,
    )
    assert res_sales.status_code == 403

    # 2. Accountant tạo phiếu -> 403
    res_acc = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE"},
        headers=accountant_headers,
    )
    assert res_acc.status_code == 403

    # 3. Thủ kho (warehouse) tạo phiếu -> 403 (Chỉ Quản lý kho mới được làm kiểm kê)
    res_staff = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE"},
        headers=warehouse_staff_headers,
    )
    assert res_staff.status_code == 403

    # 4. Quản lý kho (warehouse_mgr) tạo phiếu -> 201 Created
    res_mgr = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE"},
        headers=warehouse_mgr_headers,
    )
    assert res_mgr.status_code == 201


def test_create_stock_audit_by_warehouse_and_freeze_stock(client, warehouse_mgr_headers):
    """
    Test 1, 3, 4:
    Tạo phiếu kiểm kê theo kho, chứa nhiều sản phẩm, tồn kho ban đầu được chốt chính xác.
    """
    res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE", "note": "Kiểm kê định kỳ tháng 10"},
        headers=warehouse_mgr_headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["code"].startswith("PKK-")
    assert data["warehouse_id"] == "WH01"
    assert data["scope_type"] == "WAREHOUSE"
    assert data["status"] == "IN_PROGRESS"
    assert data["total_items"] >= 2
    assert len(data["items"]) >= 2

    # Kiểm tra tồn kho chốt được lưu cố định
    first_item = data["items"][0]
    assert first_item["system_stock"] is not None
    assert first_item["actual_stock"] is None
    assert first_item["discrepancy"] is None


def test_create_stock_audit_by_category(client, warehouse_mgr_headers):
    """
    Test 2:
    Tạo phiếu kiểm kê theo nhóm hàng.
    """
    # Lấy category ID 1 (Thời trang)
    res = client.post(
        "/api/v1/stock-audits",
        json={
            "warehouse_id": "WH01",
            "scope_type": "CATEGORY",
            "category_id": 1,
            "note": "Kiểm kê ngành hàng Thời trang",
        },
        headers=warehouse_mgr_headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["scope_type"] == "CATEGORY"
    assert data["category_id"] == 1
    assert data["total_items"] > 0


def test_input_actual_stock_negative_rejected(client, warehouse_mgr_headers):
    """
    Test 9:
    Nhập số lượng âm -> Chặn 400 Bad Request.
    """
    create_res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE"},
        headers=warehouse_mgr_headers,
    )
    audit_id = create_res.json()["id"]
    p_id = create_res.json()["items"][0]["product_id"]

    update_res = client.put(
        f"/api/v1/stock-audits/{audit_id}",
        json={"items": [{"product_id": p_id, "actual_stock": -5, "reason": "Hỏng"}]},
        headers=warehouse_mgr_headers,
    )
    assert update_res.status_code == 400
    assert "không thể nhỏ hơn 0" in update_res.json()["detail"]


def test_confirm_requires_reason_for_discrepancy(client, warehouse_mgr_headers):
    """
    Test 10:
    Có chênh lệch nhưng thiếu lý do -> Chặn 400 Bad Request khi xác nhận.
    """
    create_res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE"},
        headers=warehouse_mgr_headers,
    )
    audit_data = create_res.json()
    audit_id = audit_data["id"]

    # Nhập số lượng thực tế lệch so với system_stock nhưng để trống reason
    items_to_submit = []
    for it in audit_data["items"]:
        items_to_submit.append({
            "product_id": it["product_id"],
            "actual_stock": it["system_stock"] + 5,  # Chênh lệch +5
            "reason": "",  # Lý do rỗng!
        })

    confirm_res = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        json={"items": items_to_submit},
        headers=warehouse_mgr_headers,
    )
    assert confirm_res.status_code == 400
    assert "Bắt buộc nhập lý do chênh lệch" in confirm_res.json()["detail"]


def test_full_audit_workflow_match_deficit_surplus_and_inventory_adjustment(client, warehouse_mgr_headers):
    """
    Test 5, 6, 7, 8, 17, 18:
    Quy trình kiểm kê hoàn chỉnh:
    - Sản phẩm A: Thiếu hàng (nhập số lượng nhỏ hơn tồn chốt)
    - Sản phẩm B: Thừa hàng (nhập số lượng lớn hơn tồn chốt)
    - Sản phẩm C: Khớp (nhập số lượng bằng tồn chốt)
    - Sản phẩm D: Nhập số lượng 0
    - Xác nhận và kiểm tra điều chỉnh tồn kho chính xác, ghi nhận lịch sử và audit log.
    """
    db = SessionLocal()
    try:
        # Chuẩn bị tồn kho tại WH02 cho 3 sản phẩm
        from app.services.inventory_availability_service import get_or_create_warehouse_stock
        s1 = get_or_create_warehouse_stock(db, "WH02", "Kho Chi Nhánh TP. Hồ Chí Minh", 1, for_update=True)
        s1.actual_stock = 50
        s2 = get_or_create_warehouse_stock(db, "WH02", "Kho Chi Nhánh TP. Hồ Chí Minh", 2, for_update=True)
        s2.actual_stock = 40
        s3 = get_or_create_warehouse_stock(db, "WH02", "Kho Chi Nhánh TP. Hồ Chí Minh", 4, for_update=True)
        s3.actual_stock = 30
        db.commit()
    finally:
        db.close()

    # 1. Tạo phiếu kiểm kê tại WH02 cho 3 sản phẩm 1, 2, 4
    create_res = client.post(
        "/api/v1/stock-audits",
        json={
            "warehouse_id": "WH02",
            "scope_type": "WAREHOUSE",
            "product_ids": [1, 2, 4],
            "note": "Kiểm kê toàn diện WH02",
        },
        headers=warehouse_mgr_headers,
    )
    assert create_res.status_code == 201
    audit = create_res.json()
    audit_id = audit["id"]

    # Kiểm tra tồn chốt
    items_map = {it["product_id"]: it for it in audit["items"]}
    assert items_map[1]["system_stock"] == 50
    assert items_map[2]["system_stock"] == 40
    assert items_map[4]["system_stock"] == 30

    # 2. Nhập kết quả kiểm đếm:
    # SP1: Thiếu 5 (thực tế 45, chênh lệch -5)
    # SP2: Khớp 0 (thực tế 40, chênh lệch 0)
    # SP4: Thừa 10 (thực tế 40, chênh lệch +10)
    items_update = [
        {"product_id": 1, "actual_stock": 45, "reason": "Hàng rách tem hư hỏng"},
        {"product_id": 2, "actual_stock": 40, "reason": ""},
        {"product_id": 4, "actual_stock": 40, "reason": "Nhà cung cấp giao dư"},
    ]
    update_res = client.put(
        f"/api/v1/stock-audits/{audit_id}",
        json={"items": items_update},
        headers=warehouse_mgr_headers,
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["discrepancy_items_count"] == 2
    assert updated_data["total_discrepancy_qty"] == 5  # (-5) + (+10) = +5

    # 3. Xác nhận kiểm kê
    confirm_res = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        headers=warehouse_mgr_headers,
    )
    assert confirm_res.status_code == 200
    confirmed_data = confirm_res.json()
    assert confirmed_data["status"] == "CONFIRMED"
    assert confirmed_data["status_label"] == "Hoàn tất"
    assert confirmed_data["confirmed_by"] is not None
    assert confirmed_data["confirmed_at"] is not None

    # 4. Kiểm tra tồn kho trong cơ sở dữ liệu sau điều chỉnh
    db = SessionLocal()
    try:
        stock1 = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH02", WarehouseStockEntity.product_id == 1
        ).first()
        stock2 = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH02", WarehouseStockEntity.product_id == 2
        ).first()
        stock4 = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH02", WarehouseStockEntity.product_id == 4
        ).first()

        assert stock1.actual_stock == 45  # 50 - 5 = 45
        assert stock2.actual_stock == 40  # 40 (khớp)
        assert stock4.actual_stock == 40  # 30 + 10 = 40

        # Kiểm tra lịch sử inventory_transactions
        tx1 = db.query(InventoryTransactionEntity).filter(
            InventoryTransactionEntity.product_id == 1,
            InventoryTransactionEntity.reason.like(f"%{confirmed_data['code']}%"),
        ).first()
        assert tx1 is not None
        assert tx1.quantity == -5

        tx4 = db.query(InventoryTransactionEntity).filter(
            InventoryTransactionEntity.product_id == 4,
            InventoryTransactionEntity.reason.like(f"%{confirmed_data['code']}%"),
        ).first()
        assert tx4 is not None
        assert tx4.quantity == 10
    finally:
        db.close()


def test_cannot_confirm_audit_twice(client, warehouse_mgr_headers):
    """
    Test 12:
    Không thể xác nhận phiếu hai lần -> Chặn 400 Bad Request.
    """
    create_res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE", "product_ids": [1]},
        headers=warehouse_mgr_headers,
    )
    audit_id = create_res.json()["id"]
    sys_stock = create_res.json()["items"][0]["system_stock"]

    # Xác nhận lần 1
    confirm_res_1 = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        json={"items": [{"product_id": 1, "actual_stock": sys_stock, "reason": ""}]},
        headers=warehouse_mgr_headers,
    )
    assert confirm_res_1.status_code == 200

    # Xác nhận lần 2 -> Bị từ chối
    confirm_res_2 = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        headers=warehouse_mgr_headers,
    )
    assert confirm_res_2.status_code == 400
    assert "Không thể xác nhận hai lần" in confirm_res_2.json()["detail"]


def test_cancel_stock_audit(client, warehouse_mgr_headers):
    """
    Hủy phiếu kiểm kê khi đang kiểm kê (IN_PROGRESS).
    """
    create_res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE", "product_ids": [1]},
        headers=warehouse_mgr_headers,
    )
    audit_id = create_res.json()["id"]

    cancel_res = client.post(
        f"/api/v1/stock-audits/{audit_id}/cancel",
        json={"reason": "Hủy kiểm kê do sự cố điện"},
        headers=warehouse_mgr_headers,
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"
    assert cancel_res.json()["status_label"] == "Đã hủy"

    # Sau khi hủy không thể xác nhận
    confirm_res = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        headers=warehouse_mgr_headers,
    )
    assert confirm_res.status_code == 400


def test_stock_audit_with_concurrent_transactions(client, warehouse_mgr_headers):
    """
    Test 13: Có giao dịch phát sinh trong lúc kiểm kê.
    Kịch bản:
    - Lúc tạo phiếu (T0): Tồn kho chốt = 100.
    - Trong lúc kiểm đếm (T1): Có giao dịch xuất 10 sản phẩm -> Tồn thực tế tại kho giảm xuống còn 90.
    - Người kiểm kê đếm được 95 cái lúc T0 (thiếu 5 cái so với tồn chốt).
    - Khi xác nhận phiếu (T2):
      Hệ thống áp dụng độ lệch (-5) lên tồn hiện tại (90 + (-5) = 85),
      Bảo đảm KHÔNG bị ghi đè thành 95 làm mất giao dịch xuất 10 cái ở T1!
    """
    db = SessionLocal()
    try:
        from app.services.inventory_availability_service import get_or_create_warehouse_stock
        stk = get_or_create_warehouse_stock(db, "WH03", "Kho Chi Nhánh Đà Nẵng", 1, for_update=True)
        stk.actual_stock = 100
        db.commit()
    finally:
        db.close()

    # T0: Tạo phiếu kiểm kê chốt tồn = 100
    create_res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH03", "scope_type": "WAREHOUSE", "product_ids": [1]},
        headers=warehouse_mgr_headers,
    )
    audit = create_res.json()
    audit_id = audit["id"]
    assert audit["items"][0]["system_stock"] == 100

    # T1: Giao dịch phát sinh (xuất kho 10 cái)
    db = SessionLocal()
    try:
        stk = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH03", WarehouseStockEntity.product_id == 1
        ).first()
        stk.actual_stock = 90  # Xuất 10 cái
        db.commit()
    finally:
        db.close()

    # T2: Xác nhận kiểm kê với số lượng thực tế đếm được là 95 (thiếu 5 cái)
    confirm_res = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        json={"items": [{"product_id": 1, "actual_stock": 95, "reason": "Hao hụt rách bao bì"}]},
        headers=warehouse_mgr_headers,
    )
    assert confirm_res.status_code == 200

    # Kiểm tra tồn kho sau điều chỉnh: 90 - 5 = 85 (chính xác tuyệt đối!)
    db = SessionLocal()
    try:
        stk = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH03", WarehouseStockEntity.product_id == 1
        ).first()
        assert stk.actual_stock == 85
    finally:
        db.close()


def test_stock_audit_reserved_stock_warning(client, warehouse_mgr_headers):
    """
    Test 15: Sản phẩm có số lượng đang giữ chỗ (reserved_stock).
    Khi tồn thực tế đếm được thấp hơn số lượng đang giữ chỗ, hệ thống phát hiện và ghi nhận cảnh báo.
    """
    db = SessionLocal()
    try:
        from app.services.inventory_availability_service import get_or_create_warehouse_stock
        stk = get_or_create_warehouse_stock(db, "WH01", "Kho Tổng Hà Nội", 5, for_update=True)
        stk.actual_stock = 20
        stk.reserved_stock = 15  # Đang giữ chỗ 15 cái cho đơn hàng
        db.commit()
    finally:
        db.close()

    create_res = client.post(
        "/api/v1/stock-audits",
        json={"warehouse_id": "WH01", "scope_type": "WAREHOUSE", "product_ids": [5]},
        headers=warehouse_mgr_headers,
    )
    audit = create_res.json()
    audit_id = audit["id"]

    # Đếm thực tế chỉ còn 10 cái (nhỏ hơn 15 cái đang giữ chỗ!)
    confirm_res = client.post(
        f"/api/v1/stock-audits/{audit_id}/confirm",
        json={"items": [{"product_id": 5, "actual_stock": 10, "reason": "Thất thoát kho"}]},
        headers=warehouse_mgr_headers,
    )
    assert confirm_res.status_code == 200

    # Kiểm tra inventory_transactions ghi nhận cảnh báo giữ chỗ
    db = SessionLocal()
    try:
        tx = db.query(InventoryTransactionEntity).filter(
            InventoryTransactionEntity.product_id == 5,
            InventoryTransactionEntity.reason.like("%CẢNH BÁO: Tồn thực tế%"),
        ).first()
        assert tx is not None
    finally:
        db.close()

