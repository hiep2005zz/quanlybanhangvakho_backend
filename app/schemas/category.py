from typing import Optional, List
from pydantic import BaseModel

class CategoryBase(BaseModel):
    name: str
    parent_id: Optional[int] = None
    description: Optional[str] = None

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(CategoryBase):
    pass

class CategoryResponse(CategoryBase):
    id: int
    
    class Config:
        orm_mode = True

class CategoryTreeResponse(CategoryResponse):
    sub_categories: List['CategoryTreeResponse'] = []

CategoryTreeResponse.update_forward_refs()
