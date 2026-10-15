# backend/tests/test_stock_card.py
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import ProductEntity
from app.models.goods_receipt import WarehouseEntity, InventoryLedgerEntity

client = TestClient(app)


def get_token(username: str = "kho", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture
def stock_card_seed_data():
    db = SessionLocal()
    try:
        # Đảm bảo có warehouses chuẩn
        w1 = db.query(WarehouseEntity).filter(WarehouseEntity.code == "WH01").first()
        if not w1:
            w1 = WarehouseEntity(code="WH01", name="Kho Tổng Hà Nội", is_active=True)
            db.add(w1)
        w2 = db.query(WarehouseEntity).filter(WarehouseEntity.code == "WH02").first()
        if not w2:
            w2 = WarehouseEntity(code="WH02", name="Kho Chi Nhánh TP.HCM", is_active=True)
            db.add(w2)
        db.commit()

        # Tạo một sản phẩm chuyên dụng để test thẻ kho
        prod = db.query(ProductEntity).filter(ProductEntity.code == "TEST-SC-001").first()
        if not prod:
            prod = ProductEntity(
                code="TEST-SC-001",
                name="Sản Phẩm Thử Nghiệm Thẻ Kho",
                category="Thời trang",
                base_unit="Cái",
                cost_price=100000.0,
                sell_price=150000.0,
                stock=120,
                min_stock=10,
                status="active",
            )
            db.add(prod)
            db.commit()
            db.refresh(prod)

        # Xóa các ledger cũ nếu có của sản phẩm này
        db.query(InventoryLedgerEntity).filter(InventoryLedgerEntity.product_id == prod.id).delete()
        db.commit()

        now = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)

        # 1. Phát sinh 1: Nhập kho ban đầu (DK-001) ngày 2026-10-01
        entry1 = InventoryLedgerEntity(
            product_id=prod.id,
            warehouse_id=w1.id,
            reference_code="DK-TEST-001",
            transaction_type="RECEIPT",
            quantity=100.0,
            previous_stock=0.0,
            new_stock=100.0,
            performed_by="Admin",
            note="Số dư đầu kỳ nhập kho",
            created_at=now,
        )

        # 2. Phát sinh 2: Nhập kho từ NCC (GRN-20261005-0001) ngày 2026-10-05
        entry2 = InventoryLedgerEntity(
            product_id=prod.id,
            warehouse_id=w1.id,
            reference_code="GRN-20261005-0001",
            transaction_type="RECEIPT",
            quantity=50.0,
            previous_stock=100.0,
            new_stock=150.0,
            performed_by="Thủ kho",
            note="Nhập kho theo phiếu GRN-20261005-0001",
            created_at=now + timedelta(days=4),
        )

        # 3. Phát sinh 3: Xuất bán hàng theo đơn hàng (ORD-TEST-001) ngày 2026-10-08
        entry3 = InventoryLedgerEntity(
            product_id=prod.id,
            warehouse_id=w1.id,
            reference_code="ORD-TEST-001",
            transaction_type="ISSUE",
            quantity=-20.0,
            previous_stock=150.0,
            new_stock=130.0,
            performed_by="Sales",
            note="Xuất bán theo đơn ORD-TEST-001",
            created_at=now + timedelta(days=7),
        )

        # 4. Phát sinh 4: Xuất chuyển kho sang WH02 (CK-20261010-001) ngày 2026-10-10
        entry4 = InventoryLedgerEntity(
            product_id=prod.id,
            warehouse_id=w1.id,
            reference_code="CK-20261010-001",
            transaction_type="TRANSFER",
            quantity=-15.0,
            previous_stock=130.0,
            new_stock=115.0,
            performed_by="Thủ kho",
            note="Xuất chuyển kho sang Kho Chi Nhánh TP.HCM",
            created_at=now + timedelta(days=9),
        )

        # 5. Phát sinh 5: Điều chỉnh kiểm kê (PKK-20261012-0001) ngày 2026-10-12
        entry5 = InventoryLedgerEntity(
            product_id=prod.id,
            warehouse_id=w1.id,
            reference_code="PKK-20261012-0001",
            transaction_type="ADJUST",
            quantity=+5.0,
            previous_stock=115.0,
            new_stock=120.0,
            performed_by="Quản lý kho",
            note="Cân đối tồn sau kiểm kê thực tế thừa 5",
            created_at=now + timedelta(days=11),
        )

        db.add_all([entry1, entry2, entry3, entry4, entry5])
        db.commit()
        return prod.id
    finally:
        db.close()


