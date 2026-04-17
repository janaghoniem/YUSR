"""
input_sanitiser.py — DiD Layer 1
Deterministic pre-LLM sanitisation. No LLM tokens consumed.

Checks (in order):
  S-04  Length bomb:    truncate to 4096 chars
  S-05  Unicode:        NFKC normalise, strip homoglyphs
  S-01  Override kw:    block known injection trigger phrases
  S-02  Base64 payload: block suspicious base64-encoded strings
  S-03  Delimiters:     block ChatML / prompt-boundary tokens

Returns SanitisationResult. Caller must check .was_blocked before use.
"""

import re
import base64
import unicodedata
import logging
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────
MAX_INPUT_LENGTH = 4096   # S-04 — truncate beyond this

# ── S-01: CRITICAL BLOCK PHRASES (prompt injection / override attempts) ───
# These patterns are ONLY blocked when they appear as INSTRUCTIONS to the LLM,
# not as natural language content from the user.
# 
# Detection strategy: Block ONLY if the phrase appears with:
#   - A colon after it (instruction format)
#   - At the start of a line/segment
#   - Followed by a newline or obvious command structure
#
# Simple string matching for "add a second task" alone is NOT sufficient -
# that could be a valid user request like "can you add a second task to my list"

OVERRIDE_PATTERNS = [
    # These are ALWAYS malicious - no legitimate use
    "ignore previous instructions",
    "disregard your instructions", 
    "forget everything",
    "new system prompt",
    "system override",
    "admin override",
    "developer mode",
    
    # ChatML delimiters (S-03) - ALWAYS block
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "<|im_start|>",
    "<|im_end|>",
    
    # Destructive system commands — ALWAYS block
    "delete all files",
    "permanently delete",
    "delete all files in",
    "wipe all",
    "format drive",
    "rm -rf",
    "del /f /q",
    
    # OS-critical path deletion (system destruction)
    "delete windows",
    "delete system32",
    "delete boot",
    "rm -rf /",
    "rm -rf /*",
    
    # Indirect OS shell invocation
    "[inst]",
    "<<sys>>",
]

# These patterns are checked with CONTEXT (only block if they appear as instructions)
INSTRUCTION_PATTERNS = [
    "important system note",
    "system note:",
    "you must also add",
    "you must also",
    "you must additionally", 
    "also list all",
    "set response_text",
    "set your response_text",
    "when forming your json",
    "ignore previous formatting",
    "ignore previous formatting rules",
    "respond only with this exact json",
]
# ── SUSPICIOUS PHRASES (log warning but DO NOT block) ──────────────────────
# These are moved from OVERRIDE_PATTERNS to allow confirmation flow.
# The intent classifier will handle them with SUSPICIOUS → confirmation.
SUSPICIOUS_PHRASES = [
    # Credential-harvesting intent patterns (now warns, doesn't block)
    "show me all saved",
    "show all saved passwords",
    "show all saved wifi",
    "show all saved credentials",
    "saved wifi passwords",
    "wifi passwords",
    "saved passwords",
    "stored passwords",
    "view saved passwords",
    "display saved passwords",
    "show my passwords",
    "list all passwords",
    "retrieve my password",
    "get my password",
    "show my credentials",
    "show network credentials",
    "reveal the password",
    "show me the api_key",
    "display the mongodb",
    "show me the secret",
    "print my api key",
    "disclose",
]

# S-02: minimum length of a suspicious base64 chunk
B64_MIN_LENGTH = 40


@dataclass
class SanitisationResult:
    clean_text: str
    was_blocked: bool = False
    block_reason: str = ""
    triggered_checks: List[str] = field(default_factory=list)
    is_suspicious: bool = False
    suspicious_matches: List[str] = field(default_factory=list)


def _is_suspicious_base64(fragment: str) -> bool:
    """Return True if the fragment looks like an encoded payload."""
    if len(fragment) < B64_MIN_LENGTH:
        return False
    if re.fullmatch(r'[A-Za-z0-9+/=]{' + str(B64_MIN_LENGTH) + r',}', fragment):
        try:
            decoded = base64.b64decode(fragment + "==").decode("utf-8", errors="replace")
            # Treat as suspicious if decoded text contains common injection words
            decoded_lower = decoded.lower()
            injection_words = [
                "ignore", "system", "override", "admin",
                "prompt", "instruction", "assistant",
            ]
            if any(w in decoded_lower for w in injection_words):
                return True
        except Exception:
            pass
    return False


