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
    profile_picture: Optional[str] = None
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
    # Payment & Bank info
    paypal_email: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    routing_number: Optional[str] = None
    password_hash: Optional[str] = None
    # Video & Interview
    video_url: Optional[str] = None
    video_duration: Optional[int] = None  # in seconds
    intro_video_url: Optional[str] = None
    intro_video_duration: Optional[int] = None
    interview_answers: Optional[List[dict]] = None
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
    profile_picture: Optional[str] = None
    years_experience: str
    specialties: List[str] = []
    love_services: List[str] = []
    bio: str
    background: str
    tools_used: List[str] = []
    tax_form_type: str = "w9"
    tax_form_completed: bool = False
    paypal_email: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    routing_number: Optional[str] = None
    password: Optional[str] = None
    video_url: Optional[str] = None
    video_duration: Optional[int] = None
    intro_video_url: Optional[str] = None
    intro_video_duration: Optional[int] = None
    interview_answers: Optional[List[dict]] = None

class ApplicationResponse(BaseModel):
    id: str
    full_name: str
    email: str
    phone: str
    country: str
    profile_picture: Optional[str] = None
    years_experience: str
    specialties: List[str]
    love_services: List[str]
    bio: str
    background: str
    tools_used: List[str]
    tax_form_type: Optional[str] = "w9"
    paypal_email: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    routing_number: Optional[str] = None
    video_url: Optional[str] = None
    video_duration: Optional[int] = None
    intro_video_url: Optional[str] = None
    intro_video_duration: Optional[int] = None
    interview_answers: Optional[List[dict]] = None
    status: str
    rejection_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime

class ApplicationStatusUpdate(BaseModel):
    status: str  # 'accepted' or 'rejected'
    rejection_reason: Optional[str] = None
