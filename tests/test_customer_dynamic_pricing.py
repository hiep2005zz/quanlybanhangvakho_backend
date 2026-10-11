import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.entities import ProductEntity, DealerEntity
from app.models.price_book import PriceBookEntity, PriceBookItemEntity
from app.models.user import USERS_DB, UserInDB, DEFAULT_HASH, save_users_db
from app.core.rbac import Role

client = TestClient(app)

@pytest.fixture
def setup_dynamic_pricing_data():
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    # 1. Đảm bảo có đại lý test trong DealerEntity
    test_dealer = db.query(DealerEntity).filter(DealerEntity.id == 99).first()
    if not test_dealer:
        test_dealer = DealerEntity(
            id=99,
            code="DL099",
            name="Đại Lý Test Giá Động",
            email="dynamic_dealer@test.vn",
            phone="0988776655",
            customer_group="Dai_ly_cap_1",
            status="Đang hoạt động",
        )
        db.add(test_dealer)
    else:
        test_dealer.customer_group = "Dai_ly_cap_1"
        test_dealer.status = "Đang hoạt động"

    # 2. Đảm bảo có ít nhất 2 sản phẩm mẫu trong DB
    p1 = db.query(ProductEntity).filter(ProductEntity.id == 1).first()
    if p1:
        p1.sell_price = 200000.0  # Giá niêm yết gốc của SP 1
    
    p2 = db.query(ProductEntity).filter(ProductEntity.id == 2).first()
    if p2:
        p2.sell_price = 400000.0  # Giá niêm yết gốc của SP 2

    # 3. Tạo tài khoản User role Customer liên kết với Đại lý 99
    USERS_DB["customer_dynamic"] = UserInDB(
        id=99,
        username="customer_dynamic",
        full_name="Đại Lý Test Giá Động",
        email="dynamic_dealer@test.vn",
        phone="0988776655",
        role=Role.CUSTOMER.value,
        roles=[Role.CUSTOMER.value],
        hashed_password=DEFAULT_HASH,
        branch="Đại lý test",
    )
    save_users_db()

    # 4. Tạo Bảng giá riêng hiệu lực cho nhóm Dai_ly_cap_1
    # Chỉ chứa Product 1 với giá ưu đãi 175.000 (Product 2 KHÔNG nằm trong bảng giá này)
    pb_code = f"BG-DYN-{int(now.timestamp())}"
    price_book = PriceBookEntity(
        code=pb_code,
        name="Bảng giá Ưu đãi Đại lý Cấp 1",
        customer_group="Dai_ly_cap_1",
        valid_from=now - timedelta(days=2),
        valid_to=now + timedelta(days=15),
        status="ACTIVE",
        version=99,  # Version cao nhất để ưu tiên
        created_by="sales_manager",
    )
    db.add(price_book)
    db.flush()

    pb_item = PriceBookItemEntity(
        price_book_id=price_book.id,
        product_id=1,
        sale_price=175000.0,
        floor_price=150000.0,
    )
    db.add(pb_item)
    db.commit()

    pb_id = price_book.id
    pb_item_id = pb_item.id

    db.close()

    yield {
        "dealer_id": 99,
        "price_book_id": pb_id,
        "price_book_item_id": pb_item_id,
        "p1_original_sell_price": 200000.0,
        "p2_original_sell_price": 400000.0,
        "p1_override_price": 175000.0,
    }

    # Dọn dẹp sau test
    db_cleanup = SessionLocal()
    try:
        db_cleanup.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id == pb_id).delete()
        db_cleanup.query(PriceBookEntity).filter(PriceBookEntity.id == pb_id).delete()
        db_cleanup.commit()
    except Exception:
        db_cleanup.rollback()
    finally:
        db_cleanup.close()

    USERS_DB.pop("customer_dynamic", None)
    save_users_db()


