"""
Mobile Integration for Coordinator-Execution Communication
Bridge between Coordinator and Mobile Strategy

This module handles the async routing of mobile tasks from the coordinator
to the mobile execution strategy, converting between different message formats.

Flow:
1. Coordinator sends ActionTask with device="mobile"
2. This handler converts ActionTask → MobileTaskRequest
3. Mobile strategy executes with ReAct loop
4. Result converted back to ExecutionResult
5. Sent back to coordinator
"""

import asyncio
import logging
from typing import Dict, Any, Optional

from agents.utils.device_protocol import MobileTaskRequest
from agents.utils.protocol import ExecutionResult

logger = logging.getLogger(__name__)


async def convert_coordinator_task_to_mobile(
    task: Dict[str, Any],
    device_id: str = "default_device"
) -> MobileTaskRequest:
    """
    Convert coordinator ActionTask format to MobileTaskRequest
    
    Args:
        task: ActionTask from coordinator
        device_id: Android device to target
    
    Returns:
        MobileTaskRequest for execution
    """
    
    extra_params = dict(task.get("extra_params", {}) or {})
    if not extra_params.get("overall_goal"):
        extra_params["overall_goal"] = task.get("goal") or task.get("ai_prompt", "")
    if not extra_params.get("goal") and task.get("goal"):
        extra_params["goal"] = task.get("goal")

    return MobileTaskRequest(
        task_id=task.get("task_id", "unknown"),
        ai_prompt=task.get("ai_prompt", ""),
        device_id=extra_params.get("device_id", device_id),
        session_id=task.get("session_id", "unknown"),
        context=extra_params,
        extra_params=extra_params,
        max_steps=extra_params.get("max_steps", 15),
        timeout_seconds=extra_params.get("timeout_seconds", 30)
    )


async def convert_mobile_result_to_execution(
    mobile_result: Any,  # MobileTaskResult
    task_id: str
) -> ExecutionResult:
    """
    Convert MobileTaskResult to ExecutionResult format
    
    Args:
        mobile_result: MobileTaskResult from strategy
        task_id: Original task ID
    
    Returns:
        ExecutionResult for coordinator
    """
    
    success = mobile_result.status == "success"
    
    return ExecutionResult(
        task_id=task_id,
        status="success" if success else "failed",
        content={
            "steps_taken": mobile_result.steps_taken,
            "max_steps": mobile_result.max_steps,
            "actions": [a.dict() for a in mobile_result.actions_executed],
            "completion_reason": mobile_result.completion_reason,
            "execution_time_ms": mobile_result.execution_time_ms
        },
        error=mobile_result.error
    )
