import os, uuid, asyncio, json, re
from dotenv import load_dotenv   
from langgraph.graph import StateGraph, END
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field
from pymongo import MongoClient
from datetime import datetime
from collections import deque

# Imports for memory
from langgraph.checkpoint.mongodb import MongoDBSaver

import logging
from agents.utils.protocol import (
    Channels, AgentMessage, MessageType, AgentType, 
    ExecutionResult, TaskMessage,
    StructuredResponse, ResponseType, ContextSnapshot
)
from agents.utils.broker import broker
from ThinkingStepManager import ThinkingStepManager

logger = logging.getLogger(__name__)
load_dotenv()

# --- Initialize Groq LLM ---
from .config.settings import LLM_MODEL, GROQ_API_KEY, MONGODB_URI
from langchain_groq import ChatGroq

llm = ChatGroq(
    model=LLM_MODEL,
    temperature=0.1,
    max_tokens=2048,
    groq_api_key=GROQ_API_KEY
) 


# Initialize MongoDB checkpointer
try:
    mongo_client = MongoClient(MONGODB_URI)
    mongo_client.admin.command('ping')
    checkpointer = MongoDBSaver(
        mongo_client, 
        db_name="yusr_db",
        collection_name="langgraph_checkpoints"
    )
    logger.info("✅ Initialized MongoDB checkpointer for LangGraph")
    # Clean up any corrupt checkpoints that are missing the 'step' metadata key.
    # These cause a KeyError crash when LangGraph tries to resume the session.
    try:
        _cp_col = mongo_client["yusr_db"]["langgraph_checkpoints"]
        _deleted = _cp_col.delete_many({
            "$or": [
                {"metadata.step": {"$exists": False}},
                {"metadata": {"$exists": False}},
            ]
        })
        if _deleted.deleted_count > 0:
            logger.info(f"🗑️ Cleaned up {_deleted.deleted_count} corrupt checkpoints on startup")
    except Exception as _cleanup_err:
        logger.warning(f"⚠️ Checkpoint cleanup on startup failed (non-fatal): {_cleanup_err}")
except Exception as e:
    logger.error(f"❌ Failed to initialize MongoDB checkpointer: {e}")
    checkpointer = None

def extract_json_payload(text: str, default):
    if text is None:
        return default
    candidate = str(text).strip()
    if candidate.startswith("```"):
        parts = candidate.split("```")
        candidate = parts[1] if len(parts) > 1 else candidate
        candidate = candidate.strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()

    try:
        return json.loads(candidate)
    except Exception:
        pass

    patterns = [r"\[.*\]", r"\{.*\}"]
    for pattern in patterns:
        match = re.search(pattern, candidate, re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(0))
        except Exception:
            continue
    return default

async def save_checkpoint_compat(session_id: Optional[str], checkpoint_value, metadata: Optional[Dict[str, Any]] = None) -> bool:
    if not checkpointer or not session_id:
        return False
    kwargs = {
        "config": {"configurable": {"thread_id": session_id, "checkpoint_ns": ""}},
        "checkpoint": checkpoint_value,
        "metadata": metadata or {}
    }
    try:
        await checkpointer.aput(**kwargs)
        return True
    except TypeError as e:
        if "new_versions" not in str(e):
            raise
        kwargs["new_versions"] = {}
        await checkpointer.aput(**kwargs)
        return True

# ============================================================================
# FIX 1: IMPROVED Credential Extraction Function (GENERIC FOR ANY SITE)
# ============================================================================

def extract_credentials_from_request(user_request: Dict) -> Dict[str, Optional[str]]:
    """
    Extract email and password from user request - WORKS FOR ANY WEBSITE.
    
    Examples:
    - "login to facebook using hala@gmail.com and password Hala123"
    - "sign in with test@example.com password: mypass"
    - "login to gmail with user@example.com and password mypassword123"
    - "sign up on amazon with email@domain.com password securepass"
    - "register at example.com using email: user@example.com"
    - "write password mypass"
    """
    
    # ✅ FIX: Look in multiple fields for credentials
    text = ""
    if 'confirmation' in user_request:
        text = str(user_request.get('confirmation', ''))
    elif 'action' in user_request:
        text = str(user_request.get('action', ''))
    else:
        text = str(user_request)
    
    logger.info(f"🔍 Extracting credentials from text: '{text}'")
    
    # Email extraction - enhanced pattern
    email_patterns = [
        r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',  # Standard email
        r'email[\s:]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',  # "email: user@example.com"
        r'using[\s]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',   # "using user@example.com"
        r'with[\s]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',    # "with user@example.com"
    ]
    
    email = None
    for pattern in email_patterns:
        email_match = re.search(pattern, text, re.IGNORECASE)
        if email_match:
            email = email_match.group(1) if email_match.group(1) else email_match.group(0)
            logger.info(f"📧 Found email using pattern: {pattern}")
            break
    
    # Password extraction patterns - MORE COMPREHENSIVE FOR ANY SITE
    password = None
    
    # Pattern priorities (most specific first)
    password_patterns = [
        # Direct password patterns
        r'password[\s:]+([^\s,.!?]+)',      # "password mypass"
        r'pwd[\s:]+([^\s,.!?]+)',           # "pwd mypass"
        r'pass[\s:]+([^\s,.!?]+)',          # "pass mypass"
        
        # With connector words
        r'and[\s]+password[\s:]+([^\s,.!?]+)',      # "and password mypass"
        r'with[\s]+password[\s:]+([^\s,.!?]+)',     # "with password mypass"
        r'using[\s]+password[\s:]+([^\s,.!?]+)',    # "using password mypass"
        
        # Complex patterns for any login/signup
        r'(?:login|sign in|sign up|register|create account).*?password[\s:]+([^\s,.!?]+)',
        
        # Password after email
        r'@[^\s]+[\s]+([^\s,.!?]{4,})',  # Word after email (min 4 chars)
        
        # Generic "password is X" pattern
        r'password[\s]+is[\s]+([^\s,.!?]+)',
    ]
    
    for pattern in password_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            password = match.group(1).strip()
            # Clean up any trailing punctuation that might have been captured
            password = password.rstrip('.,;!?')
            logger.info(f"🔑 Found password using pattern: {pattern}")
            break
    
    # If no password found with patterns, try to find word after common login phrases
    if not password:
        # Look for words after login/signin phrases
        login_phrases = ['login', 'sign in', 'sign up', 'register', 'create account']
        for phrase in login_phrases:
            if phrase in text.lower():
                # Find the phrase and get next 3 words
                phrase_pos = text.lower().find(phrase)
                if phrase_pos != -1:
                    after_phrase = text[phrase_pos + len(phrase):].strip()
                    words = after_phrase.split()
                    # Look for a likely password (4+ chars, not email)
                    for word in words[:3]:  # Check first 3 words
                        if len(word) >= 4 and '@' not in word and '.' not in word:
                            password = word
                            logger.info(f"🔑 Assuming password after '{phrase}': {password}")
                            break
                if password:
                    break
    
    logger.info(f"✅ Credential extraction result - Email: {email}, Password: {'[EXTRACTED]' if password else 'NOT FOUND'}")
    
    return {'email': email, 'password': password}

# ============================================================================
# WEB AUTOMATION SUPPORT - NO HARDCODED URLs
# ============================================================================

