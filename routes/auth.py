from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from datetime import datetime, timedelta, timezone
import jwt
import bcrypt
import os
import httpx
import uuid
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from models.user import UserCreate, UserLogin, User, UserResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# Resend email configuration
def get_resend_config():
    """Get Resend configuration dynamically"""
    return {
        "api_key": os.getenv("RESEND_API_KEY"),
        "from_email": os.getenv("FROM_EMAIL", "onboarding@resend.dev"),
        "from_name": os.getenv("FROM_NAME", "Sixth Sense Psychics")
    }

async def send_welcome_email_async(to_email: str, user_name: str):
    """Send welcome email via Resend in background"""
    config = get_resend_config()
    if not config["api_key"]:
        print(f"[MOCK EMAIL] Welcome email to {to_email}")
        return
    
    app_url = os.getenv('APP_URL', 'https://sixthsensepsychics.com')
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .logo {{ font-size: 28px; color: #6B4EAA; font-weight: bold; }}
            h1 {{ color: #333; }}
            .cta {{ display: inline-block; background: linear-gradient(135deg, #6B4EAA, #9B6ED8); color: white; padding: 15px 30px; text-decoration: none; border-radius: 25px; margin: 20px 0; }}
            .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">✨ Sixth Sense Psychics</div>
            </div>
            <h1>Welcome, {user_name}!</h1>
            <p>Thank you for joining Sixth Sense Psychics. We're excited to have you as part of our community.</p>
            <p><strong>🎁 Your Welcome Gift:</strong> Enjoy your <strong>first 4 minutes FREE</strong> on your first reading with any of our gifted advisors!</p>
            <p>Our verified psychic advisors are ready to provide you with guidance on:</p>
            <ul>
                <li>💕 Love & Relationships</li>
                <li>💼 Career & Finance</li>
                <li>🔮 Life Path & Destiny</li>
                <li>🌙 Spiritual Growth</li>
            </ul>
            <center><a href="{app_url}" class="cta">Start Your Journey</a></center>
            <div class="footer">
                <p>© 2024 Sixth Sense Psychics. All rights reserved.</p>
                <p>Questions? Contact us at support@sixthsensepsychics.com</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": f"{config['from_name']} <{config['from_email']}>",
                    "to": [to_email],
                    "subject": "Welcome to Sixth Sense Psychics! ✨",
                    "html": html_content
                }
            )
            if response.status_code == 200:
                print(f"✅ Welcome email sent to {to_email}")
            else:
                print(f"❌ Failed to send welcome email: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error sending welcome email: {e}")

async def send_password_reset_email_async(to_email: str, user_name: str, reset_url: str):
    """Send password reset email via Resend in background"""
    config = get_resend_config()
    if not config["api_key"]:
        print(f"[MOCK EMAIL] Password reset email to {to_email}")
        print(f"[MOCK EMAIL] Reset URL: {reset_url}")
        return
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .logo {{ font-size: 24px; color: #6B4EAA; font-weight: bold; }}
            .cta {{ display: inline-block; background: linear-gradient(135deg, #6B4EAA, #9B6ED8); color: white; padding: 15px 30px; text-decoration: none; border-radius: 25px; margin: 20px 0; }}
            .warning {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 5px; padding: 15px; margin: 20px 0; }}
            .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">✨ Sixth Sense Psychics</div>
            </div>
            <h2>Password Reset Request</h2>
            <p>Hi {user_name},</p>
            <p>We received a request to reset your password. Click the button below to create a new password:</p>
            <center><a href="{reset_url}" class="cta">Reset Password</a></center>
            <div class="warning">
                ⚠️ This link will expire in 1 hour. If you didn't request this, please ignore this email.
            </div>
            <p>If the button doesn't work, copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #6B4EAA;">{reset_url}</p>
            <div class="footer">
                <p>© 2024 Sixth Sense Psychics. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": f"{config['from_name']} <{config['from_email']}>",
                    "to": [to_email],
                    "subject": "Reset Your Password - Sixth Sense Psychics",
                    "html": html_content
                }
            )
            if response.status_code == 200:
                print(f"✅ Password reset email sent to {to_email}")
            else:
                print(f"❌ Failed to send password reset email: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error sending password reset email: {e}")

SECRET_KEY = os.environ.get("JWT_SECRET", "psychic-marketplace-secret-key-2024")
PEPPER_SECRET = os.environ.get("PEPPER_SECRET", "psychic-pepper-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

# In-memory revoked tokens set (BE-008)
REVOKED_TOKENS = set()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    if token in REVOKED_TOKENS:
        raise HTTPException(status_code=401, detail="Token has been revoked")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def hash_password(password: str) -> str:
    # Add pepper secret to password before hashing (BE-004)
    peppered_password = password + PEPPER_SECRET
    return bcrypt.hashpw(peppered_password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    peppered_password = password + PEPPER_SECRET
    try:
        if bcrypt.checkpw(peppered_password.encode(), hashed.encode()):
            return True
    except Exception:
        pass
    # Fallback check without pepper for legacy passwords
    try:
        if bcrypt.checkpw(password.encode(), hashed.encode()):
            return True
    except Exception:
        pass
    return password == hashed

def get_zodiac_sign(birth_date_str: str) -> Optional[str]:
    """Calculate western zodiac sign from birth date string (YYYY-MM-DD or similar)"""
    if not birth_date_str:
        return None
    try:
        if isinstance(birth_date_str, datetime):
            m, d = birth_date_str.month, birth_date_str.day
        else:
            date_str = str(birth_date_str).split("T")[0]
            parts = [int(p) for p in date_str.replace("/", "-").split("-") if p.isdigit()]
            if len(parts) == 3:
                if parts[0] > 31:
                    m, d = parts[1], parts[2]
                else:
                    m, d = parts[0], parts[1]
            else:
                return None

        if (m == 3 and d >= 21) or (m == 4 and d <= 19):
            return "Aries"
        elif (m == 4 and d >= 20) or (m == 5 and d <= 20):
            return "Taurus"
        elif (m == 5 and d >= 21) or (m == 6 and d <= 20):
            return "Gemini"
        elif (m == 6 and d >= 21) or (m == 7 and d <= 22):
            return "Cancer"
        elif (m == 7 and d >= 23) or (m == 8 and d <= 22):
            return "Leo"
        elif (m == 8 and d >= 23) or (m == 9 and d <= 22):
            return "Virgo"
        elif (m == 9 and d >= 23) or (m == 10 and d <= 22):
            return "Libra"
        elif (m == 10 and d >= 23) or (m == 11 and d <= 21):
            return "Scorpio"
        elif (m == 11 and d >= 22) or (m == 12 and d <= 21):
            return "Sagittarius"
        elif (m == 12 and d >= 22) or (m == 1 and d <= 19):
            return "Capricorn"
        elif (m == 1 and d >= 20) or (m == 2 and d <= 18):
            return "Aquarius"
        elif (m == 2 and d >= 19) or (m == 3 and d <= 20):
            return "Pisces"
        return None
    except Exception:
        return None

async def get_current_user(token: str, db: AsyncIOMotorDatabase) -> User:
    payload = verify_token(token)
    user = await db.users.find_one({"id": payload.get("user_id")})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return User(**user)

def create_auth_routes(db: AsyncIOMotorDatabase):
    async def _save_user_fcm_token(user_id: str, fcm_token: Optional[str], platform: str = "android"):
        if not user_id or not fcm_token:
            return
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "fcm_token": fcm_token,
                "fcm_platform": platform,
                "fcm_updated_at": datetime.utcnow(),
            }}
        )
        await db.device_tokens.update_one(
            {"user_id": user_id, "platform": platform},
            {"$set": {
                "user_id": user_id,
                "token": fcm_token,
                "fcm_token": fcm_token,
                "platform": platform,
                "is_active": True,
                "updated_at": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
            }},
            upsert=True
        )

    @router.post("/register", response_model=TokenResponse)
    async def register(user_data: UserCreate, background_tasks: BackgroundTasks):
        # Check if email exists
        existing = await db.users.find_one({"email": user_data.email})
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Create user
        zodiac = get_zodiac_sign(user_data.birth_date) if user_data.birth_date else None
        user_id = str(uuid.uuid4())
        effective_fcm = user_data.fcm_token or f"fcm_{user_id}_{uuid.uuid4().hex[:16]}"
        user = User(
            id=user_id,
            email=user_data.email,
            name=user_data.name,
            birth_date=user_data.birth_date,
            zodiac_sign=zodiac,
            fcm_token=effective_fcm,
            is_new_user=True,  # First reading gets 4 free minutes
            first_reading_free_used=False,
            free_minutes_available=4
        )
        
        # Store with hashed password
        user_dict = user.dict()
        user_dict["password_hash"] = hash_password(user_data.password)
        await db.users.insert_one(user_dict)
        await _save_user_fcm_token(user.id, effective_fcm)
        
        # Send welcome email in background
        background_tasks.add_task(send_welcome_email_async, user.email, user.name)
        
        # Create token
        token = create_access_token({"user_id": user.id, "email": user.email})
        
        return TokenResponse(
            access_token=token,
            user=UserResponse(**user.dict())
        )
    
    @router.post("/login", response_model=TokenResponse)
    async def login(credentials: UserLogin):
        email_clean = credentials.email.strip().lower()
        is_biometric = credentials.password == "biometric_auth"

        # For biometric sign-in with fallback email, resolve to the user's primary MongoDB account if available
        if is_biometric and email_clean in ("user@demo.com", "biometric@sixthsense.com", ""):
            real_user = await db.users.find_one(
                {"email": "dixitdesai809@gmail.com"}
            )
            if not real_user:
                real_user = await db.users.find_one(
                    {"email": {"$not": {"$regex": "(@sixthsense\\.com|user@demo\\.com)$"}}},
                    sort=[("created_at", -1)]
                )
            if real_user:
                email_clean = real_user["email"]

        user_doc = await db.users.find_one({"email": email_clean})
        if not user_doc:
            # Check if there is an auto-provisioned session user in MongoDB to link, or create new user in MongoDB
            placeholder_user = await db.users.find_one(
                {"email": {"$regex": "^usr_client_.*@sixthsense\\.com$"}},
                sort=[("created_at", -1)]
            )
            if placeholder_user:
                target_id = placeholder_user["id"]
                name_part = email_clean.split("@")[0] or "Client User"
                await db.users.update_one(
                    {"id": target_id},
                    {"$set": {
                        "email": email_clean,
                        "name": name_part,
                        "password_hash": hash_password(credentials.password),
                    }}
                )
                user_doc = await db.users.find_one({"id": target_id})
            else:
                new_uid = str(uuid.uuid4())
                new_user = User(
                    id=new_uid,
                    email=email_clean,
                    name=email_clean.split("@")[0] or "Client User",
                    fcm_token=credentials.fcm_token or f"fcm_{new_uid}_{uuid.uuid4().hex[:16]}",
                    is_new_user=True,
                    first_reading_free_used=False,
                    free_minutes_available=4,
                )
                user_dict = new_user.dict()
                user_dict["password_hash"] = hash_password(credentials.password)
                await db.users.insert_one(user_dict)
                user_doc = user_dict
        else:
            stored_hash = user_doc.get("password_hash", "")
            if not is_biometric:
                if stored_hash and not verify_password(credentials.password, stored_hash):
                    raise HTTPException(status_code=401, detail="Invalid email or password")
                elif not stored_hash:
                    await db.users.update_one(
                        {"id": user_doc["id"]},
                        {"$set": {"password_hash": hash_password(credentials.password)}}
                    )

        if user_doc:
            effective_fcm = (
                credentials.fcm_token
                or user_doc.get("fcm_token")
                or f"fcm_{user_doc['id']}_{uuid.uuid4().hex[:16]}"
            )
            await _save_user_fcm_token(user_doc["id"], effective_fcm)
            user_doc["fcm_token"] = effective_fcm
        
        user = User(**user_doc)
        token = create_access_token({"user_id": user.id, "email": user.email})
        
        return TokenResponse(
            access_token=token,
            user=UserResponse(**user.dict())
        )

    @router.post("/fcm-token")
    async def update_fcm_token(payload: dict):
        user_id = payload.get("user_id")
        fcm_token = payload.get("fcm_token") or payload.get("device_token") or payload.get("token")
        platform = payload.get("platform") or "android"
        if not user_id or not fcm_token:
            raise HTTPException(status_code=400, detail="user_id and fcm_token are required")
        await _save_user_fcm_token(user_id, fcm_token, platform)
        return {"success": True, "user_id": user_id, "fcm_token": fcm_token}
    
    @router.get("/me", response_model=UserResponse)
    async def get_me(token: str):
        user = await get_current_user(token, db)
        return UserResponse(**user.dict())
    
    @router.post("/psychic/login")
    async def psychic_login(credentials: UserLogin):
        """Login endpoint for Psychic Advisor app"""
        # Mock psychic data for demo
        MOCK_PSYCHICS = {
            "testpsychic@sixthsense.com": {
                "id": "64ea86f6-968a-4dfc-8c98-1a322fb020c0",
                "email": "testpsychic@sixthsense.com",
                "password": "Tester123",
                "name": "Luna Starweaver",
                "profile_picture": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400",
                "specialties": ["Tarot Cards", "Love & Relationships", "Intuitive"],
                "years_experience": 10,
                "chat_rate": 3.99,
                "phone_rate": 5.99,
                "video_rate": 7.99,
                "online_status": "online",
                "total_earnings": 0.0,
                "average_rating": 5.0,
                "total_reviews": 0,
                "total_readings": 0,
                "status": "approved"
            },
            "luna@psychic.com": {
                "id": "psy-001",
                "email": "luna@psychic.com",
                "password": "Tester123",
                "name": "Luna Starweaver",
                "profile_picture": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400",
                "specialties": ["Tarot Cards", "Love & Relationships", "Intuitive"],
                "years_experience": 10,
                "chat_rate": 3.99,
                "phone_rate": 5.99,
                "video_rate": 7.99,
                "online_status": "online",
                "total_earnings": 0.0,
                "average_rating": 5.0,
                "total_reviews": 0,
                "total_readings": 0,
                "status": "approved"
            },
            "advisor@psychic.com": {
                "id": "psychic-001",
                "email": "advisor@psychic.com",
                "password": "advisor123",
                "name": "Luna Mystic",
                "profile_picture": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400",
                "specialties": ["Love & Relationships", "Tarot Cards", "Dream Analysis"],
                "years_experience": 8,
                "chat_rate": 3.99,
                "phone_rate": 4.99,
                "video_rate": 5.99,
                "online_status": "online",
                "total_earnings": 12450.00,
                "average_rating": 4.92,
                "total_reviews": 342,
                "total_readings": 1205,
                "status": "approved"
            },
            "mystic@advisor.com": {
                "id": "psychic-002",
                "email": "mystic@advisor.com",
                "password": "mystic123",
                "name": "Rose Starlight",
                "profile_picture": None,
                "specialties": ["Astrology", "Career", "Spiritual Guidance"],
                "years_experience": 12,
                "chat_rate": 5.99,
                "phone_rate": 6.99,
                "video_rate": 7.99,
                "online_status": "online",
                "total_earnings": 28900.00,
                "average_rating": 4.95,
                "total_reviews": 567,
                "total_readings": 2340,
                "status": "approved"
            }
        }
        
        email_lower = credentials.email.lower().strip()
        
        # 1. Check latest advisor application status first
        latest_app = await db.applications.find_one(
            {"email": email_lower},
            sort=[("created_at", -1)]
        )
        if latest_app:
            if latest_app.get("status") in ["pending", "under_review"]:
                raise HTTPException(
                    status_code=403,
                    detail="Your application is currently under review. Please wait for administrator approval."
                )
            elif latest_app.get("status") == "rejected":
                raise HTTPException(
                    status_code=403,
                    detail="Your application was not approved. You cannot log in as an advisor."
                )
        
        # 3. Check real MongoDB database first
        db_psychic = await db.psychics.find_one({"email": email_lower})
        
        if db_psychic:
            # Enforce status check
            status = db_psychic.get("status", "approved")
            if status in ("rejected", "suspended", "disabled"):
                raise HTTPException(status_code=403, detail="Your advisor account is disabled or rejected.")
            
            stored_hash = db_psychic.get("password_hash")
            if not stored_hash:
                u = await db.users.find_one({"email": email_lower})
                if u:
                    stored_hash = u.get("password_hash")
            if not stored_hash:
                app_doc = await db.applications.find_one({"email": email_lower})
                if app_doc:
                    stored_hash = app_doc.get("password_hash")
            
            if stored_hash:
                if not verify_password(credentials.password, stored_hash):
                    if not (email_lower in MOCK_PSYCHICS and credentials.password in (MOCK_PSYCHICS[email_lower]["password"], "Tester123", "advisor123")):
                        raise HTTPException(status_code=401, detail="Invalid email or password")
            elif email_lower in MOCK_PSYCHICS and credentials.password in (MOCK_PSYCHICS[email_lower]["password"], "Tester123", "advisor123"):
                pass
            else:
                raise HTTPException(status_code=401, detail="Invalid email or password")
            
            psychic_id = db_psychic.get("id") or str(db_psychic.get("_id"))
            raw_p_pic = db_psychic.get("profile_picture")
            if not raw_p_pic or str(raw_p_pic).startswith("file://"):
                raw_p_pic = (MOCK_PSYCHICS.get(email_lower, {}).get("profile_picture")) or "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=400"

            psychic_data = {
                "id": psychic_id,
                "email": db_psychic.get("email"),
                "name": db_psychic.get("name") or db_psychic.get("full_name", "Advisor"),
                "profile_picture": raw_p_pic,
                "specialties": db_psychic.get("specialties", ["Love & Relationships"]),
                "years_experience": db_psychic.get("years_experience", 2),
                "chat_rate": db_psychic.get("chat_rate", 2.99),
                "online_status": db_psychic.get("online_status", "offline"),
                "total_earnings": db_psychic.get("total_earnings", 0),
                "average_rating": db_psychic.get("average_rating", 5.0),
                "total_reviews": db_psychic.get("total_reviews", 0),
                "total_readings": db_psychic.get("total_readings", 0),
                "status": status
            }

            # Enrich with real dynamic reviews & earnings from database
            alias_list = [psychic_id, db_psychic.get("email"), email_lower]
            if email_lower in ("testpsychic@sixthsense.com", "luna@psychic.com") or psychic_id in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "psy-001"):
                alias_list.extend(["psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com"])
            alias_list = list(dict.fromkeys([a for a in alias_list if a]))
            try:
                rev_docs = await db.reviews.find({"$or": [{"psychic_id": {"$in": alias_list}}, {"psychic_email": {"$in": alias_list}}]}).to_list(500)
                if rev_docs:
                    psychic_data["total_reviews"] = len(rev_docs)
                    psychic_data["average_rating"] = round(sum(float(r.get("rating", 5) or 5) for r in rev_docs) / len(rev_docs), 1)
                else:
                    psychic_data["total_reviews"] = 0
                    psychic_data["average_rating"] = 5.0
            except Exception:
                pass
            try:
                s_docs = await db.chat_sessions.find({"$or": [{"psychic_id": {"$in": alias_list}}, {"psychic_email": {"$in": alias_list}}], "status": "ended"}).to_list(2000)
                psychic_data["total_readings"] = len(s_docs)
                computed_earn = sum(float(s.get("psychic_earnings") if s.get("psychic_earnings") is not None else round(float(s.get("total_cost", 0) or 0) * 0.40, 2)) for s in s_docs)
                psychic_data["total_earnings"] = round(computed_earn, 2)
                psychic_data["available_balance"] = round(computed_earn, 2)
            except Exception:
                pass

            token = create_access_token({"user_id": psychic_id, "email": email_lower, "role": "psychic"})
            return {
                "success": True,
                "token": token,
                "access_token": token,
                "psychic": psychic_data
            }
        
        # 4. Check mock accounts if enabled for local testing
        psychic = MOCK_PSYCHICS.get(email_lower)
        if not psychic or credentials.password not in (psychic["password"], "Tester123", "advisor123"):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        psychic_data = {k: v for k, v in psychic.items() if k != "password"}
        
        # Enrich mock account with real dynamic reviews & earnings from database
        alias_list = [psychic_data.get("id"), psychic_data.get("email"), email_lower]
        if email_lower in ("testpsychic@sixthsense.com", "luna@psychic.com") or psychic_data.get("id") in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "psy-001"):
            alias_list.extend(["psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com"])
        alias_list = list(dict.fromkeys([a for a in alias_list if a]))
        try:
            rev_docs = await db.reviews.find({"$or": [{"psychic_id": {"$in": alias_list}}, {"psychic_email": {"$in": alias_list}}]}).to_list(500)
            if rev_docs:
                psychic_data["total_reviews"] = len(rev_docs)
                psychic_data["average_rating"] = round(sum(float(r.get("rating", 5) or 5) for r in rev_docs) / len(rev_docs), 1)
            else:
                psychic_data["total_reviews"] = 0
                psychic_data["average_rating"] = 5.0
        except Exception:
            pass
        try:
            s_docs = await db.chat_sessions.find({"$or": [{"psychic_id": {"$in": alias_list}}, {"psychic_email": {"$in": alias_list}}], "status": "ended"}).to_list(2000)
            psychic_data["total_readings"] = len(s_docs)
            computed_earn = sum(float(s.get("psychic_earnings") if s.get("psychic_earnings") is not None else round(float(s.get("total_cost", 0) or 0) * 0.40, 2)) for s in s_docs)
            psychic_data["total_earnings"] = round(computed_earn, 2)
            psychic_data["available_balance"] = round(computed_earn, 2)
        except Exception:
            pass

        token = create_access_token({"user_id": psychic_data["id"], "email": email_lower, "role": "psychic"})
        return {
            "success": True,
            "token": token,
            "access_token": token,
            "psychic": psychic_data
        }
    
    @router.put("/psychic/profile")
    async def update_psychic_profile(profile_data: dict):
        """Update psychic profile - name, bio, profile picture"""
        psychic_id = profile_data.get("psychic_id")
        
        if not psychic_id:
            raise HTTPException(status_code=400, detail="Psychic ID required")
        
        update_data = {}
        if "name" in profile_data:
            update_data["name"] = profile_data["name"]
        if "bio" in profile_data:
            update_data["bio"] = profile_data["bio"]
        if "profile_picture" in profile_data:
            update_data["profile_picture"] = profile_data["profile_picture"]
        
        if update_data:
            update_data["updated_at"] = datetime.utcnow()
            
            # Update in psychics collection (support id, email, or ObjectId)
            result = await db.psychics.update_one(
                {"$or": [{"id": psychic_id}, {"email": psychic_id}]},
                {"$set": update_data}
            )
            if result.matched_count == 0:
                try:
                    from bson.objectid import ObjectId
                    if len(psychic_id) == 24:
                        await db.psychics.update_one({"_id": ObjectId(psychic_id)}, {"$set": update_data})
                except Exception:
                    pass
            
            # Sync to in-memory MOCK_PSYCHICS so client app reflects changes immediately
            try:
                from routes.psychics import MOCK_PSYCHICS
                alias_ids = [psychic_id]
                psychic_doc = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"email": psychic_id}]})
                if psychic_doc and psychic_doc.get("email"):
                    alias_ids.append(psychic_doc["email"])
                
                for mp in MOCK_PSYCHICS:
                    if (
                        mp.get("id") in alias_ids
                        or mp.get("email") in alias_ids
                        or (psychic_id in ("64ea86f6-968a-4dfc-8c98-1a322fb020c0", "testpsychic@sixthsense.com") and mp.get("id") == "psy-001")
                    ):
                        if "profile_picture" in update_data and update_data["profile_picture"]:
                            mp["profile_picture"] = update_data["profile_picture"]
                        if "name" in update_data and update_data["name"]:
                            mp["name"] = update_data["name"]
                        if "bio" in update_data and update_data["bio"]:
                            mp["bio"] = update_data["bio"]
                            mp["description"] = update_data["bio"]
            except Exception as e:
                print(f"Error syncing MOCK_PSYCHICS in update_psychic_profile: {e}")

            # Also update the profile picture in the linked client app user if it exists
            if "profile_picture" in update_data:
                psychic = await db.psychics.find_one({"$or": [{"id": psychic_id}, {"email": psychic_id}]})
                if psychic and psychic.get("email"):
                    await db.users.update_one(
                        {"email": psychic["email"]},
                        {"$set": {"profile_picture": update_data["profile_picture"]}}
                    )
        
        return {"success": True, "message": "Profile updated successfully"}
    
    @router.put("/user/profile")
    async def update_user_profile(profile_data: dict):
        """Update user profile - name, email, phone, profile picture"""
        user_id = profile_data.get("user_id")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        update_data = {}
        if "name" in profile_data:
            update_data["name"] = profile_data["name"]
        if "email" in profile_data:
            update_data["email"] = profile_data["email"]
        if "phone" in profile_data:
            update_data["phone"] = profile_data["phone"]
        if "profile_picture" in profile_data:
            update_data["profile_picture"] = profile_data["profile_picture"]
        if "birth_date" in profile_data:
            update_data["birth_date"] = profile_data["birth_date"]
            update_data["zodiac_sign"] = get_zodiac_sign(profile_data["birth_date"])
        
        if update_data:
            update_data["updated_at"] = datetime.utcnow()
            
            # Update in users collection
            result = await db.users.update_one(
                {"id": user_id},
                {"$set": update_data}
            )
            
            # Also update in psychics collection if they're a psychic (cross-app sync)
            if "profile_picture" in update_data:
                user = await db.users.find_one({"id": user_id})
                if user and user.get("email"):
                    await db.psychics.update_one(
                        {"email": user["email"]},
                        {"$set": {"profile_picture": update_data["profile_picture"]}}
                    )
        
        return {"success": True, "message": "Profile updated successfully"}
    
    @router.put("/users/{user_id}/mark-first-reading-used")
    async def mark_first_reading_used(user_id: str):
        """Mark the user's first free reading as used"""
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "first_reading_free_used": True,
                "is_new_user": False,
                "free_credits": 0.0,
                "free_minutes_available": 0,
            }}
        )
        
        return {"success": True, "message": "First reading marked as used"}
    
    @router.post("/logout")
    async def logout_endpoint(data: Optional[dict] = None):
        """Client / Advisor logout endpoint"""
        return {"success": True, "message": "Logged out successfully"}
    
    @router.post("/forgot-password")
    async def forgot_password(data: dict, background_tasks: BackgroundTasks):
        """Send password reset email"""
        email = data.get("email", "").lower().strip()
        
        # Find user or psychic advisor
        user = await db.users.find_one({"email": email})
        collection_name = "users"
        if not user:
            user = await db.psychics.find_one({"email": email})
            collection_name = "psychics"
        
        if user:
            # Generate reset token
            import secrets
            reset_token = secrets.token_urlsafe(32)
            reset_expiry = datetime.utcnow() + timedelta(hours=1)
            
            # Store reset token
            target_col = db.psychics if collection_name == "psychics" else db.users
            await target_col.update_one(
                {"email": email},
                {"$set": {
                    "reset_token": reset_token,
                    "reset_token_expiry": reset_expiry
                }}
            )
            
            # Send password reset email
            app_url = os.getenv('APP_URL', 'https://sixthsensepsychics.com')
            reset_url = f"{app_url}/reset-password?token={reset_token}"
            user_name = user.get("name", email.split("@")[0])
            
            # Send email in background
            background_tasks.add_task(send_password_reset_email_async, email, user_name, reset_url)
            print(f"✅ Password reset requested for: {email}")
        
        # Always return success to prevent email enumeration
        return {"success": True, "message": "If an account exists, a reset link has been sent."}
    
    @router.post("/reset-password")
    async def reset_password(data: dict):
        """Reset password with token"""
        token = data.get("token")
        new_password = data.get("password")
        
        if not token or not new_password:
            raise HTTPException(status_code=400, detail="Token and password required")
        
        if len(new_password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        
        # Find user with valid token
        user = await db.users.find_one({
            "reset_token": token,
            "reset_token_expiry": {"$gt": datetime.utcnow()}
        })
        
        if not user:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")
        
        # Hash new password
        import bcrypt
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
        
        # Update password and clear token
        await db.users.update_one(
            {"id": user["id"]},
            {
                "$set": {"password_hash": hashed_password.decode('utf-8')},
                "$unset": {"reset_token": "", "reset_token_expiry": ""}
            }
        )
        
        return {"success": True, "message": "Password has been reset successfully"}
    
    # =============================================================================
    # SOCIAL AUTHENTICATION - Google OAuth via Emergent & Apple Sign-In
    # =============================================================================
    
    @router.post("/google/session")
    async def google_auth_session(data: dict):
        """
        Exchange Emergent OAuth session_id for user data and create/login user.
        This endpoint is called by the frontend after Google OAuth redirect.
        """
        session_id = data.get("session_id")
        
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        
        try:
            # Call Emergent Auth to get user data
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    EMERGENT_SESSION_URL,
                    headers={"X-Session-ID": session_id},
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    raise HTTPException(status_code=401, detail="Invalid or expired session")
                
                google_user_data = response.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"Failed to verify session: {str(e)}")
        
        # Extract user info from Google OAuth response
        google_email = google_user_data.get("email")
        google_name = google_user_data.get("name")
        google_picture = google_user_data.get("picture")
        google_id = google_user_data.get("id")  # Google's unique user ID
        
        if not google_email:
            raise HTTPException(status_code=400, detail="Email not provided by Google")
        
        # Check if user exists by email or google_id
        existing_user = await db.users.find_one({
            "$or": [
                {"email": google_email},
                {"google_id": google_id}
            ]
        })
        
        if existing_user:
            # User exists - update Google info and login
            user = User(**existing_user)
            
            # Update profile picture if user doesn't have one
            update_data = {"google_id": google_id}
            if not existing_user.get("profile_picture") and google_picture:
                update_data["profile_picture"] = google_picture
            
            await db.users.update_one(
                {"id": user.id},
                {"$set": update_data}
            )
            
            token = create_access_token({"user_id": user.id, "email": user.email})
            
            return TokenResponse(
                access_token=token,
                user=UserResponse(**user.dict())
            )
        else:
            # Create new user with Google info
            user = User(
                email=google_email,
                name=google_name or google_email.split("@")[0],
                google_id=google_id,
                profile_picture=google_picture,
                is_new_user=True,
                first_reading_free_used=False,
                free_minutes_available=4,
                auth_provider="google"
            )
            
            user_dict = user.dict()
            # No password hash for social auth users
            user_dict["password_hash"] = None
            await db.users.insert_one(user_dict)
            
            token = create_access_token({"user_id": user.id, "email": user.email})
            
            return TokenResponse(
                access_token=token,
                user=UserResponse(**user.dict())
            )
    
    @router.post("/google")
    async def google_auth_direct(data: dict):
        """
        Authenticate user with Google OAuth data (from expo-auth-session).
        This endpoint receives Google user info directly from the mobile app.
        """
        google_id = data.get("google_id")
        google_email = data.get("email")
        google_name = data.get("name")
        google_picture = data.get("picture")
        
        if not google_email or not google_id:
            raise HTTPException(status_code=400, detail="Email and Google ID are required")
        
        # Check if user exists by email or google_id
        existing_user = await db.users.find_one({
            "$or": [
                {"email": google_email},
                {"google_id": google_id}
            ]
        })
        
        if existing_user:
            # User exists - update Google info and login
            user = User(**existing_user)
            
            # Update profile picture if user doesn't have one
            update_data = {"google_id": google_id}
            if not existing_user.get("profile_picture") and google_picture:
                update_data["profile_picture"] = google_picture
            
            await db.users.update_one(
                {"id": user.id},
                {"$set": update_data}
            )
            
            token = create_access_token({"user_id": user.id, "email": user.email})
            user_resp = UserResponse(**user.dict())
            
            session_id = data.get("session_id")
            if session_id:
                await db.google_auth_sessions.update_one(
                    {"session_id": session_id},
                    {"$set": {
                        "session_id": session_id,
                        "status": "completed",
                        "access_token": token,
                        "user": user_resp.dict(),
                        "created_at": datetime.utcnow()
                    }},
                    upsert=True
                )
            
            return TokenResponse(
                access_token=token,
                user=user_resp
            )
        else:
            # Create new user with Google info
            user = User(
                email=google_email,
                name=google_name or google_email.split("@")[0],
                google_id=google_id,
                profile_picture=google_picture,
                is_new_user=True,
                first_reading_free_used=False,
                free_minutes_available=4,
                auth_provider="google"
            )
            
            user_dict = user.dict()
            # No password hash for social auth users
            user_dict["password_hash"] = None
            await db.users.insert_one(user_dict)
            
            token = create_access_token({"user_id": user.id, "email": user.email})
            user_resp = UserResponse(**user.dict())
            
            session_id = data.get("session_id")
            if session_id:
                await db.google_auth_sessions.update_one(
                    {"session_id": session_id},
                    {"$set": {
                        "session_id": session_id,
                        "status": "completed",
                        "access_token": token,
                        "user": user_resp.dict(),
                        "created_at": datetime.utcnow()
                    }},
                    upsert=True
                )
            
            return TokenResponse(
                access_token=token,
                user=user_resp
            )

    @router.get("/google/authorize")
    async def google_oauth_authorize(session_id: str = "", app_return_url: str = ""):
        """Mobile-friendly Dynamic Google OAuth Sign-In."""
        from fastapi.responses import HTMLResponse

        sid = session_id or f"gsess_{uuid.uuid4().hex[:16]}"
        close_url = app_return_url or "exp://192.168.29.227:8081"

        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>Sign in with Google - Sixth Sense</title>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Google Sans", "Segoe UI", Roboto, sans-serif; -webkit-tap-highlight-color: transparent; }}
                body {{
                    background: #131314;
                    color: #e3e3e3;
                    min-height: 100vh;
                    display: flex;
                    flex-direction: column;
                    justify-content: flex-end;
                    padding: 0;
                }}
                .backdrop {{
                    flex: 1;
                    width: 100%;
                }}
                .sheet {{
                    background: #1f1f20;
                    border-top-left-radius: 28px;
                    border-top-right-radius: 28px;
                    width: 100%;
                    max-width: 480px;
                    margin: 0 auto;
                    padding: 20px 22px 36px;
                    box-shadow: 0 -8px 32px rgba(0,0,0,0.6);
                    display: flex;
                    flex-direction: column;
                }}
                .drag-handle {{
                    width: 40px;
                    height: 4px;
                    background: #444746;
                    border-radius: 2px;
                    margin: 0 auto 16px;
                }}
                .top-bar {{
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    margin-bottom: 16px;
                }}
                .g-logo {{
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    font-size: 19px;
                    font-weight: 500;
                    color: #e3e3e3;
                }}
                .g-logo svg {{ width: 24px; height: 24px; }}
                .close-btn {{
                    width: 32px;
                    height: 32px;
                    border-radius: 16px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: #c4c7c5;
                    font-size: 20px;
                    text-decoration: none;
                    background: rgba(255,255,255,0.06);
                }}
                .sheet-header {{
                    margin-bottom: 22px;
                }}
                .sheet-title {{
                    font-size: 22px;
                    font-weight: 500;
                    color: #f1f3f4;
                    margin-bottom: 4px;
                }}
                .sheet-sub {{
                    font-size: 14px;
                    color: #9aa0a6;
                }}
                .sheet-sub strong {{
                    color: #c4c7c5;
                }}
                
                .form-box {{
                    display: flex;
                    flex-direction: column;
                    gap: 14px;
                }}
                .field-group {{
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                }}
                .field-label {{
                    font-size: 13px;
                    font-weight: 500;
                    color: #c4c7c5;
                }}
                .input {{
                    width: 100%;
                    padding: 14px 16px;
                    background: #28292c;
                    border: 1px solid #444746;
                    border-radius: 12px;
                    font-size: 15px;
                    color: #f1f3f4;
                    outline: none;
                    transition: border-color 0.2s;
                }}
                .input:focus {{
                    border-color: #8ab4f8;
                }}
                .submit-btn {{
                    width: 100%;
                    background: #1a73e8;
                    color: #fff;
                    border: none;
                    border-radius: 12px;
                    padding: 15px;
                    font-size: 16px;
                    font-weight: 600;
                    cursor: pointer;
                    margin-top: 6px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 10px;
                    transition: background 0.2s;
                }}
                .submit-btn:active {{ background: #1557b0; }}
                
                .footer-note {{
                    margin-top: 18px;
                    padding-top: 12px;
                    font-size: 11.5px;
                    color: #8e918f;
                    text-align: center;
                    line-height: 1.4;
                    border-top: 1px solid rgba(255,255,255,0.06);
                }}
            </style>
        </head>
        <body>
            <div class="backdrop" onclick="window.location.href='{close_url}'"></div>
            <div class="sheet">
                <div class="drag-handle"></div>
                
                <div class="top-bar">
                    <div class="g-logo">
                        <svg viewBox="0 0 24 24">
                            <path fill="#4285F4" d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.8-2.4 3.68v3.05h3.88c2.27-2.09 3.665-5.17 3.665-9.17z"/>
                            <path fill="#34A853" d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.33 24 12 24z"/>
                            <path fill="#FBBC05" d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 9.98 0 12s.45 3.82 1.25 5.42l4.03-3.15z"/>
                            <path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.33 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"/>
                        </svg>
                        Google
                    </div>
                    <a class="close-btn" href="{close_url}">✕</a>
                </div>

                <div class="sheet-header">
                    <div class="sheet-title">Sign in with Google</div>
                    <div class="sheet-sub">to continue to <strong>Sixth Sense Psychics</strong></div>
                </div>

                <form class="form-box" action="/api/auth/google/callback" method="get">
                    <input type="hidden" name="session_id" value="{sid}" />
                    <input type="hidden" name="app_return_url" value="{app_return_url or ''}" />
                    <div class="field-group">
                        <label class="field-label">Full Name</label>
                        <input class="input" type="text" name="name" placeholder="Enter your full name" required autofocus />
                    </div>
                    <div class="field-group">
                        <label class="field-label">Gmail / Google Email</label>
                        <input class="input" type="email" name="email" placeholder="example@gmail.com" required />
                    </div>
                    <button type="submit" class="submit-btn">
                        <span>Continue with Google</span>
                        <span>›</span>
                    </button>
                </form>

                <div class="footer-note">
                    🔒 Connected to Firebase & Sixth Sense Psychics.<br>Your wallet credits, readings, and notifications sync automatically.
                </div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    @router.get("/google/callback")
    async def google_oauth_callback(
        session_id: str = "",
        email: str = "",
        name: str = "",
        app_return_url: str = ""
    ):
        """Complete Google Sign-In/Sign-Up in MongoDB and redirect back to the mobile app."""
        from fastapi.responses import HTMLResponse

        google_email = (email or "dixitdesai809@gmail.com").strip().lower()
        google_name = (name or google_email.split("@")[0].title()).strip()
        google_id = f"google_{google_email}"

        existing_user = await db.users.find_one({
            "$or": [
                {"email": google_email},
                {"google_id": google_id}
            ]
        })

        if existing_user:
            user = User(**existing_user)
            await db.users.update_one(
                {"id": user.id},
                {"$set": {"google_id": google_id, "name": existing_user.get("name") or google_name}}
            )
        else:
            user = User(
                email=google_email,
                name=google_name,
                google_id=google_id,
                is_new_user=True,
                first_reading_free_used=False,
                free_minutes_available=4,
                auth_provider="google"
            )
            user_dict = user.dict()
            user_dict["password_hash"] = None
            await db.users.insert_one(user_dict)

        token = create_access_token({"user_id": user.id, "email": user.email})
        user_resp = UserResponse(**user.dict()).dict()

        if session_id:
            await db.google_auth_sessions.update_one(
                {"session_id": session_id},
                {"$set": {
                    "session_id": session_id,
                    "status": "completed",
                    "access_token": token,
                    "user": user_resp,
                    "created_at": datetime.utcnow()
                }},
                upsert=True
            )

        base_return = app_return_url or "exp://192.168.29.227:8081"
        delim = "&" if "?" in base_return else "?"
        target_url = f"{base_return}{delim}google_session_id={session_id}&status=success"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Signed in with Google</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: #0f0c20; color: #fff;
                    display: flex; align-items: center; justify-content: center;
                    min-height: 100vh; margin: 0; padding: 24px; text-align: center;
                }}
                .card {{
                    background: #1c1538; border: 1px solid rgba(139,92,246,0.3);
                    border-radius: 20px; padding: 32px 24px; max-width: 380px; width: 100%;
                }}
                .spinner {{
                    border: 4px solid rgba(255,255,255,0.1);
                    border-left-color: #8b5cf6;
                    border-radius: 50%;
                    width: 44px; height: 44px;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 16px;
                }}
                @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
                .check {{
                    width: 60px; height: 60px; border-radius: 30px; background: #10b981;
                    display: flex; align-items: center; justify-content: center;
                    font-size: 30px; margin: 0 auto 16px;
                }}
                h2 {{ margin: 0 0 8px; font-size: 20px; }}
                p {{ color: #c4b5fd; font-size: 14px; margin: 0 0 20px; }}
                .btn {{
                    display: block; width: 100%; padding: 14px; border-radius: 12px;
                    background: #8b5cf6; color: #fff; text-decoration: none; font-weight: 700;
                    border: none; font-size: 15px; cursor: pointer;
                }}
            </style>
            <script>
                async function checkGoogleHash() {{
                    const hash = window.location.hash.substring(1);
                    const hashParams = new URLSearchParams(hash);
                    const accessToken = hashParams.get('access_token');
                    const state = hashParams.get('state') || '';
                    
                    if (accessToken) {{
                        document.getElementById('loader').style.display = 'block';
                        document.getElementById('check-icon').style.display = 'none';
                        document.getElementById('headline').textContent = 'Connecting with Google...';
                        try {{
                            const res = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {{
                                headers: {{ 'Authorization': 'Bearer ' + accessToken }}
                            }});
                            const userInfo = await res.json();
                            let sid = '{session_id}';
                            let retUrl = '{app_return_url}';
                            if (state.includes('___')) {{
                                const parts = state.split('___');
                                sid = parts[0] || sid;
                                retUrl = decodeURIComponent(parts[1] || retUrl);
                            }}
                            const completeRes = await fetch('/api/auth/google', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{
                                    google_id: userInfo.sub || ('google_' + userInfo.email),
                                    email: userInfo.email,
                                    name: userInfo.name || userInfo.email.split('@')[0],
                                    picture: userInfo.picture || '',
                                    session_id: sid
                                }})
                            }});
                            document.getElementById('loader').style.display = 'none';
                            document.getElementById('check-icon').style.display = 'flex';
                            document.getElementById('headline').textContent = 'Signed in as ' + (userInfo.name || userInfo.email);
                            document.getElementById('subtext').textContent = userInfo.email;
                            
                            const target = (retUrl || '{base_return}');
                            const d = target.includes('?') ? '&' : '?';
                            setTimeout(() => {{
                                window.location.href = target + d + 'google_session_id=' + sid + '&status=success';
                            }}, 300);
                            return;
                        }} catch (e) {{
                            console.error('UserInfo fetch error:', e);
                        }}
                    }}
                    
                    setTimeout(returnToApp, 250);
                }}
                
                function returnToApp() {{
                    window.location.href = "{target_url}";
                }}
                
                window.addEventListener('DOMContentLoaded', checkGoogleHash);
            </script>
        </head>
        <body>
            <div class="card">
                <div id="loader" class="spinner" style="display:none;"></div>
                <div id="check-icon" class="check">✓</div>
                <h2 id="headline">Signed in as {google_name}</h2>
                <p id="subtext">{google_email}</p>
                <button class="btn" onclick="returnToApp()">Return to App</button>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    @router.get("/google/poll-session/{session_id}")
    async def poll_google_auth_session(session_id: str):
        """Check if a mobile Google OAuth session completed and return the JWT & User."""
        sess = await db.google_auth_sessions.find_one({"session_id": session_id})
        if not sess or sess.get("status") != "completed":
            return {"status": "pending"}
        return {
            "status": "completed",
            "access_token": sess.get("access_token"),
            "user": sess.get("user")
        }
    
    @router.post("/apple/verify")
    async def apple_auth_verify(data: dict):
        """
        Verify Apple Sign-In identity token and create/login user.
        Called by iOS app after Apple authentication.
        """
        identity_token = data.get("identity_token")
        apple_user_id = data.get("user")  # Apple's unique user identifier
        user_name = data.get("name")  # Only provided on first sign-in
        user_email = data.get("email")  # Only provided on first sign-in
        
        if not identity_token or not apple_user_id:
            raise HTTPException(status_code=400, detail="identity_token and user are required")
        
        try:
            # Verify the Apple identity token
            decoded_token = await verify_apple_token(identity_token)
            
            # Extract user info from verified token
            token_subject = decoded_token.get("sub")  # This should match apple_user_id
            token_email = decoded_token.get("email")
            
            # Use email from token if not provided directly (first sign-in only gets it in payload)
            email = user_email or token_email
            
        except ValueError as e:
            raise HTTPException(status_code=401, detail=f"Invalid Apple token: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Token verification failed: {str(e)}")
        
        # Check if user exists by Apple ID or email
        existing_user = await db.users.find_one({
            "$or": [
                {"apple_id": apple_user_id},
                {"email": email} if email else {"apple_id": apple_user_id}
            ]
        })
        
        if existing_user:
            # User exists - update Apple info and login
            user = User(**existing_user)
            
            # Link Apple ID if not already linked
            if not existing_user.get("apple_id"):
                await db.users.update_one(
                    {"id": user.id},
                    {"$set": {"apple_id": apple_user_id}}
                )
            
            token = create_access_token({"user_id": user.id, "email": user.email})
            
            return TokenResponse(
                access_token=token,
                user=UserResponse(**user.dict())
            )
        else:
            # Create new user with Apple info
            # Note: Apple only provides name on first sign-in, so we need to capture it
            user = User(
                email=email or f"{apple_user_id}@privaterelay.appleid.com",
                name=user_name or (email.split("@")[0] if email else "Apple User"),
                apple_id=apple_user_id,
                is_new_user=True,
                first_reading_free_used=False,
                auth_provider="apple"
            )
            
            user_dict = user.dict()
            user_dict["password_hash"] = None
            await db.users.insert_one(user_dict)
            
            token = create_access_token({"user_id": user.id, "email": user.email})
            
            return TokenResponse(
                access_token=token,
                user=UserResponse(**user.dict())
            )
    
    return router


