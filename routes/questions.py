from fastapi import APIRouter, HTTPException
from typing import List
from datetime import datetime, timedelta
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.question import (
    Question, QuestionCreate, QuestionResponse, 
    ClarificationMessage, ClarificationMessageCreate, BirthDetails
)

def create_questions_routes(db):
    router = APIRouter(prefix="/questions", tags=["questions"])
    
    @router.post("/", response_model=QuestionResponse)
    async def create_question(question: QuestionCreate, user_id: str = None):
        """
        Create a new question/reading request
        """
        # For now, use a mock user_id if not provided
        if not user_id:
            user_id = "mock-user-123"
        
        # Calculate price based on delivery type
        if question.question_type == "recorded_video":
            price = 20.0 if question.delivery_type == "emergency" else 12.0
            # Set deadline based on delivery type
            if question.delivery_type == "emergency":
                deadline = datetime.utcnow() + timedelta(hours=1)
            else:
                deadline = datetime.utcnow() + timedelta(hours=24)
        else:
            price = 0.0  # Live sessions charged per minute
            deadline = None
        
        # Build third party details if provided
        third_party_details = None
        if question.is_third_party and question.third_party_name:
            third_party_details = BirthDetails(
                name=question.third_party_name,
                birth_date=question.third_party_birth_date or "",
                birth_time=question.third_party_birth_time,
                birth_location=question.third_party_birth_location
            )
        
        # Look up client name if possible
        client_name = "Client"
        if user_id and user_id != "guest":
            user_doc = await db.users.find_one({"$or": [{"id": user_id}, {"email": user_id}]})
            if user_doc:
                client_name = user_doc.get("name") or user_doc.get("full_name") or user_doc.get("email", "Client")

        # Also get psychic email if possible
        psychic_email = None
        if question.psychic_id:
            psy_doc = await db.psychics.find_one({"$or": [{"id": question.psychic_id}, {"email": question.psychic_id}]})
            if psy_doc:
                psychic_email = psy_doc.get("email")

        # Create the question object
        new_question = Question(
            id=str(uuid.uuid4()),
            client_id=user_id,
            psychic_id=question.psychic_id,
            question_text=question.question_text,
            question_type=question.question_type,
            delivery_type=question.delivery_type,
            client_video_url=question.client_video_url,
            is_third_party=question.is_third_party,
            third_party_details=third_party_details,
            price=price,
            deadline=deadline,
            status="pending"
        )
        
        # Save to database with enriched client & psychic info
        q_data = new_question.dict()
        q_data["client_name"] = client_name
        q_data["created_at"] = datetime.utcnow()
        q_data["client_video_url"] = question.client_video_url
        q_data["has_video"] = bool(question.client_video_url)
        q_data["video_url"] = question.client_video_url
        if psychic_email:
            q_data["psychic_email"] = psychic_email
        if question.psychic_id in ("psy-001", "64ea86f6-968a-4dfc-8c98-1a322fb020c0"):
            q_data["psychic_email"] = "testpsychic@sixthsense.com"

        await db.questions.insert_one(q_data)
        
        # Trigger real-time notifications, push, and WebSocket for advisor
        try:
            from routes.reading_service import notify_advisor_new_reading
            await notify_advisor_new_reading(db, q_data)
        except Exception as notif_err:
            import logging
            logging.getLogger(__name__).warning(f"Failed to notify advisor of new reading: {notif_err}")

        return QuestionResponse(**new_question.dict())
    
    @router.get("/client/{client_id}", response_model=List[QuestionResponse])
    async def get_client_questions(client_id: str):
        """
        Get all questions for a client
        """
        questions = await db.questions.find({"client_id": client_id}).to_list(100)
        return [QuestionResponse(**q) for q in questions]
    
    @router.get("/psychic/{psychic_id}", response_model=List[QuestionResponse])
    async def get_psychic_questions(psychic_id: str):
        """
        Get all questions for a psychic
        """
        questions = await db.questions.find({"psychic_id": psychic_id}).to_list(100)
        return [QuestionResponse(**q) for q in questions]
    
    @router.get("/{question_id}", response_model=QuestionResponse)
    async def get_question(question_id: str):
        """
        Get a specific question by ID
        """
        question = await db.questions.find_one({"id": question_id})
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")
        return QuestionResponse(**question)

    @router.post("/{question_id}/answer")
    async def answer_question(question_id: str, video_url: str = None, body: dict = None):
        """Submit answer for a question, credit advisor earnings, and notify both parties"""
        target_video_url = video_url
        if not target_video_url and body and isinstance(body, dict):
            target_video_url = body.get("video_url")

        from routes.reading_service import process_reading_completion
        updated_q = await process_reading_completion(db, question_id, target_video_url)
        if not updated_q:
            raise HTTPException(status_code=404, detail="Question not found")
        return {"success": True, "video_response_url": target_video_url}
    
    @router.post("/{question_id}/clarification")
    async def send_clarification_message(
        question_id: str, 
        message_data: ClarificationMessageCreate,
        user_id: str = None,
        user_type: str = "client"  # 'client' or 'psychic'
    ):
        """
        Send a clarification message (max 5 per side)
        """
        # Get the question
        question = await db.questions.find_one({"id": question_id})
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")
        
        # Check message limits
        if user_type == "client":
            if question.get("client_messages_count", 0) >= 5:
                raise HTTPException(
                    status_code=400, 
                    detail="You have reached the maximum of 5 clarification messages"
                )
        else:
            if question.get("psychic_messages_count", 0) >= 5:
                raise HTTPException(
                    status_code=400, 
                    detail="You have reached the maximum of 5 clarification messages"
                )
        
        # Create the message
        new_message = ClarificationMessage(
            id=str(uuid.uuid4()),
            sender_type=user_type,
            sender_id=user_id or "mock-user",
            message=message_data.message
        )
        
        # Update the question with the new message
        update_field = "client_messages_count" if user_type == "client" else "psychic_messages_count"
        
        await db.questions.update_one(
            {"id": question_id},
            {
                "$push": {"clarification_messages": new_message.dict()},
                "$inc": {update_field: 1}
            }
        )
        
        return {
            "success": True,
            "message": new_message.dict(),
            "remaining_messages": 5 - (question.get(update_field, 0) + 1)
        }
    
    @router.patch("/{question_id}/status")
    async def update_question_status(question_id: str, status: str):
        """
        Update question status (for psychic to accept/complete)
        """
        valid_statuses = ["pending", "accepted", "in_progress", "completed", "cancelled", "refunded"]
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
        
        update_data = {"status": status}
        if status == "completed":
            update_data["completed_at"] = datetime.utcnow()
        
        result = await db.questions.update_one(
            {"id": question_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Question not found")
        
        return {"success": True, "status": status}
    
    return router
