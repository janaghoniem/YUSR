"""
services/task_dispatcher.py
============================
Background service that polls MongoDB for pending cross-platform subtasks
and pushes them to the target device via its active WebSocket connection.

Lifecycle
---------
Started in core/lifespan.py after the broker and agents are initialised.
Stopped cleanly during shutdown.

Flow
----
1. Every `poll_interval` seconds, query `cross_platform_tasks` for documents
   with status = "pending" and expires_at > now.
2. For each document, check whether `target_session_id` has an active
   WebSocket connection in `ws_manager.active_connections`.
3. If connected → push the task payload and mark the document "delivered".
4. If not connected → leave as "pending"; retry on the next tick.

This decouples delivery from the moment the coordinator creates the task,
which handles the common case where the target device is briefly offline
(backgrounded app, sleep, etc.) and reconnects within the TTL window.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """
    Background task that polls MongoDB for pending cross-platform tasks.

    Behavior:
    - If a pending task targets *this* device (based on DEVICE_ID env), claim
      and execute it locally, then report the result back to the source server
      (if `source_server_url` is present) or write the result into MongoDB.
    - Otherwise, if the target session is connected via WebSocket, push the
      task over the WS connection (legacy behavior).
    """

    def __init__(self, user_id: Optional[str] = None, device_id: Optional[str] = None, poll_interval_seconds: int = 2):
        # Backwards-compatible signature: lifespan.py constructs with poll_interval_seconds only.
        self.poll_interval = poll_interval_seconds
        self.user_id = user_id or os.getenv("USER_ID") or os.getenv("AURA_USER_ID") or "default_user"
        self.device_id = device_id or os.getenv("DEVICE_ID") or os.getenv("AURA_DEVICE_ID") or "default_device"
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background dispatcher loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="task_dispatcher")
        logger.info("✅ TaskDispatcher started (poll interval %ds)", self.poll_interval)

    async def stop(self) -> None:
        """Stop the background dispatcher loop cleanly."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 TaskDispatcher stopped")

    async def _dispatch_loop(self) -> None:
        # Lazy imports keep startup fast and avoid circular dependencies.
        from core.mongo import get_database
        from core.dependencies import ws_manager
        # Local execution helpers
        try:
            from agents.coordinator_agent.coordinator_agent import execute_single_task, resolve_remote_task
        except Exception:
            execute_single_task = None
            resolve_remote_task = None

        db = get_database("aura_db")
        if db is None:
            logger.error("❌ TaskDispatcher: could not get MongoDB database — dispatcher disabled")
            return

        collection = db["cross_platform_tasks"]

        while self._running:
            try:
                await self._tick(collection, ws_manager, execute_single_task, resolve_remote_task)
            except Exception as exc:
                logger.error(f"❌ TaskDispatcher error: {exc}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def _tick(self, collection, ws_manager, execute_single_task, resolve_remote_task) -> None:
        """One poll cycle: find pending tasks and push to connected devices."""
        now = datetime.now(timezone.utc)

        # Fetch all pending, non-expired tasks
        pending_tasks = await asyncio.to_thread(
            lambda: list(collection.find({
                "status": "pending",
                "expires_at": {"$gt": now},
            }))
        )

        if not pending_tasks:
            return

        for task_doc in pending_tasks:
            task_id = task_doc.get("task_id", "unknown")
            target_session_id = task_doc.get("target_session_id")

            # If this task targets this device, claim and execute it locally
            if str(task_doc.get("target_device_id") or "") == str(self.device_id):
                task_payload = task_doc.get("task_payload") or {}
                
                # FIX: Skip language tasks - they require user interaction (confirmation/response)
                # and must stay in the original HTTP request context, not executed via background dispatcher
                if task_payload.get("target_agent") == "language":
                    logger.info(f"⏭️ Task {task_id} is a language task; skipping remote dispatch (requires user interaction)")
                    continue
                
                logger.info(f"🖥️ Task {task_id} is for this device {self.device_id}; claiming for local execution")
                now = datetime.now(timezone.utc)
                # Mark as running to avoid duplicate claims
                await asyncio.to_thread(
                    collection.update_one,
                    {"_id": task_doc["_id"]},
                    {"$set": {"status": "running", "started_at": now}},
                )

                # Reconstruct and execute
                try:
                    from agents.coordinator_agent.coordinator_agent import ActionTask
                    task = ActionTask(**task_payload)
                except Exception as e:
                    logger.error(f"Failed to reconstruct ActionTask for {task_id}: {e}")
                    await asyncio.to_thread(
                        collection.update_one,
                        {"_id": task_doc["_id"]},
                        {"$set": {"status": "failed", "completed_at": datetime.now(timezone.utc), "result": {"error": "invalid task payload"}}},
                    )
                    continue

                if execute_single_task is None:
                    logger.error("Local execution not available in this process; skipping")
                    continue

                try:
                    result = await execute_single_task(
                        task,
                        session_id=task_doc.get("source_session_id", ""),
                        original_message_id="dispatcher",
                        user_language=task_doc.get("task_payload", {}).get("user_language", "en"),
                        output_language=task_doc.get("task_payload", {}).get("output_language", "en"),
                        user_profile=task_doc.get("task_payload", {}).get("user_profile", {}),
                        user_id=task_doc.get("user_id") or self.user_id,
                    )
                except Exception as e:
                    logger.error(f"Local execution failed for task {task_id}: {e}", exc_info=True)
                    result = None

                # Report back to source server if URL present
                source_url = task_doc.get("source_server_url")
                if source_url and result is not None:
                    try:
                        import aiohttp
                        payload = {
                            "task_id": task_id,
                            "user_id": task_doc.get("user_id"),
                            "device_id": self.device_id,
                            "result": result.model_dump() if hasattr(result, 'model_dump') else result,
                        }
                        async with aiohttp.ClientSession() as session:
                            await session.post(f"{source_url}/device/remote-task-result", json=payload)
                    except Exception as e:
                        logger.warning(f"Failed to POST result to source {source_url} for task {task_id}: {e}")
                else:
                    # As fallback, update MongoDB doc and try resolving local future if present
                    try:
                        if result is not None and resolve_remote_task:
                            try:
                                resolve_remote_task(task_id, result.model_dump())
                            except Exception:
                                pass
                    except Exception:
                        pass

                # Persist completion
                await asyncio.to_thread(
                    collection.update_one,
                    {"_id": task_doc["_id"]},
                    {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc), "result": (result.model_dump() if result is not None else {"error": "execution_failed"})}},
                )
                continue

            if not target_session_id:
                logger.debug(f"⏳ Task {task_id}: no target_session_id, skipping")
                continue

            # Only push if the device is currently connected
            if target_session_id not in ws_manager.active_connections:
                logger.debug(
                    f"⏳ Task {task_id}: target session {target_session_id} not connected, will retry"
                )
                continue

            # Build push payload
            push_payload = {
                "type": "cross_platform_task",
                "task_id": task_id,
                "source_platform": task_doc.get("source_platform", "unknown"),
                "original_request": task_doc.get("original_request", ""),
                "task_payload": task_doc.get("task_payload", {}),
            }

            try:
                await ws_manager.send_to_session(target_session_id, push_payload)

                # Mark as delivered so we don't re-push on the next tick
                await asyncio.to_thread(
                    collection.update_one,
                    {"_id": task_doc["_id"]},
                    {"$set": {"status": "delivered", "delivered_at": datetime.now(timezone.utc)}},
                )
                logger.info(
                    f"📤 TaskDispatcher pushed task {task_id} to session {target_session_id}"
                )
            except Exception as push_exc:
                logger.warning(
                    f"⚠️ TaskDispatcher failed to push task {task_id} to {target_session_id}: {push_exc}"
                )


# Module-level singleton used by lifespan.py
dispatcher = TaskDispatcher(poll_interval_seconds=2)