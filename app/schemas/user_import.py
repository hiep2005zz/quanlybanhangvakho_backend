from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any

class BulkImportRowResult(BaseModel):
    row_index: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    branch: Optional[str] = None
    password: Optional[str] = None
    is_valid: bool = True
    errors: Dict[str, str] = {} # column name -> error message

class BulkImportPreviewResponse(BaseModel):
    rows: List[BulkImportRowResult]
    total_rows: int
    valid_count: int
    invalid_count: int

class BulkImportExecuteRequest(BaseModel):
    file_id: str  # Or we could just pass the list of valid rows back, but since we parsed it, it's safer to pass the valid rows back to the server.
    rows: List[BulkImportRowResult]

class BulkImportExecuteResponse(BaseModel):
    total_processed: int
    success_count: int
    failed_count: int
    failed_rows: List[BulkImportRowResult]