class ActionTask(BaseModel):
    """Task format for RAG-based action layer - URLs resolved by execution layer"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str  # Shared high-level objective for the whole task plan
    ai_prompt: str  # Natural language prompt for RAG LLM - this drives URL resolution
    device: Literal["desktop", "mobile"]
    context: Literal["local", "web"]
    extra_params: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    # Web-specific parameters - NO URLs here, execution layer resolves them
    web_params: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Examples:
    # Navigation: {"action": "navigate"}  # URL comes from RAG based on ai_prompt
    # Interaction: {"action": "fill", "text": "search query"}  # Selector comes from RAG
    # Extraction: {"action": "extract"}  # Selector comes from RAG
    
    target_agent: Literal["action", "reasoning"] = "action"
    depends_on: Optional[List[str]] = None
    
    class Config:
        use_enum_values = True

class TaskResult(BaseModel):
    """Result from action/reasoning layer"""
    task_id: str
    status: Literal["success", "failed", "pending", "awaiting_confirmation"]
    content: Optional[str] = None
    error: Optional[str] = None
    details: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    clarification_type: Optional[str] = None
    recoverable: bool = False


def _extract_execution_clarification(task: ActionTask, result: TaskResult) -> Optional[Dict[str, Any]]:
    """Build a normalized clarification event from execution result if needed."""
    if result.needs_clarification:
        return {
            "task_id": task.task_id,
            "task_prompt": task.ai_prompt,
            "clarification_type": result.clarification_type or "unknown",
            "question": result.clarification_question or "I need clarification to continue.",
            "recoverable": bool(result.recoverable),
            "metadata": result.metadata or {},
            "error": result.error,
        }

    # Safely access details field (may not exist for MobileTaskResult)
    details = getattr(result, 'details', '')
    combined = " ".join(filter(None, [result.error, details, result.content])).lower()
    if not combined:
        return None

    if any(k in combined for k in ["account picker", "choose account", "select account"]):
        return {
            "task_id": task.task_id,
            "task_prompt": task.ai_prompt,
            "clarification_type": "account_selection",
            "question": "I found an account selection screen. Which account should I choose?",
            "recoverable": True,
            "metadata": result.metadata or {},
            "error": result.error,
        }

    if any(k in combined for k in ["captcha", "verification code", "2fa", "otp"]):
        return {
            "task_id": task.task_id,
            "task_prompt": task.ai_prompt,
            "clarification_type": "security_verification",
            "question": "I need your help to complete a verification step on screen. Please complete it, then tell me to continue.",
            "recoverable": False,
            "metadata": result.metadata or {},
            "error": result.error,
        }

    if any(k in combined for k in ["permission", "allow", "deny access", "popup", "dialog", "modal"]):
        return {
            "task_id": task.task_id,
            "task_prompt": task.ai_prompt,
            "clarification_type": "permission_or_popup",
            "question": "An unexpected popup appeared. Should I allow it, close it, or stop?",
            "recoverable": True,
            "metadata": result.metadata or {},
            "error": result.error,
        }

    return None


def _decide_execution_clarification_action(task: ActionTask, event: Dict[str, Any]) -> Dict[str, Any]:
    """Decide whether to self-resolve, ask user, or fail safely."""
    clarification_type = event.get("clarification_type")
    recoverable = bool(event.get("recoverable"))

    if clarification_type == "account_selection":
        explicit_email = (task.extra_params or {}).get("email")
        if explicit_email:
            return {
                "decision": "self_resolve",
                "action_prompt": f"Select account {explicit_email} and continue",
                "reason": "Account selection has explicit user email",
            }
        return {
            "decision": "ask_user",
            "reason": "Account choice not explicit",
        }

    if clarification_type == "security_verification":
        return {
            "decision": "ask_user",
            "reason": "Security verification must be completed by user",
        }

    if recoverable:
        return {
            "decision": "ask_user",
            "reason": "Recoverable ambiguity requires user intent",
        }

    return {
        "decision": "fail_safely",
        "reason": "Unrecoverable execution state",
    }

# --- Queue Management (unchanged) ---
class TaskQueue:
    """Manages sequential task execution with interrupt controls"""
    def __init__(self):
        self.current_queue: deque = deque()
        self.global_queue: deque = deque()
        self.execution_history: List[Dict] = []
        self.is_paused: bool = False
        self.is_stopped: bool = False
        self.current_task_id: Optional[str] = None
        
    def add_to_current(self, tasks: List[ActionTask]):
        self.current_queue.extend(tasks)
        
    def add_to_global(self, task_plan: Dict):
        self.global_queue.append(task_plan)
        
    def get_next_task(self) -> Optional[ActionTask]:
        if self.current_queue and not self.is_paused and not self.is_stopped:
            task = self.current_queue.popleft()
            self.current_task_id = task.task_id
            return task
        return None
    
    def has_tasks(self) -> bool:
        return len(self.current_queue) > 0
    
    def pause(self):
        self.is_paused = True
        logger.info("⏸️ Task execution paused")
        
    def resume(self):
        self.is_paused = False
        logger.info("▶️ Task execution resumed")
        
    def stop(self):
        self.is_stopped = True
        self.current_queue.clear()
        logger.info("⏹️ Task execution stopped")
        
    def reset(self):
        self.is_paused = False
        self.is_stopped = False
        self.current_task_id = None
        
    def log_execution(self, task: ActionTask, result: TaskResult):
        self.execution_history.append({
            "task": task.model_dump(),
            "result": result.model_dump(),
            "timestamp": datetime.now().isoformat()
        })
        
    def get_failed_index(self) -> Optional[int]:
        for idx, entry in enumerate(self.execution_history):
            if entry["result"]["status"] == "failed":
                return idx
        return None
    
    def retry_from_failed(self) -> List[ActionTask]:
        failed_idx = self.get_failed_index()
        if failed_idx is not None:
            retry_tasks = [
                ActionTask(**entry["task"]) 
                for entry in self.execution_history[failed_idx:]
            ]
            self.execution_history = self.execution_history[:failed_idx]
            return retry_tasks
        return []

# Global task queue
task_queue = TaskQueue()
coordinator_processing_lock = asyncio.Lock()

# Track pending results
pending_results: Dict[str, asyncio.Future] = {}

# Helper function for guarded futures
def create_guarded_future(task_id: str) -> asyncio.Future:
    """
    Create a future with built-in state tracking
    
    Returns:
        asyncio.Future with task_id attached for debugging
    """
    future = asyncio.Future()
    future._task_id = task_id  # Attach ID for debugging
    future._created_at = datetime.now()
    return future

# --- LangGraph State ---
class CoordinatorState(BaseModel):
    input: Dict[str, Any]
    plan: Optional[Dict[str, Any]] = None
    tasks: List[ActionTask] = Field(default_factory=list)
    results: Dict[str, TaskResult] = Field(default_factory=dict)
    status: str = "pending"
    session_id: Optional[str] = None
    original_message_id: Optional[str] = None
    user_id: Optional[str] = None
    preferences_context: Optional[str] = None
    
    # ✅ FIX 3: Add conversation history tracking
    conversation_history: List[Dict] = Field(default_factory=list)
    last_successful_action: Optional[str] = None
    current_page_url: Optional[str] = None

# ============================================================================
# ENHANCED TASK DECOMPOSITION - NO HARDCODED URLs
# ============================================================================

async def decompose_task_to_actions(
    user_request: Dict[str, Any],
    preferences_context: str,
    device_type: str = "desktop",
    conversation_history: List[Dict] = None,  # ✅ FIX 3: Add history parameter
    session_id: str = None,  # ✅ FIX 5: Add this
    http_request_id: str = None  # ✅ FIX 5: Add this
) -> Dict[str, Any]:
    """Decompose user request into ActionTask queue - URLs resolved by execution layer"""
    
    # ✅ FIX 2: Extract credentials FIRST - FOR ANY LOGIN/SIGNUP TASK
    login_keywords = ['login', 'sign in', 'sign up', 'register', 'create account', 'log in']
    is_login_task = any(keyword in str(user_request).lower() for keyword in login_keywords)
    
    credentials = None
    if is_login_task:
        credentials = extract_credentials_from_request(user_request)
        
        if not credentials.get('email'):
            logger.error("❌ No email found in request")
            return {
                'error': 'Please provide an email address in your request (e.g., "login with user@example.com and password mypass123")',
                'tasks': []
            }
        
        if not credentials.get('password'):
            logger.error("❌ No password found in request")
            return {
                'error': 'Please provide a password in your request (e.g., "login with user@example.com and password mypass123")',
                'tasks': []
            }
        
        logger.info(f"📧 Extracted email: {credentials['email']}")
        logger.info(f"🔑 Password extracted (length: {len(credentials['password'])})")
    
    # Remove the decomposition-level thinking step entirely.
    # The coordinator already shows "preparing_tasks" before calling ainvoke.
    # Adding another one here just doubles the indicator on screen.
    
    device_hint = f"The user is on a {device_type} device. Tailor task recommendations accordingly.\n\n"
    
    # ✅ FIX 3: Build conversation history context
    history_context = ""
    if conversation_history:
        history_context = "\n\n# CONVERSATION HISTORY (Last 3 interactions)\n"
        for entry in conversation_history[-3:]:
            history_context += f"User: {entry.get('user_message', '')}\n"
            history_context += f"Action: {entry.get('action_taken', '')}\n"
            history_context += f"Result: {entry.get('result', '')}\n\n"
    
    prompt = f"""{device_hint}You are the AURA Task Decomposition Agent. Convert user requests into low-level executable tasks.

# USER REQUEST
{json.dumps(user_request, indent=2)}

# USER PREFERENCES
{preferences_context}
{history_context}"""
    
    # ✅ FIX 2: Add credentials section to prompt if applicable
    if credentials:
        prompt += f"""
        
# EXTRACTED CREDENTIALS (USE THESE EXACT VALUES):
Email: {credentials['email']}
Password: {credentials['password']}

**CRITICAL**: When creating fill tasks for login/signup, use these EXACT values in web_params:
- For email field: {{"action": "fill", "text": "{credentials['email']}"}}
- For password field: {{"action": "fill", "text": "{credentials['password']}"}}

DO NOT use placeholder values like "test_user_email" or "test_password".
# OUTPUT RULES

**FOR LOGIN TASKS**: When you see "Fill email field", you MUST use the actual email from above: "{credentials['email']}"
**FOR PASSWORD TASKS**: When you see "Fill password field", you MUST use the actual password from above: "{credentials['password']}"

"""
    
    prompt += f"""
============================
CORE BEHAVIOR RULES
============================

1. NEVER generate conversational, acknowledgment, confirmation, planning, preparation, or meta tasks.

