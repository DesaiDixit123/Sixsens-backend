from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import uuid

class Withdrawal(BaseModel):
    """Withdrawal request model"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    psychic_id: str
    amount: float
    payment_method: str  # paypal, bank_transfer, etc.
    payment_details: str  # Email or account number (masked)
    status: str = "pending"  # pending, processing, completed, rejected
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class WithdrawalCreate(BaseModel):
    amount: float
    payment_method: str
    payment_details: str

MINIMUM_WITHDRAWAL = 50.0  # $50 minimum
