import io
import re
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from app.api.deps import require_permission
from app.core.rbac import Permission, Role, is_warehouse_role, is_specific_warehouse
from app.core.database import get_db
from app.schemas.auth import UserResponse
from app.schemas.user_import import BulkImportPreviewResponse, BulkImportExecuteRequest, BulkImportExecuteResponse, BulkImportRowResult
from app.services.user_import_service import UserBulkImportService
from app.models.entities import UserEntity
from app.core.security import get_password_hash
from app.models.user import get_next_user_id, USERS_DB, save_users_db
import openpyxl

router = APIRouter()

@router.get("/template")
def download_template(current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))):
    """
    Tải tệp Excel mẫu để import danh sách người dùng.
    """
    content = UserBulkImportService.generate_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Mau_Nhap_Nguoi_Dung.xlsx"}
    )

@router.post("/preview", response_model=BulkImportPreviewResponse)
def preview_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Upload tệp Excel, trả về preview các dòng hợp lệ / không hợp lệ.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ tệp Excel (.xlsx, .xls)")
        
    try:
        content = file.file.read()
        return UserBulkImportService.parse_and_validate(content, db)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/execute", response_model=BulkImportExecuteResponse)
def execute_import(
    request: BulkImportExecuteRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Xác nhận import danh sách người dùng từ các dòng hợp lệ.
    Bỏ qua các dòng lỗi.
    Sử dụng transaction DB.
    """
    success_count = 0
    failed_count = 0
    failed_rows = []
    
    # Reload USERS_DB keys to double check memory store duplicates if they are not in DB
    existing_usernames = set(USERS_DB.keys())
    existing_emails = {u.email.lower() for u in USERS_DB.values() if u.email}
    
    for row in request.rows:
        if not row.is_valid:
            failed_count += 1
            failed_rows.append(row)
            continue
            
        try:
            # Re-validate
            clean_email = row.email.strip().lower()
            if clean_email in existing_emails:
                row.is_valid = False
                row.errors['email'] = "Email đã tồn tại (re-validation)."
                failed_count += 1
                failed_rows.append(row)
                continue
                
            # Tạo username
            prefix = re.sub(r'[^a-z0-9]', '', clean_email.split('@')[0])
            if not prefix:
                prefix = "user"
            clean_username = prefix
            counter = 1
            while clean_username in existing_usernames:
                clean_username = f"{prefix}{counter}"
                counter += 1
                
            # Hash password
            password_hash = get_password_hash(row.password)
            
            # Additional Check for role and branch
            roles = [row.role]
            if is_warehouse_role(roles) and not is_specific_warehouse(row.branch):
                row.is_valid = False
                row.errors['branch'] = "Vai trò Kho yêu cầu gắn với kho cụ thể."
                failed_count += 1
                failed_rows.append(row)
                continue
            
            new_id = get_next_user_id()
            
            # DB insert
            new_user = UserEntity(
                id=new_id,
                username=clean_username,
                full_name=row.full_name,
                email=clean_email,
                phone=row.phone,
                role=row.role,
                roles_json=json.dumps(roles),
                hashed_password=password_hash,
                branch=row.branch,
                is_active=True,
                status="ACTIVE",
                token_version=1
            )
            db.add(new_user)
            db.flush() # flush each to catch errors early
            
            # Update memory DB
            from app.models.user import UserInDB
            USERS_DB[clean_username] = UserInDB(
                id=new_id,
                username=clean_username,
                full_name=row.full_name,
                email=clean_email,
                phone=row.phone,
                role=row.role,
                roles=roles,
                hashed_password=password_hash,
                branch=row.branch,
                is_active=True,
                status="ACTIVE",
            )
            existing_usernames.add(clean_username)
            existing_emails.add(clean_email)
            
            success_count += 1
        except Exception as e:
            db.rollback()
            row.is_valid = False
            row.errors['general'] = str(e)
            failed_count += 1
            failed_rows.append(row)
    
    try:
        db.commit()
        save_users_db()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Lỗi khi lưu vào CSDL: {str(e)}")
        
    return BulkImportExecuteResponse(
        total_processed=len(request.rows),
        success_count=success_count,
        failed_count=failed_count,
        failed_rows=failed_rows
    )

@router.post("/export-errors")
def export_errors(
    request: BulkImportExecuteRequest,
    current_user: UserResponse = Depends(require_permission(Permission.USER_MANAGE.value))
):
    """
    Xuất danh sách các dòng lỗi ra file Excel để người dùng sửa.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Errors"
    
    headers = ["Dòng", "Họ và tên", "Email", "Số điện thoại", "Vai trò", "Chi nhánh / Địa bàn", "Mật khẩu", "Lý do lỗi"]
    ws.append(headers)
    
    role_vi_export_map = {
        "admin": "Quản trị hệ thống",
        "sales_manager": "Quản lý kinh doanh",
        "sales": "Nhân viên kinh doanh",
        "warehouse": "Thủ kho",
        "warehouse_manager": "Quản lý kho",
        "accountant": "Kế toán",
        "purchasing": "Nhân viên mua hàng",
        "customer": "Đại lý"
    }

    for row in request.rows:
        error_msgs = ", ".join(f"{k}: {v}" for k, v in row.errors.items())
        role_display = role_vi_export_map.get(row.role, row.role)
        ws.append([
            row.row_index,
            row.full_name,
            row.email,
            row.phone,
            role_display,
            row.branch,
            row.password,
            error_msgs
        ])
        
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Import_Errors.xlsx"}
    )
