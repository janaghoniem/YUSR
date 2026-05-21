import os
import asyncio
import logging
import json
import re
from typing import Dict, Any, Optional, List, Union
from dotenv import load_dotenv

# Model & LangChain Imports
from langchain_groq import ChatGroq
import requests

# Project Utilities
from agents.utils.protocol import Channels, AgentMessage, MessageType, AgentType
from agents.utils.broker import broker
from utils.semantic_intent import infer_task_type, paginate_content

load_dotenv()
logger = logging.getLogger(__name__)

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
REASONING_MODEL = "llama-3.3-70b-versatile"
MISTRAL_REASONING_MODEL = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")

# Maximum characters for a single file content before we split it into chunks
MAX_FILE_CHUNK_SIZE = 12000


def _build_personalization_instruction(user_profile: Dict[str, Any], target_lang: str) -> str:
    """Derive a personalization instruction from the user profile."""
    if not user_profile:
        return ""

    profession = str(user_profile.get("profession", "")).lower()
    education = str(user_profile.get("education", "")).lower()
    tone_pref = str(user_profile.get("tone", "")).lower()

    lines = []

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

    if "formal" in tone_pref:
        lines.append("Maintain a formal, professional tone throughout.")
    elif "casual" in tone_pref or "friendly" in tone_pref:
        lines.append("Keep the tone friendly and conversational.")

    if not lines:
        return ""

    user_name = user_profile.get("username", "") or user_profile.get("name", "")
    if user_name:
        lines.append(f"The user's name is {user_name}. Always act on their behalf when generating content (e.g., sign off emails using their name).")

    block = "\n".join(lines)
    return f"\nPERSONALIZATION (adapt output accordingly):\n{block}\n"


