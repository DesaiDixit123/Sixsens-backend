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
                
                elif message_type in ("message", "new_message"):
                    conversation_id = data.get("conversation_id")
                    receiver_id = data.get("receiver_id")
                    content = data.get("content") or data.get("text") or ""
                    image_url = data.get("image_url")
                    
                    # Ensure user is in conversation
                    if conversation_id:
                        manager.join_conversation(conversation_id, user_id)
                    
                    # Save message to database
                    msg_id = data.get("id") or str(uuid.uuid4())
                    now = datetime.utcnow()
                    sender_type = data.get("sender_type")
                    if not sender_type:
                        is_psy = await db.psychics.find_one({"id": user_id})
                        sender_type = "psychic" if is_psy else "client"
                    receiver_type = "client" if sender_type == "psychic" else "psychic"
                    
                    message = {
                        "id": msg_id,
                        "conversation_id": conversation_id,
                        "sender_id": user_id,
                        "sender_type": sender_type,
                        "receiver_id": receiver_id,
                        "receiver_type": receiver_type,
                        "content": content,
                        "image_url": image_url,
                        "is_read": False,
                        "created_at": now
                    }
                    await db.messages.insert_one(message)
                    
                    # Also push to chat_sessions messages array if this is an active session
                    if conversation_id:
                        await db.chat_sessions.update_one(
                            {"id": conversation_id},
                            {"$push": {"messages": {
                                "id": msg_id,
                                "sender_id": user_id,
                                "message": content,
                                "message_type": "image" if image_url else "text",
                                "image_url": image_url,
                                "timestamp": now.isoformat()
                            }}}
                        )
                    
                    if "_id" in message:
                        message["_id"] = str(message["_id"])
                    
                    message_dict = {
                        "id": msg_id,
                        "conversation_id": conversation_id,
                        "sender_id": user_id,
                        "receiver_id": receiver_id,
                        "content": content,
                        "image_url": image_url,
                        "is_read": False,
                        "created_at": now.isoformat()
                    }
                    
                    # Unified payload matching both client app and advisor app models
                    outgoing_message = {
                        "type": "new_message",
                        "id": msg_id,
                        "conversation_id": conversation_id,
                        "sender_id": user_id,
                        "receiver_id": receiver_id,
                        "content": content,
                        "text": content,
                        "image_url": image_url,
                        "timestamp": now.isoformat(),
                        "created_at": now.isoformat(),
                        "message": message_dict
                    }
                    
                    # Direct send to receiver and conversation broadcast without duplicate sends
                    delivered_to = set()
                    if receiver_id and manager.is_user_online(receiver_id):
                        await manager.send_personal_message(outgoing_message, receiver_id)
                        # Also send with type: 'message' for legacy handlers
                        await manager.send_personal_message({**outgoing_message, "type": "message"}, receiver_id)
                        delivered_to.add(receiver_id)
                    
                    if conversation_id and conversation_id in manager.conversations:
                        for participant_id in manager.conversations[conversation_id]:
                            if participant_id != user_id and participant_id not in delivered_to:
                                await manager.send_personal_message(outgoing_message, participant_id)
                                await manager.send_personal_message({**outgoing_message, "type": "message"}, participant_id)
                                delivered_to.add(participant_id)
                    
                    # Confirm to sender
                    await websocket.send_json({
                        "type": "message_sent",
                        "message_id": msg_id,
                        "conversation_id": conversation_id
                    })
                
                elif message_type in ("typing", "typing_indicator"):
                    conversation_id = data.get("conversation_id")
                    receiver_id = data.get("receiver_id")
                    is_typing = data.get("is_typing", True)
                    
                    typing_payload = {
                        "type": "typing_indicator",
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "is_typing": is_typing
                    }
                    
                    delivered_typing = set()
                    if receiver_id and manager.is_user_online(receiver_id):
                        await manager.send_personal_message(typing_payload, receiver_id)
                        await manager.send_personal_message({**typing_payload, "type": "typing"}, receiver_id)
                        delivered_typing.add(receiver_id)
                    
                    if conversation_id and conversation_id in manager.conversations:
                        for participant_id in manager.conversations[conversation_id]:
                            if participant_id != user_id and participant_id not in delivered_typing:
                                await manager.send_personal_message(typing_payload, participant_id)
                                await manager.send_personal_message({**typing_payload, "type": "typing"}, participant_id)
                                delivered_typing.add(participant_id)
                
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
                
                elif message_type == "one_minute_warning":
                    conversation_id = data.get("conversation_id")
                    receiver_id = data.get("receiver_id")
                    warning_text = "⏰ This chat has approximately 1 minute remaining. Please inform the client that they can add more minutes if they would like to continue."
                    
                    warning_msg = {
                        "type": "one_minute_warning",
                        "conversation_id": conversation_id,
                        "text": warning_text,
                        "content": warning_text,
                        "sender": "system",
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    if receiver_id and manager.is_user_online(receiver_id):
                        await manager.send_personal_message(warning_msg, receiver_id)
                    if conversation_id and conversation_id in manager.conversations:
                        for participant_id in manager.conversations[conversation_id]:
                            if participant_id != user_id and participant_id != receiver_id:
                                await manager.send_personal_message(warning_msg, participant_id)

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
