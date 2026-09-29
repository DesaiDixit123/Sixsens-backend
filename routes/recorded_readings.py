from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

class RecordedReadingOrder(BaseModel):
    psychic_id: str
    psychic_name: str
    question: str
    additional_details: Optional[str] = None
    price: float
    delivery_type: str  # standard, rush
    delivery_time: str  # 24 hours, 1 hour

class RecordedReadingResponse(BaseModel):
    order_id: str
    video_url: str
    message: Optional[str] = None

def create_recorded_readings_routes(db):
    router = APIRouter(prefix="/recorded-readings", tags=["recorded_readings"])
    
    @router.post("/order")
    async def order_recorded_reading(order: RecordedReadingOrder, user_id: str = None):
        """Client orders a recorded video reading from a psychic"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        # Check user balance
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        balance = user.get("balance", 0)
        if balance < order.price:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient credits. Need ${order.price:.2f}, have ${balance:.2f}"
            )
        
        # Create order
        order_id = str(uuid.uuid4())
        reading_order = {
            "id": order_id,
            "client_id": user_id,
            "client_name": user.get("name", "Client"),
            "client_email": user.get("email"),
            "psychic_id": order.psychic_id,
            "psychic_name": order.psychic_name,
            "question": order.question,
            "additional_details": order.additional_details,
            "price": order.price,
            "delivery_type": order.delivery_type,
            "delivery_time": order.delivery_time,
            "status": "pending",  # pending, in_progress, completed, cancelled
            "video_url": None,
            "response_message": None,
            "created_at": datetime.utcnow(),
            "due_at": datetime.utcnow(),  # Will be calculated based on delivery_time
            "completed_at": None,
        }
        
        await db.recorded_readings.insert_one(reading_order)
        
        # Deduct credits from user (60% platform, 40% psychic)
        new_balance = balance - order.price
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"balance": new_balance}}
        )
        
        # Create pending payout for psychic (40% of price)
        psychic_earnings = order.price * 0.4
        await db.pending_payouts.insert_one({
            "id": str(uuid.uuid4()),
            "psychic_id": order.psychic_id,
            "order_id": order_id,
            "amount": psychic_earnings,
            "type": "recorded_reading",
            "status": "pending",
            "created_at": datetime.utcnow()
        })
        
        # Create notification for psychic
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": order.psychic_id,
            "user_type": "psychic",
            "type": "new_reading_order",
            "title": "New Reading Order! 📹",
            "message": f"You have a new {order.delivery_type} reading request. Due in {order.delivery_time}.",
            "data": {"order_id": order_id},
            "read": False,
            "created_at": datetime.utcnow()
        })
        
        return {
            "success": True,
            "order_id": order_id,
            "message": f"Order submitted! {order.psychic_name} will respond within {order.delivery_time}.",
            "new_balance": new_balance
        }
    
    @router.get("/client/orders")
    async def get_client_orders(user_id: str = None, status: str = None):
        """Get all orders for a client"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        query = {"client_id": user_id}
        if status:
            query["status"] = status
        
        orders = await db.recorded_readings.find(query).sort("created_at", -1).to_list(50)
        
        for order in orders:
            order["_id"] = str(order["_id"])
            if "created_at" in order:
                order["created_at"] = order["created_at"].isoformat()
            if "due_at" in order:
                order["due_at"] = order["due_at"].isoformat()
            if "completed_at" in order and order["completed_at"]:
                order["completed_at"] = order["completed_at"].isoformat()
        
        return {"orders": orders}
    
    @router.get("/psychic/orders")
    async def get_psychic_orders(psychic_id: str = None, email: str = None, status: str = None):
        """Get all orders for a psychic to answer"""
        if not psychic_id and not email:
            raise HTTPException(status_code=400, detail="Psychic ID or email required")
        
        query = {}
        if psychic_id:
            query["psychic_id"] = psychic_id
        if email:
            # Find psychic by email
            psychic = await db.psychics.find_one({"email": email})
            if psychic:
                query["psychic_id"] = psychic.get("id")
        
        if status:
            query["status"] = status
        else:
            # Default to pending orders
            query["status"] = "pending"
        
        orders = await db.recorded_readings.find(query).sort("created_at", -1).to_list(50)
        
        for order in orders:
            order["_id"] = str(order["_id"])
            if "created_at" in order:
                order["created_at"] = order["created_at"].isoformat()
            if "due_at" in order:
                order["due_at"] = order["due_at"].isoformat()
        
        return {"orders": orders}
    
    @router.get("/order/{order_id}")
    async def get_order(order_id: str):
        """Get a specific order"""
        order = await db.recorded_readings.find_one({"id": order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        order["_id"] = str(order["_id"])
        if "created_at" in order:
            order["created_at"] = order["created_at"].isoformat()
        
        return order
    
    @router.put("/order/{order_id}/submit-response")
    async def submit_reading_response(order_id: str, response: RecordedReadingResponse):
        """Psychic submits their recorded video response"""
        order = await db.recorded_readings.find_one({"id": order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        if order.get("status") == "completed":
            raise HTTPException(status_code=400, detail="Order already completed")
        
        # Update order with response
        await db.recorded_readings.update_one(
            {"id": order_id},
            {"$set": {
                "video_url": response.video_url,
                "response_message": response.message,
                "status": "completed",
                "completed_at": datetime.utcnow()
            }}
        )
        
        # Mark payout as ready
        await db.pending_payouts.update_one(
            {"order_id": order_id},
            {"$set": {"status": "ready"}}
        )
        
        # Notify client
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": order["client_id"],
            "user_type": "client",
            "type": "reading_received",
            "title": "Your Reading is Ready! 🎉",
            "message": f"{order['psychic_name']} has answered your question. Watch your personalized video now!",
            "data": {"order_id": order_id},
            "read": False,
            "created_at": datetime.utcnow()
        })
        
        return {
            "success": True,
            "message": "Response submitted! The client has been notified."
        }
    
    @router.put("/order/{order_id}/cancel")
    async def cancel_order(order_id: str, user_id: str = None, reason: str = None):
        """Cancel an order and refund the client"""
        order = await db.recorded_readings.find_one({"id": order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        if order.get("status") != "pending":
            raise HTTPException(status_code=400, detail="Can only cancel pending orders")
        
        # Refund client
        await db.users.update_one(
            {"id": order["client_id"]},
            {"$inc": {"balance": order["price"]}}
        )
        
        # Update order status
        await db.recorded_readings.update_one(
            {"id": order_id},
            {"$set": {
                "status": "cancelled",
                "cancel_reason": reason,
                "cancelled_at": datetime.utcnow()
            }}
        )
        
        # Remove pending payout
        await db.pending_payouts.delete_one({"order_id": order_id})
        
        return {"success": True, "message": "Order cancelled and refunded"}
    
    return router
