from fastapi import APIRouter, HTTPException
from typing import List
from datetime import datetime
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.favorite import Favorite, FavoriteCreate

def create_favorites_routes(db):
    router = APIRouter(prefix="/favorites", tags=["favorites"])
    
    @router.post("/")
    async def add_favorite(favorite: FavoriteCreate, user_id: str = None):
        """Add a psychic to favorites"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        # Check if already favorited
        existing = await db.favorites.find_one({
            "user_id": user_id,
            "psychic_id": favorite.psychic_id
        })
        if existing:
            raise HTTPException(status_code=400, detail="Already in favorites")
        
        new_favorite = Favorite(
            user_id=user_id,
            psychic_id=favorite.psychic_id
        )
        await db.favorites.insert_one(new_favorite.dict())
        return {"success": True, "favorite": new_favorite.dict()}
    
    @router.delete("/{psychic_id}")
    async def remove_favorite(psychic_id: str, user_id: str = None):
        """Remove a psychic from favorites"""
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required")
        
        result = await db.favorites.delete_one({
            "user_id": user_id,
            "psychic_id": psychic_id
        })
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Favorite not found")
        return {"success": True}
    
    @router.get("/user/{user_id}")
    async def get_user_favorites(user_id: str):
        """Get all favorites for a user"""
        favorites = await db.favorites.find({"user_id": user_id}).to_list(100)
        # Convert MongoDB documents to JSON-serializable format
        serialized_favorites = []
        for fav in favorites:
            # Remove MongoDB ObjectId and convert to dict
            if "_id" in fav:
                del fav["_id"]
            serialized_favorites.append(fav)
        return serialized_favorites
    
    @router.get("/check/{psychic_id}")
    async def check_favorite(psychic_id: str, user_id: str = None):
        """Check if a psychic is favorited"""
        if not user_id:
            return {"is_favorite": False}
        
        existing = await db.favorites.find_one({
            "user_id": user_id,
            "psychic_id": psychic_id
        })
        return {"is_favorite": existing is not None}
    
    return router
