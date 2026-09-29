from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import os
import json
import logging

logger = logging.getLogger(__name__)

# Firebase Admin SDK setup
FIREBASE_CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), '..', 'firebase-service-account.json')
FIREBASE_INITIALIZED = False
FIREBASE_ENABLED = False  # Alias for FIREBASE_INITIALIZED

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    
    if os.path.exists(FIREBASE_CREDENTIALS_PATH):
        # Check if already initialized (to avoid multiple initializations)
        try:
            firebase_admin.get_app()
            FIREBASE_INITIALIZED = True
            FIREBASE_ENABLED = True
            logger.info("✅ Firebase Admin SDK already initialized")
        except ValueError:
            # Not initialized, initialize now
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
            FIREBASE_INITIALIZED = True
            FIREBASE_ENABLED = True
            logger.info("✅ Firebase Admin SDK initialized successfully")
    else:
        logger.warning(f"Firebase credentials not found at {FIREBASE_CREDENTIALS_PATH}")
except Exception as e:
    logger.warning(f"Firebase Admin SDK not initialized: {e}")

# Emergent Push configuration (fallback)
PUSH_BASE_URL = "https://integrations.emergentagent.com"
PUSH_KEY = os.environ.get("EMERGENT_PUSH_KEY", "placeholder")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Create shared httpx client for Emergent Push
_push_client = None

async def get_push_client():
    global _push_client
    if _push_client is None and HTTPX_AVAILABLE:
        _push_client = httpx.AsyncClient(
            base_url=PUSH_BASE_URL,
            headers={"X-Push-Key": PUSH_KEY},
            timeout=10.0,
        )
    return _push_client

class DeviceToken(BaseModel):
    user_id: str
    token: str
    platform: str = "unknown"  # ios, android, web
    device_name: Optional[str] = None

class RegisterPushBody(BaseModel):
    user_id: str
    platform: str  # "android" | "ios"
    device_token: str

class PushNotification(BaseModel):
    user_id: str
    title: str
    body: str
    data: Optional[Dict[str, Any]] = None
    image_url: Optional[str] = None
    sound: str = "default"
    badge: Optional[int] = None

class BroadcastNotification(BaseModel):
    title: str
    body: str
    data: Optional[Dict[str, Any]] = None
    topic: str = "all_users"

async def send_push(
    recipients: List[str],
    data: dict,  # {title, message, subtext?, image_url?, action_url?}
    idempotency_key: str = None,
) -> None:
    """Send push notification via Emergent managed push service"""
    if not recipients:
        return
    if len(recipients) > 100:
        raise ValueError("max 100 recipients per /trigger call; chunk before sending")
    if "title" not in data or "message" not in data:
        raise ValueError("data must include title and message")
    
    client = await get_push_client()
    if not client:
        logger.warning("Push client not available, skipping push notification")
        return
    
    payload = {"recipients": recipients, "data": data}
    if idempotency_key:
        payload["$idempotency_key"] = idempotency_key
    
    try:
        resp = await client.post("/api/v1/push/trigger", json=payload)
        if resp.status_code == 401:
            logger.error("EMERGENT_PUSH_KEY missing or invalid")
            return
        if resp.status_code >= 500:
            logger.error("Push provider unavailable")
            return
        resp.raise_for_status()
        logger.info(f"Push notification sent to {len(recipients)} recipients")
    except Exception as e:
        logger.warning(f"Push notification failed (non-blocking): {e}")

