import os
import shutil
from pathlib import Path

# CÔ LẬP TOÀN DIỆN MÔI TRƯỜNG TEST:
# Pytest chạy trên database test riêng biệt, TUYỆT ĐỐI KHÔNG can thiệp quanlybanhang.db của môi trường Web dev
BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB_PATH = BACKEND_DIR / "test_quanlybanhang.db"
DEV_DB_PATH = BACKEND_DIR / "quanlybanhang.db"

if DEV_DB_PATH.exists():
    shutil.copyfile(DEV_DB_PATH, TEST_DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

import json
import pytest

import app.models.dealer
import app.models.user

TEST_DEALERS_JSON = BACKEND_DIR / "app" / "models" / "test_dealers_data.json"
DEV_DEALERS_JSON = BACKEND_DIR / "app" / "models" / "dealers_data.json"
if DEV_DEALERS_JSON.exists():
    shutil.copyfile(DEV_DEALERS_JSON, TEST_DEALERS_JSON)
app.models.dealer.DEALERS_JSON_PATH = str(TEST_DEALERS_JSON)

TEST_USERS_JSON = BACKEND_DIR / "app" / "models" / "test_users_data.json"
DEV_USERS_JSON = BACKEND_DIR / "app" / "models" / "users_data.json"
if DEV_USERS_JSON.exists():
    shutil.copyfile(DEV_USERS_JSON, TEST_USERS_JSON)
app.models.user.DB_FILE_PATH = str(TEST_USERS_JSON)

from app.db.init_db import init_db
init_db()
from app.core.database import SessionLocal
from app.models.entities import UserEntity
from app.core.security import get_password_hash
from app.models.user import load_users_db

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    init_db()

@pytest.fixture(autouse=True)
def reset_test_state():
    """Reset database and memory state before each test run."""
    db = SessionLocal()
    try:
        # Xóa các user phụ được tạo trong lúc test
        db.query(UserEntity).filter(
            UserEntity.username.in_(["sales_moi", "admin2", "multi_role_user", "kho_invalid_branch", "kho_valid_branch", "temp_user"])
        ).delete(synchronize_session=False)

        # Xóa các đại lý test phát sinh (id > 5)
        from app.models.entities import DealerEntity
        db.query(DealerEntity).filter(DealerEntity.id > 5).delete(synchronize_session=False)

        # Xóa các sản phẩm test được tạo trong lúc test
        from app.models.entities import ProductEntity
        db.query(ProductEntity).filter(
            ProductEntity.code.in_(["SP_CONFIRM_NEW_99", "SP_TEST_NEW_01", "SP_TEST_02", "SP_TEST_03", "SP_TEST_04", "SP_TRUNG_01"])
        ).delete(synchronize_session=False)
        from app.api.v1.endpoints.products import RAW_PRODUCTS
        RAW_PRODUCTS[:] = [p for p in RAW_PRODUCTS if p.get("code") not in ["SP_CONFIRM_NEW_99", "SP_TEST_NEW_01", "SP_TEST_02", "SP_TEST_03", "SP_TEST_04", "SP_TRUNG_01"]]

        # Xóa các bảng giá test phát sinh (id > 3)
        from app.models.price_book import PriceBookEntity, PriceBookItemEntity
        test_pb_ids = [pb.id for pb in db.query(PriceBookEntity).filter(PriceBookEntity.id > 3).all()]
        if test_pb_ids:
            db.query(PriceBookItemEntity).filter(PriceBookItemEntity.price_book_id.in_(test_pb_ids)).delete(synchronize_session=False)
            db.query(PriceBookEntity).filter(PriceBookEntity.id.in_(test_pb_ids)).delete(synchronize_session=False)

        db.commit()

        # Đặt lại trạng thái ACTIVE, vai trò gốc và mật khẩu chuẩn '123' cho các user hệ thống
        h = get_password_hash("123")
        system_roles_map = {
            "admin": ("admin", ["admin"], "Toàn quốc"),
            "sales_manager": ("sales_manager", ["sales_manager"], "Toàn quốc"),
            "sales": ("sales", ["sales"], "Khu vực Miền Bắc"),
            "nguyenvana": ("sales", ["sales"], "Kho Tổng Hà Nội"),
            "kho": ("warehouse", ["warehouse"], "Kho Tổng Hà Nội"),
            "warehouse_mgr": ("warehouse_manager", ["warehouse_manager"], "Kho Tổng Hà Nội"),
            "ketoan": ("accountant", ["accountant"], "Trụ sở chính"),
            "muahang": ("purchasing", ["purchasing"], "Trụ sở chính"),
        }
        existing_db_users = {u.username: u for u in db.query(UserEntity).filter(UserEntity.username.in_(list(system_roles_map.keys()))).all()}
        for uname, (primary, r_list, branch) in system_roles_map.items():
            if uname in existing_db_users:
                u = existing_db_users[uname]
                u.hashed_password = h
                u.failed_attempts = 0
                u.locked_until = None
                u.is_active = True
                u.status = "ACTIVE"
                u.lock_reason = None
                u.locked_at = None
                u.token_version = 1
                u.role = primary
                u.roles = r_list
                u.branch = branch
            else:
                new_u = UserEntity(
                    username=uname,
                    full_name=uname.capitalize(),
                    email=f"{uname}@congty.vn",
                    role=primary,
                    roles_json=json.dumps(r_list),
                    hashed_password=h,
                    branch=branch,
                    is_active=True,
                    status="ACTIVE",
                    token_version=1
                )
                db.add(new_u)
        
        from app.models.entities import AuditLogEntity, InventoryTransactionEntity
        from sqlalchemy import func
        max_audit_id = db.query(func.max(AuditLogEntity.id)).scalar() or 0
        max_inv_id = db.query(func.max(InventoryTransactionEntity.id)).scalar() or 0
        db.commit()
    finally:
        db.close()
    
    # Đồng bộ lại USERS_DB và reset failed login attempts
    load_users_db()
    from app.services.auth_service import FAILED_ATTEMPTS
    FAILED_ATTEMPTS.clear()
    from app.models.user import USERS_DB
    from app.models.dealer import DEALERS_DB, load_dealers_db
    load_dealers_db()
    for k in list(DEALERS_DB.keys()):
        if k > 5:
            del DEALERS_DB[k]
    sales_uid = USERS_DB["sales"].id if "sales" in USERS_DB else 3
    mgr_uid = USERS_DB["sales_manager"].id if "sales_manager" in USERS_DB else 2
    if 1 in DEALERS_DB: DEALERS_DB[1].assigned_sale_id = sales_uid
    if 2 in DEALERS_DB: DEALERS_DB[2].assigned_sale_id = sales_uid
    if 3 in DEALERS_DB: DEALERS_DB[3].assigned_sale_id = sales_uid
    if 4 in DEALERS_DB: DEALERS_DB[4].assigned_sale_id = mgr_uid
    from app.models.dealer import save_dealers_db
    save_dealers_db()
    from app.api.v1.endpoints.orders import ORDERS_DB
    ORDERS_DB.clear()
    original_dealer_sales = {did: d.assigned_sale_id for did, d in DEALERS_DB.items()}
    from app.models.entities import AuditLogEntity, InventoryTransactionEntity
    from sqlalchemy import func
    max_audit_id = db.query(func.max(AuditLogEntity.id)).scalar() or 0
    max_inv_id = db.query(func.max(InventoryTransactionEntity.id)).scalar() or 0
    yield

    # Dọn sạch audit logs và inventory transactions do các ca test sinh ra để không ảnh hưởng dữ liệu thật
    cleanup_db = SessionLocal()
    try:
        from app.models.entities import AuditLogEntity, InventoryTransactionEntity, ProductEntity
        from app.services.audit_service import MEMORY_AUDIT_LOGS
        from app.api.v1.endpoints.inventory import INVENTORY_TRANSACTIONS

        # Xóa các bản ghi phát sinh trong ca test
        cleanup_db.query(AuditLogEntity).filter(AuditLogEntity.id > max_audit_id).delete(synchronize_session=False)
        cleanup_db.query(InventoryTransactionEntity).filter(InventoryTransactionEntity.id > max_inv_id).delete(synchronize_session=False)

        # Khôi phục ĐVT chuẩn cho SP001 và SP002 trong DB nếu bị test đổi
        prod1 = cleanup_db.query(ProductEntity).filter(ProductEntity.id == 1).first()
        if prod1 and str(prod1.base_unit).lower() == "lon":
            prod1.base_unit = "Cái"
            prod1.units_json = json.dumps([{"unit_name": "Lốc", "conversion_rate": 6.0}, {"unit_name": "Thùng", "conversion_rate": 24.0}], ensure_ascii=False)

        prod2 = cleanup_db.query(ProductEntity).filter(ProductEntity.id == 2).first()
        if prod2 and str(prod2.base_unit).lower() == "lon":
            prod2.base_unit = "Chiếc"
            prod2.units_json = json.dumps([{"unit_name": "Kiện", "conversion_rate": 10.0}], ensure_ascii=False)

        from app.models.entities import DealerEntity
        for did, sid in original_dealer_sales.items():
            dl_ent = cleanup_db.query(DealerEntity).filter(DealerEntity.id == did).first()
            if dl_ent and dl_ent.assigned_sale_id != sid:
                dl_ent.assigned_sale_id = sid

        cleanup_db.commit()


        # Dọn sạch bộ nhớ cache in-memory
        INVENTORY_TRANSACTIONS.clear()
        test_cleanup_reasons = [
            "Kiểm kê định kỳ phát hiện dư",
            "Kiểm kê định kỳ phát hiện thừa 5 cái",
            "Tăng giá theo bảng giá quý 4",
            "Nâng hạn mức tín dụng khách hàng VIP",
            "Khách hủy hợp đồng",
            "Nhập kho từ NCC: Nhà Cung Cấp Sabeco. Nhập kho theo thùng từ nhà cung cấp (2 Thùng = 48 Lon)",
            "Xuất kho tới: Đại lý Hà Nội. Xuất mẫu thử nghiệm cho khách (1 Lốc = 6 Lon)",
            "Nhập kho từ NCC: Công ty TNHH Bia Nước Giải Khát. Nhập kho lịch sử mốc 1 (3 Thùng = 72 Lon)",
            "Admin cân đối kho",
            "Test",
            "test",
        ]
        MEMORY_AUDIT_LOGS[:] = [
            m for m in MEMORY_AUDIT_LOGS
            if m.get("reason") not in test_cleanup_reasons
            and "Sabeco" not in (m.get("reason") or "")
            and "mốc 1" not in (m.get("reason") or "")
            and "mẫu thử nghiệm" not in (m.get("reason") or "")
            and "chiết khấu đại lý tháng 10" not in (m.get("reason") or "")
            and m.get("user_name") != "Lê Thủ Kho"
        ]

        # Khôi phục ĐVT chuẩn cho SP001 và SP002 trong RAW_PRODUCTS
        from app.api.v1.endpoints.products import RAW_PRODUCTS
        for p in RAW_PRODUCTS:
            if p.get("code") == "SP001":
                p["base_unit"] = "Cái"
                p["units"] = [
                    {"unit_name": "Lốc", "conversion_rate": 6.0},
                    {"unit_name": "Thùng", "conversion_rate": 24.0},
                ]
            elif p.get("code") == "SP002":
                p["base_unit"] = "Chiếc"
                p["units"] = [
                    {"unit_name": "Kiện", "conversion_rate": 10.0},
                ]

        from app.models.dealer import DEALERS_DB, Dealer
        from app.models.entities import DealerEntity
        DEALERS_DB.clear()
        seed_dealers = {
            1: Dealer(id=1, code="DL001", name="Đại Lý Phân Phối Miền Bắc - Sao Mai", phone="0912345678", email="saomai@daily.vn", address="120 Cầu Giấy, Hà Nội", region="Hà Nội", assigned_sale_id=3, credit_limit=100000000.0, customer_group="dai_ly_cap_1", status="Đang hoạt động"),
            2: Dealer(id=2, code="DL002", name="Đại Lý Thời Trang Tân Bình", phone="0987654321", email="tanbinh@daily.vn", address="45 Lý Thường Kiệt, TP. HCM", region="TP. HCM", assigned_sale_id=3, credit_limit=50000000.0, customer_group="dai_ly_cap_2", status="Đang hoạt động"),
            3: Dealer(id=3, code="DL003", name="Đại Lý Tổng Hợp Hải Phòng", phone="0934567890", email="haiphong@daily.vn", address="88 Lạch Tray, Hải Phòng", region="Hải Phòng", assigned_sale_id=3, credit_limit=50000000.0, customer_group="Khách sỉ", status="Đang hoạt động"),
            4: Dealer(id=4, code="DL004", name="Công Ty TNHH Bán Lẻ An Phát", phone="0945678901", email="anphat@daily.vn", address="66 Nguyễn Huệ, Đà Nẵng", region="Đà Nẵng", assigned_sale_id=2, credit_limit=50000000.0, customer_group="khach_le", status="Tạm ngừng"),
            5: Dealer(id=5, code="DL005", name="Khách Mua Lẻ Trực Tiếp", phone="0911223344", email="khachle@gmail.com", address="Số 10 Tràng Thi, Hoàn Kiếm, Hà Nội", region="Hà Nội", assigned_sale_id=None, credit_limit=20000000.0, customer_group="khach_le", status="Đang hoạt động"),
        }
        for did, sd in seed_dealers.items():
            DEALERS_DB[did] = sd
            db_d = cleanup_db.query(DealerEntity).filter(DealerEntity.id == did).first()
            if db_d:
                db_d.assigned_sale_id = sd.assigned_sale_id
                db_d.status = sd.status
                db_d.customer_group = sd.customer_group
        for did in list(DEALERS_DB.keys()):
            if did > 5:
                del DEALERS_DB[did]
        cleanup_db.commit()

    except Exception:
        cleanup_db.rollback()
    finally:
        cleanup_db.close()


