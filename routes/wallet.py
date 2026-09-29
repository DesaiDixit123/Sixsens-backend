from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
import uuid

router = APIRouter(prefix="/wallet", tags=["wallet"])

class AddFundsRequest(BaseModel):
    amount: float
    payment_method_id: Optional[str] = None

class SavePaymentMethodRequest(BaseModel):
    type: str  # card, paypal, apple_pay, google_pay
    last_four: str
    brand: Optional[str] = None  # visa, mastercard, amex
    is_default: bool = False

class PaymentMethod(BaseModel):
    id: str
    type: str
    last_four: str
    brand: Optional[str] = None
    is_default: bool = False

class Transaction(BaseModel):
    id: str
    user_id: str
    type: str  # credit_purchase, video_reading, live_chat, tip, refund
    amount: float
    description: str
    status: str  # pending, completed, failed
    created_at: datetime

class WalletResponse(BaseModel):
    balance: float
    total_available: float
    is_new_user: bool = True
    first_reading_free_used: bool = False
    free_minutes_available: int = 0  # From loyalty program
    payment_methods: List[PaymentMethod]

def create_wallet_routes(db: AsyncIOMotorDatabase):
    @router.get("/", response_model=WalletResponse)
    async def get_wallet(token: str):
        from routes.auth import verify_token
        payload = verify_token(token)
        user = await db.users.find_one({"id": payload.get("user_id")})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get saved payment methods
        payment_methods = user.get("saved_payment_methods", [])
        
        balance = user.get("balance", 0)
        is_new_user = user.get("is_new_user", True)
        first_reading_free_used = user.get("first_reading_free_used", False)
        free_minutes_available = user.get("free_minutes_available", 0)
        
        return WalletResponse(
            balance=balance,
            total_available=balance,
            is_new_user=is_new_user,
            first_reading_free_used=first_reading_free_used,
            free_minutes_available=free_minutes_available,
            payment_methods=[PaymentMethod(**pm) for pm in payment_methods]
        )
    
    @router.post("/add-funds")
    async def add_funds(token: str, request: AddFundsRequest):
        from routes.auth import verify_token
        payload = verify_token(token)
        user = await db.users.find_one({"id": payload.get("user_id")})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if request.amount < 5:
            raise HTTPException(status_code=400, detail="Minimum amount is $5")
        
        # BE-022: Atomic wallet balance update via find_one_and_update
        from pymongo import ReturnDocument
        updated_user = await db.users.find_one_and_update(
            {"id": user["id"]},
            {"$inc": {"balance": request.amount}},
            return_document=ReturnDocument.AFTER
        )
        new_balance = updated_user.get("balance", 0) if updated_user else user.get("balance", 0) + request.amount
        
        # Create transaction record
        transaction = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "type": "credit_purchase",
            "amount": request.amount,
            "description": f"Added ${request.amount:.2f} to wallet",
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await db.transactions.insert_one(transaction)
        
        return {
            "success": True,
            "new_balance": new_balance,
            "message": f"Successfully added ${request.amount:.2f} to your wallet"
        }
    
    @router.post("/payment-methods")
    async def save_payment_method(token: str, request: SavePaymentMethodRequest):
        from routes.auth import verify_token
        payload = verify_token(token)
        user = await db.users.find_one({"id": payload.get("user_id")})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Create payment method
        pm = {
            "id": str(uuid.uuid4()),
            "type": request.type,
            "last_four": request.last_four,
            "brand": request.brand,
            "is_default": request.is_default
        }
        
        payment_methods = user.get("saved_payment_methods", [])
        
        # If this is default, remove default from others
        if request.is_default:
            for existing_pm in payment_methods:
                existing_pm["is_default"] = False
        
        payment_methods.append(pm)
        
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"saved_payment_methods": payment_methods}}
        )
        
        return {"success": True, "payment_method": pm}
    
    @router.get("/transactions", response_model=List[Transaction])
    async def get_transactions(token: str, limit: int = 20):
        from routes.auth import verify_token
        payload = verify_token(token)
        
        transactions = await db.transactions.find(
            {"user_id": payload.get("user_id")}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        return [Transaction(**t) for t in transactions]
    
    return router
