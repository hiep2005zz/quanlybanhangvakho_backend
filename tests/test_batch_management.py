# backend/tests/test_batch_management.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import ProductEntity, UserEntity, WarehouseStockEntity
from app.models.goods_receipt import ProductBatchEntity, WarehouseEntity
from app.core.security import get_password_hash, create_access_token


@pytest.fixture(scope="module")
def db_session():
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def setup_users_and_products(db_session: Session):
    now = datetime.now(timezone.utc)

    # 1. Ensure Warehouse 1 exists
    wh = db_session.query(WarehouseEntity).filter(WarehouseEntity.id == 1).first()
    if not wh:
        wh = WarehouseEntity(id=1, code="WH01", name="Kho Tổng Hà Nội", is_active=True)
        db_session.add(wh)

    # 2. Users: admin, warehouse_manager, warehouse_staff, sales
    users_data = [
        ("batch_admin", "batch_admin@test.com", "admin", "Admin Batch Test"),
        ("batch_wm", "batch_wm@test.com", "warehouse_manager", "Warehouse Manager Test"),
        ("batch_staff", "batch_staff@test.com", "warehouse", "Warehouse Staff Test"),
        ("batch_sales", "batch_sales@test.com", "sales", "Sales Test"),
    ]
    tokens = {}
    for uname, email, role, fname in users_data:
        u = db_session.query(UserEntity).filter(UserEntity.username == uname).first()
        if not u:
            u = UserEntity(
                username=uname,
                email=email,
                role=role,
                full_name=fname,
                hashed_password=get_password_hash("TestPass123!"),
                is_active=True,
                token_version=1,
            )
            db_session.add(u)
            db_session.commit()
            db_session.refresh(u)
        tokens[role] = create_access_token(
            subject=u.username,
            role=u.role,
            token_version=u.token_version,
        )

    # 3. Product with batch management OFF
    p_nobatch = db_session.query(ProductEntity).filter(ProductEntity.code == "SKU-NOBATCH-01").first()
    if not p_nobatch:
        p_nobatch = ProductEntity(
            code="SKU-NOBATCH-01",
            name="Sữa Tươi Tiệt Trùng 1L",
            stock=100,
            cost_price=20000.0,
            sell_price=30000.0,
            base_unit="Hộp",
            is_batch_managed=False,
            status="active",
        )
        db_session.add(p_nobatch)
        db_session.commit()
        db_session.refresh(p_nobatch)
    else:
        p_nobatch.cost_price = 20000.0
        p_nobatch.sell_price = 30000.0
        p_nobatch.stock = 100
        p_nobatch.is_batch_managed = False

    db_session.query(ProductBatchEntity).filter(ProductBatchEntity.product_id == p_nobatch.id).delete()
    db_session.commit()

    # 4. Product with batch management ON
    p_batch = db_session.query(ProductEntity).filter(ProductEntity.code == "SKU-BATCH-01").first()
    if not p_batch:
        p_batch = ProductEntity(
            code="SKU-BATCH-01",
            name="Thuốc Kháng Sinh Amoxicillin 500mg",
            stock=50,
            cost_price=45000.0,
            sell_price=65000.0,
            base_unit="Hộp",
            is_batch_managed=True,
            status="active",
        )
        db_session.add(p_batch)
        db_session.commit()
        db_session.refresh(p_batch)
    else:
        p_batch.cost_price = 45000.0
        p_batch.sell_price = 65000.0
        p_batch.stock = 50
        p_batch.is_batch_managed = True

    db_session.query(ProductBatchEntity).filter(ProductBatchEntity.product_id == p_batch.id).delete()
    db_session.commit()

    # 5. Add initial warehouse stock if not present
    for p in [p_nobatch, p_batch]:
        ws = db_session.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == p.id,
        ).first()
        if not ws:
            db_session.add(
                WarehouseStockEntity(
                    warehouse_id="WH01",
                    warehouse_name="Kho Tổng Hà Nội",
                    product_id=p.id,
                    actual_stock=p.stock,
                    quantity_on_hand=p.stock,
                )
            )
        else:
            ws.actual_stock = p.stock
            ws.quantity_on_hand = p.stock
    db_session.commit()

    yield {
        "tokens": tokens,
        "product_nobatch_id": p_nobatch.id,
        "product_batch_id": p_batch.id,
    }

    db_session.query(ProductBatchEntity).filter(ProductBatchEntity.product_id.in_([p_nobatch.id, p_batch.id])).delete()
    p_nobatch.is_batch_managed = False
    p_batch.is_batch_managed = True
    db_session.commit()


