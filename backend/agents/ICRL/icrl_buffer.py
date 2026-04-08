"""
ICRL Buffer — Implements Explorative ICRL from arXiv:2506.06303

Strategy: Keep best-2 (by reward) + most-recent-1, deduplicated.
This prevents naive ICRL degeneration (always predicting same output)
while preserving the reward signal for the LLM to optimize against.

Key insight from paper:
- Naive ICRL (all history) → degeneration
- Explorative ICRL (filtered, stochastic) → consistent improvement
- Order matters: best examples LAST (transformer recency bias)
- Scalar rewards alone are sufficient — no text explanations needed
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Reward threshold below which an attempt is considered "negative"
# and excluded from the best-K slots (but still kept as most-recent if it IS most recent)
NEGATIVE_REWARD_THRESHOLD = 0.3

# How many "best" attempts to keep in context
MAX_BEST_SLOTS = 2

# Minimum reward delta to consider a new attempt "different enough" to store
# (prevents storing nearly identical retries)
MIN_REWARD_DELTA = 0.05


@dataclass
class ICRLAttempt:
    """A single attempt + its scalar reward."""
    attempt_number: int
    # Human-readable summary of what was attempted (task plan or action summary)
    attempt_summary: str
    # Scalar reward from FeedbackAgent (0.0 = total failure, 1.0 = perfect)
    reward: float
    # Optional: raw result content for context
    result_snippet: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __repr__(self):
        return f"Attempt {self.attempt_number} (reward={self.reward:.2f}): {self.attempt_summary[:60]}..."


class ICRLBuffer:
    """
    Explorative ICRL buffer for a single task goal.

    Stores attempt history and produces a filtered, ordered context
    following the Explorative ICRL strategy from the paper.

    Usage:
        buffer = ICRLBuffer(goal="open calculator and compute 25*25")
        buffer.add(attempt_summary="...", reward=0.2, result_snippet="...")
        context_str = buffer.build_context(round_number=1)
        instruction = buffer.get_icrl_instruction(round_number=1)
    """

    def __init__(self, goal: str, max_best: int = MAX_BEST_SLOTS):
        self.goal = goal
        self.max_best = max_best
        self._all_attempts: List[ICRLAttempt] = []
        self._attempt_counter = 0
        logger.info(f"📋 ICRL Buffer initialized for goal: '{goal[:60]}'")

    @property
    def attempt_count(self) -> int:
        return len(self._all_attempts)

    @property
    def best_reward(self) -> float:
        if not self._all_attempts:
            return 0.0
        return max(a.reward for a in self._all_attempts)

    def add(self, attempt_summary: str, reward: float, result_snippet: str = "") -> ICRLAttempt:
        """
        Add a new attempt to the buffer.
        
        Args:
            attempt_summary: Natural language description of what was attempted
                             (e.g., the task plan or action sequence)
            reward: Scalar reward from FeedbackAgent (0.0–1.0)
            result_snippet: Short excerpt of the execution result (optional, for context)
        
        Returns:
            The stored ICRLAttempt
        """
        self._attempt_counter += 1
        attempt = ICRLAttempt(
            attempt_number=self._attempt_counter,
            attempt_summary=attempt_summary,
            reward=reward,
            result_snippet=result_snippet[:300] if result_snippet else None,
        )
        self._all_attempts.append(attempt)
        logger.info(
            f"📊 ICRL: Added attempt {self._attempt_counter} "
            f"(reward={reward:.3f}, best_so_far={self.best_reward:.3f})"
        )
        return attempt

    def _get_best_attempts(self) -> List[ICRLAttempt]:
        """Return top-K attempts by reward, excluding negatives, deduplicated."""
        # Filter out clearly negative attempts for "best" slots
        positive = [a for a in self._all_attempts if a.reward >= NEGATIVE_REWARD_THRESHOLD]
        # Sort by reward descending
        positive_sorted = sorted(positive, key=lambda a: a.reward, reverse=True)
        # Take top max_best
        return positive_sorted[:self.max_best]

    def _get_most_recent(self) -> Optional[ICRLAttempt]:
        """Return the most recent attempt (regardless of reward)."""
        if not self._all_attempts:
            return None
        return self._all_attempts[-1]

    def get_explorative_context(self) -> List[ICRLAttempt]:
        """
        Build the filtered, ordered context following Explorative ICRL.
        
        Strategy (from paper + ablation studies):
        1. Take best-K by reward (positive filtering)
        2. Add most-recent-1 if not already in best-K
        3. Order by reward ascending so BEST appears LAST
           (transformer recency bias → last example gets most attention)
        
        Returns list of attempts to include, ordered worst→best.
        """
        if not self._all_attempts:
            return []

        best = self._get_best_attempts()
        most_recent = self._get_most_recent()

        # Build set of attempt numbers already selected
        selected_numbers = {a.attempt_number for a in best}
        context_attempts = list(best)

        # Add most recent if not already included
        if most_recent and most_recent.attempt_number not in selected_numbers:
            context_attempts.append(most_recent)

        # Sort ascending by reward: worst first, best last (recency bias trick)
        context_attempts.sort(key=lambda a: a.reward)

        logger.debug(
            f"🎯 ICRL context: {len(context_attempts)} attempts "
            f"(from {len(self._all_attempts)} total). "
            f"Rewards: {[round(a.reward, 2) for a in context_attempts]}"
        )
        return context_attempts

    def get_icrl_instruction(self, round_number: int) -> str:
        """
        Return the ICRL instruction to append to the prompt.
        
        Alternates between exploration and exploitation per the paper's
        "ICRL Preset" strategy:
        - Even rounds → exploration (generate something DIFFERENT)
        - Odd rounds → exploitation (IMPROVE on the best attempt)
        
        Round 0 (first attempt) gets no instruction — there's no history yet.
        """
        if round_number == 0 or not self._all_attempts:
            return ""

        best = self.best_reward
        best_attempt = next(
            (a for a in sorted(self._all_attempts, key=lambda x: x.reward, reverse=True)),
            None
        )

        if round_number % 2 == 0:
            # Exploration instruction
            return (
                "\n[ICRL EXPLORATION INSTRUCTION]\n"
                "Your previous attempts are shown above with their reward scores (0.0=failure, 1.0=success).\n"
                "For this attempt, generate a plan that is DIFFERENT from all previous attempts. "
                "Try a different approach, different tool order, or different action sequence. "
                "Do NOT repeat a plan that received a low reward.\n"
                "[/ICRL EXPLORATION INSTRUCTION]"
            )
        else:
            # Exploitation instruction
            best_desc = (
                f"(the attempt with reward={best:.2f})" if not best_attempt
                else f"(Attempt {best_attempt.attempt_number} with reward={best_attempt.reward:.2f})"
            )
            return (
                f"\n[ICRL EXPLOITATION INSTRUCTION]\n"
                f"Your previous attempts are shown above with their reward scores (0.0=failure, 1.0=success).\n"
                f"The best attempt so far is {best_desc}. "
                f"Generate a plan that IMPROVES upon this best attempt. "
                f"Keep what worked, fix what didn't. Aim for reward=1.0.\n"
                f"[/ICRL EXPLOITATION INSTRUCTION]"
            )

    def should_stop(self, success_threshold: float = 0.9) -> bool:
        """Return True if a sufficiently good attempt has been found."""
        return self.best_reward >= success_threshold

    def summary(self) -> str:
        """Human-readable summary for logging."""
        if not self._all_attempts:
            return "No attempts yet."
        rewards = [round(a.reward, 3) for a in self._all_attempts]
        return (
            f"Total attempts: {len(self._all_attempts)}, "
            f"rewards: {rewards}, "
            f"best: {self.best_reward:.3f}"
        )