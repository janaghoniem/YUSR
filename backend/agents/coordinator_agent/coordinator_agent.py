from unittest import result
import os, uuid, asyncio, json, re
# import contextvars
from dotenv import load_dotenv   
from langgraph.func import task
from langgraph.graph import StateGraph, END
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator
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
from agents.coordinator_agent.utils.intent_classifier import ExecutionMode  # kept for backward compat
from utils.semantic_intent import is_relevant_to_task, relevance_score
from ThinkingStepManager import ThinkingStepManager
from routes.cross_platform_manager import get_cross_platform_manager

# ICRL imports — In-Context Reinforcement Learning (arXiv:2506.06303)
from agents.ICRL.icrl_buffer import ICRLBuffer
from agents.ICRL.icrl_reward_bridge import compute_reward, summarize_task_attempt, classify_failure_type
from agents.ICRL.icrl_prompt_builder import inject_icrl_into_decomposition_prompt, inject_icrl_into_execution_prompt

ICRL_ENABLED = True  # Enabled for desktop; mobile is gated per-task below

logger = logging.getLogger(__name__)
load_dotenv()

# --- Initialize LLMs ---
from .config.settings import LLM_MODEL, GROQ_API_KEY, MONGODB_URI
from langchain_groq import ChatGroq
from mistralai import Mistral

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL","mistral-medium-latest")
EXECUTION_PAUSED = os.getenv("AURA_EXECUTION_PAUSED", "false").strip().lower() in {"1", "true", "yes"}

llm = ChatGroq(
    model=LLM_MODEL,
    temperature=0.05,
    max_tokens=2048,
    # max_retries=0,          # fail fast → Mistral fallback handles retries
    groq_api_key=GROQ_API_KEY
) if GROQ_API_KEY else None

mistral_client = Mistral(api_key=MISTRAL_API_KEY) if MISTRAL_API_KEY else None


def _extract_mistral_text(response_obj: Any) -> str:
    try:
        choices = getattr(response_obj, "choices", None) or []
        if not choices:
            return str(response_obj)
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(getattr(item, "text", item)))
            content = "".join(parts)
        return str(content or "")
    except Exception:
        return ""


async def llm_invoke_with_fallback(prompt: str) -> str:
    if mistral_client:
        try:
            response = await asyncio.to_thread(
                mistral_client.chat.complete,
                model=MISTRAL_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.05,
                max_tokens=2048,
            )
            text = _extract_mistral_text(response)
            if text:
                logger.info(f"✅ Coordinator primary succeeded with Mistral ({MISTRAL_MODEL})")
                return text
            logger.warning("⚠️ Coordinator Mistral returned an empty response; trying Groq backup.")
        except Exception as mistral_err:
            logger.warning(f"⚠️ Coordinator Mistral call failed, trying Groq backup: {mistral_err}")
    else:
        logger.warning("⚠️ Coordinator Mistral client unavailable: MISTRAL_API_KEY is not set")

    if not llm:
        logger.error("❌ Coordinator Groq backup unavailable: GROQ_API_KEY is not set")
        return ""

    try:
        response = await llm.ainvoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        if text:
            logger.info(f"✅ Coordinator backup succeeded with Groq ({LLM_MODEL})")
        return text
    except Exception as groq_err:
        logger.error(f"❌ Coordinator Groq backup failed: {groq_err}")
        return ""


