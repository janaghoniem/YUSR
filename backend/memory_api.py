"""
Memory Management API Endpoints - FIXED VERSION
Focused on Long-Term Memory (Preferences) Management
"""

from fastapi import APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import asyncio
import logging
from datetime import datetime
from pymongo import MongoClient
import os
from dotenv import load_dotenv

from agents.coordinator_agent.memory.mem0_manager import get_preference_manager

load_dotenv()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

MONGODB_URI = os.getenv("MONGODB_URI")

if not MONGODB_URI:
    logger.error("❌ MONGODB_URI not found in environment variables!")
    raise ValueError("MONGODB_URI is required")

# @router.get("/preferences")
# async def get_user_preferences(user_id: str, limit: int = 100):
#     """Get all stored preferences for a user - WITH DETAILED ERROR LOGGING"""
#     try:
#         logger.info(f"📥 Fetching preferences for user: {user_id}")
        
#         # Step 1: Try to import the manager
#         try:
#             from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
#             logger.info("✅ Successfully imported mem0_manager")
#         except ImportError as ie:
#             logger.error(f"❌ IMPORT ERROR: {ie}")
#             logger.error("❌ Could not import mem0_manager - check file location")
#             raise HTTPException(
#                 status_code=500, 
#                 detail=f"Import error: {str(ie)}. Check mem0_manager.py location."
#             )
        
#         # Step 2: Try to get preference manager
#         try:
#             pref_mgr = get_preference_manager(user_id)
#             logger.info(f"✅ Got preference manager for {user_id}")
#         except Exception as pm_error:
#             logger.error(f"❌ Failed to get preference manager: {pm_error}")
#             raise HTTPException(
#                 status_code=500,
#                 detail=f"Preference manager error: {str(pm_error)}. Check MongoDB connection."
#             )
        
#         # Step 3: Try to get all preferences
#         try:
#             all_prefs = pref_mgr.get_all_preferences()
#             logger.info(f"✅ Retrieved raw preferences")
#             logger.info(f"✅ Type: {type(all_prefs)}")
#             if isinstance(all_prefs, dict):
#                 logger.info(f"✅ Dict keys: {all_prefs.keys()}")
#         except Exception as prefs_error:
#             logger.error(f"❌ Failed to get preferences: {prefs_error}")
#             raise HTTPException(
#                 status_code=500,
#                 detail=f"Database error: {str(prefs_error)}"
#             )
        
#         # Step 4: Handle the response format (Mem0 can return dict or list)
#         if isinstance(all_prefs, dict):
#             # Mem0 might return {"results": [...]} or {"memories": [...]}
#             prefs_list = all_prefs.get("results", all_prefs.get("memories", all_prefs.get("data", [])))
#             logger.info(f"✅ Extracted list from dict, length: {len(prefs_list) if isinstance(prefs_list, list) else 'not a list'}")
#         elif isinstance(all_prefs, list):
#             prefs_list = all_prefs
#             logger.info(f"✅ Already a list, length: {len(prefs_list)}")
#         else:
#             logger.warning(f"⚠️ Unexpected type for all_prefs: {type(all_prefs)}, treating as empty")
#             prefs_list = []
        
#         # Ensure it's actually a list
#         if not isinstance(prefs_list, list):
#             logger.error(f"❌ prefs_list is not a list after extraction: {type(prefs_list)}")
#             prefs_list = []
        
#         logger.info(f"✅ Processing {len(prefs_list)} preferences")
        
#         # ✅ FIX: Step 5: Format for frontend with None-safe handling
#         formatted = []
#         for idx, pref in enumerate(prefs_list[:limit]):
#                     try:
#                         # ✅ FIX: Skip None values
#                         if pref is None:
#                             logger.warning(f"⚠️ Skipping None preference at index {idx}")
#                             continue
                            
#                         if isinstance(pref, dict):
#                             formatted.append({
#                                 "id": pref.get("id", f"unknown_{idx}"),
#                                 "text": pref.get("memory", pref.get("text", "Unknown")),
#                                 "category": pref.get("metadata", {}).get("category", "general") if isinstance(pref.get("metadata"), dict) else "general",
#                                 "timestamp": pref.get("metadata", {}).get("timestamp", None) if isinstance(pref.get("metadata"), dict) else None,
#                                 "confidence": pref.get("metadata", {}).get("confidence", "unknown") if isinstance(pref.get("metadata"), dict) else "unknown"
#                             })
#                         else:
#                             logger.warning(f"⚠️ Preference {idx} is not a dict: {type(pref)}")
#                     except Exception as format_error:
#                         logger.error(f"❌ Error formatting preference {idx}: {format_error}")
#                         logger.error(f"❌ Preference data: {pref}")
#                         continue

        
#         logger.info(f"✅ Returning {len(formatted)} formatted preferences")
#         return {
#             "preferences": formatted, 
#             "total": len(formatted),
#             "status": "success"
#         }
        
