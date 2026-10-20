from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List
from datetime import datetime, timedelta
import uuid
import sys
import os
import httpx
from pathlib import Path
from pymongo import ReturnDocument

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
    async def notify_psychic_of_request(
        psychic_id: str,
        psychic_email: str,
        client_name: str,
        session_type: str,
        request_id: str = "",
        rate: float = 2.99,
        minutes: int = 5,
        client_type: str = "NEW CLIENT",
    ):
        """Send push notification (FCM + Expo) & save in-app notification to psychic about incoming request"""
        try:
            target_ids = [x for x in [psychic_id, psychic_email] if x]
            if psychic_id in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0") or (psychic_email or "").lower() in ("testpsychic@sixthsense.com", "luna@psychic.com"):
                for alias in ["psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com", "luna@psychic.com"]:
                    if alias not in target_ids:
                        target_ids.append(alias)

            # Store notification in DB for the advisor app
            notif_id = str(uuid.uuid4())
            now_utc = datetime.utcnow()
            notification = {
                "id": notif_id,
                "user_id": psychic_id,
                "user_type": "psychic",
                "type": "reading",
                "notification_type": "reading",
                "related_id": request_id,
                "title": "New Client Request! 🌟",
                "body": f"{client_name} wants to start a {session_type} session with you (${rate}/min for {minutes} mins).",
                "data": {
                    "type": "session_request",
                    "request_id": request_id,
                    "session_type": session_type,
                    "client_name": client_name,
                    "client_type": client_type,
                    "rate": str(rate),
                    "minutes": str(minutes),
                    "deeplink": f"/incoming-request?requestId={request_id}&clientName={client_name}&sessionType={session_type}&rate={rate}&minutes={minutes}"
                },
                "created_at": now_utc,
                "is_read": False,
                "read": False
            }
            await db.notifications.insert_one(notification)

            try:
                from routes.websocket import manager
                await manager.send_personal_message({
                    "type": "new_notification",
                    "notification": {
                        "id": notif_id,
                        "user_id": psychic_id,
                        "user_type": "psychic",
                        "type": "reading",
                        "notification_type": "reading",
                        "title": notification["title"],
                        "body": notification["body"],
                        "is_read": False,
                        "created_at": now_utc.isoformat(),
                        "data": notification["data"],
                    }
                }, psychic_id)
            except Exception as ws_err:
                print(f"[WS NOTIF ERROR] {ws_err}")

            # Collect all unique push tokens for this psychic
            unique_tokens = []
            tokens = await db.device_tokens.find({
                "user_id": {"$in": target_ids},
                "is_active": True
            }).to_list(20)
            for td in tokens:
                tok = td.get("fcm_token") or td.get("token")
                if tok and tok not in unique_tokens:
                    unique_tokens.append(tok)

            psy_docs = await db.psychics.find({
                "$or": [{"id": {"$in": target_ids}}, {"email": {"$in": target_ids}}]
            }).to_list(10)
            for pd in psy_docs:
                tok = pd.get("fcm_token")
                if tok and tok not in unique_tokens:
                    unique_tokens.append(tok)

            usr_docs = await db.users.find({
                "$or": [{"id": {"$in": target_ids}}, {"email": {"$in": target_ids}}]
            }).to_list(10)
            for ud in usr_docs:
                tok = ud.get("fcm_token")
                if tok and tok not in unique_tokens:
                    unique_tokens.append(tok)

            if not unique_tokens:
                print(f"[PUSH] No tokens found for psychic {psychic_id} ({target_ids})")
                return

            from routes.push_notifications import send_firebase_token_push
            for tok in unique_tokens:
                try:
                    print(f"[PUSH] Dispatching FCM push to psychic {psychic_id} ({tok[:25]}...): {client_name} wants {session_type}")
                    await send_firebase_token_push(
                        token=tok,
                        title="New Client Request! 🌟",
                        body=f"{client_name} wants to start a {session_type} session with you (${rate}/min).",
                        data=notification["data"]
                    )
                except Exception as e:
                    print(f"[PUSH ERROR] {e}")
        except Exception as e:
            print(f"[PUSH ERROR] Failed to notify psychic: {e}")
    
    @router.post("/request")
    async def request_chat_session(session: ChatSessionCreate, background_tasks: BackgroundTasks, user_id: str = None, user_name: str = "Client"):
        """Request a chat/phone/video session - advisor must accept"""
        user_id = user_id or getattr(session, "client_id", None)
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        # Get psychic from DB or MOCK_PSYCHICS
        psychic = await db.psychics.find_one({"id": session.psychic_id})
        if not psychic and session.psychic_id == "psy-001":
            psychic = await db.psychics.find_one({"email": {"$in": ["testpsychic@sixthsense.com", "luna@psychic.com"]}})
        if not psychic:
            try:
                from routes.psychics import MOCK_PSYCHICS
                for mp in MOCK_PSYCHICS:
                    if mp.get("id") == session.psychic_id:
                        psychic = mp
                        break
            except Exception:
                pass
        if not psychic:
            # Try finding by email for test accounts
            psychic = await db.psychics.find_one({"email": {"$exists": True}})
        
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        if psychic.get("online_status") == "offline":
            raise HTTPException(status_code=400, detail="Psychic is offline")
        
        # Get rate based on session type
        s_type = (session.session_type or "chat").lower()
        if s_type == "video":
            rate = psychic.get("video_rate") or psychic.get("video_call_rate") or psychic.get("rate_per_minute") or 5.99
        elif s_type in ("phone", "voice", "audio"):
            rate = psychic.get("phone_rate") or psychic.get("rate_per_minute") or 4.99
            s_type = "phone"
        else:
            rate = psychic.get("chat_rate") or psychic.get("rate_per_minute") or psychic.get("price_per_minute") or 2.99
            s_type = "chat"
        
        # Determine whether client is NEW or RETURNING
        prior_sessions = await db.chat_sessions.count_documents({
            "client_id": user_id,
            "psychic_id": session.psychic_id,
            "status": "completed"
        })
        client_type = "RETURNING CLIENT" if prior_sessions > 0 else "NEW CLIENT"

        actual_psychic_id = psychic.get("id") or session.psychic_id
        actual_psychic_email = psychic.get("email")

        # Deduplication guard: if client already has a pending request created within last 15s, return it
        existing_pending = await db.session_requests.find_one({
            "client_id": user_id,
            "status": "pending",
            "$or": [{"psychic_id": actual_psychic_id}, {"requested_psychic_id": session.psychic_id}],
            "created_at": {"$gte": datetime.utcnow() - timedelta(seconds=15)}
        })
        if existing_pending:
            print(f"ℹ️ Returning existing pending session request to prevent duplicate: {existing_pending['id']}")
            return {
                "request_id": existing_pending["id"],
                "status": "pending",
                "message": "Session request sent to psychic",
                "expires_in": 60
            }

        # Create a pending session request
        request_id = str(uuid.uuid4())
        now_dt = datetime.utcnow()
        session_request = {
            "id": request_id,
            "client_id": user_id,
            "client_name": user_name,
            "client_type": client_type,
            "psychic_id": actual_psychic_id,
            "requested_psychic_id": session.psychic_id,
            "psychic_email": actual_psychic_email,
            "session_type": s_type,
            "rate_per_minute": rate,
            "requested_minutes": session.initial_minutes,
            "status": "pending",  # pending, accepted, rejected, expired
            "created_at": now_dt,
            "expires_at": now_dt + timedelta(seconds=60)
        }
        
        await db.session_requests.insert_one(session_request)
        
        # Immediately notify psychic via WebSocket for instant popup in Advisor App!
        try:
            from routes.websocket import manager
            ws_req_payload = {
                "type": "incoming_request",
                "request_id": request_id,
                "request": {
                    "id": request_id,
                    "client_id": user_id,
                    "client_name": user_name,
                    "client_type": client_type,
                    "psychic_id": actual_psychic_id,
                    "psychic_email": actual_psychic_email,
                    "session_type": s_type,
                    "rate_per_minute": rate,
                    "requested_minutes": session.initial_minutes,
                    "status": "pending",
                    "created_at": now_dt.isoformat()
                }
            }
            # send_personal_message automatically expands advisor aliases and deduplicates active sockets
            await manager.send_personal_message(ws_req_payload, actual_psychic_id)
            print(f"📲 Sent real-time incoming_request WS event ({s_type}) to advisor: {actual_psychic_id}")
        except Exception as e:
            print(f"Error sending WS incoming_request: {e}")

        # Send push notification to psychic in background
        background_tasks.add_task(
            notify_psychic_of_request,
            actual_psychic_id,
            actual_psychic_email,
            user_name,
            s_type,
            request_id,
            rate,
            session.initial_minutes,
            client_type
        )
        
        return {
            "success": True,
            "request_id": request_id,
            "status": "pending",
            "session_type": s_type,
            "client_type": client_type,
            "message": "Waiting for advisor to accept..."
        }
    
    @router.get("/request/{request_id}/status")
    async def check_request_status(request_id: str):
        """Check the status of a session request"""
        request = await db.session_requests.find_one({"id": request_id})
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        session_id = request.get("session_id")
        agora_channel = request.get("agora_channel")
        agora_app_id = request.get("agora_app_id")
        client_uid = request.get("client_uid")
        psychic_uid = request.get("psychic_uid")
        client_token = request.get("client_token")
        psychic_token = request.get("psychic_token")

        if request.get("status") == "accepted" and session_id and (not client_token or not client_uid):
            session = await db.chat_sessions.find_one({"id": session_id})
            if session:
                agora_channel = agora_channel or session.get("agora_channel")
                agora_app_id = agora_app_id or session.get("agora_app_id")
                client_uid = client_uid or session.get("client_uid")
                psychic_uid = psychic_uid or session.get("psychic_uid")
                client_token = client_token or session.get("client_token")
                psychic_token = psychic_token or session.get("psychic_token")
        
        return {
            "request_id": request_id,
            "status": request.get("status"),
            "session_id": session_id,
            "session_type": request.get("session_type", "chat"),
            "agora_channel": agora_channel,
            "agora_app_id": agora_app_id,
            "client_uid": client_uid,
            "psychic_uid": psychic_uid,
            "client_token": client_token,
            "psychic_token": psychic_token,
        }
    
    @router.get("/requests/pending/{psychic_id}")
    async def get_pending_requests(psychic_id: str):
        """Get pending session requests for a psychic"""
        psychic = await db.psychics.find_one({"id": psychic_id})
        email = psychic.get("email") if psychic else None
        
        or_clauses = [{"psychic_id": psychic_id}, {"requested_psychic_id": psychic_id}]
        if email:
            or_clauses.append({"psychic_email": email})
        if psychic_id in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0") or (email or "").lower() in ("testpsychic@sixthsense.com", "luna@psychic.com"):
            or_clauses.extend([
                {"psychic_id": "psy-001"},
                {"psychic_id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"},
                {"requested_psychic_id": "psy-001"},
                {"psychic_email": "testpsychic@sixthsense.com"},
                {"psychic_email": "luna@psychic.com"},
            ])
        
        now = datetime.utcnow()
        # Auto-expire stale pending requests
        await db.session_requests.update_many(
            {
                "status": "pending",
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"created_at": {"$lt": now - timedelta(seconds=60)}}
                ]
            },
            {"$set": {"status": "expired"}}
        )

        query = {
            "status": "pending",
            "$or": or_clauses,
            "created_at": {"$gte": now - timedelta(seconds=60)}
        }
        requests = await db.session_requests.find(query).sort("created_at", -1).to_list(10)
        
        for req in requests:
            req["_id"] = str(req["_id"])
            if "created_at" in req and hasattr(req["created_at"], "isoformat"):
                req["created_at"] = req["created_at"].isoformat()
            if "expires_at" in req and hasattr(req["expires_at"], "isoformat"):
                req["expires_at"] = req["expires_at"].isoformat()
        
        return {"requests": requests}
    
    @router.get("/requests/pending/email/{email}")
    async def get_pending_requests_by_email(email: str):
        """Get pending session requests for a psychic by email"""
        now = datetime.utcnow()
        # Auto-expire stale pending requests
        await db.session_requests.update_many(
            {
                "status": "pending",
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"created_at": {"$lt": now - timedelta(seconds=60)}}
                ]
            },
            {"$set": {"status": "expired"}}
        )

        email_lower = email.lower().strip()
        or_clauses = [{"psychic_email": email}, {"psychic_email": email_lower}]
        if email_lower in ("testpsychic@sixthsense.com", "luna@psychic.com"):
            or_clauses.extend([
                {"psychic_email": "testpsychic@sixthsense.com"},
                {"psychic_email": "luna@psychic.com"},
                {"psychic_id": "psy-001"},
                {"psychic_id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"},
                {"requested_psychic_id": "psy-001"},
            ])
        
        requests = await db.session_requests.find({
            "status": "pending",
            "$or": or_clauses,
            "created_at": {"$gte": now - timedelta(seconds=60)}
        }).sort("created_at", -1).to_list(10)
        
        for req in requests:
            req["_id"] = str(req["_id"])
            if "created_at" in req and hasattr(req["created_at"], "isoformat"):
                req["created_at"] = req["created_at"].isoformat()
            if "expires_at" in req and hasattr(req["expires_at"], "isoformat"):
                req["expires_at"] = req["expires_at"].isoformat()
        
        return {"requests": requests}
    
    @router.put("/request/{request_id}/accept")
    async def accept_session_request(request_id: str):
        """Advisor accepts a session request (supports chat, phone, video with Agora tokens)"""
        request = await db.session_requests.find_one({"id": request_id})
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        
        if request.get("status") == "accepted" and request.get("session_id"):
            existing_session = await db.chat_sessions.find_one({"id": request["session_id"]})
            if existing_session:
                existing_session["_id"] = str(existing_session.get("_id", ""))
                return {
                    "success": True,
                    "session_id": request["session_id"],
                    "session": existing_session,
                    "agora_channel": existing_session.get("agora_channel"),
                    "agora_app_id": existing_session.get("agora_app_id"),
                    "client_uid": existing_session.get("client_uid"),
                    "psychic_uid": existing_session.get("psychic_uid"),
                    "client_token": existing_session.get("client_token"),
                    "psychic_token": existing_session.get("psychic_token"),
                }

        if request.get("status") != "pending":
            created_at = request.get("created_at")
            is_recent = False
            if request.get("status") == "expired" and created_at:
                try:
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if isinstance(created_at, datetime):
                        is_recent = (datetime.utcnow() - created_at.replace(tzinfo=None)).total_seconds() < 90
                except Exception:
                    pass
            if not is_recent:
                raise HTTPException(status_code=400, detail="Request is no longer pending")
        
        # Create the actual session
        psychic = await db.psychics.find_one({"id": request["psychic_id"]})
        if not psychic:
            psychic = await db.psychics.find_one({"email": request.get("psychic_email")})
        
        rate = request.get("rate_per_minute", 2.99)
        initial_minutes = request.get("requested_minutes", 5)
        s_type = request.get("session_type", "chat")
        
        new_session = ChatSession(
            client_id=request["client_id"],
            psychic_id=request["psychic_id"],
            session_type=s_type,
            rate_per_minute=rate,
            auto_end_minutes=initial_minutes,
            total_cost=rate * initial_minutes
        )
        
        # Generate Agora RTC credentials for audio/video calls (and even available if switched in chat)
        agora_channel = f"sixthsense_{new_session.id.replace('-', '')[:16]}"
        client_uid = (abs(hash(str(request["client_id"]))) % 80000) + 1000
        psychic_uid = (abs(hash(str(request["psychic_id"]))) % 80000) + 100000
        try:
            from routes.video import generate_agora_token, AGORA_APP_ID
            agora_app_id = AGORA_APP_ID or "adff706fa53f45529d871c60da335b84"
            # In Agora RTC, token built with uid=0 is a wildcard token valid for ANY integer uid (Astrology style)
            wildcard_token = generate_agora_token(agora_channel, 0, 1)
            client_token = wildcard_token or generate_agora_token(agora_channel, client_uid, 1)
            psychic_token = wildcard_token or generate_agora_token(agora_channel, psychic_uid, 1)
        except Exception as agora_err:
            print(f"[AGORA TOKEN ERROR] {agora_err}")
            agora_app_id = os.getenv("AGORA_APP_ID", "adff706fa53f45529d871c60da335b84")
            client_token = f"mock_token_{agora_channel}_{client_uid}"
            psychic_token = f"mock_token_{agora_channel}_{psychic_uid}"

        session_doc = new_session.dict()
        session_doc["requested_psychic_id"] = request.get("requested_psychic_id", request["psychic_id"])
        session_doc["psychic_email"] = request.get("psychic_email")
        session_doc["client_name"] = request.get("client_name", "Client")
        session_doc["agora_channel"] = agora_channel
        session_doc["agora_app_id"] = agora_app_id
        session_doc["client_uid"] = client_uid
        session_doc["psychic_uid"] = psychic_uid
        session_doc["client_token"] = client_token
        session_doc["psychic_token"] = psychic_token
        await db.chat_sessions.insert_one(session_doc)
        if "_id" in session_doc:
            session_doc["_id"] = str(session_doc["_id"])
        
        # Update request status with Agora fields
        await db.session_requests.update_one(
            {"id": request_id},
            {"$set": {
                "status": "accepted",
                "session_id": new_session.id,
                "agora_channel": agora_channel,
                "agora_app_id": agora_app_id,
                "client_uid": client_uid,
                "psychic_uid": psychic_uid,
                "client_token": client_token,
                "psychic_token": psychic_token,
            }}
        )
        
        # Mark client's first reading as used once a live session is accepted
        if request.get("client_id"):
            await db.users.update_one(
                {"id": request["client_id"]},
                {"$set": {
                    "first_reading_free_used": True,
                    "is_new_user": False,
                    "free_credits": 0.0,
                    "free_minutes_available": 0,
                }}
            )
        
        # Update psychic status to busy
        if psychic:
            await db.psychics.update_one(
                {"_id": psychic["_id"]},
                {"$set": {"online_status": "busy"}}
            )

        # Notify client immediately via WebSocket & broadcast busy status
        try:
            from routes.websocket import manager
            if request.get("client_id"):
                await manager.send_personal_message({
                    "type": "request_accepted",
                    "request_id": request_id,
                    "session_id": new_session.id,
                    "session_type": s_type,
                    "psychic_id": request["psychic_id"],
                    "agora_channel": agora_channel,
                    "agora_app_id": agora_app_id,
                    "client_uid": client_uid,
                    "psychic_uid": psychic_uid,
                    "client_token": client_token,
                    "psychic_token": psychic_token,
                }, request["client_id"])
            await manager.broadcast({
                "type": "psychic_status_changed",
                "psychic_id": request["psychic_id"],
                "alias_ids": [x for x in [request.get("psychic_id"), request.get("requested_psychic_id")] if x],
                "email": request.get("psychic_email"),
                "online_status": "busy",
                "is_online": True,
            })
        except Exception as e:
            print(f"Error sending accept WS notification: {e}")
        
        return {
            "success": True,
            "session_id": new_session.id,
            "session": session_doc,
            "agora_channel": agora_channel,
            "agora_app_id": agora_app_id,
            "client_uid": client_uid,
            "psychic_uid": psychic_uid,
            "client_token": client_token,
            "psychic_token": psychic_token,
        }
    
    @router.put("/request/{request_id}/reject")
    async def reject_session_request(request_id: str):
        """Advisor rejects a session request"""
        request = await db.session_requests.find_one({"id": request_id})
        if not request:
            return {"success": True, "status": "not_found"}
        if request.get("status") != "pending":
            # Already accepted, rejected, or cancelled - do not error on late timer
            return {"success": True, "status": request.get("status")}

        await db.session_requests.update_one(
            {"id": request_id, "status": "pending"},
            {"$set": {"status": "rejected"}}
        )
        
        try:
            from routes.websocket import manager
            if request and request.get("client_id"):
                await manager.send_personal_message({
                    "type": "request_rejected",
                    "request_id": request_id,
                }, request["client_id"])
        except Exception as e:
            print(f"Error sending reject WS notification: {e}")

        return {"success": True, "status": "rejected"}

    
    @router.delete("/request/{request_id}")
    @router.post("/request/{request_id}/cancel")
    async def cancel_session_request(request_id: str):
        """Client cancels a pending session request (CLI-063)"""
        result = await db.session_requests.update_one(
            {"id": request_id, "status": "pending"},
            {"$set": {"status": "cancelled", "cancelled_at": datetime.utcnow()}}
        )
        
        if result.matched_count == 0:
            req = await db.session_requests.find_one({"id": request_id})
            if not req:
                return {"success": True, "status": "cancelled", "message": "Request not found or already closed"}
            return {"success": True, "status": req.get("status", "cancelled"), "message": "Request already processed"}
            
        return {"success": True, "status": "cancelled", "message": "Request cancelled successfully"}
    
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
        rate = psychic.get(rate_key) or psychic.get("rate_per_minute") or psychic.get("price_per_minute") or 2.99
        
        # Check user and enforce one-time 4-minute free trial
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        free_minutes_used = False
        if use_free_minutes:
            # Atomic check-and-set: prevents concurrent race conditions
            updated_user = await db.users.find_one_and_update(
                {"id": user_id, "first_reading_free_used": {"$ne": True}},
                {"$set": {"first_reading_free_used": True, "is_new_user": False}},
                return_document=ReturnDocument.AFTER
            )
            if not updated_user:
                raise HTTPException(
                    status_code=400,
                    detail="Your one-time 4-minute free trial has already been used."
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
        
        if "_id" in session_data:
            session_data["_id"] = str(session_data["_id"])
        
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
    async def end_session(session_id: str, background_tasks: BackgroundTasks, ended_by: str = "user"):
        """End a chat session, deduct client wallet, credit advisor earnings, and notify both parties"""
        session = await db.chat_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        from routes.websocket import manager

        # Idempotent check if session was already ended
        if session.get("status") == "ended":
            return {
                "success": True,
                "already_ended": True,
                "session_id": session_id,
                "duration_seconds": session.get("duration_seconds", int(session.get("total_minutes", 1) * 60)),
                "duration_minutes": session.get("total_minutes", 1.0),
                "rate_per_minute": session.get("rate_per_minute", 2.99),
                "total_cost": session.get("total_cost", 2.99),
                "psychic_earnings": session.get("psychic_earnings", round(session.get("total_cost", 2.99) * 0.40, 2)),
                "remaining_balance": session.get("remaining_balance", 0.0),
                "ended_by": session.get("ended_by", ended_by),
            }

        now_utc = datetime.utcnow()
        started_at = session.get("started_at", now_utc)
        if isinstance(started_at, str):
            try:
                started_at = datetime.fromisoformat(started_at.replace("Z", ""))
            except Exception:
                started_at = now_utc

        duration_seconds = max(1, int((now_utc - started_at).total_seconds()))
        duration_minutes = round(duration_seconds / 60.0, 2)
        rate = float(session.get("rate_per_minute", 2.99))

        # Bill minimum 1 minute (or exact pro-rata minutes if > 1 minute) so every live session has a clear billable amount
        billable_minutes = max(1.0, duration_minutes)
        total_cost = round(billable_minutes * rate, 2)
        psychic_earnings = round(total_cost * 0.40, 2)

        client_id = session.get("client_id")
        psychic_id = session.get("psychic_id")

        # Deduct total_cost from client wallet balance in db.users
        client = await db.users.find_one({"id": client_id}) if client_id else None
        current_balance = float(client.get("balance", 0.0)) if client else 0.0
        new_balance = round(max(0.0, current_balance - total_cost), 2)
        if client_id and client:
            await db.users.update_one(
                {"id": client_id},
                {"$set": {"balance": new_balance}}
            )

        await db.chat_sessions.update_one(
            {"id": session_id},
            {
                "$set": {
                    "status": "ended",
                    "ended_at": now_utc,
                    "ended_by": ended_by,
                    "duration_seconds": duration_seconds,
                    "total_minutes": duration_minutes,
                    "total_cost": total_cost,
                    "psychic_earnings": psychic_earnings,
                    "remaining_balance": new_balance,
                }
            }
        )

        # Record client transaction deduction in db.transactions
        if client_id and total_cost > 0:
            try:
                psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"email": session.get("psychic_email")}]})
                psychic_name = (psychic.get("name") if psychic else None) or session.get("psychic_name") or "Psychic Advisor"
                s_type = (session.get("session_type") or "chat").lower()
                type_label = "Audio Call" if s_type in ["phone", "voice", "audio"] else ("Video Call" if s_type == "video" else "Chat Reading")
                clean_sid = session_id.replace("-", "")[:16]
                tx_id = f"tx_sess_{clean_sid}"
                await db.transactions.update_one(
                    {"session_id": session_id, "user_id": client_id},
                    {
                        "$set": {
                            "id": tx_id,
                            "transaction_id": tx_id,
                            "user_id": client_id,
                            "client_id": client_id,
                            "type": "session_deduction",
                            "session_type": s_type,
                            "session_id": session_id,
                            "psychic_id": psychic_id,
                            "psychic_name": psychic_name,
                            "amount": total_cost,
                            "duration_minutes": duration_minutes,
                            "duration_seconds": duration_seconds,
                            "rate_per_minute": rate,
                            "description": f"{type_label} with {psychic_name} ({duration_minutes} min @ ${rate:.2f}/min)",
                            "status": "completed",
                            "created_at": now_utc,
                            "updated_at": now_utc,
                        }
                    },
                    upsert=True
                )
            except Exception as tx_err:
                print(f"[WALLET] Error recording session transaction: {tx_err}")

        end_message = {
            "type": "session_ended",
            "session_id": session_id,
            "conversation_id": session_id,
            "duration_seconds": duration_seconds,
            "duration_minutes": duration_minutes,
            "rate_per_minute": rate,
            "total_cost": total_cost,
            "psychic_earnings": psychic_earnings,
            "remaining_balance": new_balance,
            "ended_by": ended_by,
        }

        # Notify client, psychic (including all alias IDs), and conversation room
        if client_id:
            await manager.send_personal_message(end_message, client_id)
        if psychic_id:
            await manager.send_personal_message(end_message, psychic_id)
        await manager.broadcast_to_conversation(end_message, session_id)

        # Set psychic back to online and credit earnings to both lifetime and available balance
        await db.psychics.update_many(
            {"$or": [{"id": psychic_id}, {"email": session.get("psychic_email")}]},
            {
                "$set": {"online_status": "online", "is_online": True, "offers_chat": True},
                "$inc": {
                    "total_earnings": psychic_earnings,
                    "available_balance": psychic_earnings,
                    "total_readings": 1,
                },
            }
        )
        if psychic_id:
            try:
                updated_psy = await db.psychics.find_one({"id": psychic_id}) or {}
                await manager.send_personal_message({
                    "type": "earnings_updated",
                    "psychic_id": psychic_id,
                    "session_id": session_id,
                    "amount": psychic_earnings,
                    "available_balance": round(float(updated_psy.get("available_balance", 0) or 0), 2),
                    "total_earnings": round(float(updated_psy.get("total_earnings", 0) or 0), 2),
                }, psychic_id)
            except Exception as e:
                print(f"Error sending earnings_updated: {e}")
        try:
            await manager.broadcast({
                "type": "psychic_status_changed",
                "psychic_id": psychic_id,
                "alias_ids": [x for x in [psychic_id, session.get("requested_psychic_id"), "psy-001"] if x],
                "online_status": "online",
                "is_online": True,
                "offers_chat": True,
            })
        except Exception as e:
            print(f"Error broadcasting online status after session end: {e}")

        # Create in-app notifications for both Advisor and Client
        psychic = await db.psychics.find_one({"id": psychic_id})
        client_name = (client.get("name") if client else None) or session.get("client_name") or "Client"
        psychic_name = (psychic.get("name") if psychic else None) or "Your Advisor"

        try:
            adv_notif = {
                "id": str(uuid.uuid4()),
                "user_id": psychic_id,
                "user_type": "psychic",
                "type": "earning",
                "notification_type": "earning",
                "related_id": session_id,
                "title": "Session Completed 💰",
                "body": f"Session with {client_name} ended ({duration_seconds // 60}m {duration_seconds % 60}s). Client charged ${total_cost:.2f} • Your earnings: +${psychic_earnings:.2f}.",
                "is_read": False,
                "read": False,
                "created_at": now_utc,
            }
            await db.notifications.insert_one(adv_notif)
            await manager.send_personal_message({
                "type": "new_notification",
                "notification": {
                    **{k: v for k, v in adv_notif.items() if k != "_id"},
                    "created_at": now_utc.isoformat(),
                }
            }, psychic_id)

            # Trigger push notification to advisor's device
            try:
                from routes.push_notifications import send_push_to_user
                await send_push_to_user(
                    db=db,
                    user_id=psychic_id,
                    title="Session Completed 💰",
                    body=f"Session with {client_name} ended. Your earnings: +${psychic_earnings:.2f}!",
                    data={"type": "earning", "session_id": session_id, "amount": str(psychic_earnings)}
                )
            except Exception as pe:
                print(f"Error sending session end push notification: {pe}")
        except Exception as e:
            print(f"Error saving advisor session end notification: {e}")

        if client and client.get("email"):
            client_email = client.get("email")
            session_type = session.get("session_type", "chat")
            session_date = now_utc.strftime("%B %d, %Y at %I:%M %p UTC")
            background_tasks.add_task(
                send_session_receipt_email_async,
                client_email,
                client_name,
                psychic_name,
                session_type,
                duration_minutes,
                total_cost,
                session_date
            )

        return {
            "success": True,
            "session_id": session_id,
            "duration_seconds": duration_seconds,
            "duration_minutes": duration_minutes,
            "rate_per_minute": rate,
            "total_cost": total_cost,
            "psychic_earnings": psychic_earnings,
            "remaining_balance": new_balance,
            "ended_by": ended_by,
        }
    
    @router.get("/{session_id}")
    async def get_session(session_id: str):
        """Get session details"""
        session = await db.chat_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Ensure Agora credentials exist for audio/video call
        if not session.get("client_token") or not session.get("psychic_token"):
            try:
                from routes.video import generate_agora_token, AGORA_APP_ID
                agora_channel = session.get("agora_channel") or f"sixthsense_{session_id.replace('-', '')[:16]}"
                agora_app_id = session.get("agora_app_id") or AGORA_APP_ID or "adff706fa53f45529d871c60da335b84"
                client_uid = session.get("client_uid") or ((abs(hash(str(session.get("client_id", "client")))) % 80000) + 1000)
                psychic_uid = session.get("psychic_uid") or ((abs(hash(str(session.get("psychic_id", "psychic")))) % 80000) + 100000)
                
                wildcard_token = generate_agora_token(agora_channel, 0, 1)
                c_tok = wildcard_token or generate_agora_token(agora_channel, client_uid, 1)
                p_tok = wildcard_token or generate_agora_token(agora_channel, psychic_uid, 1)
                
                await db.chat_sessions.update_one(
                    {"id": session_id},
                    {"$set": {
                        "agora_channel": agora_channel,
                        "agora_app_id": agora_app_id,
                        "client_uid": client_uid,
                        "psychic_uid": psychic_uid,
                        "client_token": c_tok,
                        "psychic_token": p_tok,
                    }}
                )
                session["agora_channel"] = agora_channel
                session["agora_app_id"] = agora_app_id
                session["client_uid"] = client_uid
                session["psychic_uid"] = psychic_uid
                session["client_token"] = c_tok
                session["psychic_token"] = p_tok
            except Exception as e:
                print(f"Error ensuring Agora tokens in get_session: {e}")

        if "_id" in session:
            session["_id"] = str(session["_id"])
        return session
    
    @router.get("/active/{user_id}")
    async def get_active_sessions(user_id: str):
        """Get user's active sessions"""
        sessions = await db.chat_sessions.find({
            "client_id": user_id,
            "status": "active"
        }).to_list(10)
        for s in sessions:
            if "_id" in s:
                s["_id"] = str(s["_id"])
        return sessions
    
    @router.get("/sessions/client/{client_id}")
    @router.get("/history/{user_id}")
    async def get_client_sessions_history(client_id: str = None, user_id: str = None, limit: int = 50):
        """Get client's session history with psychic details and last messages for History tab"""
        cid = client_id or user_id or "client"
        from routes.websocket import expand_user_aliases
        target_ids = expand_user_aliases(cid)
        
        # Find all chat sessions for this client (or match client_id)
        sessions = await db.chat_sessions.find({
            "$or": [
                {"client_id": {"$in": target_ids}},
                {"client_id": cid},
            ]
        }).sort("started_at", -1).to_list(limit)

        # If empty and cid is guest/unknown, return recent sessions as fallback
        if not sessions and cid in ("guest", "client", "unknown", ""):
            sessions = await db.chat_sessions.find({}).sort("started_at", -1).to_list(limit)
        
        result = []
        for s in sessions:
            if "_id" in s:
                s["_id"] = str(s["_id"])
            
            psy_id = s.get("psychic_id")
            psy = None
            if psy_id:
                psy = await db.psychics.find_one({"id": {"$in": expand_user_aliases(psy_id)}})
            
            # Find last message
            messages = s.get("messages", [])
            last_msg = ""
            last_msg_time = s.get("created_at") or s.get("started_at")
            if messages and len(messages) > 0:
                last_item = messages[-1]
                last_msg = last_item.get("message") or last_item.get("content") or ("[Photo]" if last_item.get("message_type") == "image" else "")
                last_msg_time = last_item.get("timestamp") or last_msg_time
            else:
                db_msgs = await db.messages.find({"conversation_id": s["id"]}).sort("created_at", -1).to_list(1)
                if db_msgs:
                    m = db_msgs[0]
                    last_msg = m.get("content") or ("[Photo]" if m.get("image_url") else "")
                    last_msg_time = m.get("created_at")
            
            if not last_msg:
                s_type = (s.get("session_type") or "chat").title()
                last_msg = f"{s_type} Session"

            psychic_name = (psy.get("name") if psy else s.get("psychic_name")) or "Psychic Advisor"
            psychic_avatar = (psy.get("profile_picture") or psy.get("avatar") if psy else None) or "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150"

            result.append({
                "id": s["id"],
                "psychic_id": psy_id or "",
                "psychic_name": psychic_name,
                "psychic_avatar": psychic_avatar,
                "last_message": last_msg,
                "last_message_time": str(last_msg_time) if last_msg_time else None,
                "total_duration": s.get("duration_seconds") or (int(s.get("total_minutes") or s.get("auto_end_minutes") or 1) * 60),
                "total_cost": float(s.get("total_cost") or 0.0),
                "status": "completed" if s.get("status") in ("ended", "completed") else "active",
                "created_at": str(s.get("created_at") or s.get("started_at") or datetime.utcnow().isoformat()),
            })
        return result
    
    return router
