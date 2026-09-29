from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime
import uuid
import sys
from pathlib import Path
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.support import SupportTicket, SupportTicketCreate, SupportTicketResponse

# Model for public support submission (no auth required)
class PublicSupportSubmit(BaseModel):
    name: str
    email: str
    concern_type: str
    subject: str
    message: str

def create_support_routes(db):
    router = APIRouter(prefix="/support", tags=["support"])
    
    @router.post("/ai-assistant")
    async def ai_support_assistant(query_data: dict):
        """AI Support Assistant chatbot endpoint for customer deflection (BE-085)"""
        question = query_data.get("question", "").lower()
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")
        
        # Deflection FAQ KB
        if any(w in question for w in ["refund", "money back", "cancel"]):
            answer = "You can request a refund within 48 hours of a completed session through the Admin Support tab or by submitting a ticket."
            category = "refunds"
        elif any(w in question for w in ["free", "minutes", "gift"]):
            answer = "New users receive 4 FREE minutes on their first reading session automatically applied at checkout."
            category = "promotions"
        elif any(w in question for w in ["rate", "cost", "price", "wallet", "charge"]):
            answer = "Readings are charged per minute based on the advisor's set rate. Wallet balances can be topped up under the Wallet tab."
            category = "billing"
        else:
            answer = "Our support team is available 24/7. Please feel free to submit a support ticket below for direct assistance!"
            category = "general"
            
        return {
            "question": question,
            "answer": answer,
            "category": category,
            "deflected": category != "general"
        }
    
    @router.post("/submit")
    async def public_submit_support(data: PublicSupportSubmit):
        """
        Public endpoint for submitting support requests from landing page
        No authentication required
        """
        ticket_id = str(uuid.uuid4())
        new_ticket = {
            "id": ticket_id,
            "user_id": "guest",
            "user_type": "guest",
            "user_email": data.email,
            "user_name": data.name,
            "concern_type": data.concern_type,
            "subject": data.subject,
            "message": data.message,
            "status": "open",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        await db.support_tickets.insert_one(new_ticket)
        
        return {
            "success": True,
            "ticket_id": ticket_id,
            "message": "Support request submitted successfully"
        }
    
    @router.post("/tickets", response_model=SupportTicketResponse)
    async def create_ticket(
        ticket_data: SupportTicketCreate,
        user_id: str,
        user_type: str  # 'client' or 'psychic'
    ):
        """
        Create a support ticket / help request
        """
        new_ticket = SupportTicket(
            id=str(uuid.uuid4()),
            user_id=user_id,
            user_type=user_type,
            user_email=ticket_data.user_email,
            user_name=ticket_data.user_name,
            subject=ticket_data.subject,
            message=ticket_data.message,
            status="open"
        )
        
        await db.support_tickets.insert_one(new_ticket.dict())
        
        return SupportTicketResponse(**new_ticket.dict())
    
    @router.get("/tickets", response_model=List[SupportTicketResponse])
    async def get_all_tickets(
        status: Optional[str] = None,
        user_type: Optional[str] = None,
        limit: int = 50,
        skip: int = 0
    ):
        """
        Get all support tickets (for admin)
        """
        query = {}
        if status:
            query["status"] = status
        if user_type:
            query["user_type"] = user_type
        
        tickets = await db.support_tickets.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        return [SupportTicketResponse(**t) for t in tickets]
    
    @router.get("/tickets/user/{user_id}", response_model=List[SupportTicketResponse])
    async def get_user_tickets(user_id: str, user_type: str):
        """
        Get tickets for a specific user
        """
        tickets = await db.support_tickets.find({
            "user_id": user_id,
            "user_type": user_type
        }).sort("created_at", -1).to_list(50)
        return [SupportTicketResponse(**t) for t in tickets]
    
    @router.get("/tickets/{ticket_id}", response_model=SupportTicketResponse)
    async def get_ticket(ticket_id: str):
        """
        Get a specific ticket
        """
        ticket = await db.support_tickets.find_one({"id": ticket_id})
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return SupportTicketResponse(**ticket)
    
    @router.put("/tickets/{ticket_id}/status")
    async def update_ticket_status(ticket_id: str, status: str):
        """
        Update ticket status (for admin)
        """
        valid_statuses = ["open", "in_progress", "resolved", "closed"]
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
        
        result = await db.support_tickets.update_one(
            {"id": ticket_id},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        return {"success": True, "status": status}
    
    @router.put("/tickets/{ticket_id}/respond")
    async def respond_to_ticket(ticket_id: str, response: str):
        """
        Admin response to ticket
        """
        result = await db.support_tickets.update_one(
            {"id": ticket_id},
            {
                "$set": {
                    "admin_response": response,
                    "status": "resolved",
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        return {"success": True}
    
    @router.get("/tickets/stats/count")
    async def get_ticket_stats():
        """
        Get ticket statistics for admin dashboard
        """
        total = await db.support_tickets.count_documents({})
        open_count = await db.support_tickets.count_documents({"status": "open"})
        in_progress = await db.support_tickets.count_documents({"status": "in_progress"})
        resolved = await db.support_tickets.count_documents({"status": "resolved"})
        
        return {
            "total": total,
            "open": open_count,
            "in_progress": in_progress,
            "resolved": resolved
        }
    
    return router
