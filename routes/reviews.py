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
        """Create a review (with 90-day cooldown) - only for users with completed sessions of 5+ minutes"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        if review.rating < 1 or review.rating > 5:
            raise HTTPException(status_code=400, detail="Rating must be 1-5")
        
        # Verify user has had a completed session with this psychic (minimum 5 minutes)
        completed_session = await db.chat_sessions.find_one({
            "client_id": user_id,
            "psychic_id": review.psychic_id,
            "status": {"$in": ["completed", "ended"]},
            "$or": [
                {"duration_minutes": {"$gte": 5}},
                {"actual_duration": {"$gte": 5}},
                {"total_minutes": {"$gte": 5}}
            ]
        })
        
        if not completed_session:
            # Check if they have ANY session (to give appropriate error message)
            any_session = await db.chat_sessions.find_one({
                "client_id": user_id,
                "psychic_id": review.psychic_id,
                "status": {"$in": ["completed", "ended"]}
            })
            
            if any_session:
                raise HTTPException(
                    status_code=403, 
                    detail="Sessions must be at least 5 minutes long to leave a review"
                )
            else:
                raise HTTPException(
                    status_code=403, 
                    detail="You can only review advisors you've had a session with"
                )
        
        # Check cooldown - last review for this psychic by this user
        cooldown_date = datetime.utcnow() - timedelta(days=REVIEW_COOLDOWN_DAYS)
        recent_review = await db.reviews.find_one({
            "user_id": user_id,
            "psychic_id": review.psychic_id,
            "created_at": {"$gte": cooldown_date}
        })
        
        if recent_review:
            days_left = REVIEW_COOLDOWN_DAYS - (datetime.utcnow() - recent_review.get("created_at", datetime.utcnow())).days
            raise HTTPException(
                status_code=400, 
                detail=f"You can review this psychic again in {days_left} days"
            )
        
        new_review = Review(
            user_id=user_id,
            psychic_id=review.psychic_id,
            session_id=review.session_id,
            rating=review.rating,
            comment=review.comment
        )
        await db.reviews.insert_one(new_review.dict())
        
        # Update psychic's average rating
        all_reviews = await db.reviews.find({"psychic_id": review.psychic_id}).to_list(1000)
        if all_reviews:
            avg_rating = sum(r.get("rating", 0) for r in all_reviews) / len(all_reviews)
            await db.psychics.update_one(
                {"id": review.psychic_id},
                {
                    "$set": {"average_rating": round(avg_rating, 2)},
                    "$inc": {"total_reviews": 1}
                }
            )
        
        # If user gave 4-5 star rating, prompt them to rate the app
        if review.rating >= 4:
            await send_app_rating_prompt(user_id, "client", f"positive_review_{review.rating}_stars")
            
            # Also notify psychic if they got a 5-star review
            if review.rating == 5:
                await send_app_rating_prompt(review.psychic_id, "psychic", "received_5_star_review")
        
        return {"success": True, "review": new_review.dict()}
    
    @router.get("/psychic/{psychic_id}")
    async def get_psychic_reviews(psychic_id: str, limit: int = 20):
        """Get reviews for a psychic"""
        reviews = await db.reviews.find({"psychic_id": psychic_id}).sort("created_at", -1).to_list(limit)
        return reviews
    
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
