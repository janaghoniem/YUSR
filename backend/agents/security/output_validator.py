"""
output_validator.py — DiD Layer 3
Scan coordinator output for credential leakage, API keys, MongoDB URIs.
Redacts or blocks before the response reaches the user.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    clean_text: str
    was_modified: bool = False
    violations: List[str] = field(default_factory=list)


# ── Redaction patterns ───────────────────────────────────────────────────────
_PATTERNS = [
    # MongoDB Atlas URI
    (re.compile(r'mongodb\+srv://[^\s"\'<>]+', re.IGNORECASE), "[REDACTED-MONGODB-URI]"),
    # Generic mongodb:// URI
    (re.compile(r'mongodb://[^\s"\'<>]+', re.IGNORECASE), "[REDACTED-MONGODB-URI]"),
    # Groq API key pattern
    (re.compile(r'gsk_[A-Za-z0-9]{30,}', re.IGNORECASE), "[REDACTED-GROQ-KEY]"),
    # OpenAI API key pattern
    (re.compile(r'sk-[A-Za-z0-9]{30,}'), "[REDACTED-API-KEY]"),
    # Generic "password is X" or "password: X" in output
    (re.compile(r'(?:password|passwd|pwd)\s*(?:is|:)\s*\S+', re.IGNORECASE), "[REDACTED-CREDENTIAL]"),
    # "your password is" — T-M1 exfiltration pattern
    (re.compile(r'your\s+password\s+is\s+\S+', re.IGNORECASE), "[REDACTED-CREDENTIAL]"),
    # AWS/Azure secret key patterns
    (re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}'), "[REDACTED-AWS-KEY]"),
    # Generic long bearer tokens
    (re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*'), "[REDACTED-BEARER-TOKEN]"),
    # "api key is X" — short key tokens not caught by gsk_/sk- patterns
    (re.compile(r'(?:api\s*key|secret\s*key|access\s*key)\s+(?:is|:)\s*\S+',
                re.IGNORECASE), "[REDACTED-API-KEY]"),
    # Private key PEM header
    (re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'), "[REDACTED-PRIVATE-KEY]"),
    # Generic short tokens that look like secrets (hex, alphanumeric ≥ 20 chars after assignment)
    (re.compile(r'(?:token|secret|key)\s*[=:]\s*[A-Za-z0-9_\-]{20,}',
                re.IGNORECASE), "[REDACTED-SECRET]"),
]

# Hard-block phrases — if these appear verbatim, block entire output
_HARD_BLOCK_PHRASES = [
    "mongodb+srv://",
    "your password is",
    "the password is",
    "api key is",
    "secret key is",
]


def validate_output(text: str, context: str = "") -> ValidationResult:
    """
    Scan and redact output text.
    Returns ValidationResult with clean_text and violation log.
    """
    if not text:
        return ValidationResult(clean_text=text)

    clean = text
    violations = []
    modified = False


    # Hard block: detect AND redact — detection alone is insufficient
    text_lower = text.lower()
    for phrase in _HARD_BLOCK_PHRASES:
        if phrase in text_lower:
            violations.append(f"Hard-block phrase: '{phrase}'")
            logger.warning(
                f"🔒 Layer 3: Hard-block phrase detected in {context}: '{phrase}'"
            )
            # Enforce: redact the phrase and everything after it on the same line
            # This catches cases where the regex patterns below don't fire
            # (e.g. "the api key is not available" has no credential value to match)
            escaped = re.escape(phrase)
            clean, n = re.subn(
                escaped + r'[^\n]*',
                f"[REDACTED]",
                clean,
                flags=re.IGNORECASE,
            )
            if n > 0:
                modified = True

    # Pattern redaction
    for pattern, replacement in _PATTERNS:
        new_clean, n = pattern.subn(replacement, clean)
        if n > 0:
            violations.append(f"Pattern match: {pattern.pattern[:40]} ({n} occurrence(s))")
            clean = new_clean
            modified = True
            logger.warning(
                f"🔒 Layer 3: Redacted {n} occurrence(s) of pattern in {context}: "
                f"{pattern.pattern[:40]}"
            )

    if modified:
        logger.info(f"✅ Layer 3: Output cleaned ({len(violations)} violation(s))")

    return ValidationResult(
        clean_text=clean,
        was_modified=modified,
        violations=violations,
    )




# ── Code pre-execution scanner ───────────────────────────────────────────────
# Call this BEFORE running generated code in the sandbox.
# It catches credential patterns embedded in code (print(password), hardcoded URIs)
# that would never appear in the spoken response text.

# ── Code pre-execution scanner ───────────────────────────────────────────────
# Call this BEFORE running generated code in the sandbox.
# It catches credential patterns embedded in code (print(password), hardcoded URIs)
# that would never appear in the spoken response text.

_CODE_BLOCK_PATTERNS = [
    # Import statements - catch both 'import X' and 'from X import Y'
    re.compile(r'^\s*import\s+shutil', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*from\s+shutil\s+import', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*import\s+pathlib', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*from\s+pathlib\s+import', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*import\s+ctypes', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*from\s+ctypes\s+import', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*import\s+subprocess', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*from\s+subprocess\s+import', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*import\s+winreg', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*from\s+winreg\s+import', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*import\s+socket', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*from\s+socket\s+import', re.IGNORECASE | re.MULTILINE),
    
    # print(password) patterns
    re.compile(r'print\s*\(.*(?:password|passwd|api.?key|secret|token).*\)',
               re.IGNORECASE),
    
    # MongoDB URIs
    re.compile(r'mongodb\+srv://[^\s"\']+', re.IGNORECASE),
    re.compile(r'mongodb://[^\s"\']+', re.IGNORECASE),
    
    # API Keys
    re.compile(r'gsk_[A-Za-z0-9]{20,}', re.IGNORECASE),
    re.compile(r'sk-[A-Za-z0-9]{20,}'),
    re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}'),
    
    # Hardcoded credential assignments
    re.compile(r'(?:password|passwd|api_key|secret)\s*=\s*["\'][^"\']{6,}["\']',
               re.IGNORECASE),
    
    # netsh commands (WiFi password extraction)
    re.compile(r'netsh\s+wlan', re.IGNORECASE),

    # Shutdown / power-off commands typed via pyautogui or any write() call
    re.compile(r'shutdown\s*/[srph]', re.IGNORECASE),
    re.compile(r'shutdown\s+/s', re.IGNORECASE),
    re.compile(r'shutdown\s+now\b', re.IGNORECASE),
    re.compile(r'pyautogui\.write\s*\(\s*["\'].*shutdown', re.IGNORECASE),
    re.compile(r'pyautogui\.typewrite\s*\(\s*["\'].*shutdown', re.IGNORECASE),
    re.compile(r'\.write\s*\(\s*["\']shutdown', re.IGNORECASE),
    re.compile(r'poweroff\b', re.IGNORECASE),

    # Registry reads targeting credential stores
    re.compile(r'OpenKey.*(?:SAM|Security|Credentials)', re.IGNORECASE),
    
    # os.remove and other dangerous os operations
    re.compile(r'os\.remove\s*\(', re.IGNORECASE),
    re.compile(r'os\.unlink\s*\(', re.IGNORECASE),
    re.compile(r'os\.rmdir\s*\(', re.IGNORECASE),
    re.compile(r'os\.system\s*\(', re.IGNORECASE),
    re.compile(r'os\.popen\s*\(', re.IGNORECASE),
    
    # shutil dangerous operations
    re.compile(r'shutil\.rmtree\s*\(', re.IGNORECASE),
    re.compile(r'shutil\.move\s*\(', re.IGNORECASE),
    re.compile(r'shutil\.copy\s*\(', re.IGNORECASE),
    
    # ctypes
    re.compile(r'ctypes\.windll', re.IGNORECASE),
    re.compile(r'ctypes\.cdll', re.IGNORECASE),
]


def validate_code(code: str, context: str = "") -> ValidationResult:
    """
    Scan generated Python code for credential leakage patterns
    before sandbox execution.

    Returns ValidationResult. If violations is non-empty,
    the caller should BLOCK execution, not just log.
    """
    if not code:
        return ValidationResult(clean_text=code)

    violations = []
    code_lower = code.lower()
    
    for pattern in _CODE_BLOCK_PATTERNS:
        matches = pattern.findall(code)
        if matches:
            violations.append(
                f"Code pattern match: {pattern.pattern[:50]} "
                f"({len(matches)} occurrence(s))"
            )
            logger.warning(
                f"🔒 Layer 3 (code scan): Dangerous pattern in generated code "
                f"[{context}]: {pattern.pattern[:50]}"
            )
    
    # Also check for blocked imports using simple string matching (fallback)
    BLOCKED_IMPORTS = [
        'shutil', 'pathlib', 'ctypes', 'subprocess', 'winreg', 'socket'
    ]
    
    for blocked in BLOCKED_IMPORTS:
        # Check for 'import X' or 'from X import'
        if f'import {blocked}' in code_lower or f'from {blocked} import' in code_lower:
            if not any(blocked in v for v in violations):
                violations.append(f"Blocked import detected: {blocked}")
                logger.warning(f"🔒 Layer 3 (code scan): Blocked import '{blocked}' in [{context}]")

    return ValidationResult(
        clean_text=code,
        was_modified=False,     # code scanner reports, execution layer decides
        violations=violations,
    )


def validate_code(code: str, context: str = "") -> ValidationResult:
    """
    Scan generated Python code for credential leakage patterns
    before sandbox execution.

    Returns ValidationResult. If was_modified is True OR violations is non-empty,
    the caller should BLOCK execution, not just log.
    """
    if not code:
        return ValidationResult(clean_text=code)

    violations = []
    for pattern in _CODE_BLOCK_PATTERNS:
        matches = pattern.findall(code)
        if matches:
            violations.append(
                f"Code pattern match: {pattern.pattern[:50]} "
                f"({len(matches)} occurrence(s))"
            )
            logger.warning(
                f"🔒 Layer 3 (code scan): Dangerous pattern in generated code "
                f"[{context}]: {pattern.pattern[:50]}"
            )

    return ValidationResult(
        clean_text=code,
        was_modified=False,     # code scanner reports, execution layer decides
        violations=violations,
    )