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
    Server-Sent Events stream for thinking updates
    Frontend connects to this endpoint to receive real-time thinking steps
    """
    async def event_generator():
        thinking_queue = asyncio.Queue()

        async def handle_thinking_update(message):
            if hasattr(message, 'session_id') and message.session_id == session_id:
                if hasattr(message, 'payload'):
                    await thinking_queue.put(message.payload)

        broker.subscribe(Channels.BROADCAST, handle_thinking_update)

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(thinking_queue.get(), timeout=30)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            logger.info(f"🔌 Client disconnected from thinking stream: {session_id}")
        except Exception as exc:
            logger.error(f"❌ Thinking stream error: {exc}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")
