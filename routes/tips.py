from fastapi import APIRouter, HTTPException
from typing import List
from datetime import datetime, timedelta
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.tip import Tip, TipCreate

GOOD_TIP_THRESHOLD = 5  # $5 or more is considered a good tip

def create_tips_routes(db):
    router = APIRouter(prefix="/tips", tags=["tips"])
    
    async def send_app_rating_prompt_to_psychic(psychic_id: str, tip_amount: float):
        """Send app rating prompt to psychic after receiving a good tip"""
        try:
            # Check if we already prompted this psychic recently (within 30 days)
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            recent_prompt = await db.notifications.find_one({
                "user_id": psychic_id,
                "type": "app_rating_prompt",
                "created_at": {"$gte": thirty_days_ago}
            })
            
            if recent_prompt:
                return None
            
            notification = {
                "id": str(uuid.uuid4()),
                "user_id": psychic_id,
                "title": "Great Tip Received! 💰",
                "body": f"You just received a ${tip_amount:.2f} tip! If you're enjoying being an advisor, please rate the app!",
                "type": "app_rating_prompt",
                "trigger": f"received_tip_{tip_amount}",
                "is_read": False,
                "created_at": datetime.utcnow()
            }
            
            await db.notifications.insert_one(notification)
            return notification
        except Exception as e:
            print(f"Error sending app rating prompt: {e}")
            return None
    
    @router.post("/")
    async def send_tip(tip: TipCreate, user_id: str = None):
        """Send a tip to a psychic"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        if tip.amount < 1:
            raise HTTPException(status_code=400, detail="Minimum tip is $1")
        
        if tip.amount > 500:
            raise HTTPException(status_code=400, detail="Maximum tip is $500")
        
        new_tip = Tip(
            user_id=user_id,
            psychic_id=tip.psychic_id,
            amount=tip.amount,
            message=tip.message,
            session_id=tip.session_id
        )
        await db.tips.insert_one(new_tip.dict())
        
        # Update psychic's earnings
        psychic_earnings = tip.amount * 0.40  # 40% to psychic
        await db.psychics.update_one(
            {"id": tip.psychic_id},
            {"$inc": {"total_earnings": psychic_earnings}}
        )
        
        # If tip is $5 or more, prompt psychic to rate the app
        if tip.amount >= GOOD_TIP_THRESHOLD:
            await send_app_rating_prompt_to_psychic(tip.psychic_id, tip.amount)
        
        return {"success": True, "tip": new_tip.dict()}
    
    @router.get("/psychic/{psychic_id}")
    async def get_psychic_tips(psychic_id: str):
        """Get all tips received by a psychic"""
        tips = await db.tips.find({"psychic_id": psychic_id}).to_list(100)
        total = sum(t.get("amount", 0) for t in tips)
        return {"tips": tips, "total": total}
    
    @router.get("/user/{user_id}")
    async def get_user_tips(user_id: str):
        """Get all tips sent by a user"""
        tips = await db.tips.find({"user_id": user_id}).to_list(100)
        return tips
    
    return router
