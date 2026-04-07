import os
import asyncio
import logging
import json
from typing import Dict, Any
from dotenv import load_dotenv

# Groq & LangChain Imports
from langchain_groq import ChatGroq

# Project Utilities
from agents.utils.protocol import Channels, AgentMessage, MessageType, AgentType
from agents.utils.broker import broker

load_dotenv()
logger = logging.getLogger(__name__)

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REASONING_MODEL = "llama-3.3-70b-versatile"


def _build_personalization_instruction(user_profile: Dict[str, Any], target_lang: str) -> str:
    """
    Derive a personalization instruction from the user profile.
    This is injected into every reasoning prompt so outputs adapt to
    the user's background without hard-coded if-else logic.
    """
    if not user_profile:
        return ""

    profession = str(user_profile.get("profession", "")).lower()
    education = str(user_profile.get("education", "")).lower()
    tone_pref = str(user_profile.get("tone", "")).lower()

    lines = []

    # Profession / education driven tone
    if "student" in profession or "student" in education:
        lines.append(
            "The user is a student. Use an academic, structured tone. "
            "Explain concepts clearly with examples where helpful."
        )
    elif any(k in profession for k in ["engineer", "developer", "analyst"]):
        lines.append(
            "The user is a technical professional. Be concise and precise. "
            "Use domain terminology freely. Omit basic explanations."
        )
    elif any(k in profession for k in ["doctor", "pharmacist", "nurse", "medical"]):
        lines.append(
            "The user works in healthcare. Use accurate clinical language "
            "and be concise. Avoid oversimplification."
        )
    elif any(k in profession for k in ["manager", "executive", "director", "ceo"]):
        lines.append(
            "The user is a business professional. Provide actionable, "
            "high-level summaries. Prioritize key decisions and outcomes."
        )
    elif any(k in profession for k in ["teacher", "professor", "educator"]):
        lines.append(
            "The user is an educator. Be structured and thorough. "
            "Present information in a way that is easy to explain to others."
        )
    else:
        lines.append(
            "Use clear, simplified language. Avoid jargon. "
            "Prioritize readability and accessibility."
        )

    # Explicit tone override from profile
    if "formal" in tone_pref:
        lines.append("Maintain a formal, professional tone throughout.")
    elif "casual" in tone_pref or "friendly" in tone_pref:
        lines.append("Keep the tone friendly and conversational.")

    if not lines:
        return ""

    block = "\n".join(lines)
    return f"\nPERSONALIZATION (adapt output accordingly):\n{block}\n"


