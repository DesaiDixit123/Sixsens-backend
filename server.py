from fastapi import FastAPI, APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List
import uuid
from datetime import datetime, timedelta
import sys
import random
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add backend directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

ROOT_DIR = Path(__file__).parent

# MongoDB connection settings
mongo_url = os.getenv('MONGO_URL', 'mongodb://localhost:27017/sixthsense')
db_name = os.getenv('DB_NAME', 'sixthsense')

# Initialize MongoDB client with connection pool bounds (BE-017)
def get_db():
    try:
        client = AsyncIOMotorClient(
            mongo_url,
            serverSelectionTimeoutMS=5000,
            maxPoolSize=50,
            minPoolSize=10
        )
        return client[db_name]
    except Exception as e:
        print(f"MongoDB connection error: {e}")
        raise

db = get_db()

async def init_db_indexes():
    """Initialize MongoDB compound indexes for performance (BE-016, BE-021, BE-025)"""
    try:
        await db.users.create_index([("email", 1), ("created_at", -1)])
        await db.chat_sessions.create_index([("psychic_id", 1), ("status", 1)])
        await db.messages.create_index([("session_id", 1), ("timestamp", 1)])
        await db.notifications.create_index([("user_id", 1), ("is_read", 1)])
        await db.psychics.create_index([("status", 1), ("is_online", -1)])
        print("✅ MongoDB compound indexes initialized successfully!")
    except Exception as e:
        print(f"⚠️ Index initialization notice: {e}")

async def cleanup_stale_sessions():
    """Cleanup hung/abandoned connecting call sessions older than 5 minutes (BE-030)"""
    try:
        five_mins_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        result = await db.chat_sessions.update_many(
            {"status": "connecting", "created_at": {"$lt": five_mins_ago}},
            {"$set": {"status": "expired", "expired_at": datetime.utcnow().isoformat()}}
        )
        if result.modified_count > 0:
            print(f"🧹 Cleaned up {result.modified_count} stale call sessions.")
    except Exception as e:
        print(f"Error cleaning stale sessions: {e}")

# Create the main app
app = FastAPI(title="Psychic Marketplace API", version="1.0.0")

@app.on_event("startup")
async def on_startup():
    await init_db_indexes()
    await cleanup_stale_sessions()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Import route modules
from routes.auth import create_auth_routes
from routes.psychics import create_psychic_routes, load_deleted_psychic_ids
from routes.wallet import create_wallet_routes
from routes.questions import create_questions_routes
from routes.favorites import create_favorites_routes
from routes.tips import create_tips_routes
from routes.reviews import create_reviews_routes
from routes.chat import create_chat_routes
from routes.psychic_portal import create_psychic_portal_routes
from routes.admin import create_admin_routes
from routes.horoscope import create_horoscope_routes
from routes.messages import create_messages_routes
from routes.support import create_support_routes
from routes.applications import create_application_routes
from routes.notifications import create_notifications_routes
from routes.websocket import create_websocket_routes
from routes.payments import create_payment_routes
from routes.email import create_email_routes
from routes.storage import create_storage_routes
from routes.video import create_video_routes
from routes.recorded_readings import create_recorded_readings_routes
from routes.push_notifications import create_push_notification_routes
from routes.psychics import create_user_routes
from routes.recorded_video import create_recorded_video_routes
from routes.stripe_connect import create_stripe_connect_router

