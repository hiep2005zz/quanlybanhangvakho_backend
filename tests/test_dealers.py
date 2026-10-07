import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.dealer import DEALERS_DB, save_dealers_db

client = TestClient(app)

def get_token(username: str, password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def test_dealer_search_and_filters():
    token = get_token("sales")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Tra cứu danh sách không điều kiện
    res = client.get("/api/v1/dealers/search", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1

    # Kiểm tra mỗi đại lý trả về đầy đủ các trường
    sample = data["items"][0]
    for key in ["id", "code", "name", "phone", "region", "assigned_sale_name", "customer_group", "status"]:
        assert key in sample

    # 2. Tìm kiếm nhanh theo TÊN
    res = client.get("/api/v1/dealers/search?keyword=Sao+Mai", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    assert any("Sao Mai" in it["name"] for it in items)

    # 3. Tìm kiếm nhanh theo MÃ
    res = client.get("/api/v1/dealers/search?keyword=DL002", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["code"] == "DL002"

    # 4. Tìm kiếm nhanh theo SỐ ĐIỆN THOẠI (cả dạng thường và dạng có dấu cách)
    res = client.get("/api/v1/dealers/search?keyword=0912345678", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert any("0912345678" in it["phone"] for it in items)

    res_spaced = client.get("/api/v1/dealers/search?keyword=0912+345", headers=headers)
    assert res_spaced.status_code == 200
    items_spaced = res_spaced.json()["items"]
    assert any("0912345678" in it["phone"] for it in items_spaced)

    # 5. Lọc theo KHU VỰC
    res = client.get("/api/v1/dealers/search?region=Hà+Nội", headers=headers)
    assert res.status_code == 200
    for it in res.json()["items"]:
        assert "Hà Nội" in it["region"]

    # 6. Lọc theo NHÓM KHÁCH HÀNG
    res = client.get("/api/v1/dealers/search?customer_group=Khách+sỉ", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    for it in items:
        assert it["customer_group"] == "Khách sỉ"

    # 7. Lọc theo NGƯỜI PHỤ TRÁCH (assigned_sale_id)
    res = client.get("/api/v1/dealers/search?assigned_sale_id=3", headers=headers)
    assert res.status_code == 200
    for it in res.json()["items"]:
        assert it["assigned_sale_id"] == 3

    # 8. Lọc theo TRẠNG THÁI
    res = client.get("/api/v1/dealers/search?status=Đang+hoạt+động", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    for it in items:
        assert it["status"] == "Đang hoạt động"

    # 9. Lấy dữ liệu cấu hình bộ lọc (/filters)
    res = client.get("/api/v1/dealers/filters", headers=headers)
    assert res.status_code == 200
    filters = res.json()
    assert "regions" in filters and len(filters["regions"]) >= 1
    assert "sales" in filters and len(filters["sales"]) >= 1
    assert "customer_groups" in filters and "dai_ly_cap_1" in filters["customer_groups"]
    assert "statuses" in filters and "Đang hoạt động" in filters["statuses"]

    # 10. Tạo mới đại lý qua API POST
    import time
    unique_code = f"DL-T{int(time.time())}"
    new_dealer_payload = {
        "code": unique_code,
        "name": "Đại Lý Thử Nghiệm Hà Đông",
        "phone": "0988998877",
        "email": "hadong.test@daily.vn",
        "address": "45 Quang Trung, Hà Đông, Hà Nội",
        "region": "Hà Nội",
        "customer_group": "dai_ly_cap_1",
        "status": "Đang hoạt động"
    }
    create_res = client.post("/api/v1/dealers", json=new_dealer_payload, headers=headers)
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["code"] == unique_code
    assert created["name"] == "Đại Lý Thử Nghiệm Hà Đông"
    assert created["customer_group"] == "dai_ly_cap_1"
    assert created["status"] == "Đang hoạt động"

    # Tìm lại đại lý vừa tạo bằng keyword
    verify_res = client.get(f"/api/v1/dealers/search?keyword={unique_code}", headers=headers)
    assert verify_res.status_code == 200
    assert any(it["code"] == unique_code for it in verify_res.json()["items"])

    # 10b. Chặn tạo trùng tên đại lý (case-insensitive) -> 400
    dup_name_res = client.post(
        "/api/v1/dealers",
        json={
            "code": f"DL-DNAME{int(time.time())}",
            "name": "đại lý thử nghiệm hà đông",  # Trùng tên không phân biệt hoa thường
            "phone": "0988001122",
            "address": "45 Quang Trung, Hà Đông, Hà Nội",
            "region": "Hà Nội",
        },
        headers=headers,
    )
    assert dup_name_res.status_code == 400
    assert "đã tồn tại trên hệ thống" in dup_name_res.json()["detail"]

    # 10c. Chặn tạo trùng mã đại lý -> 400
    dup_code_res = client.post(
        "/api/v1/dealers",
        json={
            "code": unique_code,
            "name": "Đại Lý Khác Không Trùng Tên",
            "phone": "0988001133",
            "address": "45 Quang Trung, Hà Đông, Hà Nội",
            "region": "Hà Nội",
        },
        headers=headers,
    )
    assert dup_code_res.status_code == 400
    assert "đã tồn tại trên hệ thống" in dup_code_res.json()["detail"]

    # 11. Kiểm tra phân quyền truy cập: role 'kho' (thủ kho) không có quyền tra cứu
    kho_token = get_token("kho")
    kho_headers = {"Authorization": f"Bearer {kho_token}"}
    res_kho = client.get("/api/v1/dealers/search", headers=kho_headers)
    assert res_kho.status_code == 403

    # 12. Kiểm thử cập nhật trạng thái đại lý (PATCH /dealers/{id}/status)
    # Tạm ngừng
    patch_pause = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Tạm ngừng", "reason": "Kiểm tra tính năng tạm ngừng"},
        headers=headers,
    )
    assert patch_pause.status_code == 200
    assert patch_pause.json()["status"] == "Tạm ngừng"

    # Kích hoạt lại
    patch_active = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Đang hoạt động", "reason": "Kích hoạt lại"},
        headers=headers,
    )
    assert patch_active.status_code == 200
    assert patch_active.json()["status"] == "Đang hoạt động"

    # Trạng thái không hợp lệ -> 400
    patch_invalid = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Trạng thái lạ"},
        headers=headers,
    )
    assert patch_invalid.status_code == 400

    # Thủ kho đổi trạng thái -> 403
    patch_kho = client.patch(
        f"/api/v1/dealers/{created['id']}/status",
        json={"status": "Tạm ngừng"},
        headers=kho_headers,
    )
    assert patch_kho.status_code == 403

    # Dọn dẹp đại lý test
    if created["id"] in DEALERS_DB:
        del DEALERS_DB[created["id"]]
        save_dealers_db()

    from app.core.database import SessionLocal
    from app.models.entities import DealerEntity
    cleanup_db = SessionLocal()
    try:
        cleanup_db.query(DealerEntity).filter(DealerEntity.id == created["id"]).delete(synchronize_session=False)
        cleanup_db.commit()
    finally:
        cleanup_db.close()


def test_accountant_lock_unlock_and_dealer_isolation():
    """
    Kiểm thử 6 tiêu chí nghiệm thu:
    1. Kế toán khóa đại lý không nhập lý do -> Bị chặn báo lỗi 400 bắt buộc nhập.
    2. Kế toán khóa đại lý kèm lý do -> Đại lý chuyển sang "Đã khóa", ghi nhận audit log, user.is_active = True.
    3. Đại lý bị khóa đăng nhập vào -> Đăng nhập thành công, thấy duy nhất thẻ của mình, status "Đã khóa", không thấy lock_reason.
    4. Đại lý tạo đơn -> Bị chặn 400 vì đại lý bị khóa.
    5. Nhân viên tạo đơn hộ cho đại lý bị khóa -> Bị chặn 400 ở Backend API.
    6. Đơn dở dang của đại lý bị khóa -> Vẫn duyệt được.
    """
    from app.models.user import USERS_DB, UserInDB, save_users_db
    from app.core.security import get_password_hash
    from app.models.entities import AuditLogEntity
    from app.core.database import SessionLocal

    # 1. Kế toán đăng nhập
    ketoan_token = get_token("ketoan")
    ketoan_headers = {"Authorization": f"Bearer {ketoan_token}"}

    # Tạo tài khoản đại lý (role customer) để test
    cust_username = "test_customer_dealer"
    USERS_DB[cust_username] = UserInDB(
        id=999,
        username=cust_username,
        full_name="Đại Lý Test Cô Lập",
        email="test_cust@daily.vn",
        role="customer",
        roles=["customer"],
        hashed_password=get_password_hash("123"),
        is_active=True,
        branch="Hà Nội",
    )
    save_users_db()
    from app.models.dealer import sync_dealer_for_user
    sync_dealer_for_user(999, "Đại Lý Test Cô Lập", "test_cust@daily.vn", "0999888777", True)

    target_dealer = DEALERS_DB.get(999)
    assert target_dealer is not None
    assert target_dealer.id == 999

    # Tiêu chí 1: Kế toán khóa đại lý không nhập lý do -> 400 Bad Request
    res_no_reason = client.patch(
        f"/api/v1/dealers/{target_dealer.id}/status",
        json={"status": "Đã khóa", "reason": "   "},
        headers=ketoan_headers,
    )
    assert res_no_reason.status_code == 400, res_no_reason.text
    assert "lý do" in res_no_reason.json()["detail"].lower()

    # Tiêu chí 2: Kế toán khóa đại lý kèm lý do -> Chuyển sang "Đã khóa", ghi audit log, user.is_active = True
    lock_reason_text = "Nợ quá hạn 45 ngày chưa thanh toán"
    res_lock = client.patch(
        f"/api/v1/dealers/{target_dealer.id}/status",
        json={"status": "Đã khóa", "reason": lock_reason_text},
        headers=ketoan_headers,
    )
    assert res_lock.status_code == 200, res_lock.text
    locked_data = res_lock.json()
    assert locked_data["status"] == "Đã khóa"
    assert locked_data["lock_reason"] == lock_reason_text
    assert locked_data["locked_by"] == "ketoan"

    # Kiểm tra tài khoản user của đại lý TUYỆT ĐỐI không bị đổi is_active = False
    cust_user = USERS_DB[cust_username]
    assert cust_user.is_active is True

    # Kiểm tra audit log
    db = SessionLocal()
    try:
        audit = db.query(AuditLogEntity).filter(
            AuditLogEntity.action_type == "DEALER_STATUS_CHANGE",
            AuditLogEntity.entity_id == target_dealer.code
        ).order_by(AuditLogEntity.id.desc()).first()
        assert audit is not None
        assert audit.user_name in ["ketoan", "Đặng Kế Toán"]
    finally:
        db.close()

    # Tiêu chí 3: Đại lý bị khóa đăng nhập -> Đăng nhập thành công, thấy duy nhất 1 thẻ của mình, status "Đã khóa", ẩn lock_reason
    cust_token = get_token(cust_username)
    assert cust_token is not None
    cust_headers = {"Authorization": f"Bearer {cust_token}"}

    res_cust_search = client.get("/api/v1/dealers/search", headers=cust_headers)
    assert res_cust_search.status_code == 200
    cust_data = res_cust_search.json()
    assert cust_data["total"] == 1
    assert len(cust_data["items"]) == 1
    my_card = cust_data["items"][0]
    assert my_card["id"] == target_dealer.id
    assert my_card["status"] == "Đã khóa"
    # Che hoàn toàn lý do nội bộ khỏi API response khi trả về cho vai trò customer!
    assert my_card.get("lock_reason") is None

    # Tiêu chí 4: Đại lý bấm tạo đơn -> Bị chặn 400 Bad Request
    res_cust_order = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": target_dealer.id,
            "items": [{"product_id": 1, "quantity": 1, "price": 100000}],
        },
        headers=cust_headers,
    )
    assert res_cust_order.status_code == 400
    assert "khóa" in res_cust_order.json()["detail"].lower()

    # Tiêu chí 5: Nhân viên bán hàng tạo đơn hộ cho đại lý bị khóa -> Bị chặn 400
    sales_token = get_token("sales")
    sales_headers = {"Authorization": f"Bearer {sales_token}"}
    res_sales_order = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": target_dealer.id,
            "items": [{"product_id": 1, "quantity": 2, "price": 100000}],
        },
        headers=sales_headers,
    )
    assert res_sales_order.status_code == 400
    assert "khóa" in res_sales_order.json()["detail"].lower()

    # Mở khóa đại lý (chuyển về Đang hoạt động)
    res_unlock = client.patch(
        f"/api/v1/dealers/{target_dealer.id}/status",
        json={"status": "Đang hoạt động", "reason": "Đã thanh toán công nợ"},
        headers=ketoan_headers,
    )
    assert res_unlock.status_code == 200
    assert res_unlock.json()["status"] == "Đang hoạt động"

    # Tạo đơn dở dang khi đã mở khóa
    res_create_ok = client.post(
        "/api/v1/orders",
        json={
            "dealer_id": target_dealer.id,
            "items": [{"product_id": 1, "quantity": 1, "price": 50000}],
        },
        headers=sales_headers,
    )
    assert res_create_ok.status_code == 201
    created_order = res_create_ok.json()

    # Sau đó khóa lại đại lý để test Tiêu chí 6
    client.patch(
        f"/api/v1/dealers/{target_dealer.id}/status",
        json={"status": "Đã khóa", "reason": "Tạm dừng xử lý tiếp"},
        headers=ketoan_headers,
    )

    # Tiêu chí 6: Đơn dở dang của đại lý bị khóa VẪN ĐƯỢC PHÉP TIẾP TỤC XỬ LÝ (Duyệt/hủy đơn)
    admin_token = get_token("admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    res_approve = client.post(
        f"/api/v1/orders/{created_order['order_code']}/approve",
        headers=admin_headers,
    )
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "CONFIRMED"

    # Dọn dẹp
    if 999 in DEALERS_DB:
        del DEALERS_DB[999]
        save_dealers_db()
    if cust_username in USERS_DB:
        del USERS_DB[cust_username]


