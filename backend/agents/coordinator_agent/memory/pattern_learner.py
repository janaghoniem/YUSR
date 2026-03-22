"""
Pattern Learner — LLM-driven RL-style behavioral pattern detection.

Instead of hardcoded task classification, uses the LLM to analyze
conversation history and identify patterns. The LLM sees the raw
history and decides what patterns exist — no assumptions about task types.

Reward signal = how many times something appears in history.
Threshold = PATTERN_THRESHOLD occurrences before promoting to learned pattern.
"""

import os
import re
import json
import logging
from typing import List, Dict, Optional
from collections import Counter
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

PATTERN_THRESHOLD = 2

# Only promote patterns the LLM rates as this confidence or higher
MIN_LLM_CONFIDENCE = "medium"


def _get_llm_client():
    from groq import Groq
    return Groq(api_key=os.getenv("GROQ_API_KEY"))


def _extract_emails_from_text(text: str) -> List[str]:
    return re.findall(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        text
    )


def _extract_apps_from_memory(memory_text: str) -> List[str]:
    match = re.search(r"Apps used:\s*([^.]+)", memory_text, re.IGNORECASE)
    if not match:
        return []
    apps_str = match.group(1).strip()
    return [
        a.strip().lower()
        for a in apps_str.split(",")
        if a.strip() and a.strip().lower() != "none recorded"
    ]


