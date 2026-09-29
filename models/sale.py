from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid

class Sale(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str  # e.g., "Full Moon Sale", "Spring Equinox Special"
    description: str
    discount_percentage: int  # 10, 20, 30, etc.
    event_type: str  # "full_moon", "new_moon", "equinox", "solstice", "holiday", "custom"
    start_date: datetime
    end_date: datetime
    is_active: bool = False  # Admin toggles this to go live
    is_mandatory: bool = True  # All psychics must participate
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class SaleCreate(BaseModel):
    name: str
    description: str
    discount_percentage: int
    event_type: str
    start_date: datetime
    end_date: datetime
    is_mandatory: bool = True

class SaleResponse(BaseModel):
    id: str
    name: str
    description: str
    discount_percentage: int
    event_type: str
    start_date: datetime
    end_date: datetime
    is_active: bool
    is_mandatory: bool
    created_at: datetime
