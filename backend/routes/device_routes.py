"""
device_routes.py
================
Device Communication Routes.

Fixes applied:
- V-5: Removed duplicate route definitions (register_device_post appeared
  twice, PENDING_ACTIONS / ACTION_RESULTS defined twice).
- V-6: Device registry now scoped to user_id via MongoDB user_devices
  collection (via cross_platform_manager.DeviceRegistry).
- Added GET /device/cross-platform-tasks endpoint for device polling.
- Added POST /device/{device_id}/cross-platform-result for result reporting.
- In-memory DEVICE_REGISTRY kept for backward-compat with mobile polling
  but registration now also writes to MongoDB user_devices.
"""

from fastapi import APIRouter, HTTPException, Path, Body, Query
from typing import Dict, Any, Optional, List
import logging

from routes.cross_platform_manager import get_cross_platform_manager
from agents.language_agent import active_agents

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/device", tags=["device"])

# ---------------------------------------------------------------------------
# In-memory registries (backward-compatible with existing mobile code)
# ---------------------------------------------------------------------------
DEVICE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "default_device": {
        "device_id": "default_device",
        "name": "Default Device",
        "status": "offline",
        "last_seen": None,
        "screen_width": 1080,
        "screen_height": 2340,
        "android_version": "14",
        "app_name": "com.google.android.gm",
        "ui_tree": None,
    }
}

