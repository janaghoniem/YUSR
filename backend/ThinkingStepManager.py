"""
Thinking Step Manager - Tracks and broadcasts thinking progress
"""
import asyncio
import logging
from typing import Optional
from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels
from agents.utils.broker import broker

logger = logging.getLogger(__name__)

class ThinkingStepManager:
    """Manages thinking step updates across agents"""

    # ── Thinking step copy ────────────────────────────────────────────────────
    # Design principles:
    #   • Maximum 4 steps shown per request — users stop reading after that
    #   • Each step is one short sentence, first-person active voice
    #   • American English: casual, direct, no corporate jargon
    #   • Egyptian Arabic (Masri): colloquial egyptian dialect — NOT formal MSA
    #     e.g. "بفكر" not "جاري التفكير", "هاعمل" not "سيتم تنفيذ"
    #   • Steps map to real milestones only — no fake "received your request" noise
    THINKING_STEPS = {
        "en": {
            # Shown at the very start — one line only
            "processing_input":        "On it...",
            # When the LLM is classifying the request
            "analyzing_request":       "Thinking about what you need...",
            # When fetching user preferences/memory
            "checking_preferences":    "Checking what I know about you...",
            # When the request is being processed by the LLM
            "processing_request":      "Working on it...",
            # When handing off to the Coordinator
            "preparing_for_coordinator": "Planning the steps...",
            # When decomposing into sub-tasks
            "preparing_tasks":         "Breaking it down...",
            # Coordinator received — shown briefly
            "received_request":        "Got it, starting now...",
            # When blocked behind another task
            "queued_request":          "Finishing up something else first...",
            # When building the execution plan
            "creating_execution_plan": "Figuring out how to do this...",
            # Document-specific steps
            "analyzing_document":      "Reading through the document...",
            "generating_summary":      "Putting together the summary...",
            # Generic execution
            "executing_task":          "Doing it now...",
            "finalizing":              "Almost done...",
        },
        "ar": {
            # Egyptian Arabic — colloquial, warm, first-person
            "processing_input":        "حاضر...",
            "analyzing_request":       "بفكر في طلبك...",
            "checking_preferences":    "بشوف اللي بعرفه عنك...",
            "processing_request":      "شغال عليه...",
            "preparing_for_coordinator": "بخطط الخطوات...",
            "preparing_tasks":         "بقسّمها...",
            "received_request":        "تمام، بابدأ دلوقتي...",
            "queued_request":          "في حاجة تانية خلصت الأول...",
            "creating_execution_plan": "بفكر في أحسن طريقة...",
            "analyzing_document":      "بقرأ الملف...",
            "generating_summary":      "بعمل الملخص...",
            "executing_task":          "بعمله دلوقتي...",
            "finalizing":              "على وشك خلص...",
        },
    }

    # Global thinking steps being tracked: session_id -> {"step": ..., "language": ...}
    active_sessions = {}

    @staticmethod
    async def update_step(session_id: str, step_key: str, message_id: str, language: str = "en"):
        """
        Update thinking step for a session.

        Args:
            session_id:  Session ID
            step_key:    Step key from THINKING_STEPS (e.g. "analyzing_request") OR a
                         raw free-text string for backward-compat (legacy callers pass
                         full English strings directly).
            message_id:  Original HTTP request message ID
            language:    Preferred system language ("en" | "ar").  Defaults to "en" so
                         old call-sites that omit the argument keep working.
        """
        lang = "ar" if str(language).lower().startswith("ar") else "en"
        lang_steps = ThinkingStepManager.THINKING_STEPS.get(lang, {})

        # ── RESOLUTION PRIORITY ──────────────────────────────────────────────
        # 1. Try exact key lookup in the user's language.
        # 2. If the caller passed a free-text English string, see if it matches
        #    any English value so we can return the properly-translated version.
        # 3. Fall back to the raw string so legacy code keeps working.
        if step_key in lang_steps:
            step_text = lang_steps[step_key]
        else:
            # Try to find matching value in English table → translate
            en_steps = ThinkingStepManager.THINKING_STEPS.get("en", {})
            matched_key = next((k for k, v in en_steps.items() if v == step_key), None)
            if matched_key and matched_key in lang_steps:
                step_text = lang_steps[matched_key]
            else:
                # Raw free-text fallback (legacy)
                step_text = step_key

        logger.info(f"🧠 [{session_id}] Thinking step ({lang}): {step_text}")

        # Store current step
        ThinkingStepManager.active_sessions[session_id] = {"step": step_text, "language": lang}

        # Broadcast to frontend via broker
        try:
            update_msg = AgentMessage(
                message_type=MessageType.STATUS_UPDATE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                response_to=message_id,
                payload={
                    "action": "thinking_update",
                    "step": step_text,
                    "step_key": step_key,
                    "session_id": session_id,
                    "language": lang,
                }
            )
            await broker.publish(Channels.BROADCAST, update_msg)
        except Exception as e:
            logger.warning(f"⚠️ Failed to broadcast thinking update: {e}")

    @staticmethod
    async def clear_steps(session_id: str):
        """Clear thinking steps for a session"""
        if session_id in ThinkingStepManager.active_sessions:
            del ThinkingStepManager.active_sessions[session_id]
            logger.info(f"🧠 [{session_id}] Cleared thinking steps")

        # Broadcast a clear message so clients can remove UI indicators
        try:
            clear_msg = AgentMessage(
                message_type=MessageType.STATUS_UPDATE,
                sender=AgentType.COORDINATOR,
                receiver=AgentType.LANGUAGE,
                session_id=session_id,
                payload={
                    "action": "thinking_clear",
                    "session_id": session_id
                }
            )
            await broker.publish(Channels.BROADCAST, clear_msg)
            logger.info(f"🧠 [{session_id}] Broadcasted thinking_clear to clients")
        except Exception as e:
            logger.warning(f"⚠️ Failed to broadcast thinking_clear: {e}")