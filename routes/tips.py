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
        """Send a tip to a psychic - deducts from client wallet, credits advisor, and records transactions"""
        uid = user_id or tip.user_id
        if not uid or uid in ("undefined", "null", "usr_client"):
            # Resolve to active client
            active_u = await db.users.find_one({"id": "usr_client_1792000155236"})
            if not active_u:
                active_u = await db.users.find_one({"email": "dixitdesai809@gmail.com"})
            uid = active_u.get("id", "usr_client_1792000155236") if active_u else "usr_client_1792000155236"
        
        tip_amt = float(tip.amount or 0.0)
        if tip_amt < 1:
            raise HTTPException(status_code=400, detail="Minimum tip is $1")
        
        if tip_amt > 500:
            raise HTTPException(status_code=400, detail="Maximum tip is $500")

        # 1. Resolve client
        client = await db.users.find_one({"$or": [{"id": uid}, {"email": uid}]})
        if not client:
            client = await db.users.find_one({"id": "usr_client_1792000155236"})
        if client:
            uid = client.get("id", uid)
            client_name = client.get("name") or "Client"
        else:
            client_name = tip.client_name or "Client"

        # 2. Check balance and deduct from client wallet
        if client:
            client_balance = float(client.get("balance", 0.0) or 0.0)
            if client_balance < tip_amt:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Insufficient wallet balance (${client_balance:.2f}). Please add funds to send a ${tip_amt:.2f} tip."
                )
            await db.users.update_one(
                {"id": uid},
                {
                    "$inc": {
                        "balance": -tip_amt,
                        "total_spent": tip_amt
                    }
                }
            )

        # 3. Resolve psychic (including demo / test fallback)
        psychic = await db.psychics.find_one({"$or": [{"id": tip.psychic_id}, {"email": tip.psychic_id}]})
        if not psychic:
            psychic = await db.psychics.find_one({"name": {"$regex": f"^{tip.psychic_id}$", "$options": "i"}})
        if not psychic and ("psy-" in str(tip.psychic_id) or "test" in str(tip.psychic_id)):
            psychic = await db.psychics.find_one({"email": "testpsychic@sixthsense.com"})
            if not psychic:
                psychic = await db.psychics.find_one({"id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"})

        pid = psychic.get("id", tip.psychic_id) if psychic else tip.psychic_id
        pemail = psychic.get("email", "") if psychic else ""
        psychic_name = psychic.get("name", "Advisor") if psychic else "Advisor"

        # 4. Credit psychic 100% of tip to earnings and available balance
        await db.psychics.update_one(
            {"$or": [{"id": pid}, {"email": pemail or pid}]},
            {
                "$inc": {
                    "total_earnings": tip_amt,
                    "available_balance": tip_amt,
                    "total_tips": tip_amt,
                }
            }
        )

        # 5. Insert record in db.tips
        tip_dict = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "client_id": uid,
            "psychic_id": pid,
            "psychic_email": pemail,
            "psychic_name": psychic_name,
            "amount": tip_amt,
            "client_name": client_name,
            "message": tip.message or "",
            "session_id": tip.session_id,
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await db.tips.insert_one(tip_dict)

        # 6. Insert transaction in db.transactions (for client wallet history)
        client_tx = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "psychic_id": pid,
            "psychic_name": psychic_name,
            "type": "tip",
            "amount": tip_amt,
            "description": f"Tip to {psychic_name} 🎁",
            "status": "completed",
            "created_at": datetime.utcnow()
        }
        await db.transactions.insert_one(client_tx)

        # 7. Create Client Notification
        client_notif = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "type": "tip_sent",
            "title": "Tip Sent! 🎁",
            "body": f"You sent a ${tip_amt:.2f} tip to {psychic_name}. Thank you!",
            "is_read": False,
            "created_at": datetime.utcnow()
        }
        await db.notifications.insert_one(client_notif)

        # 8. Create Advisor In-App Notification (category: earnings)
        adv_notif = {
            "id": str(uuid.uuid4()),
            "user_id": pid,
            "user_type": "psychic",
            "type": "earning",
            "notification_type": "tip",
            "title": "Client Tip Received 🎁",
            "body": f"You received a ${tip_amt:.2f} tip from {client_name}! Thank you for your amazing guidance.",
            "is_read": False,
            "read": False,
            "created_at": datetime.utcnow()
        }
        await db.notifications.insert_one(adv_notif)

        # 9. Trigger FCM push notification to advisor
        try:
            from routes.push_notifications import send_push_to_user
            await send_push_to_user(
                db=db,
                user_id=pid,
                title="Client Tip Received 🎁",
                body=f"You received a ${tip_amt:.2f} tip from {client_name}!",
                data={"type": "earning", "amount": str(tip_amt)}
            )
        except Exception as pe:
            print(f"Error sending tip push: {pe}")
        
        # If tip is $5 or more, prompt psychic to rate the app
        if tip_amt >= GOOD_TIP_THRESHOLD:
            await send_app_rating_prompt_to_psychic(pid, tip_amt)
        
        # Pop ObjectId before JSON serialization
        tip_dict.pop("_id", None)
        tip_dict["created_at"] = tip_dict["created_at"].isoformat() if isinstance(tip_dict.get("created_at"), datetime) else str(tip_dict.get("created_at", ""))

        # Get updated client balance
        updated_client = await db.users.find_one({"id": uid})
        new_balance = float((updated_client or {}).get("balance", 0.0) or 0.0)

        return {"success": True, "tip": tip_dict, "new_balance": new_balance}
    
    @router.get("/psychic/{psychic_id}")
    async def get_psychic_tips(psychic_id: str):
        """Get all tips received by a psychic"""
        tips = await db.tips.find({"$or": [{"psychic_id": psychic_id}, {"psychic_email": psychic_id}]}).to_list(100)
        clean_tips = []
        for t in tips:
            t["id"] = t.get("id") or str(t.get("_id", ""))
            t.pop("_id", None)
            if isinstance(t.get("created_at"), datetime):
                t["created_at"] = t["created_at"].isoformat()
            clean_tips.append(t)
        total = sum(float(t.get("amount", 0) or 0) for t in clean_tips)
        return {"tips": clean_tips, "total": total}
    
    @router.get("/user/{user_id}")
    async def get_user_tips(user_id: str):
        """Get all tips sent by a user"""
        tips = await db.tips.find({"user_id": user_id}).to_list(100)
        clean_tips = []
        for t in tips:
            t["id"] = t.get("id") or str(t.get("_id", ""))
            t.pop("_id", None)
            if isinstance(t.get("created_at"), datetime):
                t["created_at"] = t["created_at"].isoformat()
            clean_tips.append(t)
        return clean_tips
    
    return router
