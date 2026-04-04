import asyncio
import logging
import os
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
import base64
import tempfile
import time
import io
import wave
from pathlib import Path
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
# Use Google Gemini API for transcription and TTS
from google import genai
from google.genai import types
from gtts import gTTS
import tempfile
from pathlib import Path
import base64
import logging
# Import broker and agents
from agents.utils.broker import broker
from agents.language_agent import start_language_agent
from agents.coordinator_agent.coordinator_agent import start_coordinator_agent
from agents.reasoning_agent import start_reasoning_agent
# from agents.execution_agent.Coordinator import start_execution_agent
from agents.execution_agent.RAG.code_execution import initialize_execution_agent_for_server
from agents.execution_agent.strategies.task_memory import TaskMemory
from agents.utils.protocol import (
    AgentMessage, MessageType, AgentType, Channels,
    ClarificationMessage, StructuredResponse, ContextSnapshot, ResponseType
)
from ThinkingStepManager import ThinkingStepManager
from routes.device_routes import router as device_router
from dotenv import load_dotenv
import json
from memory_api import router as memory_router
from datetime import datetime, timezone
from face_auth import face_auth
import base64

# --- WebSocket connection manager ---
class ConnectionManager:
    """Manages active WebSocket connections per session"""
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}  # session_id -> ws
    
    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        # Close existing connection for same session if any
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

ws_manager = ConnectionManager()

# Context snapshot store (in-memory, could move to MongoDB)
context_snapshots: dict[str, ContextSnapshot] = {}

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Suppress noisy device-polling / HTTP-request logs ──
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Store pending responses (for HTTP API)
pending_responses = {}


def _preload_task_memory_embedding() -> None:
    """Warm TaskMemory + embedding model before first user task."""
    try:
        logger.info("🔥 Preloading TaskMemory embedding model...")
        mem = TaskMemory()
        mem._get_embedder()
        mem._embed(["startup warmup"])
        logger.info("✅ TaskMemory embedding model preloaded")
    except Exception as e:
        logger.warning(f"⚠️ TaskMemory preload skipped: {e}")

# Initialize Google Gemini API client using new SDK
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_KEY:
    logger.warning("❌ GEMINI_API_KEY not set - Gemini services may not work")

try:
    genai_client = genai.Client(api_key=GEMINI_KEY)
    logger.info("✅ Google Gemini client initialized")
except Exception as e:
    logger.error(f"❌ Failed to initialize Gemini client: {e}")
    genai_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""
    # Startup
    logger.info("🚀 Starting AURA Backend...")
    
    # Start broker
    await broker.start()
    logger.info("✅ Broker started")
    
    # Subscribe to output channels BEFORE starting agents
    broker.subscribe(Channels.LANGUAGE_OUTPUT, handle_language_output)
    broker.subscribe(Channels.COORDINATOR_TO_LANGUAGE, handle_coordinator_output)
    broker.subscribe(Channels.WEBSOCKET_OUTPUT, handle_ws_output)
    logger.info("✅ Subscribed to output channels")

    # Preload embedding model so first action task doesn't pay cold-start cost
    await asyncio.to_thread(_preload_task_memory_embedding)
    
    # Start all agents as background tasks (don't wait for them)
    try:
        logger.info("🚀 Starting Language Agent...")
        asyncio.create_task(start_language_agent(broker))
        await asyncio.sleep(0.1)  # Allow task to register
        
        logger.info("🚀 Starting Coordinator Agent...")
        asyncio.create_task(start_coordinator_agent(broker))
        await asyncio.sleep(0.1)
        
        logger.info("🚀 Starting Reasoning Agent...")
        asyncio.create_task(start_reasoning_agent())
        await asyncio.sleep(0.1)
        
        logger.info("🚀 Starting Execution Agent...")
        asyncio.create_task(initialize_execution_agent_for_server(broker))
        await asyncio.sleep(0.1)
        
        logger.info("✅ All agents scheduled successfully")
    except Exception as e:
        logger.error(f"❌ Error starting agents: {e}", exc_info=True)
    
    # Yield control back to FastAPI so server can start
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down AURA Backend...")
    await broker.stop()
    logger.info("✅ Broker stopped")


app = FastAPI(
    title="AURA Unified Backend (Pub/Sub)",
    description="Multi-agent system with message broker",
    version="3.0.0",
    lifespan=lifespan
)

# Include device routes
app.include_router(device_router)
app.include_router(memory_router)
logger.info("✅ Memory API routes registered at /api/memory")

# CORS for Electron and WebSocket (allow local network)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # React default
        "http://localhost:5173",  # Vite default
        "http://localhost:8080",  # Alternative
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://192.168.1.18:3000",  # Local network dev machine
        "http://192.168.1.18:5173",
        "http://192.168.1.18:8080",
        "ws://localhost:3000",  # WebSocket
        "ws://127.0.0.1:3000",
        "ws://192.168.1.18:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("✅ CORS middleware configured for local network connections")


# ============================================================================
# HTTP API Endpoints
# ============================================================================


logger = logging.getLogger(__name__)

@app.post("/text-to-speech")
async def text_to_speech(request: Request):
    """
    Convert text to speech using gTTS (Google Text-to-Speech)
    
    ✅ Supports both Arabic and English
    ✅ Auto-detects language
    ✅ Always works (no API quota issues)
    ✅ Returns base64-encoded audio
    """
    try:
        logger.info("🔊 Received TTS request")
        
        data = await request.json()
        text = data.get("text", "").strip()
        lang = data.get("lang", None)  # None = auto-detect
        
        if not text:
            logger.error("❌ No text provided for TTS")
            raise HTTPException(status_code=400, detail="Missing 'text' field")
        
        logger.info(f"🗣️ Generating speech for: '{text[:50]}...'")
        
        # ── Always detect language from the actual text content ────────────
        # Bug: the frontend passes lang='ar-EG' from stale localStorage even
        # when the text is English (thinking steps, completion messages).
        # gTTS then reads English text with an Arabic voice → garbled audio.
        # Fix: derive language from character ratio first; use the caller hint
        # only when the text is too short/ambiguous to detect confidently.
        arabic_chars = sum(1 for ch in text if '\u0600' <= ch <= '\u06FF')
        total_chars = max(len(text.replace(' ', '')), 1)
        arabic_ratio = arabic_chars / total_chars

        if arabic_ratio > 0.15:          # clear Arabic majority
            lang = 'ar'
        elif arabic_ratio == 0.0:         # zero Arabic characters → English
            lang = 'en'
        else:                             # ambiguous — trust the caller hint
            hint = (lang or 'en').lower().strip()
            lang = 'ar' if hint.startswith('ar') else 'en'

        tld = 'com.eg' if lang == 'ar' else 'com'
        logger.info(
            f"🌐 TTS language: {lang} "
            f"(arabic_ratio={arabic_ratio:.2f}, caller_hint={data.get('lang', 'none')})"
        )
        
        # Generate TTS with gTTS
        # slow=False for natural speed
        tts = gTTS(text=text, lang=lang, tld=tld, slow=False)
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
            tts.save(tmp_audio.name)
            tmp_path = tmp_audio.name
        
        try:
            # Read the audio file
            with open(tmp_path, "rb") as audio_file:
                audio_bytes = audio_file.read()
            
            logger.info(f"✅ Generated {len(audio_bytes)} bytes of audio")
            
            # Encode to base64
            base64_audio = base64.b64encode(audio_bytes).decode('utf-8')
            logger.info(f"✅ Encoded {len(base64_audio)} base64 characters")
            
            return {
                "status": "success",
                "audio_data": base64_audio,
                "format": "mp3",
                "language": lang,
                "provider": "gtts"
            }
            
        finally:
            # Cleanup temp file
            try:
                Path(tmp_path).unlink()
                logger.info(f"🗑️ Cleaned up temp file: {tmp_path}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to delete temp file: {e}")
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ TTS error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")

