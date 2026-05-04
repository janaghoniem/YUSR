"""
cross_platform_manager.py
=========================
Handles cross-platform task routing: detecting when a user on one device
wants a task executed on another device, then delivering it.

Architecture
------------
- WebSocket push (primary):   target device is online → push task payload
                              directly over existing /ws/{session_id} connection.
- MongoDB queue (fallback):   target device is offline → write to
                              `cross_platform_tasks` collection; device picks
                              up tasks when it reconnects or polls
                              GET /device/cross-platform-tasks.

Detection is intentionally lightweight (regex + keyword match) and runs
in the Language Agent BEFORE the Coordinator so the Coordinator receives
a pre-classified payload with `device_type: "mixed"` and `target_device`.

Security
--------
- Tasks are scoped to `user_id`; a device can only claim tasks belonging
  to its authenticated user.
- No device IP addresses are stored or exposed.
- MongoDB documents carry a TTL index (24 h) so orphaned tasks self-delete.
- `target_device_id` is validated against the `user_devices` registry
  before delivery; unknown device IDs are rejected.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.mongo import get_database

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-platform intent patterns
# ---------------------------------------------------------------------------

# Each tuple: (regex_pattern, source_platform_hint, target_platform_hint)
# Both hints may be None when the direction isn't specified by the user.
_CROSS_PLATFORM_PATTERNS: List[Tuple[re.Pattern, Optional[str], Optional[str]]] = [
    # "from my laptop/computer/desktop" → target = desktop
    (re.compile(
        r"\b(from|on|using|at)\s+(my\s+)?(laptop|computer|desktop|pc|windows|mac)\b",
        re.IGNORECASE,
    ), None, "desktop"),

    # "from my phone/mobile/android" → target = mobile
    (re.compile(
        r"\b(from|on|using|at)\s+(my\s+)?(phone|mobile|android|iphone|tablet)\b",
        re.IGNORECASE,
    ), None, "mobile"),

    # "on my other device" → mixed, resolve at delivery time
    (re.compile(
        r"\b(on|to|from)\s+my\s+other\s+device\b",
        re.IGNORECASE,
    ), None, None),

    # "send to my phone" / "open on my laptop"
    (re.compile(
        r"\b(send|open|run|execute|do|perform)\s+.{0,30}\b(on|to)\s+(my\s+)?(phone|mobile|android|tablet)\b",
        re.IGNORECASE,
    ), None, "mobile"),
    (re.compile(
        r"\b(send|open|run|execute|do|perform)\s+.{0,30}\b(on|to)\s+(my\s+)?(laptop|computer|desktop|pc|windows|mac)\b",
        re.IGNORECASE,
    ), None, "desktop"),

    # Arabic cross-platform triggers
    (re.compile(r"\b(من|على|في)\s+(لابتوب|حاسوب|كمبيوتر|ويندوز|ماك)\b", re.IGNORECASE), None, "desktop"),
    (re.compile(r"\b(من|على|في)\s+(موبايل|تليفون|جوال|أندرويد)\b", re.IGNORECASE), None, "mobile"),
]

# High-confidence cross-device phrases that skip the pattern check
_CROSS_DEVICE_PHRASES = [
    "from my laptop", "from my computer", "from my desktop", "from my pc",
    "from my phone", "from my mobile", "from my android",
    "on my other device", "on the other device",
    "من لابتوبي", "من حاسوبي", "من موبايلي", "من تليفوني",
]


def detect_cross_platform_intent(
    text: str,
    source_platform: str = "desktop",
) -> Optional[Dict[str, Any]]:
    """
    Analyse user input text to detect cross-platform task intent.

    Returns a dict when cross-platform intent is detected:
    {
        "is_cross_platform": True,
        "target_platform": "mobile" | "desktop" | None,
        "source_platform": "desktop" | "mobile",
        "confidence": 0.0–1.0,
        "matched_phrase": str,
    }
    Returns None when no cross-platform intent is found.
    """
    if not text:
        return None

    text_lower = text.lower().strip()

    # Fast path: exact phrase match
    for phrase in _CROSS_DEVICE_PHRASES:
        if phrase in text_lower:
            target = (
                "desktop"
                if any(k in phrase for k in ("laptop", "computer", "desktop", "pc", "حاسوب", "لابتوب"))
                else "mobile"
            )
            # If the target matches the source, it's not cross-platform
            if target == source_platform:
                continue
            return {
                "is_cross_platform": True,
                "target_platform": target,
                "source_platform": source_platform,
                "confidence": 0.95,
                "matched_phrase": phrase,
            }

    # Regex pattern scan
    for pattern, _src_hint, tgt_hint in _CROSS_PLATFORM_PATTERNS:
        m = pattern.search(text)
        if m:
            target_platform = tgt_hint
            # If target is same as source, skip
            if target_platform and target_platform == source_platform:
                continue
            return {
                "is_cross_platform": True,
                "target_platform": target_platform,  # may be None → resolve later
                "source_platform": source_platform,
                "confidence": 0.80,
                "matched_phrase": m.group(0),
            }

    return None


# ---------------------------------------------------------------------------
# Device Registry (MongoDB-backed)
# ---------------------------------------------------------------------------

class DeviceRegistry:
    """
    Manages the per-user device registry in MongoDB (`user_devices` collection).

    Schema per document:
    {
        "user_id": str,
        "devices": [
            {
                "device_id": str,
                "platform": "desktop" | "mobile",
                "label": str,
                "session_id": str,
                "online": bool,
                "last_seen": ISODate,
            }
        ]
    }
    """

    def __init__(self, db):
        self._col = db["user_devices"]

    async def register_device(
        self,
        user_id: str,
        device_id: str,
        platform: str,
        session_id: str,
        label: str = "",
    ) -> None:
        """Register or update a device for a user."""
        import asyncio
        now = datetime.now(timezone.utc)
        device_doc = {
            "device_id": device_id,
            "platform": platform,
            "label": label or f"{platform.capitalize()} device",
            "session_id": session_id,
            "online": True,
            "last_seen": now,
        }
        await asyncio.to_thread(
            self._col.update_one,
            {"user_id": user_id, "devices.device_id": device_id},
            {"$set": {"devices.$": device_doc}},
        )
        # If no match (device not yet in array), push it
        result = await asyncio.to_thread(
            self._col.update_one,
            {"user_id": user_id},
            {
                "$setOnInsert": {"user_id": user_id},
                "$addToSet": {"devices": device_doc},
            },
            True,  # upsert
        )
        logger.info(f"📱 Registered device {device_id} ({platform}) for user {user_id}")

    async def set_device_online(self, user_id: str, device_id: str, session_id: str, online: bool) -> None:
        import asyncio
        now = datetime.now(timezone.utc)
        await asyncio.to_thread(
            self._col.update_one,
            {"user_id": user_id, "devices.device_id": device_id},
            {"$set": {
                "devices.$.online": online,
                "devices.$.last_seen": now,
                "devices.$.session_id": session_id,
            }},
        )

    async def get_user_devices(self, user_id: str) -> List[Dict[str, Any]]:
        import asyncio
        doc = await asyncio.to_thread(
            self._col.find_one,
            {"user_id": user_id},
        )
        if not doc:
            return []
        return doc.get("devices", [])

    async def get_target_device(
        self,
        user_id: str,
        target_platform: Optional[str],
        exclude_device_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best target device for a cross-platform task.
        Prefers online devices; falls back to most-recently-seen offline device.
        """
        devices = await self.get_user_devices(user_id)
        candidates = [
            d for d in devices
            if (target_platform is None or d.get("platform") == target_platform)
            and d.get("device_id") != exclude_device_id
        ]
        if not candidates:
            return None

        # Prefer online
        online = [d for d in candidates if d.get("online")]
        if online:
            return online[0]

        # Fall back to most recently seen
        candidates.sort(
            key=lambda d: d.get("last_seen") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return candidates[0] if candidates else None

    async def validate_device_belongs_to_user(self, user_id: str, device_id: str) -> bool:
        devices = await self.get_user_devices(user_id)
        return any(d["device_id"] == device_id for d in devices)


# ---------------------------------------------------------------------------
# Cross-Platform Task Manager
# ---------------------------------------------------------------------------

class CrossPlatformTaskManager:
    """
    Routes tasks to a different device than the one that issued the command.

    Delivery strategy:
      1. Check if target device has an active WebSocket session.
         If yes → push the task payload via ws_manager.send_to_session().
      2. If target device is offline → write to MongoDB `cross_platform_tasks`
         with status="pending". Device polls on reconnect.
    """

    def __init__(self, db, ws_manager):
        self._col = db["cross_platform_tasks"]
        self._ws_manager = ws_manager
        self._registry = DeviceRegistry(db)
        self._ensure_ttl_index()

    def _ensure_ttl_index(self):
        """Create TTL index so expired tasks auto-delete after 24 hours."""
        try:
            self._col.create_index("expires_at", expireAfterSeconds=0)
            logger.info("✅ TTL index ensured on cross_platform_tasks.expires_at")
        except Exception as e:
            logger.warning(f"⚠️ Could not create TTL index: {e}")

    async def deliver_task(
        self,
        user_id: str,
        source_device_id: str,
        source_session_id: str,
        target_platform: Optional[str],
        task_payload: Dict[str, Any],
        original_request: str,
    ) -> Dict[str, Any]:
        """
        Deliver a cross-platform task to the correct device.

        Returns:
            {
                "status": "delivered" | "queued" | "no_target_device",
                "task_id": str,
                "target_device_id": str | None,
                "delivery_method": "websocket" | "mongodb_queue" | None,
            }
        """
        task_id = f"xp_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=24)

        # Resolve target device
        target_device = await self._registry.get_target_device(
            user_id=user_id,
            target_platform=target_platform,
            exclude_device_id=source_device_id,
        )

        if not target_device:
            logger.warning(f"⚠️ No target device found for user {user_id} platform={target_platform}")
            return {
                "status": "no_target_device",
                "task_id": task_id,
                "target_device_id": None,
                "delivery_method": None,
            }

        target_device_id = target_device["device_id"]
        target_session_id = target_device.get("session_id")

        # Build the task document
        task_doc = {
            "task_id": task_id,
            "user_id": user_id,
            "source_device_id": source_device_id,
            "source_session_id": source_session_id,
            "target_device_id": target_device_id,
            "target_session_id": target_session_id,
            "source_platform": task_payload.get("device_type", "unknown"),
            "target_platform": target_device.get("platform"),
            "task_payload": task_payload,
            "original_request": original_request,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
            "delivered_at": None,
            "result": None,
        }

        # Attempt WebSocket push first
        if target_device.get("online") and target_session_id:
            ws_connected = target_session_id in self._ws_manager.active_connections
            if ws_connected:
                try:
                    push_payload = {
                        "type": "cross_platform_task",
                        "task_id": task_id,
                        "source_platform": task_doc["source_platform"],
                        "original_request": original_request,
                        "task_payload": task_payload,
                    }
                    await self._ws_manager.send_to_session(target_session_id, push_payload)
                    task_doc["status"] = "delivered"
                    task_doc["delivered_at"] = datetime.now(timezone.utc)
                    await self._save_task(task_doc)
                    logger.info(f"✅ Cross-platform task {task_id} pushed via WebSocket to {target_device_id}")
                    return {
                        "status": "delivered",
                        "task_id": task_id,
                        "target_device_id": target_device_id,
                        "delivery_method": "websocket",
                    }
                except Exception as e:
                    logger.warning(f"⚠️ WebSocket push failed for {target_device_id}: {e} — falling back to queue")

        # Fallback: MongoDB queue
        await self._save_task(task_doc)
        logger.info(f"📬 Cross-platform task {task_id} queued in MongoDB for {target_device_id}")
        return {
            "status": "queued",
            "task_id": task_id,
            "target_device_id": target_device_id,
            "delivery_method": "mongodb_queue",
        }

    async def _save_task(self, task_doc: Dict[str, Any]) -> None:
        import asyncio
        await asyncio.to_thread(self._col.insert_one, dict(task_doc))

    async def claim_pending_tasks(self, user_id: str, device_id: str) -> List[Dict[str, Any]]:
        """
        Called when a device reconnects or polls for pending tasks.
        Returns all pending tasks for this device and marks them as delivered.
        Validates that device_id belongs to user_id before returning tasks.
        """
        if not await self._registry.validate_device_belongs_to_user(user_id, device_id):
            logger.warning(f"🚫 Device {device_id} does not belong to user {user_id} — rejecting task claim")
            return []

        import asyncio
        now = datetime.now(timezone.utc)
        tasks = await asyncio.to_thread(
            self._col.find,
            {
                "user_id": user_id,
                "target_device_id": device_id,
                "status": "pending",
                "expires_at": {"$gt": now},
            },
        )
        tasks = list(tasks)
        if not tasks:
            return []

        task_ids = [t["task_id"] for t in tasks]
        await asyncio.to_thread(
            self._col.update_many,
            {"task_id": {"$in": task_ids}},
            {"$set": {"status": "delivered", "delivered_at": now}},
        )
        # Remove MongoDB internal _id before returning
        for t in tasks:
            t.pop("_id", None)
        logger.info(f"📨 Delivered {len(tasks)} pending cross-platform tasks to {device_id}")
        return tasks

    async def update_task_result(
        self,
        task_id: str,
        user_id: str,
        device_id: str,
        result: Dict[str, Any],
        status: str = "completed",
    ) -> bool:
        """Record the result of a completed cross-platform task."""
        import asyncio
        # Validate ownership before writing
        if not await self._registry.validate_device_belongs_to_user(user_id, device_id):
            logger.warning(f"🚫 Device {device_id} tried to update task {task_id} owned by another user")
            return False

        res = await asyncio.to_thread(
            self._col.update_one,
            {"task_id": task_id, "user_id": user_id, "target_device_id": device_id},
            {"$set": {"status": status, "result": result, "completed_at": datetime.now(timezone.utc)}},
        )
        return res.modified_count > 0


# ---------------------------------------------------------------------------
# Singleton accessor (lazy-initialized)
# ---------------------------------------------------------------------------

_manager_instance: Optional[CrossPlatformTaskManager] = None


def get_cross_platform_manager(db=None, ws_manager=None) -> Optional[CrossPlatformTaskManager]:
    global _manager_instance
    if _manager_instance is None:
        if db is None:
            db = get_database("aura_db")
        if ws_manager is None:
            try:
                from core.dependencies import ws_manager as shared_ws_manager
                ws_manager = shared_ws_manager
            except Exception:
                ws_manager = None
        if db is None or ws_manager is None:
            return None
        _manager_instance = CrossPlatformTaskManager(db, ws_manager)
    return _manager_instance


def init_cross_platform_manager(db, ws_manager) -> CrossPlatformTaskManager:
    global _manager_instance
    _manager_instance = CrossPlatformTaskManager(db, ws_manager)
    return _manager_instance