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
from agents.utils.protocol import (
    AgentMessage, MessageType, AgentType, Channels,
    ClarificationMessage, StructuredResponse, ContextSnapshot, ResponseType
)
from ThinkingStepManager import ThinkingStepManager
from routes.device_routes import router as device_router
from dotenv import load_dotenv
import json
from memory_api import router as memory_router

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

# Store pending responses (for HTTP API)
pending_responses = {}

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
    title="YUSR Unified Backend (Pub/Sub)",
    description="Multi-agent system with message broker",
    version="3.0.0",
    lifespan=lifespan
)

# Include device routes
app.include_router(device_router)
app.include_router(memory_router)
logger.info("✅ Memory API routes registered at /api/memory")

# CORS for Electron
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # React default
        "http://localhost:5173",  # Vite default
        "http://localhost:8080",  # Alternative
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("✅ CORS middleware configured")


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
        
        # Auto-detect language if not specified
        if not lang:
            # Simple heuristic: check if text contains Arabic characters
            if any('\u0600' <= char <= '\u06FF' for char in text):
                lang = 'ar'  # Arabic
                logger.info("🌐 Detected language: Arabic")
            else:
                lang = 'en'  # English
                logger.info("🌐 Detected language: English")
        else:
            logger.info(f"🌐 Using specified language: {lang}")

        normalized_lang = (lang or "").lower().strip()
        tld = "com"
        if normalized_lang.startswith("ar"):
            lang = "ar"
            tld = "com.eg"
        elif normalized_lang.startswith("en"):
            lang = "en"
            tld = "com"
        
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
        user_id = data.get("user_id", "test_user")  # ✅ ADD THIS LINE
        user_language = data.get("user_language", "en")
        
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
        
        # NEW: Send initial thinking update
        await ThinkingStepManager.update_step(session_id, "Processing input...", message.message_id)
        
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
                    "step": payload.get("step", "")
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
                user_language = data.get("user_language", "en")
                
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
                
                await ThinkingStepManager.update_step(session_id, "Processing input...", message.message_id)
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

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    logger.info(f"🚀 Starting server on 0.0.0.0:{port}")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )