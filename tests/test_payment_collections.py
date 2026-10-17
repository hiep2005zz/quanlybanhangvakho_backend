# backend/tests/test_payment_collections.py
"""
Bộ kiểm thử Tự động Phân hệ Thu tiền theo tuyến & Đối chiếu tiền nộp về công ty.
Kiểm thử toàn diện 3 tiêu chí cốt lõi:
1. Ghi nhận khoản thu ngay tại cửa hàng kèm ảnh biên lai (COL-YYYYMMDD-XXXX).
2. Khoản thu ở trạng thái 'Chờ nộp' cho tới khi kế toán xác nhận đã nhận tiền (Phân nhiệm Four-Eyes).
3. Bảng đối chiếu tiền thu theo từng nhân viên và từng ngày (Daily Reconciliation).
"""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.entities import UserEntity, DealerEntity
from app.models.payment_collection import PaymentCollectionEntity
from app.core.security import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sales_user():
    db = SessionLocal()
    user = db.query(UserEntity).filter(UserEntity.username == "sales").first()
    db.close()
    return user


@pytest.fixture
def sales_headers(sales_user):
    token = create_access_token(sales_user.username, sales_user.role, token_version=sales_user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def accountant_user():
    db = SessionLocal()
    user = db.query(UserEntity).filter(UserEntity.username == "ketoan").first()
    db.close()
    return user


@pytest.fixture
def accountant_headers(accountant_user):
    token = create_access_token(accountant_user.username, accountant_user.role, token_version=accountant_user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_user():
    db = SessionLocal()
    user = db.query(UserEntity).filter(UserEntity.username == "admin").first()
    db.close()
    return user


@pytest.fixture
def admin_headers(admin_user):
    token = create_access_token(admin_user.username, admin_user.role, token_version=admin_user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_dealer():
    db = SessionLocal()
    dealer = db.query(DealerEntity).first()
    db.close()
    return dealer


# =============================================================================
# TIÊU CHÍ 1: GHI NHẬN KHOẢN THU TẠI CỬA HÀNG KÈM ẢNH BIÊN LAI
# =============================================================================
def test_create_payment_collection_with_receipt(client, sales_headers, sample_dealer):
    """
    Tiêu chí 1: Nhân viên kinh doanh ghi nhận khoản thu tại cửa hàng kèm ảnh biên lai.
    Mã phiếu có dạng COL-YYYYMMDD-XXXX và trạng thái ban đầu bắt buộc là 'PENDING_REMITTANCE' (Chờ nộp).
    """
    receipt_dummy = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    payload = {
        "dealer_id": sample_dealer.id,
        "amount": 2500000.0,
        "payment_method": "CASH",
        "receipt_image_url": receipt_dummy,
        "route_name": "Tuyến Ba Đình - Cầu Giấy",
        "note": "Thu tiền mặt đơn hàng đợt 1 tại cửa hàng",
    }
    res = client.post("/api/v1/payment-collections", json=payload, headers=sales_headers)
    assert res.status_code == 201, f"Lỗi tạo khoản thu: {res.text}"
    data = res.json()

    assert data["code"].startswith("COL-")
    assert data["amount"] == 2500000.0
    assert data["dealer_id"] == sample_dealer.id
    assert data["dealer_name"] == sample_dealer.name
    assert data["receipt_image_url"] == receipt_dummy
    assert data["status"] == "PENDING_REMITTANCE", "Trạng thái ban đầu bắt buộc phải là 'Chờ nộp' (PENDING_REMITTANCE)!"

    # Kiểm tra endpoint lấy chi tiết khoản thu theo ID
    detail_res = client.get(f"/api/v1/payment-collections/{data['id']}", headers=sales_headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == data["id"]
    assert detail_res.json()["code"] == data["code"]


def test_create_payment_collection_validations(client, sales_headers, sample_dealer):
    """
    Kiểm tra ràng buộc dữ liệu: Số tiền phải > 0, đại lý phải tồn tại.
    """
    # 1. Số tiền <= 0
    res_zero = client.post(
        "/api/v1/payment-collections",
        json={"dealer_id": sample_dealer.id, "amount": 0},
        headers=sales_headers
    )
    assert res_zero.status_code == 422

    # 2. Đại lý không tồn tại
    res_no_dealer = client.post(
        "/api/v1/payment-collections",
        json={"dealer_id": 999999, "amount": 500000},
        headers=sales_headers
    )
    assert res_no_dealer.status_code == 404


# =============================================================================
# TIÊU CHÍ 2: KHOẢN THU Ở TRẠNG THÁI CHỜ NỘP TỚI KHI KẾ TOÁN XÁC NHẬN
# =============================================================================
def test_payment_collection_lifecycle_pending_to_confirmed(client, sales_headers, accountant_headers, sample_dealer):
    """
    Tiêu chí 2:
    1. Tạo khoản thu -> PENDING_REMITTANCE (Chờ nộp).
    2. NVKD nộp tiền -> SUBMITTED (Đã nộp).
    3. Kế toán xác nhận nhận đủ tiền -> CONFIRMED (Đã xác nhận).
    """
    # Bước 1: Tạo khoản thu
    payload = {
        "dealer_id": sample_dealer.id,
        "amount": 1800000.0,
        "payment_method": "CASH",
        "note": "Thu đợt 2",
    }
    c_res = client.post("/api/v1/payment-collections", json=payload, headers=sales_headers)
    assert c_res.status_code == 201
    col_id = c_res.json()["id"]
    assert c_res.json()["status"] == "PENDING_REMITTANCE"

    # Bước 2: NVKD nộp tiền về công ty
    s_res = client.post(
        f"/api/v1/payment-collections/{col_id}/submit-remittance",
        json={"remittance_note": "Đã nộp quỹ tiền mặt cuối ngày"},
        headers=sales_headers
    )
    assert s_res.status_code == 200
    assert s_res.json()["status"] == "SUBMITTED"

    # Bước 3: Kế toán xác nhận đã nhận tiền
    a_res = client.post(
        f"/api/v1/payment-collections/{col_id}/confirm",
        json={"confirmation_note": "Kế toán đã đếm đủ 1.800.000 đ"},
        headers=accountant_headers
    )
    assert a_res.status_code == 200
    assert a_res.json()["status"] == "CONFIRMED"
    assert a_res.json()["confirmed_by"] is not None


def test_segregation_of_duties_sales_cannot_confirm_own_remittance(client, sales_headers, sample_dealer):
    """
    Ràng buộc phân nhiệm (Segregation of Duties):
    Nhân viên kinh doanh không được quyền tự duyệt / tự xác nhận khoản tiền mình nộp!
    """
    # Tạo khoản thu
    c_res = client.post(
        "/api/v1/payment-collections",
        json={"dealer_id": sample_dealer.id, "amount": 1000000.0},
        headers=sales_headers
    )
    col_id = c_res.json()["id"]

    # NVKD cố tình gọi API confirm -> Phải bị chặn 403 Forbidden
    conf_res = client.post(
        f"/api/v1/payment-collections/{col_id}/confirm",
        json={"confirmation_note": "Tự duyệt"},
        headers=sales_headers
    )
    assert conf_res.status_code == 403, "Nhân viên kinh doanh TUYỆT ĐỐI KHÔNG ĐƯỢC TỰ DUYỆT khoản tiền nộp!"


def test_accountant_can_reject_discrepant_remittance(client, sales_headers, accountant_headers, sample_dealer):
    """
    Kế toán phát hiện tiền lệch hoặc thiếu biên lai có quyền từ chối (REJECTED) kèm lý do.
    """
    c_res = client.post(
        "/api/v1/payment-collections",
        json={"dealer_id": sample_dealer.id, "amount": 3000000.0},
        headers=sales_headers
    )
    col_id = c_res.json()["id"]

    # NVKD nộp tiền
    client.post(f"/api/v1/payment-collections/{col_id}/submit-remittance", json={}, headers=sales_headers)

    # Kế toán từ chối vì thiếu 200k tiền mặt
    rej_res = client.post(
        f"/api/v1/payment-collections/{col_id}/reject",
        json={"rejection_reason": "Thiếu 200.000 đ tiền mặt so với biên lai và rách hóa đơn"},
        headers=accountant_headers
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["status"] == "REJECTED"
    assert "Thiếu 200.000 đ" in rej_res.json()["rejection_reason"]


# =============================================================================
# TIÊU CHÍ 3: BẢNG ĐỐI CHIẾU TIỀN THU THEO TỪNG NHÂN VIÊN VÀ TỪNG NGÀY
# =============================================================================
def test_daily_reconciliation_table(client, sales_headers, accountant_headers, admin_headers, sample_dealer):
    """
    Tiêu chí 3: Bảng đối chiếu tiền thu theo từng nhân viên và từng ngày (Daily Reconciliation).
    Tổng hợp:
    - Tổng tiền thu trong ngày
    - Tiền chờ nộp
    - Tiền đã nộp
    - Tiền đã khớp
    - Trạng thái cân bằng is_balanced
    """
    # Tạo 2 khoản thu trong ngày
    c1 = client.post(
        "/api/v1/payment-collections",
        json={"dealer_id": sample_dealer.id, "amount": 1000000.0, "note": "Khoản 1"},
        headers=sales_headers
    ).json()

    c2 = client.post(
        "/api/v1/payment-collections",
        json={"dealer_id": sample_dealer.id, "amount": 2000000.0, "note": "Khoản 2"},
        headers=sales_headers
    ).json()

    # Khoản 1 nộp và kế toán duyệt
    client.post(f"/api/v1/payment-collections/{c1['id']}/submit-remittance", json={}, headers=sales_headers)
    client.post(f"/api/v1/payment-collections/{c1['id']}/confirm", json={}, headers=accountant_headers)

    # Khoản 2 vẫn ở PENDING_REMITTANCE (Chờ nộp)

    # Gọi API Bảng đối chiếu
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    recon_res = client.get(
        f"/api/v1/payment-collections/daily-reconciliation?from_date={today_str}&to_date={today_str}",
        headers=accountant_headers
    )
    assert recon_res.status_code == 200
    data = recon_res.json()

    assert "summary" in data
    assert "items" in data
    assert data["summary"]["grand_total_collected"] >= 3000000.0
    assert data["summary"]["grand_total_confirmed"] >= 1000000.0
    assert data["summary"]["grand_total_pending_remittance"] >= 2000000.0

    # Tìm bản ghi đối chiếu của ngày hôm nay
    today_items = [it for it in data["items"] if it["date"] == today_str and it["sales_id"] == c1["sales_id"]]
    assert len(today_items) > 0
    item = today_items[0]
    assert item["total_collected"] >= 3000000.0
    assert item["total_confirmed"] >= 1000000.0
    assert item["total_pending_remittance"] >= 2000000.0
    assert item["is_balanced"] is False, "Do còn khoản 2 chờ nộp nên ngày này chưa cân bằng (is_balanced=False)!"
