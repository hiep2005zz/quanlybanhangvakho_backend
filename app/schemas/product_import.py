# backend/app/schemas/product_import.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class ProductBulkRowData(BaseModel):
    sku: str = ""
    name: str = ""
    unit: str = "Cái"
    sell_price: float = 0.0
    cost_price: Optional[float] = None
    category: str = "Thời trang"
    stock: int = 0

class ProductBulkRowResult(BaseModel):
    row_index: int
    sku: str = ""
    name: str = ""
    unit: str = "Cái"
    sell_price: float = 0.0
    cost_price: Optional[float] = None
    category: str = "Thời trang"
    stock: int = 0
    status: str = "NEW"  # "NEW" | "UPDATE" | "ERROR"
    errors: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)

class ProductBulkPreviewResponse(BaseModel):
    rows: List[ProductBulkRowResult]
    total_rows: int
    new_count: int
    update_count: int
    error_count: int
    can_import: bool

class ProductBulkConfirmRequest(BaseModel):
    file_id: Optional[str] = None
    skip_errors: bool = True
    rows: List[ProductBulkRowResult]

class ProductBulkConfirmResponse(BaseModel):
    total_processed: int
    created_count: int
    updated_count: int
    failed_count: int
    status: str = "success"
    message: str = ""