# EMAIL TASK RULES (CRITICAL)
When the user asks to compose, send, or draft an email:
a) ALWAYS use the USER PREFERENCES section above to pick the concrete email app.
   - If preferences mention Gmail → use Gmail.
   - If preferences mention Outlook → use Outlook.
   - If preferences mention any mail app → use that app.
   - Only if preferences are completely silent about email → use "open default email app" (local).
   NEVER output a vague "open email client" task when memory has a preference.

b) ALWAYS generate a reasoning task FIRST that produces the email content fields IF user doesnt give email subject and content, only a description.
    e.g. user request: "Reschedule tomorrow's meeting with Sara" -- this implies an email but doesn't give subject or body. role of reasoning task: "Compose a complete email for this request: 'Reschedule tomorrow's meeting with Sara'. Return a JSON object with keys SUBJECT and BODY.",
    user request: "Send an email to Sara about rescheduling tomorrow's meeting. Subject should be 'Meeting Rescheduled' and body should say 'Hi Sara, ...'" -- reasoning task is NOT needed here because subject and body are explicitly provided by user.
   The reasoning task ai_prompt must say:
   "Compose a complete email for this request: <user intent>.
    Return a JSON object with keys SUBJECT and BODY."
    The reasoning task output will be a JSON object injected as input_content, e.g.:
    {{"SUBJECT": "Meeting Rescheduled", "BODY": "Hi Sara, ..."}}

c) The action tasks that fill Subject and Body MUST depend on the reasoning task, if present, and
   will receive the generated JSON in extra_params["input_content"].

❌ INVALID tasks include:
- "Confirm receipt of the request"
- "Prepare to execute the task"
- "Pass the request to another agent"
- "Wait for user input"

✅ Every task MUST represent a real-world executable operation.

2. Detect SIMPLE vs COMPOSITE requests:

A SIMPLE request:
- Can be completed by a single real-world action
- Examples: "Open Notepad", "Go to google.com"

➜ For SIMPLE requests: Return EXACTLY ONE task

A COMPOSITE request:
- Requires multiple real actions
- Examples: "Search Amazon for white socks and extract prices"

➜ Only COMPOSITE requests may be decomposed.

3. Always prefer the MINIMAL valid execution plan.

4. For configuration tasks (setting alarms, filling forms, creating events), always include a final confirmation step to ensure the task is completed (e.g., "Press OK to save the alarm").

# DEVICE & CONTEXT

- **device**: "desktop" or "mobile"
- **context**: "local" (native OS/apps) or "web" (browser automation)

# TARGET AGENTS

- **action**: UI automation (click, type, navigate)
- **reasoning**: Logic tasks (summarize, analyze)

# TASK STRUCTURE

Each task must have:
- **ai_prompt**: Natural language instruction (CRITICAL: this is used by RAG to determine URLs and selectors)
- **goal**: The SAME high-level goal string for the entire task plan (must be identical in all tasks)
- **device**: "desktop" or "mobile"
- **context**: "local" or "web"
- **target_agent**: "action" or "reasoning"
- **extra_params**: Additional data (app_name, file_path, etc.)
- **web_params**: Web-specific parameters (action type only - NO URLs!)
- **depends_on**: task_id of prerequisite task

# WEB_PARAMS STRUCTURE (for context: "web")

🚨 CRITICAL: Do NOT hardcode URLs, selectors, or wait strategies!
The execution layer will use RAG to determine these from the ai_prompt.

For navigation tasks:
{{
  "action": "navigate"
}}

For interaction tasks (click, fill):
{{
  "action": "fill",
  "text": "search query"  // Only include text for fill actions
}}

For extraction tasks:
{{
  "action": "extract"
}}

============================
EXAMPLES (YAML format for brevity, output must be JSON)
============================

## Example 1: Simple Desktop Task

User: "Open Notepad"

- task_id: task_1
  goal: Open Notepad
  ai_prompt: Open Notepad application
  device: desktop
  context: local
  target_agent: action
  extra_params:
    app_name: notepad
  web_params: {{}}
  depends_on: null

## Example 2: Login Task (ANY WEBSITE)

User: "Login to Gmail with user@example.com and password mypass123"

- task_id: task_1
  goal: Log in to Gmail with the provided credentials
  ai_prompt: Navigate to Gmail login page
  device: desktop
  context: web
  target_agent: action
  web_params:
    action: navigate
  depends_on: null

- task_id: task_2
  goal: Log in to Gmail with the provided credentials
  ai_prompt: Fill the email or username field with user@example.com
  device: desktop
  context: web
  target_agent: action
  web_params:
    action: fill
    text: user@example.com
  depends_on: ["task_1"]

- task_id: task_3
  goal: Log in to Gmail with the provided credentials
  ai_prompt: Fill the password field with mypass123
  device: desktop
  context: web
  target_agent: action
  web_params:
    action: fill
    text: mypass123
  depends_on: ["task_2"]

- task_id: task_4
  goal: Log in to Gmail with the provided credentials
  ai_prompt: Click the Sign In or Login submit button
  device: desktop
  context: web
  target_agent: action
  web_params:
    action: click
  depends_on: ["task_3"]

## Example 3: Email Composition Task

User: "Compose an email to rescheduling tomorrow's meeting with Sara@gmail.com"
(Note: user does NOT provide subject or body, so reasoning task is needed to generate them. Assume user preferences indicate Gmail as email app.)

- task_id: task_1
  goal: Compose and send a meeting reschedule email to Sara
  ai_prompt: 
    Compose a complete email for this request:
    "Reschedule tomorrow's meeting with Sara".
    Return a JSON object with keys SUBJECT and BODY.
  device: mobile
  context: local
  target_agent: reasoning
  extra_params: {{}}
  web_params: {{}}
  depends_on: null

- task_id: task_2
  goal: Compose and send a meeting reschedule email to Sara
  ai_prompt: Navigate to Gmail 
  device: mobile
  context: local
  target_agent: action
  extra_params:
    app_name: gmail
  depends_on: null

- task_id: task_3
  goal: Compose and send a meeting reschedule email to Sara
  ai_prompt: Compose new email to sara@gmail.com
  device: mobile
  context: local
  target_agent: action
  extra_params:
    recipient: sara@gmail.com
  depends_on: ["task_2"]

- task_id: task_4
  goal: Compose and send a meeting reschedule email to Sara
  ai_prompt: Fill the Subject field with the SUBJECT value from the composed email
  device: mobile
  context: local
  target_agent: action
  extra_params: {{}}
  depends_on: ["task_1", "task_3"]

- task_id: task_5
  goal: Compose and send a meeting reschedule email to Sara
  ai_prompt: Fill the email body with the BODY value from the composed email
  device: mobile
  context: local
  target_agent: action
  extra_params: {{}}
  depends_on: ["task_4"]

- task_id: task_6
  goal: Compose and send a meeting reschedule email to Sara
  ai_prompt: Click the Send button to send the email
  device: mobile
  context: local
  target_agent: action
  extra_params: {{}}
  depends_on: ["task_5"]

EXPLANATION: task_1 (reasoning) returns {{"SUBJECT": "...", "BODY": "..."}}.
task_2 navigates Gmail in parallel. task_3 fills the To field directly (known from
the user request). tasks 4-5 depend on task_1 and receive the JSON as input_content
so the action layer can parse SUBJECT and BODY individually.
The email app (Gmail) is chosen from USER PREFERENCES, not hardcoded.

## Example 4: Mobile Configuration Task

User: "Set the alarm for 7 am"

- task_id: task_1
  goal: Set an alarm for 7:00 AM
  ai_prompt: Open the Clock app on mobile device
  device: mobile
  context: local
  target_agent: action
  extra_params:
    app_name: clock
  depends_on: null

- task_id: task_2
  goal: Set an alarm for 7:00 AM
  ai_prompt: Set the alarm time to 7:00 AM
  device: mobile
  context: local
  target_agent: action
  extra_params:
    time: "7:00"
  depends_on: ["task_1"]

- task_id: task_3
  goal: Set an alarm for 7:00 AM
  ai_prompt: Press OK or Save to confirm the alarm setting
  device: mobile
  context: local
  target_agent: action
  depends_on: ["task_2"]

## Example 5: Mixed Action + Reasoning (Content Generation)

User: "Open Notepad and write me a scary story"

- task_id: task_1
  goal: Open Notepad and produce a scary story in it
  ai_prompt: Open Notepad application
  device: desktop
  context: local
  target_agent: action
  extra_params:
    app_name: notepad
  web_params: {{}}
  depends_on: null

- task_id: task_2
  goal: Open Notepad and produce a scary story in it
  ai_prompt: Write a very scary story
  device: desktop
  context: local
  target_agent: reasoning
  extra_params: {{}}
  web_params: {{}}
  depends_on: ["task_1"]

- task_id: task_3
  goal: Open Notepad and produce a scary story in it
  ai_prompt: Type the generated story text into the active Notepad window
  device: desktop
  context: local
  target_agent: action
  extra_params: {{}}
  web_params: {{}}
  depends_on: ["task_2"]

EXPLANATION: Task 2 uses "reasoning" because writing a story is content generation. Task 3 uses "action" to type the result into Notepad.

# WHEN TO USE target_agent: "reasoning" vs "action"

