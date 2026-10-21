# backend/tests/test_debt_reconciliation.py
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import DealerEntity, OrderEntity, UserEntity
from app.models.invoice import InvoiceEntity
from app.models.payment_collection import PaymentCollectionEntity
from app.models.debt_reconciliation import DebtReconciliationEntity

client = TestClient(app)


def get_token(username: str = "admin", password: str = "123") -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture(autouse=True)
def setup_reconciliation_data():
    db = SessionLocal()
    # Tìm hoặc tạo dealer test
    dealer = db.query(DealerEntity).filter(DealerEntity.code == "DL-RECON-01").first()
    if not dealer:
        dealer = DealerEntity(
            code="DL-RECON-01",
            name="Đại Lý Đối Chiếu Test",
            phone="0911223344",
            email="dealer_recon@test.com",
            address="123 Đường Test, Hà Nội",
            credit_limit=100000000.0,
            status="active",
        )
        db.add(dealer)
        db.commit()
        db.refresh(dealer)

    # Xóa dữ liệu đối chiếu cũ của dealer này
    db.query(DebtReconciliationEntity).filter(DebtReconciliationEntity.dealer_id == dealer.id).delete()
    db.query(PaymentCollectionEntity).filter(PaymentCollectionEntity.dealer_id == dealer.id).delete()
    db.query(InvoiceEntity).filter(InvoiceEntity.dealer_id == dealer.id).delete()
    db.commit()

    # Tạo 1 hóa đơn trước kỳ (tháng 9/2026) -> tạo số dư đầu kỳ
    prior_inv = InvoiceEntity(
        invoice_code="HD-PRIOR-01",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        issue_date=datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc),
        due_date=datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc),
        total_amount=5000000.0,
        paid_amount=0.0,
        remaining_debt=5000000.0,
        status="OVERDUE",
    )
    db.add(prior_inv)

    # Tạo 1 hóa đơn trong kỳ (tháng 10/2026) -> phát sinh tăng 10,000,000
    period_inv = InvoiceEntity(
        invoice_code="HD-OCT-01",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        issue_date=datetime(2026, 10, 5, 9, 30, 0, tzinfo=timezone.utc),
        due_date=datetime(2026, 11, 5, 9, 30, 0, tzinfo=timezone.utc),
        total_amount=10000000.0,
        paid_amount=0.0,
        remaining_debt=10000000.0,
        status="UNPAID",
    )
    db.add(period_inv)

    # Tìm user sales để tạo payment collection
    sales_user = db.query(UserEntity).filter(UserEntity.role == "sales").first() or db.query(UserEntity).first()

    # Tạo 1 khoản thu trong kỳ (tháng 10/2026) -> phát sinh giảm 3,000,000
    payment = PaymentCollectionEntity(
        code="PC-OCT-01",
        sales_id=sales_user.id if sales_user else 1,
        sales_name=sales_user.full_name if sales_user else "Sales Test",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        amount=3000000.0,
        payment_method="BANK_TRANSFER",
        collection_date=datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc),
        status="CONFIRMED",
    )
    db.add(payment)
    db.commit()
    yield dealer
    db.close()


def test_preview_debt_reconciliation(setup_reconciliation_data):
    dealer = setup_reconciliation_data
    token = get_token("admin")

    res = client.post(
        "/api/v1/debt-reconciliations/preview",
        json={
            "dealer_id": dealer.id,
            "from_date": "2026-10-01",
            "to_date": "2026-10-31",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["dealer_id"] == dealer.id
    assert data["opening_balance"] == 5000000.0
    assert data["total_increase"] == 10000000.0
    assert data["total_decrease"] == 3000000.0
    # Dư cuối = 5,000,000 + 10,000,000 - 3,000,000 = 12,000,000
    assert data["closing_balance"] == 12000000.0
    assert len(data["items"]) == 2


def test_create_send_and_confirm_lifecycle(setup_reconciliation_data):
    dealer = setup_reconciliation_data
    admin_token = get_token("admin")

    # 1. Kế toán tạo bản nháp (DRAFT)
    res = client.post(
        "/api/v1/debt-reconciliations",
        json={
            "dealer_id": dealer.id,
            "from_date": "2026-10-01",
            "to_date": "2026-10-31",
            "note": "Biên bản chốt tháng 10/2026",
            "send_immediately": False,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200, res.text
    rec = res.json()
    rec_id = rec["id"]
    assert rec["status"] == "DRAFT"
    assert rec["opening_balance"] == 5000000.0
    assert rec["closing_balance"] == 12000000.0

    # 2. Gửi cho đại lý (SENT)
    res_send = client.post(
        f"/api/v1/debt-reconciliations/{rec_id}/send",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_send.status_code == 200
    assert res_send.json()["status"] == "SENT"

    # 3. Đại lý xác nhận số liệu khớp (CONFIRMED)
    # Lấy token của đại lý hoặc kế toán xác nhận
    res_confirm = client.post(
        f"/api/v1/debt-reconciliations/{rec_id}/confirm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_confirm.status_code == 200
    confirmed_data = res_confirm.json()
    assert confirmed_data["status"] == "CONFIRMED"
    assert confirmed_data["confirmed_at"] is not None


def test_dealer_dispute_and_resolve(setup_reconciliation_data):
    dealer = setup_reconciliation_data
    admin_token = get_token("admin")

    # 1. Tạo và gửi ngay
    res = client.post(
        "/api/v1/debt-reconciliations",
        json={
            "dealer_id": dealer.id,
            "from_date": "2026-10-01",
            "to_date": "2026-10-31",
            "send_immediately": True,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rec_id = res.json()["id"]

    # 2. Đại lý báo chênh lệch (DISPUTED)
    res_dispute = client.post(
        f"/api/v1/debt-reconciliations/{rec_id}/dispute",
        json={
            "dealer_reported_balance": 10000000.0,
            "reason": "Đã chuyển khoản 2,000,000 ngày 25/10 nhưng chưa thấy trừ",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_dispute.status_code == 200
    disputed_data = res_dispute.json()
    assert disputed_data["status"] == "DISPUTED"
    assert disputed_data["dealer_reported_balance"] == 10000000.0

    # 3. Kế toán giải quyết khiếu nại
    res_resolve = client.post(
        f"/api/v1/debt-reconciliations/{rec_id}/resolve",
        json={
            "resolution_note": "Đã kiểm tra sao kê ngân hàng và xác nhận khoản 2,000,000",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_resolve.status_code == 200
    resolved_data = res_resolve.json()
    assert "Đã kiểm tra sao kê" in resolved_data["resolution_note"]


def test_list_and_kpi_summary(setup_reconciliation_data):
    admin_token = get_token("admin")
    res = client.get(
        "/api/v1/debt-reconciliations",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "kpi_summary" in data
    assert "total_count" in data["kpi_summary"]
