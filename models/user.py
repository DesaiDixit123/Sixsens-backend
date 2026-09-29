from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime
import uuid

class UserBase(BaseModel):
    email: EmailStr
    name: str
    birth_date: Optional[str] = None
    zodiac_sign: Optional[str] = None

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    birth_date: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class User(UserBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile_picture: Optional[str] = None
    balance: float = 0.0
    status: str = "active"  # active, suspended, banned
    created_at: datetime = Field(default_factory=datetime.utcnow)
    saved_payment_methods: List[dict] = []
    # Free 5-min reward tracking for loyal clients ($100+ spent)
    total_spent: float = 0.0  # Total amount spent on readings
    spending_progress: float = 0.0  # Progress towards next $100 milestone (0-100)
    free_minutes_earned: int = 0  # Total free minutes earned from spending
    free_minutes_available: int = 0  # Available free minutes to use (from loyalty program)
    # First reading - 4 minutes free for new users
    is_new_user: bool = True  # True until first reading is completed
    first_reading_free_used: bool = False  # Has the 4 free minutes been used
    # Social authentication fields
    google_id: Optional[str] = None  # Google OAuth user ID
    apple_id: Optional[str] = None  # Apple Sign-In user ID
    auth_provider: Optional[str] = None  # "email", "google", or "apple"

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    birth_date: Optional[str] = None
    zodiac_sign: Optional[str] = None
    profile_picture: Optional[str] = None
    balance: float
    status: str
    # Free 5-min reward tracking for loyal clients
    total_spent: float = 0.0
    spending_progress: float = 0.0
    free_minutes_earned: int = 0
    free_minutes_available: int = 0
    # First reading - 4 minutes free for new users
    is_new_user: bool = True
    first_reading_free_used: bool = False

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
