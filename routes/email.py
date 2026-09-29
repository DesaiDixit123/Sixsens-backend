from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
import uuid
import os
import httpx

# Resend integration
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "onboarding@resend.dev")
FROM_NAME = os.getenv("FROM_NAME", "Sixth Sense Psychics")

RESEND_ENABLED = bool(RESEND_API_KEY)

class EmailRequest(BaseModel):
    to_email: EmailStr
    to_name: Optional[str] = None
    subject: str
    html_content: str
    text_content: Optional[str] = None

class WelcomeEmailRequest(BaseModel):
    to_email: EmailStr
    user_name: str

class SessionReceiptRequest(BaseModel):
    to_email: EmailStr
    user_name: str
    psychic_name: str
    session_type: str
    duration_minutes: int
    amount_charged: float
    session_date: str

class PasswordResetRequest(BaseModel):
    to_email: EmailStr
    user_name: str
    reset_token: str
    reset_url: str

def create_email_routes(db):
    router = APIRouter(prefix="/email", tags=["email"])
    
    async def send_email(to_email: str, to_name: str, subject: str, html_content: str, text_content: str = None):
        """Send email via Resend or log if not configured"""
        
        # Log email for debugging
        email_log = {
            "id": str(uuid.uuid4()),
            "to_email": to_email,
            "to_name": to_name,
            "subject": subject,
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        
        if RESEND_ENABLED:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {RESEND_API_KEY}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                            "to": [to_email],
                            "subject": subject,
                            "html": html_content
                        }
                    )
                    
                    if response.status_code == 200:
                        email_log["status"] = "sent"
                        email_log["resend_response"] = response.json()
                        await db.email_logs.insert_one(email_log)
                        return {"success": True, "status": "sent"}
                    else:
                        email_log["status"] = "failed"
                        email_log["error"] = response.text
                        await db.email_logs.insert_one(email_log)
                        print(f"Resend error: {response.text}")
                        return {"success": False, "error": response.text}
            except Exception as e:
                email_log["status"] = "failed"
                email_log["error"] = str(e)
                await db.email_logs.insert_one(email_log)
                print(f"Resend error: {e}")
                return {"success": False, "error": str(e)}
        else:
            # Mock email - just log it
            email_log["status"] = "mocked"
            email_log["html_content"] = html_content[:500]  # Store preview
            await db.email_logs.insert_one(email_log)
            print(f"[MOCK EMAIL] To: {to_email}, Subject: {subject}")
            return {"success": True, "status": "mocked", "message": "Resend not configured"}
    
    @router.get("/config")
    async def get_email_config():
        """Check email configuration status"""
        return {
            "resend_enabled": RESEND_ENABLED,
            "from_email": FROM_EMAIL
        }
    
    @router.post("/send")
    async def send_custom_email(request: EmailRequest, background_tasks: BackgroundTasks):
        """Send a custom email"""
        result = await send_email(
            request.to_email,
            request.to_name or request.to_email,
            request.subject,
            request.html_content,
            request.text_content
        )
        return result
    
    @router.post("/welcome")
    async def send_welcome_email(request: WelcomeEmailRequest):
        """Send welcome email to new user"""
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
                <h1>Welcome, {request.user_name}!</h1>
                <p>Thank you for joining Sixth Sense Psychics. We're excited to have you as part of our community.</p>
                <p><strong>🎁 Your Welcome Gift:</strong> Enjoy your <strong>first 4 minutes FREE</strong> on your first reading with any of our gifted advisors!</p>
                <p>Our verified psychic advisors are ready to provide you with guidance on:</p>
                <ul>
                    <li>💕 Love & Relationships</li>
                    <li>💼 Career & Finance</li>
                    <li>🔮 Life Path & Destiny</li>
                    <li>🌙 Spiritual Growth</li>
                </ul>
                <center><a href="https://sixthsensepsychics.com" class="cta">Start Your Journey</a></center>
                <div class="footer">
                    <p>© 2024 Sixth Sense Psychics. All rights reserved.</p>
                    <p>Questions? Contact us at support@sixthsensepsychics.com</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return await send_email(
            request.to_email,
            request.user_name,
            "Welcome to Sixth Sense Psychics! ✨",
            html_content
        )
    
    @router.post("/session-receipt")
    async def send_session_receipt(request: SessionReceiptRequest):
        """Send session receipt email"""
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; }}
                .header {{ text-align: center; margin-bottom: 30px; }}
                .logo {{ font-size: 24px; color: #6B4EAA; font-weight: bold; }}
                .receipt-box {{ background: #f9f9f9; border-radius: 10px; padding: 20px; margin: 20px 0; }}
                .row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #eee; }}
                .total {{ font-size: 18px; font-weight: bold; color: #6B4EAA; }}
                .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 30px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">✨ Sixth Sense Psychics</div>
                    <h2>Session Receipt</h2>
                </div>
                <p>Hi {request.user_name},</p>
                <p>Thank you for your reading session. Here are your session details:</p>
                <div class="receipt-box">
                    <div class="row"><span>Advisor:</span><span>{request.psychic_name}</span></div>
                    <div class="row"><span>Session Type:</span><span>{request.session_type}</span></div>
                    <div class="row"><span>Duration:</span><span>{request.duration_minutes} minutes</span></div>
                    <div class="row"><span>Date:</span><span>{request.session_date}</span></div>
                    <div class="row total"><span>Amount Charged:</span><span>${request.amount_charged:.2f}</span></div>
                </div>
                <p>We hope you found your reading insightful. Feel free to leave a review for your advisor!</p>
                <div class="footer">
                    <p>© 2024 Sixth Sense Psychics. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return await send_email(
            request.to_email,
            request.user_name,
            f"Your Reading with {request.psychic_name} - Receipt",
            html_content
        )
    
    @router.post("/password-reset")
    async def send_password_reset(request: PasswordResetRequest):
        """Send password reset email"""
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
                <p>Hi {request.user_name},</p>
                <p>We received a request to reset your password. Click the button below to create a new password:</p>
                <center><a href="{request.reset_url}" class="cta">Reset Password</a></center>
                <div class="warning">
                    ⚠️ This link will expire in 1 hour. If you didn't request this, please ignore this email.
                </div>
                <p>If the button doesn't work, copy and paste this link into your browser:</p>
                <p style="word-break: break-all; color: #6B4EAA;">{request.reset_url}</p>
                <div class="footer">
                    <p>© 2024 Sixth Sense Psychics. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return await send_email(
            request.to_email,
            request.user_name,
            "Reset Your Password - Sixth Sense Psychics",
            html_content
        )
    
    @router.get("/logs")
    async def get_email_logs(limit: int = 50):
        """Get recent email logs (admin only)"""
        logs = await db.email_logs.find().sort("created_at", -1).limit(limit).to_list(limit)
        return logs
    
    return router
