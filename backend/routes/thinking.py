"""
thinking.py
===========
Server-Sent Events stream for real-time thinking step updates.

Fix V-13: The original never unsubscribed from BROADCAST on disconnect,
causing dead closures to accumulate in the broker.  The updated
event_generator wraps execution in try/finally and calls
broker.unsubscribe() when the client disconnects or an error occurs.
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agents.utils.protocol import Channels
from core.dependencies import broker, logger

router = APIRouter()


@router.get("/thinking-stream/{session_id}")
async def thinking_stream(session_id: str):
    """
    Server-Sent Events stream for thinking updates.
    Frontend connects here to receive real-time thinking steps.
    """
    async def event_generator():
        thinking_queue: asyncio.Queue = asyncio.Queue()

        async def handle_thinking_update(message):
            if hasattr(message, "session_id") and message.session_id == session_id:
                if hasattr(message, "payload"):
                    await thinking_queue.put(message.payload)

        broker.subscribe(Channels.BROADCAST, handle_thinking_update)
        logger.info(f"🔌 Thinking stream opened for session: {session_id}")

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(thinking_queue.get(), timeout=30)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keeps the connection alive through proxies
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            logger.info(f"🔌 Client disconnected from thinking stream: {session_id}")
        except Exception as exc:
            logger.error(f"❌ Thinking stream error: {exc}")
        finally:
            # FIX V-13: Always unsubscribe to prevent dead callback accumulation
            removed = broker.unsubscribe(Channels.BROADCAST, handle_thinking_update)
            logger.info(
                f"🧹 Thinking stream subscription cleaned up for {session_id} "
                f"(removed={removed})"
            )

    return StreamingResponse(event_generator(), media_type="text/event-stream")