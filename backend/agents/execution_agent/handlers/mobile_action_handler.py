"""
mobile_action_handler.py

Routes mobile tasks to the MobileReActStrategy.

Sits between:
  - Coordinator (sends ActionTask with device="mobile")
  - MobileStrategy (3-tier ReAct loop)
  - Android Device (HTTP API at localhost:8000)
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from agents.execution_agent.core.exec_agent_models import ExecutionResult
from agents.execution_agent.strategies.mobile_strategy import (
    MobileStrategy, MobileTaskRequest, compute_smart_timeout,
)
from agents.utils.broker import broker
from agents.utils.protocol import AgentType, Channels, MessageType

logger = logging.getLogger(__name__)


def _failed_result(task_id: str, error: str, duration: float = 0.0) -> ExecutionResult:
    """
    Convenience builder so every failed path fills all required dataclass fields.
    """
    return ExecutionResult(
        status="failed",
        task_id=task_id,
        context="mobile",
        action="react_loop",
        details="",
        logs=[],
        timestamp=datetime.now().isoformat(),
        duration=duration,
        error=error,
    )


class MobileActionHandler:
    """Handles execution of mobile tasks via the 3-tier ReAct loop."""

    def __init__(self, device_id: str = "default_device"):
        self.device_id       = device_id
        self.mobile_strategy = MobileStrategy(device_id)
        logger.info(f"✅ MobileActionHandler ready | device={device_id}")

    async def handle_action_task(
        self,
        task_id:    str,
        session_id: str,
        task:       Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ExecutionResult:
        """
        Execute one atomic mobile task.

        Parameters
        ----------
        task_id    : str — coordinator task identifier
        session_id : str — current session
        task       : dict — the ActionTask payload (ai_prompt, extra_params, …)
        """
        actual_task = task or kwargs.get("task_data")

        if not actual_task:
            logger.error(f"❌ No task data provided for {task_id}")
            return _failed_result(task_id, "Task data missing in request")

        ai_prompt = actual_task.get("ai_prompt", "").strip()
        if not ai_prompt:
            return _failed_result(task_id, "Missing ai_prompt")

        extra       = dict(actual_task.get("extra_params", {}) or {})
        if not extra.get("overall_goal"):
            extra["overall_goal"] = actual_task.get("goal") or ai_prompt
        if not extra.get("goal") and actual_task.get("goal"):
            extra["goal"] = actual_task.get("goal")
        device_id   = extra.get("device_id", self.device_id)
        max_steps   = int(extra.get("max_steps", 15))
        # FIX 2: compute adjusted timeout so the outer wait_for matches what the strategy uses
        timeout_sec = compute_smart_timeout(ai_prompt, int(extra.get("timeout_seconds", 30)))

        logger.info(f"📱 Mobile task {task_id}: '{ai_prompt}'")
        _ic = extra.get("input_content", "")
        if _ic:
            logger.info(
                f"   input_content ({len(_ic)} chars): "
                f"{_ic[:200]}{'...' if len(_ic) > 200 else ''}"
            )

        mobile_task = MobileTaskRequest(
            task_id         = task_id,
            ai_prompt       = ai_prompt,
            device_id       = device_id,
            session_id      = session_id or "default_session",
            context         = extra,
            extra_params    = extra,
            max_steps       = max_steps,
            timeout_seconds = timeout_sec,
        )

        t_start = asyncio.get_event_loop().time()

        try:
            result = await asyncio.wait_for(
                self.mobile_strategy.execute_task(mobile_task),
                timeout=float(timeout_sec),
            )
        except asyncio.TimeoutError:
            strat   = self.mobile_strategy
            elapsed = asyncio.get_event_loop().time() - t_start
            logger.warning(
                f"⏱️ Task {task_id} timed out after {timeout_sec}s — "
                f"steps={len(strat.action_history)} "
                f"llm_calls={strat.total_llm_calls} "
                f"tiers={dict(strat.tier_stats)}"
            )
            return ExecutionResult(
                status    = "failed",
                task_id   = task_id,
                context   = "mobile",
                action    = "react_loop",
                details   = f"Timed out after {timeout_sec}s",
                logs      = [],
                timestamp = datetime.now().isoformat(),
                duration  = elapsed,
                error     = f"Task timed out after {timeout_sec}s",
                metadata  = {
                    "token_usage": dict(strat.token_usage),
                    "llm_calls":   strat.total_llm_calls,
                    "steps_taken": len(strat.action_history),
                    "tier_stats":  dict(strat.tier_stats),
                },
            )
        except Exception as e:
            elapsed = asyncio.get_event_loop().time() - t_start
            logger.error(f"❌ Mobile task {task_id} raised: {e}", exc_info=True)
            return ExecutionResult(
                status    = "failed",
                task_id   = task_id,
                context   = "mobile",
                action    = "react_loop",
                details   = "",
                logs      = [],
                timestamp = datetime.now().isoformat(),
                duration  = elapsed,
                error     = str(e),
            )

        elapsed = result.execution_time_ms / 1000.0
        success = result.status == "success"
        return ExecutionResult(
            status    = "success" if success else "failed",
            task_id   = task_id,
            context   = "mobile",
            action    = "react_loop",
            details   = result.completion_reason or result.error or "Mobile task executed",
            logs      = [],
            timestamp = datetime.now().isoformat(),
            duration  = elapsed,
            error     = result.error,
            metadata  = {
                "token_usage": result.token_usage or {},
                "llm_calls":   result.llm_calls,
                "steps_taken": result.steps_taken,
                "tier_stats":  dict(self.mobile_strategy.tier_stats),
            },
        )


# ── Singleton ───────────────────────────────────────────────────────────────

_mobile_handler: Optional[MobileActionHandler] = None


def initialize_mobile_handler(device_id: str = "default_device"):
    global _mobile_handler
    _mobile_handler = MobileActionHandler(device_id)
    logger.info("✅ Mobile handler initialized")


async def get_mobile_handler() -> MobileActionHandler:
    global _mobile_handler
    if _mobile_handler is None:
        initialize_mobile_handler()
    return _mobile_handler


# ── Message broker integration ──────────────────────────────────────────────

async def handle_mobile_action_task(message: Dict[str, Any]):
    """Callback for mobile tasks arriving via the message broker."""
    try:
        payload    = message.get("payload", {})
        task_id    = payload.get("task_id", "unknown")
        session_id = payload.get("session_id", "default")
        task_data  = payload.get("task", {})

        logger.info(f"📬 Broker mobile task: {task_id}")
        handler = await get_mobile_handler()
        result  = await handler.handle_action_task(
            task_id=task_id, session_id=session_id, task=task_data,
        )

        await broker.publish(
            Channels.EXECUTION_TO_COORDINATOR,
            {
                "type":  MessageType.TASK_RESULT,
                "agent": AgentType.EXECUTION,
                "payload": {
                    "task_id": task_id,
                    "status":  result.status,
                    "content": result.details,
                    "error":   result.error,
                },
            },
        )
    except Exception as e:
        logger.error(f"❌ handle_mobile_action_task: {e}", exc_info=True)