@app.post("/transcribe")
async def transcribe_audio(request: Request):
    """
    Transcribe audio using Google Gemini with file upload.
    Supports bilingual Arabic/English transcription.
    Accepts base64-encoded audio bytes.
    """
    try:
        logger.info("🎤 Received audio transcription request")

        if not genai_client:
            logger.error("❌ Google Gemini client not initialized")
            raise HTTPException(status_code=500, detail="Transcription service not available - GEMINI_API_KEY not set")

        data = await request.json()
        audio_data = data.get("audio_data", "")
        session_id = data.get("session_id", "default")

        if not audio_data:
            raise HTTPException(status_code=400, detail="Missing 'audio_data'")

        audio_bytes = base64.b64decode(audio_data)
        logger.info(f"📦 Received {len(audio_bytes)} bytes of audio data")

        # Save to temp file for Gemini upload
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            # Upload file to Gemini using new SDK
            logger.info(f"📤 Uploading audio file to Gemini: {tmp_path}")
            uploaded_file = genai_client.files.upload(file=tmp_path)
            logger.info(f"✅ File uploaded with URI: {uploaded_file.uri}")

            # Transcription prompt for bilingual support
            prompt = """
You are a speech transcription system designed for bilingual users (Arabic and English).

Your task:
1. Accurately transcribe the audio exactly as spoken.
2. The speech may contain a mix of Arabic and English words.
3. If the speaker mentions a command such as "open calculator", "open WhatsApp", "افتح calculator", etc.:
   - Detect the app or command name, even if the rest of the sentence is Arabic.
   - Keep the **app or command name in English**, exactly as said (for example: "افتح calculator").
4. Do NOT translate any Arabic words.
5. Do NOT invent or guess missing words.
6. If the audio is silent, unclear, or contains no recognizable speech, respond exactly with:
   "Couldn't catch that. Please try again."

Formatting rules:
- Return ONLY the final transcript text, nothing else.
- No punctuation or additional commentary.
- Keep mixed-language sentences exactly as spoken.

Examples:
Arabic only → "افتح الكاميرا"
Mixed → "افتح calculator"
English → "open calendar"
Silent → "Couldn't catch that. Please try again."
"""

            # Generate transcript using new SDK
            logger.info("🔄 Sending to Gemini for transcription...")
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[prompt, uploaded_file]
            )

            transcript = response.text.strip() if response.text else ""

            if not transcript or len(transcript) < 2:
                logger.warning("⚠️ Empty or silent audio detected")
                transcript = "Couldn't catch that. Please try again."

            logger.info(f"📝 TRANSCRIBED TEXT: '{transcript}'")

            return {"status": "success", "transcript": transcript, "session_id": session_id}

        finally:
            # Cleanup temp file
            try:
                Path(tmp_path).unlink()
                logger.info(f"🗑️ Cleaned up temp file: {tmp_path}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to delete temp file: {e}")

    except Exception as e:
        logger.error(f"❌ Transcription error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")




@app.post("/session/new")
async def create_new_session(request: Request):
    """Create a new chat session and clear short-term memory"""
    try:
        data = await request.json()
        old_session_id = data.get("old_session_id")
        new_session_id = data.get("new_session_id")
        user_id = data.get("user_id", "test_user")
        
        logger.info(f"🔄 Creating new session: {old_session_id} → {new_session_id}")
        
        # Clear Language Agent's conversation history
        from agents.language_agent import active_agents
        agent_key = f"{user_id}_{old_session_id}"
        
        if agent_key in active_agents:
            logger.info(f"🗑️ Clearing conversation for {agent_key}")
            active_agents[agent_key].clear_conversation()
            # Remove old agent
            del active_agents[agent_key]
            logger.info(f"✅ Cleared and removed agent: {agent_key}")
        else:
            logger.info(f"ℹ️ No active agent found for {agent_key}")
        
        # ✅ FIX: Send session control message to Coordinator to clear LangGraph checkpoint
        try:
            session_control_msg = AgentMessage(
                message_type=MessageType.STATUS_UPDATE,
                sender=AgentType.LANGUAGE,
                receiver=AgentType.COORDINATOR,
                session_id=old_session_id,
                payload={
                    "command": "start_new_chat",
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id
                }
            )
            
            # Publish to session control channel (Coordinator listens to this)
            await broker.publish(Channels.SESSION_CONTROL, session_control_msg)
            logger.info(f"✅ Sent session control message to Coordinator")
        except Exception as e:
            logger.warning(f"⚠️ Failed to send session control message: {e}")
        
        return {
            "status": "success",
            "old_session_id": old_session_id,
            "new_session_id": new_session_id,
            "message": "New chat session created. Short-term memory cleared."
        }
        
    except Exception as e:
        logger.error(f"❌ Session creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/process")
