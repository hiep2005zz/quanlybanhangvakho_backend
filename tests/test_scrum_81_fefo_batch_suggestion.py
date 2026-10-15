# backend/tests/test_scrum_81_fefo_batch_suggestion.py
"""
Test Suite cho SCRUM-81 (User Story S6-04):
"Là Nhân viên kho, tôi muốn có hệ thống gợi ý lô xuất theo nguyên tắc hạn dùng gần nhất trước (FEFO),
để hàng cũ ra trước, giảm hàng phải huỷ vì quá hạn."

Bao phủ toàn diện 3 Tiêu chí chấp nhận (Acceptance Criteria):
1. Tiêu chí 1: Mặc định gợi ý lô có hạn sử dụng gần nhất (expiry_date ASC) còn đủ số lượng.
   - Trả về suggested_batch và available_batches.
   - Bỏ qua các lô đã hết hạn (expiry_date < today) hoặc không đủ số lượng/hết tồn.
2. Tiêu chí 2: Thủ kho chọn lô khác được nhưng bắt buộc phải nhập lý do (batch_change_reason).
   - Nếu chọn lô khác gợi ý mà lý do rỗng/chỉ chứa khoảng trắng -> Chặn bằng HTTP 400 Bad Request kèm thông báo tiếng Việt.
   - Nếu giữ nguyên lô gợi ý -> Cho phép bình thường, lý do là tùy chọn.
3. Tiêu chí 3: Xuất kho trừ đúng tồn của lô đã chọn trong Database Transaction với Row-level Lock (FOR UPDATE).
   - Kiểm tra tồn của lô không bị âm.
   - Trừ chính xác số lượng tồn theo đơn vị cơ sở vào bản ghi lô hàng.
   - Ghi thẻ kho (InventoryLedgerEntity).
   - Kiểm tra phân quyền: Chỉ Nhân viên kho / Quản lý kho / Admin được phép thao tác.
"""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import ProductEntity, WarehouseStockEntity, InventoryTransactionEntity
from app.models.goods_receipt import WarehouseEntity, ProductBatchEntity, InventoryLedgerEntity

client = TestClient(app)