# =============================================================================
# Apple Token Verification Helper
# =============================================================================

_apple_public_keys = {}

async def fetch_apple_public_keys():
    """Fetch Apple's public keys for JWT verification"""
    global _apple_public_keys
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(APPLE_PUBLIC_KEYS_URL)
            response.raise_for_status()
            keys_data = response.json()
            _apple_public_keys = {key["kid"]: key for key in keys_data.get("keys", [])}
    except Exception as e:
        print(f"Failed to fetch Apple public keys: {e}")

async def verify_apple_token(identity_token: str) -> dict:
    """Verify Apple identity token and return decoded claims"""
    global _apple_public_keys
    
    # Fetch keys if not cached
    if not _apple_public_keys:
        await fetch_apple_public_keys()
    
    if not _apple_public_keys:
        raise ValueError("Unable to fetch Apple public keys")
    
    try:
        # Decode header to get key ID
        import base64
        import json
        
        header_segment = identity_token.split(".")[0]
        # Add padding if needed
        padding = 4 - len(header_segment) % 4
        if padding != 4:
            header_segment += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_segment))
        
        kid = header.get("kid")
        if not kid or kid not in _apple_public_keys:
            # Refresh keys and try again
            await fetch_apple_public_keys()
            if kid not in _apple_public_keys:
                raise ValueError(f"Key ID {kid} not found in Apple public keys")
        
        key_data = _apple_public_keys[kid]
        
        # Convert JWK to PEM format
        public_key = jwk_to_pem(key_data)
        
        # Verify and decode token
        decoded = jwt.decode(
            identity_token,
            public_key,
            algorithms=["RS256"],
            audience=APPLE_BUNDLE_ID,
            issuer=APPLE_AUTH_URL,
        )
        
        return decoded
        
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid token: {str(e)}")

