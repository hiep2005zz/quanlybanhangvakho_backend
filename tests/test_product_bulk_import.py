# backend/tests/test_product_bulk_import.py
import io
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.models.entities import ProductEntity
from app.core.database import SessionLocal

client = TestClient(app)

def get_token(username: str = "sales_manager", password: str = "123") -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]

def make_excel_file(rows: list, headers: list = None) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    if headers is None:
        headers = ["Mã SKU", "Tên sản phẩm", "Đơn vị tính", "Giá bán niêm yết", "Giá vốn", "Ngành hàng", "Số lượng tồn"]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

# ==============================================================================
# 1. TEST TẢI TEMPLATE (Acceptance Criteria 1)
# ==============================================================================
def test_download_product_import_template():
    """Kiểm tra tải file template Excel chuẩn."""
    token = get_token("sales_manager")
    res = client.get("/api/v1/products/import-template", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "spreadsheetml.sheet" in res.headers["content-type"]
    assert len(res.content) > 0

def test_download_template_unauthorized():
    """Chưa đăng nhập không được tải file template."""
    res = client.get("/api/v1/products/import-template")
    assert res.status_code == 401

# ==============================================================================
# 2. TEST BULK PREVIEW & VALIDATE (Acceptance Criteria 2 & 3)
# ==============================================================================
def test_preview_valid_file_with_new_and_update():
    """
    Kiểm tra file hợp lệ có cả SKU đã tồn tại (SP001) và SKU mới (SP_TEST_NEW_01).
    - SP001: status == 'UPDATE'
    - SP_TEST_NEW_01: status == 'NEW'
    """
    token = get_token("sales_manager")
    rows = [
        ["SP001", "Áo thun Polo Nam Cập Nhật Tên", "Cái", 220000, 90000, "Thời trang", 150],
        ["SP_TEST_NEW_01", "Áo Len Dệt Kim Thu Đông", "Chiếc", 450000, 200000, "Thời trang", 60]
    ]
    file_buf = make_excel_file(rows)
    files = {"file": ("test_import.xlsx", file_buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    res = client.post("/api/v1/products/bulk-preview", headers={"Authorization": f"Bearer {token}"}, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["total_rows"] == 2
    assert data["error_count"] == 0
    assert data["update_count"] == 1
    assert data["new_count"] == 1

    # Kiểm tra dòng 1 (SP001 đã có)
    row_sp001 = next(r for r in data["rows"] if r["sku"] == "SP001")
    assert row_sp001["status"] == "UPDATE"
    assert len(row_sp001["errors"]) == 0

    # Kiểm tra dòng 2 (SP_TEST_NEW_01 chưa có)
    row_new = next(r for r in data["rows"] if r["sku"] == "SP_TEST_NEW_01")
    assert row_new["status"] == "NEW"
    assert len(row_new["errors"]) == 0

def test_preview_file_with_missing_mandatory_fields():
    """Kiểm tra báo lỗi khi thiếu SKU, thiếu tên sản phẩm, sai định dạng giá bán."""
    token = get_token("sales_manager")
    rows = [
        ["", "Sản phẩm thiếu SKU", "Cái", 100000, 50000, "Thời trang", 10],
        ["SP_TEST_02", "", "Cái", 100000, 50000, "Thời trang", 10],
        ["SP_TEST_03", "Sản phẩm sai giá", "Cái", "chữ_không_phải_số", 50000, "Thời trang", 10],
        ["SP_TEST_04", "Sản phẩm đơn vị sai", "ĐơnVịLạHoắc", 100000, 50000, "Thời trang", 10]
    ]
    file_buf = make_excel_file(rows)
    files = {"file": ("test_errors.xlsx", file_buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    res = client.post("/api/v1/products/bulk-preview", headers={"Authorization": f"Bearer {token}"}, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["total_rows"] == 4
    assert data["error_count"] == 4
    for r in data["rows"]:
        assert r["status"] == "ERROR"
        assert len(r["errors"]) > 0

def test_preview_file_with_duplicate_skus_in_file():
    """Kiểm tra báo lỗi khi các dòng trong cùng một file bị trùng mã SKU."""
    token = get_token("sales_manager")
    rows = [
        ["SP_TRUNG_01", "Sản phẩm trùng lần 1", "Cái", 150000, 70000, "Thời trang", 10],
        ["SP_TRUNG_01", "Sản phẩm trùng lần 2", "Cái", 160000, 75000, "Thời trang", 20],
    ]
    file_buf = make_excel_file(rows)
    files = {"file": ("test_duplicate.xlsx", file_buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    res = client.post("/api/v1/products/bulk-preview", headers={"Authorization": f"Bearer {token}"}, files=files)
    assert res.status_code == 200
    data = res.json()
    # Dòng 2 phải bị đánh dấu ERROR vì trùng SKU với dòng 1
    row2 = data["rows"][1]
    assert row2["status"] == "ERROR"
    assert any("trùng lặp" in e for e in row2["errors"])

# ==============================================================================
# 3. TEST BULK CONFIRM & TRANSACTION UPSERT (Acceptance Criteria 3)
# ==============================================================================
def test_bulk_confirm_upsert_success():
    """Kiểm tra xác nhận import thành công tạo mới và cập nhật."""
    token = get_token("sales_manager")

    confirm_payload = {
        "file_id": "test_confirm.xlsx",
        "skip_errors": True,
        "rows": [
            {
                "row_index": 2,
                "sku": "SP002",  # SKU đã có -> UPDATE
                "name": "Quần Jeans Slimfit Đã Cập Nhật",
                "unit": "Cái",
                "sell_price": 410000,
                "cost_price": 180000,
                "category": "Thời trang",
                "stock": 55,
                "status": "UPDATE",
                "errors": [],
                "data": {}
            },
            {
                "row_index": 3,
                "sku": "SP_CONFIRM_NEW_99",  # SKU mới -> CREATE
                "name": "Mũ Lưỡi Trai Phong Cách",
                "unit": "Chiếc",
                "sell_price": 125000,
                "cost_price": 45000,
                "category": "Phụ kiện",
                "stock": 100,
                "status": "NEW",
                "errors": [],
                "data": {}
            },
            {
                "row_index": 4,
                "sku": "",
                "name": "Dòng lỗi bị bỏ qua",
                "status": "ERROR",
                "errors": ["Mã SKU bắt buộc"],
                "data": {}
            }
        ]
    }

    res = client.post("/api/v1/products/bulk-confirm", headers={"Authorization": f"Bearer {token}"}, json=confirm_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["total_processed"] == 2
    assert data["updated_count"] == 1
    assert data["created_count"] == 1
    assert data["failed_count"] == 1

    # Kiểm tra lại qua GET /api/v1/products
    list_res = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
    assert list_res.status_code == 200
    items = list_res.json()["items"]

    # Đảm bảo SP002 đã được cập nhật giá và tên
    p_sp002 = next(p for p in items if p["code"] == "SP002")
    assert p_sp002["name"] == "Quần Jeans Slimfit Đã Cập Nhật"
    assert p_sp002["sell_price"] == 410000

    # Đảm bảo SP_CONFIRM_NEW_99 đã có trong danh sách
    p_new = next(p for p in items if p["code"] == "SP_CONFIRM_NEW_99")
    assert p_new["name"] == "Mũ Lưỡi Trai Phong Cách"
    assert p_new["sell_price"] == 125000
    assert p_new["stock"] == 100
