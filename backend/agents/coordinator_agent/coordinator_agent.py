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

# DEVICE & CONTEXT

- **device**: "mobile"
- **context**:
    - "local" -> for all mobile tasks, including web browsing on mobile.

Mobile examples below are the only examples in this SOP.

