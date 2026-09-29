from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid
import os
import time
import logging
import cloudinary
import cloudinary.uploader
import cloudinary.api
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Firebase for push notifications
FIREBASE_INITIALIZED = False
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    
    FIREBASE_CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), '..', 'firebase-service-account.json')
    if os.path.exists(FIREBASE_CREDENTIALS_PATH):
        try:
            firebase_admin.get_app()
            FIREBASE_INITIALIZED = True
        except ValueError:
            pass
        if not FIREBASE_INITIALIZED:
            try:
                cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
                FIREBASE_INITIALIZED = True
            except:
                pass
except Exception as e:
    logger.warning(f"Firebase not available for call notifications: {e}")

# Agora configuration
AGORA_APP_ID = os.getenv("AGORA_APP_ID")
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE")
AGORA_ENABLED = bool(AGORA_APP_ID and AGORA_APP_CERTIFICATE)

# Import Agora token builder
if AGORA_ENABLED:
    try:
        from agora_token_builder import RtcTokenBuilder
        print(f"[OK] Agora configured - App ID: {AGORA_APP_ID[:8]}...")
    except ImportError:
        print("[WARN] agora-token-builder not installed, Agora tokens will be mocked")
        RtcTokenBuilder = None
else:
    RtcTokenBuilder = None
    print("[WARN] Agora credentials not configured")

# Cloudinary configuration for video storage
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
CLOUDINARY_ENABLED = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)

if CLOUDINARY_ENABLED:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
    print("✅ Cloudinary configured for video storage")

class VideoCallRequest(BaseModel):
    caller_id: str
    callee_id: str
    call_type: str = "video"  # video, voice
    psychic_id: Optional[str] = None
    caller_name: Optional[str] = None  # For push notification

class JoinCallRequest(BaseModel):
    channel_name: str
    user_id: str
    role: str = "publisher"  # publisher, subscriber

class AgoraTokenRequest(BaseModel):
    channel_name: str
    uid: int
    role: str = "publisher"  # publisher, subscriber
    expire_seconds: int = 3600

async def send_call_notification(db, callee_id: str, caller_name: str, call_type: str, call_id: str, channel_name: str):
    """Send push notification to callee about incoming call"""
    if not FIREBASE_INITIALIZED:
        logger.warning(f"Firebase not initialized, cannot send call notification to {callee_id}")
        return False
    
    try:
        # Get callee's device tokens
        tokens = await db.device_tokens.find({
            "user_id": callee_id,
            "is_active": True
        }).to_list(10)
        
        if not tokens:
            logger.warning(f"No device tokens found for user {callee_id}")
            return False
        
        call_type_display = "Video" if call_type == "video" else "Voice"
        
        for token_doc in tokens:
            try:
                notification = messaging.Notification(
                    title=f"Incoming {call_type_display} Call",
                    body=f"{caller_name} is calling you"
                )
                
                # Data payload for the app to handle
                data = {
                    "type": "incoming_call",
                    "call_id": call_id,
                    "call_type": call_type,
                    "caller_name": caller_name,
                    "channel_name": channel_name
                }
                
                # iOS config with high priority and APNs badge (BE-063 & BE-065)
                apns_config = messaging.APNSConfig(
                    headers={
                        "apns-priority": "10",
                        "apns-push-type": "alert"
                    },
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="incoming_call_ringtone.mp3",
                            badge=1,
                            content_available=True,
                            category="INCOMING_CALL"
                        )
                    )
                )
                
                # Android config with high priority and Full-Screen Intent payload (BE-064 & BE-065)
                android_config = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="incoming_call_ringtone.mp3",
                        channel_id="incoming_calls",
                        priority="max"
                    ),
                    data={"full_screen_intent": "true", "screen": "IncomingCall", "params": json.dumps({"call_id": call_id})}
                )
                
                message = messaging.Message(
                    notification=notification,
                    data=data,
                    token=token_doc["token"],
                    apns=apns_config,
                    android=android_config
                )
                
                response = messaging.send(message)
                logger.info(f"✅ Call notification sent to {callee_id}: {response}")
                
            except Exception as e:
                # Cleanup invalid FCM tokens (BE-061)
                err_str = str(e)
                if "NotRegistered" in err_str or "Unregistered" in err_str or "invalid" in err_str.lower():
                    await db.device_tokens.delete_one({"token": token_doc["token"]})
                    logger.info(f"🧹 Deleted invalid FCM token: {token_doc['token'][:10]}...")
                else:
                    logger.error(f"Failed to send call notification to token: {e}")
                continue
        
        return True
        
    except Exception as e:
        logger.error(f"Error sending call notification: {e}")
        return False

