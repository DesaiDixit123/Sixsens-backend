from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.message import (
    Message, MessageCreate, MessageResponse,
    Conversation, ConversationResponse,
    Notification, NotificationResponse
)

MAX_DAILY_MESSAGES = 5

def create_messages_routes(db):
    router = APIRouter(prefix="/messages", tags=["messages"])
    
    def get_conversation_id(client_id: str, psychic_id: str) -> str:
        """Generate a consistent conversation ID"""
        return f"conv_{min(client_id, psychic_id)}_{max(client_id, psychic_id)}"
    
    async def check_has_reading(client_id: str, psychic_id: str) -> bool:
        """Check if client has purchased at least one reading OR has an active chat session with this psychic"""
        # Check for recorded readings/questions
        reading = await db.questions.find_one({
            "client_id": client_id,
            "psychic_id": psychic_id,
            "status": {"$in": ["completed", "pending", "accepted", "in_progress"]}
        })
        if reading:
            return True
        
        # Check for active or recent chat sessions
        chat_session = await db.chat_sessions.find_one({
            "client_id": client_id,
            "psychic_id": psychic_id,
            "status": {"$in": ["pending", "active", "accepted", "ended"]}
        })
        if chat_session:
            return True
        
        # Check for call sessions (voice/video)
        call_session = await db.call_sessions.find_one({
            "client_id": client_id,
            "psychic_id": psychic_id,
            "status": {"$in": ["pending", "active", "accepted", "ended", "completed"]}
        })
        if call_session:
            return True
            
        return False
    
    async def get_daily_message_count(conversation_id: str, sender_type: str, timezone_offset: int = 0) -> int:
        """Get the number of messages sent today by user type"""
        # Calculate start of day in user's timezone
        now = datetime.utcnow()
        # Adjust for timezone (offset in minutes)
        user_now = now - timedelta(minutes=timezone_offset)
        start_of_day = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_day_utc = start_of_day + timedelta(minutes=timezone_offset)
        
        count = await db.messages.count_documents({
            "conversation_id": conversation_id,
            "sender_type": sender_type,
            "created_at": {"$gte": start_of_day_utc}
        })
        return count
    
    async def create_notification(user_id: str, user_type: str, title: str, body: str, notification_type: str, related_id: str = None):
        """Create an in-app notification"""
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            user_type=user_type,
            title=title,
            body=body,
            notification_type=notification_type,
            related_id=related_id
        )
        await db.notifications.insert_one(notification.dict())
        return notification
    
    @router.post("/send", response_model=MessageResponse)
    async def send_message(
        message_data: MessageCreate,
        sender_id: Optional[str] = None,
        sender_type: Optional[str] = None,  # 'client' or 'psychic'
        timezone_offset: int = 0  # minutes offset from UTC
    ):
        """
        Send a message (either in a live chat session or post-reading conversation)
        """
        from routes.websocket import expand_user_aliases, manager

        sender_id = sender_id or message_data.sender_id or "unknown"
        if not sender_type:
            sender_type = message_data.sender_type
        if not sender_type:
            is_psy = await db.psychics.find_one({"id": {"$in": expand_user_aliases(sender_id)}})
            sender_type = "psychic" if is_psy else "client"

        receiver_type = 'psychic' if sender_type == 'client' else 'client'
        receiver_id = message_data.receiver_id or ""
        
        if sender_type == 'client':
            client_id = sender_id
            psychic_id = receiver_id
        else:
            client_id = receiver_id
            psychic_id = sender_id

        # Check if this is an active/recent live chat session
        is_live_session = False
        if message_data.conversation_id:
            session_doc = await db.chat_sessions.find_one({"id": message_data.conversation_id})
            if session_doc:
                is_live_session = True
                conversation_id = message_data.conversation_id
                if not receiver_id:
                    receiver_id = session_doc.get("client_id") if sender_type == "psychic" else session_doc.get("psychic_id")
            else:
                conversation_id = message_data.conversation_id
        else:
            conversation_id = get_conversation_id(client_id, psychic_id)

        if not is_live_session and not message_data.conversation_id:
            # Check if client has purchased a reading from this psychic (including alias IDs)
            has_reading = False
            for pid in expand_user_aliases(psychic_id):
                if await check_has_reading(client_id, pid):
                    has_reading = True
                    break
            if not has_reading:
                raise HTTPException(
                    status_code=403,
                    detail="You can only message psychics from whom you have purchased a reading."
                )
            
            # Check daily message limit for offline messages
            daily_count = await get_daily_message_count(conversation_id, sender_type, timezone_offset)
            if daily_count >= MAX_DAILY_MESSAGES:
                raise HTTPException(
                    status_code=429,
                    detail=f"You have reached your daily limit of {MAX_DAILY_MESSAGES} messages. Limit resets at midnight."
                )
        
        # Create the message
        new_message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            sender_id=sender_id,
            sender_type=sender_type,
            receiver_id=receiver_id,
            receiver_type=receiver_type,
            content=message_data.content,
            image_url=message_data.image_url
        )
        
        await db.messages.insert_one(new_message.dict())

        if is_live_session:
            await db.chat_sessions.update_one(
                {"id": conversation_id},
                {"$push": {"messages": {
                    "id": new_message.id,
                    "sender_id": sender_id,
                    "sender_type": sender_type,
                    "message": message_data.content,
                    "message_type": "image" if message_data.image_url else "text",
                    "image_url": message_data.image_url,
                    "timestamp": new_message.created_at.isoformat()
                }}}
            )
        
        # Update or create conversation
        existing_conv = await db.conversations.find_one({"id": conversation_id})
        
        if existing_conv:
            await db.conversations.update_one(
                {"id": conversation_id},
                {
                    "$set": {
                        "last_message": message_data.content[:100],
                        "last_message_time": datetime.utcnow()
                    },
                    "$inc": {"unread_count": 1}
                }
            )
        else:
            client_data = await db.users.find_one({"id": client_id})
            psychic_data = await db.psychics.find_one({"id": {"$in": expand_user_aliases(psychic_id)}})
            
            new_conv = Conversation(
                id=conversation_id,
                client_id=client_id,
                psychic_id=psychic_data.get("id", psychic_id) if psychic_data else psychic_id,
                client_name=client_data.get("name", "Client") if client_data else "Client",
                psychic_name=psychic_data.get("name", "Psychic") if psychic_data else "Psychic",
                psychic_avatar=psychic_data.get("avatar") if psychic_data else None,
                last_message=message_data.content[:100],
                last_message_time=datetime.utcnow(),
                unread_count=1
            )
            await db.conversations.insert_one(new_conv.dict())
        
        # Create notification for receiver
        sender_name = "Client" if sender_type == 'client' else "Psychic"
        if sender_type == 'client':
            client_data = await db.users.find_one({"id": sender_id})
            if client_data:
                sender_name = client_data.get("name", "Client")
        else:
            psychic_data = await db.psychics.find_one({"id": {"$in": expand_user_aliases(sender_id)}})
            if psychic_data:
                sender_name = psychic_data.get("name", "Psychic")
        
        if receiver_id:
            await create_notification(
                user_id=receiver_id,
                user_type=receiver_type,
                title=f"New message from {sender_name}",
                body=message_data.content[:50] + ("..." if len(message_data.content) > 50 else ""),
                notification_type="message",
                related_id=conversation_id
            )
        
        # Send real-time message via WebSocket
        try:
            ws_message = {
                "type": "new_message",
                "id": new_message.id,
                "conversation_id": conversation_id,
                "sender_id": sender_id,
                "sender_type": sender_type,
                "receiver_id": receiver_id,
                "content": message_data.content,
                "text": message_data.content,
                "image_url": message_data.image_url,
                "timestamp": new_message.created_at.isoformat(),
                "created_at": new_message.created_at.isoformat(),
                "message": {
                    "id": new_message.id,
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "sender_type": sender_type,
                    "receiver_id": receiver_id,
                    "receiver_type": receiver_type,
                    "content": message_data.content,
                    "image_url": message_data.image_url,
                    "is_read": False,
                    "created_at": new_message.created_at.isoformat()
                }
            }
            if receiver_id:
                await manager.send_personal_message(ws_message, receiver_id)
            await manager.broadcast_to_conversation(ws_message, conversation_id, exclude_user=sender_id)
            print(f"✅ WebSocket message sent to {receiver_id} (conv {conversation_id})")
        except Exception as e:
            print(f"⚠️ WebSocket delivery failed (user may be offline): {e}")
        
        return MessageResponse(**new_message.dict())
    
    @router.get("/conversations/{user_id}", response_model=List[ConversationResponse])
    async def get_conversations(
        user_id: str,
        user_type: str,  # 'client' or 'psychic'
        timezone_offset: int = 0
    ):
        """
        Get all conversations for a user
        """
        from routes.websocket import expand_user_aliases
        target_ids = expand_user_aliases(user_id)
        query_field = "client_id" if user_type == 'client' else "psychic_id"
        conversations = await db.conversations.find({query_field: {"$in": target_ids}}).sort("last_message_time", -1).to_list(100)
        
        result = []
        existing_conv_ids = set()
        for conv in conversations:
            existing_conv_ids.add(conv.get("id"))
            daily_count = await get_daily_message_count(conv["id"], user_type, timezone_offset)
            remaining = max(0, MAX_DAILY_MESSAGES - daily_count)
            
            unread = await db.messages.count_documents({
                "conversation_id": conv["id"],
                "receiver_id": {"$in": target_ids},
                "is_read": False
            })
            
            is_new_client = False
            if user_type == 'psychic':
                client_id = conv["client_id"]
                psychic_id = conv["psychic_id"]
                completed_readings_count = await db.questions.count_documents({
                    "client_id": client_id,
                    "psychic_id": {"$in": expand_user_aliases(psychic_id)},
                    "status": "completed"
                })
                is_new_client = completed_readings_count <= 1
            
            result.append(ConversationResponse(
                id=conv["id"],
                client_id=conv["client_id"],
                psychic_id=conv["psychic_id"],
                client_name=conv.get("client_name"),
                psychic_name=conv.get("psychic_name"),
                psychic_avatar=conv.get("psychic_avatar"),
                client_avatar=conv.get("client_avatar"),
                last_message=conv.get("last_message"),
                last_message_time=conv.get("last_message_time"),
                unread_count=unread,
                remaining_messages=remaining,
                is_new_client=is_new_client
            ))

        # Also merge chat_sessions so all client readings & live chats appear in Client Messages
        session_field = "client_id" if user_type == "client" else "psychic_id"
        recent_sessions = await db.chat_sessions.find({
            "$or": [
                {session_field: {"$in": target_ids}},
                {session_field: user_id},
            ]
        }).sort("started_at", -1).to_list(50)

        if not recent_sessions and user_type == "psychic":
            recent_sessions = await db.chat_sessions.find({}).sort("started_at", -1).to_list(50)

        for s in recent_sessions:
            s_id = s.get("id")
            if not s_id or s_id in existing_conv_ids:
                continue
            
            s_client = s.get("client_id") or "client"
            s_psychic = s.get("psychic_id") or "psychic"
            client_doc = await db.users.find_one({"id": s_client}) if s_client else None
            psy_doc = await db.psychics.find_one({"id": {"$in": expand_user_aliases(s_psychic)}}) if s_psychic else None
            
            msgs = s.get("messages", [])
            last_msg = ""
            last_time = s.get("created_at") or s.get("started_at")
            if msgs and len(msgs) > 0:
                last_item = msgs[-1]
                last_msg = last_item.get("message") or last_item.get("content") or ("[Photo]" if last_item.get("message_type") == "image" else "")
                last_time = last_item.get("timestamp") or last_time
            else:
                db_msgs = await db.messages.find({"conversation_id": s_id}).sort("created_at", -1).to_list(1)
                if db_msgs:
                    m = db_msgs[0]
                    last_msg = m.get("content") or ("[Photo]" if m.get("image_url") else "")
                    last_time = m.get("created_at")
            
            if not last_msg:
                last_msg = f"{s.get('session_type', 'chat').title()} Session"

            dt = None
            if last_time:
                if isinstance(last_time, str):
                    try:
                        dt = datetime.fromisoformat(last_time.replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:
                        dt = datetime.utcnow()
                elif isinstance(last_time, datetime):
                    dt = last_time

            result.append(ConversationResponse(
                id=s_id,
                client_id=s_client,
                psychic_id=s_psychic,
                client_name=s.get("client_name") or (client_doc.get("name") if client_doc else "Client"),
                psychic_name=s.get("psychic_name") or (psy_doc.get("name") if psy_doc else "Advisor"),
                psychic_avatar=psy_doc.get("profile_picture") or psy_doc.get("avatar") if psy_doc else None,
                client_avatar=client_doc.get("profile_picture") or client_doc.get("avatar") if client_doc else None,
                last_message=last_msg,
                last_message_time=dt,
                unread_count=0,
                remaining_messages=MAX_DAILY_MESSAGES,
                is_new_client=True
            ))
            existing_conv_ids.add(s_id)
        
        return result
    
    @router.get("/conversation/{conversation_id}", response_model=List[MessageResponse])
    async def get_conversation_messages(
        conversation_id: str,
        user_id: str = "user",
        limit: int = 50,
        skip: int = 0
    ):
        """
        Get messages in a conversation
        """
        from routes.websocket import expand_user_aliases
        user_aliases = set(expand_user_aliases(user_id))

        messages = await db.messages.find(
            {"conversation_id": conversation_id}
        ).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        
        # If no messages in db.messages, check db.chat_sessions array
        if len(messages) == 0:
            sess = await db.chat_sessions.find_one({"id": conversation_id})
            if sess and sess.get("messages"):
                raw_msgs = sess.get("messages", [])
                for rm in raw_msgs:
                    rm_id = rm.get("id") or str(uuid.uuid4())
                    ts = rm.get("timestamp") or datetime.utcnow()
                    if isinstance(ts, str):
                        try:
                            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                        except Exception:
                            ts = datetime.utcnow()
                    messages.append({
                        "id": rm_id,
                        "conversation_id": conversation_id,
                        "sender_id": rm.get("sender_id") or "user",
                        "sender_type": rm.get("sender_type") or "client",
                        "receiver_id": user_id,
                        "receiver_type": "psychic" if rm.get("sender_type") == "client" else "client",
                        "content": rm.get("message") or rm.get("content") or "",
                        "image_url": rm.get("image_url"),
                        "is_read": True,
                        "created_at": ts,
                    })

        # Mark messages as read
        await db.messages.update_many(
            {
                "conversation_id": conversation_id,
                "receiver_id": {"$in": list(user_aliases)},
                "is_read": False
            },
            {"$set": {"is_read": True}}
        )
        
        # Reverse to show oldest first
        messages.reverse()
        
        normalized = []
        for msg in messages:
            if "content" not in msg and "text" in msg:
                msg["content"] = msg["text"]
            if "content" not in msg and "message" in msg:
                msg["content"] = msg["message"]
            if "created_at" not in msg and "timestamp" in msg:
                msg["created_at"] = msg["timestamp"]
            if "created_at" not in msg:
                msg["created_at"] = datetime.utcnow()
            if "receiver_id" not in msg or not msg["receiver_id"]:
                msg["receiver_id"] = "unknown"
            normalized.append(MessageResponse(**msg))
        return normalized
    
    @router.get("/remaining/{user_id}")
    async def get_remaining_messages(
        user_id: str,
        user_type: str,
        other_user_id: str,
        timezone_offset: int = 0
    ):
        """
        Get remaining messages for today in a conversation
        """
        if user_type == 'client':
            conversation_id = get_conversation_id(user_id, other_user_id)
        else:
            conversation_id = get_conversation_id(other_user_id, user_id)
        
        daily_count = await get_daily_message_count(conversation_id, user_type, timezone_offset)
        
        return {
            "used": daily_count,
            "remaining": max(0, MAX_DAILY_MESSAGES - daily_count),
            "limit": MAX_DAILY_MESSAGES
        }
    
    # Notification endpoints
    @router.get("/notifications/{user_id}", response_model=List[NotificationResponse])
    async def get_notifications(
        user_id: str,
        user_type: str = "psychic",
        unread_only: bool = False,
        limit: int = 50
    ):
        """
        Get notifications for a user (including alias IDs)
        """
        from routes.websocket import expand_user_aliases
        target_ids = expand_user_aliases(user_id)
        query = {"user_id": {"$in": target_ids}}
        if unread_only:
            query["is_read"] = {"$ne": True}
        
        notifications = await db.notifications.find(query).sort("created_at", -1).limit(limit).to_list(limit)
        result = []
        for n in notifications:
            result.append(NotificationResponse(
                id=n.get("id", str(uuid.uuid4())),
                user_id=n.get("user_id", user_id),
                user_type=n.get("user_type", user_type),
                title=n.get("title", "Notification"),
                body=n.get("body", ""),
                notification_type=n.get("notification_type") or n.get("type") or "message",
                related_id=n.get("related_id"),
                is_read=bool(n.get("is_read", n.get("read", False))),
                created_at=n.get("created_at", datetime.utcnow())
            ))
        return result
    
    @router.get("/notifications/{user_id}/count")
    async def get_unread_notification_count(user_id: str, user_type: str = "psychic"):
        """
        Get count of unread notifications
        """
        from routes.websocket import expand_user_aliases
        target_ids = expand_user_aliases(user_id)
        count = await db.notifications.count_documents({
            "user_id": {"$in": target_ids},
            "is_read": {"$ne": True}
        })
        return {"unread_count": count}
    
    @router.post("/notifications/{notification_id}/read")
    async def mark_notification_read(notification_id: str):
        """
        Mark a notification as read
        """
        await db.notifications.update_one(
            {"id": notification_id},
            {"$set": {"is_read": True, "read": True}}
        )
        return {"success": True}
    
    @router.post("/notifications/{user_id}/read-all")
    async def mark_all_notifications_read(user_id: str, user_type: str = "psychic"):
        """
        Mark all notifications as read for a user
        """
        from routes.websocket import expand_user_aliases
        target_ids = expand_user_aliases(user_id)
        await db.notifications.update_many(
            {"user_id": {"$in": target_ids}},
            {"$set": {"is_read": True, "read": True}}
        )
        return {"success": True}
    
    @router.get("/unread-count/{user_id}")
    async def get_unread_message_count(user_id: str, user_type: str = "client"):
        """
        Get count of unread messages for a user (across all conversations)
        """
        from routes.websocket import expand_user_aliases
        target_ids = expand_user_aliases(user_id)
        if user_type == "client":
            count = await db.messages.count_documents({
                "receiver_id": {"$in": target_ids},
                "sender_type": "psychic",
                "is_read": False
            })
        else:
            count = await db.messages.count_documents({
                "receiver_id": {"$in": target_ids},
                "sender_type": "client",
                "is_read": False
            })
        
        return {"count": count}
    
    return router
