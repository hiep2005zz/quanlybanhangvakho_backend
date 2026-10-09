from typing import Optional, List
from pydantic import BaseModel, ConfigDict

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
    model_config = ConfigDict(from_attributes=True)

class CategoryTreeResponse(CategoryResponse):
    sub_categories: List['CategoryTreeResponse'] = []

CategoryTreeResponse.model_rebuild()