def generate_agora_token(channel_name: str, uid: int, role: int = 1, expire_time: int = 3600):
    """Generate Agora RTC token using official agora-token-builder"""
    if not AGORA_ENABLED or not RtcTokenBuilder:
        return None
    
    try:
        current_time = int(time.time())
        privilege_expired_ts = current_time + expire_time
        
        # Role: 1 = publisher (can send audio/video), 2 = subscriber (receive only)
        token = RtcTokenBuilder.buildTokenWithUid(
            AGORA_APP_ID,
            AGORA_APP_CERTIFICATE,
            channel_name,
            uid,
            role,
            privilege_expired_ts
        )
        
        print(f"✅ Generated Agora token for channel={channel_name}, uid={uid}, role={role}")
        return token
    except Exception as e:
        print(f"❌ Error generating Agora token: {e}")
        return None

def create_video_routes(db):
    router = APIRouter(prefix="/video", tags=["video"])
    
    @router.get("/config")
    async def get_video_config():
        """Get video call configuration"""
        return {
            "agora_enabled": AGORA_ENABLED,
            "cloudinary_enabled": CLOUDINARY_ENABLED,
            "app_id": AGORA_APP_ID if AGORA_ENABLED else "mock_app_id",
            "features": {
                "video_call": True,
                "voice_call": True,
                "screen_share": False,
                "recording": AGORA_ENABLED,
                "cloud_storage": CLOUDINARY_ENABLED
            }
        }
    
    @router.post("/token")
    async def get_agora_token(request: AgoraTokenRequest):
        """Generate Agora RTC token for joining a channel"""
        if not AGORA_ENABLED:
            return {
                "token": f"mock_token_{request.channel_name}_{request.uid}",
                "uid": request.uid,
                "channel_name": request.channel_name,
                "app_id": "mock_app_id",
                "agora_enabled": False
            }
        
        # Role: 1 = publisher (can send audio/video), 2 = subscriber (receive only)
        role = 1 if request.role == "publisher" else 2
        token = generate_agora_token(
            request.channel_name, 
            request.uid, 
            role, 
            request.expire_seconds
        )
        
        if not token:
            raise HTTPException(status_code=500, detail="Failed to generate Agora token")
        
        return {
            "token": token,
            "uid": request.uid,
            "channel_name": request.channel_name,
            "app_id": AGORA_APP_ID,
            "agora_enabled": True,
            "expires_in": request.expire_seconds
        }
    
    @router.post("/initiate-call")
    async def initiate_call(request: VideoCallRequest):
        """Initiate a video/voice call (BE-047: Cryptographically secure UUID channel name)"""
        channel_name = f"ch_{uuid.uuid4().hex}"
        
        # Generate consistent UIDs from user IDs (positive integers required by Agora)
        caller_uid = abs(hash(request.caller_id)) % 100000 + 1
        callee_uid = abs(hash(request.callee_id)) % 100000 + 1
        
        if caller_uid == callee_uid:
            callee_uid = callee_uid + 1
        
        call = {
            "id": str(uuid.uuid4()),
            "channel_name": channel_name,
            "caller_id": request.caller_id,
            "callee_id": request.callee_id,
            "caller_uid": caller_uid,
            "callee_uid": callee_uid,
            "psychic_id": request.psychic_id,
            "call_type": request.call_type,
            "status": "ringing",
            "created_at": datetime.utcnow(),
            "started_at": None,
            "ended_at": None,
            "duration_seconds": 0
        }
        await db.calls.insert_one(call)
        
        # Generate tokens
        caller_token = None
        callee_token = None
        
        if AGORA_ENABLED and RtcTokenBuilder:
            caller_token = generate_agora_token(channel_name, caller_uid)
            callee_token = generate_agora_token(channel_name, callee_uid)
        
        # Get caller name for notification
        caller_name = request.caller_name or "Someone"
        if not request.caller_name:
            # Try to get caller name from database
            try:
                caller = await db.users.find_one({"id": request.caller_id})
                if caller:
                    caller_name = caller.get("name") or caller.get("email", "Someone").split("@")[0]
            except:
                pass
        
        # Send push notification to callee (advisor)
        await send_call_notification(
            db=db,
            callee_id=request.callee_id,
            caller_name=caller_name,
            call_type=request.call_type,
            call_id=call["id"],
            channel_name=channel_name
        )
        
        return {
            "call_id": call["id"],
            "channel_name": channel_name,
            "app_id": AGORA_APP_ID or "mock_app_id",
            "caller_token": caller_token or f"mock_token_{channel_name}_caller",
            "callee_token": callee_token or f"mock_token_{channel_name}_callee",
            "caller_uid": caller_uid,
            "callee_uid": callee_uid,
            "agora_enabled": AGORA_ENABLED
        }
    
    @router.post("/join")
    async def join_call(request: JoinCallRequest):
        """Get token to join an existing call"""
        token = None
        uid = hash(request.user_id) % 100000
        
        if AGORA_ENABLED:
            role = 1 if request.role == "publisher" else 2
            token = generate_agora_token(request.channel_name, uid, role)
        
        return {
            "token": token or f"mock_token_{request.channel_name}_{request.user_id}",
            "uid": uid,
            "channel_name": request.channel_name,
            "app_id": AGORA_APP_ID or "mock_app_id"
        }
    
    @router.put("/call/{call_id}/answer")
    async def answer_call(call_id: str):
        """Answer an incoming call"""
        result = await db.calls.update_one(
            {"id": call_id},
            {
                "$set": {
                    "status": "connected",
                    "started_at": datetime.utcnow()
                }
            }
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Call not found")
        
        return {"success": True, "status": "connected"}

    # ============ RTC UTILITIES & ACTIVE SESSION RECOVERY (BE-055, BE-058, BE-059) ============
    @router.post("/renew-token")
    async def renew_agora_token(request: dict):
        """Renew Agora RTC token dynamically during an active call (BE-055)"""
        channel_name = request.get("channel_name")
        uid = request.get("uid")
        role = request.get("role", 1)
        if not channel_name or not uid:
            raise HTTPException(status_code=400, detail="channel_name and uid are required")
        
        token = generate_agora_token(channel_name, int(uid), int(role), expire_time=3600)
        return {
            "token": token or f"mock_token_{channel_name}_{uid}",
            "expires_in": 3600,
            "renewed_at": datetime.utcnow().isoformat()
        }

    @router.get("/active-session/{user_id}")
    async def get_active_call_session(user_id: str):
        """Recover active call session for instant reconnection after network drops (BE-058)"""
        active_call = await db.calls.find_one({
            "$or": [{"caller_id": user_id}, {"callee_id": user_id}],
            "status": {"$in": ["connected", "ringing"]}
        })
        if not active_call:
            return {"has_active_call": False}

        # Check maximum call duration cap (120 minutes - BE-059)
        if active_call.get("started_at"):
            duration = (datetime.utcnow() - active_call["started_at"]).total_seconds()
            if duration > 7200:  # 120 minutes limit
                await db.calls.update_one({"id": active_call["id"]}, {"$set": {"status": "ended", "ended_at": datetime.utcnow()}})
                return {"has_active_call": False, "reason": "Max call duration limit (120 mins) exceeded"}

        return {
            "has_active_call": True,
            "call_id": active_call.get("id"),
            "channel_name": active_call.get("channel_name"),
            "status": active_call.get("status"),
            "caller_id": active_call.get("caller_id"),
            "callee_id": active_call.get("callee_id")
        }
    
    @router.put("/call/{call_id}/end")
    async def end_call(call_id: str):
        """End an active call and notify the other party"""
        call = await db.calls.find_one({"id": call_id})
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        
        ended_at = datetime.utcnow()
        started_at = call.get("started_at")
        duration = 0
        
        if started_at:
            duration = int((ended_at - started_at).total_seconds())
        
        await db.calls.update_one(
            {"id": call_id},
            {
                "$set": {
                    "status": "ended",
                    "ended_at": ended_at,
                    "duration_seconds": duration
                }
            }
        )
        
        # Notify both parties via WebSocket that the call has ended
        from routes.websocket import manager
        caller_id = call.get("caller_id")
        callee_id = call.get("callee_id")
        
        end_message = {
            "type": "call_ended",
            "call_id": call_id,
            "duration_seconds": duration,
            "ended_by": "user"
        }
        
        # Notify caller
        if caller_id:
            await manager.send_personal_message(end_message, caller_id)
        
        # Notify callee (psychic)
        if callee_id:
            await manager.send_personal_message(end_message, callee_id)
        
        return {
            "success": True,
            "duration_seconds": duration,
            "duration_minutes": round(duration / 60, 2)
        }
    
    @router.put("/call/{call_id}/reject")
    async def reject_call(call_id: str):
        """Reject an incoming call"""
        await db.calls.update_one(
            {"id": call_id},
            {"$set": {"status": "rejected", "ended_at": datetime.utcnow()}}
        )
        return {"success": True, "status": "rejected"}
    
    @router.get("/call/{call_id}")
    async def get_call_status(call_id: str):
        """Get call status and details"""
        call = await db.calls.find_one({"id": call_id})
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        
        if "_id" in call:
            del call["_id"]
        
        # Convert datetime objects
        for key in ["created_at", "started_at", "ended_at"]:
            if call.get(key):
                call[key] = call[key].isoformat()
        
        return call
    
    @router.get("/history/{user_id}")
    async def get_call_history(user_id: str, limit: int = 20):
        """Get user's call history"""
        calls = await db.calls.find(
            {"$or": [{"caller_id": user_id}, {"callee_id": user_id}]}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        for call in calls:
            if "_id" in call:
                del call["_id"]
            for key in ["created_at", "started_at", "ended_at"]:
                if call.get(key):
                    call[key] = call[key].isoformat()
        
        return calls
    
    # ============== VIDEO RECORDING STORAGE (CLOUDINARY) ==============
    
    @router.post("/recording/upload")
    async def upload_recording(
        call_id: str,
        file: UploadFile = File(...),
        background_tasks: BackgroundTasks = None
    ):
        """Upload a video/audio recording to Cloudinary"""
        if not CLOUDINARY_ENABLED:
            raise HTTPException(status_code=503, detail="Cloud storage not configured")
        
        # Verify call exists
        call = await db.calls.find_one({"id": call_id})
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        
        try:
            # Read file content
            content = await file.read()
            
            # Determine resource type
            is_video = file.content_type and file.content_type.startswith("video")
            resource_type = "video" if is_video else "raw"
            
            # Upload to Cloudinary
            result = cloudinary.uploader.upload(
                content,
                resource_type=resource_type,
                folder="sixth_sense_recordings",
                public_id=f"recording_{call_id}_{uuid.uuid4().hex[:8]}",
                overwrite=True
            )
            
            # Store recording info in database
            recording = {
                "id": str(uuid.uuid4()),
                "call_id": call_id,
                "cloudinary_public_id": result["public_id"],
                "url": result["secure_url"],
                "format": result.get("format"),
                "duration": result.get("duration"),
                "size_bytes": result.get("bytes"),
                "resource_type": resource_type,
                "created_at": datetime.utcnow()
            }
            await db.recordings.insert_one(recording)
            
            # Update call with recording URL
            await db.calls.update_one(
                {"id": call_id},
                {"$set": {"recording_url": result["secure_url"]}}
            )
            
            print(f"✅ Recording uploaded: {result['secure_url']}")
            
            return {
                "success": True,
                "recording_id": recording["id"],
                "url": result["secure_url"],
                "duration": result.get("duration"),
                "format": result.get("format")
            }
            
        except Exception as e:
            print(f"❌ Cloudinary upload error: {e}")
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    
    @router.post("/recording/upload-base64")
    async def upload_recording_base64(data: dict):
        """Upload a base64-encoded video/audio recording to Cloudinary"""
        if not CLOUDINARY_ENABLED:
            raise HTTPException(status_code=503, detail="Cloud storage not configured")
        
        call_id = data.get("call_id")
        base64_data = data.get("data")
        content_type = data.get("content_type", "video/mp4")
        
        if not call_id or not base64_data:
            raise HTTPException(status_code=400, detail="call_id and data are required")
        
        # Verify call exists
        call = await db.calls.find_one({"id": call_id})
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        
        try:
            # Prepare data URI
            if not base64_data.startswith("data:"):
                base64_data = f"data:{content_type};base64,{base64_data}"
            
            # Determine resource type
            is_video = "video" in content_type
            resource_type = "video" if is_video else "raw"
            
            # Upload to Cloudinary
            result = cloudinary.uploader.upload(
                base64_data,
                resource_type=resource_type,
                folder="sixth_sense_recordings",
                public_id=f"recording_{call_id}_{uuid.uuid4().hex[:8]}",
                overwrite=True
            )
            
            # Store recording info in database
            recording = {
                "id": str(uuid.uuid4()),
                "call_id": call_id,
                "cloudinary_public_id": result["public_id"],
                "url": result["secure_url"],
                "format": result.get("format"),
                "duration": result.get("duration"),
                "size_bytes": result.get("bytes"),
                "resource_type": resource_type,
                "created_at": datetime.utcnow()
            }
            await db.recordings.insert_one(recording)
            
            # Update call with recording URL
            await db.calls.update_one(
                {"id": call_id},
                {"$set": {"recording_url": result["secure_url"]}}
            )
            
            print(f"✅ Base64 recording uploaded: {result['secure_url']}")
            
            return {
                "success": True,
                "recording_id": recording["id"],
                "url": result["secure_url"],
                "duration": result.get("duration"),
                "format": result.get("format")
            }
            
        except Exception as e:
            print(f"❌ Cloudinary upload error: {e}")
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    
    @router.get("/recording/{call_id}")
    async def get_recording(call_id: str):
        """Get recording URL for a specific call"""
        recording = await db.recordings.find_one({"call_id": call_id})
        if not recording:
            # Check if call has recording URL directly
            call = await db.calls.find_one({"id": call_id})
            if call and call.get("recording_url"):
                return {"url": call["recording_url"], "call_id": call_id}
            raise HTTPException(status_code=404, detail="Recording not found")
        
        if "_id" in recording:
            del recording["_id"]
        if recording.get("created_at"):
            recording["created_at"] = recording["created_at"].isoformat()
        
        return recording
    
    @router.get("/recordings/{user_id}")
    async def get_user_recordings(user_id: str, limit: int = 20):
        """Get all recordings for a user's calls"""
        # Get user's calls first
        calls = await db.calls.find(
            {"$or": [{"caller_id": user_id}, {"callee_id": user_id}]}
        ).to_list(100)
        
        call_ids = [call["id"] for call in calls]
        
        # Get recordings for those calls
        recordings = await db.recordings.find(
            {"call_id": {"$in": call_ids}}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        for rec in recordings:
            if "_id" in rec:
                del rec["_id"]
            if rec.get("created_at"):
                rec["created_at"] = rec["created_at"].isoformat()
        
        return recordings
    
    @router.delete("/recording/{recording_id}")
    async def delete_recording(recording_id: str):
        """Delete a recording from Cloudinary and database"""
        if not CLOUDINARY_ENABLED:
            raise HTTPException(status_code=503, detail="Cloud storage not configured")
        
        recording = await db.recordings.find_one({"id": recording_id})
        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")
        
        try:
            # Delete from Cloudinary
            public_id = recording.get("cloudinary_public_id")
            if public_id:
                cloudinary.uploader.destroy(
                    public_id,
                    resource_type=recording.get("resource_type", "video")
                )
            
            # Delete from database
            await db.recordings.delete_one({"id": recording_id})
            
            # Remove recording URL from call
            await db.calls.update_one(
                {"id": recording.get("call_id")},
                {"$unset": {"recording_url": ""}}
            )
            
            print(f"✅ Recording deleted: {recording_id}")
            return {"success": True, "deleted_id": recording_id}
            
        except Exception as e:
            print(f"❌ Delete error: {e}")
            raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    
    return router