def test_customer_lock_unlock_order_creation_restrictions():
    """Kiểm tra đại lý bị khóa không tạo được đơn, mở khóa hiển thị đúng Đang hoạt động và không tạo được đơn cho đại lý khác."""
    from datetime import date, timedelta
    from app.models.user import USERS_DB, UserInDB, save_users_db
    from app.core.security import get_password_hash
    from app.models.dealer import sync_dealer_for_user

    ketoan_token = get_token("ketoan")
    ketoan_headers = {"Authorization": f"Bearer {ketoan_token}"}

    test_user = "test_cust_lock_flow"
    USERS_DB[test_user] = UserInDB(
        id=998,
        username=test_user,
        full_name="Đại Lý Test Khóa Mở Khóa",
        email="lockflow@daily.vn",
        role="customer",
        roles=["customer"],
        hashed_password=get_password_hash("123"),
        is_active=True,
        branch="Hà Nội",
    )
    save_users_db()
    sync_dealer_for_user(998, "Đại Lý Test Khóa Mở Khóa", "lockflow@daily.vn", "0999888666", True)

    cust_token = get_token(test_user)
    cust_headers = {"Authorization": f"Bearer {cust_token}"}

    # 1. Kế toán khóa đại lý
    res_lock = client.patch(
        "/api/v1/dealers/998/status",
        json={"status": "Đã khóa", "reason": "Tạm dừng phục vụ do nợ xấu"},
        headers=ketoan_headers,
    )
    assert res_lock.status_code == 200
    assert res_lock.json()["status"] == "Đã khóa"

    # 2. Đại lý bị khóa gọi /orders/dealers -> chỉ thấy chính mình và status là Đã khóa
    res_dealers_locked = client.get("/api/v1/orders/dealers", headers=cust_headers)
    assert res_dealers_locked.status_code == 200
    dealers_data = res_dealers_locked.json()
    assert len(dealers_data) == 1
    assert dealers_data[0]["id"] == 998
    assert dealers_data[0]["status"] == "Đã khóa"

    # 3. Đại lý bị khóa cố tạo đơn qua /orders/sales-entry -> 400 Bad Request
    res_create_se = client.post(
        "/api/v1/orders/sales-entry",
        headers=cust_headers,
        json={
            "dealer_id": 998,
            "delivery_point": "123 Phố Huế, Hà Nội",
            "desired_delivery_date": (date.today() + timedelta(days=5)).isoformat(),
            "items": [{"product_id": 1, "quantity": 1, "price": 100000}],
        },
    )
    assert res_create_se.status_code == 400
    assert "khóa" in res_create_se.json()["detail"].lower()

    # 4. Đại lý bị khóa cố tạo đơn chéo cho đại lý khác (ví dụ dealer 1) -> 400 Bad Request
    res_cross = client.post(
        "/api/v1/orders/sales-entry",
        headers=cust_headers,
        json={
            "dealer_id": 1,
            "delivery_point": "123 Phố Huế, Hà Nội",
            "desired_delivery_date": (date.today() + timedelta(days=5)).isoformat(),
            "items": [{"product_id": 1, "quantity": 1, "price": 100000}],
        },
    )
    assert res_cross.status_code == 400

    # 5. Kế toán mở khóa đại lý -> Trạng thái chuyển về Đang hoạt động
    res_unlock = client.patch(
        "/api/v1/dealers/998/status",
        json={"status": "Đang hoạt động", "reason": "Đã xử lý xong"},
        headers=ketoan_headers,
    )
    assert res_unlock.status_code == 200
    assert res_unlock.json()["status"] == "Đang hoạt động"

    # 6. Đại lý kiểm tra /orders/dealers -> Trả về Đang hoạt động (không còn báo khóa)
    res_dealers_unlocked = client.get("/api/v1/orders/dealers", headers=cust_headers)
    assert res_dealers_unlocked.status_code == 200
    unlocked_data = res_dealers_unlocked.json()
    assert len(unlocked_data) == 1
    assert unlocked_data[0]["status"] == "Đang hoạt động"

    # Dọn dẹp
    if 998 in DEALERS_DB:
        del DEALERS_DB[998]
        save_dealers_db()
    if test_user in USERS_DB:
        del USERS_DB[test_user]


