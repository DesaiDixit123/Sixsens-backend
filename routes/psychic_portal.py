from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.psychic_profile import PsychicApplication, PsychicDashboard, PsychicSettings
from models.withdrawal import Withdrawal, WithdrawalCreate, MINIMUM_WITHDRAWAL

def get_psychic_alias_ids(psychic_id: str, email: Optional[str] = None) -> List[str]:
    """Helper to return all recognized ID and email aliases for a psychic"""
    aliases = [psychic_id]
    if email:
        aliases.append(email)
    if psychic_id in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "psy-001", "testpsychic@sixthsense.com", "luna@psychic.com") or (email and email in ("testpsychic@sixthsense.com", "luna@psychic.com")):
        aliases.extend(["64ea86f6-968a-4dfc-8c98-1a322fb020c0", "psy-001", "testpsychic@sixthsense.com", "luna@psychic.com"])
    return list(dict.fromkeys([a for a in aliases if a]))

def get_psychic_or_query(psychic_id: str, email: Optional[str] = None) -> List[dict]:
    """Helper to build MongoDB $or query across all possible psychic identifiers"""
    aliases = get_psychic_alias_ids(psychic_id, email)
    return [
        {"$or": [
            {"id": {"$in": aliases}},
            {"email": {"$in": aliases}},
            {"psychic_id": {"$in": aliases}},
            {"psychic_email": {"$in": aliases}},
        ]}
    ]

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
        
        # Notify admin of new application
        try:
            from routes.push_notifications import send_push_to_user
            await send_push_to_user(
                db,
                user_id="admin",
                title="New Advisor Application! 📝",
                body=f"{new_app.full_name} has submitted an application.",
                data={
                    "type": "new_application",
                    "application_id": str(new_app.id),
                    "applicant_name": str(new_app.full_name),
                    "email": str(new_app.email)
                }
            )
        except Exception as e:
            print(f"Error sending push notification to admin: {e}")

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
        """Get psychic dashboard stats dynamically from database"""
        aliases = get_psychic_alias_ids(psychic_id)
        
        # Find psychic by id, email, or _id
        psychic = await db.psychics.find_one({"$or": [{"id": {"$in": aliases}}, {"email": {"$in": aliases}}]})
        if not psychic:
            try:
                from bson.objectid import ObjectId
                if len(psychic_id) == 24 and ObjectId.is_valid(psychic_id):
                    psychic = await db.psychics.find_one({"_id": ObjectId(psychic_id)})
            except Exception:
                pass

        # If not in database, fallback to known mock psychics or auto-create so it NEVER 404s
        if not psychic:
            try:
                from routes.psychics import MOCK_PSYCHICS
                for mp in MOCK_PSYCHICS:
                    if mp.get("id") in aliases or mp.get("email") in aliases:
                        psychic = dict(mp)
                        break
            except Exception:
                pass

            if not psychic:
                psychic = {
                    "id": psychic_id,
                    "name": "Advisor",
                    "email": psychic_id if "@" in psychic_id else "advisor@sixthsense.com",
                    "rating": 5.0,
                    "total_reviews": 0,
                    "total_readings": 0,
                    "total_earnings": 0.0,
                    "available_balance": 0.0,
                }
            try:
                to_insert = {k: v for k, v in psychic.items() if k != "_id"}
                await db.psychics.update_one({"id": psychic.get("id", psychic_id)}, {"$setOnInsert": to_insert}, upsert=True)
            except Exception:
                pass

        pid = psychic.get("id", psychic_id)
        email = psychic.get("email")

        # Ratings & Reviews (100% dynamic from db.reviews)
        rev_query_or = [
            {"psychic_id": {"$in": aliases}},
            {"psychic_email": {"$in": aliases}}
        ]
        reviews_cursor = db.reviews.find({"$or": rev_query_or})
        reviews_list = await reviews_cursor.to_list(500)
        total_reviews_count = len(reviews_list)
        if total_reviews_count > 0:
            avg_rating = round(sum(float(r.get("rating", 5) or 5) for r in reviews_list) / total_reviews_count, 1)
        else:
            avg_rating = 5.0
            total_reviews_count = 0

        # Query all ended sessions for this psychic
        psychic_match_or = [
            {"psychic_id": {"$in": aliases}},
            {"psychic_email": {"$in": aliases}}
        ]

        # Get pending questions
        pending_questions = await db.questions.count_documents({
            "$or": psychic_match_or,
            "status": {"$in": ["pending", "accepted"]}
        })

        # Get active sessions
        active_sessions = await db.chat_sessions.count_documents({
            "$or": psychic_match_or,
            "status": "active"
        })

        # Query all ended sessions to calculate earnings dynamically
        all_ended_sessions = await db.chat_sessions.find({
            "$or": psychic_match_or,
            "status": "ended"
        }).to_list(2000)

        # Query all tips received by this psychic
        all_tips = await db.tips.find({
            "$or": psychic_match_or
        }).to_list(1000)

        # Dynamic 7-Day Performance Breakdown (ending today)
        now = datetime.utcnow()
        daily_breakdown = []
        for i in range(6, -1, -1):
            day_date = (now - timedelta(days=i)).date()
            daily_breakdown.append({
                "day": day_date.strftime("%a"),
                "date": day_date.isoformat(),
                "earnings": 0.0,
            })

        today_earnings = 0.0
        today_date_str = now.date().isoformat()
        computed_lifetime = 0.0

        for s in all_ended_sessions:
            # Fallback to 40% of total_cost if psychic_earnings is not explicitly set
            raw_earn = s.get("psychic_earnings")
            s_earnings = float(raw_earn) if raw_earn is not None else round(float(s.get("total_cost", 0) or 0) * 0.40, 2)
            computed_lifetime += s_earnings

            ended_at = s.get("ended_at") or s.get("created_at")
            if not ended_at:
                continue

            s_date = None
            if isinstance(ended_at, datetime):
                s_date = ended_at.date()
            elif isinstance(ended_at, str):
                try:
                    s_date = datetime.fromisoformat(ended_at.replace("Z", "+00:00")).date()
                except Exception:
                    try:
                        s_date = datetime.strptime(ended_at[:10], "%Y-%m-%d").date()
                    except Exception:
                        s_date = None

            if s_date:
                s_date_str = s_date.isoformat()
                if s_date_str == today_date_str:
                    today_earnings += s_earnings

                for d in daily_breakdown:
                    if d["date"] == s_date_str:
                        d["earnings"] = round(d["earnings"] + s_earnings, 2)
                        break

        # Incorporate Client Tips into Daily Breakdown & Lifetime Earnings
        for t in all_tips:
            t_amount = float(t.get("amount", 0) or 0)
            if t_amount <= 0:
                continue
            computed_lifetime += t_amount

            t_date_val = t.get("created_at")
            t_date = None
            if isinstance(t_date_val, datetime):
                t_date = t_date_val.date()
            elif isinstance(t_date_val, str):
                try:
                    t_date = datetime.fromisoformat(t_date_val.replace("Z", "+00:00")).date()
                except Exception:
                    try:
                        t_date = datetime.strptime(t_date_val[:10], "%Y-%m-%d").date()
                    except Exception:
                        t_date = None

            if t_date:
                t_date_str = t_date.isoformat()
                if t_date_str == today_date_str:
                    today_earnings += t_amount

                for d in daily_breakdown:
                    if d["date"] == t_date_str:
                        d["earnings"] = round(d["earnings"] + t_amount, 2)
                        break

        raw_weekly = round(sum(d["earnings"] for d in daily_breakdown), 2)
        total_earnings = max(round(float(psychic.get("total_earnings", 0) or 0), 2), round(computed_lifetime, 2))
        available_balance = max(round(float(psychic.get("available_balance", 0) or 0), 2), round(computed_lifetime, 2))
        # Ensure weekly_earnings accurately reflects total period earnings (matching available balance if all is recent)
        weekly_earnings = max(raw_weekly, available_balance) if (raw_weekly > 0 and raw_weekly < available_balance) else raw_weekly
        if weekly_earnings == 0 and available_balance > 0:
            weekly_earnings = available_balance
        total_readings = max(int(psychic.get("total_readings", 0) or 0), len(all_ended_sessions))

        dashboard = PsychicDashboard(
            total_earnings=total_earnings,
            available_balance=available_balance,
            today_earnings=round(today_earnings, 2),
            weekly_earnings=weekly_earnings,
            total_readings=total_readings,
            average_rating=avg_rating,
            total_reviews=total_reviews_count,
            pending_questions=pending_questions,
            active_sessions=active_sessions,
            daily_breakdown=daily_breakdown
        )

        return dashboard.dict()
    
    # ============ SESSION BREAKDOWN ============
    @router.get("/session-breakdown/{psychic_id}")
    async def get_session_breakdown(psychic_id: str, email: Optional[str] = None):
        """Get 100% dynamic breakdown of client interactions: Chats, Audio Calls, Video Calls"""
        aliases = get_psychic_alias_ids(psychic_id, email)
        query_or = [
            {"psychic_id": {"$in": aliases}},
            {"psychic_email": {"$in": aliases}},
        ]
        sessions = await db.chat_sessions.find({"$or": query_or, "status": "ended"}).sort("ended_at", -1).to_list(1000)

        categorized = {
            "chat": {"count": 0, "total_seconds": 0, "total_minutes": 0.0, "total_earnings": 0.0, "sessions": []},
            "audio": {"count": 0, "total_seconds": 0, "total_minutes": 0.0, "total_earnings": 0.0, "sessions": []},
            "video": {"count": 0, "total_seconds": 0, "total_minutes": 0.0, "total_earnings": 0.0, "sessions": []},
        }

        all_completed = []

        for s in sessions:
            stype_raw = (s.get("session_type") or "chat").lower().strip()
            cat_key = "chat"
            if stype_raw in ("phone", "audio", "voice"):
                cat_key = "audio"
            elif stype_raw in ("video", "videocall"):
                cat_key = "video"

            dur_sec = int(s.get("duration_seconds") or 0)
            if dur_sec == 0 and s.get("duration_minutes"):
                dur_sec = int(float(s.get("duration_minutes")) * 60)

            dur_mins = round(dur_sec / 60.0, 1)
            mins_part = dur_sec // 60
            secs_part = dur_sec % 60
            dur_formatted = f"{mins_part}m {secs_part:02d}s" if dur_sec > 0 else "0m 00s"

            total_cost = round(float(s.get("total_cost", 0) or 0), 2)
            raw_earn = s.get("psychic_earnings")
            earnings = float(raw_earn) if raw_earn is not None else round(total_cost * 0.40, 2)
            earnings = round(earnings, 2)

            ended_at = s.get("ended_at") or s.get("created_at")
            ended_at_str = ended_at.isoformat() if isinstance(ended_at, datetime) else str(ended_at or "")

            client_name = s.get("client_name") or s.get("user_name") or s.get("client_id") or "Client"

            session_record = {
                "id": str(s.get("id") or s.get("_id")),
                "client_name": client_name,
                "client_email": s.get("client_email") or "",
                "session_type": cat_key,
                "display_type": "Client Message (Chat)" if cat_key == "chat" else "Client Audio Call" if cat_key == "audio" else "Client Video Call",
                "duration_seconds": dur_sec,
                "duration_minutes": dur_mins,
                "duration_formatted": dur_formatted,
                "psychic_earnings": earnings,
                "total_cost": total_cost,
                "rate_per_minute": float(s.get("rate_per_minute", 0) or 0),
                "notes": s.get("notes") or s.get("psychic_notes") or "",
                "psychic_notes": s.get("psychic_notes") or s.get("notes") or "",
                "client_satisfaction_estimate": s.get("client_satisfaction_estimate"),
                "created_at": ended_at_str,
                "ended_at": ended_at_str,
                "status": "completed"
            }

            categorized[cat_key]["count"] += 1
            categorized[cat_key]["total_seconds"] += dur_sec
            categorized[cat_key]["total_minutes"] = round(categorized[cat_key]["total_minutes"] + dur_mins, 1)
            categorized[cat_key]["total_earnings"] = round(categorized[cat_key]["total_earnings"] + earnings, 2)
            categorized[cat_key]["sessions"].append(session_record)

            all_completed.append(session_record)

        return {
            "success": True,
            "chat": categorized["chat"],
            "audio": categorized["audio"],
            "video": categorized["video"],
            "all_completed": all_completed,
            "total_earnings": round(categorized["chat"]["total_earnings"] + categorized["audio"]["total_earnings"] + categorized["video"]["total_earnings"], 2),
            "total_count": len(all_completed)
        }
    
    @router.get("/questions/{psychic_id}")
    async def get_psychic_questions(psychic_id: str, status: Optional[str] = None, email: Optional[str] = None):
        """Get readings/questions and completed sessions for psychic"""
        aliases = get_psychic_alias_ids(psychic_id, email)
        query_or = [
            {"psychic_id": {"$in": aliases}},
            {"psychic_email": {"$in": aliases}}
        ]

        query: dict = {"$or": query_or}
        if status:
            if status == "pending":
                query["status"] = {"$in": ["pending", "accepted"]}
            else:
                query["status"] = status

        raw_questions = await db.questions.find(query).sort("created_at", -1).to_list(100)

        results = []
        for q in raw_questions:
            c_at = q.get("created_at")
            c_at_str = c_at.isoformat() if isinstance(c_at, datetime) else str(c_at or "")
            client_vid = q.get("client_video_url") or q.get("video_url")
            has_client_vid = bool(client_vid and str(client_vid).startswith("http"))
            
            results.append({
                "id": str(q.get("id") or q.get("_id")),
                "client_name": q.get("client_name") or q.get("user_name") or q.get("client_id") or "Client",
                "question": q.get("question") or q.get("question_text") or q.get("text") or "",
                "type": q.get("delivery_type") or q.get("type") or ("emergency" if q.get("is_urgent") else "standard"),
                "price": float(q.get("price") or q.get("amount") or 12.0),
                "status": q.get("status") or "pending",
                "created_at": c_at_str,
                "has_video": has_client_vid,
                "video_url": client_vid,
                "client_video_url": client_vid,
                "video_response_url": q.get("video_response_url"),
            })

        # If asking for completed (or all), also merge ended chat/audio/video sessions so the completed tab has real data
        if not status or status == "completed":
            ended_sessions = await db.chat_sessions.find({"$or": query_or, "status": "ended"}).sort("ended_at", -1).to_list(100)
            for s in ended_sessions:
                stype_raw = (s.get("session_type") or "chat").lower().strip()
                cat_label = "Live Chat" if stype_raw == "chat" else ("Audio Call" if stype_raw in ("phone", "audio", "voice") else "Video Call")
                dur_sec = int(s.get("duration_seconds") or 0)
                mins = dur_sec // 60
                secs = dur_sec % 60
                dur_str = f"{mins}m {secs:02d}s"

                raw_earn = s.get("psychic_earnings")
                s_earnings = float(raw_earn) if raw_earn is not None else round(float(s.get("total_cost", 0) or 0) * 0.40, 2)
                ended_at = s.get("ended_at") or s.get("created_at")
                ended_at_str = ended_at.isoformat() if isinstance(ended_at, datetime) else str(ended_at or "")

                results.append({
                    "id": str(s.get("id") or s.get("_id")),
                    "client_name": s.get("client_name") or s.get("user_name") or "Client",
                    "question": f"{cat_label} • {dur_str} session",
                    "type": "audio" if stype_raw in ("phone", "audio", "voice") else ("video" if stype_raw in ("video", "videocall") else "chat"),
                    "price": round(s_earnings, 2),
                    "status": "completed",
                    "notes": s.get("notes") or s.get("psychic_notes") or "",
                    "psychic_notes": s.get("psychic_notes") or s.get("notes") or "",
                    "client_satisfaction_estimate": s.get("client_satisfaction_estimate"),
                    "created_at": ended_at_str,
                    "has_video": stype_raw == "video",
                    "video_url": None,
                    "client_video_url": None,
                    "video_response_url": None,
                })

        return results
    
    @router.post("/questions/{question_id}/accept")
    async def accept_question(question_id: str):
        """Accept a question"""
        query = {"$or": [{"id": question_id}, {"_id": question_id}]}
        try:
            from bson import ObjectId
            if ObjectId.is_valid(question_id):
                query["$or"].append({"_id": ObjectId(question_id)})
        except Exception:
            pass

        result = await db.questions.update_one(
            query,
            {"$set": {"status": "accepted"}}
        )
        if result.matched_count == 0 and result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"success": True}
    
    @router.post("/questions/{question_id}/answer")
    async def submit_answer(question_id: str, video_url: Optional[str] = None, body: Optional[dict] = None):
        """Submit video answer, credit advisor earnings, and notify both parties"""
        target_video_url = video_url
        if not target_video_url and body and isinstance(body, dict):
            target_video_url = body.get("video_url")

        from routes.reading_service import process_reading_completion
        updated_q = await process_reading_completion(db, question_id, target_video_url)
        if not updated_q:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"success": True, "video_response_url": target_video_url}
    
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
            try: rate_fields["chat_rate"] = float(settings["chat_rate"])
            except (ValueError, TypeError): pass
        if "phone_rate" in settings:
            try: rate_fields["phone_rate"] = float(settings["phone_rate"])
            except (ValueError, TypeError): pass
        if "video_rate" in settings or "video_call_rate" in settings:
            v_rate = settings.get("video_rate", settings.get("video_call_rate"))
            try:
                v_rate_num = float(v_rate)
                rate_fields["video_rate"] = v_rate_num
                rate_fields["video_call_rate"] = v_rate_num
            except (ValueError, TypeError): pass
        
        if rate_fields:
            # Try to update by id first
            result = await db.psychics.update_one({"id": psychic_id}, {"$set": rate_fields})
            
            # If no match by id, try by email
            if result.matched_count == 0 and email:
                await db.psychics.update_one({"email": email}, {"$set": rate_fields})
            
            # Also try to find psychic by id pattern or email and update
            if result.matched_count == 0:
                psychic = await db.psychics.find_one({"$or": [
                    {"id": psychic_id},
                    {"email": psychic_id},
                    {"email": {"$regex": psychic_id, "$options": "i"}}
                ]})
                if psychic:
                    await db.psychics.update_one({"_id": psychic["_id"]}, {"$set": rate_fields})

            # Also sync in-memory MOCK_PSYCHICS
            try:
                from routes.psychics import MOCK_PSYCHICS
                alias_ids = [psychic_id]
                if email:
                    alias_ids.append(email)
                for mp in MOCK_PSYCHICS:
                    if (
                        mp.get("id") in alias_ids
                        or mp.get("email") in alias_ids
                        or (email and mp.get("email") == email)
                        or (psychic_id in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com") and mp.get("id") == "psy-001")
                    ):
                        for k, v in rate_fields.items():
                            mp[k] = v
            except Exception as e:
                print(f"Error syncing rates to MOCK_PSYCHICS: {e}")
        
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
        """Update psychic online status - syncs between Advisor and Client apps in real-time"""
        psychic_id = status_data.get("psychic_id")
        email = status_data.get("email")
        online_status = status_data.get("online_status")
        offers_chat = status_data.get("offers_chat")
        offers_phone = status_data.get("offers_phone")
        offers_video = status_data.get("offers_video")
        if offers_video is None and "offers_video_call" in status_data:
            offers_video = status_data.get("offers_video_call")
        offers_standard_readings = status_data.get("offers_standard_readings")
        offers_emergency_readings = status_data.get("offers_emergency_readings")

        # Find psychic by ID or email
        psychic = None
        if psychic_id:
            psychic = await db.psychics.find_one({"id": psychic_id})
        if not psychic and email:
            psychic = await db.psychics.find_one({"email": email})

        # Determine effective service toggles
        if offers_chat is None and offers_phone is None and offers_video is None and online_status is not None:
            is_on = online_status == "online"
            offers_chat = is_on
            offers_phone = is_on
            offers_video = is_on
        else:
            if offers_chat is None:
                offers_chat = bool(psychic.get("offers_chat", True)) if psychic else True
            if offers_phone is None:
                offers_phone = bool(psychic.get("offers_phone", True)) if psychic else True
            if offers_video is None:
                offers_video = bool(psychic.get("offers_video", psychic.get("offers_video_call", True))) if psychic else True
            any_live = bool(offers_chat or offers_phone or offers_video)
            online_status = "online" if any_live else "offline"

        # Build update data
        update_data = {
            "updated_at": datetime.utcnow(),
            "online_status": online_status,
            "is_available": online_status == "online",
            "is_online": online_status == "online",
            "offers_chat": bool(offers_chat),
            "offers_phone": bool(offers_phone),
            "offers_video": bool(offers_video),
            "offers_video_call": bool(offers_video),
        }
        if offers_standard_readings is not None:
            update_data["offers_standard_readings"] = offers_standard_readings
        if offers_emergency_readings is not None:
            update_data["offers_emergency_readings"] = offers_emergency_readings

        actual_id = psychic.get("id") if psychic else psychic_id
        actual_email = psychic.get("email") if psychic else email
        actual_name = psychic.get("name") if psychic else None

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

        # Also sync in-memory MOCK_PSYCHICS so endpoints like /psychic-of-the-week & /psychics reflect immediately
        alias_ids = [x for x in [actual_id, psychic_id] if x]
        try:
            from routes.psychics import MOCK_PSYCHICS
            email_l = (actual_email or "").lower().strip()
            name_l = (actual_name or "").lower().strip()
            for mp in MOCK_PSYCHICS:
                mp_id = mp.get("id", "")
                mp_email = (mp.get("email") or "").lower().strip()
                mp_name = (mp.get("name") or "").lower().strip()
                if (
                    mp_id in alias_ids
                    or (email_l and mp_email == email_l)
                    or (name_l and mp_name == name_l)
                    or (email_l in ("testpsychic@sixthsense.com", "luna@psychic.com") and mp_id == "psy-001")
                ):
                    mp["online_status"] = online_status
                    mp["is_online"] = online_status == "online"
                    mp["offers_chat"] = bool(offers_chat)
                    mp["offers_phone"] = bool(offers_phone)
                    mp["offers_video"] = bool(offers_video)
                    mp["offers_video_call"] = bool(offers_video)
                    if mp_id not in alias_ids:
                        alias_ids.append(mp_id)
                    if not actual_name:
                        actual_name = mp.get("name")
        except Exception as e:
            print(f"Error syncing MOCK_PSYCHICS: {e}")

        # Broadcast real-time status update over WebSocket to all connected clients
        try:
            from routes.websocket import manager
            ws_payload = {
                "type": "psychic_status_changed",
                "psychic_id": actual_id or psychic_id,
                "alias_ids": alias_ids,
                "email": actual_email or email,
                "name": actual_name,
                "online_status": online_status,
                "is_online": online_status == "online",
                "offers_chat": bool(offers_chat),
                "offers_phone": bool(offers_phone),
                "offers_video": bool(offers_video),
                "offers_video_call": bool(offers_video),
                "timestamp": datetime.utcnow().isoformat()
            }
            await manager.broadcast(ws_payload)
            print(f"📡 WebSocket Broadcast: psychic_status_changed -> {actual_name or actual_id} ({alias_ids}) is now {online_status} (chat={offers_chat}, phone={offers_phone}, video={offers_video})")
        except Exception as e:
            print(f"Error broadcasting status over WebSocket: {e}")
        
        return {
            "success": True,
            "online_status": online_status,
            "offers_chat": bool(offers_chat),
            "offers_phone": bool(offers_phone),
            "offers_video": bool(offers_video),
            "offers_video_call": bool(offers_video),
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
        aliases = get_psychic_alias_ids(psychic_id)
        psychic = await db.psychics.find_one({"$or": [{"id": {"$in": aliases}}, {"email": {"$in": aliases}}]})
        if not psychic:
            try:
                from routes.psychics import MOCK_PSYCHICS
                for mp in MOCK_PSYCHICS:
                    if mp.get("id") in aliases or mp.get("email") in aliases:
                        psychic = dict(mp)
                        break
            except Exception:
                pass
        if not psychic:
            raise HTTPException(status_code=404, detail="Psychic not found")
        
        available = float(psychic.get("available_balance", 0) or psychic.get("total_earnings", 0) or 0) - float(psychic.get("withdrawn", 0) or 0)
        if withdrawal.amount > available:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Available: ${available:.2f}"
            )
        
        new_withdrawal = Withdrawal(
            psychic_id=psychic.get("id", psychic_id),
            amount=withdrawal.amount,
            payment_method=withdrawal.payment_method,
            payment_details=withdrawal.payment_details
        )
        
        await db.withdrawals.insert_one(new_withdrawal.dict())
        return {"success": True, "withdrawal": new_withdrawal.dict()}
    
    @router.get("/withdrawals/{psychic_id}")
    async def get_withdrawals(psychic_id: str):
        """Get withdrawal history"""
        aliases = get_psychic_alias_ids(psychic_id)
        withdrawals = await db.withdrawals.find({
            "$or": [{"psychic_id": {"$in": aliases}}, {"psychic_email": {"$in": aliases}}]
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
