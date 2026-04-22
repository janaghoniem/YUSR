"""
mobile_strategy_codegen.py

MobileCodeGenStrategy — LLM Code-Generation strategy for Android UI automation.

Instead of a step-by-step ReAct loop, the LLM generates a complete
uiautomator2 Python script from the task description + a single UI snapshot.

Architecture:
┌──────────────────────────────────────────────────────────────────────────────┐
│  Tier 1: Template Cache                               0 tokens · ~5 ms       │
│  • Fuzzy-match task → inject dynamic params → execute                        │
│  • Stored templates have {placeholder} values for every dynamic input        │
├──────────────────────────────────────────────────────────────────────────────┤
│  Tier 2: LLM Code Generation                          ~400 tokens · 1 call  │
│  • Single LLM call: task + initial UI snapshot → full uiautomator2 script   │
│  • No step loop — entire automation expressed as one program                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  Tier 3: Error Recovery                               +1-2 LLM calls        │
│  • On execution failure: LLM regenerates with error + traceback context     │
│  • Max MAX_CODE_RETRIES attempts before reporting failure                   │
└──────────────────────────────────────────────────────────────────────────────┘

Cache design:
  • Successful code is stored as a template with {placeholder} values
  • "Set alarm at 5:30 PM"  → stores {alarm_hour}, {alarm_minute}, {alarm_period}
  • "Set alarm at 7:45 AM"  → inject → execute (zero LLM cost)
  • Templates keyed by SHA-1(app + normalised task pattern); JSON-backed
  • Ready for ChromaDB/vector-store upgrade via CacheBackend protocol

Key differences from MobileReActStrategy:
  • UI tree fetched ONCE for context; code navigates via uiautomator2 API, not
    via element IDs resolved each step
  • App launch:  d.app_start(package) — instant, no home-screen navigation
  • Low-level:   resource IDs, text/desc matchers, direct coordinate taps
  • No stuck-detection, thought-loop detection, or multi-tier action dispatch
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import sys
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, runtime_checkable

import uiautomator2 as u2

from agents.utils.device_protocol import (
    MobileTaskRequest,
    MobileTaskResult,
    SemanticUITree,
)
from agents.execution_agent.core.exec_agent_models import ExecutionResult

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def compute_smart_timeout(task_text: str, base_timeout: int = 30) -> int:
    """
    Adjust timeout based on task complexity indicators.
    Returns adjusted timeout in seconds.
    """
    adjusted = base_timeout
    
    # Keywords that suggest longer operations
    complex_keywords = {
        "search": 5,
        "scroll": 3,
        "load": 5,
        "wait": 3,
        "navigate": 3,
        "install": 10,
        "download": 10,
    }
    
    text_lower = (task_text or "").lower()
    for keyword, extra in complex_keywords.items():
        if keyword in text_lower:
            adjusted = max(adjusted, base_timeout + extra)
    
    return min(adjusted, 120)  # Cap at 120 seconds


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

CACHE_FILE: Path = Path(
    os.getenv("CODEGEN_CACHE_FILE", "~/.mobile_codegen_cache.json")
).expanduser()

#: Android package names for common apps
APP_PACKAGES: Dict[str, str] = {
    "gmail":           "com.google.android.gm",
    "chrome":          "com.android.chrome",
    "clock":           "com.google.android.deskclock",
    "contacts":        "com.google.android.contacts",
    "play_store":      "com.android.vending",
    "youtube":         "com.google.android.youtube",
    "maps":            "com.google.android.apps.maps",
    "google docs":     "com.google.android.apps.docs",
    "google sheets":   "com.google.android.apps.spreadsheets",
    "google slides":   "com.google.android.apps.presentations",
    "google drive":    "com.google.android.apps.docs",
    "google photos":   "com.google.android.apps.photos",
    "google meet":     "com.google.android.apps.meetings",
    "google keep":     "com.google.android.keep",
    "google calendar": "com.google.android.calendar",
    "google tasks":    "com.google.android.apps.tasks",
    "settings":        "com.android.settings",
    "camera":          "com.android.camera2",
    "messages":        "com.google.android.apps.messaging",
    "whatsapp":        "com.whatsapp",
    "spotify":         "com.spotify.music",
    "calculator":      "com.google.android.calculator",
    "files":           "com.google.android.apps.nbu.files",
    "dialer":          "com.google.android.dialer",
    "phone":           "com.google.android.dialer",
}

#: Reverse map: package tail → canonical name (for display only)
PACKAGE_TO_APP: Dict[str, str] = {
    pkg.rsplit(".", 1)[-1]: app for app, pkg in APP_PACKAGES.items()
}

# ── Regex patterns for parameter extraction ────────────────────────────────
_TIME_RE    = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.IGNORECASE)
_EMAIL_RE   = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE)
_PHONE_RE   = re.compile(r"\b(\+?\d[\d\s\-().]{6,})\b")
_QUOTED_RE  = re.compile(r'["\']([^"\']{1,300})["\']')
_SUBJECT_RE = re.compile(r"subject\s+[\"']?([^\"'.\n]{1,120})[\"']?", re.IGNORECASE)
_BODY_RE    = re.compile(r"(?:body|message|content)\s+[\"']([^\"']{1,500})[\"']", re.IGNORECASE)
_QUERY_RE   = re.compile(
    r"(?:search(?:\s+for)?|find|look\s+up|google|type)\s+[\"']?([^\"'.,!?\n]{2,120})[\"']?",
    re.IGNORECASE,
)

# ── Strategy knobs ─────────────────────────────────────────────────────────
CACHE_SIM_THRESHOLD: float = 0.55   # Jaccard similarity for cache hit
CODE_EXEC_TIMEOUT:   int   = 90     # seconds before killing subprocess
MAX_CODE_RETRIES:    int   = 2      # LLM regeneration attempts on failure

# Keys coming from the coordinator that should not appear in user prompt params
_INTERNAL_KEYS: Set[str] = {
    "input_from", "device_id", "app_name", "package_name", "file_path",
    "max_steps", "timeout_seconds", "language", "overall_goal", "goal",
    "session_id", "uiautomator_port",
}


# ══════════════════════════════════════════════════════════════════════════════
#  DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TaskParameters:
    """
    Dynamic values extracted from a task description.
    Used for template injection and code generation context.

    Example:
        raw = {
            "alarm_hour":   "05",
            "alarm_minute": "30",
            "alarm_period": "PM",
            "alarm_time":   "5:30 PM",
        }
    """
    raw: Dict[str, Any] = field(default_factory=dict)

    # ── Access helpers ─────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.raw[key] = value

    def has(self, key: str) -> bool:
        return key in self.raw and bool(self.raw[key])

    # ── Template injection ─────────────────────────────────────────────

    def inject(self, template_code: str) -> str:
        """Replace every {key} placeholder with its extracted value."""
        result = template_code
        for k, v in self.raw.items():
            result = result.replace(f"{{{k}}}", str(v))
        return result

    def missing_keys(self, template_code: str) -> List[str]:
        """Return placeholder names that are present in template but missing from params."""
        placeholders = set(re.findall(r"\{(\w+)\}", template_code))
        return sorted(placeholders - set(self.raw.keys()))

    # ── Debug ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:  # noqa: D105
        return f"TaskParameters({self.raw})"

    def describe(self) -> str:
        """Human-readable param list for LLM prompts."""
        if not self.raw:
            return "  (none extracted)"
        return "\n".join(f"  {k} = {v!r}" for k, v in self.raw.items())


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CodeTemplate:
    """
    A cached, working automation script with dynamic {placeholder} values.

    Storage example for "set alarm at 5:30 PM":
        task_pattern   = "set alarm at {alarm_time}"
        code_template  = '''
            d.app_start("com.google.android.deskclock")
            d.app_wait("com.google.android.deskclock", timeout=6)
            time.sleep(1.5)
            d(description="Add alarm").click()
            time.sleep(0.5)
            d(resourceId="android:id/input_hour").set_text("{alarm_hour}")
            d(resourceId="android:id/input_minute").set_text("{alarm_minute}")
            d(text="{alarm_period}").click()
            d(text="OK").click()
            time.sleep(0.5)
            print("TASK_COMPLETE")
        '''
        parameter_schema = {
            "alarm_hour":   "str — zero-padded 12h hour e.g. '05'",
            "alarm_minute": "str — zero-padded minute e.g. '30'",
            "alarm_period": "str — 'AM' or 'PM'",
            "alarm_time":   "str — display e.g. '5:30 PM'",
        }
    """

    template_id:      str
    task_pattern:     str                 # normalised task with {placeholders}
    app:              str                 # canonical app name
    package:          str                 # Android package
    code_template:    str                 # uiautomator2 Python with {placeholders}
    parameter_schema: Dict[str, str]      # param_name → human description
    keywords:         List[str]           = field(default_factory=list)
    success_count:    int                 = 0
    failure_count:    int                 = 0
    created_at:       str                 = ""
    last_used:        str                 = ""

    @property
    def reliability(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CodeTemplate":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExecutionOutcome:
    """Result of running a generated uiautomator2 script."""
    success:           bool
    stdout:            str = ""
    stderr:            str = ""
    return_code:       int = 0
    execution_time_ms: int = 0
    error_summary:     str = ""

    def __bool__(self) -> bool:
        return self.success


# ══════════════════════════════════════════════════════════════════════════════
#  CACHE BACKEND PROTOCOL  (swap to ChromaDB without touching strategy)
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class CacheBackend(Protocol):
    """Interface that any cache backend must implement."""

    def lookup(
        self,
        task_text: str,
        app: str,
        threshold: float = CACHE_SIM_THRESHOLD,
    ) -> Optional[Tuple[CodeTemplate, float]]: ...

    def add(self, template: CodeTemplate) -> None: ...
    def mark_success(self, template_id: str) -> None: ...
    def mark_failure(self, template_id: str) -> None: ...
    def stats(self) -> str: ...


# ══════════════════════════════════════════════════════════════════════════════
#  TEMPLATE CACHE  (JSON-backed, keyword-Jaccard similarity)
# ══════════════════════════════════════════════════════════════════════════════

class TemplateCache:
    """
    Persistent JSON-backed template store.

    Similarity is computed as Jaccard overlap on keyword sets extracted from
    the task text and the template's task_pattern.  App mismatch halves the
    score.  Replace this class with a ChromaDB-backed implementation that
    satisfies CacheBackend without changing any other code.
    """

    _STOPWORDS: Set[str] = {
        "the", "a", "an", "in", "on", "at", "to", "for", "of",
        "and", "or", "is", "it", "my", "me", "i", "please", "with",
    }

    def __init__(self, cache_file: Path = CACHE_FILE) -> None:
        self._path:  Path                      = cache_file
        self._store: Dict[str, CodeTemplate]   = {}
        self._load()
        logger.info(
            f"[CACHE] TemplateCache ready: "
            f"{len(self._store)} templates | path={self._path}"
        )

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            loaded = 0
            for record in raw.get("templates", []):
                try:
                    t = CodeTemplate.from_dict(record)
                    self._store[t.template_id] = t
                    loaded += 1
                except Exception as e:
                    logger.debug(f"[CACHE] Skipping malformed record: {e}")
            logger.debug(f"[CACHE] Loaded {loaded} templates from disk")
        except Exception as e:
            logger.warning(f"[CACHE] Load failed ({e}) — starting with empty cache")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"templates": [t.to_dict() for t in self._store.values()]}
            with self._path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[CACHE] Save failed: {e}")

    # ── Keyword helpers ───────────────────────────────────────────────────

    @classmethod
    def _kw(cls, text: str) -> Set[str]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return {t for t in tokens if t not in cls._STOPWORDS and len(t) >= 2}

    @staticmethod
    def _jaccard(a: Set[str], b: Set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # ── Public API ────────────────────────────────────────────────────────

    def lookup(
        self,
        task_text: str,
        app: str,
        threshold: float = CACHE_SIM_THRESHOLD,
    ) -> Optional[Tuple[CodeTemplate, float]]:
        """
        Find the best-matching template.
        Returns (template, similarity_score) when score >= threshold, else None.
        """
        if not self._store:
            return None

        query_kw  = self._kw(task_text)
        app_lower = (app or "").lower().strip()
        best_t:   Optional[CodeTemplate] = None
        best_sim: float = 0.0

        for t in self._store.values():
            template_kw = set(t.keywords)
            sim = self._jaccard(query_kw, template_kw)
            # Penalise cross-app matches rather than hard-reject
            if app_lower and t.app.lower() != app_lower:
                sim *= 0.4
            # Penalise unreliable templates
            if t.reliability < 0.5 and (t.success_count + t.failure_count) >= 3:
                sim *= 0.6
            if sim > best_sim:
                best_sim = sim
                best_t   = t

        if best_t is not None and best_sim >= threshold:
            logger.info(
                f"[CACHE] HIT  sim={best_sim:.2f} | "
                f"pattern='{best_t.task_pattern[:55]}' | app={best_t.app}"
            )
            return best_t, best_sim

        logger.info(
            f"[CACHE] MISS best_sim={best_sim:.2f} < {threshold} | "
            f"query='{task_text[:55]}'"
        )
        return None

    def add(self, template: CodeTemplate) -> None:
        template.keywords   = list(self._kw(template.task_pattern))
        template.created_at = template.created_at or datetime.utcnow().isoformat()
        self._store[template.template_id] = template
        self._save()
        logger.info(
            f"[CACHE] Stored '{template.task_pattern[:55]}' "
            f"id={template.template_id[:8]}"
        )

    def mark_success(self, template_id: str) -> None:
        if template_id in self._store:
            t = self._store[template_id]
            t.success_count += 1
            t.last_used      = datetime.utcnow().isoformat()
            self._save()

    def mark_failure(self, template_id: str) -> None:
        if template_id in self._store:
            self._store[template_id].failure_count += 1
            self._save()

    def stats(self) -> str:
        total = len(self._store)
        if not total:
            return "TemplateCache(empty)"
        avg_r = sum(t.reliability for t in self._store.values()) / total
        return f"TemplateCache(n={total} avg_reliability={avg_r:.0%})"


# ══════════════════════════════════════════════════════════════════════════════
#  PARAMETER EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

class ParameterExtractor:
    """
    Extracts all dynamic values from a task description using regex.

    All methods are static — no state, composable.
    """

    @staticmethod
    def extract(
        task_text:    str,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> TaskParameters:
        """
        Return TaskParameters populated from task_text and optional extra_params.
        extra_params values always take precedence over regex-extracted ones.
        """
        params = TaskParameters()
        extra  = extra_params or {}
        text   = task_text or ""

        # ── Time ──────────────────────────────────────────────────────────
        ParameterExtractor._extract_time(text, params)

        # ── Email ─────────────────────────────────────────────────────────
        ParameterExtractor._extract_email(text, params)

        # ── Search query ──────────────────────────────────────────────────
        ParameterExtractor._extract_query(text, params)

        # ── Phone number ──────────────────────────────────────────────────
        pm = _PHONE_RE.search(text)
        if pm:
            phone = re.sub(r"[\s\-().]", "", pm.group(1))
            params.set("phone_number", phone)

        # ── Generic quoted values (subject, body, contact, etc.) ──────────
        ParameterExtractor._extract_quoted(text, params)

        # ── Extra params override (highest priority) ───────────────────────
        for k, v in extra.items():
            if k in _INTERNAL_KEYS or v is None:
                continue
            val = str(v).strip()
            if not val:
                continue
            # Map generic keys to canonical names
            canonical = {
                "text":         "search_query",
                "query":        "search_query",
                "to":           "recipient_email",
                "subject":      "email_subject",
                "body":         "email_body",
                "message":      "email_body",
                "contact":      "contact_name",
                "event":        "event_title",
                "title":        "event_title",
                "value":        "input_value",
            }.get(k, k)
            params.set(canonical, val)

        # Derive alarm_time display string if not already set
        if params.has("alarm_hour") and not params.has("alarm_time"):
            h   = params.get("alarm_hour", "12")
            m   = params.get("alarm_minute", "00")
            p   = params.get("alarm_period", "AM")
            params.set("alarm_time", f"{int(h)}:{m} {p}")

        logger.debug(f"[PARAMS] Extracted: {params}")
        return params

    # ── Private helpers ────────────────────────────────────────────────────

    @staticmethod
    def _extract_time(text: str, params: TaskParameters) -> None:
        tm = _TIME_RE.search(text)
        if not tm:
            return
        hour   = int(tm.group(1))
        minute = int(tm.group(2))
        period = (tm.group(3) or "").upper()

        # Normalise to 12-hour
        if not period:
            period = "PM" if (12 <= hour < 24) else "AM"
        if hour > 12:
            hour  -= 12
            period = "PM"
        if hour == 0:
            hour   = 12
            period = "AM"

        params.set("alarm_hour",   f"{hour:02d}")
        params.set("alarm_minute", f"{minute:02d}")
        params.set("alarm_period", period)
        params.set("alarm_time",   f"{hour}:{minute:02d} {period}")

    @staticmethod
    def _extract_email(text: str, params: TaskParameters) -> None:
        emails = _EMAIL_RE.findall(text)
        if emails:
            params.set("recipient_email", emails[0])
            if len(emails) > 1:
                params.set("cc_email", emails[1])

        sm = _SUBJECT_RE.search(text)
        if sm:
            params.set("email_subject", sm.group(1).strip())

        bm = _BODY_RE.search(text)
        if bm:
            params.set("email_body", bm.group(1).strip())

    @staticmethod
    def _extract_query(text: str, params: TaskParameters) -> None:
        qm = _QUERY_RE.search(text)
        if qm:
            params.set("search_query", qm.group(1).strip().strip("'\""))

    @staticmethod
    def _extract_quoted(text: str, params: TaskParameters) -> None:
        quoted = _QUOTED_RE.findall(text)
        if quoted:
            # First quoted string fills search_query if not already set
            if not params.has("search_query"):
                params.set("search_query", quoted[0])
            # Second fills email_subject if not already set
            if len(quoted) >= 2 and not params.has("email_subject"):
                params.set("email_subject", quoted[1])


# ══════════════════════════════════════════════════════════════════════════════
#  PLACEHOLDER EXTRACTOR  (working code → reusable template)
# ══════════════════════════════════════════════════════════════════════════════

class PlaceholderExtractor:
    """
    Converts a working, concrete script into a reusable code template
    by replacing task-specific string literals with {placeholder} names.

    Only replaces values that appear *inside string literals* — this prevents
    accidental substitution in resource IDs, comments, or variable names.

    Example:
        concrete:  d(resourceId="android:id/input_hour").set_text("05")
        template:  d(resourceId="android:id/input_hour").set_text("{alarm_hour}")
    """

    # Descriptions displayed in parameter_schema for common keys
    _PARAM_DESCRIPTIONS: Dict[str, str] = {
        "alarm_hour":      "str — zero-padded 12h hour e.g. '05'",
        "alarm_minute":    "str — zero-padded minute e.g. '30'",
        "alarm_period":    "str — 'AM' or 'PM'",
        "alarm_time":      "str — display e.g. '5:30 PM'",
        "recipient_email": "str — email address e.g. 'user@example.com'",
        "email_subject":   "str — subject line",
        "email_body":      "str — email body text",
        "search_query":    "str — search terms",
        "contact_name":    "str — full name",
        "event_title":     "str — calendar event title",
        "phone_number":    "str — digits only e.g. '01234567890'",
        "input_value":     "str — generic input value",
    }

    def extract(
        self,
        code:      str,
        params:    TaskParameters,
        task_text: str = "",
    ) -> Tuple[str, Dict[str, str]]:
        """
        Returns (template_code, parameter_schema).
        """
        template = code
        schema:  Dict[str, str] = {}

        # Process longest values first to avoid partial overlaps
        items = sorted(params.raw.items(), key=lambda kv: -len(str(kv[1])))

        for key, val in items:
            val_str = str(val)
            if len(val_str) < 2:
                continue
            placeholder = f"{{{key}}}"
            new_template = self._replace_in_string_literals(template, val_str, placeholder)
            if new_template != template:
                template = new_template
                schema[key] = self._PARAM_DESCRIPTIONS.get(
                    key, f"str — value e.g. {val_str!r}"
                )

        logger.debug(
            f"[PLACEHOLDER] {len(schema)} placeholders extracted: {list(schema.keys())}"
        )
        return template, schema

    @staticmethod
    def _replace_in_string_literals(code: str, value: str, placeholder: str) -> str:
        """Replace `value` with `placeholder` only inside quoted string literals."""
        escaped = re.escape(value)
        # Match double-quoted and single-quoted strings separately
        def _sub(m: re.Match) -> str:
            s = m.group(0)
            if value in s:
                inner  = s[1:-1].replace(value, placeholder)
                return s[0] + inner + s[-1]
            return s
        pattern = re.compile(
            r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
        )
        return pattern.sub(_sub, code)


# ══════════════════════════════════════════════════════════════════════════════
#  UI SNAPSHOT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_ui_snapshot(
    xml_dump: Any,
    max_elements: int = 50,
) -> str:
    """
    Parse a UIAutomator2 XML hierarchy dump and return a concise text snapshot.

    Format per element:
        [resource_id_or_class]  "label"  @(cx%,cy%)  [FLAGS]

    The snapshot is included in the LLM prompt once per task.
    """
    if not xml_dump:
        return "(no UI data available)"

    # Accept str or bytes
    if isinstance(xml_dump, bytes):
        xml_dump = xml_dump.decode("utf-8", errors="replace")
    if not isinstance(xml_dump, str) or not xml_dump.strip():
        return "(empty UI dump)"

    try:
        root = ET.fromstring(xml_dump)
    except ET.ParseError as e:
        logger.debug(f"[SNAPSHOT] XML parse error: {e}")
        return "(UI parse error)"

    lines: List[str] = []
    sw = sh = 0
    count = [0]

    def _parse_bounds(raw: str) -> Optional[Dict[str, int]]:
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (raw or "").strip())
        if not m:
            return None
        return {
            "left": int(m.group(1)), "top": int(m.group(2)),
            "right": int(m.group(3)), "bottom": int(m.group(4)),
        }

    def visit(node: ET.Element) -> None:
        nonlocal sw, sh
        if count[0] >= max_elements:
            return

        attrs    = node.attrib
        bounds   = _parse_bounds(attrs.get("bounds", ""))
        if bounds:
            sw = max(sw, bounds["right"])
            sh = max(sh, bounds["bottom"])

        clickable = attrs.get("clickable",  "false").lower() == "true"
        focusable = attrs.get("focusable",  "false").lower() == "true"
        visible   = attrs.get("visible-to-user", "true").lower() != "false"

        if (clickable or focusable) and visible:
            text  = attrs.get("text", "")
            desc  = attrs.get("content-desc", "")
            rid   = attrs.get("resource-id", "")
            cls   = attrs.get("class", "").rsplit(".", 1)[-1]
            label = text or desc or "(no text)"

            rid_or_cls = rid if rid else cls

            coord = ""
            if bounds and sw > 0 and sh > 0:
                cx    = (bounds["left"] + bounds["right"])  // 2
                cy    = (bounds["top"]  + bounds["bottom"]) // 2
                coord = f" @({cx * 100 // sw}%, {cy * 100 // sh}%)"

            flags: List[str] = []
            if clickable:                                         flags.append("CLK")
            if focusable:                                         flags.append("FOC")
            if attrs.get("scrollable", "false").lower() == "true": flags.append("SCR")
            if attrs.get("enabled",    "true").lower()  == "false": flags.append("DISABLED")
            flag_str = f" [{','.join(flags)}]" if flags else ""

            lines.append(f"[{rid_or_cls}]  {label!r}{coord}{flag_str}")
            count[0] += 1

        for child in node:
            visit(child)

    visit(root)
    return "\n".join(lines) if lines else "(no interactive elements found)"


# ══════════════════════════════════════════════════════════════════════════════
#  CODE EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

class CodeExecutor:
    """
    Executes a uiautomator2 Python script in a child subprocess with a timeout.

    The executor prepends a preamble that:
    - imports uiautomator2, time, re, sys
    - connects to the device and assigns `d`
    - sets implicit-wait

    Generated code only needs to use `d` and `time`.
    """

    _PREAMBLE = textwrap.dedent(
        """\
        import uiautomator2 as u2
        import time
        import re
        import sys

        _serial = {serial!r}
        d = u2.connect(_serial) if _serial else u2.connect()
        d.implicitly_wait(3.0)

        """
    )

    def __init__(
        self,
        device_serial: str = "",
        timeout: int = CODE_EXEC_TIMEOUT,
    ) -> None:
        self.device_serial = device_serial
        self.timeout       = timeout

    def _build_script(self, code: str) -> str:
        preamble = self._PREAMBLE.format(serial=self.device_serial or "")
        # Dedent user code so it runs at module scope
        return preamble + textwrap.dedent(code)

    async def execute(self, code: str) -> ExecutionOutcome:
        """Write script to a temp file and execute it asynchronously."""
        full_script = self._build_script(code)
        logger.debug(f"[EXEC] Full script ({len(full_script.splitlines())} lines):\n{full_script}")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(full_script)
            tmp_path = fh.name

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                elapsed = int((time.monotonic() - t0) * 1000)
                return ExecutionOutcome(
                    success=False,
                    error_summary=f"Execution timed out after {self.timeout}s",
                    return_code=-1,
                    execution_time_ms=elapsed,
                )

            elapsed    = int((time.monotonic() - t0) * 1000)
            stdout     = stdout_b.decode("utf-8", errors="replace")
            stderr     = stderr_b.decode("utf-8", errors="replace")
            rc         = proc.returncode or 0
            success    = (rc == 0)

            error_summary = ""
            if not success:
                # Surface the most actionable error line
                meaningful = [
                    l for l in stderr.splitlines()
                    if l.strip() and not l.strip().startswith("#")
                ]
                error_summary = meaningful[-1] if meaningful else f"Return code {rc}"

            logger.info(
                f"[EXEC] rc={rc} | {elapsed}ms | "
                f"stdout={stdout[:80]!r} | err={error_summary[:80]!r}"
            )
            return ExecutionOutcome(
                success=success,
                stdout=stdout,
                stderr=stderr,
                return_code=rc,
                execution_time_ms=elapsed,
                error_summary=error_summary,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  LLM SYSTEM PROMPT  (static uiautomator2 API reference)
# ══════════════════════════════════════════════════════════════════════════════

_CODEGEN_SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are an expert Android automation engineer.

    Your job: given a task description, a list of extracted parameters, and an
    optional screen snapshot, output a complete, runnable Python automation script.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ENVIRONMENT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    A connected uiautomator2 device object `d` is available.
    `time` and `re` are already imported.
    DO NOT add import statements or reassign `d`.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    APP LAUNCH  (always start here — never navigate manually)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    d.app_start("com.google.android.gm")           # Gmail
    d.app_start("com.android.chrome")              # Chrome
    d.app_start("com.google.android.deskclock")    # Clock / alarms
    d.app_start("com.android.vending")             # Play Store
    d.app_start("com.google.android.youtube")      # YouTube
    d.app_start("com.google.android.apps.maps")    # Maps
    d.app_start("com.google.android.calendar")     # Calendar
    d.app_start("com.google.android.contacts")     # Contacts
    d.app_start("com.android.settings")            # Settings
    d.app_wait("com.xxx", timeout=6)               # wait for foreground

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ELEMENT SELECTORS  (prefer resource-id > description > text)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    d(text="OK")
    d(textContains="Search")
    d(description="Add alarm")
    d(resourceId="android:id/hours")
    d(resourceId="com.android.chrome:id/url_bar")
    d(className="android.widget.EditText")
    d(text="Send", className="android.widget.Button")   # combined

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    INTERACTIONS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    d(text="Send").click()
    d(text="Delete").long_click()
    d(resourceId="...").set_text("hello")          # focus + clear + type
    d(resourceId="...").clear_text()               # clear only
    d(resourceId="...").get_text()                 # read current value
    d.send_keys("text")                            # type to currently-focused field
    d.press("back")   d.press("home")   d.press("enter")   d.press("search")
    d.click(540, 960)                              # coordinate tap
    d.swipe_ext("up", scale=0.8)                   # fling scroll
    d(scrollable=True).scroll.to(text="Target")    # scroll until visible

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    WAITING & GUARDS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    d(text="...").wait(timeout=5)                  # wait for element to appear
    if d(text="Allow").exists(timeout=2):          # conditional guard
        d(text="Allow").click()
    time.sleep(1.5)                                # after app_start / major transition
    time.sleep(0.5)                                # after click
    time.sleep(0.3)                                # after typing

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    MANDATORY RULES
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    1. ALWAYS begin with d.app_start(package) + d.app_wait(package, timeout=6).
    2. ALWAYS end the script with:  print("TASK_COMPLETE")
    3. Add d(text="Allow").exists(timeout=2) guards for optional permission dialogs.
    4. Use set_text() for text fields — never simulate key-by-key typing.
    5. After pressing enter/search, add time.sleep(2) for results to load.
    6. Do NOT wrap the whole script in try/except — let real errors surface.
    7. Output ONLY raw Python code. No markdown fences, no explanations.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    REFERENCE PATTERNS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SEARCH in Chrome
    d.app_start("com.android.chrome")
    d.app_wait("com.android.chrome", timeout=6)
    time.sleep(1.5)
    if d(description="Search or type URL").exists(timeout=3):
        d(description="Search or type URL").click()
    d(resourceId="com.android.chrome:id/url_bar").set_text("vets near me")
    d.press("enter")
    time.sleep(2)
    print("TASK_COMPLETE")

    # SET ALARM in Clock
    d.app_start("com.google.android.deskclock")
    d.app_wait("com.google.android.deskclock", timeout=6)
    time.sleep(1.5)
    d(description="Add alarm").click()
    time.sleep(0.5)
    d(resourceId="android:id/input_hour").set_text("05")
    d(resourceId="android:id/input_minute").set_text("30")
    d(text="PM").click()
    d(text="OK").click()
    time.sleep(0.5)
    print("TASK_COMPLETE")

    # COMPOSE EMAIL in Gmail
    d.app_start("com.google.android.gm")
    d.app_wait("com.google.android.gm", timeout=6)
    time.sleep(1.5)
    d(description="Compose").click()
    time.sleep(1)
    d(resourceId="com.google.android.gm:id/to").set_text("user@example.com")
    d.press("enter")
    d(resourceId="com.google.android.gm:id/subject").set_text("Hello")
    d(resourceId="com.google.android.gm:id/body").click()
    d.send_keys("Message body here")
    d(description="Send").click()
    time.sleep(1)
    print("TASK_COMPLETE")
    """
).strip()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN STRATEGY CLASS
# ══════════════════════════════════════════════════════════════════════════════