def test_requirement_1_chronological_order(stock_card_seed_data):
    """
    Tiêu chí 1: Danh sách toàn bộ phát sinh nhập, xuất, chuyển, điều chỉnh theo thứ tự thời gian.
    """
    prod_id = stock_card_seed_data
    token = get_token("kho", "123")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(f"/api/v1/inventory/stock-card?product_id={prod_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    entries = data["entries"]
    assert len(entries) >= 5

    # Kiểm tra thứ tự thời gian tăng dần
    dates = [e["created_at"] for e in entries]
    assert dates == sorted(dates)

    # Kiểm tra có đầy đủ 4 loại nghiệp vụ: RECEIPT, ISSUE, TRANSFER, ADJUST
    types = {e["transaction_type"] for e in entries}
    assert "RECEIPT" in types
    assert "ISSUE" in types
    assert "TRANSFER" in types
    assert "ADJUST" in types


def test_requirement_2_balance_stock_and_voucher_links(stock_card_seed_data):
    """
    Tiêu chí 2: Mỗi dòng hiển thị tồn sau phát sinh (balance_stock) và liên kết tới chứng từ gốc.
    """
    prod_id = stock_card_seed_data
    token = get_token("kho", "123")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(f"/api/v1/inventory/stock-card?product_id={prod_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    entries = data["entries"]
    for e in entries:
        # Kiểm tra tính toàn vẹn toán học: balance_stock = previous_stock + quantity
        assert round(e["balance_stock"], 2) == round(e["previous_stock"] + e["quantity"], 2)

        # Kiểm tra liên kết chứng từ gốc
        v_info = e["voucher_info"]
        assert v_info is not None
        assert v_info["voucher_code"] == e["reference_code"]
        assert v_info["voucher_type"] in ["grn", "order", "transfer", "audit", "initial", "adjustment"]

        # Kiểm tra đường dẫn URL đối với các chứng từ hệ thống
        if v_info["voucher_type"] == "grn":
            assert "/goods-receipt" in v_info["url"]
        elif v_info["voucher_type"] == "transfer":
            assert "/stock-transfers" in v_info["url"]
        elif v_info["voucher_type"] == "audit":
            assert "/stock-audits" in v_info["url"]


def test_requirement_3_filter_warehouse_and_date_range(stock_card_seed_data):
    """
    Tiêu chí 3: Lọc theo kho và khoảng thời gian.
    """
    prod_id = stock_card_seed_data
    token = get_token("kho", "123")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Lọc theo kho WH01
    resp_wh = client.get(f"/api/v1/inventory/stock-card?product_id={prod_id}&warehouse_id=WH01", headers=headers)
    assert resp_wh.status_code == 200
    entries_wh = resp_wh.json()["entries"]
    for e in entries_wh:
        assert e["warehouse_id"] == "WH01"

    # 2. Lọc theo khoảng thời gian: Từ 2026-10-06 đến 2026-10-11
    # Kỳ này sẽ bao gồm ORD (ngày 8) và CK (ngày 10). Giao dịch ngày 1 và ngày 5 nằm ở tồn đầu kỳ!
    resp_date = client.get(
        f"/api/v1/inventory/stock-card?product_id={prod_id}&from_date=2026-10-06&to_date=2026-10-11",
        headers=headers
    )
    assert resp_date.status_code == 200
    data_date = resp_date.json()

    summary = data_date["summary"]
    entries_date = data_date["entries"]

    # Tồn đầu kỳ tính từ 2 giao dịch ngày 1 (+100) và ngày 5 (+50) = 150
    assert summary["opening_stock"] == 150.0

    # Trong kỳ có 2 giao dịch: -20 (ORD) và -15 (CK) -> Tổng xuất = 35
    assert summary["total_issue"] == 35.0
    assert summary["total_receipt"] == 0.0
    assert summary["closing_stock"] == 115.0

    assert len(entries_date) == 2
    assert entries_date[0]["reference_code"] == "ORD-TEST-001"
    assert entries_date[0]["previous_stock"] == 150.0
    assert entries_date[0]["balance_stock"] == 130.0

    assert entries_date[1]["reference_code"] == "CK-20261010-001"
    assert entries_date[1]["previous_stock"] == 130.0
    assert entries_date[1]["balance_stock"] == 115.0
