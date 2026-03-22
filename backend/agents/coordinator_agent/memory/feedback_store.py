"""
Feedback Store — records outcomes of pattern-based suggestions.

Positive signal: user confirmed the suggestion ("yes", "sure", etc.)
Negative signal: user rejected it ("no", "use X instead", "wrong", etc.)
Neutral: user ignored it and moved to something else

No ML training. Just MongoDB counters that pattern_learner reads
to decide confidence level of each pattern.
"""

import os
import logging
from datetime import datetime
from typing import Optional, List
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

POSITIVE_SIGNALS = [
    "yes", "yeah", "yep", "sure", "ok", "okay", "correct",
    "that's right", "go ahead", "do it", "use that", "use it",
    "نعم", "ايوه", "تمام", "اوكي", "صح", "استخدمه"
]

NEGATIVE_SIGNALS = [
    "no", "nope", "wrong", "not that", "don't", "use", "instead",
    "different", "other", "change", "cancel", "stop",
    "لا", "خطأ", "مش", "غير", "بدل", "استخدم", "اغير"
]


def _get_feedback_collection():
    client = MongoClient(os.getenv("MONGODB_URI"))
    return client["yusr_db"]["pattern_feedback"]


def record_feedback(
    user_id: str,
    pattern_text: str,
    pattern_category: str,
    outcome: str,
    session_id: str = ""
):
    """
    Record whether a pattern suggestion was accepted or rejected.
    
    Args:
        user_id: The user
        pattern_text: The pattern that was suggested e.g. "User frequently uses Chrome"
        pattern_category: e.g. "app_usage", "contact"
        outcome: "positive", "negative", or "neutral"
        session_id: optional session tracking
    """
    try:
        col = _get_feedback_collection()
        col.insert_one({
            "user_id": user_id,
            "pattern_text": pattern_text,
            "pattern_category": pattern_category,
            "outcome": outcome,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        })
        logger.info(f"📊 Feedback recorded: '{pattern_text[:40]}' → {outcome}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to record feedback: {e}")


def get_pattern_feedback_summary(user_id: str, pattern_text: str) -> dict:
    """
    Get acceptance rate for a specific pattern.
    Pattern learner calls this to decide whether to keep or demote a pattern.
    """
    try:
        col = _get_feedback_collection()
        records = list(col.find({
            "user_id": user_id,
            "pattern_text": pattern_text
        }))

        positive = sum(1 for r in records if r.get("outcome") == "positive")
        negative = sum(1 for r in records if r.get("outcome") == "negative")
        neutral = sum(1 for r in records if r.get("outcome") == "neutral")
        total = len(records)

        return {
            "total_feedback": total,
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "acceptance_rate": positive / total if total > 0 else None
        }
    except Exception as e:
        logger.warning(f"⚠️ Failed to get feedback summary: {e}")
        return {"total_feedback": 0, "acceptance_rate": None}


def detect_signal(user_text: str) -> str:
    """
    Detect whether the user's message is a positive, negative, or neutral signal.
    Returns "positive", "negative", or "neutral".
    """
    text = user_text.strip().lower()

    if any(signal == text or text.startswith(signal) for signal in NEGATIVE_SIGNALS):
        return "negative"

    if any(signal == text or text.startswith(signal) for signal in POSITIVE_SIGNALS):
        return "positive"

    return "neutral"