#     except HTTPException:
#         # Re-raise HTTP exceptions as-is
#         raise
#     except Exception as e:
#         # Catch-all for unexpected errors
#         logger.error(f"❌ UNEXPECTED ERROR in get_user_preferences: {e}")
#         logger.error(f"❌ Error type: {type(e).__name__}")
#         import traceback
#         logger.error(f"❌ Traceback:\n{traceback.format_exc()}")
#         raise HTTPException(
#             status_code=500, 
#             detail=f"Unexpected error: {str(e)}"
#         )
@router.get("/preferences")
async def get_preferences(user_id: str = "test_user", limit: int = 100):
    """Get user preferences with proper formatting"""
    try:
        logger.info(f"📥 Fetching preferences for user: {user_id}")
        
        # Import and get manager
        logger.info(f"✅ Successfully imported mem0_manager")
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        
        pref_mgr = await asyncio.to_thread(get_preference_manager, user_id)
        logger.info(f"✅ Got preference manager for {user_id}")
        
        all_prefs = await asyncio.to_thread(pref_mgr.get_all_preferences)
        logger.info(f"✅ Retrieved {len(all_prefs)} preferences")
        
        # Format for frontend
        formatted_prefs = []
        for pref in all_prefs[:limit]:
            formatted_prefs.append({
                "id": pref.get("id", ""),
                "text": pref.get("memory", ""),
                "category": pref.get("metadata", {}).get("category", "general"),
                "timestamp": pref.get("metadata", {}).get("timestamp")
            })
        
        logger.info(f"✅ Returning {len(formatted_prefs)} formatted preferences")
        
        return {
            "status": "success",
            "preferences": formatted_prefs,
            "total": len(formatted_prefs)
        }
        
    except Exception as e:
        logger.error(f"❌ Get preferences failed: {e}", exc_info=True)
        return {
            "status": "error",
            "preferences": [],
            "error": str(e)
        }
     

# async def clear_user_preferences(user_id: str):
#     """Clear ALL long-term memory (preferences) for a user"""
#     try:
#         logger.warning(f"🗑️ CLEARING ALL PREFERENCES for user {user_id}")
        
#         client = MongoClient(MONGODB_URI)
#         db = client["yusr_db"]
        
#         # ✅ FIX: Correct field name for Mem0 + use Mem0 API for proper deletion
#         try:
#             from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
#             pref_mgr = get_preference_manager(user_id)
            
#             # Get all preferences first
#             all_prefs = pref_mgr.get_all_preferences()
            
#             # Extract preference list
#             if isinstance(all_prefs, dict):
#                 prefs_list = all_prefs.get("results", all_prefs.get("memories", []))
#             else:
#                 prefs_list = all_prefs if isinstance(all_prefs, list) else []
            
#             # Delete each preference by ID using Mem0 API
#             delete_count = 0
#             for pref in prefs_list:
#                 if pref is None:
#                     continue
                    
#                 if isinstance(pref, dict):
#                     mem_id = pref.get("id") or pref.get("memory_id")
#                     if mem_id:
#                         try:
#                             pref_mgr.delete_preference(mem_id)
#                             delete_count += 1
#                         except Exception as del_error:
#                             logger.warning(f"⚠️ Failed to delete {mem_id}: {del_error}")
            
#             logger.warning(f"✅ Deleted {delete_count} preferences via Mem0 API")
            
#             # Also try MongoDB direct delete as fallback
#             pref_result = db["mem0_preferences"].delete_many({
#                 "$or": [
#                     {"user_id": user_id},
#                     {"metadata.user_id": user_id}
#                 ]
#             })
            
#             logger.warning(f"✅ Deleted {pref_result.deleted_count} documents from MongoDB")
            
#             return {
#                 "status": "success",
#                 "preferences_deleted": delete_count,
#                 "mongodb_deleted": pref_result.deleted_count,
#                 "message": f"Deleted {delete_count} preferences"
#             }
            
#         except Exception as mem0_error:
#             logger.error(f"❌ Mem0 API delete failed: {mem0_error}")
            