async def process_user_input(request: Request):
    """
    Main endpoint for user input
    
    Flow: HTTP → Language Agent → Coordinator → Execution → HTTP Response
    """
    try:
        data = await request.json()
        session_id = data.get("session_id", "default")
        user_input = data.get("input", "").strip()
        is_clarification = data.get("is_clarification", False)
        device_type = data.get("device_type", "mobile")
        user_id = data.get("user_id", "test_user")
        # Detect language from the actual input text — more reliable than the
        # frontend's stored value which may be stale from a prior session
        def _detect_lang_http(text: str) -> str:
            if not text:
                return "en"
            arabic_chars = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
            ratio = arabic_chars / max(len(text.replace(" ", "")), 1)
            return "ar" if ratio > 0.15 else "en"
        user_language = _detect_lang_http(user_input) or data.get("user_language", "en")
        
        if not user_input:
            raise HTTPException(status_code=400, detail="Missing 'input' field")
        
        logger.info(f"📥 HTTP request from session {session_id}: {user_input}")
        logger.info(f"📱 Device type: {device_type}")
        
        # Create message
        if is_clarification:
            message = AgentMessage(
                message_type=MessageType.CLARIFICATION_RESPONSE,
                sender=AgentType.LANGUAGE,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                payload={"answer": user_input, "input": user_input, "device_type": device_type, "user_id": user_id, "user_language": user_language}
            )
        else:
            message = AgentMessage(
                message_type=MessageType.TASK_REQUEST,
                sender=AgentType.LANGUAGE,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                payload={"input": user_input, "device_type": device_type,"user_id": user_id, "user_language": user_language}
            )
        
        logger.info(f"⏳ Creating pending response for message ID: {message.message_id}")
        future = asyncio.Future()
        pending_responses[message.message_id] = future
        logger.info(f"📝 Registered pending response. Total pending: {len(pending_responses)}")
        logger.info(f"📝 Pending IDs: {list(pending_responses.keys())}")
        
        # NEW: Send initial thinking update in the user's preferred language
        await ThinkingStepManager.update_step(
            session_id, "processing_input", message.message_id, language=user_language
        )
        
        logger.info(f"📤 Publishing message to {Channels.LANGUAGE_INPUT}")
        await broker.publish(Channels.LANGUAGE_INPUT, message)
        
        logger.info(f"⏰ Waiting up to 60s for response...")
        try:
            response = await asyncio.wait_for(future, timeout=60.0)
            logger.info(f"✅ Response received: {response}")
            
            # NEW: Clear thinking steps
            await ThinkingStepManager.clear_steps(session_id)
            
            return response
        except asyncio.TimeoutError:
            logger.error(f"❌ TIMEOUT waiting for response to message: {message.message_id}")
            logger.error(f"❌ Pending responses at timeout: {list(pending_responses.keys())}")
            await ThinkingStepManager.clear_steps(session_id)
            raise HTTPException(status_code=504, detail="Request timeout")
        finally:
            if message.message_id in pending_responses:
                pending_responses.pop(message.message_id)
                logger.info(f"🗑️ Cleaned up pending response: {message.message_id}")
    
    except asyncio.TimeoutError:
        logger.error("❌ Request timeout - already handled in main flow")
        raise HTTPException(status_code=504, detail="Request timeout")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



# ============================================================================
# USER ONBOARDING & ACCOUNT MANAGEMENT
# ============================================================================

class OnboardingData(BaseModel):
    user_id: str
    username: str
    password: str
    introduction: str
    preferences: dict

@app.post("/onboarding/create-account")
async def create_account(data: OnboardingData):
    """
    Creates a new user account after onboarding.
    Stores username, hashed password, intro, and preferences in MongoDB.
    """
    try:
        from pymongo import MongoClient
        import hashlib, os
        from dotenv import load_dotenv
        load_dotenv()

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        users_col = db["users"]

        # Check for duplicate username
        existing_user = users_col.find_one({"username": data.username})
        if existing_user:
            logger.warning(f"Duplicate username attempt: {data.username}")
            raise HTTPException(status_code=409, detail="Username already taken")

        # Check if user_id already exists (shouldn't, but just in case)
        existing_by_id = users_col.find_one({"user_id": data.user_id})
        if existing_by_id:
            logger.warning(f"Duplicate user_id attempt: {data.user_id}")
            # This is a conflict - user_id should be unique
            raise HTTPException(status_code=409, detail="User ID already exists")

        # Hash password (optional, could be empty string if using face only)
        hashed_pw = hashlib.sha256(data.password.encode()).hexdigest() if data.password else ""

        user_doc = {
            "user_id": data.user_id,
            "username": data.username,
            "password_hash": hashed_pw,
            "introduction": data.introduction,
            "preferences": data.preferences,
            "created_at": datetime.utcnow().isoformat(),
            "onboarding_complete": True,
            "has_face_auth": True  # Since they registered face during onboarding
        }

        users_col.insert_one(user_doc)
        logger.info(f"✅ Account created for user_id: {data.user_id}, username: {data.username}")

        # Store user profile into Mem0 for agent personalization
        try:
            from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
            pref_mgr = get_preference_manager(data.user_id)

            # Store preferred language as a preference
            language_pref = data.preferences.get("language", "English")
            lang_code = "ar" if "عرب" in language_pref or language_pref.lower() == "arabic" else "en"
            pref_mgr.add_preference(
                f"User's preferred language is {language_pref} ({lang_code})",
                metadata={"category": "language_preference", "source": "onboarding", "lang_code": lang_code}
            )

            # Store user introduction (contains profession/interests)
            if data.introduction:
                pref_mgr.add_preference(
                    data.introduction,
                    metadata={"category": "personal_info", "source": "onboarding"}
                )

            # Store theme preference
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
    except Exception as e:
        logger.error(f"❌ Account creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/onboarding/check-username")
async def check_username(username: str):
    """Check if a username is available."""
    try:
        from pymongo import MongoClient
        import os
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        exists = db["users"].find_one({"username": username}) is not None
        return {"available": not exists}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


"""
Fixed message handlers for server.py
Replace your handle_language_output and handle_coordinator_output functions with these
"""

async def handle_language_output(message):
    """Handle output from Language Agent - supports both dict and AgentMessage"""
    
    # Normalize to AgentMessage if it's a dict
    if isinstance(message, dict):
        logger.warning("⚠️ Received dict instead of AgentMessage from Language Agent, converting...")
        message = AgentMessage(**message)
    
    logger.info(f"📨 Received from Language Agent: {message.message_type}")
    logger.info(f"📋 Message ID: {message.message_id}")
    logger.info(f"📋 Response to: {message.response_to}")
    logger.info(f"📋 Payload: {message.payload}")
    logger.info(f"📋 Current pending responses: {list(pending_responses.keys())}")
    
    if message.message_type == MessageType.CLARIFICATION_REQUEST:
        response_content = {
            "status": "clarification_needed",
            "question": message.payload.get("question", "Need more information."),
            "response_id": message.message_id
        }
        
        logger.info(f"❓ Clarification needed: {response_content['question']}")
        
        target_id = message.response_to
        logger.info(f"🔍 Looking for pending response with ID: {target_id}")
        
        if target_id and target_id in pending_responses:
            logger.info(f"✅ FOUND! Resolving pending request: {target_id}")
            pending_responses[target_id].set_result(response_content)
            logger.info(f"✅ Response resolved successfully")
        else:
            logger.error(f"❌ NO PENDING RESPONSE FOUND for: {target_id}")
    
    elif message.message_type == MessageType.TASK_RESPONSE:
        response_content = {
            "status": message.payload.get("status", "completed"),
            "text": message.payload.get("response", "Task completed"),
            "task_id": message.task_id,
            "structured_response": message.payload.get("structured_response"),
            "followup_action": message.payload.get("followup_action"),
            "user_language": message.payload.get("user_language"),
        }
        
        logger.info(f"✅ Task response from Language Agent: {response_content}")
        
        target_id = message.response_to
        if target_id and target_id in pending_responses:
            logger.info(f"✅ Resolving pending request: {target_id}")
            pending_responses[target_id].set_result(response_content)
        else:
            logger.error(f"❌ NO PENDING RESPONSE FOUND for: {target_id}")


