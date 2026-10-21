from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

class Psychic(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    email: str
    profile_picture: str
    description: str
    about_me: str
    years_experience: int
    previous_platforms: Optional[str] = None
    specialties: List[str] = []  # Tarot Cards, Pendulum, etc.
    reading_methods: List[str] = []  # Tools they use
    topics: List[str] = []  # Love, Career, etc.
    languages: List[str] = ["English"]
    chat_rate: float = 2.99  # Per minute rate ($1.99 - $6.99)
    phone_rate: float = 4.99
    video_rate: float = 6.99
    video_call_rate: float = 6.99
    status: str = "approved"  # pending, approved, rejected, suspended, banned
    online_status: str = "offline"  # online, busy, offline
    average_rating: float = 0.0
    total_reviews: int = 0
    total_readings: int = 0
    is_featured: bool = False
    advisor_number: int = 0  # Order in which advisor joined (1-30 = founding advisors)
    offers_chat: bool = True
    offers_phone: bool = True
    offers_video: bool = True
    offers_video_call: bool = True
    offers_recorded_readings: bool = True  # Toggle for standard/emergency video questions
    balance: float = 0.0
    total_earnings: float = 0.0
    boost_enabled: bool = False
    suspension_end_date: Optional[datetime] = None  # If suspended, when it ends
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    @property
    def is_new(self) -> bool:
        """Psychic is new until they complete 300 readings (first 30 advisors exempt)"""
        if self.advisor_number > 0 and self.advisor_number <= 30:
            return False  # First 30 advisors are not subject to new rule
        return self.total_readings < 300
    
    @property
    def can_receive_recorded_questions(self) -> bool:
        """Psychic can only receive recorded questions when online and toggle is on"""
        return self.online_status == 'online' and self.offers_recorded_readings

class PsychicListResponse(BaseModel):
    id: str
    name: str
    profile_picture: str
    profile_image: Optional[str] = None
    description: str
    specialties: List[str]
    topics: List[str]
    reading_methods: List[str]
    chat_rate: float
    price_per_minute: Optional[float] = None
    phone_rate: float = 4.99
    video_rate: float = 6.99
    video_call_rate: float = 6.99
    online_status: str
    average_rating: float
    rating: Optional[float] = None
    total_reviews: int
    total_readings: int
    is_featured: bool
    is_new: bool
    advisor_number: int = 0
    offers_chat: bool = True
    offers_phone: bool = True
    offers_video: bool = True
    offers_video_call: bool = True
    offers_recorded_readings: bool = True

    can_receive_recorded_questions: bool = False
    intro_video_url: Optional[str] = None
    intro_video_duration: Optional[int] = None
    interview_answers: Optional[List[dict]] = None

    def __init__(self, **data):
        if not data.get("profile_image") and data.get("profile_picture"):
            data["profile_image"] = data["profile_picture"]
        if data.get("price_per_minute") is None and data.get("chat_rate") is not None:
            data["price_per_minute"] = data["chat_rate"]
        avg_r = data.get("average_rating")
        if not avg_r or float(avg_r) <= 0:
            data["average_rating"] = 5.0
        if data.get("rating") is None:
            data["rating"] = data.get("average_rating", 5.0)
        super().__init__(**data)


