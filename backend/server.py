import logging
import os

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.lifespan import lifespan
from memory_api import router as memory_router
from routes.auth import router as auth_router
from routes.chat import router as chat_router
from routes.device_routes import router as device_router
from routes.thinking import router as thinking_router
from routes.voice import router as voice_router
from routes.websocket import router as websocket_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(
    title="AURA Unified Backend (Pub/Sub)",
    description="Multi-agent system with message broker",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(device_router)
app.include_router(memory_router)
app.include_router(voice_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(thinking_router)
app.include_router(websocket_router)

logger.info("✅ Memory API routes registered at /api/memory")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://192.168.1.18:3000",
        "http://192.168.1.18:5173",
        "http://192.168.1.18:8080",
        "ws://localhost:3000",
        "ws://127.0.0.1:3000",
        "ws://192.168.1.18:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("✅ CORS middleware configured for local network connections")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    from core.dependencies import broker, genai_client

    return {
        "status": "healthy",
        "service": "YUSR Unified Backend (Pub/Sub)",
        "version": "3.0.0",
        "broker": "running" if broker.running else "stopped",
        "transcription": "available (Google Gemini)" if genai_client else "unavailable",
        "tts": "available (Google Gemini TTS)" if genai_client else "unavailable",
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "YUSR Unified Backend",
        "description": "Multi-agent system with message broker",
        "architecture": "Publisher/Subscriber",
        "version": "3.0.0",
        "endpoints": {
            "/process": "POST - Process user input",
            "/transcribe": "POST - Transcribe audio to text",
            "/text-to-speech": "POST - Convert text to speech",
            "/reset": "POST - Reset conversation session",
            "/health": "GET - Service health check",
        },
        "agents": {
            "language": "Natural language understanding",
            "coordinator": "Task orchestration",
            "execution": "UI automation",
        },
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    logger.info(f"🚀 Starting server on 0.0.0.0:{port}")

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
        access_log=True,
    )