async def handle_coordinator_output(message):
    """Handle output from Coordinator - supports both dict and AgentMessage"""
    
    # Normalize to AgentMessage
    if isinstance(message, dict):
        logger.warning("⚠️ Received dict from Coordinator, converting...")
        try:
            message = AgentMessage(**message)
        except Exception as e:
            logger.error(f"❌ Failed to convert: {e}")
            logger.error(f"❌ Dict content: {message}")
            return
    
    logger.info(f"📨 Coordinator → Server: {message.message_type}")
    
    if message.message_type == MessageType.TASK_RESPONSE:
        result = message.payload
        
        # Extract spoken text safely (never fall back to raw details JSON)
        response_text = result.get("response") or "Task completed"
        
        # Extract structured response if present
        structured_response = result.get("structured_response")
        
        # Prefer spoken_text from structured response over raw text
        if structured_response and structured_response.get("spoken_text"):
            response_text = structured_response["spoken_text"]
        follow_up_question = result.get("follow_up_question")
        has_follow_up_question = bool(follow_up_question and str(follow_up_question).strip())

        user_language = result.get("user_language") or (structured_response or {}).get("user_language")

        try:
            from agents.language_agent import get_agent_for_session
            agent = get_agent_for_session(message.session_id)
            if agent:
                expects_reply = bool(
                    result.get("status") == "clarification_needed"
                    or has_follow_up_question
                    or (structured_response and structured_response.get("offer_read_aloud"))
                    or response_text.strip().endswith("?")
                )
                agent.remember_assistant_output(
                    follow_up_question if has_follow_up_question else response_text,
                    expects_reply=expects_reply,
                    metadata={
                        "structured_response": structured_response,
                        "status": result.get("status"),
                    }
                )
                if user_language:
                    agent.preferred_language = user_language
        except Exception as e:
            logger.warning(f"⚠️ Failed to sync coordinator response into language session memory: {e}")
        
        response = {
            "status": "clarification_needed" if has_follow_up_question else result.get("status", "completed"),
            "task_id": message.task_id,
            "text": response_text,  # This goes to TTS
            "question": follow_up_question if has_follow_up_question else (response_text if result.get("status") == "clarification_needed" else None),
            "response_id": message.message_id if has_follow_up_question else None,
            "result": result,
            "structured_response": structured_response,  # Forward for WS structured delivery
            "user_language": user_language,
        }
        
        logger.info(f"✅ Task completed, sending to TTS: '{response_text}'")
        
        target_id = message.response_to
        if target_id and target_id in pending_responses:
            logger.info(f"✅ Resolving pending request: {target_id}")
            pending_responses[target_id].set_result(response)
        else:
            logger.warning(f"⚠️ No pending response for {target_id}, trying fallback...")


async def handle_ws_output(message):
    """Route broker messages to WebSocket clients"""
    if isinstance(message, dict):
        message = AgentMessage(**message)
    session_id = message.session_id
    if session_id:
        await ws_manager.send_to_session(session_id, {
            "type": message.payload.get("ws_type", "message"),
            **message.payload
        })


# ============================================================================
# WebSocket Endpoint — replaces blocking POST /process for real-time comms
# ============================================================================

# Interrupt command mapping (English + Arabic)
INTERRUPT_COMMANDS = {
    # English
    "stop": "stop", "cancel": "stop", "abort": "stop",
    "aura stop": "stop", "aura cancel": "stop",
    "pause": "pause", "wait": "pause", "hold on": "pause",
    "aura pause": "pause", "aura wait": "pause",
    "continue": "resume", "go on": "resume", "resume": "resume",
    "aura continue": "resume", "aura resume": "resume",
    "undo": "undo", "undo that": "undo", "go back": "undo",
    "aura undo": "undo",
    "redo": "retry", "try again": "retry",
    "aura redo": "retry",
    # Arabic
    "أورا وقف": "stop", "وقف": "stop", "أوقف": "stop", "إلغاء": "stop",
    "أورا انتظر": "pause", "انتظر": "pause",
    "أورا استمر": "resume", "استمر": "resume",
    "أورا تراجع": "undo", "تراجع": "undo",
    "أورا أعد": "retry", "أعد": "retry",
}

