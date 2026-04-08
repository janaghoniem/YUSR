"""
ICRL Reward Bridge — Async wrapper around the existing FeedbackAgent.

Converts a completed ActionTask + TaskResult into a scalar reward (0.0–1.0)
using the FeedbackAgent's evaluate_execution() method.

Key design decisions:
- FeedbackAgent.evaluate_execution() is synchronous (uses Groq SDK directly).
  We wrap it in asyncio.to_thread() to avoid blocking the coordinator's event loop.
- We build a minimal trajectory dict from ActionTask + TaskResult — the FeedbackAgent
  expects a List[Dict] of actions taken.
- For "success" results we can shortcut: reward=1.0 (saves a Groq API call).
- For "failed" results we optionally call FeedbackAgent for nuanced scoring,
  or fall back to heuristic rewards to save tokens.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Heuristic rewards used as fallback when FeedbackAgent call is skipped
HEURISTIC_REWARDS = {
    "success": 1.0,
    "failed": 0.1,
    "pending": 0.3,
    "awaiting_confirmation": 0.5,
    "timeout": 0.05,
}

# Set to False to use fast heuristic rewards (no extra API calls)
# Set to True to use FeedbackAgent LLM for nuanced scoring on failures
USE_FEEDBACK_AGENT_FOR_FAILURES = True


def _build_trajectory_from_task_result(
    task_dict: Dict[str, Any],
    result_dict: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Build a trajectory list compatible with FeedbackAgent.evaluate_execution().
    
    FeedbackAgent expects: List[Dict] with keys like 'action', 'result', 'status'
    """
    return [
        {
            "action": task_dict.get("ai_prompt", "unknown action"),
            "task_id": task_dict.get("task_id", ""),
            "context": task_dict.get("context", "local"),
            "target_agent": task_dict.get("target_agent", "action"),
            "result": result_dict.get("content", ""),
            "status": result_dict.get("status", "failed"),
            "error": result_dict.get("error", ""),
        }
    ]


async def compute_reward(
    goal: str,
    task_dict: Dict[str, Any],
    result_dict: Dict[str, Any],
    user_feedback: Optional[str] = None,
) -> float:
    """
    Compute a scalar reward (0.0–1.0) for a completed task execution.
    
    Fast path: success → 1.0 (no API call needed)
    Failure path: call FeedbackAgent for nuanced scoring OR use heuristic
    
    Args:
        goal: The high-level goal string (e.g., "Open calculator and compute 25*25")
        task_dict: The ActionTask as a dict (task.model_dump())
        result_dict: The TaskResult as a dict (result.model_dump())
        user_feedback: Optional user correction text
    
    Returns:
        Scalar reward in [0.0, 1.0]
    """
    status = result_dict.get("status", "failed")

    # Fast path for clear success
    if status == "success":
        content = result_dict.get("content", "")
        if content and "EXECUTION_SUCCESS" in str(content):
            logger.debug(f"⚡ ICRL reward shortcut: status=success → reward=1.0")
            return 1.0
        # Success but no explicit success marker — still high reward
        return 0.85

    # Fast path for clear failure with no feedback available
    if not USE_FEEDBACK_AGENT_FOR_FAILURES:
        heuristic = HEURISTIC_REWARDS.get(status, 0.1)
        logger.debug(f"⚡ ICRL heuristic reward: status={status} → reward={heuristic}")
        return heuristic

    # Use FeedbackAgent for nuanced failure scoring
    try:
        from agents.feedback_agent import FeedbackAgent

        trajectory = _build_trajectory_from_task_result(task_dict, result_dict)
        feedback_agent = FeedbackAgent()

        # Run synchronous FeedbackAgent in thread pool to avoid blocking event loop
        evaluation = await asyncio.to_thread(
            feedback_agent.evaluate_execution,
            goal,
            trajectory,
            user_feedback,
        )

        reward = float(evaluation.score)
        reward = max(0.0, min(1.0, reward))  # Clamp to [0, 1]

        logger.info(
            f"🎯 ICRL FeedbackAgent reward: {reward:.3f} "
            f"(success={evaluation.is_success}, "
            f"status={status})"
        )
        return reward

    except Exception as e:
        logger.warning(
            f"⚠️ ICRL reward computation failed, using heuristic: {e}"
        )
        return HEURISTIC_REWARDS.get(status, 0.1)


def summarize_task_attempt(
    task_dict: Dict[str, Any],
    result_dict: Dict[str, Any],
) -> str:
    """
    Build a concise human-readable summary of a task attempt.
    This is what the LLM sees in the ICRL context block.
    
    Kept short on purpose — the LLM doesn't need full details,
    just enough to distinguish this attempt from others.
    """
    prompt = task_dict.get("ai_prompt", "unknown")[:150]
    status = result_dict.get("status", "unknown")
    error = result_dict.get("error", "")
    content_preview = str(result_dict.get("content", ""))[:100]

    parts = [f"Task: {prompt}", f"Status: {status}"]
    if error:
        parts.append(f"Error: {error[:100]}")
    elif content_preview:
        parts.append(f"Output: {content_preview}")

    return " | ".join(parts)