class ReasoningAgent:
    def __init__(self):
        self.llm = ChatGroq(
            model=REASONING_MODEL,
            temperature=0.2,
            groq_api_key=GROQ_API_KEY
        ) if GROQ_API_KEY else None
        self.mistral_api_key = MISTRAL_API_KEY

        self.base_system_prompt = """You are the REASONING AGENT – the cognitive brain of the AURA multi-agent system.

PURPOSE:
You are responsible for all high-level intellectual processing inside AURA.
You transform raw or intermediate outputs from other agents into meaningful,
actionable, and human‑understandable results.

CORE RESPONSIBILITIES:
1. Text Summarization: Condense long or complex content into structured key points.
2. Information Extraction: Identify and structure specific data (e.g., names, dates, entities, links, values).
3. Text Generation: Generate coherent, context‑aware text based on instructions.
4. Code Generation: Produce clean, correct code snippets based on requirements.
5. Code Debugging: Analyze code, detect errors, and suggest precise fixes.
6. Explanation & Teaching: Explain documents, concepts, or outputs in clear, understandable language.
7. Decision Support: Recommend next steps based on previous results or failures.
8. Comparison & Evaluation: Compare outputs, approaches, or datasets logically and objectively.
9. Validation & Consistency Checking: Detect contradictions, missing elements, or logical flaws.
10. Email Composition: When asked to compose an email, produce a JSON object inside the "result" field:
    {
      "SUBJECT": "<subject line>",
      "BODY": "<full email body>"
    }
    (TO and FROM are handled separately by the action layer – do NOT include them.)
11. FILE / APPLICATION GENERATION (Website, App, Scripts, Configuration):
    When asked to "create a website", "build an app", "generate a complete project", or produce multiple files/code files:
    - Output a JSON object with the key "files". The value must be another object mapping file paths (including directories) to their complete content.
    - Example:
      {
        "result": {
          "files": {
            "index.html": "<!DOCTYPE html>...",
            "styles/main.css": "body { ... }",
            "scripts/app.js": "console.log('Hello');"
          }
        },
        "metadata": { "type": "multi_file_generation" }
      }
    - Paths may include subdirectories (e.g., "src/components/Header.js"). Use forward slashes.
    - Each file's content must be the full, final text that should be written to disk.
    - For very large files (e.g., > 12000 characters), **split the content into multiple parts** using the following format:
      {
        "files": {
          "very_large_file.txt": {
            "chunks": [
              {"part": 1, "content": "first 12k chars..."},
              {"part": 2, "content": "next 12k chars..."}
            ],
            "total_parts": 2
          }
        }
      }
      The execution layer will merge the chunks automatically. Use this only when necessary.
    - Do NOT embed binary data (images, fonts) – generate only text files (HTML, CSS, JS, JSON, config, source code, etc.).
    - If multiple files are required, include all of them in the same "files" object.

STRICT OPERATIONAL RULES:
- You NEVER interact with UI elements, browsers, mouse, keyboard, or operating system.
- You NEVER execute tools or external actions.
- You ONLY reason over the content and parameters provided by the Coordinator or Action Layer.
- If required data is missing or ambiguous, explicitly state what is needed.
- You MUST avoid hallucinating unknown information.
- You MUST return your output as a valid JSON object with this structure:
  - "result": your main output (string, plain object, or "files" object)
  - "metadata": an object with "confidence" (0.0-1.0), "notes" (optional), "assumptions" (optional)
- For file generation tasks, always set metadata["type"] = "multi_file_generation".

You are an internal reasoning component, not a user‑facing assistant.
Your goal is correctness, clarity, and usefulness to the system."""

    # ... existing helper methods (_extract_mistral_text, _extract_first_json_object, _strip_markdown, etc.) ...
    # (Keep all helper methods exactly as they were, unchanged for brevity, but we'll copy them below)
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
        """Best-effort extractor for malformed JSON email payloads."""
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
        if self.mistral_api_key:
            try:
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
                        logger.info(f"✅ Reasoning primary succeeded with Mistral ({MISTRAL_REASONING_MODEL})")
                        return text
                    logger.warning("⚠️ Mistral reasoning returned an empty response; trying Groq backup")
                else:
                    logger.warning(f"⚠️ Mistral reasoning request failed ({response.status_code}); trying Groq backup")
            except Exception as mistral_err:
                logger.warning(f"⚠️ Mistral reasoning call failed, trying Groq backup: {mistral_err}")
        else:
            logger.warning("⚠️ MISTRAL_API_KEY missing for reasoning agent; trying Groq backup")

        if not self.llm:
            logger.error("❌ Groq fallback unavailable: GROQ_API_KEY is not set")
            return ""

        try:
            response = await self.llm.ainvoke(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            if text:
                logger.info(f"✅ Reasoning fallback succeeded with Groq ({REASONING_MODEL})")
            return text
        except Exception as groq_err:
            logger.error(f"❌ Groq reasoning fallback failed: {groq_err}")
            return ""

    def _build_system_prompt(self, user_profile: Dict[str, Any], target_lang: str) -> str:
        personalization = _build_personalization_instruction(user_profile, target_lang)
        return f"{self.base_system_prompt}{personalization}"

    @staticmethod
    def _ensure_files_object(result_content: Any) -> Dict[str, Any]:
        """
        If the result contains a "files" key, validate its structure.
        If it's malformed, attempt to repair it.
        """
        if not isinstance(result_content, dict):
            return result_content
        if "files" not in result_content:
            return result_content

        files_dict = result_content["files"]
        if not isinstance(files_dict, dict):
            logger.warning("⚠️ 'files' value is not a dict, ignoring file generation")
            return result_content

        # Validate / repair each file entry
        repaired = {}
        for path, content in files_dict.items():
            # If content is a string, it's fine
            if isinstance(content, str):
                # Trim any unnecessary leading/trailing whitespace but keep indentation
                if not content.strip() and len(content) > 0:
                    logger.warning(f"⚠️ File {path} has only whitespace content")
                repaired[path] = content
            # If content is already a chunked structure, keep it (but ensure it has required fields)
            elif isinstance(content, dict) and "chunks" in content and isinstance(content["chunks"], list):
                # Ensure each chunk has "part" and "content"
                valid_chunks = []
                for i, chunk in enumerate(content["chunks"]):
                    if isinstance(chunk, dict) and "content" in chunk:
                        chunk_part = chunk.get("part", i+1)
                        valid_chunks.append({"part": chunk_part, "content": chunk.get("content", "")})
                    else:
                        logger.warning(f"⚠️ Skipping invalid chunk in {path}")
                if valid_chunks:
                    repaired[path] = {
                        "chunks": valid_chunks,
                        "total_parts": content.get("total_parts", len(valid_chunks))
                    }
                else:
                    logger.warning(f"⚠️ No valid chunks for {path}, dropping file")
            else:
                # Unexpected type – try to convert to string
                logger.warning(f"⚠️ Unexpected content type for {path}: {type(content)}. Converting to string.")
                repaired[path] = str(content)

        if not repaired:
            logger.warning("⚠️ No valid files remain after validation, removing 'files' key")
            del result_content["files"]
        else:
            result_content["files"] = repaired
        return result_content

    @staticmethod
    def _chunk_large_files(result_content: Dict[str, Any], max_chunk_size: int = MAX_FILE_CHUNK_SIZE) -> Dict[str, Any]:
        """
        If any file content (string) exceeds max_chunk_size, split it into chunks
        and replace the string with a chunked structure.
        """
        if not isinstance(result_content, dict) or "files" not in result_content:
            return result_content

        files_dict = result_content["files"]
        new_files = {}
        for path, content in files_dict.items():
            if isinstance(content, str) and len(content) > max_chunk_size:
                logger.info(f"✂️ Splitting large file {path} ({len(content)} chars) into chunks")
                chunks = []
                total_parts = (len(content) + max_chunk_size - 1) // max_chunk_size
                for i in range(total_parts):
                    start = i * max_chunk_size
                    end = start + max_chunk_size
                    chunk_text = content[start:end]
                    chunks.append({"part": i+1, "content": chunk_text})
                new_files[path] = {
                    "chunks": chunks,
                    "total_parts": total_parts
                }
            else:
                new_files[path] = content
        result_content["files"] = new_files
        return result_content

    async def process_reasoning_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the logic requested by the Coordinator.
        Personalization is derived from `user_profile` in the payload.
        """
        ai_prompt = task_payload.get("ai_prompt", "Process the following content.")
        content = task_payload.get("content", "")
        extra_params = task_payload.get("extra_params", {})

        # Language resolution
        explicit_lang = str(
            task_payload.get("output_language")
            or task_payload.get("user_language")
            or extra_params.get("language")
            or "en"
        ).lower().strip()
        target_lang = "ar" if explicit_lang.startswith("ar") else "en"
        logger.info(f"🌐 Reasoning Agent target language: {target_lang}")

        user_profile: Dict[str, Any] = task_payload.get("user_profile") or extra_params.get("user_profile") or {}

        # Inject input_content if present
        if "input_content" in extra_params:
            content = extra_params["input_content"]
            logger.info(f"📥 Using input_content from extra_params ({len(content)} chars)")

        # Clean success/failure markers
        if content:
            content = content.replace("EXECUTION_SUCCESS", "").replace("FAILED:", "").strip()
            logger.info(f"🧹 Cleaned content: {len(content)} chars")

        try:
            logger.info(f"🧠 Reasoning Agent processing task: {ai_prompt[:50]}...")
            if content:
                logger.info(f"📊 Content preview: {content[:200]}...")

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

            # Clean the response (strip fences, extract JSON)
            clean_response = response_text.strip()
            if clean_response.startswith("```"):
                lines = clean_response.split("\n")
                inner_lines = lines[1:]
                if inner_lines and inner_lines[-1].strip() == "```":
                    inner_lines = inner_lines[:-1]
                clean_response = "\n".join(inner_lines).strip()

            extracted_json = self._extract_first_json_object(clean_response)
            if extracted_json:
                clean_response = extracted_json

            # Parse JSON
            try:
                parsed_response = json.loads(clean_response)
                parsed_response = self._sanitize_result_content(parsed_response)
                result_content = parsed_response.get("result", parsed_response)

                # --- NEW: Handle multi‑file generation gracefully ---
                # First, if the result is a dict and contains a "files" key, validate and chunk it
                if isinstance(result_content, dict) and "files" in result_content:
                    result_content = self._ensure_files_object(result_content)
                    result_content = self._chunk_large_files(result_content, MAX_FILE_CHUNK_SIZE)
                    # Keep the structured object for the coordinator; do NOT stringify it.
                    # The coordinator will convert it to JSON string when needed.
                    result_content_structured = result_content
                    # For pagination, we create a JSON string representation
                    result_content_str = json.dumps(result_content, ensure_ascii=False)
                else:
                    # Normal text or other structured output
                    if isinstance(result_content, (dict, list)):
                        result_content_str = json.dumps(result_content, ensure_ascii=False)
                    elif not isinstance(result_content, str):
                        result_content_str = str(result_content)
                    else:
                        result_content_str = result_content
                    result_content_structured = result_content_str

                task_type = infer_task_type(ai_prompt, extra_params.get("format") or extra_params.get("target_format"))
                page_size = int(extra_params.get("page_size") or 2000)

                # If this is a multi-file generation, we paginate the JSON string representation
                # but also keep the structured files metadata for downstream merging.
                paginated_content = paginate_content(
                    result_content_str,
                    task_type=task_type,
                    page_size=page_size
                )

                metadata = parsed_response.get("metadata", {}) or {}
                metadata["paginated_content"] = paginated_content
                metadata["task_type"] = task_type
                if isinstance(result_content, dict) and "files" in result_content:
                    metadata["generation_type"] = "multi_file"
                    metadata["total_files"] = len(result_content["files"])
                    metadata["chunking_applied"] = any(
                        isinstance(v, dict) and "chunks" in v for v in result_content["files"].values()
                    )

                logger.info(f"✅ Reasoning complete: {result_content_str[:200]}...")
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "success",
                    "content": result_content_str,   # JSON string (coordinator will parse if needed)
                    "metadata": metadata,
                }

            except json.JSONDecodeError:
                # Last resort: try to recover email or file generation from malformed JSON
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

                # Try to detect if response looks like a raw files dict (e.g., "files": {...}) without outer wrapper
                maybe_files_match = re.search(r'"files"\s*:\s*\{[^}]*\}', clean_response, re.DOTALL)
                if maybe_files_match:
                    try:
                        # Attempt to wrap it into a proper JSON object
                        wrapped = "{" + maybe_files_match.group(0) + "}"
                        parsed_wrapped = json.loads(wrapped)
                        if "files" in parsed_wrapped:
                            result_content = self._ensure_files_object(parsed_wrapped)
                            result_content = self._chunk_large_files(result_content, MAX_FILE_CHUNK_SIZE)
                            result_content_str = json.dumps(result_content, ensure_ascii=False)
                            logger.warning("✅ Recovered file generation output from unescaped JSON")
                            return {
                                "task_id": task_payload.get("task_id"),
                                "status": "success",
                                "content": result_content_str,
                                "metadata": {"recovered": True, "type": "multi_file"},
                            }
                    except Exception:
                        pass

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
    logger.info(f"✅ Reasoning Agent started (Mistral primary: {MISTRAL_REASONING_MODEL}, Groq backup: {REASONING_MODEL})")
    control_state = {"paused": False, "stopped": False}

    async def handle_reasoning_request(message: AgentMessage):
        if control_state["stopped"]:
            logger.warning("⏹️ Reasoning request ignored due to stop command")
            response_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.REASONING,
                receiver=AgentType.COORDINATOR,
                session_id=message.session_id,
                task_id=message.task_id,
                response_to=message.message_id,
                payload={
                    "task_id": message.task_id,
                    "status": "failed",
                    "error": "Reasoning execution stopped by user",
                    "content": "",
                    "metadata": {"interrupted": True, "command": "stop"},
                }
            )
            await broker.publish(Channels.REASONING_TO_COORDINATOR, response_msg)
            return

        while control_state["paused"] and not control_state["stopped"]:
            await asyncio.sleep(0.2)

        task_id = message.task_id
        payload = message.payload

        result = await agent.process_reasoning_task(payload)
        result["task_id"] = task_id

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
        logger.info(f"Content preview: {result.get('content', '')[:200]}")

    async def handle_interrupt(message: AgentMessage):
        if message.message_type != MessageType.INTERRUPT_COMMAND:
            return
        command = (message.payload or {}).get("command", "").strip().lower()
        if command == "pause":
            control_state["paused"] = True
        elif command == "resume":
            control_state["paused"] = False
            control_state["stopped"] = False
        elif command == "stop":
            control_state["paused"] = False
            control_state["stopped"] = True
        logger.info(f"🛰️ Reasoning interrupt state updated: {control_state}")

    broker.subscribe(Channels.COORDINATOR_TO_REASONING, handle_reasoning_request)
    broker.subscribe(Channels.INTERRUPT_CONTROL, handle_interrupt)

    while True:
        await asyncio.sleep(1)