# =====================================================================
# TIÊU CHÍ 1: Bật / Tắt quản lý lô theo từng SKU
# =====================================================================
def test_criterion_1_toggle_batch_tracking_boundary_conditions(client, setup_users_and_products):
    token_staff = setup_users_and_products["tokens"]["warehouse"]
    headers = {"Authorization": f"Bearer {token_staff}"}
    p_id = setup_users_and_products["product_nobatch_id"]

    # 1. Bật quản lý lô cho SKU đang có tồn kho (100 Hộp) nhưng KHÔNG truyền thông tin lô ban đầu -> CHẶN 400
    res = client.patch(
        f"/api/v1/batches/product/{p_id}/tracking",
        json={"is_batch_managed": True},
        headers=headers,
    )
    assert res.status_code == 400
    assert "chưa được phân bổ vào lô" in res.json()["detail"]

    # 2. Bật quản lý lô với HSD trước ngày sản xuất -> CHẶN 422/400 validation
    now = datetime.now(timezone.utc)
    res_invalid_dates = client.patch(
        f"/api/v1/batches/product/{p_id}/tracking",
        json={
            "is_batch_managed": True,
            "initial_batch_number": "LOT-INIT-01",
            "initial_manufacturing_date": (now + timedelta(days=10)).isoformat(),
            "initial_expiry_date": now.isoformat(),
        },
        headers=headers,
    )
    assert res_invalid_dates.status_code == 422 or res_invalid_dates.status_code == 400

    # 3. Bật quản lý lô hợp lệ kèm số lô và HSD -> Thành công, tự động tạo lô cho tồn kho hiện hữu
    res_ok = client.patch(
        f"/api/v1/batches/product/{p_id}/tracking",
        json={
            "is_batch_managed": True,
            "initial_batch_number": "LOT-INIT-01",
            "initial_manufacturing_date": (now - timedelta(days=30)).isoformat(),
            "initial_expiry_date": (now + timedelta(days=180)).isoformat(),
            "reason": "Bật quản lý hạn dùng cho sản phẩm thực phẩm",
        },
        headers=headers,
    )
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["is_batch_managed"] is True
    assert data["batch_created"] is not None
    assert data["batch_created"]["batch_number"] == "LOT-INIT-01"
    assert data["batch_created"]["remaining_quantity"] == 100.0

    # 4. Tắt quản lý lô -> Thành công, bảo lưu các lô cũ
    res_disable = client.patch(
        f"/api/v1/batches/product/{p_id}/tracking",
        json={"is_batch_managed": False, "reason": "Tạm dừng theo dõi lô"},
        headers=headers,
    )
    assert res_disable.status_code == 200
    assert res_disable.json()["is_batch_managed"] is False


