from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
import os
import httpx

# Emergent LLM Key for AI features
EMERGENT_LLM_KEY = os.getenv("EMERGENT_LLM_KEY", "")

# Try to import emergentintegrations for proper API access
try:
    from emergentintegrations.llm import EmergentLLM
    HAS_EMERGENT_SDK = True
except ImportError:
    HAS_EMERGENT_SDK = False

def create_horoscope_routes(db):
    router = APIRouter(prefix="/horoscope", tags=["horoscope"])
    
    ZODIAC_SIGNS = [
        "aries", "taurus", "gemini", "cancer", "leo", "virgo",
        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"
    ]
    
    ZODIAC_DATES = {
        "aries": ("March 21", "April 19"),
        "taurus": ("April 20", "May 20"),
        "gemini": ("May 21", "June 20"),
        "cancer": ("June 21", "July 22"),
        "leo": ("July 23", "August 22"),
        "virgo": ("August 23", "September 22"),
        "libra": ("September 23", "October 22"),
        "scorpio": ("October 23", "November 21"),
        "sagittarius": ("November 22", "December 21"),
        "capricorn": ("December 22", "January 19"),
        "aquarius": ("January 20", "February 18"),
        "pisces": ("February 19", "March 20")
    }
    
    async def generate_ai_horoscope(sign: str, period: str = "daily"):
        """Generate AI horoscope using Emergent LLM"""
        try:
            if HAS_EMERGENT_SDK and EMERGENT_LLM_KEY:
                # Use Emergent SDK
                llm = EmergentLLM(api_key=EMERGENT_LLM_KEY)
                prompt = f"""Generate a {period} mindful guidance for {sign.capitalize()}. Include:
1. Overall energy/theme for the day
2. Love & Relationships advice
3. Career & Finance guidance
4. Lucky numbers (3 numbers between 1-99)
5. A brief inspirational message

Format as JSON with keys: overall, love, career, lucky_numbers (array), message. Return ONLY valid JSON, no other text."""
                
                response = await llm.chat_async(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are a mystical astrologer providing insightful, positive, and empowering mindful guidance readings. Keep responses concise (2-3 sentences per section) and uplifting. Always respond with valid JSON only."},
                        {"role": "user", "content": prompt}
                    ]
                )
                
                content = response.get("content", "") if isinstance(response, dict) else str(response)
                
                # Try to parse JSON from response
                import json
                try:
                    # Extract JSON from response
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0]
                    return json.loads(content.strip())
                except Exception:
                    # Fallback if JSON parsing fails
                    return get_fallback_horoscope(sign)
            else:
                # Use fallback if no SDK or key
                return get_fallback_horoscope(sign)
        except Exception as e:
            print(f"AI horoscope error: {e}")
            # Return fallback mindful guidance
            return get_fallback_horoscope(sign)
    
    def get_fallback_horoscope(sign: str):
        """Fallback mindful guidance if AI fails"""
        fallbacks = {
            "aries": {
                "overall": "Your fiery energy is strong today. Channel it into productive endeavors and watch opportunities unfold.",
                "love": "Open your heart to unexpected connections. Someone special may surprise you.",
                "career": "Take bold action on that project you've been considering. Leadership opportunities await.",
                "lucky_numbers": [9, 27, 45],
                "message": "Your courage will light the way forward."
            },
            "taurus": {
                "overall": "Stability and comfort are your themes today. Trust your practical instincts.",
                "love": "Nurture existing relationships with patience and care.",
                "career": "Financial opportunities may present themselves. Stay grounded in decisions.",
                "lucky_numbers": [6, 18, 33],
                "message": "Slow and steady wins the race."
            },
            "gemini": {
                "overall": "Communication flows easily today. Express yourself freely and connect with others.",
                "love": "Conversations lead to deeper understanding in relationships.",
                "career": "Networking and collaboration bring success. Share your ideas boldly.",
                "lucky_numbers": [5, 14, 23],
                "message": "Your words have power today."
            },
            "cancer": {
                "overall": "Emotional intuition is heightened. Trust your feelings and inner guidance.",
                "love": "Home and family bring comfort. Nurture those closest to you.",
                "career": "Creative projects flourish. Let your imagination guide you.",
                "lucky_numbers": [2, 11, 29],
                "message": "Your sensitivity is your strength."
            },
            "leo": {
                "overall": "Your natural charisma shines brightly today. Embrace the spotlight.",
                "love": "Romance and passion are highlighted. Express your heart boldly.",
                "career": "Leadership roles suit you well. Take charge with confidence.",
                "lucky_numbers": [1, 19, 37],
                "message": "You were born to shine."
            },
            "virgo": {
                "overall": "Attention to detail serves you well today. Organization brings peace.",
                "love": "Small gestures of love mean the most. Show care through actions.",
                "career": "Your analytical skills solve complex problems. Trust your methods.",
                "lucky_numbers": [4, 16, 32],
                "message": "Perfection is found in the details."
            },
            "libra": {
                "overall": "Balance and harmony guide your day. Seek beauty in all things.",
                "love": "Partnerships thrive through compromise and understanding.",
                "career": "Diplomacy opens doors. Use your charm wisely.",
                "lucky_numbers": [7, 15, 24],
                "message": "Beauty surrounds you when you seek it."
            },
            "scorpio": {
                "overall": "Deep transformation is possible today. Embrace change fearlessly.",
                "love": "Intensity deepens connections. Don't fear vulnerability.",
                "career": "Investigative work yields discoveries. Dig deeper.",
                "lucky_numbers": [8, 17, 44],
                "message": "From darkness comes your greatest light."
            },
            "sagittarius": {
                "overall": "Adventure calls to you today. Expand your horizons boldly.",
                "love": "Freedom and growth strengthen relationships. Share your dreams.",
                "career": "Big-picture thinking leads to success. Aim high.",
                "lucky_numbers": [3, 21, 39],
                "message": "The journey is the destination."
            },
            "capricorn": {
                "overall": "Ambition and discipline drive you forward. Stay focused on goals.",
                "love": "Loyalty and commitment deepen bonds. Build together.",
                "career": "Hard work pays off today. Recognition awaits.",
                "lucky_numbers": [10, 28, 46],
                "message": "Your persistence will be rewarded."
            },
            "aquarius": {
                "overall": "Innovation and originality spark breakthroughs. Think differently.",
                "love": "Friendship forms the foundation of lasting love.",
                "career": "Revolutionary ideas gain traction. Share your vision.",
                "lucky_numbers": [11, 22, 47],
                "message": "Your uniqueness changes the world."
            },
            "pisces": {
                "overall": "Intuition and creativity flow strongly. Trust your inner wisdom.",
                "love": "Compassion and empathy deepen connections. Love unconditionally.",
                "career": "Artistic and spiritual pursuits flourish. Follow your dreams.",
                "lucky_numbers": [12, 20, 48],
                "message": "Your dreams hold the key."
            }
        }
        return fallbacks.get(sign.lower(), fallbacks["aries"])
    
    @router.get("/signs")
    async def get_zodiac_signs():
        """Get all zodiac signs with their date ranges"""
        return [
            {"sign": sign, "start": dates[0], "end": dates[1]}
            for sign, dates in ZODIAC_DATES.items()
        ]
    
    @router.get("/{sign}")
    async def get_horoscope(sign: str, period: str = "daily", use_ai: bool = True):
        """Get mindful guidance for a zodiac sign"""
        sign_lower = sign.lower()
        if sign_lower not in ZODIAC_SIGNS:
            raise HTTPException(status_code=400, detail="Invalid zodiac sign")
        
        # Check cache first (with error handling for missing DB)
        cache_key = f"{sign_lower}_{period}_{datetime.utcnow().strftime('%Y-%m-%d')}"
        cached = None
        try:
            cached = await db.horoscope_cache.find_one({"cache_key": cache_key})
        except Exception as e:
            print(f"Cache lookup error: {e}")
        
        if cached and cached.get("horoscope"):
            return {
                "sign": sign_lower,
                "period": period,
                "date": datetime.utcnow().strftime("%Y-%m-%d"),
                "horoscope": cached.get("horoscope"),
                "cached": True
            }
        
        # Generate new mindful guidance
        if use_ai:
            horoscope_data = await generate_ai_horoscope(sign_lower, period)
        else:
            horoscope_data = get_fallback_horoscope(sign_lower)
        
        # Cache the result (with error handling)
        try:
            await db.horoscope_cache.update_one(
                {"cache_key": cache_key},
                {"$set": {
                    "sign": sign_lower,
                    "period": period,
                    "horoscope": horoscope_data,
                    "created_at": datetime.utcnow()
                }},
                upsert=True
            )
        except Exception as e:
            print(f"Cache write error: {e}")
        
        return {
            "sign": sign_lower,
            "period": period,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "horoscope": horoscope_data,
            "cached": False
        }
    
    @router.get("/compatibility/{sign1}/{sign2}")
    async def get_compatibility(sign1: str, sign2: str):
        """Get compatibility between two signs"""
        sign1_lower = sign1.lower()
        sign2_lower = sign2.lower()
        
        if sign1_lower not in ZODIAC_SIGNS or sign2_lower not in ZODIAC_SIGNS:
            raise HTTPException(status_code=400, detail="Invalid zodiac sign")
        
        # Simple compatibility matrix (mock)
        # In production, this would be more sophisticated
        compatibility_scores = {
            ("fire", "fire"): 85,
            ("fire", "air"): 90,
            ("fire", "water"): 50,
            ("fire", "earth"): 60,
            ("air", "air"): 80,
            ("air", "water"): 55,
            ("air", "earth"): 65,
            ("water", "water"): 90,
            ("water", "earth"): 85,
            ("earth", "earth"): 75
        }
        
        elements = {
            "aries": "fire", "leo": "fire", "sagittarius": "fire",
            "taurus": "earth", "virgo": "earth", "capricorn": "earth",
            "gemini": "air", "libra": "air", "aquarius": "air",
            "cancer": "water", "scorpio": "water", "pisces": "water"
        }
        
        e1 = elements.get(sign1_lower)
        e2 = elements.get(sign2_lower)
        key = tuple(sorted([e1, e2]))
        score = compatibility_scores.get(key, 70)
        
        return {
            "sign1": sign1_lower,
            "sign2": sign2_lower,
            "compatibility_score": score,
            "elements": {"sign1": e1, "sign2": e2},
            "description": f"{sign1.capitalize()} and {sign2.capitalize()} have a {score}% compatibility."
        }
    
    return router