class MobileCodeGenStrategy:
    """
    Code-generation strategy for Android UI automation.

    Flow per task:
    ┌───────────────────────────────────────────────────────────────────────┐
    │  1. Extract dynamic parameters from task text (ParameterExtractor)   │
    │  2. Infer target app + package                                        │
    │  3. Cache lookup (TemplateCache)                                      │
    │     ├─ HIT  → inject params → execute → on success: return ✅        │
    │     │          on failure: fall through to LLM generation             │
    │     └─ MISS → continue                                                │
    │  4. Fetch a SINGLE UI snapshot for LLM context                       │
    │  5. LLM generates complete uiautomator2 script                       │
    │  6. Execute script                                                    │
    │     ├─ SUCCESS → extract placeholders → store template → return ✅   │
    │     └─ FAILURE → feed error to LLM → regenerate (max 2×)             │
    └───────────────────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        device_id:        str          = "default_device",
        uiautomator_host: str          = "http://localhost",
        uiautomator_port: int          = 9008,
        llm_provider:     str          = "cerebras",
        cache_file:       Optional[Path] = None,
    ) -> None:
        self.device_id            = device_id
        self.uiautomator_base_url = f"{uiautomator_host.rstrip('/')}:{uiautomator_port}"
        self.llm_provider         = (llm_provider or "cerebras").strip().lower()
        self.current_task:        Optional[MobileTaskRequest] = None
        self.token_usage:         Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls:     int            = 0
        # Backward-compatible fields expected by existing handler/coordinator code.
        self.action_history:      List[Dict[str, Any]] = []
        self.tier_stats:          Dict[str, int] = {
            "tier1_cache_hits": 0,
            "tier1_cache_misses": 0,
            "tier2_generations": 0,
            "tier3_retries": 0,
            "execution_attempts": 0,
        }

        # Sub-components
        self.cache           = TemplateCache(cache_file or CACHE_FILE)
        self.param_extractor = ParameterExtractor()
        self.placeholderizer = PlaceholderExtractor()
        self._executor:      Optional[CodeExecutor] = None  # resolved lazily per-task

        # LLM client
        self._init_llm()

        logger.info(
            f"✅ MobileCodeGenStrategy ready | device={device_id} | "
            f"llm={self.llm_provider} | {self.cache.stats()}"
        )

    def _init_llm(self) -> None:
        if self.llm_provider == "cerebras":
            try:
                from cerebras.cloud.sdk import Cerebras
                self.llm_client = Cerebras(api_key=os.getenv("CEREBRAS_API_KEY", ""))
                self.model      = "llama3.1-8b"
                logger.info("✅ Cerebras: llama3.1-8b")
            except ImportError:
                raise RuntimeError("Install cerebras-cloud-sdk: pip install cerebras-cloud-sdk")
        else:
            from groq import AsyncGroq
            self.llm_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", ""))
            self.model      = "llama3.1-8b"
            logger.info("✅ Groq: llama3.1-8b")

    # ── LLM helpers ────────────────────────────────────────────────────────

    async def _llm_chat(self, **kwargs: Any) -> Any:
        """Provider-agnostic async chat completion."""
        fn = self.llm_client.chat.completions.create
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        result = await asyncio.to_thread(lambda: fn(**kwargs))
        return await result if inspect.isawaitable(result) else result

    def _track_usage(self, response: Any) -> None:
        try:
            u = getattr(response, "usage", None)
            if u:
                self.token_usage["prompt"]     += int(getattr(u, "prompt_tokens",     0) or 0)
                self.token_usage["completion"] += int(getattr(u, "completion_tokens", 0) or 0)
                self.token_usage["total"]      += int(getattr(u, "total_tokens",      0) or 0)
            self.total_llm_calls += 1
        except Exception:
            pass

    # ── Device helpers ─────────────────────────────────────────────────────

    def _resolve_serial(self) -> str:
        """Resolve ADB serial from task context, env vars, or device_id."""
        logical: Set[str] = {"", "default_device", "android_device_1", "android_device"}
        candidates: List[str] = []

        if self.current_task:
            for src in [self.current_task.extra_params or {}, self.current_task.context or {}]:
                for key in ("adb_serial", "android_serial", "device_serial", "serial", "device_id"):
                    v = str(src.get(key) or "").strip()
                    if v:
                        candidates.append(v)

        for env in ("AURA_ANDROID_SERIAL", "ANDROID_SERIAL"):
            v = os.getenv(env, "").strip()
            if v:
                candidates.append(v)

        candidates.append((self.device_id or "").strip())

        # Deduplicate preserving order
        seen: Set[str] = set()
        candidates = [c for c in candidates if c and not (c in seen or seen.add(c))]

        # Query connected devices
        connected: List[str] = []
        try:
            import adbutils
            connected = [d.serial for d in adbutils.adb.device_list() if getattr(d, "serial", None)]
        except Exception:
            pass

        for c in candidates:
            if c in logical:
                continue
            if not connected or c in connected:
                return c

        if connected:
            for s in connected:
                if s.startswith("emulator-"):
                    return s
            return connected[0]

        return ""

    def _get_executor(self) -> CodeExecutor:
        if self._executor is None:
            self._executor = CodeExecutor(
                device_serial=self._resolve_serial(),
                timeout=CODE_EXEC_TIMEOUT,
            )
        return self._executor

    async def _fetch_xml_dump(self) -> str:
        """Fetch UIAutomator XML hierarchy from device — called once per task."""
        try:
            serial = self._resolve_serial()

            def _dump() -> str:
                dev = u2.connect(serial) if serial else u2.connect()
                return dev.dump_hierarchy()

            raw = await asyncio.wait_for(asyncio.to_thread(_dump), timeout=15.0)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace")
            return str(raw or "")
        except Exception as e:
            logger.warning(f"[DEVICE] XML dump failed: {e}")
            return ""

    # ── App inference ─────────────────────────────────────────────────────

    @staticmethod
    def _infer_app(text: str) -> Tuple[str, str]:
        """
        Return (canonical_app_name, package_name) from task text.
        Returns ("unknown", "") when no app can be determined.
        """
        t = text.lower()
        checks: List[Tuple[Tuple[str, ...], str]] = [
            (("gmail", "email", "compose", "recipient", "subject"),           "gmail"),
            (("alarm", "clock", "stopwatch", "timer"),                        "clock"),
            (("contacts", "contact", "call log"),                             "contacts"),
            (("play store", "install app", "google play", "download app"),    "play_store"),
            (("youtube", "watch video"),                                      "youtube"),
            (("maps", "directions", "navigate to", "location"),               "maps"),
            (("google docs", " docs ", "document"),                           "google docs"),
            (("google sheets", "spreadsheet"),                                "google sheets"),
            (("google slides", "presentation"),                               "google slides"),
            (("google calendar", "calendar", "schedule", "event"),            "google calendar"),
            (("google keep", "keep", "note"),                                 "google keep"),
            (("settings",),                                                   "settings"),
            (("chrome", "browser", "search the web", "google.com", "url"),    "chrome"),
            (("whatsapp",),                                                   "whatsapp"),
            (("spotify", "music", "playlist"),                                "spotify"),
        ]
        for keywords, app in checks:
            if any(k in t for k in keywords):
                return app, APP_PACKAGES.get(app, "")
        return "unknown", ""

    # ── Code generation ───────────────────────────────────────────────────

    def _build_user_prompt(
        self,
        task_text:    str,
        overall_goal: str,
        params:       TaskParameters,
        app:          str,
        package:      str,
        ui_snapshot:  str,
        error_ctx:    str = "",
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        # Collect non-internal extra params for context
        extra_lines: List[str] = []
        for k, v in (extra_params or {}).items():
            if k in _INTERNAL_KEYS or v is None:
                continue
            extra_lines.append(f"  {k.upper()} = {str(v)!r}")

        extra_section = (
            "\nADDITIONAL CONTEXT:\n" + "\n".join(extra_lines) + "\n"
            if extra_lines else ""
        )
        error_section = (
            f"\n{'━'*50}\n"
            f"PREVIOUS ATTEMPT FAILED — fix this error:\n"
            f"{error_ctx}\n"
            f"{'━'*50}\n"
        ) if error_ctx else ""

        return (
            f"TASK:          {task_text}\n"
            f"OVERALL GOAL:  {overall_goal}\n"
            f"TARGET APP:    {app}  (package: {package or 'unknown'})\n"
            f"\nEXTRACTED PARAMETERS (use these exact values):\n"
            f"{params.describe()}\n"
            f"{extra_section}"
            f"{error_section}"
            f"\nCURRENT SCREEN AT TASK START:\n"
            f"{ui_snapshot}\n"
            f"\nWrite the complete uiautomator2 script. "
            f"Raw Python only — no markdown, no commentary."
        )

    @staticmethod
    def _clean_generated_code(raw: str) -> str:
        """Strip markdown fences and leading/trailing whitespace."""
        raw = re.sub(r"^```(?:python|py)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"\n?```\s*$", "", raw.strip(), flags=re.MULTILINE)
        return raw.strip()

    async def _generate_code(
        self,
        task_text:    str,
        overall_goal: str,
        params:       TaskParameters,
        app:          str,
        package:      str,
        ui_snapshot:  str,
        error_ctx:    str = "",
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        user_prompt = self._build_user_prompt(
            task_text, overall_goal, params, app, package,
            ui_snapshot, error_ctx, extra_params,
        )
        logger.info(
            f"[LLM] Generating code | app={app} | "
            f"params={list(params.raw.keys())} | retry={'yes' if error_ctx else 'no'}"
        )
        logger.debug(f"[LLM] USER_PROMPT:\n{user_prompt}")

        response = await self._llm_chat(
            model=self.model,
            messages=[
                {"role": "system", "content": _CODEGEN_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=900,
        )
        self._track_usage(response)
        raw  = response.choices[0].message.content or ""
        code = self._clean_generated_code(raw)

        logger.info(f"[LLM] Generated {len(code.splitlines())} lines")
        logger.debug(f"[LLM] CODE:\n{code}")
        return code

    # ── Error formatting for retry context ────────────────────────────────

    @staticmethod
    def _error_context(outcome: ExecutionOutcome) -> str:
        parts: List[str] = []
        if outcome.error_summary:
            parts.append(f"Error: {outcome.error_summary}")
        if outcome.stderr:
            tail = "\n".join(outcome.stderr.splitlines()[-20:])
            parts.append(f"Traceback (tail):\n{tail}")
        return "\n".join(parts)

    # ── Template storage ──────────────────────────────────────────────────

    @staticmethod
    def _template_id(task_text: str, app: str) -> str:
        return hashlib.sha1(f"{app}:{task_text}".encode()).hexdigest()

    def _normalise_task_pattern(self, task_text: str, params: TaskParameters) -> str:
        """Replace concrete dynamic values in task_text with {placeholder} names."""
        pattern = task_text
        for k, v in sorted(params.raw.items(), key=lambda kv: -len(str(kv[1]))):
            pattern = pattern.replace(str(v), f"{{{k}}}")
        return pattern

    def _store_successful_code(
        self,
        code:      str,
        task_text: str,
        app:       str,
        package:   str,
        params:    TaskParameters,
    ) -> None:
        template_code, schema = self.placeholderizer.extract(code, params, task_text)
        task_pattern          = self._normalise_task_pattern(task_text, params)
        tid                   = self._template_id(task_text, app)

        existing = self.cache._store.get(tid)
        if existing:
            existing.success_count   += 1
            existing.code_template    = template_code   # replace with freshest code
            existing.parameter_schema.update(schema)
            existing.last_used        = datetime.utcnow().isoformat()
            self.cache._save()
            logger.info(
                f"[CACHE] Updated template {tid[:8]} "
                f"(success_count={existing.success_count})"
            )
            return

        template = CodeTemplate(
            template_id      = tid,
            task_pattern     = task_pattern,
            app              = app,
            package          = package,
            code_template    = template_code,
            parameter_schema = schema,
            success_count    = 1,
        )
        self.cache.add(template)

    # ── Main entry point ──────────────────────────────────────────────────

    async def execute_task(self, task: MobileTaskRequest) -> MobileTaskResult:
        self.current_task = task
        self._executor    = None  # reset per task so serial is re-resolved
        t0                = time.monotonic()
        self.action_history = []

        overall_goal: str = (
            task.extra_params.get("overall_goal")
            or task.extra_params.get("goal")
            or (task.context or {}).get("overall_goal")
            or task.ai_prompt
        )

        logger.info(
            f"\n{'='*70}\n🎯 CODEGEN TASK START\n"
            f"   task   : {task.ai_prompt}\n"
            f"   goal   : {overall_goal}\n"
            f"   device : {task.device_id}\n"
            f"{'='*70}"
        )

        # ── 1. Extract parameters ─────────────────────────────────────────
        params = ParameterExtractor.extract(task.ai_prompt, task.extra_params)
        logger.info(f"[PARAMS] {params}")

        # ── 2. Infer app ──────────────────────────────────────────────────
        app, package = self._infer_app(f"{task.ai_prompt} {overall_goal}")
        explicit_app = (task.extra_params.get("app_name") or "").strip().lower()
        if explicit_app and explicit_app in APP_PACKAGES:
            app     = explicit_app
            package = APP_PACKAGES[app]
        logger.info(f"[APP] app={app!r} | package={package!r}")

        executor = self._get_executor()

        # ── 3. Cache lookup ───────────────────────────────────────────────
        cache_hit = self.cache.lookup(task.ai_prompt, app)
        if cache_hit:
            self.tier_stats["tier1_cache_hits"] += 1
            template, score = cache_hit
            missing = params.missing_keys(template.code_template)
            if not missing:
                logger.info(
                    f"[T1] Cache hit (sim={score:.2f}) — "
                    f"injecting {len(params.raw)} params → executing"
                )
                injected = params.inject(template.code_template)
                self.action_history.append({
                    "stage": "tier1_cache_execute",
                    "similarity": round(score, 3),
                })
                self.tier_stats["execution_attempts"] += 1
                outcome  = await executor.execute(injected)
                if outcome.success:
                    self.cache.mark_success(template.template_id)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    logger.info(f"✅ [T1] Cache hit success | {elapsed_ms}ms")
                    return self._build_result(
                        task.task_id, "success", 1, elapsed_ms,
                        completion_reason=f"Template cache (sim={score:.2f})",
                    )
                else:
                    logger.warning(
                        f"[T1] Cached code failed: {outcome.error_summary} "
                        f"— falling through to LLM"
                    )
                    self.action_history.append({
                        "stage": "tier1_cache_failed",
                        "error": outcome.error_summary,
                    })
                    self.cache.mark_failure(template.template_id)
            else:
                logger.info(
                    f"[T1] Cache hit but missing params {missing} "
                    f"— cannot inject, falling through to LLM"
                )
                self.action_history.append({
                    "stage": "tier1_cache_missing_params",
                    "missing": missing,
                })
        else:
            self.tier_stats["tier1_cache_misses"] += 1

        # ── 4. Fetch UI snapshot (once) ───────────────────────────────────
        logger.info("[DEVICE] Fetching initial UI snapshot …")
        await asyncio.sleep(1.0)
        xml_dump    = await self._fetch_xml_dump()
        ui_snapshot = build_ui_snapshot(xml_dump)
        logger.info(f"[DEVICE] UI snapshot: {len(ui_snapshot.splitlines())} elements")

        # ── 5. LLM generate + execute with retry ──────────────────────────
        code      = ""
        error_ctx = ""
        outcome:  Optional[ExecutionOutcome] = None
        last_failure_reason = ""

        for attempt in range(1 + MAX_CODE_RETRIES):
            label = f"attempt {attempt + 1}/{1 + MAX_CODE_RETRIES}"
            try:
                self.tier_stats["tier2_generations"] += 1
                if attempt > 0:
                    self.tier_stats["tier3_retries"] += 1
                code = await self._generate_code(
                    task_text    = task.ai_prompt,
                    overall_goal = overall_goal,
                    params       = params,
                    app          = app,
                    package      = package,
                    ui_snapshot  = ui_snapshot,
                    error_ctx    = error_ctx,
                    extra_params = task.extra_params,
                )
            except Exception as e:
                logger.error(f"[LLM] Generation error ({label}): {e}")
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                return self._build_error_result(task.task_id, f"LLM error: {e}", elapsed_ms)

            if not code.strip():
                logger.warning(f"[LLM] Empty code response ({label})")
                self.action_history.append({"stage": "llm_empty", "attempt": attempt + 1})
                last_failure_reason = "LLM returned empty code"
                error_ctx = "You returned empty code. Output a complete Python script."
                continue

            logger.info(f"[T2/T3] Executing — {label}")
            self.tier_stats["execution_attempts"] += 1
            self.action_history.append({"stage": "execute", "attempt": attempt + 1})
            outcome = await executor.execute(code)

            if outcome.success:
                logger.info(f"✅ [T2] Code executed successfully ({label})")
                break

            # Feed error back into next generation attempt
            error_ctx = self._error_context(outcome)
            last_failure_reason = outcome.error_summary or "Execution failed"
            self.action_history.append({
                "stage": "execute_failed",
                "attempt": attempt + 1,
                "error": outcome.error_summary,
            })
            logger.warning(
                f"[T2/T3] Execution failed ({label}): {outcome.error_summary}"
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if outcome and outcome.success:
            # ── 6. Store working code as template ─────────────────────────
            try:
                self._store_successful_code(code, task.ai_prompt, app, package, params)
            except Exception as e:
                logger.warning(f"[CACHE] Template storage failed (non-fatal): {e}")

            return self._build_result(
                task.task_id, "success", 1, elapsed_ms,
                completion_reason=f"Code generated and executed in {elapsed_ms}ms",
            )

        err = outcome.error_summary if outcome else (last_failure_reason or "No execution outcome")
        logger.error(f"❌ Task failed after all attempts: {err}")
        return self._build_error_result(task.task_id, err, elapsed_ms)

    # ── Result builders ────────────────────────────────────────────────────

    def _log_metrics(self, status: str, elapsed_ms: int) -> None:
        logger.info(
            f"\n{'='*60}\n"
            f"📊 {'✅' if status == 'success' else '❌'} {status.upper()} | "
            f"{elapsed_ms}ms | "
            f"llm_calls={self.total_llm_calls} | "
            f"tokens={self.token_usage} | "
            f"{self.cache.stats()}\n"
            f"{'='*60}"
        )

    def _build_result(
        self,
        task_id:           str,
        status:            str,
        steps:             int,
        elapsed_ms:        int,
        completion_reason: str = "",
        error:             str = "",
    ) -> MobileTaskResult:
        self._log_metrics(status, elapsed_ms)
        return MobileTaskResult(
            task_id           = task_id,
            status            = status,
            steps_taken       = steps,
            actions_executed  = [],
            execution_time_ms = elapsed_ms,
            error             = error or None,
            completion_reason = completion_reason or None,
            token_usage       = dict(self.token_usage),
            llm_calls         = self.total_llm_calls,
        )

    def _build_error_result(
        self,
        task_id:    str,
        error:      str,
        elapsed_ms: int = 0,
    ) -> MobileTaskResult:
        return self._build_result(task_id, "failed", 0, elapsed_ms, error=error)


# ══════════════════════════════════════════════════════════════════════════════
#  BACKWARD COMPATIBILITY
# ══════════════════════════════════════════════════════════════════════════════

#: Drop-in alias so existing coordinator code doesn't need changes
MobileStrategy       = MobileCodeGenStrategy
MobileReActStrategy  = MobileCodeGenStrategy


# ══════════════════════════════════════════════════════════════════════════════
#  INTEGRATION FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

async def execute_mobile_task(
    task:      Dict[str, Any],
    device_id: str = "emulator-5554",
) -> ExecutionResult:
    """
    Top-level entry point matching the original signature.
    Creates a MobileCodeGenStrategy, runs the task, and returns an ExecutionResult.
    """
    try:
        extra_params = dict(task.get("extra_params", {}) or {})
        if not extra_params.get("overall_goal"):
            extra_params["overall_goal"] = task.get("goal") or task.get("ai_prompt", "")

        uiautomator_port = int(
            extra_params.get("uiautomator_port")
            or task.get("uiautomator_port", 9008)
        )
        timeout = int(task.get("timeout_seconds", CODE_EXEC_TIMEOUT))

        mobile_task = MobileTaskRequest(
            task_id         = task.get("task_id",   "unknown"),
            ai_prompt       = task.get("ai_prompt", ""),
            device_id       = device_id,
            session_id      = task.get("session_id", "default"),
            context         = extra_params,
            extra_params    = extra_params,
            max_steps       = 1,         # not used in code-gen flow
            timeout_seconds = timeout,
        )

        strategy = MobileCodeGenStrategy(
            device_id        = device_id,
            uiautomator_port = uiautomator_port,
        )
        result = await strategy.execute_task(mobile_task)

        return ExecutionResult(
            status    = "success" if result.status == "success" else "failed",
            task_id   = result.task_id,
            context   = "mobile",
            action    = "codegen",
            details   = result.completion_reason or result.error or "",
            logs      = [],
            timestamp = datetime.now().isoformat(),
            duration  = result.execution_time_ms / 1000.0,
            metadata  = {
                "token_usage": result.token_usage or {},
                "llm_calls":   result.llm_calls,
                "steps_taken": result.steps_taken,
            },
            error = result.error,
        )
    except Exception as e:
        logger.error(f"❌ execute_mobile_task: {e}", exc_info=True)
        return ExecutionResult(
            status    = "failed",
            task_id   = task.get("task_id", "unknown"),
            context   = "mobile",
            action    = "codegen",
            details   = "",
            logs      = [],
            timestamp = datetime.now().isoformat(),
            duration  = 0.0,
            error     = str(e),
        )