def create_push_notification_routes(db):
    router = APIRouter(prefix="/push", tags=["push_notifications"])
    
    async def send_firebase_notification(token: str, title: str, body: str, data: dict = None, image_url: str = None):
        """Send notification via Firebase Admin SDK (V1 API)"""
        if not FIREBASE_INITIALIZED:
            logger.warning(f"[MOCK PUSH] Firebase not initialized. To: {token[:30]}..., Title: {title}")
            return {"success": False, "mock": True, "error": "Firebase not initialized"}
        
        try:
            # Build the message
            notification = messaging.Notification(
                title=title,
                body=body,
                image=image_url
            )
            
            # Android specific config
            android_config = messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    sound='default',
                    click_action='FLUTTER_NOTIFICATION_CLICK',
                    channel_id='high_importance_channel'
                )
            )
            
            # iOS specific config
            apns_config = messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound='default',
                        badge=1,
                        content_available=True
                    )
                )
            )
            
            message = messaging.Message(
                notification=notification,
                data=data or {},
                token=token,
                android=android_config,
                apns=apns_config
            )
            
            # Send the message
            response = messaging.send(message)
            logger.info(f"✅ Firebase push sent successfully: {response}")
            return {"success": True, "message_id": response}
            
        except Exception as e:
            logger.error(f"❌ Firebase push failed: {e}")
            return {"success": False, "error": str(e)}
    
    # Emergent Push registration endpoint
    @router.post("/register-push", status_code=201)
    async def register_push(body: RegisterPushBody):
        """Register device token with Emergent Push service"""
        # Store token locally for Firebase
        await db.device_tokens.update_one(
            {"user_id": body.user_id, "platform": body.platform},
            {"$set": {
                "user_id": body.user_id,
                "token": body.device_token,
                "platform": body.platform,
                "is_active": True,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )
        logger.info(f"📱 Device token registered for user {body.user_id} on {body.platform}")
        return {"status": "registered"}
    
    async def send_fcm_notification(token: str, title: str, body: str, data: dict = None, image_url: str = None):
        """Send notification via Firebase (wrapper for backward compatibility)"""
        return await send_firebase_notification(token, title, body, data, image_url)
    
    @router.get("/config")
    async def get_push_config():
        """Get push notification configuration"""
        return {
            "firebase_enabled": FIREBASE_ENABLED,
            "emergent_push_enabled": PUSH_KEY != "placeholder",
            "supported_platforms": ["ios", "android", "web"]
        }
    
    @router.post("/register-token")
    async def register_device_token(device: DeviceToken):
        """Register a device token for push notifications"""
        # Check if token already exists
        existing = await db.device_tokens.find_one({
            "user_id": device.user_id,
            "token": device.token
        })
        
        if existing:
            # Update last seen
            await db.device_tokens.update_one(
                {"_id": existing["_id"]},
                {"$set": {"last_seen": datetime.utcnow()}}
            )
            return {"success": True, "message": "Token updated"}
        
        # Insert new token
        token_doc = {
            "id": str(uuid.uuid4()),
            "user_id": device.user_id,
            "token": device.token,
            "platform": device.platform,
            "device_name": device.device_name,
            "created_at": datetime.utcnow(),
            "last_seen": datetime.utcnow(),
            "is_active": True
        }
        await db.device_tokens.insert_one(token_doc)
        
        return {"success": True, "message": "Token registered"}
    
    @router.delete("/unregister-token")
    async def unregister_device_token(user_id: str, token: str):
        """Unregister a device token"""
        await db.device_tokens.delete_one({
            "user_id": user_id,
            "token": token
        })
        return {"success": True, "message": "Token unregistered"}
    
    @router.post("/send")
    async def send_push_notification(notification: PushNotification, background_tasks: BackgroundTasks):
        """Send a push notification to a user"""
        # Get user's device tokens
        tokens = await db.device_tokens.find({
            "user_id": notification.user_id,
            "is_active": True
        }).to_list(10)
        
        if not tokens:
            return {"success": False, "message": "No device tokens found for user"}
        
        # Log notification
        notification_log = {
            "id": str(uuid.uuid4()),
            "user_id": notification.user_id,
            "title": notification.title,
            "body": notification.body,
            "data": notification.data,
            "sent_to_devices": len(tokens),
            "created_at": datetime.utcnow(),
            "status": "sent"
        }
        await db.notification_logs.insert_one(notification_log)
        
        # Send to all devices
        results = []
        for token_doc in tokens:
            result = await send_fcm_notification(
                token_doc["token"],
                notification.title,
                notification.body,
                notification.data,
                notification.image_url
            )
            results.append(result)
        
        return {
            "success": True,
            "devices_notified": len(tokens),
            "notification_id": notification_log["id"],
            "firebase_enabled": FIREBASE_ENABLED
        }
    
    @router.get("/debug/status")
    async def get_push_debug_status():
        """Get detailed push notification status for debugging"""
        # Count device tokens
        total_tokens = await db.device_tokens.count_documents({})
        active_tokens = await db.device_tokens.count_documents({"is_active": True})
        
        # Get sample tokens (for debugging)
        sample_tokens = await db.device_tokens.find({}).limit(5).to_list(5)
        sample_info = []
        for t in sample_tokens:
            sample_info.append({
                "user_id": t.get("user_id", "")[:20] + "..." if t.get("user_id") else "N/A",
                "platform": t.get("platform"),
                "is_active": t.get("is_active"),
                "token_preview": t.get("token", "")[:30] + "..." if t.get("token") else "N/A",
                "updated_at": str(t.get("updated_at", "N/A"))
            })
        
        return {
            "firebase_initialized": FIREBASE_INITIALIZED,
            "firebase_enabled": FIREBASE_ENABLED,
            "firebase_credentials_path": FIREBASE_CREDENTIALS_PATH,
            "firebase_credentials_exists": os.path.exists(FIREBASE_CREDENTIALS_PATH),
            "total_device_tokens": total_tokens,
            "active_device_tokens": active_tokens,
            "sample_tokens": sample_info,
            "emergent_push_key_set": PUSH_KEY != "placeholder"
        }
    
    @router.get("/debug/users-with-tokens")
    async def get_users_with_tokens():
        """Debug: Show which users have device tokens registered"""
        # Get all device tokens
        all_tokens = await db.device_tokens.find({"is_active": True}).to_list(100)
        token_user_ids = set(t.get("user_id") for t in all_tokens if t.get("user_id"))
        
        # Get all users
        all_users = await db.users.find({}).to_list(100)
        user_info = []
        for u in all_users[:10]:  # Limit to 10 for debug
            uid = u.get("id", "")
            user_info.append({
                "user_id": uid[:30] + "..." if len(uid) > 30 else uid,
                "email": u.get("email", "N/A"),
                "has_token": uid in token_user_ids
            })
        
        return {
            "total_users": len(all_users),
            "total_tokens": len(all_tokens),
            "users_with_tokens": len([u for u in user_info if u.get("has_token")]),
            "sample_users": user_info,
            "token_user_ids": [t[:30] + "..." for t in list(token_user_ids)[:5]]
        }
    
    @router.post("/debug/test-push/{user_id}")
    async def test_push_notification(user_id: str):
        """Send a test push notification to a specific user"""
        # Get user's device tokens
        tokens = await db.device_tokens.find({
            "user_id": user_id,
            "is_active": True
        }).to_list(10)
        
        if not tokens:
            return {
                "success": False,
                "error": "No active device tokens found for this user",
                "firebase_initialized": FIREBASE_INITIALIZED
            }
        
        results = []
        for token_doc in tokens:
            result = await send_firebase_notification(
                token_doc["token"],
                "🔔 Test Notification",
                "This is a test push notification from Sixth Sense Psychics!",
                {"type": "test", "timestamp": str(datetime.utcnow())}
            )
            results.append({
                "platform": token_doc.get("platform"),
                "result": result
            })
        
        return {
            "success": True,
            "firebase_initialized": FIREBASE_INITIALIZED,
            "tokens_found": len(tokens),
            "results": results
        }
    
    @router.post("/debug/test-all-tokens")
    async def test_all_tokens():
        """Send test push to ALL active device tokens"""
        all_tokens = await db.device_tokens.find({"is_active": True}).to_list(100)
        
        if not all_tokens:
            return {
                "success": False,
                "error": "No active device tokens found",
                "firebase_initialized": FIREBASE_INITIALIZED
            }
        
        results = []
        for token_doc in all_tokens:
            result = await send_firebase_notification(
                token_doc["token"],
                "🔔 Direct Test Push",
                "If you see this banner, push notifications are WORKING!",
                {"type": "test", "timestamp": str(datetime.utcnow())}
            )
            results.append({
                "platform": token_doc.get("platform"),
                "user_id_preview": token_doc.get("user_id", "")[:20] + "...",
                "result": result
            })
        
        return {
            "success": True,
            "firebase_initialized": FIREBASE_INITIALIZED,
            "tokens_tested": len(all_tokens),
            "results": results
        }
    
    async def notify_psychic_new_client(psychic_id: str, client_name: str, session_type: str):
        """Send notification to psychic about new client"""
        notification = PushNotification(
            user_id=psychic_id,
            title="New Client Request! 🌟",
            body=f"{client_name} wants to start a {session_type} session with you.",
            data={
                "type": "new_client_request",
                "client_name": client_name,
                "session_type": session_type
            }
        )
        
        return await send_push_notification(notification, BackgroundTasks())
    
    @router.post("/send-session-reminder")
    async def send_session_reminder(user_id: str, psychic_name: str, minutes_until: int):
        """Send session reminder notification"""
        notification = PushNotification(
            user_id=user_id,
            title="Session Reminder ⏰",
            body=f"Your reading with {psychic_name} starts in {minutes_until} minutes.",
            data={
                "type": "session_reminder",
                "psychic_name": psychic_name,
                "minutes_until": minutes_until
            }
        )
        
        return await send_push_notification(notification, BackgroundTasks())
    
    @router.post("/send-new-message")
    async def send_new_message_notification(user_id: str, sender_name: str, message_preview: str):
        """Send new message notification"""
        notification = PushNotification(
            user_id=user_id,
            title=f"New message from {sender_name}",
            body=message_preview[:100] + ("..." if len(message_preview) > 100 else ""),
            data={
                "type": "new_message",
                "sender_name": sender_name
            }
        )
        
        return await send_push_notification(notification, BackgroundTasks())
    
    @router.get("/tokens/{user_id}")
    async def get_user_tokens(user_id: str):
        """Get user's registered device tokens"""
        tokens = await db.device_tokens.find(
            {"user_id": user_id},
            {"token": 0}  # Don't expose actual tokens
        ).to_list(10)
        
        return {
            "user_id": user_id,
            "device_count": len(tokens),
            "devices": [{
                "platform": t.get("platform"),
                "device_name": t.get("device_name"),
                "last_seen": t.get("last_seen").isoformat() if t.get("last_seen") else None
            } for t in tokens]
        }
    
    @router.get("/history/{user_id}")
    async def get_notification_history(user_id: str, limit: int = 20):
        """Get user's notification history"""
        logs = await db.notification_logs.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        for log in logs:
            if "_id" in log:
                del log["_id"]
            if log.get("created_at"):
                log["created_at"] = log["created_at"].isoformat()
        
        return logs
    
    return router
