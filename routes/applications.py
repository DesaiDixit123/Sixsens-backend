from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import List, Optional
from datetime import datetime
import uuid
import sys
import os
import aiofiles
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.application import (
    PsychicApplication, ApplicationCreate, ApplicationResponse, ApplicationStatusUpdate
)
from models.message import Notification

def create_application_routes(db):
    router = APIRouter(prefix="/applications", tags=["applications"])
    
    async def send_email(to_email: str, subject: str, body: str):
        """
        Send email notification.
        NOTE: This is a placeholder. In production, integrate with:
        - SendGrid, Mailgun, AWS SES, or similar email service
        
        For now, we'll log the email and store it in database for admin to see.
        """
        email_record = {
            "id": str(uuid.uuid4()),
            "to_email": to_email,
            "subject": subject,
            "body": body,
            "status": "queued",  # In production: 'sent', 'failed', 'delivered'
            "created_at": datetime.utcnow()
        }
        await db.email_queue.insert_one(email_record)
        print(f"📧 Email queued to {to_email}: {subject}")
        return email_record
    
    async def create_admin_notification(title: str, body: str, notification_type: str, related_id: str = None):
        """Create notification for all admin users"""
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id="admin",  # Special admin user ID
            user_type="admin",
            title=title,
            body=body,
            notification_type=notification_type,
            related_id=related_id
        )
        await db.notifications.insert_one(notification.dict())
        return notification
    
    async def create_applicant_notification(applicant_id: str, email: str, title: str, body: str, notification_type: str, related_id: str = None):
        """Create notification for applicant"""
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=applicant_id,
            user_type="applicant",
            title=title,
            body=body,
            notification_type=notification_type,
            related_id=related_id
        )
        await db.notifications.insert_one(notification.dict())
        return notification
    
    @router.post("/submit", response_model=ApplicationResponse)
    async def submit_application(application_data: ApplicationCreate):
        """
        Submit a new psychic advisor application.
        - Creates the application record
        - Notifies admin of new application
        - Sends confirmation email to applicant
        """
        # Check if email already has a pending application
        existing = await db.applications.find_one({
            "email": application_data.email,
            "status": {"$in": ["pending", "under_review"]}
        })
        if existing:
            raise HTTPException(
                status_code=400,
                detail="You already have a pending application. Please wait for review."
            )
        
        # Check if email was previously rejected - no reapplications allowed
        rejected = await db.applications.find_one({
            "email": application_data.email,
            "status": "rejected"
        })
        if rejected:
            raise HTTPException(
                status_code=403,
                detail="Your previous application was not approved. You cannot submit a new application."
            )
        
        # Create application
        new_application = PsychicApplication(
            id=str(uuid.uuid4()),
            full_name=application_data.full_name,
            email=application_data.email,
            phone=application_data.phone,
            country=application_data.country,
            years_experience=application_data.years_experience,
            specialties=application_data.specialties,
            love_services=application_data.love_services,
            bio=application_data.bio,
            background=application_data.background,
            tools_used=application_data.tools_used,
            tax_form_type=application_data.tax_form_type,
            tax_form_completed=application_data.tax_form_completed,
            paypal_email=application_data.paypal_email,
            video_url=application_data.video_url,
            video_duration=application_data.video_duration,
            status="pending"
        )
        
        await db.applications.insert_one(new_application.dict())
        
        # Notify admin of new application
        await create_admin_notification(
            title="New Advisor Application",
            body=f"{application_data.full_name} from {application_data.country} has submitted an application.",
            notification_type="new_application",
            related_id=new_application.id
        )
        
        # Send confirmation email to applicant
        await send_email(
            to_email=application_data.email,
            subject="Application Received - Psychic Advisor Platform",
            body=f"""
Dear {application_data.full_name},

Thank you for applying to become a Psychic Advisor on our platform!

We have received your application and our team will review it carefully. You can expect to hear back from us within 1-2 weeks.

What happens next:
1. Our team will review your application and video interview
2. We may reach out if we need additional information
3. You will receive an email notification about our decision

If you have any questions in the meantime, please contact our support team.

Best regards,
The Psychic Advisor Team
            """
        )
        
        return ApplicationResponse(**new_application.dict())
    
    @router.post("/upload-video")
    async def upload_video(file: UploadFile = File(...)):
        """
        Upload a video file from the advisor app and store it on the server.
        Returns a URL for the stored video that the admin can stream.
        """
        # Create upload directory if it doesn't exist
        upload_dir = Path(__file__).parent.parent / "uploads" / "advisor_videos"
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate unique filename
        file_ext = Path(file.filename).suffix if file.filename else ".mp4"
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = upload_dir / unique_filename
        
        # Save the file
        async with aiofiles.open(file_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
        
        # Return a URL to access the video
        video_url = f"/api/applications/video/{unique_filename}"
        return {"video_url": video_url, "filename": unique_filename}
    
    @router.get("/video/{filename}")
    async def serve_video(filename: str):
        """Serve a stored advisor video file"""
        from fastapi.responses import FileResponse
        
        upload_dir = Path(__file__).parent.parent / "uploads" / "advisor_videos"
        file_path = upload_dir / filename
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Video not found")
        
        return FileResponse(str(file_path), media_type="video/mp4")


    @router.get("/", response_model=List[ApplicationResponse])
    async def get_all_applications(
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0
    ):
        """Get all applications (for admin)"""
        query = {}
        if status:
            query["status"] = status
        
        applications = await db.applications.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        return [ApplicationResponse(**app) for app in applications]
    
    @router.get("/stats")
    async def get_application_stats():
        """Get application statistics for admin dashboard"""
        total = await db.applications.count_documents({})
        pending = await db.applications.count_documents({"status": "pending"})
        under_review = await db.applications.count_documents({"status": "under_review"})
        accepted = await db.applications.count_documents({"status": "accepted"})
        rejected = await db.applications.count_documents({"status": "rejected"})
        
        return {
            "total": total,
            "pending": pending,
            "under_review": under_review,
            "accepted": accepted,
            "rejected": rejected
        }
    
    @router.get("/{application_id}", response_model=ApplicationResponse)
    async def get_application(application_id: str):
        """Get a specific application"""
        application = await db.applications.find_one({"id": application_id})
        if not application:
            raise HTTPException(status_code=404, detail="Application not found")
        return ApplicationResponse(**application)
    
    @router.get("/check/{email}")
    async def check_application_status(email: str):
        """Check application status by email"""
        application = await db.applications.find_one({"email": email})
        if not application:
            return {"has_application": False}
        return {
            "has_application": True,
            "status": application["status"],
            "submitted_at": application["created_at"],
            "reviewed_at": application.get("reviewed_at")
        }
    
    @router.get("/email/{email}")
    async def get_application_by_email(email: str):
        """Get full application data by email (for login flow)"""
        application = await db.applications.find_one({"email": email.lower()})
        if not application:
            return None
        return {
            "id": application.get("id"),
            "email": application.get("email"),
            "full_name": application.get("full_name"),
            "status": application.get("status"),
            "rejection_reason": application.get("rejection_reason"),
            "created_at": application.get("created_at"),
            "reviewed_at": application.get("reviewed_at")
        }
    
    @router.get("/psychic/{psychic_identifier}")
    async def get_application_by_psychic(psychic_identifier: str):
        """Get application data by psychic ID or email (for profile screen)"""
        # Try to find by email first
        application = await db.applications.find_one({"email": psychic_identifier})
        
        # If not found, try to find by psychic ID (search in psychics collection first)
        if not application:
            psychic = await db.psychics.find_one({"id": psychic_identifier})
            if psychic and psychic.get("application_id"):
                application = await db.applications.find_one({"id": psychic["application_id"]})
            elif psychic and psychic.get("email"):
                application = await db.applications.find_one({"email": psychic["email"]})
        
        if not application:
            # Return minimal data if no application found
            return {
                "email": psychic_identifier if "@" in psychic_identifier else None,
                "phone": None,
                "status": "not_found"
            }
        
        return {
            "email": application.get("email"),
            "phone": application.get("phone"),
            "full_name": application.get("full_name"),
            "country": application.get("country"),
            "status": application.get("status"),
            "submitted_at": application.get("created_at"),
            "specialties": application.get("specialties", []),
            "years_experience": application.get("years_experience")
        }
    
    @router.put("/{application_id}/status")
    async def update_application_status(
        application_id: str,
        status_update: ApplicationStatusUpdate,
        admin_id: str = "admin"
    ):
        """
        Accept or reject an application.
        - Updates application status
        - Sends notification to applicant
        - Sends email to applicant
        """
        application = await db.applications.find_one({"id": application_id})
        if not application:
            raise HTTPException(status_code=404, detail="Application not found")
        
        if status_update.status not in ["accepted", "rejected", "under_review"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        
        update_data = {
            "status": status_update.status,
            "reviewed_by": admin_id,
            "reviewed_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        if status_update.status == "rejected" and status_update.rejection_reason:
            update_data["rejection_reason"] = status_update.rejection_reason
        
        await db.applications.update_one(
            {"id": application_id},
            {"$set": update_data}
        )
        
        applicant_email = application["email"]
        applicant_name = application["full_name"]
        
        if status_update.status == "accepted":
            # Send acceptance notification
            await create_applicant_notification(
                applicant_id=application_id,
                email=applicant_email,
                title="Application Accepted!",
                body="Congratulations! Your application has been approved. Welcome to the team!",
                notification_type="application_accepted",
                related_id=application_id
            )
            
            # Send acceptance email
            await send_email(
                to_email=applicant_email,
                subject="Congratulations! Your Application Has Been Accepted",
                body=f"""
Dear {applicant_name},

Great news! Your application to become a Psychic Advisor has been APPROVED!

Welcome to the team! We're excited to have you on board.

Next Steps:
1. Download the Psychic Advisor app if you haven't already
2. Log in with your email: {applicant_email}
3. Complete your profile setup
4. Set your availability and rates
5. Start accepting readings!

If you have any questions, our support team is here to help.

Welcome aboard!

Best regards,
The Psychic Advisor Team
                """
            )
            
            # Create psychic account
            new_psychic = {
                "id": str(uuid.uuid4()),
                "application_id": application_id,
                "name": applicant_name,
                "email": applicant_email,
                "phone": application["phone"],
                "country": application["country"],
                "specialties": application["specialties"],
                "bio": application["bio"],
                "years_experience": application["years_experience"],
                "tools_used": application.get("tools_used", []),
                "chat_rate": 2.99,
                "phone_rate": 3.99,
                "video_rate": 4.99,
                "online_status": "offline",
                "is_verified": True,
                "average_rating": 0,
                "total_readings": 0,
                "created_at": datetime.utcnow()
            }
            await db.psychics.insert_one(new_psychic)
            
        elif status_update.status == "rejected":
            # Send rejection notification
            await create_applicant_notification(
                applicant_id=application_id,
                email=applicant_email,
                title="Application Update",
                body="We've reviewed your application. Please check your email for details.",
                notification_type="application_rejected",
                related_id=application_id
            )
            
            # Send rejection email
            reason_text = ""
            if status_update.rejection_reason:
                reason_text = f"\n\nFeedback: {status_update.rejection_reason}\n"
            
            await send_email(
                to_email=applicant_email,
                subject="Application Update - Psychic Advisor Platform",
                body=f"""
Dear {applicant_name},

Thank you for your interest in becoming a Psychic Advisor on our platform.

After careful review of your application, we regret to inform you that we are unable to move forward with your application at this time.
{reason_text}
This decision was not easy, and we encourage you to reapply in the future if circumstances change.

If you have any questions about this decision, please don't hesitate to contact our support team.

Thank you for your understanding.

Best regards,
The Psychic Advisor Team
                """
            )
        
        return {"success": True, "status": status_update.status}
    
    @router.get("/emails/queue")
    async def get_email_queue(limit: int = 50):
        """Get queued emails (for admin to see what emails were sent)"""
        emails = await db.email_queue.find().sort("created_at", -1).limit(limit).to_list(limit)
        # Convert ObjectId to string for JSON serialization
        for email in emails:
            if '_id' in email:
                email['_id'] = str(email['_id'])
        return emails
    
    return router
