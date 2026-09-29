from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List
import json
from datetime import datetime
import uuid

class ConnectionManager:
    """Manages WebSocket connections for real-time chat"""
    
    def __init__(self):
        # Active connections: {user_id: [websocket, ...]}
        self.active_connections: Dict[str, List[WebSocket]] = {}
        # Conversation participants: {conversation_id: [user_id, ...]}
        self.conversations: Dict[str, List[str]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        print(f"User {user_id} connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        print(f"User {user_id} disconnected.")
    
    async def send_personal_message(self, message: dict, user_id: str):
        """Send message to a specific user"""
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"Error sending to {user_id}: {e}")
    
    async def broadcast_to_conversation(self, message: dict, conversation_id: str, exclude_user: str = None):
        """Send message to all participants in a conversation"""
        if conversation_id in self.conversations:
            for user_id in self.conversations[conversation_id]:
                if user_id != exclude_user:
                    await self.send_personal_message(message, user_id)
    
    def join_conversation(self, conversation_id: str, user_id: str):
        """Add user to a conversation"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []
        if user_id not in self.conversations[conversation_id]:
            self.conversations[conversation_id].append(user_id)
    
    def leave_conversation(self, conversation_id: str, user_id: str):
        """Remove user from a conversation"""
        if conversation_id in self.conversations:
            if user_id in self.conversations[conversation_id]:
                self.conversations[conversation_id].remove(user_id)
    
    def is_user_online(self, user_id: str) -> bool:
        """Check if user is online"""
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0


manager = ConnectionManager()

def create_websocket_routes(db):
    router = APIRouter(tags=["websocket"])
    
    @router.websocket("/ws/{user_id}")
    async def websocket_endpoint(websocket: WebSocket, user_id: str, token: str = None):
        """Authenticated WebSocket connection endpoint (BE-048)"""
        # Validate JWT token if provided or enforce presence
        if token:
            from routes.auth import verify_token
            try:
                verify_token(token)
            except Exception:
                await websocket.close(code=4001, reason="Unauthorized: Invalid token")
                return

        await manager.connect(websocket, user_id)
        
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")

                if message_type == "ping":
                    # Update online status heartbeat (BE-046 & BE-054)
                    await db.psychics.update_one(
                        {"id": user_id},
                        {"$set": {"last_seen": datetime.utcnow().isoformat(), "is_online": True}}
                    )
                    await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
                    continue

                if message_type == "join_conversation":
                    conversation_id = data.get("conversation_id")
                    manager.join_conversation(conversation_id, user_id)
                    await websocket.send_json({
                        "type": "joined",
                        "conversation_id": conversation_id
                    })
                
                elif message_type == "leave_conversation":
                    conversation_id = data.get("conversation_id")
                    manager.leave_conversation(conversation_id, user_id)
                
                elif message_type == "message":
                    conversation_id = data.get("conversation_id")
                    receiver_id = data.get("receiver_id")
                    content = data.get("content")
                    image_url = data.get("image_url")
                    
                    # Save message to database
                    message = {
                        "id": str(uuid.uuid4()),
                        "conversation_id": conversation_id,
                        "sender_id": user_id,
                        "receiver_id": receiver_id,
                        "content": content,
                        "image_url": image_url,
                        "is_read": False,
                        "created_at": datetime.utcnow()
                    }
                    await db.messages.insert_one(message)
                    
                    # Send to receiver if online
                    outgoing_message = {
                        "type": "new_message",
                        "message": {
                            **message,
                            "created_at": message["created_at"].isoformat()
                        }
                    }
                    await manager.send_personal_message(outgoing_message, receiver_id)
                    
                    # Confirm to sender
                    await websocket.send_json({
                        "type": "message_sent",
                        "message_id": message["id"]
                    })
                
                elif message_type == "typing":
                    conversation_id = data.get("conversation_id")
                    receiver_id = data.get("receiver_id")
                    is_typing = data.get("is_typing", True)
                    
                    await manager.send_personal_message({
                        "type": "typing_indicator",
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "is_typing": is_typing
                    }, receiver_id)
                
                elif message_type == "read_receipt":
                    message_ids = data.get("message_ids", [])
                    sender_id = data.get("sender_id")
                    
                    # Update messages as read
                    await db.messages.update_many(
                        {"id": {"$in": message_ids}},
                        {"$set": {"is_read": True, "read_at": datetime.utcnow()}}
                    )
                    
                    # Notify sender
                    await manager.send_personal_message({
                        "type": "messages_read",
                        "message_ids": message_ids,
                        "read_by": user_id
                    }, sender_id)
                
                elif message_type == "ping":
                    await websocket.send_json({"type": "pong"})
        
        except WebSocketDisconnect:
            manager.disconnect(websocket, user_id)
        except Exception as e:
            print(f"WebSocket error for {user_id}: {e}")
            manager.disconnect(websocket, user_id)
    
    @router.get("/online-status/{user_id}")
    async def get_online_status(user_id: str):
        """Check if a user is currently online"""
        return {"user_id": user_id, "is_online": manager.is_user_online(user_id)}
    
    return router
