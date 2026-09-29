from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from typing import Optional
from datetime import datetime
import uuid
import base64
import os
from pathlib import Path

# For now, we'll store images as base64 in MongoDB
# This can be upgraded to S3/GCS later

def create_storage_routes(db):
    router = APIRouter(prefix="/storage", tags=["storage"])
    
    @router.get("/generated/{filename}")
    async def get_generated_file(filename: str):
        """Serve generated files like hiring posters"""
        static_dir = Path(__file__).parent.parent / "static"
        file_path = static_dir / filename
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        # Determine content type
        content_type = "application/octet-stream"
        if filename.endswith(".png"):
            content_type = "image/png"
        elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif filename.endswith(".gif"):
            content_type = "image/gif"
        
        return FileResponse(file_path, media_type=content_type)
    
    @router.post("/upload")
    async def upload_file(
        file: UploadFile = File(...),
        user_id: str = Form(...),
        file_type: str = Form(default="image"),  # image, video, document
        purpose: str = Form(default="profile"),   # profile, chat, question
        authorization: Optional[str] = Form(default=None)
    ):
        """Upload a file and return its URL/ID (BE-006: Secured upload endpoint)"""
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        
        # Validate file type
        allowed_types = {
            "image": ["image/jpeg", "image/png", "image/gif", "image/webp"],
            "video": ["video/mp4", "video/quicktime", "video/webm"],
            "document": ["application/pdf"]
        }
        
        if file.content_type not in allowed_types.get(file_type, []):
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file type. Allowed: {allowed_types.get(file_type, [])}"
            )
        
        # Size limits
        max_sizes = {
            "image": 10 * 1024 * 1024,    # 10MB
            "video": 100 * 1024 * 1024,   # 100MB
            "document": 25 * 1024 * 1024  # 25MB
        }
        
        content = await file.read()
        if len(content) > max_sizes.get(file_type, 10 * 1024 * 1024):
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Max size: {max_sizes.get(file_type, 10) // (1024*1024)}MB"
            )
        
        # Generate unique filename
        ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
        file_id = str(uuid.uuid4())
        filename = f"{file_id}.{ext}"
        
        # Store file metadata and content
        file_doc = {
            "id": file_id,
            "filename": filename,
            "original_name": file.filename,
            "content_type": file.content_type,
            "file_type": file_type,
            "purpose": purpose,
            "user_id": user_id,
            "size": len(content),
            "data": base64.b64encode(content).decode("utf-8"),
            "created_at": datetime.utcnow()
        }
        
        await db.files.insert_one(file_doc)
        
        # Return URL that can be used to retrieve the file
        base_url = os.getenv("BACKEND_URL", "")
        file_url = f"{base_url}/api/storage/file/{file_id}"
        
        return {
            "success": True,
            "file_id": file_id,
            "url": file_url,
            "filename": filename,
            "size": len(content)
        }
    
    @router.get("/file/{file_id}")
    async def get_file(file_id: str):
        """Retrieve a file by ID"""
        file_doc = await db.files.find_one({"id": file_id})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Return as data URL for images
        content_type = file_doc.get("content_type", "application/octet-stream")
        data = file_doc.get("data", "")
        
        return {
            "file_id": file_id,
            "content_type": content_type,
            "data_url": f"data:{content_type};base64,{data}",
            "filename": file_doc.get("original_name"),
            "size": file_doc.get("size")
        }
    
    @router.delete("/file/{file_id}")
    async def delete_file(file_id: str, user_id: str):
        """Delete a file (owner only)"""
        file_doc = await db.files.find_one({"id": file_id})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")
        
        if file_doc.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        await db.files.delete_one({"id": file_id})
        return {"success": True, "message": "File deleted"}
    
    @router.post("/upload-base64")
    async def upload_base64(
        user_id: str,
        base64_data: str,
        content_type: str = "image/jpeg",
        purpose: str = "profile"
    ):
        """Upload a file from base64 data (for mobile camera uploads)"""
        
        # Remove data URL prefix if present
        if ";base64," in base64_data:
            base64_data = base64_data.split(";base64,")[1]
        
        try:
            content = base64.b64decode(base64_data)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 data")
        
        # Size limit: 10MB
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Max 10MB.")
        
        file_id = str(uuid.uuid4())
        ext = content_type.split("/")[-1]
        filename = f"{file_id}.{ext}"
        
        file_doc = {
            "id": file_id,
            "filename": filename,
            "original_name": filename,
            "content_type": content_type,
            "file_type": "image",
            "purpose": purpose,
            "user_id": user_id,
            "size": len(content),
            "data": base64_data,
            "created_at": datetime.utcnow()
        }
        
        await db.files.insert_one(file_doc)
        
        base_url = os.getenv("BACKEND_URL", "")
        file_url = f"{base_url}/api/storage/file/{file_id}"
        
        return {
            "success": True,
            "file_id": file_id,
            "url": file_url,
            "data_url": f"data:{content_type};base64,{base64_data}"
        }
    
    @router.get("/user-files/{user_id}")
    async def get_user_files(user_id: str, purpose: Optional[str] = None):
        """Get all files uploaded by a user"""
        query = {"user_id": user_id}
        if purpose:
            query["purpose"] = purpose
        
        files = await db.files.find(
            query,
            {"data": 0}  # Exclude actual data for listing
        ).sort("created_at", -1).to_list(100)
        
        return files
    
    return router
