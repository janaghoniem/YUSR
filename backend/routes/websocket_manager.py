import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections per session."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].close()
            except Exception:
                pass
        self.active_connections[session_id] = websocket
        logger.info(f"🔌 WebSocket connected: {session_id}")

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)
        logger.info(f"🔌 WebSocket disconnected: {session_id}")

    async def send_to_session(self, session_id: str, data: dict):
        ws = self.active_connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.warning(f"⚠️ Failed to send to {session_id}: {e}")
                self.disconnect(session_id)

    async def broadcast(self, data: dict):
        for sid in list(self.active_connections.keys()):
            await self.send_to_session(sid, data)
