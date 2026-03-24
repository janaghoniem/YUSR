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
    ExecutionResult, TaskMessage
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
except Exception as e:
    logger.error(f"❌ Failed to initialize MongoDB checkpointer: {e}")
    checkpointer = None


# 
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
    depends_on: Optional[str] = None
    
    class Config:
        use_enum_values = True

class TaskResult(BaseModel):
    """Result from action/reasoning layer"""
    task_id: str
    status: Literal["success", "failed", "pending"]
    content: Optional[str] = None
    error: Optional[str] = None

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
    
    # ✅ FIX 5: Update thinking step
    if session_id and http_request_id:
        await ThinkingStepManager.update_step(
            session_id,
            "⚙️ Preparing tasks...",
            http_request_id
        )
    
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
- **context**: "local" (desktop apps) or "web" (browser automation)

# TARGET AGENTS

- **action**: UI automation (click, type, navigate)
- **reasoning**: Logic tasks (summarize, analyze)

# TASK STRUCTURE

Each task must have:
- **ai_prompt**: Natural language instruction (CRITICAL: this is used by RAG to determine URLs and selectors)
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
VALID EXAMPLES
============================

## Example 1: Simple Desktop Task

User: "Open Notepad"

Return:
[
  {{
    "task_id": "task_1",
    "ai_prompt": "Open Notepad application",
    "device": "desktop",
    "context": "local",
    "target_agent": "action",
    "extra_params": {{"app_name": "notepad"}},
    "web_params": {{}},
    "depends_on": null
  }}
]

## Example 2: Simple Web Navigation Task

User: "Go to Google"

Return:
[
  {{
    "task_id": "task_1",
    "ai_prompt": "Navigate to Google homepage",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "navigate"
    }},
    "depends_on": null
  }}
]

EXPLANATION: The ai_prompt "Navigate to Google homepage" will be sent to the web execution layer,
which will use RAG to generate Playwright code that includes the URL https://www.google.com.

## Example 3: Login Task (ANY WEBSITE)

User: "Login to Gmail with user@example.com and password mypass123"

Tasks:
[
  {{
    "task_id": "task_1",
    "ai_prompt": "Navigate to Gmail login page",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "navigate"
    }},
    "depends_on": null
  }},
  {{
    "task_id": "task_2",
    "ai_prompt": "Fill email field with user@example.com",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "fill",
      "text": "user@example.com"
    }},
    "depends_on": "task_1"
  }},
  {{
    "task_id": "task_3",
    "ai_prompt": "Fill password field with mypass123",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "fill",
      "text": "mypass123"
    }},
    "depends_on": "task_2"
  }},
  {{
    "task_id": "task_4",
    "ai_prompt": "Click login button",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "click"
    }},
    "depends_on": "task_3"
  }}
]

## Example 4: Composite Web Task (E-commerce Search)

User: "Search Amazon for white socks and extract the first 5 product titles"

Tasks:
[
  {{
    "task_id": "task_1",
    "ai_prompt": "Navigate to Amazon homepage",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "navigate"
    }},
    "depends_on": null
  }},
  {{
    "task_id": "task_2",
    "ai_prompt": "Fill Amazon search box with 'white socks'",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "fill",
      "text": "white socks"
    }},
    "depends_on": "task_1"
  }},
  {{
    "task_id": "task_3",
    "ai_prompt": "Click Amazon search button",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "click"
    }},
    "depends_on": "task_2"
  }},
  {{
    "task_id": "task_4",
    "ai_prompt": "Extract first 5 product titles from Amazon search results",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "extract"
    }},
    "depends_on": "task_3"
  }}
]

EXPLANATION: Each ai_prompt is descriptive enough for RAG to generate the correct
Playwright code with selectors, URLs, and wait strategies.

## Example 5: Mixed Desktop + Web Task

User: "Search Google for 'Playwright tutorial' and copy the first result title to Notepad"