- **"action"**: Tasks that interact with the OS, apps, or browser (open, click, type, navigate, fill, screenshot, etc.)
- **"reasoning"**: Tasks that generate, summarize, analyze, research, write, translate, or answer questions. Content creation (stories, essays, code, emails, poems) is ALWAYS reasoning. If a task does NOT require interacting with a UI element, it is reasoning.

Examples of REASONING tasks:
- "Write a scary story" → reasoning
- "Summarize this article" → reasoning
- "Translate this to Arabic" → reasoning
- "Draft an email to my boss" → reasoning (IMPORTANT: ai_prompt must request SUBJECT and BODY together)
- "Explain quantum computing" → reasoning
- "Generate a Python script" → reasoning

EMAIL COMPOSITION RULE: When needed (i.e., ai_prompt requests SUBJECT and BODY), a single reasoning task must always generate ALL email
fields together (Subject, Body) in one structured output. Never split these
into separate reasoning tasks. The action layer will parse the output and fill
each field individually.

Examples of ACTION tasks:
- "Open Notepad" → action
- "Click the submit button" → action
- "Navigate to google.com" → action
- "Type 'hello' in the search box" → action

# CRITICAL RULES

1. **One action per task** - never combine multiple actions
2. **Explicit dependencies** - if task B needs task A's output, set "depends_on"
3. **Descriptive prompts** - ai_prompt should be detailed enough for RAG to understand
4. **Correct context** - web tasks get context: "web", desktop tasks get "local"
5. **Minimal extra_params** - ONLY include action type and text (for fill), nothing else
6. **NO URLs** - NEVER hardcode URLs, let RAG resolve them from ai_prompt
7. **NO selectors** - NEVER hardcode selectors, let RAG find them from ai_prompt
8. **Empty web_params** - For local tasks, set web_params: {{}}
9. **Include confirmation steps** - For configuration tasks (alarms, forms, settings), always add a final task to confirm/save changes
10. **Content generation = reasoning** - Writing, summarizing, translating, or any creative/analytical task MUST use target_agent: "reasoning"
11. **Shared goal** - Every task in the output must include a non-empty "goal" and it must be exactly the same across all tasks in that decomposition

============================
OUTPUT RULES
============================

Return ONLY valid JSON array of tasks (no markdown, no explanations):
[
  {{
    "task_id": <string>,
    "goal": <string, identical across all tasks>,
    "ai_prompt": <string>,
    "device": <"desktop" | "mobile">,
    "context": <"local" | "web">,
    "target_agent": <"action" | "reasoning">,
    "extra_params": <object>,
    "web_params": <object>,
    "depends_on": <array of strings | null>
  }},
  ...
]

