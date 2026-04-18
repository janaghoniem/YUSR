import hashlib
import logging
import os
import uuid
from datetime import datetime

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from pymongo import MongoClient

from face_auth import face_auth
from core.dependencies import logger

router = APIRouter()


class OnboardingData(BaseModel):
    user_id: str
    username: str
    password: str
    email: str = ""
    introduction: str
    preferences: dict


@router.post("/onboarding/create-account")
async def create_account(data: OnboardingData):
    """
    Creates a new user account after onboarding.
    Stores username, hashed password, intro, and preferences in MongoDB.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        users_col = db["users"]

        existing_user = users_col.find_one({"username": data.username})
        if existing_user:
            logger.warning(f"Duplicate username attempt: {data.username}")
            raise HTTPException(status_code=409, detail="Username already taken")

        existing_by_id = users_col.find_one({"user_id": data.user_id})
        if existing_by_id:
            logger.warning(f"Duplicate user_id attempt: {data.user_id}")
            raise HTTPException(status_code=409, detail="User ID already exists")

        hashed_pw = hashlib.sha256(data.password.encode()).hexdigest() if data.password else ""

        user_doc = {
            "user_id": data.user_id,
            "username": data.username,
            "email": data.email,
            "password_hash": hashed_pw,
            "introduction": data.introduction,
            "preferences": data.preferences,
            "created_at": datetime.utcnow().isoformat(),
            "onboarding_complete": True,
            "has_face_auth": True,
        }

        users_col.insert_one(user_doc)
        logger.info(f"✅ Account created for user_id: {data.user_id}, username: {data.username}")

        try:
            from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
            pref_mgr = get_preference_manager(data.user_id)

            language_pref = data.preferences.get("language", "English")
            lang_code = "ar" if "عرب" in language_pref or language_pref.lower() == "arabic" else "en"
            pref_mgr.add_preference(
                f"User's preferred language is {language_pref} ({lang_code})",
                metadata={"category": "language_preference", "source": "onboarding", "lang_code": lang_code}
            )

            if data.introduction:
                pref_mgr.add_preference(
                    data.introduction,
                    metadata={"category": "personal_info", "source": "onboarding"}
                )

            theme = data.preferences.get("theme", "")
            if theme:
                pref_mgr.add_preference(
                    f"User prefers the {theme} theme",
                    metadata={"category": "ui_preference", "source": "onboarding"}
                )

            logger.info(f"✅ Stored onboarding profile into Mem0 for user_id: {data.user_id}")
        except Exception as mem_err:
            logger.warning(f"⚠️ Failed to store onboarding profile into Mem0 (non-fatal): {mem_err}")

        return {"status": "ok", "message": "Account created successfully", "user_id": data.user_id}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Account creation failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/onboarding/check-username")
async def check_username(username: str):
    """Names are not required to be unique. Always returns available."""
    return {"available": True}


class SessionCreateRequest(BaseModel):
    user_id: str


@router.post("/onboarding/session/create")
async def create_session_for_user(data: SessionCreateRequest):
    """
    Create a new stable session ID server-side, tied to user_id.
    Android calls this once per app launch after login.
    Returns a session_id the client must persist and reuse.
    """
    session_id = f"session_{data.user_id}_{uuid.uuid4().hex[:12]}"
    logger.info(f"✅ Session created for user {data.user_id}: {session_id}")
    return {
        "status": "ok",
        "session_id": session_id,
        "user_id": data.user_id,
    }


class IntroductionRequest(BaseModel):
    user_id: str
    language: str
    answers: dict


@router.post("/onboarding/store-introduction")
async def store_android_introduction(data: IntroductionRequest):
    """
    Store onboarding answers as Mem0 preferences for Android users.
    Called after account creation completes.
    """
    try:
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        pref_mgr = get_preference_manager(data.user_id)

        answers = data.answers
        prefs_to_store = []

        if answers.get("name"):
            prefs_to_store.append((
                f"User's name is {answers['name']}",
                {"category": "personal_info", "source": "android_onboarding"}
            ))

        if answers.get("job"):
            prefs_to_store.append((
                f"User's job/role is: {answers['job']}",
                {"category": "personal_info", "source": "android_onboarding"}
            ))

        if answers.get("accessibility"):
            prefs_to_store.append((
                f"Accessibility preferences: {answers['accessibility']}",
                {"category": "accessibility", "source": "android_onboarding"}
            ))

        if answers.get("tasks"):
            prefs_to_store.append((
                f"User wants AURA to help with: {answers['tasks']}",
                {"category": "task_preference", "source": "android_onboarding"}
            ))

        lang_label = "Arabic" if data.language == "ar" else "English"
        prefs_to_store.append((
            f"User's preferred language is {lang_label} ({data.language})",
            {"category": "language_preference", "source": "android_onboarding", "lang_code": data.language}
        ))

        for pref_text, metadata in prefs_to_store:
            pref_mgr.add_preference_zero_token(pref_text, metadata=metadata)

        logger.info(f"✅ Stored {len(prefs_to_store)} onboarding prefs for {data.user_id}")
        return {"status": "ok", "stored_count": len(prefs_to_store)}

    except Exception as exc:
        logger.error(f"❌ Failed to store introduction: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class LoginData(BaseModel):
    username: str
    password: str


@router.post("/onboarding/login")
async def login(data: LoginData):
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]

        hashed_pw = hashlib.sha256(data.password.encode()).hexdigest()
        user = db["users"].find_one({
            "username": data.username,
            "password_hash": hashed_pw
        })

        if not user:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        return {
            "status": "ok",
            "user_id": user["user_id"],
            "username": user["username"],
            "preferences": user.get("preferences", {})
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/onboarding/verify-face")
async def verify_face_login(request: Request):
    """
    Verify face biometrics for login
    """
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        face_image = data.get("face_image", "")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required")

        if not face_image:
            raise HTTPException(status_code=400, detail="Face image is required")

        logger.info(f"Verifying face for user: {username}")

        if not face_auth.get_user_face_status(username):
            raise HTTPException(
                status_code=404,
                detail="No face biometrics found for this username. Please sign up first."
            )

        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)

        verified, result = face_auth.verify_face(username, encoding)

        if not verified:
            raise HTTPException(status_code=401, detail=result)

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        user = db["users"].find_one({"username": username})

        if not user:
            raise HTTPException(status_code=404, detail="User account not found")

        logger.info(f"✅ Face verification successful for {username} (confidence: {result.get('confidence', 'N/A')})")

        return {
            "status": "ok",
            "user_id": user["user_id"],
            "username": user["username"],
            "preferences": user.get("preferences", {}),
            "confidence": result.get("confidence", 0.95),
            "auth_method": "face"
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Face verification error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/onboarding/face-status/{username}")
async def get_face_status(username: str):
    """
    Check if a user has face biometrics registered
    """
    try:
        has_face = face_auth.get_user_face_status(username)
        return {
            "username": username,
            "has_face_auth": has_face
        }
    except Exception as exc:
        logger.error(f"❌ Error checking face status: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/onboarding/face-data/{user_id}")
async def delete_face_data(user_id: str):
    """
    Delete face data for a user (useful for account deletion)
    """
    try:
        deleted = face_auth.delete_face_data(user_id)
        if deleted:
            return {"status": "success", "message": "Face data deleted"}
        else:
            return {"status": "not_found", "message": "No face data found"}
    except Exception as exc:
        logger.error(f"❌ Error deleting face data: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/onboarding/login-face-only")
async def login_face_only(request: Request):
    """
    Login using ONLY face - no username required
    Uses strict thresholds for high security
    """
    logger.info("Received face-only login request")
    try:
        data = await request.json()
        face_image = data.get("face_image", "")

        if not face_image:
            raise HTTPException(status_code=400, detail="Face image required")

        logger.info("Processing face-only login with strict security")

        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]

        all_face_users = list(db["face_auth_data"].find({}))

        if not all_face_users:
            logger.warning("No face data found in database")
            raise HTTPException(status_code=404, detail="No registered faces found. Please sign up first.")

        matches = []

        for user_data in all_face_users:
            stored_encrypted = user_data.get("face_encoding_data")
            if not stored_encrypted:
                continue

            stored_encoding = face_auth._verify_encoding(stored_encrypted)
            if stored_encoding is None:
                logger.warning(f"Corrupted face data for user {user_data.get('username')}")
                continue

            stored_array = np.array(stored_encoding)
            current_array = np.array(encoding)
            distance = np.linalg.norm(stored_array - current_array)
            confidence_percent = face_auth.calculate_confidence(distance)

            matches.append({
                "user_data": user_data,
                "distance": distance,
                "confidence_percent": confidence_percent,
                "confidence": confidence_percent / 100
            })

            logger.info(f"Comparing with user {user_data['username']}: distance={distance:.4f}, confidence={confidence_percent:.1f}%")

        matches.sort(key=lambda x: (x["distance"], -x["confidence_percent"]))

        if not matches:
            raise HTTPException(status_code=404, detail="No valid face data found")

        best_match = matches[0]

        is_acceptable = (
            best_match["distance"] <= face_auth.max_acceptable_distance and
            best_match["confidence_percent"] >= face_auth.min_confidence_percent
        )

        if not is_acceptable:
            logger.warning(f"Face rejected. Best match: {best_match['user_data']['username']} with {best_match['confidence_percent']:.1f}% confidence (need > {face_auth.min_confidence_percent:.0f}%)")

            if len(matches) > 1 and matches[1]["distance"] - best_match["distance"] < 0.05:
                raise HTTPException(
                    status_code=401,
                    detail="Face ambiguous. Multiple similar faces detected. Please ensure good lighting and look directly at the camera."
                )

            raise HTTPException(
                status_code=401,
                detail=f"Face not recognized. Best match confidence: {best_match['confidence_percent']:.1f}% (need > {face_auth.min_confidence_percent:.0f}%). Please ensure good lighting and look directly at the camera."
            )

        user = db["users"].find_one({"user_id": best_match["user_data"]["user_id"]})

        if not user:
            logger.error(f"User account not found for user_id: {best_match['user_data']['user_id']}")
            raise HTTPException(status_code=404, detail="User account not found")

        logger.info(f"✅ Face-only login successful for {user['username']} (confidence: {best_match['confidence_percent']:.1f}%, distance: {best_match['distance']:.4f})")

        return {
            "status": "ok",
            "user_id": user["user_id"],
            "username": user["username"],
            "preferences": user.get("preferences", {}),
            "confidence": best_match["confidence"],
            "confidence_percent": best_match["confidence_percent"],
            "distance": round(best_match["distance"], 4),
            "auth_method": "face_only"
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Face-only login error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/onboarding/register-face")
async def register_face(request: Request):
    """
    Register face biometrics with quality check
    """
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        user_id = data.get("user_id", "")
        face_image = data.get("face_image", "")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required")

        if not face_image:
            raise HTTPException(status_code=400, detail="Face image is required")

        if not user_id:
            raise HTTPException(status_code=400, detail="User ID is required")

        logger.info(f"Processing face registration for user: {username}, user_id: {user_id}")

        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]

        existing_face = db["face_auth_data"].find_one({"user_id": user_id})
        if existing_face and existing_face.get("username") != username:
            logger.warning(f"User ID {user_id} is already associated with username {existing_face['username']}")
            raise HTTPException(
                status_code=409,
                detail=f"User ID {user_id} is already associated with a different username. Please logout and try again."
            )

        encoding2, _ = face_auth.process_face_image(face_image)
        if not encoding2:
            raise HTTPException(status_code=400, detail="Could not reliably detect face. Please try again with better lighting.")

        success, msg = face_auth.store_face_data(user_id, username, encoding)
        if not success:
            raise HTTPException(status_code=500, detail=msg)

        verify_check = face_auth.get_user_face_status(username)
        if not verify_check:
            logger.error(f"❌ CRITICAL: Face registered but NOT found in DB for {username}")
            raise HTTPException(status_code=500, detail="Face data failed to persist. Please retry.")

        logger.info(f"✅ Face registered and VERIFIED in DB for {username} (user_id: {user_id})")
        return {
            "status": "success",
            "message": "Face biometrics registered successfully",
            "user_id": user_id,
            "username": username,
            "action": "registered"
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Face registration error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/debug/user-sessions/{user_id}")
async def debug_user_sessions(user_id: str):
    """
    Debug endpoint to list all sessions for a user
    (Remove in production or add authentication)
    """
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        sessions = list(db["language_agent_conversations"].find(
            {"user_id": user_id},
            {"session_id": 1, "title": 1, "timestamp": 1}
        ).sort("timestamp", -1))

        return {
            "user_id": user_id,
            "session_count": len(sessions),
            "sessions": sessions
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/onboarding/cleanup-user/{user_id}")
async def cleanup_user_data(user_id: str):
    """
    Cleanup endpoint to remove all traces of a user
    Useful for debugging and testing
    """
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]

        user_result = db["users"].delete_one({"user_id": user_id})
        face_result = db["face_auth_data"].delete_one({"user_id": user_id})
        conv_result = db["language_agent_conversations"].delete_many({"user_id": user_id})

        return {
            "status": "success",
            "deleted": {
                "user": user_result.deleted_count,
                "face_data": face_result.deleted_count,
                "conversations": conv_result.deleted_count
            }
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/user/profile")
async def get_user_profile(user_id: str):
    """Get user profile (name, email)"""
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        user = db["users"].find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "status": "ok",
            "username": user.get("username", ""),
            "email": user.get("email", ""),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class UpdateProfileData(BaseModel):
    user_id: str
    username: str = ""
    email: str = ""


@router.put("/user/profile")
async def update_user_profile(data: UpdateProfileData):
    """Update user name and/or email"""
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]

        update_fields = {}
        if data.username:
            existing = db["users"].find_one({"username": data.username, "user_id": {"$ne": data.user_id}})
            if existing:
                raise HTTPException(status_code=409, detail="Username already taken")
            update_fields["username"] = data.username
        if data.email is not None:
            update_fields["email"] = data.email

        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = db["users"].update_one(
            {"user_id": data.user_id},
            {"$set": update_fields}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        return {"status": "ok", "updated": update_fields}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
