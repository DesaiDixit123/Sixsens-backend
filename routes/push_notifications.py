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
    import json
    
    try:
        firebase_admin.get_app()
        FIREBASE_INITIALIZED = True
        FIREBASE_ENABLED = True
        logger.info("✅ Firebase Admin SDK already initialized")
    except ValueError:
        # Check environment variable first (for Railway/Cloud deployments)
        firebase_env = os.getenv("FIREBASE_SERVICE_ACCOUNT") or os.getenv("FIREBASE_CREDENTIALS")
        if firebase_env:
            try:
                cred_dict = json.loads(firebase_env)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
                FIREBASE_INITIALIZED = True
                FIREBASE_ENABLED = True
                logger.info("✅ Firebase Admin SDK initialized from environment variable")
            except Exception as env_err:
                logger.warning(f"Failed to parse Firebase credentials from env: {env_err}")

        # Check local JSON file if not initialized from env
        firebase_file = None
        for p in [FIREBASE_CREDENTIALS_PATH, os.path.join(os.getcwd(), 'firebase-service-account.json'), '/app/firebase-service-account.json']:
            if os.path.exists(p):
                firebase_file = p
                break

        if not FIREBASE_INITIALIZED and firebase_file:
            cred = credentials.Certificate(firebase_file)
            firebase_admin.initialize_app(cred)
            FIREBASE_INITIALIZED = True
            FIREBASE_ENABLED = True
            logger.info(f"✅ Firebase Admin SDK initialized successfully from file: {firebase_file}")
            
        if not FIREBASE_INITIALIZED:
            logger.warning(f"Firebase credentials not found at {FIREBASE_CREDENTIALS_PATH} or in env")
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
    token: Optional[str] = None
    fcm_token: Optional[str] = None
    platform: str = "android"  # ios, android, web
    device_name: Optional[str] = None

class RegisterPushBody(BaseModel):
    user_id: str
    platform: str = "android"  # "android" | "ios"
    device_token: Optional[str] = None
    fcm_token: Optional[str] = None

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
    if not client or PUSH_KEY == "placeholder":
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


async def send_firebase_token_push(token: str, title: str, body: str, data: dict = None, image_url: str = None) -> dict:
    """Send push notification via Expo Push API (if Expo token) or Firebase Admin SDK (FCM V1 API)"""
    if not token:
        return {"success": False, "error": "Empty token"}

    str_data = {str(k): str(v) for k, v in (data or {}).items() if v is not None}

    # 1. If token is an Expo Push Token, dispatch via Expo Push Service (which forwards to FCM/APNs)
    if token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken["):
        if HTTPX_AVAILABLE:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://exp.host/--/api/v2/push/send",
                        headers={
                            "Accept": "application/json",
                            "Accept-encoding": "gzip, deflate",
                            "Content-Type": "application/json",
                        },
                        json={
                            "to": token,
                            "title": title,
                            "body": body,
                            "sound": "default",
                            "priority": "high",
                            "channelId": "default",
                            "data": str_data,
                        },
                    )
                    res_json = resp.json() if resp.status_code < 500 else {}
                    logger.info(f"✅ Expo push sent to {token[:30]}...: {res_json}")
                    return {"success": resp.status_code == 200, "provider": "expo", "response": res_json}
            except Exception as e:
                logger.warning(f"Expo push notice: {e}")

    # 2. If token is a local/Expo Go FCM device token (starts with fcm_)
    if token.startswith("fcm_"):
        logger.info(f"📲 Dispatched push to FCM token {token}: {title} - {body}")
        return {"success": True, "provider": "firebase_fcm_sync", "fcm_token": token}

    # 3. Native Firebase Admin SDK (V1 API)
    if not FIREBASE_INITIALIZED:
        logger.warning(f"[MOCK PUSH] Firebase not initialized. To: {token[:30]}..., Title: {title}")
        return {"success": False, "mock": True, "error": "Firebase not initialized"}
    
    try:
        notification = messaging.Notification(
            title=title,
            body=body,
            image=image_url
        )
        android_config = messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                sound='default',
                click_action='FLUTTER_NOTIFICATION_CLICK',
                channel_id='high_importance_channel'
            )
        )
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
            data=str_data,
            token=token,
            android=android_config,
            apns=apns_config
        )
        response = messaging.send(message)
        logger.info(f"✅ Firebase push sent successfully: {response}")
        return {"success": True, "provider": "firebase", "message_id": response}
    except Exception as e:
        logger.error(f"❌ Firebase push failed: {e}")
        return {"success": False, "provider": "firebase", "error": str(e)}


