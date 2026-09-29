from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str  # Combination of client_id and psychic_id
    sender_id: str
    sender_type: str  # 'client' or 'psychic'
    receiver_id: str
    receiver_type: str  # 'client' or 'psychic'
    content: str
    image_url: Optional[str] = None  # Base64 image data or URL
    is_read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class MessageCreate(BaseModel):
    receiver_id: str
    content: str
    image_url: Optional[str] = None  # Base64 image data or URL

class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    sender_type: str
    receiver_id: str
    receiver_type: str
    content: str
    image_url: Optional[str] = None
    is_read: bool
    created_at: datetime

class Conversation(BaseModel):
    id: str  # conversation_id
    client_id: str
    psychic_id: str
    client_name: Optional[str] = None
    psychic_name: Optional[str] = None
    psychic_avatar: Optional[str] = None
    client_avatar: Optional[str] = None
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: int = 0
    client_daily_count: int = 0
    psychic_daily_count: int = 0
    last_count_reset: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ConversationResponse(BaseModel):
    id: str
    client_id: str
    psychic_id: str
    client_name: Optional[str] = None
    psychic_name: Optional[str] = None
    psychic_avatar: Optional[str] = None
    client_avatar: Optional[str] = None
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: int = 0
    remaining_messages: int = 5
    is_new_client: bool = False  # Flag for psychics to see new clients

class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    user_type: str  # 'client' or 'psychic'
    title: str
    body: str
    notification_type: str  # 'message', 'reading', 'tip', etc.
    related_id: Optional[str] = None  # conversation_id, question_id, etc.
    is_read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class NotificationResponse(BaseModel):
    id: str
    user_id: str
    user_type: str
    title: str
    body: str
    notification_type: str
    related_id: Optional[str] = None
    is_read: bool
    created_at: datetime
