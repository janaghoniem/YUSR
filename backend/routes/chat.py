import asyncio
import json
import os
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from pymongo import MongoClient

from ThinkingStepManager import ThinkingStepManager
from agents.language_agent import active_agents
from agents.utils.broker import broker
from agents.utils.protocol import AgentMessage, AgentType, Channels, MessageType
from core.dependencies import (
    AGENT_CONFIRMATION_TIMEOUT_SECONDS,
    AGENT_RESPONSE_TIMEOUT_SECONDS,
    logger,
    pending_responses,
)
from utils.text_utils import detect_language_from_text, detect_interrupt

router = APIRouter()


@router.post("/session/new")
async def create_new_session(request: Request):
    """Create a new chat session and clear short-term memory"""
    try:
        data = await request.json()
        old_session_id = data.get("old_session_id")
        new_session_id = data.get("new_session_id")
        user_id = data.get("user_id", "test_user")

        logger.info(f"🔄 Creating new session: {old_session_id} → {new_session_id}")

        agent_key = f"{user_id}_{old_session_id}"

        if agent_key in active_agents:
            logger.info(f"🗑️ Clearing conversation for {agent_key}")
            active_agents[agent_key].clear_conversation()
            del active_agents[agent_key]
            logger.info(f"✅ Cleared and removed agent: {agent_key}")
        else:
            logger.info(f"ℹ️ No active agent found for {agent_key}")

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

            await broker.publish(Channels.SESSION_CONTROL, session_control_msg)
            logger.info("✅ Sent session control message to Coordinator")
        except Exception as exc:
            logger.warning(f"⚠️ Failed to send session control message: {exc}")

        return {
            "status": "success",
            "old_session_id": old_session_id,
            "new_session_id": new_session_id,
            "message": "New chat session created. Short-term memory cleared."
        }

    except Exception as exc:
        logger.error(f"❌ Session creation failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/process")
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
        user_id = data.get("user_id", "test_user")
        user_language = detect_language_from_text(user_input) or data.get("user_language", "en")

        if not user_input:
            raise HTTPException(status_code=400, detail="Missing 'input' field")

        logger.info(f"📥 HTTP request from session {session_id}: {user_input}")
        logger.info(f"📱 Device type: {device_type}")

        if is_clarification:
            _CONFIRM_WORDS_HTTP = {
                "yes", "y", "ok", "okay", "sure", "proceed", "do it", "done",
                "no", "cancel", "stop", "نعم", "موافق", "لا", "آه", "إلغاء",
            }
            _answer_lower_http = user_input.lower().strip()
            _is_short_confirmation_http = (
                _answer_lower_http in _CONFIRM_WORDS_HTTP
                or (len(user_input.split()) <= 4 and not any(
                    kw in _answer_lower_http for kw in ["delete", "open", "create", "list", "send", "copy", "move", "show"]
                ))
            )
            _msg_type_http = MessageType.CONFIRMATION_RESPONSE if _is_short_confirmation_http else MessageType.CLARIFICATION_RESPONSE
            if not _is_short_confirmation_http:
                logger.info(f"↩️ HTTP clarification looks like a new task — routing as CLARIFICATION_RESPONSE: '{user_input[:60]}'")
            message = AgentMessage(
                message_type=_msg_type_http,
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
                payload={"input": user_input, "device_type": device_type, "user_id": user_id, "user_language": user_language}
            )

        logger.info(f"⏳ Creating pending response for message ID: {message.message_id}")
        future = asyncio.Future()
        pending_responses[message.message_id] = future
        logger.info(f"📝 Registered pending response. Total pending: {len(pending_responses)}")
        logger.info(f"📝 Pending IDs: {list(pending_responses.keys())}")

        await ThinkingStepManager.update_step(
            session_id, "processing_input", message.message_id, language=user_language
        )

        logger.info(f"📤 Publishing message to {Channels.LANGUAGE_INPUT}")
        asyncio.create_task(broker.publish(Channels.LANGUAGE_INPUT, message))

        logger.info(f"⏰ Waiting up to {AGENT_RESPONSE_TIMEOUT_SECONDS:.0f}s for response...")
        try:
            response = await asyncio.wait_for(future, timeout=AGENT_RESPONSE_TIMEOUT_SECONDS)
            logger.info(f"✅ Response received: {response}")
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
    except Exception as exc:
        logger.error(f"❌ Error processing request: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reset")
