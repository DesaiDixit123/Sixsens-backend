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
        sender_id: str,
        sender_type: str,  # 'client' or 'psychic'
        timezone_offset: int = 0  # minutes offset from UTC
    ):
        """
        Send a message (max 5 per day per person)
        Messages are for doubts/clarifications only, not free readings.
        """
        receiver_type = 'psychic' if sender_type == 'client' else 'client'
        
        # Determine client_id and psychic_id
        if sender_type == 'client':
            client_id = sender_id
            psychic_id = message_data.receiver_id
        else:
            client_id = message_data.receiver_id
            psychic_id = sender_id
        
        # Check if client has purchased a reading from this psychic
        has_reading = await check_has_reading(client_id, psychic_id)
        if not has_reading:
            raise HTTPException(
                status_code=403,
                detail="You can only message psychics from whom you have purchased a reading."
            )
        
        conversation_id = get_conversation_id(client_id, psychic_id)
        
        # Check daily message limit
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
            receiver_id=message_data.receiver_id,
            receiver_type=receiver_type,
            content=message_data.content,
            image_url=message_data.image_url  # Include image URL if provided
        )
        
        await db.messages.insert_one(new_message.dict())
        
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
            # Get names for the conversation
            client_data = await db.users.find_one({"id": client_id})
            psychic_data = await db.psychics.find_one({"id": psychic_id})
            
            new_conv = Conversation(
                id=conversation_id,
                client_id=client_id,
                psychic_id=psychic_id,
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
            psychic_data = await db.psychics.find_one({"id": sender_id})
            if psychic_data:
                sender_name = psychic_data.get("name", "Psychic")
        
        await create_notification(
            user_id=message_data.receiver_id,
            user_type=receiver_type,
            title=f"New message from {sender_name}",
            body=message_data.content[:50] + ("..." if len(message_data.content) > 50 else ""),
            notification_type="message",
            related_id=conversation_id
        )
        
        # Send real-time message via WebSocket
        try:
            from routes.websocket import manager
            ws_message = {
                "type": "new_message",
                "message": {
                    "id": new_message.id,
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "sender_type": sender_type,
                    "receiver_id": message_data.receiver_id,
                    "receiver_type": receiver_type,
                    "content": message_data.content,
                    "image_url": message_data.image_url,
                    "is_read": False,
                    "created_at": new_message.created_at.isoformat()
                }
            }
            await manager.send_personal_message(ws_message, message_data.receiver_id)
            print(f"✅ WebSocket message sent to {message_data.receiver_id}")
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
        query_field = "client_id" if user_type == 'client' else "psychic_id"
        conversations = await db.conversations.find({query_field: user_id}).sort("last_message_time", -1).to_list(100)
        
        result = []
        for conv in conversations:
            # Get remaining messages for today
            daily_count = await get_daily_message_count(conv["id"], user_type, timezone_offset)
            remaining = max(0, MAX_DAILY_MESSAGES - daily_count)
            
            # Get unread count for this user
            unread = await db.messages.count_documents({
                "conversation_id": conv["id"],
                "receiver_id": user_id,
                "is_read": False
            })
            
            # Determine if this is a new client (for psychic view)
            # A client is "new" if they only have 1 completed reading with this psychic
            is_new_client = False
            if user_type == 'psychic':
                client_id = conv["client_id"]
                psychic_id = conv["psychic_id"]
                completed_readings_count = await db.questions.count_documents({
                    "client_id": client_id,
                    "psychic_id": psychic_id,
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
        
        return result
    
    @router.get("/conversation/{conversation_id}", response_model=List[MessageResponse])
    async def get_conversation_messages(
        conversation_id: str,
        user_id: str,
        limit: int = 50,
        skip: int = 0
    ):
        """
        Get messages in a conversation
        """
        messages = await db.messages.find(
            {"conversation_id": conversation_id}
        ).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        
        # Mark messages as read
        await db.messages.update_many(
            {
                "conversation_id": conversation_id,
                "receiver_id": user_id,
                "is_read": False
            },
            {"$set": {"is_read": True}}
        )
        
        # Reverse to show oldest first
        messages.reverse()
        
        return [MessageResponse(**msg) for msg in messages]
    
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
        user_type: str,
        unread_only: bool = False,
        limit: int = 50
    ):
        """
        Get notifications for a user
        """
        query = {"user_id": user_id, "user_type": user_type}
        if unread_only:
            query["is_read"] = False
        
        notifications = await db.notifications.find(query).sort("created_at", -1).limit(limit).to_list(limit)
        return [NotificationResponse(**n) for n in notifications]
    
    @router.get("/notifications/{user_id}/count")
    async def get_unread_notification_count(user_id: str, user_type: str):
        """
        Get count of unread notifications
        """
        count = await db.notifications.count_documents({
            "user_id": user_id,
            "user_type": user_type,
            "is_read": False
        })
        return {"unread_count": count}
    
    @router.post("/notifications/{notification_id}/read")
    async def mark_notification_read(notification_id: str):
        """
        Mark a notification as read
        """
        result = await db.notifications.update_one(
            {"id": notification_id},
            {"$set": {"is_read": True}}
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"success": True}
    
    @router.post("/notifications/{user_id}/read-all")
    async def mark_all_notifications_read(user_id: str, user_type: str):
        """
        Mark all notifications as read for a user
        """
        await db.notifications.update_many(
            {"user_id": user_id, "user_type": user_type},
            {"$set": {"is_read": True}}
        )
        return {"success": True}
    
    @router.get("/unread-count/{user_id}")
    async def get_unread_message_count(user_id: str, user_type: str = "client"):
        """
        Get count of unread messages for a user (across all conversations)
        """
        # Determine the field to check based on user type
        if user_type == "client":
            # For clients, count messages from psychics that are unread
            count = await db.messages.count_documents({
                "$or": [
                    {"receiver_id": user_id},
                    {"conversation_id": {"$regex": user_id}}
                ],
                "sender_type": "psychic",
                "is_read": False
            })
        else:
            # For psychics, count messages from clients that are unread
            count = await db.messages.count_documents({
                "$or": [
                    {"receiver_id": user_id},
                    {"conversation_id": {"$regex": user_id}}
                ],
                "sender_type": "client",
                "is_read": False
            })
        
        return {"count": count}
    
    return router
