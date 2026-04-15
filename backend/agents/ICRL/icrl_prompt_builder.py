"""
ICRL Prompt Builder — Formats the ICRL history context block.

Produces the string that gets injected into coordinator/execution prompts:

    === PREVIOUS ATTEMPTS (ICRL Context) ===
    Attempt 1:
    Plan: [description of what was tried]
    Result: [short snippet]
    Reward: 0.20

    ---
    Attempt 3:
    Plan: [description of what was tried]
    Result: [short snippet]
    Reward: 0.75

    === END ICRL CONTEXT ===

The LLM sees the reward numbers explicitly and uses them as the signal.
"""

from __future__ import annotations
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.ICRL.icrl_buffer import ICRLBuffer, ICRLAttempt


def build_icrl_context_block(buffer: "ICRLBuffer") -> str:
    """
    Build the formatted ICRL history block to inject into a prompt.
    
    Returns empty string if no attempts have been made yet.
    """
    attempts = buffer.get_explorative_context()
    if not attempts:
        return ""

    lines = ["", "=" * 50]
    lines.append("PREVIOUS ATTEMPTS (In-Context RL History)")
    lines.append(
        "Below are your previous attempts at this task and their reward scores. "
        "Reward 0.0 = complete failure. Reward 1.0 = perfect success. "
        "Learn from this history to generate a better plan."
    )
    lines.append("=" * 50)

    for attempt in attempts:
        lines.append(f"\nAttempt {attempt.attempt_number}:")
        lines.append(f"Plan/Action: {attempt.attempt_summary}")
        if attempt.result_snippet:
            lines.append(f"Result: {attempt.result_snippet}")
        lines.append(f"Reward: {attempt.reward:.2f}")
        lines.append("---")

    lines.append("=" * 50)
    lines.append("")

    return "\n".join(lines)


def inject_icrl_into_decomposition_prompt(
    base_prompt: str,
    buffer: "ICRLBuffer",
    round_number: int,
) -> str:
    """
    Inject ICRL history + instruction into the task decomposition prompt
    used by coordinator's decompose_task_to_actions().
    
    The context block is inserted BEFORE the OUTPUT RULES section
    so the LLM sees it as recent context when generating the task list.
    
    Args:
        base_prompt: The original decomposition prompt string
        buffer: ICRLBuffer with attempt history
        round_number: Current retry round (0 = first attempt, no history)
    
    Returns:
        Modified prompt with ICRL context injected
    """
    if round_number == 0 or buffer.attempt_count == 0:
        return base_prompt  # No history yet, don't modify

    context_block = build_icrl_context_block(buffer)
    instruction = buffer.get_icrl_instruction(round_number)

    icrl_section = context_block + instruction

    # Inject before the OUTPUT RULES section (always present in decomp prompt)
    insertion_marker = "============================\nOUTPUT RULES"
    if insertion_marker in base_prompt:
        return base_prompt.replace(
            insertion_marker,
            icrl_section + "\n" + insertion_marker,
            1  # Only replace first occurrence
        )
    else:
        # Fallback: append before the final "Generate the task decomposition now:" line
        fallback_marker = "Generate the task decomposition now:"
        if fallback_marker in base_prompt:
            return base_prompt.replace(
                fallback_marker,
                icrl_section + "\n" + fallback_marker,
                1
            )
        # Last resort: append at end
        return base_prompt + "\n" + icrl_section


def inject_icrl_into_execution_prompt(
    base_prompt: str,
    buffer: "ICRLBuffer",
    round_number: int,
) -> str:
    """
    Inject ICRL history into an execution-level prompt (for single-task retries).
    Used when a specific ActionTask fails and is retried with ICRL context.
    
    Args:
        base_prompt: The original task prompt / ai_prompt string
        buffer: ICRLBuffer for this specific task
        round_number: Current retry round number
    
    Returns:
        Modified prompt with ICRL context prepended
    """
    if round_number == 0 or buffer.attempt_count == 0:
        return base_prompt

    context_block = build_icrl_context_block(buffer)
    instruction = buffer.get_icrl_instruction(round_number)

    return context_block + instruction + "\n\nCURRENT TASK:\n" + base_prompt