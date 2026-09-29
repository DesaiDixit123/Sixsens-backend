from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

class BirthDetails(BaseModel):
    """Birth details for 3rd party reading"""
    name: str
    birth_date: str  # YYYY-MM-DD
    birth_time: Optional[str] = None  # HH:MM format
    birth_location: Optional[str] = None

class ClarificationMessage(BaseModel):
    """A single clarification message between client and psychic"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender_type: str  # 'client' or 'psychic'
    sender_id: str
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Question(BaseModel):
    """Question submitted by client to psychic"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str
    psychic_id: str
    
    # Question details
    question_text: str
    question_type: str = "recorded_video"  # recorded_video, live_chat, live_phone, live_video
    delivery_type: Optional[str] = None  # 'standard' (24h) or 'emergency' (1h) - for recorded_video
    
    # Client video question (optional - client can record video to ask their question)
    client_video_url: Optional[str] = None  # Client's video recording of their question
    
    # Birth details (for 3rd party readings)
    is_third_party: bool = False
    third_party_details: Optional[BirthDetails] = None
    
    # Pricing
    price: float = 12.0  # $12 standard, $20 emergency
    
    # Status
    status: str = "pending"  # pending, accepted, in_progress, completed, cancelled, refunded
    
    # Acceptance tracking
    accepted_at: Optional[datetime] = None  # When psychic accepted
    must_complete: bool = False  # If paid, psychic must complete when online
    
    # Clarification messages (max 5 per side)
    clarification_messages: List[ClarificationMessage] = []
    client_messages_count: int = 0  # Track messages sent by client
    psychic_messages_count: int = 0  # Track messages sent by psychic
    
    # Response (for recorded video)
    video_response_url: Optional[str] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deadline: Optional[datetime] = None  # When the answer is due
    completed_at: Optional[datetime] = None

class QuestionCreate(BaseModel):
    """Request model for creating a question"""
    psychic_id: str
    question_text: str
    question_type: str = "recorded_video"
    delivery_type: Optional[str] = "standard"  # standard or emergency
    client_video_url: Optional[str] = None  # Client's video recording
    is_third_party: bool = False
    third_party_name: Optional[str] = None
    third_party_birth_date: Optional[str] = None
    third_party_birth_time: Optional[str] = None
    third_party_birth_location: Optional[str] = None

class QuestionResponse(BaseModel):
    """Response model for question"""
    id: str
    client_id: str
    psychic_id: str
    question_text: str
    question_type: str
    delivery_type: Optional[str]
    client_video_url: Optional[str]
    is_third_party: bool
    third_party_details: Optional[BirthDetails]
    price: float
    status: str
    accepted_at: Optional[datetime]
    must_complete: bool
    clarification_messages: List[ClarificationMessage]
    client_messages_count: int
    psychic_messages_count: int
    video_response_url: Optional[str]
    created_at: datetime
    deadline: Optional[datetime]
    completed_at: Optional[datetime]

class ClarificationMessageCreate(BaseModel):
    """Request model for sending a clarification message"""
    question_id: str
    message: str
