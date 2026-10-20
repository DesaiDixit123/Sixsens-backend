from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response, Request
from fastapi.responses import FileResponse, Response, RedirectResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid
import base64
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class UploadBase64Request(BaseModel):
    user_id: Optional[str] = "advisor"
    base64_data: str
    content_type: Optional[str] = "image/jpeg"
    purpose: Optional[str] = "profile"

# Storage configuration
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
VIDEO_DIR = UPLOAD_DIR / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

def create_storage_routes(db):
    router = APIRouter(prefix="/storage", tags=["storage"])

    @router.post("/upload-video")
    async def upload_video(
        file: UploadFile = File(...),
        question_id: Optional[str] = Form(default=None),
        user_id: Optional[str] = Form(default=None)
    ):
        """Upload advisor reading video directly to disk for smooth streaming"""
        file_id = str(uuid.uuid4())
        ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "mp4"
        if ext.lower() not in ["mp4", "mov", "m4v", "webm", "3gp"]:
            ext = "mp4"
        
        filename = f"{file_id}.{ext}"
        target_path = VIDEO_DIR / filename

        # Write in chunks
        with open(target_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        file_size = target_path.stat().st_size
        base_url = os.getenv("BACKEND_URL", "http://192.168.29.227:8000")
        video_url = f"{base_url}/api/storage/video/{filename}"

        await db.files.insert_one({
            "id": file_id,
            "filename": filename,
            "original_name": file.filename,
            "content_type": f"video/{ext}",
            "file_type": "video",
            "path": str(target_path),
            "size": file_size,
            "question_id": question_id,
            "user_id": user_id,
            "created_at": datetime.utcnow()
        })

        return {
            "success": True,
            "file_id": file_id,
            "url": video_url,
            "filename": filename,
            "size": file_size
        }

    @router.get("/video/{filename}")
    async def get_video(filename: str):
        """Serve video directly with HTTP range request support for mobile players"""
        target_path = VIDEO_DIR / filename
        if not target_path.exists():
            file_id = filename.split(".")[0]
            matched = list(VIDEO_DIR.glob(f"{file_id}.*"))
            if matched:
                target_path = matched[0]
            else:
                raise HTTPException(status_code=404, detail="Video file not found")

        ext = target_path.suffix.lstrip(".").lower()
        content_type = f"video/{ext}" if ext in ["mp4", "webm", "quicktime"] else "video/mp4"

        return FileResponse(
            target_path,
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=86400"
            }
        )
    
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
    async def get_file(file_id: str, format: Optional[str] = None):
        """Retrieve a file by ID - returns raw image bytes or redirects to CDN"""
        file_doc = await db.files.find_one({"id": file_id})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")
        
        # If it has a remote Cloudinary URL, redirect to it
        if file_doc.get("url") and file_doc["url"].startswith("http"):
            return RedirectResponse(file_doc["url"])

        content_type = file_doc.get("content_type", "image/jpeg")
        data = file_doc.get("data", "")
        
        if format == "json":
            return {
                "file_id": file_id,
                "content_type": content_type,
                "data_url": f"data:{content_type};base64,{data}",
                "filename": file_doc.get("original_name"),
                "size": file_doc.get("size")
            }

        try:
            image_bytes = base64.b64decode(data)
            return Response(
                content=image_bytes,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"}
            )
        except Exception:
            raise HTTPException(status_code=500, detail="Error decoding file")
    
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
        request: Request,
        payload: Optional[UploadBase64Request] = None
    ):
        """Upload a file from base64 data (supports mobile camera uploads, Cloudinary, and local storage)"""
        uid = "advisor"
        b64 = ""
        ctype = "image/jpeg"
        purp = "profile"

        if payload:
            uid = payload.user_id or "advisor"
            b64 = payload.base64_data or ""
            ctype = payload.content_type or "image/jpeg"
            purp = payload.purpose or "profile"
        else:
            try:
                body = await request.json()
                uid = body.get("user_id", "advisor")
                b64 = body.get("base64_data", "")
                ctype = body.get("content_type", "image/jpeg")
                purp = body.get("purpose", "profile")
            except Exception:
                pass

        if not b64:
            raise HTTPException(status_code=400, detail="Missing base64_data")
        
        # Remove data URL prefix if present
        if ";base64," in b64:
            b64 = b64.split(";base64,")[1]
        
        try:
            content = base64.b64decode(b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 data")
        
        # Size limit: 10MB
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Max 10MB.")
        
        file_id = str(uuid.uuid4())

        # Attempt upload to Cloudinary for permanent CDN URL
        try:
            import cloudinary.uploader
            res = cloudinary.uploader.upload(
                f"data:{ctype};base64,{b64}",
                folder="profiles",
                resource_type="image"
            )
            cloud_url = res.get("secure_url") or res.get("url")
            if cloud_url:
                await db.files.insert_one({
                    "id": file_id,
                    "url": cloud_url,
                    "content_type": ctype,
                    "file_type": "image",
                    "purpose": purp,
                    "user_id": uid,
                    "size": len(content),
                    "created_at": datetime.utcnow()
                })
                return {
                    "success": True,
                    "file_id": file_id,
                    "url": cloud_url,
                    "data_url": cloud_url
                }
        except Exception as e:
            logger.warning(f"Cloudinary upload failed, falling back to db: {e}")
        
        ext = ctype.split("/")[-1]
        filename = f"{file_id}.{ext}"
        
        file_doc = {
            "id": file_id,
            "filename": filename,
            "original_name": filename,
            "content_type": ctype,
            "file_type": "image",
            "purpose": purp,
            "user_id": uid,
            "size": len(content),
            "data": b64,
            "created_at": datetime.utcnow()
        }
        
        await db.files.insert_one(file_doc)
        
        base_url = os.getenv("BACKEND_URL", "http://192.168.29.73:8000")
        file_url = f"{base_url}/api/storage/file/{file_id}"
        
        return {
            "success": True,
            "file_id": file_id,
            "url": file_url,
            "data_url": f"data:{ctype};base64,{b64}"
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
