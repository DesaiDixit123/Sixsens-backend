from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
import uuid

class PsychicApplication(BaseModel):
    """Psychic registration application"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    
    # Personal info
    full_name: str
    email: str
    phone: str
    
    # Professional info
    experience_years: int
    specialties: List[str]
    reading_methods: List[str]
    bio: str
    
    # Video interview
    interview_video_url: Optional[str] = None
    
    # Pricing
    chat_rate: float = 2.99
    phone_rate: float = 3.99
    video_rate: float = 4.99
    
    # Status
    status: str = "pending"  # pending, approved, rejected
    rejection_reason: Optional[str] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None

class PsychicDashboard(BaseModel):
    """Psychic dashboard stats"""
    total_earnings: float = 0
    pending_earnings: float = 0
    total_readings: int = 0
    average_rating: float = 0
    total_reviews: int = 0
    pending_questions: int = 0
    active_sessions: int = 0

class PsychicSettings(BaseModel):
    """Psychic profile settings"""
    psychic_id: str
    
    # Availability
    is_available: bool = True
    vacation_mode: bool = False
    vacation_end_date: Optional[datetime] = None
    
    # Profile boost
    boost_active: bool = False
    boost_expires: Optional[datetime] = None
    
    # Pricing
    chat_rate: float = 2.99
    phone_rate: float = 3.99
    video_rate: float = 4.99
    standard_video_rate: float = 12.0
    emergency_video_rate: float = 20.0
    
    # Auto-settings
    auto_accept_questions: bool = False
    notification_sound: bool = True
