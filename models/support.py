from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid

class SupportTicket(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    user_type: str  # 'client' or 'psychic'
    user_email: str
    user_name: Optional[str] = None
    subject: str
    message: str
    status: str = "open"  # open, in_progress, resolved, closed
    admin_response: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class SupportTicketCreate(BaseModel):
    user_email: str
    user_name: Optional[str] = None
    subject: str
    message: str

class SupportTicketResponse(BaseModel):
    id: str
    user_id: str
    user_type: str
    user_email: str
    user_name: Optional[str]
    subject: str
    message: str
    status: str
    admin_response: Optional[str]
    created_at: datetime
    updated_at: datetime