async def reset_session(request: Request):
    """Reset a conversation session"""
    data = await request.json()
    session_id = data.get("session_id", "default")

    logger.info(f"🔄 Resetting session: {session_id}")

    reset_msg = AgentMessage(
        message_type=MessageType.STATUS_UPDATE,
        sender=AgentType.LANGUAGE,
        receiver=AgentType.LANGUAGE,
        session_id=session_id,
        payload={"action": "reset"}
    )

    await broker.publish(Channels.BROADCAST, reset_msg)

    return {"status": "reset", "session_id": session_id}


@router.post("/new-chat")
async def new_chat_endpoint(request: dict):
    """Handle new chat creation - clear session state"""
    try:
        session_id = request.get("session_id")
        user_id = request.get("user_id", "test_user")

        logger.info(f"🔄 New chat requested - clearing session: {session_id}")

        agent_key = f"{user_id}_{session_id}"

        if agent_key in active_agents:
            active_agents[agent_key].clear_conversation()
            logger.info(f"✅ Cleared language agent for {agent_key}")
        else:
            logger.info(f"ℹ️ No active agent found for {agent_key}")

        return {"status": "success", "message": "New chat started", "session_id": session_id}

    except Exception as exc:
        logger.error(f"❌ New chat error: {exc}")
        return {"status": "error", "message": str(exc)}, 500


@router.get("/chat-messages/{session_id}")
async def get_chat_messages(session_id: str, user_id: str):
    """
    Fetch all messages for a specific session, filtered by user_id.
    Returns only user and assistant messages (strips system prompt).
    """
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        doc = db["language_agent_conversations"].find_one(
            {"session_id": session_id, "user_id": user_id},
            sort=[("timestamp", -1)]
        )

        if not doc or "messages" not in doc:
            logger.warning(f"No messages found for session {session_id} belonging to user {user_id}")
            return {"messages": [], "session_id": session_id}

        import json as json_lib

        def clean_content(role, content):
            if role != "assistant" or not isinstance(content, str):
                return content
            try:
                parsed = json_lib.loads(content)
                return (
                    parsed.get("response_text") or
                    parsed.get("text") or
                    parsed.get("response") or
                    content
                )
            except Exception:
                return content

        messages = [
            {
                "role": m["role"],
                "content": clean_content(m["role"], m.get("content", ""))
            }
            for m in doc["messages"]
            if m.get("role") in ("user", "assistant")
        ]

        logger.info(f"✅ Retrieved {len(messages)} messages for session {session_id} (user: {user_id})")
        return {"messages": messages, "session_id": session_id}

    except Exception as exc:
        logger.error(f"❌ Failed to fetch chat messages: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/chats/{user_id}")
async def get_user_chats(user_id: str):
    """
    Returns all chat sessions for a user, sorted by most recent.
    Reads directly from yusr_db.language_agent_conversations.
    Generates a title from the first user message if none is stored.
    """
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        docs = list(
            db["language_agent_conversations"].find(
                {"user_id": user_id},
                {"session_id": 1, "title": 1, "messages": 1, "timestamp": 1}
            ).sort("timestamp", -1)
        )

        chats = []
        for doc in docs:
            sid = doc.get("session_id")
            if not sid:
                continue

            title = doc.get("title")
            if not title:
                messages = doc.get("messages", [])
                for m in messages:
                    if m.get("role") == "user":
                        content = m.get("content", "")
                        title = content[:40] + ("..." if len(content) > 40 else "")
                        break
            if not title:
                title = "New Chat"

            chats.append({
                "session_id": sid,
                "title": title,
                "timestamp": doc.get("timestamp", 0)
            })

        logger.info(f"✅ Returning {len(chats)} chats for user {user_id}")
        return {"chats": chats}

    except Exception as exc:
        logger.error(f"❌ Failed to get chats for user {user_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/update-chat-title")