class ReasoningAgent:
    def __init__(self):
        self.llm = ChatGroq(
            model=REASONING_MODEL,
            temperature=0.2,
            groq_api_key=GROQ_API_KEY
        )

        self.base_system_prompt = """You are the REASONING AGENT – the cognitive brain of the AURA multi-agent system.

PURPOSE:
You are responsible for all high-level intellectual processing inside AURA.
You transform raw or intermediate outputs from other agents into meaningful,
actionable, and human-understandable results.

CORE RESPONSIBILITIES:
1. Text Summarization: Condense long or complex content into structured key points.
2. Information Extraction: Identify and structure specific data (e.g., names, dates, entities, links, values).
3. Text Generation: Generate coherent, context-aware text based on instructions.
4. Code Generation: Produce clean, correct code snippets based on requirements.
5. Code Debugging: Analyze code, detect errors, and suggest precise fixes.
6. Explanation & Teaching: Explain documents, concepts, or outputs in clear, understandable language.
7. Decision Support: Recommend next steps based on previous results or failures.
8. Comparison & Evaluation: Compare outputs, approaches, or datasets logically and objectively.
9. Validation & Consistency Checking: Detect contradictions, missing elements, or logical flaws.
10. Email Composition: When asked to compose an email, ALWAYS produce a JSON object
    inside the "result" field containing ONLY the fields the system will type into the UI.
    The TO and FROM fields are handled separately by the action layer — do NOT include them.
    Required format:
    {{
      "SUBJECT": "<subject line>",
      "BODY": "<full email body>",
    }} 
    Rules:
    - SUBJECT must be concise and specific to the request.
    - BODY must be a complete, professionally written email body (greeting + content + sign-off).
    - Never return a plain-text block, never include TO or FROM, never return only the body.
    - The outer JSON wrapper still applies: {"result": {...}, "metadata": {...}}
    

STRICT OPERATIONAL RULES:
- You NEVER interact with UI elements, browsers, mouse, keyboard, or operating system.
- You NEVER execute tools or external actions.
- You ONLY reason over the content and parameters provided by the Coordinator or Action Layer.
- If required data is missing or ambiguous, explicitly state what is needed.
- You MUST avoid hallucinating unknown information.
- You MUST return your output as a valid JSON object with this structure:
  - "result": your main output
  - "metadata": an object with "confidence" (0.0-1.0), "notes" (optional), "assumptions" (optional)

You are an internal reasoning component, not a user-facing assistant.
Your goal is correctness, clarity, and usefulness to the system."""

    def _build_system_prompt(self, user_profile: Dict[str, Any], target_lang: str) -> str:
        """Combine base system prompt with dynamic personalization instruction."""
        personalization = _build_personalization_instruction(user_profile, target_lang)
        return f"{self.base_system_prompt}{personalization}"

    async def process_reasoning_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the logic requested by the Coordinator.
        Personalization is derived from `user_profile` in the payload.
        """
        ai_prompt = task_payload.get("ai_prompt", "Process the following content.")
        content = task_payload.get("content", "")
        extra_params = task_payload.get("extra_params", {})

        # ── Language resolution ───────────────────────────────────────────────
        # output_language is set by the Language Agent and reflects what the user
        # actually spoke/typed — it is the authoritative source.
        # extra_params.language is derived from user_language which can be stale
        # from a prior session, so it has LOWER priority than output_language.
        #
        # Priority chain (high → low):
        #   1. output_language  — set by Language Agent from live input detection
        #   2. user_language    — set by server from live input detection  
        #   3. extra_params.language — may be stale, used only as last resort
        explicit_lang = str(
            task_payload.get("output_language")
            or task_payload.get("user_language")
            or extra_params.get("language")
            or "en"
        ).lower().strip()
        target_lang = "ar" if explicit_lang.startswith("ar") else "en"
        logger.info(f"🌐 Reasoning Agent target language: {target_lang} "
                    f"(from output_language={task_payload.get('output_language')}, "
                    f"user_language={task_payload.get('user_language')})")

        # ── User profile for personalization ─────────────────────────────────
        user_profile: Dict[str, Any] = task_payload.get("user_profile") or extra_params.get("user_profile") or {}

        # ── FIX 1: Extract input_content from multiple sources ────────────────
        if "input_content" in extra_params:
            content = extra_params["input_content"]
            logger.info(f"📥 Using input_content from extra_params ({len(content)} chars)")

        # ── FIX 2: Clean success/failure markers from content ─────────────────
        if content:
            content = content.replace("EXECUTION_SUCCESS", "").replace("FAILED:", "").strip()
            logger.info(f"🧹 Cleaned content: {len(content)} chars")

        # ── FIX 3: Validate content exists ────────────────────────────────────
        if not content or content == "":
            data_keywords = ["summarize", "analyze", "analyse", "review", "translate",
                             "extract", "check", "fix", "debug", "explain this", "describe this"]
            needs_data = any(kw in ai_prompt.lower() for kw in data_keywords)
            if needs_data:
                logger.warning(f"⚠️ Data-processing task with no content — attempting with prompt only")
            else:
                logger.info(f"📝 Standalone generation task — no input data required")

        try:
            logger.info(f"🧠 Reasoning Agent processing task: {ai_prompt[:50]}...")
            if content:
                logger.info(f"📊 Content preview: {content[:200]}...")

            # Build dynamic system prompt with personalization
            system_prompt = self._build_system_prompt(user_profile, target_lang)

            lang_instruction = (
                f"\nOUTPUT LANGUAGE REQUIREMENT: Return the `result` field strictly in "
                f"{'Arabic' if target_lang == 'ar' else 'English'}.\n"
            )

            if content:
                full_prompt = (
                    f"{system_prompt}"
                    f"{lang_instruction}"
                    f"\nTASK: {ai_prompt}"
                    f"\n\nDATA TO PROCESS:\n{content}"
                    f"\n\nEXTRA PARAMETERS: {json.dumps(extra_params)}"
                    f"\n\nPlease respond with valid JSON only."
                )
            else:
                full_prompt = (
                    f"{system_prompt}"
                    f"{lang_instruction}"
                    f"\nTASK: {ai_prompt}"
                    f"\n\nEXTRA PARAMETERS: {json.dumps(extra_params)}"
                    f"\n\nPlease respond with valid JSON only."
                )

            response = await self.llm.ainvoke(full_prompt)
            response_text = response.content if hasattr(response, 'content') else str(response)
            logger.info(f"🤖 REASONING RESPONSE ({len(response_text)} chars): {response_text[:200]}...")

            # ── Strip markdown code fences before parsing ──────────────────────────
            # The LLM sometimes wraps JSON in ```json ... ``` fences.
            # If the raw fenced block is stored and later injected into an
            # execution task's prompt as a string literal, the subprocess
            # sandbox will fail to write the .py file on Windows (charmap
            # codec can't encode Arabic characters embedded in the literal).
            clean_response = response_text.strip()
            if clean_response.startswith("```"):
                # Strip leading fence line (e.g. ```json or just ```)
                lines = clean_response.split("\n")
                # Drop first line (the opening fence) and any trailing ``` line
                inner_lines = lines[1:]
                if inner_lines and inner_lines[-1].strip() == "```":
                    inner_lines = inner_lines[:-1]
                clean_response = "\n".join(inner_lines).strip()

            # Parse JSON response
            try:
                parsed_response = json.loads(clean_response)
                result_content = parsed_response.get("result", parsed_response)
                if isinstance(result_content, (dict, list)):
                    result_content = json.dumps(result_content, ensure_ascii=False)
                elif not isinstance(result_content, str):
                    result_content = str(result_content)
                    
                logger.info(f"✅ Reasoning complete: {result_content[:200]}...")
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "success",
                    "content": result_content,
                    "metadata": parsed_response.get("metadata", {})
                }
            except json.JSONDecodeError:
                # Last resort: if still not parseable, return the cleaned text directly.
                # This avoids injecting raw fenced JSON blocks into downstream tasks.
                logger.warning("⚠️ Response was not valid JSON, using cleaned raw text")
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "success",
                    "content": clean_response,
                    "metadata": {"notes": "Response was not in JSON format"}
                }

        except Exception as e:
            logger.error(f"❌ Reasoning Agent Error: {e}", exc_info=True)
            return {
                "task_id": task_payload.get("task_id"),
                "status": "failed",
                "error": str(e),
                "content": ""
            }


async def start_reasoning_agent():
    agent = ReasoningAgent()
    logger.info(f"✅ Reasoning Agent (Groq) started using {REASONING_MODEL}")

    async def handle_reasoning_request(message: AgentMessage):
        """
        Callback for when the Coordinator sends a task to the Reasoning channel.
        """
        task_id = message.task_id
        payload = message.payload

        # Process the logic
        result = await agent.process_reasoning_task(payload)

        # Add task_id to result payload
        result["task_id"] = task_id

        # Send response back to Coordinator
        response_msg = AgentMessage(
            message_type=MessageType.EXECUTION_RESPONSE,
            sender=AgentType.REASONING,
            receiver=AgentType.COORDINATOR,
            session_id=message.session_id,
            task_id=task_id,
            response_to=message.message_id,
            payload=result
        )

        await broker.publish(Channels.REASONING_TO_COORDINATOR, response_msg)
        logger.info(f"📤 Sent reasoning result for task {task_id}")
        logger.info(f"Content: {result}")

    # Subscribe to the reasoning channel
    broker.subscribe(Channels.COORDINATOR_TO_REASONING, handle_reasoning_request)

    while True:
        await asyncio.sleep(1)