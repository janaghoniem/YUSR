import logging
import os
from dotenv import load_dotenv
from google import genai

from agents.utils.broker import broker
from agents.utils.protocol import ContextSnapshot
from routes.websocket_manager import ConnectionManager

load_dotenv()

logger = logging.getLogger(__name__)

pending_responses: dict[str, object] = {}
context_snapshots: dict[str, ContextSnapshot] = {}

AGENT_RESPONSE_TIMEOUT_SECONDS = float(os.getenv("AGENT_RESPONSE_TIMEOUT_SECONDS", "180"))
AGENT_CONFIRMATION_TIMEOUT_SECONDS = float(os.getenv("AGENT_CONFIRMATION_TIMEOUT_SECONDS", "300"))

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_KEY:
    logger.warning("❌ GEMINI_API_KEY not set - Gemini services may not work")

try:
    genai_client = genai.Client(api_key=GEMINI_KEY)
    logger.info("✅ Google Gemini client initialized")
except Exception as exc:
    logger.error(f"❌ Failed to initialize Gemini client: {exc}")
    genai_client = None

ws_manager = ConnectionManager()

__all__ = [
    "AGENT_CONFIRMATION_TIMEOUT_SECONDS",
    "AGENT_RESPONSE_TIMEOUT_SECONDS",
    "ContextSnapshot",
    "GEMINI_KEY",
    "broker",
    "context_snapshots",
    "genai_client",
    "logger",
    "pending_responses",
    "ws_manager",
]