Generate the task decomposition now:"""

    try:
        response = await llm.ainvoke(prompt)
        response_text = response.content if hasattr(response, 'content') else str(response)
        response_text = response_text.strip()

        # ── ROBUST JSON EXTRACTION ─────────────────────────────────────────────
        # The LLM sometimes produces:
        #   1. A JSON array wrapped in ```json ... ``` fences
        #   2. A JSON array preceded or followed by prose (e.g. a story continuation)
        #   3. Two JSON objects on separate lines → "Extra data" JSONDecodeError
        #   4. A plain dict {"tasks": [...]} instead of a bare array
        # We handle all cases by extracting ONLY the first valid JSON array found.

        # Step A: strip markdown fences
        if "```" in response_text:
            parts = response_text.split("```")
            # Take the content between the first pair of fences
            if len(parts) > 1:
                inner = parts[1].strip()
                if inner.startswith("json"):
                    inner = inner[4:].strip()
                response_text = inner

        # Step B: find the first '[' ... ']' balanced JSON array in the text.
        # This discards any leading/trailing prose or extra JSON objects.
        def _extract_first_json_array(text: str) -> Optional[str]:
            """Return the first balanced JSON array substring found in text."""
            start = text.find("[")
            if start == -1:
                return None
            depth = 0
            in_string = False
            escape_next = False
            for i, ch in enumerate(text[start:], start):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\" and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                if in_string:
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
            return None

        json_array_str = _extract_first_json_array(response_text)

        # Step C: also try top-level dict with "tasks" key as fallback
        parsed = None
        if json_array_str:
            try:
                parsed = json.loads(json_array_str)
            except json.JSONDecodeError:
                pass

        if parsed is None:
            # Try the whole (stripped) text as a dict
            try:
                candidate = json.loads(response_text.strip())
                if isinstance(candidate, dict) and "tasks" in candidate:
                    parsed = candidate["tasks"]
                elif isinstance(candidate, list):
                    parsed = candidate
            except json.JSONDecodeError:
                pass

        if parsed is None:
            raise ValueError(f"No valid JSON task array found in LLM response. "
                             f"Response preview: {response_text[:200]}")

        # Step D: normalise to a flat list
        if isinstance(parsed, dict) and "tasks" in parsed:
            parsed = parsed["tasks"]

        if not isinstance(parsed, list):
            raise ValueError(f"Expected a JSON array of tasks, got: {type(parsed)}")

        # Step E: filter out non-dict entries (safety)
        task_dicts = [t for t in parsed if isinstance(t, dict)]
        if not task_dicts:
            raise ValueError("JSON array contained no task objects")

        # Step F: normalize/validate shared goal across all tasks.
        # If missing, derive from first prompt to keep downstream execution safe.
        shared_goal = next(
            (
                str(t.get("goal", "")).strip()
                for t in task_dicts
                if isinstance(t.get("goal"), str) and str(t.get("goal", "")).strip()
            ),
            ""
        )
        if not shared_goal:
            shared_goal = str(user_request.get("original_input") or user_request.get("confirmation") or user_request.get("action") or "Complete the requested task").strip()

        for t in task_dicts:
            t["goal"] = shared_goal

        action_tasks = [ActionTask(**task) for task in task_dicts]

        logger.info(f"📋 Decomposed into {len(action_tasks)} tasks")
        return {"tasks": action_tasks}

    except Exception as e:
        logger.error(f"❌ Task decomposition failed: {e}")
        return {"error": str(e)}

# --- REST OF THE CODE REMAINS THE SAME ---
# (Orchestration graph, execution, broker integration, etc.)

def create_coordinator_graph():
    """Create the coordinator orchestration graph"""
    graph = StateGraph(dict)

    async def analyze_and_plan(state: Dict) -> Dict:
        """STEP 1: Decompose user request into ActionTask queue"""
        raw_task = state["input"]
        session_id = state.get("session_id")
        user_id = state.get("user_id", "default_user")
        original_message_id = state.get("original_message_id")
        device_type = raw_task.get("device_type", "desktop")

        # Retrieve user preferences
        previous_execution_state = None

        try:
            from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
            pref_mgr = get_preference_manager(user_id)
            
            if checkpointer and session_id:
                try:
                    checkpoint_data = await checkpointer.aget(
                        config={"configurable": {"thread_id": session_id, "checkpoint_ns": ""}}
                    )
                    if checkpoint_data and "execution_state" in checkpoint_data:
                        previous_execution_state = checkpoint_data["execution_state"]
                        logger.info(f"🔄 Found previous execution state")
                except Exception as e:
                    logger.debug(f"No previous execution state: {e}")
            
            preferences_context = pref_mgr.get_relevant_preferences(
                str(raw_task.get("original_input", raw_task.get("confirmation", ""))), limit=5
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not retrieve preferences: {e}")
            preferences_context = "No user preferences available"

        if previous_execution_state:
            execution_context = f"\n\n# PREVIOUS EXECUTION STATE\n"
            execution_context += f"Failed at task: {previous_execution_state.get('failed_task_id')}\n"
            execution_context += f"Completed tasks: {previous_execution_state.get('completed_task_ids', [])}\n"
            execution_context += f"User is asking to retry. Continue from where you left off"
            preferences_context = f"{preferences_context}{execution_context}"

        # Decompose task
        # ✅ FIX 3: Pass conversation history to decomposition
        plan_result = await decompose_task_to_actions(
            raw_task, 
            preferences_context, 
            device_type,
            conversation_history=state.get("conversation_history", []),
            session_id=session_id,  # ✅ FIX 5: Pass these parameters
            http_request_id=original_message_id  # ✅ FIX 5: Pass these parameters
        )
        
        # Surface decomposition errors when present
        if isinstance(plan_result, dict) and "error" in plan_result:
            logger.error(f"❌ Decomposition returned error: {plan_result['error']}")
            tasks = []
        else:
            tasks = plan_result.get("tasks", [])
            try:
                tasks_dump = [t.model_dump() if hasattr(t, 'model_dump') else t for t in tasks]
                logger.info(f"📋 Decomposition result ({len(tasks_dump)} tasks): {json.dumps(tasks_dump, indent=2)}")
            except Exception as e:
                logger.info(f"📋 Decomposed into {len(tasks)} tasks (failed to serialize tasks: {e})")
        
        # Set device in all tasks if not already set
        for task in tasks:
            if getattr(task, "device", None) is None:
                task.device = device_type
        
        return {
            "input": state["input"],
            "tasks": tasks,
            "status": "ready",
            "session_id": session_id,
            "original_message_id": original_message_id,
            "user_id": user_id,
            "preferences_context": preferences_context,
        }

    async def execute_tasks(state: Dict) -> Dict:
        """STEP 2: Execute tasks sequentially"""
        tasks = state["tasks"]
        session_id = state.get("session_id")
        original_message_id = state.get("original_message_id")
        user_language = state.get("input", {}).get("user_language", "en")
        # output_language: language for task content (may differ from system language)
        output_language = state.get("input", {}).get("output_language", user_language)
        # user_profile: personalization data forwarded from Language Agent
        user_profile = state.get("input", {}).get("user_profile") or {}
        
        task_queue.reset()
        task_queue.add_to_current(tasks)
        
        results = {}
        task_outputs = {}
        clarification_event = None

        if checkpointer and session_id:
            try:
                # execution_state={
                #     "completed_task_ids": list(results.keys()),
                #     "failed_task_ids":task_queue.get_failed_index(),
                #     "remaining_tasks": [t.task_id for t in list(task_queue.current_queue)],
                #     "timestamp": datetime.now().isoformat()
                # }
                # Get failed task IDs from execution history
                failed_task_ids = []
                for entry in task_queue.execution_history:
                    if entry.get("result", {}).get("status") == "failed":
                        task_data = entry.get("task", {})
                        if isinstance(task_data, dict):
                            failed_task_ids.append(task_data.get("task_id", ""))
                        elif hasattr(task_data, "task_id"):
                            failed_task_ids.append(task_data.task_id)
                
                execution_state={
                    "completed_task_ids": list(results.keys()),
                    "failed_task_ids": failed_task_ids,
                    "remaining_tasks": [t.task_id for t in list(task_queue.current_queue)],
                    "timestamp": datetime.now().isoformat()
                }
                #edit here
                # await save_checkpoint_compat(
                #     session_id,
                #     {"execution_state": execution_state},
                #     {"type": "task_progress"}
                # )
                await save_checkpoint_compat(
                    session_id,
                    {
                        "v": 1,
                        "id": str(uuid.uuid4()),
                        "ts": datetime.now().isoformat(),
                        "channel_values": {"execution_state": execution_state},
                        "channel_versions": {},
                        "versions_seen": {},
                        "pending_sends": [],
                    },
                    {"type": "task_progress"}
                )
                logger.info(f"💾 Saved task progress")
            except Exception as e:
                logger.error(f"❌ Failed to save task progress: {e}") 
        
        while task_queue.has_tasks():
            if task_queue.is_stopped:
                logger.warning("⏹️ Execution stopped by user")
                break
                
            while task_queue.is_paused:
                await asyncio.sleep(0.5)
            
            current_task = task_queue.get_next_task()
            if not current_task:
                break
            
            # Check dependencies
            if current_task.depends_on:
                dep_ids = current_task.depends_on
                dependencies_met = all(
                   results.get(dep_id.strip()) 
                   and results.get(dep_id.strip()).status == "success"
                   for dep_id in dep_ids
                )
                
                if not dependencies_met:
                    logger.warning(f"⏭️ Skipping {current_task.task_id} - dependencies not met")
                    results[current_task.task_id] = TaskResult(
                        task_id=current_task.task_id,
                        status="failed",
                        error="Dependency failed"
                    )
                    continue
            
            # Inject dependent task outputs
            if current_task.extra_params.get("input_from"):
                input_task_id = current_task.extra_params["input_from"]
                if input_task_id in task_outputs:
                    current_task.extra_params["input_content"] = task_outputs[input_task_id]
            
            # Auto-inject reasoning output into dependent action tasks.
            # IMPORTANT: Only inject the clean text content — never raw fenced JSON blocks.
            # Raw blocks injected as string literals into generated pyautogui code cause
            # a "charmap codec can't encode" error on Windows when the subprocess writes
            # the .py file, because the system codepage (cp1252) can't handle Arabic/Unicode.
            if current_task.depends_on:
                dep_id = current_task.depends_on[0].strip()
                if dep_id in task_outputs and "input_content" not in current_task.extra_params:
                    raw_dep_output = task_outputs[dep_id]
                    # Strip any remaining markdown fences (defensive — reasoning agent
                    # should already return clean text, but guard here as well)
                    if isinstance(raw_dep_output, str):
                        stripped = raw_dep_output.strip()
                        if stripped.startswith("```"):
                            lines = stripped.split("\n")
                            inner = lines[1:]
                            if inner and inner[-1].strip() == "```":
                                inner = inner[:-1]
                            stripped = "\n".join(inner).strip()
                        # If it looks like JSON with a "result" key, extract just the result
                        if stripped.startswith("{") or stripped.startswith("["):
                            try:
                                parsed = json.loads(stripped)
                                if isinstance(parsed, dict) and "result" in parsed:
                                    stripped = str(parsed["result"])
                            except Exception:
                                pass
                        raw_dep_output = stripped
                    current_task.extra_params["input_content"] = raw_dep_output
                    logger.info(f"📎 Auto-injected output from {dep_id} into {current_task.task_id}")
            
            # ── Per-task debug block — shows full task before execution ──────
            # This makes it easy to verify: routing, injected content, params.
            _task_dump = current_task.model_dump()
            _ic = _task_dump.get("extra_params", {}).get("input_content", "")
            logger.info(
                f"\n{'='*70}\n"
                f"▶ TASK DISPATCHING: {current_task.task_id}\n"
                f"   ai_prompt    : {current_task.ai_prompt}\n"
                f"   device       : {current_task.device}\n"
                f"   context      : {current_task.context}\n"
                f"   target_agent : {current_task.target_agent}\n"
                f"   depends_on   : {current_task.depends_on}\n"
                f"   extra_params : {json.dumps({k: v for k, v in _task_dump.get('extra_params', {}).items() if k != 'input_content'}, ensure_ascii=False)}\n"
                f"   web_params   : {json.dumps(_task_dump.get('web_params', {}), ensure_ascii=False)}\n"
                f"   input_content: {'('+str(len(_ic))+' chars) ' + _ic[:120] + ('...' if len(_ic) > 120 else '') if _ic else '(none)'}\n"
                f"{'='*70}"
            )

            # Execute task
            logger.info(f"🔄 Executing {current_task.task_id}: {current_task.ai_prompt[:50]}...")
            result = await execute_single_task(
                current_task, session_id, original_message_id,
                user_language, output_language, user_profile
            )
            
            results[current_task.task_id] = result
            task_queue.log_execution(current_task, result)
            
            if result.content:
                cleaned_content = result.content.replace("EXECUTION_SUCCESS", "").replace("FAILED:", "").strip()
                # task_outputs[current_task.task_id] = result.content
                            # Only store if there's actual content
                if cleaned_content:
                    task_outputs[current_task.task_id] = cleaned_content
                    logger.info(f"💾 Stored output for {current_task.task_id}")
                    logger.info(f"   Length: {len(cleaned_content)} chars")
                    logger.info(f"   Preview: {cleaned_content[:200]}...")
                else:
                    logger.warning(f"⚠️ Task {current_task.task_id} produced empty output after cleaning")

            event = _extract_execution_clarification(current_task, result)
            if event:
                decision = _decide_execution_clarification_action(current_task, event)
                event["decision"] = decision.get("decision")
                event["decision_reason"] = decision.get("reason")
                logger.warning(
                    f"⚠️ Clarification event on {current_task.task_id}: {event['clarification_type']} -> {event['decision']}"
                )

                if decision.get("decision") == "self_resolve":
                    action_prompt = decision.get("action_prompt")
                    if action_prompt:
                        resolve_task = ActionTask(
                            task_id=f"{current_task.task_id}_resolve",
                            goal=current_task.goal,
                            ai_prompt=action_prompt,
                            device=current_task.device,
                            context=current_task.context,
                            extra_params=current_task.extra_params or {},
                            web_params=current_task.web_params or {},
                            target_agent="action",
                            depends_on=[current_task.task_id],
                        )
                        logger.info(f"🛠️ Attempting self-resolution: {resolve_task.ai_prompt}")
                        resolve_result = await execute_single_task(
                            resolve_task, session_id, original_message_id,
                            user_language, output_language, user_profile
                        )
                        results[resolve_task.task_id] = resolve_result
                        task_queue.log_execution(resolve_task, resolve_result)

                        if resolve_result.status == "success":
                            logger.info("✅ Self-resolution succeeded, continuing workflow")
                            continue
                        event["decision"] = "ask_user"
                        event["decision_reason"] = "Self-resolution failed"

                if event.get("decision") in {"ask_user", "fail_safely"}:
                    clarification_event = event
                    break

                
            if result.status == "failed":
                logger.error(f"❌ Task {current_task.task_id} failed: {result.error}")
                break
        
        return {
            **state,
            "results": results,
            "execution_clarification": clarification_event,
            "status": "completed",
            "session_id": session_id,
            "original_message_id": original_message_id
        }

    async def send_feedback(state: Dict) -> Dict:
        """STEP 3: Send results to Language Agent"""
        results = state.get("results", {})
        execution_clarification = state.get("execution_clarification")
        session_id = state.get("session_id")
        original_message_id = state.get("original_message_id")
        user_id = state.get("user_id", "default_user")

        def _extract_readable_text(raw):
            if raw is None:
                return ""
            if isinstance(raw, str):
                cleaned = raw.replace("EXECUTION_SUCCESS", "").replace("FAILED:", "").strip()
                if not cleaned:
                    return ""
                if (cleaned.startswith("{") and cleaned.endswith("}")) or (cleaned.startswith("[") and cleaned.endswith("]")):
                    try:
                        parsed = json.loads(cleaned)
                        return _extract_readable_text(parsed)
                    except Exception:
                        return cleaned
                return cleaned
            if isinstance(raw, list):
                return "\n\n".join([_extract_readable_text(item) for item in raw if _extract_readable_text(item)])
            if isinstance(raw, dict):
                for key in ["content", "text", "response", "message", "summary", "full_content", "result"]:
                    if raw.get(key):
                        extracted = _extract_readable_text(raw.get(key))
                        if extracted:
                            return extracted
                details = raw.get("details")
                if isinstance(details, list):
                    joined = "\n\n".join([_extract_readable_text(d) for d in details if _extract_readable_text(d)])
                    if joined:
                        return joined
                return ""
            return str(raw)
        
        success_count = sum(1 for r in results.values() if r.status == "success")
        total_count = len(results)
        
        # ✅ FIX 3: Update conversation history
        if success_count > 0:
            if "conversation_history" not in state:
                state["conversation_history"] = []
            
            state["conversation_history"].append({
                "user_message": state['input'].get('original_input', state['input'].get('confirmation', state['input'].get('action', ''))),
                "action_taken": f"Executed {success_count} tasks",
                "result": "success" if success_count == total_count else "partial",
                "timestamp": datetime.now().isoformat()
            })
            
            # Keep only last 10 interactions
            if len(state["conversation_history"]) > 10:
                state["conversation_history"] = state["conversation_history"][-10:]
        
        original_request = state["input"].get("original_input", state["input"].get("confirmation", state["input"].get("action", "")))
        user_language = state["input"].get("user_language") or "en"
        output_language = state["input"].get("output_language") or user_language
        user_profile = state["input"].get("user_profile") or {}
        is_arabic = user_language == "ar"

        if execution_clarification:
            decision = execution_clarification.get("decision")
            if decision == "ask_user":
                question = execution_clarification.get("question") or (
                    "I need clarification to continue." if not is_arabic else "أحتاج توضيحًا للمتابعة."
                )
                response_payload = {
                    "status": "clarification_needed",
                    "response": question,
                    "user_language": user_language,
                    "clarification": execution_clarification,
                }
                response_msg = AgentMessage(
                    message_type=MessageType.TASK_RESPONSE,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    response_to=original_message_id,
                    payload=response_payload,
                )
                await broker.publish(Channels.COORDINATOR_TO_LANGUAGE, response_msg)
                await broker.publish(
                    Channels.WEBSOCKET_OUTPUT,
                    AgentMessage(
                        message_type=MessageType.CLARIFICATION_REQUEST,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=original_message_id,
                        payload={
                            "ws_type": "clarification_needed",
                            "question": question,
                            "user_language": user_language,
                            "clarification": execution_clarification,
                        },
                    ),
                )
                return {"status": "clarification_needed"}

            fail_text = (
                "تعذّر إكمال المهمة بأمان بسبب حالة غير متوقعة."
                if is_arabic else
                "I couldn't complete the task safely due to an unexpected state."
            )
            response_msg = AgentMessage(
                message_type=MessageType.TASK_RESPONSE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                response_to=original_message_id,
                payload={
                    "status": "failed",
                    "response": fail_text,
                    "user_language": user_language,
                    "clarification": execution_clarification,
                },
            )
            await broker.publish(Channels.COORDINATOR_TO_LANGUAGE, response_msg)
            return {"status": "failed"}

        # ── Build readable content from task results ─────────────────────────
        detail_lines = []
        has_reasoning_content = False
        for task_obj in state.get("tasks", []):
            tid = task_obj.task_id if hasattr(task_obj, 'task_id') else task_obj.get('task_id', '')
            r = results.get(tid)
            if r and r.content:
                extracted_content = _extract_readable_text(r.content)
                if extracted_content:
                    detail_lines.append(extracted_content)
                if hasattr(task_obj, 'target_agent') and task_obj.target_agent == "reasoning":
                    has_reasoning_content = True

        full_content = "\n\n".join(detail_lines) if detail_lines else ""

        # ── Delegate user-facing message to Language Agent ────────────────────
        # The Coordinator does NOT generate user communication directly.
        # Instead it calls the Language Agent's Communication Mode which applies
        # the correct language, tone, and personalization to the result.
        response_text = ""
        follow_ups = []
        try:
            from agents.language_agent import get_agent_for_session
            lang_agent = get_agent_for_session(session_id)
            if lang_agent:
                completion_result = lang_agent.generate_completion_message(
                    original_request=original_request,
                    result_content=full_content or (
                        f"Completed {success_count}/{total_count} steps."
                        if not is_arabic
                        else f"تم إكمال {success_count}/{total_count} خطوات."
                    ),
                    result_metadata={
                        "success_count": success_count,
                        "total_count": total_count,
                        "has_reasoning_content": has_reasoning_content,
                    },
                    lang=user_language,
                )
                response_text = completion_result.get("message", "")
                follow_ups = completion_result.get("follow_ups", [])
                logger.info(f"✅ Language Agent generated completion message: {response_text[:100]}")
        except Exception as e:
            logger.warning(f"⚠️ Language Agent message generation failed, using fallback: {e}")

        # ── Safe fallback if Language Agent unavailable ───────────────────────
        if not response_text:
            if success_count == total_count and total_count > 0:
                response_text = (
                    f"تم تنفيذ المهمة بنجاح! تم تنفيذ {success_count} خطوات."
                    if is_arabic else
                    f"Task completed successfully! Executed {success_count} steps."
                )
            elif success_count > 0:
                response_text = (
                    f"تم تنفيذ المهمة جزئيًا: نجحت {success_count} من {total_count} خطوة."
                    if is_arabic else
                    f"Partially completed: {success_count}/{total_count} steps succeeded."
                )
            else:
                response_text = (
                    "تعذر إكمال المهمة. حاول مرة أخرى."
                    if is_arabic else
                    "Task could not be completed. Please try again."
                )

        # Build follow-up question for read-aloud offer (appended to response)
        follow_up_question = None
        if success_count == 0:
            follow_up_question = (
                "المهمة ما كملتش. تحب أحاول تاني؟"
                if is_arabic else
                "The task didn't complete. Would you like me to try again?"
            )
        elif success_count < total_count:
            follow_up_question = (
                "تم التنفيذ جزئيًا. تحب أحاول أكمل الخطوات اللي فشلت؟"
                if is_arabic else
                "It was only partially completed. Would you like me to retry the failed steps?"
            )
        elif has_reasoning_content and len(full_content) > 200 and not follow_ups:
            follow_up_question = (
                "تحب أقرأ النتائج بصوت عالي ولا أشرحها باختصار؟"
                if is_arabic else
                "Would you like me to read the results out loud or explain them briefly?"
            )

        if follow_up_question:
            response_text = f"{response_text} {follow_up_question}"

        # Determine response type
        if success_count == 0:
            resp_type = ResponseType.ERROR_RECOVERABLE
        elif success_count < total_count:
            resp_type = ResponseType.PARTIAL_RESULT
        elif detail_lines:
            resp_type = ResponseType.RESULT_WITH_CONTENT
        else:
            resp_type = ResponseType.SIMPLE_ACK
        
        # Build StructuredResponse
        structured = StructuredResponse(
            type=resp_type,
            spoken_text=response_text,
            full_content=full_content if full_content != response_text else None,
            offer_read_aloud=has_reasoning_content and len(full_content) > 200,
            offer_actions=(follow_ups if follow_ups else []) + (["undo", "retry"] if success_count > 0 else ["retry"]),
            context_for_undo={"original_request": original_request, "completed_tasks": [t.task_id for t in state.get("tasks", [])]}
        )
        
        response_msg = AgentMessage(
            message_type=MessageType.TASK_RESPONSE,
            sender=AgentType.COORDINATOR,
            receiver=AgentType.LANGUAGE,
            session_id=session_id,
            response_to=original_message_id,
            payload={
                "status": "success" if success_count > 0 else "failed",
                "response": response_text,
                "user_language": user_language,
                "output_language": output_language,
                "follow_up_question": follow_up_question,
                "follow_ups": follow_ups,
                "structured_response": structured.model_dump(),
                "result": {
                    "completed_tasks": {k: v.status for k, v in results.items()},
                    "details": [v.model_dump() for v in results.values()]
                }
            }
        )
        
        logger.info(f"📤 Sending feedback: {response_text}")
        await broker.publish(Channels.COORDINATOR_TO_LANGUAGE, response_msg)
        
        # Also publish structured response via WebSocket channel for real-time delivery
        ws_msg = AgentMessage(
            message_type=MessageType.STRUCTURED_RESPONSE,
            sender=AgentType.COORDINATOR,
            receiver=AgentType.LANGUAGE,
            session_id=session_id,
            response_to=original_message_id,
            payload={
                **structured.model_dump(),
                "user_language": user_language,
            }
        )
        await broker.publish(Channels.WEBSOCKET_OUTPUT, ws_msg)
        
        if success_count == total_count and total_count > 0:
            try:
                from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
                pref_mgr = get_preference_manager(user_id)
                
                task_summary = {
                    # Prefer confirmation from Language Agent for a faithful representation of the user's intent
                    "original_request": state['input'].get('original_input', state['input'].get('confirmation', state['input'].get('action', ''))),
                    "completed_steps": [t.ai_prompt for t in state['tasks']],
                    "total_steps": total_count
                }
                
                extraction_prompt = f"""Based on this completed task, extract user preferences for future tasks.