def detect_interrupt(text: str):
    """Detect interrupt commands in text (case-insensitive, partial match)"""
    text_lower = text.strip().lower()
    # Exact match first
    if text_lower in INTERRUPT_COMMANDS:
        return INTERRUPT_COMMANDS[text_lower]
    # Partial: check if any command is at the start of the text
    for cmd, action in INTERRUPT_COMMANDS.items():
        if text_lower.startswith(cmd):
            return action
    return None


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Bidirectional WebSocket for real-time communication.
    
    Client → Server messages:
      { type: "user_input", text: "...", device_type: "...", user_id: "..." }
      { type: "interrupt", command: "stop|pause|resume|undo|retry" }
      { type: "clarification_response", answer: "...", user_id: "..." }
    
    Server → Client messages:
      { type: "thinking", step: "..." }
      { type: "task_progress", task_id: "...", status: "..." }
      { type: "clarification_needed", question: "..." }
      { type: "response_complete", text: "...", ... }
      { type: "proactive_prompt", suggestion: "...", offer_actions: [...] }
      { type: "interrupt_ack", message: "...", options: [...] }
      { type: "context_saved", snapshot_id: "..." }
    """
    await ws_manager.connect(session_id, websocket)
    
    # Subscribe a session-specific handler for broker broadcasts
    async def ws_broadcast_handler(message):
        if isinstance(message, dict):
            try:
                message = AgentMessage(**message)
            except Exception:
                return
        if message.session_id == session_id or message.session_id is None:
            payload = message.payload or {}
            # Route thinking updates
            if payload.get("action") == "thinking_update":
                await ws_manager.send_to_session(session_id, {
                    "type": "thinking_step",
                    "step": payload.get("step", ""),
                    "language": payload.get("language", "en"),
                })
            elif payload.get("action") == "thinking_clear":
                await ws_manager.send_to_session(session_id, {
                    "type": "thinking_clear"
                })
            # Route task progress
            elif payload.get("ws_type") == "task_progress":
                await ws_manager.send_to_session(session_id, payload)
    
    broker.subscribe(Channels.BROADCAST, ws_broadcast_handler)
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            
            # --- INTERRUPT COMMAND ---
            if msg_type == "interrupt":
                command = data.get("command", "stop")
                logger.info(f"🛑 WebSocket interrupt: {command} for session {session_id}")
                
                if command == "undo":
                    # Check for context snapshot
                    snap = context_snapshots.get(session_id)
                    if snap and snap.is_reversible:
                        await ws_manager.send_to_session(session_id, {
                            "type": "interrupt_ack",
                            "command": "undo",
                            "message": f"Undone. {len(snap.completed_tasks)} tasks were rolled back.",
                            "snapshot_id": snap.snapshot_id,
                            "options": ["resume", "discard"]
                        })
                    else:
                        await ws_manager.send_to_session(session_id, {
                            "type": "interrupt_ack",
                            "command": "undo",
                            "message": "Nothing to undo.",
                            "options": []
                        })
                    continue
                
                # Send interrupt to coordinator
                interrupt_msg = AgentMessage(
                    message_type=MessageType.INTERRUPT_COMMAND,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.COORDINATOR,
                    session_id=session_id,
                    payload={"command": command}
                )
                await broker.publish(Channels.INTERRUPT_CONTROL, interrupt_msg)
                
                # Save context snapshot on stop
                if command == "stop":
                    from agents.coordinator_agent.coordinator_agent import task_queue
                    snapshot = ContextSnapshot(
                        session_id=session_id,
                        user_id=data.get("user_id", "unknown"),
                        original_request=data.get("original_request", ""),
                        pending_tasks=[],
                        is_reversible=False
                    )
                    context_snapshots[session_id] = snapshot
                
                await ws_manager.send_to_session(session_id, {
                    "type": "interrupt_ack",
                    "command": command,
                    "message": f"Command '{command}' executed.",
                    "options": ["resume", "undo", "discard"] if command == "stop" else []
                })
                continue
            
            # --- USER INPUT ---
            if msg_type == "user_input":
                user_text = data.get("text", "").strip()
                device_type = data.get("device_type", "desktop")
                user_id = data.get("user_id", "test_user")

                # ── Detect language from the ACTUAL input text ────────────────
                # The frontend sends user_language from localStorage, which can be
                # stale from a previous session in a different language.
                # Detecting from the input text itself is always more accurate.
                def _detect_lang(text: str) -> str:
                    if not text:
                        return "en"
                    arabic_chars = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
                    ratio = arabic_chars / max(len(text.replace(" ", "")), 1)
                    return "ar" if ratio > 0.15 else "en"

                detected_language = _detect_lang(user_text)
                # Frontend hint is used only as tiebreaker when detection is ambiguous
                frontend_hint = data.get("user_language", "en")
                user_language = detected_language if detected_language in ("en", "ar") else frontend_hint
                
                if not user_text:
                    continue
                
                # Check for interrupt commands in text input
                interrupt_action = detect_interrupt(user_text)
                if interrupt_action:
                    logger.info(f"🛑 Voice/text interrupt detected: '{user_text}' → {interrupt_action}")
                    interrupt_msg = AgentMessage(
                        message_type=MessageType.INTERRUPT_COMMAND,
                        sender=AgentType.LANGUAGE,
                        receiver=AgentType.COORDINATOR,
                        session_id=session_id,
                        payload={"command": interrupt_action}
                    )
                    await broker.publish(Channels.INTERRUPT_CONTROL, interrupt_msg)
                    
                    if interrupt_action == "stop":
                        snapshot = ContextSnapshot(
                            session_id=session_id,
                            user_id=user_id,
                            original_request=user_text,
                            is_reversible=False
                        )
                        context_snapshots[session_id] = snapshot
                    
                    await ws_manager.send_to_session(session_id, {
                        "type": "interrupt_ack",
                        "command": interrupt_action,
                        "message": f"Command '{interrupt_action}' received.",
                        "options": ["resume", "undo", "discard"] if interrupt_action in ("stop", "pause") else []
                    })
                    continue
                
                # Normal input → publish to Language Agent
                message = AgentMessage(
                    message_type=MessageType.TASK_REQUEST,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    payload={
                        "input": user_text,
                        "device_type": device_type,
                        "user_id": user_id,
                        "user_language": user_language,
                    }
                )
                
                # Create pending response (same mechanism as HTTP)
                future = asyncio.Future()
                pending_responses[message.message_id] = future
                
                # First thinking step uses the language detected from actual input text,
                # not from localStorage — this prevents the Arabic-session stale-language bug.
                await ThinkingStepManager.update_step(
                    session_id, "processing_input", message.message_id, language=user_language
                )
                await broker.publish(Channels.LANGUAGE_INPUT, message)
                
                # Wait for response asynchronously (don't block WebSocket read loop)
                async def wait_and_send(msg_id, fut):
                    try:
                        response = await asyncio.wait_for(fut, timeout=60.0)
                        await ThinkingStepManager.clear_steps(session_id)
                        
                        # Detect structured response
                        ws_response = {"type": "completion"}
                        if isinstance(response, dict):
                            if response.get("status") == "clarification_needed":
                                structured = response.get("structured_response") or {}
                                ws_response = {
                                    "type": "clarification",
                                    "question": response.get("question", ""),
                                    "response_id": response.get("response_id", ""),
                                    "user_language": response.get("user_language"),
                                    "text": response.get("text", ""),
                                    "spoken_text": response.get("text", ""),
                                    "structured_response": structured,
                                    "full_content": structured.get("full_content", ""),
                                    "offer_read_aloud": structured.get("offer_read_aloud", False),
                                    "offer_actions": structured.get("offer_actions", []),
                                }
                            else:
                                # Check for structured response with proactive prompts
                                structured = response.get("structured_response")
                                if structured:
                                    spoken = structured.get("spoken_text", response.get("text", "Task completed"))
                                    ws_response = {
                                        "type": "completion",
                                        "spoken_text": spoken,
                                        "text": spoken,
                                        "full_content": structured.get("full_content", ""),
                                        "offer_read_aloud": structured.get("offer_read_aloud", False),
                                        "offer_actions": structured.get("offer_actions", []),
                                        "structured_response": structured,
                                        "status": response.get("status", "completed"),
                                        "task_id": response.get("task_id"),
                                        "user_language": response.get("user_language") or structured.get("user_language"),
                                    }
                                else:
                                    spoken = response.get("text", "Task completed")
                                    ws_response = {
                                        "type": "completion",
                                        "spoken_text": spoken,
                                        "text": spoken,
                                        "status": response.get("status", "completed"),
                                        "task_id": response.get("task_id"),
                                        "user_language": response.get("user_language"),
                                    }
                        
                        await ws_manager.send_to_session(session_id, ws_response)
                    except asyncio.TimeoutError:
                        await ThinkingStepManager.clear_steps(session_id)
                        await ws_manager.send_to_session(session_id, {
                            "type": "error",
                            "message": "Request timed out"
                        })
                    finally:
                        pending_responses.pop(msg_id, None)
                
                asyncio.create_task(wait_and_send(message.message_id, future))
                continue
            
            # --- CLARIFICATION RESPONSE ---
            if msg_type == "clarification_response":
                answer = data.get("answer", "").strip()
                user_id = data.get("user_id", "test_user")
                device_type = data.get("device_type", "desktop")
                user_language = data.get("user_language", "en")
                
                if not answer:
                    continue
                
                # Check for interrupts even in clarification
                interrupt_action = detect_interrupt(answer)
                if interrupt_action:
                    interrupt_msg = AgentMessage(
                        message_type=MessageType.INTERRUPT_COMMAND,
                        sender=AgentType.LANGUAGE,
                        receiver=AgentType.COORDINATOR,
                        session_id=session_id,
                        payload={"command": interrupt_action}
                    )
                    await broker.publish(Channels.INTERRUPT_CONTROL, interrupt_msg)
                    await ws_manager.send_to_session(session_id, {
                        "type": "interrupt_ack",
                        "command": interrupt_action,
                        "message": f"Command '{interrupt_action}' received.",
                        "options": []
                    })
                    continue
                
                message = AgentMessage(
                    message_type=MessageType.CLARIFICATION_RESPONSE,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    payload={
                        "answer": answer,
                        "input": answer,
                        "device_type": device_type,
                        "user_id": user_id,
                        "user_language": user_language,
                    }
                )
                
                future = asyncio.Future()
                pending_responses[message.message_id] = future
                await broker.publish(Channels.LANGUAGE_INPUT, message)
                
                async def wait_clarification(msg_id, fut):
                    try:
                        response = await asyncio.wait_for(fut, timeout=60.0)
                        ws_resp = {"type": "response_complete"}
                        if isinstance(response, dict):
                            if response.get("status") == "clarification_needed":
                                structured = response.get("structured_response") or {}
                                ws_resp = {
                                    "type": "clarification_needed",
                                    "question": response.get("question", ""),
                                    "response_id": response.get("response_id", ""),
                                    "user_language": response.get("user_language"),
                                    "text": response.get("text", ""),
                                    "spoken_text": response.get("text", ""),
                                    "structured_response": structured,
                                    "full_content": structured.get("full_content", ""),
                                    "offer_read_aloud": structured.get("offer_read_aloud", False),
                                    "offer_actions": structured.get("offer_actions", []),
                                }
                            else:
                                structured = response.get("structured_response")
                                if structured and structured.get("offer_read_aloud"):
                                    ws_resp = {
                                        "type": "proactive_prompt",
                                        "text": structured.get("spoken_text", response.get("text", "")),
                                        "full_content": structured.get("full_content", ""),
                                        "offer_read_aloud": True,
                                        "offer_actions": structured.get("offer_actions", []),
                                        "status": response.get("status", "completed"),
                                    }
                                else:
                                    ws_resp = {
                                        "type": "response_complete",
                                        "text": response.get("text", "Task completed"),
                                        "status": response.get("status", "completed"),
                                    }
                        await ws_manager.send_to_session(session_id, ws_resp)
                    except asyncio.TimeoutError:
                        await ws_manager.send_to_session(session_id, {
                            "type": "error", "message": "Request timed out"
                        })
                    finally:
                        pending_responses.pop(msg_id, None)
                
                asyncio.create_task(wait_clarification(message.message_id, future))
                continue
    
    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}", exc_info=True)
        ws_manager.disconnect(session_id)


@app.post("/reset")
async def reset_session(request: Request):
    """Reset a conversation session"""
    data = await request.json()
    session_id = data.get("session_id", "default")
    
    logger.info(f"🔄 Resetting session: {session_id}")
    
    # Broadcast reset message
    reset_msg = AgentMessage(
        message_type=MessageType.STATUS_UPDATE,
        sender=AgentType.LANGUAGE,
        receiver=AgentType.LANGUAGE,
        session_id=session_id,
        payload={"action": "reset"}
    )
    
    await broker.publish(Channels.BROADCAST, reset_msg)
    
    return {"status": "reset", "session_id": session_id}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "YUSR Unified Backend (Pub/Sub)",
        "version": "3.0.0",
        "broker": "running" if broker.running else "stopped",
        "transcription": "available (Google Gemini)" if genai_client else "unavailable",
        "tts": "available (Google Gemini TTS)" if genai_client else "unavailable"
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
            "/health": "GET - Service health check"
        },
        "agents": {
            "language": "Natural language understanding",
            "coordinator": "Task orchestration",
            "execution": "UI automation"
        }
    }

from fastapi.responses import StreamingResponse

@app.get("/thinking-stream/{session_id}")
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
                    # Forward the entire payload so clients can react to different actions
                    await thinking_queue.put(message.payload)
        
        # Subscribe to broadcast channel
        broker.subscribe(Channels.BROADCAST, handle_thinking_update)
        
        try:
            while True:
                try:
                    # Wait for thinking updates with timeout
                    payload = await asyncio.wait_for(thinking_queue.get(), timeout=30)
                    # Send full payload as JSON so clients can handle update/clear events
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    # Keep connection alive with heartbeat
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            logger.info(f"🔌 Client disconnected from thinking stream: {session_id}")
        except Exception as e:
            logger.error(f"❌ Thinking stream error: {e}")
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/new-chat")
async def new_chat_endpoint(request: dict):
    """Handle new chat creation - clear session state"""
    try:
        session_id = request.get("session_id")
        user_id = request.get("user_id", "test_user")
        
        logger.info(f"🔄 New chat requested - clearing session: {session_id}")
        
        # Clear language agent conversation for this session
        from agents.language_agent import active_agents
        agent_key = f"{user_id}_{session_id}"
        
        if agent_key in active_agents:
            active_agents[agent_key].clear_conversation()
            logger.info(f"✅ Cleared language agent for {agent_key}")
        else:
            logger.info(f"ℹ️ No active agent found for {agent_key}")
        
        return {"status": "success", "message": "New chat started", "session_id": session_id}
        
    except Exception as e:
        logger.error(f"❌ New chat error: {e}")
        return {"status": "error", "message": str(e)}, 500

class LoginData(BaseModel):
    username: str
    password: str

@app.post("/onboarding/login")
async def login(data: LoginData):
    try:
        from pymongo import MongoClient
        import hashlib, os
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@app.get("/chat-messages/{session_id}")
async def get_chat_messages(session_id: str, user_id: str):
    """
    Fetch all messages for a specific session, filtered by user_id.
    Returns only user and assistant messages (strips system prompt).
    """
    try:
        from pymongo import MongoClient
        import os
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        # IMPORTANT: Verify ownership with user_id - this ensures user isolation
        doc = db["language_agent_conversations"].find_one(
            {"session_id": session_id, "user_id": user_id},
            sort=[("timestamp", -1)]
        )

        if not doc or "messages" not in doc:
            logger.warning(f"No messages found for session {session_id} belonging to user {user_id}")
            return {"messages": [], "session_id": session_id}

        # Strip system messages, return only user/assistant
        import json as json_lib

        def clean_content(role, content):
            """Strip JSON wrapper from assistant messages stored by language agent."""
            if role != "assistant" or not isinstance(content, str):
                return content
            try:
                parsed = json_lib.loads(content)
                return (
                    parsed.get("response_text") or
                    parsed.get("text") or
                    parsed.get("response") or
                    content
                )
            except Exception:
                return content

        messages = [
            {
                "role": m["role"],
                "content": clean_content(m["role"], m.get("content", ""))
            }
            for m in doc["messages"]
            if m.get("role") in ("user", "assistant")
        ]
        
        logger.info(f"✅ Retrieved {len(messages)} messages for session {session_id} (user: {user_id})")
        return {"messages": messages, "session_id": session_id}

    except Exception as e:
        logger.error(f"❌ Failed to fetch chat messages: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 


# ============================================================================
# CHAT LIST, TITLES, AND HISTORY
# ============================================================================

@app.get("/chats/{user_id}")
async def get_user_chats(user_id: str):
    """
    Returns all chat sessions for a user, sorted by most recent.
    Reads directly from yusr_db.language_agent_conversations.
    Generates a title from the first user message if none is stored.
    """
    try:
        from pymongo import MongoClient
        import os
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        # IMPORTANT: Filter by user_id - ensures user isolation
        docs = list(
            db["language_agent_conversations"].find(
                {"user_id": user_id},  # This line ensures user isolation
                {"session_id": 1, "title": 1, "messages": 1, "timestamp": 1}
            ).sort("timestamp", -1)
        )

        chats = []
        for doc in docs:
            sid = doc.get("session_id")
            if not sid:
                continue

            # Use stored title or derive from first user message
            title = doc.get("title")
            if not title:
                messages = doc.get("messages", [])
                for m in messages:
                    if m.get("role") == "user":
                        content = m.get("content", "")
                        # Truncate to 40 chars as working title
                        title = content[:40] + ("..." if len(content) > 40 else "")
                        break
            if not title:
                title = "New Chat"

            chats.append({
                "session_id": sid,
                "title": title,
                "timestamp": doc.get("timestamp", 0)
            })

        logger.info(f"✅ Returning {len(chats)} chats for user {user_id}")
        return {"chats": chats}

    except Exception as e:
        logger.error(f"❌ Failed to get chats for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-chat-title")
async def update_chat_title(request: Request):
    """
    Saves a human-readable title for a session.
    Called by frontend after first AI response generates a title.
    """
    try:
        from pymongo import MongoClient
        import os
        data = await request.json()
        session_id = data.get("session_id")
        user_id = data.get("user_id")
        title = data.get("title", "").strip()

        if not session_id or not user_id or not title:
            raise HTTPException(status_code=400, detail="Missing session_id, user_id, or title")

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        db["language_agent_conversations"].update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"title": title}},
            upsert=False
        )

        logger.info(f"✅ Title updated for session {session_id}: '{title}'")
        return {"status": "ok", "title": title}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to update title: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-chat-title")
async def generate_chat_title(request: Request):
    """
    Generates a chat title.
    - summarize=False (default): called on first message, just truncates text
    - summarize=True: called after 4 exchanges, uses Gemini to summarize full conversation
    """
    try:
        data = await request.json()
        message = data.get("message", "").strip()
        summarize = data.get("summarize", False)
        session_id = data.get("session_id")
        user_id = data.get("user_id")

        if not summarize:
            # Fast path — just truncate first message, no API call
            title = message[:40] + ("..." if len(message) > 40 else "")
            return {"title": title or "New Chat"}

        # Summarize path — fetch full conversation and ask Gemini
        if not genai_client:
            title = message[:40] + ("..." if len(message) > 40 else "")
            return {"title": title or "New Chat"}

        # Get full conversation from MongoDB
        conversation_text = ""
        if session_id and user_id:
            try:
                from pymongo import MongoClient
                import os
                client = MongoClient(os.getenv("MONGODB_URI"))
                db = client["yusr_db"]
                doc = db["language_agent_conversations"].find_one(
                    {"session_id": session_id, "user_id": user_id}
                )
                if doc and "messages" in doc:
                    lines = []
                    for m in doc["messages"]:
                        if m.get("role") == "user":
                            lines.append(f"User: {m['content'][:100]}")
                        elif m.get("role") == "assistant":
                            # Parse response_text out of JSON if needed
                            content = m.get("content", "")
                            try:
                                parsed = json.loads(content)
                                content = parsed.get("response_text", content)
                            except Exception:
                                pass
                            lines.append(f"AURA: {content[:100]}")
                    conversation_text = "\n".join(lines[:10])  # first 10 turns max
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch conversation for title: {e}")

        if not conversation_text:
            title = message[:40] + ("..." if len(message) > 40 else "")
            return {"title": title or "New Chat"}

        prompt = f"""Summarize this conversation into a very short title of maximum 5 words.

