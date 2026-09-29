from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.psychic_profile import PsychicApplication, PsychicDashboard, PsychicSettings
from models.withdrawal import Withdrawal, WithdrawalCreate, MINIMUM_WITHDRAWAL

def create_psychic_portal_routes(db):
    router = APIRouter(prefix="/psychic-portal", tags=["psychic-portal"])
    
    # ============ APPLICATION ============
    @router.post("/apply")
    async def submit_application(application: dict):
        """Submit psychic application with profile picture, intro video, and test reading"""
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        
        # Hash password if provided
        password_hash = None
        if application.get("password"):
            password_hash = pwd_context.hash(application.get("password"))
        
        new_app = PsychicApplication(
            user_id=application.get("user_id", str(uuid.uuid4())),
            full_name=application.get("full_name"),
            email=application.get("email"),
            phone=application.get("phone"),
            experience_years=application.get("experience_years", 0),
            specialties=application.get("specialties", []),
            reading_methods=application.get("reading_methods", []),
            bio=application.get("bio", ""),
            chat_rate=application.get("chat_rate", 2.99),
            phone_rate=application.get("phone_rate", 3.99),
            video_rate=application.get("video_rate", 4.99)
        )
        
        # Store additional fields
        app_data = new_app.dict()
        app_data["profile_picture"] = application.get("profile_picture")
        app_data["intro_video_url"] = application.get("intro_video_url")
        app_data["test_reading_video_url"] = application.get("test_reading_video_url")
        app_data["password_hash"] = password_hash
        
        await db.psychic_applications.insert_one(app_data)
        return {"success": True, "application_id": new_app.id}
    
    @router.get("/application/{application_id}")
    async def get_application(application_id: str):
        """Get application status"""
        app = await db.psychic_applications.find_one({"id": application_id})
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        return app
    
    # ============ DASHBOARD ============
    @router.get("/dashboard/{psychic_id}")
    async def get_dashboard(psychic_id: str):
        """Get psychic dashboard stats"""
        # Get psychic data
        psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        # Get pending questions
        pending_questions = await db.questions.count_documents({
            "psychic_id": psychic_id,
            "status": "pending"
        })
        
        # Get active sessions
        active_sessions = await db.chat_sessions.count_documents({
            "psychic_id": psychic_id,
            "status": "active"
        })
        
        dashboard = PsychicDashboard(
            total_earnings=psychic.get("total_earnings", 0),
            pending_earnings=psychic.get("pending_earnings", 0),
            total_readings=psychic.get("total_readings", 0),
            average_rating=psychic.get("average_rating", 0),
            total_reviews=psychic.get("total_reviews", 0),
            pending_questions=pending_questions,
            active_sessions=active_sessions
        )
        
        return dashboard.dict()
    
    @router.get("/questions/{psychic_id}")
    async def get_pending_questions(psychic_id: str):
        """Get pending questions for psychic to answer"""
        questions = await db.questions.find({
            "psychic_id": psychic_id,
            "status": {"$in": ["pending", "accepted"]}
        }).sort("created_at", -1).to_list(50)
        return questions
    
    @router.post("/questions/{question_id}/accept")
    async def accept_question(question_id: str):
        """Accept a question"""
        result = await db.questions.update_one(
            {"id": question_id},
            {"$set": {"status": "accepted"}}
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"success": True}
    
    @router.post("/questions/{question_id}/answer")
    async def submit_answer(question_id: str, video_url: str):
        """Submit video answer"""
        result = await db.questions.update_one(
            {"id": question_id},
            {
                "$set": {
                    "status": "completed",
                    "video_response_url": video_url,
                    "completed_at": datetime.utcnow()
                }
            }
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"success": True}
    
    # ============ SETTINGS ============
    @router.get("/settings/{psychic_id}")
    async def get_settings(psychic_id: str):
        """Get psychic settings"""
        settings = await db.psychic_settings.find_one({"psychic_id": psychic_id})
        if not settings:
            # Create default settings
            settings = PsychicSettings(psychic_id=psychic_id).dict()
            await db.psychic_settings.insert_one(settings)
        return settings
    
    @router.put("/settings/{psychic_id}")
    async def update_settings(psychic_id: str, settings: dict, email: str = None):
        """Update psychic settings - supports both id and email lookup"""
        # Save to psychic_settings collection
        await db.psychic_settings.update_one(
            {"psychic_id": psychic_id},
            {"$set": settings},
            upsert=True
        )
        
        # Also update rates in psychics collection
        rate_fields = {}
        if "chat_rate" in settings:
            rate_fields["chat_rate"] = settings["chat_rate"]
        if "phone_rate" in settings:
            rate_fields["phone_rate"] = settings["phone_rate"]
        if "video_rate" in settings:
            rate_fields["video_rate"] = settings["video_rate"]
        
        if rate_fields:
            # Try to update by id first
            result = await db.psychics.update_one({"id": psychic_id}, {"$set": rate_fields})
            
            # If no match by id, try by email
            if result.matched_count == 0 and email:
                await db.psychics.update_one({"email": email}, {"$set": rate_fields})
            
            # Also try to find psychic by id pattern or email and update
            if result.matched_count == 0:
                # Maybe psychic_id contains email
                psychic = await db.psychics.find_one({"$or": [
                    {"id": psychic_id},
                    {"email": psychic_id},
                    {"email": {"$regex": psychic_id, "$options": "i"}}
                ]})
                if psychic:
                    await db.psychics.update_one({"_id": psychic["_id"]}, {"$set": rate_fields})
        
        return {"success": True}
    
    @router.post("/vacation/{psychic_id}")
    async def toggle_vacation(psychic_id: str, enable: bool, end_date: str = None):
        """Toggle vacation mode"""
        update = {
            "vacation_mode": enable,
            "is_available": not enable
        }
        if enable and end_date:
            update["vacation_end_date"] = datetime.fromisoformat(end_date)
        
        await db.psychic_settings.update_one(
            {"psychic_id": psychic_id},
            {"$set": update},
            upsert=True
        )
        
        # Update online status
        await db.psychics.update_one(
            {"id": psychic_id},
            {"$set": {"online_status": "offline" if enable else "online"}}
        )
        
        return {"success": True, "vacation_mode": enable}
    
    @router.post("/update-status")
    async def update_online_status(status_data: dict):
        """Update psychic online status - syncs between Advisor and Client apps"""
        psychic_id = status_data.get("psychic_id")
        email = status_data.get("email")
        online_status = status_data.get("online_status")
        offers_chat = status_data.get("offers_chat")
        offers_phone = status_data.get("offers_phone")
        offers_video = status_data.get("offers_video")
        offers_standard_readings = status_data.get("offers_standard_readings")
        offers_emergency_readings = status_data.get("offers_emergency_readings")
        
        # Build update data - only include fields that were provided
        update_data = {"updated_at": datetime.utcnow()}
        
        if online_status is not None:
            update_data["online_status"] = online_status
            update_data["is_available"] = online_status == "online"
        if offers_chat is not None:
            update_data["offers_chat"] = offers_chat
        if offers_phone is not None:
            update_data["offers_phone"] = offers_phone
        if offers_video is not None:
            update_data["offers_video"] = offers_video
        if offers_standard_readings is not None:
            update_data["offers_standard_readings"] = offers_standard_readings
        if offers_emergency_readings is not None:
            update_data["offers_emergency_readings"] = offers_emergency_readings
        
        # Find psychic by ID or email
        psychic = None
        if psychic_id:
            psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic and email:
            psychic = await db.psychics.find_one({"email": email})
        
        if not psychic:
            # Create/update psychic entry if it doesn't exist
            filter_query = {"email": email} if email else {"id": psychic_id}
            await db.psychics.update_one(
                filter_query,
                {"$set": update_data},
                upsert=True
            )
        else:
            # Update existing psychic
            await db.psychics.update_one(
                {"_id": psychic["_id"]},
                {"$set": update_data}
            )
        
        return {
            "success": True,
            "online_status": online_status,
            "message": "Status updated successfully"
        }
    
    # ============ WITHDRAWALS ============
    @router.post("/withdraw")
    async def request_withdrawal(withdrawal: WithdrawalCreate, psychic_id: str = None):
        """Request withdrawal (minimum $50)"""
        if not psychic_id:
            raise HTTPException(status_code=400, detail="Psychic ID required")
        
        if withdrawal.amount < MINIMUM_WITHDRAWAL:
            raise HTTPException(
                status_code=400, 
                detail=f"Minimum withdrawal is ${MINIMUM_WITHDRAWAL}"
            )
        
        # Check available balance
        psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        available = psychic.get("total_earnings", 0) - psychic.get("withdrawn", 0)
        if withdrawal.amount > available:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Available: ${available:.2f}"
            )
        
        new_withdrawal = Withdrawal(
            psychic_id=psychic_id,
            amount=withdrawal.amount,
            payment_method=withdrawal.payment_method,
            payment_details=withdrawal.payment_details
        )
        
        await db.withdrawals.insert_one(new_withdrawal.dict())
        return {"success": True, "withdrawal": new_withdrawal.dict()}
    
    @router.get("/withdrawals/{psychic_id}")
    async def get_withdrawals(psychic_id: str):
        """Get withdrawal history"""
        withdrawals = await db.withdrawals.find({
            "psychic_id": psychic_id
        }).sort("created_at", -1).to_list(50)
        return withdrawals
    
    # ============ PROFILE BOOST ============
    @router.post("/boost/{psychic_id}")
    async def activate_boost(psychic_id: str, hours: int = 24):
        """Activate profile boost (mock - would require payment)"""
        expires = datetime.utcnow() + timedelta(hours=hours)
        
        await db.psychic_settings.update_one(
            {"psychic_id": psychic_id},
            {
                "$set": {
                    "boost_active": True,
                    "boost_expires": expires
                }
            },
            upsert=True
        )
        
        return {"success": True, "boost_expires": expires.isoformat()}
    
    return router
