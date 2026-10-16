# backend/tests/test_receipt_voucher.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import UserEntity
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
def setup_accountant_user(db_session: Session):
    u = db_session.query(UserEntity).filter(UserEntity.username == "test_accountant").first()
    if not u:
        u = UserEntity(
            username="test_accountant",
            email="test_accountant@ttcs.vn",
            role="accountant",
            full_name="Nguyễn Văn Kế Toán",
            hashed_password=get_password_hash("AccountantPass123!"),
            is_active=True,
            token_version=1,
        )
        db_session.add(u)
        db_session.commit()
        db_session.refresh(u)
    token = create_access_token(
        subject=u.username,
        role=u.role,
        token_version=u.token_version,
    )
    return token


def test_create_receipt_voucher_success(client: TestClient, setup_accountant_user: str):
    headers = {"Authorization": f"Bearer {setup_accountant_user}"}
    payload = {
        "amount": 18500000.0,
        "payment_method": "Tiền mặt",
        "reason": "Thu tiền thanh toán đơn hàng DH-DL001-01",
        "order_code": "DH-DL001-01",
        "payer_name": "Trần Văn Đại Lý",
        "note": "Khách đưa tiền mặt tại quầy giao dịch",
    }
    response = client.post("/api/v1/dealers/1/receipt-vouchers", json=payload, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["amount"] == 18500000.0
    assert "Mười tám triệu năm trăm nghìn đồng chẵn." in data["amount_in_words"]
    assert data["payment_method"] == "Tiền mặt"
    assert data["dealer_id"] == 1
    assert data["receipt_code"].startswith("PT-DL001-")
    assert data["collector_role"] == "Kế toán công nợ"


def test_create_receipt_voucher_invalid_amount(client: TestClient, setup_accountant_user: str):
    headers = {"Authorization": f"Bearer {setup_accountant_user}"}
    payload = {
        "amount": 0,
        "payment_method": "Tiền mặt",
        "reason": "Thu tiền 0 đồng",
    }
    response = client.post("/api/v1/dealers/1/receipt-vouchers", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Số tiền thu phải lớn hơn 0" in response.json()["detail"]


def test_get_receipt_vouchers_list(client: TestClient, setup_accountant_user: str):
    headers = {"Authorization": f"Bearer {setup_accountant_user}"}
    response = client.get("/api/v1/dealers/1/receipt-vouchers", headers=headers)
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert len(items) >= 1