# Initialize MongoDB checkpointer
try:
    mongo_client = MongoClient(MONGODB_URI)
    mongo_client.admin.command('ping')
    checkpointer = MongoDBSaver(
        mongo_client, 
        db_name="yusr_db",
        collection_name="langgraph_checkpoints"
    )
    # Register custom Pydantic types so LangGraph's msgpack serializer can
    # deserialize them from checkpoints without warnings or future crashes.
    # Without this, any checkpoint saved with ActionTask/TaskResult in state
    # will emit a warning now and raise an error in a future LangGraph version.
    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        _serde = checkpointer.serde if hasattr(checkpointer, 'serde') else None
        if _serde and hasattr(_serde, 'allowed_msgpack_modules'):
            _mod = "agents.coordinator_agent.coordinator_agent"
            if _mod not in _serde.allowed_msgpack_modules:
                _serde.allowed_msgpack_modules.append(_mod)
                logger.info(f"✅ Registered {_mod} in LangGraph msgpack allowlist")
    except Exception as _reg_err:
        logger.debug(f"⚠️ Could not register msgpack modules (non-fatal): {_reg_err}")
    logger.info("✅ Initialized MongoDB checkpointer for LangGraph")

    # Clean up any corrupt checkpoints that are missing the 'step' metadata key.
    # These cause a KeyError crash when LangGraph tries to resume the session.
    try:
        _cp_col = mongo_client["yusr_db"]["langgraph_checkpoints"]
        _deleted = _cp_col.delete_many({
            "$or": [
                {"metadata.step": {"$exists": False}},
                {"metadata": {"$exists": False}},
                {"metadata": None},
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

    def _balanced_json_candidates(raw_text: str):
        """Yield balanced JSON object/array substrings from a noisy response."""
        seen = set()

        def _scan(open_char: str, close_char: str):
            start = 0
            while True:
                start = raw_text.find(open_char, start)
                if start == -1:
                    return

                depth = 0
                in_string = False
                escape_next = False
                for idx, ch in enumerate(raw_text[start:], start):
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == "\\" and in_string:
                        escape_next = True
                        continue
                    if ch == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == open_char:
                        depth += 1
                    elif ch == close_char:
                        depth -= 1
                        if depth == 0:
                            candidate = raw_text[start:idx + 1]
                            if candidate not in seen:
                                seen.add(candidate)
                                yield candidate
                            break
                start += 1

        yield raw_text
        yield from _scan("[", "]")
        yield from _scan("{", "}")

    for json_candidate in _balanced_json_candidates(candidate):
        try:
            return json.loads(json_candidate)
        except Exception:
            continue
    return default


def _coerce_task_dict_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an LLM decomposition payload into a list of task dicts."""
    if not isinstance(payload, list):
        return []

    task_dicts: List[Dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            task_dicts.append(item)
            continue

        if isinstance(item, str):
            parsed_item = extract_json_payload(item, None)
            if isinstance(parsed_item, dict):
                task_dicts.append(parsed_item)
                continue
            try:
                stripped_item = item.strip()
                if stripped_item.startswith("{") and stripped_item.endswith("}"):
                    parsed_item = json.loads(stripped_item)
                    if isinstance(parsed_item, dict):
                        task_dicts.append(parsed_item)
            except Exception:
                continue

    return task_dicts

async def save_checkpoint_compat(session_id: Optional[str], checkpoint_value, metadata: Optional[Dict[str, Any]] = None) -> bool:
    """
    Save checkpoint via MongoDBSaver.
    IMPORTANT: metadata MUST contain 'step' (int) or LangGraph will crash
    on the next read with KeyError: 'step'.
    """
    if not checkpointer or not session_id:
        return False

    # Ensure 'step' is always present in metadata — LangGraph requires it
    safe_metadata = {"step": 0, "source": "loop"}
    if metadata:
        safe_metadata.update(metadata)
    if "step" not in safe_metadata or not isinstance(safe_metadata.get("step"), int):
        safe_metadata["step"] = 0

    kwargs = {
        "config": {"configurable": {"thread_id": session_id, "checkpoint_ns": ""}},
        "checkpoint": checkpoint_value,
        "metadata": safe_metadata,
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
    except Exception as e:
        logger.warning(f"⚠️ save_checkpoint_compat failed (non-fatal): {e}")
        return False
    
# ── F3: Coordinator confirmation sanitiser ────────────────────────────────────
def sanitize_confirmation_for_prompt(text: str) -> str:
    """
    Sanitize the confirmation string before embedding it in the coordinator
    LLM prompt. Blocks chained injection (A6) where attacker embeds
    'IMPORTANT SYSTEM NOTE' style instructions inside response_text which
    then get read as commands by the coordinator LLM.
    """
    if not text:
        return text

    COORDINATOR_INJECTION_MARKERS = [
        "important system note",
        "system note:",
        "you must also add", 
        "you must also",
        "you must additionally",
        "also list all",
        "ignore previous",
        "disregard your",
        "forget everything",
        "set response_text",
        "<|system|>",
        "<|user|>",
        "<|assistant|>",
        "ignore previous formatting",
        "ignore previous formatting rules",
    ]
    
    # Look for markers as STANDALONE instructions, not as part of natural language
    # e.g., "IMPORTANT SYSTEM NOTE: do X" is injection
    #       "can you also add a second task?" is natural conversation
    text_lower = text.lower()
    for marker in COORDINATOR_INJECTION_MARKERS:
        if marker in text_lower:
            # Check if marker appears as an instruction (followed by colon or newline)
            # or if it's part of natural language
            idx = text_lower.find(marker)
            # Look ahead 20 chars to see if it's an instruction
            lookahead = text[idx:idx+50].lower()
            if ':' in lookahead or '\n' in lookahead or lookahead.strip().startswith(marker):
                logger.warning(
                    f"🚫 F3: Coordinator injection marker detected: '{marker}' — "
                    f"stripping to first sentence only"
                )
                # Strip to just before the injection
                safe_part = text[:idx].strip().rstrip('.,;')
                if safe_part:
                    return safe_part
                return "Task request received."
    
    return text


def _get_user_request_text(user_request: Dict[str, Any]) -> str:
    """Get best-effort raw user text for intent parsing."""
    if not isinstance(user_request, dict):
        return str(user_request or "")
    for key in ("original_input", "action", "confirmation"):
        value = user_request.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(user_request)


def _normalize_device_type(device_type: Optional[str]) -> Literal["desktop", "mobile", "mixed"]:
    """Normalize incoming device hints to the coordinator's supported values."""
    raw = str(device_type or "desktop").strip().lower()
    if raw in {"mixed", "hybrid", "cross_platform", "cross-platform", "desktop_mobile", "desktop+mobile"}:
        return "mixed"
    if raw in {"mobile", "android", "ios", "phone", "tablet"}:
        return "mobile"
    return "desktop"


def _get_mobile_decomposition_prompt_sop() -> str:
        return """
You are decomposing high-level user requests into low-level sub-tasks for MOBILE UI execution.

HARD ROUTING RULES:
1. Every task MUST use device: "mobile".
2. Every task MUST use context: "local".
3. NEVER use target_agent: "api" on mobile.
4. Mobile handles email, browsing, and app operations via local automation tasks.
5. Even when the user says "open Chrome and search for X", you MUST use context: "local".
6. Mobile browser opening, navigation, and typing are handled by the local automation layer.

TARGET AGENTS:
- action: UI automation (tap, type, navigate, fill forms, extract text)
- reasoning: Logic tasks (summarize, analyze, write, translate, generate content)
- language: Confirmation/read-aloud tasks (read generated content to user, ask for confirmation)

WHEN TO USE target_agent: "reasoning" vs "action"

- Extracting text from UI, files, or webpages (e.g., “read the price”, “get the error message”, “copy the visible text”) -> ALWAYS target_agent: "action".
- Reasoning is for content generation, summarisation, translation, or analysis AFTER text has been extracted by an action task.
- If the user asks to understand or interpret extracted content, use two tasks:
        1. Action task to extract the raw text.
        2. Reasoning task that depends on the action task and receives the text via extra_params["input_content"].
- "action": Tasks that interact with the OS, apps, files, or browser (open file, read file, click, type, navigate, fill, screenshot, etc.). A reasoning component CANNOT open files or applications.
- "reasoning": Tasks that generate, summarize, analyze, research, write, translate, or answer questions. Content creation (stories, essays, code, emails, poems) is ALWAYS reasoning. If a task does NOT require interacting with a UI element or file system, it is reasoning.

Examples of REASONING tasks on mobile:
- "Write a scary story" -> reasoning
- "Summarize this article" -> reasoning
- "Solve these math problems" -> reasoning
- "Translate this to Arabic" -> reasoning
- "Draft an email to my boss" -> reasoning (IMPORTANT: ai_prompt must request SUBJECT and BODY together)
- "Explain quantum computing" -> reasoning
- "Generate a Python script" -> reasoning

EMAIL ON MOBILE:
- Do NOT use API/email agent routing.
- Build local app steps (open mail app, compose, fill fields, send).
- If subject/body are not explicitly provided, add one reasoning task first to generate SUBJECT and BODY.
- Add language confirmation before send for generated content ONLY.

GENERAL RULES:
1. One action per task - never combine multiple actions, except for search submission where typing the query and submitting (Enter/click) may be combined in a single fill task
2. Explicit dependencies - if task B needs task A's output, set "depends_on"
3. Descriptive prompts - ai_prompt should be detailed enough for RAG to understand
4. Correct context - all mobile tasks use context: "local"
5. Minimal extra_params - ONLY include action type and text (for fill), nothing else
6. Include URLs for known sites only when mobile browser navigation is explicit
7. NO selectors - NEVER hardcode CSS selectors, let RAG find them from ai_prompt
8. Empty web_params - For local tasks, set web_params: {}
9. Include confirmation steps - For configuration tasks (emails, forms, settings), always add a final task to confirm/save changes
10. Content generation = reasoning - Writing, summarizing, translating, or any creative/analytical task MUST use target_agent: "reasoning"
11. Shared goal - Every task in the output must include a non-empty "goal" and it must be exactly the same across all tasks in that decomposition
12. Research communication - For informational or research queries (e.g., 'check the weather', 'latest news', 'nearest pharmacy'), ensuring the result is communicated back to the disabled user is critical. You MUST include a final task with target_agent: "reasoning" that depends on the search results extracted and returned by an "action" agent content extraction task and formats them into a natural, helpful conversational response.
13. Confirmation for sensitive actions – When a task generates content that will be sent or committed (e.g., composing an email then sending it, sending a message, submitting a form), you MUST insert a confirmation task AFTER generation but BEFORE the final send action. NOTE: this applies to generated content ONLY. If the user explicitly provides the content (e.g., "Set the alarm for 7 am"), you do NOT need a confirmation task for the time value. But if the user asks you to "Draft an email to my boss about missing tomorrow's meeting" and you generate the email content, you MUST add a confirmation task to read back the generated email and ask for approval before sending. For mobile, this confirmation task should use target_agent: "language" with ai_prompt that reads out the generated content and asks for user confirmation or critique before proceeding to any action tasks that interact with the UI or send information.
  - The confirmation task must have:
    - target_agent: "language"
    - ai_prompt: "(e.g., Read out the generated content. Ask the user to confirm or critique this task. Wait for their response.)"
    - extra_params: Must contain {{"input_from": "<generation_task_id>"}} so the language agent actually receives the text to read out.
    - depends_on: the generation task
  - The send task must depend on the confirmation task.
  - If a user replies with a **critique** or asks for revisions on previously generated content (e.g., "make it shorter", "sound more professional"), treat it as a NEW modification request. You MUST generate a fresh pipeline to revise the content (using target_agent: "reasoning" with the old content and user critique), fill the revised content, and once again append a language confirmation task before sending. 
  - The Language Agent will handle the user interaction and signal approval or return the user's critique.
    - CRITICAL: When an action task comes AFTER a confirmation task (e.g., sending content after user confirms), the action task must ALSO include {"input_from": "<generation_task_id>"} to receive the ORIGINAL generated content, NOT the confirmation task's output. Example: if task_1 generates email, task_2 confirms it, then task_3 sends it, task_3 should have input_from: "task_1", not the confirmation task.

# DEVICE & CONTEXT

- **device**: "mobile"
- **context**:
    - "local" -> for all mobile tasks, including web browsing on mobile.

Mobile examples below are the only examples in this SOP.

============================
EXAMPLES (MOBILE)
============================

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
    extra_params: {}
    web_params: {}
    depends_on: null

- task_id: task_2
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Read out the generated email SUBJECT and BODY to the user and ask for confirmation/critique before opening any app or sending it. Wait for their response.
    device: mobile
    context: local
    target_agent: language
    extra_params:
        input_from: "task_1"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Navigate to Gmail
    device: mobile
    context: local
    target_agent: action
    extra_params:
        app_name: gmail
    depends_on: ["task_2"]

- task_id: task_4
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Compose new email to sara@gmail.com
    device: mobile
    context: local
    target_agent: action
    extra_params:
        recipient: sara@gmail.com
    depends_on: ["task_3"]

- task_id: task_5
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Fill the Subject field with the SUBJECT value from the composed email
    device: mobile
    context: local
    target_agent: action
    extra_params: {}
    depends_on: ["task_1", "task_4"]

- task_id: task_6
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Fill the email body with the BODY value from the composed email
    device: mobile
    context: local
    target_agent: action
    extra_params: {}
    depends_on: ["task_1", "task_5"]

- task_id: task_7
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Click the Send button to send the email
    device: mobile
    context: local
    target_agent: action
    extra_params: {}
    depends_on: ["task_6"]

EXPLANATION: task_1 (reasoning) returns {"SUBJECT": "...", "BODY": "..."}.
task_2 explicitly takes input_from: "task_1" so the language agent reads the generated content to the user for confirmation BEFORE any action tasks start.
task_3 navigates Gmail and depends on task_2. task_4 fills the To field directly (known from the user request). tasks 5-6 receive the JSON as input_content from task_1 so the action layer can parse SUBJECT and BODY individually. They also depend sequentially on task_4/5.

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
    success_message: "Clock app opened"
    failure_message: "Failed to open Clock app"
    depends_on: null

- task_id: task_2
    goal: Set an alarm for 7:00 AM
    ai_prompt: Set the alarm time to 7:00 AM
    device: mobile
    context: local
    target_agent: action
    extra_params:
        time: "7:00"
    success_message: "Alarm time set to 7:00 AM"
    failure_message: "Failed to set alarm time"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Set an alarm for 7:00 AM
    ai_prompt: Press OK or Save to confirm the alarm setting
    device: mobile
    context: local
    target_agent: action
    success_message: "Alarm set successfully for 7:00 AM"
    failure_message: "Failed to confirm alarm setting"
    depends_on: ["task_2"]

# SUCCESS/FAILURE MESSAGE RULES

- Every task MUST have both success_message and failure_message
- Messages should be brief (1-2 sentences), user-friendly, and action-oriented
- Success messages should convey what was accomplished (e.g., "Email sent to Sara")
- Failure messages should suggest next steps (e.g., "Check your connection and retry")
- These messages will be vocalized to the user via thinking steps as tasks execute
- Use the user's language only if confirmed in conversation history

============================
OUTPUT RULES
============================

Return ONLY a valid JSON array of tasks (no markdown, no explanations, no extra text before or after).  
Follow this exact schema for each task object:

[
  {{
    "task_id": <string, unique>,
    "goal": <string, identical for all tasks in this decomposition>,
    "ai_prompt": <string>,
    "device": "mobile",
    "context": "local",
    "target_agent": "action" | "reasoning" | "language",
    "extra_params": <object>,
    "web_params": <object>,
    "depends_on": <array of strings | null>,
    "success_message": <string, required>,
    "failure_message": <string, required>
  }},
  ...
]

IMPORTANT:  
- Every task MUST include both `success_message` and `failure_message` (short, user‑friendly sentences).  
- Do NOT output any text outside the JSON array.  
- Do NOT wrap the JSON in ````json```` fences – return raw JSON.

Generate the task decomposition now:"""


def _get_desktop_decomposition_prompt_sop() -> str:
        return """
============================
DESKTOP COORDINATOR SOP
============================

You are decomposing tasks for DESKTOP execution.

This SOP contains the desktop/web/email subset of the original coordinator
prompt. Keep every desktop, web, and email example, and preserve the full
Google/API routing guidance.

HARD ROUTING RULES:
1. Every task MUST use device: "desktop".
2. Use context: "web" for browser automation and "local" for native OS/app tasks.
3. The api agent is desktop-only and available here.
4. Desktop browser automation uses context: "web".
5. Desktop native app and OS tasks use context: "local".

TARGET AGENTS (desktop):
- action: UI automation (click, type, navigate, fill forms, extract text)
- reasoning: Logic tasks (summarize, analyze, write, translate, generate content)
- language: Confirmation/read-aloud tasks (read generated content to user, ask for confirmation)
- api: Google API operations (YouTube search, Calendar, Drive, Gmail send). ALWAYS use this for API calls.

WHEN TO USE target_agent: "reasoning" vs "action"

- Extracting text from UI, files, or webpages (e.g., “read the price”, “get the error message”, “copy the visible text”) -> ALWAYS target_agent: "action".
- Reasoning is for content generation, summarisation, translation, or analysis AFTER text has been extracted by an action task.
- If the user asks to understand or interpret extracted content, use two tasks:
        1. Action task to extract the raw text.
        2. Reasoning task that depends on the action task and receives the text via extra_params["input_content"].
- "action": Tasks that interact with the OS, apps, files, or browser (open file, read file, click, type, navigate, fill, screenshot, etc.). A reasoning component CANNOT open files or applications.
- "reasoning": Tasks that generate, summarize, analyze, research, write, translate, or answer questions. Content creation (stories, essays, code, emails, poems) is ALWAYS reasoning. If a task does NOT require interacting with a UI element or file system, it is reasoning.

Examples of REASONING tasks:
- "Write a scary story" -> reasoning
- "Summarize this article" -> reasoning
- "Solve these math problems" -> reasoning
- "Translate this to Arabic" -> reasoning
- "Draft an email to my boss" -> reasoning (IMPORTANT: ai_prompt must request SUBJECT and BODY together)
- "Explain quantum computing" -> reasoning
- "Generate a Python script" -> reasoning

EMAIL/API RULES:
- For direct send-email operations on desktop, you may route to target_agent: "api".
- For Google API tasks (YouTube search/info, Calendar, Drive), prefer target_agent: "api".
- For browser watch/play/navigation requests, use target_agent: "action" with context "web".

EMAIL TASK RULES (CRITICAL)
When the user asks to compose, send, or draft an email:

a) ALWAYS generate a reasoning task FIRST that produces the email content fields IF user doesnt give email subject and content, only a description.
     e.g. user request: "Reschedule tomorrow's meeting with Sara" -- this implies an email but doesn't give subject or body. role of reasoning task: "Compose a complete email for this request: 'Reschedule tomorrow's meeting with Sara'. Return a JSON object with keys SUBJECT and BODY."
     user request: "Send an email to Sara about rescheduling tomorrow's meeting. Subject should be 'Meeting Rescheduled' and body should say 'Hi Sara, ...'" -- reasoning task is NOT needed here because subject and body are explicitly provided by user.
     The reasoning task ai_prompt must say:
     "Compose a complete email for this request: <user intent>.
        Return a JSON object with keys SUBJECT and BODY."
     The reasoning task output will be a JSON object injected as input_content, e.g.:
     {"SUBJECT": "Meeting Rescheduled", "BODY": "Hi Sara, ..."}

b) The action tasks that fill Subject and Body MUST depend on the reasoning task, if present, and
     will receive the generated JSON in extra_params["input_content"].

⚠️ EMAIL ROUTING FALLBACK (Critical for typos):
If the user request contains any variation of "email", "send", "compose", "draft", or related keywords,
ALWAYS route to target_agent: "api" with operation: "send" REGARDLESS of what mail app is mentioned.
DO NOT generate desktop UI automation tasks like pyautogui or keyboard control for email send operations.
Email sending ALWAYS uses the Gmail API endpoint which is atomic and reliable.

REASON: Desktop UI automation for email is fragile (login screens, multi-step forms, permissions).
The email API is 100% reliable and handles all Gmail/OAuth edge cases.

TYPO HANDLING: Input like "sedn email", "sned mail", "compose mesage" should STILL route to api agent.

✅ GOOGLE API ROUTING (Gmail, YouTube, Calendar, Drive, Cookies):
If the user request contains keywords for Google API operations, route to target_agent: "api" with the appropriate operation:

GMAIL / EMAIL OPERATIONS:
- Keywords: "send email", "send mail", "compose email", "write email", "draft email", "email to",
  "mail to", "shoot an email", "send a message to", "forward email", "reply to email",
  "sedn email", "sned mail" (typos), "بعت ايميل", "ارسل رسالة", "اكتب ايميل"
    -> operation: "send" with to/subject/body in extra_params
- Keywords: "read my emails", "check my inbox", "show unread emails", "inbox", "new emails",
  "latest emails", "my messages", "what emails do I have", "اقرأ ايميلاتي", "شوف الإيميلات"
    -> operation: "list_emails" with max_results/label_ids in extra_params
- Keywords: "read email", "open email", "show email", "email content", "what does the email say",
  "اقرأ الإيميل ده", "افتح الرسالة"
    -> operation: "read_email" with message_id in extra_params
- Keywords: "search emails", "find email", "email from", "look for email", "دور على إيميل"
    -> operation: "search_emails" with query in extra_params
- Keywords: "delete email", "trash email", "remove email", "امسح الإيميل"
    -> operation: "delete_email" with message_id in extra_params
- Keywords: "mark as read", "mark read", "اعمله مقروء"
    -> operation: "mark_read" with message_id in extra_params
- Keywords: "reply", "reply to", "respond to email", "رد على الإيميل"
    -> operation: "reply_email" with message_id/body in extra_params

YOUTUBE OPERATIONS:
- Keywords: "youtube search", "search on youtube", "find videos", "youtube video", "video on youtube",
  "search youtube", "look up on youtube", "find on youtube", "ابحث على يوتيوب", "دور على فيديو",
  "what videos are there", "find a video about", "youtube for"
    -> operation: "youtube_search" with query in extra_params
- Keywords: "youtube info", "video info", "how many views", "video details", "video statistics",
  "likes on video", "who made this video", "channel info", "video duration", "video description",
  "كم مشاهدة", "معلومات الفيديو"
    -> operation: "youtube_video_info" with video_url in extra_params
- Keywords: "my youtube subscriptions", "channels I follow", "subscribed channels"
    -> operation: "youtube_subscriptions" with max_results in extra_params
- Keywords: "youtube trending", "trending videos", "what's trending", "popular videos"
    -> operation: "youtube_search" with query: "trending" in extra_params
- Keywords: "youtube comments", "video comments", "comments on the video"
    -> operation: "youtube_video_info" with video_url in extra_params (comments are included in info)

GOOGLE CALENDAR OPERATIONS:
- Keywords: "create event", "add event", "schedule", "calendar", "meeting", "appointment",
  "set a reminder", "add to calendar", "book a slot", "put it in my calendar", "remind me",
  "schedule a call", "اعمل موعد", "ضيف حاجة في التقويم", "حدد موعد"
    -> operation: "calendar_create" with title/start_time/end_time/description in extra_params
- Keywords: "list events", "show calendar", "upcoming events", "my calendar", "upcoming meetings",
  "what's on my calendar", "do I have anything", "what are my plans", "show my schedule",
  "what meetings do I have", "إيه اللي عندي", "شوف تقويمي", "مواعيدي"
    -> operation: "calendar_list" with max_results in extra_params
- Keywords: "delete event", "cancel meeting", "remove appointment", "امسح الموعد", "الغ الاجتماع"
    -> operation: "calendar_delete" with event_id in extra_params
- Keywords: "update event", "reschedule", "change meeting time", "edit appointment",
  "move the meeting", "غير موعد", "عدل الحدث"
    -> operation: "calendar_update" with event_id/updated fields in extra_params
- Keywords: "get event", "event details", "what time is the meeting", "تفاصيل الحدث"
    -> operation: "calendar_get" with event_id in extra_params

GOOGLE DRIVE OPERATIONS:
- Keywords: "upload file", "upload to drive", "save to drive", "drive upload", "put file on drive",
  "store on drive", "ارفع ملف", "احفظ على درايف"
    -> operation: "drive_upload" with file_path/parent_folder_id in extra_params
- Keywords: "list files", "my drive files", "files on drive", "drive list", "show my files",
  "what files do I have", "my documents", "show drive", "browse drive", "ملفاتي", "شوف درايف"
    -> operation: "drive_list" with max_results in extra_params
- Keywords: "search files", "find file", "find document", "look for file", "search my drive",
  "search in drive", "do I have a file", "دور على ملف", "ابحث في درايف", "find in my files",
  "search in my files", "look in drive", "is there a file"
    -> operation: "drive_search" with query in extra_params
- Keywords: "download file", "get file from drive", "fetch from drive", "حمل الملف", "جيب الملف"
    -> operation: "drive_download" with file_id/destination_path in extra_params
- Keywords: "delete file from drive", "remove from drive", "امسح من درايف"
    -> operation: "drive_delete" with file_id in extra_params
- Keywords: "share file", "share on drive", "give access to file", "شارك الملف"
    -> operation: "drive_share" with file_id/email/role in extra_params
- Keywords: "create folder", "make folder on drive", "new folder in drive", "اعمل فولدر"
    -> operation: "drive_create_folder" with name/parent_folder_id in extra_params
- Keywords: "rename file", "rename on drive", "change file name", "غير اسم الملف"
    -> operation: "drive_rename" with file_id/new_name in extra_params
- Keywords: "move file", "move to folder", "نقل الملف"
    -> operation: "drive_move" with file_id/new_parent_id in extra_params

BROWSER COOKIE INJECTION (for Google web automation):
- Keywords: "access google", "login to google", "google credentials", "browser login",
  "use my google account", "authenticate google", "sign in to google"
    -> operation: "get_browser_cookies" (enables seamless Google property access without UI login)

ROUTING RULES:
1. ALWAYS prefer API operations (Gmail, YouTube, Calendar, Drive) over browser automation
2. API operations are ATOMIC, RELIABLE, and FASTER than UI automation
3. Only use browser automation if user explicitly asks to "watch" a video, "see" calendar UI, etc.
4. For API operations, set: target_agent: "api", context: "web", web_params: {}
5. Example: User says "Search YouTube for cat videos" ->
     {
         "target_agent": "api",
         "operation": "youtube_search",
         "ai_prompt": "Search YouTube for cat videos and return results",
         "extra_params": {"operation": "youtube_search", "query": "cat videos"},
         "web_params": {}
     }
6. Example: User says "Find the file report.pdf on my Drive" ->
     {
         "target_agent": "api",
         "operation": "drive_search",
         "ai_prompt": "Search Google Drive for a file named report.pdf",
         "extra_params": {"operation": "drive_search", "query": "report.pdf"},
         "web_params": {}
     }

HYBRID WORKFLOWS:
- Search YouTube (API) -> Extract results (reasoning) -> Watch in browser (action) is a valid 3-task flow
- But never duplicate: don't search YouTube both via API AND browser automation in same task plan

Examples of ACTION tasks:
- "Open the file worksheet.txt" -> action
- "Read text from the file" -> action
- "Open Notepad" -> action
- "Click the submit button" -> action
- "Navigate to google.com" -> action
- "Type 'hello' in the search box" -> action

# TASK STRUCTURE

Each task must have:
- ai_prompt: Natural language instruction (CRITICAL: this is used by RAG to determine URLs and selectors)
- goal: The SAME high-level goal string for the entire task plan (must be identical in all tasks)
- device: "desktop" or "mobile"
- context: "local" or "web"
- target_agent: "action" or "reasoning" or "language" or "api"
- extra_params: Additional data (app_name, file_path, operation, url, etc.)
- web_params: Web-specific parameters
- depends_on: task_id of prerequisite task
- success_message: (REQUIRED) User-friendly message to vocalize/display if task succeeds
- failure_message: (REQUIRED) User-friendly message to vocalize/display if task fails

# WEB_PARAMS STRUCTURE (for context: "web")

Do NOT hardcode CSS selectors or wait strategies. The execution layer uses RAG to determine selectors and interaction strategies from ai_prompt.
You MAY construct homepage URLs for well-known sites (Google, YouTube, Gmail, Amazon, etc.).
Do NOT construct search URLs for any site. For searches, navigate to the site homepage first and then use interaction tasks to enter the query and submit.
For unknown sites, put enough detail in ai_prompt and let RAG resolve navigation.

SEARCH WORKFLOW RULES:
- "open google and search for X" -> task 1: navigate to https://www.google.com, task 2: type X in the search box and submit (press Enter or click Search).
- "search for X on Amazon" -> task 1: navigate to https://www.amazon.com, task 2: type X in the search box and submit.
- If a browser tab is already open on the target site, skip the navigation step and only perform the search input + submit.

CRITICAL DISTINCTIONS:
- "open google and search for X" -> Google search (multi-step), NOT YouTube
- "play X" / "watch X" -> YouTube search (multi-step), NOT Google
- "search for X" without mentioning a specific site -> default to Google search (multi-step)
- "search youtube for X" -> YouTube search (multi-step)
- "open the link named X" / "click on X" / "open the X result" WHEN A PAGE IS ALREADY OPEN -> CLICK on the current page, do NOT guess a URL
- Only construct navigation URLs for well-known site homepages or when the user provides an explicit URL

SEARCH DESTINATION AWARENESS (IMPORTANT):
Some sites navigate directly to the content page after a search (no results list).
Examples: Wikipedia, dictionary sites, documentation sites, encyclopedias.
Other sites show a results list first that requires a click (Google, YouTube, Amazon, Bing).

RULE: After any search, check what URL the browser is now on.
- If the URL already contains the article/content (e.g., /wiki/Topic, /word/Topic, /docs/Topic) -> the browser is already on content. Do NOT generate a "click first result" task. Go directly to extraction.
- If the URL is a search results page (e.g., /search?q=, /results?, /find?) -> generate a click task to open the first result, THEN extract.

HOW TO DETERMINE WHICH CASE APPLIES AT DECOMPOSITION TIME:
- If the user says "search [site] for X and read/extract/get..." and the site is known to go direct (Wikipedia, MDN, dictionaries) -> 3-task plan: navigate -> search -> extract
- If the user says "search [site] for X and read/extract/get..." and the site shows results first (Google, YouTube, Amazon) -> 4-task plan: navigate -> search -> click result -> extract
- If unsure -> default to the 4-task pattern (safer, the click will be a no-op if already on content)

For navigation tasks WITH a known URL (explicit URL or homepage URL only):
{
    "action": "navigate",
    "url": "<explicit URL or homepage URL>"
}
extra_params must ALSO include: {"action": "navigate", "url": "<same URL>"}

Never encode a search query into the URL for search tasks.

For navigation tasks WITHOUT a known URL (unknown/unfamiliar sites):
{
    "action": "navigate"
}
Put the site name or description in ai_prompt — RAG will resolve the URL.
For interaction tasks (click, fill):
{
    "action": "fill",
    "text": "search query"
}

For extraction tasks:
{
    "action": "extract"
}

Summary:
- DO construct homepage URLs for well-known sites (Google, YouTube, Gmail, Amazon, etc.)
- DO use homepage navigation + search box interaction for any search
- DO put descriptive text in ai_prompt for RAG to use
- Do not hardcode CSS selectors — RAG finds them from the live page
- Do not hardcode wait strategies — the execution layer handles timing
- Do not guess URLs for unknown sites — let RAG resolve from ai_prompt

# CRITICAL RULES

1. One action per task - never combine multiple actions
2. Explicit dependencies - if task B needs task A's output, set "depends_on"
3. Descriptive prompts - ai_prompt should be detailed enough for RAG to understand
4. Correct context - web tasks get context: "web", desktop tasks get "local"
5. Minimal extra_params - ONLY include action type and text (for fill), nothing else
6. Include URLs for well-known site homepages or explicit URLs only - Do NOT construct search URLs. Use a multi-step flow (open site -> search input -> submit).
7. NO selectors - NEVER hardcode CSS selectors, let RAG find them from ai_prompt
8. Empty web_params - For local tasks, set web_params: {}
9. Include confirmation steps - For configuration tasks (alarms, forms, settings), always add a final task to confirm/save changes
10. Content generation = reasoning - Writing, summarizing, translating, or any creative/analytical task MUST use target_agent: "reasoning"
11. Shared goal - Every task in the output must include a non-empty "goal" and it must be exactly the same across all tasks in that decomposition
12. Research communication - For informational or research queries (e.g., 'check the weather', 'latest news', 'nearest pharmacy'), ensuring the result is communicated back to the disabled user is critical. You MUST include a final task with target_agent: "reasoning" that depends on the search results extracted and returned by an "action" agent content extraction task and formats them into a natural, helpful conversational response.
13. Confirmation for sensitive actions – When a task generates content that will be sent or committed (e.g., composing an email then sending it, sending a message, submitting a form), you MUST insert a confirmation task AFTER generation but BEFORE the final send action. NOTE: This ONLY applies to reasoning tasks that generate content to be sent/committed. It does NOT apply to pure action tasks (e.g., "set an alarm for 7 am" does NOT require confirmation because it's a direct action with no generated content).
        - target_agent: "language"
        - ai_prompt: "(e.g., Read out the generated content. Ask the user to confirm or critique this task. Wait for their response.)"
        - extra_params: Must contain {"input_from": "<generation_task_id>"} so the language agent actually receives the text to read out.
        - depends_on: the generation task
    - The send task must depend on the confirmation task.
    - If a user replies with a critique or asks for revisions on previously generated content (e.g., "make it shorter", "sound more professional"), treat it as a NEW modification request. You MUST generate a fresh pipeline to revise the content (using target_agent: "reasoning" with the old content and user critique), fill the revised content, and once again append a language confirmation task before sending.
    - The Language Agent will handle the user interaction and signal approval or return the user's critique.
    - CRITICAL: When an action task comes AFTER a confirmation task (e.g., typing/saving content after user confirms), the action task must ALSO include {"input_from": "<generation_task_id>"} to receive the ORIGINAL generated content, NOT the confirmation task's output. Example: if task_2 generates content, task_3 confirms it, then task_4 types it, task_4 should have input_from: "task_2", not the confirmation task.

============================
EXAMPLES (DESKTOP/WEB/EMAIL)
============================

## Example 0: Browser Navigation — Google Search (Multi-step)

User: "open google and search for web automation"

- task_id: task_1
    goal: open google and search for web automation
    ai_prompt: Navigate to https://www.google.com
    device: desktop
    context: web
    target_agent: action
    extra_params:
        action: navigate
        url: "https://www.google.com"
    web_params:
        action: navigate
        url: "https://www.google.com"
    success_message: "Google homepage loaded successfully"
    failure_message: "Failed to open Google. Please check your internet connection"
    depends_on: null

- task_id: task_2
    goal: open google and search for web automation
    ai_prompt: Type "web automation" in the Google search box and submit the search (press Enter or click Search)
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: fill
        text: "web automation"
    success_message: "Search submitted successfully"
    failure_message: "Failed to submit the search. Please try again"
    depends_on: ["task_1"]

EXPLANATION: Do NOT hardcode search URLs. Open the homepage first, then perform the search via the search box.

## Example 0-DIRECT: Search on a site that navigates directly to content (no results page)

User: "search Wikipedia for artificial intelligence and read the first paragraph"
(Same pattern applies to: dictionary lookups, documentation sites, encyclopedia searches)

- task_id: task_1
    goal: Search for artificial intelligence and read the first paragraph
    ai_prompt: Navigate to https://www.wikipedia.org
    device: desktop
    context: web
    target_agent: action
    extra_params:
        action: navigate
        url: "https://www.wikipedia.org"
    web_params:
        action: navigate
        url: "https://www.wikipedia.org"
    success_message: "Site loaded successfully"
    failure_message: "Could not reach the site"
    depends_on: null

- task_id: task_2
    goal: Search for artificial intelligence and read the first paragraph
    ai_prompt: Type "artificial intelligence" in the search box and press Enter to submit
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: fill
        text: "artificial intelligence"
    success_message: "Search submitted, content page loaded"
    failure_message: "Search failed"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Search for artificial intelligence and read the first paragraph
    ai_prompt: Extract the full text of the first paragraph from the article page. Return only the raw paragraph text without formatting or metadata.
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: extract
    success_message: "First paragraph extracted successfully"
    failure_message: "Could not extract paragraph text"
    depends_on: ["task_2"]

EXPLANATION: This site's search navigates directly to the content page (no intermediate results list).
After task_2 the browser is already on the article, so task_3 extracts immediately.
NEVER generate a "click first result" task when the search already landed on content.
Key signal: URL contains a content path (/wiki/Topic, /docs/Topic, /word/Topic) rather than a query string (/search?q=Topic).

## Example 0-RESULTS: Search on a site that shows a results list first

User: "search Google for artificial intelligence and read the first result"

- task_id: task_1
    goal: Search Google for artificial intelligence and read the first result
    ai_prompt: Navigate to https://www.google.com
    device: desktop
    context: web
    target_agent: action
    extra_params:
        action: navigate
        url: "https://www.google.com"
    web_params:
        action: navigate
        url: "https://www.google.com"
    success_message: "Google loaded"
    failure_message: "Could not reach Google"
    depends_on: null

- task_id: task_2
    goal: Search Google for artificial intelligence and read the first result
    ai_prompt: Type "artificial intelligence" in the Google search box and press Enter
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: fill
        text: "artificial intelligence"
    success_message: "Search results loaded"
    failure_message: "Search failed"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Search Google for artificial intelligence and read the first result
    ai_prompt: Click on the first organic search result link to open the page
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: click
    success_message: "Opened first result"
    failure_message: "Could not click first result"
    depends_on: ["task_2"]

- task_id: task_4
    goal: Search Google for artificial intelligence and read the first result
    ai_prompt: Extract the main content or first paragraph from the opened page. Return only the raw text.
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: extract
    success_message: "Content extracted successfully"
    failure_message: "Could not extract content"
    depends_on: ["task_3"]

EXPLANATION: Google shows a results list, so a click task is required before extraction.
Signal: URL after search is a query string (/search?q=Topic) rather than a content path.

## Example 0b: Browser Navigation — Play/Watch Video

User: "play relaxing music"

- task_id: task_1
    goal: play relaxing music
    ai_prompt: Navigate to https://www.youtube.com
    device: desktop
    context: web
    target_agent: action
    extra_params:
        action: navigate
        url: "https://www.youtube.com"
    web_params:
        action: navigate
        url: "https://www.youtube.com"
    success_message: "YouTube opened successfully"
    failure_message: "Could not reach YouTube. Check your connection and try again"
    depends_on: null

- task_id: task_2
    goal: play relaxing music
    ai_prompt: Type "relaxing music" in the YouTube search box and submit the search (press Enter or click Search)
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: fill
        text: "relaxing music"
    success_message: "YouTube search submitted"
    failure_message: "Failed to submit the YouTube search"
    depends_on: ["task_1"]

EXPLANATION: "play" implies video/music, but searches must be multi-step (open site, then search).



## Example 0c: YouTube API Search (get structured results)

User: "search youtube for cat videos and give me the top 5 results"

- task_id: task_1
    goal: search youtube for cat videos and give me the top 5 results
    ai_prompt: Search YouTube for cat videos and return top 5 results
    device: desktop
    context: web
    target_agent: api
    extra_params:
        operation: youtube_search
        query: "cat videos"
        max_results: 5
    web_params: {}
    success_message: "Found top 5 cat videos. Here are the results:"
    failure_message: "YouTube search failed. Please check your internet connection"
    depends_on: null

EXPLANATION: When the user wants structured results (list of videos, titles, etc.) use target_agent: "api" with operation: "youtube_search". When they just want to watch/play, use target_agent: "action" with a YouTube URL (see Example 0b).

## Example 0d: Click a Link on the Current Page

Browser is currently open on: https://www.google.com/search?q=web+automation+papers
User: "open the link named Cybernaut"

- task_id: task_1
    goal: open the link named Cybernaut
    ai_prompt: Click on the link with text "Cybernaut" on the current page
    device: desktop
    context: web
    target_agent: action
    extra_params: {}
    web_params:
        action: click
    success_message: "Opened Cybernaut link. Page loaded successfully"
    failure_message: "Could not find or click the Cybernaut link. Please try again"
    depends_on: null

EXPLANATION: The user is referring to a link VISIBLE on the current Google search results page. Do NOT guess a URL like "https://www.cybernaut.com" — the actual href is unknown. Instead, generate a CLICK task and let the execution layer find the link by its text. This applies whenever the user says "open the link named X", "click on X", "open the X result", etc. while a page is already open.

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
    web_params: {}
    success_message: "Notepad opened successfully"
    failure_message: "Failed to open Notepad. Please try again"
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
    success_message: "Gmail login page loaded"
    failure_message: "Could not reach Gmail login page"
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
    success_message: "Email address entered"
    failure_message: "Could not enter email address. Please try again"
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
    success_message: "Password entered"
    failure_message: "Could not enter password. Please try again"
    depends_on: ["task_2"]

- task_id: task_4
    goal: Log in to Gmail with the provided credentials
    ai_prompt: Click the Sign In or Login submit button
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: click
    success_message: "Logged in to Gmail successfully"
    failure_message: "Login failed. Please check your credentials and try again"
    depends_on: ["task_3"]

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
    web_params: {}
    success_message: "Notepad opened successfully"
    failure_message: "Failed to open Notepad"
    depends_on: null

- task_id: task_2
    goal: Open Notepad and produce a scary story in it
    ai_prompt: Write a very scary story
    device: desktop
    context: local
    target_agent: reasoning
    extra_params: {}
    web_params: {}
    success_message: "Story written successfully"
    failure_message: "Failed to write story. Please try again"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Open Notepad and produce a scary story in it
    ai_prompt: Confirm the generated story with the user. Read it out loud and ask if they want to proceed with typing it into Notepad or make changes.
    device: desktop
    context: local
    target_agent: language
    extra_params:
            input_from: "task_2"
    web_params: {}
    depends_on: ["task_2"]

- task_id: task_4
    goal: Open Notepad and produce a scary story in it
    ai_prompt: Type the generated story text into the active Notepad window
    device: desktop
    context: local
    target_agent: action
    extra_params:
        input_from: "task_2"
    web_params: {}
    success_message: "Story pasted into Notepad. Check it out!"
    failure_message: "Failed to type story into Notepad"
    depends_on: ["task_3"]

============================
SUCCESS/FAILURE MESSAGE RULES
============================
- Every task MUST have both success_message and failure_message
- Messages should be brief (1-2 sentences), user-friendly, and action-oriented
- Success messages should convey what was accomplished (e.g., "Email sent to Sara")
- Failure messages should suggest next steps (e.g., "Check your connection and retry")
- These messages will be vocalized to the user via thinking steps as tasks execute
- Use the user's language only if confirmed in conversation history

============================
OUTPUT RULES
============================

Return ONLY a valid JSON array of tasks (no markdown, no explanations, no extra text before or after).  
Follow this exact schema for each task object:

[
  {{
    "task_id": <string, unique>,
    "goal": <string, identical for all tasks in this decomposition>,
    "ai_prompt": <string>,
    "device": "desktop",
    "context": "local" | "web",
    "target_agent": "action" | "reasoning" | "language" | "email",
    "extra_params": <object>,
    "web_params": <object>,
    "depends_on": <array of strings | null>,
    "success_message": <string, required>,
    "failure_message": <string, required>
  }},
  ...
]

IMPORTANT:  
- Every task MUST include both `success_message` and `failure_message` (short, user‑friendly sentences).  
- Do NOT output any text outside the JSON array.  
- Do NOT wrap the JSON in ````json```` fences – return raw JSON.

Generate the task decomposition now:"""


# def _get_mixed_decomposition_prompt_sop() -> str:
#     return """
# ============================
# MIXED COORDINATOR SOP (DESKTOP + MOBILE)
# ============================

# You decompose a user request into a **single dependency graph** where each task is explicitly assigned to either `desktop` or `mobile`.  
# You must respect the capabilities and constraints of each platform.

# ────────────────────────────────────────────────────────────────
# HARD ROUTING RULES
# ────────────────────────────────────────────────────────────────
# 1. Every task MUST set `device` to either "desktop" or "mobile" (never "mixed").
# 2. For `device="mobile"`:
#    - `context` MUST be "local".
#    - `target_agent` MUST be one of: `action`, `reasoning`, `language`.
#    - `target_agent` MUST NOT be `api` (the API agent is desktop‑only).
# 3. For `device="desktop"`:
#    - `context` MAY be "web" (browser automation) or "local" (native OS/app).
#    - `target_agent` MAY be `action`, `reasoning`, `language`, or `api`.
# 4. Keep **one action per task** – never combine multiple UI steps.
# 5. Use explicit `depends_on` whenever a task needs the output of a previous task.
# 6. Every task MUST include **both** `success_message` and `failure_message` (short, user‑friendly sentences).

# ────────────────────────────────────────────────────────────────
# CROSS‑DEVICE DATA FLOW (HANDOFF)
# ────────────────────────────────────────────────────────────────
# When a task on one device needs the **output** of a task on a different device:
# - **Do NOT** generate a handoff task – the system will insert it automatically.
# - Instead, set:
#   - `depends_on` = `["<task_id_of_producer>"]`
#   - `extra_params["input_from"] = "<task_id_of_producer>"`
# - The system will route the produced content to the consuming task.

# **Confirmation tasks (`target_agent: "language"`) MUST run on the user’s current (source) device** – they require user interaction.  
# If a confirmation task depends on output from another device, set `input_from` as above; the handoff will happen automatically.

# ────────────────────────────────────────────────────────────────
# CONTENT GENERATION + CONFIRMATION (CRITICAL)
# ────────────────────────────────────────────────────────────────
# When a task **generates content** that will later be sent, saved, or committed (e.g., drafting an email, writing a story, composing a message):
# 1. Use `target_agent: "reasoning"` to generate the content. Its `ai_prompt` must request a JSON object (e.g., `{"SUBJECT": "...", "BODY": "..."}`).
# 2. **Immediately after generation**, add a **confirmation task** (`target_agent: "language"`) that reads the generated content aloud and asks for user approval/critique.
#    - The confirmation task MUST have:
#      - `extra_params["input_from"] = "<generation_task_id>"`
#      - `depends_on = ["<generation_task_id>"]`
#    - The confirmation task runs on the **source device** (the one the user is currently using).
# 3. Only after the user confirms, add the final action task that sends/saves the content.
#    - That final action task MUST also have `input_from = "<generation_task_id>"` (not the confirmation task ID).
# 4. If the user gives a critique (e.g., “make it shorter”), you must generate a **new revision pipeline** (reasoning → language confirmation → send). The system will loop automatically.

# **Exceptions:** Pure action tasks (e.g., “set alarm for 7 AM”) do **not** need confirmation – they contain no generated content.

# ────────────────────────────────────────────────────────────────
# RESEARCH / INFORMATIONAL QUERIES
# ────────────────────────────────────────────────────────────────
# If the user asks for information (e.g., “check the weather”, “latest news”, “nearest pharmacy”):
# - First use `target_agent: "action"` with appropriate `context` to extract the raw data.
# - Then add a **final `reasoning` task** that depends on the extraction task and formats the data into a natural, helpful conversation. This reasoning task MUST run on the source device.

# ────────────────────────────────────────────────────────────────
# DEVICE‑SPECIFIC RULES
# ────────────────────────────────────────────────────────────────
# **DESKTOP**
# - Browser automation → `context: "web"`.
# - Native apps / OS → `context: "local"`.
# - API operations (YouTube search, Calendar, Drive, Gmail send) → `target_agent: "api"`.
# - For search workflows: open homepage → type query → submit → (if results page) click first result → extract. Construct URLs for well‑known homepages only.

# **MOBILE**
# - Everything is `context: "local"` – even web browsing.
# - No `api` agent – use `action` for UI automation (tap, type, navigate).
# - Email on mobile: use local app steps (open mail app → compose → fill fields → send). If the user does not provide subject/body, generate them with a `reasoning` task first.
# - Mobile action tasks are given a longer timeout (≥120 seconds) automatically.

# ────────────────────────────────────────────────────────────────
# EXAMPLES
# ────────────────────────────────────────────────────────────────

# ### Example 1: Cross‑device search + set alarm

# **User:** “Search YouTube on my desktop for ‘study music’, then set an alarm on my phone for 7 AM.”

# - **task_1** (desktop, web, action)  
#   goal: Search YouTube and set phone alarm  
#   ai_prompt: Navigate to YouTube and search for “study music”  
#   extra_params: {"action": "navigate", "url": "https://www.youtube.com/results?search_query=study+music"}  
#   web_params: {"action": "navigate", "url": "https://www.youtube.com/results?search_query=study+music"}  
#   success_message: “YouTube search results loaded.”  
#   failure_message: “Could not reach YouTube.”  
#   depends_on: null

# - **task_2** (mobile, local, action)  
#   goal: Search YouTube and set phone alarm  
#   ai_prompt: Open the Clock app on the phone  
#   extra_params: {"app_name": "clock"}  
#   web_params: {}  
#   success_message: “Clock app opened.”  
#   failure_message: “Failed to open Clock app.”  
#   depends_on: null   (no dependency – both start in parallel)

# - **task_3** (mobile, local, action)  
#   goal: Search YouTube and set phone alarm  
#   ai_prompt: Set alarm time to 7:00 AM  
#   extra_params: {"time": "7:00"}  
#   web_params: {}  
#   success_message: “Alarm set for 7 AM.”  
#   failure_message: “Could not set alarm.”  
#   depends_on: ["task_2"]

# **Note:** Desktop and mobile tasks run concurrently where dependencies allow. The system handles device routing.

# ### Example 2: Desktop reasoning → mobile action (with confirmation)

# **User:** “Write a short story on my desktop, then send it as a WhatsApp message from my phone.”

# - **task_1** (desktop, local, reasoning)  
#   goal: Write a short story and send via WhatsApp  
#   ai_prompt: Write a very short story (2–3 paragraphs). Return ONLY the story text, no extra commentary.  
#   extra_params: {}  
#   web_params: {}  
#   success_message: “Story written.”  
#   failure_message: “Failed to write story.”  
#   depends_on: null

# - **task_2** (source device = desktop, local, language)  
#   goal: Write a short story and send via WhatsApp  
#   ai_prompt: Read the generated story aloud to the user and ask: “Should I send this story via WhatsApp on your phone?” Wait for confirmation or critique.  
#   extra_params: {"input_from": "task_1"}  
#   web_params: {}  
#   success_message: “User confirmed.”  
#   failure_message: “User rejected the story.”  
#   depends_on: ["task_1"]

# - **task_3** (mobile, local, action)  
#   goal: Write a short story and send via WhatsApp  
#   ai_prompt: Open WhatsApp and navigate to a recent chat  
#   extra_params: {"app_name": "whatsapp"}  
#   web_params: {}  
#   success_message: “WhatsApp opened.”  
#   failure_message: “Could not open WhatsApp.”  
#   depends_on: ["task_2"]

# - **task_4** (mobile, local, action)  
#   goal: Write a short story and send via WhatsApp  
#   ai_prompt: Paste the story text into the message input field and send it.  
#   extra_params: {"input_from": "task_1"}  # uses the original story, not the confirmation output  
#   web_params: {}  
#   success_message: “Story sent via WhatsApp.”  
#   failure_message: “Failed to send story.”  
#   depends_on: ["task_3", "task_1"]

# ### Example 3: Mobile email composition (no desktop involvement)

# **User:** “Draft an email on my phone to Sara about tomorrow’s meeting.”

# - **task_1** (mobile, local, reasoning)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Compose an email for: “Reschedule tomorrow’s meeting with Sara”. Return a JSON object with keys SUBJECT and BODY.  
#   extra_params: {}  
#   web_params: {}  
#   success_message: “Email content generated.”  
#   failure_message: “Could not generate email.”  
#   depends_on: null

# - **task_2** (mobile, local, language)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Read the generated email subject and body to the user and ask for confirmation before opening the email app.  
#   extra_params: {"input_from": "task_1"}  
#   web_params: {}  
#   success_message: “User approved.”  
#   failure_message: “User rejected the email.”  
#   depends_on: ["task_1"]

# - **task_3** (mobile, local, action)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Open Gmail app  
#   extra_params: {"app_name": "gmail"}  
#   web_params: {}  
#   success_message: “Gmail opened.”  
#   failure_message: “Could not open Gmail.”  
#   depends_on: ["task_2"]

# - **task_4** (mobile, local, action)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Fill recipient with sara@gmail.com  
#   extra_params: {"recipient": "sara@gmail.com"}  
#   web_params: {}  
#   success_message: “Recipient added.”  
#   failure_message: “Could not add recipient.”  
#   depends_on: ["task_3"]

# - **task_5** (mobile, local, action)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Fill subject field with the SUBJECT value from task_1  
#   extra_params: {"input_from": "task_1"}  
#   web_params: {}  
#   success_message: “Subject filled.”  
#   failure_message: “Could not fill subject.”  
#   depends_on: ["task_1", "task_4"]

# - **task_6** (mobile, local, action)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Fill body with the BODY value from task_1  
#   extra_params: {"input_from": "task_1"}  
#   web_params: {}  
#   success_message: “Body filled.”  
#   failure_message: “Could not fill body.”  
#   depends_on: ["task_1", "task_5"]

# - **task_7** (mobile, local, action)  
#   goal: Draft and send email to Sara  
#   ai_prompt: Click the Send button  
#   extra_params: {}  
#   web_params: {}  
#   success_message: “Email sent to Sara.”  
#   failure_message: “Failed to send email.”  
#   depends_on: ["task_6"]

# ────────────────────────────────────────────────────────────────
# OUTPUT FORMAT
# ────────────────────────────────────────────────────────────────
# Return ONLY a valid JSON array of tasks. No markdown, no explanations, no extra text.

# Schema for each task object:
# {
#   "task_id": "string (unique)",
#   "goal": "string (identical for all tasks in this decomposition)",
#   "ai_prompt": "string",
#   "device": "desktop" | "mobile",
#   "context": "local" | "web",
#   "target_agent": "action" | "reasoning" | "language" | "api",
#   "extra_params": { ... },
#   "web_params": { ... },
#   "depends_on": ["task_id"] | null,
#   "success_message": "string (short, user‑friendly)",
#   "failure_message": "string (short, user‑friendly)"
# }

# Generate the task decomposition now:
# """

def _get_decomposition_prompt_sop(device_type: Literal["desktop", "mobile", "mixed"]) -> str:
    """Return the device-specific SOP for task decomposition."""
    if device_type == "mobile":
        return _get_mobile_decomposition_prompt_sop()
    if device_type == "mixed":
        return _get_mixed_decomposition_prompt_sop()

    return _get_desktop_decomposition_prompt_sop()


def _apply_device_specific_task_routing(task_dicts: List[Dict[str, Any]], device_type: Literal["desktop", "mobile", "mixed"]) -> None:
    """Enforce routing guarantees after LLM decomposition."""
    for task in task_dicts:
        if not isinstance(task, dict):
            continue

        extra_params = task.get("extra_params")
        if not isinstance(extra_params, dict):
            extra_params = {}

        target_agent = str(task.get("target_agent") or "action").strip().lower()
        if target_agent == "langauge":
            target_agent = "language"

        if device_type == "mobile":
            task["device"] = "mobile"
            task["context"] = "local"

            if target_agent == "api":
                task["target_agent"] = "action"
                extra_params["routing_note"] = "mobile_sop_api_agent_disabled"
                if not task.get("ai_prompt"):
                    task["ai_prompt"] = "Perform this requested operation in the mobile app using local automation."
                task["web_params"] = {}
            elif target_agent in {"action", "reasoning", "language"}:
                task["target_agent"] = target_agent
                task["web_params"] = {}
            else:
                task["target_agent"] = "action"
                task["web_params"] = {}
        elif device_type == "mixed":
            task_device = str(task.get("device") or "desktop").strip().lower()
            if task_device not in {"desktop", "mobile"}:
                task_device = "desktop"
            task["device"] = task_device

            if task_device == "mobile":
                task["context"] = "local"
                if target_agent == "api":
                    task["target_agent"] = "action"
                    extra_params["routing_note"] = "mixed_sop_mobile_api_agent_disabled"
                    task["web_params"] = {}
                elif target_agent in {"action", "reasoning", "language"}:
                    task["target_agent"] = target_agent
                    task["web_params"] = {}
                else:
                    task["target_agent"] = "action"
                    task["web_params"] = {}
            else:
                if target_agent in {"action", "reasoning", "language", "api"}:
                    task["target_agent"] = target_agent
                else:
                    task["target_agent"] = "action"

                task_context = str(task.get("context") or "local").strip().lower()
                if task_context not in {"local", "web"}:
                    task["context"] = "local"
                else:
                    task["context"] = task_context
        else:
            task["device"] = "desktop"
            if target_agent == "email":
                target_agent = "api"  # normalize legacy "email" target → api agent
            if target_agent in {"action", "reasoning", "language", "api"}:
                task["target_agent"] = target_agent
            else:
                task["target_agent"] = "action"

        task["extra_params"] = extra_params


def _normalize_cross_device_handoff_metadata(task_dict: Dict[str, Any]) -> None:
    """Normalize and sanitize cross-device handoff metadata for mixed-mode tasks."""
    if not isinstance(task_dict, dict):
        return

    extra_params = task_dict.get("extra_params")
    if not isinstance(extra_params, dict):
        extra_params = {}

    handoff_required = bool(extra_params.get("handoff_required"))
    if not handoff_required:
        task_dict["extra_params"] = extra_params
        return

    current_device = str(task_dict.get("device") or "desktop").strip().lower()
    handoff_from = str(extra_params.get("handoff_from") or current_device).strip().lower()
    handoff_to = str(extra_params.get("handoff_to") or "desktop").strip().lower()

    if handoff_from not in {"desktop", "mobile"}:
        handoff_from = current_device if current_device in {"desktop", "mobile"} else "desktop"
    if handoff_to not in {"desktop", "mobile"}:
        handoff_to = "desktop"

    if handoff_from == handoff_to:
        extra_params["handoff_required"] = False
        extra_params.pop("handoff_from", None)
        extra_params.pop("handoff_to", None)
        extra_params.pop("handoff_reason", None)
        extra_params.pop("handoff_session_ref", None)
        task_dict["extra_params"] = extra_params
        return

    # Never allow raw credentials/tokens to be emitted in decomposition metadata.
    for sensitive_key in ("handoff_token", "session_token", "access_token", "refresh_token", "password", "secret"):
        extra_params.pop(sensitive_key, None)

    extra_params["handoff_required"] = True
    extra_params["handoff_from"] = handoff_from
    extra_params["handoff_to"] = handoff_to
    if not str(extra_params.get("handoff_reason") or "").strip():
        extra_params["handoff_reason"] = "Cross-device step requires secure handoff approval."
    if not str(extra_params.get("handoff_session_ref") or "").strip():
        extra_params["handoff_session_ref"] = f"handoff_{uuid.uuid4().hex[:12]}"

    task_dict["extra_params"] = extra_params


def _normalize_device_type(device_type: Optional[str]) -> Literal["desktop", "mobile", "mixed"]:
    """Normalize incoming device hints to the coordinator's supported values."""
    raw = str(device_type or "desktop").strip().lower()
    if raw in {"mixed", "hybrid", "cross_platform", "cross-platform", "desktop_mobile", "desktop+mobile"}:
        return "mixed"
    if raw in {"mobile", "android", "ios", "phone", "tablet"}:
        return "mobile"
    return "desktop"


def _get_mobile_decomposition_prompt_sop() -> str:
        return """
You are decomposing high-level user requests into low-level sub-tasks for MOBILE UI execution.

HARD ROUTING RULES:
1. Every task MUST use device: "mobile".
2. Every task MUST use context: "local".
3. NEVER use target_agent: "email" on mobile.
4. Mobile handles email, browsing, and app operations via local automation tasks.
5. Even when the user says "open Chrome and search for X", you MUST use context: "local".
6. Mobile browser opening, navigation, and typing are handled by the local automation layer.

TARGET AGENTS:
- action: UI automation (tap, type, navigate, fill forms, extract text)
- reasoning: Logic tasks (summarize, analyze, write, translate, generate content)
- language: Confirmation/read-aloud tasks (read generated content to user, ask for confirmation)

WHEN TO USE target_agent: "reasoning" vs "action"

- Extracting text from UI, files, or webpages (e.g., “read the price”, “get the error message”, “copy the visible text”) -> ALWAYS target_agent: "action".
- Reasoning is for content generation, summarisation, translation, or analysis AFTER text has been extracted by an action task.
- If the user asks to understand or interpret extracted content, use two tasks:
        1. Action task to extract the raw text.
        2. Reasoning task that depends on the action task and receives the text via extra_params["input_content"].
- "action": Tasks that interact with the OS, apps, files, or browser (open file, read file, click, type, navigate, fill, screenshot, etc.). A reasoning component CANNOT open files or applications.
- "reasoning": Tasks that generate, summarize, analyze, research, write, translate, or answer questions. Content creation (stories, essays, code, emails, poems) is ALWAYS reasoning. If a task does NOT require interacting with a UI element or file system, it is reasoning.

Examples of REASONING tasks on mobile:
- "Write a scary story" -> reasoning
- "Summarize this article" -> reasoning
- "Solve these math problems" -> reasoning
- "Translate this to Arabic" -> reasoning
- "Draft an email to my boss" -> reasoning (IMPORTANT: ai_prompt must request SUBJECT and BODY together)
- "Explain quantum computing" -> reasoning
- "Generate a Python script" -> reasoning

EMAIL ON MOBILE:
- Do NOT use API/email agent routing.
- Build local app steps (open mail app, compose, fill fields, send).
- If subject/body are not explicitly provided, add one reasoning task first to generate SUBJECT and BODY.
- Add language confirmation before send for generated content ONLY.

GENERAL RULES:
1. One action per task - never combine multiple actions
2. Explicit dependencies - if task B needs task A's output, set "depends_on"
3. Descriptive prompts - ai_prompt should be detailed enough for RAG to understand
4. Correct context - all mobile tasks use context: "local"
5. Minimal extra_params - ONLY include action type and text (for fill), nothing else
6. Include URLs for known sites only when mobile browser navigation is explicit
7. NO selectors - NEVER hardcode CSS selectors, let RAG find them from ai_prompt
8. Empty web_params - For local tasks, set web_params: {}
9. Include confirmation steps - For configuration tasks (emails, forms, settings), always add a final task to confirm/save changes
10. Content generation = reasoning - Writing, summarizing, translating, or any creative/analytical task MUST use target_agent: "reasoning"
11. Shared goal - Every task in the output must include a non-empty "goal" and it must be exactly the same across all tasks in that decomposition
12. Research communication - For informational or research queries (e.g., 'check the weather', 'latest news', 'nearest pharmacy'), ensuring the result is communicated back to the disabled user is critical. You MUST include a final task with target_agent: "reasoning" that depends on the search results extracted and returned by an "action" agent content extraction task and formats them into a natural, helpful conversational response.
13. Confirmation for sensitive actions – When a task generates content that will be sent or committed (e.g., composing an email then sending it, sending a message, submitting a form), you MUST insert a confirmation task AFTER generation but BEFORE the final send action. NOTE: this applies to generated content ONLY. If the user explicitly provides the content (e.g., "Set the alarm for 7 am"), you do NOT need a confirmation task for the time value. But if the user asks you to "Draft an email to my boss about missing tomorrow's meeting" and you generate the email content, you MUST add a confirmation task to read back the generated email and ask for approval before sending. For mobile, this confirmation task should use target_agent: "language" with ai_prompt that reads out the generated content and asks for user confirmation or critique before proceeding to any action tasks that interact with the UI or send information.
  - The confirmation task must have:
    - target_agent: "language"
    - ai_prompt: "(e.g., Read out the generated content. Ask the user to confirm or critique this task. Wait for their response.)"
    - extra_params: Must contain {{"input_from": "<generation_task_id>"}} so the language agent actually receives the text to read out.
    - depends_on: the generation task
  - The send task must depend on the confirmation task.
  - If a user replies with a **critique** or asks for revisions on previously generated content (e.g., "make it shorter", "sound more professional"), treat it as a NEW modification request. You MUST generate a fresh pipeline to revise the content (using target_agent: "reasoning" with the old content and user critique), fill the revised content, and once again append a language confirmation task before sending. 
  - The Language Agent will handle the user interaction and signal approval or return the user's critique.
    - CRITICAL: When an action task comes AFTER a confirmation task (e.g., sending content after user confirms), the action task must ALSO include {"input_from": "<generation_task_id>"} to receive the ORIGINAL generated content, NOT the confirmation task's output. Example: if task_1 generates email, task_2 confirms it, then task_3 sends it, task_3 should have input_from: "task_1", not the confirmation task.

# DEVICE & CONTEXT

- **device**: "mobile"
- **context**:
    - "local" -> for all mobile tasks, including web browsing on mobile.

Mobile examples below are the only examples in this SOP.

============================
EXAMPLES (MOBILE)
============================

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
    extra_params: {}
    web_params: {}
    depends_on: null

- task_id: task_2
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Read out the generated email SUBJECT and BODY to the user and ask for confirmation/critique before opening any app or sending it. Wait for their response.
    device: mobile
    context: local
    target_agent: language
    extra_params:
        input_from: "task_1"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Navigate to Gmail
    device: mobile
    context: local
    target_agent: action
    extra_params:
        app_name: gmail
    depends_on: ["task_2"]

- task_id: task_4
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Compose new email to sara@gmail.com
    device: mobile
    context: local
    target_agent: action
    extra_params:
        recipient: sara@gmail.com
    depends_on: ["task_3"]

- task_id: task_5
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Fill the Subject field with the SUBJECT value from the composed email
    device: mobile
    context: local
    target_agent: action
    extra_params: {}
    depends_on: ["task_1", "task_4"]

- task_id: task_6
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Fill the email body with the BODY value from the composed email
    device: mobile
    context: local
    target_agent: action
    extra_params: {}
    depends_on: ["task_1", "task_5"]

- task_id: task_7
    goal: Compose and send a meeting reschedule email to Sara
    ai_prompt: Click the Send button to send the email
    device: mobile
    context: local
    target_agent: action
    extra_params: {}
    depends_on: ["task_6"]

EXPLANATION: task_1 (reasoning) returns {"SUBJECT": "...", "BODY": "..."}.
task_2 explicitly takes input_from: "task_1" so the language agent reads the generated content to the user for confirmation BEFORE any action tasks start.
task_3 navigates Gmail and depends on task_2. task_4 fills the To field directly (known from the user request). tasks 5-6 receive the JSON as input_content from task_1 so the action layer can parse SUBJECT and BODY individually. They also depend sequentially on task_4/5.

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
    success_message: "Clock app opened"
    failure_message: "Failed to open Clock app"
    depends_on: null

- task_id: task_2
    goal: Set an alarm for 7:00 AM
    ai_prompt: Set the alarm time to 7:00 AM
    device: mobile
    context: local
    target_agent: action
    extra_params:
        time: "7:00"
    success_message: "Alarm time set to 7:00 AM"
    failure_message: "Failed to set alarm time"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Set an alarm for 7:00 AM
    ai_prompt: Press OK or Save to confirm the alarm setting
    device: mobile
    context: local
    target_agent: action
    success_message: "Alarm set successfully for 7:00 AM"
    failure_message: "Failed to confirm alarm setting"
    depends_on: ["task_2"]

# SUCCESS/FAILURE MESSAGE RULES

- Every task MUST have both success_message and failure_message
- Messages should be brief (1-2 sentences), user-friendly, and action-oriented
- Success messages should convey what was accomplished (e.g., "Email sent to Sara")
- Failure messages should suggest next steps (e.g., "Check your connection and retry")
- These messages will be vocalized to the user via thinking steps as tasks execute
- Use the user's language only if confirmed in conversation history

============================
OUTPUT RULES
============================

Return ONLY a valid JSON array of tasks (no markdown, no explanations, no extra text before or after).  
Follow this exact schema for each task object:

[
  {{
    "task_id": <string, unique>,
    "goal": <string, identical for all tasks in this decomposition>,
    "ai_prompt": <string>,
    "device": "mobile",
    "context": "local",
    "target_agent": "action" | "reasoning" | "language",
    "extra_params": <object>,
    "web_params": <object>,
    "depends_on": <array of strings | null>,
    "success_message": <string, required>,
    "failure_message": <string, required>
  }},
  ...
]

IMPORTANT:  
- Every task MUST include both `success_message` and `failure_message` (short, user‑friendly sentences).  
- Do NOT output any text outside the JSON array.  
- Do NOT wrap the JSON in ````json```` fences – return raw JSON.

Generate the task decomposition now:"""


def _get_desktop_decomposition_prompt_sop() -> str:
        return """
============================
DESKTOP COORDINATOR SOP
============================

You are decomposing tasks for DESKTOP execution.

This SOP contains the desktop/web/email subset of the original coordinator
prompt. Keep every desktop, web, and email example, and preserve the full
Google/API routing guidance.

HARD ROUTING RULES:
1. Every task MUST use device: "desktop".
2. Use context: "web" for browser automation and "local" for native OS/app tasks.
3. The email agent is desktop-only and available here.
4. Desktop browser automation uses context: "web".
5. Desktop native app and OS tasks use context: "local".

TARGET AGENTS (desktop):
- action: UI automation (click, type, navigate, fill forms, extract text)
- reasoning: Logic tasks (summarize, analyze, write, translate, generate content)
- language: Confirmation/read-aloud tasks (read generated content to user, ask for confirmation)
- email: Google API operations (YouTube search, Calendar, Drive, Gmail send). ALWAYS use this for API calls.

WHEN TO USE target_agent: "reasoning" vs "action"

- Extracting text from UI, files, or webpages (e.g., “read the price”, “get the error message”, “copy the visible text”) -> ALWAYS target_agent: "action".
- Reasoning is for content generation, summarisation, translation, or analysis AFTER text has been extracted by an action task.
- If the user asks to understand or interpret extracted content, use two tasks:
        1. Action task to extract the raw text.
        2. Reasoning task that depends on the action task and receives the text via extra_params["input_content"].
- "action": Tasks that interact with the OS, apps, files, or browser (open file, read file, click, type, navigate, fill, screenshot, etc.). A reasoning component CANNOT open files or applications.
- "reasoning": Tasks that generate, summarize, analyze, research, write, translate, or answer questions. Content creation (stories, essays, code, emails, poems) is ALWAYS reasoning. If a task does NOT require interacting with a UI element or file system, it is reasoning.

Examples of REASONING tasks:
- "Write a scary story" -> reasoning
- "Summarize this article" -> reasoning
- "Solve these math problems" -> reasoning
- "Translate this to Arabic" -> reasoning
- "Draft an email to my boss" -> reasoning (IMPORTANT: ai_prompt must request SUBJECT and BODY together)
- "Explain quantum computing" -> reasoning
- "Generate a Python script" -> reasoning

EMAIL/API RULES:
- For direct send-email operations on desktop, you may route to target_agent: "email".
- For Google API tasks (YouTube search/info, Calendar, Drive), prefer target_agent: "email".
- For browser watch/play/navigation requests, use target_agent: "action" with context "web".

EMAIL TASK RULES (CRITICAL)
When the user asks to compose, send, or draft an email:
a) ALWAYS use the USER PREFERENCES section above to pick the concrete email app.
     - If preferences mention Gmail -> use Gmail.
     - If preferences mention Outlook -> use Outlook.
     - If preferences mention any mail app -> use that app.
     - Only if preferences are completely silent about email -> use "open default email app" (local).
     NEVER output a vague "open email client" task when memory has a preference.

b) ALWAYS generate a reasoning task FIRST that produces the email content fields IF user doesnt give email subject and content, only a description.
     e.g. user request: "Reschedule tomorrow's meeting with Sara" -- this implies an email but doesn't give subject or body. role of reasoning task: "Compose a complete email for this request: 'Reschedule tomorrow's meeting with Sara'. Return a JSON object with keys SUBJECT and BODY."
     user request: "Send an email to Sara about rescheduling tomorrow's meeting. Subject should be 'Meeting Rescheduled' and body should say 'Hi Sara, ...'" -- reasoning task is NOT needed here because subject and body are explicitly provided by user.
     The reasoning task ai_prompt must say:
     "Compose a complete email for this request: <user intent>.
        Return a JSON object with keys SUBJECT and BODY."
     The reasoning task output will be a JSON object injected as input_content, e.g.:
     {"SUBJECT": "Meeting Rescheduled", "BODY": "Hi Sara, ..."}

c) The action tasks that fill Subject and Body MUST depend on the reasoning task, if present, and
     will receive the generated JSON in extra_params["input_content"].

⚠️ EMAIL ROUTING FALLBACK (Critical for typos):
If the user request contains any variation of "email", "send", "compose", "draft", or related keywords,
ALWAYS route to target_agent: "email" with operation: "send" REGARDLESS of what mail app is mentioned.
DO NOT generate desktop UI automation tasks like pyautogui or keyboard control for email send operations.
Email sending ALWAYS uses the Gmail API endpoint which is atomic and reliable.

REASON: Desktop UI automation for email is fragile (login screens, multi-step forms, permissions).
The email API is 100% reliable and handles all Gmail/OAuth edge cases.

TYPO HANDLING: Input like "sedn email", "sned mail", "compose mesage" should STILL route to email agent.

✅ GOOGLE API ROUTING (YouTube, Calendar, Drive, Cookies):
If the user request contains keywords for Google API operations, route to target_agent: "email" with the appropriate operation:

YOUTUBE OPERATIONS:
- Keywords: "youtube search", "search on youtube", "find videos", "youtube video", "video on youtube"
    -> operation: "youtube_search" with query in extra_params
- Keywords: "youtube info", "video info", "how many views", "video details", "video statistics"
    -> operation: "youtube_video_info" with video_url in extra_params

GOOGLE CALENDAR OPERATIONS:
- Keywords: "create event", "add event", "schedule", "calendar", "meeting", "appointment"
    -> operation: "calendar_create" with title/start_time/end_time/description in extra_params
- Keywords: "list events", "show calendar", "upcoming events", "my calendar", "upcoming meetings"
    -> operation: "calendar_list" with max_results in extra_params

GOOGLE DRIVE OPERATIONS:
- Keywords: "upload file", "upload to drive", "save to drive", "drive upload"
    -> operation: "drive_upload" with file_path/parent_folder_id in extra_params
- Keywords: "list files", "my drive files", "files on drive", "drive list", "show my files"
    -> operation: "drive_list" with max_results in extra_params

BROWSER COOKIE INJECTION (for Google web automation):
- Keywords: "access google", "login to google", "google credentials", "browser login"
    -> operation: "get_browser_cookies" (enables seamless Google property access without UI login)

ROUTING RULES:
1. ALWAYS prefer API operations (YouTube, Calendar, Drive) over browser automation
2. API operations are ATOMIC, RELIABLE, and FASTER than UI automation
3. Only use browser automation if user explicitly asks to "watch" a video, "see" calendar UI, etc.
4. For API operations, set: target_agent: "email", context: "web", web_params: {}
5. Example: User says "Search YouTube for cat videos" ->
     {
         "target_agent": "email",
         "operation": "youtube_search",
         "ai_prompt": "Search YouTube for cat videos and return results",
         "extra_params": {"operation": "youtube_search", "query": "cat videos"},
         "web_params": {}
     }

HYBRID WORKFLOWS:
- Search YouTube (API) -> Extract results (reasoning) -> Watch in browser (action) is a valid 3-task flow
- But never duplicate: don't search YouTube both via API AND browser automation in same task plan

Examples of ACTION tasks:
- "Open the file worksheet.txt" -> action
- "Read text from the file" -> action
- "Open Notepad" -> action
- "Click the submit button" -> action
- "Navigate to google.com" -> action
- "Type 'hello' in the search box" -> action

# TASK STRUCTURE

Each task must have:
- ai_prompt: Natural language instruction (CRITICAL: this is used by RAG to determine URLs and selectors)
- goal: The SAME high-level goal string for the entire task plan (must be identical in all tasks)
- device: "desktop" or "mobile"
- context: "local" or "web"
- target_agent: "action" or "reasoning" or "language" or "email"
- extra_params: Additional data (app_name, file_path, operation, url, etc.)
- web_params: Web-specific parameters
- depends_on: task_id of prerequisite task
- success_message: (REQUIRED) User-friendly message to vocalize/display if task succeeds
- failure_message: (REQUIRED) User-friendly message to vocalize/display if task fails

# WEB_PARAMS STRUCTURE (for context: "web")

Do NOT hardcode CSS selectors or wait strategies. The execution layer uses RAG to determine selectors and interaction strategies from ai_prompt.
You SHOULD construct full URLs for well-known sites (see table below) — this is a fast path.
For unknown sites, put enough detail in ai_prompt and let RAG resolve navigation.

For well-known sites, include the full URL in both web_params AND extra_params.

URL CONSTRUCTION RULES:

| User intent | URL to construct |
|---|---|
| "open google and search for X" | https://www.google.com/search?q=X (replace spaces with +) |
| "search for X" / "look up X" / "find X" | https://www.google.com/search?q=X |
| "go to google" / "open google" | https://www.google.com |
| "play X" / "watch X" | https://www.youtube.com/results?search_query=X |
| "search youtube for X" | https://www.youtube.com/results?search_query=X |
| "go to youtube" | https://www.youtube.com |
| "open facebook" | https://www.facebook.com |
| "go to reddit" | https://www.reddit.com |
| "open gmail" | https://mail.google.com |
| "go to <any_known_site>" | https://www.<site>.com |
| "go to <explicit URL>" | Use the URL as-is |

CRITICAL DISTINCTIONS:
- "open google and search for X" -> Google search, NOT YouTube
- "play X" / "watch X" -> YouTube search, NOT Google
- "search for X" without mentioning a specific site -> default to Google search
- "search youtube for X" -> YouTube search
- "open the link named X" / "click on X" / "open the X result" WHEN A PAGE IS ALREADY OPEN -> CLICK on the current page, do NOT guess a URL
- Only construct navigation URLs when the user explicitly asks to go to a NEW site or search engine

For navigation tasks WITH a known URL:
{
    "action": "navigate",
    "url": "<constructed URL>"
}
extra_params must ALSO include: {"action": "navigate", "url": "<same URL>"}

For navigation tasks WITHOUT a known URL (unknown/unfamiliar sites):
{
    "action": "navigate"
}
Put the site name or description in ai_prompt — RAG will resolve the URL.

For interaction tasks (click, fill):
{
    "action": "fill",
    "text": "search query"
}

For extraction tasks:
{
    "action": "extract"
}

Summary:
- DO construct URLs for well-known sites (Google, YouTube, Gmail, etc.)
- DO put descriptive text in ai_prompt for RAG to use
- Do not hardcode CSS selectors — RAG finds them from the live page
- Do not hardcode wait strategies — the execution layer handles timing
- Do not guess URLs for unknown sites — let RAG resolve from ai_prompt

# CRITICAL RULES

1. One action per task - never combine multiple actions
2. Explicit dependencies - if task B needs task A's output, set "depends_on"
3. Descriptive prompts - ai_prompt should be detailed enough for RAG to understand
4. Correct context - web tasks get context: "web", desktop tasks get "local"
5. Minimal extra_params - ONLY include action type and text (for fill), nothing else
6. Include URLs for known sites - For well-known sites (Google, YouTube, Facebook, etc.), construct and include the URL in web_params and extra_params. For unknown sites, let RAG resolve them from ai_prompt.
7. NO selectors - NEVER hardcode CSS selectors, let RAG find them from ai_prompt
8. Empty web_params - For local tasks, set web_params: {}
9. Include confirmation steps - For configuration tasks (alarms, forms, settings), always add a final task to confirm/save changes
10. Content generation = reasoning - Writing, summarizing, translating, or any creative/analytical task MUST use target_agent: "reasoning"
11. Shared goal - Every task in the output must include a non-empty "goal" and it must be exactly the same across all tasks in that decomposition
12. Research communication - For informational or research queries (e.g., 'check the weather', 'latest news', 'nearest pharmacy'), ensuring the result is communicated back to the disabled user is critical. You MUST include a final task with target_agent: "reasoning" that depends on the search results extracted and returned by an "action" agent content extraction task and formats them into a natural, helpful conversational response.
13. Confirmation for sensitive actions – When a task generates content that will be sent or committed (e.g., composing an email then sending it, sending a message, submitting a form), you MUST insert a confirmation task AFTER generation but BEFORE the final send action. NOTE: This ONLY applies to reasoning tasks that generate content to be sent/committed. It does NOT apply to pure action tasks (e.g., "set an alarm for 7 am" does NOT require confirmation because it's a direct action with no generated content).
        - target_agent: "language"
        - ai_prompt: "(e.g., Read out the generated content. Ask the user to confirm or critique this task. Wait for their response.)"
        - extra_params: Must contain {"input_from": "<generation_task_id>"} so the language agent actually receives the text to read out.
        - depends_on: the generation task
    - The send task must depend on the confirmation task.
    - If a user replies with a critique or asks for revisions on previously generated content (e.g., "make it shorter", "sound more professional"), treat it as a NEW modification request. You MUST generate a fresh pipeline to revise the content (using target_agent: "reasoning" with the old content and user critique), fill the revised content, and once again append a language confirmation task before sending.
    - The Language Agent will handle the user interaction and signal approval or return the user's critique.
    - CRITICAL: When an action task comes AFTER a confirmation task (e.g., typing/saving content after user confirms), the action task must ALSO include {"input_from": "<generation_task_id>"} to receive the ORIGINAL generated content, NOT the confirmation task's output. Example: if task_2 generates content, task_3 confirms it, then task_4 types it, task_4 should have input_from: "task_2", not the confirmation task.

============================
EXAMPLES (DESKTOP/WEB/EMAIL)
============================

## Example 0: Browser Navigation — Google Search

User: "open google and search for web automation"

- task_id: task_1
    goal: open google and search for web automation
    ai_prompt: Navigate to Google and search for web automation
    device: desktop
    context: web
    target_agent: action
    extra_params:
        action: navigate
        url: "https://www.google.com/search?q=web+automation"
    web_params:
        action: navigate
        url: "https://www.google.com/search?q=web+automation"
    success_message: "Search results loaded successfully"
    failure_message: "Failed to load Google search. Please check your internet connection"
    depends_on: null

EXPLANATION: The user wants Google search, NOT YouTube. Construct the Google search URL with the query. This is a SINGLE task — just navigate to the search results page.

## Example 0b: Browser Navigation — Play/Watch Video

User: "play relaxing music"

- task_id: task_1
    goal: play relaxing music
    ai_prompt: Search YouTube for relaxing music
    device: desktop
    context: web
    target_agent: action
    extra_params:
        action: navigate
        url: "https://www.youtube.com/results?search_query=relaxing+music"
    web_params:
        action: navigate
        url: "https://www.youtube.com/results?search_query=relaxing+music"
    success_message: "YouTube music search loaded. Enjoy!"
    failure_message: "Could not reach YouTube. Check your connection and try again"
    depends_on: null

EXPLANATION: "play" implies video/music -> route to YouTube search.

## Example 0c: YouTube API Search (get structured results)

User: "search youtube for cat videos and give me the top 5 results"

- task_id: task_1
    goal: search youtube for cat videos and give me the top 5 results
    ai_prompt: Search YouTube for cat videos and return top 5 results
    device: desktop
    context: web
    target_agent: email
    extra_params:
        operation: youtube_search
        query: "cat videos"
        max_results: 5
    web_params: {}
    success_message: "Found top 5 cat videos. Here are the results:"
    failure_message: "YouTube search failed. Please check your internet connection"
    depends_on: null

EXPLANATION: When the user wants structured results (list of videos, titles, etc.) use target_agent: "email" with operation: "youtube_search". When they just want to watch/play, use target_agent: "action" with a YouTube URL (see Example 0b).

## Example 0d: Click a Link on the Current Page

Browser is currently open on: https://www.google.com/search?q=web+automation+papers
User: "open the link named Cybernaut"

- task_id: task_1
    goal: open the link named Cybernaut
    ai_prompt: Click on the link with text "Cybernaut" on the current page
    device: desktop
    context: web
    target_agent: action
    extra_params: {}
    web_params:
        action: click
    success_message: "Opened Cybernaut link. Page loaded successfully"
    failure_message: "Could not find or click the Cybernaut link. Please try again"
    depends_on: null

EXPLANATION: The user is referring to a link VISIBLE on the current Google search results page. Do NOT guess a URL like "https://www.cybernaut.com" — the actual href is unknown. Instead, generate a CLICK task and let the execution layer find the link by its text. This applies whenever the user says "open the link named X", "click on X", "open the X result", etc. while a page is already open.

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
    web_params: {}
    success_message: "Notepad opened successfully"
    failure_message: "Failed to open Notepad. Please try again"
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
    success_message: "Gmail login page loaded"
    failure_message: "Could not reach Gmail login page"
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
    success_message: "Email address entered"
    failure_message: "Could not enter email address. Please try again"
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
    success_message: "Password entered"
    failure_message: "Could not enter password. Please try again"
    depends_on: ["task_2"]

- task_id: task_4
    goal: Log in to Gmail with the provided credentials
    ai_prompt: Click the Sign In or Login submit button
    device: desktop
    context: web
    target_agent: action
    web_params:
        action: click
    success_message: "Logged in to Gmail successfully"
    failure_message: "Login failed. Please check your credentials and try again"
    depends_on: ["task_3"]

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
    web_params: {}
    success_message: "Notepad opened successfully"
    failure_message: "Failed to open Notepad"
    depends_on: null

- task_id: task_2
    goal: Open Notepad and produce a scary story in it
    ai_prompt: Write a very scary story
    device: desktop
    context: local
    target_agent: reasoning
    extra_params: {}
    web_params: {}
    success_message: "Story written successfully"
    failure_message: "Failed to write story. Please try again"
    depends_on: ["task_1"]

- task_id: task_3
    goal: Open Notepad and produce a scary story in it
    ai_prompt: Confirm the generated story with the user. Read it out loud and ask if they want to proceed with typing it into Notepad or make changes.
    device: desktop
    context: local
    target_agent: language
    extra_params:
            input_from: "task_2"
    web_params: {}
    depends_on: ["task_2"]

- task_id: task_4
    goal: Open Notepad and produce a scary story in it
    ai_prompt: Type the generated story text into the active Notepad window
    device: desktop
    context: local
    target_agent: action
    extra_params:
        input_from: "task_2"
    web_params: {}
    success_message: "Story pasted into Notepad. Check it out!"
    failure_message: "Failed to type story into Notepad"
    depends_on: ["task_3"]

============================
SUCCESS/FAILURE MESSAGE RULES
============================
- Every task MUST have both success_message and failure_message
- Messages should be brief (1-2 sentences), user-friendly, and action-oriented
- Success messages should convey what was accomplished (e.g., "Email sent to Sara")
- Failure messages should suggest next steps (e.g., "Check your connection and retry")
- These messages will be vocalized to the user via thinking steps as tasks execute
- Use the user's language only if confirmed in conversation history

============================
OUTPUT RULES
============================

Return ONLY a valid JSON array of tasks (no markdown, no explanations, no extra text before or after).  
Follow this exact schema for each task object:

[
  {{
    "task_id": <string, unique>,
    "goal": <string, identical for all tasks in this decomposition>,
    "ai_prompt": <string>,
    "device": "desktop",
    "context": "local" | "web",
    "target_agent": "action" | "reasoning" | "language" | "email",
    "extra_params": <object>,
    "web_params": <object>,
    "depends_on": <array of strings | null>,
    "success_message": <string, required>,
    "failure_message": <string, required>
  }},
  ...
]

IMPORTANT:  
- Every task MUST include both `success_message` and `failure_message` (short, user‑friendly sentences).  
- Do NOT output any text outside the JSON array.  
- Do NOT wrap the JSON in ````json```` fences – return raw JSON.

Generate the task decomposition now:"""


def _get_mixed_decomposition_prompt_sop() -> str:
        return """
============================
MIXED COORDINATOR SOP (DESKTOP + MOBILE)
============================

You are decomposing tasks for MIXED cross-platform execution.

GOAL:
- Produce a single dependency graph where each task is explicitly assigned to either desktop or mobile.
- Use desktop for browser/API-heavy tasks when appropriate.
- Use mobile for app-local tasks and phone-native operations.

HARD ROUTING RULES:
1. Every task MUST set device to either "desktop" or "mobile" (never "mixed").
2. For device="mobile", context MUST be "local" and target_agent MUST NOT be "email".
3. For device="desktop", context may be "web" or "local".
4. Keep one action per task with strict dependencies.
5. If a task on one device needs output from another device, set explicit depends_on and include handoff metadata in extra_params.

TARGET AGENTS:
- action
- reasoning
- language
- email (desktop-only)

HANDOFF AND CONFIRMATION RULES:
- When cross-device control/session transfer is required, add an explicit preparation task first.
- IMPORTANT: Do NOT insert a language confirmation task for pure action tasks like:
  - Opening/closing files, documents, or applications
  - Navigating to URLs or locations
  - Launching programs or services
  - Uploading/downloading files
  - Clicking buttons or menu items
- ONLY use target_agent: "language" for confirmation when content has been GENERATED (e.g., drafting an email, writing a document, composing a message) that will be sent, committed, or saved.
- For cross-device operations that DO need confirmation, include extra_params fields:
  - handoff_required: true
  - handoff_from: "desktop" | "mobile"
  - handoff_to: "desktop" | "mobile"
  - handoff_reason: short sentence
- Never include raw secrets or tokens in output.

CONTEXT RULES:
- device="desktop" and browser automation -> context="web"
- device="desktop" and native app/OS action -> context="local"
- device="mobile" -> context="local"

OUTPUT RULES:
- Return ONLY a JSON array.
- Every task must include: task_id, goal, ai_prompt, device, context, target_agent, extra_params, web_params, depends_on, success_message, failure_message.
- Keep the same non-empty goal across all tasks in the plan.

Generate the task decomposition now:"""


def _get_decomposition_prompt_sop(device_type: Literal["desktop", "mobile", "mixed"]) -> str:
    """Return the device-specific SOP for task decomposition."""
    if device_type == "mobile":
        return _get_mobile_decomposition_prompt_sop()
    if device_type == "mixed":
        return _get_mixed_decomposition_prompt_sop()

    return _get_desktop_decomposition_prompt_sop()


def _sanitize_task_dicts(task_dicts: List[Dict[str, Any]]) -> None:
    """Sanitize task dictionaries to ensure task_id and depends_on are strings.
    
    This fixes LLM outputs where numeric task IDs or indices are used instead of strings.
    Mutates task_dicts in place.
    """
    for t in task_dicts:
        if not isinstance(t, dict):
            continue
            
        # task_id must be string
        if "task_id" in t and t["task_id"] is not None:
            t["task_id"] = str(t["task_id"])
            
        # depends_on must be list of strings or None
        if "depends_on" in t and t["depends_on"] is not None:
            # If it's a single value (int or str), wrap in a list
            if not isinstance(t["depends_on"], list):
                t["depends_on"] = [t["depends_on"]]
            
            # Convert each element to string and filter out empty/None
            t["depends_on"] = [
                str(d) for d in t["depends_on"] 
                if d is not None and str(d).strip()
            ]
            
            # If list is now empty, set to None
            if not t["depends_on"]:
                t["depends_on"] = None
        else:
            # Explicitly set to None if missing or already None
            t["depends_on"] = None


def _apply_device_specific_task_routing(task_dicts: List[Dict[str, Any]], device_type: Literal["desktop", "mobile", "mixed"]) -> None:
    """Enforce routing guarantees after LLM decomposition."""
    for task in task_dicts:
        if not isinstance(task, dict):
            continue

        extra_params = task.get("extra_params")
        if not isinstance(extra_params, dict):
            extra_params = {}

        target_agent = str(task.get("target_agent") or "action").strip().lower()
        if target_agent == "langauge":
            target_agent = "language"

        if device_type == "mobile":
            task["device"] = "mobile"
            task["context"] = "local"

            if target_agent == "email":
                task["target_agent"] = "action"
                extra_params["routing_note"] = "mobile_sop_email_agent_disabled"
                if not task.get("ai_prompt"):
                    task["ai_prompt"] = "Perform this requested operation in the mobile app using local automation."
                task["web_params"] = {}
            elif target_agent in {"action", "reasoning", "language"}:
                task["target_agent"] = target_agent
                task["web_params"] = {}
            else:
                task["target_agent"] = "action"
                task["web_params"] = {}
        elif device_type == "mixed":
            task_device = str(task.get("device") or "desktop").strip().lower()
            if task_device not in {"desktop", "mobile"}:
                task_device = "desktop"
            task["device"] = task_device

            if task_device == "mobile":
                task["context"] = "local"
                if target_agent == "email":
                    task["target_agent"] = "action"
                    extra_params["routing_note"] = "mixed_sop_mobile_email_agent_disabled"
                    task["web_params"] = {}
                elif target_agent in {"action", "reasoning", "language"}:
                    task["target_agent"] = target_agent
                    task["web_params"] = {}
                else:
                    task["target_agent"] = "action"
                    task["web_params"] = {}
            else:
                if target_agent in {"action", "reasoning", "language", "email"}:
                    task["target_agent"] = target_agent
                else:
                    task["target_agent"] = "action"

                task_context = str(task.get("context") or "local").strip().lower()
                if task_context not in {"local", "web"}:
                    task["context"] = "local"
                else:
                    task["context"] = task_context
        else:
            task["device"] = "desktop"
            if target_agent in {"action", "reasoning", "language", "email"}:
                task["target_agent"] = target_agent
            else:
                task["target_agent"] = "action"

        task["extra_params"] = extra_params


def _normalize_cross_device_handoff_metadata(task_dict: Dict[str, Any]) -> None:
    """Normalize and sanitize cross-device handoff metadata for mixed-mode tasks."""
    if not isinstance(task_dict, dict):
        return

    extra_params = task_dict.get("extra_params")
    if not isinstance(extra_params, dict):
        extra_params = {}

    handoff_required = bool(extra_params.get("handoff_required"))
    if not handoff_required:
        task_dict["extra_params"] = extra_params
        return

    current_device = str(task_dict.get("device") or "desktop").strip().lower()
    handoff_from = str(extra_params.get("handoff_from") or current_device).strip().lower()
    handoff_to = str(extra_params.get("handoff_to") or "desktop").strip().lower()

    if handoff_from not in {"desktop", "mobile"}:
        handoff_from = current_device if current_device in {"desktop", "mobile"} else "desktop"
    if handoff_to not in {"desktop", "mobile"}:
        handoff_to = "desktop"

    if handoff_from == handoff_to:
        extra_params["handoff_required"] = False
        extra_params.pop("handoff_from", None)
        extra_params.pop("handoff_to", None)
        extra_params.pop("handoff_reason", None)
        extra_params.pop("handoff_session_ref", None)
        task_dict["extra_params"] = extra_params
        return

    # Never allow raw credentials/tokens to be emitted in decomposition metadata.
    for sensitive_key in ("handoff_token", "session_token", "access_token", "refresh_token", "password", "secret"):
        extra_params.pop(sensitive_key, None)

    extra_params["handoff_required"] = True
    extra_params["handoff_from"] = handoff_from
    extra_params["handoff_to"] = handoff_to
    if not str(extra_params.get("handoff_reason") or "").strip():
        extra_params["handoff_reason"] = "Cross-device step requires secure handoff approval."
    if not str(extra_params.get("handoff_session_ref") or "").strip():
        extra_params["handoff_session_ref"] = f"handoff_{uuid.uuid4().hex[:12]}"

    task_dict["extra_params"] = extra_params


def _looks_like_email_send_intent(text: str) -> bool:
    """
    Detect if user is asking to send/compose an email.
    Uses both strict regex and fuzzy matching to handle typos.
    """
    t = (text or "").lower()
    
    # Strict regex match (fast path for common cases)
    if re.search(r"\b(send|compose|draft|write)\s+(an?\s+)?(email|mail|gmail|message)\b", t):
        return True
    
    # Fuzzy match for typos (slow path, only if strict match fails)
    # Common typos: "sedn", "sned", "snd", "sen", etc.
    from difflib import SequenceMatcher
    
    # Extract verbs and nouns from text
    words = re.findall(r'\b\w+\b', t)
    send_variants = ['send', 'compose', 'draft', 'write']
    email_variants = ['email', 'mail', 'gmail', 'message']
    
    # Check if any word is a close match to send/compose/draft/write
    for word in words:
        for send_word in send_variants:
            similarity = SequenceMatcher(None, word, send_word).ratio()
            if similarity > 0.75:  # 75% match threshold
                # If we found a close match to a send verb, check for email noun
                for email_word in email_variants:
                    if email_word in t:
                        return True
    
    return False


def _clean_email_field_value(value: str) -> str:
    cleaned = (value or "").strip().strip('"\'').strip()
    while cleaned and cleaned[0] in {":", "=", "-", ","}:
        cleaned = cleaned[1:].strip()
    return cleaned.rstrip(".,;!?").strip()


def _extract_email_send_payload(user_request: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Extract direct send-email payload (to, subject, body) when explicitly provided."""
    text = _get_user_request_text(user_request)
    if not _looks_like_email_send_intent(text):
        return None

    recipient = None
    recipient_match = re.search(
        r"\bto\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        text,
        re.IGNORECASE,
    )
    if recipient_match:
        recipient = recipient_match.group(1).strip()
    else:
        any_email = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
        if any_email:
            recipient = any_email.group(1).strip()

    subject = None
    subject_patterns = [
        r"\bsubject\s*(?:is|=|:)?\s*[\"']?(.+?)[\"']?(?=\s+(?:and\s+)?(?:body|content|message|text)\b|$)",
        r"\bwith\s+subject\s*[\"']?(.+?)[\"']?(?=\s+(?:and\s+)?(?:body|content|message|text)\b|$)",
    ]
    for pattern in subject_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            subject = _clean_email_field_value(match.group(1))
            if subject:
                break

    body = None
    body_patterns = [
        r"\b(?:body|content|message|text)\s*(?:is|=|:)?\s*[\"']?(.+?)[\"']?(?=\s+(?:and\s+)?subject\b|$)",
        r"\bwith\s+(?:body|content|message|text)\s*[\"']?(.+?)[\"']?(?=\s+(?:and\s+)?subject\b|$)",
    ]
    for pattern in body_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            body = _clean_email_field_value(match.group(1))
            if body:
                break

    if recipient and subject and body:
        return {"to": recipient, "subject": subject, "body": body}

    return None

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
    
    # ✅ FIX: Prefer raw user input over paraphrased confirmation text.
    text = ""
    if 'original_input' in user_request and str(user_request.get('original_input', '')).strip():
        text = str(user_request.get('original_input', ''))
    elif 'action' in user_request and str(user_request.get('action', '')).strip():
        text = str(user_request.get('action', ''))
    elif 'confirmation' in user_request:
        text = str(user_request.get('confirmation', ''))
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
        # "password is X" must come first to avoid capturing "is" as password
        r'password\s+is\s+([^\s,.!?]+)',
        r'pass\s+is\s+([^\s,.!?]+)',

        # Direct password patterns
        r'password[\s:=]+([^\s,.!?]+)',      # "password mypass"
        r'pwd[\s:=]+([^\s,.!?]+)',           # "pwd mypass"
        r'pass[\s:=]+([^\s,.!?]+)',          # "pass mypass"
        
        # With connector words
        r'and\s+password[\s:=]+([^\s,.!?]+)',      # "and password mypass"
        r'with\s+password[\s:=]+([^\s,.!?]+)',     # "with password mypass"
        r'using\s+password[\s:=]+([^\s,.!?]+)',    # "using password mypass"
        
        # Complex patterns for any login/signup
        r'(?:login|sign in|sign up|register|create account).*?password[\s:=]+([^\s,.!?]+)',
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
# DEVICE INFERENCE & HANDOFF FUNCTIONS
# ============================================================================

def infer_device_from_context(prompt: str, original_request: str, last_device: str) -> str:
    """Decide if this task should run on desktop or mobile."""
    prompt_lower = (prompt or "").lower()
    request_lower = (original_request or "").lower()
    
    # 1. Explicit mention in prompt
    if "on my phone" in prompt_lower or "on my mobile" in prompt_lower:
        return "mobile"
    if "on my desktop" in prompt_lower or "on my laptop" in prompt_lower:
        return "desktop"
    
    # 2. Fallback: last device or source device from original request
    if "from my phone" in request_lower:
        return last_device if last_device else "mobile"
    if "from my desktop" in request_lower or "from my laptop" in request_lower:
        return "desktop"
    
    return "desktop"


def create_handoff_task(from_device: str, to_device: str, data_key: str = "input_content") -> Dict[str, Any]:
    """Create a handoff task that transfers data between devices."""
    return {
        "task_id": f"handoff_{uuid.uuid4().hex[:8]}",
        "goal": "Transfer data between devices",
        "ai_prompt": f"Transfer the output of the previous task from {from_device} to {to_device}.",
        "device": from_device,
        "context": "local",
        "target_agent": "action",
        "extra_params": {
            "handoff_required": True,
            "handoff_to": to_device,
            "handoff_data_key": data_key,
            "single_task_handoff": True
        },
        "web_params": {},
        "depends_on": None,
        "success_message": f"Data sent to {to_device}.",
        "failure_message": "Handoff failed."
    }


def assign_devices_and_handoffs(tasks: List[Dict[str, Any]], original_request: str) -> List[Dict[str, Any]]:
    """Post-process tasks to assign devices and insert handoff tasks when crossing devices."""
    if not tasks or not isinstance(tasks, list):
        return tasks
    
    new_tasks = []
    last_output_device = "desktop"
    
    # Check if user request has device hints
    request_lower = (original_request or "").lower()
    if "from my phone" in request_lower or "on my phone" in request_lower:
        last_output_device = "mobile"
    
    for task in tasks:
        if not isinstance(task, dict):
            new_tasks.append(task)
            continue
        
        # 1. Decide target device for this task
        ai_prompt = task.get("ai_prompt", "")
        target_device = infer_device_from_context(ai_prompt, original_request, last_output_device)
        
        # Override if device is already set in task
        if "device" not in task or not task.get("device"):
            task["device"] = target_device
        else:
            target_device = task.get("device")
        
        # 2. If device changed from previous task, insert a handoff task
        if last_output_device != target_device and len(new_tasks) > 0:
            # Get the task ID of the previous task to create dependency
            prev_task_id = None
            if new_tasks:
                prev_task_id = new_tasks[-1].get("task_id")
            
            handoff_task = create_handoff_task(last_output_device, target_device)
            if prev_task_id:
                handoff_task["depends_on"] = [prev_task_id]
            
            # Current task should depend on handoff task
            task["depends_on"] = [handoff_task["task_id"]] if not task.get("depends_on") else [handoff_task["task_id"]] + (task.get("depends_on") or [])
            
            new_tasks.append(handoff_task)
        
        new_tasks.append(task)
        last_output_device = target_device
    
    return new_tasks

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
    target_device_id: Optional[str] = None
    
    # Web-specific parameters - NO URLs here, execution layer resolves them
    web_params: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Examples:
    # Navigation: {"action": "navigate"}  # URL comes from RAG based on ai_prompt
    # Interaction: {"action": "fill", "text": "search query"}  # Selector comes from RAG
    # Extraction: {"action": "extract"}  # Selector comes from RAG
    
    target_agent: Literal["action", "reasoning", "language", "email", "api"] = "action"
    depends_on: Optional[List[str]] = None
    success_message: Optional[str] = None
    failure_message: Optional[str] = None

    @field_validator("task_id", mode="before")
    @classmethod
    def _coerce_task_id(cls, value):
        if value is None or value == "":
            return str(uuid.uuid4())
        return str(value)
    
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

    # Keyword-based popup/dialog detection only applies to execution tasks,
    # NOT to API results (api agent) where content may contain false positives.
    if getattr(task, 'target_agent', '') == 'api':
        return None

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

    # if any(k in combined for k in ["permission", "allow", "deny access", "popup", "dialog", "modal"]):
    #     return {
    #         "task_id": task.task_id,
    #         "task_prompt": task.ai_prompt,
    #         "clarification_type": "permission_or_popup",
    #         "question": "An unexpected popup appeared. Should I allow it, close it, or stop?",
    #         "recoverable": True,
    #         "metadata": result.metadata or {},
    #         "error": result.error,
    #     }

    # return None


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


def _email_api_credentials_missing(result: TaskResult) -> bool:
    """Detect missing Gmail API credentials from structured metadata or error text."""
    metadata = result.metadata or {}
    if isinstance(metadata, dict) and metadata.get("email_api_credentials_missing"):
        return True

    combined = " ".join(filter(None, [result.error, result.details, result.content])).lower()
    if "no credentials for user" in combined or "no credentials found for user" in combined:
        return True
    return False


def _build_email_web_fallback_task(task: ActionTask) -> Optional[ActionTask]:
    """Create a web compose task when Gmail API credentials are missing for a send operation."""
    if task.target_agent != "api":
        return None

    extra_params = task.extra_params or {}
    operation = str(extra_params.get("operation") or "send").strip().lower()
    if operation != "send":
        return None

    recipient = str(extra_params.get("to") or "").strip()
    subject = str(extra_params.get("subject") or "").strip()
    body = str(extra_params.get("body") or "").strip()

    if not recipient:
        return None

    prompt_parts = [
        "Open Gmail in the browser and compose a new email.",
        f"Set the recipient to {recipient}.",
    ]
    if subject:
        prompt_parts.append(f"Set the subject to: {subject}.")
    if body:
        prompt_parts.append(f"Use this exact message body:\n{body}")
    prompt_parts.append("Send the email.")

    fallback_extra = dict(extra_params)
    fallback_extra.update(
        {
            "overall_goal": task.goal or task.ai_prompt,
            "goal": task.goal or task.ai_prompt,
            "fallback_source": "email_api_missing_credentials",
            "email_to": recipient,
            "email_subject": subject,
            "email_body": body,
        }
    )

    return ActionTask(
        task_id=f"{task.task_id}_web_fallback",
        goal=task.goal or task.ai_prompt,
        ai_prompt=" ".join(prompt_parts),
        device=task.device,
        context="web",
        target_agent="action",
        extra_params=fallback_extra,
        web_params={},
        depends_on=None,
    )


async def _attempt_email_web_fallback(
    task: ActionTask,
    result: TaskResult,
    session_id: str,
    original_message_id: str,
    user_language: str,
    output_language: str,
    user_profile: Optional[Dict[str, Any]],
    user_id: Optional[str],
) -> Optional[TaskResult]:
    """Attempt web compose fallback when email API fails because credentials are missing."""
    if task.target_agent != "email" or result.status != "failed":
        return None

    if not _email_api_credentials_missing(result):
        return None

    fallback_task = _build_email_web_fallback_task(task)
    if not fallback_task:
        return None

    logger.warning(
        f"🔁 Gmail API credentials missing for {task.task_id}; trying web compose fallback via {fallback_task.task_id}"
    )

    fallback_result = await execute_single_task(
        fallback_task,
        session_id,
        original_message_id,
        user_language,
        output_language,
        user_profile,
        user_id,
    )

    merged_metadata = dict(result.metadata or {})
    merged_metadata.update(
        {
            "fallback_mode": "email_api_to_web_compose",
            "fallback_task_id": fallback_task.task_id,
            "fallback_status": fallback_result.status,
        }
    )
    if fallback_result.metadata:
        merged_metadata["web_fallback_metadata"] = fallback_result.metadata

    if fallback_result.status == "success":
        logger.info(f"✅ Web compose fallback succeeded for {task.task_id}")
        return TaskResult(
            task_id=task.task_id,
            status="success",
            content=fallback_result.content or "Email sent using web fallback.",
            details=fallback_result.details or "email:send_web_fallback",
            metadata=merged_metadata,
            error=None,
        )

    logger.error(
        f"❌ Web compose fallback failed for {task.task_id}: {fallback_result.error}"
    )
    return TaskResult(
        task_id=task.task_id,
        status=fallback_result.status,
        content=fallback_result.content,
        error=fallback_result.error
        or "Web compose fallback failed after Gmail API credentials were missing.",
        details=fallback_result.details,
        metadata=merged_metadata,
        needs_clarification=fallback_result.needs_clarification,
        clarification_question=fallback_result.clarification_question,
        clarification_type=fallback_result.clarification_type,
        recoverable=fallback_result.recoverable,
    )

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
        
    def add_to_global(self, task_plan: Any):
        self.global_queue.append(task_plan)
        
    def get_next_task(self) -> Optional[ActionTask]:
        if not self.current_queue and self.global_queue and not self.is_paused and not self.is_stopped:
            next_plan = self.global_queue.popleft()
            if isinstance(next_plan, dict):
                queued_tasks = next_plan.get("tasks", [])
            elif isinstance(next_plan, list):
                queued_tasks = next_plan
            else:
                queued_tasks = []
            if queued_tasks:
                logger.info(f"📦 Loading queued plan with {len(queued_tasks)} tasks into current queue")
                self.current_queue.extend(queued_tasks)

        if self.current_queue and not self.is_paused and not self.is_stopped:
            task = self.current_queue.popleft()
            self.current_task_id = task.task_id
            return task
        return None
    
    def has_tasks(self) -> bool:
        return len(self.current_queue) > 0 or len(self.global_queue) > 0

    def snapshot(self) -> Dict[str, Any]:
        current_items = [
            {
                "task_id": t.task_id,
                "title": t.ai_prompt,
                "target_agent": t.target_agent,
            }
            for t in list(self.current_queue)
        ]
        global_items: List[Dict[str, Any]] = []
        for plan in list(self.global_queue):
            if isinstance(plan, dict) and isinstance(plan.get("tasks"), list):
                for task in plan["tasks"]:
                    if isinstance(task, ActionTask):
                        global_items.append(
                            {
                                "task_id": task.task_id,
                                "title": task.ai_prompt,
                                "target_agent": task.target_agent,
                            }
                        )
            elif isinstance(plan, list):
                for task in plan:
                    if isinstance(task, ActionTask):
                        global_items.append(
                            {
                                "task_id": task.task_id,
                                "title": task.ai_prompt,
                                "target_agent": task.target_agent,
                            }
                        )

        active_items: List[Dict[str, Any]] = []
        pending_items: List[Dict[str, Any]] = []
        deferred_items: List[Dict[str, Any]] = []

        if current_items:
            active_items.append(current_items[0])
            pending_items.extend(current_items[1:])
        pending_items.extend(global_items)

        return {
            "current_queue_size": len(self.current_queue),
            "global_queue_size": len(self.global_queue),
            "is_paused": self.is_paused,
            "is_stopped": self.is_stopped,
            "current_task_id": self.current_task_id,
            "current_tasks": current_items,
            "global_tasks": global_items,
            # Frontend-friendly normalized shape for queue popovers.
            "active": active_items,
            "pending": pending_items,
            "deferred": deferred_items,
            "frequent_or_sequential": len(current_items) >= 3 or len(global_items) >= 3,
        }
    
    def pause(self):
        self.is_paused = True
        logger.info("⏸️ Task execution paused")
        
    def resume(self):
        self.is_paused = False
        logger.info("▶️ Task execution resumed")
        
    def stop(self):
        self.is_stopped = True
        self.is_paused = False
        self.current_queue.clear()
        self.global_queue.clear()
        self.current_task_id = None
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

# Global task queues keyed by session_id.
# Keep a default alias for legacy call sites, but always bind the session
# queue locally inside execution / interrupt handlers.
task_queues: Dict[str, TaskQueue] = {}


def get_session_task_queue(session_id: Optional[str]) -> TaskQueue:
    queue_key = session_id or "__default__"
    if queue_key not in task_queues:
        task_queues[queue_key] = TaskQueue()
    return task_queues[queue_key]


task_queue = get_session_task_queue(None)
coordinator_processing_lock = asyncio.Lock()

# Track pending results
pending_results: Dict[str, asyncio.Future] = {}
# Track pending remote subtasks created for cross-device execution
_pending_remote_tasks: Dict[str, asyncio.Future] = {}
#hala edit ashan el web 
_session_browser_state: Dict[str, Dict] = {}
_session_youtube_results: Dict[str, List[Dict[str, Any]]] = {}
deferred_language_messages: deque = deque()
_session_stop_events: Dict[str, Dict[str, Any]] = {}
_session_execution_context: Dict[str, str] = {}

# ICRL: Per-session, per-task buffers for In-Context Reinforcement Learning
# Key: f"{session_id}:{task_id}" → ICRLBuffer
# Cleared when a new chat session starts
_icrl_buffers: Dict[str, ICRLBuffer] = {}

# ── ICRL TEST FLAG — set to True temporarily to force round 0 to fail ────────
# This injects a bad task into the round-0 decomposition so maybe_retry_plan
# always has something to retry. Set back to False in production.
_ICRL_FORCE_FAIL_ROUND0 = False
# ─────────────────────────────────────────────────────────────────────────────

def _get_icrl_buffer(session_id: str, task_id: str, goal: str) -> ICRLBuffer:
    """Get or create an ICRL buffer for a specific task within a session."""
    key = f"{session_id}:{task_id}"
    if key not in _icrl_buffers:
        _icrl_buffers[key] = ICRLBuffer(goal=goal)
    return _icrl_buffers[key]

def _clear_icrl_buffers_for_session(session_id: str):
    """Clear all ICRL buffers for a session (called on new chat)."""
    keys_to_remove = [k for k in _icrl_buffers if k.startswith(f"{session_id}:")]
    for k in keys_to_remove:
        del _icrl_buffers[k]
    if keys_to_remove:
        logger.info(f"🗑️ ICRL: Cleared {len(keys_to_remove)} buffers for session {session_id}")

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


def resolve_remote_task(task_id: str, result: Dict[str, Any]) -> bool:
    """Resolve a pending remote task future when the device posts a result."""
    future = _pending_remote_tasks.get(task_id)
    if not future:
        return False
    if future.done():
        return True
    try:
        future.set_result(result)
        return True
    except Exception:
        return False

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
    plan_error: Optional[str] = None

# ============================================================================
# ENHANCED TASK DECOMPOSITION - NO HARDCODED URLs
# ============================================================================

async def decompose_task_to_actions(
    user_request: Dict[str, Any],
    preferences_context: str,
    device_type: str = "desktop",
    conversation_history: List[Dict] = None,
    session_id: str = None,
    http_request_id: str = None,
    current_page_url: str = None
) -> Dict[str, Any]:
    """Decompose user request into ActionTask queue - URLs resolved by execution layer"""
    normalized_device_type = _normalize_device_type(device_type)
    request_text = _get_user_request_text(user_request)
    selected_target_device_id = user_request.get("target_device_id")

    # ─────────────────────────────────────────────────────────────────────
    # PRE-CHECK: "open the first/second result" from cached YouTube results
    # This needs session state and can't be deferred to the LLM.
    # ─────────────────────────────────────────────────────────────────────
    _NTH_RESULT_RE = re.compile(
        r'\b(?:open|play|watch|click|go\s+to)\s+(?:the\s+)?'
        r'(?:(\d+)(?:st|nd|rd|th)?|first|second|third|fourth|fifth|last)\s+'
        r'(?:result|video|one|link|item)',
        re.I,
    )
    nth_match = _NTH_RESULT_RE.search(request_text)
    if nth_match and session_id:
        cached_videos = _session_youtube_results.get(session_id, [])
        if cached_videos:
            _ORDINAL_MAP = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4, "last": -1}
            matched_text = nth_match.group(0).lower()
            idx = None
            if nth_match.group(1):  # numeric like "3rd"
                idx = int(nth_match.group(1)) - 1
            else:
                for word, val in _ORDINAL_MAP.items():
                    if word in matched_text:
                        idx = val
                        break
            if idx is not None:
                if idx == -1:
                    idx = len(cached_videos) - 1
                if 0 <= idx < len(cached_videos):
                    video = cached_videos[idx]
                    vid_id = video.get("video_id", "")
                    title = video.get("title", "Unknown")
                    direct_url = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else None
                    if direct_url:
                        logger.info(f"📹 Opening cached YouTube result #{idx + 1}: {title} → {direct_url}")
                        shortcut_context = "web" if normalized_device_type == "desktop" else "local"
                        shortcut_web_params = {"action": "navigate", "url": direct_url} if normalized_device_type == "desktop" else {}
                        shortcut_device = "mobile" if normalized_device_type == "mobile" else "desktop"
                        browser_task = ActionTask(
                            task_id="task_1",
                            goal=f"Open YouTube video: {title}",
                            ai_prompt=f"Navigate to {direct_url} and play the video",
                            device=shortcut_device,
                            context=shortcut_context,
                            target_agent="action",
                            extra_params={"action": "navigate", "url": direct_url},
                            web_params=shortcut_web_params,
                            depends_on=None,
                        )
                        return {"tasks": [browser_task]}
                else:
                    logger.warning(f"⚠️ Requested result #{idx + 1} but only {len(cached_videos)} cached results")
        else:
            logger.warning(f"⚠️ No cached YouTube results for session {session_id}")

    # Direct email API short-circuit: avoid unnecessary Gmail UI/login automation.
    direct_email_payload = _extract_email_send_payload(user_request)
    if direct_email_payload and normalized_device_type == "desktop":
        logger.info(
            f"📧 Direct Email API routing detected for recipient {direct_email_payload['to']}"
        )
        email_task = ActionTask(
            task_id="task_1",
            goal=f"Send an email to {direct_email_payload['to']}",
            ai_prompt="Send an email via Gmail API",
            device="desktop",
            context="local",
            target_agent="api",
            extra_params={
                "operation": "send",
                "to": direct_email_payload["to"],
                "subject": direct_email_payload["subject"],
                "body": direct_email_payload["body"],
            },
            web_params={},
            depends_on=None,
        )
        return {"tasks": [email_task]}
    
    # ✅ FIX 2: Extract credentials FIRST - FOR ANY LOGIN/SIGNUP TASK
    login_keywords = ['login', 'sign in', 'sign up', 'register', 'create account', 'log in', 'authenticate']
    login_context_markers = ['with', 'using', 'email', 'password', 'account', '@']
    full_text = request_text.lower()
    is_email_send_intent = _looks_like_email_send_intent(request_text)
    is_login_task = any(keyword in full_text for keyword in login_keywords) and any(marker in full_text for marker in login_context_markers)

    # Credential-only follow-ups may omit explicit login keywords.
    if not is_login_task and not is_email_send_intent:
        has_password_phrase = any(kw in full_text for kw in ['password', 'pwd', 'pass', 'credential'])
        has_explicit_email_credential = bool(
            re.search(
                r'(?:email|username|user)\s*(?:is|:|=)\s*[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
                full_text,
                re.IGNORECASE,
            )
        )
        if has_password_phrase or has_explicit_email_credential:
            is_login_task = True
    
    credentials = None
    if is_login_task:
        credentials = extract_credentials_from_request(user_request)
        
        # ✅ FIX: Allow email-only OR password-only for multi-page flows
        # Scenario 1: User gives only email first → fill email, click next → later provide password on page 2
        # Scenario 2: User gives both email+password → fill both in sequence (handling multi-page auto)
        if not credentials.get('email') and not credentials.get('password'):
            logger.error("❌ No email or password found in request")
            return {
                'error': 'Please provide at least an email address or password (e.g., "login with user@example.com" or "use password mypass123")',
                'tasks': []
            }
        
        if credentials.get('email'):
            logger.info(f"📧 Extracted email: {credentials['email']}")
        if credentials.get('password'):
            logger.info(f"🔑 Password extracted (length: {len(credentials['password'])})")
        
        # Log which fields are available
        if credentials.get('email') and not credentials.get('password'):
            logger.info("📍 Only email provided - will fill email field on current page")
        elif credentials.get('password') and not credentials.get('email'):
            logger.info("📍 Only password provided - will fill password field on current page or after navigation")
        else:
            logger.info("📍 Both email and password provided - will handle multi-page auth flow")
    
    # Remove the decomposition-level thinking step entirely.
    # The coordinator already shows "preparing_tasks" before calling ainvoke.
    # Adding another one here just doubles the indicator on screen.
    
    device_hint = f"The user is on a {normalized_device_type} device. Tailor task recommendations accordingly.\n\n"
    if current_page_url:
         device_hint += f"🌐 The browser is currently open on: {current_page_url}\n"
         device_hint += (
             "⚠️ CRITICAL — CURRENT PAGE AWARENESS:\n"
             "The user may be referring to content VISIBLE on this page (links, buttons, videos, text).\n"
             "If the user says things like:\n"
             "  - 'open the link named X' / 'click on X' / 'open X' (where X is text on the page)\n"
             "  - 'open first video' / 'click the button' / 'play it' / 'play the first one'\n"
             "  - 'open the third result' / 'click the one about Y'\n"
             "Then you MUST generate a CLICK task on the current page, NOT a navigate task to a guessed URL.\n"
             "Use web_params: {\"action\": \"click\"} and put the link text in ai_prompt.\n"
             "Do NOT fabricate/guess URLs — the link's actual href is unknown; let the execution layer find and click it.\n"
             "Only generate a navigate task if the user explicitly names a website or asks to go somewhere NEW.\n\n"
         )
    else:
         device_hint += "No browser page is currently open.\n\n"
    
    # ✅ FIX 3 + Fix 5: Build conversation history context with active app/page state
    history_context = ""
    if conversation_history:
        history_context = "\n\n# CONVERSATION HISTORY (Last 3 interactions)\n"
        history_context += (
            "IMPORTANT: If an app or browser page is listed as 'currently open', "
            "do NOT generate a task to re-open it. Interact with the existing window directly.\n\n"
        )
        for entry in conversation_history[-3:]:
            history_context += f"User: {entry.get('user_message', '')}\n"
            history_context += f"Action: {entry.get('action_taken', '')}\n"
            if entry.get('actions_detail'):
                history_context += f"Steps taken: {'; '.join(entry['actions_detail'])}\n"
            history_context += f"Result: {entry.get('result', '')}\n"
            if entry.get('apps_currently_open'):
                history_context += (
                    f"Apps currently open (DO NOT reopen): "
                    f"{', '.join(entry['apps_currently_open'])}\n"
                )
            if entry.get('current_page_url'):
                history_context += f"Browser currently at: {entry['current_page_url']}\n"
            history_context += "\n"
    
# Extract ICRL history injected by decompose_task_to_actions_with_icrl()
    _icrl_history_block = user_request.get("_icrl_history", "")
    _icrl_round = user_request.get("_icrl_round", 0)
    _icrl_best_reward = user_request.get("_icrl_best_reward", 0.0)

    # Build a clean copy of the request without the internal ICRL keys for display
    _clean_request = {k: v for k, v in user_request.items()
                      if not k.startswith("_icrl_")}

    prompt = f"""{device_hint}You are the AURA Task Decomposition Agent. Convert user requests into low-level executable tasks.

# USER REQUEST
{json.dumps(_clean_request, indent=2)}

# USER PREFERENCES
{preferences_context}
{history_context}"""

    # Inject ICRL history as a clearly labelled section so the LLM actually sees it
    if _icrl_history_block:
        prompt += f"""

============================z
PREVIOUS ATTEMPT HISTORY (In-Context RL — Round {_icrl_round})
============================
Best reward achieved so far: {_icrl_best_reward:.2f} (0.0 = total failure, 1.0 = perfect success)

{_icrl_history_block}

INSTRUCTION: Every previous attempt above failed (reward ≤ {_icrl_best_reward:.2f}).
You MUST generate a plan that is DIFFERENT from all of the above.
Do NOT repeat the same task prompt, same approach, or same tool sequence.
============================
"""
    
    # ✅ FIX 2: Add credentials section to prompt if applicable
    if credentials:
        prompt += f"""
        
# EXTRACTED CREDENTIALS (MULTI-PAGE AUTH AWARE):
"""
        
        if credentials.get('email'):
            prompt += f"""Email: {credentials['email']}
**RULE**: For email field: {{"action": "fill", "text": "{credentials['email']}"}}
"""
        
        if credentials.get('password'):
            prompt += f"""Password: {credentials['password']}
**RULE**: For password field: {{"action": "fill", "text": "{credentials['password']}"}}
"""
        
        # ✅ NEW: Multi-page auth awareness
        prompt += f"""
**CRITICAL FOR MULTI-PAGE FORMS** (e.g., Google, Facebook, Microsoft):
- If user provides ONLY email: Create ONE task to fill the email field, then click "Next"
- If user provides ONLY password: Create tasks to fill the password field and submit
- If user provides BOTH email+password: Create tasks in THIS ORDER:
  1. Fill email field with {credentials.get('email', 'N/A')}
  2. Click "Next" / "Continue" button
  3. Wait for password field to appear (may be new page or revealed form)
  4. Fill password field with {credentials.get('password', 'N/A')}
  5. Click "Sign in" / "Login" / "Submit" button

**IMPORTANT**: After filling the email field, ALWAYS check if there's a "Next", "Continue", or "Submit" button to proceed to the next step.
"""
    
    prompt += _get_decomposition_prompt_sop(normalized_device_type)

    try:
        response_text = await llm_invoke_with_fallback(prompt)
        response_text = response_text.strip()

        # Robust JSON extraction: accept plain JSON, fenced JSON, or JSON
        # embedded inside prose. The fallback LLM may prepend commentary even
        # when the actual payload is valid JSON.
        parsed = extract_json_payload(response_text, None)
        if isinstance(parsed, dict):
            if "tasks" in parsed:
                parsed = parsed["tasks"]
            elif isinstance(parsed.get("task"), list):
                parsed = parsed["task"]
            elif isinstance(parsed.get("plan"), list):
                parsed = parsed["plan"]
            elif isinstance(parsed.get("decomposition"), list):
                parsed = parsed["decomposition"]

        if parsed is None:
            raise ValueError(f"No valid JSON task array found in LLM response. "
                             f"Response preview: {response_text[:200]}")

        if not isinstance(parsed, list):
            raise ValueError(f"Expected a JSON array of tasks, got: {type(parsed)}")

        # Step E: normalize the parsed array into task dictionaries.
        task_dicts = _coerce_task_dict_list(parsed)
        if not task_dicts:
            repair_prompt = f"""You are repairing a malformed task decomposition response.

USER REQUEST:
{json.dumps(_clean_request, indent=2)}

BROKEN RESPONSE:
{response_text}

Return ONLY a valid JSON array where every element is a task object with at least:
- task_id
- goal
- ai_prompt
- device
- context
- target_agent
Do not return strings, markdown, or explanation text."""
            repair_text = (await llm_invoke_with_fallback(repair_prompt)).strip()
            repaired = extract_json_payload(repair_text, None)
            task_dicts = _coerce_task_dict_list(repaired)
            if not task_dicts:
                raise ValueError("JSON array contained no task objects")

        # Step E.5: sanitize task dicts to ensure string task_ids and depends_on
        _sanitize_task_dicts(task_dicts)

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

        for idx, t in enumerate(task_dicts):
            t["goal"] = shared_goal

            extra_params = t.get("extra_params") or {}
            if not isinstance(extra_params, dict):
                extra_params = {}

            # Inject the shared top-level goal for downstream mobile execution.
            if not extra_params.get("overall_goal"):
                extra_params["overall_goal"] = shared_goal
            if not extra_params.get("goal"):
                extra_params["goal"] = shared_goal

            # Best-effort app propagation so dependent mobile steps inherit the app.
            if not extra_params.get("app_name"):
                dep_raw = t.get("depends_on")
                dep_ids = dep_raw if isinstance(dep_raw, list) else ([dep_raw] if dep_raw else [])
                for dep_id in dep_ids:
                    for prev in task_dicts[:idx]:
                        if prev.get("task_id") == dep_id:
                            prev_app = (prev.get("extra_params") or {}).get("app_name", "")
                            if prev_app:
                                extra_params["app_name"] = prev_app
                                break
                    if extra_params.get("app_name"):
                        break

            # Give mobile action tasks a larger runtime budget so the mobile
            # execution layer does not cut off a task that is still making progress.
            if normalized_device_type == "mobile" and str(t.get("target_agent") or "action").strip().lower() == "action":
                timeout_seconds = extra_params.get("timeout_seconds")
                if not isinstance(timeout_seconds, (int, float)):
                    timeout_seconds = 0
                extra_params["timeout_seconds"] = max(int(timeout_seconds), 120)

            # Give mobile action tasks a larger runtime budget so the mobile
            # execution layer does not cut off a task that is still making progress.
            if normalized_device_type == "mobile" and str(t.get("target_agent") or "action").strip().lower() == "action":
                timeout_seconds = extra_params.get("timeout_seconds")
                if not isinstance(timeout_seconds, (int, float)):
                    timeout_seconds = 0
                extra_params["timeout_seconds"] = max(int(timeout_seconds), 120)

            t["extra_params"] = extra_params

        _apply_device_specific_task_routing(task_dicts, normalized_device_type)

        if normalized_device_type == "mixed":
            for t in task_dicts:
                _normalize_cross_device_handoff_metadata(t)

        # Create ActionTask objects with error handling for any remaining validation issues
        action_tasks = []
        for idx, task in enumerate(task_dicts):
            try:
                action_task = ActionTask(**task)
                action_tasks.append(action_task)
            except Exception as e:
                logger.error(
                    f"❌ Failed to create ActionTask for task {idx} ({task.get('task_id', 'unknown')}): {e}\n"
                    f"Raw task dict: {json.dumps(task, indent=2, default=str)}"
                )
                # Re-sanitize and retry once
                try:
                    _sanitize_task_dicts([task])
                    action_task = ActionTask(**task)
                    action_tasks.append(action_task)
                    logger.info(f"✅ ActionTask created successfully after re-sanitization")
                except Exception as retry_err:
                    logger.error(f"❌ ActionTask creation failed even after re-sanitization: {retry_err}")
                    raise

        # ── LLM VALIDATION PASS ────────────────────────────────────────────────
        validation_prompt = f"""You are the AURA Task Decomposition Validator. Review the proposed decomposition for the user request.

USER REQUEST:
{json.dumps(user_request, indent=2)}

PROPOSED DECOMPOSITION:
{json.dumps(task_dicts, indent=2)}

**YOUR JOB**:
- ONLY modify the plan if you find a CLEAR VIOLATION of the rules.
- DO NOT add or remove steps just because you would have written it differently.
- DO NOT change the order of steps unless it breaks a logical dependency.
- Return the plan EXACTLY as given if no violation exists.

**Device‑Logical Validation**:
Imagine you are giving step‑by‑step instructions to a user on that device (e.g., an Android phone). Ask yourself: would these steps be logical and complete?
- For mobile: Do not assume a browser is already open unless the user said so. Opening a browser is its own step.
- Do not skip necessary taps (e.g., after filling a field, a “Next” or “Send” button must be clicked).
- Ensure that the number of steps matches what a human would need to do.
If the plan violates this common‑sense device logic, you MAY add missing steps or remove redundant ones, but only if strictly necessary.

**CRITICAL RULES TO ENFORCE**:
1. **Confirmation for sensitive actions** – When a task generates content that will be sent or committed (e.g., composing an email then sending it, sending a message, submitting a form), you MUST ensure a confirmation task exists AFTER generation but BEFORE the final send action.
  - The confirmation task must have: target_agent: "language", and its ai_prompt MUST explicitly tell the agent to read the generated content aloud to the user and ask for confirmation or critique.
  - The confirmation task must explicitly declare `input_from: "<generation_task_id>"` in its `extra_params` so the text is routed to the language agent.
  - The send task must depend on the confirmation task.
  - If the user provides a critique (e.g., "make it shorter"), ensure the task sequence re-generates the content using a reasoning task, re-fills the new text, and asks for confirmation again.
2. **One action per task** - never combine multiple actions.
3. NO selectors hardcoded. URLs are allowed for well-known sites when explicitly requested.
4. Content generation MUST use target_agent: "reasoning".

Return ONLY a valid JSON array of tasks (same format as input). Do not include markdown or explanations.
"""
        try:
            val_text = await llm_invoke_with_fallback(validation_prompt)
            val_text = val_text.strip()

            val_parsed = extract_json_payload(val_text, None)
            if isinstance(val_parsed, dict):
                if "tasks" in val_parsed:
                    val_parsed = val_parsed["tasks"]
                elif isinstance(val_parsed.get("task"), list):
                    val_parsed = val_parsed["task"]
                elif isinstance(val_parsed.get("plan"), list):
                    val_parsed = val_parsed["plan"]
                elif isinstance(val_parsed.get("decomposition"), list):
                    val_parsed = val_parsed["decomposition"]

            if isinstance(val_parsed, list) and len(val_parsed) > 0:
                validated_tasks_dicts = _coerce_task_dict_list(val_parsed)
                for t in validated_tasks_dicts:
                    t["goal"] = shared_goal  # Enforce shared goal
                action_tasks = [ActionTask(**t) for t in validated_tasks_dicts]
                logger.info(f"✅ Validation pass applied. Final tasks: {len(action_tasks)}")
        except Exception as ve:
            logger.warning(f"⚠️ Validation pass failed: {ve}. Using original decomposition.")

        # ── PLAN GRAPH VALIDATION ──────────────────────────────────────────────
        try:
            valid_ids = {t.task_id for t in action_tasks}
            # 1. Remove dangling dependencies
            for t in action_tasks:
                if t.depends_on:
                    cleaned_deps = [dep for dep in t.depends_on if dep in valid_ids]
                    t.depends_on = cleaned_deps if cleaned_deps else None
            
            # 2. Check for cycles (basic DFS)
            visited = set()
            path = set()
            def has_cycle(task_id):
                if task_id in path:
                    return True
                if task_id in visited:
                    return False
                visited.add(task_id)
                path.add(task_id)
                task = next((t for t in action_tasks if t.task_id == task_id), None)
                if task and task.depends_on:
                    for dep in task.depends_on:
                        if has_cycle(dep):
                            return True
                path.remove(task_id)
                return False

            for t in action_tasks:
                if has_cycle(t.task_id):
                    logger.error(f"❌ Cycle detected involving task {t.task_id}, clearing its dependencies to break cycle.")
                    t.depends_on = None
                    # Re-run cycle breaking if necessary, but clearing one is usually enough for simple cases.
                    path.clear()
                    
        except Exception as graph_e:
            logger.warning(f"⚠️ Plan graph validation encountered an error: {graph_e}")

        #here
        logger.info(f"📋 Decomposed into {len(action_tasks)} tasks")
        
        # ── POST-PROCESS: Assign devices and insert handoff tasks ────────────
        task_dicts_for_handoff = [t.dict() for t in action_tasks]
        processed_task_dicts = assign_devices_and_handoffs(task_dicts_for_handoff, request_text)
        if selected_target_device_id:
            for task_dict in processed_task_dicts:
                task_dict["target_device_id"] = selected_target_device_id
        action_tasks = [ActionTask(**task) for task in processed_task_dicts]
        logger.info(f"📋 After device assignment and handoffs: {len(action_tasks)} tasks")
        
        return {"tasks": action_tasks}

    except Exception as e:
        logger.error(f"❌ Task decomposition failed: {e}")
        return {"error": str(e)}


async def decompose_task_to_actions_with_icrl(
    user_request: Dict[str, Any],
    preferences_context: str,
    device_type: str = "desktop",
    conversation_history: List[Dict] = None,
    session_id: str = None,
    http_request_id: str = None,
    current_page_url: str = None,
    icrl_buffer: Optional[ICRLBuffer] = None,
    icrl_round: int = 0,
) -> Dict[str, Any]:
    """
    ICRL-aware wrapper around decompose_task_to_actions.
    
    On round 0: calls decompose_task_to_actions normally (no history).
    On rounds 1+: injects the ICRL history context + instruction into the
    decomposition prompt before calling the LLM.
    
    This implements the core ICRL loop for PLAN-LEVEL retries:
    if the entire plan failed, we retry the decomposition with reward-annotated
    history so the LLM generates a better plan.
    
    Args:
        ...(same as decompose_task_to_actions)...
        icrl_buffer: Shared ICRLBuffer tracking plan-level attempts
        icrl_round: Current round number (0 = first attempt)
    
    Returns:
        Same as decompose_task_to_actions: {"tasks": [...]} or {"error": "..."}
    """
    if not ICRL_ENABLED or icrl_round == 0 or icrl_buffer is None or icrl_buffer.attempt_count == 0:
        # First attempt — no ICRL history yet, call normally
        result = await decompose_task_to_actions(
            user_request, preferences_context, device_type,
            conversation_history, session_id, http_request_id, current_page_url
        )
        # ── TEST ONLY: inject a guaranteed-fail task to force ICRL retry ──
        if _ICRL_FORCE_FAIL_ROUND0 and result.get("tasks"):
            from agents.coordinator_agent.coordinator_agent import ActionTask
            import uuid as _uuid
            fail_task = ActionTask(
                task_id=f"icrl_test_fail_{_uuid.uuid4().hex[:6]}",
                goal=result["tasks"][0].goal if result["tasks"] else "test",
                ai_prompt="__ICRL_FORCED_FAILURE_DO_NOT_EXECUTE__",
                device="desktop",
                context="local",
                target_agent="action",
                extra_params={"_icrl_test": True},
                depends_on=None,
            )
            result["tasks"].append(fail_task)
            logger.warning("⚠️ ICRL TEST MODE: Injected forced-fail task into round-0 plan")
        # ─────────────────────────────────────────────────────────────────
        return result

    # Build the base prompt (we need to intercept it before the LLM call)
    # We reconstruct the prompt the same way decompose_task_to_actions does,
    # inject ICRL context, then call the LLM directly.
    
    logger.info(
        f"🔄 ICRL round {icrl_round}: injecting {icrl_buffer.attempt_count} "
        f"attempt(s) into decomposition prompt. "
        f"Best reward so far: {icrl_buffer.best_reward:.3f}"
    )

    # Get the base prompt by calling the original function but capturing what it would send.
    # Rather than duplicating the entire prompt construction (which is huge and would drift),
    # we use a simpler approach: add ICRL context to the user_request dict so the LLM
    # sees it naturally in the "USER REQUEST" section of the prompt.
    icrl_context_block = ""
    from agents.ICRL.icrl_prompt_builder import build_icrl_context_block
    icrl_context_block = build_icrl_context_block(icrl_buffer)
    icrl_instruction = icrl_buffer.get_icrl_instruction(icrl_round)

    # Inject ICRL as an additional field in the request so it appears in the
    # "USER REQUEST" JSON block the LLM sees
    icrl_enriched_request = dict(user_request)
    icrl_enriched_request["_icrl_history"] = icrl_context_block + icrl_instruction
    icrl_enriched_request["_icrl_round"] = icrl_round
    icrl_enriched_request["_icrl_best_reward"] = round(icrl_buffer.best_reward, 3)

    result = await decompose_task_to_actions(
        icrl_enriched_request, preferences_context, device_type,
        conversation_history, session_id, http_request_id, current_page_url
    )
    return result


async def split_independent_user_requests(raw_task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Split a single user utterance into independent top-level requests for global queueing.
    Returns one item when the request is singular or tightly coupled.
    """
    base_text = str(raw_task.get("original_input") or raw_task.get("confirmation") or "").strip()
    if not base_text:
        return [raw_task]

    split_prompt = f"""You are an intent splitting classifier for an automation coordinator.
Decide whether the request contains MULTIPLE INDEPENDENT top-level goals that should run as separate queued plans.

USER REQUEST:
{base_text}

Rules:
1. Split only when goals are unrelated and can be planned independently.
2. Do NOT split tightly coupled flows (e.g. "open Gmail and send an email").
3. Keep each split goal executable and concise.
4. If uncertain, do not split.

Return strict JSON only:
{{
  "split": true/false,
  "goals": ["goal 1", "goal 2"]
}}"""

    try:
        llm_text = await llm_invoke_with_fallback(split_prompt)
        payload = extract_json_payload(llm_text, {"split": False, "goals": []})
    except Exception as e:
        logger.warning(f"⚠️ Independent-intent split failed, using single-plan fallback: {e}")
        payload = {"split": False, "goals": []}

    goals = payload.get("goals") if isinstance(payload, dict) else []
    should_split = bool(isinstance(payload, dict) and payload.get("split") and isinstance(goals, list))

    cleaned_goals: List[str] = []
    if should_split:
        for g in goals:
            g_text = str(g).strip()
            if g_text:
                cleaned_goals.append(g_text)

    if len(cleaned_goals) <= 1:
        return [raw_task]

    split_requests: List[Dict[str, Any]] = []
    for goal in cleaned_goals[:4]:
        sub = dict(raw_task)
        sub["original_input"] = goal
        sub["confirmation"] = goal
        split_requests.append(sub)

    logger.info(
        f"🧩 Split request into {len(split_requests)} independent plans: "
        + " | ".join(r.get("original_input", "") for r in split_requests)
    )
    return split_requests


def namespace_task_plan(tasks: List[ActionTask], namespace: str) -> List[ActionTask]:
    """Prefix task IDs/dependencies to avoid collisions across global queued plans."""
    id_map = {t.task_id: f"{namespace}{t.task_id}" for t in tasks}
    namespaced: List[ActionTask] = []
    for task in tasks:
        deps = task.depends_on or []
        mapped_deps = [id_map.get(dep, f"{namespace}{dep}") for dep in deps] if deps else None
        cloned = ActionTask(
            task_id=id_map.get(task.task_id, f"{namespace}{task.task_id}"),
            goal=task.goal,
            ai_prompt=task.ai_prompt,
            device=task.device,
            context=task.context,
            extra_params=dict(task.extra_params or {}),
            web_params=dict(task.web_params or {}),
            target_agent=task.target_agent,
            target_device_id=task.target_device_id,
            depends_on=mapped_deps,
        )
        input_from = cloned.extra_params.get("input_from")
        if input_from:
            cloned.extra_params["input_from"] = id_map.get(input_from, f"{namespace}{input_from}")
        namespaced.append(cloned)
    return namespaced

def create_coordinator_graph():
    """Create the coordinator orchestration graph"""
    graph = StateGraph(dict)

    async def analyze_and_plan(state: Dict) -> Dict:
        """STEP 1: Decompose user request into ActionTask queue"""
        raw_task = state["input"]
        session_id = state.get("session_id")
        user_id = state.get("user_id", "default_user")
        original_message_id = state.get("original_message_id")
        device_type = _normalize_device_type(raw_task.get("device_type", "desktop"))

        cross_device_target = raw_task.get("cross_device_target") or {}
        selected_target_device_id = raw_task.get("target_device_id")
        target_platform = raw_task.get("target_device") or cross_device_target.get("target_platform")
        is_cross_platform = bool(cross_device_target) or device_type == "mixed"

        # Resume paused credentials-required tasks instead of decomposing a fresh plan.
        saved_browser = _session_browser_state.get(session_id, {}) if session_id else {}
        if saved_browser.get("pending_clarification_type") == "credentials_required":
            provided = extract_credentials_from_request(raw_task)
            if provided.get("email") or provided.get("password"):
                paused_task_data = saved_browser.get("paused_task_data") or {}
                if paused_task_data:
                    merged_extra = dict(paused_task_data.get("extra_params") or {})
                    if provided.get("email"):
                        merged_extra["google_email"] = provided["email"]
                    if provided.get("password"):
                        merged_extra["google_password"] = provided["password"]

                    resumed_task = ActionTask(
                        task_id=paused_task_data.get("task_id", "task_resume_credentials"),
                        goal=paused_task_data.get("goal") or "Continue pending authentication flow",
                        ai_prompt=paused_task_data.get("ai_prompt") or "Continue Google login with provided credentials",
                        device=paused_task_data.get("device") or device_type,
                        context=paused_task_data.get("context") or "web",
                        target_agent=paused_task_data.get("target_agent") or "action",
                        extra_params=merged_extra,
                        web_params=paused_task_data.get("web_params") or {},
                        depends_on=None,
                    )

                    saved_browser.pop("pending_clarification_type", None)
                    saved_browser.pop("paused_task_data", None)
                    _session_browser_state[session_id] = saved_browser

                    logger.info("🔁 Resuming paused credentials-required task without full re-decomposition")
                    return {
                        "input": state["input"],
                        "tasks": [resumed_task],
                        "status": "ready",
                        "session_id": session_id,
                        "original_message_id": original_message_id,
                        "user_id": user_id,
                        "preferences_context": "Resumed pending credentials-required task",
                        "plan_error": "",
                    }

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

            # Use pre-fetched preferences from Language Agent when available —
            # this avoids a redundant vector search for the same query.
            _prefetched = str(raw_task.get("_prefetched_preferences", "")).strip()
            if _prefetched:
                logger.info("⚡ Using pre-fetched preferences from Language Agent (skipping duplicate Mem0 search)")
                preferences_context = _prefetched
            else:
                preferences_context = pref_mgr.get_relevant_preferences(
                    str(raw_task.get("original_input", raw_task.get("confirmation", ""))), limit=5
                )

            # ── Memory Fix 3: Strip credentials and conversation_history from coordinator context ──
            # T-M3/T-M4: Coordinator was embedding stored passwords into task
            # decomposition prompt, potentially passing them to execution agents.
            # Also strip conversation_history entries — these accumulate per-task and
            # flood the decomposition prompt, causing hallucination on long sessions.
            _CRED_MARKERS_COORD = [
                "password", "passwd", "pwd", "secret",
                "api key", "apikey", "api_key", "token",
                "private key", "passphrase",
            ]
            if isinstance(preferences_context, list):
                _orig_count = len(preferences_context)
                preferences_context = [
                    p for p in preferences_context
                    if not any(m in str(p).lower() for m in _CRED_MARKERS_COORD)
                    and p.get("metadata", {}).get("category") != "conversation_history"
                ]
                _blocked = _orig_count - len(preferences_context)
                if _blocked > 0:
                    logger.warning(
                        f"🚫 Memory Fix 3: Removed {_blocked} credential/history memory "
                        f"item(s) from coordinator context"
                    )
                # Convert list of dicts to a readable numbered string for the LLM
                _lines = []
                for _i, _p in enumerate(preferences_context, 1):
                    if isinstance(_p, dict):
                        _text = _p.get("memory") or _p.get("text") or str(_p)
                    else:
                        _text = str(_p)
                    _lines.append(f"{_i}. {_text.strip()}")
                preferences_context = "\n".join(_lines) if _lines else "No user preferences available"
            elif isinstance(preferences_context, str):
                _clean_lines = []
                for _line in preferences_context.split('\n'):
                    if any(m in _line.lower() for m in _CRED_MARKERS_COORD):
                        logger.warning(
                            f"🚫 Memory Fix 3: Removed credential line from context: "
                            f"'{_line[:60]}'"
                        )
                    else:
                        _clean_lines.append(_line)
                preferences_context = '\n'.join(_clean_lines)
            # ─────────────────────────────────────────────────────────────────────

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
        # hala edit ashan el web
        saved_browser = _session_browser_state.get(session_id, {})
        current_page_url = saved_browser.get("current_page_url")

        # ── Load persisted conversation_history from checkpoint if not in state ──
        # This handles the restart case: state["conversation_history"] starts as []
        # on each new server process, but the checkpoint may have prior entries.
        loaded_history = state.get("conversation_history", [])
        if not loaded_history and checkpointer and session_id:
            try:
                checkpoint_data = await checkpointer.aget(
                    config={"configurable": {"thread_id": session_id, "checkpoint_ns": ""}}
                )
                if checkpoint_data:
                    # Try channel_values first (our custom save format)
                    ch_vals = checkpoint_data.get("channel_values", {})
                    persisted_history = ch_vals.get("conversation_history", [])
                    if persisted_history and isinstance(persisted_history, list):
                        loaded_history = persisted_history
                        logger.info(
                            f"🔄 Restored conversation_history from checkpoint: "
                            f"{len(loaded_history)} entries"
                        )
            except Exception as _load_err:
                logger.debug(f"⚠️ conversation_history load failed (non-fatal): {_load_err}")
        # ─────────────────────────────────────────────────────────────────────
        
        split_requests = await split_independent_user_requests(raw_task)
        all_plans: List[List[ActionTask]] = []
        plan_error = ""

        for idx, sub_request in enumerate(split_requests):
            plan_result = await decompose_task_to_actions(
                sub_request,
                preferences_context,
                device_type,
                conversation_history=loaded_history,
                session_id=session_id,
                http_request_id=original_message_id,
                current_page_url=current_page_url
            )

            this_error = plan_result.get("error", "") if isinstance(plan_result, dict) else ""
            if this_error:
                plan_error = this_error
                logger.error(f"❌ Decomposition returned error for plan {idx + 1}: {this_error}")
                all_plans = []
                break

            plan_tasks: List[ActionTask] = plan_result.get("tasks", [])
            if idx > 0:
                plan_tasks = namespace_task_plan(plan_tasks, namespace=f"g{idx + 1}_")

            for task in plan_tasks:
                if getattr(task, "device", None) is None:
                    task.device = device_type

            all_plans.append(plan_tasks)

            try:
                tasks_dump = [t.model_dump() if hasattr(t, "model_dump") else t for t in plan_tasks]
                logger.info(
                    f"📋 Decomposition result for plan {idx + 1} ({len(tasks_dump)} tasks): "
                    f"{json.dumps(tasks_dump, indent=2)}"
                )
            except Exception as e:
                logger.info(f"📋 Decomposed plan {idx + 1} into {len(plan_tasks)} tasks (serialize failed: {e})")

        primary_tasks: List[ActionTask] = all_plans[0] if all_plans else []
        queued_task_plans: List[List[ActionTask]] = all_plans[1:] if len(all_plans) > 1 else []
        tasks = [t for plan in all_plans for t in plan]
        
        return {
            "input": state["input"],
            "tasks": tasks,
            "primary_tasks": primary_tasks,
            "queued_task_plans": queued_task_plans,
            "status": "ready",
            "session_id": session_id,
            "original_message_id": original_message_id,
            "user_id": user_id,
            "preferences_context": preferences_context,
            "plan_error": plan_error,
        }

    async def execute_tasks(state: Dict) -> Dict:
        """STEP 2: Execute tasks sequentially"""
        tasks = state.get("primary_tasks") or state["tasks"]
        queued_task_plans = state.get("queued_task_plans") or []
        session_id = state.get("session_id")
        original_message_id = state.get("original_message_id")
        user_id = state.get("user_id", "default_user")
        user_language = state.get("input", {}).get("user_language", "en")
        # output_language: language for task content (may differ from system language)
        output_language = state.get("input", {}).get("output_language", user_language)
        # user_profile: personalization data forwarded from Language Agent
        user_profile = state.get("input", {}).get("user_profile") or {}
        user_id = state.get("input", {}).get("user_id", "default_user")
        task_queue = get_session_task_queue(session_id)
        current_device = _normalize_device_type(state.get("input", {}).get("device_type", "desktop"))
        current_device_id = (
            state.get("input", {}).get("device_id")
            or state.get("input", {}).get("source_device_id")
            or session_id
            or ""
        )

        if EXECUTION_PAUSED or state.get("input", {}).get("execution_mode") == "plan_only":
            try:
                await broker.publish(
                    Channels.WEBSOCKET_OUTPUT,
                    AgentMessage(
                        message_type=MessageType.TASK_PROGRESS,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=original_message_id,
                        payload={
                            "ws_type": "task_progress",
                            "stage": "coordinator",
                            "phase": "execution_paused",
                            "active": False,
                            "queue_snapshot": task_queue.snapshot(),
                        },
                    ),
                )
            except Exception as pause_err:
                logger.debug(f"⚠️ Failed to publish execution_paused update: {pause_err}")

            return {
                **state,
                "results": {},
                "execution_paused": True,
                "status": "paused",
                "session_id": session_id,
                "original_message_id": original_message_id,
            }

        async def handle_confirmation_revision_loop(current_task: ActionTask, initial_result: TaskResult) -> TaskResult:
            """
            If language confirmation returns critique, regenerate draft via reasoning,
            then re-ask confirmation. Loop with a safe retry cap.
            """
            if current_task.target_agent != "language":
                return initial_result

            result = initial_result
            max_revision_rounds = 3
            revision_round = 0

            while (
                result.status == "awaiting_confirmation"
                and isinstance(result.metadata, dict)
                and result.metadata.get("confirmation_decision") == "critique"
                and revision_round < max_revision_rounds
            ):
                revision_round += 1
                critique_text = str(
                    result.metadata.get("user_critique")
                    or result.content
                    or ""
                ).strip()

                input_from = (current_task.extra_params or {}).get("input_from")
                prior_content = (
                    (current_task.extra_params or {}).get("input_content")
                    or result.metadata.get("draft_content")
                    or (task_outputs.get(input_from) if input_from else "")
                )

                if not prior_content:
                    logger.error(
                        f"❌ Cannot revise confirmation task {current_task.task_id}: missing prior draft content"
                    )
                    return TaskResult(
                        task_id=current_task.task_id,
                        status="failed",
                        error="Missing prior draft content for critique revision",
                    )

                revision_prompt = (
                    "Revise the previously drafted content using the user critique. "
                    "Preserve the same structure/format as the previous content.\n\n"
                    f"USER CRITIQUE:\n{critique_text}\n\n"
                    f"PREVIOUS CONTENT:\n{prior_content}\n\n"
                    "Return only the revised final content."
                )

                revision_task = ActionTask(
                    task_id=f"{current_task.task_id}_revise_{revision_round}",
                    goal=current_task.goal,
                    ai_prompt=revision_prompt,
                    device=current_task.device,
                    context="local",
                    extra_params={
                        "overall_goal": (current_task.extra_params or {}).get("overall_goal", current_task.goal),
                        "goal": (current_task.extra_params or {}).get("goal", current_task.goal),
                        "input_content": prior_content,
                    },
                    web_params={},
                    target_agent="reasoning",
                    depends_on=None,
                )

                logger.info(
                    f"🔁 Confirmation critique detected for {current_task.task_id}; "
                    f"starting revision round {revision_round}/{max_revision_rounds}"
                )
                revision_result = await execute_single_task(
                    revision_task,
                    session_id,
                    original_message_id,
                    user_language,
                    output_language,
                    user_profile,
                    user_id,
                )
                results[revision_task.task_id] = revision_result
                task_queue.log_execution(revision_task, revision_result)

                if revision_result.status != "success" or not (revision_result.content or "").strip():
                    logger.error(
                        f"❌ Revision failed for {current_task.task_id} on round {revision_round}: "
                        f"{revision_result.error or 'empty content'}"
                    )
                    return TaskResult(
                        task_id=current_task.task_id,
                        status="failed",
                        error=revision_result.error or "Failed to regenerate revised content",
                        metadata={"source": "confirmation_revision"},
                    )

                revised_content = revision_result.content.strip()
                if input_from:
                    task_outputs[input_from] = revised_content
                current_task.extra_params = dict(current_task.extra_params or {})
                current_task.extra_params["input_content"] = revised_content

                result = await execute_single_task(
                    current_task,
                    session_id,
                    original_message_id,
                    user_language,
                    output_language,
                    user_profile,
                    user_id,
                )

            if (
                result.status == "awaiting_confirmation"
                and isinstance(result.metadata, dict)
                and result.metadata.get("confirmation_decision") == "critique"
            ):
                return TaskResult(
                    task_id=current_task.task_id,
                    status="failed",
                    error="Maximum revision attempts reached without approval",
                    metadata={"source": "confirmation_revision", "max_rounds": max_revision_rounds},
                )

            if (
                result.status == "failed"
                and isinstance(result.metadata, dict)
                and result.metadata.get("confirmation_decision") == "rejected"
            ):
                return TaskResult(
                    task_id=current_task.task_id,
                    status="failed",
                    error="User rejected the drafted content",
                    metadata={"source": "confirmation", "decision": "rejected"},
                )

            return result

        task_queue.reset()
        task_queue.add_to_current(tasks)
        for queued_plan in queued_task_plans:
            if queued_plan:
                task_queue.add_to_global({"tasks": queued_plan})
        if queued_task_plans:
            logger.info(
                f"📚 Added {len(queued_task_plans)} independent plan(s) to global queue"
            )
        
        results = {}
        task_outputs = {}
        clarification_event = None

        # Signal UI layers that coordinator execution phase has started.
        try:
            await broker.publish(
                Channels.WEBSOCKET_OUTPUT,
                AgentMessage(
                    message_type=MessageType.TASK_PROGRESS,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    response_to=original_message_id,
                    payload={
                        "ws_type": "task_progress",
                        "stage": "coordinator",
                        "phase": "execution_started",
                        "active": True,
                        "queue_snapshot": task_queue.snapshot(),
                    },
                ),
            )
        except Exception as _phase_start_err:
            logger.warning(f"⚠️ Failed to publish coordinator start phase: {_phase_start_err}")

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
                # Original upstream version (commented for reference)
                # await save_checkpoint_compat(
                #     session_id,
                #     {"execution_state": execution_state},
                #     {"type": "task_progress"}
                # )
                
                # Stashed changes version - use both methods for redundancy
                await checkpointer.aput(
                    config={"configurable": {"thread_id": session_id, "checkpoint_ns": ""}},
                    checkpoint={
                        "v": 1,
                        "id": str(uuid.uuid4()),
                        "ts": datetime.now().isoformat(),
                        "channel_values": {"execution_state": execution_state},
                        "channel_versions": {},
                        "versions_seen": {},
                        "pending_sends": [],
                    },
                    metadata={"step": 0, "type": "task_progress"},
                    new_versions=[]
                )
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
                    {"step": 0, "type": "task_progress"}  # step key is required
                )
                logger.info(f"💾 Saved task progress")
            except Exception as e:
                logger.error(f"❌ Failed to save task progress: {e}") 
        
        while task_queue.has_tasks():
            if task_queue.is_stopped:
                logger.warning("⏹️ Execution stopped by user")
                break
                
            while task_queue.is_paused:
                if task_queue.is_stopped:
                    break
                await asyncio.sleep(0.5)

            if task_queue.is_stopped:
                logger.warning("⏹️ Execution stopped while paused")
                break
            
            current_task = task_queue.get_next_task()
            if not current_task:
                break

            # Defense-in-depth: ensure dispatched tasks always retain shared goal context.
            if current_task.extra_params is None:
                current_task.extra_params = {}
            if not current_task.extra_params.get("overall_goal"):
                current_task.extra_params["overall_goal"] = current_task.goal or current_task.ai_prompt
            if not current_task.extra_params.get("goal"):
                current_task.extra_params["goal"] = current_task.goal or current_task.ai_prompt

            if not current_task.extra_params.get("app_name") and current_task.depends_on:
                dep_raw = current_task.depends_on
                dep_ids = dep_raw if isinstance(dep_raw, list) else [dep_raw]
                for dep_id in dep_ids:
                    dep_id = dep_id.strip() if isinstance(dep_id, str) else dep_id
                    dep_task = next((t for t in state.get("tasks", []) if getattr(t, "task_id", None) == dep_id), None)
                    dep_app = (getattr(dep_task, "extra_params", {}) or {}).get("app_name") if dep_task else ""
                    if dep_app:
                        current_task.extra_params["app_name"] = dep_app
                        break
            
            # Check dependencies
            if current_task.depends_on:
                dep_ids = current_task.depends_on
                dependencies_met = all(
                   results.get(dep_id.strip()) 
                   and results.get(dep_id.strip()).status in {"success", "awaiting_confirmation"}
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
                                    res_val = parsed["result"]
                                    if isinstance(res_val, (dict, list)):
                                        stripped = json.dumps(res_val, ensure_ascii=False)
                                    else:
                                        stripped = str(res_val)
                            except Exception:
                                pass
                        raw_dep_output = stripped
                    
                    if not raw_dep_output or (isinstance(raw_dep_output, str) and not raw_dep_output.strip()):
                        logger.warning(f"Warning: Reasoning task {dep_id} produced empty output")
                        results[current_task.task_id] = TaskResult(
                            task_id=current_task.task_id,
                            status="failed",
                            error=f"Dependency output was empty: {dep_id}"
                        )
                        continue

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
            try:
                await broker.publish(
                    Channels.WEBSOCKET_OUTPUT,
                    AgentMessage(
                        message_type=MessageType.TASK_PROGRESS,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=original_message_id,
                        payload={
                            "ws_type": "task_progress",
                            "stage": "coordinator",
                            "phase": "executing_task",
                            "active": True,
                            "task_id": current_task.task_id,
                            "task_title": current_task.ai_prompt,
                            "queue_snapshot": task_queue.snapshot(),
                        },
                    ),
                )
            except Exception as progress_err:
                logger.debug(f"Could not publish per-task progress: {progress_err}")

            task_device = _normalize_device_type(getattr(current_task, "device", "desktop"))
            
            # FIX: Language tasks must ALWAYS execute on the source device (where the user is)
            # They require user interaction (confirmation/response) which cannot happen over remote dispatch
            if current_task.target_agent == "language":
                task_device = current_device
                logger.info(f"🔧 Language task {current_task.task_id} forced to source device ({current_device}) for user interaction")
            
            if task_device and task_device != current_device:
                result = await route_single_task(
                    current_task,
                    session_id,
                    original_message_id,
                    user_language,
                    output_language,
                    user_profile,
                    user_id,
                    current_device_id,
                    forced_target_device_id=getattr(current_task, "target_device_id", None),
                )
            else:
                result = await execute_single_task(
                    current_task, session_id, original_message_id,
                    user_language, output_language, user_profile,
                    user_id
                )
            result = await handle_confirmation_revision_loop(current_task, result)

            fallback_result = await _attempt_email_web_fallback(
                current_task,
                result,
                session_id,
                original_message_id,
                user_language,
                output_language,
                user_profile,
                user_id,
            )
            if fallback_result is not None:
                result = fallback_result
            
            results[current_task.task_id] = result
            task_queue.log_execution(current_task, result)
            _session_execution_context[session_id] = current_task.ai_prompt

            try:
                default_success = f"Completed: {current_task.ai_prompt}"
                default_failure = f"Failed: {current_task.ai_prompt}"
                await broker.publish(
                    Channels.WEBSOCKET_OUTPUT,
                    AgentMessage(
                        message_type=MessageType.TASK_PROGRESS,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=original_message_id,
                        payload={
                            "ws_type": "task_progress",
                            "stage": "coordinator",
                            "phase": "task_result",
                            "active": True,
                            "task_id": current_task.task_id,
                            "queue_snapshot": task_queue.snapshot(),
                            "task_result": {
                                "task_id": current_task.task_id,
                                "status": result.status,
                                "success_message": current_task.success_message or default_success,
                                "failure_message": current_task.failure_message or default_failure,
                                "content": result.content,
                                "details": result.details,
                                "metadata": result.metadata or {},
                            },
                        },
                    ),
                )
            except Exception as task_result_ws_err:
                logger.debug(f"Could not publish task_result update: {task_result_ws_err}")
            
            if current_task.context == "web" and current_task.target_agent == "action" and session_id:
                extracted_url = None
                url_match = re.search(r'PAGE_URL:(https?://[^\s\n]+)', result.content or "")
                if url_match:
                    extracted_url = url_match.group(1).strip()
                # Also capture URLs from fast-path navigation results like "Navigated to <url>"
                if not extracted_url:
                    nav_match = re.search(r'Navigated to (https?://[^\s\n]+)', result.content or "")
                    if nav_match:
                        extracted_url = nav_match.group(1).strip()
                # Also capture from web_params/extra_params URL (task carried explicit URL)
                if not extracted_url:
                    wp_url = (getattr(current_task, 'web_params', None) or {}).get('url') or \
                             (getattr(current_task, 'extra_params', None) or {}).get('url')
                    if wp_url and result.status == "success":
                        extracted_url = wp_url
                if extracted_url:
                    _state = _session_browser_state.get(session_id, {})
                    _state.update({
                        "current_page_url": extracted_url,
                        "last_web_task": current_task.ai_prompt
                    })
                    _session_browser_state[session_id] = _state
                    logger.info(f"📍 Browser state saved: {extracted_url}")
            
            # ✅ CAPTURE RICHEST AVAILABLE OUTPUT FOR CROSS-AGENT DATA SHARING
            # Prefer extracted_data (structured) over plain content when available
            output_to_store = None
            web_action = (getattr(current_task, 'web_params', None) or {}).get('action')
            extracted_payload = getattr(result, 'extracted_data', None)
            if isinstance(extracted_payload, str):
                extracted_payload = extracted_payload.strip() or None
            
            if web_action == "extract" and extracted_payload:
                output_to_store = json.dumps(extracted_payload) if isinstance(extracted_payload, dict) else str(extracted_payload)
                logger.info(f"📊 Storing extracted_data from {current_task.task_id} (extract)")
            elif hasattr(result, 'extracted_data') and result.extracted_data:
                # Web extraction result - prefer rich structured data
                output_to_store = json.dumps(result.extracted_data) if isinstance(result.extracted_data, dict) else str(result.extracted_data)
                logger.info(f"📊 Storing extracted_data from {current_task.task_id} (structured)")
            elif result.content:
                # Plain text content
                cleaned_content = result.content.replace("EXECUTION_SUCCESS", "").replace("FAILED:", "").strip()
                #hala edit ashan el web
                if current_task.context == "web" and current_task.target_agent == "action":
                    cleaned_content = re.sub(r'\nPAGE_URL:https?://[^\s\n]+', '', cleaned_content).strip()
                # task_outputs[current_task.task_id] = result.content
                            # Only store if there's actual content
                cleaned_content = re.sub(r'\nPAGE_URL:https?://[^\s\n]+', '', cleaned_content).strip()
                if cleaned_content and cleaned_content.strip() != "(fallback_extract)":
                    output_to_store = cleaned_content
                    logger.info(f"💾 Storing cleaned content from {current_task.task_id}")
                elif current_task.context == "web" and current_task.target_agent == "action":
                    url_fallback = None
                    url_match = re.search(r'PAGE_URL:(https?://[^\s\n]+)', result.content or "")
                    if url_match:
                        url_fallback = url_match.group(1).strip()
                    if not url_fallback:
                        nav_match = re.search(r'Navigated to (https?://[^\s\n]+)', result.content or "")
                        if nav_match:
                            url_fallback = nav_match.group(1).strip()
                    if not url_fallback and session_id:
                        url_fallback = _session_browser_state.get(session_id, {}).get("current_page_url")
                    if url_fallback:
                        output_to_store = f"PAGE_URL:{url_fallback}"
                        logger.info(f"💾 Storing page URL from {current_task.task_id}")
            
            if output_to_store:
                task_outputs[current_task.task_id] = output_to_store
                logger.info(f"💾 Stored output for {current_task.task_id}")
                logger.info(f"   Length: {len(output_to_store)} chars")
                logger.info(f"   Preview: {output_to_store[:200]}...")
                
                # Cache YouTube results for "open the first/second result" follow-ups
                if (
                    session_id
                    and getattr(current_task, 'target_agent', '') == 'api'
                    and (getattr(current_task, 'extra_params', {}) or {}).get('operation') == 'youtube_search'
                ):
                    try:
                        parsed = json.loads(output_to_store) if isinstance(output_to_store, str) else output_to_store
                        videos = parsed.get("videos", []) if isinstance(parsed, dict) else []
                        if videos:
                            _session_youtube_results[session_id] = videos
                            logger.info(f"📹 Cached {len(videos)} YouTube results for session {session_id}")
                    except Exception:
                        pass
            else:
                logger.warning(f"⚠️ Task {current_task.task_id} produced empty output")

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
                            user_language, output_language, user_profile,
                            user_id
                        )
                        results[resolve_task.task_id] = resolve_result
                        task_queue.log_execution(resolve_task, resolve_result)

                        if resolve_result.status == "success":
                            logger.info("✅ Self-resolution succeeded, continuing workflow")
                            continue
                        event["decision"] = "ask_user"
                        event["decision_reason"] = "Self-resolution failed"

                if event.get("decision") in {"ask_user", "fail_safely"}:
                    if (
                        session_id
                        and event.get("decision") == "ask_user"
                        and event.get("clarification_type") == "credentials_required"
                    ):
                        _state = _session_browser_state.get(session_id, {})
                        _state.update(
                            {
                                "pending_clarification_type": "credentials_required",
                                "paused_task_data": current_task.model_dump(),
                                "paused_at": datetime.now().isoformat(),
                            }
                        )
                        _session_browser_state[session_id] = _state
                    clarification_event = event
                    if event.get("decision") == "fail_safely":
                        task_queue.current_queue.clear()
                    break

                
            if result.status == "failed":
                logger.error(f"❌ Task {current_task.task_id} failed: {result.error}")
                task_queue.current_queue.clear()
                break
        
        # ── ICRL: Plan-level retry if execution failed ────────────────────────
        # If the plan failed and we have session context, compute a plan-level
        # reward and store it. The coordinator graph currently runs once per
        # request, so plan-level ICRL retries are handled via the LangGraph
        # checkpoint mechanism — the next invocation will have ICRL history.
        # Here we store the plan-level reward in the state for potential future use.
        plan_success_count = sum(1 for r in results.values() if r.status == "success")
        plan_total = len(results)
        plan_reward = plan_success_count / max(plan_total, 1)

        if ICRL_ENABLED and session_id and plan_total > 0:
            try:
                plan_goal = state.get("input", {}).get(
                    "original_input",
                    state.get("input", {}).get("confirmation", "unknown goal")
                )
                plan_buffer_key = f"{session_id}:__plan__"
                if plan_buffer_key not in _icrl_buffers:
                    _icrl_buffers[plan_buffer_key] = ICRLBuffer(goal=plan_goal)

                plan_buffer = _icrl_buffers[plan_buffer_key]
                tasks_summary = "; ".join(
                    t.ai_prompt[:60] for t in state.get("tasks", [])
                )
                plan_buffer.add(
                    attempt_summary=f"Plan with {plan_total} tasks: {tasks_summary}",
                    reward=plan_reward,
                    result_snippet=f"{plan_success_count}/{plan_total} tasks succeeded",
                )
                logger.info(
                    f"📊 ICRL plan-level: reward={plan_reward:.3f} "
                    f"({plan_success_count}/{plan_total} tasks), "
                    f"buffer={plan_buffer.summary()}"
                )
            except Exception as _plan_icrl_err:
                logger.warning(f"⚠️ ICRL plan recording failed (non-fatal): {_plan_icrl_err}")
        # ─────────────────────────────────────────────────────────────────────

        return {
            **state,
            "results": results,
            "execution_clarification": clarification_event,
            "status": "completed",
            "session_id": session_id,
            "_execution_stopped_by_user": task_queue.is_stopped,
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
                # Handle YouTube/API structured results first
                if "videos" in raw and isinstance(raw["videos"], list):
                    lines = []
                    for i, v in enumerate(raw["videos"], 1):
                        title = v.get("title", "Unknown")
                        channel = v.get("channel", "Unknown")
                        vid_id = v.get("video_id", "")
                        url = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
                        lines.append(f"{i}. {title} — by {channel}\n   {url}")
                    return "\n".join(lines) if lines else "No results found."
                if "events" in raw and isinstance(raw["events"], list):
                    lines = []
                    for i, e in enumerate(raw["events"], 1):
                        summary = e.get("summary", "No title")
                        start = e.get("start", "")
                        lines.append(f"{i}. {summary} — {start}")
                    return "\n".join(lines) if lines else "No events found."
                if "files" in raw and isinstance(raw["files"], list):
                    lines = []
                    for i, f_item in enumerate(raw["files"], 1):
                        name = f_item.get("name", "Unknown")
                        lines.append(f"{i}. {name}")
                    return "\n".join(lines) if lines else "No files found."
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
        plan_error = state.get("plan_error", "")

        original_request = state["input"].get("original_input", state["input"].get("confirmation", state["input"].get("action", "")))
        user_language = state["input"].get("user_language") or "en"
        output_language = state["input"].get("output_language") or user_language
        user_profile = state["input"].get("user_profile") or {}
        is_arabic = user_language == "ar"

        routing_result = state.get("routing_result")
        if routing_result:
            status = routing_result.get("status")
            target_id = routing_result.get("target_device_id")
            delivery_method = routing_result.get("delivery_method")

            if status == "running":
                response_text = (
                    "تم إرسال المهمة للجهاز الآخر الآن."
                    if is_arabic
                    else "Sent the task to your other device now."
                )
            elif status == "queued":
                response_text = (
                    "الجهاز الآخر غير متصل الآن. تم حفظ المهمة وسيتم إرسالها عند الاتصال."
                    if is_arabic
                    else "The target device is offline. The task is queued and will be delivered when it reconnects."
                )
            else:
                response_text = (
                    "لم أجد جهازاً آخر متاحاً لهذا المستخدم. تأكد أن جهازاً آخر متصل."
                    if is_arabic
                    else "No other target device was found. Please make sure another device is connected."
                )

            structured = StructuredResponse(
                type=ResponseType.SIMPLE_ACK,
                spoken_text=response_text,
                full_content="",
                offer_read_aloud=False,
                offer_actions=["retry"],
                context_for_undo={"original_request": original_request},
            )

            response_msg = AgentMessage(
                message_type=MessageType.TASK_RESPONSE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                response_to=original_message_id,
                payload={
                    "status": "completed",
                    "response": response_text,
                    "user_language": user_language,
                    "output_language": output_language,
                    "structured_response": structured.model_dump(),
                    "routing": {
                        "status": status,
                        "target_device_id": target_id,
                        "delivery_method": delivery_method,
                    },
                },
            )
            await broker.publish(Channels.COORDINATOR_TO_LANGUAGE, response_msg)

            await broker.publish(
                Channels.WEBSOCKET_OUTPUT,
                AgentMessage(
                    message_type=MessageType.STRUCTURED_RESPONSE,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    response_to=original_message_id,
                    payload={
                        **structured.model_dump(),
                        "user_language": user_language,
                    },
                ),
            )
            return {"status": "completed"}

        if state.get("execution_paused"):
            plan_count = len(state.get("tasks", []))
            response_text = (
                f"تم إيقاف التنفيذ مؤقتًا. الخطة جاهزة وبها {plan_count} خطوة."
                if is_arabic
                else f"Execution is paused. The plan is ready with {plan_count} steps."
            )

            structured = StructuredResponse(
                type=ResponseType.SIMPLE_ACK,
                spoken_text=response_text,
                full_content="",
                offer_read_aloud=False,
                offer_actions=["resume", "retry"],
                context_for_undo={"original_request": original_request},
            )

            response_msg = AgentMessage(
                message_type=MessageType.TASK_RESPONSE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                response_to=original_message_id,
                payload={
                    "status": "paused",
                    "response": response_text,
                    "user_language": user_language,
                    "output_language": output_language,
                    "structured_response": structured.model_dump(),
                },
            )
            await broker.publish(Channels.COORDINATOR_TO_LANGUAGE, response_msg)
            await broker.publish(
                Channels.WEBSOCKET_OUTPUT,
                AgentMessage(
                    message_type=MessageType.STRUCTURED_RESPONSE,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    response_to=original_message_id,
                    payload={
                        **structured.model_dump(),
                        "user_language": user_language,
                    },
                ),
            )
            return {"status": "paused"}
        

        # Signal UI layers that coordinator execution phase finished.
        try:
            await broker.publish(
                Channels.WEBSOCKET_OUTPUT,
                AgentMessage(
                    message_type=MessageType.TASK_PROGRESS,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    response_to=original_message_id,
                    payload={
                        "ws_type": "task_progress",
                        "stage": "coordinator",
                        "phase": "execution_finished",
                        "active": False,
                        "queue_snapshot": task_queue.snapshot(),
                    },
                ),
            )
        except Exception as _phase_finish_err:
            logger.warning(f"⚠️ Failed to publish coordinator finish phase: {_phase_finish_err}")

        # ✅ FIX 3: Update conversation history — enriched with active app context
        # This is what tells Turn 2 ("write hello world") that Notepad is already open.
        # The app_name and context fields let the decomposition LLM skip re-opening apps.
        if success_count > 0:
            if "conversation_history" not in state:
                state["conversation_history"] = []

            # Collect the apps that were successfully opened/used in this turn
            apps_used_this_turn = list({
                (t.extra_params or {}).get("app_name", "")
                for t in state.get("tasks", [])
                if hasattr(t, "extra_params")
                and (t.extra_params or {}).get("app_name", "")
                and results.get(t.task_id) is not None
                and results[t.task_id].status == "success"
            } - {""})

            # Collect successful task prompts for richer context
            successful_actions = [
                t.ai_prompt[:80]
                for t in state.get("tasks", [])
                if hasattr(t, "task_id")
                and results.get(t.task_id) is not None
                and results[t.task_id].status == "success"
            ]

            history_entry = {
                "user_message": state['input'].get('original_input', state['input'].get('confirmation', state['input'].get('action', ''))),
                "action_taken": f"Executed {success_count} tasks",
                "actions_detail": successful_actions[:3],  # top 3 for brevity
                "result": "success" if success_count == total_count else "partial",
                "timestamp": datetime.now().isoformat(),
            }

            # If apps were used, record them so future turns can target the active window
            if apps_used_this_turn:
                history_entry["apps_currently_open"] = apps_used_this_turn
                logger.info(f"📝 Conversation history: apps now open = {apps_used_this_turn}")

            # If a browser page was navigated to, record it
            saved_browser = _session_browser_state.get(session_id, {})
            if saved_browser.get("current_page_url"):
                history_entry["current_page_url"] = saved_browser["current_page_url"]

            state["conversation_history"].append(history_entry)

            # Keep only last 10 interactions
            if len(state["conversation_history"]) > 10:
                state["conversation_history"] = state["conversation_history"][-10:]
        
        # original_request, user_language, output_language, user_profile, is_arabic are already defined above.

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

        # If user issued stop, do not emit a synthetic "task completed" response.
        # Send directly to WEBSOCKET_OUTPUT so frontend receives it immediately.
        if task_queue.is_stopped:
            stopped_text = (
                "تم الإيقاف. تم إلغاء المهمة."
                if is_arabic
                else "Stopped. Task cancelled."
            )
            stopped_structured = StructuredResponse(
                type=ResponseType.SIMPLE_ACK,
                spoken_text=stopped_text,
                full_content="",
                offer_read_aloud=False,
                offer_actions=["resume"],
                context_for_undo={"original_request": original_request},
            )

            stopped_msg = AgentMessage(
                message_type=MessageType.TASK_RESPONSE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                response_to=original_message_id,
                payload={
                    "status": "stopped",
                    "response": stopped_text,
                    "spoken_text": stopped_text,
                    "user_language": user_language,
                    "output_language": output_language,
                    "structured_response": stopped_structured.model_dump(),
                    "result": {
                        "completed_tasks": {k: v.status for k, v in results.items()},
                        "details": [v.model_dump() for v in results.values()],
                    },
                },
            )
            await broker.publish(Channels.WEBSOCKET_OUTPUT, stopped_msg)
            return {"status": "stopped"}

        # Override the error response if we had a planning error
        paginated_pages: List[Dict[str, Any]] = []
        if total_count == 0 and plan_error:
            # We don't have tasks because decomposition failed (e.g. rate limit). 
            # We construct a synthetic message to pass down to Language Agent.
            full_content = f"A task planning error occurred: {plan_error}"
            success_count = 0
            has_reasoning_content = False
            detail_lines = []
        else:
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
                        r_meta = r.metadata if isinstance(r.metadata, dict) else {}
                        paginated = r_meta.get("paginated_content") if isinstance(r_meta, dict) else None
                        pages = (((paginated or {}).get("content") or {}).get("pages") if isinstance(paginated, dict) else None)
                        if isinstance(pages, list) and pages:
                            paginated_pages = pages

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
                        "plan_error": plan_error,
                    },
                    lang=user_language,
                )
                response_text = completion_result.get("message", "")
                follow_ups = completion_result.get("follow_ups", [])
                logger.info(f"✅ Language Agent generated completion message: {response_text[:100]}")
        except Exception as e:
            logger.warning(f"⚠️ Language Agent message generation failed, using fallback: {e}")

        # ── Safe fallback if Language Agent unavailable ───────────────────────
        used_fallback = False
        if not response_text:
            used_fallback = True
            if total_count == 0 and plan_error:
                if "Rate limit" in plan_error or "429" in plan_error:
                    response_text = "بعتذر، نأسف لحدوث ضغط على النظام يرجى المحاولة لاحقاً." if is_arabic else "I apologize for the high system load, please try again later."
                else:
                    response_text = f"حدث خطأ أثناء التخطيط: {plan_error}" if is_arabic else f"Planning error occurred: {plan_error}"
            elif success_count == total_count and total_count > 0:
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

        # Build follow-up question for read-aloud offer (only if fallback used)
        follow_up_question = None
        if used_fallback:
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
            full_content=full_content if full_content and full_content != response_text else "",
            content_pages=paginated_pages,
            offer_read_aloud=has_reasoning_content and len(full_content) > 200,
            offer_actions=(follow_ups if follow_ups else []) + (["undo", "retry"] if success_count > 0 else ["retry"]),
            context_for_undo={"original_request": original_request, "completed_tasks": [t.task_id for t in state.get("tasks", [])]}
        )
        
        # ── DiD Layer 3: Output Validation ────────────────────────────────────
        try:
            from agents.security.output_validator import validate_output
            _val = validate_output(response_text, context="coordinator")
            if _val.was_modified:
                logger.warning(
                    f"🔒 Layer 3: Output violations detected: {_val.violations}"
                )
            response_text = _val.clean_text
        except Exception as _val_err:
            logger.warning(f"⚠️ Layer 3 output validation failed (non-fatal): {_val_err}")
        # ─────────────────────────────────────────────────────────────────────

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
        
        # ── ICRL: Store plan-level feedback in TaskMemory via FeedbackAgent ─────
        # This runs once per plan (not per task) to save tokens.
        # It stores the improvements list in TaskMemory so future similar tasks
        # get hint context from the execution agent's RAG retrieval.
        # Only runs when there were actual tasks to evaluate.
        if total_count > 0:
            try:
                from agents.feedback_agent import FeedbackAgent
                from agents.execution_agent.strategies.task_memory import TaskMemory

                # Build a compact trajectory from all tasks in this plan
                plan_trajectory = [
                    {
                        "action": t.ai_prompt,
                        "task_id": t.task_id,
                        "context": t.context,
                        "target_agent": t.target_agent,
                        "result": (results.get(t.task_id).content or "") if results.get(t.task_id) else "",
                        "status": (results.get(t.task_id).status or "unknown") if results.get(t.task_id) else "unknown",
                        "error": (results.get(t.task_id).error or "") if results.get(t.task_id) else "",
                    }
                    for t in state.get("tasks", [])
                    if hasattr(t, "task_id")
                ]

                fb_agent = FeedbackAgent()
                try:
                    task_memory = TaskMemory()
                    fb_agent.attach_memory(task_memory)
                except Exception:
                    pass  # TaskMemory optional

                # Use asyncio.to_thread since evaluate_and_store is synchronous
                import asyncio as _asyncio
                evaluation = await _asyncio.to_thread(
                    fb_agent.evaluate_and_store,
                    original_request,
                    plan_trajectory,
                    None,  # no user_feedback here
                )
                logger.info(
                    f"🎯 Plan-level FeedbackAgent: score={evaluation.score:.3f}, "
                    f"success={evaluation.is_success}, "
                    f"improvements={len(evaluation.improvements)}"
                )
                if evaluation.improvements:
                    logger.info(
                        f"💡 Plan improvements stored: "
                        + " | ".join(evaluation.improvements[:3])
                    )
            except Exception as _fb_err:
                logger.debug(f"⚠️ Plan-level FeedbackAgent storage failed (non-fatal): {_fb_err}")
        # ─────────────────────────────────────────────────────────────────────

        if success_count == total_count and total_count > 0:
            try:
                if not (success_count == total_count and total_count > 0):
                    return
                from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
                pref_mgr = get_preference_manager(user_id)

                # TC48: Skip extraction for pure task-only commands that contain
                # no personal signal. These commands open apps or perform generic
                # UI actions and will never yield a storable preference — calling
                # the LLM for them wastes tokens and adds latency.
                _TASK_ONLY_PREFIXES = (
                    "open ", "launch ", "start ", "close ", "run ",
                    "click ", "type ", "press ", "navigate to ", "go to ",
                    "scroll ", "copy ", "paste ", "select all",
                    "search for ", "search ", "play ", "watch ",
                    "set alarm", "set an alarm", "find ",
                )
                _original_req_raw = str(
                    state["input"].get("original_input",
                    state["input"].get("confirmation",
                    state["input"].get("action", "")))
                ).strip()
                _sanitized_request = sanitize_confirmation_for_prompt(_original_req_raw)
                _original_req_lower = _original_req_raw.lower()
                # A request has a personal signal if it mentions the user specifically.
                # Without a personal signal, there is nothing worth storing as a preference.
                _has_personal_signal = any(
                    marker in _original_req_lower
                    for marker in ["my ", " i ", "i'm ", "i am ", " me ", "@", "prefer", "always", "usually"]
                )
                _is_task_only = (
                    any(_original_req_lower.startswith(p) for p in _TASK_ONLY_PREFIXES)
                    and not _has_personal_signal
                )
                if _is_task_only:
                    logger.info(
                        f"⏭️ TC48: Skipping preference extraction for task-only command: "
                        f"'{_original_req_lower[:60]}'"
                    )
                else:
                    pass  # fall through to extraction block below

                if not _is_task_only:
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
2. FREQUENT_RECIPIENTS: Email addresses the user has sent TO (e.g., "User sent email to sara@example.com") — store as "User sent email to <address>", never as "User's email is" or "User has account"
3. PATTERNS: How the user works (e.g., "Sends emails with short subjects", "Opens YouTube for videos", "Sets early morning alarms")

STRICT RULES — violating these produces corrupted memory:
- Email addresses found in the task are RECIPIENTS the user contacted, never the user's own account or identity. Always store them as "User sent email to <address>" under category "frequent_recipient".
- NEVER store "User's email is X", "Has Gmail account X", "User has account X", or any phrasing that treats a recipient address as belonging to the user.
- NEVER store the number of steps, retries, or any system execution metric as a preference.
- NEVER store the content of what was typed or written (e.g. the body of a note or email) as a preference.
- Only extract behavioral patterns about HOW the user works, not WHAT was in a specific task.

OUTPUT FORMAT (JSON array):
[
  {{
    "preference": "User sent email to sara@example.com",
    "category": "frequent_recipient",
    "confidence": "medium"
  }}
]

If nothing genuinely behavioral was revealed (e.g. opening notepad once with no user data), return: []

Extract now:"""
                
                extraction_text = await llm_invoke_with_fallback(extraction_prompt)
                preferences_to_store = extract_json_payload(extraction_text, [])
                
                if preferences_to_store and isinstance(preferences_to_store, list):
                    for pref_obj in preferences_to_store:
                        if pref_obj.get("confidence") in ["high", "medium"]:
                            # TC60: If the original request was a note-writing task,
                            # the extracted "preference" may actually be the note's content
                            # masquerading as a behavioral fact. Block any preference whose
                            # text closely mirrors the content of a note/write task.
                            _pref_text = str(pref_obj.get("preference", "")).lower()
                            _NOTE_TASK_SIGNALS = [
                                "write note", "create note", "save note",
                                "add note", "note:", "write:", "record:",
                            ]
                            _original_was_note = any(
                                s in _original_req_lower for s in _NOTE_TASK_SIGNALS
                            )
                            if _original_was_note:
                                # Check if the extracted preference literally contains
                                # words from the note task content (not a behavioral pattern)
                                _NOTE_CONTENT_INDICATORS = [
                                    "lists desktop files", "always lists", "user always",
                                    "note says", "note content",
                                ]
                                if any(ind in _pref_text for ind in _NOTE_CONTENT_INDICATORS):
                                    logger.warning(
                                        f"🚫 TC60: Blocked note-content-as-preference: "
                                        f"'{_pref_text[:80]}'"
                                    )
                                    continue
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
                                logger.debug(f"⚠️ Could not store preference: {pref_err}")
                
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
                    f"User completed task: {_sanitized_request}. "
                    f"Apps used: {apps_used_str}. "
                    f"Task types: {', '.join(contexts_used)}."
                )
                
                # pref_mgr.add_preference_zero_token(
                #     conversation_context,
                #     metadata={
                #         "category": "conversation_history",
                #         "session_id": session_id,
                #         "timestamp": datetime.now().isoformat(),
                #         "apps_used": apps_used,
                #         "steps": success_count,
                #         "original_request": task_summary["original_request"]
                #     }
                # )
                
                # # Also store simpler version with regular method in try-except
                # try:
                #     conversation_context_simple = f"User requested: {task_summary['original_request']}. "
                #     conversation_context_simple += f"Successfully completed {success_count} steps."
                    
                #     pref_mgr.add_preference(
                #         conversation_context_simple,
                #         metadata={
                #             "category": "conversation_history",
                #             "session_id": session_id,
                #             "timestamp": datetime.now().isoformat()
                #         }
                #     )
                #     logger.info(f"💾 Stored conversation context")
                # except Exception as ctx_err:
                #     logger.debug(f"⚠️ Could not store conversation context (regular method): {ctx_err}")
                
                # conversation_history is NOT stored in Mem0 — it accumulates unboundedly
                # and floods the coordinator decomposition prompt on long sessions,
                # causing hallucination. It is persisted via LangGraph checkpoint instead
                # (state["conversation_history"] → checkpoint at end of send_feedback).
                logger.debug(f"⏭️ Skipping Mem0 conversation_history write — handled by checkpoint")
            except Exception as e:
                logger.debug(f"⚠️ Preference storage operation encountered issue: {e}")
        # ── Pattern Learning: detect behavioral patterns from history ─────────
        # Runs after every fully successful task. Patterns are stored in Mem0
        # under category="learned_pattern" and surface in the next request's
        # decomposition prompt via get_relevant_preferences(), giving the
        # coordinator a head start on tasks the user repeats frequently.
        # This is the bridge between pattern_learner and the ICRL loop:
        # better priors → better first-round plans → fewer ICRL retries needed.
        if success_count == total_count and total_count > 0:
            try:
                from agents.coordinator_agent.memory.pattern_learner import run_pattern_learning
                from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
                _pl_mgr = get_preference_manager(user_id)
                _new_patterns = run_pattern_learning(user_id, _pl_mgr)
                if _new_patterns:
                    logger.info(f"🧠 Pattern learner stored {_new_patterns} new/updated patterns for {user_id}")
            except Exception as _pl_err:
                logger.debug(f"⚠️ Pattern learning failed (non-fatal): {_pl_err}")
        # ─────────────────────────────────────────────────────────────────────

        if task_queue.global_queue:
            logger.info(f"📋 Processing next task from global queue")

        # ── Persist conversation_history to checkpoint so it survives restart ──
        # Without this, a server restart empties the history and Turn 2 ("leave
        # the meeting") wouldn't know Discord is open from a prior turn.
        if checkpointer and session_id and state.get("conversation_history"):
            try:
                await save_checkpoint_compat(
                    session_id,
                    {
                        "v": 1,
                        "id": str(uuid.uuid4()),
                        "ts": datetime.now().isoformat(),
                        "channel_values": {
                            "conversation_history": state["conversation_history"]
                        },
                        "channel_versions": {},
                        "versions_seen": {},
                        "pending_sends": [],
                    },
                    {"step": 0, "type": "conversation_history"}
                )
                logger.info(
                    f"💾 Persisted conversation_history "
                    f"({len(state['conversation_history'])} entries) to checkpoint"
                )
            except Exception as _ch_err:
                logger.debug(f"⚠️ conversation_history persist failed (non-fatal): {_ch_err}")
        # ─────────────────────────────────────────────────────────────────────

        return {"status": "completed"}

    # Build graph
    # ── ICRL: Plan-level retry node ───────────────────────────────────────────
    MAX_ICRL_PLAN_RETRIES = 2  # Max times to re-decompose a failing plan
    ICRL_RETRY_SUCCESS_THRESHOLD = 0.6  # Minimum success ratio to skip retry
    # ICRL_RETRY_SUCCESS_THRESHOLD = 1.1

    async def maybe_retry_plan(state: Dict) -> Dict:
        """
        ICRL plan-level retry node — fully looping implementation.

        Loops internally up to MAX_ICRL_PLAN_RETRIES times so that each
        retry passes through this node's decision logic rather than bypassing it.
        The previous implementation called execute_tasks() directly on retry,
        which meant rounds 2+ were structurally unreachable.

        Exit conditions (checked at start of every iteration):
        - execution_clarification set → skip (not a plan failure)
        - icrl_round >= MAX_ICRL_PLAN_RETRIES → max retries hit
        - plan_reward >= ICRL_RETRY_SUCCESS_THRESHOLD → good enough
        - plan_error set → decomposition itself failed
        """
        current_state = state

        # ── ICRL master gate: bail out early when ICRL is globally disabled
        if not ICRL_ENABLED:
            logger.debug("🔄 ICRL disabled — skipping plan retry node")
            return {**current_state, "_icrl_plan_round": 0}

        while True:
            results = current_state.get("results", {})
            session_id = current_state.get("session_id")
            icrl_round = current_state.get("_icrl_plan_round", 0)

            if not session_id or not results:
                return {**current_state, "_icrl_plan_round": icrl_round}

            # Skip ICRL retries for mobile plans — mobile task failures are
            # usually environmental (app not installed, permissions) not plan errors.
            _plan_device = str(
                current_state.get("input", {}).get("device_type", "desktop")
            ).strip().lower()
            if _plan_device in {"mobile", "android", "ios", "phone", "tablet"}:
                logger.debug("🔄 ICRL: Skipping plan retry — mobile device")
                return {**current_state, "_icrl_plan_round": icrl_round}

            # Results may be TaskResult objects OR plain dicts depending on
            # whether LangGraph checkpointing serialized them. Handle both.
            def _get_status(r) -> str:
                if isinstance(r, dict):
                    return r.get("status", "unknown")
                return getattr(r, "status", "unknown")

            success_count = sum(1 for r in results.values() if _get_status(r) == "success")
            total_count = len(results)
            plan_reward = success_count / max(total_count, 1)
            task_queue = get_session_task_queue(session_id)

            logger.info(
                f"🔄 ICRL plan check: round={icrl_round}, "
                f"reward={plan_reward:.2f} ({success_count}/{total_count}), "
                f"max_retries={MAX_ICRL_PLAN_RETRIES}"
            )

            # ── Exit conditions ───────────────────────────────────────────────

            if current_state.get("execution_clarification"):
                logger.info("🔄 ICRL: Skipping plan retry — execution stopped for clarification, not failure")
                return {**current_state, "_icrl_plan_round": icrl_round}

            if current_state.get("_execution_stopped_by_user"):
                logger.info("🔄 ICRL: Skipping plan retry — execution was stopped by user, not a plan failure")
                return {**current_state, "_icrl_plan_round": icrl_round}

            if icrl_round >= MAX_ICRL_PLAN_RETRIES:
                logger.info("🔄 ICRL: Max plan retries reached, proceeding to feedback")
                return {**current_state, "_icrl_plan_round": icrl_round}

            if plan_reward >= ICRL_RETRY_SUCCESS_THRESHOLD:
                logger.info(
                    f"🔄 ICRL: Plan reward {plan_reward:.2f} >= threshold "
                    f"{ICRL_RETRY_SUCCESS_THRESHOLD}, no retry needed"
                )
                return {**current_state, "_icrl_plan_round": icrl_round}

            if current_state.get("plan_error"):
                logger.info("🔄 ICRL: Skipping plan retry due to decomposition error")
                return {**current_state, "_icrl_plan_round": icrl_round}

            # ── Guard: only retry decomposition if failure was decomposition-related ──
            # If all failures were execution errors (bad selectors, timeouts, pyautogui
            # crashes), re-decomposing produces the same plan and wastes an API call.
            # Only retry when at least one task failed due to wrong decomposition or
            # broken dependency chains.
            failed_tasks_this_round = [
                t for t in current_state.get("tasks", [])
                if results.get(t.task_id) and results[t.task_id].status == "failed"
            ]
            failure_types_this_round = set()
            for ft in failed_tasks_this_round:
                ft_result = results[ft.task_id]
                failure_types_this_round.add(
                    classify_failure_type(ft.model_dump(), ft_result.model_dump())
                )

               
            # If every failure is a pure execution error, re-decomposing may still help
            # (e.g. wrong task order, wrong app, wrong step sequence).
            # Only skip retry if ALL failures are dependency-chain failures caused by
            # a single upstream task — in that case a different decomposition won't help
            # until the upstream task itself is fixed.
            # "execution" failures ARE retryable because the decomposition may have
            # chosen the wrong approach (wrong app, wrong sequence, missing confirmation step).
            # If every failure is a pure execution timeout, re-decomposing produces
            # the same plan and wastes API calls. Detect this and skip.
            def _is_timeout_result(task_obj) -> bool:
                r = results.get(task_obj.task_id)
                if r is None:
                    return False
                error_str = str(getattr(r, "error", "") or "").lower()
                content_str = str(getattr(r, "content", "") or "").lower()
                return "timeout" in error_str or "timeout" in content_str

            all_timed_out = bool(failed_tasks_this_round) and all(
                _is_timeout_result(t) for t in failed_tasks_this_round
            )

            if all_timed_out:
                logger.info(
                    "🔄 ICRL: Skipping plan retry — all failures are execution timeouts "
                    "(re-decomposing the same task will produce the same timeout)"
                )
                return {**current_state, "_icrl_plan_round": icrl_round}

            ALWAYS_SKIP_RETRY_TYPES = set()  # currently nothing is truly un-retryable at plan level
            has_retryable_failure = bool(
                failure_types_this_round - ALWAYS_SKIP_RETRY_TYPES
            )
            if not has_retryable_failure and failure_types_this_round:
                logger.info(
                    f"🔄 ICRL: Skipping plan retry — no retryable failures "
                    f"({failure_types_this_round})"
                )
                return {**current_state, "_icrl_plan_round": icrl_round}
            # ── Plan failed — attempt retry ───────────────────────────────────
            new_icrl_round = icrl_round + 1
            logger.info(
                f"🔄 ICRL: Plan reward {plan_reward:.2f} < threshold, "
                f"retrying decomposition (round {new_icrl_round}/{MAX_ICRL_PLAN_RETRIES})"
            )

            plan_buffer_key = f"{session_id}:__plan__"
            plan_buffer = _icrl_buffers.get(plan_buffer_key)
            if plan_buffer is None:
                plan_goal = current_state.get("input", {}).get(
                    "original_input",
                    current_state.get("input", {}).get("confirmation", "unknown goal")
                )
                plan_buffer = ICRLBuffer(goal=plan_goal)
                _icrl_buffers[plan_buffer_key] = plan_buffer

            device_type = current_state.get("input", {}).get("device_type", "desktop")
            device_type = _normalize_device_type(device_type)
            user_id = current_state.get("user_id", "default_user")
            original_message_id = current_state.get("original_message_id")

            try:
                from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
                pref_mgr = get_preference_manager(user_id)
                preferences_context = pref_mgr.get_relevant_preferences(
                    str(current_state.get("input", {}).get("original_input", "")), limit=5
                )
            except Exception:
                preferences_context = current_state.get("preferences_context", "No preferences available")

            saved_browser = _session_browser_state.get(session_id, {})
            current_page_url = saved_browser.get("current_page_url")

            plan_result = await decompose_task_to_actions_with_icrl(
                user_request=current_state["input"],
                preferences_context=preferences_context,
                device_type=device_type,
                conversation_history=current_state.get("conversation_history", []),
                session_id=session_id,
                http_request_id=original_message_id,
                current_page_url=current_page_url,
                icrl_buffer=plan_buffer,
                icrl_round=new_icrl_round,
            )

            if plan_result.get("error") or not plan_result.get("tasks"):
                logger.warning(
                    f"⚠️ ICRL plan retry failed to produce new tasks: "
                    f"{plan_result.get('error', 'no tasks')}"
                )
                return {**current_state, "_icrl_plan_round": new_icrl_round}

            new_tasks = plan_result["tasks"]
            logger.info(
                f"✅ ICRL: New plan generated ({len(new_tasks)} tasks) for round {new_icrl_round}"
            )
            # Log the full new decomposition so you can see what changed
            try:
                new_tasks_dump = [t.model_dump() if hasattr(t, 'model_dump') else t for t in new_tasks]
                logger.info(
                    f"📋 ICRL Retry Round {new_icrl_round} — New Decomposition:\n"
                    + "\n".join(
                        f"  [{i+1}] {t.get('task_id')} | agent={t.get('target_agent')} | "
                        f"context={t.get('context')} | prompt={t.get('ai_prompt', '')[:80]}"
                        for i, t in enumerate(new_tasks_dump)
                    )
                )
                logger.info(
                    f"📋 ICRL Plan Buffer state BEFORE executing round {new_icrl_round}:\n"
                    f"{plan_buffer.dump()}"
                )
            except Exception as _log_err:
                logger.debug(f"ICRL decomposition log failed (non-fatal): {_log_err}")

            task_queue.reset()
            task_queue.add_to_current(new_tasks)

            # Execute the new plan and update current_state for the next loop iteration
            exec_state = {
                **current_state,
                "tasks": new_tasks,
                "results": {},
                "_icrl_plan_round": new_icrl_round,
                "execution_clarification": None,  # reset clarification for new attempt
                "plan_error": None,
            }
            executed_state = await execute_tasks(exec_state)
            current_state = executed_state

            # ── Update plan buffer with this retry's result ───────────────────
            # Without this, the LLM sees the same buffer on round N+1 as round N.
            retry_results = executed_state.get("results", {})
            retry_success = sum(1 for r in retry_results.values() if r.status == "success")
            retry_total = len(retry_results)
            retry_reward = retry_success / max(retry_total, 1)

            tasks_summary = "; ".join(
                t.ai_prompt[:60] for t in new_tasks
            )
            # Classify plan failure type from the failed tasks in this retry
            failed_tasks_in_retry = [
                t for t in new_tasks
                if retry_results.get(t.task_id) and retry_results[t.task_id].status == "failed"
            ]
            plan_failure_type = "unknown"
            if failed_tasks_in_retry:
                ft = classify_failure_type(
                    failed_tasks_in_retry[0].model_dump(),
                    retry_results[failed_tasks_in_retry[0].task_id].model_dump(),
                )
                plan_failure_type = ft

            new_attempt = plan_buffer.add(
                attempt_summary=f"[Round {new_icrl_round}] Plan with {retry_total} tasks: {tasks_summary}",
                reward=retry_reward,
                result_snippet=f"{retry_success}/{retry_total} tasks succeeded",
            )
            new_attempt.failure_type = plan_failure_type

            logger.info(
                f"📊 ICRL plan buffer updated after retry round {new_icrl_round}: "
                f"reward={retry_reward:.3f}, failure_type={plan_failure_type}"
            )
            logger.info(
                f"📋 ICRL plan buffer full state after round {new_icrl_round}:\n"
                f"{plan_buffer.dump()}"
            )
            # Loop back to check if this new result meets the threshold

    def route_after_execution(state: Dict) -> str:
        """
        Route after execute_tasks:
        - If plan failed badly AND retries remain → maybe_retry_plan
        - Otherwise → feedback
        
        We always go through maybe_retry_plan; it decides internally
        whether to actually retry or pass through.
        """
        return "maybe_retry_plan"

    # ─────────────────────────────────────────────────────────────────────────

    # Build graph
    graph.add_node("analyze", analyze_and_plan)
    graph.add_node("execute", execute_tasks)
    graph.add_node("maybe_retry_plan", maybe_retry_plan)
    graph.add_node("feedback", send_feedback)

    graph.set_entry_point("analyze")

    def route_after_analysis(state):
        return "execute"

    graph.add_conditional_edges("analyze", route_after_analysis)
    graph.add_edge("execute", "maybe_retry_plan")
    graph.add_edge("maybe_retry_plan", "feedback")
    graph.add_edge("feedback", END)

    return graph.compile(checkpointer=checkpointer)

def _extract_task_delay_seconds(task: ActionTask) -> int:
    """Infer wait duration (in seconds) from task metadata/prompt for scheduled delays."""
    extra_params = task.extra_params or {}
    raw_delay = extra_params.get("delay") if isinstance(extra_params, dict) else None
    if isinstance(raw_delay, (int, float)) and raw_delay > 0:
        return int(raw_delay)

    prompt = (task.ai_prompt or "").lower().strip()
    if not prompt:
        return 0

    match = re.search(r"wait\s+for\s+(\d+)\s*(second|seconds|minute|minutes|hour|hours)", prompt)
    if not match:
        return 0

    value = int(match.group(1))
    unit = match.group(2)
    if "hour" in unit:
        return value * 3600
    if "minute" in unit:
        return value * 60
    return value


async def execute_single_task(
    task: ActionTask,
    session_id: str,
    original_message_id: str,
    user_language: str = "en",
    output_language: str = "en",
    user_profile: Optional[Dict[str, Any]] = None,
    user_id: str = "default_user"
) -> TaskResult:
    """Execute a single task via action/reasoning layer or mobile strategy"""
    task_queue = get_session_task_queue(session_id)

    # Native scheduled-delay handling prevents long waits from timing out in the
    # execution sandbox (which currently has a 60s cap for action tasks).
    delay_seconds = _extract_task_delay_seconds(task)
    if delay_seconds > 0:
        logger.info(f"⏳ Coordinator native delay for {task.task_id}: {delay_seconds}s")
        remaining = delay_seconds
        while remaining > 0:
            if task_queue.is_stopped:
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error="Scheduled wait cancelled by user",
                )

            while task_queue.is_paused and not task_queue.is_stopped:
                await asyncio.sleep(0.25)
            if task_queue.is_stopped:
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error="Scheduled wait cancelled by user",
                )

            tick = min(1, remaining)
            await asyncio.sleep(tick)
            remaining -= tick

        return TaskResult(
            task_id=task.task_id,
            status="success",
            content=f"Delay completed after {delay_seconds} seconds",
            metadata={"scheduled_delay_seconds": delay_seconds, "handled_by": "coordinator_native_delay"},
        )
    
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
    
    # ── Destructive OS command gate ───────────────────────────────────────────
    # Blocks any action task whose prompt contains OS-level destructive commands
    # that should never be executed regardless of how the plan was generated.
    # This catches cases where the LLM decomposer produced a shutdown/delete plan
    # from ambiguous input that slipped past the intent classifier.
    _BLOCKED_TASK_PATTERNS = [
        r"shutdown\s*/[srph]",
        r"shutdown\s+/s",
        r"shutdown\s+now\b",
        r"shutdown\s+the\s+(?:computer|pc|desktop|system)",
        r"shut\s*down\s+(?:the\s+)?(?:computer|pc|desktop|system)",
        r"power\s*off\s+(?:the\s+)?(?:computer|pc|desktop|system)",
        r"turn\s+off\s+(?:the\s+)?(?:computer|pc|desktop|system)",
        r"(?:type|write|enter)\s+.*shutdown\s*/s",
        r"poweroff\b",
        r"rm\s+-rf\s+/",
        r"del\s+/f\s+/s\s+[Cc]:\\\\[Ww]indows",
        r"format\s+[Cc]:",
    ]
    if task.target_agent == "action":
        _prompt_lower = (task.ai_prompt or "").lower()
        _ep_lower = str(task.extra_params or "").lower()
        _combined = _prompt_lower + " " + _ep_lower
        for _pat in _BLOCKED_TASK_PATTERNS:
            if re.search(_pat, _combined, re.IGNORECASE):
                logger.error(
                    f"🚫 DESTRUCTIVE TASK BLOCKED: task={task.task_id}, "
                    f"matched pattern='{_pat}', prompt='{task.ai_prompt[:100]}'"
                )
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error=(
                        f"Task blocked by safety gate: destructive OS command detected "
                        f"in task prompt (pattern: {_pat}). "
                        f"This action requires explicit confirmation and cannot be executed "
                        f"from an automated plan."
                    ),
                )
    # ─────────────────────────────────────────────────────────────────────────

    # Route to appropriate agent
    if task.target_agent == "action":
        channel = Channels.COORDINATOR_TO_EXECUTION
        receiver = AgentType.EXECUTION
    elif task.target_agent == "language":
        channel = Channels.COORDINATOR_TO_LANGUAGE
        receiver = AgentType.LANGUAGE
    elif task.target_agent in {"email", "api"}:
        channel = Channels.COORDINATOR_TO_API
        receiver = AgentType.API
    else:
        channel = Channels.COORDINATOR_TO_REASONING
        receiver = AgentType.REASONING
    
    task_payload = task.model_dump()
    task_payload["user_id"] = user_id
    
    extra_params = task_payload.get("extra_params") or {}
    if not isinstance(extra_params, dict):
        extra_params = {}
    if user_id:
        extra_params["user_id"] = user_id
        task_payload["user_id"] = user_id
    task_payload["extra_params"] = extra_params

    # ── Mobile execution hint: whether the mobile execution agent should
    # return to the app screen after performing this action. This is a
    # heuristic flag (not a hardcoded rule) that downstream mobile
    # executors can use to decide navigation behavior.
    try:
        device_hint = str(getattr(task, "device", "") or "").strip().lower()
        if task.target_agent == "action" and device_hint == "mobile":
            # Respect explicit signal from decomposition if present
            if "return_to_app_after_exec" not in extra_params:
                prompt_lower = str(getattr(task, "ai_prompt", "") or "").lower()
                final_keywords = [
                    "click the send",
                    "click send",
                    "press send",
                    "send the email",
                    "send email",
                    "submit",
                    "save",
                    "confirm and send",
                ]
                is_final = any(k in prompt_lower for k in final_keywords)
                extra_params["return_to_app_after_exec"] = bool(is_final)
            task_payload["extra_params"] = extra_params
    except Exception:
        # Non-fatal - don't block task publishing on this heuristic
        pass

    if task.target_agent == "email":
        for key in ["operation", "to", "subject", "body", "attachments", "max_results", "query",
                     "search_query", "video_url", "title", "start_time", "end_time",
                     "description", "file_path", "parent_folder_id"]:
            if key not in task_payload and key in extra_params:
                task_payload[key] = extra_params[key]
        if "operation" not in task_payload:
            task_payload["operation"] = "send"

    if task.target_agent == "reasoning":
        task_payload["user_language"] = user_language
        task_payload["user_id"] = user_id
        # Pass output_language (may differ from user_language when user requests a different
        # language for the task output, e.g. Arabic user asking for an English summary)
        task_payload["output_language"] = output_language or user_language
        extra_params["language"] = output_language or user_language
        # Carry user_profile so Reasoning Agent can personalize its output style
        if user_profile:
            extra_params["user_profile"] = user_profile
        task_payload["extra_params"] = extra_params
        # Also set at top level for direct access
        task_payload["user_profile"] = user_profile or {}

    # Use CONFIRMATION_REQUEST for Language Agent to ask user, else EXECUTION_REQUEST
    msg_type = MessageType.CONFIRMATION_REQUEST if receiver == AgentType.LANGUAGE else MessageType.EXECUTION_REQUEST

    # Create message
    task_msg = AgentMessage(
        message_type=msg_type,
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
    
    # Wait for result.
    # Language confirmation tasks depend on human response and need a longer SLA.
    # Mobile action tasks can legitimately take longer than desktop UI actions,
    # so give them a larger coordinator-side budget as well.
    if task.target_agent == "language":
        wait_timeout = 180
    elif task.target_agent == "action" and str(getattr(task, "device", "")).strip().lower() == "mobile":
        task_timeout_hint = task.extra_params.get("timeout_seconds") if isinstance(task.extra_params, dict) else None
        if not isinstance(task_timeout_hint, (int, float)):
            task_timeout_hint = 0
        wait_timeout = max(180, int(task_timeout_hint) + 30)
    else:
        wait_timeout = 60
    try:
        result_payload = await asyncio.wait_for(future, timeout=wait_timeout)
        payload_status = result_payload.get("status", "failed")
        _interrupted = payload_status == "stopped" or bool(result_payload.get("metadata", {}).get("interrupted"))
        if payload_status not in {"success", "failed", "pending", "awaiting_confirmation"}:
            payload_status = "failed"

        content = result_payload.get("content")
        if not content:
            content = result_payload.get("details")

        # Fix Pydantic validation error: Convert dict content to JSON string
        if isinstance(content, dict):
            content = json.dumps(content, indent=2)

        result = TaskResult(
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

        # ── ICRL: Record attempt + reward for non-reasoning tasks ────────────
        # We only run ICRL on action tasks (not reasoning/language) because
        # those are the ones that fail due to UI automation errors and benefit
        # most from reward-guided retry strategies.
        if (
            ICRL_ENABLED
            and session_id
            and task.target_agent == "action"
            and payload_status in ("success", "failed")
            and not _interrupted
            and str(getattr(task, "device", "desktop")).strip().lower() != "mobile"
        ):       
            try:
                icrl_buffer = _get_icrl_buffer(
                    session_id, task.task_id, task.goal or task.ai_prompt
                )
                task_dict = task.model_dump()
                result_dict = result.model_dump()
                result_dict["status"] = payload_status  # ensure correct status

                reward = await compute_reward(
                    goal=task.goal or task.ai_prompt,
                    task_dict=task_dict,
                    result_dict=result_dict,
                )
                attempt_summary = summarize_task_attempt(task_dict, result_dict)
                failure_type = classify_failure_type(task_dict, result_dict)
                new_attempt = icrl_buffer.add(
                    attempt_summary=attempt_summary,
                    reward=reward,
                    result_snippet=str(content or "")[:200],
                )
                new_attempt.failure_type = failure_type
                logger.info(
                    f"📊 ICRL recorded: task={task.task_id}, "
                    f"reward={reward:.3f}, failure_type={failure_type}, "
                    f"buffer={icrl_buffer.summary()}"
                )
                logger.debug(
                    f"📋 ICRL buffer full state for task={task.task_id}:\n"
                    f"{icrl_buffer.dump()}"
                )
            except Exception as _icrl_err:
                logger.warning(f"⚠️ ICRL recording failed (non-fatal): {_icrl_err}")
        # ─────────────────────────────────────────────────────────────────────

        return result

    except asyncio.TimeoutError:
        logger.error(f"⏰ Task {task.task_id} timeout after {wait_timeout} seconds")
        # Record timeout as near-zero reward in ICRL buffer
        if (
            ICRL_ENABLED
            and session_id
            and task.target_agent == "action"
            and str(getattr(task, "device", "desktop")).strip().lower() != "mobile"
        ):
            try:
                icrl_buffer = _get_icrl_buffer(
                    session_id, task.task_id, task.goal or task.ai_prompt
                )
                icrl_buffer.add(
                    attempt_summary=f"Task: {task.ai_prompt[:100]} | Status: timeout",
                    reward=0.05,
                    result_snippet=f"Task timed out after {wait_timeout} seconds",
                )
            except Exception:
                pass
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error=f"Task timeout after {wait_timeout}s"
        )
    finally:
        pending_results.pop(task.task_id, None)


async def route_single_task(
    task: ActionTask,
    session_id: str,
    original_message_id: str,
    user_language: str = "en",
    output_language: str = "en",
    user_profile: Optional[Dict[str, Any]] = None,
    user_id: str = "default_user",
    source_device_id: str = "",
    forced_target_device_id: Optional[str] = None,
) -> TaskResult:
    """Route a single task to the best matching remote device and wait for its result."""
    mgr = get_cross_platform_manager()
    if not mgr:
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error="Cross-platform manager unavailable",
        )

    target_device = None
    target_device_id = forced_target_device_id or getattr(task, "target_device_id", None)
    if target_device_id:
        device_context = await mgr._registry.find_device_context(target_device_id)  # noqa: SLF001 - coordinator needs registry lookup
        if device_context and device_context.get("device"):
            target_device = device_context["device"]
    if not target_device:
        target_device = await mgr._registry.get_target_device(  # noqa: SLF001 - coordinator needs registry lookup
            user_id=user_id,
            target_platform=_normalize_device_type(getattr(task, "device", "") or None),
            exclude_device_id=source_device_id or None,
        )
    if not target_device:
        _pending_remote_tasks.pop(task.task_id, None)
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error="No matching target device available",
        )

    target_device_id = target_device.get("device_id")
    target_session_id = target_device.get("session_id")
    if not target_device_id or not target_session_id:
        _pending_remote_tasks.pop(task.task_id, None)
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error="Target device is missing an active session",
        )

    task_payload = task.model_dump()
    task_payload["user_id"] = user_id
    task_payload["source_device_id"] = source_device_id
    task_payload["source_session_id"] = session_id

    if task.target_agent == "reasoning":
        task_payload["user_language"] = user_language
        task_payload["output_language"] = output_language or user_language
        if user_profile:
            task_payload["user_profile"] = user_profile

    try:
        # Create a pending remote task in MongoDB (no HTTP callback)
        await mgr.create_remote_task(
            user_id=user_id,
            target_device_id=target_device_id,
            target_session_id=target_session_id,
            task=task,
            source_server_url="",
        )

        # Determine reasonable timeout based on agent type
        if task.target_agent == "language":
            wait_timeout = 180
        elif task.target_agent == "action" and str(getattr(task, "device", "")).strip().lower() == "mobile":
            task_timeout_hint = task.extra_params.get("timeout_seconds") if isinstance(task.extra_params, dict) else None
            if not isinstance(task_timeout_hint, (int, float)):
                task_timeout_hint = 0
            wait_timeout = max(180, int(task_timeout_hint) + 30)
        else:
            wait_timeout = 60

        # Poll MongoDB for completion
        from core.mongo import get_database

        db = get_database("aura_db")
        if db is None:
            return TaskResult(task_id=task.task_id, status="failed", error="MongoDB unavailable")
        col = db["cross_platform_tasks"]

        start = asyncio.get_event_loop().time()
        poll_interval = 1.0
        while True:
            doc = await asyncio.to_thread(col.find_one, {"task_id": task.task_id, "user_id": user_id})
            if doc:
                status = doc.get("status")
                if status in ("completed", "failed"):
                    result_data = doc.get("result", {}) or {}
                    payload_status = "success" if status == "completed" else "failed"
                    content = result_data.get("content") or result_data.get("details")
                    if isinstance(content, dict):
                        content = json.dumps(content, indent=2)
                    return TaskResult(
                        task_id=task.task_id,
                        status=payload_status,
                        content=content,
                        error=result_data.get("error"),
                        details=result_data.get("details"),
                        metadata=result_data.get("metadata") or {},
                        needs_clarification=bool(result_data.get("needs_clarification", False)),
                        clarification_question=result_data.get("clarification_question"),
                        clarification_type=result_data.get("clarification_type"),
                        recoverable=bool(result_data.get("recoverable", False)),
                    )
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= wait_timeout:
                logger.error(f"⏰ Routed task {task.task_id} timeout while polling for device result")
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error="Routed task timeout",
                )
            await asyncio.sleep(poll_interval)

    except Exception as e:
        logger.error(f"❌ Failed routing remote task {task.task_id}: {e}")
        return TaskResult(task_id=task.task_id, status="failed", error=str(e))

# Initialize graph
coordinator_graph = create_coordinator_graph()

# --- Broker Integration ---
async def start_coordinator_agent(broker_instance):
    """Start Coordinator Agent with broker"""
    
    async def handle_task_from_language(message: AgentMessage):
        """Handle task from Language Agent"""

        if message.message_type != MessageType.TASK_REQUEST:
            return

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
        incoming_text = (
            message.payload.get("original_input")
            or message.payload.get("confirmation")
            or message.payload.get("action")
            or ""
        )

        stop_event = _session_stop_events.get(session_id)
        if ICRL_ENABLED and stop_event and incoming_text:
            try:
                stopped_task_id = stop_event.get("task_id") or "stopped_plan"
                goal = stop_event.get("goal") or "Interrupted plan"
                icrl_buffer = _get_icrl_buffer(session_id, stopped_task_id, goal)
                attempt = icrl_buffer.add(
                    attempt_summary=f"Post-stop clarification: {incoming_text[:180]}",
                    reward=0.0,
                    result_snippet="User issued clarification after a stop; plan penalized.",
                )
                attempt.failure_type = "post_stop_clarification"
                logger.info(f"📉 ICRL penalty applied for post-stop clarification in session {session_id}")
            except Exception as icrl_penalty_err:
                logger.debug(f"Could not apply post-stop ICRL penalty: {icrl_penalty_err}")
            finally:
                _session_stop_events.pop(session_id, None)

        if was_queued:
            reference_text = _session_execution_context.get(session_id) or task_queue.current_task_id or ""
            is_relevant = is_relevant_to_task(incoming_text, str(reference_text), threshold=0.24)
            rel_score = relevance_score(incoming_text, str(reference_text))

            if not is_relevant:
                deferred_language_messages.append(message)
                logger.info(
                    f"🧾 Request deferred to global queue (relevance={rel_score:.2f}): {incoming_text[:80]}"
                )

                await broker_instance.publish(
                    Channels.COORDINATOR_TO_LANGUAGE,
                    AgentMessage(
                        message_type=MessageType.TASK_RESPONSE,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=http_request_id,
                        payload={
                            "status": "processing",
                            "response": (
                                "Added to your global task queue. I will finish the active workflow first."
                                if user_language != "ar"
                                else "تمت إضافة طلبك إلى قائمة الانتظار العامة. سأكمل المهمة الحالية أولاً."
                            ),
                            "user_language": user_language,
                        },
                    ),
                )
                await broker_instance.publish(
                    Channels.WEBSOCKET_OUTPUT,
                    AgentMessage(
                        message_type=MessageType.TASK_PROGRESS,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=http_request_id,
                        payload={
                            "ws_type": "task_progress",
                            "stage": "coordinator",
                            "phase": "queued_request",
                            "active": True,
                            "queue_snapshot": task_queue.snapshot(),
                            "deferred_requests": len(deferred_language_messages),
                        },
                    ),
                )
                return

            logger.info(
                f"🔁 Relevant in-flight input detected (relevance={rel_score:.2f}). Interrupting current execution to re-plan with extra context."
            )
            await broker_instance.publish(
                Channels.INTERRUPT_CONTROL,
                AgentMessage(
                    message_type=MessageType.INTERRUPT_COMMAND,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.COORDINATOR,
                    session_id=session_id,
                    payload={"command": "stop", "reason": "relevant_user_context", "user_id": user_id},
                ),
            )

            reference_text = _session_execution_context.get(session_id) or task_queue.current_task_id or ""
            is_relevant = is_relevant_to_task(incoming_text, str(reference_text), threshold=0.24)
            rel_score = relevance_score(incoming_text, str(reference_text))

            if not is_relevant:
                deferred_language_messages.append(message)
                logger.info(
                    f"🧾 Request deferred to global queue (relevance={rel_score:.2f}): {incoming_text[:80]}"
                )

                await broker_instance.publish(
                    Channels.COORDINATOR_TO_LANGUAGE,
                    AgentMessage(
                        message_type=MessageType.TASK_RESPONSE,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=http_request_id,
                        payload={
                            "status": "processing",
                            "response": (
                                "Added to your global task queue. I will finish the active workflow first."
                                if user_language != "ar"
                                else "تمت إضافة طلبك إلى قائمة الانتظار العامة. سأكمل المهمة الحالية أولاً."
                            ),
                            "user_language": user_language,
                        },
                    ),
                )
                await broker_instance.publish(
                    Channels.WEBSOCKET_OUTPUT,
                    AgentMessage(
                        message_type=MessageType.TASK_PROGRESS,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.LANGUAGE,
                        session_id=session_id,
                        response_to=http_request_id,
                        payload={
                            "ws_type": "task_progress",
                            "stage": "coordinator",
                            "phase": "queued_request",
                            "active": True,
                            "queue_snapshot": task_queue.snapshot(),
                            "deferred_requests": len(deferred_language_messages),
                        },
                    ),
                )
                return

            logger.info(
                f"🔁 Relevant in-flight input detected (relevance={rel_score:.2f}). Interrupting current execution to re-plan with extra context."
            )
            await broker_instance.publish(
                Channels.INTERRUPT_CONTROL,
                AgentMessage(
                    message_type=MessageType.INTERRUPT_COMMAND,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.COORDINATOR,
                    session_id=session_id,
                    payload={"command": "stop", "reason": "relevant_user_context", "user_id": user_id},
                ),
            )

            logger.info("📥 Another request is executing — waiting for coordinator lock")
            await ThinkingStepManager.update_step(
                session_id,
                "queued_request",
                http_request_id,
                language=user_language
            )

        # ── F3: Sanitise confirmation string before coordinator LLM sees it ──
        _raw_payload = dict(message.payload)
        if "confirmation" in _raw_payload:
            _raw_payload["confirmation"] = sanitize_confirmation_for_prompt(
                _raw_payload.get("confirmation", "")
            )
        # ─────────────────────────────────────────────────────────────────────

        state_input = {
            # Pass the user_id through to the state context
            "user_id": user_id,
            "input": _raw_payload,
            "session_id": session_id,
            "original_message_id": http_request_id,
            "user_id": user_id,
            "conversation_history": []
        }

        config = {
            "configurable": {
                "thread_id": session_id,
                "checkpoint_ns": "",   # required by MongoDBSaver
                "user_id": user_id,
            }
        }

        deferred_to_reprocess = None

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
            except asyncio.TimeoutError:
                # ── Memory Fix 5: Queue recovery on timeout (T-M4 DoS fix) ─────
                # V-SYS-01: The coordinator queue can deadlock if a task hangs,
                # blocking all subsequent requests. Reset the queue on timeout so
                # the server recovers without a manual restart.
                task_queue.stop()
                task_queue.reset()
                logger.warning(
                    "🔄 Memory Fix 5: Queue cleared after task timeout — "
                    "server remains operational"
                )
                raise
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
                        {
                            "$or": [
                                {"thread_id": session_id},
                                {"config.configurable.thread_id": session_id},
                            ]
                        }
                    )
                            mongo_client["yusr_db"]["langgraph_checkpoints"].delete_many(
                        {
                            "$or": [
                                {"thread_id": session_id},
                                {"config.configurable.thread_id": session_id},
                            ]
                        }
                    )
                            logger.info(f"🗑️ Cleared corrupt checkpoint for session {session_id}")
                    except Exception as _clear_err:
                        logger.warning(f"⚠️ Could not clear checkpoint: {_clear_err}")
    
                    #result = await coordinator_graph.ainvoke(state_input, None)
                    # Retry with a fresh config — passing None causes LangGraph
                    # to raise ValueError when a checkpointer is attached.
                    fresh_config = {
                        "configurable": {
                            "thread_id": f"{session_id}_fresh_{uuid.uuid4().hex[:8]}",
                            "checkpoint_ns": "",
                        }
                    }
                    result = await coordinator_graph.ainvoke(state_input, fresh_config)
                else:
                    raise

            logger.info(f"✅ Task processing complete: {result.get('status')}")
            if deferred_language_messages:
                deferred_to_reprocess = deferred_language_messages.popleft()

        if deferred_to_reprocess:
            async def _republish_deferred():
                await asyncio.sleep(0.05)
                await broker_instance.publish(Channels.LANGUAGE_TO_COORDINATOR, deferred_to_reprocess)

            asyncio.create_task(_republish_deferred())

  
    async def handle_action_result(message: AgentMessage):
        """
        Handle result from Action/Reasoning layer (ASYNC-SAFE VERSION)
        
        ✅ FIXES:
        - InvalidStateError from duplicate set_result() calls
        - Race conditions in future handling
        - Silent failures when results arrive out of order
        """
        if message.message_type != MessageType.EXECUTION_RESPONSE:
            return

        sender = str(getattr(message, "sender", "") or "").lower()
        if not sender or sender not in {"execution", "reasoning", "api"}:
            logger.warning(
                f"⚠️ Ignoring EXECUTION_RESPONSE from non-executor sender '{sender or 'unknown'}'"
            )
            return
            
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

    async def handle_language_execution_result(message: AgentMessage):
        """
        Handle EXECUTION_RESPONSE messages coming from the Language Agent
        (e.g. user confirmations). The Language Agent publishes these on
        Channels.LANGUAGE_TO_COORDINATOR so we must resolve the coordinator's
        pending_results futures here as well. This prevents coordinator-side
        task timeouts when confirmations arrive via the language layer.
        """
        if message.message_type != MessageType.EXECUTION_RESPONSE:
            return

        task_id = message.task_id
        result_status = message.payload.get('status', 'unknown')
        logger.info(f"📬 Language EXECUTION_RESPONSE for {task_id}: {result_status}")

        # If the coordinator is not expecting this result, warn and ignore.
        if task_id not in pending_results:
            logger.warning(f"⚠️ Received language execution result for unknown task {task_id} (may have timed out)")
            return

        future = pending_results[task_id]
        if future.done():
            logger.warning(f"⚠️ Task {task_id} already resolved - ignoring duplicate language result")
            return

        try:
            future.set_result(message.payload)
            logger.debug(f"✅ Successfully set language result for task {task_id}")
        except asyncio.InvalidStateError as e:
            logger.error(f"❌ InvalidStateError setting language result for {task_id}: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error setting language result for {task_id}: {e}")
    
    async def handle_interrupt_command(message: AgentMessage):
        """Handle pause/stop/resume commands with context snapshot support"""
        command = message.payload.get("command")
        task_queue = get_session_task_queue(message.session_id)

        async def _broadcast_execution_control(command_name: str, reason: str):
            task_ids = set(pending_results.keys())
            current_task_id = task_queue.current_task_id
            payload_task_id = message.payload.get("task_id")
            if current_task_id:
                task_ids.add(current_task_id)
            if payload_task_id:
                task_ids.add(payload_task_id)

            logger.info(f"📢 Broadcasting {command_name} signal to execution agent")

            if task_ids:
                logger.warning(f"🔪 Found {len(task_ids)} in-flight task(s), sending {command_name} signals...")
                for task_id in list(task_ids):
                    logger.warning(f"   → {command_name.title()} task {task_id}")

                    control_signal = AgentMessage(
                        message_type=MessageType.CONTROL,
                        sender=AgentType.COORDINATOR,
                        receiver=AgentType.EXECUTION,
                        session_id=message.session_id,
                        task_id=task_id,
                        payload={
                            "command": command_name,
                            "task_id": task_id,
                            "reason": reason,
                            "timestamp": datetime.now().isoformat()
                        }
                    )
                    await broker_instance.publish(Channels.EXECUTION_STOP_SIGNAL, control_signal)
            else:
                logger.info(f"   (No in-flight tasks to {command_name})")
        
        if command == "pause":
            task_queue.pause()
            await _broadcast_execution_control("pause", "User initiated pause")
        elif command == "resume":
            task_queue.resume()
        elif command == "stop":
            stopped_task_id = task_queue.current_task_id
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
            
            # Stop coordinator queues now (prevents any new dispatch).
            task_queue.stop()
            _session_stop_events[message.session_id] = {
                "task_id": stopped_task_id,
                "goal": _session_execution_context.get(message.session_id, "Interrupted plan"),
                "stopped_at": datetime.now().isoformat(),
            }

            # Resolve and clear all in-flight futures so execution flow halts quickly.
            for pending_task_id, future in list(pending_results.items()):
                if future.done():
                    continue
                try:
                    future.set_result(
                        {
                            "status": "stopped",
                            "error": "Execution stopped by user",
                            "metadata": {"interrupted": True, "command": "stop"},
                        }
                    )
                except Exception:
                    pass

            pending_results.clear()

            # Broadcast explicit coordinator phase end for desktop/mobile widget handling.
            await broker_instance.publish(
                Channels.WEBSOCKET_OUTPUT,
                AgentMessage(
                    message_type=MessageType.TASK_PROGRESS,
                    sender=AgentType.COORDINATOR,
                    receiver=AgentType.LANGUAGE,
                    session_id=message.session_id,
                    response_to=message.message_id,
                    payload={
                        "ws_type": "task_progress",
                        "stage": "coordinator",
                        "phase": "execution_stopped",
                        "active": False,
                        "queue_snapshot": task_queue.snapshot(),
                    },
                ),
            )
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
                # ICRL: Clear in-context RL buffers for this session
                _clear_icrl_buffers_for_session(session_id)
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
    broker_instance.subscribe(Channels.API_TO_COORDINATOR, handle_action_result)
    broker_instance.subscribe(Channels.REASONING_TO_COORDINATOR, handle_action_result)
    # Also handle execution responses originating from the Language Agent
    broker_instance.subscribe(Channels.LANGUAGE_TO_COORDINATOR, handle_language_execution_result)
    broker_instance.subscribe(Channels.INTERRUPT_CONTROL, handle_interrupt_command)
    broker_instance.subscribe(Channels.SESSION_CONTROL, handle_session_control)
    
    logger.info("✅ Coordinator Agent started with RAG action layer support")
    
    while True:
        await asyncio.sleep(1)