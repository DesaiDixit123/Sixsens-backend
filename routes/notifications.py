from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field
import uuid
import random

router = APIRouter(prefix="/notifications", tags=["notifications"])

class NotificationCreate(BaseModel):
    user_id: str
    title: str
    body: str
    type: str = "promotional"  # promotional, system, reading_update, etc.

class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    title: str
    body: str
    type: str = "promotional"
    is_read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class NotificationPreferences(BaseModel):
    promotional: bool = True
    reading_updates: bool = True
    system: bool = True

# Promotional notification messages
PROMOTIONAL_MESSAGES = [
    {
        "title": "Need Clarity? ✨",
        "body": "Your favorite psychics are online now! Get a reading and find the answers you seek."
    },
    {
        "title": "The Stars Are Aligned 🌟",
        "body": "Today is the perfect day for a reading. Connect with an advisor and discover what the universe has in store."
    },
    {
        "title": "Trust Your Intuition 🔮",
        "body": "Something on your mind? Our gifted psychics are ready to guide you through any situation."
    },
    {
        "title": "Special Energy Today 💫",
        "body": "We sense big things for you! Get a personalized reading now and unlock your potential."
    },
    {
        "title": "Your Path Awaits 🌙",
        "body": "Questions about love, career, or life? Our top-rated advisors are waiting to help you."
    },
    {
        "title": "Time for Guidance 🌺",
        "body": "Life's big decisions deserve cosmic insight. Book a reading with a trusted psychic today!"
    },
    {
        "title": "Unlock Your Future 🗝️",
        "body": "Curious about what's next? Our psychics can reveal the path ahead. Connect now!"
    },
    {
        "title": "Astrology Check ✨",
        "body": "When was your last reading? Reconnect with your spiritual journey today."
    },
]

def create_notifications_routes(db):
    
    @router.get("/user/{user_id}")
    async def get_user_notifications(user_id: str, user_type: str = None, limit: int = 50):
        """Get notifications for a user"""
        query = {"user_id": user_id}
        if user_type:
            query["user_type"] = user_type
            
        notifications = await db.notifications.find(query).sort("created_at", -1).limit(limit).to_list(limit)
        
        # Convert MongoDB documents to clean dicts
        clean_notifications = []
        for n in notifications:
            clean_n = {k: v for k, v in n.items() if k != '_id'}
            clean_notifications.append(clean_n)
        
        return {"notifications": clean_notifications}
    
    @router.post("/mark-read/{notification_id}")
    async def mark_notification_read(notification_id: str):
        """Mark a notification as read"""
        result = await db.notifications.update_one(
            {"id": notification_id},
            {"$set": {"is_read": True}}
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"success": True}
    
    @router.post("/mark-all-read/{user_id}")
    async def mark_all_read(user_id: str):
        """Mark all notifications as read for a user"""
        await db.notifications.update_many(
            {"user_id": user_id, "is_read": False},
            {"$set": {"is_read": True}}
        )
        return {"success": True}
    
    @router.get("/unread-count/{user_id}")
    async def get_unread_count(user_id: str, user_type: str = None):
        """Get unread notification count for a user"""
        query = {"user_id": user_id, "is_read": False}
        if user_type:
            query["user_type"] = user_type
        count = await db.notifications.count_documents(query)
        return {"count": count}
    
    @router.get("/debug/all")
    async def debug_all_notifications(limit: int = 50):
        """Debug endpoint to view all notifications"""
        notifications = await db.notifications.find({}).sort("created_at", -1).limit(limit).to_list(limit)
        clean_notifications = []
        for n in notifications:
            clean_n = {k: v for k, v in n.items() if k != '_id'}
            clean_notifications.append(clean_n)
        return {"total": len(clean_notifications), "notifications": clean_notifications}
    
    @router.get("/preferences/{user_id}")
    async def get_notification_preferences(user_id: str):
        """Get notification preferences for a user"""
        prefs = await db.notification_preferences.find_one({"user_id": user_id})
        if not prefs:
            # Return defaults
            return NotificationPreferences().dict()
        return prefs
    
    @router.post("/preferences/{user_id}")
    async def update_notification_preferences(user_id: str, prefs: NotificationPreferences):
        """Update notification preferences for a user"""
        await db.notification_preferences.update_one(
            {"user_id": user_id},
            {"$set": {**prefs.dict(), "user_id": user_id}},
            upsert=True
        )
        return {"success": True}
    
    @router.post("/send-promotional")
    async def send_promotional_notifications():
        """
        Send promotional notifications to all signed-up users (2 per day).
        This should be called by a cron job/scheduler twice daily.
        """
        # Get all users who have promotional notifications enabled (or haven't set preferences)
        users = await db.users.find({"status": "active"}).to_list(10000)
        
        sent_count = 0
        for user in users:
            user_id = user.get("id")
            
            # Check if user has disabled promotional notifications
            prefs = await db.notification_preferences.find_one({"user_id": user_id})
            if prefs and not prefs.get("promotional", True):
                continue
            
            # Check how many promotional notifications were sent today
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_count = await db.notifications.count_documents({
                "user_id": user_id,
                "type": "promotional",
                "created_at": {"$gte": today_start}
            })
            
            # Only send if fewer than 2 promotional notifications sent today
            if today_count >= 2:
                continue
            
            # Pick a random promotional message
            message = random.choice(PROMOTIONAL_MESSAGES)
            
            notification = Notification(
                user_id=user_id,
                title=message["title"],
                body=message["body"],
                type="promotional"
            )
            
            await db.notifications.insert_one(notification.dict())
            sent_count += 1
        
        return {
            "success": True,
            "message": f"Sent {sent_count} promotional notifications",
            "sent_count": sent_count
        }
    
    @router.post("/send-test/{user_id}")
    async def send_test_notification(user_id: str):
        """Send a test promotional notification to a specific user"""
        message = random.choice(PROMOTIONAL_MESSAGES)
        
        notification = Notification(
            user_id=user_id,
            title=message["title"],
            body=message["body"],
            type="promotional"
        )
        
        await db.notifications.insert_one(notification.dict())
        
        return {
            "success": True,
            "notification": notification.dict()
        }
    
    @router.delete("/{notification_id}")
    async def delete_notification(notification_id: str):
        """Delete a notification"""
        result = await db.notifications.delete_one({"id": notification_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"success": True}
    
    return router