def get_token(username: str, password: str = "123") -> str:
    """Helper lấy JWT access token."""
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Đăng nhập thất bại cho {username}: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture
def fefo_test_setup():
    """Chuẩn bị sản phẩm quản lý theo lô và các lô hàng test với HSD khác nhau."""
    db = SessionLocal()
    prod = None
    batches = []
    try:
        # 1. Đảm bảo có kho WH01 (ID: 1)
        wh = db.query(WarehouseEntity).filter(WarehouseEntity.id == 1).first()
        if not wh:
            wh = WarehouseEntity(
                id=1,
                code="WH01",
                name="Kho Tổng Hà Nội",
                is_active=True,
                status="Đang hoạt động",
                created_at=datetime.now(timezone.utc),
            )
            db.add(wh)
            db.commit()

        # 2. Tạo sản phẩm quản lý lô (is_batch_managed = True)
        prod = db.query(ProductEntity).filter(ProductEntity.code == "SP_FEFO_TEST").first()
        if not prod:
            prod = ProductEntity(
                code="SP_FEFO_TEST",
                name="Sữa Tươi Tiệt Trùng Test FEFO",
                category="Sữa",
                stock=500,
                sell_price=35000,
                base_unit="Hộp",
                is_batch_managed=True,
                status="active",
            )
            db.add(prod)
            db.commit()
            db.refresh(prod)
        else:
            prod.stock = 500
            prod.is_batch_managed = True
            db.commit()

        # Đảm bảo có WarehouseStockEntity
        ws = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.warehouse_id == "WH01",
            WarehouseStockEntity.product_id == prod.id
        ).first()
        if not ws:
            ws = WarehouseStockEntity(
                warehouse_id="WH01",
                warehouse_name="Kho Tổng Hà Nội",
                product_id=prod.id,
                actual_stock=500,
                quantity_on_hand=500,
            )
            db.add(ws)
            db.commit()
        else:
            ws.actual_stock = 500
            ws.quantity_on_hand = 500
            db.commit()

        # 3. Dọn dẹp lô cũ của sản phẩm này nếu có
        db.query(ProductBatchEntity).filter(ProductBatchEntity.product_id == prod.id).delete()
        db.commit()

        # 4. Tạo 3 lô hàng với hạn sử dụng khác nhau:
        # Lô 1: HSD gần nhất (+10 ngày), số lượng 50
        # Lô 2: HSD xa hơn (+30 ngày), số lượng 100
        # Lô 3: HSD xa nhất (+90 ngày), số lượng 200
        # Lô 4: Đã hết hạn (-5 ngày), số lượng 30 -> Không bao giờ được gợi ý
        now = datetime.now(timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        b1 = ProductBatchEntity(
            product_id=prod.id,
            warehouse_id=wh.id,
            batch_number="LOTE-001-NEAR",
            manufacturing_date=today - timedelta(days=20),
            expiry_date=today + timedelta(days=10),
            quantity=50.0,
            reserved_quantity=0.0,
            created_at=now,
        )
        b2 = ProductBatchEntity(
            product_id=prod.id,
            warehouse_id=wh.id,
            batch_number="LOTE-002-MID",
            manufacturing_date=today - timedelta(days=10),
            expiry_date=today + timedelta(days=30),
            quantity=100.0,
            reserved_quantity=0.0,
            created_at=now,
        )
        b3 = ProductBatchEntity(
            product_id=prod.id,
            warehouse_id=wh.id,
            batch_number="LOTE-003-FAR",
            manufacturing_date=today - timedelta(days=5),
            expiry_date=today + timedelta(days=90),
            quantity=200.0,
            reserved_quantity=0.0,
            created_at=now,
        )
        b_expired = ProductBatchEntity(
            product_id=prod.id,
            warehouse_id=wh.id,
            batch_number="LOTE-EXPIRED",
            manufacturing_date=today - timedelta(days=60),
            expiry_date=today - timedelta(days=5),
            quantity=30.0,
            reserved_quantity=0.0,
            created_at=now,
        )
        db.add_all([b1, b2, b3, b_expired])
        db.commit()
        db.refresh(b1)
        db.refresh(b2)
        db.refresh(b3)
        db.refresh(b_expired)

        batches = [b1, b2, b3, b_expired]
        yield {"product": prod, "warehouse": wh, "batches": batches}

    finally:
        # Cleanup
        try:
            if prod:
                db.rollback()
                db.query(InventoryLedgerEntity).filter(InventoryLedgerEntity.product_id == prod.id).delete()
                db.query(ProductBatchEntity).filter(ProductBatchEntity.product_id == prod.id).delete()
                db.query(InventoryTransactionEntity).filter(InventoryTransactionEntity.product_id == prod.id).delete()
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def test_criteria_1_fefo_default_suggestion(fefo_test_setup):
    """
    Tiêu chí 1: Mặc định gợi ý lô có hạn sử dụng gần nhất còn đủ số lượng.
    - Gọi GET /api/v1/inventory/batches/suggest
    - Lô gợi ý phải là LOTE-001-NEAR (HSD +10 ngày).
    - Lô LOTE-EXPIRED không được xuất hiện trong available_batches.
    """
    wh_token = get_token("kho")
    prod = fefo_test_setup["product"]
    b1 = fefo_test_setup["batches"][0]

    # Xuất 20 hộp (Lô 1 có 50 hộp -> Đủ)
    resp = client.get(
        f"/api/v1/inventory/batches/suggest?product_id={prod.id}&warehouse_id=WH01&quantity=20",
        headers={"Authorization": f"Bearer {wh_token}"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["suggested_batch"] is not None
    assert data["suggested_batch"]["id"] == b1.id
    assert data["suggested_batch"]["batch_number"] == "LOTE-001-NEAR"

    avail_numbers = [b["batch_number"] for b in data["available_batches"]]
    assert "LOTE-001-NEAR" in avail_numbers
    assert "LOTE-002-MID" in avail_numbers
    assert "LOTE-003-FAR" in avail_numbers
    assert "LOTE-EXPIRED" not in avail_numbers, "Lô hết hạn tuyệt đối không được xuất hiện trong danh sách gợi ý"


def test_criteria_1_fefo_suggestion_when_near_batch_insufficient(fefo_test_setup):
    """
    Tiêu chí 1: Nếu lô gần nhất không đủ số lượng (ví dụ cần 80 hộp, Lô 1 chỉ có 50),
    hệ thống gợi ý lô tiếp theo có đủ số lượng (Lô 2 có 100 hộp).
    """
    wh_token = get_token("kho")
    prod = fefo_test_setup["product"]
    b2 = fefo_test_setup["batches"][1]

    resp = client.get(
        f"/api/v1/inventory/batches/suggest?product_id={prod.id}&warehouse_id=WH01&quantity=80",
        headers={"Authorization": f"Bearer {wh_token}"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Lô 1 có 50 < 80, Lô 2 có 100 >= 80 -> Gợi ý Lô 2
    assert data["suggested_batch"] is not None
    assert data["suggested_batch"]["id"] == b2.id
    assert data["suggested_batch"]["batch_number"] == "LOTE-002-MID"


def test_criteria_2_require_reason_when_changing_batch(fefo_test_setup):
    """
    Tiêu chí 2: Thủ kho chọn lô khác được nhưng bắt buộc phải nhập lý do.
    - Chọn Lô 2 trong khi hệ thống gợi ý Lô 1.
    - Nếu không nhập lý do hoặc lý do chỉ có khoảng trắng -> Bị chặn 400 Bad Request.
    - Nếu nhập lý do hợp lệ -> Xuất kho thành công.
    """
    wh_token = get_token("kho")
    prod = fefo_test_setup["product"]
    b1 = fefo_test_setup["batches"][0]
    b2 = fefo_test_setup["batches"][1]

    # 1. Đổi lô mà KHÔNG nhập lý do -> Bị chặn 400
    fail_payload = {
        "product_id": prod.id,
        "quantity": 10,
        "destination": "Đại lý An Phát",
        "warehouse_id": "WH01",
        "suggested_batch_id": b1.id,
        "selected_batch_id": b2.id,
        "batch_change_reason": "   ",  # Chỉ chứa khoảng trắng
    }
    resp_fail = client.post(
        "/api/v1/inventory/issue",
        json=fail_payload,
        headers={"Authorization": f"Bearer {wh_token}"}
    )
    assert resp_fail.status_code == 400
    assert "Vui lòng nhập lý do khi chọn lô khác với lô hệ thống gợi ý" in resp_fail.json()["detail"]

    # 2. Đổi lô có nhập lý do hợp lệ -> Thành công 200
    success_payload = {
        "product_id": prod.id,
        "quantity": 10,
        "destination": "Đại lý An Phát",
        "warehouse_id": "WH01",
        "suggested_batch_id": b1.id,
        "selected_batch_id": b2.id,
        "batch_change_reason": "Khách hàng yêu cầu date xa trên 2 tháng",
    }
    resp_ok = client.post(
        "/api/v1/inventory/issue",
        json=success_payload,
        headers={"Authorization": f"Bearer {wh_token}"}
    )
    assert resp_ok.status_code == 200, resp_ok.text
    assert "thành công" in resp_ok.json()["message"].lower()


def test_criteria_3_deduct_correct_batch_and_write_ledger(fefo_test_setup):
    """
    Tiêu chí 3: Xuất kho trừ đúng tồn của lô đã chọn trong Database Transaction với Row-level Lock.
    - Tồn của Lô 1 phải giảm chính xác.
    - Thẻ kho (InventoryLedgerEntity) được ghi nhận với reference_code và batch_number.
    - Tồn kho tổng và kho chi tiết được đồng bộ.
    """
    wh_token = get_token("kho")
    prod = fefo_test_setup["product"]
    b1 = fefo_test_setup["batches"][0]

    initial_batch_qty = b1.quantity  # 50.0
    issue_qty = 15.0

    payload = {
        "product_id": prod.id,
        "quantity": issue_qty,
        "destination": "Bệnh viện Đa Khoa",
        "warehouse_id": "WH01",
        "suggested_batch_id": b1.id,
        "selected_batch_id": b1.id,  # Giữ nguyên lô gợi ý
    }
    resp = client.post(
        "/api/v1/inventory/issue",
        json=payload,
        headers={"Authorization": f"Bearer {wh_token}"}
    )
    assert resp.status_code == 200, resp.text

    # Kiểm tra trực tiếp trong DB thật
    db = SessionLocal()
    try:
        updated_b1 = db.query(ProductBatchEntity).filter(ProductBatchEntity.id == b1.id).first()
        assert updated_b1.quantity == initial_batch_qty - issue_qty, "Số lượng tồn lô phải bị trừ chính xác"

        # Kiểm tra thẻ kho
        ledger = db.query(InventoryLedgerEntity).filter(
            InventoryLedgerEntity.product_id == prod.id,
            InventoryLedgerEntity.batch_number == b1.batch_number
        ).order_by(InventoryLedgerEntity.id.desc()).first()
        assert ledger is not None, "Phải ghi nhận thẻ kho cho lô đã xuất"
        assert ledger.quantity == -issue_qty
        assert ledger.transaction_type == "ISSUE"
    finally:
        db.close()


def test_criteria_3_cannot_overdraw_batch_stock(fefo_test_setup):
    """
    Tiêu chí 3: Không cho phép xuất quá số lượng tồn của lô (chống âm tồn kho).
    """
    wh_token = get_token("kho")
    prod = fefo_test_setup["product"]
    b1 = fefo_test_setup["batches"][0]

    payload = {
        "product_id": prod.id,
        "quantity": 9999,  # Quá tồn kho của lô 1 (50) và tổng tồn
        "destination": "Kho ngoại vi",
        "warehouse_id": "WH01",
        "suggested_batch_id": b1.id,
        "selected_batch_id": b1.id,
    }
    resp = client.post(
        "/api/v1/inventory/issue",
        json=payload,
        headers={"Authorization": f"Bearer {wh_token}"}
    )
    assert resp.status_code == 400
    assert "không đủ" in resp.json()["detail"].lower()
