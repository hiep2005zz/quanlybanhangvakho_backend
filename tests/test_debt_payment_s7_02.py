# backend/tests/test_debt_payment_s7_02.py
"""
Bộ kiểm thử tự động toàn diện cho chức năng S7-02:
'Là Kế toán công nợ, tôi muốn ghi nhận thanh toán và đối trừ vào công nợ, để biết chính xác đại lý còn nợ hoá đơn nào.'

Kiểm chứng đầy đủ 5 Tiêu chí chấp nhận:
- Tiêu chí 1: Ghi số tiền, hình thức thu, ngày thu, người thu, ghi chú.
- Tiêu chí 2: Chọn đối trừ vào hoá đơn cụ thể (MANUAL), hoặc tự động đối trừ theo nguyên tắc hoá đơn cũ nhất trước (FIFO).
- Tiêu chí 3: Cho phép thu một phần; hoá đơn chuyển sang Thanh toán một phần (PARTIALLY_PAID).
- Tiêu chí 4: Không cho thu vượt tổng công nợ còn lại (HTTP 400 kèm thông báo tiếng Việt chuẩn).
- Tiêu chí 5: Đã ghi nhận thì không xoá (chặn DELETE), chỉ lập bút toán điều chỉnh.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import DealerEntity, OrderEntity, UserEntity
from app.models.invoice import InvoiceEntity
from app.models.debt_payment import (
    PaymentReceiptEntity,
    PaymentAllocationEntity,
    PaymentAdjustmentEntity,
)
from app.models.user import USERS_DB, UserInDB, DEFAULT_HASH, save_users_db
from app.core.rbac import Role
from app.services.debt_payment_service import (
    create_payment_receipt,
    create_payment_adjustment,
    preview_fifo_allocation,
    get_dealer_debt_summary,
    get_dealer_invoices_for_allocation,
)
from app.schemas.debt_payment import (
    CreatePaymentReceiptRequest,
    AllocationItemRequest,
    CreateAdjustmentRequest,
)

client = TestClient(app)


@pytest.fixture
def accountant_token():
    """Tạo phiên đăng nhập của Kế toán công nợ."""
    USERS_DB["test_ketoan_s702"] = UserInDB(
        id=9901,
        username="test_ketoan_s702",
        full_name="Đặng Kế Toán Test",
        email="ketoan_test@company.vn",
        role=Role.ACCOUNTANT.value,
        roles=[Role.ACCOUNTANT.value],
        hashed_password=DEFAULT_HASH,
        branch="Trụ sở chính",
    )
    save_users_db()

    resp = client.post("/api/v1/auth/login", json={"username": "test_ketoan_s702", "password": "123"})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    yield token
    USERS_DB.pop("test_ketoan_s702", None)
    save_users_db()


@pytest.fixture
def sample_dealer_with_invoices():
    """Tạo đại lý mẫu và 3 hoá đơn với các mốc ngày khác nhau để kiểm thử FIFO và đối trừ."""
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    dealer = DealerEntity(
        id=8801,
        code="DL_TEST_8801",
        name="Đại Lý Kiểm Thử S7-02",
        phone="0911999888",
        email="test_s702@daily.vn",
        address="100 Phố Huế, Hà Nội",
        credit_limit=50000000.0,
        customer_group="Đại lý cấp 1",
        status="Đang hoạt động",
    )
    db.merge(dealer)
    db.commit()

    # Dọn dẹp hoá đơn cũ của dealer này nếu có
    db.query(PaymentAllocationEntity).filter(
        PaymentAllocationEntity.invoice_id.in_(
            db.query(InvoiceEntity.id).filter(InvoiceEntity.dealer_id == 8801)
        )
    ).delete(synchronize_session=False)
    db.query(PaymentAdjustmentEntity).filter(
        PaymentAdjustmentEntity.payment_id.in_(
            db.query(PaymentReceiptEntity.id).filter(PaymentReceiptEntity.dealer_id == 8801)
        )
    ).delete(synchronize_session=False)
    db.query(PaymentReceiptEntity).filter(PaymentReceiptEntity.dealer_id == 8801).delete(synchronize_session=False)
    db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == 8801).delete(synchronize_session=False)
    db.commit()

    # Tạo 3 hoá đơn:
    # 1. Cũ nhất (30 ngày trước): Tổng 3,000,000 đ, Còn nợ 3,000,000 đ
    # 2. Ở giữa (15 ngày trước): Tổng 5,000,000 đ, Còn nợ 5,000,000 đ
    # 3. Mới nhất (2 ngày trước): Tổng 2,000,000 đ, Còn nợ 2,000,000 đ
    inv1 = InvoiceEntity(
        id=88011,
        invoice_code="HD-TEST-001",
        order_code="ORD-TEST-001",
        dealer_id=8801,
        dealer_name="Đại Lý Kiểm Thử S7-02",
        issue_date=now - timedelta(days=30),
        due_date=now + timedelta(days=5),
        total_amount=3000000.0,
        paid_amount=0.0,
        remaining_debt=3000000.0,
        status="UNPAID",
    )
    inv2 = InvoiceEntity(
        id=88012,
        invoice_code="HD-TEST-002",
        order_code="ORD-TEST-002",
        dealer_id=8801,
        dealer_name="Đại Lý Kiểm Thử S7-02",
        issue_date=now - timedelta(days=15),
        due_date=now + timedelta(days=15),
        total_amount=5000000.0,
        paid_amount=0.0,
        remaining_debt=5000000.0,
        status="UNPAID",
    )
    inv3 = InvoiceEntity(
        id=88013,
        invoice_code="HD-TEST-003",
        order_code="ORD-TEST-003",
        dealer_id=8801,
        dealer_name="Đại Lý Kiểm Thử S7-02",
        issue_date=now - timedelta(days=2),
        due_date=now + timedelta(days=28),
        total_amount=2000000.0,
        paid_amount=0.0,
        remaining_debt=2000000.0,
        status="UNPAID",
    )
    db.add_all([inv1, inv2, inv3])
    db.commit()
    db.close()

    yield 8801

    # Cleanup sau test
    cleanup_db = SessionLocal()
    try:
        cleanup_db.query(PaymentAllocationEntity).filter(
            PaymentAllocationEntity.invoice_id.in_([88011, 88012, 88013])
        ).delete(synchronize_session=False)
        cleanup_db.query(PaymentAdjustmentEntity).filter(
            PaymentAdjustmentEntity.payment_id.in_(
                cleanup_db.query(PaymentReceiptEntity.id).filter(PaymentReceiptEntity.dealer_id == 8801)
            )
        ).delete(synchronize_session=False)
        cleanup_db.query(PaymentReceiptEntity).filter(PaymentReceiptEntity.dealer_id == 8801).delete(synchronize_session=False)
        cleanup_db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == 8801).delete(synchronize_session=False)
        cleanup_db.query(DealerEntity).filter(DealerEntity.id == 8801).delete(synchronize_session=False)
        cleanup_db.commit()
    finally:
        cleanup_db.close()


def test_tieu_chi_1_and_2_auto_fifo(accountant_token, sample_dealer_with_invoices):
    """
    TIÊU CHÍ 1 & 2:
    - Thu 4,500,000 đ theo chế độ AUTO_FIFO (hoá đơn cũ nhất trước).
    - Hoá đơn 1 (3,000,000 đ cũ nhất): Được trả hết 100% -> PAID, remaining = 0.
    - Hoá đơn 2 (5,000,000 đ tiếp theo): Được trả 1,500,000 đ -> PARTIALLY_PAID, remaining = 3,500,000 đ.
    - Hoá đơn 3 (2,000,000 đ mới nhất): Chưa được trả -> UNPAID, remaining = 2,000,000 đ.
    - Tiêu chí 1: Phiếu thu có đủ 5 trường: số tiền, hình thức thu, ngày thu, người thu, ghi chú, mã PT-YYYYMMDD-XXX.
    """
    dealer_id = sample_dealer_with_invoices

    # Kiểm tra xem trước FIFO (Preview)
    prev_resp = client.post(
        "/api/v1/debt-payments/preview-fifo",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json={"dealer_id": dealer_id, "amount": 4500000.0},
    )
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert len(prev_data) == 2
    assert prev_data[0]["invoice_code"] == "HD-TEST-001"
    assert prev_data[0]["allocated_amount"] == 3000000.0
    assert prev_data[0]["new_status"] == "PAID"
    assert prev_data[1]["invoice_code"] == "HD-TEST-002"
    assert prev_data[1]["allocated_amount"] == 1500000.0
    assert prev_data[1]["new_status"] == "PARTIALLY_PAID"

    # Ghi nhận thanh toán thật
    payload = {
        "dealer_id": dealer_id,
        "amount": 4500000.0,
        "payment_method": "Chuyển khoản",
        "payment_date": datetime.now(timezone.utc).isoformat(),
        "collector_name": "Đặng Kế Toán Test",
        "note": "Thu tiền đợt 1 qua ngân hàng Vietcombank",
        "allocation_mode": "AUTO_FIFO",
    }
    resp = client.post(
        "/api/v1/debt-payments",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json=payload,
    )
    assert resp.status_code == 201, f"Create receipt failed: {resp.text}"
    data = resp.json()

    # Kiểm tra Tiêu chí 1
    assert data["receipt_code"].startswith("PT-")
    assert data["amount"] == 4500000.0
    assert data["payment_method"] == "Chuyển khoản"
    assert data["collector_name"] == "Đặng Kế Toán Test"
    assert data["note"] == "Thu tiền đợt 1 qua ngân hàng Vietcombank"
    assert data["allocation_mode"] == "AUTO_FIFO"
    assert data["status"] == "CONFIRMED"

    # Kiểm tra Tiêu chí 2 & 3: Phân bổ và trạng thái hoá đơn
    allocations = data["allocations"]
    assert len(allocations) == 2
    alloc1 = next(a for a in allocations if a["invoice_id"] == 88011)
    alloc2 = next(a for a in allocations if a["invoice_id"] == 88012)
    assert alloc1["allocated_amount"] == 3000000.0
    assert alloc1["invoice_remaining"] == 0.0
    assert alloc1["invoice_status"] == "PAID"

    assert alloc2["allocated_amount"] == 1500000.0
    assert alloc2["invoice_remaining"] == 3500000.0
    assert alloc2["invoice_status"] == "PARTIALLY_PAID"

    # Kiểm tra tổng công nợ còn lại của đại lý giảm chính xác 4.5M
    summary_resp = client.get(
        f"/api/v1/debt-payments/dealers/{dealer_id}/summary",
        headers={"Authorization": f"Bearer {accountant_token}"},
    )
    assert summary_resp.status_code == 200
    s_data = summary_resp.json()
    assert s_data["total_paid"] == 4500000.0
    assert s_data["total_remaining_debt"] == (10000000.0 - 4500000.0)


def test_tieu_chi_2_and_3_manual_allocation(accountant_token, sample_dealer_with_invoices):
    """
    TIÊU CHÍ 2 (Chế độ 2 - Thủ công) & TIÊU CHÍ 3 (Thu một phần):
    - Kế toán chọn đích danh hoá đơn HD-TEST-002 và HD-TEST-003 để đối trừ thủ công.
    - Nhập: HD-TEST-002: 2,000,000 đ (một phần), HD-TEST-003: 2,000,000 đ (toàn bộ). Tổng thu = 4,000,000 đ.
    - Hoá đơn HD-TEST-001 (cũ nhất) KHÔNG bị trừ tiền.
    """
    dealer_id = sample_dealer_with_invoices

    payload = {
        "dealer_id": dealer_id,
        "amount": 4000000.0,
        "payment_method": "Tiền mặt",
        "collector_name": "Đặng Kế Toán Test",
        "note": "Thu tiền chỉ định thủ công vào HĐ 2 và HĐ 3",
        "allocation_mode": "MANUAL",
        "allocations": [
            {"invoice_id": 88012, "allocated_amount": 2000000.0},
            {"invoice_id": 88013, "allocated_amount": 2000000.0},
        ],
    }
    resp = client.post(
        "/api/v1/debt-payments",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json=payload,
    )
    assert resp.status_code == 201
    data = resp.json()

    assert data["allocation_mode"] == "MANUAL"
    assert len(data["allocations"]) == 2

    # HD 88012 được trả 2M / 5M -> PARTIALLY_PAID
    inv2_alloc = next(a for a in data["allocations"] if a["invoice_id"] == 88012)
    assert inv2_alloc["allocated_amount"] == 2000000.0
    assert inv2_alloc["invoice_status"] == "PARTIALLY_PAID"

    # HD 88013 được trả 2M / 2M -> PAID
    inv3_alloc = next(a for a in data["allocations"] if a["invoice_id"] == 88013)
    assert inv3_alloc["allocated_amount"] == 2000000.0
    assert inv3_alloc["invoice_status"] == "PAID"

    # Kiểm tra phân bổ sai tổng số tiền -> phải trả lỗi 400
    wrong_payload = {
        "dealer_id": dealer_id,
        "amount": 5000000.0,
        "allocation_mode": "MANUAL",
        "allocations": [
            {"invoice_id": 88012, "allocated_amount": 2000000.0},
        ],
    }
    wrong_resp = client.post(
        "/api/v1/debt-payments",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json=wrong_payload,
    )
    assert wrong_resp.status_code == 400


def test_tieu_chi_4_prevent_overpayment(accountant_token, sample_dealer_with_invoices):
    """
    TIÊU CHÍ 4: Không cho thu vượt tổng công nợ còn lại.
    Tổng nợ hiện tại là 10,000,000 đ.
    Nếu nhập 15,000,000 đ -> Hệ thống lập tức trả lỗi HTTP 400 và thông báo tiếng Việt chính xác:
    'Số tiền thu không được vượt quá tổng công nợ còn lại của đại lý.'
    """
    dealer_id = sample_dealer_with_invoices

    payload = {
        "dealer_id": dealer_id,
        "amount": 15000000.0,  # Vượt quá tổng nợ 10,000,000 đ
        "payment_method": "Tiền mặt",
        "allocation_mode": "AUTO_FIFO",
    }
    resp = client.post(
        "/api/v1/debt-payments",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json=payload,
    )
    assert resp.status_code == 400
    err_detail = resp.json()["detail"]
    assert "Số tiền thu không được vượt quá tổng công nợ còn lại của đại lý" in err_detail


def test_tieu_chi_5_prevent_delete_and_allow_adjustment(accountant_token, sample_dealer_with_invoices):
    """
    TIÊU CHÍ 5:
    1. Đã ghi nhận thì không xoá: Gọi lệnh DELETE phải trả về lỗi 400.
    2. Chỉ lập bút toán điều chỉnh:
       - Bắt buộc phải có 'Lý do điều chỉnh *'.
       - Hoàn lại số tiền đã thu và số còn nợ trên các hoá đơn.
       - Tự động khôi phục lại trạng thái hoá đơn (UNPAID hoặc PARTIALLY_PAID).
       - Trạng thái phiếu thu chuyển sang ADJUSTED.
    """
    dealer_id = sample_dealer_with_invoices

    # 1. Ghi nhận một phiếu thu 3,000,000 đ (trừ hết vào HĐ 1)
    create_resp = client.post(
        "/api/v1/debt-payments",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json={
            "dealer_id": dealer_id,
            "amount": 3000000.0,
            "payment_method": "Tiền mặt",
            "allocation_mode": "AUTO_FIFO",
        },
    )
    assert create_resp.status_code == 201
    receipt_id = create_resp.json()["id"]

    # 2. Thao tác DELETE bị CHẶN TUYỆT ĐỐI (HTTP 400)
    del_resp = client.delete(
        f"/api/v1/debt-payments/{receipt_id}",
        headers={"Authorization": f"Bearer {accountant_token}"},
    )
    assert del_resp.status_code == 400
    assert "Khoản thanh toán đã ghi nhận không thể xoá" in del_resp.json()["detail"]

    # 3. Lập bút toán điều chỉnh không có lý do -> Bị từ chối (Lý do điều chỉnh là bắt buộc)
    bad_adj_resp = client.post(
        f"/api/v1/debt-payments/{receipt_id}/adjust",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json={"reason": "   "},
    )
    assert bad_adj_resp.status_code in (400, 422)

    # 4. Lập bút toán điều chỉnh hợp lệ kèm lý do
    adj_resp = client.post(
        f"/api/v1/debt-payments/{receipt_id}/adjust",
        headers={"Authorization": f"Bearer {accountant_token}"},
        json={"reason": "Khách hàng chuyển nhầm tài khoản ngân hàng của chi nhánh khác, cần điều chỉnh đảo số liệu"},
    )
    assert adj_resp.status_code == 201
    adj_data = adj_resp.json()
    assert adj_data["adjustment_code"].startswith("DC-")
    assert adj_data["adjustment_amount"] == 3000000.0
    assert "Khách hàng chuyển nhầm" in adj_data["reason"]

    # 5. Kiểm tra phiếu thu đã chuyển sang trạng thái ADJUSTED
    detail_resp = client.get(
        f"/api/v1/debt-payments/{receipt_id}",
        headers={"Authorization": f"Bearer {accountant_token}"},
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "ADJUSTED"

    # 6. Kiểm tra hoá đơn HD-TEST-001 đã được khôi phục nợ và trạng thái UNPAID
    invs_resp = client.get(
        f"/api/v1/debt-payments/dealers/{dealer_id}/invoices",
        headers={"Authorization": f"Bearer {accountant_token}"},
    )
    assert invs_resp.status_code == 200
    inv1_data = next(i for i in invs_resp.json() if i["id"] == 88011)
    assert inv1_data["paid_amount"] == 0.0
    assert inv1_data["remaining_debt"] == 3000000.0
    assert inv1_data["status"] == "UNPAID"
