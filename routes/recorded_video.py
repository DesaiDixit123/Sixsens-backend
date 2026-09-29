from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
from bson import ObjectId

router = APIRouter(prefix="/recorded-video-orders", tags=["recorded-video-orders"])

class RecordedVideoOrderCreate(BaseModel):
    user_id: str
    psychic_id: str
    question: str
    additional_details: Optional[str] = ""
    price: float
    delivery_type: Optional[str] = "standard"  # standard (24hrs) or rush (1hr)
    delivery_time: Optional[str] = "24 hours"

class RecordedVideoOrderResponse(BaseModel):
    id: str
    user_id: str
    psychic_id: str
    question: str
    additional_details: str
    price: float
    status: str
    video_url: Optional[str] = None
    created_at: str
    responded_at: Optional[str] = None

def create_recorded_video_routes(db):
    @router.post("")
    async def create_order(order: RecordedVideoOrderCreate):
        """Create a new recorded video order"""
        # Check user has enough credits
        user = await db.users.find_one({"id": order.user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if (user.get("credits", 0) < order.price):
            raise HTTPException(status_code=400, detail="Insufficient credits")
        
        # Deduct credits
        await db.users.update_one(
            {"id": order.user_id},
            {"$inc": {"credits": -order.price}}
        )
        
        # Create order
        order_doc = {
            "user_id": order.user_id,
            "psychic_id": order.psychic_id,
            "question": order.question,
            "additional_details": order.additional_details or "",
            "price": order.price,
            "delivery_type": order.delivery_type or "standard",
            "delivery_time": order.delivery_time or "24 hours",
            "status": "pending",  # pending, recording, completed, cancelled
            "video_url": None,
            "created_at": datetime.utcnow().isoformat(),
            "responded_at": None,
        }
        
        result = await db.recorded_video_orders.insert_one(order_doc)
        order_doc["id"] = str(result.inserted_id)
        
        return {"success": True, "order_id": order_doc["id"], "message": "Order created successfully"}
    
    @router.get("/user/{user_id}")
    async def get_user_orders(user_id: str):
        """Get all recorded video orders for a user"""
        orders = await db.recorded_video_orders.find({"user_id": user_id}).sort("created_at", -1).to_list(100)
        
        for order in orders:
            order["id"] = str(order.pop("_id"))
        
        return orders
    
    @router.get("/psychic/{psychic_id}")
    async def get_psychic_orders(psychic_id: str):
        """Get all pending recorded video orders for a psychic"""
        orders = await db.recorded_video_orders.find({
            "psychic_id": psychic_id,
            "status": {"$in": ["pending", "recording"]}
        }).sort("created_at", 1).to_list(100)
        
        for order in orders:
            order["id"] = str(order.pop("_id"))
            # Get user info
            user = await db.users.find_one({"id": order["user_id"]})
            if user:
                order["user_name"] = user.get("name", "Anonymous")
        
        return orders
    
    @router.get("/psychic/{psychic_id}/all")
    async def get_psychic_all_orders(psychic_id: str):
        """Get all recorded video orders for a psychic (including completed)"""
        orders = await db.recorded_video_orders.find({
            "psychic_id": psychic_id
        }).sort("created_at", -1).to_list(100)
        
        for order in orders:
            order["id"] = str(order.pop("_id"))
            user = await db.users.find_one({"id": order["user_id"]})
            if user:
                order["user_name"] = user.get("name", "Anonymous")
        
        return orders
    
    @router.put("/{order_id}/submit-video")
    async def submit_video_response(order_id: str, video_url: str):
        """Psychic submits video response for an order"""
        try:
            order = await db.recorded_video_orders.find_one({"_id": ObjectId(order_id)})
        except:
            raise HTTPException(status_code=400, detail="Invalid order ID")
        
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        if order["status"] == "completed":
            raise HTTPException(status_code=400, detail="Order already completed")
        
        # Update order with video
        await db.recorded_video_orders.update_one(
            {"_id": ObjectId(order_id)},
            {
                "$set": {
                    "video_url": video_url,
                    "status": "completed",
                    "responded_at": datetime.utcnow().isoformat()
                }
            }
        )
        
        # Pay the psychic (40% of the price)
        psychic_earnings = order["price"] * 0.4
        await db.psychics.update_one(
            {"id": order["psychic_id"]},
            {"$inc": {"earnings": psychic_earnings}}
        )
        
        return {"success": True, "message": "Video response submitted successfully"}
    
    @router.put("/{order_id}/status")
    async def update_order_status(order_id: str, status: str):
        """Update order status (recording, cancelled)"""
        if status not in ["pending", "recording", "completed", "cancelled"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        
        try:
            result = await db.recorded_video_orders.update_one(
                {"_id": ObjectId(order_id)},
                {"$set": {"status": status}}
            )
        except:
            raise HTTPException(status_code=400, detail="Invalid order ID")
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        
        # If cancelled, refund the user
        if status == "cancelled":
            order = await db.recorded_video_orders.find_one({"_id": ObjectId(order_id)})
            if order:
                await db.users.update_one(
                    {"id": order["user_id"]},
                    {"$inc": {"credits": order["price"]}}
                )
        
        return {"success": True, "message": f"Order status updated to {status}"}
    
    return router