async def update_chat_title(request: Request):
    """
    Saves a human-readable title for a session.
    Called by frontend after first AI response generates a title.
    """
    try:
        data = await request.json()
        session_id = data.get("session_id")
        user_id = data.get("user_id")
        title = data.get("title", "").strip()

        if not session_id or not user_id or not title:
            raise HTTPException(status_code=400, detail="Missing session_id, user_id, or title")

        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        db["language_agent_conversations"].update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"title": title}},
            upsert=False
        )

        logger.info(f"✅ Title updated for session {session_id}: '{title}'")
        return {"status": "ok", "title": title}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ Failed to update title: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate-chat-title")
async def generate_chat_title(request: Request):
    """
    Generates a chat title.
    - summarize=False (default): called on first message, just truncates text
    - summarize=True: called after 4 exchanges, uses Gemini to summarize full conversation
    """
    try:
        data = await request.json()
        message = data.get("message", "").strip()
        summarize = data.get("summarize", False)
        session_id = data.get("session_id")
        user_id = data.get("user_id")

        if not summarize:
            title = message[:40] + ("..." if len(message) > 40 else "")
            return {"title": title or "New Chat"}

        from core.dependencies import genai_client

        if not genai_client:
            title = message[:40] + ("..." if len(message) > 40 else "")
            return {"title": title or "New Chat"}

        conversation_text = ""
        if session_id and user_id:
            try:
                client = MongoClient(os.getenv("MONGODB_URI"))
                db = client["yusr_db"]
                doc = db["language_agent_conversations"].find_one(
                    {"session_id": session_id, "user_id": user_id}
                )
                if doc and "messages" in doc:
                    lines = []
                    for m in doc["messages"]:
                        if m.get("role") == "user":
                            lines.append(f"User: {m['content'][:100]}")
                        elif m.get("role") == "assistant":
                            content = m.get("content", "")
                            try:
                                parsed = json.loads(content)
                                content = parsed.get("response_text", content)
                            except Exception:
                                pass
                            lines.append(f"AURA: {content[:100]}")
                    conversation_text = "\n".join(lines[:10])
            except Exception as exc:
                logger.warning(f"⚠️ Could not fetch conversation for title: {exc}")

        if not conversation_text:
            title = message[:40] + ("..." if len(message) > 40 else "")
            return {"title": title or "New Chat"}

        prompt = f"""Summarize this conversation into a very short title of maximum 5 words.

{conversation_text}

Rules:
- Maximum 5 words
- No punctuation at the end
- No quotes
- Be specific about what was done or discussed
- Examples: "Open Notepad write preferences", "Search Amazon white socks", "Set 7am alarm"

Return ONLY the title, nothing else."""

        response = genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        title = response.text.strip().strip('"').strip("'")
        if len(title) > 50:
            title = title[:50]

        logger.info(f"✅ Summarized title: '{title}'")
        return {"title": title}

    except Exception as exc:
        logger.error(f"❌ Title generation failed: {exc}")
        return {"title": data.get("message", "New Chat")[:35] if 'data' in locals() else "New Chat"}


@router.delete("/chats/{session_id}")
async def delete_chat(session_id: str, user_id: str):
    """Delete a specific chat session and all its messages"""
    try:
        client = MongoClient(os.getenv("MONGODB_URI"))
        db = client["yusr_db"]

        result = db["language_agent_conversations"].delete_one(
            {"session_id": session_id, "user_id": user_id}
        )
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Chat not found or access denied")

        return {"status": "ok", "deleted_session_id": session_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
