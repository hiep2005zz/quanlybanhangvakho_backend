# backend/tests/test_stock_adjustments.py
"""
BỘ KIỂM THỬ CHẤT LƯỢNG CAO CẤP (ENTERPRISE QA SUITE - 30 YEARS TEST LEAD STANDARD)
Phân hệ: Phiếu điều chỉnh tồn kho có phê duyệt (Stock Adjustment with Approval Workflow).
Tiêu chí nghiệm thu cốt lõi:
1. Dùng cho hàng hỏng, hàng mất, hàng hết hạn phải huỷ.
2. Bắt buộc chọn lý do và ghi rõ giá trị tổn thất (> 0).
3. Chỉ Quản lý kho được duyệt; Người lập và người duyệt TUYỆT ĐỐI KHÔNG ĐƯỢC LÀ MỘT (Four-eyes principle).
4. Mọi phiếu điều chỉnh đều xuất hiện trên thẻ kho CHỈ Quản lý kho và Admin hiện.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import (
    StockAdjustmentEntity,
    WarehouseStockEntity,
    ProductEntity,
    InventoryTransactionEntity,
    AuditLogEntity,
    UserEntity,
)
from app.models.goods_receipt import InventoryLedgerEntity
from app.core.security import create_access_token, get_password_hash


@pytest.fixture(autouse=True)
def setup_test_users():
    db = SessionLocal()
    # Đảm bảo warehouse_mgr2 tồn tại làm người duyệt độc lập
    u = db.query(UserEntity).filter(UserEntity.username == "warehouse_mgr2").first()
    if not u:
        u = UserEntity(
            username="warehouse_mgr2",
            full_name="Quản Lý Kho Số 2",
            email="wh_mgr2@company.com",
            role="warehouse_manager",
            hashed_password=get_password_hash("Secret123!"),
            branch="Kho Tổng Hà Nội",
            is_active=True,
            status="ACTIVE"
        )
        db.add(u)
        db.commit()
    db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_headers():
    token = create_access_token("admin", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def warehouse_mgr1_headers():
    # Quản lý kho 1 (warehouse_mgr)
    token = create_access_token("warehouse_mgr", "warehouse_manager")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def warehouse_mgr2_headers():
    # Quản lý kho 2 độc lập (warehouse_mgr2)
    token = create_access_token("warehouse_mgr2", "warehouse_manager")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def warehouse_staff_headers():
    # Thủ kho thông thường (kho / tranthib)
    token = create_access_token("kho", "warehouse")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sales_headers():
    # Nhân viên kinh doanh (sales)
    token = create_access_token("sales", "sales")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def accountant_headers():
    # Kế toán (ketoan)
    token = create_access_token("ketoan", "accountant")
    return {"Authorization": f"Bearer {token}"}


def get_valid_test_product(db, min_stock=10):
    """Tìm sản phẩm hợp lệ còn tồn tại trong DB và kho WH01."""
    ws = db.query(WarehouseStockEntity).join(
        ProductEntity, WarehouseStockEntity.product_id == ProductEntity.id
    ).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.actual_stock >= min_stock,
        ProductEntity.status == "active"
    ).first()
    assert ws is not None, f"Cần ít nhất 1 sản phẩm có tồn >= {min_stock} trong WH01"
    return ws.product_id, ws.actual_stock


# -----------------------------------------------------------------------------
# TC01: Lập phiếu điều chỉnh thành công với các lý do hợp lệ (damaged, lost, expired)
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("reason_type,reason_note", [
    ("damaged", "Rách bao bì và vỡ vỏ hộp trong quá trình bốc xếp tại kệ A1"),
    ("lost", "Hao hụt thất thoát trong kho sau khi đối chiếu kiểm đếm"),
    ("expired", "Hàng hết hạn sử dụng không đủ điều kiện xuất kho, cần hủy theo quy chuẩn"),
])
def test_create_stock_adjustment_valid_reasons(
    client, warehouse_mgr1_headers, reason_type, reason_note
):
    db = SessionLocal()
    prod_id, initial_stock = get_valid_test_product(db, min_stock=10)
    db.close()

    payload = {
        "warehouse_id": "WH01",
        "reason_type": reason_type,
        "reason_note": reason_note,
        "items": [
            {
                "product_id": prod_id,
                "quantity": 2,
                "cost_price": 150000.0,
                "loss_value": 300000.0,
                "note": f"Ghi chú kiểm tra {reason_type}"
            }
        ]
    }

    resp = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert resp.status_code == 201, f"Lỗi tạo phiếu: {resp.text}"
    data = resp.json()

    assert data["code"].startswith("ADJ-WH01-")
    assert data["reason_type"] == reason_type
    assert data["status"] == "PENDING"
    assert data["status_label"] == "Chờ duyệt"
    assert data["total_loss_value"] == 300000.0
    assert data["total_quantity"] == 2
    assert len(data["items"]) == 1
    assert data["created_by"] in ["warehouse_mgr", "Hoàng Quản Lý Kho", "Hoang Quan Ly Kho"]

    # Kiểm tra tồn kho CHƯA BỊ TRỪ khi phiếu mới ở trạng thái PENDING
    db = SessionLocal()
    ws_after = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == prod_id
    ).first()
    assert ws_after.actual_stock == initial_stock, "Tồn kho thực tế không được thay đổi khi phiếu chưa duyệt!"
    db.close()


# -----------------------------------------------------------------------------
# TC02: Validation bắt buộc chọn lý do & ghi chú chi tiết
# -----------------------------------------------------------------------------
def test_create_stock_adjustment_validation_missing_reason(client, warehouse_mgr1_headers):
    # Lý do rỗng hoặc không hợp lệ
    payload = {
        "warehouse_id": "WH01",
        "reason_type": "invalid_reason",
        "reason_note": "Ghi chú hợp lệ",
        "items": [{"product_id": 1, "quantity": 1, "cost_price": 50000.0, "loss_value": 50000.0}]
    }
    resp = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert resp.status_code in [400, 422], "Hệ thống phải chặn khi reason_type không hợp lệ"

    # Ghi chú quá ngắn hoặc trống
    payload["reason_type"] = "damaged"
    payload["reason_note"] = ""
    resp = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert resp.status_code in [400, 422], "Hệ thống phải chặn khi reason_note bị trống"


# -----------------------------------------------------------------------------
# TC03: Validation bắt buộc ghi rõ giá trị tổn thất (> 0)
# -----------------------------------------------------------------------------
def test_create_stock_adjustment_validation_zero_loss_value(client, warehouse_mgr1_headers):
    payload = {
        "warehouse_id": "WH01",
        "reason_type": "damaged",
        "reason_note": "Bị hỏng do nước mưa dột",
        "total_loss_value": 0.0,
        "items": [
            {
                "product_id": 1,
                "quantity": 2,
                "cost_price": 0.0,
                "loss_value": 0.0,
            }
        ]
    }
    resp = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert resp.status_code == 400, "Bắt buộc phải ghi rõ giá trị tổn thất > 0"
    assert "tổn thất" in resp.json().get("detail", "").lower()


# -----------------------------------------------------------------------------
# TC04: Chặn điều chỉnh giảm vượt quá tồn kho thực tế
# -----------------------------------------------------------------------------
def test_create_stock_adjustment_exceeds_stock(client, warehouse_mgr1_headers):
    db = SessionLocal()
    prod_id, actual_stock = get_valid_test_product(db, min_stock=1)
    db.close()

    payload = {
        "warehouse_id": "WH01",
        "reason_type": "lost",
        "reason_note": "Thất thoát số lượng khổng lồ",
        "items": [
            {
                "product_id": prod_id,
                "quantity": actual_stock + 9999,  # Vượt quá tồn kho thực tế
                "cost_price": 100000.0,
                "loss_value": (actual_stock + 9999) * 100000.0,
            }
        ]
    }
    resp = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert resp.status_code == 400
    assert "không đủ" in resp.json().get("detail", "").lower() or "tồn" in resp.json().get("detail", "").lower()


# -----------------------------------------------------------------------------
# TC05: Phân quyền duyệt: Thủ kho / Sales / Kế toán bị CHẶN 403 Forbidden
# -----------------------------------------------------------------------------
def test_approve_stock_adjustment_forbidden_roles(
    client, warehouse_mgr1_headers, warehouse_staff_headers, sales_headers, accountant_headers
):
    # Tạo phiếu PENDING bởi warehouse_mgr
    db = SessionLocal()
    prod_id, actual_stock = get_valid_test_product(db, min_stock=5)
    db.close()

    payload = {
        "warehouse_id": "WH01",
        "reason_type": "damaged",
        "reason_note": "Hàng vỡ hộp",
        "items": [{"product_id": prod_id, "quantity": 1, "cost_price": 200000.0, "loss_value": 200000.0}]
    }
    create_res = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert create_res.status_code == 201, f"Lỗi tạo phiếu: {create_res.text}"
    adj_id = create_res.json()["id"]

    # 1. Nhân viên kinh doanh cố duyệt -> 403
    resp_sales = client.post(f"/api/v1/stock-adjustments/{adj_id}/approve", json={}, headers=sales_headers)
    assert resp_sales.status_code == 403, "Sales tuyệt đối không được duyệt điều chỉnh kho!"

    # 2. Kế toán cố duyệt -> 403
    resp_acc = client.post(f"/api/v1/stock-adjustments/{adj_id}/approve", json={}, headers=accountant_headers)
    assert resp_acc.status_code == 403, "Kế toán không được tự ý duyệt điều chỉnh kho!"

    # 3. Thủ kho thông thường (warehouse) cố duyệt -> 403
    resp_staff = client.post(f"/api/v1/stock-adjustments/{adj_id}/approve", json={}, headers=warehouse_staff_headers)
    assert resp_staff.status_code == 403, "Thủ kho thông thường không được duyệt phiếu điều chỉnh!"


# -----------------------------------------------------------------------------
# TC06: NGUYÊN TẮC BỐN MẮT (FOUR-EYES PRINCIPLE): Người lập không được tự duyệt
# -----------------------------------------------------------------------------
def test_four_eyes_principle_creator_cannot_approve_own_adjustment(client, warehouse_mgr1_headers):
    # warehouse_mgr là người lập phiếu
    db = SessionLocal()
    prod_id, actual_stock = get_valid_test_product(db, min_stock=5)
    db.close()

    payload = {
        "warehouse_id": "WH01",
        "reason_type": "lost",
        "reason_note": "Mất 1 chiếc trong ca làm",
        "items": [{"product_id": prod_id, "quantity": 1, "cost_price": 250000.0, "loss_value": 250000.0}]
    }
    create_res = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert create_res.status_code == 201, f"Lỗi tạo phiếu: {create_res.text}"
    adj_id = create_res.json()["id"]

    # CHÍNH warehouse_mgr gọi approve phiếu của mình -> PHẢI BỊ CHẶN 400 BAD REQUEST
    approve_res = client.post(
        f"/api/v1/stock-adjustments/{adj_id}/approve",
        json={"note": "Tôi tự duyệt phiếu của mình"},
        headers=warehouse_mgr1_headers
    )
    assert approve_res.status_code == 400, "Quy tắc Four-eyes: Phải chặn không cho người lập tự duyệt chính phiếu của mình!"
    err_detail = approve_res.json().get("detail", "")
    assert "four-eyes" in err_detail.lower() or "người lập" in err_detail.lower()


# -----------------------------------------------------------------------------
# TC07: Phê duyệt hợp lệ bởi Quản lý kho độc lập khác (warehouse_mgr2) hoặc Admin
# -----------------------------------------------------------------------------
def test_approve_stock_adjustment_success_by_independent_manager(
    client, warehouse_mgr1_headers, warehouse_mgr2_headers
):
    db = SessionLocal()
    prod_id, initial_stock = get_valid_test_product(db, min_stock=10)
    db.close()

    # 1. warehouse_mgr lập phiếu
    payload = {
        "warehouse_id": "WH01",
        "reason_type": "expired",
        "reason_note": "Thuốc/thực phẩm hết hạn sử dụng",
        "items": [{"product_id": prod_id, "quantity": 3, "cost_price": 100000.0, "loss_value": 300000.0}]
    }
    create_res = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert create_res.status_code == 201, f"Lỗi tạo phiếu: {create_res.text}"
    adj_data = create_res.json()
    adj_id = adj_data["id"]
    adj_code = adj_data["code"]

    # 2. warehouse_mgr2 (người khác) phê duyệt phiếu
    approve_res = client.post(
        f"/api/v1/stock-adjustments/{adj_id}/approve",
        json={"note": "Đã kiểm tra thực tế hàng hủy, đồng ý duyệt"},
        headers=warehouse_mgr2_headers
    )
    assert approve_res.status_code == 200, f"Duyệt thất bại: {approve_res.text}"
    appr_data = approve_res.json()
    assert appr_data["status"] == "APPROVED"
    assert appr_data["approved_by"] in ["warehouse_mgr2", "Quản Lý Kho Số 2"]
    assert appr_data["approved_at"] is not None

    # 3. Kiểm tra tồn kho thực tế ĐÃ ĐƯỢC TRỪ CHÍNH XÁC
    db = SessionLocal()
    ws_after = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == prod_id
    ).first()
    assert ws_after.actual_stock == initial_stock - 3, "Tồn kho thực tế phải giảm đúng 3 đơn vị!"

    # 4. Kiểm tra ghi Thẻ kho (InventoryLedgerEntity)
    ledger = db.query(InventoryLedgerEntity).filter(
        InventoryLedgerEntity.reference_code == adj_code,
        InventoryLedgerEntity.product_id == prod_id
    ).first()
    assert ledger is not None, "Phiếu điều chỉnh phải ghi vào Thẻ kho (InventoryLedgerEntity)!"
    assert ledger.transaction_type == "ADJUST"
    assert ledger.quantity == -3.0
    assert ledger.previous_stock == initial_stock
    assert ledger.new_stock == initial_stock - 3
    assert "Hàng hết hạn phải huỷ" in ledger.note
    assert "300.000" in ledger.note or "300,000" in ledger.note

    # 5. Kiểm tra ghi InventoryTransactionEntity
    tx = db.query(InventoryTransactionEntity).filter(
        InventoryTransactionEntity.product_id == prod_id,
        InventoryTransactionEntity.reason.like(f"%{adj_code}%")
    ).first()
    assert tx is not None, "Phải ghi nhận InventoryTransactionEntity!"
    assert tx.type == "adjust"
    assert tx.quantity == -3

    # 6. Kiểm tra AuditLogEntity
    audit = db.query(AuditLogEntity).filter(
        AuditLogEntity.entity_type == "StockAdjustment",
        AuditLogEntity.entity_id == adj_code,
        AuditLogEntity.action_type == "STOCK_ADJUSTMENT_APPROVED"
    ).first()
    assert audit is not None, "Phải ghi nhận nhật ký kiểm toán STOCK_ADJUSTMENT_APPROVED!"
    db.close()


# -----------------------------------------------------------------------------
# TC08: RÀNG BUỘC HIỂN THỊ THẺ KHO: CHỈ Quản lý kho và Admin mới thấy phiếu điều chỉnh
# -----------------------------------------------------------------------------
def test_stock_card_adjustment_visibility_manager_and_admin_only(
    client, warehouse_mgr1_headers, warehouse_mgr2_headers, warehouse_staff_headers, admin_headers
):
    db = SessionLocal()
    prod_id, actual_stock = get_valid_test_product(db, min_stock=5)
    db.close()

    # Tạo & duyệt 1 phiếu điều chỉnh để phát sinh bản ghi Thẻ kho
    payload = {
        "warehouse_id": "WH01",
        "reason_type": "damaged",
        "reason_note": "Test hiển thị thẻ kho bảo mật",
        "items": [{"product_id": prod_id, "quantity": 1, "cost_price": 500000.0, "loss_value": 500000.0}]
    }
    cr = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert cr.status_code == 201, f"Lỗi tạo: {cr.text}"
    adj_id = cr.json()["id"]
    adj_code = cr.json()["code"]

    ap = client.post(f"/api/v1/stock-adjustments/{adj_id}/approve", json={}, headers=warehouse_mgr2_headers)
    assert ap.status_code == 200, f"Lỗi duyệt: {ap.text}"

    # 1. Quản lý kho (warehouse_mgr) xem Thẻ kho -> PHẢI THẤY phiếu điều chỉnh ADJ
    resp_mgr = client.get(
        f"/api/v1/inventory/stock-card?product_id={prod_id}&warehouse_id=WH01",
        headers=warehouse_mgr1_headers
    )
    assert resp_mgr.status_code == 200
    mgr_items = resp_mgr.json()["items"]
    adj_in_mgr = any(it["reference_code"] == adj_code for it in mgr_items)
    assert adj_in_mgr is True, "Quản lý kho BẮT BUỘC phải nhìn thấy phiếu điều chỉnh trên Thẻ kho!"

    # 2. Admin xem Thẻ kho -> PHẢI THẤY phiếu điều chỉnh ADJ
    resp_admin = client.get(
        f"/api/v1/inventory/stock-card?product_id={prod_id}&warehouse_id=WH01",
        headers=admin_headers
    )
    assert resp_admin.status_code == 200
    admin_items = resp_admin.json()["items"]
    adj_in_admin = any(it["reference_code"] == adj_code for it in admin_items)
    assert adj_in_admin is True, "Admin BẮT BUỘC phải nhìn thấy phiếu điều chỉnh trên Thẻ kho!"

    # 3. THỦ KHO THÔNG THƯỜNG (warehouse) xem Thẻ kho -> TUYỆT ĐỐI KHÔNG ĐƯỢC THẤY phiếu điều chỉnh này!
    resp_staff = client.get(
        f"/api/v1/inventory/stock-card?product_id={prod_id}&warehouse_id=WH01",
        headers=warehouse_staff_headers
    )
    assert resp_staff.status_code == 200
    staff_items = resp_staff.json()["items"]
    adj_in_staff = any(it["reference_code"] == adj_code for it in staff_items)
    assert adj_in_staff is False, "Bảo mật thẻ kho: Thủ kho thông thường KHÔNG được thấy phiếu điều chỉnh tồn kho!"


# -----------------------------------------------------------------------------
# TC09: Từ chối duyệt phiếu điều chỉnh (REJECTED) kèm lý do
# -----------------------------------------------------------------------------
def test_reject_stock_adjustment_with_reason(client, warehouse_mgr1_headers, warehouse_mgr2_headers):
    db = SessionLocal()
    prod_id, initial_stock = get_valid_test_product(db, min_stock=5)
    db.close()

    # Lập phiếu
    payload = {
        "warehouse_id": "WH01",
        "reason_type": "lost",
        "reason_note": "Mất hàng nghi vấn",
        "items": [{"product_id": prod_id, "quantity": 2, "cost_price": 100000.0, "loss_value": 200000.0}]
    }
    cr = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert cr.status_code == 201, f"Lỗi tạo: {cr.text}"
    adj_id = cr.json()["id"]

    # Từ chối duyệt
    reject_res = client.post(
        f"/api/v1/stock-adjustments/{adj_id}/reject",
        json={"reason": "Không đủ bằng chứng mất hàng, yêu cầu trích xuất camera"},
        headers=warehouse_mgr2_headers
    )
    assert reject_res.status_code == 200, f"Lỗi từ chối: {reject_res.text}"
    r_data = reject_res.json()
    assert r_data["status"] == "REJECTED"
    assert r_data["rejection_reason"] == "Không đủ bằng chứng mất hàng, yêu cầu trích xuất camera"
    assert r_data["rejected_by"] in ["warehouse_mgr2", "Quản Lý Kho Số 2"]

    # Tồn kho không đổi
    db = SessionLocal()
    ws_after = db.query(WarehouseStockEntity).filter(
        WarehouseStockEntity.warehouse_id == "WH01",
        WarehouseStockEntity.product_id == prod_id
    ).first()
    assert ws_after.actual_stock == initial_stock, "Phiếu bị từ chối thì tồn kho không được thay đổi!"
    db.close()


# -----------------------------------------------------------------------------
# TC10: Chống duyệt lại phiếu đã hoàn tất hoặc đã từ chối
# -----------------------------------------------------------------------------
def test_cannot_reapprove_finalized_adjustment(client, warehouse_mgr1_headers, warehouse_mgr2_headers):
    # Lập phiếu & duyệt
    db = SessionLocal()
    prod_id, actual_stock = get_valid_test_product(db, min_stock=5)
    db.close()

    payload = {
        "warehouse_id": "WH01",
        "reason_type": "damaged",
        "reason_note": "Hư hỏng",
        "items": [{"product_id": prod_id, "quantity": 1, "cost_price": 50000.0, "loss_value": 50000.0}]
    }
    cr = client.post("/api/v1/stock-adjustments", json=payload, headers=warehouse_mgr1_headers)
    assert cr.status_code == 201, f"Lỗi tạo: {cr.text}"
    adj_id = cr.json()["id"]

    client.post(f"/api/v1/stock-adjustments/{adj_id}/approve", json={}, headers=warehouse_mgr2_headers)

    # Cố tình duyệt lại lần 2 -> 400
    re_appr = client.post(f"/api/v1/stock-adjustments/{adj_id}/approve", json={}, headers=warehouse_mgr2_headers)
    assert re_appr.status_code == 400, "Không được duyệt lại phiếu đã hoàn tất!"


# -----------------------------------------------------------------------------
# TC11: Quản trị viên (Admin) được phép tự duyệt phiếu điều chỉnh do chính mình lập
# -----------------------------------------------------------------------------
def test_admin_can_approve_own_adjustment(client, admin_headers):
    db = SessionLocal()
    prod_id, actual_stock = get_valid_test_product(db, min_stock=5)
    db.close()

    # Admin tạo phiếu
    payload = {
        "warehouse_id": "WH01",
        "reason_type": "lost",
        "reason_note": "Admin ghi nhận hao hụt kiểm kho",
        "items": [{"product_id": prod_id, "quantity": 1, "cost_price": 75000.0, "loss_value": 75000.0}]
    }
    cr = client.post("/api/v1/stock-adjustments", json=payload, headers=admin_headers)
    assert cr.status_code == 201, f"Admin tạo phiếu lỗi: {cr.text}"
    adj_id = cr.json()["id"]

    # Admin tự duyệt phiếu của chính mình -> Thành công 200 OK
    approve_res = client.post(
        f"/api/v1/stock-adjustments/{adj_id}/approve",
        json={"note": "Admin tự phê duyệt phiếu"},
        headers=admin_headers
    )
    assert approve_res.status_code == 200, f"Admin phải được tự duyệt phiếu của mình: {approve_res.text}"
    data = approve_res.json()
    assert data["status"] == "APPROVED"
    assert data["status_label"] == "Đã duyệt"

