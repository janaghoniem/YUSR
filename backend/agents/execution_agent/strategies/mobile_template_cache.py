"""
mobile_template_cache.py — ChromaDB-backed template cache for mobile UI automation.

BUGS FIXED vs previous version:
  FIX A  infer_app() — "messages" and "whatsapp" were missing from the keyword
         map. This caused app="unknown", package="", and _launch_app_and_wait("")
         to be a no-op. The LLM then generated code against the currently visible
         app (the AURA app itself), typing into AURA's text field instead.

  FIX B  extract_harness_body() — nested helper functions (e.g. _switch_to_text_mode)
         contain `return` statements. Extracting the full body as flat top-level code
         caused SyntaxError: 'return' outside function, which immediately failed
         and marked the template unreliable on first use. Fix: strip nested def
         blocks entirely during extraction; leave only the call site code.

  FIX C  Verification keyword matching — "Task keywords found on screen: ['messages']"
         was confirming success when 'messages' appeared anywhere in the UI text
         (including the AURA app's own interface). Verification for app-specific
         tasks now requires the app's package name to appear in the hierarchy.
         (Applied in mobile_strategy_codegen.py — see comment at bottom.)

  FIX D  Cross-app fallback penalty raised from 1.3× to 1.8× and now only
         activates when candidates=0. Previously, app="unknown" triggered an
         unfiltered query that found a contacts template at dist=0.47 < threshold,
         executing it for a Messages task.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.utils import embedding_functions
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False
    logger.warning("chromadb not installed — cache will be a no-op. pip install chromadb")


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_CACHE_DIR = Path(__file__).parent / "task_memory_db"
CHROMA_PATH: Path = Path(
    os.getenv("MOBILE_CACHE_DIR", str(_DEFAULT_CACHE_DIR))
).expanduser()

COLLECTION_NAME = "mobile_task_templates"

# Cosine distance threshold (1 - similarity). 0.45 → requires ≥0.55 similarity.
DISTANCE_THRESHOLD: float = float(os.getenv("CACHE_DISTANCE_THRESHOLD", "0.45"))

TASK_TYPES = {"launch", "navigate", "fill", "action", "confirm", "extract", "search"}

_INTERNAL_KEYS: Set[str] = {
    "input_from", "device_id", "app_name", "package_name", "file_path",
    "max_steps", "timeout_seconds", "language", "overall_goal", "goal",
    "session_id", "uiautomator_port", "input_content",
}


# ══════════════════════════════════════════════════════════════════════════════
#  FIX A — COMPLETE APP INFERENCE MAP
# ══════════════════════════════════════════════════════════════════════════════

APP_PACKAGES: Dict[str, str] = {
    "gmail":           "com.google.android.gm",
    "chrome":          "com.android.chrome",
    "clock":           "com.google.android.deskclock",
    "contacts":        "com.google.android.contacts",
    "play_store":      "com.android.vending",
    "youtube":         "com.google.android.youtube",
    "maps":            "com.google.android.apps.maps",
    "google docs":     "com.google.android.apps.docs.editors.docs",
    "google calendar": "com.google.android.calendar",
    "settings":        "com.android.settings",
    "messages":        "com.google.android.apps.messaging",   # FIX A
    "whatsapp":        "com.whatsapp",                        # FIX A
    "camera":          "com.android.camera2",
    "calculator":      "com.google.android.calculator",
    "dialer":          "com.google.android.dialer",
    "phone":           "com.google.android.dialer",
    "spotify":         "com.spotify.music",
    "files":           "com.google.android.apps.nbu.files",
}

# Order matters — first match wins. More specific phrases before generic ones.
_APP_KEYWORD_RULES: List[Tuple[Tuple[str, ...], str]] = [
    # Messaging — must come BEFORE generic "contact" patterns
    (("whatsapp",), "whatsapp"),
    (("messages app", "in the messages", "via messages", "messages app",
      "sms", "text message", "message thread", "messaging app",
      "message field", "send a message", "message to haya",
      "open messages", "message input"), "messages"),
    # Email
    (("gmail", "compose email", "send email", "recipient", "subject",
            "inbox", "email app", " email ", "open gmail",
            "composing", "composition", "compose a new", "start composing",
            "new email composition"), "gmail"),
    # Clock / Alarm
    (("alarm", "clock app", "stopwatch", "timer", "open clock",
      "open the clock"), "clock"),
    # Contacts — only the Contacts app, not a contact person name
    (("contacts app", "open contacts", "add contact", "create contact",
      "contact name field", "phone number field", "contact form",
      "contact screen"), "contacts"),
    # Browser
    (("chrome", "browser", "search the web", "google.com", "url bar",
      "address bar"), "chrome"),
    # Play Store
    (("play store", "install app", "google play", "download app",
      "app store", "download the app", "install the app"), "play_store"),
    # YouTube
    (("youtube", "watch video", "play video"), "youtube"),
    # Maps
    (("google maps", "maps app", "directions to", "find on map",
      "get directions", "open maps"), "maps"),
    # Calendar
    (("google calendar", "calendar app", "calendar event",
      "schedule event"), "google calendar"),
    # Docs
    (("google docs", " docs ", "document title"), "google docs"),
    # Settings
    (("settings app", "open settings", "device settings"), "settings"),
]


def infer_app(task_text: str, explicit_app: str = "") -> Tuple[str, str]:
    """
    FIX A: Returns (app_name, package_name).
    explicit_app (from coordinator extra_params.app_name) takes priority.
    """
    if explicit_app:
        ea = explicit_app.lower().strip()
        if ea in APP_PACKAGES:
            return ea, APP_PACKAGES[ea]
        for app_key in APP_PACKAGES:
            if ea in app_key or app_key in ea:
                return app_key, APP_PACKAGES[app_key]

    t = task_text.lower()
    for keywords, app in _APP_KEYWORD_RULES:
        if any(k in t for k in keywords):
            return app, APP_PACKAGES.get(app, "")

    return "unknown", ""


# ══════════════════════════════════════════════════════════════════════════════
#  DATA MODEL
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CodeTemplate:
    template_id:      str
    task_pattern:     str
    aliases:          List[str]
    app:              str
    package:          str
    task_type:        str
    code_template:    str
    parameter_schema: Dict[str, str]
    success_count:    int = 0
    failure_count:    int = 0
    created_at:       str = ""
    last_used:        str = ""

    @property
    def reliability(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.5

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "template_id":      self.template_id,
            "task_pattern":     self.task_pattern,
            "aliases":          json.dumps(self.aliases),
            "app":              self.app,
            "package":          self.package,
            "task_type":        self.task_type,
            "code_template":    self.code_template,
            "parameter_schema": json.dumps(self.parameter_schema),
            "success_count":    self.success_count,
            "failure_count":    self.failure_count,
            "created_at":       self.created_at or datetime.now().isoformat(),
            "last_used":        self.last_used or "",
        }

    @classmethod
    def from_metadata(cls, meta: Dict[str, Any]) -> "CodeTemplate":
        return cls(
            template_id=meta["template_id"],
            task_pattern=meta["task_pattern"],
            aliases=json.loads(meta.get("aliases", "[]")),
            app=meta["app"],
            package=meta["package"],
            task_type=meta.get("task_type", "action"),
            code_template=meta["code_template"],
            parameter_schema=json.loads(meta.get("parameter_schema", "{}")),
            success_count=int(meta.get("success_count", 0)),
            failure_count=int(meta.get("failure_count", 0)),
            created_at=meta.get("created_at", ""),
            last_used=meta.get("last_used", ""),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  PLACEHOLDER EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

class PlaceholderExtractor:
    PARAM_DESCRIPTIONS: Dict[str, str] = {
        "alarm_hour":        "str — zero-padded 12h hour e.g. '05'",
        "alarm_hour_no_pad": "str — unpadded hour e.g. '5'",
        "alarm_minute":      "str — zero-padded minute e.g. '30'",
        "alarm_period":      "str — 'AM' or 'PM'",
        "alarm_time":        "str — display e.g. '5:30 PM'",
        "recipient_email":   "str — email address",
        "email_subject":     "str — subject line",
        "email_body":        "str — email body text",
        "search_query":      "str — search terms",
        "contact_name":      "str — full name",
        "event_title":       "str — calendar event title",
        "event_date":        "str — ISO date e.g. '2026-10-07'",
        "phone_number":      "str — digits only",
        "input_value":       "str — generic input value",
        "target_url":        "str — full URL",
        "group_name":        "str — WhatsApp group name",
        "message_text":      "str — message body",
        "doc_title":         "str — document title",
    }

    def extract(
        self,
        code: str,
        params: Dict[str, str],
        task_text: str = "",
    ) -> Tuple[str, Dict[str, str]]:
        template = code
        schema: Dict[str, str] = {}
        for key, val in sorted(params.items(), key=lambda kv: -len(str(kv[1]))):
            if key in _INTERNAL_KEYS:
                continue
            val_str = str(val).strip()
            if len(val_str) < 2:
                continue
            placeholder = f"{{{key}}}"
            new_template = self._replace_in_string_literals(template, val_str, placeholder)
            if new_template != template:
                template = new_template
                schema[key] = self.PARAM_DESCRIPTIONS.get(key, f"str — value e.g. {val_str!r}")
        return template, schema

    @staticmethod
    def _replace_in_string_literals(code: str, value: str, placeholder: str) -> str:
        def _sub(m: re.Match) -> str:
            s = m.group(0)
            if value in s:
                inner = s[1:-1].replace(value, placeholder)
                return s[0] + inner + s[-1]
            return s
        return re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'').sub(_sub, code)

    def inject(self, template_code: str, params: Dict[str, str]) -> str:
        result = template_code
        for k, v in params.items():
            result = result.replace(f"{{{k}}}", str(v))
        result = re.sub(r"\.set_text\((\d+)\)", lambda m: f'.set_text("{m.group(1)}")', result)
        return result

    def missing_keys(self, template_code: str, params: Dict[str, str]) -> List[str]:
        placeholders = set(re.findall(r"\{(\w+)\}", template_code))
        return sorted(placeholders - set(params.keys()))


# ══════════════════════════════════════════════════════════════════════════════
#  FIX B — HARNESS BODY EXTRACTOR (strips nested def blocks)
# ══════════════════════════════════════════════════════════════════════════════

def extract_harness_body(fn) -> str:
    """
    FIX B: Extract harness function body as flat, runnable top-level code.

    Nested helper functions (like _switch_to_text_mode in set_alarm_time) contain
    `return` statements. When the full body is extracted as top-level code, those
    `return` statements cause SyntaxError: 'return' outside function.

    This extractor detects indented `def` blocks and removes their entire body,
    leaving only the outer function's statements (which may include calls to those
    helpers — those calls become no-ops since the helpers aren't defined, so we
    also remove call sites that reference stripped helpers).
    """
    import inspect
    src = inspect.getsource(fn)
    lines = src.splitlines()

    body_lines: List[str] = []
    in_body = False
    in_docstring = False
    docstring_char = ""
    in_nested_def = False
    nested_def_indent = 0
    stripped_helpers: Set[str] = set()

    for line in lines:
        stripped = line.strip()
        current_indent = len(line) - len(line.lstrip()) if line.strip() else 0

        if stripped.startswith("@"):
            continue
        if stripped.startswith("def ") and not in_body:
            in_body = True
            continue
        if not in_body:
            continue

        # Skip opening docstring
        if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
            docstring_char = stripped[:3]
            if stripped.count(docstring_char) >= 2:
                continue
            in_docstring = True
            continue
        if in_docstring:
            if docstring_char in stripped:
                in_docstring = False
            continue

        # Detect nested def — capture helper name and skip its entire body
        if stripped.startswith("def ") and in_body:
            helper_name = re.match(r"def\s+(\w+)", stripped)
            if helper_name:
                stripped_helpers.add(helper_name.group(1))
            in_nested_def = True
            nested_def_indent = current_indent
            continue

        if in_nested_def:
            # Still inside nested def while more indented than the def line
            if stripped and current_indent > nested_def_indent:
                continue
            else:
                in_nested_def = False
                # Don't skip this line — it's the first line after the nested def

        body_lines.append(line)

    code = textwrap.dedent("\n".join(body_lines)).strip()

    # Remove device-connection boilerplate
    code = re.sub(r"^\s*d\s*=\s*connect\([^\)]*\)\s*\n?", "", code, flags=re.MULTILINE)
    # Remove app_start / open_*_app calls
    code = re.sub(r"^\s*(?:open_\w+_app|d\.app_start)\([^\)]*\)\s*\n?", "", code, flags=re.MULTILINE)
    # Remove bare launch-wait sleeps
    code = re.sub(r"^\s*time\.sleep\(\s*[23]\.[05]\s*\)\s*\n?", "", code, flags=re.MULTILINE)
    # Replace case.attr / config.attr with placeholder tokens
    code = re.sub(r"\bcase\.\w+\b", lambda m: _resolve_case_attr(m.group(0)), code)
    code = re.sub(r"\bconfig\.\w+\b", '""', code)

    # Remove calls to stripped helper functions
    for helper in stripped_helpers:
        # Remove: if not _helper(): / _helper() / if _helper():
        code = re.sub(
            rf"^\s*(?:if\s+(?:not\s+)?)?{re.escape(helper)}\([^\)]*\)[^\n]*\n?",
            "",
            code,
            flags=re.MULTILINE,
        )

    # Clean up blank lines
    code = "\n".join(l for l in code.splitlines() if l.strip())

    if not code.strip():
        return '# (empty body)\nprint("TASK_COMPLETE")'

    if not code.rstrip().endswith('print("TASK_COMPLETE")'):
        code += '\nprint("TASK_COMPLETE")'

    return code


def _resolve_case_attr(attr_ref: str) -> str:
    attr = attr_ref.split(".", 1)[-1]
    return {
        "hour":       "{alarm_hour}",
        "minute":     "{alarm_minute}",
        "period":     "{alarm_period}",
        "recipient":  "{recipient_email}",
        "subject":    "{email_subject}",
        "body":       "{email_body}",
        "name":       "{contact_name}",
        "phone":      "{phone_number}",
        "query":      "{search_query}",
        "group_name": "{group_name}",
        "message":    "{message_text}",
        "title":      "{event_title}",
        "date":       "{event_date}",
    }.get(attr, f'"{attr_ref}"')


# ══════════════════════════════════════════════════════════════════════════════
#  CHROMA TEMPLATE CACHE
# ══════════════════════════════════════════════════════════════════════════════

class ChromaTemplateCache:

    def __init__(self, persist_dir: Path = CHROMA_PATH) -> None:
        if not _CHROMA_AVAILABLE:
            raise RuntimeError("chromadb is not installed. pip install chromadb")
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
        )
        self._col = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )
        self._alias_col = self._client.get_or_create_collection(
            name=f"{COLLECTION_NAME}_aliases",
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"[CACHE] ChromaTemplateCache ready | "
            f"templates={self._col.count()} | "
            f"aliases={self._alias_col.count()} | "
            f"dir={persist_dir}"
        )

    @staticmethod
    def _infer_task_type(task_text: str) -> str:
        t = task_text.lower()
        # Compose/new-email tasks must be "action" — they open a compose screen.
        # Previously "start composing" matched "start " → "launch", which caused
        # the is_launch_task fast-path to fire and just call app_start() instead
        # of clicking the Compose FAB.
        if any(k in t for k in ("compose", "composing", "composition",
                                 "new email composition", "create new email")):
            return "action"
        # "find and tap" / "find and open" are navigate, not search
        if any(k in t for k in ("find and tap", "find and open", "search for the chat")):
            return "navigate"
        if any(k in t for k in ("open ", "launch ", "start the ", "start ")):
            return "launch"
        if any(k in t for k in ("navigate", "go to", "tap the", "click")):
            return "navigate"
        if any(k in t for k in ("fill ", "type ", "enter ", "write ",
                                 "input ", "set the alarm", "set the event",
                                 "set the document")):
            return "fill"
        if any(k in t for k in ("search for", "look up")):
            return "search"
        if any(k in t for k in ("confirm", "press ok", "press save", "tap send",
                                 "tap the send", "click send", "click the send")):
            return "confirm"
        if any(k in t for k in ("extract", "read", "get the", "what is")):
            return "extract"
        return "action"

    # Pairs (query_type, template_type) that are fundamentally incompatible.
    _INCOMPATIBLE: Set[Tuple[str, str]] = {
        ("fill",     "launch"),
        ("fill",     "confirm"),
        ("fill",     "navigate"),
        ("confirm",  "fill"),
        ("confirm",  "launch"),
        ("confirm",  "navigate"),
        ("confirm",  "search"),
        ("search",   "launch"),
        ("search",   "confirm"),
        ("launch",   "fill"),
        ("launch",   "confirm"),
        ("launch",   "search"),
        ("navigate", "fill"),
        ("navigate", "confirm"),
    }

    def _type_penalty(self, query_type: str, tmpl_type: str) -> float:
        if not query_type or not tmpl_type or query_type == tmpl_type:
            return 0.0
        if (query_type, tmpl_type) in self._INCOMPATIBLE:
            return float("inf")
        return 0.15

    def add(self, template: CodeTemplate) -> None:
        self._col.upsert(
            ids=[template.template_id],
            documents=[template.task_pattern],
            metadatas=[template.to_metadata()],
        )
        if template.aliases:
            self._alias_col.upsert(
                ids=[f"{template.template_id}__alias_{i}" for i in range(len(template.aliases))],
                documents=template.aliases,
                metadatas=[{"template_id": template.template_id, "app": template.app}
                           for _ in template.aliases],
            )
        logger.info(
            f"[CACHE] Stored '{template.task_pattern[:55]}' | "
            f"app={template.app} | aliases={len(template.aliases)} | "
            f"id={template.template_id[:8]}"
        )

    def lookup(
        self,
        task_text: str,
        app: str,
        task_type: Optional[str] = None,
        n_candidates: int = 6,
    ) -> Optional[Tuple[CodeTemplate, float]]:
        inferred_type = task_type or self._infer_task_type(task_text)
        candidates: List[Tuple[str, float]] = []
        app_filter = {"app": app} if app != "unknown" else None

        # Pass 1: canonical collection, app-filtered
        try:
            n = min(n_candidates, max(1, self._col.count()))
            kwargs: Dict[str, Any] = dict(
                query_texts=[task_text], n_results=n, include=["distances"]
            )
            if app_filter:
                kwargs["where"] = app_filter
            res = self._col.query(**kwargs)
            for tid, dist in zip(res["ids"][0], res["distances"][0]):
                candidates.append((tid, dist))
        except Exception as e:
            logger.debug(f"[CACHE] Pass 1 error: {e}")

        # Pass 2: alias collection, app-filtered
        try:
            n_a = min(n_candidates, max(1, self._alias_col.count()))
            kwargs = dict(
                query_texts=[task_text], n_results=n_a,
                include=["distances", "metadatas"]
            )
            if app_filter:
                kwargs["where"] = app_filter
            alias_res = self._alias_col.query(**kwargs)
            for alias_meta, dist in zip(alias_res["metadatas"][0], alias_res["distances"][0]):
                real_tid = alias_meta.get("template_id", "")
                if real_tid:
                    candidates.append((real_tid, dist))
        except Exception as e:
            logger.debug(f"[CACHE] Pass 2 error: {e}")

        # FIX D: Pass 3 cross-app fallback — only when zero candidates, heavy penalty
        if not candidates:
            try:
                n = min(n_candidates, max(1, self._col.count()))
                res = self._col.query(
                    query_texts=[task_text], n_results=n, include=["distances"]
                )
                for tid, dist in zip(res["ids"][0], res["distances"][0]):
                    candidates.append((tid, dist * 1.8))  # FIX D: was 1.3
            except Exception as e:
                logger.debug(f"[CACHE] Pass 3 error: {e}")

        if not candidates:
            logger.info(f"[CACHE] MISS (no candidates) | query='{task_text[:55]}'")
            return None

        best_template: Optional[CodeTemplate] = None
        best_score: float = float("inf")
        seen_ids: Set[str] = set()
        debug_lines: List[str] = []

        for tid, raw_dist in candidates:
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            tmpl = self._fetch_by_id(tid)
            if tmpl is None:
                continue

            score = raw_dist
            penalty = self._type_penalty(inferred_type, tmpl.task_type)
            if penalty == float("inf"):
                debug_lines.append(
                    f"  REJECT type={tmpl.task_type} query={inferred_type} "
                    f"dist={raw_dist:.3f} '{tmpl.task_pattern[:45]}'"
                )
                continue
            score += penalty

            total = tmpl.success_count + tmpl.failure_count
            if total >= 3 and tmpl.reliability < 0.5:
                score += 0.15

            debug_lines.append(
                f"  id={tid[:8]} dist={raw_dist:.3f} adj={score:.3f} "
                f"type={tmpl.task_type} ok={tmpl.success_count}/{tmpl.failure_count} "
                f"'{tmpl.task_pattern[:45]}'"
            )

            if score < best_score:
                best_score = score
                best_template = tmpl

        if best_template is None or best_score > DISTANCE_THRESHOLD:
            logger.info(
                f"[CACHE] MISS | best={best_score:.3f} > {DISTANCE_THRESHOLD} | "
                f"inferred_type={inferred_type} | query='{task_text[:55]}'\n"
                + "\n".join(debug_lines)
            )
            return None

        similarity = round(1 - best_score, 3)
        logger.info(
            f"[CACHE] HIT | sim={similarity:.3f} | "
            f"'{best_template.task_pattern[:55]}' | "
            f"app={best_template.app} | type={best_template.task_type}"
        )
        return best_template, similarity

    def _fetch_by_id(self, template_id: str) -> Optional[CodeTemplate]:
        try:
            res = self._col.get(ids=[template_id], include=["metadatas"])
            if res["metadatas"]:
                return CodeTemplate.from_metadata(res["metadatas"][0])
        except Exception as e:
            logger.debug(f"[CACHE] fetch_by_id error: {e}")
        return None

    def mark_success(self, template_id: str) -> None:
        tmpl = self._fetch_by_id(template_id)
        if not tmpl:
            return
        tmpl.success_count += 1
        tmpl.last_used = datetime.now().isoformat()
        self._col.upsert(
            ids=[template_id],
            documents=[tmpl.task_pattern],
            metadatas=[tmpl.to_metadata()],
        )

    def mark_failure(self, template_id: str) -> None:
        tmpl = self._fetch_by_id(template_id)
        if not tmpl:
            return
        tmpl.failure_count += 1
        self._col.upsert(
            ids=[template_id],
            documents=[tmpl.task_pattern],
            metadatas=[tmpl.to_metadata()],
        )

    def stats(self) -> str:
        n = self._col.count()
        if n == 0:
            return "ChromaTemplateCache(empty)"
        try:
            all_meta = self._col.get(include=["metadatas"])["metadatas"]
            rels = [
                int(m.get("success_count", 0)) /
                max(1, int(m.get("success_count", 0)) + int(m.get("failure_count", 0)))
                for m in all_meta
            ]
            return f"ChromaTemplateCache(n={n} avg_reliability={sum(rels)/len(rels):.0%})"
        except Exception:
            return f"ChromaTemplateCache(n={n})"

    def export_json(self, path: Path) -> None:
        res = self._col.get(include=["metadatas", "documents"])
        templates = [CodeTemplate.from_metadata(m) for m in res["metadatas"]]
        path.write_text(json.dumps(
            {"version": 2, "exported_at": datetime.now().isoformat(),
             "templates": [asdict(t) for t in templates]},
            indent=2, ensure_ascii=False,
        ))

    def import_json(self, path: Path, overwrite: bool = False) -> int:
        data = json.loads(path.read_text())
        imported = 0
        for record in data.get("templates", []):
            t = CodeTemplate(**{k: v for k, v in record.items()
                                if k in CodeTemplate.__dataclass_fields__})
            if self._fetch_by_id(t.template_id) and not overwrite:
                continue
            self.add(t)
            imported += 1
        return imported