Tasks:
[
  {{
    "task_id": "task_1",
    "ai_prompt": "Navigate to Google homepage",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "navigate"
    }},
    "depends_on": null
  }},
  {{
    "task_id": "task_2",
    "ai_prompt": "Fill Google search box with 'Playwright tutorial'",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "fill",
      "text": "Playwright tutorial"
    }},
    "depends_on": "task_1"
  }},
  {{
    "task_id": "task_3",
    "ai_prompt": "Extract the first search result title from Google",
    "device": "desktop",
    "context": "web",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{
      "action": "extract"
    }},
    "depends_on": "task_2"
  }},
  {{
    "task_id": "task_4",
    "ai_prompt": "Open Notepad application",
    "device": "desktop",
    "context": "local",
    "target_agent": "action",
    "extra_params": {{"app_name": "notepad"}},
    "web_params": {{}},
    "depends_on": "task_3"
  }},
  {{
    "task_id": "task_5",
    "ai_prompt": "Type the extracted search result title into Notepad",
    "device": "desktop",
    "context": "local",
    "target_agent": "action",
    "extra_params": {{"input_from": "task_3"}},
    "web_params": {{}},
    "depends_on": "task_4"
  }}
]

## Example 5: Mobile Configuration Task

User: "Set the alarm for 7 am"

Tasks:
[
  {{
    "task_id": "task_1",
    "ai_prompt": "Open the Clock app on mobile device",
    "device": "mobile",
    "context": "local",
    "target_agent": "action",
    "extra_params": {{"app_name": "clock"}},
    "web_params": {{}},
    "depends_on": null
  }},
  {{
    "task_id": "task_2",
    "ai_prompt": "Set the alarm time to 7:00 AM",
    "device": "mobile",
    "context": "local",
    "target_agent": "action",
    "extra_params": {{"time": "7:00"}},
    "web_params": {{}},
    "depends_on": "task_1"
  }},
  {{
    "task_id": "task_3",
    "ai_prompt": "Press OK or Save to confirm the alarm setting",
    "device": "mobile",
    "context": "local",
    "target_agent": "action",
    "extra_params": {{}},
    "web_params": {{}},
    "depends_on": "task_2"
  }}
]

# CRITICAL RULES

1. **One action per task** - never combine multiple actions
2. **Explicit dependencies** - if task B needs task A's output, set "depends_on"
3. **Descriptive prompts** - ai_prompt should be detailed enough for RAG to understand
4. **Correct context** - web tasks get context: "web", desktop tasks get "local"
5. **Minimal web_params** - ONLY include action type and text (for fill), nothing else
6. **NO URLs** - NEVER hardcode URLs, let RAG resolve them from ai_prompt
7. **NO selectors** - NEVER hardcode selectors, let RAG find them from ai_prompt
8. **Empty web_params** - For local tasks, set web_params: {{}}
9. **Include confirmation steps** - For configuration tasks (alarms, forms, settings), always add a final task to confirm/save changes

============================
OUTPUT RULES
============================

Return ONLY valid JSON array of tasks (no markdown, no explanations):
[
  {{
    "task_id": <string>,
    "ai_prompt": <string>,
    "device": <"desktop" | "mobile">,
    "context": <"local" | "web">,
    "target_agent": <"action" | "reasoning">,
    "extra_params": <object>,
    "web_params": <object>,
    "depends_on": <string | null>
  }},
  ...
]

