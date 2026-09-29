from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
import uuid

class ChatMessage(BaseModel):
    """Single chat message"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender_type: str  # 'client' or 'psychic'
    sender_id: str
    message: str
    message_type: str = "text"  # text, image
    image_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ChatSession(BaseModel):
    """Live chat session"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str
    psychic_id: str
    session_type: str = "chat"  # chat, phone, video
    status: str = "active"  # active, paused, ended
    rate_per_minute: float
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    total_minutes: float = 0
    total_cost: float = 0
    messages: List[ChatMessage] = []
    # Timer tracking
    warning_sent: bool = False  # 1-minute warning
    auto_end_minutes: int = 0  # Auto-end after X minutes (0 = no limit)

class ChatSessionCreate(BaseModel):
    psychic_id: str
    session_type: str = "chat"
    initial_minutes: int = 5  # Pre-purchased minutes

class AddMinutesRequest(BaseModel):
    session_id: str
    minutes: int
