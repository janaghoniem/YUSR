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

# Optional sentence-transformers for semantic cache similarity.
# If not installed, falls back to Jaccard + synonym normalization.
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _SENTENCE_MODEL = SentenceTransformer("all-MiniLM-L6-v2")   # 22MB, runs locally
    _SEMANTIC_CACHE_AVAILABLE = True
    logger_tmp = logging.getLogger(__name__)
    logger_tmp.info("✅ Sentence-Transformers loaded — using semantic cache similarity")
except ImportError:
    _SEMANTIC_CACHE_AVAILABLE = False
    _SENTENCE_MODEL = None

from agents.utils.device_protocol import (
    MobileTaskRequest,
    MobileTaskResult,
    SemanticUITree,
)
from agents.execution_agent.core.exec_agent_models import ExecutionResult
from agents.execution_agent.strategies.cache_adapter import CacheAdapter
from agents.execution_agent.strategies.mobile_template_cache import infer_app

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
    # App stores
    "play store":      "com.android.vending",
    "google play":     "com.android.vending",
    "app store":       "com.android.vending",   # Android has no iOS App Store
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

# Words in task descriptions that look like quoted values but are actually
# UI element names / button labels — never extract as params.
_TASK_LABEL_WORDS: Set[str] = {
    "compose", "new email", "send", "ok", "cancel", "back", "next",
    "save", "done", "confirm", "submit", "search", "yes", "no",
    "add alarm", "alarm", "install", "get", "open", "close",
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
        # Post-injection: fix any unquoted numeric args to set_text()
        # e.g. set_text(05) → set_text("05")
        # This happens when PlaceholderExtractor captured a value inside
        # a numeric context rather than a string literal.
        result = re.sub(
            r"\.set_text\((\d+)\)",
            lambda m: f'.set_text("{m.group(1)}")',
            result,
        )
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
        self._path:    Path                    = cache_file
        self._store:   Dict[str, CodeTemplate] = {}
        # Embedding cache: template_id → numpy vector (lazy, computed on first lookup)
        self._embeds:  Dict[str, Any]          = {}
        self._load()
        logger.info(
            f"[CACHE] TemplateCache ready: {len(self._store)} templates | "
            f"path={self._path} | "
            f"mode={'semantic' if _SEMANTIC_CACHE_AVAILABLE else 'jaccard'}"
        )

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

    def _embed(self, text: str) -> Any:
        """Return sentence embedding for text, using the global model."""
        if not _SEMANTIC_CACHE_AVAILABLE or _SENTENCE_MODEL is None:
            return None
        return _SENTENCE_MODEL.encode(text, normalize_embeddings=True)

    @staticmethod
    def _cosine(a: Any, b: Any) -> float:
        """Cosine similarity between two normalized embedding vectors."""
        if a is None or b is None:
            return 0.0
        return float(np.dot(a, b))

    def lookup(self, task_text: str, app: str, threshold: float = CACHE_SIM_THRESHOLD) -> Optional[Tuple[CodeTemplate, float]]:
        if not self._store:
            return None

        app_lower = (app or "").lower().strip()
        best_t:   Optional[CodeTemplate] = None
        best_sim: float = 0.0

        if _SEMANTIC_CACHE_AVAILABLE and _SENTENCE_MODEL is not None:
            # ── Semantic similarity path ──────────────────────────────────
            # Embed the query once; embed each template lazily (cached).
            query_vec = self._embed(task_text)
            for t in self._store.values():
                # Lazy-compute and cache template embedding
                if t.template_id not in self._embeds:
                    self._embeds[t.template_id] = self._embed(t.task_pattern)
                tmpl_vec = self._embeds[t.template_id]
                sim = self._cosine(query_vec, tmpl_vec)
                # Cross-app penalty: wrong app → halve similarity
                if app_lower and t.app.lower() != app_lower:
                    sim *= 0.4
                # Reliability penalty: templates that fail often
                if t.reliability < 0.5 and (t.success_count + t.failure_count) >= 3:
                    sim *= 0.6
                if sim > best_sim:
                    best_sim = sim
                    best_t   = t
            # Semantic similarity is naturally in [0,1].
            # 0.60 is the right floor — same-app variants score 0.60-0.75 and
            # should hit cache. The cross-app 0.4x penalty prevents misfires.
            sem_threshold = max(threshold, 0.60)
            if best_t is not None and best_sim >= sem_threshold:
                logger.info(
                    f"[CACHE] HIT (semantic) sim={best_sim:.3f} | "
                    f"pattern='{best_t.task_pattern[:55]}' | app={best_t.app}"
                )
                return best_t, best_sim
            logger.info(
                f"[CACHE] MISS (semantic) best_sim={best_sim:.3f} < {sem_threshold} | "
                f"query='{task_text[:55]}'"
            )
            return None

        else:
            # ── Jaccard fallback path (no sentence-transformers) ──────────
            query_kw = self._kw_query(task_text)
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
                logger.info(f"[CACHE] HIT (jaccard) sim={best_sim:.2f} | pattern='{best_t.task_pattern[:55]}' | app={best_t.app}")
                return best_t, best_sim
            logger.info(f"[CACHE] MISS (jaccard) best_sim={best_sim:.2f} < {threshold} | query='{task_text[:55]}'")
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

        # ── Parse email JSON from input_content OR overall_goal context ────
        # When a reasoning agent produces {"SUBJECT": "...", "BODY": "..."},
        # it's stored as task_1 output. The coordinator injects it as
        # input_content ONLY if task_1 is in the current task's depends_on.
        # If it's not (e.g. the decomposer dropped the dependency), we try
        # to find it in all extra_params values — the coordinator sometimes
        # passes it under input_content from a previous task in the chain.
        # As a final fallback: parse overall_goal for explicit subject/body markers.
        input_content = (extra.get("input_content") or "").strip()
        # If input_content looks like metadata (Template cache..., Code generated...),
        # try to find actual JSON elsewhere in extra_params.
        if not input_content.startswith("{"):
            for val in extra.values():
                if isinstance(val, str) and val.strip().startswith('{"SUBJECT"'):
                    input_content = val.strip()
                    break
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

        # Also try extracting email fields from higher-level context such as
        # the overall goal or other coordinator-provided extras when the
        # task text itself lacks them. Useful when decomposer/coordinator
        # placed the subject/body in `overall_goal`.
        for ctx_key in ("overall_goal", "goal"):
            ctx_val = extra.get(ctx_key)
            if isinstance(ctx_val, str) and ctx_val:
                ParameterExtractor._extract_email(ctx_val, params)
        
        # Additional fallback: if alarm params are still missing but text
        # contains time patterns, try extraction from task text one more time
        # with a looser heuristic (some coordinators phrase times differently).
        if not params.has("alarm_hour") and any(w in (text or "").lower() for w in ("alarm", "set time", "timer")):
            ParameterExtractor._extract_time(text, params)

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
        # Also store single-digit hour variant for template matching flexibility
        params.set("alarm_hour_no_pad", str(hour))

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

    # Pattern: "Enter/Type/Write X in the <field> field" — extracts actual param values
    # from task descriptions like "Enter hello world in the Subject field"
    _ENTER_IN_FIELD_RE = re.compile(
        r"(?:enter|type|write|put|fill(?:\s+in)?|input)\s+"
        r"(?P<value>.+?)\s+in(?:\s+the)?\s+"
        r"(?P<field>\w[\w\s]*?)\s+(?:field|box|input|area)",
        re.IGNORECASE,
    )
    _FIELD_PARAM_MAP: Dict[str, str] = {
        "subject":    "email_subject",
        "to":         "recipient_email",
        "body":       "email_body",
        "message":    "email_body",
        "content":    "email_body",
        "email body": "email_body",
        "search":     "search_query",
        "query":      "search_query",
    }

    @staticmethod
    def _extract_query(text: str, params: TaskParameters) -> None:
        # FIX #2: Skip query extraction entirely for navigation/click sentences.
        nav_verbs = re.compile(
            r"^\s*(navigate|click|press|tap|open|go to|scroll|swipe|drag|select|"
            r"confirm|submit|dismiss|close|back|return)\b",
            re.IGNORECASE,
        )
        if nav_verbs.match(text):
            logger.debug(f"[PARAMS] Skipping query extraction for navigation task: {text[:60]}")
            return

        # ── "Enter X in the Subject/Body/To field" pattern ──────────────
        m = ParameterExtractor._ENTER_IN_FIELD_RE.search(text)
        if m:
            value     = m.group("value").strip().strip("'\"")
            field     = m.group("field").strip().lower()
            param_key = None
            for field_kw, pk in ParameterExtractor._FIELD_PARAM_MAP.items():
                if field_kw in field:
                    param_key = pk
                    break
            if param_key and value and value.lower() not in _TASK_LABEL_WORDS:
                if not params.has(param_key):
                    params.set(param_key, value)
                    logger.debug(f"[PARAMS] 'Enter X in field': {param_key}={value!r}")
                return  # don't also run _QUERY_RE

        qm = _QUERY_RE.search(text)
        if qm:
            raw = (qm.group(1) or qm.group(2) or "").strip().strip("'\"")
            if raw:
                params.set("search_query", raw)

    @staticmethod
    def _extract_quoted(text: str, params: TaskParameters) -> None:
        quoted = _QUOTED_RE.findall(text)
        # Filter out UI button/label words — these are task instructions, not param values
        real_values = [
            q for q in quoted
            if q.lower().strip() not in _TASK_LABEL_WORDS and len(q.strip()) >= 2
        ]
        if real_values:
            if not params.has("search_query"):
                params.set("search_query", real_values[0])
            if len(real_values) >= 2 and not params.has("email_subject"):
                params.set("email_subject", real_values[1])


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
#  APP PLAYBOOKS — injected dynamically, one per task
#  These replace the hardcoded sections in the old monolithic prompt.
#  Add new apps here without touching the base prompt.
# ══════════════════════════════════════════════════════════════════════════════

_APP_PLAYBOOKS: Dict[str, str] = {

    "chrome": """
CHROME PLAYBOOK:
- Address bar: d(resourceId="com.android.chrome:id/url_bar") — guard with .exists(timeout=8)
- If url_bar not in snapshot, use d(className="android.widget.EditText") or tap (0.5, 0.07)
- To navigate/search: click bar → clear_text() → set_text(url_or_query) → d.press("enter")
- NO Enter/Go button exists — always use d.press("enter") or d.press("search")
- After submit: time.sleep(2.5)
""",

    "clock": """
CLOCK / ALARM PLAYBOOK:
Read the task carefully — it is ONE of these sub-tasks, not all of them at once:

SUB-TASK A — "Open alarm / Navigate to alarm / Add alarm" (no time params present):
  Only click Add Alarm. Do NOT open the time picker or set any time.
  if d(description="Add alarm").exists(timeout=3):
      d(description="Add alarm").click()
  elif d(text="Add alarm").exists(timeout=3):
      d(text="Add alarm").click()
  time.sleep(1.5)
  print("TASK_COMPLETE")

SUB-TASK B — "Set the alarm time to HH:MM AM/PM" (alarm_hour, alarm_minute, alarm_period params present):
  The time picker may already be open (from sub-task A) OR you may need to open it.
  Step 1: Check if time picker EditText is visible. If not, click Add Alarm then switch modes.
  Step 2: Switch to TEXT INPUT — dial ignores set_text() silently.
    Use 12-position grid search (see positions list in clock_set_alarm_001 template).
    Confirm EditText visible BEFORE calling set_text. If not found, use sys.exit(1) NOT exit().
    IMPORTANT: sys.exit(1) causes retry; exit() causes false rc=0 success.
  Step 3: Strip leading zero: hour_str = str(int(alarm_hour))
    d(resourceId="android:id/input_hour").clear_text(); .set_text(hour_str)
    d(resourceId="android:id/input_minute").clear_text(); .set_text(alarm_minute)
    OR d(className="android.widget.EditText")[0/1] if resourceId not found
  Step 4: d(text=alarm_period) or d(description=alarm_period)
  Step 5: d(text="OK") or d(text="Save") or d(text="Done")

Mode switch selectors (try in order until EditText appears):
  description="Switch to text input mode"  (NOT the longer "for the time input." variant)
  description="keyboard"
  resourceId="com.google.android.deskclock:id/material_timepicker_mode_button"
  Coordinate grid: (0.85,0.75) (0.85,0.80) (0.85,0.70) (0.15,0.75) (0.15,0.80)
                   (0.15,0.70) (0.85,0.65) (0.15,0.65) (0.85,0.85) (0.15,0.85)
                   (0.50,0.80) (0.50,0.85)
""",

    "gmail": """
GMAIL PLAYBOOK:
INVALID keywords (never use): hint=, placeholder=, label=, desc= (use description= always)

IMPORTANT: description="Compose" does NOT exist on this device. Use resourceId FIRST.

Open compose FAB:
  if d(resourceId="com.google.android.gm:id/compose_button").exists(timeout=5):
      d(resourceId="com.google.android.gm:id/compose_button").click()
  elif d(resourceId="com.google.android.gm:id/fab").exists(timeout=3):
      d(resourceId="com.google.android.gm:id/fab").click()
  else:
      # Last resort: tap bottom-right FAB area
      d.click(0.9, 0.9)
  time.sleep(2.0)

To field — compose screen must be open first:
  to_field = None
  for rid in ["com.google.android.gm:id/to",
              "com.google.android.gm:id/people_edit_text",
              "com.google.android.gm:id/recipient_text_view"]:
      if d(resourceId=rid).exists(timeout=3):
          to_field = d(resourceId=rid); break
  if to_field is None:
      if d(className="android.widget.MultiAutoCompleteTextView").exists(timeout=2):
          to_field = d(className="android.widget.MultiAutoCompleteTextView")
      else:
          to_field = d(className="android.widget.EditText")
  to_field.click(); to_field.set_text(recipient_email); d.press("enter"); time.sleep(0.5)

Subject: d(resourceId="com.google.android.gm:id/subject") or d(className="android.widget.EditText")[1]
Body: tap (0.5, 0.65) → last EditText on screen
Send: d(resourceId="com.google.android.gm:id/send") or d(description="Send")
For fill tasks: use email_subject and email_body variables (pre-declared) — NEVER hardcode values
""",

    "youtube": """
YOUTUBE PLAYBOOK:
IMPORTANT: description='Search' does NOT exist on this device — use resourceId.

Open search bar:
  if d(resourceId="com.google.android.youtube:id/menu_item_1").exists(timeout=3):
      d(resourceId="com.google.android.youtube:id/menu_item_1").click()
  elif d(resourceId="com.google.android.youtube:id/toolbar_search_button").exists(timeout=3):
      d(resourceId="com.google.android.youtube:id/toolbar_search_button").click()
  else:
      # Tap top-right search area by coordinate
      d.click(0.92, 0.05)
  time.sleep(1.0)

Type query (search input field):
  search_box = None
  for rid in ["com.google.android.youtube:id/search_edit_text",
              "com.google.android.youtube:id/youtube_query_text_view"]:
      if d(resourceId=rid).exists(timeout=3):
          search_box = d(resourceId=rid); break
  if search_box is None:
      search_box = d(className="android.widget.EditText")
  search_box.click(); search_box.set_text(search_query)
  d.press("search"); time.sleep(2.5)

Click first video result (titles are in LABEL section of snapshot):
  # Try textContains with first few words of the query
  words = search_query.split()[:3]
  for w in words:
      if d(textContains=w, clickable=True).exists(timeout=2):
          d(textContains=w, clickable=True)[0].click(); break
  else:
      # Fallback: first clickable ViewGroup below search bar
      d(className="android.view.ViewGroup", clickable=True)[2].click()
""",

    "play_store": """
PLAY STORE PLAYBOOK:
Search bar: d(resourceId="com.android.vending:id/search_bar_hint") or
            d(description="Search for apps & games")
Type: d(className="android.widget.EditText").set_text(search_query)
Submit: d.press("search") → time.sleep(2.0)
First result: d(resourceId="com.android.vending:id/li_title")[0].click() OR
              first clickable TextView
Install: d(text="Install") or d(text="Get") — click and wait
Wait for install: poll d(text="Open").exists() up to 60s
""",

    "maps": """
MAPS PLAYBOOK:
Search: d(resourceId="com.google.android.apps.maps:id/search_omnibox_text_field")
        or d(description="Search here")
Type destination: .set_text(search_query) → d.press("search") → time.sleep(2.0)
Directions: d(description="Directions") or d(text="Directions")
""",

    "google_calendar": """
GOOGLE CALENDAR PLAYBOOK:
New event: d(description="Create new event") or d(resourceId="...fab")
Title: first EditText
Date/time: tap date/time fields and use the pickers
Save: d(text="Save") or d(description="Save")
""",
}

def _get_app_playbook(app: str) -> str:
    """Return the playbook for the given app, or empty string for unknown apps."""
    key = app.lower().replace(" ", "_").replace("-", "_")
    # Try exact match first, then partial match
    if key in _APP_PLAYBOOKS:
        return _APP_PLAYBOOKS[key]
    for k, v in _APP_PLAYBOOKS.items():
        if k in key or key in k:
            return v
    return ""   # Unknown app — LLM reasons from snapshot alone


# ══════════════════════════════════════════════════════════════════════════════
#  LLM SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

# FIX #4: Added explicit "NEVER use desc=" rule — uiautomator2 uses description=
# FIX #5: Added Chrome-specific enter/search pattern
# FIX #6: Strengthened .exists() guard rule
# FIX #7: Added hard MAX LINES rule

_CODEGEN_BASE_PROMPT = textwrap.dedent(
    """    You are an expert Android automation engineer.
    Given a task, parameters, and a UI snapshot, write a complete uiautomator2 Python script.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ENVIRONMENT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    • `d` = connected uiautomator2 device. `time` and `re` already imported.
    • Param variables are pre-declared (alarm_hour, email_subject, search_query, etc.)
    • DO NOT add imports or reassign `d`. DO NOT call app_start().

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    REASONING FROM THE SNAPSHOT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    The snapshot lists every interactive element on screen RIGHT NOW.
    It has two sections: INTERACTIVE ELEMENTS and VISIBLE TEXT labels.

    Before writing code, ask: "Which element in the snapshot maps to what I need?"
    - "search" intent → find EditText, SearchView, elements with "search" in desc/text
    - "tap X" → element with text="X" or description="X"
    - "type in field Y" → EditText near label Y
    - "install/download" → button with text "Install", "Get", "Download"
    - Can't find it? It appears after a click → use .exists(timeout=N) guard

    SELECTOR PRIORITY (most stable → least):
    1. d(resourceId="exact_rid")          ← only from snapshot, never invent
    2. d(description="exact_desc")        ← use description=, NEVER desc=
    3. d(text="exact_text")
    4. d(textContains="partial")
    5. d(className="...")[index]          ← last resort

    VALID selector keywords: resourceId, text, textContains, textStartsWith,
    description, className, index, instance, scrollable, clickable, focusable
    !! NEVER use: hint=, placeholder=, label=, id=, name= !!

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    POST-NAVIGATION ELEMENTS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Elements that appear AFTER a click are NOT in the snapshot.
    Always wrap them in .exists(timeout=N) guards:
        if d(resourceId="android:id/input_hour").exists(timeout=4):
            d(resourceId="android:id/input_hour").set_text("5")
    NEVER call .wait() on an invented selector — it hangs forever.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    INTERACTIONS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    d(sel).click()    d(sel).set_text("v")    d(sel).clear_text()
    d(sel).exists(timeout=N)
    d.press("back"/"home"/"enter"/"search")
    d.click(x_pct, y_pct)    d.swipe_ext("up")
    d(scrollable=True).scroll.to(text="X")

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    EXTRACTING SCREEN CONTENT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    For extract/read tasks, print content BEFORE TASK_COMPLETE:
        results = []
        for el in d(className="android.widget.TextView"):
            try:
                txt = el.get_text()
                if txt and len(txt.strip()) > 3:
                    results.append(txt.strip())
            except: pass
        seen = set(); unique = [r for r in results if r not in seen and not seen.add(r)]
        if unique:
            print("SEARCH_RESULTS:")
            for i, r in enumerate(unique[:10], 1): print(f"{i}. {r}")

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    MANDATORY RULES
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    1. DO NOT call app_start() — app already open.
    2. ALWAYS end with: print("TASK_COMPLETE")
    3. Guard permission dialogs: if d(text="Allow").exists(timeout=2): d(text="Allow").click()
    4. Use set_text(), never character-by-character typing.
    5. After d.press("enter"/"search"): time.sleep(2.0)
    6. Do NOT use try/except — let errors surface for retry engine.
    7. Output ONLY raw Python. No markdown, no comments.
    8. MAX 25 LINES. Simple tasks need simple scripts.
    9. Param variables are in scope — use them directly.
    """
).strip()


def _build_codegen_system_prompt(app: str) -> str:
    """
    Build the system prompt for a specific app by combining the base prompt
    with the app-specific playbook (if one exists).

    This replaces the old 300-line monolith:
    - Base prompt: ~70 lines of reasoning principles, valid for ALL apps
    - Playbook: 10-20 lines of app-specific patterns, injected only when needed
    - Unknown apps: no playbook — LLM reads snapshot and reasons from scratch
    """
    playbook = _get_app_playbook(app)
    if playbook:
        playbook_section = (
            f"\n    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            f"\n    APP-SPECIFIC PLAYBOOK: {app.upper()}"
            f"\n    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            f"\n{playbook}"
        )
        return _CODEGEN_BASE_PROMPT + playbook_section
    return _CODEGEN_BASE_PROMPT


# Keep backward-compat alias pointing at the base (used in tests/logging)
_CODEGEN_SYSTEM_PROMPT = _CODEGEN_BASE_PROMPT


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

        self.cache           = CacheAdapter()
        self.param_extractor = ParameterExtractor()
        self.placeholderizer = PlaceholderExtractor()
        self._executor:      Optional[CodeExecutor] = None
        # action_history: referenced by mobile_action_handler.py for step logging.
        self.action_history: List[Tuple[str, bool]] = []

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
                self.model      = "llama3.1-8b"   # 120B params, 3000 t/s — much better code quality
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

    async def _is_app_foreground(self, package: str) -> bool:
        """Return True if `package` is currently the foreground app."""
        if not package:
            return False
        serial = self._resolve_serial()
        def _check() -> bool:
            dev = u2.connect(serial) if serial else u2.connect()
            try:
                info = dev.app_current()
                current_pkg = (info or {}).get("package", "")
                return current_pkg == package or current_pkg.startswith(package)
            except Exception:
                return False
        try:
            return await asyncio.wait_for(asyncio.to_thread(_check), timeout=5.0)
        except Exception:
            return False

    async def _launch_app_and_wait(self, package: str, min_elements: int = 8) -> None:
        """
        Launch `package` via ADB and poll until the app UI is actually loaded.

        IMPORTANT: If the app is already in the foreground, skip app_start().
        Calling app_start() when Gmail has a compose screen open will save the
        draft and return to the inbox — destroying any partially-filled form.

        Strategy: check foreground first. If already there, just verify elements
        are loaded. If not, start the app and poll until ready.
        """
        if not package:
            return
        serial = self._resolve_serial()

        # ── Skip app_start if already in foreground ───────────────────────
        already_running = await self._is_app_foreground(package)
        if already_running:
            # Verify the screen has enough elements to be usable
            xml  = await self._fetch_xml_dump()
            snap = build_ui_snapshot(xml)
            if snap.count("\n") >= min_elements:
                logger.info(f"[LAUNCH] ✅ {package} already in foreground — skipping app_start")
                return
            # App is in foreground but screen is loading — brief wait
            logger.info(f"[LAUNCH] {package} in foreground but screen loading — waiting 1.5s")
            await asyncio.sleep(1.5)
            return

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
            f"TASK: {task_text}\n"
            f"APP:  {app}  (package: {package or 'unknown'})\n"
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
        # Use the dynamic system prompt (base + app playbook)
        system_prompt = _build_codegen_system_prompt(app)
        logger.debug(f"[LLM] System prompt: {len(system_prompt)} chars | app={app}")

        response = await self._llm_chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
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
        try:
            self.cache.store_successful(
                code=code,
                task_text=task_text,
                app=app,
                package=package,
                params=params.raw,
                task_type=self.cache._inner._infer_task_type(task_text),
            )
        except Exception as e:
            logger.warning(f"[CACHE] store_successful failed (non-fatal): {e}")

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

        # Respect explicit app_name from coordinator first, then fall back to
        # shared inference that knows about messages/whatsapp and other apps.
        explicit_app = (task.extra_params or {}).get("app_name", "").strip()
        app, package = infer_app(task.ai_prompt, explicit_app)
        logger.info(f"[APP] app={app!r} | package={package!r}")

        executor = self._get_executor()

        # ── Cache lookup ──────────────────────────────────────────────────
        cache_hit = self.cache.lookup(task.ai_prompt, app)
        if cache_hit:
            template, score = cache_hit
            missing = self.cache._placeholderizer.missing_keys(
                template.code_template, params.raw
            )
            # Also respect stored parameter_schema: some templates may reference
            # params as variables (email_subject) rather than {placeholders}.
            # Ensure required schema keys are present at runtime.
            schema_keys = set()
            try:
                schema_keys = set(getattr(template, "parameter_schema", {}) or {})
            except Exception:
                schema_keys = set()
            schema_missing = sorted(schema_keys - set(params.raw.keys())) if schema_keys else []
            if schema_missing:
                missing = sorted(set(missing) | set(schema_missing))
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
                        elapsed_ms = int((time.monotonic() - t0) * 1000)

                        # Verify outcome for tasks where cached code can silently
                        # produce wrong results (e.g. alarm set to default time).
                        # For clock/alarm tasks, the old cached template may have
                        # been stored before the mode-switch fix and still taps a
                        # dial that ignores set_text(). We catch this here.
                        needs_verify = app == "clock" or any(
                            k in task.ai_prompt.lower()
                            for k in ("alarm", "set the alarm", "time to")
                        )
                        if needs_verify:
                            _settle = 1.5
                            logger.info(f"[T1] Settling {_settle}s before verify (alarm task)...")
                            await asyncio.sleep(_settle)
                            verified, reason = await self._verify_task_completion(
                                task.ai_prompt, app, params
                            )
                            if not verified:
                                logger.warning(
                                    f"[T1] Cache hit verification FAILED: {reason} "
                                    f"— invalidating template and regenerating"
                                )
                                self.cache.mark_failure(template.template_id)
                                # Force reliability below threshold so it won't be used again
                                t = self.cache._store.get(template.template_id)
                                if t:
                                    t.failure_count += 3   # penalise heavily
                                    self.cache._save()
                                # Fall through to LLM generation below
                            else:
                                self.cache.mark_success(template.template_id)
                                logger.info(f"✅ [T1] Cache hit success + verified | {elapsed_ms}ms | {reason}")
                                stdout_content = (outcome.stdout or "").strip()
                                content_lines = [l for l in stdout_content.splitlines()
                                                 if l.strip() and l.strip() != "TASK_COMPLETE"]
                                cr = "\n".join(content_lines) if content_lines else f"Template cache (sim={score:.2f})"
                                return self._build_result(
                                    task.task_id, "success", 1, elapsed_ms,
                                    completion_reason=cr,
                                )
                        else:
                            self.cache.mark_success(template.template_id)
                            logger.info(f"✅ [T1] Cache hit success | {elapsed_ms}ms")
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

        # Email apps (gmail) need more elements before we consider the screen ready
        _snap_min = 15 if app in ("gmail", "email") else _MIN_SNAPSHOT_ELEMENTS

        async def _live_snapshot(label: str, min_elems: int = _snap_min) -> str:
            # More retries and longer waits for email app which loads slowly
            max_tries = 5 if app in ("gmail", "email") else 3
            for snap_try in range(max_tries):
                xml  = await self._fetch_xml_dump()
                snap = build_ui_snapshot(xml)
                elem_count = snap.count("\n")
                if elem_count >= min_elems or snap_try == max_tries - 1:
                    logger.info(f"[DEVICE] {label} snapshot: ~{elem_count} elements (try {snap_try + 1})")
                    return snap
                wait = _SNAPSHOT_RETRY_DELAY * (snap_try + 1)  # progressive backoff
                logger.warning(
                    f"[DEVICE] {label} snapshot has only ~{elem_count} elements "
                    f"— retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)
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
                # Guard: if stdout contains MODE_SWITCH_FAILED the script signalled
                # a soft failure (was using exit() before sys.exit(1) fix) — treat as failure
                if "MODE_SWITCH_FAILED" in (outcome.stdout or ""):
                    logger.warning(f"[T2/T3] stdout contains MODE_SWITCH_FAILED — treating as failure")
                    outcome = ExecutionOutcome(
                        success=False,
                        error_summary="MODE_SWITCH_FAILED: keyboard mode toggle not found; "
                                      "see snapshot and try coordinate or selector alternatives",
                    )
                    error_ctx = (
                        "CRITICAL: The clock mode switch FAILED. EditText fields never appeared. "
                        "The keyboard toggle icon was NOT found at any of the 12 grid positions. "
                        "Read the snapshot carefully — look for a small icon near the time display "
                        "that could be the keyboard toggle. Try d.click() at different coordinates "
                        "based on what you see. Do NOT call set_text on the dial — it silently ignores it."
                    )
                    continue
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
            pkg_name = APP_PACKAGES.get(app, "")

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

                # For "send" tasks: first validate fields, then check compose is gone
                if task_is_send:
                    # Pre-send check: look for empty required fields warning
                    # Gmail shows "Please add a recipient" or leaves compose open
                    error_indicators = [
                        "please add a recipient",
                        "add a recipient",
                        "subject is required",
                        "recipient required",
                    ]
                    for err in error_indicators:
                        if err in snap_low:
                            return False, f"Gmail validation error visible: {err}"

                    compose_gone = "to" not in snap_low[:200] and "subject" not in snap_low[:200]
                    has_compose_btn = "compose" in snap_low
                    if compose_gone and has_compose_btn:
                        return True, "Compose screen closed — email sent"
                    if "send" in snap_low and ("to" in snap_low or "subject" in snap_low):
                        return False, "Compose screen still visible — check all fields are filled"
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

            # ── MESSAGES ─────────────────────────────────────────────────
            if app == "messages" or any(k in task_low for k in (
                "messages app", "message thread", "sms", "send a message",
                "in the messages",
            )):
                pkg = "com.google.android.apps.messaging"
                if pkg not in snap_low and "messaging" not in snap_low:
                    return False, "Messages app not in UI hierarchy — app may not have opened"
                if "start_chat_fab" in snap_low or "start chat" in snap_low:
                    return True, "Messages conversation list visible"
                if "compose_message_text" in snap_low or "message_text" in snap_low:
                    return True, "Messages compose field visible — inside chat"
                if "messaging" in snap_low and elem_count >= 8:
                    return True, f"Messages app active ({elem_count} elements)"
                return False, "Messages: UI not confirmed — package not in hierarchy"

            # ── WHATSAPP ─────────────────────────────────────────────────
            if app == "whatsapp" or "whatsapp" in task_low:
                if "com.whatsapp" not in snap_low and "whatsapp" not in snap_low:
                    return False, "WhatsApp not in UI hierarchy"
                return True, f"WhatsApp active ({elem_count} elements)"

            # ── GENERIC FALLBACK ─────────────────────────────────────────
            if pkg_name and pkg_name in snap_low:
                return True, f"Package {pkg_name} confirmed in hierarchy"

            if elem_count >= 20:
                return True, f"Substantial UI ({elem_count} elements) — trusting TASK_COMPLETE"

            if any(k in task_low for k in ("open", "launch", "start")):
                return False, f"Could not confirm app opened (only {elem_count} elements, package not in hierarchy)"

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
#  FIX #12 shared infer_app: messages/whatsapp and explicit app handling
#          App inference now comes from mobile_template_cache.infer_app(),
#          which correctly handles explicit app_name plus messages/whatsapp.