# =====================================================================
# TIÊU CHÍ 2: Quản lý thông tin từng lô (4 thuộc tính, Validation, Không âm kho)
# =====================================================================
def test_criterion_2_batch_creation_and_constraints(client, setup_users_and_products):
    token_staff = setup_users_and_products["tokens"]["warehouse"]
    headers = {"Authorization": f"Bearer {token_staff}"}
    p_id = setup_users_and_products["product_batch_id"]
    now = datetime.now(timezone.utc)

    # 1. Chặn tạo lô có HSD trước hoặc bằng Ngày sản xuất
    res_bad_date = client.post(
        "/api/v1/batches",
        json={
            "product_id": p_id,
            "warehouse_id": 1,
            "batch_number": "LOT-ERR-01",
            "manufacturing_date": now.isoformat(),
            "expiry_date": (now - timedelta(days=1)).isoformat(),
            "initial_quantity": 10,
        },
        headers=headers,
    )
    assert res_bad_date.status_code in [400, 422]

    # 2. Tạo lô hợp lệ với 4 thuộc tính cốt lõi
    res_create = client.post(
        "/api/v1/batches",
        json={
            "product_id": p_id,
            "warehouse_id": 1,
            "batch_number": "LOT-MED-VALID-01",
            "manufacturing_date": (now - timedelta(days=20)).isoformat(),
            "expiry_date": (now + timedelta(days=90)).isoformat(),
            "initial_quantity": 25,
            "note": "Lô hàng mẫu kiểm nghiệm",
        },
        headers=headers,
    )
    assert res_create.status_code == 201
    b_data = res_create.json()
    assert b_data["batch_number"] == "LOT-MED-VALID-01"
    assert b_data["remaining_quantity"] == 25.0
    assert b_data["expiry_status"] == "SAFE"

    # 3. Trừ kho vượt quá số lượng còn lại -> CHẶN 400 (Không gây âm kho)
    res_overdraw = client.post(
        "/api/v1/inventory/issue",
        json={
            "product_id": p_id,
            "quantity": 50,  # Lô chỉ có 25
            "destination": "Bệnh viện Bạch Mai",
            "batch_number": "LOT-MED-VALID-01",
        },
        headers=headers,
    )
    assert res_overdraw.status_code == 400
    assert "không đủ" in res_overdraw.json()["detail"]


# =====================================================================
# TIÊU CHÍ 3: Cảnh báo lô sắp hết hạn theo ngưỡng khai báo được
# =====================================================================
def test_criterion_3_configurable_expiry_alert_threshold(client, setup_users_and_products):
    token_staff = setup_users_and_products["tokens"]["warehouse"]
    headers = {"Authorization": f"Bearer {token_staff}"}
    p_id = setup_users_and_products["product_batch_id"]
    now = datetime.now(timezone.utc)

    # Tạo lô còn 15 ngày nữa hết hạn
    client.post(
        "/api/v1/batches",
        json={
            "product_id": p_id,
            "warehouse_id": 1,
            "batch_number": "LOT-EXP-15DAYS",
            "manufacturing_date": (now - timedelta(days=100)).isoformat(),
            "expiry_date": (now + timedelta(days=15)).isoformat(),
            "initial_quantity": 10,
        },
        headers=headers,
    )

    # 1. Truy vấn với ngưỡng warning_days = 10 ngày -> Lô 15 ngày phải là SAFE
    res_10 = client.get(
        f"/api/v1/batches?product_id={p_id}&warning_days=10&search=LOT-EXP-15DAYS",
        headers=headers,
    )
    assert res_10.status_code == 200
    items_10 = res_10.json()["items"]
    assert len(items_10) == 1
    assert items_10[0]["expiry_status"] == "SAFE"

    # 2. Truy vấn với ngưỡng warning_days = 30 ngày -> Lô 15 ngày chạm ngưỡng -> NEAR_EXPIRY
    res_30 = client.get(
        f"/api/v1/batches?product_id={p_id}&warning_days=30&search=LOT-EXP-15DAYS",
        headers=headers,
    )
    assert res_30.status_code == 200
    items_30 = res_30.json()["items"]
    assert len(items_30) == 1
    assert items_30[0]["expiry_status"] == "NEAR_EXPIRY"


