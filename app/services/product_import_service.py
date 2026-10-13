# backend/app/services/product_import_service.py
import io
import re
from typing import List, Dict, Any, Tuple, Optional
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.entities import ProductEntity, CategoryEntity
from app.schemas.product_import import (
    ProductBulkRowResult,
    ProductBulkPreviewResponse,
    ProductBulkConfirmRequest,
    ProductBulkConfirmResponse,
)

VALID_UNITS = {"Cái", "Chiếc", "Bộ", "Đôi", "Hộp", "Thùng", "Gói", "Kg", "Gram", "Mét", "Lít", "Chai", "Cuộn"}

class ProductBulkImportService:
    @staticmethod
    def generate_template() -> bytes:
        """
        Tạo file Excel mẫu (.xlsx) chuẩn cho nghiệp vụ nhập danh mục sản phẩm:
        - Các cột: Mã SKU, Tên sản phẩm, Đơn vị tính cơ sở, Giá bán niêm yết (VNĐ), Giá vốn nhập kho (VNĐ), Danh mục/Ngành hàng, Số lượng tồn ban đầu.
        - Định dạng header sang trọng, có dòng dữ liệu mẫu, hướng dẫn chi tiết.
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "Danh mục sản phẩm"

        # Headers chuẩn
        headers = [
            "Mã SKU (*)",
            "Tên sản phẩm (*)",
            "Đơn vị tính cơ sở (*)",
            "Giá bán niêm yết (VNĐ) (*)",
            "Giá vốn nhập kho (VNĐ)",
            "Ngành hàng / Danh mục",
            "Số lượng tồn kho ban đầu"
        ]
        ws.append(headers)

        # Dữ liệu mẫu (3 dòng chuẩn)
        sample_rows = [
            ["SP006", "Áo sơ mi Oxford Nam Dài Tay", "Cái", 320000, 150000, "Thời trang", 50],
            ["SP007", "Giày Lười Da Bò Nam", "Đôi", 750000, 420000, "Giày dép", 20],
            ["SP008", "Ví da cầm tay nam cao cấp", "Cái", 290000, 120000, "Phụ kiện", 40],
        ]
        for row in sample_rows:
            ws.append(row)

        # Định dạng giao diện Header
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid") # Deep navy
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin", color="CBD5E1"),
            right=Side(style="thin", color="CBD5E1"),
            top=Side(style="thin", color="CBD5E1"),
            bottom=Side(style="thin", color="CBD5E1")
        )

        ws.row_dimensions[1].height = 28

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align
            cell.border = thin_border

        # Định dạng các dòng dữ liệu mẫu
        for row in ws.iter_rows(min_row=2, max_row=len(sample_rows) + 1, min_col=1, max_col=len(headers)):
            ws.row_dimensions[row[0].row].height = 22
            for cell in row:
                cell.font = Font(name="Calibri", size=11)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center")
                # Format cột số
                if cell.column in [4, 5, 7]:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if cell.column in [4, 5]:
                        cell.number_format = "#,##0"
                    elif cell.column == 7:
                        cell.number_format = "#,##0"
                elif cell.column == 1:
                    cell.number_format = "@" # Cột SKU dạng text

        # Tự động căn chỉnh độ rộng cột
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 5, 18)

        # Sheet 2: Hướng dẫn nhập & đơn vị tính hợp lệ
        ws_guide = wb.create_sheet(title="Hướng dẫn & Quy chuẩn")
        guide_headers = ["Quy tắc nhập liệu", "Chi tiết", "Ví dụ"]
        ws_guide.append(guide_headers)
        ws_guide.row_dimensions[1].height = 26
        guide_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
        for col_idx in range(1, len(guide_headers) + 1):
            c = ws_guide.cell(row=1, column=col_idx)
            c.fill = guide_fill
            c.font = header_font
            c.alignment = header_align
            c.border = thin_border

        rules = [
            ["Mã SKU (*)", "Bắt buộc. Chữ & số không dấu, không chứa dấu cách, duy nhất.", "SP001, AO-NAM-01, GI-002"],
            ["Tên sản phẩm (*)", "Bắt buộc. Tên hàng hóa rõ ràng từ 2 đến 255 ký tự.", "Áo thun Polo Cotton cao cấp"],
            ["Đơn vị tính (*)", "Bắt buộc. Phải thuộc danh sách đơn vị chuẩn bên dưới.", "Cái, Chiếc, Bộ, Đôi, Hộp, Thùng, Gói, Kg, Gram, Mét, Lít, Chai, Cuộn"],
            ["Giá bán niêm yết (*)", "Bắt buộc. Số không âm (>= 0). Là giá bán cho khách hàng.", "199000"],
            ["Giá vốn nhập kho", "Không bắt buộc (mặc định 0). Là giá vốn để tính biên lợi nhuận.", "85000"],
            ["Ngành hàng / Danh mục", "Không bắt buộc (mặc định 'Thời trang'). Nếu danh mục chưa có sẽ tự động gán 'Thời trang'.", "Thời trang, Giày dép, Phụ kiện"],
            ["Số lượng tồn ban đầu", "Không bắt buộc (mặc định 0). Số nguyên >= 0.", "100"],
            ["Cơ chế Upsert", "Nếu SKU đã có trong hệ thống -> Cập nhật thông tin; Nếu SKU chưa có -> Tạo mới.", "[Tạo mới] hoặc [Cập nhật]"]
        ]
        for r in rules:
            ws_guide.append(r)

        for row in ws_guide.iter_rows(min_row=2, max_row=len(rules) + 1, min_col=1, max_col=len(guide_headers)):
            ws_guide.row_dimensions[row[0].row].height = 22
            for cell in row:
                cell.font = Font(name="Calibri", size=10.5)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center")

        for col in ws_guide.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_guide.column_dimensions[col_letter].width = max(max_len + 4, 22)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

    @staticmethod
    def parse_and_validate(file_content: bytes, db: Session) -> ProductBulkPreviewResponse:
        """
        Parse file Excel, validate từng dòng và so khớp SKU trong database.
        Hỗ trợ tối ưu hiệu năng cho tệp lên đến 5.000 dòng.
        """
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
            sheet = wb.active
        except Exception as e:
            raise ValueError(f"Không thể đọc file Excel: {str(e)}")

        # 1. Truy vấn toàn bộ SKU hiện có trong DB để so khớp O(1)
        # Sử dụng tuple/set truy vấn nhanh
        existing_products_db = db.query(ProductEntity.code).all()
        existing_skus_set = {str(p[0]).strip().upper() for p in existing_products_db if p[0]}

        # Đồng thời kiểm tra trong RAW_PRODUCTS in-memory (nếu có thêm mã)
        from app.api.v1.endpoints.products import RAW_PRODUCTS
        for p in RAW_PRODUCTS:
            if "code" in p and p["code"]:
                existing_skus_set.add(str(p["code"]).strip().upper())

        # 2. Truy vấn danh mục hợp lệ
        categories_db = db.query(CategoryEntity.id, CategoryEntity.name).all()
        category_name_to_id = {c.name.strip().lower(): c.id for c in categories_db}

        rows_result: List[ProductBulkRowResult] = []
        skus_in_file: Dict[str, int] = {}  # SKU -> dòng xuất hiện đầu tiên
        
        new_count = 0
        update_count = 0
        error_count = 0

        # Đọc từng dòng từ dòng 2
        for idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if all(v is None for v in row):
                continue  # Bỏ qua dòng trống hoàn toàn

            raw_sku = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ""
            raw_name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
            raw_unit = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
            raw_sell_price = row[3] if len(row) > 3 else None
            raw_cost_price = row[4] if len(row) > 4 else None
            raw_category = str(row[5]).strip() if len(row) > 5 and row[5] is not None else ""
            raw_stock = row[6] if len(row) > 6 else None

            errors: List[str] = []

            # 1. Validate SKU
            sku_clean = ""
            if not raw_sku:
                errors.append("Mã SKU bắt buộc không được để trống")
            else:
                sku_clean = raw_sku.upper()
                if len(sku_clean) > 50:
                    errors.append("Mã SKU không được vượt quá 50 ký tự")
                elif " " in sku_clean:
                    errors.append("Mã SKU không được chứa khoảng trắng")
                elif not re.match(r'^[A-Za-z0-9_\-\.]+$', sku_clean):
                    errors.append("Mã SKU chỉ được chứa chữ cái, số và dấu -, _, .")
                
                # Kiểm tra trùng lặp trong chính file đang import
                if sku_clean in skus_in_file:
                    first_row = skus_in_file[sku_clean]
                    errors.append(f"Mã SKU '{sku_clean}' bị trùng lặp với dòng {first_row} trong file")
                else:
                    skus_in_file[sku_clean] = idx

            # 2. Validate Tên sản phẩm
            name_clean = ""
            if not raw_name:
                errors.append("Tên sản phẩm bắt buộc không được để trống")
            else:
                name_clean = raw_name
                if len(name_clean) < 2:
                    errors.append("Tên sản phẩm tối thiểu 2 ký tự")
                elif len(name_clean) > 255:
                    errors.append("Tên sản phẩm không được vượt quá 255 ký tự")

            # 3. Validate Đơn vị tính cơ sở
            unit_clean = raw_unit if raw_unit else "Cái"
            if not raw_unit:
                errors.append("Đơn vị tính cơ sở không được để trống")
            else:
                # Chuẩn hóa viết hoa chữ cái đầu
                matched_unit = None
                for u in VALID_UNITS:
                    if u.lower() == raw_unit.lower():
                        matched_unit = u
                        break
                if matched_unit:
                    unit_clean = matched_unit
                else:
                    errors.append(f"Đơn vị tính '{raw_unit}' không hợp lệ (Chấp nhận: {', '.join(sorted(VALID_UNITS))})")

            # 4. Validate Giá bán
            sell_price_clean = 0.0
            if raw_sell_price is None or str(raw_sell_price).strip() == "":
                errors.append("Giá bán niêm yết bắt buộc không được để trống")
            else:
                try:
                    sell_price_clean = float(raw_sell_price)
                    if sell_price_clean < 0:
                        errors.append("Giá bán niêm yết không được là số âm")
                except (ValueError, TypeError):
                    errors.append("Giá bán niêm yết phải là định dạng số hợp lệ")

            # 5. Validate Giá vốn (tùy chọn)
            cost_price_clean = 0.0
            if raw_cost_price is not None and str(raw_cost_price).strip() != "":
                try:
                    cost_price_clean = float(raw_cost_price)
                    if cost_price_clean < 0:
                        errors.append("Giá vốn nhập kho không được là số âm")
                except (ValueError, TypeError):
                    errors.append("Giá vốn nhập kho phải là định dạng số hợp lệ")

            # 6. Validate Danh mục
            category_clean = raw_category if raw_category else "Thời trang"

            # 7. Validate Tồn kho (tùy chọn)
            stock_clean = 0
            if raw_stock is not None and str(raw_stock).strip() != "":
                try:
                    stock_clean = int(float(raw_stock))
                    if stock_clean < 0:
                        errors.append("Số lượng tồn kho ban đầu không được là số âm")
                except (ValueError, TypeError):
                    errors.append("Số lượng tồn kho ban đầu phải là số nguyên")

            # Xác định trạng thái dòng: ERROR | UPDATE | NEW
            if errors:
                row_status = "ERROR"
                error_count += 1
            else:
                if sku_clean in existing_skus_set:
                    row_status = "UPDATE"
                    update_count += 1
                else:
                    row_status = "NEW"
                    new_count += 1

            row_data = {
                "sku": sku_clean,
                "name": name_clean,
                "unit": unit_clean,
                "sell_price": sell_price_clean,
                "cost_price": cost_price_clean,
                "category": category_clean,
                "stock": stock_clean
            }

            rows_result.append(ProductBulkRowResult(
                row_index=idx,
                sku=sku_clean,
                name=name_clean,
                unit=unit_clean,
                sell_price=sell_price_clean,
                cost_price=cost_price_clean,
                category=category_clean,
                stock=stock_clean,
                status=row_status,
                errors=errors,
                data=row_data
            ))

        total_rows = len(rows_result)
        can_import = (total_rows > 0 and (error_count == 0 or (new_count + update_count > 0)))

        return ProductBulkPreviewResponse(
            rows=rows_result,
            total_rows=total_rows,
            new_count=new_count,
            update_count=update_count,
            error_count=error_count,
            can_import=can_import
        )

    @staticmethod
    def execute_upsert(request: ProductBulkConfirmRequest, db: Session) -> ProductBulkConfirmResponse:
        """
        Thực hiện Upsert hàng loạt vào CSDL và đồng bộ in-memory RAW_PRODUCTS theo transaction an toàn.
        Tối ưu hóa:
        - Bulk fetch tất cả ProductEntity theo SKU trong 1 query.
        - Phân loại Insert (mới) và Update (cũ).
        - Giao dịch an toàn (atomic commit / rollback).
        """
        created_count = 0
        updated_count = 0
        failed_count = 0

        # Lọc danh sách dòng cần import
        target_rows = []
        for r in request.rows:
            if r.status == "ERROR":
                if request.skip_errors:
                    failed_count += 1
                    continue
                else:
                    raise ValueError(f"Dòng {r.row_index} có lỗi: {', '.join(r.errors)}")
            target_rows.append(r)

        if not target_rows:
            return ProductBulkConfirmResponse(
                total_processed=0,
                created_count=0,
                updated_count=0,
                failed_count=failed_count,
                status="success",
                message="Không có dòng dữ liệu hợp lệ nào được chọn để import."
            )

        # 1. Fetch Categories mapping
        categories = db.query(CategoryEntity).all()
        cat_map = {c.name.strip().lower(): (c.id, c.name) for c in categories}

        # 2. Bulk fetch existing products by SKU
        skus_to_check = [r.sku for r in target_rows if r.sku]
        existing_products = db.query(ProductEntity).filter(ProductEntity.code.in_(skus_to_check)).all()
        existing_by_sku = {p.code.strip().upper(): p for p in existing_products}

        from app.api.v1.endpoints.products import RAW_PRODUCTS

        try:
            for row in target_rows:
                sku_key = row.sku.strip().upper()
                cat_info = cat_map.get(row.category.strip().lower())
                cat_id = cat_info[0] if cat_info else None
                cat_name = cat_info[1] if cat_info else (row.category.strip() or "Thời trang")

                if sku_key in existing_by_sku:
                    # UPDATE
                    prod = existing_by_sku[sku_key]
                    prod.name = row.name
                    prod.category = cat_name
                    prod.category_id = cat_id
                    prod.sell_price = row.sell_price
                    if row.cost_price is not None and row.cost_price > 0:
                        prod.cost_price = row.cost_price
                    if row.stock is not None and row.stock >= 0:
                        prod.stock = row.stock
                    updated_count += 1
                else:
                    # CREATE NEW
                    new_prod = ProductEntity(
                        code=row.sku,
                        name=row.name,
                        category=cat_name,
                        category_id=cat_id,
                        stock=row.stock or 0,
                        cost_price=row.cost_price or 0.0,
                        sell_price=row.sell_price or 0.0
                    )
                    db.add(new_prod)
                    existing_by_sku[sku_key] = new_prod
                    created_count += 1

            db.commit()

            # 3. Đồng bộ lại vào in-memory RAW_PRODUCTS
            # Lấy danh sách ID lớn nhất hiện tại trong RAW_PRODUCTS
            existing_raw_skus = {p["code"].strip().upper(): p for p in RAW_PRODUCTS if "code" in p}
            max_id = max((p["id"] for p in RAW_PRODUCTS), default=0)

            for row in target_rows:
                sku_key = row.sku.strip().upper()
                cat_info = cat_map.get(row.category.strip().lower())
                cat_id = cat_info[0] if cat_info else None
                cat_name = cat_info[1] if cat_info else (row.category.strip() or "Thời trang")

                if sku_key in existing_raw_skus:
                    raw_item = existing_raw_skus[sku_key]
                    raw_item["name"] = row.name
                    raw_item["category"] = cat_name
                    raw_item["category_id"] = cat_id
                    raw_item["sell_price"] = row.sell_price
                    if row.cost_price is not None and row.cost_price > 0:
                        raw_item["cost_price"] = row.cost_price
                    if row.stock is not None and row.stock >= 0:
                        raw_item["stock"] = row.stock
                else:
                    max_id += 1
                    new_raw_item = {
                        "id": max_id,
                        "code": row.sku,
                        "name": row.name,
                        "category": cat_name,
                        "category_id": cat_id,
                        "stock": row.stock or 0,
                        "cost_price": row.cost_price or 0.0,
                        "sell_price": row.sell_price or 0.0
                    }
                    RAW_PRODUCTS.append(new_raw_item)
                    existing_raw_skus[sku_key] = new_raw_item

            total_processed = created_count + updated_count
            return ProductBulkConfirmResponse(
                total_processed=total_processed,
                created_count=created_count,
                updated_count=updated_count,
                failed_count=failed_count,
                status="success",
                message=f"Đã nhập thành công {total_processed} sản phẩm ({created_count} tạo mới, {updated_count} cập nhật)."
            )
        except Exception as e:
            db.rollback()
            raise RuntimeError(f"Lỗi transaction lưu sản phẩm vào cơ sở dữ liệu: {str(e)}")
