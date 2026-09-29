from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List
from datetime import datetime
import uuid
import sys
import os
import httpx
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.chat import ChatSession, ChatSessionCreate, ChatMessage, AddMinutesRequest

# Resend email configuration
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "onboarding@resend.dev")
FROM_NAME = os.getenv("FROM_NAME", "Sixth Sense Psychics")

def sanitize_chat_content(text: str) -> tuple:
    """BE-076: AI regex content filter intercepting phone numbers, WhatsApp, and off-platform payment attempts"""
    import re
    if not text:
        return text, False
    
    flagged = False
    # Patterns for phone numbers, WhatsApp, Telegram, CashApp, Venmo, PayPal
    patterns = [
        r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',  # Phone numbers
        r'\b(?:whatsapp|wa\.me|telegram|t\.me|cashapp|\$cashapp|venmo|paypal|zelle)\b',  # Platform names
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'  # Email addresses
    ]
    
    sanitized_text = text
    for pattern in patterns:
        if re.search(pattern, sanitized_text, re.IGNORECASE):
            sanitized_text = re.sub(pattern, "[Blocked Contact Info]", sanitized_text, flags=re.IGNORECASE)
            flagged = True
            
    return sanitized_text, flagged

async def send_session_receipt_email_async(to_email: str, user_name: str, psychic_name: str, session_type: str, duration_minutes: float, amount_charged: float, session_date: str):
    """Send session receipt email via Resend in background"""
    if not RESEND_API_KEY:
        print(f"[MOCK EMAIL] Session receipt email to {to_email}")
        return
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .logo {{ font-size: 24px; color: #6B4EAA; font-weight: bold; }}
            .receipt-box {{ background: #f9f9f9; border-radius: 10px; padding: 20px; margin: 20px 0; }}
            .row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #eee; }}
            .total {{ font-size: 18px; font-weight: bold; color: #6B4EAA; }}
            .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">✨ Sixth Sense Psychics</div>
                <h2>Session Receipt</h2>
            </div>
            <p>Hi {user_name},</p>
            <p>Thank you for your reading session. Here are your session details:</p>
            <div class="receipt-box">
                <div class="row"><span>Advisor:</span><span>{psychic_name}</span></div>
                <div class="row"><span>Session Type:</span><span>{session_type.title()}</span></div>
                <div class="row"><span>Duration:</span><span>{duration_minutes:.1f} minutes</span></div>
                <div class="row"><span>Date:</span><span>{session_date}</span></div>
                <div class="row total"><span>Amount Charged:</span><span>${amount_charged:.2f}</span></div>
            </div>
            <p>We hope you found your reading insightful. Feel free to leave a review for your advisor!</p>
            <div class="footer">
                <p>© 2024 Sixth Sense Psychics. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                    "to": [to_email],
                    "subject": f"Your Reading with {psychic_name} - Receipt",
                    "html": html_content
                }
            )
            if response.status_code == 200:
                print(f"✅ Session receipt email sent to {to_email}")
            else:
                print(f"❌ Failed to send session receipt email: {response.text}")
    except Exception as e:
        print(f"❌ Error sending session receipt email: {e}")

def create_chat_routes(db):
    router = APIRouter(prefix="/chat", tags=["chat"])
    
    # Helper function to send push notification to psychic
    async def notify_psychic_of_request(psychic_id: str, psychic_email: str, client_name: str, session_type: str):
        """Send push notification to psychic about incoming request"""
        try:
            # Find psychic's push tokens
            tokens = await db.device_tokens.find({
                "$or": [
                    {"user_id": psychic_id},
                    {"user_id": psychic_email}
                ],
                "is_active": True
            }).to_list(10)
            
            if not tokens:
                print(f"[PUSH] No tokens found for psychic {psychic_id}")
                return
            
            # Send notification via FCM or Emergent push
            for token_doc in tokens:
                try:
                    # Log the notification attempt
                    print(f"[PUSH] Sending to psychic {psychic_id}: {client_name} wants {session_type}")
                    
                    # Store notification in DB for the advisor app to poll
                    notification = {
                        "id": str(uuid.uuid4()),
                        "user_id": psychic_id,
                        "title": "New Client Request! 🌟",
                        "body": f"{client_name} wants to start a {session_type} session with you.",
                        "data": {
                            "type": "session_request",
                            "session_type": session_type,
                            "client_name": client_name
                        },
                        "created_at": datetime.utcnow(),
                        "read": False
                    }
                    await db.notifications.insert_one(notification)
                except Exception as e:
                    print(f"[PUSH ERROR] {e}")
        except Exception as e:
            print(f"[PUSH ERROR] Failed to notify psychic: {e}")
    
    @router.post("/request")
    async def request_chat_session(session: ChatSessionCreate, background_tasks: BackgroundTasks, user_id: str = None, user_name: str = "Client"):
        """Request a chat/phone/video session - advisor must accept"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        # Get psychic's rate
        psychic = await db.psychics.find_one({"id": session.psychic_id})
        if not psychic:
            # Try finding by email for test accounts
            psychic = await db.psychics.find_one({"email": {"$exists": True}})
        
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        if psychic.get("online_status") == "offline":
            raise HTTPException(status_code=400, detail="Psychic is offline")
        
        # Get rate based on session type
        rate_key = f"{session.session_type}_rate"
        rate = psychic.get(rate_key, 2.99)
        
        # Create a pending session request
        request_id = str(uuid.uuid4())
        session_request = {
            "id": request_id,
            "client_id": user_id,
            "client_name": user_name,
            "psychic_id": session.psychic_id,
            "psychic_email": psychic.get("email"),
            "session_type": session.session_type,
            "rate_per_minute": rate,
            "requested_minutes": session.initial_minutes,
            "status": "pending",  # pending, accepted, rejected, expired
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow()  # 60 second timeout
        }
        
        await db.session_requests.insert_one(session_request)
        
        # Send push notification to psychic in background
        background_tasks.add_task(
            notify_psychic_of_request,
            session.psychic_id,
            psychic.get("email"),
            user_name,
            session.session_type
        )
        
        return {
            "success": True,
            "request_id": request_id,
            "status": "pending",
            "message": "Waiting for advisor to accept..."
        }
    
    @router.get("/request/{request_id}/status")
    async def check_request_status(request_id: str):
        """Check the status of a session request"""
        request = await db.session_requests.find_one({"id": request_id})
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        return {
            "request_id": request_id,
            "status": request.get("status"),
            "session_id": request.get("session_id")
        }
    
    @router.get("/requests/pending/{psychic_id}")
    async def get_pending_requests(psychic_id: str):
        """Get pending session requests for a psychic"""
        # Also check by email for test accounts
        psychic = await db.psychics.find_one({"id": psychic_id})
        email = psychic.get("email") if psychic else None
        
        query = {"status": "pending"}
        if email:
            query["$or"] = [{"psychic_id": psychic_id}, {"psychic_email": email}]
        else:
            query["psychic_id"] = psychic_id
        
        requests = await db.session_requests.find(query).sort("created_at", -1).to_list(10)
        
        # Convert ObjectId to string for JSON serialization
        for req in requests:
            req["_id"] = str(req["_id"])
            if "created_at" in req:
                req["created_at"] = req["created_at"].isoformat()
            if "expires_at" in req:
                req["expires_at"] = req["expires_at"].isoformat()
        
        return {"requests": requests}
    
    @router.get("/requests/pending/email/{email}")
    async def get_pending_requests_by_email(email: str):
        """Get pending session requests for a psychic by email"""
        requests = await db.session_requests.find({
            "psychic_email": email,
            "status": "pending"
        }).sort("created_at", -1).to_list(10)
        
        for req in requests:
            req["_id"] = str(req["_id"])
            if "created_at" in req:
                req["created_at"] = req["created_at"].isoformat()
            if "expires_at" in req:
                req["expires_at"] = req["expires_at"].isoformat()
        
        return {"requests": requests}
    
    @router.put("/request/{request_id}/accept")
    async def accept_session_request(request_id: str):
        """Advisor accepts a session request"""
        request = await db.session_requests.find_one({"id": request_id})
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        if request.get("status") != "pending":
            raise HTTPException(status_code=400, detail="Request is no longer pending")
        
        # Create the actual session
        psychic = await db.psychics.find_one({"id": request["psychic_id"]})
        if not psychic:
            psychic = await db.psychics.find_one({"email": request.get("psychic_email")})
        
        rate = request.get("rate_per_minute", 2.99)
        initial_minutes = request.get("requested_minutes", 5)
        
        new_session = ChatSession(
            client_id=request["client_id"],
            psychic_id=request["psychic_id"],
            session_type=request["session_type"],
            rate_per_minute=rate,
            auto_end_minutes=initial_minutes,
            total_cost=rate * initial_minutes
        )
        
        await db.chat_sessions.insert_one(new_session.dict())
        
        # Update request status
        await db.session_requests.update_one(
            {"id": request_id},
            {"$set": {"status": "accepted", "session_id": new_session.id}}
        )
        
        # Update psychic status to busy
        if psychic:
            await db.psychics.update_one(
                {"_id": psychic["_id"]},
                {"$set": {"online_status": "busy"}}
            )
        
        return {
            "success": True,
            "session_id": new_session.id,
            "session": new_session.dict()
        }
    
    @router.put("/request/{request_id}/reject")
    async def reject_session_request(request_id: str):
        """Advisor rejects a session request"""
        result = await db.session_requests.update_one(
            {"id": request_id, "status": "pending"},
            {"$set": {"status": "rejected"}}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Request not found or already processed")
        
        return {"success": True, "status": "rejected"}
    
    @router.post("/start")
    async def start_session(session: ChatSessionCreate, user_id: str = None, use_free_minutes: bool = False):
        """Start a new chat/phone/video session"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        # Get psychic's rate
        psychic = await db.psychics.find_one({"id": session.psychic_id})
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        if psychic.get("online_status") == "offline":
            raise HTTPException(status_code=400, detail="Psychic is offline")
        
        # Get rate based on session type
        rate_key = f"{session.session_type}_rate"
        rate = psychic.get(rate_key, 2.99)
        
        # Check if user is using free minutes
        free_minutes_used = False
        if use_free_minutes:
            # Mark free minutes as USED immediately when session starts
            # This ensures free minutes are only used ONCE, even if session ends early
            user = await db.users.find_one({"id": user_id})
            if user and not user.get("first_reading_free_used", False):
                await db.users.update_one(
                    {"id": user_id},
                    {"$set": {"first_reading_free_used": True}}
                )
                free_minutes_used = True
        
        # Calculate initial cost (free if using free minutes)
        initial_cost = 0 if free_minutes_used else (rate * session.initial_minutes)
        
        new_session = ChatSession(
            client_id=user_id,
            psychic_id=session.psychic_id,
            session_type=session.session_type,
            rate_per_minute=rate,
            auto_end_minutes=session.initial_minutes,
            total_cost=initial_cost
        )
        
        # Store whether this session used free minutes
        session_data = new_session.dict()
        session_data["free_minutes_used"] = free_minutes_used
        session_data["grace_period_active"] = False
        session_data["grace_period_end"] = None
        
        await db.chat_sessions.insert_one(session_data)
        
        # Update psychic status to busy
        await db.psychics.update_one(
            {"id": session.psychic_id},
            {"$set": {"online_status": "busy"}}
        )
        
        return {
            "success": True, 
            "session": session_data,
            "free_minutes_used": free_minutes_used
        }
    
    @router.post("/check-credits")
    async def check_credits_during_session(session_id: str, elapsed_minutes: float, user_id: str = None):
        """Check if user has enough credits and handle grace period"""
        session = await db.chat_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        balance = user.get("balance", 0)
        rate = session.get("rate_per_minute", 2.99)
        free_minutes_used = session.get("free_minutes_used", False)
        
        # Calculate remaining time based on balance
        if free_minutes_used and elapsed_minutes < 4:
            # Still in free minutes period
            remaining_free = 4 - elapsed_minutes
            remaining_paid = balance / rate if rate > 0 else 0
            return {
                "has_credits": True,
                "in_free_period": True,
                "free_minutes_remaining": remaining_free,
                "paid_minutes_available": remaining_paid,
                "balance": balance,
                "should_show_add_credits": balance < (rate * 2),  # Show warning if less than 2 min of credits
                "grace_period_active": False
            }
        else:
            # Paid minutes or free minutes exhausted
            remaining_paid = balance / rate if rate > 0 else 0
            
            # If balance is low (less than 1 minute), start grace period
            if remaining_paid < 1:
                # Check if grace period already active
                if not session.get("grace_period_active"):
                    # Start 60-second grace period
                    grace_end = datetime.utcnow().timestamp() + 60
                    await db.chat_sessions.update_one(
                        {"id": session_id},
                        {"$set": {
                            "grace_period_active": True,
                            "grace_period_end": grace_end
                        }}
                    )
                    return {
                        "has_credits": False,
                        "in_free_period": False,
                        "paid_minutes_available": remaining_paid,
                        "balance": balance,
                        "should_show_add_credits": True,
                        "grace_period_active": True,
                        "grace_period_seconds": 60,
                        "message": "Low credits! Add more to continue your session."
                    }
                else:
                    # Grace period already active - check if expired
                    grace_end = session.get("grace_period_end", 0)
                    remaining_grace = max(0, grace_end - datetime.utcnow().timestamp())
                    return {
                        "has_credits": False,
                        "in_free_period": False,
                        "paid_minutes_available": remaining_paid,
                        "balance": balance,
                        "should_show_add_credits": True,
                        "grace_period_active": True,
                        "grace_period_seconds": remaining_grace,
                        "should_end_session": remaining_grace <= 0,
                        "message": "Add credits now to continue!"
                    }
            
            return {
                "has_credits": True,
                "in_free_period": False,
                "paid_minutes_available": remaining_paid,
                "balance": balance,
                "should_show_add_credits": remaining_paid < 2,
                "grace_period_active": False
            }
    
    @router.post("/message")
    async def send_message(session_id: str, message: str, user_id: str = None, user_type: str = "client", image_url: str = None):
        """Send a message in a chat session"""
        session = await db.chat_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if session.get("status") != "active":
            raise HTTPException(status_code=400, detail="Session is not active")
        
        new_message = ChatMessage(
            sender_type=user_type,
            sender_id=user_id or "unknown",
            message=message,
            message_type="image" if image_url else "text",
            image_url=image_url
        )
        
        await db.chat_sessions.update_one(
            {"id": session_id},
            {"$push": {"messages": new_message.dict()}}
        )
        
        return {"success": True, "message": new_message.dict()}
    
    @router.post("/add-minutes")
    async def add_minutes(request: AddMinutesRequest, user_id: str = None):
        """Add more minutes to an active session (1-tap add)"""
        session = await db.chat_sessions.find_one({"id": request.session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if session.get("status") != "active":
            raise HTTPException(status_code=400, detail="Session is not active")
        
        additional_cost = session.get("rate_per_minute", 2.99) * request.minutes
        
        await db.chat_sessions.update_one(
            {"id": request.session_id},
            {
                "$inc": {
                    "auto_end_minutes": request.minutes,
                    "total_cost": additional_cost
                },
                "$set": {"warning_sent": False}  # Reset warning
            }
        )
        
        return {"success": True, "minutes_added": request.minutes, "additional_cost": additional_cost}
    
    @router.post("/end/{session_id}")
    async def end_session(session_id: str, background_tasks: BackgroundTasks):
        """End a chat session and notify both parties"""
        session = await db.chat_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Calculate final duration and cost
        started_at = session.get("started_at", datetime.utcnow())
        duration_minutes = (datetime.utcnow() - started_at).total_seconds() / 60
        total_cost = duration_minutes * session.get("rate_per_minute", 2.99)
        
        await db.chat_sessions.update_one(
            {"id": session_id},
            {
                "$set": {
                    "status": "ended",
                    "ended_at": datetime.utcnow(),
                    "total_minutes": round(duration_minutes, 2),
                    "total_cost": round(total_cost, 2)
                }
            }
        )
        
        # Notify both parties via WebSocket that the session has ended
        from routes.websocket import manager
        client_id = session.get("client_id")
        psychic_id = session.get("psychic_id")
        
        end_message = {
            "type": "session_ended",
            "session_id": session_id,
            "duration_minutes": round(duration_minutes, 2),
            "total_cost": round(total_cost, 2),
            "ended_by": "user"
        }
        
        # Notify client
        if client_id:
            await manager.send_personal_message(end_message, client_id)
        
        # Notify psychic
        if psychic_id:
            await manager.send_personal_message(end_message, psychic_id)
        
        # Set psychic back to online
        await db.psychics.update_one(
            {"id": session.get("psychic_id")},
            {"$set": {"online_status": "online"}}
        )
        
        # Update psychic earnings (40%)
        psychic_earnings = total_cost * 0.40
        await db.psychics.update_one(
            {"id": session.get("psychic_id")},
            {"$inc": {"total_earnings": psychic_earnings, "total_readings": 1}}
        )
        
        # Send session receipt email to client
        client = await db.users.find_one({"id": session.get("client_id")})
        psychic = await db.psychics.find_one({"id": session.get("psychic_id")})
        
        if client and client.get("email"):
            client_email = client.get("email")
            client_name = client.get("name", client_email.split("@")[0])
            psychic_name = psychic.get("name", "Your Advisor") if psychic else "Your Advisor"
            session_type = session.get("session_type", "chat")
            session_date = datetime.utcnow().strftime("%B %d, %Y at %I:%M %p UTC")
            
            background_tasks.add_task(
                send_session_receipt_email_async,
                client_email,
                client_name,
                psychic_name,
                session_type,
                round(duration_minutes, 2),
                round(total_cost, 2),
                session_date
            )
        
        return {
            "success": True,
            "duration_minutes": round(duration_minutes, 2),
            "total_cost": round(total_cost, 2)
        }
    
    @router.get("/{session_id}")
    async def get_session(session_id: str):
        """Get session details"""
        session = await db.chat_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    
    @router.get("/active/{user_id}")
    async def get_active_sessions(user_id: str):
        """Get user's active sessions"""
        sessions = await db.chat_sessions.find({
            "client_id": user_id,
            "status": "active"
        }).to_list(10)
        return sessions
    
    @router.get("/history/{user_id}")
    async def get_session_history(user_id: str, limit: int = 20):
        """Get user's session history"""
        sessions = await db.chat_sessions.find({
            "client_id": user_id
        }).sort("started_at", -1).to_list(limit)
        return sessions
    
    return router