Generate the task decomposition now:"""

    try:
        response = await llm.ainvoke(prompt)
        response_text = response.content if hasattr(response, 'content') else str(response)
        response_text = response_text.strip()

        if response_text.startswith("```"):
            parts = response_text.split("```")
            response_text = parts[1] if len(parts) > 1 else response_text
            if response_text.strip().startswith("json"):
                response_text = response_text.strip()[4:]

        parsed = json.loads(response_text.strip())

        if isinstance(parsed, list):
            action_tasks = [ActionTask(**task) for task in parsed]
        elif isinstance(parsed, dict) and "tasks" in parsed:
            action_tasks = [ActionTask(**task) for task in parsed["tasks"]]
        else:
            raise ValueError("Invalid task decomposition format")

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
                        config={"configurable": {"thread_id": session_id}}
                    )
                    if checkpoint_data and "execution_state" in checkpoint_data:
                        previous_execution_state = checkpoint_data["execution_state"]
                        logger.info(f"🔄 Found previous execution state")
                except Exception as e:
                    logger.debug(f"No previous execution state: {e}")
            
            preferences_context = pref_mgr.get_relevant_preferences(
                str(raw_task.get("confirmation", "")), limit=5
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
        
        task_queue.reset()
        task_queue.add_to_current(tasks)
        
        results = {}
        task_outputs = {}

        if checkpointer and session_id:
            try:
                execution_state={
                    "completed_task_ids": list(results.keys()),
                    "failed_task_ids":task_queue.get_failed_index(),
                    "remaining_tasks": [t.task_id for t in list(task_queue.current_queue)],
                    "timestamp": datetime.now().isoformat()
                }
                await checkpointer.aput(
                    config={"configurable": {"thread_id": session_id}},
                    checkpoint={"execution_state": execution_state},
                    metadata = {"type": "task_progress"},
                    new_versions=[]
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
                dep_ids = current_task.depends_on.split(",")
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
            
            # Execute task
            logger.info(f"🔄 Executing {current_task.task_id}: {current_task.ai_prompt[:50]}...")
            result = await execute_single_task(current_task, session_id, original_message_id)
            
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

                
            if result.status == "failed":
                logger.error(f"❌ Task {current_task.task_id} failed: {result.error}")
                break
        
        return {
            **state,
            "results": results,
            "status": "completed",
            "session_id": session_id,
            "original_message_id": original_message_id
        }

    async def send_feedback(state: Dict) -> Dict:
        """STEP 3: Send results to Language Agent"""
        results = state.get("results", {})
        session_id = state.get("session_id")
        original_message_id = state.get("original_message_id")
        user_id = state.get("user_id", "default_user")
        
        success_count = sum(1 for r in results.values() if r.status == "success")
        total_count = len(results)
        
        # ✅ FIX 3: Update conversation history
        if success_count > 0:
            if "conversation_history" not in state:
                state["conversation_history"] = []
            
            state["conversation_history"].append({
                # Prefer the 'confirmation' payload from the Language Agent; fall back to 'action' for compatibility
                "user_message": state['input'].get('confirmation', state['input'].get('action', '')),
                "action_taken": f"Executed {success_count} tasks",
                "result": "success" if success_count == total_count else "partial",
                "timestamp": datetime.now().isoformat()
            })
            
            # Keep only last 10 interactions
            if len(state["conversation_history"]) > 10:
                state["conversation_history"] = state["conversation_history"][-10:]
        
        if success_count == total_count and total_count > 0:
            response_text = f"Task completed successfully! Executed {success_count} steps."
        elif success_count > 0:
            response_text = f"Partially completed: {success_count}/{total_count} steps succeeded."
        else:
            response_text = "Task could not be completed. Please try again."
        
        response_msg = AgentMessage(
            message_type=MessageType.TASK_RESPONSE,
            sender=AgentType.COORDINATOR,
            receiver=AgentType.LANGUAGE,
            session_id=session_id,
            response_to=original_message_id,
            payload={
                "status": "success" if success_count > 0 else "failed",
                "response": response_text,
                "result": {
                    "completed_tasks": {k: v.status for k, v in results.items()},
                    "details": [v.model_dump() for v in results.values()]
                }
            }
        )
        
        logger.info(f"📤 Sending feedback: {response_text}")
        await broker.publish(Channels.COORDINATOR_TO_LANGUAGE, response_msg)
        
        if success_count == total_count and total_count > 0:
            try:
                from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
                pref_mgr = get_preference_manager(user_id)
                
                task_summary = {
                    # Prefer confirmation from Language Agent for a faithful representation of the user's intent
                    "original_request": state['input'].get('confirmation', state['input'].get('action', '')),
                    "completed_steps": [t.ai_prompt for t in state['tasks']],
                    "total_steps": total_count
                }
                
                extraction_prompt = f"""Based on this completed task, extract user preferences for future tasks.

COMPLETED TASK:
{json.dumps(task_summary, indent=2)}

RULES:
- Only extract repeatable preferences (app choices, workflows, patterns)
- Ignore one-time actions
- Format as clear statements

OUTPUT FORMAT (JSON array):
[
  {{
    "preference": "User prefers Chrome for web browsing",
    "category": "app_usage",
    "confidence": "high"
  }}
]

If NO preferences, return: []

