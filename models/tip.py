from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import uuid

class Tip(BaseModel):
    """Tip/bonus model"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    psychic_id: str
    amount: float
    message: str = ""
    session_id: Optional[str] = None  # Optional link to a reading session
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TipCreate(BaseModel):
    psychic_id: str
    amount: float
    message: str = ""
    session_id: Optional[str] = None