def _extract_request_from_memory(memory_text: str) -> str:
    match = re.search(
        r"User completed task:\s*(.+?)\.",
        memory_text,
        re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return memory_text.strip()


def _llm_analyze_patterns(
    history_entries: List[str],
    existing_patterns: List[str]
) -> List[Dict]:
    """
    Ask the LLM to look at ALL history entries and identify patterns.
    The LLM decides what patterns exist — no hardcoded task types.
    """
    client = _get_llm_client()

    history_block = "\n".join(
        f"[{i+1}] {entry}" for i, entry in enumerate(history_entries)
    )

    existing_block = (
        "\n".join(f"- {p}" for p in existing_patterns)
        if existing_patterns
        else "None yet."
    )

    prompt = f"""You are analyzing a user's behavioral history to identify recurring patterns.

HISTORY OF USER ACTIONS ({len(history_entries)} entries):
{history_block}

PATTERNS ALREADY KNOWN (do not repeat these):
{existing_block}

Your job:
1. Look at the history entries above
2. Identify behaviors the user does REPEATEDLY (at least {PATTERN_THRESHOLD} times)
3. Identify tools, people, workflows the user consistently uses
4. Do NOT assume anything not evidenced in the history
5. Do NOT repeat patterns already known
6. Be specific — "uses Chrome for YouTube" is better than "uses browser"
7. Include frequency evidence when you have it

For each pattern found, provide:
- preference: A clear factual statement about the user (e.g. "User sends emails to shahd@... regularly")
- category: One of: app_usage, contact, workflow, preference, account
- confidence: "high" (seen 4+ times) or "medium" (seen 2-3 times)
- evidence: Brief explanation of what in the history supports this

Return JSON array only, no markdown:
[
  {{
    "preference": "...",
    "category": "...",
    "confidence": "high" or "medium",
    "evidence": "..."
  }}
]

If no new patterns found, return: []"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You analyze user behavioral history. "
                        "Return only valid JSON. No markdown. No explanation outside JSON."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=800
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            inner = lines[1:]
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            raw = "\n".join(inner).strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []

        # Filter to only medium/high confidence
        valid = [
            p for p in parsed
            if isinstance(p, dict)
            and p.get("confidence") in ("high", "medium")
            and p.get("preference")
        ]
        logger.info(f"LLM identified {len(valid)} patterns from history")
        return valid

    except json.JSONDecodeError as e:
        logger.warning(f"LLM pattern analysis returned invalid JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"LLM pattern analysis failed: {e}")
        return []


def _rule_based_patterns(
    history_entries: List[str],
    existing_pattern_texts: set,
    user_id: str = ""
) -> List[Dict]:
    """
    Fast rule-based patterns that don't need LLM.
    These are high-precision signals: email frequency, app frequency.
    Runs in addition to LLM analysis as a safety net.
    """
    detected = []

    # Frequent emails
    email_counter = Counter()
    for entry in history_entries:
        for email in _extract_emails_from_text(entry):
            email_counter[email] += 1

    for email, count in email_counter.items():
        if count >= PATTERN_THRESHOLD:
            text = f"User frequently contacts {email} (contacted {count} times)"
            if text.lower() not in existing_pattern_texts:
                detected.append({
                    "preference": text,
                    "category": "contact",
                    "confidence": "high" if count >= 4 else "medium",
                    "evidence": f"Email appeared {count} times in history",
                    "source": "rule_based"
                })

    # Frequent apps
    app_counter = Counter()
    for entry in history_entries:
        for app in _extract_apps_from_memory(entry):
            app_counter[app] += 1

    for app, count in app_counter.items():
        if count >= PATTERN_THRESHOLD and app:
            text = f"User frequently uses {app} (used {count} times)"
            if text.lower() not in existing_pattern_texts:
                # Try to use feedback data for real confidence
                # Fall back to count-based if feedback store has no data yet
                confidence = "high" if count >= 4 else "medium"
                evidence = f"App '{app}' appeared {count} times in history"
                try:
                    from agents.coordinator_agent.memory.feedback_store import (
                        get_pattern_feedback_summary
                    )
                    feedback = get_pattern_feedback_summary(user_id, text)
                    if feedback["total_feedback"] >= 2:
                        rate = feedback["acceptance_rate"]
                        if rate >= 0.75:
                            confidence = "high"
                        elif rate >= 0.4:
                            confidence = "medium"
                        else:
                            confidence = "low"
                        evidence = (
                            f"App '{app}' used {count} times, "
                            f"suggestion accepted {int(rate * 100)}% of the time"
                        )
                except Exception:
                    pass  # keep count-based confidence

                detected.append({
                    "preference": text,
                    "category": "app_usage",
                    "confidence": confidence,
                    "evidence": evidence,
                    "source": "rule_based"
                })
    # Repeated request structures
    request_counter = Counter()
    for entry in history_entries:
        req = _extract_request_from_memory(entry)
        # Normalize out specific values to find structural repeats
        normalized = re.sub(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "<email>", req
        )
        normalized = re.sub(r"\b\d{1,2}:\d{2}\b", "<time>", normalized)
        normalized = re.sub(r"\d+", "<num>", normalized)
        normalized = normalized.strip().lower()
        if len(normalized) > 8:
            request_counter[normalized] += 1

    for request, count in request_counter.items():
        if count >= PATTERN_THRESHOLD:
            text = f"User repeatedly performs: '{request}' (done {count} times)"
            if text.lower() not in existing_pattern_texts:
                detected.append({
                    "preference": text,
                    "category": "workflow",
                    "confidence": "high" if count >= 3 else "medium",
                    "evidence": f"Pattern '{request}' appeared {count} times",
                    "source": "rule_based"
                })

    return detected


def analyze_patterns(user_id: str, pref_mgr) -> List[Dict]:
    """
    Full pattern detection: rule-based + LLM.
    Returns list of new patterns to store. Does NOT write to DB.
    """
    all_memories = pref_mgr.get_all_preferences()
    if not all_memories:
        logger.info(f"No memories to analyze for {user_id}")
        return []

    history_entries = []
    existing_pattern_texts = set()
    existing_pattern_display = []

    for mem in all_memories:
        if not isinstance(mem, dict):
            continue
        category = mem.get("metadata", {}).get("category", "")
        memory_text = mem.get("memory", "")

        if category == "conversation_history":
            history_entries.append(memory_text)
        elif category == "learned_pattern":
            existing_pattern_texts.add(memory_text.lower().strip())
            existing_pattern_display.append(memory_text)

    if len(history_entries) < PATTERN_THRESHOLD:
        logger.info(
            f"Not enough history for {user_id} "
            f"({len(history_entries)} entries, need {PATTERN_THRESHOLD})"
        )
        return []

    logger.info(
        f"Running pattern analysis for {user_id}: "
        f"{len(history_entries)} history entries, "
        f"{len(existing_pattern_texts)} existing patterns"
    )

    # Rule-based (fast, no LLM)
    #rule_patterns = _rule_based_patterns(history_entries, existing_pattern_texts)
    # Rule-based (fast, no LLM)
    rule_patterns = _rule_based_patterns(history_entries, existing_pattern_texts, user_id)

    # Add rule-based patterns to existing set before LLM call
    # so LLM doesn't duplicate them
    combined_existing = list(existing_pattern_display) + [
        p["preference"] for p in rule_patterns
    ]

    # LLM-based (slower, smarter)
    llm_patterns = _llm_analyze_patterns(history_entries, combined_existing)

    # Deduplicate between rule-based and LLM results
    all_new_patterns = rule_patterns.copy()
    llm_texts = set(p["preference"].lower() for p in rule_patterns)

    for p in llm_patterns:
        if p["preference"].lower() not in llm_texts:
            all_new_patterns.append(p)
            llm_texts.add(p["preference"].lower())

    logger.info(
        f"Detected {len(all_new_patterns)} new patterns "
        f"({len(rule_patterns)} rule-based, "
        f"{len(llm_patterns)} LLM-based) for {user_id}"
    )
    return all_new_patterns


def store_patterns(user_id: str, pref_mgr, patterns: List[Dict]) -> int:
    """Store detected patterns back into Mem0. Returns count stored."""
    stored = 0
    for pattern in patterns:
        try:
            pref_mgr.add_preference(
                pattern["preference"],
                metadata={
                    "category": "learned_pattern",
                    "confidence": pattern.get("confidence", "medium"),
                    "pattern_source": pattern.get("source", "llm"),
                    "evidence": pattern.get("evidence", ""),
                    "user_id": user_id
                }
            )
            stored += 1
            logger.info(
                f"✅ Stored pattern [{pattern.get('source','llm')}]: "
                f"{pattern['preference'][:60]}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Failed to store pattern: {e}")
    return stored

def update_pattern_confidence(user_id: str, pref_mgr) -> int:
    """
    Re-evaluate confidence of existing learned_pattern memories
    based on accumulated feedback.
    
    - If acceptance_rate >= 0.75 and total_feedback >= 3: promote to "high"
    - If acceptance_rate < 0.4 and total_feedback >= 3: demote, delete pattern
    - Otherwise: leave unchanged
    
    Returns number of patterns updated.
    """
    try:
        from agents.coordinator_agent.memory.feedback_store import (
            get_pattern_feedback_summary
        )
    except Exception as e:
        logger.warning(f"Feedback store unavailable, skipping confidence update: {e}")
        return 0

    all_memories = pref_mgr.get_all_preferences()
    updated = 0

    for mem in all_memories:
        if not isinstance(mem, dict):
            continue
        meta = mem.get("metadata", {})
        if not isinstance(meta, dict):
            continue
        if meta.get("category") != "learned_pattern":
            continue

        pattern_text = mem.get("memory", "")
        mem_id = mem.get("id", "")
        if not pattern_text or not mem_id:
            continue

        summary = get_pattern_feedback_summary(user_id, pattern_text)

        if summary["total_feedback"] < 3:
            # Not enough feedback yet to make a decision
            continue

        rate = summary["acceptance_rate"]
        current_confidence = meta.get("confidence", "medium")

        if rate >= 0.75 and current_confidence != "high":
            logger.info(
                f"⬆️ Promoting pattern to high confidence "
                f"(rate={rate:.2f}): {pattern_text[:50]}"
            )
            # Delete old, store new with updated confidence
            try:
                pref_mgr.delete_preference(mem_id)
                pref_mgr.add_preference(
                    pattern_text,
                    metadata={
                        **meta,
                        "confidence": "high",
                        "acceptance_rate": rate,
                        "feedback_count": summary["total_feedback"]
                    }
                )
                updated += 1
            except Exception as e:
                logger.warning(f"Failed to update confidence: {e}")

        elif rate < 0.4:
            logger.info(
                f"⬇️ Removing low-acceptance pattern "
                f"(rate={rate:.2f}): {pattern_text[:50]}"
            )
            try:
                pref_mgr.delete_preference(mem_id)
                updated += 1
            except Exception as e:
                logger.warning(f"Failed to remove pattern: {e}")

    return updated


def run_pattern_learning(user_id: str, pref_mgr) -> int:
    """
    Full pipeline: update confidence from feedback + analyze + store.
    Call this after every successful task completion.
    Returns number of new patterns stored.
    """
    try:
        # First: update confidence of existing patterns based on feedback
        updated = update_pattern_confidence(user_id, pref_mgr)
        if updated > 0:
            logger.info(f"🔄 Updated confidence for {updated} existing patterns")

        # Then: detect new patterns from history
        patterns = analyze_patterns(user_id, pref_mgr)
        if not patterns:
            return updated
        stored = store_patterns(user_id, pref_mgr, patterns)
        logger.info(
            f"🧠 Pattern learning complete for {user_id}: "
            f"{stored} new patterns stored, {updated} updated"
        )
        return stored + updated
    except Exception as e:
        logger.error(f"❌ Pattern learning failed for {user_id}: {e}")
        return 0
    """
    Full pipeline: analyze + store.
    Call this after every successful task completion.
    Returns number of new patterns stored.
    """
    try:
        patterns = analyze_patterns(user_id, pref_mgr)
        if not patterns:
            return 0
        stored = store_patterns(user_id, pref_mgr, patterns)
        logger.info(
            f"🧠 Pattern learning complete for {user_id}: "
            f"{stored} new patterns stored"
        )
        return stored
    except Exception as e:
        logger.error(f"❌ Pattern learning failed for {user_id}: {e}")
        return 0