# =====================================================================
# TIÊU CHÍ 4: Chặn xuất lô quá hạn & Cơ chế Quản lý kho ghi đè (Override)
# =====================================================================
def test_criterion_4_expired_batch_hard_block_and_manager_override(client, setup_users_and_products):
    tokens = setup_users_and_products["tokens"]
    staff_headers = {"Authorization": f"Bearer {tokens['warehouse']}"}
    manager_headers = {"Authorization": f"Bearer {tokens['warehouse_manager']}"}
    p_id = setup_users_and_products["product_batch_id"]
    now = datetime.now(timezone.utc)

    # Tạo 1 lô ĐÃ QUÁ HẠN (hết hạn 5 ngày trước)
    res_exp_lot = client.post(
        "/api/v1/batches",
        json={
            "product_id": p_id,
            "warehouse_id": 1,
            "batch_number": "LOT-EXPIRED-99",
            "manufacturing_date": (now - timedelta(days=200)).isoformat(),
            "expiry_date": (now - timedelta(days=5)).isoformat(),
            "initial_quantity": 20,
            "note": "Lô hàng tồn bị quá hạn lưu kho",
        },
        headers=staff_headers,
    )
    assert res_exp_lot.status_code == 201

    # 1. Nhân viên kho xuất lô quá hạn thông thường -> MẶC ĐỊNH CHẶN CỨNG 400
    res_block = client.post(
        "/api/v1/inventory/issue",
        json={
            "product_id": p_id,
            "quantity": 5,
            "destination": "Đại lý Hà Đông",
            "batch_number": "LOT-EXPIRED-99",
        },
        headers=staff_headers,
    )
    assert res_block.status_code == 400
    assert "quá hạn sử dụng" in res_block.json()["detail"]

    # 2. Nhân viên kho cố tình gửi cờ override_expired=True -> CHẶN 403 Forbidden (Không đủ thẩm quyền)
    res_forbidden_override = client.post(
        "/api/v1/inventory/issue",
        json={
            "product_id": p_id,
            "quantity": 5,
            "destination": "Đại lý Hà Đông",
            "batch_number": "LOT-EXPIRED-99",
            "override_expired": True,
            "override_reason": "Tôi tự ý xuất",
        },
        headers=staff_headers,
    )
    assert res_forbidden_override.status_code == 403
    assert "Chỉ Quản lý kho" in res_forbidden_override.json()["detail"]

    # 3. Quản lý kho (Warehouse Manager) gửi override_expired=True nhưng THIẾU lý do -> CHẶN 400
    res_missing_reason = client.post(
        "/api/v1/inventory/issue",
        json={
            "product_id": p_id,
            "quantity": 5,
            "destination": "Khu tiêu hủy dược phẩm",
            "batch_number": "LOT-EXPIRED-99",
            "override_expired": True,
            "override_reason": "",
        },
        headers=manager_headers,
    )
    assert res_missing_reason.status_code == 400
    assert "lý do" in res_missing_reason.json()["detail"]

    # 4. Quản lý kho gửi override_expired=True kèm lý do đầy đủ -> XUẤT THÀNH CÔNG & TRỪ TỒN
    res_override_ok = client.post(
        "/api/v1/inventory/issue",
        json={
            "product_id": p_id,
            "quantity": 5,
            "destination": "Khu tiêu hủy dược phẩm",
            "batch_number": "LOT-EXPIRED-99",
            "override_expired": True,
            "override_reason": "Xuất mẫu sang phòng kiểm nghiệm độc chất tiêu hủy",
        },
        headers=manager_headers,
    )
    assert res_override_ok.status_code == 200
    assert "Xuất kho thành công" in res_override_ok.json()["message"]

    # Kiểm tra số lượng còn lại của lô sau khi xuất (20 - 5 = 15)
    res_check = client.get(
        f"/api/v1/batches?product_id={p_id}&search=LOT-EXPIRED-99",
        headers=staff_headers,
    )
    assert res_check.status_code == 200
    items = res_check.json()["items"]
    assert len(items) == 1
    assert items[0]["remaining_quantity"] == 15.0


# =====================================================================
# PHÂN TRANG: Kiểm tra chia nhỏ 10 sản phẩm/trang (page=1, page=2)
# =====================================================================
def test_criterion_pagination_page_and_page_size(client, setup_users_and_products):
    token_staff = setup_users_and_products["tokens"]["warehouse"]
    headers = {"Authorization": f"Bearer {token_staff}"}
    p_id = setup_users_and_products["product_batch_id"]

    # Đảm bảo có ít nhất 2 lô cho product này
    res_p1 = client.get(
        f"/api/v1/batches?product_id={p_id}&page=1&page_size=1",
        headers=headers,
    )
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert data_p1["page"] == 1
    assert data_p1["page_size"] == 1
    assert len(data_p1["items"]) == 1
    assert data_p1["total"] >= 2
    assert data_p1["total_pages"] >= 2

    # Lấy trang 2
    res_p2 = client.get(
        f"/api/v1/batches?product_id={p_id}&page=2&page_size=1",
        headers=headers,
    )
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert data_p2["page"] == 2
    assert data_p2["page_size"] == 1
    assert len(data_p2["items"]) == 1

    # Hai trang phải trả về 2 lô khác nhau
    assert data_p1["items"][0]["id"] != data_p2["items"][0]["id"]

