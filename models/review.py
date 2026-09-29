from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import uuid

class Review(BaseModel):
    """Review model with 90-day cooldown"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    psychic_id: str
    session_id: str  # Link to reading session
    rating: int  # 1-5 stars
    comment: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
class ReviewCreate(BaseModel):
    psychic_id: str
    session_id: str
    rating: int
    comment: str = ""