def test_bulk_assign_dealers_and_audit_history():
    """
    Kiểm thử chuyển giao hàng loạt đại lý (POST /api/v1/dealers/bulk-assign)
    và kiểm tra lịch sử chuyển giao (GET /api/v1/audit-logs/entity/Dealer/{code}).
    Đảm bảo:
    1. Ghi nhận đúng action_type='DEALER_ASSIGNMENT'.
    2. Chi tiết old_val và new_val phản ánh đúng nhân viên cũ và mới.
    3. Endpoint lấy lịch sử đối tượng Dealer trả về đúng bản ghi chuyển giao.
    """
    admin_token = get_token("admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    import time
    from app.models.user import USERS_DB, load_users_db
    load_users_db()

    sales_users = [u for u in USERS_DB.values() if "sales" in (u.get_roles() if hasattr(u, "get_roles") else [u.role])]
    assert len(sales_users) >= 2, "Cần ít nhất 2 sales users để kiểm thử điều chuyển"
    sales1, sales2 = sales_users[0], sales_users[1]

    ts = int(time.time())
    code1 = f"DL-BLK1-{ts}"
    code2 = f"DL-BLK2-{ts}"

    # Tạo 2 đại lý thử nghiệm do sales1 phụ trách
    dealer1_resp = client.post(
        "/api/v1/dealers",
        headers=headers,
        json={
            "code": code1,
            "name": f"Đại lý Test Bulk 1 {ts}",
            "phone": f"098{ts % 10000000:07d}",
            "address": "Hà Nội",
            "region": "Hà Nội",
            "customer_group": "Đại lý cấp 1",
            "assigned_sale_id": sales1.id,
        }
    )
    assert dealer1_resp.status_code == 201, f"Tạo d1 thất bại: {dealer1_resp.text}"
    d1 = dealer1_resp.json()

    dealer2_resp = client.post(
        "/api/v1/dealers",
        headers=headers,
        json={
            "code": code2,
            "name": f"Đại lý Test Bulk 2 {ts}",
            "phone": f"097{ts % 10000000:07d}",
            "address": "Hải Phòng",
            "region": "Hải Phòng",
            "customer_group": "Đại lý cấp 1",
            "assigned_sale_id": sales1.id,
        }
    )
    assert dealer2_resp.status_code == 201, f"Tạo d2 thất bại: {dealer2_resp.text}"
    d2 = dealer2_resp.json()

    try:
        # Thực hiện chuyển giao hàng loạt từ sales1 sang sales2
        bulk_resp = client.post(
            "/api/v1/dealers/bulk-assign",
            headers=headers,
            json={
                "dealer_ids": [d1["id"], d2["id"]],
                "new_sale_id": sales2.id,
                "reason": "Điều chuyển hàng loạt khu vực miền Bắc",
            }
        )
        assert bulk_resp.status_code == 200, f"Bulk assign thất bại: {bulk_resp.text}"
        res_data = bulk_resp.json()
        assert res_data["assigned_count"] == 2
        assert "thành công" in res_data["message"].lower()

        # Kiểm tra lịch sử của đại lý 1
        history_resp = client.get(
            f"/api/v1/audit-logs/entity/Dealer/{d1['code']}",
            headers=headers,
        )
        assert history_resp.status_code == 200, f"Lấy lịch sử thất bại: {history_resp.text}"
        logs = history_resp.json()
        assert len(logs) > 0, "Lịch sử không được rỗng sau khi chuyển giao hàng loạt"

        # Bản ghi mới nhất phải là DEALER_ASSIGNMENT
        assign_log = next((l for l in logs if l["action_type"] == "DEALER_ASSIGNMENT"), None)
        assert assign_log is not None, "Không tìm thấy log DEALER_ASSIGNMENT trong lịch sử đại lý"
        assert assign_log["reason"] == "Điều chuyển hàng loạt khu vực miền Bắc"
        assert assign_log["entity_id"] == d1["code"]

        import json
        old_val = json.loads(assign_log["old_values"]) if isinstance(assign_log["old_values"], str) else assign_log["old_values"]
        new_val = json.loads(assign_log["new_values"]) if isinstance(assign_log["new_values"], str) else assign_log["new_values"]
        assert old_val["assigned_sale_id"] == sales1.id
        assert new_val["assigned_sale_id"] == sales2.id
        assert new_val["assigned_sale_name"] == sales2.full_name

    finally:
        # Dọn dẹp đại lý test
        for did in [d1["id"], d2["id"]]:
            if did in DEALERS_DB:
                del DEALERS_DB[did]
        from app.core.database import SessionLocal
        from app.models.entities import DealerEntity
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(DealerEntity).filter(DealerEntity.id.in_([d1["id"], d2["id"]])).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()

