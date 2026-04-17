import logging

from agents.language_agent import get_agent_for_session
from agents.utils.protocol import AgentMessage, MessageType
from core.dependencies import logger, pending_responses, ws_manager

log = logging.getLogger(__name__)


async def handle_language_output(message):
    """Handle output from Language Agent - supports both dict and AgentMessage"""

    if isinstance(message, dict):
        log.warning("⚠️ Received dict instead of AgentMessage from Language Agent, converting...")
        message = AgentMessage(**message)

    log.info(f"📨 Received from Language Agent: {message.message_type}")
    log.info(f"📋 Message ID: {message.message_id}")
    log.info(f"📋 Response to: {message.response_to}")
    log.info(f"📋 Payload: {message.payload}")
    log.info(f"📋 Current pending responses: {list(pending_responses.keys())}")

    if message.message_type == MessageType.CLARIFICATION_REQUEST:
        response_content = {
            "status": "clarification_needed",
            "question": message.payload.get("question", "Need more information."),
            "response_id": message.message_id,
        }

        log.info(f"❓ Clarification needed: {response_content['question']}")

        target_id = message.response_to
        log.info(f"🔍 Looking for pending response with ID: {target_id}")

        if target_id and target_id in pending_responses:
            if not pending_responses[target_id].done():
                log.info(f"✅ FOUND! Resolving pending request: {target_id}")
                pending_responses[target_id].set_result(response_content)
                log.info("✅ Response resolved successfully")
            else:
                log.warning(f"⚠️ Pending request already resolved: {target_id}")
        else:
            log.error(f"❌ NO PENDING RESPONSE FOUND for: {target_id}")

    elif message.message_type == MessageType.CONFIRMATION_REQUEST:
        response_content = {
            "status": "clarification_needed",
            "question": message.payload.get("question", "Please confirm."),
            "response_id": message.message_id,
        }

        log.info(f"❓ Confirmation needed: {response_content['question']}")

        target_id = message.response_to
        log.info(f"🔍 Looking for pending response with ID: {target_id}")

        if target_id and target_id in pending_responses:
            if not pending_responses[target_id].done():
                log.info(f"✅ FOUND! Resolving pending request: {target_id}")
                pending_responses[target_id].set_result(response_content)
                log.info("✅ Response resolved successfully")
            else:
                log.warning(f"⚠️ Pending request already resolved: {target_id}")
        else:
            log.error(f"❌ NO PENDING RESPONSE FOUND for: {target_id}")

    elif message.message_type == MessageType.TASK_RESPONSE:
        response_content = {
            "status": message.payload.get("status", "completed"),
            "text": message.payload.get("response", "Task completed"),
            "task_id": message.task_id,
            "structured_response": message.payload.get("structured_response"),
            "followup_action": message.payload.get("followup_action"),
            "user_language": message.payload.get("user_language"),
        }

        log.info(f"✅ Task response from Language Agent: {response_content}")

        target_id = message.response_to
        if target_id and target_id in pending_responses:
            log.info(f"✅ Resolving pending request: {target_id}")
            pending_responses[target_id].set_result(response_content)
        else:
            log.error(f"❌ NO PENDING RESPONSE FOUND for: {target_id}")


async def handle_coordinator_output(message):
    """Handle output from Coordinator - supports both dict and AgentMessage"""

    if isinstance(message, dict):
        log.warning("⚠️ Received dict from Coordinator, converting...")
        try:
            message = AgentMessage(**message)
        except Exception as exc:
            log.error(f"❌ Failed to convert: {exc}")
            log.error(f"❌ Dict content: {message}")
            return

    log.info(f"📨 Coordinator → Server: {message.message_type}")

    if message.message_type == MessageType.TASK_RESPONSE:
        result = message.payload
        response_text = result.get("response") or "Task completed"
        structured_response = result.get("structured_response")

        if structured_response and structured_response.get("spoken_text"):
            response_text = structured_response["spoken_text"]
        follow_up_question = result.get("follow_up_question")
        has_follow_up_question = bool(follow_up_question and str(follow_up_question).strip())

        user_language = result.get("user_language") or (structured_response or {}).get("user_language")

        try:
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
                    },
                )
                if user_language:
                    agent.preferred_language = user_language
        except Exception as exc:
            log.warning(f"⚠️ Failed to sync coordinator response into language session memory: {exc}")

        response = {
            "status": "clarification_needed" if has_follow_up_question else result.get("status", "completed"),
            "task_id": message.task_id,
            "text": response_text,
            "question": follow_up_question if has_follow_up_question else (response_text if result.get("status") == "clarification_needed" else None),
            "response_id": message.message_id if has_follow_up_question else None,
            "result": result,
            "structured_response": structured_response,
            "user_language": user_language,
        }

        log.info(f"✅ Task completed, sending to TTS: '{response_text}'")

        target_id = message.response_to
        if target_id and target_id in pending_responses:
            if not pending_responses[target_id].done():
                log.info(f"✅ Resolving pending request: {target_id}")
                pending_responses[target_id].set_result(response)
            else:
                log.warning(f"⚠️ Pending request already resolved: {target_id}")
        else:
            log.warning(f"⚠️ No pending response for {target_id}, trying fallback...")


async def handle_ws_output(message):
    """Route broker messages to WebSocket clients"""
    if isinstance(message, dict):
        message = AgentMessage(**message)
    session_id = message.session_id
    if session_id:
        await ws_manager.send_to_session(session_id, {
            "type": message.payload.get("ws_type", "message"),
            **message.payload,
        })