COMPLETED TASK:
{json.dumps(task_summary, indent=2)}

Extract facts in these categories:
1. TOOLS: Apps/sites the user regularly uses (e.g., "Uses Gmail for email", "Uses Chrome as browser")
2. CONTACTS: People/emails the user interacts with (e.g., "Frequently emails shahd2202743@miuegypt.edu.eg")
3. PATTERNS: How the user works (e.g., "Sends emails with short subjects", "Opens YouTube for videos")
4. ACCOUNTS: Accounts the user has (e.g., "Has Gmail account hala2206898@miuegypt.edu.eg") - NO PASSWORDS EVER

Be generous. If something appeared in the task, it is worth remembering.
One-time actions still reveal user patterns (e.g., sent email means user uses email).
Always extract at least 1-2 facts unless the task was purely system-level with no user data.

OUTPUT FORMAT (JSON array):
[
  {{
    "preference": "Uses Gmail for sending emails",
    "category": "app_usage",
    "confidence": "high"
  }}
]

If truly nothing useful (e.g. task was just opening notepad with no user data), return: []

Extract now:"""
                
                extraction_response = await llm.ainvoke(extraction_prompt)
                extraction_text = extraction_response.content if hasattr(extraction_response, 'content') else str(extraction_response)
                preferences_to_store = extract_json_payload(extraction_text, [])
                
                if preferences_to_store and isinstance(preferences_to_store, list):
                    for pref_obj in preferences_to_store:
                        if pref_obj.get("confidence") in ["high", "medium"]:
                            pref_mgr.add_preference_zero_token(
                                pref_obj["preference"],
                                metadata={
                                    "category": pref_obj.get("category", "general"),
                                    "confidence": pref_obj.get("confidence", "medium"),
                                    "extracted_from": task_summary["original_request"]
                                }
                            )
                            logger.info(f"💾 Stored preference: {pref_obj['preference']}")
                
                apps_used = list(set(
                    t.extra_params.get("app_name", "")
                    for t in state.get("tasks", [])
                    if hasattr(t, "extra_params") and t.extra_params.get("app_name")
                ))
                contexts_used = list(set(
                    t.context
                    for t in state.get("tasks", [])
                    if hasattr(t, "context")
                ))
                apps_used_str = ", ".join(filter(None, apps_used)) if apps_used else "none recorded"
                
                conversation_context = (
                    f"User completed task: {task_summary['original_request']}. "
                    f"Apps used: {apps_used_str}. "
                    f"Task types: {', '.join(contexts_used)}. "
                    f"Steps taken: {success_count}."
                )
                
                pref_mgr.add_preference_zero_token(
                    conversation_context,
                    metadata={
                        "category": "conversation_history",
                        "session_id": session_id,
                        "timestamp": datetime.now().isoformat(),
                        "apps_used": apps_used,
                        "steps": success_count,
                        "original_request": task_summary["original_request"]
                    }
                )
                
            except Exception as e:
                logger.error(f"❌ Failed to store preferences: {e}")
        
        if task_queue.global_queue:
            logger.info(f"📋 Processing next task from global queue")
        
        return {"status": "completed"}

    # Build graph
    graph.add_node("analyze", analyze_and_plan)
    graph.add_node("execute", execute_tasks)
    graph.add_node("feedback", send_feedback)

    graph.set_entry_point("analyze")
    
    def route_after_analysis(state):
        return "execute"

    graph.add_conditional_edges("analyze", route_after_analysis)
    graph.add_edge("execute", "feedback")
    graph.add_edge("feedback", END)

    return graph.compile(checkpointer=checkpointer)

async def execute_single_task(
    task: ActionTask,
    session_id: str,
    original_message_id: str,
    user_language: str = "en",
    output_language: str = "en",
    user_profile: Optional[Dict[str, Any]] = None,
) -> TaskResult:
    """Execute a single task via action/reasoning layer or mobile strategy"""
    
    # ════════════════════════════════════════════════════════════════
    # WEB TASK LOGGING
    # ════════════════════════════════════════════════════════════════
    if task.context == "web":
        logger.info(f"🌐 WEB TASK: {task.task_id}")
        logger.info(f"   ai_prompt: {task.ai_prompt}")
        logger.info(f"   web_params: {json.dumps(task.web_params, indent=2)}")
        logger.info("   ℹ️  Note: URL and selectors will be resolved by execution layer via RAG")
    
    # ════════════════════════════════════════════════════════════════
    # MOBILE TASK ROUTING (NEW)
    # ════════════════════════════════════════════════════════════════
    if task.device == "mobile" and task.target_agent == "action":
        # Route to mobile strategy directly (skip broker)
        logger.info(f"📱 Mobile task detected: {task.task_id}")
        try:
            from agents.execution_agent.handlers.mobile_action_handler import (
                initialize_mobile_handler, get_mobile_handler
            )
            
            # Get device_id - use android_device_1 by default for Flutter apps
            device_id = task.extra_params.get("device_id", "android_device_1")
            
            logger.info(f"📱 Using device: {device_id}")
            
            # Initialize if needed
            initialize_mobile_handler(device_id=device_id)
            handler = await get_mobile_handler()
            
            # Execute directly
            result = await handler.handle_action_task(
                task_data=task.model_dump(),
                task_id=task.task_id,
                session_id=session_id
            )
            
            return TaskResult(
                task_id=task.task_id,
                status=result.status,
                content=getattr(result, 'details', '') or getattr(result, 'content', '') or '',
                error=result.error
            )
        except Exception as e:
            logger.error(f"❌ Mobile task execution failed: {e}", exc_info=True)
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                error=f"Mobile execution error: {str(e)}"
            )
    
    # ════════════════════════════════════════════════════════════════
    # DESKTOP TASK ROUTING (ORIGINAL)
    # ════════════════════════════════════════════════════════════════
    
    # Route to appropriate agent
    if task.target_agent == "action":
        channel = Channels.COORDINATOR_TO_EXECUTION
        receiver = AgentType.EXECUTION
    else:
        channel = Channels.COORDINATOR_TO_REASONING
        receiver = AgentType.REASONING
    
    task_payload = task.model_dump()
    if task.target_agent == "reasoning":
        task_payload["user_language"] = user_language
        # Pass output_language (may differ from user_language when user requests a different
        # language for the task output, e.g. Arabic user asking for an English summary)
        task_payload["output_language"] = output_language or user_language
        extra_params = task_payload.get("extra_params") or {}
        if not isinstance(extra_params, dict):
            extra_params = {}
        extra_params["language"] = output_language or user_language
        # Carry user_profile so Reasoning Agent can personalize its output style
        if user_profile:
            extra_params["user_profile"] = user_profile
        task_payload["extra_params"] = extra_params
        # Also set at top level for direct access
        task_payload["user_profile"] = user_profile or {}

    # Create message
    task_msg = AgentMessage(
        message_type=MessageType.EXECUTION_REQUEST,
        sender=AgentType.COORDINATOR,
        receiver=receiver,
        session_id=session_id,
        task_id=task.task_id,
        response_to=original_message_id,
        payload=task_payload
    )
    
    # Create future for response
    future = create_guarded_future(task.task_id)
    pending_results[task.task_id] = future
    
    # Publish
    logger.info(f"📤 Publishing task {task.task_id} to {receiver}")
    await broker.publish(channel, task_msg)
    
    # Wait for result
    try:
        result_payload = await asyncio.wait_for(future, timeout=60)
        payload_status = result_payload.get("status", "failed")
        if payload_status not in {"success", "failed", "pending", "awaiting_confirmation"}:
            payload_status = "failed"

        content = result_payload.get("content")
        if not content:
            content = result_payload.get("details")

        # Fix Pydantic validation error: Convert dict content to JSON string
        if isinstance(content, dict):
            content = json.dumps(content, indent=2)  # Pretty format for readability

        return TaskResult(
            task_id=task.task_id,
            status=payload_status,
            content=content,
            error=result_payload.get("error"),
            details=result_payload.get("details"),
            metadata=result_payload.get("metadata") or {},
            needs_clarification=bool(result_payload.get("needs_clarification", False)),
            clarification_question=result_payload.get("clarification_question"),
            clarification_type=result_payload.get("clarification_type"),
            recoverable=bool(result_payload.get("recoverable", False)),
        )
    except asyncio.TimeoutError:
        logger.error(f"⏰ Task {task.task_id} timeout after 60 seconds")
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error="Task timeout"
        )
    finally:
        pending_results.pop(task.task_id, None)

# Initialize graph
coordinator_graph = create_coordinator_graph()

# --- Broker Integration ---
async def start_coordinator_agent(broker_instance):
    """Start Coordinator Agent with broker"""
    
    async def handle_task_from_language(message: AgentMessage):
        """Handle task from Language Agent"""
        
        http_request_id = message.response_to if message.response_to else message.message_id
        user_id = message.payload.get("user_id", "default_user")
        session_id = message.session_id
        # Resolve the user's preferred language early so all thinking step updates
        # are shown in the correct language from the very first step.
        user_language = message.payload.get("user_language") or "en"

        # No "received" step — "preparing_for_coordinator" in the Language Agent
        # already tells the user we've got it. Showing another step here just adds
        # visual noise without conveying new information.
        
        # Log a helpful summary of the incoming payload: prefer the confirmation text if present
        payload_summary = message.payload.get('confirmation') or message.payload.get('action') or str(message.payload)
        try:
            payload_json = json.dumps(message.payload, default=str)
        except Exception:
            payload_json = str(message.payload)
        logger.info(f"📨 Coordinator received confirmation: {payload_summary} | full_payload: {payload_json}")
        was_queued = coordinator_processing_lock.locked()
        if was_queued:
            logger.info("📥 Another request is executing — waiting for coordinator lock")
            await ThinkingStepManager.update_step(
                session_id,
                "queued_request",
                http_request_id,
                language=user_language
            )

        state_input = {
            "input": message.payload,
            "session_id": session_id,
            "original_message_id": http_request_id,
            "user_id": user_id,
            "conversation_history": []
        }
        config = {
            "configurable": {
                "thread_id": session_id,
                "user_id": user_id
            }
        }

        async with coordinator_processing_lock:
            # Only emit "figuring out how to do this" before the heavy LLM decomposition.
            # We deliberately skip a post-completion step — ThinkingStepManager.clear_steps
            # is called by the server after the response, which removes the indicator cleanly.
            await ThinkingStepManager.update_step(
                session_id,
                "preparing_tasks",
                http_request_id,
                language=user_language
            )

            try:
                result = await coordinator_graph.ainvoke(state_input, config)
            except KeyError as _ke:
                if str(_ke) == "'step'":
                    logger.warning(
                        f"⚠️ Corrupt LangGraph checkpoint for session {session_id} "
                        f"(missing 'step' key) — clearing checkpoint and retrying fresh"
                    )
                    # Clear the corrupt checkpoint from MongoDB
                    try:
                        if checkpointer and session_id:
                            mongo_client["yusr_db"]["langgraph_checkpoints"].delete_many(
                                {"thread_id": session_id}
                            )
                            logger.info(f"🗑️ Cleared corrupt checkpoint for session {session_id}")
                    except Exception as _clear_err:
                        logger.warning(f"⚠️ Could not clear checkpoint: {_clear_err}")
                    # Retry without checkpoint (fresh graph run)
                    result = await coordinator_graph.ainvoke(state_input, None)
                else:
                    raise

            logger.info(f"✅ Task processing complete: {result.get('status')}")

  
    async def handle_action_result(message: AgentMessage):
        """
        Handle result from Action/Reasoning layer (ASYNC-SAFE VERSION)
        
        ✅ FIXES:
        - InvalidStateError from duplicate set_result() calls
        - Race conditions in future handling
        - Silent failures when results arrive out of order
        """
        task_id = message.task_id
        result_status = message.payload.get('status', 'unknown')
        
        logger.info(f"📬 Result for {task_id}: {result_status}")
        
        # Check if we're expecting this result
        if task_id not in pending_results:
            logger.warning(f"⚠️ Received result for unknown task {task_id} (may have timed out)")
            return
        
        future = pending_results[task_id]
        
        # ✅ CRITICAL FIX: Guard against duplicate results
        if future.done():
            logger.warning(
                f"⚠️ Task {task_id} already resolved - "
                f"ignoring duplicate result with status '{result_status}'"
            )
            return
        
        # ✅ Safe to set result now
        try:
            future.set_result(message.payload)
            logger.debug(f"✅ Successfully set result for task {task_id}")
        except asyncio.InvalidStateError as e:
            # This should never happen now, but log if it does
            logger.error(
                f"❌ Unexpected InvalidStateError for {task_id}: {e}\n"
                f"Future state: done={future.done()}, cancelled={future.cancelled()}"
            )
        except Exception as e:
            logger.error(f"❌ Unexpected error setting result for {task_id}: {e}")
    
    async def handle_interrupt_command(message: AgentMessage):
        """Handle pause/stop/resume commands with context snapshot support"""
        command = message.payload.get("command")
        
        if command == "pause":
            task_queue.pause()
        elif command == "resume":
            task_queue.resume()
        elif command == "stop":
            # Save context snapshot before stopping for potential undo/resume
            try:
                completed = [
                    {
                        "task_id": e["task"].get("task_id"),
                        "ai_prompt": e["task"].get("ai_prompt"),
                        "status": e["result"].get("status"),
                    }
                    for e in task_queue.execution_history
                    if e["result"]["status"] == "success"
                ]
                pending = [
                    {
                        "task_id": t.task_id,
                        "ai_prompt": t.ai_prompt,
                        "target_agent": t.target_agent,
                    }
                    for t in list(task_queue.current_queue)
                ]
                
                snapshot = ContextSnapshot(
                    session_id=message.session_id,
                    user_id=message.payload.get("user_id", "unknown"),
                    original_request=message.payload.get("original_request", ""),
                    completed_tasks=completed,
                    pending_tasks=pending,
                    current_task_state={"task_id": task_queue.current_task_id} if task_queue.current_task_id else None,
                    execution_outputs={
                        e["task"]["task_id"]: e["result"].get("content", "")
                        for e in task_queue.execution_history
                        if e["result"]["status"] == "success"
                    },
                    is_reversible=len(completed) > 0
                )
                
                # Publish snapshot via WebSocket for frontend undo capability
                snapshot_msg = AgentMessage(
                    message_type=MessageType.TASK_PROGRESS,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=message.session_id,
                    response_to=message.message_id,
                    payload={
                        "type": "context_snapshot",
                        "snapshot": snapshot.model_dump()
                    }
                )
                await broker_instance.publish(Channels.WEBSOCKET_OUTPUT, snapshot_msg)
                logger.info(f"📸 Saved context snapshot: {len(completed)} completed, {len(pending)} pending")
            except Exception as e:
                logger.error(f"❌ Failed to save context snapshot: {e}")
            
            task_queue.stop()
        elif command == "retry":
            # Retry from last failed task
            retry_tasks = task_queue.retry_from_failed()
            if retry_tasks:
                task_queue.add_to_current(retry_tasks)
                task_queue.resume()
                logger.info(f"🔄 Retrying from failed task ({len(retry_tasks)} tasks)")
        
        # Send acknowledgment
        ack_msg = AgentMessage(
            message_type=MessageType.INTERRUPT_ACK,
            sender=AgentType.COORDINATOR,
            receiver=AgentType.LANGUAGE,
            session_id=message.session_id,
            response_to=message.message_id,
            payload={"status": "acknowledged", "command": command}
        )
        await broker_instance.publish(Channels.COORDINATOR_TO_LANGUAGE, ack_msg)
        
        # Also send ack via WebSocket for real-time UI update
        await broker_instance.publish(Channels.WEBSOCKET_OUTPUT, ack_msg)
    
    async def handle_session_control(message: AgentMessage):
        """Handle session reset"""
        command = message.payload.get("command")
        session_id = message.session_id
        
        if command == "start_new_chat":
            try:
                await save_checkpoint_compat(session_id, None, {"cleared": True})
                logger.info(f"🗑️ Cleared session history for {session_id}")
            except Exception as e:
                logger.error(f"❌ Failed to clear session: {e}")

            confirm_msg = AgentMessage(
                message_type=MessageType.TASK_RESPONSE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                response_to=message.message_id,
                payload={
                    "status": "Success",
                    "response": "Started a new conversation. Previous session memory cleared."
                }
            )
            await broker_instance.publish(Channels.COORDINATOR_TO_LANGUAGE, confirm_msg)
    
    # Subscribe to channels
    broker_instance.subscribe(Channels.LANGUAGE_TO_COORDINATOR, handle_task_from_language)
    broker_instance.subscribe(Channels.EXECUTION_TO_COORDINATOR, handle_action_result)
    broker_instance.subscribe(Channels.REASONING_TO_COORDINATOR, handle_action_result)
    broker_instance.subscribe(Channels.INTERRUPT_CONTROL, handle_interrupt_command)
    broker_instance.subscribe(Channels.SESSION_CONTROL, handle_session_control)
    
    logger.info("✅ Coordinator Agent started with RAG action layer support")
    
    while True:
        await asyncio.sleep(1)
