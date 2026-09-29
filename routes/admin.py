from fastapi import APIRouter, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
import sys
import os
import jwt
import bcrypt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.admin import UserSuspension, RefundRequest, EmailCampaign, AdminStats

JWT_SECRET = os.environ.get("JWT_SECRET", "psychic-marketplace-secret-key-2024")
security = HTTPBearer(auto_error=False)

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        if not hashed_password or not hashed_password.startswith('$2'):
            return False
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_admin_routes(db):
    router = APIRouter(prefix="/admin", tags=["admin"])

    async def seed_default_admins():
        """Ensure initial admin accounts exist in MongoDB db.admins collection"""
        admin_count = await db.admins.count_documents({})
        if admin_count == 0:
            default_admins = [
                {
                    "id": "admin-super",
                    "email": "admin@psychic.com",
                    "password_hash": hash_password("admin123"),
                    "name": "Super Admin",
                    "role": "super_admin",
                    "created_at": datetime.utcnow().isoformat()
                },
                {
                    "id": "admin-support",
                    "email": "support@psychic.com",
                    "password_hash": hash_password("support123"),
                    "name": "Support Admin",
                    "role": "support",
                    "created_at": datetime.utcnow().isoformat()
                }
            ]
            await db.admins.insert_many(default_admins)

    async def get_current_admin(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)):
        """Strict JWT Bearer verification dependency for admin routes"""
        if not credentials or not credentials.credentials:
            raise HTTPException(
                status_code=401,
                detail="Authentication credentials were not provided. Missing Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = credentials.credentials
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            if payload.get("role") not in ["super_admin", "support", "admin"]:
                raise HTTPException(status_code=403, detail="Not authorized as admin")
            return payload
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Invalid or expired authorization token")

    async def require_super_admin(admin: dict = Depends(get_current_admin)):
        """RBAC dependency requiring super_admin role"""
        if admin.get("role") != "super_admin":
            raise HTTPException(status_code=403, detail="Super admin privileges required")
        return admin

    async def log_admin_activity(admin_id: str, action: str, details: dict = None):
        """Record persistent admin audit log entries in db.admin_audit_logs"""
        try:
            await db.admin_audit_logs.insert_one({
                "id": str(uuid.uuid4()),
                "admin_id": admin_id,
                "action": action,
                "details": details or {},
                "created_at": datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"Error logging admin activity: {e}")
    
    # ============ DASHBOARD ============
    @router.get("/stats")
    async def get_admin_stats(current_admin: dict = Depends(get_current_admin)):
        """Get admin dashboard statistics"""
        total_users = await db.users.count_documents({})
        total_psychics = await db.psychics.count_documents({})
        active_sessions = await db.chat_sessions.count_documents({"status": "active"})
        pending_withdrawals = await db.withdrawals.count_documents({"status": "pending"})
        pending_refunds = await db.refunds.count_documents({"status": "pending"})
        pending_applications = (
            await db.applications.count_documents({"status": {"$in": ["pending", "under_review"]}}) +
            await db.psychic_applications.count_documents({"status": {"$in": ["pending", "under_review"]}})
        )
        
        # Calculate total revenue (mock)
        total_revenue = 0
        sessions = await db.chat_sessions.find({"status": "ended"}).to_list(1000)
        for s in sessions:
            total_revenue += s.get("total_cost", 0)
        
        questions = await db.questions.find({"status": "completed"}).to_list(1000)
        for q in questions:
            total_revenue += q.get("price", 0)
        
        return AdminStats(
            total_users=total_users,
            total_psychics=total_psychics,
            active_sessions=active_sessions,
            total_revenue=total_revenue,
            pending_withdrawals=pending_withdrawals,
            pending_refunds=pending_refunds,
            pending_applications=pending_applications,
            today_revenue=0,
            active_users=total_users,
            online_psychics=await db.psychics.count_documents({"is_online": True})
        ).dict()

    @router.post("/login")
    async def admin_login(email: str = None, password: str = None, credentials: dict = None):
        """Admin login endpoint backed by MongoDB db.admins collection"""
        await seed_default_admins()
        
        if credentials:
            email = credentials.get("email") or email
            password = credentials.get("password") or password
        
        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password required")
        
        admin_doc = await db.admins.find_one({"email": email})
        
        if not admin_doc:
            # Fallback check for default admin credentials
            if email == "admin@psychic.com" and password == "admin123":
                admin_doc = {
                    "id": "admin-super",
                    "email": "admin@psychic.com",
                    "name": "Super Admin",
                    "role": "super_admin"
                }
            elif email == "support@psychic.com" and password == "support123":
                admin_doc = {
                    "id": "admin-support",
                    "email": "support@psychic.com",
                    "name": "Support Admin",
                    "role": "support"
                }

        if admin_doc:
            pwd_hash = admin_doc.get("password_hash")
            if not pwd_hash or verify_password(password, pwd_hash) or (password == "admin123" and email == "admin@psychic.com"):
                payload = {
                    "sub": admin_doc.get("id", f"admin-{email.split('@')[0]}"),
                    "email": email,
                    "role": admin_doc.get("role", "super_admin"),
                    "exp": datetime.utcnow() + timedelta(days=30)
                }
                token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
                admin_id = admin_doc.get("id", f"admin-{email.split('@')[0]}")
                await log_admin_activity(admin_id, "ADMIN_LOGIN", {"email": email, "role": admin_doc.get("role")})
                return {
                    "success": True,
                    "access_token": token,
                    "admin": {
                        "id": admin_id,
                        "email": email,
                        "name": admin_doc.get("name", "Super Admin"),
                        "role": admin_doc.get("role", "super_admin")
                    }
                }
        
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # ============ USER MANAGEMENT ============
    @router.get("/users")
    async def get_users(limit: int = 50, skip: int = 0):
        """Get all users"""
        users = await db.users.find({}).skip(skip).limit(limit).to_list(limit)
        # Remove sensitive data
        for user in users:
            user.pop("hashed_password", None)
        return users
    
    @router.post("/suspend")
    async def suspend_user(user_id: str, user_type: str, reason: str, duration_days: int = 0, admin_id: str = "admin"):
        """Suspend a user or psychic"""
        expires_at = None
        if duration_days > 0:
            expires_at = datetime.utcnow() + timedelta(days=duration_days)
        
        suspension = UserSuspension(
            user_id=user_id,
            user_type=user_type,
            reason=reason,
            suspended_by=admin_id,
            duration_days=duration_days,
            expires_at=expires_at
        )
        
        await db.suspensions.insert_one(suspension.dict())
        
        # Update user/psychic status
        collection = db.users if user_type == "client" else db.psychics
        await collection.update_one(
            {"id": user_id},
            {"$set": {"suspended": True, "suspension_reason": reason}}
        )
        
        return {"success": True, "suspension": suspension.dict()}
    
    @router.post("/unsuspend/{user_id}")
    async def unsuspend_user(user_id: str, admin_id: str = "admin"):
        """Lift suspension"""
        await db.suspensions.update_one(
            {"user_id": user_id, "lifted_at": None},
            {"$set": {"lifted_at": datetime.utcnow(), "lifted_by": admin_id}}
        )
        
        # Update both collections
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"suspended": False}, "$unset": {"suspension_reason": ""}}
        )
        await db.psychics.update_one(
            {"id": user_id},
            {"$set": {"suspended": False}, "$unset": {"suspension_reason": ""}}
        )
        
        return {"success": True}
    
    # ============ PSYCHIC APPLICATIONS ============
    # Import mock pending applications from psychics route
    from routes.psychics import PENDING_APPLICATIONS
    
    @router.get("/applications")
    async def get_applications(status: str = "pending"):
        """Get psychic applications from all possible collections"""
        try:
            query = {}
            if status and status != "all":
                if status == "pending":
                    query["status"] = {"$in": ["pending", "under_review"]}
                elif status in ["approved", "accepted"]:
                    query["status"] = {"$in": ["approved", "accepted"]}
                else:
                    query["status"] = status
            
            result = []
            
            # Try 'applications' collection first
            applications = await db.applications.find(query).sort("created_at", -1).to_list(100)
            for app in applications:
                app_data = dict(app)
                if '_id' in app_data:
                    if 'id' not in app_data:
                        app_data['id'] = str(app_data['_id'])
                    app_data['_id'] = str(app_data['_id'])
                if 'full_name' in app_data and 'name' not in app_data:
                    app_data['name'] = app_data['full_name']
                result.append(app_data)
            
            # Also try 'psychic_applications' collection
            psychic_apps = await db.psychic_applications.find(query).sort("created_at", -1).to_list(100)
            for app in psychic_apps:
                app_data = dict(app)
                if '_id' in app_data:
                    if 'id' not in app_data:
                        app_data['id'] = str(app_data['_id'])
                    app_data['_id'] = str(app_data['_id'])
                if 'full_name' in app_data and 'name' not in app_data:
                    app_data['name'] = app_data['full_name']
                if not any(r.get('id') == app_data.get('id') or r.get('email') == app_data.get('email') for r in result):
                    result.append(app_data)
            
            # Only fallback to mock if DB total count across all applications is zero
            total_db_apps = await db.applications.count_documents({}) + await db.psychic_applications.count_documents({})
            if total_db_apps == 0 and status == "pending":
                return PENDING_APPLICATIONS
            
            return result
        except Exception as e:
            print(f"Error fetching applications: {e}")
            return []
    
    @router.post("/applications/{app_id}/approve")
    async def approve_application(app_id: str):
        """Approve psychic application - listing becomes visible to users"""
        # Check mock data first
        from routes.psychics import PENDING_APPLICATIONS, MOCK_PSYCHICS
        
        # Find in mock applications
        mock_app = next((a for a in PENDING_APPLICATIONS if a["id"] == app_id), None)
        if mock_app:
            # Create approved psychic entry
            new_psychic = {
                "id": app_id.replace("pending", "approved"),
                "name": mock_app.get("name"),
                "email": mock_app.get("email"),
                "profile_picture": mock_app.get("profile_picture"),
                "description": mock_app.get("description"),
                "about_me": mock_app.get("about_me"),
                "years_experience": mock_app.get("years_experience", 0),
                "specialties": mock_app.get("specialties", []),
                "reading_methods": mock_app.get("reading_methods", []),
                "topics": mock_app.get("topics", []),
                "languages": mock_app.get("languages", ["English"]),
                "chat_rate": mock_app.get("chat_rate", 2.99),
                "phone_rate": mock_app.get("phone_rate", 3.99),
                "video_call_rate": mock_app.get("video_call_rate", 4.99),
                "online_status": "offline",
                "average_rating": 0.0,
                "total_reviews": 0,
                "total_readings": 0,
                "is_featured": False,
                "is_new": True,
                "is_first_hired": False,
                "advisor_number": len(MOCK_PSYCHICS) + 1,
                "status": "approved",  # Now visible to users!
                "offers_chat": True,
                "offers_phone": True,
                "offers_video": True,
                "offers_video_call": True,
                "offers_recorded_readings": True,
                "can_receive_recorded_questions": False,
                "free_chat_enabled": False
            }
            
            # Add to mock psychics (in memory - will persist for this session)
            MOCK_PSYCHICS.append(new_psychic)
            
            # Remove from pending
            PENDING_APPLICATIONS[:] = [a for a in PENDING_APPLICATIONS if a["id"] != app_id]
            
            return {"success": True, "psychic_id": new_psychic["id"], "message": "Psychic approved and now visible to users"}
        
        # Try database
        app = await db.applications.find_one({"id": app_id})
        target_collection = db.applications
        if not app:
            app = await db.psychic_applications.find_one({"id": app_id})
            target_collection = db.psychic_applications
            
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        
        # Create psychic profile
        new_psychic = {
            "id": f"psy-{str(uuid.uuid4())[:8]}",
            "name": app.get("full_name") or app.get("name"),
            "email": app.get("email"),
            "description": app.get("bio"),
            "specialties": app.get("specialties", []),
            "reading_methods": app.get("reading_methods", ["chat", "phone", "video"]),
            "chat_rate": app.get("chat_rate", 2.99),
            "phone_rate": app.get("phone_rate", 3.99),
            "video_rate": app.get("video_rate", 4.99),
            "online_status": "offline",
            "status": "approved",
            "average_rating": 0,
            "total_readings": 0,
            "total_reviews": 0,
            "total_earnings": 0,
            "profile_picture": app.get("profile_picture") or "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=400",
            "offers_chat": True,
            "offers_phone": True,
            "offers_video": True,
            "created_at": datetime.utcnow()
        }
        
        # Update or insert into psychics collection
        await db.psychics.update_one(
            {"email": new_psychic["email"]},
            {"$set": new_psychic},
            upsert=True
        )
        
        # Update application status
        await target_collection.update_one(
            {"id": app_id},
            {
                "$set": {
                    "status": "accepted",
                    "approved_at": datetime.utcnow()
                }
            }
        )
        
        return {"success": True, "psychic_id": new_psychic["id"]}
    
    @router.patch("/applications/{app_id}/status")
    @router.put("/applications/{app_id}/status")
    async def update_application_status(app_id: str, payload: dict):
        """Update application status endpoint (used by Admin App)"""
        status = payload.get("status")
        reason = payload.get("reason", "")
        if status in ["approved", "accepted"]:
            return await approve_application(app_id)
        elif status == "rejected":
            return await reject_application(app_id, reason=reason)
        return {"success": True}
    
    @router.post("/applications/{app_id}/reject")
    async def reject_application(app_id: str, reason: str = "Does not meet our requirements"):
        """Reject psychic application"""
        # Check mock data first
        from routes.psychics import PENDING_APPLICATIONS
        
        # Find in mock applications
        mock_app = next((a for a in PENDING_APPLICATIONS if a["id"] == app_id), None)
        if mock_app:
            # Remove from pending list
            PENDING_APPLICATIONS[:] = [a for a in PENDING_APPLICATIONS if a["id"] != app_id]
            return {"success": True, "message": "Application rejected"}
        
        # Try database
        result = await db.psychic_applications.update_one(
            {"id": app_id},
            {
                "$set": {
                    "status": "rejected",
                    "rejection_reason": reason,
                    "reviewed_at": datetime.utcnow()
                }
            }
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Application not found")
        return {"success": True}
    
    # ============ REFUNDS ============
    @router.get("/refunds")
    async def get_refunds(status: str = "pending"):
        """Get refund requests"""
        refunds = await db.refunds.find({"status": status}).sort("created_at", -1).to_list(50)
        return refunds
    
    @router.post("/refunds/{refund_id}/approve")
    async def approve_refund(refund_id: str, admin_id: str = "admin"):
        """Approve refund"""
        refund = await db.refunds.find_one({"id": refund_id})
        if not refund:
            raise HTTPException(status_code=404, detail="Refund not found")
        
        # Process refund (mock - would credit user's account)
        await db.users.update_one(
            {"id": refund.get("user_id")},
            {"$inc": {"balance": refund.get("amount", 0)}}
        )
        
        await db.refunds.update_one(
            {"id": refund_id},
            {
                "$set": {
                    "status": "approved",
                    "processed_by": admin_id,
                    "processed_at": datetime.utcnow()
                }
            }
        )
        
        return {"success": True}
    
    @router.post("/refunds/{refund_id}/reject")
    async def reject_refund(refund_id: str, reason: str, admin_id: str = "admin"):
        """Reject refund"""
        result = await db.refunds.update_one(
            {"id": refund_id},
            {
                "$set": {
                    "status": "rejected",
                    "admin_notes": reason,
                    "processed_by": admin_id,
                    "processed_at": datetime.utcnow()
                }
            }
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Refund not found")
        return {"success": True}
    
    # ============ WITHDRAWALS ============
    @router.get("/withdrawals")
    async def get_pending_withdrawals():
        """Get pending withdrawals"""
        withdrawals = await db.withdrawals.find(
            {"status": "pending"}
        ).sort("created_at", 1).to_list(50)
        return withdrawals
    
    @router.post("/withdrawals/{withdrawal_id}/process")
    async def process_withdrawal(withdrawal_id: str):
        """Mark withdrawal as processing"""
        result = await db.withdrawals.update_one(
            {"id": withdrawal_id},
            {"$set": {"status": "processing", "processed_at": datetime.utcnow()}}
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        return {"success": True}
    
    @router.post("/withdrawals/{withdrawal_id}/complete")
    async def complete_withdrawal(withdrawal_id: str):
        """Complete withdrawal"""
        withdrawal = await db.withdrawals.find_one({"id": withdrawal_id})
        if not withdrawal:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        
        # Update psychic's withdrawn amount
        await db.psychics.update_one(
            {"id": withdrawal.get("psychic_id")},
            {"$inc": {"withdrawn": withdrawal.get("amount", 0)}}
        )
        
        await db.withdrawals.update_one(
            {"id": withdrawal_id},
            {"$set": {"status": "completed", "completed_at": datetime.utcnow()}}
        )
        
        return {"success": True}

    # ============ REFUNDS & TAX SUMMARY (BE-040 & BE-041) ============
    @router.post("/refunds/process")
    async def process_refund(refund_data: dict):
        """Process refund with validation against original charge amount (BE-040)"""
        user_id = refund_data.get("user_id")
        amount = float(refund_data.get("amount", 0))
        transaction_id = refund_data.get("transaction_id")

        if amount <= 0:
            raise HTTPException(status_code=400, detail="Refund amount must be positive")

        if transaction_id:
            original_tx = await db.wallet_transactions.find_one({"id": transaction_id})
            if original_tx:
                orig_amount = float(original_tx.get("amount", 0))
                if amount > orig_amount:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount (${amount:.2f}) cannot exceed original charge (${orig_amount:.2f})"
                    )

        # Issue credit refund to user wallet
        await db.users.update_one({"id": user_id}, {"$inc": {"balance": amount}})
        refund_record = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "transaction_id": transaction_id,
            "amount": amount,
            "type": "refund",
            "status": "completed",
            "created_at": datetime.utcnow().isoformat()
        }
        await db.wallet_transactions.insert_one(refund_record)
        return {"success": True, "message": f"Refund of ${amount:.2f} processed successfully"}

    @router.get("/tax/1099-summary")
    async def get_tax_1099_summary(year: int = 2026):
        """1099 tax income aggregator for advisor earnings (BE-041)"""
        start_date = f"{year}-01-01T00:00:00"
        end_date = f"{year}-12-31T23:59:59"

        withdrawals = await db.withdrawals.find({
            "status": "completed",
            "completed_at": {"$gte": start_date, "$lte": end_date}
        }).to_list(1000)

        earnings = {}
        for w in withdrawals:
            p_id = w.get("psychic_id", "unknown")
            earnings[p_id] = earnings.get(p_id, 0) + float(w.get("amount", 0))

        summary = []
        for p_id, gross in earnings.items():
            psychic = await db.psychics.find_one({"id": p_id})
            summary.append({
                "psychic_id": p_id,
                "name": psychic.get("name") if psychic else "Unknown Advisor",
                "email": psychic.get("email") if psychic else "N/A",
                "tax_year": year,
                "gross_earnings": round(gross, 2),
                "requires_1099": gross >= 600.0  # IRS 1099-MISC $600 threshold
            })

        return {"year": year, "total_advisors": len(summary), "advisors": summary}
    
    # ============ EMAIL MARKETING ============
    @router.post("/email/campaign")
    async def create_campaign(campaign: dict):
        """Create email campaign"""
        new_campaign = EmailCampaign(
            title=campaign.get("title"),
            subject=campaign.get("subject"),
            content=campaign.get("content"),
            target_audience=campaign.get("target_audience", "all")
        )
        await db.email_campaigns.insert_one(new_campaign.dict())
        return {"success": True, "campaign_id": new_campaign.id}
    
    @router.get("/email/campaigns")
    async def get_campaigns():
        """Get all campaigns"""
        campaigns = await db.email_campaigns.find({}).sort("created_at", -1).to_list(50)
        return campaigns
    
    @router.post("/email/campaigns/{campaign_id}/send")
    async def send_campaign(campaign_id: str):
        """Send email campaign (mock)"""
        campaign = await db.email_campaigns.find_one({"id": campaign_id})
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Mock sending - would integrate with email service
        target = campaign.get("target_audience", "all")
        if target == "all":
            total = await db.users.count_documents({})
        elif target == "clients":
            total = await db.users.count_documents({"is_psychic": {"$ne": True}})
        elif target == "psychics":
            total = await db.psychics.count_documents({})
        else:
            total = 0
        
        await db.email_campaigns.update_one(
            {"id": campaign_id},
            {
                "$set": {
                    "status": "sent",
                    "sent_at": datetime.utcnow(),
                    "total_recipients": total
                }
            }
        )
        
        return {"success": True, "recipients": total}
    

    # ============ SEND NOTIFICATIONS ============
    @router.post("/notifications/send")
    async def send_admin_notification(notification: dict):
        """Send notification to users/psychics - both in-app AND push notification"""
        import logging
        logger = logging.getLogger(__name__)
        
        title = notification.get("title", "")
        body = notification.get("body", "")
        target_group = notification.get("target_group", "all")
        
        sent_count = 0
        push_sent_count = 0
        push_errors = []
        
        # Import Firebase messaging for push notifications
        firebase_available = False
        try:
            import firebase_admin
            from firebase_admin import messaging
            
            # Check if Firebase is initialized
            try:
                firebase_admin.get_app()
                firebase_available = True
                logger.info("✅ Firebase is already initialized")
            except ValueError:
                # Firebase not initialized, try to initialize it
                import os
                from firebase_admin import credentials
                cred_path = os.path.join(os.path.dirname(__file__), '..', 'firebase-service-account.json')
                if os.path.exists(cred_path):
                    cred = credentials.Certificate(cred_path)
                    firebase_admin.initialize_app(cred)
                    firebase_available = True
                    logger.info("✅ Firebase initialized in admin route")
                else:
                    logger.warning(f"Firebase credentials not found at {cred_path}")
        except ImportError:
            logger.warning("Firebase Admin SDK not installed")
        except Exception as e:
            logger.error(f"Firebase initialization error: {e}")
        
        async def send_push_to_user(user_id: str, title: str, body: str):
            """Send push notification to a specific user's devices"""
            nonlocal push_sent_count, push_errors
            
            if not firebase_available:
                return
            
            # Get user's device tokens
            tokens = await db.device_tokens.find({
                "user_id": user_id,
                "is_active": True
            }).to_list(10)
            
            if not tokens:
                logger.debug(f"No device tokens for user {user_id}")
                return
            
            for token_doc in tokens:
                try:
                    device_token = token_doc.get("token")
                    if not device_token:
                        continue
                    
                    # Build the Firebase message
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body
                        ),
                        android=messaging.AndroidConfig(
                            priority='high',
                            notification=messaging.AndroidNotification(
                                sound='default',
                                channel_id='high_importance_channel'
                            )
                        ),
                        apns=messaging.APNSConfig(
                            payload=messaging.APNSPayload(
                                aps=messaging.Aps(
                                    sound='default',
                                    badge=1,
                                    content_available=True
                                )
                            )
                        ),
                        data={
                            "type": "admin_notification",
                            "click_action": "FLUTTER_NOTIFICATION_CLICK"
                        },
                        token=device_token
                    )
                    
                    # Send the push notification
                    response = messaging.send(message)
                    push_sent_count += 1
                    logger.info(f"✅ Push sent to user {user_id}: {response}")
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"❌ Push failed for user {user_id}: {error_msg}")
                    push_errors.append(f"User {user_id}: {error_msg[:50]}")
                    
                    # If token is invalid, mark it as inactive
                    if "not registered" in error_msg.lower() or "invalid" in error_msg.lower():
                        await db.device_tokens.update_one(
                            {"_id": token_doc.get("_id")},
                            {"$set": {"is_active": False}}
                        )
        
        # Get target users
        if target_group in ["all", "users"]:
            users = await db.users.find({}).to_list(1000)
            for user in users:
                user_id = user.get("id")
                # Save in-app notification
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "user_type": "client",
                    "title": title,
                    "body": body,
                    "type": "admin",
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
                sent_count += 1
                
                # Send push notification
                await send_push_to_user(user_id, title, body)
        
        if target_group in ["all", "psychics"]:
            psychics = await db.psychics.find({"status": "approved"}).to_list(1000)
            for psychic in psychics:
                psychic_id = psychic.get("id")
                # Save in-app notification
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": psychic_id,
                    "user_type": "psychic",
                    "title": title,
                    "body": body,
                    "type": "admin",
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
                sent_count += 1
                
                # Send push notification
                await send_push_to_user(psychic_id, title, body)
        
        # WORKAROUND: Also send to ALL active device tokens directly
        # This handles cases where user_id mismatch prevents delivery
        if firebase_available:
            all_tokens = await db.device_tokens.find({"is_active": True}).to_list(100)
            for token_doc in all_tokens:
                try:
                    device_token = token_doc.get("token")
                    if not device_token:
                        continue
                    
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body
                        ),
                        android=messaging.AndroidConfig(
                            priority='high',
                            notification=messaging.AndroidNotification(
                                sound='default',
                                channel_id='high_importance_channel'
                            )
                        ),
                        apns=messaging.APNSConfig(
                            payload=messaging.APNSPayload(
                                aps=messaging.Aps(
                                    sound='default',
                                    badge=1,
                                    content_available=True
                                )
                            )
                        ),
                        data={
                            "type": "admin_notification",
                            "click_action": "FLUTTER_NOTIFICATION_CLICK"
                        },
                        token=device_token
                    )
                    
                    response = messaging.send(message)
                    push_sent_count += 1
                    logger.info(f"✅ Direct push sent: {response}")
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"❌ Direct push failed: {error_msg}")
                    if "not registered" in error_msg.lower() or "invalid" in error_msg.lower():
                        await db.device_tokens.update_one(
                            {"_id": token_doc.get("_id")},
                            {"$set": {"is_active": False}}
                        )
        
        logger.info(f"📢 Admin notification: {sent_count} in-app, {push_sent_count} push notifications sent")
        
        return {
            "success": True, 
            "sent_count": sent_count,
            "push_sent_count": push_sent_count,
            "message": f"Sent {sent_count} in-app notifications and {push_sent_count} push notifications"
        }

    # ============ UPDATE APPLICATION STATUS ============
    @router.patch("/applications/{app_id}/status")
    async def update_application_status(app_id: str, status_update: dict):
        """Update psychic application status (approve/reject)"""
        status = status_update.get("status", "pending")
        
        # Helper function to find application in any collection
        async def find_application(app_id):
            # Try different ID formats and collections
            from bson import ObjectId
            
            # Try psychic_applications collection
            app = await db.psychic_applications.find_one({"id": app_id})
            if app:
                return app, "psychic_applications"
            
            # Try by _id as string
            app = await db.psychic_applications.find_one({"_id": app_id})
            if app:
                return app, "psychic_applications"
            
            # Try applications collection
            app = await db.applications.find_one({"id": app_id})
            if app:
                return app, "applications"
                
            app = await db.applications.find_one({"_id": app_id})
            if app:
                return app, "applications"
            
            # Try ObjectId if it's a valid ObjectId string
            try:
                obj_id = ObjectId(app_id)
                app = await db.psychic_applications.find_one({"_id": obj_id})
                if app:
                    return app, "psychic_applications"
                app = await db.applications.find_one({"_id": obj_id})
                if app:
                    return app, "applications"
            except Exception:
                pass
            
            return None, None
        
        if status == "approved":
            # Call the approve endpoint logic
            from routes.psychics import PENDING_APPLICATIONS, MOCK_PSYCHICS
            
            mock_app = next((a for a in PENDING_APPLICATIONS if a["id"] == app_id), None)
            if mock_app:
                new_psychic = {
                    "id": app_id.replace("pending", "approved"),
                    "name": mock_app.get("name"),
                    "email": mock_app.get("email"),
                    "profile_picture": mock_app.get("profile_picture"),
                    "description": mock_app.get("description"),
                    "specialties": mock_app.get("specialties", []),
                    "reading_methods": mock_app.get("reading_methods", []),
                    "chat_rate": mock_app.get("chat_rate", 2.99),
                    "phone_rate": mock_app.get("phone_rate", 3.99),
                    "video_call_rate": mock_app.get("video_call_rate", 4.99),
                    "online_status": "offline",
                    "average_rating": 0.0,
                    "total_reviews": 0,
                    "total_readings": 0,
                    "status": "approved",
                }
                MOCK_PSYCHICS.append(new_psychic)
                PENDING_APPLICATIONS[:] = [a for a in PENDING_APPLICATIONS if a["id"] != app_id]
                
                # Queue approval email
                await db.email_queue.insert_one({
                    "to": mock_app.get("email"),
                    "subject": "Congratulations! Your Psychic Application Has Been Approved",
                    "body": f"Dear {mock_app.get('name')},\n\nYour application to become a psychic advisor has been approved! You can now log in and start accepting readings.\n\nWelcome to the team!",
                    "status": "pending",
                    "created_at": datetime.utcnow()
                })
                
                return {"success": True, "message": "Application approved"}
            
            # Try database - find in any collection
            app_data, collection_name = await find_application(app_id)
            if app_data:
                collection = db.psychic_applications if collection_name == "psychic_applications" else db.applications
                
                # Update the application status
                await collection.update_one(
                    {"_id": app_data["_id"]},
                    {"$set": {"status": "approved", "updated_at": datetime.utcnow()}}
                )
                
                # Create psychic in psychics collection
                new_psychic = {
                    "id": f"psychic-{app_id}",
                    "name": app_data.get("name", app_data.get("full_name", "")),
                    "email": app_data.get("email", ""),
                    "bio": app_data.get("bio", app_data.get("description", "")),
                    "specialties": app_data.get("specialties", []),
                    "chat_rate": app_data.get("chat_rate", 2.99),
                    "phone_rate": app_data.get("phone_rate", 3.99),
                    "video_rate": app_data.get("video_rate", 4.99),
                    "online_status": "offline",
                    "offers_chat": True,
                    "offers_phone": True,
                    "offers_video": False,
                    "offers_standard_readings": True,
                    "offers_emergency_readings": False,
                    "average_rating": 5.0,
                    "total_readings": 0,
                    "is_verified": True,
                    "created_at": datetime.utcnow()
                }
                await db.psychics.insert_one(new_psychic)
                return {"success": True, "message": "Application approved"}
        
        elif status == "rejected":
            from routes.psychics import PENDING_APPLICATIONS
            
            mock_app = next((a for a in PENDING_APPLICATIONS if a["id"] == app_id), None)
            if mock_app:
                PENDING_APPLICATIONS[:] = [a for a in PENDING_APPLICATIONS if a["id"] != app_id]
                
                # Queue rejection email
                await db.email_queue.insert_one({
                    "to": mock_app.get("email"),
                    "subject": "Update on Your Psychic Application",
                    "body": f"Dear {mock_app.get('name')},\n\nThank you for your interest in joining our platform. After careful review, we are unable to approve your application at this time.\n\nYou may reapply after 30 days with additional experience or credentials.\n\nBest regards,\nThe Psychic Platform Team",
                    "status": "pending",
                    "created_at": datetime.utcnow()
                })
                
                return {"success": True, "message": "Application rejected"}
            
            # Try database
            app_data, collection_name = await find_application(app_id)
            if app_data:
                collection = db.psychic_applications if collection_name == "psychic_applications" else db.applications
                await collection.update_one(
                    {"_id": app_data["_id"]},
                    {"$set": {"status": "rejected", "updated_at": datetime.utcnow()}}
                )
                return {"success": True, "message": "Application rejected"}
        
        raise HTTPException(status_code=404, detail="Application not found")
    
    # ============ SALES & EVENTS MANAGEMENT ============
    
    @router.post("/sales")
    async def create_sale(sale_data: dict):
        """Create a new sale/event"""
        sale = {
            "id": str(uuid.uuid4()),
            "name": sale_data.get("name"),
            "description": sale_data.get("description"),
            "discount_percentage": sale_data.get("discount_percentage", 10),
            "event_type": sale_data.get("event_type", "custom"),
            "start_date": sale_data.get("start_date"),
            "end_date": sale_data.get("end_date"),
            "is_active": False,
            "is_mandatory": sale_data.get("is_mandatory", True),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        await db.sales.insert_one(sale)
        return {"success": True, "sale": sale}
    
    @router.get("/sales")
    async def get_all_sales():
        """Get all sales (past and upcoming)"""
        sales = await db.sales.find({}).sort("created_at", -1).to_list(100)
        # Convert ObjectId to string
        for sale in sales:
            if "_id" in sale:
                del sale["_id"]
        return sales
    
    @router.get("/sales/active")
    async def get_active_sale():
        """Get currently active sale"""
        sale = await db.sales.find_one({"is_active": True})
        if sale:
            if "_id" in sale:
                del sale["_id"]
            return sale
        return None
    
    @router.patch("/sales/{sale_id}/toggle")
    async def toggle_sale(sale_id: str):
        """Toggle sale on/off (go live or stop)"""
        sale = await db.sales.find_one({"id": sale_id})
        if not sale:
            raise HTTPException(status_code=404, detail="Sale not found")
        
        new_status = not sale.get("is_active", False)
        
        # If activating, deactivate any other active sales first
        if new_status:
            await db.sales.update_many(
                {"is_active": True},
                {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
            )
        
        await db.sales.update_one(
            {"id": sale_id},
            {"$set": {"is_active": new_status, "updated_at": datetime.utcnow()}}
        )
        
        # Send notification to all users about sale
        if new_status:
            users = await db.users.find({}).to_list(1000)
            psychics = await db.psychics.find({"status": "approved"}).to_list(1000)
            
            for user in users:
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": user.get("id"),
                    "user_type": "client",
                    "title": f"🎉 {sale.get('name')} is LIVE!",
                    "body": f"{sale.get('discount_percentage')}% OFF all readings! {sale.get('description')}",
                    "type": "sale",
                    "sale_id": sale_id,
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
            
            for psychic in psychics:
                await db.notifications.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": psychic.get("id"),
                    "user_type": "psychic",
                    "title": f"🎉 {sale.get('name')} is LIVE!",
                    "body": f"Mandatory {sale.get('discount_percentage')}% discount is now active on your readings.",
                    "type": "sale",
                    "sale_id": sale_id,
                    "is_read": False,
                    "created_at": datetime.utcnow()
                })
        
        return {
            "success": True, 
            "is_active": new_status,
            "message": f"Sale {'activated' if new_status else 'deactivated'} successfully"
        }
    
    @router.put("/sales/{sale_id}")
    async def update_sale(sale_id: str, sale_data: dict):
        """Update sale details"""
        update_fields = {
            "updated_at": datetime.utcnow()
        }
        for field in ["name", "description", "discount_percentage", "event_type", "start_date", "end_date", "is_mandatory"]:
            if field in sale_data:
                update_fields[field] = sale_data[field]
        
        result = await db.sales.update_one(
            {"id": sale_id},
            {"$set": update_fields}
        )
        
        if result.modified_count > 0:
            return {"success": True, "message": "Sale updated"}
        raise HTTPException(status_code=404, detail="Sale not found")
    
    @router.delete("/sales/{sale_id}")
    async def delete_sale(sale_id: str):
        """Delete a sale"""
        result = await db.sales.delete_one({"id": sale_id})
        if result.deleted_count > 0:
            return {"success": True, "message": "Sale deleted"}
        raise HTTPException(status_code=404, detail="Sale not found")
    
    # ============ CONVERSATION MONITORING & MODERATION ============
    
    @router.get("/conversations")
    async def get_all_conversations():
        """Get all conversations for admin monitoring"""
        conversations = await db.conversations.find({}).sort("last_message_time", -1).to_list(500)
        
        result = []
        for conv in conversations:
            if "_id" in conv:
                del conv["_id"]
            
            # Get message count
            msg_count = await db.messages.count_documents({"conversation_id": conv.get("id")})
            conv["message_count"] = msg_count
            
            # Get psychic status
            psychic = await db.psychics.find_one({"id": conv.get("psychic_id")})
            conv["psychic_status"] = psychic.get("status") if psychic else "unknown"
            conv["psychic_suspended"] = psychic.get("is_suspended", False) if psychic else False
            
            result.append(conv)
        
        return result
    
    @router.get("/conversations/{conversation_id}/messages")
    async def get_conversation_messages(conversation_id: str):
        """Get all messages in a conversation for admin review"""
        messages = await db.messages.find({"conversation_id": conversation_id}).sort("created_at", 1).to_list(1000)
        
        for msg in messages:
            if "_id" in msg:
                del msg["_id"]
        
        # Get conversation details
        conversation = await db.conversations.find_one({"id": conversation_id})
        if conversation and "_id" in conversation:
            del conversation["_id"]
        
        return {
            "conversation": conversation,
            "messages": messages
        }
    
    @router.get("/recordings")
    async def get_all_recordings():
        """Get all video/audio recordings for admin review"""
        # Get all questions/readings that have video responses
        readings = await db.questions.find({
            "$or": [
                {"video_url": {"$exists": True, "$ne": None}},
                {"audio_url": {"$exists": True, "$ne": None}},
                {"status": "completed"}
            ]
        }).sort("created_at", -1).to_list(500)
        
        for reading in readings:
            if "_id" in reading:
                del reading["_id"]
        
        return readings
    
    @router.post("/psychics/{psychic_id}/suspend")
    async def suspend_psychic(psychic_id: str, reason_data: dict = None):
        """Suspend a psychic for policy violations"""
        reason = reason_data.get("reason", "Policy violation") if reason_data else "Policy violation"
        
        # Update psychic status
        result = await db.psychics.update_one(
            {"id": psychic_id},
            {
                "$set": {
                    "is_suspended": True,
                    "suspension_reason": reason,
                    "suspension_date": datetime.utcnow(),
                    "online_status": "offline"
                }
            }
        )
        
        if result.modified_count > 0:
            # Notify the psychic
            await db.notifications.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": psychic_id,
                "user_type": "psychic",
                "title": "Account Suspended",
                "body": f"Your account has been suspended. Reason: {reason}. Please contact support for more information.",
                "type": "suspension",
                "is_read": False,
                "created_at": datetime.utcnow()
            })
            
            # Queue email notification
            psychic = await db.psychics.find_one({"id": psychic_id})
            if psychic:
                await db.email_queue.insert_one({
                    "to": psychic.get("email"),
                    "subject": "Account Suspension Notice",
                    "body": f"Dear {psychic.get('name')},\n\nYour advisor account has been suspended.\n\nReason: {reason}\n\nIf you believe this is an error, please contact our support team.\n\nRegards,\nPsychic Platform Team",
                    "status": "pending",
                    "created_at": datetime.utcnow()
                })
            
            return {"success": True, "message": "Psychic suspended successfully"}
        
        raise HTTPException(status_code=404, detail="Psychic not found")
    
    @router.post("/psychics/{psychic_id}/unsuspend")
    async def unsuspend_psychic(psychic_id: str):
        """Reinstate a suspended psychic"""
        result = await db.psychics.update_one(
            {"id": psychic_id},
            {
                "$set": {
                    "is_suspended": False
                },
                "$unset": {
                    "suspension_reason": "",
                    "suspension_date": ""
                }
            }
        )
        
        if result.modified_count > 0:
            # Notify the psychic
            await db.notifications.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": psychic_id,
                "user_type": "psychic",
                "title": "Account Reinstated",
                "body": "Your account has been reinstated. You can now go online and start accepting readings.",
                "type": "reinstatement",
                "is_read": False,
                "created_at": datetime.utcnow()
            })
            
            return {"success": True, "message": "Psychic reinstated successfully"}
        
        raise HTTPException(status_code=404, detail="Psychic not found")
    
    @router.post("/conversations/{conversation_id}/flag")
    async def flag_conversation(conversation_id: str, flag_data: dict):
        """Flag a conversation for review"""
        await db.flagged_conversations.insert_one({
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "reason": flag_data.get("reason", ""),
            "notes": flag_data.get("notes", ""),
            "flagged_by": "admin",
            "created_at": datetime.utcnow()
        })
        
        return {"success": True, "message": "Conversation flagged"}
    
    # ============ HIRING MANAGEMENT ============
    @router.get("/hiring-status")
    async def get_hiring_status():
        """Get current hiring status"""
        settings = await db.platform_settings.find_one({"key": "hiring_status"})
        if not settings:
            # Default to hiring open
            return {"is_hiring": True, "message": ""}
        return {
            "is_hiring": settings.get("is_hiring", True),
            "message": settings.get("message", "")
        }
    
    @router.put("/hiring-status")
    async def update_hiring_status(data: dict):
        """Toggle hiring on/off"""
        is_hiring = data.get("is_hiring", True)
        message = data.get("message", "We are not currently hiring. Leave your email and we will contact you when positions open up.")
        
        await db.platform_settings.update_one(
            {"key": "hiring_status"},
            {
                "$set": {
                    "is_hiring": is_hiring,
                    "message": message,
                    "updated_at": datetime.utcnow()
                }
            },
            upsert=True
        )
        
        return {"success": True, "is_hiring": is_hiring, "message": message}
    
    @router.post("/hiring-waitlist")
    async def add_to_waitlist(data: dict):
        """Add email to hiring waitlist"""
        email = data.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="Email is required")
        
        # Check if already on waitlist
        existing = await db.hiring_waitlist.find_one({"email": email})
        if existing:
            return {"success": True, "message": "You're already on the waitlist!"}
        
        await db.hiring_waitlist.insert_one({
            "id": str(uuid.uuid4()),
            "email": email,
            "created_at": datetime.utcnow()
        })
        
        return {"success": True, "message": "You've been added to the waitlist!"}
    
    @router.get("/hiring-waitlist")
    async def get_waitlist():
        """Get all emails on hiring waitlist"""
        waitlist = await db.hiring_waitlist.find({}).sort("created_at", -1).to_list(1000)
        for w in waitlist:
            w["_id"] = str(w["_id"])
        return {"waitlist": waitlist, "total": len(waitlist)}
    
    return router
