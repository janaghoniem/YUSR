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
USE_FEEDBACK_AGENT_FOR_FAILURES = False


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
    Failure path: call FeedbackAgent for nuanced scoring AND store improvements
                  in TaskMemory so future similar tasks benefit from them.
                  Falls back to heuristic if FeedbackAgent is unavailable.

    Args:
        goal: The high-level goal string (e.g., "Open calculator and compute 25*25")
        task_dict: The ActionTask as a dict (task.model_dump())
        result_dict: The TaskResult as a dict (result.model_dump())
        user_feedback: Optional user correction text

    Returns:
        Scalar reward in [0.0, 1.0]
    """
    status = result_dict.get("status", "failed")

    # Fast path for clear success — no API call needed
    if status == "success":
        content = result_dict.get("content", "")
        if content and "EXECUTION_SUCCESS" in str(content):
            logger.debug(f"⚡ ICRL reward shortcut: status=success → reward=1.0")
            return 1.0
        # Success but no explicit success marker — still high reward
        return 0.85

    # # Fast path when FeedbackAgent is disabled — use heuristic
    # if not USE_FEEDBACK_AGENT_FOR_FAILURES:
    #     heuristic = HEURISTIC_REWARDS.get(status, 0.1)
    #     logger.debug(f"⚡ ICRL heuristic reward: status={status} → reward={heuristic}")
    #     return heuristic

    # Fast path for statuses that FeedbackAgent cannot meaningfully evaluate:
    # - timeout: task never ran, no trajectory to score
    # - pending: task hasn't finished
    # Also used when FeedbackAgent is globally disabled
    HEURISTIC_ONLY_STATUSES = {"timeout", "pending"}
    if status in HEURISTIC_ONLY_STATUSES or not USE_FEEDBACK_AGENT_FOR_FAILURES:
        heuristic = HEURISTIC_REWARDS.get(status, 0.1)
        logger.debug(f"⚡ ICRL heuristic reward: status={status} → reward={heuristic}")
        return heuristic

    # Use FeedbackAgent for nuanced failure scoring.
    # We call evaluate_and_store() (not just evaluate_execution()) so that the
    # improvements list is persisted to TaskMemory — this feeds back into the
    # execution agent's hint retrieval on future similar tasks.
    try:
        from agents.feedback_agent import FeedbackAgent
        from agents.execution_agent.strategies.task_memory import TaskMemory

        trajectory = _build_trajectory_from_task_result(task_dict, result_dict)

        # Build a FeedbackAgent instance with TaskMemory attached so that
        # evaluate_and_store() can persist improvements automatically.
        feedback_agent = FeedbackAgent()
        try:
            task_memory = TaskMemory()
            feedback_agent.attach_memory(task_memory)
        except Exception as _mem_err:
            # TaskMemory may not be available on all machines (ChromaDB optional).
            # Degrade gracefully — evaluate_and_store will skip storage if memory is None.
            logger.debug(f"⚠️ TaskMemory unavailable for FeedbackAgent (non-fatal): {_mem_err}")

        # Run synchronous evaluate_and_store in thread pool to avoid blocking event loop.
        # evaluate_and_store calls evaluate_execution internally AND persists improvements.
        evaluation = await asyncio.to_thread(
            feedback_agent.evaluate_and_store,
            goal,
            trajectory,
            user_feedback,
        )

        reward = float(evaluation.score)
        reward = max(0.0, min(1.0, reward))  # Clamp to [0, 1]

        logger.info(
            f"🎯 ICRL FeedbackAgent reward: {reward:.3f} "
            f"(success={evaluation.is_success}, status={status}, "
            f"improvements={len(evaluation.improvements)})"
        )

        # Log the reasoning so we can distinguish legitimate 0.0 from a crash
        if reward == 0.0:
            logger.info(f"💬 FeedbackAgent reasoning (reward=0.0): {evaluation.reasoning[:200]}")

        # Log improvements for observability
        if evaluation.improvements:
            logger.info(
                f"💡 ICRL improvements stored: "
                + " | ".join(evaluation.improvements[:3])
            )

        return reward

    except Exception as e:
        logger.warning(
            f"⚠️ ICRL reward computation failed, using heuristic: {e}"
        )
        return HEURISTIC_REWARDS.get(status, 0.1)

# ── Strings your execution layer actually emits ───────────────────────────────
# Sourced from: LocalSandbox, ExecutionValidator, coordinator_agent.py error paths

# These appear in result.error or result.content when the EXECUTION layer failed
# (the plan was structurally correct, but the automation/subprocess failed)
_EXECUTION_ERROR_SIGNALS = [
    # From LocalSandbox subprocess runner (execution.py)
    "execution timeout after",          # LocalSandbox timeout
    "execution timeout",                # ExecutionValidator
    "non-zero exit code",               # ExecutionValidator
    "code reported failure",            # ExecutionValidator: FAILED: found in stdout
    "no success indicator found",       # ExecutionValidator: EXECUTION_SUCCESS missing
    "error indicator found in stderr",  # ExecutionValidator
    "security validation failed",       # SecurityValidator blocked the code
    "blocked import",                   # SecurityValidator AST check
    "blocked builtin",                  # SecurityValidator AST check
    "blocked method call",              # SecurityValidator AST check
    "mobile execution error",           # mobile_action_handler in coordinator
    "task timeout",                     # asyncio.TimeoutError in execute_single_task
    # Python runtime errors that come through as stderr/content
    "syntaxerror",
    "nameerror",
    "attributeerror",
    "typeerror",
    "indexerror",
    "keyerror",
    "runtimeerror",
    "zerodivisionerror",
    "importerror",
    "modulenotfounderror",
    "filenotfounderror",
    "permissionerror",
    "valueerror",
    "traceback (most recent call last)",
    "execution_failed",                 # printed by generated code on failure
]

# These appear when the COORDINATOR / PLAN was wrong
# (wrong task ordering, missing steps, wrong dependency chain)
_DECOMPOSITION_ERROR_SIGNALS = [
    "dependency failed",                # exact string set by coordinator_agent.py
    "dependency output was empty",      # coordinator: upstream reasoning task empty
    "all contexts exhausted",           # RAGWithSandbox: no RAG context matched task
    "no code generated",               # RAGWithSandbox: RAG returned nothing
    "planning error",                   # coordinator plan_error field
    "task planning error",              # coordinator send_feedback fallback text
]


def classify_failure_type(
    task_dict: Dict[str, Any],
    result_dict: Dict[str, Any],
) -> str:
    """
    Classify whether a failure originated from:
    - "execution"     → the plan structure was fine, but the subprocess/automation failed
    - "decomposition" → the coordinator generated the wrong plan (bad deps, wrong order)
    - "dependency"    → an upstream task failed and skipped this task
    - "success"       → not a failure
    - "unknown"       → cannot classify

    Uses only strings that your actual system emits (verified from execution.py,
    coordinator_agent.py, and LocalSandbox/ExecutionValidator error paths).
    """
    status = result_dict.get("status", "")

    if status == "success":
        return "success"

    error = str(result_dict.get("error", "")).lower()
    content = str(result_dict.get("content", "")).lower()
    combined = error + " " + content

    # Dependency chain: coordinator sets this exact string
    if "dependency failed" in combined:
        return "dependency"

    # Check decomposition signals first (more specific / higher priority)
    for signal in _DECOMPOSITION_ERROR_SIGNALS:
        if signal in combined:
            return "decomposition"

    # Check execution signals
    for signal in _EXECUTION_ERROR_SIGNALS:
        if signal in combined:
            return "execution"

    # Fallback: action tasks with unclassified failures lean execution
    # (reasoning/language failures that aren't classified are "unknown")
    if task_dict.get("target_agent") == "action" and status == "failed":
        return "execution"

    return "unknown"


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
    failure_type = classify_failure_type(task_dict, result_dict)

    parts = [f"Task: {prompt}", f"Status: {status}", f"FailureType: {failure_type}"]
    if error:
        parts.append(f"Error: {error[:100]}")
    elif content_preview:
        parts.append(f"Output: {content_preview}")

    return " | ".join(parts)