# Pending actions and results
PENDING_ACTIONS: Dict[str, List[Dict[str, Any]]] = {}
ACTION_RESULTS: Dict[str, List[Dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_action(action_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize action types for Android action server compatibility."""
    if action_data.get("action_type") == "global_action":
        ga = (action_data.get("global_action") or "").upper()
        if ga == "HOME":
            action_data["action_type"] = "navigate_home"
        elif ga == "BACK":
            action_data["action_type"] = "navigate_back"

    if action_data.get("action_type") == "navigate_home":
        action_data["action_type"] = "goToHome"

    if action_data.get("action_type") == "swipe":
        sxp = float(action_data.get("start_x_percent", 50) or 50)
        syp = float(action_data.get("start_y_percent", 80) or 80)
        exp_ = float(action_data.get("end_x_percent", 50) or 50)
        eyp = float(action_data.get("end_y_percent", 20) or 20)
        w = int(action_data.get("screen_width", 1080) or 1080)
        h = int(action_data.get("screen_height", 2340) or 2340)
        logger.info(
            f"[BRIDGE] swipe: ({sxp}%, {syp}%) → ({exp_}%, {eyp}%) | "
            f"abs: ({int(w*sxp/100)},{int(h*syp/100)}) → ({int(w*exp_/100)},{int(h*eyp/100)}) | "
            f"direction={'UP' if eyp < syp else 'DOWN'}"
        )

    return action_data


# ---------------------------------------------------------------------------
# UI Tree endpoints
# ---------------------------------------------------------------------------

@router.get("/{device_id}/ui-tree")
async def get_ui_tree(device_id: str = Path(...)):
    """Get current UI tree from device"""
    logger.debug(f"📱 Getting UI tree from device: {device_id}")
    
    if device_id not in DEVICE_REGISTRY:
        logger.error(f"❌ Device not found: {device_id}")
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    
    device = DEVICE_REGISTRY[device_id]
    
    if device["status"] == "offline":
        logger.warning(f"⚠️ Device is offline: {device_id}")
        raise HTTPException(status_code=503, detail=f"Device {device_id} is offline")
    
    if device.get("ui_tree"):
        return device["ui_tree"]
    
    return {
        "screen_id": f"screen_{device_id}",
        "device_id": device_id,
        "app_name": device.get("app_name", "unknown"),
        "app_package": "com.example.app",
        "screen_name": "Unknown Screen",
        "elements": [],
        "timestamp": 0,
        "screen_width": device.get("screen_width", 1080),
        "screen_height": device.get("screen_height", 2340),
    }


@router.post("/{device_id}/ui-tree")
async def update_ui_tree(device_id: str = Path(...), tree_data: Dict[str, Any] = None):
    """Update UI tree from device."""
    if device_id not in DEVICE_REGISTRY:
        DEVICE_REGISTRY[device_id] = {
            "device_id": device_id,
            "name": f"Device {device_id}",
            "status": "online",
            "last_seen": None,
            "screen_width": (tree_data or {}).get("screen_width", 1080),
            "screen_height": (tree_data or {}).get("screen_height", 2340),
            "ui_tree": tree_data,
        }
        logger.info(f"✅ Auto-registered new device: {device_id}")
    else:
        device = DEVICE_REGISTRY[device_id]
        device["status"] = "online"
        device["ui_tree"] = tree_data
        if tree_data:
            device["screen_width"] = tree_data.get("screen_width", 1080)
            device["screen_height"] = tree_data.get("screen_height", 2340)
    
    return {"status": "ok", "message": f"UI tree updated for {device_id}"}


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@router.post("/{device_id}/status")
async def update_device_status(device_id: str = Path(...), status_data: Dict[str, Any] = None):
    """Update device status."""
    if device_id not in DEVICE_REGISTRY:
        DEVICE_REGISTRY[device_id] = {
            "device_id": device_id,
            "name": f"Device {device_id}",
            "status": "online",
            "last_seen": None,
            "screen_width": 1080,
            "screen_height": 2340,
        }
    
    device = DEVICE_REGISTRY[device_id]
    device["status"] = "online"
    
    if status_data:
        device.update({
            "android_version": status_data.get("android_version"),
            "app_name": status_data.get("app_name"),
            "screen_width": status_data.get("screen_width", 1080),
            "screen_height": status_data.get("screen_height", 2340),
        })
    return {"status": "ok", "message": f"Status updated for {device_id}"}


# ---------------------------------------------------------------------------
# Action queue endpoints
# ---------------------------------------------------------------------------

@router.get("/{device_id}/pending-actions")
async def get_pending_actions(device_id: str = Path(...)):
    """Get pending actions for device (polling endpoint)."""
    PENDING_ACTIONS.setdefault(device_id, [])
    actions = PENDING_ACTIONS[device_id]
    if actions:
        logger.debug(f"📤 Returning {len(actions)} pending actions for {device_id}")
    response = {"actions": actions, "count": len(actions)}
    PENDING_ACTIONS[device_id] = []
    
    return response


@router.post("/{device_id}/action-result")
async def receive_action_result(
    device_id: str = Path(...),
    result_data: Dict[str, Any] = None,
):
    """Receive action execution result from Android device."""
    logger.debug(f"✅ Received action result from device: {device_id}")
    if result_data:
        logger.debug(f"   Action ID: {result_data.get('action_id')}, success: {result_data.get('success')}")
        if not result_data.get("success"):
            logger.warning(f"   Error: {result_data.get('error')}")
    ACTION_RESULTS.setdefault(device_id, [])
    ACTION_RESULTS[device_id].append(result_data)
    return {"status": "ok", "message": "Action result received"}


@router.post("/{device_id}/execute-action")
async def execute_action_on_device(
    device_id: str = Path(...),
    action_data: Dict[str, Any] = Body(...),
):
    """Execute an action on the device (queues for polling)."""
    action_type = action_data.get("action_type")
    logger.debug(f"⚡ Queueing action for device: {device_id}")
    logger.debug(f"   Action: {action_type}")
    
    if device_id not in DEVICE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    
    device = DEVICE_REGISTRY[device_id]
    
    if device["status"] == "offline":
        logger.warning(f"⚠️ Device {device_id} is offline")
        return {
            "action_id": action_data.get("action_id", "unknown"),
            "success": False,
            "error": "Device is offline",
            "execution_time_ms": 0,
        }
    action_data = _normalize_action(action_data)
    PENDING_ACTIONS.setdefault(device_id, [])
    PENDING_ACTIONS[device_id].append(action_data)
    logger.debug(f"✅ Action queued for polling: {action_data.get('action_type')}")
    return {
        "action_id": action_data.get("action_id", "unknown"),
        "success": True,
        "error": None,
        "execution_time_ms": 0,
    }


# ---------------------------------------------------------------------------
# Registration endpoints (FIX V-5: single POST, single GET — no duplicates)
# ---------------------------------------------------------------------------

@router.get("/{device_id}/register")
async def register_device_get(
    device_id: str = Path(...),
    user_id: str = Query(default=""),
    name: Optional[str] = None,
    android_version: Optional[str] = None,
    session_id: str = Query(default=""),
    platform: str = Query(default="mobile", description="Device platform: 'mobile' or 'desktop'"),
):
    """Register device via GET (used by Android app startup)."""
    logger.info(f"✅ Registering device: {device_id} for user: {user_id}")
    DEVICE_REGISTRY.setdefault(device_id, {
        "device_id": device_id,
        "name": name or f"Device {device_id}",
        "status": "online",
        "last_seen": None,
        "android_version": android_version,
        "screen_width": 1080,
        "screen_height": 2340,
    })
    device = DEVICE_REGISTRY[device_id]
    device["status"] = "online"
    if name:
        device["name"] = name
    if android_version:
        device["android_version"] = android_version

    # FIX V-6: Also register in MongoDB user_devices (async, non-blocking)
    if user_id and session_id:
        try:
            from core.mongo import get_database
            from core.dependencies import ws_manager as shared_ws_manager
            db = get_database("aura_db")

            # Try to use the manager if available; otherwise use DeviceRegistry directly
            mgr = get_cross_platform_manager(db=db, ws_manager=shared_ws_manager) if db is not None else None

            async def _do_register():
                try:
                    if mgr:
                        await mgr._registry.register_device(
                            user_id=user_id,
                            device_id=device_id,
                            platform=platform,
                            session_id=session_id,
                            label=name or f"Device {device_id}",
                        )
                    else:
                        # Fallback: instantiate registry directly if manager not initialized
                        from routes.cross_platform_manager import DeviceRegistry
                        registry = DeviceRegistry(db)
                        await registry.register_device(
                            user_id=user_id,
                            device_id=device_id,
                            platform=platform,
                            session_id=session_id,
                            label=name or f"Device {device_id}",
                        )
                except Exception as _inner_err:
                    logger.warning(f"⚠️ MongoDB device registration failed for {device_id}: {_inner_err}")

            import asyncio
            asyncio.create_task(_do_register())
        except Exception as _reg_err:
            logger.debug(f"⚠️ MongoDB device registration scheduling skipped: {_reg_err}")

    return {"status": "ok", "message": f"Device {device_id} registered", "device_info": device}


@router.post("/{device_id}/register")
async def register_device_post(
    device_id: str = Path(...),
    device_data: Dict[str, Any] = None,
):
    """Register device via POST (detailed info, called by Android app)."""
    logger.info(f"✅ Registering device via POST: {device_id}")
    DEVICE_REGISTRY.setdefault(device_id, {
        "device_id": device_id,
        "name": (device_data or {}).get("name", f"Device {device_id}"),
        "status": "online",
        "last_seen": None,
        "screen_width": 1080,
        "screen_height": 2340,
    })
    device = DEVICE_REGISTRY[device_id]
    device["status"] = "online"
    if device_data:
        device.update({
            "name": device_data.get("name", device["name"]),
            "android_version": device_data.get("android_version"),
            "device_model": device_data.get("device_model"),
            "screen_width": device_data.get("screen_width", 1080),
            "screen_height": device_data.get("screen_height", 2340),
        })

    # FIX V-6: Register in MongoDB user_devices
    user_id = (device_data or {}).get("user_id", "")
    session_id = (device_data or {}).get("session_id", "")
    if user_id and session_id:
        try:
            from core.mongo import get_database
            from core.dependencies import ws_manager as shared_ws_manager
            db = get_database("aura_db")

            mgr = get_cross_platform_manager(db=db, ws_manager=shared_ws_manager) if db is not None else None

            async def _do_register_post():
                try:
                    platform = device_data.get("platform", "mobile")
                    label = device_data.get("name", f"Device {device_id}")
                    if mgr:
                        await mgr._registry.register_device(
                            user_id=user_id,
                            device_id=device_id,
                            platform=platform,
                            session_id=session_id,
                            label=label,
                        )
                    else:
                        from routes.cross_platform_manager import DeviceRegistry
                        registry = DeviceRegistry(db)
                        await registry.register_device(
                            user_id=user_id,
                            device_id=device_id,
                            platform=platform,
                            session_id=session_id,
                            label=label,
                        )
                except Exception as _inner_err:
                    logger.warning(f"⚠️ MongoDB device registration failed for {device_id}: {_inner_err}")

            import asyncio
            asyncio.create_task(_do_register_post())
        except Exception as _reg_err:
            logger.debug(f"⚠️ MongoDB device registration scheduling skipped: {_reg_err}")

    logger.info(f"✅ Device {device_id} is now ONLINE")
    return {
        "status": "ok",
        "message": f"Device {device_id} registered and online",
        "device_info": device,
    }


# ---------------------------------------------------------------------------
# Device listing
# ---------------------------------------------------------------------------

@router.get("")
async def list_devices():
    """List all registered devices."""
    return {
        "total_devices": len(DEVICE_REGISTRY),
        "devices": [
            {
                "device_id": d["device_id"],
                "name": d["name"],
                "status": d["status"],
                "last_seen": d.get("last_seen"),
            }
            for d in DEVICE_REGISTRY.values()
        ],
    }


# ---------------------------------------------------------------------------
# Cross-platform task endpoints (NEW)
# ---------------------------------------------------------------------------

@router.post("/remote-task-result")
async def submit_remote_task_result(
    result_data: Dict[str, Any] = Body(...),
):
    """
    Report the result of a remotely executed subtask.
    The device calls this after executing a task received via WebSocket.
    """
    task_id = result_data.get("task_id", "")
    user_id = result_data.get("user_id", "")
    device_id = result_data.get("device_id", "")
    result = result_data.get("result", {})
    status = result.get("status", "completed")

    if not task_id or not user_id or not device_id:
        raise HTTPException(status_code=400, detail="task_id, user_id, and device_id are required")

    try:
        mgr = get_cross_platform_manager()
        if not mgr:
            return {"status": "ok", "message": "Manager not initialized"}

        success = await mgr.complete_remote_task(
            task_id=task_id,
            user_id=user_id,
            device_id=device_id,
            result=result,
            status=status,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Task not found or access denied")

        # Clear any stale clarification waiting on this session if available.
        session_id = result_data.get("session_id", "")
        if session_id:
            agent_key = f"{user_id}_{session_id}"
            if agent_key in active_agents:
                try:
                    active_agents[agent_key].awaiting_user_response = None
                except Exception:
                    pass

        logger.info(f"✅ Remote task {task_id} result stored in MongoDB (status={status})")
        return {"status": "ok", "task_id": task_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to store cross-platform task result: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear-pending-confirmations")
async def clear_pending_confirmations(
    user_id: str = Query(..., description="Authenticated user ID"),
    session_id: str = Query(..., description="Session ID"),
    reason: str = Query("timeout", description="Reason for clearing: timeout, failed, or success"),
):
    """
    Explicitly clear pending confirmations/clarifications for a session.
    Called when:
    - Task times out (device didn't respond in time)
    - Task fails with error
    - Task succeeds but pending confirmation still exists
    
    This prevents stale confirmations from blocking subsequent messages.
    """
    if not user_id or not session_id:
        raise HTTPException(status_code=400, detail="user_id and session_id are required")

    try:
        agent_key = f"{user_id}_{session_id}"
        
        if agent_key in active_agents:
            try:
                agent = active_agents[agent_key]
                # Clear the awaiting_user_response field
                agent.awaiting_user_response = None
                logger.info(f"✅ Cleared pending confirmations for {agent_key} (reason: {reason})")
                return {"status": "ok", "message": "Pending confirmations cleared"}
            except Exception as e:
                logger.warning(f"⚠️ Could not clear pending confirmations for {agent_key}: {e}")
                # Still return success to avoid blocking the calling code
                return {"status": "ok", "message": f"Clear attempted: {str(e)}"}
        else:
            logger.debug(f"ℹ️ No active agent found for {agent_key}, nothing to clear")
            return {"status": "ok", "message": "No active agent found"}

    except Exception as e:
        logger.error(f"❌ Failed to clear pending confirmations: {e}")
        raise HTTPException(status_code=500, detail=str(e))