Extract now:"""
                
                extraction_response = await llm.ainvoke(extraction_prompt)
                extraction_text = extraction_response.content if hasattr(extraction_response, 'content') else str(extraction_response)

                extraction_text = extraction_text.strip()
                if extraction_text.startswith("```"):
                    extraction_text = extraction_text.split("```")[1]
                    if extraction_text.startswith("json"):
                        extraction_text = extraction_text[4:]

                preferences_to_store = json.loads(extraction_text.strip())

                if preferences_to_store and isinstance(preferences_to_store, list):
                    for pref_obj in preferences_to_store:
                        if pref_obj.get("confidence") in ["high", "medium"]:
                            try:
                                pref_mgr.add_preference(
                                    pref_obj["preference"],
                                    metadata={
                                        "category": pref_obj.get("category", "general"),
                                        "confidence": pref_obj.get("confidence", "medium"),
                                        "extracted_from": task_summary["original_request"]
                                    }
                                )
                                logger.info(f"💾 Stored preference: {pref_obj['preference']}")
                            except Exception as pref_err:
                                logger.debug(f"⚠️ Could not store individual preference: {pref_err}")
                
                try:
                    conversation_context = f"User requested: {task_summary['original_request']}. "
                    conversation_context += f"Successfully completed {success_count} steps."
                    
                    pref_mgr.add_preference(
                        conversation_context,
                        metadata={
                            "category": "conversation_history",
                            "session_id": session_id,
                            "timestamp": datetime.now().isoformat()
                        }
                    )
                    logger.info(f"💾 Stored conversation context")
                except Exception as ctx_err:
                    logger.debug(f"⚠️ Could not store conversation context: {ctx_err}")
                
            except Exception as e:
                logger.debug(f"⚠️ Preference storage operation encountered issue: {e}")

                
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
    original_message_id: str
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
                content=result.details,
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
    
    # Create message
    task_msg = AgentMessage(
        message_type=MessageType.EXECUTION_REQUEST,
        sender=AgentType.COORDINATOR,
        receiver=receiver,
        session_id=session_id,
        task_id=task.task_id,
        response_to=original_message_id,
        payload=task.model_dump()  # ← Also fix deprecated .dict() to .model_dump()
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
        return TaskResult(**result_payload)
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

        # ✅ FIX 5: STEP 1
        await ThinkingStepManager.update_step(
            session_id, 
            "👀 Received your request...", 
            http_request_id
        )
        
        # Log a helpful summary of the incoming payload: prefer the confirmation text if present
        payload_summary = message.payload.get('confirmation') or message.payload.get('action') or str(message.payload)
        try:
            payload_json = json.dumps(message.payload, default=str)
        except Exception:
            payload_json = str(message.payload)
        logger.info(f"📨 Coordinator received confirmation: {payload_summary} | full_payload: {payload_json}")

        if task_queue.has_tasks() and not task_queue.is_stopped:
            logger.info("📥 Adding task to global queue (currently executing)")
            task_queue.add_to_global(message.payload)
            return
        
        state_input = {
            "input": message.payload,
            "session_id": session_id,
            "original_message_id": http_request_id,
            "user_id": user_id,
            "conversation_history": []  # ✅ FIX 3: Initialize empty if not present
        }
        config = {
            "configurable": {
                "thread_id": session_id,
                "user_id": user_id
            }
        }

        # ✅ FIX 5: STEP 2 (before decomposition)
        await ThinkingStepManager.update_step(
            session_id, 
            "🧠 Analyzing your request...", 
            http_request_id
        )

        result = await coordinator_graph.ainvoke(state_input, config)
        logger.info(f"✅ Task processing complete: {result.get('status')}")
        
        # ✅ FIX 5: STEP 3
        await ThinkingStepManager.update_step(
            session_id, 
            "📋 Creating execution plan...", 
            http_request_id
        )
    
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
        """Handle pause/stop/resume commands"""
        command = message.payload.get("command")
        
        if command == "pause":
            task_queue.pause()
        elif command == "resume":
            task_queue.resume()
        elif command == "stop":
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
            message_type=MessageType.TASK_RESPONSE,
            sender=AgentType.COORDINATOR,
            receiver=AgentType.LANGUAGE,
            session_id=message.session_id,
            response_to=message.message_id,
            payload={"status": "acknowledged", "command": command}
        )
        await broker_instance.publish(Channels.COORDINATOR_TO_LANGUAGE, ack_msg)
    
    async def handle_session_control(message: AgentMessage):
        """Handle session reset"""
        command = message.payload.get("command")
        session_id = message.session_id
        
        if command == "start_new_chat":
            try:
                await checkpointer.aput(
                    config={"configurable": {"thread_id": session_id}},
                    checkpoint=None,
                    metadata={"cleared": True},
                    new_versions=[]
                )
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