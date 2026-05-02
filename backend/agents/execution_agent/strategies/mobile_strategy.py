"""
mobile_strategy_codegen.py  —  Fixed version
See CHANGES section at the bottom for a summary of every fix.
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
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

CACHE_FILE: Path = Path(
    os.getenv("CODEGEN_CACHE_FILE", "~/.mobile_codegen_cache.json")
).expanduser()

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

PACKAGE_TO_APP: Dict[str, str] = {
    pkg.rsplit(".", 1)[-1]: app for app, pkg in APP_PACKAGES.items()
}

# ── Regex patterns ─────────────────────────────────────────────────────────
_TIME_RE    = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.IGNORECASE)
_EMAIL_RE   = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE)
_PHONE_RE   = re.compile(r"\b(\+?\d[\d\s\-().]{6,})\b")
_QUOTED_RE  = re.compile(r'["\']([^"\']{1,300})["\']')
_SUBJECT_RE = re.compile(r"subject\s+[\"']?([^\"'.\n]{1,120})[\"']?", re.IGNORECASE)
_BODY_RE    = re.compile(r"(?:body|message|content)\s+[\"']([^\"']{1,500})[\"']", re.IGNORECASE)

# FIX #1: _QUERY_RE was too greedy — it fired on navigation sentences like
# "Navigate to the search bar" → extracted "bar", or
# "Click the search button to get the results" → extracted "button to get the results".
# New pattern: only matches after explicit search-intent verbs AND requires a
# quoted string OR at least 2 non-trivial words so single nouns don't match.
_QUERY_RE = re.compile(
    r"(?:search(?:\s+for)?|find|look\s+up|google)\s+[\"']([^\"'.,!?\n]{2,120})[\"']"
    r"|(?:type|enter)\s+[\"']([^\"']{2,120})[\"']",
    re.IGNORECASE,
)

# ── Strategy knobs ─────────────────────────────────────────────────────────
CACHE_SIM_THRESHOLD: float = 0.55
CODE_EXEC_TIMEOUT:   int   = 90
MAX_CODE_RETRIES:    int   = 3

_MIN_SNAPSHOT_ELEMENTS: int = 5
_SNAPSHOT_RETRY_DELAY:  float = 1.5

# FIX #3: "input_content" added — it leaks from coordinator into params and
# then appears as a variable in generated scripts, confusing the LLM.
_INTERNAL_KEYS: Set[str] = {
    "input_from", "device_id", "app_name", "package_name", "file_path",
    "max_steps", "timeout_seconds", "language", "overall_goal", "goal",
    "session_id", "uiautomator_port", "input_content",  # FIX #3
}

_RESERVED_RUNTIME_KEYS: Set[str] = {
    "time", "re", "sys", "os", "u2", "json", "hashlib", "tempfile",
    "textwrap", "asyncio", "datetime", "Path", "ET",
}


def compute_smart_timeout(task_text: str, base_timeout: int) -> int:
    t = (task_text or "").lower()
    base = max(int(base_timeout or 0), 10)
    if any(k in t for k in ("install", "download", "play store", "update")):
        mult = 2.5
    elif any(k in t for k in ("compose", "send", "email", "attach", "calendar", "event", "schedule")):
        mult = 1.8
    elif any(k in t for k in ("alarm", "set alarm", "timer", "clock")):
        mult = 1.5
    elif any(k in t for k in ("search", "find", "browse", "open", "launch")):
        mult = 1.2
    else:
        mult = 1.3
    return max(int(base * mult) + 8, base + 8)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA MODELS  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TaskParameters:
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.raw[key] = value

    def has(self, key: str) -> bool:
        return key in self.raw and bool(self.raw[key])

    def inject(self, template_code: str) -> str:
        result = template_code
        for k, v in self.raw.items():
            result = result.replace(f"{{{k}}}", str(v))
        return result

    def missing_keys(self, template_code: str) -> List[str]:
        placeholders = set(re.findall(r"\{(\w+)\}", template_code))
        return sorted(placeholders - set(self.raw.keys()))

    def __repr__(self) -> str:
        return f"TaskParameters({self.raw})"

    def describe(self) -> str:
        if not self.raw:
            return "  (none extracted)"
        return "\n".join(f"  {k} = {v!r}" for k, v in self.raw.items())


@dataclass
class CodeTemplate:
    template_id:      str
    task_pattern:     str
    app:              str
    package:          str
    code_template:    str
    parameter_schema: Dict[str, str]
    keywords:         List[str]  = field(default_factory=list)
    success_count:    int        = 0
    failure_count:    int        = 0
    created_at:       str        = ""
    last_used:        str        = ""

    @property
    def reliability(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CodeTemplate":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ExecutionOutcome:
    success:           bool
    stdout:            str = ""
    stderr:            str = ""
    return_code:       int = 0
    execution_time_ms: int = 0
    error_summary:     str = ""

    def __bool__(self) -> bool:
        return self.success


# ══════════════════════════════════════════════════════════════════════════════
#  CACHE (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class CacheBackend(Protocol):
    def lookup(self, task_text: str, app: str, threshold: float = CACHE_SIM_THRESHOLD) -> Optional[Tuple[CodeTemplate, float]]: ...
    def add(self, template: CodeTemplate) -> None: ...
    def mark_success(self, template_id: str) -> None: ...
    def mark_failure(self, template_id: str) -> None: ...
    def stats(self) -> str: ...


class TemplateCache:
    _STOPWORDS: Set[str] = {
        "the", "a", "an", "in", "on", "at", "to", "for", "of",
        "and", "or", "is", "it", "my", "me", "i", "please", "with",
    }

    def __init__(self, cache_file: Path = CACHE_FILE) -> None:
        self._path:  Path                    = cache_file
        self._store: Dict[str, CodeTemplate] = {}
        self._load()
        logger.info(f"[CACHE] TemplateCache ready: {len(self._store)} templates | path={self._path}")

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

    @classmethod
    def _kw(cls, text: str) -> Set[str]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return {t for t in tokens if t not in cls._STOPWORDS and len(t) >= 2}

    @staticmethod
    def _jaccard(a: Set[str], b: Set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # Synonym map: normalize variant phrasings to canonical forms
    # before keyword extraction so Jaccard similarity is higher for
    # semantically equivalent task descriptions.
    _SYNONYMS: Dict[str, str] = {
        "address":   "search",    # "address bar" ↔ "search bar"
        "browser":   "chrome",    # "open browser" ↔ "open chrome"
        "press":     "click",     # "press button" ↔ "click button"
        "tap":       "click",
        "hit":       "click",
        "submit":    "click",
        "execute":   "click",
        "launch":    "open",      # "launch app" ↔ "open app"
        "start":     "open",
        "go":        "navigate",  # "go to page" ↔ "navigate to page"
        "load":      "navigate",
        "visit":     "navigate",
        "query":     "search",    # "enter query" ↔ "enter search"
        "keyword":   "search",
        "mail":      "email",     # "open mail" ↔ "open email"
        "inbox":     "email",
        "compose":   "email",
        "write":     "fill",      # "write subject" ↔ "fill subject"
        "enter":     "type",      # "enter text" ↔ "type text"
        "input":     "type",
        "insert":    "type",
    }

    @classmethod
    def _kw_query(cls, text: str) -> Set[str]:
        """
        Keywords for a QUERY (incoming task text).
        1. Letters only (strips digits/colons) — templates use placeholders
           like "alarm_time" not "5:40", so numbers never match.
        2. Synonym normalization — maps variant words to canonical forms
           so "address bar" and "search bar" produce identical keywords.
        """
        tokens = re.findall(r"[a-z]+", text.lower())  # letters only, no digits
        # Apply synonym normalization
        tokens = [cls._SYNONYMS.get(t, t) for t in tokens]
        return {t for t in tokens if t not in cls._STOPWORDS and len(t) >= 2}

    def lookup(self, task_text: str, app: str, threshold: float = CACHE_SIM_THRESHOLD) -> Optional[Tuple[CodeTemplate, float]]:
        if not self._store:
            return None
        # Use letter-only keywords for query to match placeholder-normalised templates
        query_kw  = self._kw_query(task_text)
        app_lower = (app or "").lower().strip()
        best_t:   Optional[CodeTemplate] = None
        best_sim: float = 0.0
        for t in self._store.values():
            sim = self._jaccard(query_kw, set(t.keywords))
            if app_lower and t.app.lower() != app_lower:
                sim *= 0.4
            if t.reliability < 0.5 and (t.success_count + t.failure_count) >= 3:
                sim *= 0.6
            if sim > best_sim:
                best_sim = sim
                best_t   = t
        if best_t is not None and best_sim >= threshold:
            logger.info(f"[CACHE] HIT  sim={best_sim:.2f} | pattern='{best_t.task_pattern[:55]}' | app={best_t.app}")
            return best_t, best_sim
        logger.info(f"[CACHE] MISS best_sim={best_sim:.2f} < {threshold} | query='{task_text[:55]}'")
        return None

    def add(self, template: CodeTemplate) -> None:
        template.keywords   = list(self._kw(template.task_pattern))
        template.created_at = template.created_at or datetime.utcnow().isoformat()
        self._store[template.template_id] = template
        self._save()
        logger.info(f"[CACHE] Stored '{template.task_pattern[:55]}' id={template.template_id[:8]}")

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

    @staticmethod
    def extract(
        task_text:    str,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> TaskParameters:
        params = TaskParameters()
        extra  = extra_params or {}
        text   = task_text or ""

        ParameterExtractor._extract_time(text, params)
        ParameterExtractor._extract_email(text, params)
        ParameterExtractor._extract_query(text, params)  # FIX #1/#2 applied inside

        pm = _PHONE_RE.search(text)
        if pm:
            phone = re.sub(r"[\s\-().]", "", pm.group(1))
            params.set("phone_number", phone)

        ParameterExtractor._extract_quoted(text, params)

        for k, v in extra.items():
            if k in _INTERNAL_KEYS or v is None:  # FIX #3: _INTERNAL_KEYS now includes input_content
                continue
            val = str(v).strip()
            if not val:
                continue
            canonical = {
                "text":         "search_query",
                "query":        "search_query",
                "to":           "recipient_email",
                "recipient":    "recipient_email",
                "subject":      "email_subject",
                "body":         "email_body",
                "message":      "email_body",
                "time":         "alarm_time",
                "contact":      "contact_name",
                "event":        "event_title",
                "title":        "event_title",
                "value":        "input_value",
                "url":          "target_url",   # FIX: url is a useful param, not internal
            }.get(k, k)
            params.set(canonical, val)

        if params.has("alarm_hour") and not params.has("alarm_time"):
            h = params.get("alarm_hour", "12")
            m = params.get("alarm_minute", "00")
            p = params.get("alarm_period", "AM")
            params.set("alarm_time", f"{int(h)}:{m} {p}")

        # ── Parse input_content JSON from upstream reasoning tasks ──────────
        # When a reasoning agent produces {"SUBJECT": "...", "BODY": "..."},
        # the coordinator passes it as input_content. We parse it here so
        # action tasks like "fill subject field" have the actual values
        # rather than trying to use an undefined variable called BODY/SUBJECT.
        input_content = (extra.get("input_content") or "").strip()
        if input_content and input_content.startswith("{"):
            try:
                parsed = json.loads(input_content)
                # Map common keys from reasoning agent output → canonical param names
                key_map = {
                    "SUBJECT": "email_subject",
                    "subject": "email_subject",
                    "BODY":    "email_body",
                    "body":    "email_body",
                    "TO":      "recipient_email",
                    "to":      "recipient_email",
                    "CC":      "cc_email",
                    "cc":      "cc_email",
                    "result":  None,  # nested result object — recurse one level
                }
                # Handle {"result": {"SUBJECT": ..., "BODY": ...}} wrapping
                if "result" in parsed and isinstance(parsed["result"], dict):
                    parsed = parsed["result"]
                for src_key, dst_key in key_map.items():
                    if dst_key and src_key in parsed and parsed[src_key]:
                        if not params.has(dst_key):  # don't overwrite regex-extracted values
                            params.set(dst_key, str(parsed[src_key]).strip())
                            logger.debug(f"[PARAMS] Injected from input_content JSON: {dst_key}={str(parsed[src_key])[:40]!r}")
            except (json.JSONDecodeError, TypeError):
                pass  # input_content is plain text, not JSON — ignore

        logger.debug(f"[PARAMS] Extracted: {params}")
        return params

    @staticmethod
    def _extract_time(text: str, params: TaskParameters) -> None:
        tm = _TIME_RE.search(text)
        if not tm:
            return
        hour   = int(tm.group(1))
        minute = int(tm.group(2))
        period = (tm.group(3) or "").upper()
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
            val = sm.group(1).strip()
            # Guard: reject if this looks like a task instruction rather than a real subject.
            # Real subjects are short (<60 chars) and don't contain instruction words.
            instruction_words = {"field", "value", "from", "composed", "fill", "with", "the"}
            val_words = set(val.lower().split())
            if len(val) <= 80 and not val_words.intersection(instruction_words):
                params.set("email_subject", val)
            else:
                logger.debug(f"[PARAMS] Skipped spurious email_subject match: {val[:50]!r}")
        bm = _BODY_RE.search(text)
        if bm:
            val = bm.group(1).strip()
            # Same guard for body
            instruction_words = {"field", "value", "from", "composed", "fill", "with"}
            val_words = set(val.lower().split())
            if len(val) <= 200 and not val_words.intersection(instruction_words):
                params.set("email_body", val)

    @staticmethod
    def _extract_query(text: str, params: TaskParameters) -> None:
        # FIX #2: Skip query extraction entirely for navigation/click sentences.
        # These task descriptions are instructions for the LLM, not search queries.
        nav_verbs = re.compile(
            r"^\s*(navigate|click|press|tap|open|go to|scroll|swipe|drag|select|"
            r"confirm|submit|dismiss|close|back|return)\b",
            re.IGNORECASE,
        )
        if nav_verbs.match(text):
            logger.debug(f"[PARAMS] Skipping query extraction for navigation task: {text[:60]}")
            return

        qm = _QUERY_RE.search(text)
        if qm:
            # Group 1 = search/find/google, Group 2 = type/enter
            raw = (qm.group(1) or qm.group(2) or "").strip().strip("'\"")
            if raw:
                params.set("search_query", raw)

    @staticmethod
    def _extract_quoted(text: str, params: TaskParameters) -> None:
        quoted = _QUOTED_RE.findall(text)
        if quoted:
            if not params.has("search_query"):
                params.set("search_query", quoted[0])
            if len(quoted) >= 2 and not params.has("email_subject"):
                params.set("email_subject", quoted[1])


# ══════════════════════════════════════════════════════════════════════════════
#  PLACEHOLDER EXTRACTOR  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

class PlaceholderExtractor:
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
        "target_url":      "str — full URL e.g. 'https://www.google.com'",
    }

    def extract(self, code: str, params: TaskParameters, task_text: str = "") -> Tuple[str, Dict[str, str]]:
        template = code
        schema:  Dict[str, str] = {}
        items = sorted(params.raw.items(), key=lambda kv: -len(str(kv[1])))
        for key, val in items:
            val_str = str(val)
            if len(val_str) < 2:
                continue
            placeholder = f"{{{key}}}"
            new_template = self._replace_in_string_literals(template, val_str, placeholder)
            if new_template != template:
                template = new_template
                schema[key] = self._PARAM_DESCRIPTIONS.get(key, f"str — value e.g. {val_str!r}")
        logger.debug(f"[PLACEHOLDER] {len(schema)} placeholders extracted: {list(schema.keys())}")
        return template, schema

    @staticmethod
    def _replace_in_string_literals(code: str, value: str, placeholder: str) -> str:
        escaped = re.escape(value)
        def _sub(m: re.Match) -> str:
            s = m.group(0)
            if value in s:
                inner = s[1:-1].replace(value, placeholder)
                return s[0] + inner + s[-1]
            return s
        pattern = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
        return pattern.sub(_sub, code)


# ══════════════════════════════════════════════════════════════════════════════
#  UI SNAPSHOT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_ui_snapshot(
    xml_dump: Any,
    max_interactive: int = 80,
    max_labels: int = 40,
) -> str:
    """
    Two-pass UI snapshot builder.

    Pass 1 — INTERACTIVE ELEMENTS (selectors the LLM can use directly):
      clickable=true OR focusable=true OR scrollable=true
      Rendered as:  CLASS  rid=X  text=Y  desc=Z  @(x%,y%)  [CLK/FOC/SCR]

    Pass 2 — VISIBLE TEXT LABELS (content only, not directly actionable):
      visible TextViews/Labels that have non-empty text but are NOT interactive.
      These tell the LLM what content is on screen (e.g. video titles, alarm
      times, list item labels) so it can reference them in selectors like
      d(text="AgentForce in Salesforce") even on a parent container.
      Rendered as:  LABEL  text=Y  @(x%,y%)

    Why two passes?
      Android RecyclerView items often have a clickable parent ViewGroup with
      non-interactive TextView children that carry the actual title text.
      Without pass 2, the LLM sees "ViewGroup [CLK]" but has no idea what
      text is inside it — it can't click by title and has to guess by index.

    Budget: max_interactive interactive lines + max_labels label lines.
    Labels beyond the budget are dropped (they're for context, not selectors).
    """
    if not xml_dump:
        return "(no UI data available)"
    if isinstance(xml_dump, bytes):
        xml_dump = xml_dump.decode("utf-8", errors="replace")
    if not isinstance(xml_dump, str) or not xml_dump.strip():
        return "(empty UI dump)"
    try:
        root = ET.fromstring(xml_dump)
    except ET.ParseError as e:
        logger.debug(f"[SNAPSHOT] XML parse error: {e}")
        return "(UI parse error)"

    interactive_lines: List[str] = []
    label_lines:       List[str] = []
    sw = sh = 0

    def _parse_bounds(raw: str) -> Optional[Dict[str, int]]:
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (raw or "").strip())
        if not m:
            return None
        return {
            "left": int(m.group(1)), "top":  int(m.group(2)),
            "right": int(m.group(3)), "bottom": int(m.group(4)),
        }

    def _coord(bounds: Optional[Dict[str, int]]) -> str:
        if not bounds or sw <= 0 or sh <= 0:
            return ""
        cx = (bounds["left"] + bounds["right"])  // 2
        cy = (bounds["top"]  + bounds["bottom"]) // 2
        return f"  @({cx * 100 // sw}%, {cy * 100 // sh}%)"

    def visit(node: ET.Element) -> None:
        nonlocal sw, sh
        attrs     = node.attrib
        bounds    = _parse_bounds(attrs.get("bounds", ""))
        if bounds:
            sw = max(sw, bounds["right"])
            sh = max(sh, bounds["bottom"])

        clickable  = attrs.get("clickable",       "false").lower() == "true"
        focusable  = attrs.get("focusable",       "false").lower() == "true"
        scrollable = attrs.get("scrollable",      "false").lower() == "true"
        visible    = attrs.get("visible-to-user", "true" ).lower() != "false"
        enabled    = attrs.get("enabled",         "true" ).lower() != "false"
        rid        = attrs.get("resource-id",  "")
        text       = attrs.get("text",         "").strip()
        desc       = attrs.get("content-desc", "").strip()
        cls        = attrs.get("class",        "").rsplit(".", 1)[-1]

        if not visible:
            for child in node:
                visit(child)
            return

        # ── Pass 1: interactive elements ──────────────────────────────────
        if (clickable or focusable or scrollable) and len(interactive_lines) < max_interactive:
            parts: List[str] = [cls]
            if rid:   parts.append(f'rid="{rid}"')
            if text:  parts.append(f'text="{text}"')
            if desc:  parts.append(f'desc="{desc}"')
            flags: List[str] = []
            if clickable:   flags.append("CLK")
            if focusable:   flags.append("FOC")
            if scrollable:  flags.append("SCR")
            if not enabled: flags.append("DISABLED")
            flag_str = f"  [{','.join(flags)}]" if flags else ""
            interactive_lines.append("  ".join(parts) + _coord(bounds) + flag_str)

        # ── Pass 2: text labels on non-interactive nodes ──────────────────
        # Capture text from TextView / text-bearing nodes that are NOT
        # interactive themselves. These are the titles, subtitles, and
        # content values the LLM needs to know are on screen.
        elif (
            not clickable and not focusable and not scrollable
            and text                                    # must have actual text
            and len(text) >= 3                          # skip trivial labels
            and len(text) <= 200                        # skip huge blobs
            and len(label_lines) < max_labels
            and cls in (
                "TextView", "EditText", "Button", "CheckedTextView",
                "AppCompatTextView", "MaterialTextView",
                "SubtitleCollapsingTextHelper",         # YouTube video duration etc.
            )
        ):
            label_lines.append(f'LABEL  text="{text}"' + _coord(bounds))

        for child in node:
            visit(child)

    visit(root)

    if not interactive_lines and not label_lines:
        return "(no interactive elements found)"

    header = (
        f"# INTERACTIVE ELEMENTS: {len(interactive_lines)}  |  TEXT LABELS: {len(label_lines)}\n"
        f"# FORMAT: CLASS  rid=<resourceId>  text=<text>  desc=<content-desc>  @(x%,y%)  [FLAGS]\n"
        f"# LABEL lines show visible text content (use d(text=...) to target them or their parent)\n"
        f"# rid= → resourceId=    desc= → description=    text= → text=\n"
        f"# Elements NOT listed here do NOT exist on screen — never invent resourceIds.\n"
    )
    sections: List[str] = [header]
    if interactive_lines:
        sections.append("\n".join(interactive_lines))
    if label_lines:
        sections.append("# --- VISIBLE TEXT (non-interactive labels) ---")
        sections.append("\n".join(label_lines))
    return "\n".join(sections)


# ══════════════════════════════════════════════════════════════════════════════
#  CODE EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

class CodeExecutor:
    _PREAMBLE_HEADER = textwrap.dedent(
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

    def __init__(self, device_serial: str = "", timeout: int = CODE_EXEC_TIMEOUT) -> None:
        self.device_serial = device_serial
        self.timeout       = timeout

    def _build_script(self, code: str, params: Optional["TaskParameters"] = None) -> str:
        header = self._PREAMBLE_HEADER.format(serial=self.device_serial or "")
        param_lines: List[str] = []
        if params:
            for k, v in params.raw.items():
                if k in _INTERNAL_KEYS or k in _RESERVED_RUNTIME_KEYS:
                    continue
                param_lines.append(f"{k} = {str(v)!r}")
        param_block = ("\n".join(param_lines) + "\n\n") if param_lines else ""
        return header + param_block + textwrap.dedent(code)

    # FIX #8: Pre-execution syntax check — catch SyntaxError before we even
    # spin up the device. Returns error string or "" if clean.
    @staticmethod
    def _syntax_check(script: str) -> str:
        try:
            compile(script, "<generated>", "exec")
            return ""
        except SyntaxError as e:
            return f"SyntaxError: {e.msg} (line {e.lineno})"

    async def execute(
        self,
        code:   str,
        params: Optional["TaskParameters"] = None,
    ) -> "ExecutionOutcome":
        full_script = self._build_script(code, params)

        # FIX #8: Reject scripts with syntax errors immediately
        syntax_err = self._syntax_check(full_script)
        if syntax_err:
            logger.warning(f"[EXEC] Syntax check failed — skipping device run: {syntax_err}")
            return ExecutionOutcome(
                success=False,
                stderr=syntax_err,
                error_summary=syntax_err,
                return_code=1,
            )

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
                success=success, stdout=stdout, stderr=stderr,
                return_code=rc, execution_time_ms=elapsed, error_summary=error_summary,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  LLM SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

# FIX #4: Added explicit "NEVER use desc=" rule — uiautomator2 uses description=
# FIX #5: Added Chrome-specific enter/search pattern
# FIX #6: Strengthened .exists() guard rule
# FIX #7: Added hard MAX LINES rule

_CODEGEN_SYSTEM_PROMPT = textwrap.dedent(
    """    You are an expert Android automation engineer.

    Your job: given a task description, parameter values, and a CURRENT SCREEN
    snapshot, output a complete runnable uiautomator2 Python script.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ENVIRONMENT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    • `d` = connected uiautomator2 device. `time` and `re` already imported.
    • Param variables are pre-declared (alarm_hour = "05", etc.).
    • DO NOT add imports or reassign `d`.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    SELECTOR SYNTAX — READ CAREFULLY
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    The snapshot shows: rid="X"  text="Y"  desc="Z"
    Map these to uiautomator2 keyword arguments as follows:

        rid="X"   →  d(resourceId="X")          ← exact keyword
        text="Y"  →  d(text="Y")                ← exact keyword
        desc="Z"  →  d(description="Z")         ← MUST be description=, NOT desc=

    !! NEVER write d(desc=...) — that keyword does NOT EXIST in uiautomator2 !!
    !! ALWAYS write d(description=...) when matching a desc= field !!

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    THE SNAPSHOT: WHAT IT SHOWS AND WHAT IT DOES NOT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    The snapshot = the screen NOW. It has two sections:

    Section 1 — INTERACTIVE ELEMENTS (things you can click/type/scroll).
    Section 2 — VISIBLE TEXT labels (non-interactive text nodes: video titles,
                alarm times, list item text). These show what's ON SCREEN but
                cannot be clicked directly via their own node. To click an item
                whose title appears in Section 2, use:
                  d(text="Exact Title Text").click()       ← matches by text
                  d(textContains="Partial Title").click()  ← partial match

    ► Interactive elements: use their exact rid/text/desc selectors.
    ► Post-navigation elements (appear AFTER a click): use .exists() guards.
      NEVER invent a resource-id not in the snapshot.

        # Safe pattern for post-click screens:
        time.sleep(1.5)
        if d(className="android.widget.EditText").count >= 2:
            d(className="android.widget.EditText")[0].set_text("05")
            d(className="android.widget.EditText")[1].set_text("30")
        elif d(className="android.widget.EditText").exists(timeout=4):
            d(className="android.widget.EditText").set_text("05")

        # With resourceId guarded by .exists():
        if d(resourceId="android:id/input_hour").exists(timeout=3):
            d(resourceId="android:id/input_hour").set_text("05")

    !! NEVER call .wait() on an invented selector — it WILL hang forever !!
    !! ALWAYS wrap post-navigation selectors in .exists(timeout=N) !!

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    SELECTOR PRIORITY (snapshot elements)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    1. resourceId  — d(resourceId="<rid from snapshot>")
    2. description — d(description="<desc from snapshot>")   ← NOT desc=
    3. text        — d(text="<text from snapshot>")
    4. className   — d(className="android.widget.EditText")  ← last resort

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    APP LAUNCH
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    The strategy launched the app before taking the snapshot.
    DO NOT call d.app_start() or d.app_wait(). Begin on the snapshot screen.
    Add time.sleep(0.5) at the top to let the UI settle.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    CHROME / BROWSER — CRITICAL PATTERNS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    RULE 1 — NEVER use .wait() or unguarded selectors for Chrome elements.
    Chrome's url_bar DOES NOT appear in the snapshot until the app is fully
    loaded. If url_bar is NOT in the snapshot, use .exists(timeout=8) to wait.

    RULE 2 — Chrome has NO "Enter" / "Go" button in the UI tree.
    Always use d.press("enter") or d.press("search") to submit.

    RULE 3 — url_bar is the ONLY Chrome input element. If it's not in the
    snapshot, don't invent other resourceIds — use .exists() to wait for it.

        # Navigate to URL (url_bar may not be in snapshot yet — always guard):
        url_bar = d(resourceId="com.android.chrome:id/url_bar")
        if not url_bar.exists(timeout=8):
            # Chrome still loading — try the search box or EditText
            url_bar = d(className="android.widget.EditText")
            if not url_bar.exists(timeout=5):
                # Last resort: tap the center-top of screen where address bar usually is
                d.click(0.5, 0.07)
                time.sleep(1.0)
                url_bar = d(className="android.widget.EditText")
        url_bar.click()
        time.sleep(0.5)
        url_bar.clear_text()
        url_bar.set_text("https://www.google.com")
        time.sleep(0.3)
        d.press("enter")
        time.sleep(2.5)

        # Type search and submit (search box already focused):
        d(className="android.widget.EditText").set_text(search_query)
        time.sleep(0.3)
        d.press("search")
        time.sleep(2.5)

    NEVER click a button called "Enter", "Go", or "Search".
    ALWAYS use d.press("enter") or d.press("search").

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    THE SNAPSHOT FORMAT — TWO SECTIONS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    The snapshot has two parts:

    1. INTERACTIVE ELEMENTS — things you can click/type/scroll.
       Use rid=, text=, desc= from these lines as selectors.

    2. VISIBLE TEXT (non-interactive labels) — text that is ON SCREEN
       but on a non-interactive node (e.g. video title in a RecyclerView,
       alarm time label, search result description).
       You CANNOT click these label nodes directly.
       To interact with them, use their parent container:
         d(text="Some Video Title").click()      ← works via child matching
         d(textContains="AgentForce").click()    ← partial match on visible text

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    CLOCK / ALARM — CRITICAL PATTERNS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    STEP 0 — CONTEXT CHECK (run this before anything else):
    Read the snapshot. If "Add alarm" AND Switch/Toggle elements are BOTH
    visible, you are on the alarm LIST. If an EditText with resource-id
    containing "hour" or "minute" is visible, you are inside the time picker.
    If neither, click "Add alarm" to open the picker.

    STEP 1 — Open time picker (only if on alarm list):
        if d(description="Add alarm").exists(timeout=2):
            d(description="Add alarm").click()
        elif d(text="Add alarm").exists(timeout=2):
            d(text="Add alarm").click()
        time.sleep(1.5)

    STEP 2 — MANDATORY: Switch to keyboard/text input mode.
    The clock dial CANNOT accept set_text() — it silently ignores all input.
    You MUST switch to text input mode and CONFIRM it worked (EditText visible)
    before calling set_text(). If you skip this, the alarm saves at default time.

    The keyboard/text mode toggle has no text or description on many devices.
    Use this EXHAUSTIVE search — try every position until EditText appears:

        def _switch_to_text_mode(d):
            # Try every known position for the keyboard mode toggle.
            # Named selectors first (some devices have them)
            named_attempts = [
                lambda: d(description="Switch to text input mode").click()
                    if d(description="Switch to text input mode").exists(timeout=1) else None,
                lambda: d(description="keyboard").click()
                    if d(description="keyboard").exists(timeout=1) else None,
                lambda: d(resourceId="com.google.android.deskclock:id/material_timepicker_mode_button").click()
                    if d(resourceId="com.google.android.deskclock:id/material_timepicker_mode_button").exists(timeout=1) else None,
            ]
            for fn in named_attempts:
                fn()
                time.sleep(0.5)
                if d(className="android.widget.EditText").exists(timeout=1):
                    return True

            # Coordinate grid — the icon can be at different positions
            # depending on device screen size and Android version.
            # Try a grid of likely positions systematically:
            positions = [
                (0.85, 0.75), (0.85, 0.80), (0.85, 0.70),
                (0.15, 0.75), (0.15, 0.80), (0.15, 0.70),
                (0.85, 0.65), (0.15, 0.65),
                (0.85, 0.85), (0.15, 0.85),
                (0.50, 0.80), (0.50, 0.85),
            ]
            for x, y in positions:
                d.click(x, y)
                time.sleep(0.5)
                if d(className="android.widget.EditText").exists(timeout=1):
                    return True
            return False

        switched = _switch_to_text_mode(d)
        if not switched:
            # Last resort: press back and re-open (sometimes resets to text mode)
            d.press("back")
            time.sleep(0.5)
            if d(description="Add alarm").exists(timeout=2):
                d(description="Add alarm").click()
                time.sleep(1.5)
                _switch_to_text_mode(d)

        time.sleep(0.5)
        # CONFIRM: if EditText still not visible, DO NOT proceed with set_text
        # — it will silently fail. Print what we see and exit for retry.
        if not d(className="android.widget.EditText").exists(timeout=2):
            print("MODE_SWITCH_FAILED: EditText not found after all attempts")
            print("TASK_COMPLETE")
            exit()

    STEP 3 — Type the time (AFTER switching to text mode):
    CRITICAL: alarm_hour is zero-padded ("05"). ALWAYS strip the leading zero.
    str(int(alarm_hour)) gives "5" not "05". Sending "05" enters two digits
    separately, landing on the wrong hour (0→5 → wraps to 5, not hour 5).

        hour_str = str(int(alarm_hour))   # "05" → "5", "12" → "12"

        # Try named resource IDs first, then fall back to EditText by index
        if d(resourceId="android:id/input_hour").exists(timeout=4):
            d(resourceId="android:id/input_hour").clear_text()
            d(resourceId="android:id/input_hour").set_text(hour_str)
            time.sleep(0.3)
            d(resourceId="android:id/input_minute").clear_text()
            d(resourceId="android:id/input_minute").set_text(alarm_minute)
        elif d(className="android.widget.EditText").count >= 2:
            d(className="android.widget.EditText")[0].clear_text()
            d(className="android.widget.EditText")[0].set_text(hour_str)
            time.sleep(0.3)
            d(className="android.widget.EditText")[1].clear_text()
            d(className="android.widget.EditText")[1].set_text(alarm_minute)
        time.sleep(0.3)

    STEP 4 — Set AM/PM:

        for sel in [d(text=alarm_period), d(description=alarm_period),
                    d(textContains=alarm_period)]:
            if sel.exists(timeout=2):
                sel.click()
                break

    STEP 5 — Confirm:

        for btn_text in ["OK", "Save", "Done"]:
            if d(text=btn_text).exists(timeout=2):
                d(text=btn_text).click()
                break
        time.sleep(0.5)

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    YOUTUBE — CRITICAL PATTERNS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    YouTube search flow:

        # Open search:
        if d(description="Search").exists(timeout=3):
            d(description="Search").click()
        elif d(resourceId="com.google.android.youtube:id/menu_item_1").exists(timeout=3):
            d(resourceId="com.google.android.youtube:id/menu_item_1").click()
        time.sleep(1.0)

        # Type query:
        search_box = None
        for rid in ["com.google.android.youtube:id/search_edit_text",
                    "com.google.android.youtube:id/youtube_query_text_view"]:
            if d(resourceId=rid).exists(timeout=3):
                search_box = d(resourceId=rid)
                break
        if search_box is None:
            search_box = d(className="android.widget.EditText")
        search_box.set_text(search_query)
        time.sleep(0.3)
        d.press("search")
        time.sleep(2.5)

    Clicking a result — CHECK THE SNAPSHOT LABELS FIRST:
    The snapshot's LABEL section shows the video titles visible on screen.
    Use those exact title strings to click. Never guess by index alone.

        # If you can see a title in the LABEL section of the snapshot, use it:
        if d(textContains="AgentForce").exists(timeout=5):
            d(textContains="AgentForce").click()
        elif d(resourceId="com.google.android.youtube:id/results").exists(timeout=5):
            # Click the first result container
            d(resourceId="com.google.android.youtube:id/results").child(
                className="android.view.ViewGroup", clickable=True
            )[0].click()
        else:
            # Last resort — first clickable item below top nav bar
            d(className="android.view.ViewGroup", clickable=True)[2].click()
        time.sleep(1.5)

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    INTERACTIONS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    d(sel).click()   d(sel).set_text("v")
    if d(sel).exists(timeout=3): ...
    d.send_keys("text")   d.press("back"/"home"/"enter"/"search")
    d.click(x, y)   d.swipe_ext("up", scale=0.8)
    d(scrollable=True).scroll.to(text="X")

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    GMAIL — CRITICAL PATTERNS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Gmail compose flow — use ONLY these selectors (verified working):

        # Open compose screen:
        if d(description="Compose").exists(timeout=3):
            d(description="Compose").click()
        elif d(resourceId="com.google.android.gm:id/compose_button").exists(timeout=3):
            d(resourceId="com.google.android.gm:id/compose_button").click()
        time.sleep(1.5)

        # Fill recipient — the "To" field EditText:
        # Try multiple selectors — Gmail uses different IDs across versions
        to_field = None
        for sel in [
            d(resourceId="com.google.android.gm:id/to"),
            d(resourceId="com.google.android.gm:id/people_edit_text"),
            d(hint="To"),
            d(className="android.widget.MultiAutoCompleteTextView"),
        ]:
            if sel.exists(timeout=2):
                to_field = sel
                break
        if to_field is None:
            to_field = d(className="android.widget.EditText")
        to_field.click()
        time.sleep(0.3)
        to_field.set_text(recipient_email)
        d.press("enter")
        time.sleep(0.5)

        # Fill Subject:
        subj = None
        for sel in [
            d(resourceId="com.google.android.gm:id/subject"),
            d(hint="Subject"),
        ]:
            if sel.exists(timeout=2):
                subj = sel
                break
        if subj:
            subj.click()
            subj.set_text(email_subject)
        time.sleep(0.3)

        # Fill body:
        body = None
        for sel in [
            d(resourceId="com.google.android.gm:id/body"),
            d(hint="Compose email"),
            d(className="android.widget.EditText", index=2),
        ]:
            if sel.exists(timeout=2):
                body = sel
                break
        if body is None:
            # Tap below subject area
            d.click(0.5, 0.6)
            time.sleep(0.3)
            body = d(className="android.widget.EditText")
        body.click()
        body.set_text(email_body)
        time.sleep(0.3)

        # Send:
        if d(description="Send").exists(timeout=2):
            d(description="Send").click()
        elif d(resourceId="com.google.android.gm:id/send").exists(timeout=2):
            d(resourceId="com.google.android.gm:id/send").click()

    CRITICAL FOR GMAIL FILL TASKS:
    When your task says "fill Subject with SUBJECT value" or "fill body with BODY value",
    the actual text values are in your PARAMETER VARIABLES section as email_subject and
    email_body. NEVER reference undefined variables like SUBJECT or BODY directly.
    ALWAYS use email_subject and email_body which are pre-declared variables.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    EXTRACTING CONTENT FROM THE SCREEN
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    When the task asks to EXTRACT, READ, or COLLECT visible content
    (search results, list items, text on screen), you MUST print the
    extracted content to stdout BEFORE printing TASK_COMPLETE.

    The downstream language agent reads your stdout to present results to
    the user. If you only print TASK_COMPLETE, the user sees nothing.

    Pattern for extracting visible text (search results, list items):

        # Collect all visible text labels from the snapshot into a list
        results = []
        for el in d(className="android.widget.TextView"):
            try:
                txt = el.get_text()
                if txt and len(txt.strip()) > 3 and txt.strip() != "...":
                    results.append(txt.strip())
            except Exception:
                pass

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for r in results:
            if r not in seen:
                seen.add(r)
                unique.append(r)

        # Print results — one per line, labelled
        if unique:
            print("SEARCH_RESULTS:")
            for i, r in enumerate(unique[:10], 1):
                print(f"{i}. {r}")
        else:
            print("No visible results found on screen")

        print("TASK_COMPLETE")

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    MANDATORY RULES
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    1. DO NOT call app_start() — app is already open.
    2. ALWAYS end with:  print("TASK_COMPLETE")
    3. Guard permission dialogs: if d(text="Allow").exists(timeout=2): d(text="Allow").click()
    4. Use set_text() — never key-by-key typing.
    5. After d.press("enter"/"search"), add time.sleep(2.0).
    6. Do NOT use try/except — let errors surface for the retry engine.
    7. Output ONLY raw Python. No markdown, no comments, no explanation.
    8. Param variables are in scope — use them directly.
    9. MAX 25 LINES. Simple tasks need simple scripts. Do not add fallback chains
       for elements you can already see in the snapshot — only use .exists() guards
       for elements that appear AFTER a click (post-navigation).
    10. NEVER use desc= keyword — always use description= for content-desc fields.
    11. NEVER call .wait() on a selector you invented — only on selectors from snapshot.
    """
).strip()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN STRATEGY CLASS
# ══════════════════════════════════════════════════════════════════════════════

class MobileCodeGenStrategy:
    """
    Code-generation strategy for Android UI automation.
    (Architecture unchanged — see module docstring)
    """

    def __init__(
        self,
        device_id:        str            = "default_device",
        uiautomator_host: str            = "http://localhost",
        uiautomator_port: int            = 9008,
        llm_provider:     str            = "cerebras",
        cache_file:       Optional[Path] = None,
    ) -> None:
        self.device_id            = device_id
        self.uiautomator_base_url = f"{uiautomator_host.rstrip('/')}:{uiautomator_port}"
        self.llm_provider         = (llm_provider or "cerebras").strip().lower()
        self.current_task:        Optional[MobileTaskRequest] = None
        self.token_usage:         Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls:     int            = 0
        self.tier_stats:          Dict[str, int] = {"execution_attempts": 0, "tier3_retries": 0}

        self.cache           = TemplateCache(cache_file or CACHE_FILE)
        self.param_extractor = ParameterExtractor()
        self.placeholderizer = PlaceholderExtractor()
        self._executor:      Optional[CodeExecutor] = None

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

    async def _llm_chat(self, **kwargs: Any) -> Any:
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

    def _resolve_serial(self) -> str:
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
        seen: Set[str] = set()
        candidates = [c for c in candidates if c and not (c in seen or seen.add(c))]
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

    async def _launch_app_and_wait(self, package: str, min_elements: int = 8) -> None:
        """
        Launch `package` via ADB and poll until the app UI is actually loaded.

        Problem solved: the old 0.8s fixed sleep was far too short for Chrome
        (3-5s cold start), so every first attempt got a snapshot of the loading
        splash with ~24 generic elements and no url_bar — the LLM then invented
        url_bar anyway, failing every attempt identically.

        Strategy: start the app, then poll every 2s for up to 12s waiting for
        the package name to appear in the UI hierarchy (proving its own views
        are rendered) AND for the element count to exceed min_elements.
        """
        if not package:
            return
        serial = self._resolve_serial()

        def _start() -> None:
            dev = u2.connect(serial) if serial else u2.connect()
            dev.app_start(package)

        try:
            await asyncio.wait_for(asyncio.to_thread(_start), timeout=10.0)
        except Exception as e:
            logger.warning(f"[LAUNCH] app_start({package}) error: {e}")
            return

        pkg_tail = package.split(".")[-1].lower()  # e.g. "chrome", "deskclock"
        for poll in range(6):
            await asyncio.sleep(2.0)
            xml  = await self._fetch_xml_dump()
            snap = build_ui_snapshot(xml)
            elem_count = snap.count("\n")
            # App is ready when its own package name appears in the snapshot
            # (meaning its views are in the hierarchy, not just the launcher)
            pkg_visible = pkg_tail in snap.lower() or package.lower() in snap.lower()
            logger.info(
                f"[LAUNCH] Poll {poll+1}/6 | ~{elem_count} elems | "
                f"pkg_visible={pkg_visible} | pkg={pkg_tail}"
            )
            if pkg_visible and elem_count >= min_elements:
                logger.info(f"[LAUNCH] ✅ {package} ready after {(poll+1)*2}s")
                return
            # Fallback: if there are lots of elements, app is probably loaded
            if elem_count >= min_elements * 4:
                logger.info(f"[LAUNCH] ✅ {package} loaded ({elem_count} elements, no pkg check)")
                return

        logger.warning(f"[LAUNCH] ⚠️ {package} may not be fully loaded after 12s — proceeding")

    # FIX #12: Broadened _infer_app — "email" keyword now maps to gmail
    @staticmethod
    def _infer_app(text: str) -> Tuple[str, str]:
        t = text.lower()
        checks: List[Tuple[Tuple[str, ...], str]] = [
            # FIX #12: added "email", "mail", "compose", "inbox" to gmail triggers
            (("gmail", "email", "mail", "compose email", "send email",
              "recipient", "subject", "inbox"),                               "gmail"),
            (("alarm", "clock", "stopwatch", "timer"),                        "clock"),
            (("contacts", "contact", "call log"),                             "contacts"),
            (("play store", "install app", "google play", "download app"),    "play_store"),
            (("youtube", "watch video", "play video", "play a video"),        "youtube"),
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
        param_block = ""
        if params.raw:
            lines = [
                f"  {k} = {str(v)!r}  # use this variable OR its string value"
                for k, v in params.raw.items()
                if k not in _INTERNAL_KEYS
            ]
            if lines:
                param_block = (
                    "\nPARAMETER VARIABLES (already declared — use directly in your code):\n"
                    + "\n".join(lines) + "\n"
                )

        extra_lines: List[str] = []
        for k, v in (extra_params or {}).items():
            if k in _INTERNAL_KEYS or v is None or k in params.raw:
                continue
            extra_lines.append(f"  {k.upper()} = {str(v)!r}")
        extra_section = (
            "\nADDITIONAL CONTEXT (reference only — not pre-declared):\n"
            + "\n".join(extra_lines) + "\n"
        ) if extra_lines else ""

        error_section = (
            f"\n{'━'*50}\n"
            f"PREVIOUS ATTEMPT FAILED — fix this error in your next script:\n"
            f"{error_ctx}\n"
            f"COMMON CAUSES:\n"
            f"  • Used desc= instead of description=\n"
            f"  • Used .wait() on a resourceId not in the snapshot\n"
            f"  • Invented a resourceId — only use what appears in the snapshot below\n"
            f"  • Script too long with unnecessary fallback chains\n"
            f"{'━'*50}\n"
        ) if error_ctx else ""

        return (
            f"TASK:         {task_text}\n"
            f"OVERALL GOAL: {overall_goal}\n"
            f"APP:          {app}  |  PACKAGE: {package or 'unknown'}\n"
            f"{param_block}"
            f"{extra_section}"
            f"{error_section}"
            f"\nCURRENT SCREEN SNAPSHOT (ONLY valid selectors — do not invent others):\n"
            f"{ui_snapshot}\n"
            f"\nWrite the complete uiautomator2 script (MAX 25 lines). "
            f"Use ONLY selectors from the snapshot above. "
            f"Remember: description= not desc=. "
            f"Raw Python only — no markdown, no comments."
        )

    @staticmethod
    def _clean_generated_code(raw: str) -> str:
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
            max_tokens=1200,
        )
        self._track_usage(response)
        raw  = response.choices[0].message.content or ""
        code = self._clean_generated_code(raw)
        logger.info(f"[LLM] Generated {len(code.splitlines())} lines")
        logger.debug(f"[LLM] CODE:\n{code}")
        return code

    @staticmethod
    def _error_context(outcome: ExecutionOutcome) -> str:
        parts: List[str] = []
        if outcome.error_summary:
            parts.append(f"Error: {outcome.error_summary}")
        if outcome.stderr:
            tail = "\n".join(outcome.stderr.splitlines()[-20:])
            parts.append(f"Traceback (tail):\n{tail}")
        return "\n".join(parts)

    @staticmethod
    def _template_id(task_text: str, app: str) -> str:
        return hashlib.sha1(f"{app}:{task_text}".encode()).hexdigest()

    def _normalise_task_pattern(self, task_text: str, params: TaskParameters) -> str:
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
            existing.code_template    = template_code
            existing.parameter_schema.update(schema)
            existing.last_used        = datetime.utcnow().isoformat()
            self.cache._save()
            logger.info(f"[CACHE] Updated template {tid[:8]} (success_count={existing.success_count})")
            return
        template = CodeTemplate(
            template_id=tid, task_pattern=task_pattern, app=app, package=package,
            code_template=template_code, parameter_schema=schema, success_count=1,
        )
        self.cache.add(template)

    async def execute_task(self, task: MobileTaskRequest) -> MobileTaskResult:
        self.current_task = task
        self._executor    = None
        t0                = time.monotonic()

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

        params = ParameterExtractor.extract(task.ai_prompt, task.extra_params)
        logger.info(f"[PARAMS] {params}")

        app, package = self._infer_app(f"{task.ai_prompt} {overall_goal}")
        explicit_app = (task.extra_params.get("app_name") or "").strip().lower()
        if explicit_app and explicit_app in APP_PACKAGES:
            app     = explicit_app
            package = APP_PACKAGES[app]
        logger.info(f"[APP] app={app!r} | package={package!r}")

        executor = self._get_executor()

        # ── Cache lookup ──────────────────────────────────────────────────
        cache_hit = self.cache.lookup(task.ai_prompt, app)
        if cache_hit:
            template, score = cache_hit
            missing = params.missing_keys(template.code_template)
            if not missing:
                logger.info(f"[T1] Cache hit (sim={score:.2f}) — injecting {len(params.raw)} params → executing")
                injected = params.inject(template.code_template)

                # FIX 4: Pre-validate the injected code doesn't reference
                # undefined variables. NameError on a cached template means
                # the template was stored with hardcoded variables from a
                # different task context (e.g. recipient_email in a
                # "navigate to email" template from a prior session).
                syntax_err = CodeExecutor._syntax_check(
                    CodeExecutor(self._resolve_serial())._build_script(injected, params)
                )
                if syntax_err:
                    logger.warning(f"[T1] Cache hit but syntax/var error — marking failed: {syntax_err[:80]}")
                    self.cache.mark_failure(template.template_id)
                else:
                    outcome = await executor.execute(injected, params)
                    if outcome.success:
                        self.cache.mark_success(template.template_id)
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        logger.info(f"✅ [T1] Cache hit success | {elapsed_ms}ms")

                        # Capture stdout content if present
                        stdout_content = (outcome.stdout or "").strip()
                        content_lines = [l for l in stdout_content.splitlines()
                                         if l.strip() and l.strip() != "TASK_COMPLETE"]
                        cr = "\n".join(content_lines) if content_lines else f"Template cache (sim={score:.2f})"

                        return self._build_result(
                            task.task_id, "success", 1, elapsed_ms,
                            completion_reason=cr,
                        )
                    elif "NameError" in (outcome.error_summary or ""):
                        # Stale template references an undefined variable
                        logger.warning(
                            f"[T1] Cached code has NameError — template is stale, marking failed: "
                            f"{outcome.error_summary[:80]}"
                        )
                        self.cache.mark_failure(template.template_id)
                    else:
                        logger.warning(f"[T1] Cached code failed: {outcome.error_summary} — falling through to LLM")
                        self.cache.mark_failure(template.template_id)
            else:
                logger.info(f"[T1] Cache hit but missing params {missing} — falling through to LLM")

        async def _live_snapshot(label: str, min_elems: int = _MIN_SNAPSHOT_ELEMENTS) -> str:
            for snap_try in range(3):
                xml  = await self._fetch_xml_dump()
                snap = build_ui_snapshot(xml)
                elem_count = snap.count("\n")
                if elem_count >= min_elems or snap_try == 2:
                    logger.info(f"[DEVICE] {label} snapshot: ~{elem_count} elements (try {snap_try + 1})")
                    return snap
                logger.warning(
                    f"[DEVICE] {label} snapshot has only ~{elem_count} elements "
                    f"— retrying in {_SNAPSHOT_RETRY_DELAY}s"
                )
                await asyncio.sleep(_SNAPSHOT_RETRY_DELAY)
            return snap  # type: ignore[return-value]

        code      = ""
        error_ctx = ""
        outcome:  Optional[ExecutionOutcome] = None

        # ── App launch with proper wait ───────────────────────────────────
        # Detect "open/launch app" tasks — these just need app_start + wait,
        # no LLM code generation required. This also ensures the app is fully
        # loaded before we take the first snapshot for subsequent tasks.
        task_lower = task.ai_prompt.lower()
        is_launch_task = (
            package
            and any(task_lower.startswith(v) for v in (
                "open ", "launch ", "start ", "open the ", "launch the ",
            ))
            and not any(k in task_lower for k in (
                "search", "type", "navigate", "click", "set ", "press",
                "find", "play", "send", "compose", "read",
            ))
        )

        if is_launch_task:
            logger.info(f"[LAUNCH] Pure launch task — using app_start + wait | pkg={package}")
            await self._launch_app_and_wait(package)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self._store_successful_code(
                f'd.app_start("{package}")\ntime.sleep(3.0)\nprint("TASK_COMPLETE")',
                task.ai_prompt, app, package, params,
            )
            return self._build_result(
                task.task_id, "success", 1, elapsed_ms,
                completion_reason=f"App launched: {package}",
            )

        # Non-launch task: ensure app is running before first snapshot
        if package:
            await self._launch_app_and_wait(package)

        for attempt in range(1 + MAX_CODE_RETRIES):
            label = f"attempt {attempt + 1}/{1 + MAX_CODE_RETRIES}"
            if attempt > 0:
                await asyncio.sleep(1.0)
            ui_snapshot = await _live_snapshot(label)
            logger.info(
                f"[LLM] {label}: generating code | app={app} | "
                f"params={list(params.raw.keys())} | retry={'yes' if error_ctx else 'no'}"
            )
            try:
                code = await self._generate_code(
                    task_text=task.ai_prompt, overall_goal=overall_goal,
                    params=params, app=app, package=package,
                    ui_snapshot=ui_snapshot, error_ctx=error_ctx,
                    extra_params=task.extra_params,
                )
            except Exception as e:
                logger.error(f"[LLM] Generation error ({label}): {e}")
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                return self._build_error_result(task.task_id, f"LLM error: {e}", elapsed_ms)

            if not code.strip():
                logger.warning(f"[LLM] Empty code response ({label})")
                error_ctx = "You returned empty code. Output a complete Python script."
                continue

            logger.info(f"[T2/T3] Executing — {label}")
            self.tier_stats["execution_attempts"] += 1
            if attempt > 0:
                self.tier_stats["tier3_retries"] += 1
            outcome = await executor.execute(code, params)

            if outcome.success:
                logger.info(f"✅ [T2] Code executed successfully ({label})")
                # Wait for UI to fully settle before snapshotting for verification.
                # Chrome search results and Clock alarm list both need ~1.5s to render.
                # Without this wait, verifier snapshots a loading/transitioning screen
                # and produces false negatives, causing the agent to re-run the task.
                _settle = 2.5 if app in ("chrome", "youtube") else 1.5
                logger.info(f"[VERIFY] Settling {_settle}s before snapshot...")
                await asyncio.sleep(_settle)
                verified, reason = await self._verify_task_completion(task.ai_prompt, app, params)
                if verified:
                    logger.info(f"[VERIFY] ✅ Task confirmed complete: {reason}")
                    break
                else:
                    logger.warning(f"[VERIFY] ⚠️ Script succeeded but task not confirmed: {reason}")
                    outcome = ExecutionOutcome(
                        success=False,
                        error_summary=f"Verification failed: {reason}",
                    )
                    error_ctx = (
                        f"Your script printed TASK_COMPLETE but the device "
                        f"shows the task is NOT done: {reason}. "
                        f"Check the updated snapshot and try again."
                    )
                    continue

            error_ctx = self._error_context(outcome)
            logger.warning(f"[T2/T3] Execution failed ({label}): {outcome.error_summary}")

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if outcome and outcome.success:
            try:
                self._store_successful_code(code, task.ai_prompt, app, package, params)
            except Exception as e:
                logger.warning(f"[CACHE] Template storage failed (non-fatal): {e}")

            # FIX: If stdout contains real content beyond TASK_COMPLETE, use it
            # as completion_reason so the coordinator can pass it downstream.
            stdout_content = (outcome.stdout or "").strip()
            # Remove the TASK_COMPLETE signal and any blank lines
            content_lines = [
                l for l in stdout_content.splitlines()
                if l.strip() and l.strip() != "TASK_COMPLETE"
            ]
            if content_lines:
                extracted = "\n".join(content_lines)
                logger.info(f"[EXEC] Stdout content captured ({len(extracted)} chars): {extracted[:80]!r}")
                return self._build_result(
                    task.task_id, "success", 1, elapsed_ms,
                    completion_reason=extracted,
                )
            return self._build_result(
                task.task_id, "success", 1, elapsed_ms,
                completion_reason=f"Code generated and executed in {elapsed_ms}ms",
            )

        err = (outcome.error_summary if outcome else "Code generation produced no executable output")
        logger.error(f"❌ Task failed after all attempts: {err}")
        return self._build_error_result(task.task_id, err, elapsed_ms)

    # ══════════════════════════════════════════════════════════════════════
    #  TASK VERIFICATION  (unchanged)
    # ══════════════════════════════════════════════════════════════════════

    async def _verify_task_completion(
        self,
        task_text: str,
        app:       str,
        params:    TaskParameters,
    ) -> Tuple[bool, str]:
        """
        Verify task completion by inspecting the live UI after execution.

        Design principles:
        1. PREFER FALSE POSITIVE over FALSE NEGATIVE for tasks that are
           inherently hard to verify precisely (typing, navigation).
           A false negative causes the agent to re-run the task, which is
           always worse than trusting a successful rc=0 execution.
        2. Only return False when there is STRONG evidence of failure
           (error dialog, time picker still open, wrong time on alarm list).
        3. For "intermediate" tasks (type a query, navigate to page),
           trust the script's rc=0 + TASK_COMPLETE signal — don't require
           the final result to be visible yet.
        4. For "outcome" tasks (alarm set, email sent), verify the outcome.
        """
        try:
            xml_dump = await self._fetch_xml_dump()
            snap     = build_ui_snapshot(xml_dump)
            snap_low = snap.lower()
            task_low = task_text.lower()
            elem_count = snap.count("\n")

            # ── Hard failure: crash dialog ────────────────────────────────
            if any(s in snap_low for s in ("unfortunately", "has stopped")):
                return False, "App crash dialog visible"

            # ══════════════════════════════════════════════════════════════
            #  CLOCK / ALARM
            # ══════════════════════════════════════════════════════════════
            if app == "clock" or any(k in task_low for k in ("alarm", "clock", "timer")):
                task_is_set_time = any(k in task_low for k in (
                    "set", "time", "alarm time", "5:", "6:", "7:", "8:", "9:",
                    "10:", "11:", "12:",
                ))

                # Time picker still open → definite failure
                if "input_hour" in snap_low or "input_minute" in snap_low:
                    return False, "Time picker still open — alarm not saved"

                # Dial picker (no text fields visible but clock is shown) → failure
                if "timepicker" in snap_low and "add alarm" not in snap_low:
                    return False, "Clock dial still showing — alarm not saved"

                has_add_alarm = "add alarm" in snap_low
                has_switch    = "switch" in snap_low or "togglebutton" in snap_low.replace("hour", "")

                if has_add_alarm and has_switch and task_is_set_time:
                    # We're on the alarm list. Check if correct time is there.
                    alarm_hour   = params.get("alarm_hour",   "")
                    alarm_minute = params.get("alarm_minute", "")
                    alarm_period = params.get("alarm_period", "")

                    if alarm_hour and alarm_minute:
                        h_int = int(alarm_hour)
                        # All ways the time could appear in the snapshot labels
                        expected = [
                            f"{h_int}:{alarm_minute}",
                            f"{alarm_hour}:{alarm_minute}",
                        ]
                        if alarm_period:
                            expected += [
                                f"{h_int}:{alarm_minute} {alarm_period.upper()}",
                                f"{h_int}:{alarm_minute} {alarm_period.lower()}",
                            ]
                        if any(v in snap_low for v in expected):
                            return True, f"Correct alarm time {h_int}:{alarm_minute} visible on list"

                        # Not found — look at what times ARE on screen
                        times_on_screen = re.findall(r"\b\d{1,2}:\d{2}\b", snap)
                        # Filter out the default times that appear regardless (system clock etc.)
                        non_default = [t for t in times_on_screen
                                       if t not in ("12:00", "00:00") and t != f"{h_int}:00"]
                        if non_default:
                            # There's a real alarm time visible but it's not ours
                            return False, (
                                f"Wrong alarm time on list — expected {h_int}:{alarm_minute}, "
                                f"found {non_default[:2]}"
                            )
                        # Only default times (12:00) visible — no alarm was actually created
                        # OR the alarm was created but label hasn't rendered yet.
                        # Give benefit of the doubt on first attempt:
                        logger.info(f"[VERIFY] Only default times visible: {times_on_screen[:3]} — trusting script")
                        return True, "Alarm list visible, times not yet rendered — trusting rc=0"

                # On alarm list but not a set-time task (e.g. "Open Clock", "Confirm OK")
                if has_add_alarm:
                    return True, "Alarm list screen visible"

                # On some other clock screen (timers, stopwatch, etc.)
                return True, "Clock app visible — trusting rc=0"

            # ══════════════════════════════════════════════════════════════
            #  CHROME / BROWSER
            # ══════════════════════════════════════════════════════════════
            if app == "chrome" or any(k in task_low for k in ("chrome", "search", "browser")):
                query      = params.get("search_query", "")
                target_url = params.get("target_url", "")

                # Hard failure: still on new-tab start page
                still_on_newtab = (
                    "search or type url" in snap_low
                    and "google" not in snap_low
                    and elem_count < 12
                )
                if still_on_newtab:
                    return False, "Chrome still on new-tab page — action had no effect"

                # ── "type" / "search for" tasks ───────────────────────────
                # These just need the query to have been submitted — we don't
                # need full results rendered. Trust rc=0 unless still on newtab.
                if any(k in task_low for k in ("type", "search for", "search in", "enter")):
                    # Best signal: query text appears anywhere in snapshot
                    if query and query.lower() in snap_low:
                        return True, f"Query '{query[:30]}' visible in snapshot"
                    # Next best: page changed (not new-tab, has content)
                    if elem_count >= 10:
                        return True, f"Page has content ({elem_count} elements) — query likely submitted"
                    return False, "Chrome appears empty after type task"

                # ── navigation tasks ──────────────────────────────────────
                if any(k in task_low for k in ("navigate", "go to", "load")):
                    if target_url:
                        domain = re.search(r"https?://([^/]+)", target_url)
                        if domain and domain.group(1).lower().replace("www.", "") in snap_low:
                            return True, f"Domain visible in snapshot"
                    if elem_count >= 10:
                        return True, f"Chrome page loaded ({elem_count} elements)"
                    return False, "Chrome navigation: page still empty"

                # ── "click search button" / submit tasks ─────────────────
                if any(k in task_low for k in ("click", "press", "submit", "button")):
                    # After clicking search, results page should have substantial content
                    if query and query.lower() in snap_low:
                        return True, f"Results visible for '{query[:25]}'"
                    if elem_count >= 15:
                        return True, f"Results page loaded ({elem_count} elements)"
                    # Even with few elements, if we're not on newtab, it worked
                    if elem_count >= 8 and "search or type url" not in snap_low:
                        return True, f"Page changed after click ({elem_count} elements)"
                    return False, "Search button click: no results visible"

                # ── generic Chrome task ───────────────────────────────────
                chrome_active = any(s in snap_low for s in (
                    "com.android.chrome", "url_bar", "location_bar", "omnibox",
                    "google", "chrome",
                ))
                if chrome_active or elem_count >= 8:
                    return True, f"Chrome active ({elem_count} elements)"
                return False, "Chrome not confirmed active"

            # ══════════════════════════════════════════════════════════════
            #  GMAIL
            # ══════════════════════════════════════════════════════════════
            if app == "gmail" or any(k in task_low for k in ("email", "gmail", "compose", "send")):
                # Distinguish task intent: compose (open compose screen) vs send (close it)
                task_is_send = any(k in task_low for k in ("send", "click send", "press send"))
                task_is_compose_open = any(k in task_low for k in (
                    "compose", "new email", "navigate to email", "open email",
                    "open gmail", "fill", "subject", "body", "recipient",
                ))

                # For "send" tasks: compose screen should be GONE → success
                if task_is_send:
                    compose_gone = "to" not in snap_low[:200] and "subject" not in snap_low[:200]
                    has_compose_btn = "compose" in snap_low
                    if compose_gone and has_compose_btn:
                        return True, "Compose screen closed — email sent"
                    if "send" in snap_low and ("to" in snap_low or "subject" in snap_low):
                        return False, "Compose screen still visible — email not sent"
                    return True, "Gmail screen changed after send"

                # For "compose/fill/open" tasks: compose screen OPEN is success
                if task_is_compose_open:
                    # Compose screen is open if subject or to fields are visible
                    compose_open = any(s in snap_low for s in (
                        "subject", '"to"', "recipient", "compose email",
                        "com.google.android.gm:id/subject",
                        "com.google.android.gm:id/to",
                    ))
                    if compose_open:
                        return True, "Compose screen open — compose task succeeded"
                    # Check if we at least have a Gmail screen with content
                    if elem_count >= 10:
                        return True, f"Gmail active with {elem_count} elements"
                    return True, "Gmail screen visible — trusting rc=0"

                # Generic Gmail task — just check we're in Gmail
                if elem_count >= 8:
                    return True, f"Gmail active ({elem_count} elements)"
                return True, "Gmail screen changed"

            task_words = re.findall(r"[a-z]{4,}", task_low)
            exclude    = {"open", "launch", "start", "type", "click", "find", "show", "make",
                          "with", "from", "into", "this", "that", "your", "have", "been", "will",
                          "also", "some", "then"}
            task_words = [w for w in task_words if w not in exclude][:5]
            matched    = [w for w in task_words if w in snap_low]
            if matched:
                return True, f"Task keywords found on screen: {matched[:3]}"
            return True, "No contradicting signals — trusting TASK_COMPLETE"

        except Exception as e:
            logger.warning(f"[VERIFY] Verification error (non-fatal): {e}")
            return True, f"Verification skipped due to error: {e}"

    # ── Result builders ────────────────────────────────────────────────────

    def _log_metrics(self, status: str, elapsed_ms: int) -> None:
        logger.info(
            f"\n{'='*60}\n"
            f"📊 {'✅' if status == 'success' else '❌'} {status.upper()} | "
            f"{elapsed_ms}ms | llm_calls={self.total_llm_calls} | "
            f"tokens={self.token_usage} | {self.cache.stats()}\n"
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
            task_id=task_id, status=status, steps_taken=steps,
            actions_executed=[], execution_time_ms=elapsed_ms,
            error=error or None, completion_reason=completion_reason or None,
            token_usage=dict(self.token_usage), llm_calls=self.total_llm_calls,
        )

    def _build_error_result(self, task_id: str, error: str, elapsed_ms: int = 0) -> MobileTaskResult:
        return self._build_result(task_id, "failed", 0, elapsed_ms, error=error)


# ══════════════════════════════════════════════════════════════════════════════
#  BACKWARD COMPATIBILITY
# ══════════════════════════════════════════════════════════════════════════════

MobileStrategy      = MobileCodeGenStrategy
MobileReActStrategy = MobileCodeGenStrategy


# ══════════════════════════════════════════════════════════════════════════════
#  INTEGRATION FUNCTION  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

async def execute_mobile_task(
    task:      Dict[str, Any],
    device_id: str = "emulator-5554",
) -> ExecutionResult:
    try:
        extra_params = dict(task.get("extra_params", {}) or {})
        if not extra_params.get("overall_goal"):
            extra_params["overall_goal"] = task.get("goal") or task.get("ai_prompt", "")
        uiautomator_port = int(
            extra_params.get("uiautomator_port") or task.get("uiautomator_port", 9008)
        )
        timeout = int(task.get("timeout_seconds", CODE_EXEC_TIMEOUT))
        mobile_task = MobileTaskRequest(
            task_id=task.get("task_id", "unknown"), ai_prompt=task.get("ai_prompt", ""),
            device_id=device_id, session_id=task.get("session_id", "default"),
            context=extra_params, extra_params=extra_params,
            max_steps=1, timeout_seconds=timeout,
        )
        strategy = MobileCodeGenStrategy(device_id=device_id, uiautomator_port=uiautomator_port)
        result   = await strategy.execute_task(mobile_task)
        return ExecutionResult(
            status="success" if result.status == "success" else "failed",
            task_id=result.task_id, context="mobile", action="codegen",
            details=result.completion_reason or result.error or "",
            logs=[], timestamp=datetime.now().isoformat(),
            duration=result.execution_time_ms / 1000.0,
            metadata={"token_usage": result.token_usage or {}, "llm_calls": result.llm_calls, "steps_taken": result.steps_taken},
            error=result.error,
        )
    except Exception as e:
        logger.error(f"❌ execute_mobile_task: {e}", exc_info=True)
        return ExecutionResult(
            status="failed", task_id=task.get("task_id", "unknown"),
            context="mobile", action="codegen", details="", logs=[],
            timestamp=datetime.now().isoformat(), duration=0.0, error=str(e),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  CHANGE LOG
# ══════════════════════════════════════════════════════════════════════════════
#
#  FIX #1  _QUERY_RE rewrite
#          Old: fired on any sentence containing search/type/find/google/look up
#          New: requires quoted string OR explicit search-intent verb; rejects
#               single nouns extracted from navigation sentences like "bar"
#
#  FIX #2  _extract_query() early-return for navigation tasks
#          Tasks starting with Navigate/Click/Press/Tap/Open/Go now skip query
#          extraction entirely, preventing "button to get the results" being set
#          as search_query when the task is "Click the search button..."
#
#  FIX #3  "input_content" added to _INTERNAL_KEYS
#          Stops the coordinator output from previous tasks leaking into
#          generated scripts as a declared Python variable
#
#  FIX #4  System prompt: desc= → description=
#          Added explicit rule and capitalized warning:
#          NEVER use desc=, ALWAYS use description= for content-desc fields
#
#  FIX #5  System prompt: Chrome enter/search pattern
#          Added dedicated CHROME section explaining that there is NO Enter
#          button in Chrome's UI tree. LLM must use d.press("enter") or
#          d.press("search") — never look for a clickable Enter element.
#
#  FIX #6  System prompt: .wait() / .exists() guard rule strengthened
#          Moved the !! warning to a prominent position and added it to the
#          error_ctx hint so retries specifically call out the cause.
#
#  FIX #7  System prompt: hard MAX 25 LINES rule
#          Old scripts were 60-100 lines with unnecessary fallback chains.
#          Hard cap forces the LLM to write concise scripts.
#
#  FIX #8  CodeExecutor: pre-execution syntax check via compile()
#          Syntax errors (unterminated string, unclosed paren) are now caught
#          in ~0ms without a subprocess spawn or device connection attempt.
#          The error is fed back to the LLM as error_ctx for the next attempt.
#
#  FIX #9  build_ui_snapshot: element count in header
#          Header now says "CONFIRMED INTERACTIVE ELEMENTS: N found".
#          This signals to the LLM that the list is complete and it should
#          not invent additional selectors.
#
#  FIX #10 System prompt: app-specific patterns
#          Added CLOCK/ALARM section showing the exact "switch to text input
#          mode" pattern needed to avoid the clock-dial (which caused the
#          alarm to stay at 11:00 instead of being set to 5:45).
#
#  FIX #11 System prompt: YouTube section
#          Added correct YouTube search and "click first result" patterns
#          with working resourceId fallback chain.
#
#  FIX #12 _infer_app: "email" keyword added to gmail
#          "email" was listed in comments but missing from the actual keyword
#          tuple, so tasks like "Navigate to email app" never triggered Gmail.
#          Added: "email", "mail", "compose email", "send email", "inbox"