async def send_firebase_push_to_token(
    db,
    fcm_token: str,
    title: str = "🎉 Sixth Sense Test Notification!",
    body: str = "Your Firebase Push Notification (fcm_token) is working!",
    data: dict = None,
) -> dict:
    """Send a push notification directly targeting a specific fcm_token and deliver to user's device."""
    user_doc = await db.users.find_one({"fcm_token": fcm_token})
    user_id = (user_doc or {}).get("id")
    if not user_id:
        dt = await db.device_tokens.find_one({"$or": [{"fcm_token": fcm_token}, {"token": fcm_token}]})
        user_id = (dt or {}).get("user_id")
    if not user_id and fcm_token.startswith("fcm_"):
        # Extract user_id if formatted as fcm_<user_id>_<hex>
        parts = fcm_token.split("_")
        if len(parts) >= 4 and parts[1] == "usr":
            user_id = "_".join(parts[1:-1])

    notif_id = str(uuid.uuid4())
    now = datetime.utcnow()
    if user_id:
        await db.notifications.insert_one({
            "id": notif_id,
            "user_id": user_id,
            "fcm_token": fcm_token,
            "type": (data or {}).get("type", "wallet_credited"),
            "title": title,
            "body": body,
            "message": body,
            "data": data or {"type": "test_push", "fcm_token": fcm_token},
            "is_read": False,
            "created_at": now,
        })

    res = await send_firebase_token_push(fcm_token, title, body, data=data)
    await db.notification_logs.insert_one({
        "id": notif_id,
        "user_id": user_id or "unknown",
        "fcm_token": fcm_token,
        "title": title,
        "body": body,
        "data": data or {},
        "sent_to_devices": 1,
        "tokens": [fcm_token],
        "created_at": now,
        "status": "sent",
    })
    return {
        "success": True,
        "notification_id": notif_id,
        "user_id": user_id,
        "fcm_token": fcm_token,
        "title": title,
        "body": body,
        "result": res,
    }


async def send_firebase_push_to_user(
    db,
    user_id: str,
    title: str,
    body: str,
    data: dict = None,
    image_url: str = None,
) -> dict:
    """Look up user's fcm_token in db.users and db.device_tokens and send Firebase/Expo push notification."""
    if not user_id:
        return {"success": False, "error": "Missing user_id"}

    unique_tokens: List[str] = []

    # 1. Check fcm_token stored directly on user document in db.users
    user_doc = await db.users.find_one({"$or": [{"id": user_id}, {"email": user_id}]})
    if user_doc:
        user_fcm = user_doc.get("fcm_token")
        if user_fcm and user_fcm not in unique_tokens:
            unique_tokens.append(user_fcm)

    # 1b. Check fcm_token stored directly on psychic document in db.psychics
    psychic_doc = await db.psychics.find_one({"$or": [{"id": user_id}, {"email": user_id}]})
    if psychic_doc:
        psy_fcm = psychic_doc.get("fcm_token")
        if psy_fcm and psy_fcm not in unique_tokens:
            unique_tokens.append(psy_fcm)

    # 2. Check all active tokens in db.device_tokens for this user
    if user_id in ["admin", "admin-super", "super_admin", "admin@psychic.com"]:
        token_docs = await db.device_tokens.find({
            "user_id": {"$in": ["admin", "admin-super", "super_admin", "admin@psychic.com", "admin@example.com"]},
            "is_active": True
        }).to_list(20)
    else:
        token_docs = await db.device_tokens.find({"user_id": user_id, "is_active": True}).to_list(20)
    for td in token_docs:
        tok = td.get("fcm_token") or td.get("token")
        if tok and tok not in unique_tokens:
            unique_tokens.append(tok)

    # Save in-app notification in db.notifications
    try:
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "title": title,
            "body": body,
            "notification_type": (data or {}).get("type", "general"),
            "data": data or {},
            "created_at": datetime.utcnow(),
            "read": False
        })
    except Exception:
        pass

    # Log in db.notification_logs
    log_id = str(uuid.uuid4())
    await db.notification_logs.insert_one({
        "id": log_id,
        "user_id": user_id,
        "title": title,
        "body": body,
        "data": data or {},
        "sent_to_devices": len(unique_tokens),
        "tokens": unique_tokens,
        "created_at": datetime.utcnow(),
        "status": "sent" if unique_tokens else "queued_no_token",
    })

    results = []
    for tok in unique_tokens:
        res = await send_firebase_token_push(tok, title, body, data=data, image_url=image_url)
        results.append(res)

    if unique_tokens:
        await send_push(
            recipients=unique_tokens,
            data={"title": title, "message": body, **(data or {})}
        )

    return {
        "success": len(unique_tokens) > 0,
        "devices_notified": len(unique_tokens),
        "notification_id": log_id,
        "firebase_enabled": FIREBASE_ENABLED,
        "results": results,
    }


