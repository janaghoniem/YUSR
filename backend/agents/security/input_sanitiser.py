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
from typing import List

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────
MAX_INPUT_LENGTH = 4096   # S-04 — truncate beyond this

# S-01: phrases that signal prompt injection / override attempts
# Keep entries lowercase for case-insensitive matching.
OVERRIDE_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "forget everything",
    "you are now",
    "act as if",
    "pretend you are",
    "new system prompt",
    "system override",
    "admin override",
    "developer mode",
    "ignore the above",
    "disregard the above",
    # Coordinator-injection variants (A6)
    "important system note",
    "system note:",
    "you must also add",
    "add a second task",
    "add a task to",
    "also list all",
    "set response_text",
    # Structural injection (A4 original)
    "ignore previous formatting",
    "ignore previous formatting rules",      # A4 variant — live-test bypass
    "ignore previous format",
    "you must respond only with this exact json",
    "respond only with this exact json",
    # ChatML delimiters (S-03)
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "<|im_start|>",
    "<|im_end|>",
    # Destructive social-engineering phrases (B2/B3 fix)
# Destructive social-engineering phrases (B2/B3 fix)
    "delete all files",
    "permanently delete",
    "delete all files in",
    "wipe all",
    "format drive",
    "rm -rf",
    "del /f /q",
    # Credential-harvesting intent patterns (B4 fix)
    "show me all saved",
    "show all saved",
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
    # Indirect OS shell invocation
    "open cmd",
    "open command prompt",
    "open powershell",
    "open terminal",
    "open bash",
    "launch cmd",
    "launch powershell",
    "run cmd",
    "run command prompt",
    "run powershell",
    "run shell",
    # Missing ChatML delimiters
    "[inst]",
    "<<sys>>",
    # Missing disregard variant
    "disregard your prior",
    "disregard all",

]

# S-02: minimum length of a suspicious base64 chunk
B64_MIN_LENGTH = 40


@dataclass
class SanitisationResult:
    clean_text: str
    was_blocked: bool = False
    block_reason: str = ""
    triggered_checks: List[str] = field(default_factory=list)


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

    # ── S-01: Override keyword detection ────────────────────────────────────
    text_lower = text.lower()
    for pattern in OVERRIDE_PATTERNS:
        if pattern in text_lower:
            checks_triggered.append(f"S-01-override:{pattern[:30]}")
            logger.warning(f"🚫 S-01: Override keyword detected: '{pattern}'")
            return SanitisationResult(
                clean_text=text,
                was_blocked=True,
                block_reason=f"Override keyword detected: '{pattern}'",
                triggered_checks=checks_triggered,
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
            )

    # ── All checks passed ─────────────────────────────────────────────────────
    if checks_triggered:
        logger.info(f"✅ Input sanitised (checks: {checks_triggered})")
    return SanitisationResult(clean_text=text, triggered_checks=checks_triggered)