# Define Models
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Psychic Marketplace API v1.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@api_router.get("/create-test-accounts")
async def create_test_accounts():
    """Create test accounts for Client and Advisor apps - for testing purposes"""
    try:
        import bcrypt
        
        # Password hash for "Tester123"
        password_hash = bcrypt.hashpw("Tester123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Create/Update psychic user account
        psychic_user = await db.users.find_one({"email": "testpsychic@sixthsense.com"})
        
        if psychic_user:
            # Update existing user's password
            await db.users.update_one(
                {"email": "testpsychic@sixthsense.com"},
                {"$set": {"password_hash": password_hash, "is_verified": True, "status": "active"}}
            )
        else:
            # Create psychic user account
            user_doc = {
                "email": "testpsychic@sixthsense.com",
                "password_hash": password_hash,
                "name": "Luna Starweaver",
                "user_type": "psychic",
                "is_verified": True,
                "status": "active",
                "created_at": datetime.utcnow()
            }
            await db.users.insert_one(user_doc)
        
        # Create/Update psychic profile
        existing_psychic = await db.psychics.find_one({"email": "testpsychic@sixthsense.com"})
        if existing_psychic:
            await db.psychics.update_one(
                {"email": "testpsychic@sixthsense.com"},
                {"$set": {"online_status": "online", "is_available": True, "is_verified": True, "status": "active"}}
            )
        else:
            # Create new psychic profile
            psychic_doc = {
                "email": "testpsychic@sixthsense.com",
                "name": "Luna Starweaver",
                "specialties": ["Love & Relationships", "Career"],
                "reading_methods": ["Tarot", "Clairvoyance"],
                "price_per_minute": 3.99,
                "rating": 4.8,
                "total_readings": 150,
                "online_status": "online",
                "is_available": True,
                "is_verified": True,
                "status": "active",
                "bio": "Experienced psychic advisor ready to help guide your path.",
                "created_at": datetime.utcnow()
            }
            await db.psychics.insert_one(psychic_doc)
        
        # Also create client test account
        client_user = await db.users.find_one({"email": "tester@sixthsense.com"})
        if client_user:
            await db.users.update_one(
                {"email": "tester@sixthsense.com"},
                {"$set": {"password_hash": password_hash, "is_verified": True, "status": "active"}}
            )
        else:
            client_doc = {
                "email": "tester@sixthsense.com",
                "password_hash": password_hash,
                "name": "Test Client",
                "user_type": "client",
                "is_verified": True,
                "status": "active",
                "created_at": datetime.utcnow()
            }
            await db.users.insert_one(client_doc)
        
        # Create wallet for client with credits (100 free credits for testing)
        await db.wallets.update_one(
            {"email": "tester@sixthsense.com"},
            {"$set": {"balance": 100.0, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        
        # Also update user balance directly
        await db.users.update_one(
            {"email": "tester@sixthsense.com"},
            {"$set": {"balance": 100.0}}
        )
        
        # Also create the freelancer's test accounts
        advisor_password_hash = bcrypt.hashpw("advisor123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Create advisor@psychic.com account
        advisor_user = await db.users.find_one({"email": "advisor@psychic.com"})
        if advisor_user:
            await db.users.update_one(
                {"email": "advisor@psychic.com"},
                {"$set": {"password_hash": advisor_password_hash, "is_verified": True, "status": "active", "user_type": "psychic"}}
            )
        else:
            await db.users.insert_one({
                "email": "advisor@psychic.com",
                "password_hash": advisor_password_hash,
                "name": "Luna Mystic",
                "user_type": "psychic",
                "is_verified": True,
                "status": "active",
                "created_at": datetime.utcnow()
            })
        
        # Create/Update psychic profile for advisor@psychic.com - MUST be "approved" to show in Client app
        existing_advisor = await db.psychics.find_one({"email": "advisor@psychic.com"})
        if existing_advisor:
            await db.psychics.update_one(
                {"email": "advisor@psychic.com"},
                {"$set": {
                    "name": "Luna Mystic",
                    "online_status": "online",
                    "is_available": True,
                    "is_verified": True,
                    "status": "approved"
                }}
            )
        else:
            await db.psychics.insert_one({
                "id": "test-luna-mystic",
                "email": "advisor@psychic.com",
                "name": "Luna Mystic",
                "profile_picture": "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=400",
                "specialties": ["Love & Relationships", "Career & Finance", "Tarot"],
                "tools_used": ["Tarot Cards", "Crystal Ball", "Intuitive"],
                "bio": "Experienced psychic advisor specializing in love, career, and life guidance. Let me help illuminate your path.",
                "years_experience": "5-10 years",
                "chat_rate": 2.99,
                "phone_rate": 3.99,
                "video_rate": 4.99,
                "average_rating": 4.9,
                "total_readings": 250,
                "online_status": "online",
                "is_available": True,
                "is_verified": True,
                "status": "approved",
                "created_at": datetime.utcnow()
            })
        
        return {
            "success": True,
            "message": "Test accounts created/updated!",
            "client_login": {
                "email": "tester@sixthsense.com",
                "password": "Tester123"
            },
            "advisor_login": {
                "email": "testpsychic@sixthsense.com",
                "password": "Tester123"
            },
            "freelancer_advisor_login": {
                "email": "advisor@psychic.com",
                "password": "advisor123"
            },
            "instructions": "Use these credentials in the apps"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

# Public endpoint for active sale - accessible by all apps
@api_router.get("/sales/active")
async def get_active_sale_public():
    """Get currently active sale - public endpoint for client/psychic apps"""
    sale = await db.sales.find_one({"is_active": True})
    if sale:
        if "_id" in sale:
            del sale["_id"]
        return sale
    return None

# Include the main router
app.include_router(api_router)

# Mount static files for generated images
static_dir = ROOT_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Include feature routers
auth_router = create_auth_routes(db)
psychics_router = create_psychic_routes(db)
wallet_router = create_wallet_routes(db)
questions_router = create_questions_routes(db)
favorites_router = create_favorites_routes(db)
tips_router = create_tips_routes(db)
reviews_router = create_reviews_routes(db)
chat_router = create_chat_routes(db)
psychic_portal_router = create_psychic_portal_routes(db)
admin_router = create_admin_routes(db)
horoscope_router = create_horoscope_routes(db)
messages_router = create_messages_routes(db)
support_router = create_support_routes(db)
applications_router = create_application_routes(db)
notifications_router = create_notifications_routes(db)
websocket_router = create_websocket_routes(db)
payments_router = create_payment_routes(db)
email_router = create_email_routes(db)
storage_router = create_storage_routes(db)
video_router = create_video_routes(db)
push_router = create_push_notification_routes(db)
user_router = create_user_routes(db)
recorded_video_router = create_recorded_video_routes(db)
recorded_readings_router = create_recorded_readings_routes(db)
stripe_connect_router = create_stripe_connect_router(db)

app.include_router(auth_router, prefix="/api")
app.include_router(psychics_router, prefix="/api")
app.include_router(wallet_router, prefix="/api")
app.include_router(questions_router, prefix="/api")
app.include_router(favorites_router, prefix="/api")
app.include_router(tips_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(psychic_portal_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(horoscope_router, prefix="/api")
app.include_router(messages_router, prefix="/api")
app.include_router(support_router, prefix="/api")
app.include_router(applications_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")
app.include_router(payments_router, prefix="/api")
app.include_router(email_router, prefix="/api")
app.include_router(storage_router, prefix="/api")
app.include_router(video_router, prefix="/api")
app.include_router(push_router, prefix="/api")
app.include_router(user_router, prefix="/api")
app.include_router(recorded_video_router, prefix="/api")
app.include_router(recorded_readings_router, prefix="/api")
app.include_router(stripe_connect_router, prefix="/api")

# Configured explicit CORS allowed origins (BE-003, AUD-038)
ALLOWED_ORIGINS = [
    origin.strip() for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8081,http://localhost:19006,http://localhost:3000,http://127.0.0.1:8081,http://192.168.29.113:8081,http://192.168.29.249:8081,https://web-production-9acf9.up.railway.app"
    ).split(",") if origin.strip() and origin.strip() != "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global exception handler to sanitize 500 error responses (BE-011)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "detail": "An internal server error occurred.",
            "error_code": "INTERNAL_SERVER_ERROR"
        }
    )

# Promotional notification messages
PROMOTIONAL_MESSAGES = [
    {"title": "Need Clarity? ✨", "body": "Your favorite psychics are online now! Get a reading and find the answers you seek."},
    {"title": "The Stars Are Aligned 🌟", "body": "Today is the perfect day for a reading. Connect with an advisor and discover what the universe has in store."},
    {"title": "Trust Your Intuition 🔮", "body": "Something on your mind? Our gifted psychics are ready to guide you through any situation."},
    {"title": "Special Energy Today 💫", "body": "We sense big things for you! Get a personalized reading now and unlock your potential."},
    {"title": "Your Path Awaits 🌙", "body": "Questions about love, career, or life? Our top-rated advisors are waiting to help you."},
    {"title": "Time for Guidance 🌺", "body": "Life's big decisions deserve cosmic insight. Book a reading with a trusted psychic today!"},
    {"title": "Unlock Your Future 🗝️", "body": "Curious about what's next? Our psychics can reveal the path ahead. Connect now!"},
    {"title": "Astrology Check ✨", "body": "When was your last reading? Reconnect with your spiritual journey today."},
]

# App rating prompt messages
APP_RATING_MESSAGES = {
    "client_positive": {
        "title": "Enjoying Your Experience? ⭐",
        "body": "We noticed you had a great reading! If you're loving the app, please take a moment to rate us. Your feedback helps others discover us!"
    },
    "psychic_positive": {
        "title": "You're Doing Amazing! 🌟",
        "body": "Congratulations on that 5-star rating and tip! If you're enjoying being an advisor, please rate the app. Your review helps other psychics join!"
    }
}

async def check_and_send_app_rating_prompt(user_id: str, user_type: str, trigger_type: str):
    """
    Check if user should receive app rating prompt and send if appropriate.
    
    For clients: Triggered after positive rating (4-5 stars) AND session > 10 min
    For psychics: Triggered after receiving 5-star rating AND tip > $5
    """
    try:
        # Check if we already prompted this user recently (within 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_prompt = await db.notifications.find_one({
            "user_id": user_id,
            "type": "app_rating_prompt",
            "created_at": {"$gte": thirty_days_ago}
        })
        
        if recent_prompt:
            return None  # Don't spam with rating prompts
        
        # Get appropriate message
        message_key = f"{user_type}_positive"
        message = APP_RATING_MESSAGES.get(message_key)
        
        if not message:
            return None
        
        # Create and save notification
        notification = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "title": message["title"],
            "body": message["body"],
            "type": "app_rating_prompt",
            "trigger": trigger_type,
            "is_read": False,
            "created_at": datetime.utcnow()
        }
        
        await db.notifications.insert_one(notification)
        logger.info(f"📱 Sent app rating prompt to {user_type} {user_id}")
        return notification
        
    except Exception as e:
        logger.error(f"Error sending app rating prompt: {e}")
        return None

async def send_scheduled_promotional_notifications():
    """Scheduled task to send promotional notifications to all users (max 2/day per user)"""
    try:
        logger.info("🔔 Running scheduled promotional notifications...")
        
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
            
            notification = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "title": message["title"],
                "body": message["body"],
                "type": "promotional",
                "is_read": False,
                "created_at": datetime.utcnow()
            }
            
            await db.notifications.insert_one(notification)
            sent_count += 1
        
        logger.info(f"✅ Sent {sent_count} promotional notifications")
        return sent_count
    except Exception as e:
        logger.error(f"❌ Error sending promotional notifications: {e}")
        return 0

# Initialize scheduler
scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def startup_event():
    """Start the scheduler and load deleted psychics when app starts"""
    # Load deleted psychic IDs from database
    await load_deleted_psychic_ids(db)
    
    # Schedule notifications at 9 AM and 6 PM UTC daily
    scheduler.add_job(
        send_scheduled_promotional_notifications,
        CronTrigger(hour=9, minute=0),  # 9:00 AM UTC
        id="morning_notifications",
        replace_existing=True
    )
    scheduler.add_job(
        send_scheduled_promotional_notifications,
        CronTrigger(hour=18, minute=0),  # 6:00 PM UTC
        id="evening_notifications",
        replace_existing=True
    )
    scheduler.start()
    logger.info("📅 Promotional notification scheduler started (9 AM & 6 PM UTC daily)")

@app.on_event("shutdown")
async def shutdown_db_client():
    scheduler.shutdown()
    # Note: db client is managed by motor and auto-closes
    logger.info("🛑 Scheduler shutdown complete")