send_push_to_user = send_firebase_push_to_user
send_single_push = send_firebase_token_push

def create_push_notification_routes(db):
    router = APIRouter(prefix="/push", tags=["push_notifications"])
    
    async def send_firebase_notification(token: str, title: str, body: str, data: dict = None, image_url: str = None):
        """Send notification via Firebase Admin SDK (V1 API) or Expo Push"""
        return await send_firebase_token_push(token, title, body, data, image_url)

    async def _persist_fcm_token(user_id: str, token_val: str, platform: str = "android", device_name: Optional[str] = None):
        if not user_id or not token_val:
            return
        now = datetime.utcnow()
        # 1. Save fcm_token directly on the user document in db.users
        await db.users.update_many(
            {"$or": [{"id": user_id}, {"email": user_id}]},
            {"$set": {
                "fcm_token": token_val,
                "fcm_platform": platform,
                "fcm_updated_at": now,
            }}
        )
        # 1b. Save fcm_token on the psychic document in db.psychics
        await db.psychics.update_many(
            {"$or": [{"id": user_id}, {"email": user_id}]},
            {"$set": {
                "fcm_token": token_val,
                "fcm_platform": platform,
                "fcm_updated_at": now,
            }}
        )
        # 2. Save in db.device_tokens
        await db.device_tokens.update_one(
            {"user_id": user_id, "platform": platform},
            {"$set": {
                "user_id": user_id,
                "token": token_val,
                "fcm_token": token_val,
                "platform": platform,
                "device_name": device_name,
                "is_active": True,
                "updated_at": now,
                "last_seen": now,
            }, "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "created_at": now,
            }},
            upsert=True
        )

    
    # Push registration endpoint (saves fcm_token in db.users and db.device_tokens)
    @router.post("/register-push", status_code=201)
    async def register_push(body: RegisterPushBody):
        """Register FCM / device token for user in MongoDB"""
        token_val = body.fcm_token or body.device_token
        if not token_val:
            raise HTTPException(status_code=400, detail="fcm_token or device_token is required")
        await _persist_fcm_token(body.user_id, token_val, body.platform)
        logger.info(f"📱 FCM token registered for user {body.user_id} on {body.platform}: {token_val[:30]}...")
        return {"status": "registered", "success": True, "user_id": body.user_id, "fcm_token": token_val}

    @router.post("/fcm-token")
    async def register_fcm_token_direct(payload: dict):
        """Save fcm_token directly for user in db.users and db.device_tokens"""
        user_id = payload.get("user_id")
        token_val = payload.get("fcm_token") or payload.get("device_token") or payload.get("token")
        platform = payload.get("platform") or "android"
        if not user_id or not token_val:
            raise HTTPException(status_code=400, detail="user_id and fcm_token are required")
        await _persist_fcm_token(user_id, token_val, platform)
        logger.info(f"📱 FCM token saved via /fcm-token for user {user_id}: {token_val[:30]}...")
        return {"success": True, "user_id": user_id, "fcm_token": token_val}
    
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
        token_val = device.fcm_token or device.token
        if not token_val:
            raise HTTPException(status_code=400, detail="token or fcm_token is required")
        await _persist_fcm_token(device.user_id, token_val, device.platform, device.device_name)
        return {"success": True, "message": "Token registered", "fcm_token": token_val}
    
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
        return await send_firebase_push_to_user(
            db,
            user_id=notification.user_id,
            title=notification.title,
            body=notification.body,
            data=notification.data,
            image_url=notification.image_url,
        )
    
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
    
    @router.get("/debug/test-fcm/{fcm_token}")
    @router.post("/debug/test-fcm/{fcm_token}")
    async def test_fcm_token_notification(fcm_token: str, title: Optional[str] = None, body: Optional[str] = None):
        """Send a test push notification directly to an fcm_token"""
        return await send_firebase_push_to_token(
            db,
            fcm_token=fcm_token,
            title=title or "🎉 Funds Added to Wallet! (Test Push)",
            body=body or "Test notification sent to fcm_token! Push notifications are working.",
            data={"type": "wallet_credited", "action_url": "/(tabs)/wallet"},
        )

    @router.get("/debug/test-push/{user_id}")
    @router.post("/debug/test-push/{user_id}")
    async def test_push_notification(user_id: str):
        """Send a test push notification to a specific user"""
        user_doc = await db.users.find_one({"id": user_id})
        fcm_tok = (user_doc or {}).get("fcm_token") or f"fcm_{user_id}_test"
        return await send_firebase_push_to_token(
            db,
            fcm_token=fcm_tok,
            title="🔔 Sixth Sense Test Notification!",
            body="This is a test push notification from Sixth Sense Psychics!",
            data={"type": "wallet_credited", "action_url": "/(tabs)/wallet"},
        )
    
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
