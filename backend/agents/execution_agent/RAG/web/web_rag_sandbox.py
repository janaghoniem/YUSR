# ============================================================================
# web_rag_sandbox.py
# ============================================================================
# PURPOSE: Validate RAG-generated Playwright code before it is executed
#          via exec() in WebExecutionPipeline._execute_generated_code()
#
# SCOPE (narrow and surgical):
#   - Replaces the weak _security_check() inside WebExecutionPipeline
#   - Only called on code the LLM just generated, nothing else
#   - Zero changes to any other pipeline logic
#
# HOW TO PLUG IN (one line change in web_execution.py):
#   FIND   (line ~1068):  security_result = self._security_check(generated_code)
#   REPLACE WITH:         security_result = rag_sandbox.check(generated_code)
#
# That's the only change needed.
# ============================================================================

import re
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class WebRAGCodeSandbox:
    """
    Drop-in replacement for WebExecutionPipeline._security_check().

    Returns the same dict shape the pipeline already expects:
        { 'passed': bool, 'violations': List[str] }

    What it catches that the original 6-pattern check misses:
    ─────────────────────────────────────────────────────────
    Original gaps                       Fixed here
    ───────────────────────────────     ──────────────────────────────────
    Only exact string match             Regex-based (handles spaces, cases)
    Misses subprocess.run / Popen       Full subprocess family covered
    Misses exec() with spaces           Handles `exec (` and `exec\t(`
    Misses open() write mode            write/append file open blocked
    Misses direct HTTP libs             requests / httpx / aiohttp blocked
    Misses env var credential reads     os.environ sensitive key reads
    Misses __builtins__ override        __builtins__ manipulation blocked
    No URL safety on page.goto()        Validates goto() targets inline
    """

    # ── Patterns that must NOT appear in RAG-generated Playwright code ────────

    _DANGEROUS = [

        # Shell / process execution
        (r"\bos\.system\s*\(",                      "os.system() shell call"),
        (r"\bsubprocess\.(run|Popen|call|"
         r"check_output|check_call|getoutput)\s*\(", "subprocess execution"),
        (r"\beval\s*\(",                             "eval() code execution"),
        (r"\bexec\s*\(",                             "exec() code execution"),
        (r"__import__\s*\(",                         "__import__() dynamic import"),
        (r"\bcompile\s*\(",                          "compile() code generation"),

        # File system write / delete
        (r"\bopen\s*\([^)]*['\"][wa+]['\"]",         "file open in write/append mode"),
        (r"\bos\.(remove|unlink|rmdir|makedirs"
         r"|rename|replace)\s*\(",                   "os filesystem mutation"),
        (r"\bshutil\.(rmtree|move|copy2?|"
         r"copytree|rmdir)\s*\(",                    "shutil filesystem mutation"),
        (r"pathlib\.Path[^)]*\."
         r"(unlink|rmdir|write_text|write_bytes|"
         r"mkdir|rename|replace)\s*\(",              "pathlib filesystem mutation"),

        # Direct HTTP networking (only page.goto / Playwright allowed)
        (r"\bimport\s+(requests|httpx|"
         r"aiohttp|urllib)\b",                       "direct HTTP library import"),
        (r"\brequests\.(get|post|put|delete|"
         r"patch|head|request)\s*\(",                "direct HTTP request"),

        # Credential / secret exfiltration
        (r"os\.environ.*?"
         r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)",  "env var secret read"),
        (r"\bopen\s*\(['\"].*"
         r"(\.env|secrets|credentials|config)\b",    "sensitive file read attempt"),

        # Python internals abuse
        (r"__builtins__",                            "__builtins__ override"),
        (r"__globals__",                             "__globals__ access"),
        (r"\bctypes\b",                              "ctypes low-level access"),
        (r"\bpickle\.(loads|load)\s*\(",             "pickle deserialization"),
    ]

    # ── URL patterns the LLM might embed in page.goto() ──────────────────────

    _BLOCKED_URL_PATTERNS = [
        (r"page\.goto\s*\(['\"]file://",
         "file:// navigation in generated code"),
        (r"page\.goto\s*\(['\"]javascript:",
         "javascript: navigation in generated code"),
        (r"page\.goto\s*\(['\"]data:",
         "data: URI navigation in generated code"),
        (r"page\.goto\s*\(['\"]https?://"
         r"(169\.254\.169\.254|"            # AWS metadata
         r"metadata\.google\.internal|"    # GCP metadata
         r"192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)",
         "SSRF target in page.goto()"),
    ]

    # ── Compile all patterns once at class load ───────────────────────────────

    _COMPILED_DANGEROUS = [
        (re.compile(pat, re.IGNORECASE | re.MULTILINE), label)
        for pat, label in _DANGEROUS
    ]

    _COMPILED_URL = [
        (re.compile(pat, re.IGNORECASE | re.MULTILINE), label)
        for pat, label in _BLOCKED_URL_PATTERNS
    ]

    # ─────────────────────────────────────────────────────────────────────────

    def check(self, code: str) -> Dict:
        """
        Main entry point — same return shape as original _security_check().

            result = rag_sandbox.check(generated_code)
            if not result['passed']:
                # existing pipeline error path

        Returns:
            { 'passed': bool, 'violations': List[str] }
        """
        if not isinstance(code, str) or not code.strip():
            return {'passed': False, 'violations': ["Empty or invalid code"]}

        violations: List[str] = []

        for pattern, label in self._COMPILED_DANGEROUS:
            if pattern.search(code):
                violations.append(f"Blocked: {label}")
                logger.warning(f"[RAGSandbox] {label} detected in generated code")

        for pattern, label in self._COMPILED_URL:
            if pattern.search(code):
                violations.append(f"Blocked URL in code: {label}")
                logger.warning(f"[RAGSandbox] {label}")

        passed = len(violations) == 0

        if passed:
            logger.debug("[RAGSandbox] Generated code passed security check ✅")
        else:
            logger.error(
                f"[RAGSandbox] Code BLOCKED — {len(violations)} violation(s):\n"
                + "\n".join(f"  • {v}" for v in violations)
            )

        return {'passed': passed, 'violations': violations}


# ── Module-level singleton — import this in web_execution.py ─────────────────
rag_sandbox = WebRAGCodeSandbox()