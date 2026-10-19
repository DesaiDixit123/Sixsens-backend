from fastapi import APIRouter, HTTPException
from typing import List
from datetime import datetime, timedelta
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.review import Review, ReviewCreate

REVIEW_COOLDOWN_DAYS = 90  # 90-day cooldown between reviews for same psychic

def create_reviews_routes(db):
    router = APIRouter(prefix="/reviews", tags=["reviews"])
    
    async def send_app_rating_prompt(user_id: str, user_type: str, trigger: str):
        """Send app rating prompt notification"""
        try:
            # Check if we already prompted this user recently (within 30 days)
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            recent_prompt = await db.notifications.find_one({
                "user_id": user_id,
                "type": "app_rating_prompt",
                "created_at": {"$gte": thirty_days_ago}
            })
            
            if recent_prompt:
                return None
            
            messages = {
                "client_positive": {
                    "title": "Enjoying Your Experience? ⭐",
                    "body": "We noticed you had a great reading! If you're loving the app, please take a moment to rate us!"
                },
                "psychic_positive": {
                    "title": "You're Doing Amazing! 🌟",
                    "body": "Congratulations on that 5-star rating! If you enjoy being an advisor here, please rate the app!"
                }
            }
            
            message = messages.get(f"{user_type}_positive")
            if not message:
                return None
            
            notification = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "title": message["title"],
                "body": message["body"],
                "type": "app_rating_prompt",
                "trigger": trigger,
                "is_read": False,
                "created_at": datetime.utcnow()
            }
            
            await db.notifications.insert_one(notification)
            return notification
        except Exception as e:
            print(f"Error sending app rating prompt: {e}")
            return None
    
    @router.post("/")
    async def create_review(review: ReviewCreate, user_id: str = None):
        """Create a review for a completed reading session"""
        uid = user_id if (user_id and user_id != "undefined" and user_id != "null") else (getattr(review, "user_id", None) or "usr_client")
        
        if review.rating < 1 or review.rating > 5:
            raise HTTPException(status_code=400, detail="Rating must be between 1 and 5 stars")
        
        # Resolve psychic (with demo fallback)
        psychic = await db.psychics.find_one({"$or": [{"id": review.psychic_id}, {"email": review.psychic_id}]})
        if not psychic:
            psychic = await db.psychics.find_one({"name": {"$regex": f"^{review.psychic_id}$", "$options": "i"}})
        if not psychic and ("psy-" in str(review.psychic_id) or "test" in str(review.psychic_id)):
            psychic = await db.psychics.find_one({"email": "testpsychic@sixthsense.com"})
            if not psychic:
                psychic = await db.psychics.find_one({"id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0"})

        pid = psychic.get("id", review.psychic_id) if psychic else review.psychic_id
        pemail = psychic.get("email", "") if psychic else ""

        # Check duplicate review for the same session if session_id is provided
        if review.session_id:
            existing_for_session = await db.reviews.find_one({
                "session_id": review.session_id,
                "user_id": uid
            })
            if existing_for_session:
                raise HTTPException(
                    status_code=400, 
                    detail="You have already submitted a review for this session"
                )

        review_dict = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "psychic_id": pid,
            "psychic_email": pemail,
            "session_id": review.session_id or f"session_{uuid.uuid4().hex[:12]}",
            "rating": int(review.rating),
            "comment": review.comment or "",
            "created_at": datetime.utcnow()
        }
        await db.reviews.insert_one(review_dict)
        
        # Update psychic's average rating and total reviews
        all_reviews = await db.reviews.find({"$or": [{"psychic_id": pid}, {"psychic_email": pemail or pid}]}).to_list(1000)
        if all_reviews:
            avg_rating = sum(r.get("rating", 0) for r in all_reviews) / len(all_reviews)
            await db.psychics.update_one(
                {"$or": [{"id": pid}, {"email": pemail or pid}]},
                {
                    "$set": {"average_rating": round(avg_rating, 2)},
                    "$inc": {"total_reviews": 1}
                }
            )
        
        # If user gave 4-5 star rating, prompt them to rate the app
        if review.rating >= 4:
            await send_app_rating_prompt(uid, "client", f"positive_review_{review.rating}_stars")
            
            # Also notify psychic if they got a 5-star review
            if review.rating == 5:
                await send_app_rating_prompt(pid, "psychic", "received_5_star_review")
        
        review_dict.pop("_id", None)
        review_dict["created_at"] = review_dict["created_at"].isoformat()
        return {"success": True, "review": review_dict}
    
    @router.get("/psychic/{psychic_id}")
    async def get_psychic_reviews(psychic_id: str, limit: int = 50):
        """Get enriched reviews for a psychic with client names and statistics"""
        psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"email": psychic_id}]})
        pid = psychic.get("id", psychic_id) if psychic else psychic_id
        pemail = psychic.get("email") if psychic else None

        query_or = [{"psychic_id": pid}]
        if pemail:
            query_or.append({"psychic_email": pemail})
            query_or.append({"psychic_id": pemail})
        if pid == "64ea86f6-968a-4dfc-8c98-1a322fb020c0" or pemail == "testpsychic@sixthsense.com":
            query_or.append({"psychic_id": "psy-001"})

        raw_reviews = await db.reviews.find({"$or": query_or}).sort("created_at", -1).to_list(limit)

        reviews_list = []
        breakdown = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        total_rating_sum = 0

        for r in raw_reviews:
            r_rating = int(r.get("rating", 5) or 5)
            total_rating_sum += r_rating
            breakdown[r_rating] = breakdown.get(r_rating, 0) + 1

            uid = r.get("user_id")
            client = await db.users.find_one({"id": uid}) if uid else None
            client_name = (client.get("name") if client else None) or r.get("client_name") or "Verified Client"

            c_at = r.get("created_at")
            c_at_str = c_at.isoformat() if isinstance(c_at, datetime) else str(c_at or "")

            reviews_list.append({
                "id": str(r.get("id") or r.get("_id")),
                "user_id": uid,
                "client_name": client_name,
                "rating": r_rating,
                "comment": r.get("comment") or "",
                "session_id": r.get("session_id") or "",
                "created_at": c_at_str,
            })

        total_reviews = len(reviews_list)
        avg_rating = round(total_rating_sum / total_reviews, 1) if total_reviews > 0 else 5.0

        return {
            "reviews": reviews_list,
            "total_reviews": total_reviews,
            "average_rating": avg_rating,
            "rating_breakdown": breakdown
        }
    
    @router.get("/can-review/{psychic_id}")
    async def can_review(psychic_id: str, user_id: str = None):
        """Check if user can review this psychic (cooldown check)"""
        if not user_id:
            return {"can_review": False, "reason": "Not logged in"}
        
        cooldown_date = datetime.utcnow() - timedelta(days=REVIEW_COOLDOWN_DAYS)
        recent_review = await db.reviews.find_one({
            "user_id": user_id,
            "psychic_id": psychic_id,
            "created_at": {"$gte": cooldown_date}
        })
        
        if recent_review:
            days_left = REVIEW_COOLDOWN_DAYS - (datetime.utcnow() - recent_review.get("created_at", datetime.utcnow())).days
            return {"can_review": False, "days_left": days_left}
        
        return {"can_review": True}
    
    @router.get("/public/testimonials")
    async def get_public_testimonials(limit: int = 6):
        """
        Get public testimonials for the landing page
        Returns recent 4-5 star reviews with user initials (privacy)
        """
        # Get recent high-rated reviews (4-5 stars)
        reviews = await db.reviews.find({
            "rating": {"$gte": 4}
        }).sort("created_at", -1).limit(limit * 2).to_list(limit * 2)
        
        testimonials = []
        for review in reviews:
            if len(testimonials) >= limit:
                break
                
            # Get user info for initials
            user = await db.users.find_one({"id": review.get("user_id")})
            if user and review.get("comment"):
                # Get user's first name initial and last initial for privacy
                full_name = user.get("name", "Anonymous")
                name_parts = full_name.split()
                if len(name_parts) >= 2:
                    display_name = f"{name_parts[0]} {name_parts[-1][0]}."
                else:
                    display_name = f"{name_parts[0][0]}."
                
                testimonials.append({
                    "name": display_name,
                    "text": review.get("comment", ""),
                    "rating": review.get("rating", 5),
                    "created_at": review.get("created_at")
                })
        
        return testimonials
    
    return router
