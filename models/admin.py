from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
import uuid

class UserSuspension(BaseModel):
    """User/Psychic suspension record"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    user_type: str  # 'client' or 'psychic'
    reason: str
    suspended_by: str  # Admin ID
    duration_days: int = 0  # 0 = permanent
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    lifted_at: Optional[datetime] = None
    lifted_by: Optional[str] = None

class RefundRequest(BaseModel):
    """Refund request model"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    session_id: str
    amount: float
    reason: str
    status: str = "pending"  # pending, approved, rejected
    admin_notes: Optional[str] = None
    processed_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None

class EmailCampaign(BaseModel):
    """Email marketing campaign"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    subject: str
    content: str
    target_audience: str  # 'all', 'clients', 'psychics', 'inactive'
    status: str = "draft"  # draft, scheduled, sent
    scheduled_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    total_recipients: int = 0
    opened_count: int = 0
    clicked_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AdminStats(BaseModel):
    """Admin dashboard statistics"""
    total_users: int = 0
    total_psychics: int = 0
    active_sessions: int = 0
    total_revenue: float = 0
    pending_withdrawals: int = 0
    pending_refunds: int = 0
    pending_applications: int = 0