def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_customer_dynamic_pricing_override(setup_dynamic_pricing_data):
    """
    Tiêu chí 1:
    - SP1 có trong Bảng giá -> Trả về giá bán 175.000 từ PriceListItems
    - SP2 không có trong Bảng giá -> Trả về giá niêm yết 400.000 từ bảng Products
    """
    token_customer = get_token("customer_dynamic")
    headers = {"Authorization": f"Bearer {token_customer}"}

    res = client.get("/api/v1/products", headers=headers)
    assert res.status_code == 200
    data = res.json()
    items = data["items"]

    p1 = next((p for p in items if p["id"] == 1), None)
    p2 = next((p for p in items if p["id"] == 2), None)

    assert p1 is not None, "Product 1 phải tồn tại trong response"
    assert p2 is not None, "Product 2 phải tồn tại trong response"

    # SP1 CÓ trong PriceListItems -> Giá được ghi đè bằng sale_price của bảng giá
    assert p1["sell_price"] == setup_dynamic_pricing_data["p1_override_price"]

    # SP2 KHÔNG CÓ trong PriceListItems -> Giữ nguyên giá niêm yết từ Products
    assert p2["sell_price"] == setup_dynamic_pricing_data["p2_original_sell_price"]


def test_realtime_price_update_without_modifying_products_table(setup_dynamic_pricing_data):
    """
    Tiêu chí 2 & Quy tắc nghiêm ngặt 1:
    - Quản lý kinh doanh sửa giá trong Bảng giá từ 175.000 -> 165.000.
    - Customer refresh lại API thấy ngay 165.000 (Real-time).
    - Bảng Products gốc trong CSDL TUYỆT ĐỐI không bị sửa (sell_price vẫn là 200.000).
    """
    token_customer = get_token("customer_dynamic")
    headers = {"Authorization": f"Bearer {token_customer}"}

    # 1. Admin/Sales Manager cập nhật giá trong bảng giá
    db = SessionLocal()
    try:
        item = db.query(PriceBookItemEntity).filter(
            PriceBookItemEntity.id == setup_dynamic_pricing_data["price_book_item_id"]
        ).first()
        item.sale_price = 165000.0
        db.commit()
    finally:
        db.close()

    # 2. Customer gọi lại API -> Ngay lập tức nhận giá 165.000 (Real-time)
    res = client.get("/api/v1/products", headers=headers)
    assert res.status_code == 200
    p1 = next((p for p in res.json()["items"] if p["id"] == 1), None)
    assert p1["sell_price"] == 165000.0

    # 3. KIỂM TRA BẢO TOÀN CƠ SỞ DỮ LIỆU: Bảng products gốc KHÔNG hề bị ghi đè!
    db_verify = SessionLocal()
    try:
        p1_db = db_verify.query(ProductEntity).filter(ProductEntity.id == 1).first()
        assert p1_db.sell_price == setup_dynamic_pricing_data["p1_original_sell_price"], \
            "VI PHẠM QUY TẮC: Bảng products gốc bị ghi đè giá!"
    finally:
        db_verify.close()


def test_api_role_isolation_admin_and_sales_see_listed_price(setup_dynamic_pricing_data):
    """
    Quy tắc nghiêm ngặt 2: Cô lập luồng API:
    - Customer: Thấy giá ưu đãi từ Bảng giá (175.000 hoặc 165.000).
    - Admin & Sales: Giữ nguyên câu lệnh query cũ, thấy Giá niêm yết gốc (200.000).
    """
    token_admin = get_token("admin")
    token_sales = get_token("sales")

    # Admin gọi API sản phẩm
    res_admin = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token_admin}"})
    assert res_admin.status_code == 200
    p1_admin = next((p for p in res_admin.json()["items"] if p["id"] == 1), None)
    # Admin phải thấy giá niêm yết gốc, KHÔNG bị ảnh hưởng bởi bảng giá Customer
    assert p1_admin["sell_price"] == setup_dynamic_pricing_data["p1_original_sell_price"]

    # Sales gọi API sản phẩm
    res_sales = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token_sales}"})
    assert res_sales.status_code == 200
    p1_sales = next((p for p in res_sales.json()["items"] if p["id"] == 1), None)
    assert p1_sales["sell_price"] == setup_dynamic_pricing_data["p1_original_sell_price"]
