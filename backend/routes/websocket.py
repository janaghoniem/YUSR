import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ThinkingStepManager import ThinkingStepManager
from agents.utils.broker import broker
from agents.utils.protocol import AgentMessage, AgentType, Channels, ContextSnapshot, MessageType
from core.dependencies import (
    AGENT_CONFIRMATION_TIMEOUT_SECONDS,
    AGENT_RESPONSE_TIMEOUT_SECONDS,
    context_snapshots,
    logger,
    pending_responses,
    ws_manager,
)
from utils.text_utils import classify_confirmation_intent, detect_interrupt, detect_language_from_text

router = APIRouter()

# Session -> device context cache (kept in-memory for quick updates on disconnect)
_WS_DEVICE_CONTEXT: dict[str, dict] = {}

from urllib.parse import parse_qs


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Bidirectional WebSocket for real-time communication.

    Client → Server messages:
      { type: "user_input", text: "...", device_type: "...", user_id: "..." }
      { type: "interrupt", command: "stop|pause|resume|undo|retry" }
      { type: "clarification_response", answer: "...", user_id: "..." }

    Server → Client messages:
      { type: "thinking", step: "..." }
      { type: "task_progress", task_id: "...", status: "..." }
      { type: "clarification_needed", question: "..." }
      { type: "response_complete", text: "...", ... }
      { type: "proactive_prompt", suggestion: "...", offer_actions: [...] }
      { type: "interrupt_ack", message: "...", options: [...] }
      { type: "context_saved", snapshot_id: "..." }
    """
    await ws_manager.connect(session_id, websocket)

    # Immediately register device in MongoDB if query params provide identity.
    try:
        query_string = websocket.scope.get("query_string", b"").decode()
        qs = parse_qs(query_string)
        qs_user = qs.get("user_id") or qs.get("uid") or []
        qs_device = qs.get("device_id") or qs.get("did") or []
        qs_platform = qs.get("platform") or qs.get("device_type") or []

        user_id = qs_user[0] if qs_user else None
        device_id = qs_device[0] if qs_device else session_id
        platform = (qs_platform[0] if qs_platform else None) or "desktop"

        if user_id:
            _WS_DEVICE_CONTEXT[session_id] = {
                "user_id": user_id,
                "device_id": device_id,
                "platform": "mobile" if str(platform).lower() == "mobile" else "desktop",
            }
            from routes.cross_platform_manager import get_cross_platform_manager
            mgr = get_cross_platform_manager()
            if mgr:
                # schedule background registration so connect path isn't blocked
                async def _reg():
                    try:
                        await mgr._registry.register_device(
                            user_id=user_id,
                            device_id=device_id,
                            platform=_WS_DEVICE_CONTEXT[session_id]["platform"],
                            session_id=session_id,
                            label=f"{_WS_DEVICE_CONTEXT[session_id]['platform'].capitalize()} {device_id}",
                        )
                        logger.info(f"✅ WebSocket auto-registered device {device_id} for user {user_id}")
                    except Exception as e:
                        logger.warning(f"⚠️ WebSocket auto-registration failed for {device_id}: {e}")

                asyncio.create_task(_reg())
    except Exception as e:
        logger.debug(f"⚠️ Failed to auto-register WebSocket device: {e}")

    async def ws_broadcast_handler(message):
        if isinstance(message, dict):
            try:
                message = AgentMessage(**message)
            except Exception:
                return
        if message.session_id == session_id or message.session_id is None:
            payload = message.payload or {}
            if payload.get("action") == "thinking_update":
                await ws_manager.send_to_session(session_id, {
                    "type": "thinking_step",
                    "step": payload.get("step", ""),
                    "step_key": payload.get("step_key", ""),
                    "language": payload.get("language", "en"),
                })
            elif payload.get("action") == "thinking_clear":
                await ws_manager.send_to_session(session_id, {
                    "type": "thinking_clear"
                })
            elif payload.get("ws_type") == "task_progress":
                await ws_manager.send_to_session(session_id, payload)

    broker.subscribe(Channels.BROADCAST, ws_broadcast_handler)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "interrupt":
                command = data.get("command", "stop")
                logger.info(f"🛑 WebSocket interrupt: {command} for session {session_id}")

                if command == "undo":
                    snap = context_snapshots.get(session_id)
                    if snap and snap.is_reversible:
                        await ws_manager.send_to_session(session_id, {
                            "type": "interrupt_ack",
                            "command": "undo",
                            "message": f"Undone. {len(snap.completed_tasks)} tasks were rolled back.",
                            "snapshot_id": snap.snapshot_id,
                            "options": ["resume", "discard"]
                        })
                    else:
                        await ws_manager.send_to_session(session_id, {
                            "type": "interrupt_ack",
                            "command": "undo",
                            "message": "Nothing to undo.",
                            "options": []
                        })
                    continue

                interrupt_msg = AgentMessage(
                    message_type=MessageType.INTERRUPT_COMMAND,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.COORDINATOR,
                    session_id=session_id,
                    payload={"command": command}
                )
                await broker.publish(Channels.INTERRUPT_CONTROL, interrupt_msg)

                if command == "stop":
                    snapshot = ContextSnapshot(
                        session_id=session_id,
                        user_id=data.get("user_id", "unknown"),
                        original_request=data.get("original_request", ""),
                        pending_tasks=[],
                        is_reversible=False
                    )
                    context_snapshots[session_id] = snapshot

                await ws_manager.send_to_session(session_id, {
                    "type": "interrupt_ack",
                    "command": command,
                    "message": f"Command '{command}' executed.",
                    "options": ["resume", "undo", "discard"] if command == "stop" else []
                })
                continue

            if msg_type == "user_input":
                user_text = data.get("text", "").strip()
                device_type = data.get("device_type", "desktop")
                user_id = data.get("user_id", "test_user")

                detected_language = detect_language_from_text(user_text)
                frontend_hint = data.get("user_language", "en")
                user_language = detected_language if detected_language in ("en", "ar") else frontend_hint

                from routes.cross_platform_manager import get_cross_platform_manager
                if not user_text:
                    continue


                # Cache session -> device linkage so we can update device online status on disconnect.
                interrupt_action = detect_interrupt(user_text)
                if interrupt_action:
                    logger.info(f"🛑 Voice/text interrupt detected: '{user_text}' → {interrupt_action}")
                    interrupt_msg = AgentMessage(
                        message_type=MessageType.INTERRUPT_COMMAND,
                        sender=AgentType.LANGUAGE,
                        receiver=AgentType.COORDINATOR,
                        session_id=session_id,
                        payload={"command": interrupt_action}
                    )
                    await broker.publish(Channels.INTERRUPT_CONTROL, interrupt_msg)

                    if interrupt_action == "stop":
                        snapshot = ContextSnapshot(
                            session_id=session_id,
                            user_id=user_id,
                            original_request=user_text,
                            is_reversible=False
                        )
                        context_snapshots[session_id] = snapshot
                    await ws_manager.connect(session_id, websocket)
                    await ws_manager.send_to_session(session_id, {
                        "type": "interrupt_ack",
                        "command": interrupt_action,
                        "message": f"Command '{interrupt_action}' received.",
                        "options": ["resume", "undo", "discard"] if interrupt_action in ("stop", "pause") else []
                    })
                    continue

                message = AgentMessage(
                    message_type=MessageType.TASK_REQUEST,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    payload={
                        "input": user_text,
                        "device_type": device_type,
                        "user_id": user_id,
                        "user_language": user_language,
                    }
                )

                future = asyncio.Future()
                pending_responses[message.message_id] = future

                await ThinkingStepManager.update_step(
                    session_id, "processing_input", message.message_id, language=user_language
                )
                asyncio.create_task(broker.publish(Channels.LANGUAGE_INPUT, message))

                async def wait_and_send(msg_id, fut):
                    try:
                        response = await asyncio.wait_for(fut, timeout=AGENT_RESPONSE_TIMEOUT_SECONDS)
                        await ThinkingStepManager.clear_steps(session_id)

                        device_id = data.get("device_id") or session_id

                        if user_id and device_id:
                            _WS_DEVICE_CONTEXT[session_id] = {
                                "user_id": user_id,
                                "device_id": device_id,
                                "platform": "mobile" if str(device_type).lower() == "mobile" else "desktop",
                            }
                            mgr = get_cross_platform_manager()
                            if mgr:
                                try:
                                    await mgr._registry.register_device(
                                        user_id=user_id,
                                        device_id=device_id,
                                        platform=_WS_DEVICE_CONTEXT[session_id]["platform"],
                                        session_id=session_id,
                                        label=f"{_WS_DEVICE_CONTEXT[session_id]['platform'].capitalize()} {device_id}",
                                    )
                                except Exception as reg_err:
                                    logger.debug(f"⚠️ WebSocket device registration failed: {reg_err}")
                        ws_response = {"type": "completion"}
                        if isinstance(response, dict):
                            if response.get("status") == "clarification_needed":
                                structured = response.get("structured_response") or {}
                                full_content = structured.get("full_content") or response.get("full_content", "")
                                content_pages = structured.get("content_pages") or response.get("content_pages", [])
                                offer_read_aloud = bool(
                                    structured.get("offer_read_aloud", response.get("offer_read_aloud", False))
                                )
                                ws_response = {
                                    "type": "clarification",
                                    "question": response.get("question", ""),
                                    "response_id": response.get("response_id", ""),
                                    "user_language": response.get("user_language"),
                                    "text": response.get("text", ""),
                                    "spoken_text": response.get("text", ""),
                                    "structured_response": structured,
                                    "full_content": full_content,
                                    "content_pages": content_pages,
                                    "offer_read_aloud": offer_read_aloud,
                                    "offer_actions": structured.get("offer_actions", []),
                                }
                            elif response.get("status") == "confirmation_needed":
                                structured = response.get("structured_response") or {}
                                ws_response = {
                                    "type": "confirmation_needed",
                                    "question": response.get("question", ""),
                                    "response_id": response.get("response_id", ""),
                                    "user_language": response.get("user_language"),
                                    "text": response.get("text", ""),
                                    "spoken_text": response.get("text", ""),
                                    "structured_response": structured,
                                    "full_content": structured.get("full_content", ""),
                                    "offer_read_aloud": structured.get("offer_read_aloud", False),
                                    "offer_actions": structured.get("offer_actions", []),
                                }
                            else:
                                structured = response.get("structured_response")
                                if structured:
                                    spoken = structured.get("spoken_text", response.get("text", "Task completed"))
                                    ws_response = {
                                        "type": "completion",
                                        "spoken_text": spoken,
                                        "text": spoken,
                                        "full_content": structured.get("full_content", ""),
                                        "offer_read_aloud": structured.get("offer_read_aloud", False),
                                        "offer_actions": structured.get("offer_actions", []),
                                        "structured_response": structured,
                                        "status": response.get("status", "completed"),
                                        "task_id": response.get("task_id"),
                                        "user_language": response.get("user_language") or structured.get("user_language"),
                                    }
                                else:
                                    spoken = response.get("text", "Task completed")
                                    ws_response = {
                                        "type": "completion",
                                        "spoken_text": spoken,
                                        "text": spoken,
                                        "status": response.get("status", "completed"),
                                        "task_id": response.get("task_id"),
                                        "user_language": response.get("user_language"),
                                    }

                        await ws_manager.send_to_session(session_id, ws_response)
                    except asyncio.TimeoutError:
                        await ThinkingStepManager.clear_steps(session_id)
                        await ws_manager.send_to_session(session_id, {
                            "type": "error",
                            "message": "Request timed out"
                        })
                    finally:
                        pending_responses.pop(msg_id, None)

                asyncio.create_task(wait_and_send(message.message_id, future))
                continue

            if msg_type == "clarification_response":
                answer = data.get("answer", "").strip()
                user_id = data.get("user_id", "test_user")
                device_type = data.get("device_type", "desktop")
                user_language = data.get("user_language", "en")

                if not answer:
                    continue

                interrupt_action = detect_interrupt(answer)
                if interrupt_action:
                    interrupt_msg = AgentMessage(
                        message_type=MessageType.INTERRUPT_COMMAND,
                        sender=AgentType.LANGUAGE,
                        receiver=AgentType.COORDINATOR,
                        session_id=session_id,
                        payload={"command": interrupt_action}
                    )
                    await broker.publish(Channels.INTERRUPT_CONTROL, interrupt_msg)
                    await ws_manager.send_to_session(session_id, {
                        "type": "interrupt_ack",
                        "command": interrupt_action,
                        "message": f"Command '{interrupt_action}' received.",
                        "options": []
                    })
                    continue

                _is_short_confirmation = classify_confirmation_intent(answer) in {"affirmative", "negative"}

                _msg_type = MessageType.CONFIRMATION_RESPONSE if _is_short_confirmation else MessageType.TASK_REQUEST

                if not _is_short_confirmation:
                    logger.info(
                        f"↩️ Clarification answer looks like a new task — routing as TASK_REQUEST: '{answer[:60]}'"
                    )

                message = AgentMessage(
                    message_type=_msg_type,
                    sender=AgentType.LANGUAGE,
                    receiver=AgentType.LANGUAGE,
                    session_id=session_id,
                    payload={
                        "answer": answer,
                        "input": answer,
                        "device_type": device_type,
                        "user_id": user_id,
                        "user_language": user_language,
                    }
                )

                future = asyncio.Future()
                pending_responses[message.message_id] = future
                asyncio.create_task(broker.publish(Channels.LANGUAGE_INPUT, message))

                async def wait_clarification(msg_id, fut):
                    try:
                        response = await asyncio.wait_for(fut, timeout=AGENT_CONFIRMATION_TIMEOUT_SECONDS)
                        ws_resp = {"type": "response_complete"}
                        if isinstance(response, dict):
                            if response.get("status") == "processing":
                                ws_resp = {
                                    "type": "processing",
                                    "text": response.get("text", "Proceeding..."),
                                    "status": "processing",
                                }
                            elif response.get("status") == "clarification_needed":
                                structured = response.get("structured_response") or {}
                                full_content = structured.get("full_content") or response.get("full_content", "")
                                content_pages = structured.get("content_pages") or response.get("content_pages", [])
                                offer_read_aloud = bool(
                                    structured.get("offer_read_aloud", response.get("offer_read_aloud", False))
                                )
                                ws_resp = {
                                    "type": "clarification_needed",
                                    "question": response.get("question", ""),
                                    "response_id": response.get("response_id", ""),
                                    "user_language": response.get("user_language"),
                                    "text": response.get("text", ""),
                                    "spoken_text": response.get("text", ""),
                                    "structured_response": structured,
                                    "full_content": full_content,
                                    "content_pages": content_pages,
                                    "offer_read_aloud": offer_read_aloud,
                                    "offer_actions": structured.get("offer_actions", []),
                                }
                            else:
                                structured = response.get("structured_response")
                                if structured and structured.get("offer_read_aloud"):
                                    ws_resp = {
                                        "type": "proactive_prompt",
                                        "text": structured.get("spoken_text", response.get("text", "")),
                                        "full_content": structured.get("full_content", ""),
                                        "offer_read_aloud": True,
                                        "offer_actions": structured.get("offer_actions", []),
                                        "status": response.get("status", "completed"),
                                    }
                                else:
                                    ws_resp = {
                                        "type": "response_complete",
                                        "text": response.get("text", "Task completed"),
                                        "status": response.get("status", "completed"),
                                    }
                        await ws_manager.send_to_session(session_id, ws_resp)
                    except asyncio.TimeoutError:
                        await ws_manager.send_to_session(session_id, {
                            "type": "error", "message": "Request timed out"
                        })
                    finally:
                        pending_responses.pop(msg_id, None)

                asyncio.create_task(wait_clarification(message.message_id, future))
                continue

    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)
        context = _WS_DEVICE_CONTEXT.pop(session_id, None)
        if context:
            mgr = get_cross_platform_manager()
            if mgr:
                try:
                    await mgr._registry.set_device_online(
                        user_id=context.get("user_id", ""),
                        device_id=context.get("device_id", ""),
                        session_id=session_id,
                        online=False,
                    )
                except Exception as off_err:
                    logger.debug(f"⚠️ WebSocket device offline update failed: {off_err}")
    except Exception as exc:
        logger.error(f"❌ WebSocket error: {exc}", exc_info=True)
        ws_manager.disconnect(session_id)
        context = _WS_DEVICE_CONTEXT.pop(session_id, None)
        if context:
            mgr = get_cross_platform_manager()
            if mgr:
                try:
                    await mgr._registry.set_device_online(
                        user_id=context.get("user_id", ""),
                        device_id=context.get("device_id", ""),
                        session_id=session_id,
                        online=False,
                    )
                except Exception as off_err:
                    logger.debug(f"⚠️ WebSocket device offline update failed: {off_err}")
