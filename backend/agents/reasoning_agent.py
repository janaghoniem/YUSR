import os
import asyncio
import logging
import json
import re
from typing import Dict, Any
from dotenv import load_dotenv

# Model & LangChain Imports
from langchain_groq import ChatGroq
import requests

# Project Utilities
from agents.utils.protocol import Channels, AgentMessage, MessageType, AgentType
from agents.utils.broker import broker

load_dotenv()
logger = logging.getLogger(__name__)

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
REASONING_MODEL = "llama-3.3-70b-versatile"
MISTRAL_REASONING_MODEL = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")


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

    user_name = user_profile.get("username", "") or user_profile.get("name", "")
    if user_name:
        lines.append(f"The user's name is {user_name}. Always act on their behalf when generating content (e.g. sign off emails using their name).")

    block = "\n".join(lines)
    return f"\nPERSONALIZATION (adapt output accordingly):\n{block}\n"


class ReasoningAgent:
    def __init__(self):
        self.llm = ChatGroq(
            model=REASONING_MODEL,
            temperature=0.2,
            groq_api_key=GROQ_API_KEY
        ) if GROQ_API_KEY else None
        self.mistral_api_key = MISTRAL_API_KEY  # Store API key for REST calls

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

    @staticmethod
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

    @staticmethod
    def _extract_first_json_object(text: str) -> str:
        """Extract the first balanced JSON object substring from text."""
        if not text:
            return ""
        start = text.find("{")
        if start == -1:
            return ""

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
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return ""

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove chat-style markdown artifacts so downstream agents receive plain text."""
        if not text:
            return ""
        cleaned = str(text)
        cleaned = re.sub(r"```[a-zA-Z]*", "", cleaned)
        cleaned = cleaned.replace("```", "")
        cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"__(.*?)__", r"\1", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
        cleaned = re.sub(r"^[ \t]*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.replace("*", "")
        cleaned = cleaned.replace("_", "")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _sanitize_result_content(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: cls._sanitize_result_content(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._sanitize_result_content(v) for v in value]
        if isinstance(value, str):
            return cls._strip_markdown(value)
        return value

    @staticmethod
    def _extract_email_field(text: str, field: str) -> str:
        """Best-effort extractor for malformed JSON email payloads (handles triple-quoted BODY)."""
        if not text:
            return ""
        pattern = rf'"{field}"\s*:\s*("""|"|\')(.*?)\1'
        match = re.search(pattern, text, flags=re.DOTALL)
        if not match:
            return ""
        return match.group(2).strip()

    @classmethod
    def _build_email_fallback_json(cls, raw: str) -> Dict[str, Any]:
        subject = cls._extract_email_field(raw, "SUBJECT")
        body = cls._extract_email_field(raw, "BODY")
        if not subject and not body:
            return {}
        return {
            "result": {
                "SUBJECT": cls._strip_markdown(subject),
                "BODY": cls._strip_markdown(body),
            },
            "metadata": {
                "confidence": 0.6,
                "notes": "Recovered structured email content from malformed model output",
            },
        }

    async def _invoke_with_fallback(self, prompt: str) -> str:
        if self.llm:
            try:
                response = await self.llm.ainvoke(prompt)
                return response.content if hasattr(response, "content") else str(response)
            except Exception as groq_err:
                logger.warning(f"⚠️ Groq reasoning call failed, trying Mistral fallback: {groq_err}")
        else:
            logger.warning("⚠️ GROQ_API_KEY missing for reasoning agent; trying Mistral fallback")

        if not self.mistral_api_key:
            logger.error("❌ Mistral fallback unavailable: MISTRAL_API_KEY is not set")
            return ""

        try:
            # Use REST API directly instead of client library
            url = "https://api.mistral.ai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.mistral_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": MISTRAL_REASONING_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
            
            response = await asyncio.to_thread(
                lambda: requests.post(url, json=payload, headers=headers, timeout=30)
            )
            
            if response.status_code == 200:
                result = response.json()
                text = result['choices'][0]['message']['content']
                if text:
                    logger.info(f"✅ Reasoning fallback succeeded with Mistral ({MISTRAL_REASONING_MODEL})")
                return text
            else:
                logger.error(f"❌ Mistral API error: {response.status_code} - {response.text}")
                return ""
        except Exception as mistral_err:
            logger.error(f"❌ Mistral reasoning fallback failed: {mistral_err}")
            return ""

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

            response_text = await self._invoke_with_fallback(full_prompt)
            if not response_text:
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "failed",
                    "error": "Both Groq and Mistral reasoning calls failed",
                    "content": "",
                }
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

            # Keep only the first JSON object if any prose wrappers leaked in.
            extracted_json = self._extract_first_json_object(clean_response)
            if extracted_json:
                clean_response = extracted_json

            # Fix literal newlines/tabs inside JSON string values
            # (Mistral often returns bare newlines in strings, which is invalid JSON)
            def _fix_newlines_in_json_strings(s: str) -> str:
                """Escape bare newlines/tabs that appear inside JSON string values."""
                out = []
                in_str = False
                esc = False
                for ch in s:
                    if esc:
                        out.append(ch)
                        esc = False
                        continue
                    if ch == '\\' and in_str:
                        out.append(ch)
                        esc = True
                        continue
                    if ch == '"':
                        in_str = not in_str
                        out.append(ch)
                        continue
                    if in_str:
                        if ch == '\n':
                            out.append('\\n')
                            continue
                        if ch == '\r':
                            out.append('\\r')
                            continue
                        if ch == '\t':
                            out.append('\\t')
                            continue
                    out.append(ch)
                return ''.join(out)

            clean_response = _fix_newlines_in_json_strings(clean_response)

            # Strip JavaScript-style // comments outside of JSON string values
            # (ministral-3b often adds "// Hypothetical placeholder" after values)
            def _strip_js_comments(s: str) -> str:
                out = []
                i = 0
                in_str = False
                esc = False
                while i < len(s):
                    ch = s[i]
                    if esc:
                        out.append(ch)
                        esc = False
                        i += 1
                        continue
                    if ch == '\\' and in_str:
                        out.append(ch)
                        esc = True
                        i += 1
                        continue
                    if ch == '"':
                        in_str = not in_str
                        out.append(ch)
                        i += 1
                        continue
                    if not in_str and ch == '/' and i + 1 < len(s) and s[i + 1] == '/':
                        # Skip until end of line
                        while i < len(s) and s[i] != '\n':
                            i += 1
                        continue
                    out.append(ch)
                    i += 1
                return ''.join(out)

            clean_response = _strip_js_comments(clean_response)

            # Parse JSON response
            try:
                parsed_response = json.loads(clean_response)
                parsed_response = self._sanitize_result_content(parsed_response)
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
                # Last resort: recover common malformed email JSON payloads from fallback model output.
                recovered = self._build_email_fallback_json(clean_response)
                if recovered:
                    logger.warning("⚠️ Recovered malformed reasoning JSON using email field extractor")
                    recovered = self._sanitize_result_content(recovered)
                    return {
                        "task_id": task_payload.get("task_id"),
                        "status": "success",
                        "content": json.dumps(recovered.get("result", {}), ensure_ascii=False),
                        "metadata": recovered.get("metadata", {}),
                    }

                logger.warning("⚠️ Response was not valid JSON, returning failure")
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "failed",
                    "error": "Reasoning response did not contain valid JSON",
                    "content": clean_response,
                    "metadata": {"notes": "Failed to parse reasoning output"}
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
    logger.info(f"✅ Reasoning Agent started (Groq primary: {REASONING_MODEL}, Mistral fallback: {MISTRAL_REASONING_MODEL})")

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