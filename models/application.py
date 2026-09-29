from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

class PsychicApplication(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Personal Info
    full_name: str
    email: str
    phone: str
    country: str
    # Experience
    years_experience: str
    specialties: List[str] = []
    love_services: List[str] = []
    bio: str
    # Background
    background: str
    tools_used: List[str] = []
    # Tax info stored as reference (actual tax docs handled separately)
    tax_form_type: str  # 'w9' or 'w8ben'
    tax_form_completed: bool = False
    # Payment
    paypal_email: str
    # Video
    video_url: Optional[str] = None
    video_duration: Optional[int] = None  # in seconds
    # Status
    status: str = "pending"  # pending, under_review, accepted, rejected
    rejection_reason: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ApplicationCreate(BaseModel):
    full_name: str
    email: str
    phone: str
    country: str
    years_experience: str
    specialties: List[str] = []
    love_services: List[str] = []
    bio: str
    background: str
    tools_used: List[str] = []
    tax_form_type: str
    tax_form_completed: bool = False
    paypal_email: str
    video_url: Optional[str] = None
    video_duration: Optional[int] = None

class ApplicationResponse(BaseModel):
    id: str
    full_name: str
    email: str
    phone: str
    country: str
    years_experience: str
    specialties: List[str]
    love_services: List[str]
    bio: str
    background: str
    tools_used: List[str]
    tax_form_type: str
    paypal_email: str
    video_url: Optional[str]
    video_duration: Optional[int]
    status: str
    rejection_reason: Optional[str]
    reviewed_at: Optional[datetime]
    created_at: datetime

class ApplicationStatusUpdate(BaseModel):
    status: str  # 'accepted' or 'rejected'
    rejection_reason: Optional[str] = None