#             # Fallback to direct MongoDB delete
#             pref_result = db["mem0_preferences"].delete_many({
#                 "$or": [
#                     {"user_id": user_id},
#                     {"metadata.user_id": user_id}
#                 ]
#             })
            
#             return {
#                 "status": "partial_success",
#                 "preferences_deleted": pref_result.deleted_count,
#                 "message": f"Deleted {pref_result.deleted_count} via MongoDB (Mem0 API failed)"
#             }
        
#     except Exception as e:
#         logger.error(f"❌ Clear preferences failed: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear-preferences")
async def clear_user_preferences(user_id: str):
    """
    Clear learned behavioral preferences for a user.

    KEEPS (permanent identity — set at onboarding, must survive reset):
      personal_info, language_preference, ui_preference

    DELETES (learned over time — what the user actually wants to reset):
      app_usage, contact, workflow, learned_pattern,
      conversation_history, general, and any uncategorised docs
    """
    # Categories that belong to permanent user identity — never touch these
    PROTECTED_CATEGORIES = {"personal_info", "language_preference", "ui_preference"}

    try:
        logger.warning(f"🗑️ Clearing learned preferences for user {user_id} (keeping identity data)")

        mem0_deleted = 0
        raw_deleted = 0
        errors = []

        # ── Path 1: Mem0-managed documents ───────────────────────────────────
        # These were written via memory.add() and have a proper Mem0 ID.
        # get_all_preferences() only returns these — zero-token docs are invisible to it.
        try:
            from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
            pref_mgr = await asyncio.to_thread(get_preference_manager, user_id)
            all_prefs = await asyncio.to_thread(pref_mgr.get_all_preferences) # returns List[Dict]

            for pref in all_prefs:
                if not isinstance(pref, dict):
                    continue
                category = pref.get("metadata", {}).get("category", "general")
                if category in PROTECTED_CATEGORIES:
                    logger.info(f"🔒 Keeping protected memory: [{category}] {pref.get('memory', '')[:50]}")
                    continue
                mem_id = pref.get("id") or pref.get("memory_id")
                if mem_id:
                    try:
                        await asyncio.to_thread(pref_mgr.delete_preference, mem_id)
                        mem0_deleted += 1
                        logger.info(f"🗑️ Deleted via Mem0: [{category}] {mem_id}")
                    except Exception as del_err:
                        errors.append(f"Mem0 delete failed for {mem_id}: {del_err}")
                        logger.warning(f"⚠️ {errors[-1]}")

            logger.warning(f"✅ Mem0 path: deleted {mem0_deleted} preferences")

        except Exception as mem0_err:
            errors.append(f"Mem0 path error: {mem0_err}")
            logger.error(f"❌ Mem0 path failed: {mem0_err}")

        # ── Path 2: Zero-token raw MongoDB inserts ────────────────────────────
        # add_preference_zero_token() bypasses Mem0 entirely and stores docs with
        # schema: { embedding: [...], payload: { user_id, data, memory, metadata } }
        # Mem0's get_all() cannot see these. We must query MongoDB directly using
        # the correct nested path "payload.user_id".
        try:
            collection = await asyncio.to_thread(_get_stats_collection)

            # Find all zero-token docs for this user (payload.user_id is the correct path)
            raw_docs = await asyncio.to_thread(
                lambda: list(collection.find(
                    {"payload.user_id": user_id},
                    {"_id": 1, "payload.metadata.category": 1, "payload.data": 1}
                ))
            )

            ids_to_delete = []
            for doc in raw_docs:
                category = (
                    doc.get("payload", {})
                       .get("metadata", {})
                       .get("category", "general")
                )
                if category in PROTECTED_CATEGORIES:
                    logger.info(f"🔒 Keeping protected raw doc: [{category}]")
                    continue
                ids_to_delete.append(doc["_id"])

            if ids_to_delete:
                result = await asyncio.to_thread(
                    collection.delete_many, {"_id": {"$in": ids_to_delete}}
                )
                raw_deleted = result.deleted_count
                logger.warning(f"✅ Raw MongoDB path: deleted {raw_deleted} zero-token documents")
            else:
                logger.info("ℹ️ No zero-token documents found to delete")

        except Exception as raw_err:
            errors.append(f"Raw MongoDB path error: {raw_err}")
            logger.error(f"❌ Raw MongoDB path failed: {raw_err}")

        total_deleted = mem0_deleted + raw_deleted
        logger.warning(
            f"✅ Clear complete for {user_id}: "
            f"{mem0_deleted} Mem0 + {raw_deleted} raw = {total_deleted} total deleted. "
            f"Protected categories preserved: {PROTECTED_CATEGORIES}"
        )

        return {
            "status": "success",
            "preferences_deleted": total_deleted,
            "mem0_deleted": mem0_deleted,
            "raw_deleted": raw_deleted,
            "protected_categories": list(PROTECTED_CATEGORIES),
            "errors": errors if errors else None,
            "message": (
                f"Cleared {total_deleted} learned preferences. "
                f"Personal info, language, and UI preferences were preserved."
            )
        }

    except Exception as e:
        logger.error(f"❌ Clear preferences failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
@router.put("/preferences/{preference_id}")
async def update_preference(
    preference_id: str,
    new_text: str,
    user_id: str,
    category: Optional[str] = None
):
    """
    Update a specific preference by ID
    
    Args:
        preference_id: MongoDB document ID (e.g., "abc123...")
        new_text: New preference text to replace old one
        user_id: User identifier
        category: Optional new category
    
    Returns:
        Success/failure status
    """
    try:
        logger.info(f"📝 Updating preference {preference_id} for user {user_id}")
        
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        pref_mgr = await asyncio.to_thread(get_preference_manager, user_id)
        
        success = await asyncio.to_thread(
            pref_mgr.update_preference,
            preference_id,
            new_text,
            {"category": category} if category else None
        )
        
        if success:
            logger.info(f"✅ Preference updated: {preference_id}")
            return {
                "status": "success",
                "message": f"Preference updated successfully",
                "preference_id": preference_id,
                "new_text": new_text
            }
        else:
            logger.error(f"❌ Preference not found: {preference_id}")
            raise HTTPException(
                status_code=404, 
                detail=f"Preference {preference_id} not found"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Update failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Update error: {str(e)}"
        )

@router.delete("/preferences/{preference_id}")
async def delete_single_preference(
    preference_id: str,
    user_id: str
):
    """Delete a single preference by ID"""
    try:
        logger.info(f"🗑️ Deleting preference {preference_id} for user {user_id}")
        
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        pref_mgr = await asyncio.to_thread(get_preference_manager, user_id)
        
        success = await asyncio.to_thread(pref_mgr.delete_preference, preference_id)
        
        if success:
            logger.info(f"✅ Preference deleted: {preference_id}")
            return {
                "status": "success",
                "message": "Preference deleted successfully",
                "preference_id": preference_id
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Preference {preference_id} not found"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Delete failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Delete error: {str(e)}"
        )
    
    
# Module-level shared client — created once, reused for every request.
# This eliminates the 400–800ms cold-connection cost per /stats call.
_stats_mongo_client: Optional[MongoClient] = None

def _get_stats_collection():
    global _stats_mongo_client
    if _stats_mongo_client is None:
        _stats_mongo_client = MongoClient(MONGODB_URI)
    return _stats_mongo_client["yusr_db"]["mem0_preferences"]


@router.get("/stats")
async def get_memory_stats(user_id: str):
    """
    Get preference statistics.
    Uses a shared MongoClient (created once at module load) and count_documents()
    instead of loading all documents into RAM.
    """
    try:
        logger.info(f"📊 Fetching memory stats for user: {user_id}")

        col = await asyncio.to_thread(_get_stats_collection)

        # count_documents uses an index scan — no full collection load
        total_prefs = await asyncio.to_thread(
            col.count_documents, {"user_id": user_id}
        )
        personal_info = await asyncio.to_thread(
            col.count_documents,
            {"user_id": user_id, "metadata.category": "personal_info"}
        )
        app_usage = await asyncio.to_thread(
            col.count_documents,
            {"user_id": user_id, "metadata.category": "app_usage"}
        )

        # Rough storage estimate: avg BSON doc ~500 bytes (avoids loading all docs)
        estimated_storage_bytes = total_prefs * 500
        storage_size_mb = round(estimated_storage_bytes / (1024 * 1024), 4)

        stats = {
            "total_preferences": total_prefs,
            "personal_info_count": personal_info,
            "app_preferences_count": app_usage,
            "storage_size_mb": storage_size_mb,
            "storage_bytes": estimated_storage_bytes,
            "status": "success"
        }

        logger.info(f"✅ Stats: {stats}")
        return stats

    except Exception as e:
        logger.error(f"❌ Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "memory_api",
        "timestamp": datetime.now().isoformat()
    }