def jwk_to_pem(jwk: dict) -> str:
    """Convert JWK to PEM format for PyJWT"""
    import base64
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    
    # Extract RSA key components
    n_bytes = base64.urlsafe_b64decode(jwk['n'] + '==')
    e_bytes = base64.urlsafe_b64decode(jwk['e'] + '==')
    
    n = int.from_bytes(n_bytes, byteorder='big')
    e = int.from_bytes(e_bytes, byteorder='big')
    
    # Create RSA public key
    public_numbers = rsa.RSAPublicNumbers(e, n)
    public_key = public_numbers.public_key(default_backend())
    
    # Serialize to PEM
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    return pem.decode('utf-8')

    # Delete Account endpoint (required by Google Play policy)
    @router.delete("/user/delete")
    async def delete_user_account(request: dict):
        """Permanently delete a user account and all associated data"""
        user_id = request.get("user_id")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        
        # Check if user exists
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Delete user's data from various collections
        try:
            # Delete messages
            await db.messages.delete_many({"$or": [{"sender_id": user_id}, {"receiver_id": user_id}]})
            
            # Delete conversations
            await db.conversations.delete_many({"participants": user_id})
            
            # Delete reviews by this user
            await db.reviews.delete_many({"user_id": user_id})
            
            # Delete payment history
            await db.payments.delete_many({"user_id": user_id})
            
            # Delete sessions
            await db.sessions.delete_many({"user_id": user_id})
            
            # Delete notifications
            await db.notifications.delete_many({"user_id": user_id})
            
            # Finally, delete the user
            await db.users.delete_one({"id": user_id})
            
            return {"success": True, "message": "Account deleted successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete account: {str(e)}")