def sanitise_input(text: str) -> SanitisationResult:
    """
    Apply all Layer 1 sanitisation checks.
    Returns SanitisationResult — caller checks .was_blocked.
    """
    if not text:
        return SanitisationResult(clean_text="")

    checks_triggered = []
    suspicious_matches = []

    # ── S-04: Length bomb ────────────────────────────────────────────────────
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
        checks_triggered.append("S-04-length-truncated")
        logger.info(f"✂️  S-04: Input truncated to {MAX_INPUT_LENGTH} chars")

    # ── S-05: Unicode normalisation + confusable homoglyph mapping ───────────
    normalised = unicodedata.normalize("NFKC", text)
    if normalised != text:
        checks_triggered.append("S-05-unicode-normalised")
        logger.info("🔤 S-05: Unicode normalisation applied")

    _CONFUSABLES = str.maketrans({
        "\u0456": "i",   # Cyrillic і → Latin i
        "\u0430": "a",   # Cyrillic а → Latin a
        "\u0435": "e",   # Cyrillic е → Latin e
        "\u043e": "o",   # Cyrillic о → Latin o
        "\u0440": "r",   # Cyrillic р → Latin r
        "\u0441": "c",   # Cyrillic с → Latin c
        "\u0445": "x",   # Cyrillic х → Latin x
        "\u03bf": "o",   # Greek ο → Latin o
        "\u03b5": "e",   # Greek ε → Latin e
        "\u0455": "s",   # Cyrillic ѕ → Latin s
        "\u04cf": "i",   # Cyrillic ӏ → Latin i
        "\uff49": "i",   # Fullwidth i → Latin i
        "\uff4f": "o",   # Fullwidth o → Latin o
        "\u2027": ".",   # Hyphenation point → period
    })
    homoglyph_mapped = normalised.translate(_CONFUSABLES)
    if homoglyph_mapped != normalised:
        checks_triggered.append("S-05-homoglyph-mapped")
        logger.info("🔤 S-05: Homoglyph mapping applied")

    text = homoglyph_mapped

    # ── CHECK SUSPICIOUS PHRASES FIRST (log but don't block) ─────────────────
    text_lower = text.lower()
    for phrase in SUSPICIOUS_PHRASES:
        if phrase in text_lower:
            suspicious_matches.append(phrase)
            logger.info(f"⚠️ S-01-suspicious: Phrase detected (will route to confirmation): '{phrase}'")

    # ── S-01: SMART OVERRIDE KEYWORD DETECTION ───────────────────────────────
    # First check ALWAYS_BLOCK patterns (no context needed)
    for pattern in OVERRIDE_PATTERNS:
        if pattern in text_lower:
            checks_triggered.append(f"S-01-override:{pattern[:30]}")
            logger.warning(f"🚫 S-01: Override keyword detected: '{pattern}'")
            return SanitisationResult(
                clean_text=text,
                was_blocked=True,
                block_reason=f"Override keyword detected: '{pattern}'",
                triggered_checks=checks_triggered,
                is_suspicious=bool(suspicious_matches),
                suspicious_matches=suspicious_matches,
            )
    
    # Then check INSTRUCTION_PATTERNS with context
    for pattern in INSTRUCTION_PATTERNS:
        if pattern in text_lower:
            # Check if this is being used as an INSTRUCTION (not natural language)
            idx = text_lower.find(pattern)
            lookahead = text[idx:idx+80].lower()
            
            # It's an instruction if:
            # 1. Followed by colon (instruction format)
            # 2. At start of message/line and followed by command
            # 3. Preceded by nothing or newline
            is_instruction = False
            
            if ':' in lookahead:
                is_instruction = True
            elif idx == 0 or text[idx-1] in ['\n', ' ']:
                # Check if what follows looks like a command
                after_pattern = text[idx+len(pattern):idx+80].strip()
                if after_pattern and not after_pattern[0] in ['.', ',', '?', '!']:
                    # Likely a command, not natural language
                    is_instruction = True
            
            if is_instruction:
                checks_triggered.append(f"S-01-instruction:{pattern[:30]}")
                logger.warning(f"🚫 S-01: Instruction pattern detected: '{pattern}'")
                return SanitisationResult(
                    clean_text=text,
                    was_blocked=True,
                    block_reason=f"Instruction pattern detected: '{pattern}'",
                    triggered_checks=checks_triggered,
                    is_suspicious=bool(suspicious_matches),
                    suspicious_matches=suspicious_matches,
                )
            else:
                logger.info(f"ℹ️ Pattern '{pattern}' appears as natural language, not blocking")

    # ── S-02: Base64 payload detection ───────────────────────────────────────
    tokens = re.findall(r'[A-Za-z0-9+/=]{20,}', text)
    for token in tokens:
        if _is_suspicious_base64(token):
            checks_triggered.append(f"S-02-base64:{token[:20]}")
            logger.warning(f"🚫 S-02: Suspicious base64 payload detected")
            return SanitisationResult(
                clean_text=text,
                was_blocked=True,
                block_reason="Suspicious base64-encoded payload detected",
                triggered_checks=checks_triggered,
                is_suspicious=bool(suspicious_matches),
                suspicious_matches=suspicious_matches,
            )

    # ── All checks passed ─────────────────────────────────────────────────────
    if checks_triggered:
        logger.info(f"✅ Input sanitised (checks: {checks_triggered})")
    
    if suspicious_matches:
        logger.info(f"⚠️ Input contains suspicious phrases (will route to confirmation): {suspicious_matches}")
    
    return SanitisationResult(
        clean_text=text,
        triggered_checks=checks_triggered,
        is_suspicious=bool(suspicious_matches),
        suspicious_matches=suspicious_matches,
    )
    """
    Apply all Layer 1 sanitisation checks.
    Returns SanitisationResult — caller checks .was_blocked.
    """
    if not text:
        return SanitisationResult(clean_text="")

    checks_triggered = []
    suspicious_matches = []

    # ── S-04: Length bomb ────────────────────────────────────────────────────
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
        checks_triggered.append("S-04-length-truncated")
        logger.info(f"✂️  S-04: Input truncated to {MAX_INPUT_LENGTH} chars")

    # ── S-05: Unicode normalisation + confusable homoglyph mapping ───────────
    normalised = unicodedata.normalize("NFKC", text)
    if normalised != text:
        checks_triggered.append("S-05-unicode-normalised")
        logger.info("🔤 S-05: Unicode normalisation applied")

    # NFKC alone does not map Cyrillic/Greek lookalikes to Latin equivalents.
    # Apply a minimal confusables table for the most common bypass attempts.
    _CONFUSABLES = str.maketrans({
        "\u0456": "i",   # Cyrillic і → Latin i
        "\u0430": "a",   # Cyrillic а → Latin a
        "\u0435": "e",   # Cyrillic е → Latin e
        "\u043e": "o",   # Cyrillic о → Latin o
        "\u0440": "r",   # Cyrillic р → Latin r
        "\u0441": "c",   # Cyrillic с → Latin c
        "\u0445": "x",   # Cyrillic х → Latin x
        "\u03bf": "o",   # Greek ο → Latin o
        "\u03b5": "e",   # Greek ε → Latin e
        "\u0455": "s",   # Cyrillic ѕ → Latin s
        "\u04cf": "i",   # Cyrillic ӏ → Latin i
        "\uff49": "i",   # Fullwidth i → Latin i
        "\uff4f": "o",   # Fullwidth o → Latin o
        "\u2027": ".",   # Hyphenation point → period
    })
    homoglyph_mapped = normalised.translate(_CONFUSABLES)
    if homoglyph_mapped != normalised:
        checks_triggered.append("S-05-homoglyph-mapped")
        logger.info("🔤 S-05: Homoglyph mapping applied")

    text = homoglyph_mapped

    # ── CHECK SUSPICIOUS PHRASES FIRST (log but don't block) ─────────────────
    text_lower = text.lower()
    for phrase in SUSPICIOUS_PHRASES:
        if phrase in text_lower:
            suspicious_matches.append(phrase)
            logger.info(f"⚠️ S-01-suspicious: Phrase detected (will route to confirmation): '{phrase}'")

    # ── S-01: Override keyword detection (CRITICAL BLOCK) ────────────────────
    for pattern in OVERRIDE_PATTERNS:
        if pattern in text_lower:
            checks_triggered.append(f"S-01-override:{pattern[:30]}")
            logger.warning(f"🚫 S-01: Override keyword detected: '{pattern}'")
            return SanitisationResult(
                clean_text=text,
                was_blocked=True,
                block_reason=f"Override keyword detected: '{pattern}'",
                triggered_checks=checks_triggered,
                is_suspicious=bool(suspicious_matches),
                suspicious_matches=suspicious_matches,
            )

    # ── S-02: Base64 payload detection ───────────────────────────────────────
    # Split on whitespace and check each token that looks like base64
    tokens = re.findall(r'[A-Za-z0-9+/=]{20,}', text)
    for token in tokens:
        if _is_suspicious_base64(token):
            checks_triggered.append(f"S-02-base64:{token[:20]}")
            logger.warning(f"🚫 S-02: Suspicious base64 payload detected")
            return SanitisationResult(
                clean_text=text,
                was_blocked=True,
                block_reason="Suspicious base64-encoded payload detected",
                triggered_checks=checks_triggered,
                is_suspicious=bool(suspicious_matches),
                suspicious_matches=suspicious_matches,
            )

    # ── All checks passed ─────────────────────────────────────────────────────
    if checks_triggered:
        logger.info(f"✅ Input sanitised (checks: {checks_triggered})")
    
    if suspicious_matches:
        logger.info(f"⚠️ Input contains suspicious phrases (will route to confirmation): {suspicious_matches}")
    
    return SanitisationResult(
        clean_text=text,
        triggered_checks=checks_triggered,
        is_suspicious=bool(suspicious_matches),
        suspicious_matches=suspicious_matches,
    )