from pydantic import BaseModel, Field
from datetime import datetime
import uuid

class Favorite(BaseModel):
    """Favorite psychic model"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    psychic_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class FavoriteCreate(BaseModel):
    psychic_id: str