{conversation_text}

Rules:
- Maximum 5 words
- No punctuation at the end
- No quotes
- Be specific about what was done or discussed
- Examples: "Open Notepad write preferences", "Search Amazon white socks", "Set 7am alarm"

Return ONLY the title, nothing else."""

        response = genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        title = response.text.strip().strip('"').strip("'")
        if len(title) > 50:
            title = title[:50]

        logger.info(f"✅ Summarized title: '{title}'")
        return {"title": title}

    except Exception as e:
        logger.error(f"❌ Title generation failed: {e}")
        return {"title": data.get("message", "New Chat")[:35] if 'data' in dir() else "New Chat"}


#face authentication endpoints

@app.post("/onboarding/register-face")
async def register_face(request: Request):
    """
    Register face biometrics for a user (signup flow)
    """
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        user_id = data.get("user_id", "")
        face_image = data.get("face_image", "")
        
        # Validate inputs
        if not username:
            raise HTTPException(status_code=400, detail="Username is required")
        
        if not face_image:
            raise HTTPException(status_code=400, detail="Face image is required")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        
        logger.info(f"Processing face registration for user: {username}")
        
        # Process the face image
        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)
        
        # Check if username already exists with face data
        if face_auth.get_user_face_status(username):
            # Optionally update existing face data
            success, msg = face_auth.store_face_data(user_id, username, encoding)
            if not success:
                raise HTTPException(status_code=500, detail=msg)
            
            return {
                "status": "success",
                "message": "Face biometrics updated successfully",
                "user_id": user_id,
                "username": username,
                "action": "updated"
            }
        else:
            # Store new face data
            success, msg = face_auth.store_face_data(user_id, username, encoding)
            if not success:
                raise HTTPException(status_code=500, detail=msg)
            
            return {
                "status": "success",
                "message": "Face biometrics registered successfully",
                "user_id": user_id,
                "username": username,
                "action": "registered"
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Face registration error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/onboarding/verify-face")
async def verify_face_login(request: Request):
    """
    Verify face biometrics for login
    """
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        face_image = data.get("face_image", "")
        
        # Validate inputs
        if not username:
            raise HTTPException(status_code=400, detail="Username is required")
        
        if not face_image:
            raise HTTPException(status_code=400, detail="Face image is required")
        
        logger.info(f"Verifying face for user: {username}")
        
        # Check if user has face data
        if not face_auth.get_user_face_status(username):
            raise HTTPException(
                status_code=404, 
                detail="No face biometrics found for this username. Please sign up first."
            )
        
        # Process the face image
        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)
        
        # Verify face
        verified, result = face_auth.verify_face(username, encoding)
        
        if not verified:
            raise HTTPException(status_code=401, detail=result)
        
        # Get user data from users collection
        from pymongo import MongoClient
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
    except Exception as e:
        logger.error(f"❌ Face verification error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/onboarding/face-status/{username}")
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
    except Exception as e:
        logger.error(f"❌ Error checking face status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/onboarding/face-data/{user_id}")
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
    except Exception as e:
        logger.error(f"❌ Error deleting face data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Add this to server.py (after other onboarding endpoints)

# Make sure numpy is imported at the top of the file (add this if missing)
import numpy as np

@app.post("/onboarding/login-face-only")
async def login_face_only(request: Request):
    """
    Login using ONLY face - no username required
    Uses strict thresholds for high security
    """
    try:
        data = await request.json()
        face_image = data.get("face_image", "")
        
        if not face_image:
            raise HTTPException(status_code=400, detail="Face image required")
        
        logger.info(f"Processing face-only login with strict security")
        
        # Process the face image with multiple passes for accuracy
        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)
        
        # Find user by face encoding (compare against all stored faces)
        from pymongo import MongoClient
        import numpy as np
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        
        # Get all users with face data
        all_face_users = list(db["face_auth_data"].find({}))
        
        if not all_face_users:
            logger.warning("No face data found in database")
            raise HTTPException(status_code=404, detail="No registered faces found. Please sign up first.")
        
        # Find the best match
        matches = []
        
        for user_data in all_face_users:
            stored_encrypted = user_data.get("face_encoding_data")
            if not stored_encrypted:
                continue
            
            stored_encoding = face_auth._verify_encoding(stored_encrypted)
            if stored_encoding is None:
                logger.warning(f"Corrupted face data for user {user_data.get('username')}")
                continue
            
            # Calculate distance
            stored_array = np.array(stored_encoding)
            current_array = np.array(encoding)
            distance = np.linalg.norm(stored_array - current_array)
            
            # Calculate confidence
            confidence_percent = face_auth.calculate_confidence(distance)
            
            matches.append({
                "user_data": user_data,
                "distance": distance,
                "confidence_percent": confidence_percent,
                "confidence": confidence_percent / 100
            })
            
            logger.info(f"Comparing with user {user_data['username']}: distance={distance:.4f}, confidence={confidence_percent:.1f}%")
        
        # Sort by distance (best first) and confidence (best first)
        matches.sort(key=lambda x: (x["distance"], -x["confidence_percent"]))
        
        if not matches:
            raise HTTPException(status_code=404, detail="No valid face data found")
        
        best_match = matches[0]
        
        # STRICT ACCEPTANCE CRITERIA
        is_acceptable = (
            best_match["distance"] <= face_auth.max_acceptable_distance and 
            best_match["confidence_percent"] >= face_auth.min_confidence_percent
        )
        
        if not is_acceptable:
            logger.warning(f"Face rejected. Best match: {best_match['user_data']['username']} with {best_match['confidence_percent']:.1f}% confidence (need > {face_auth.min_confidence_percent:.0f}%)")
            
            # Check if there's a second match that's also close (could indicate confusion)
            if len(matches) > 1 and matches[1]["distance"] - best_match["distance"] < 0.05:
                raise HTTPException(
                    status_code=401,
                    detail=f"Face ambiguous. Multiple similar faces detected. Please ensure good lighting and look directly at the camera."
                )
            
            raise HTTPException(
                status_code=401,
                detail=f"Face not recognized. Best match confidence: {best_match['confidence_percent']:.1f}% (need > {face_auth.min_confidence_percent:.0f}%). Please ensure good lighting and look directly at the camera."
            )
        
        # Get full user data
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
    except Exception as e:
        logger.error(f"❌ Face-only login error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/onboarding/register-face")
async def register_face(request: Request):
    """
    Register face biometrics with quality check
    """
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        user_id = data.get("user_id", "")
        face_image = data.get("face_image", "")
        
        # Validate inputs
        if not username:
            raise HTTPException(status_code=400, detail="Username is required")
        
        if not face_image:
            raise HTTPException(status_code=400, detail="Face image is required")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        
        logger.info(f"Processing face registration for user: {username}, user_id: {user_id}")
        
        # Process the face image with quality check
        encoding, message = face_auth.process_face_image(face_image)
        if not encoding:
            raise HTTPException(status_code=400, detail=message)
        
        # Check if this user_id is already associated with a different username
        from pymongo import MongoClient
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        
        existing_face = db["face_auth_data"].find_one({"user_id": user_id})
        if existing_face and existing_face.get("username") != username:
            logger.warning(f"User ID {user_id} is already associated with username {existing_face['username']}")
            raise HTTPException(
                status_code=409, 
                detail=f"User ID {user_id} is already associated with a different username. Please logout and try again."
            )
        
        # Verify face quality by checking if we can detect the face consistently
        # Process again with different parameters to ensure consistency
        encoding2, _ = face_auth.process_face_image(face_image)
        if not encoding2:
            raise HTTPException(status_code=400, detail="Could not reliably detect face. Please try again with better lighting.")
        
        # Store new face data
        success, msg = face_auth.store_face_data(user_id, username, encoding)
        if not success:
            raise HTTPException(status_code=500, detail=msg)
        
        logger.info(f"✅ Face registered successfully for {username} (user_id: {user_id})")
        
        return {
            "status": "success",
            "message": "Face biometrics registered successfully",
            "user_id": user_id,
            "username": username,
            "action": "registered"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Face registration error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    

    
@app.get("/debug/user-sessions/{user_id}")
async def debug_user_sessions(user_id: str):
    """
    Debug endpoint to list all sessions for a user
    (Remove in production or add authentication)
    """
    try:
        from pymongo import MongoClient
        import os
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/onboarding/cleanup-user/{user_id}")
async def cleanup_user_data(user_id: str):
    """
    Cleanup endpoint to remove all traces of a user
    Useful for debugging and testing
    """
    try:
        from pymongo import MongoClient
        import os
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["aura_db"]
        
        # Delete from users collection
        user_result = db["users"].delete_one({"user_id": user_id})
        
        # Delete from face_auth_data
        face_result = db["face_auth_data"].delete_one({"user_id": user_id})
        
        # Delete from conversations
        conv_result = db["language_agent_conversations"].delete_many({"user_id": user_id})
        
        return {
            "status": "success",
            "deleted": {
                "user": user_result.deleted_count,
                "face_data": face_result.deleted_count,
                "conversations": conv_result.deleted_count
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    logger.info(f"🚀 Starting server on 0.0.0.0:{port}")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=False  # ✅ Disable uvicorn's built-in HTTP access logging
    )