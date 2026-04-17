"""
mobile_strategy.py

MobileReActStrategy — 3-Tier ReAct loop for Android UI automation.

Tier 1  Deterministic handlers        (0 tokens · 0 ms)
Tier 2  ChromaDB semantic retrieval   (0 tokens · ~5 ms)
Tier 3  LLM ReAct loop                (~400 ms/step)
"""

import asyncio
import inspect
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

from agents.utils.device_protocol import (
    MobileTaskRequest, MobileTaskResult, UIAction, ActionResult,
    SemanticUITree,
)
from agents.execution_agent.core.exec_agent_models import ExecutionResult
from agents.execution_agent.strategies.task_memory import (
    TaskMemory, RecipeStep, RetrievalResult,
    resolve_element, _signature_jaccard,
    THRESHOLD_EXECUTE, THRESHOLD_HINT, SIGNATURE_OVERLAP,
)

import httpx
import uiautomator2 as u2

logger = logging.getLogger(__name__)

# ── Module-level singleton ─────────────────────────────────────────────────
_shared_task_memory: Optional[TaskMemory] = None

# App-name resolution cache shared across strategy instances.
_app_name_cache: Dict[str, str] = {}

def _get_task_memory() -> TaskMemory:
    global _shared_task_memory
    if _shared_task_memory is None:
        _shared_task_memory = TaskMemory()
    return _shared_task_memory

# ── FIX 1: Session-level app memory ───────────────────────────────────────
_session_last_app: str = "unknown"
_session_last_id: str = ""

_LAUNCHER_PACKAGE_HINTS: Tuple[str, ...] = (
    "launcher", "systemui", "quickstep", "trebuchet", "pixel",
)

_SYSTEM_PACKAGES: Set[str] = {
    "com.android.systemui",
    "android",
}

_SUGGESTION_CONTROL_PREFIXES: Tuple[str, ...] = (
    "edit suggestion", "search suggestion",
    "refine:", "more:", "options for", "filter:", "sort by", "category:",
    "search for", "show predictions", "search settings", "clear search",
    "remove", "delete search", "voice search", "search by image",
    "search with", "find more", "related:", "suggested:", "trending",
)

_SUGGESTION_CONTROL_RESOURCE_IDS: Tuple[str, ...] = (
    "refine", "edit_query", "query_edit", "overflow", "more_options",
    "filter_chip", "category_chip", "settings_button", "voice_btn",
    "camera_btn", "discover", "feed", "option",
)

_SEARCH_BUTTON_SYNONYMS: Set[str] = {
    "click the search button", "press search", "submit search",
    "click search", "tap search button", "hit search",
}


def _sync_session_app_memory(session_id: str) -> None:
    """Reset app memory when session changes to avoid cross-session pollution."""
    global _session_last_app, _session_last_id
    sid = (session_id or "").strip() or "default_session"
    if _session_last_id and _session_last_id != sid:
        logger.info(
            f"[CACHE] Session changed ({_session_last_id} → {sid}) — resetting app memory"
        )
        _session_last_app = "unknown"
    _session_last_id = sid


def _is_non_target_package(pkg: str) -> bool:
    p = (pkg or "").lower()
    if not p:
        return True
    if "aura" in p:
        return True
    return any(h in p for h in _LAUNCHER_PACKAGE_HINTS)


def _set_session_last_app(app: str) -> None:
    global _session_last_app
    if app and app != "unknown":
        _session_last_app = app


def _normalize_app_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _infer_app_from_text(ai_prompt: str, overall_goal: str) -> str:
    t = f"{ai_prompt} {overall_goal}".lower()
    if any(k in t for k in ("gmail", "email", "compose", "recipient", "subject")):
        return "gmail"
    if any(k in t for k in ("alarm", "clock", "stopwatch", "timer")):
        return "clock"
    if any(k in t for k in ("contacts", "contact")):
        return "contacts"
    if any(k in t for k in ("play store", "app store", "install", "download app",
                            "google play", "apk", "talabat")):
        return "play_store"
    if any(k in t for k in ("youtube", "video", "watch")):
        return "youtube"
    if any(k in t for k in ("maps", "directions", "navigate to", "location")):
        return "maps"
    if any(k in t for k in ("chrome", "browser", "google.com", "search the web", "pharmacy")):
        return "chrome"
    return ""




# ── Generic page words — signals URL/descriptor not app name ───────────────
_GENERIC_PAGE_WORDS: Set[str] = {
    "page", "search", "default", "url", "website", "site", "content",
    "home", "tab", "view", "result", "results", "query", "screen",
    "address", "navigation", "navigate", "web",
}

_APP_PACKAGE_ALIASES: Dict[str, List[str]] = {
    "gmail": ["gm", "gmail", "googlemail"],
    "youtube": ["youtube"],
    "maps": ["maps", "waze", "citymapper"],
    "chrome": ["chrome", "chromium"],
    "play_store": ["vending", "play", "market"],
    "clock": ["clock", "deskclock", "alarmclock"],
    "contacts": ["contacts", "dialer"],
    "camera": ["camera"],
    "messages": ["messaging", "messages", "mms"],
    "whatsapp": ["whatsapp"],
    "spotify": ["spotify"],
    "notes": ["notes", "keep", "memo", "notepad"],
    "settings": ["settings"],
}

# ── Coordinator-internal extra_params keys ─────────────────────────────────
_INTERNAL_KEYS = {
    "input_from", "device_id", "app_name", "file_path",
    "max_steps", "timeout_seconds", "language",
}

_SIG_VERIFY_THRESHOLD = 0.50

_UIA2_CONNECT_TIMEOUT = 8.0
_UIA2_HIERARCHY_TIMEOUT = 12.0
_UIA2_INITIAL_RETRIES = 3
_UIA2_INITIAL_RETRY_DELAY = 2.0


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 2: smart timeout as module-level function (handler can call it too)
# ══════════════════════════════════════════════════════════════════════════════

def compute_smart_timeout(goal: str, default: int) -> int:
    g = goal.lower()
    if any(k in g for k in ("alarm", "schedule", "set time")): return max(90, default)
    if any(k in g for k in ("email", "compose", "recipient")): return max(90, default)
    if any(k in g for k in ("open", "launch", "start", "navigate")): return max(60, default)
    if any(k in g for k in ("chrome", "browser", "search", "fill", "pharmacy", "google.com", "type url")):
        return max(90, default)
    return max(45, default)


# ══════════════════════════════════════════════════════════════════════════════
#  PRUNED TREE + SIGNATURE
# ══════════════════════════════════════════════════════════════════════════════

def build_pruned_tree_string(ui_tree: SemanticUITree, max_elements: int = 30) -> str:
    w = max(ui_tree.screen_width,  1)
    h = max(ui_tree.screen_height, 1)
    lines = [f"Screen: {ui_tree.screen_name or ui_tree.app_name}"]

    qualifying = []
    for elem in ui_tree.elements:
        if elem.visibility != "visible":
            continue
        if not elem.clickable and not elem.focusable:
            continue
        if not elem.enabled and not elem.content_description:
            continue
        qualifying.append(elem)

    if len(qualifying) > max_elements:
        fields = [e for e in qualifying if e.focusable or e.type in ("textfield", "edittext")]
        buttons = [e for e in qualifying if e not in fields]
        budget = max(max_elements - len(fields), 8)
        selected = fields + buttons[:budget]
        selected.sort(key=lambda e: (
            e.bounds.get("top", 0) if e.bounds else 0,
            e.bounds.get("left", 0) if e.bounds else 0,
        ))
        qualifying = selected

    for elem in qualifying[:max_elements]:
        if elem.text:
            label = f'"{elem.text}"'
        elif elem.hint_text:
            label = f'[hint: {elem.hint_text}]'
        elif elem.content_description:
            label = f'"{elem.content_description}"'
        else:
            label = "(no text)"
        coord = ""
        if elem.bounds:
            cx = (elem.bounds.get("left", 0) + elem.bounds.get("right",  0)) // 2
            cy = (elem.bounds.get("top",  0) + elem.bounds.get("bottom", 0)) // 2
            coord = f" @({cx * 100 // w}%,{cy * 100 // h}%)"
        flags = []
        if elem.clickable:  flags.append("CLICKABLE")
        if elem.focusable:  flags.append("FOCUSABLE")
        if not elem.enabled: flags.append("DISABLED")
        flag_str = (" [" + "|".join(flags) + "]") if flags else ""
        lines.append(f"[id:{elem.element_id}] {elem.type.upper()} {label}{coord}{flag_str}")
    return "\n".join(lines)


def build_screen_signature(ui_tree: SemanticUITree) -> str:
    parts = []
    for e in ui_tree.elements:
        if not (e.clickable or e.focusable): continue
        cls  = (e.class_name or e.type or "").rsplit(".", 1)[-1]
        rid  = (e.resource_id or "")
        tail = rid.split(":id/")[-1] if ":id/" in rid else rid.rsplit("/", 1)[-1]
        if cls or tail:
            parts.append(f"{cls}:{tail}")
    parts.sort()
    return ",".join(parts[:40])


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════

class MobileReActStrategy:

    def __init__(
        self,
        device_id: str = "default_device",
        uiautomator_host: str = "http://localhost",
        uiautomator_port: int = 9008,
        llm_provider: str = "cerebras",
    ):
        self.device_id   = device_id
        self.uiautomator_base_url = f"{uiautomator_host.rstrip('/')}:{uiautomator_port}"
        self.uiautomator_timeout = 10.0
        self._u2_device = None
        self._u2_serial = ""
        self.llm_provider = (llm_provider or "cerebras").strip().lower()

        if self.llm_provider == "cerebras":
            try:
                from cerebras.cloud.sdk import Cerebras
                self.llm_client = Cerebras(api_key=(os.getenv("CEREBRAS_API_KEY") or ""))
                self.model = "llama3.1-8b"
                logger.info("✅ Using Cerebras model: llama3.1-8b")
            except ImportError:
                logger.error("❌ Cerebras SDK not installed. Run: pip install cerebras-cloud-sdk")
                raise
        else:
            from groq import AsyncGroq
            self.llm_client = AsyncGroq(api_key=(os.getenv("GROQ_API_KEY") or ""))
            self.model = "llama3.1-8b"

        self.current_ui_tree:      Optional[SemanticUITree]     = None
        self.previous_ui_trees:    List[SemanticUITree]         = []
        self.action_history:       List[Dict]                   = []
        self.device_state:         str                          = "unknown"
        self.stuck_counter:        int                          = 0
        self._prev_device_state:   str                          = "unknown"
        self.failed_elements:      Set[int]                     = set()
        self.last_clicked_element: Optional[int]                = None
        self.last_action_was_click:bool                         = False
        self.app_drawer_attempted: bool                         = False
        self._app_drawer_phase:    int                          = 0
        self.incomplete_ui_count:  int                          = 0
        self.element_retry_count:  Dict[int, int]               = {}
        self.MAX_ELEMENT_RETRIES:  int                          = 3
        self.typed_texts:          Dict[int, str]               = {}
        self.current_task:         Optional[MobileTaskRequest]  = None
        self._popup_tap_attempted: Set[str]                     = set()
        self._switched_to_text_input: bool                      = False
        self._keyboard_dismissed:  bool                         = False
        self._time_picker_pm_attempts: int                      = 0   # FIX 6
        self._add_alarm_clicked: bool                           = False
        self._add_alarm_screen_sig: str                         = ""
        self._search_just_submitted: bool                       = False
        self._search_needs_confirm: bool                        = False
        self._search_confirm_app: str                           = ""
        self._search_pre_element_count: int                     = 0
        self._email_just_sent: bool                             = False
        self._t2_completed_steps:  List[Dict]                   = []
        self._last_t2_hint_record_id: Optional[str]             = None
        self.token_usage:          Dict[str, int]               = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls:      int                          = 0
        self.total_ui_elements_seen: int                        = 0
        self.max_ui_elements_seen: int                          = 0
        self.ui_samples_count:     int                          = 0
        self.tier_stats:           Dict[str, int]               = {"tier1": 0, "tier2": 0, "tier3_llm": 0}
        self.recent_thoughts:      List[str]                    = []
        self.thought_loop_recoveries: int                       = 0
        self._initial_ui_signature: Optional[Tuple[int, str]]  = None
        self.task_memory = _get_task_memory()
        logger.info(
            f"✅ MobileReActStrategy ready | device={device_id} | "
            f"uiautomator={self.uiautomator_base_url} | memory={self.task_memory.stats()}"
        )

    async def _llm_chat_completion(self, **kwargs):
        """Provider-agnostic chat completion helper (supports sync and async SDKs)."""
        create_fn = self.llm_client.chat.completions.create
        if inspect.iscoroutinefunction(create_fn):
            return await create_fn(**kwargs)
        result = await asyncio.to_thread(lambda: create_fn(**kwargs))
        if inspect.isawaitable(result):
            return await result
        return result

    def _extract_xml_dump(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="ignore")
        if isinstance(payload, dict):
            for key in ("xml", "hierarchy", "dump", "data", "result"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return ""

    @staticmethod
    def _parse_bounds(bounds_raw: str) -> Optional[Dict[str, int]]:
        if not bounds_raw:
            return None
        match = re.match(
            r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
            bounds_raw.strip(),
        )
        if not match:
            return None
        return {
            "left": int(match.group(1)),
            "top": int(match.group(2)),
            "right": int(match.group(3)),
            "bottom": int(match.group(4)),
        }

    @staticmethod
    def _class_to_type(class_name: str, text: str = "", content_desc: str = "", resource_id: str = "") -> str:
        class_tail = (class_name or "").rsplit(".", 1)[-1].lower()
        rid_tail = (resource_id or "").rsplit("/", 1)[-1].lower()
        blob = f"{class_tail} {rid_tail} {text} {content_desc}".lower()

        if "edittext" in blob or "textfield" in blob or "input" in blob:
            return "textfield"
        if "imagebutton" in blob:
            return "imagebutton"
        if "button" in blob:
            return "button"
        if "switch" in blob:
            return "switch"
        if "checkbox" in blob:
            return "checkbox"
        if "radiobutton" in blob:
            return "radiobutton"
        if "scroll" in blob:
            return "scrollview"
        if "recyclerview" in blob:
            return "recyclerview"
        if "tab" in blob:
            return "tab"
        if any(token in blob for token in ("textview", "label", "title")):
            return "text"
        if class_tail:
            return class_tail
        return "view"

    def _parse_uia2_tree(self, payload: Any) -> Optional[SemanticUITree]:
        xml_text = self._extract_xml_dump(payload)
        if not xml_text:
            return None

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.debug(f"UI tree parse error: {exc}")
            return None

        elements: List[Dict[str, Any]] = []
        counter = 1
        screen_width = 0
        screen_height = 0
        app_package = root.attrib.get("package", "") or "unknown"
        app_name = app_package or root.attrib.get("class", "unknown") or "unknown"
        screen_name = root.attrib.get("class") or root.attrib.get("resource-id") or None
        package_stats: Dict[str, Dict[str, float]] = {}

        def visit(node: ET.Element, parent_id: Optional[int] = None) -> Optional[int]:
            nonlocal counter, screen_width, screen_height, app_package, app_name, screen_name

            attrs = node.attrib or {}
            bounds = self._parse_bounds(attrs.get("bounds", ""))
            if bounds:
                screen_width = max(screen_width, bounds["right"])
                screen_height = max(screen_height, bounds["bottom"])

            node_package = attrs.get("package", "") or ""
            if node_package and app_package == "unknown":
                app_package = node_package
                app_name = node_package

            class_name = attrs.get("class", "") or attrs.get("className", "") or ""
            resource_id = attrs.get("resource-id", "") or attrs.get("resourceId", "") or ""
            text = attrs.get("text", "") or ""
            content_desc = attrs.get("content-desc", "") or attrs.get("contentDescription", "") or ""
            hint_text = attrs.get("hint-text", "") or attrs.get("hintText", "") or ""
            visible = attrs.get("visible-to-user", "true").lower() != "false"

            is_interactive = attrs.get("clickable", "false").lower() == "true" or attrs.get("focusable", "false").lower() == "true"
            include_node = bool(class_name or text or content_desc or resource_id or is_interactive)

            if node_package:
                st = package_stats.setdefault(node_package, {"count": 0.0, "interactive": 0.0, "area": 0.0})
                st["count"] += 1.0
                if is_interactive:
                    st["interactive"] += 1.0
                if bounds:
                    w = max(0, int(bounds["right"]) - int(bounds["left"]))
                    h = max(0, int(bounds["bottom"]) - int(bounds["top"]))
                    st["area"] += float(w * h)

            element_id: Optional[int] = None
            child_ids: List[int] = []
            current_element: Optional[Dict[str, Any]] = None
            if include_node:
                element_id = counter
                counter += 1
                current_element = {
                    "element_id": element_id,
                    "type": self._class_to_type(class_name, text, content_desc, resource_id),
                    "text": text or None,
                    "content_description": content_desc or None,
                    "hint_text": hint_text or None,
                    "clickable": attrs.get("clickable", "false").lower() == "true",
                    "focusable": attrs.get("focusable", "false").lower() == "true",
                    "scrollable": attrs.get("scrollable", "false").lower() == "true",
                    "bounds": bounds,
                    "parent_id": parent_id,
                    "child_ids": [],
                    "resource_id": resource_id or None,
                    "package_name": node_package or None,
                    "class_name": class_name or None,
                    "enabled": attrs.get("enabled", "true").lower() != "false",
                    "selected": attrs.get("selected", "false").lower() == "true",
                    "visibility": "visible" if visible else "invisible",
                }
                elements.append(current_element)

            current_parent = element_id if element_id is not None else parent_id
            for child in list(node):
                child_id = visit(child, current_parent)
                if child_id is not None:
                    child_ids.append(child_id)

            if current_element is not None and child_ids:
                current_element["child_ids"] = child_ids

            return element_id

        visit(root, None)

        if screen_width == 0 or screen_height == 0:
            bounds = self._parse_bounds(root.attrib.get("bounds", ""))
            if bounds:
                screen_width = max(screen_width, bounds["right"])
                screen_height = max(screen_height, bounds["bottom"])
        if screen_width == 0:
            screen_width = 1080
        if screen_height == 0:
            screen_height = 2340

        if package_stats:
            def _pkg_score(pkg: str) -> Tuple[float, float, float]:
                st = package_stats.get(pkg, {})
                return (
                    float(st.get("interactive", 0.0)),
                    float(st.get("count", 0.0)),
                    float(st.get("area", 0.0)),
                )

            non_system_packages = [
                p for p in package_stats.keys()
                if p.lower() not in _SYSTEM_PACKAGES and "systemui" not in p.lower()
            ]
            pool = non_system_packages if non_system_packages else list(package_stats.keys())
            if pool:
                best_pkg = max(pool, key=_pkg_score)
                app_package = best_pkg
                app_name = best_pkg

        try:
            return SemanticUITree(
                device_id=self.device_id,
                app_name=app_name or "unknown",
                app_package=app_package or "unknown",
                screen_name=screen_name,
                elements=elements,
                screen_width=screen_width,
                screen_height=screen_height,
            )
        except Exception as exc:
            logger.debug(f"UI tree build error: {exc}")
            return None

    async def _resolve_app_from_package(self, package: str = "", app_name: str = "") -> str:
        package = (package or "").strip()
        app_name = (app_name or "").strip()
        cache_key = f"{package}:{app_name}"
        cached = _app_name_cache.get(cache_key)
        if cached:
            return cached

        if not package and not app_name:
            return "unknown"

        lower_pkg = package.lower()
        deterministic_map = {
            "chrome": "chrome",
            "gmail": "gmail",
            "nexuslauncher": "nexus launcher",
            "launcher": "launcher",
            "systemui": "system ui",
            "settings": "settings",
            "youtube": "youtube",
            "maps": "google maps",
            "contacts": "contacts",
            "clock": "clock",
            "play": "play store",
            "vending": "play store",
        }
        for k, v in deterministic_map.items():
            if k in lower_pkg:
                _app_name_cache[cache_key] = v
                return v

        if package:
            tail = package.rsplit(".", 1)[-1]
            if tail:
                tail_norm = re.sub(r"[^a-z0-9]+", " ", tail.lower()).strip()
                if tail_norm and tail_norm not in {"unknown", "app"}:
                    _app_name_cache[cache_key] = tail_norm
                    return tail_norm

        prompt = (
            f"Package: '{package}'\n"
            f"Display name: '{app_name}'\n"
            "Return ONLY a 1-3 word lowercase canonical app name. "
            "Examples: 'gmail', 'google maps', 'play store', 'clock'. "
            "No punctuation, no explanation."
        )
        try:
            resp = await self._llm_chat_completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10,
            )
            canonical = (resp.choices[0].message.content or "").strip().lower()
        except Exception as exc:
            logger.debug(f"[CACHE] app resolution failed for '{package}': {exc}")
            canonical = (app_name or package.rsplit(".", 1)[-1] or "unknown").strip().lower()

        canonical = re.sub(r"[^a-z0-9 ]+", "", canonical).strip() or "unknown"
        _app_name_cache[cache_key] = canonical
        return canonical

    @staticmethod
    def _uia_center(bounds: Optional[Dict[str, int]]) -> Tuple[int, int]:
        if not bounds:
            return 0, 0
        return (
            (int(bounds.get("left", 0)) + int(bounds.get("right", 0))) // 2,
            (int(bounds.get("top", 0)) + int(bounds.get("bottom", 0))) // 2,
        )

    def _action_to_uia2(self, action: UIAction) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": action.action_id}

        if action.action_type == "global_action":
            key = action.global_action or "BACK"
            if key == "ENTER":
                payload["method"] = "pressKey"
                payload["params"] = ["ENTER"]
            elif key == "SEARCH":
                payload["method"] = "pressKey"
                payload["params"] = ["SEARCH"]   # KEYCODE_SEARCH = 84
            else:
                payload["method"] = "pressKey"
                payload["params"] = [key]
            return payload

        if action.action_type == "swipe":
            width = getattr(self.current_ui_tree, "screen_width", 1080) or 1080
            height = getattr(self.current_ui_tree, "screen_height", 2340) or 2340
            payload["method"] = "swipe"
            payload["params"] = [
                int(width * (action.start_x_percent or 50) / 100),
                int(height * (action.start_y_percent or 80) / 100),
                int(width * (action.end_x_percent or 50) / 100),
                int(height * (action.end_y_percent or 20) / 100),
                int(action.duration or 300),
            ]
            return payload

        if action.action_type == "coordinate_tap":
            payload["method"] = "click"
            payload["params"] = [int(action.x or 0), int(action.y or 0)]
            return payload

        element = None
        if self.current_ui_tree and action.element_id is not None:
            try:
                element = self.current_ui_tree.get_element_by_id(int(action.element_id))
            except Exception:
                element = None

        if action.action_type in ("click", "long_click", "double_click"):
            cx, cy = self._uia_center(getattr(element, "bounds", None))
            if cx == 0 and cy == 0 and element is not None:
                cx, cy = 0, 0
            if action.action_type == "click":
                payload["method"] = "click"
                payload["params"] = [cx, cy]
            elif action.action_type == "long_click":
                payload["method"] = "longClick"
                payload["params"] = [cx, cy, int(action.duration or 1000)]
            else:
                payload["method"] = "doubleClick"
                payload["params"] = [cx, cy]
            return payload

        if action.action_type == "type":
            payload["method"] = "setText"
            payload["params"] = [action.text or ""]
            return payload

        payload["method"] = action.action_type
        payload["params"] = []
        return payload

    # ══════════════════════════════════════════════════════════════════════
    #  ENTRY POINT
    # ══════════════════════════════════════════════════════════════════════

    async def execute_task(self, task: MobileTaskRequest) -> MobileTaskResult:
        self.current_task = task
        _sync_session_app_memory(task.session_id)

        # FIX 2: adjust timeout on the task object so outer wait_for also sees it
        adjusted = compute_smart_timeout(task.ai_prompt, task.timeout_seconds)
        if adjusted != task.timeout_seconds:
            logger.info(f"⏱️ Timeout adjusted: {task.timeout_seconds}s → {adjusted}s")
        task.timeout_seconds = adjusted

        overall_goal = (
            task.extra_params.get("overall_goal")
            or task.extra_params.get("goal")
            or (task.context or {}).get("overall_goal")
            or (task.context or {}).get("goal")
            or task.ai_prompt
        )
        logger.info(
            f"[CACHE] overall_goal resolved: '{overall_goal[:60]}' "
            f"(ai_prompt='{task.ai_prompt[:40]}')"
        )
        app          = await self._resolve_app_from_package(
            task.extra_params.get("package_name", ""),
            task.extra_params.get("app_name", ""),
        )
        inferred_app = _infer_app_from_text(task.ai_prompt, overall_goal)
        if inferred_app and not (task.extra_params.get("app_name") or "").strip():
            if app in ("unknown", _session_last_app):
                if app != inferred_app:
                    logger.info(f"[CACHE] app inferred from task text: '{inferred_app}' (was '{app}')")
                app = inferred_app
                _set_session_last_app(inferred_app)

        logger.info(f"\n{'='*70}\n🎯 TASK START\n"
                    f"   goal   : {task.ai_prompt}\n"
                    f"   context: {overall_goal}\n"
                    f"   app    : {app}\n"
                    f"   device : {task.device_id}\n"
                    f"   steps  : {task.max_steps}  timeout: {task.timeout_seconds}s\n"
                    f"{'='*70}\n")

        self._reset_state()
        start_time        = asyncio.get_event_loop().time()
        actions_executed: List[UIAction] = []
        thought_history:  List[str]      = []

        logger.info("👁️ Getting initial UI state …")
        await asyncio.sleep(1.5)
        ui_tree = await self._fetch_ui_tree_with_retries(
            "wait",
            max_attempts=_UIA2_INITIAL_RETRIES,
            retry_delay=_UIA2_INITIAL_RETRY_DELAY,
        )
        if not ui_tree:
            return self._build_error_result(task.task_id, "Failed to get initial UI tree")

        self.current_ui_tree       = ui_tree
        self.previous_ui_trees.append(ui_tree)
        # FIX 1: refine app using live screen if coordinator didn't supply one
        app = await self._resolve_app_from_package(ui_tree.app_package or "", ui_tree.app_name or "")
        ui_tree.app_name = app
        self.device_state          = self._detect_device_state(ui_tree)
        self._prev_device_state    = self.device_state
        self._update_ui_stats(ui_tree)
        self._initial_ui_signature = (len(ui_tree.elements), ui_tree.screen_name or "")
        current_sig                = build_screen_signature(ui_tree)
        logger.info(f"✅ Initial screen: {ui_tree.screen_name or ui_tree.app_name} "
                    f"({len(ui_tree.elements)} elements) | state={self.device_state} | app={app}")

        task_lower = (task.ai_prompt or "").lower().strip()
        if (
            task_lower in _SEARCH_BUTTON_SYNONYMS
            or (task_lower.startswith("click") and "search" in task_lower and "button" in task_lower)
        ):
            if self.device_state not in ("home_screen", "app_drawer", "in_aura"):
                screen_text = " ".join(
                    (e.text or e.content_description or "").lower()
                    for e in ui_tree.elements[:30]
                )
                keyboard_open = any(
                    kw in screen_text for kw in ("search or type url", "search the web", "type to search")
                )
                if not keyboard_open:
                    logger.info("[T1] Search already submitted — declaring complete immediately")
                    return self._build_result(
                        task.task_id,
                        "success",
                        0,
                        [],
                        0.0,
                        completion_reason="Search already submitted via IME",
                    )

        # ── Tier 2 retrieval ───────────────────────────────────────────────
        # Skip current_signature on initial query — signature filtering blocks all hits
        self._log_t2_query_request(
            phase="initial-step",
            step_instruction=task.ai_prompt,
            overall_goal=overall_goal,
            app=app,
            current_signature="",
            top_k=5,
        )
        t2_result: RetrievalResult = self.task_memory.query(
            step_instruction  = task.ai_prompt,
            overall_goal      = overall_goal,
            app               = app,
            current_signature = "",
        )
        self._log_cache_result(t2_result, task.ai_prompt, app)   # FIX 5
        self._log_t2_nonfire_reason(t2_result, phase="initial-step")

        # Fallback: if step query misses but goal differs, query by goal to pull full sequence
        if t2_result.band == "none" and overall_goal != task.ai_prompt:
            self._log_t2_query_request(
                phase="goal-fallback",
                step_instruction=overall_goal,
                overall_goal=overall_goal,
                app=app,
                current_signature="",
                top_k=8,
            )
            t2_goal_query = self.task_memory.query(
                step_instruction  = overall_goal,
                overall_goal      = overall_goal,
                app               = app,
                current_signature = "",
                top_k             = 8,
            )
            self._log_t2_nonfire_reason(t2_goal_query, phase="goal-fallback")
            if t2_goal_query.band in ("execute", "hint"):
                logger.info(
                    f"[CACHE] Goal-based fallback: band={t2_goal_query.band} "
                    f"(step query was MISS, goal query found {len(t2_goal_query.recipes)} steps)"
                )
                t2_result = t2_goal_query
                self._log_cache_result(t2_result, overall_goal, app)

        if t2_result.band == "execute":
            not_in_target = not self._in_target_app(app, self.device_state)
            script_result = await self._execute_tier2_script(
                task, t2_result, overall_goal, app, actions_executed, start_time,
                skip_sig_for_first_step=not_in_target,
            )
            if script_result is not None:
                return script_result
            if not t2_result.hint_text and t2_result.recipes:
                t2_result.hint_text = self.task_memory._build_hint(t2_result.recipes, task.ai_prompt)
            t2_result.band = "hint"
            logger.info("[T2] Handing off to Tier 3")

        tier3_hint = ""
        t2_result = self._filter_hint_relevance(task.ai_prompt, t2_result)
        if t2_result.band == "hint" and app and app != "unknown":
            in_right_app = self._in_target_app(app, self.device_state)
            if not in_right_app:
                logger.info(
                    f"[CACHE] Hint suppressed — not yet in '{app}' "
                    f"(state={self.device_state}). Will re-query on app entry."
                )
                tier3_hint = ""
                self._last_t2_hint_record_id = None
            else:
                tier3_hint = t2_result.hint_text or ""
                self._last_t2_hint_record_id = t2_result.recipes[0].record_id if t2_result.recipes else None
        elif t2_result.band == "hint":
            tier3_hint = t2_result.hint_text or ""
            self._last_t2_hint_record_id = t2_result.recipes[0].record_id if t2_result.recipes else None
        else:
            self._last_t2_hint_record_id = None

        if self._is_in_time_picker(self.current_ui_tree):
            if tier3_hint:
                logger.debug("[T1] Time picker active — T2 hints suppressed")
            tier3_hint = ""

        # ── ReAct Loop ────────────────────────────────────────────────────
        for step in range(task.max_steps):
            logger.info(f"\n{'='*70}\n📍 STEP {step+1}/{task.max_steps}\n{'='*70}")

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > task.timeout_seconds:
                self._penalize_last_t2_hint("timeout")
                return self._build_result(task.task_id, "timeout",
                    step, actions_executed, elapsed, error=f"Timeout after {task.timeout_seconds}s")

            if self._is_in_time_picker(self.current_ui_tree):
                tier3_hint = ""

            # Incomplete UI guard
            if len(self.current_ui_tree.elements) < 5:
                self.incomplete_ui_count += 1
                if self.incomplete_ui_count >= 3:
                    logger.error("❌ UI stuck loading — BACK")
                    await self._execute_action_on_device(
                        UIAction(action_type="global_action", global_action="BACK", duration=1000))
                    await asyncio.sleep(3.0)
                    fresh = await self._fetch_ui_tree_with_retries("global_action")
                    if fresh:
                        self.current_ui_tree = fresh
                        self.device_state = self._detect_device_state(fresh)
                        self._update_ui_stats(fresh)
                    self.incomplete_ui_count = 0
                    continue
            else:
                self.incomplete_ui_count = 0

            # Stuck detection (skipped during time picker)
            if self._detect_stuck() and not self._is_in_time_picker(self.current_ui_tree):
                if self._screen_changed_significantly(self.current_ui_tree):
                    return self._build_result(task.task_id, "success",
                        step, actions_executed,
                        asyncio.get_event_loop().time() - start_time,
                        completion_reason="Task completed (screen changed from initial state)")
                logger.error("❌ Stuck — BACK")
                await self._execute_action_on_device(
                    UIAction(action_type="global_action", global_action="BACK", duration=1000))
                await asyncio.sleep(3.0)
                fresh = await self._fetch_ui_tree_with_retries("global_action")
                if fresh:
                    self.current_ui_tree = fresh
                    self.device_state = self._detect_device_state(fresh)
                    self._update_ui_stats(fresh)

            # ── TIER 1 ─────────────────────────────────────────────────────
            action_json:   Optional[Dict] = None
            decision_tier: str            = ""
            thought:       str            = ""

            t1 = self._tier1(task, self.current_ui_tree, self.device_state)
            if t1:
                thought       = t1["thought"]
                action_json   = t1
                decision_tier = "tier1"
                self.tier_stats["tier1"] += 1
                logger.info(f"[T1] {thought}")

            # ── TIER 3 — LLM ───────────────────────────────────────────────
            if action_json is None:
                decision_tier = "tier3_llm"
                self.tier_stats["tier3_llm"] += 1

                handoff_ctx = ""
                if self._t2_completed_steps:
                    done = ", ".join(s.get("step_instruction","?")[:40] for s in self._t2_completed_steps)
                    handoff_ctx = f"\n📌 ALREADY COMPLETED (do NOT repeat):\n{done}\n"

                pruned = build_pruned_tree_string(self.current_ui_tree)
                logger.info(f"[T3] LLM step | elements: {len(self.current_ui_tree.elements)}")

                thought, action_json = await self._llm_react(
                    goal=task.ai_prompt, overall_goal=overall_goal,
                    pruned_tree=pruned, thought_history=thought_history,
                    step_number=step+1, hint_context=tier3_hint,
                    handoff_context=handoff_ctx, extra_params=task.extra_params,
                    app=app,
                )

            if not action_json:
                logger.error("❌ No action — fallback scroll")
                action_json = {"thought": "fallback scroll", "action_type": "scroll",
                               "direction": "up", "duration": 300}

            thought_history.append(thought)
            logger.info(f"💭 Thought: {thought}")

            # FIX 7: thought loop detection only for Tier 3
            if decision_tier == "tier3_llm" and self._detect_thought_loop(thought):
                screen_is_meaningful = self.device_state not in (
                    "home_screen", "app_drawer", "in_aura", "unknown"
                )
                changed_sig = self._screen_changed_significantly(self.current_ui_tree)

                if changed_sig and screen_is_meaningful:
                    return self._build_result(task.task_id, "success", step+1,
                        actions_executed, asyncio.get_event_loop().time() - start_time,
                        completion_reason="Task completed (screen changed)")
                if changed_sig and not screen_is_meaningful:
                    logger.warning("🚨 Thought loop on navigation screen — resetting app drawer phase")
                    self._app_drawer_phase = 0
                    self.recent_thoughts.clear()
                    self.thought_loop_recoveries += 1
                    if self.thought_loop_recoveries <= 2:
                        tier3_hint = ""
                        continue
                self.thought_loop_recoveries += 1
                if self.thought_loop_recoveries <= 1:
                    logger.warning("🚨 Thought loop — soft recovery")
                    self.recent_thoughts.clear()
                    tier3_hint = ""
                    continue
                for _eid, _txt in self.typed_texts.items():
                    if _txt.lower() in task.ai_prompt.lower():
                        val = self._get_live_field_value(_eid)
                        if val and _txt.lower() in val.lower():
                            return self._build_result(task.task_id, "success", step+1,
                                actions_executed, asyncio.get_event_loop().time() - start_time,
                                completion_reason="Completed (text confirmed in field)")
                logger.error("🚨 Thought loop persists — failing")
                self._penalize_last_t2_hint("thought_loop")
                return self._build_result(task.task_id, "failed", step+1,
                    actions_executed, asyncio.get_event_loop().time() - start_time,
                    error="Thought loop — unable to complete on current screen")

            # Completion verification
            if action_json.get("action_type") == "complete" and decision_tier != "tier1":
                action_json = self._verify_complete_claim(action_json, task.ai_prompt)

            if action_json.get("action_type") == "complete":
                logger.info("✅ GOAL ACHIEVED")
                elapsed = asyncio.get_event_loop().time() - start_time
                self._store_learned_steps(task.ai_prompt, overall_goal, app, actions_executed)
                
                # Screen content extraction for search/lookup tasks
                extracted_content = None
                goal_lower = task.ai_prompt.lower()
                is_extraction_task = any(kw in goal_lower for kw in (
                    "search", "find", "look up", "get results", "show", "list",
                    "what are", "read", "extract", "parse"
                ))
                if is_extraction_task and self.current_ui_tree:
                    extracted_content = self._extract_screen_content(self.current_ui_tree)
                    if extracted_content:
                        logger.info(f"[EXTRACT] Screen content extracted: {len(extracted_content)} chars")
                
                result = self._build_result(task.task_id, "success", step+1,
                    actions_executed, elapsed, completion_reason=action_json.get("reason","Task completed"))
                
                # Attach extracted content to result metadata so coordinator can forward it
                if extracted_content:
                    result.completion_reason = f"Task completed\n\nSCREEN_CONTENT:\n{extracted_content}"
                
                return result

            # Blacklist guard
            if action_json.get("action_type") == "click":
                eid = action_json.get("element_id")
                if eid and eid in self.failed_elements:
                    logger.warning(f"🚫 Blacklisted element {eid} — skipping")
                    continue

            # TYPE duplicate guard
            if action_json.get("action_type") == "type":
                eid       = action_json.get("element_id")
                txt_typed = (action_json.get("text") or "").strip()
                action_json["clear_first"] = True
                if eid in self.typed_texts and self.typed_texts[eid] == txt_typed:
                    live = self._get_live_field_value(eid)
                    if live and txt_typed.lower() in live.lower():
                        return self._build_result(task.task_id, "success", step+1,
                            actions_executed, asyncio.get_event_loop().time() - start_time,
                            completion_reason="Completed (text already typed in field)")
                    logger.warning(
                        f"[T3] Type duplicate guard: field {eid} missing expected text, retrying"
                    )
                    del self.typed_texts[eid]
                if eid is not None:
                    self.typed_texts[eid] = txt_typed

            # Execute
            logger.info(f"🎬 ACT: {action_json.get('action_type')} | {thought}")
            action = self._to_ui_action(action_json)
            if action is None:
                logger.warning("⚠️ Could not build UIAction — skipping")
                continue

            if action_json.get("action_type") == "click":
                self.last_clicked_element  = action_json.get("element_id")
                self.last_action_was_click = True
            else:
                self.last_action_was_click = False

            self.action_history.append({"step": step+1, "action": action_json, "device_state": self.device_state})
            result = await self._execute_action_on_device(action)
            actions_executed.append(action)

            if not result.success:
                logger.warning(f"⚠️ Action failed: {result.error}")
                if action_json.get("action_type") == "click":
                    eid = action_json.get("element_id")
                    if eid:
                        self.element_retry_count[eid] = self.element_retry_count.get(eid, 0) + 1
                        if self.element_retry_count[eid] >= self.MAX_ELEMENT_RETRIES:
                            logger.error(f"🚫 Blacklisting element {eid}")
                            self.failed_elements.add(eid)
            else:
                logger.info("✅ Action executed")

            # Observe
            wait_time = self._wait_for_action(action_json.get("action_type"))
            logger.info(f"⏳ Waiting {wait_time}s …")
            await asyncio.sleep(wait_time)

            new_ui = await self._fetch_ui_tree_with_retries(
                action_type=action_json.get("action_type"), max_attempts=3, retry_delay=2.0)

            if new_ui:
                prev_ui_tree = self.current_ui_tree
                self.current_ui_tree = new_ui
                self.previous_ui_trees.append(new_ui)
                self._update_ui_stats(new_ui)
                if len(self.previous_ui_trees) > 5:
                    self.previous_ui_trees.pop(0)

                refreshed_app = await self._resolve_app_from_package(new_ui.app_package or "", new_ui.app_name or "")
                if refreshed_app != app:
                    logger.info(f"[CACHE] app updated from live screen: {app} → {refreshed_app}")
                    app = refreshed_app
                    if self._search_needs_confirm:
                        logger.info("[T1] Clearing _search_needs_confirm — app changed mid-search")
                        self._search_needs_confirm = False
                        self._search_just_submitted = False
                        self._search_confirm_app = ""
                new_ui.app_name = app

                if (
                    action_json.get("action_type") == "click"
                    and self._is_alarm_list_screen(new_ui)
                    and prev_ui_tree is not None
                    and not self._is_alarm_list_screen(prev_ui_tree)
                    and "alarm" in (task.ai_prompt + overall_goal).lower()
                ):
                    logger.info("✅ Alarm saved — transitioned to alarm list")
                    elapsed = asyncio.get_event_loop().time() - start_time
                    self._store_learned_steps(task.ai_prompt, overall_goal, app, actions_executed)
                    return self._build_result(
                        task.task_id,
                        "success",
                        step + 1,
                        actions_executed,
                        elapsed,
                        completion_reason="Alarm set and saved",
                    )

                new_state = self._detect_device_state(new_ui)
                if new_state != self.device_state:
                    logger.info(f"🔄 State: {self.device_state} → {new_state}")
                    prev_state_snapshot     = self.device_state
                    self._prev_device_state = self.device_state
                    self.device_state       = new_state
                    self.stuck_counter      = 0
                    tier3_hint              = ""
                    if self._search_needs_confirm:
                        target_a = _normalize_app_token(app).replace(" ", "_")
                        if not (new_state.endswith(target_a) or f"in_app_{target_a}" == new_state):
                            self._search_needs_confirm = False
                            self._search_just_submitted = False
                            self._search_confirm_app = ""

                    # In-loop Tier 2 re-query when transitioning into an app
                    fresh_t2 = await self._requery_tier2_on_app_entry(
                        task, overall_goal, app, prev_state_snapshot
                    )
                    if fresh_t2 is not None:
                        fresh_t2 = self._filter_hint_relevance(task.ai_prompt, fresh_t2)
                        if fresh_t2.band == "execute":
                            script_result = await self._execute_tier2_script(
                                task, fresh_t2, overall_goal, app,
                                actions_executed, start_time,
                                skip_sig_for_first_step=True,
                            )
                            if script_result is not None:
                                return script_result
                            tier3_hint = fresh_t2.hint_text or ""
                            self._last_t2_hint_record_id = fresh_t2.recipes[0].record_id if fresh_t2.recipes else None
                            logger.info("[T2] In-loop script failed → T3 with hint")
                        elif fresh_t2.band == "hint":
                            tier3_hint = fresh_t2.hint_text or ""
                            self._last_t2_hint_record_id = fresh_t2.recipes[0].record_id if fresh_t2.recipes else None
                            logger.info(f"[CACHE] In-loop hint: {tier3_hint[:80]}")
                else:
                    self.stuck_counter += 1

                # EMAIL SENT DETECTION: compose screen -> inbox transition after click
                in_gmail = "gm" in (new_ui.app_package or "").lower()
                if action_json.get("action_type") == "click" and in_gmail and prev_ui_tree is not None:
                    prev_was_compose = self._is_gmail_compose_screen(prev_ui_tree)
                    now_inbox = (
                        any(
                            (e.content_description or "").strip() == "Compose"
                            and getattr(e, "clickable", False)
                            and (getattr(e, "type", "") or "").lower() in ("imagebutton", "button")
                            for e in new_ui.elements
                        )
                        and not any((e.text or "").lower() in ("to", "subject") for e in new_ui.elements)
                        and not any((e.hint_text or "").lower() in ("to", "recipients") for e in new_ui.elements)
                        and len(new_ui.elements) > 30
                    )
                    if prev_was_compose and now_inbox and not self._email_just_sent:
                        self._email_just_sent = True
                        logger.info("✅ Email sent — compose→inbox transition detected")
                        elapsed = asyncio.get_event_loop().time() - start_time
                        self._store_learned_steps(task.ai_prompt, overall_goal, app, actions_executed)
                        return self._build_result(
                            task.task_id,
                            "success",
                            step + 1,
                            actions_executed,
                            elapsed,
                            completion_reason="Email sent",
                        )

                goal_is_play = any(kw in task.ai_prompt.lower() for kw in (
                    "play", "open", "watch", "view", "read", "listen"
                ))
                if goal_is_play and self._is_content_player_screen(new_ui):
                    logger.info("✅ Content player/detail screen detected — task complete")
                    elapsed = asyncio.get_event_loop().time() - start_time
                    self._store_learned_steps(task.ai_prompt, overall_goal, app, actions_executed)
                    return self._build_result(
                        task.task_id,
                        "success",
                        step + 1,
                        actions_executed,
                        elapsed,
                        completion_reason="Content player opened",
                    )

                if (self.last_action_was_click and self._is_click_task(task.ai_prompt)
                        and self._screen_changed_significantly(new_ui)):
                    elapsed = asyncio.get_event_loop().time() - start_time
                    self._store_learned_steps(task.ai_prompt, overall_goal, app, actions_executed)
                    return self._build_result(task.task_id, "success", step+1,
                        actions_executed, elapsed, completion_reason="Click task completed — screen changed")

                # App verification
                target_app = self._extract_target_app(task.ai_prompt)
                if (self.last_action_was_click and target_app
                        and not self._target_is_generic_page(target_app)
                        and len(new_ui.elements) >= 5
                        and new_state not in ("home_screen", "app_drawer")):
                    already_verified = self._in_target_app(target_app, new_state)
                    if already_verified:
                        logger.debug(f"[T1] App verify skipped — already confirmed in '{target_app}'")
                    elif self._is_in_time_picker(new_ui):
                        logger.debug("[T1] App verify skipped — inside time picker")
                    elif new_state in ("home_screen", "app_drawer"):
                        logger.debug(f"[T1] App verify skipped — still on {new_state} during navigation")
                    else:
                        verification = await self._verify_app_llm(target_app, new_ui)
                        if not verification["success"]:
                            eid = self.last_clicked_element
                            rc  = self.element_retry_count.get(eid, 0) + 1
                            self.element_retry_count[eid] = rc
                            logger.error(f"❌ Wrong app: expected={target_app} got={verification['actual']}")
                            if rc >= self.MAX_ELEMENT_RETRIES:
                                self.failed_elements.add(eid)
                            await self._execute_action_on_device(
                                UIAction(action_type="global_action", global_action="BACK", duration=1000))
                            await asyncio.sleep(2.0)
                            fresh = await self._fetch_ui_tree_with_retries("global_action")
                            if fresh:
                                self.current_ui_tree = fresh
                                self.device_state = self._detect_device_state(fresh)
                                self._update_ui_stats(fresh)
                            self.last_action_was_click = False
                            continue

                logger.info(f"👁️ New screen: {new_ui.screen_name or new_ui.app_name} ({len(new_ui.elements)} elements)")

                if action_json.get("action_type") == "type":
                    eid = action_json.get("element_id")
                    txt_typed = (action_json.get("text") or "").strip()
                    if self._typed_value_applied(action_json, new_ui):
                        live_val = (self._get_live_field_value(int(eid)) or "") if eid is not None else ""
                        logger.info(f"[T3] Type verified: field {eid} shows '{live_val[:30]}'")
                        self.stuck_counter = 0

                        # ── NEW: if this type action submitted a search, check for immediate completion ──
                        # _search_needs_confirm was just set by _wait_for_action if IME fired
                        if self._search_needs_confirm:
                            # Snapshot the element count now so suggestion clicker has reference
                            self._search_pre_element_count = len(new_ui.elements)
                    else:
                        logger.warning(
                            f"[T3] Type may have failed: expected '{txt_typed[:20]}' "
                            f"in field {eid}"
                        )
                        try:
                            if eid is not None:
                                refocus = UIAction(action_type="click", element_id=int(eid), duration=300)
                                await self._execute_action_on_device(refocus)
                                actions_executed.append(refocus)
                                await asyncio.sleep(0.4)
                                retry_ui = await self._fetch_ui_tree_with_retries(
                                    action_type="click", max_attempts=2, retry_delay=0.6,
                                )
                                if retry_ui:
                                    self.current_ui_tree = retry_ui
                                    self.previous_ui_trees.append(retry_ui)
                                    self._update_ui_stats(retry_ui)
                                    refreshed_app = await self._resolve_app_from_package(
                                        retry_ui.app_package or "",
                                        retry_ui.app_name or "",
                                    )
                                    if refreshed_app != app:
                                        logger.info(
                                            f"[CACHE] app updated from live screen: {app} → {refreshed_app}"
                                        )
                                        app = refreshed_app
                                    retry_ui.app_name = app
                        except Exception as e:
                            logger.debug(f"[TYPE] Refocus retry failed: {e}")
                        if eid is not None:
                            self.typed_texts.pop(int(eid), None)
            else:
                logger.warning("⚠️ Failed to get new UI tree")

        logger.warning("⚠️ MAX STEPS REACHED")
        self._penalize_last_t2_hint("max_steps")
        return self._build_result(task.task_id, "failed", task.max_steps,
            actions_executed, asyncio.get_event_loop().time() - start_time,
            error=f"Max steps ({task.max_steps}) reached")

    # ══════════════════════════════════════════════════════════════════════
    #  FIX 5 — structured cache logging
    # ══════════════════════════════════════════════════════════════════════

    def _log_cache_result(self, result: RetrievalResult, query: str, app: str):
        q = query[:50]
        if result.band == "none":
            sim  = result.best_sim
            best = result.best_label[:50] if result.best_label else "n/a"
            logger.info(f"[CACHE] MISS | query='{q}' | app={app} | best_sim={sim:.3f} | best='{best}'")
        elif result.band == "hint":
            r = result.recipes[0]
            s = r.selectors[0] if r.selectors else {}
            logger.info(f"[CACHE] HIT-HINT | query='{q}' | app={app} | sim={r.similarity:.3f} | "
                        f"matched='{r.step_instruction[:50]}' | sel={s.get('by')}='{s.get('value','')[:30]}'")
        elif result.band == "execute":
            r = result.recipes[0]
            s = r.selectors[0] if r.selectors else {}
            logger.info(f"[CACHE] HIT-EXECUTE | query='{q}' | app={app} | sim={r.similarity:.3f} | "
                        f"matched='{r.step_instruction[:50]}' | action={r.action_type} | "
                        f"sel={s.get('by')}='{s.get('value','')[:30]}'")

    def _log_t2_query_request(
        self,
        phase: str,
        step_instruction: str,
        overall_goal: str,
        app: str,
        current_signature: str,
        top_k: int,
    ) -> None:
        sig_preview = (current_signature[:80] + "…") if current_signature and len(current_signature) > 80 else current_signature
        logger.info(
            "[CACHE] QUERY "
            f"phase={phase} | app={app} | state={self.device_state} | top_k={top_k} | "
            f"exec>={THRESHOLD_EXECUTE:.2f} hint>={THRESHOLD_HINT:.2f} sig>={SIGNATURE_OVERLAP:.2f}"
        )
        logger.info(
            f"[CACHE] QUERY step='{(step_instruction or '')[:80]}' | "
            f"goal='{(overall_goal or '')[:80]}' | sig='{sig_preview or ''}'"
        )

    def _log_t2_nonfire_reason(self, result: RetrievalResult, phase: str) -> None:
        if result.band != "none":
            return
        logger.info(
            f"[CACHE] NO-FIRE phase={phase} | best_sim={result.best_sim:.3f} | "
            f"hint_threshold={THRESHOLD_HINT:.2f} | best='{(result.best_label or '')[:80]}'"
        )
        if result.best_sim > 0:
            if result.best_sim < THRESHOLD_HINT:
                logger.info(
                    "[CACHE] NO-FIRE reason: semantic similarity below hint threshold; "
                    "Tier 2 intentionally skipped"
                )
        else:
            logger.info(
                "[CACHE] NO-FIRE reason: no viable candidates returned (possibly app filter, "
                "empty collection subset, or signature mismatch upstream)"
            )

    # ══════════════════════════════════════════════════════════════════════
    #  TIER 2
    # ══════════════════════════════════════════════════════════════════════

    async def _requery_tier2_on_app_entry(
        self,
        task: MobileTaskRequest,
        overall_goal: str,
        app: str,
        prev_state: str,
    ) -> Optional[RetrievalResult]:
        """
        Re-run Tier 2 after entering target app.
        Uses task.ai_prompt as the primary step query so retrieval matches
        the current sub-task (not a later step from overall_goal).
        """
        came_from_nav = prev_state in ("home_screen", "app_drawer", "in_aura", "unknown")
        in_right_app_now = self._in_target_app(app, self.device_state)
        just_entered_app = came_from_nav and in_right_app_now
        if not just_entered_app:
            return None

        ai_lower = task.ai_prompt.lower()
        if any(kw in ai_lower for kw in ("open", "launch", "start", "navigate to")):
            logger.debug("[CACHE] In-loop re-query skipped — task is app-open only")
            return None

        current_sig = build_screen_signature(self.current_ui_tree)

        fresh = self.task_memory.query(
            step_instruction  = task.ai_prompt,
            overall_goal      = overall_goal,
            app               = app,
            current_signature = "",
            top_k             = 8,
        )
        self._log_t2_nonfire_reason(fresh, phase="in-loop-step")

        if fresh.band == "none":
            self._log_t2_query_request(
                phase="in-loop-goal-fallback",
                step_instruction=overall_goal,
                overall_goal=overall_goal,
                app=app,
                current_signature=current_sig,
                top_k=5,
            )
            fresh = self.task_memory.query(
                step_instruction  = overall_goal,
                overall_goal      = overall_goal,
                app               = app,
                current_signature = current_sig,
                top_k             = 5,
            )
            self._log_t2_nonfire_reason(fresh, phase="in-loop-goal-fallback")

        self._log_cache_result(fresh, f"[re-query] {task.ai_prompt[:40]}", app)
        return fresh if fresh.band != "none" else None

    def _filter_hint_relevance(self, ai_prompt: str, result: RetrievalResult) -> RetrievalResult:
        """Drop misleading navigation hints for non-navigation steps."""
        if result.band != "hint" or not result.recipes:
            return result

        top_recipe = result.recipes[0]
        ai_lower = (ai_prompt or "").lower()
        is_nav_step = any(kw in ai_lower for kw in ("open", "launch", "start"))
        hint_is_nav = any(
            kw in (top_recipe.step_instruction or "").lower()
            for kw in ("open", "launch", "start", "close")
        )
        is_single_step_nav_hint = hint_is_nav and len(result.recipes) == 1
        if is_single_step_nav_hint and not is_nav_step:
            logger.info(
                f"[CACHE] Hint rejected: single-step navigation hint for non-navigation step "
                f"(hint='{(top_recipe.step_instruction or '')[:40]}')"
            )
            return RetrievalResult(band="none", recipes=[], best_sim=0.0, best_label="", hint_text="")

        if hint_is_nav and not is_nav_step and len(result.recipes) > 1:
            logger.info(
                f"[CACHE] Multi-step hint: skipping nav preamble, using step 2+ as hint "
                f"(sequence has {len(result.recipes)} steps)"
            )
            remaining = result.recipes[1:]
            result.hint_text = self.task_memory._build_hint(remaining, ai_prompt)
            result.recipes = remaining
        return result

    def _penalize_last_t2_hint(self, reason: str) -> None:
        rid = self._last_t2_hint_record_id
        if not rid:
            return
        logger.info(f"[CACHE] Penalizing hint record {rid[:8]} due to {reason}")
        self.task_memory.mark_failure(rid)
        self._last_t2_hint_record_id = None

    async def _execute_tier2_script(
        self,
        task,
        t2_result,
        overall_goal,
        app,
        actions_executed,
        start_time,
        skip_sig_for_first_step: bool = False,
    ) -> Optional[MobileTaskResult]:
        logger.info(f"[T2] Guided script: {len(t2_result.recipes)} steps")
        self.tier_stats["tier2"] += 1
        self._t2_completed_steps = []

        for i, recipe in enumerate(t2_result.recipes):
            logger.info(f"[T2] Step {i+1}: {recipe.action_type} | '{recipe.step_instruction[:55]}'")
            logger.debug(f"[CACHE] T2 selectors: {recipe.selectors}")

            live_sig  = build_screen_signature(self.current_ui_tree)
            sig_match = _signature_jaccard(recipe.screen_signature, live_sig)
            sig_ok = (
                not recipe.screen_signature
                or (i == 0 and skip_sig_for_first_step)
                or sig_match >= _SIG_VERIFY_THRESHOLD
            )
            if not sig_ok:
                logger.warning(f"[T2] Sig mismatch ({sig_match:.0%}) — Tier 3")
                return None

            # Context verification for EXECUTE band (catches goal-context mismatch)
            if t2_result.band == "execute" and i == 0:
                context_ok = await self._verify_recipe_context(recipe, task.ai_prompt)
                if not context_ok:
                    self.task_memory.mark_failure(recipe.record_id)
                    logger.warning("[T2] Context verify rejected recipe → downgrade to hint")
                    t2_result.hint_text = self.task_memory._build_hint(
                        t2_result.recipes,
                        task.ai_prompt,
                    )
                    return None

            # ACTION-TYPE SANITY CHECK for nav goals
            goal_is_nav = any(kw in task.ai_prompt.lower() for kw in ("open", "launch", "start", "navigate to"))
            recipe_is_internal_click = (
                recipe.action_type == "click"
                and bool(recipe.selectors)
                and not any(
                    kw in (recipe.selectors[0].get("value", "") or "").lower()
                    for kw in (app.lower(), "play store", "clock", "gmail", "settings")
                )
            )
            if goal_is_nav and recipe_is_internal_click and i == 0:
                logger.warning(
                    f"[T2] Nav-goal sanity fail: 'open {app}' should not start with "
                    f"click selector='{recipe.selectors[0].get('value','')[:40]}' → downgrade to hint"
                )
                self.task_memory.mark_failure(recipe.record_id)
                t2_result.hint_text = ""
                return None

            params: Dict[str, str] = {
                k: str(v) for k, v in task.extra_params.items()
                if k not in _INTERNAL_KEYS and v is not None
            }

            element_id = None
            if recipe.action_type in ("click", "type"):
                # Hard gate: for EXECUTE band, selectors must resolve on the current screen.
                if t2_result.band == "execute":
                    test_eid = resolve_element(
                        recipe.selectors,
                        self.current_ui_tree.elements,
                        blacklist=self.failed_elements,
                        params=params,
                    )
                    if test_eid is None:
                        self.task_memory.mark_failure(recipe.record_id)
                        logger.warning(
                            "[T2] EXECUTE gate failed: selectors not found on screen → downgrade to hint"
                        )
                        # Preserve hint text so Tier 3 gets guided context on handoff.
                        t2_result.hint_text = self.task_memory._build_hint(
                            t2_result.recipes,
                            task.ai_prompt,
                        )
                        return None
                    element_id = test_eid
                else:
                    element_id = resolve_element(
                        recipe.selectors,
                        self.current_ui_tree.elements,
                        blacklist=self.failed_elements,
                        params=params,
                    )
                    if element_id is None:
                        logger.warning(f"[T2] Element not found step {i+1} — Tier 3")
                        return None

            if recipe.action_type == "click":
                action_json: Dict = {"action_type": "click", "element_id": element_id,
                                     "thought": f"[T2] {recipe.step_instruction}"}
            elif recipe.action_type == "type":
                text_value = recipe.typed_value or ""
                if recipe.param_key and recipe.param_key in params:
                    text_value = params[recipe.param_key]
                action_json = {"action_type": "type", "element_id": element_id,
                               "text": text_value, "clear_first": True,
                               "thought": f"[T2] {recipe.step_instruction}"}
            elif recipe.action_type == "scroll":
                action_json = {"action_type": "scroll", "direction": recipe.direction or "down",
                               "duration": 500, "thought": f"[T2] {recipe.step_instruction}"}
            elif recipe.action_type == "back":
                action_json = {"action_type": "global_action", "global_action": "BACK",
                               "thought": f"[T2] {recipe.step_instruction}"}
            elif recipe.action_type == "global_action":
                # Nav tasks stored as global_action should not be EXECUTE-band executed
                # (they have no selectors to resolve on-screen). Downgrade to hint.
                if t2_result.band == "execute":
                    logger.warning("[T2] Nav recipe (global_action) in EXECUTE band — downgrading to hint")
                    t2_result.hint_text = self.task_memory._build_hint(t2_result.recipes, task.ai_prompt)
                    return None  # hand off to T1/T3
                # In hint context, just track completion and continue
                self._t2_completed_steps.append({
                    "step_instruction": recipe.step_instruction,
                    "action_type": recipe.action_type,
                })
                continue
            else:
                continue

            action = self._to_ui_action(action_json)
            if action is None:
                return None

            result = await self._execute_action_on_device(action)
            actions_executed.append(action)
            if not result.success:
                return None

            wait = self._wait_for_action(recipe.action_type)
            await asyncio.sleep(wait)
            new_ui = await self._fetch_ui_tree_with_retries(recipe.action_type)
            if new_ui:
                self.current_ui_tree = new_ui
                self.device_state    = self._detect_device_state(new_ui)
                self._update_ui_stats(new_ui)
            else:
                return None

            self._t2_completed_steps.append({"step_instruction": recipe.step_instruction,
                                              "action_type": recipe.action_type})
            self.task_memory.increment_success(recipe.record_id)
            logger.debug(f"[CACHE] T2 step {i+1} ok | id={recipe.record_id[:8]}")

        elapsed = asyncio.get_event_loop().time() - start_time
        return self._build_result(task.task_id, "success", len(t2_result.recipes),
            actions_executed, elapsed, completion_reason=f"Tier 2 script ({len(t2_result.recipes)} steps)")

    # ══════════════════════════════════════════════════════════════════════
    #  TIER 1
    # ══════════════════════════════════════════════════════════════════════

    def _tier1(self, task: MobileTaskRequest, ui_tree: SemanticUITree, device_state: str) -> Optional[Dict[str, Any]]:
        return self._tier1_deterministic(task, ui_tree, device_state)

    def _tier1_deterministic(
        self,
        task: MobileTaskRequest,
        ui_tree: SemanticUITree,
        device_state: str,
    ) -> Optional[Dict[str, Any]]:
        if not ui_tree or not ui_tree.elements:
            return None

        # Clock time entry owns the entire interaction in Android Clock.
        clock_time = self._tier1_clock_time_entry(task, ui_tree)
        if clock_time:
            return clock_time

        # Search suggestion clicker — highest priority
        suggestion_click = self._find_and_click_first_suggestion(ui_tree)
        if suggestion_click:
            return suggestion_click

        # Click→type rescue when field is repeatedly clicked with no UI change.
        click_type = self._detect_click_to_type_pattern(task, ui_tree)
        if click_type:
            return click_type

        # Search results visible → complete before any ENTER fallback can loop.
        results_complete = self._detect_search_results_screen(task, ui_tree)
        if results_complete:
            return results_complete

        # ── T1: Post-type ENTER press ─────────────────────────────────────────
        # If the last action was a type on a search/URL field and no IME fired,
        # press ENTER as a universal submit.
        enter_action = self._detect_enter_needed(task, ui_tree)
        if enter_action:
            return enter_action

        # Navigation tasks: if already inside target app, complete now.
        if self._is_nav_goal(task.ai_prompt) and device_state.startswith("in_app_"):
            target = self._extract_target_app(task.ai_prompt)
            if target and self._in_target_app(target, device_state):
                logger.info(f"[T1] Already in target app '{target}' → complete")
                return {
                    "thought": f"'{target}' is already open",
                    "action_type": "complete",
                }

            # Generic navigation requests like "navigate to the search engine"
            # are no-ops when the search affordance is already visible.
            task_lower = (task.ai_prompt or "").lower().strip()
            redundant_nav_phrases = {
                "navigate to the search engine",
                "navigate to google",
                "navigate to the google search page",
                "go to the search bar",
                "open the search bar",
                "navigate to search",
                "go to google",
                "navigate to google.com",
                "open google search",
            }
            if task_lower in redundant_nav_phrases and device_state.startswith("in_app_"):
                has_search_bar = any(
                    e.focusable and any(
                        kw in (e.hint_text or e.content_description or e.resource_id or "").lower()
                        for kw in ("search", "url", "address", "omnibox")
                    )
                    for e in ui_tree.elements
                )
                if has_search_bar:
                    logger.info("[T1] Redundant navigation task — search bar already visible → complete")
                    return {
                        "thought": "search bar is already visible and accessible — no navigation needed",
                        "action_type": "complete",
                    }

        # AM/PM correction in time picker
        if self._is_in_time_picker(ui_tree):
            extra = task.extra_params or {}
            goal_text = f"{task.ai_prompt} {extra.get('overall_goal', '')} {extra.get('goal', '')}".lower()
            wants_am = bool(re.search(r"\bam\b", goal_text))
            wants_pm = bool(re.search(r"\bpm\b", goal_text))
            if wants_pm:
                wants_am = False
            if not wants_am and not wants_pm:
                hm = re.search(r"\b([1-9]|1[01])\s*:", goal_text)
                if hm:
                    wants_am = True

            if wants_am or wants_pm:
                target = "AM" if wants_am else "PM"
                wrong = "PM" if wants_am else "AM"
                wrong_selected = next(
                    (
                        e for e in ui_tree.elements
                        if e.type == "button"
                        and (e.text or "").upper() == wrong
                        and getattr(e, "selected", False)
                    ),
                    None,
                )
                if wrong_selected:
                    correct_btn = next(
                        (
                            e for e in ui_tree.elements
                            if e.type == "button" and (e.text or "").upper() == target
                        ),
                        None,
                    )
                    if correct_btn:
                        logger.info(
                            f"[T1] Time picker: correcting {wrong} → {target} "
                            f"(element {correct_btn.element_id})"
                        )
                        return {
                            "thought": f"Correct AM/PM to {target}",
                            "action_type": "click",
                            "element_id": correct_btn.element_id,
                        }

        if "aura" in (ui_tree.app_package or "").lower():
            logger.info("[T1] AURA detected → HOME")
            return {"thought": "exit AURA", "action_type": "global_action", "global_action": "HOME"}

        if self.stuck_counter >= 10:
            recent_types = [
                h for h in self.action_history[-5:]
                if h.get("action", {}).get("action_type") == "type"
            ]
            if not recent_types:
                logger.info(f"[T1] Hard stuck ({self.stuck_counter}) → HOME")
                return {
                    "thought": "hard stuck recovery",
                    "action_type": "global_action",
                    "global_action": "HOME",
                }

        dialog = self._handle_system_dialog(ui_tree)
        if dialog:
            return dialog

        if device_state == "home_screen" and self._is_nav_goal(task.ai_prompt):
            nav_action = self._handle_app_navigation(task.ai_prompt, ui_tree)
            if nav_action:
                return nav_action

        return None

    def _is_dialog_screen(self, ui_tree: SemanticUITree) -> bool:
        elements = ui_tree.elements or []
        if not elements or len(elements) > 5:
            return False

        dismiss_vocab = {"allow", "deny", "ok", "okay", "cancel", "dismiss", "close", "continue", "not now", "skip"}
        actionable = 0
        system_like = 0
        for elem in elements:
            blob = f"{elem.content_description or ''} {elem.text or ''}".lower().strip()
            if elem.clickable or elem.focusable:
                actionable += 1
                if any(word in blob for word in dismiss_vocab):
                    system_like += 1
        return actionable > 0 and system_like >= 1

    def _handle_system_dialog(self, ui_tree: SemanticUITree) -> Optional[Dict[str, Any]]:
        if not self._is_dialog_screen(ui_tree):
            return None

        positive_vocab = ("allow", "continue", "ok", "okay", "accept", "agree", "yes", "got it")
        negative_vocab = ("deny", "cancel", "not now", "no thanks", "dismiss", "close", "skip")

        choices = []
        for elem in ui_tree.elements:
            if not (elem.clickable or elem.focusable):
                continue
            blob = f"{elem.content_description or ''} {elem.text or ''}".lower().strip()
            if any(word in blob for word in positive_vocab):
                choices.append((1, elem))
            elif any(word in blob for word in negative_vocab):
                choices.append((0, elem))

        if not choices:
            return None

        choices.sort(key=lambda item: item[0], reverse=True)
        chosen = choices[0][1]
        label = (chosen.content_description or chosen.text or "dialog")[:40]
        logger.info(f"[T1] System dialog → click '{label}' id={chosen.element_id}")
        return {
            "thought": f"dismiss system dialog ('{label}')",
            "action_type": "click",
            "element_id": chosen.element_id,
        }

    def _is_nav_goal(self, goal: str) -> bool:
        gl = (goal or "").lower()
        return any(kw in gl for kw in ("open", "launch", "start", "navigate to"))

    def _handle_app_navigation(self, goal: str, ui_tree: SemanticUITree) -> Optional[Dict[str, Any]]:
        target = self._extract_target_app(goal)
        if not target or self._target_is_generic_page(target):
            return None

        # Always check if app is visible first regardless of phase
        clickable_targets = [
            elem for elem in ui_tree.elements
            if elem.clickable and target.lower() in 
            f"{elem.text or ''} {elem.content_description or ''}".lower()
        ]
        if clickable_targets:
            chosen = clickable_targets[0]
            logger.info(f"[T1] Navigation: click visible app '{target}' id={chosen.element_id}")
            self._app_drawer_phase = 0
            return {
                "thought": f"open {target}",
                "action_type": "click",
                "element_id": chosen.element_id,
            }

        if self._app_drawer_phase == 0:
            self._app_drawer_phase = 1
            logger.info(f"[T1] Nav phase 1: '{target}' not visible → swipe up to open drawer")
            return {
                "thought": f"open app drawer to find {target}",
                "action_type": "swipe",
                "start_x_percent": 50, "start_y_percent": 92,
                "end_x_percent": 50, "end_y_percent": 20,
                "duration": 300,
            }

        if self._app_drawer_phase == 1:
            self._app_drawer_phase = 2
            logger.info(f"[T1] Nav phase 2: scroll drawer down to find {target}")
            return {
                "thought": f"scroll app drawer to find {target}",
                "action_type": "swipe",
                "start_x_percent": 50, "start_y_percent": 75,
                "end_x_percent": 50, "end_y_percent": 30,
                "duration": 350,
            }

        if self._app_drawer_phase == 2:
            self._app_drawer_phase = 3
            logger.info(f"[T1] Nav phase 3: scroll drawer further down")
            return {
                "thought": f"scroll app drawer further to find {target}",
                "action_type": "swipe",
                "start_x_percent": 50, "start_y_percent": 75,
                "end_x_percent": 50, "end_y_percent": 25,
                "duration": 350,
            }

        if self._app_drawer_phase == 3:
            self._app_drawer_phase = 4
            logger.info(f"[T1] Nav phase 4: '{target}' not found → HOME reset, try once more")
            return {
                "thought": f"reset to launcher root to retry {target}",
                "action_type": "global_action",
                "global_action": "HOME",
            }

        if self._app_drawer_phase == 4:
            # Tried everything — hand off to T3 to handle it however it can
            logger.info(f"[T1] Nav phase 5: giving up T1 nav for '{target}' → T3")
            self._app_drawer_phase = 0  # reset for next task
            return None  # T3 takes over

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  TIER 3 — LLM REACT
    # ══════════════════════════════════════════════════════════════════════

    async def _llm_react(
        self, goal, overall_goal, pruned_tree, thought_history,
        step_number, hint_context, handoff_context, extra_params, app,
    ) -> Tuple[str, Optional[Dict]]:

        param_ctx    = self._format_extra_params(extra_params)
        blacklist_str = (f"⛔ Do NOT click element IDs: {sorted(list(self.failed_elements))}. "
                         if self.failed_elements else "")

        valid_action_types = (
            "click", "type", "scroll", "swipe",
            "global_action", "coordinate_tap", "wait", "complete",
        )
        system_prompt = f"""You are an Android UI automation agent operating in a ReAct loop.

RESPONSE FORMAT (every time — no exceptions):
Thought: <one sentence: what you see, what you will do>
Action: {{"action_type": "...", ...}}

VALID action_type values: {", ".join(valid_action_types)}

─── FIELD INTERACTION — READ THIS FIRST ────────────────────────────────────
Rule: Click a text field ONCE to focus it. Then immediately TYPE.
    WRONG: click field → click field again → click field again
    RIGHT: click field → type text (next step)

If you click an element and the screen does NOT change:
    → The element is now focused/selected. Issue TYPE immediately.
    → Never click the same element twice in a row.

On Android, double-tapping the Chrome address bar or Gmail fields
opens voice input or Google Lens — this is ALWAYS wrong.
────────────────────────────────────────────────────────────────────────────

─── SEARCH FLOW (universal) ────────────────────────────────────────────────
1. Find the search bar/icon → click once to focus.
2. Type the FULL query with clear_first=true.
3. After typing: DO NOT click again. The field now has the text.
4. If results are visible → declare complete immediately.
5. If still in search/typing mode → press ENTER:
    {{"action_type": "global_action", "global_action": "ENTER"}}
6. Suggestions: click ONLY if text DIRECTLY contains your query.
   NEVER click: "Edit suggestion", "Refine:", voice/camera buttons.
────────────────────────────────────────────────────────────────────────────

─── NAVIGATION ─────────────────────────────────────────────────────────────
- Open an app by clicking its icon. Never use the browser to launch apps.
- Once inside the target app → declare complete. Do not keep navigating.
- BACK: {{"action_type": "global_action", "global_action": "BACK"}}
────────────────────────────────────────────────────────────────────────────

─── COMPLETION ─────────────────────────────────────────────────────────────
- Declare complete only with visible on-screen evidence.
- Handle permission dialogs by clicking "Allow", "OK", "Got it" first.
────────────────────────────────────────────────────────────────────────────

Example:
Thought: I see a focusable search bar. I will click it.
Action: {{"action_type": "click", "element_id": 42}}

Thought: The screen is unchanged — the bar is focused. I will type now.
Action: {{"action_type": "type", "element_id": 42, "text": "vets in new cairo", "clear_first": true}}

Thought: Results are visible on screen. Task complete.
Action: {{"action_type": "complete"}}"""

        # Add navigation scope guard
        if any(kw in goal.lower() for kw in ("navigate", "open", "launch")):
            nav_scope = (
                f"\n🚨 SCOPE GUARD: Your ONLY job this step is: '{goal}'\n"
                f"Do NOT send emails, type content, or perform actions beyond opening the app.\n"
                f"Once the app is open, declare complete.\n"
            )
        else:
            nav_scope = ""

        _last_click_hint = ""
        if (
            self.action_history
            and self.action_history[-1].get("action", {}).get("action_type") == "click"
            and self.stuck_counter >= 1
        ):
            last_eid = self.action_history[-1].get("action", {}).get("element_id")
            if last_eid and self.current_ui_tree:
                try:
                    last_elem = self.current_ui_tree.get_element_by_id(int(last_eid))
                except Exception:
                    last_elem = None
                if last_elem and (last_elem.focusable or last_elem.type in ("textfield", "edittext")):
                    _last_click_hint = (
                        f"\n⚠️ FIELD STATE: You clicked element {last_eid} and the screen did NOT change. "
                        "The field IS focused. Your next action MUST be TYPE — not another click.\n"
                    )

        user_prompt = (
            f"OVERALL GOAL: {overall_goal}\n"
            f"CURRENT STEP: {goal}\n"
            f"Step {step_number} | Device state: {self.device_state}\n"
            + (f"\n{param_ctx}"       if param_ctx       else "")
            + (f"\n{hint_context}"    if hint_context     else "")
            + (f"\n{handoff_context}" if handoff_context  else "")
            + (f"\n{_last_click_hint}" if _last_click_hint else "")
            + f"\n{blacklist_str}\n"
            + nav_scope
            + (
                f"\n⚠️ APP SCOPE: You must be in {app.upper()} for this step. "
                f"Currently: {self.device_state}. Navigate there first.\n"
                if app and app != "unknown" and not self._in_target_app(app, self.device_state)
                else ""
            )
            + f"\nCURRENT SCREEN:\n{pruned_tree}\n"
            + (f"\nPrior steps: {' → '.join(thought_history[-3:])}" if thought_history else "")
            + "\n\nRespond with Thought and Action."
        )

        logger.info(
            f"[T3] LLM_INPUT step={step_number} | state={self.device_state} | app={app} | "
            f"hint_len={len(hint_context or '')} | handoff_len={len(handoff_context or '')} | "
            f"history_items={len(thought_history)} | ui_lines={len((pruned_tree or '').splitlines())}"
        )
        logger.debug("[T3] SYSTEM_PROMPT:\n" + system_prompt)
        logger.debug("[T3] USER_PROMPT:\n" + user_prompt)

        raw_response = ""
        for attempt in range(2):
            suffix = "\n\nSTRICT: Start with 'Thought:' on line 1." if attempt == 1 else ""
            try:
                logger.info(f"[T3] LLM_CALL attempt={attempt+1}/2")
                response = await self._llm_chat_completion(
                    model=self.model,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user",   "content": user_prompt + suffix}],
                    temperature=0.2, max_tokens=300,
                )
                raw_response = response.choices[0].message.content.strip()
                self._track_llm_usage(response)
                logger.info(f"[T3] LLM_RAW: {raw_response[:300]}")
                logger.debug(f"[T3] Raw LLM full:\n{raw_response}")
                break
            except Exception as e:
                logger.error(f"[T3] LLM error attempt {attempt+1}: {e}")

        if not raw_response:
            return "fallback scroll", {"action_type": "scroll", "direction": "up", "duration": 300,
                                       "thought": "LLM unavailable"}

        thought_text = "No thought"
        action_json  = None

        tm = re.search(r"Thought:\s*(.+?)(?:\nAction:|$)", raw_response, re.DOTALL)
        if tm: thought_text = tm.group(1).strip()

        am = re.search(r"Action:\s*(\{.+?\})", raw_response, re.DOTALL)
        if am:
            try: action_json = json.loads(am.group(1).strip())
            except json.JSONDecodeError: pass

        if action_json is None:
            js = self._extract_json(raw_response)
            if js:
                try: action_json = json.loads(js)
                except json.JSONDecodeError: pass

        if action_json is None:
            logger.error(f"[T3] No valid action: {raw_response[:200]}")
            return "fallback scroll", {"action_type": "scroll", "direction": "up", "duration": 300,
                                       "thought": "parse failed"}

        atype = (action_json.get("action_type") or "").strip()
        normalize_map: Dict[str, Optional[Tuple[str, Dict[str, Any]]]] = {
            "navigate_back": ("global_action", {"global_action": "BACK"}),
            "press_back":    ("global_action", {"global_action": "BACK"}),
            "go_back":       ("global_action", {"global_action": "BACK"}),
            "back":          ("global_action", {"global_action": "BACK"}),
            "press_enter":      ("global_action", {"global_action": "ENTER"}),
            "press_return":     ("global_action", {"global_action": "ENTER"}),
            "submit":           ("global_action", {"global_action": "ENTER"}),
            "keyboard_enter":   ("global_action", {"global_action": "ENTER"}),
            "tap":           ("click", {}),
            "press":         ("click", {}),
            "input_text":    ("type", {}),
            "enter_text":    ("type", {}),
            "send_keys":     ("type", {}),
            "open_app":      None,
            "navigate":      None,
        }
        if atype in normalize_map:
            replacement = normalize_map[atype]
            if replacement is None:
                logger.warning(f"[T3] Invalid action_type '{atype}' — replaced with scroll")
                action_json = {
                    "action_type": "scroll",
                    "direction": "down",
                    "duration": 300,
                    "thought": f"invalid action: {atype}",
                }
            else:
                new_type, extra = replacement
                action_json["action_type"] = new_type
                action_json.update(extra)
                logger.info(f"[T3] Normalized action_type '{atype}' → '{new_type}'")

        logger.info(f"[T3] LLM_THOUGHT: {thought_text}")
        logger.info(f"[T3] LLM_ACTION_JSON: {json.dumps(action_json, ensure_ascii=False)}")

        return thought_text, action_json

    # ══════════════════════════════════════════════════════════════════════
    #  CONTEXT BUILDERS
    # ══════════════════════════════════════════════════════════════════════

    def _is_content_player_screen(self, ui_tree: SemanticUITree) -> bool:
        player_indicators = {
            "pause", "scrubber", "seek", "timeline", "playback",
            "fullscreen", "mini_player", "video_container", "player",
            "like", "dislike", "subscribe",
        }
        for e in ui_tree.elements:
            blob = f"{e.content_description or ''} {e.resource_id or ''}".lower()
            if any(ind in blob for ind in player_indicators):
                return True
        return False

    def _find_and_click_first_suggestion(self, ui_tree: SemanticUITree) -> Optional[Dict[str, Any]]:
        """
        After IME fires, click the first REAL suggestion that semantically matches
        the typed query. Universal across apps.
        """
        if not self._search_needs_confirm:
            return None

        if self._search_confirm_app:
            current_pkg = (ui_tree.app_package or "").lower()
            origin_pkg = self._search_confirm_app.lower()
            if current_pkg and origin_pkg and current_pkg != origin_pkg:
                logger.info(f"[T1] Suggestion clicker aborted — app changed ({origin_pkg} → {current_pkg})")
                self._search_needs_confirm = False
                self._search_confirm_app = ""
                return None

        typed_query = ""
        for txt in reversed(list(self.typed_texts.values())):
            if txt and len(txt) > 1:
                typed_query = txt.strip().lower()
                break

        if not typed_query:
            self._search_needs_confirm = False
            self._search_confirm_app = ""
            return None

        h = max(ui_tree.screen_height, 1)
        query_words = set(typed_query.split())
        candidates = []

        for e in ui_tree.elements:
            if not e.clickable:
                continue
            label = (e.text or e.content_description or "").strip()
            if not label or len(label) < 2 or not e.bounds:
                continue

            cy = (e.bounds.get("top", 0) + e.bounds.get("bottom", 0)) // 2
            cy_pct = cy * 100 // h
            if cy_pct < 20:
                continue

            label_lower = label.lower()
            rid_lower = (e.resource_id or "").lower()
            if any(label_lower.startswith(p) for p in _SUGGESTION_CONTROL_PREFIXES):
                continue
            if any(p in rid_lower for p in _SUGGESTION_CONTROL_RESOURCE_IDS):
                continue
            if e.type in ("imagebutton", "switch", "checkbox", "radiobutton"):
                continue
            if label_lower in {
                "google", "clear", "close", "back", "microphone", "camera",
                "search", "go", "enter", "submit", "ok", "cancel", "more",
            }:
                continue

            label_words = set(label_lower.split())
            query_word_count = max(len(query_words), 1)
            label_word_count = max(len(label_words), 1)
            overlap_ratio = len(query_words & label_words) / query_word_count
            is_prefix_completion = label_lower.startswith(typed_query[:max(4, len(typed_query) - 3)])
            is_full_contain = typed_query in label_lower
            length_ratio = label_word_count / query_word_count
            is_too_long = length_ratio > 1.6
            is_near_exact = label_lower == typed_query or (typed_query in label_lower and length_ratio <= 1.3)
            is_good_match = (
                is_near_exact
                or (is_prefix_completion and not is_too_long)
                or (overlap_ratio >= 0.5 and not is_too_long)
                or is_full_contain and not is_too_long
            )
            if not is_good_match:
                logger.debug(
                    f"[T1] Suggestion rejected: '{label[:40]}' "
                    f"overlap={overlap_ratio:.0%} length_ratio={length_ratio:.1f} too_long={is_too_long} "
                    f"query='{typed_query[:30]}'"
                )
                continue

            candidates.append((cy_pct, e))

        if not candidates:
            # ── Check if the screen already changed due to search submission ──
            if self.current_ui_tree and self._search_pre_element_count > 0:
                current_count = len(self.current_ui_tree.elements)
                pre_count = self._search_pre_element_count
                change_ratio = abs(current_count - pre_count) / max(pre_count, 1)
                if change_ratio >= 0.10:  # 10% change = results loaded / overlay dismissed
                    logger.info(
                        f"[T1] Search submitted, screen changed "
                        f"({pre_count}→{current_count} elements, {change_ratio:.0%}) — complete"
                    )
                    self._search_needs_confirm = False
                    self._search_confirm_app = ""
                    self._search_pre_element_count = 0
                    return {
                        "thought": "search submitted via IME and screen updated — task complete",
                        "action_type": "complete",
                    }

            logger.info(f"[T1] Suggestion clicker: no matching candidate for '{typed_query[:40]}' — skipping")
            self._search_needs_confirm = False
            self._search_confirm_app = ""
            self._search_pre_element_count = 0
            return None

        candidates.sort(key=lambda x: x[0])
        first = candidates[0][1]
        label = (first.text or first.content_description or "")[:50]
        logger.info(f"[T1] Suggestion clicker: clicking '{label}' id={first.element_id} @{candidates[0][0]}%")
        self._search_needs_confirm = False
        self._search_confirm_app = ""
        self._search_pre_element_count = 0
        return {
            "thought": f"Clicking matching search suggestion: {label}",
            "action_type": "click",
            "element_id": first.element_id,
        }

    def _tier1_clock_time_entry(
        self,
        task: MobileTaskRequest,
        ui_tree: SemanticUITree,
    ) -> Optional[Dict[str, Any]]:
        """
        Android Clock uses a sequential digit pad, not separate hour/minute fields.
        Typing must be done as a single stream: 5:30 → type "0530" once.
        This T1 handler owns all time entry in the clock app entirely.
        """
        if not self._is_in_time_picker(ui_tree):
            return None

        extra = task.extra_params or {}
        goal_text = f"{task.ai_prompt} {extra.get('time', '')} {extra.get('overall_goal', '')}".lower()
        time_match = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", goal_text, re.IGNORECASE)
        if not time_match:
            return None

        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        am_pm = (time_match.group(3) or "").upper()
        pad_sequence = f"{hour:02d}{minute:02d}"

        digit_field = None
        for e in ui_tree.elements:
            if e.focusable and e.enabled:
                blob = (e.hint_text or e.content_description or e.resource_id or "").lower()
                if any(kw in blob for kw in ("hour", "minute", "time", "clock")):
                    digit_field = e
                    break
            if e.focusable and e.enabled and e.type in ("textfield",):
                digit_field = e
                break

        if digit_field is None:
            return None

        already_typed_sequence = any(v == pad_sequence for v in self.typed_texts.values())
        if already_typed_sequence:
            return None

        logger.info(f"[T1] Clock number pad: typing '{pad_sequence}' for {hour}:{minute:02d} {am_pm}")
        return {
            "thought": f"entering time {hour}:{minute:02d} {am_pm} as pad sequence '{pad_sequence}'",
            "action_type": "type",
            "element_id": digit_field.element_id,
            "text": pad_sequence,
            "clear_first": True,
        }

    def _detect_search_results_screen(
        self,
        task: MobileTaskRequest,
        ui_tree: SemanticUITree,
    ) -> Optional[Dict[str, Any]]:
        """
        After search submission, detect if results are visible and complete.
        Fires once — cleared after firing.
        """
        if self._search_pre_element_count == 0 and not any(
            h.get("action", {}).get("global_action") == "ENTER"
            for h in self.action_history[-3:]
        ):
            return None

        goal_lower = (task.ai_prompt or "").lower()
        is_search_task = any(kw in goal_lower for kw in (
            "search", "find", "type", "look", "query"
        ))
        if not is_search_task:
            return None

        result_signals = 0
        for e in ui_tree.elements:
            blob = f"{e.text or ''} {e.content_description or ''} {e.resource_id or ''}".lower()
            if any(kw in blob for kw in (
                "views", "subscribers", "ago", "duration", "channel",
                "result", "web", "wikipedia", "https://",
                "download", "install", "rating",
            )):
                result_signals += 1
            if result_signals >= 2:
                break

        pre = self._search_pre_element_count
        if pre > 0:
            change = abs(len(ui_tree.elements) - pre) / max(pre, 1)
            if change >= 0.15 and result_signals >= 1:
                logger.info(f"[T1] Search results detected — {len(ui_tree.elements)} elements, {result_signals} signals")
                self._search_pre_element_count = 0
                self._search_needs_confirm = False
                self._search_confirm_app = ""
                return {
                    "thought": "search results are visible on screen — task complete",
                    "action_type": "complete",
                }

        if result_signals >= 3:
            logger.info(f"[T1] Search results detected by signals ({result_signals}) — task complete")
            self._search_pre_element_count = 0
            self._search_needs_confirm = False
            self._search_confirm_app = ""
            return {
                "thought": "search results are visible on screen — task complete",
                "action_type": "complete",
            }

        return None

    def _detect_click_to_type_pattern(
        self,
        task: MobileTaskRequest,
        ui_tree: SemanticUITree,
    ) -> Optional[Dict[str, Any]]:
        """
        If the same focusable element is clicked repeatedly with no screen change,
        issue a type action instead of another click.
        """
        if self.stuck_counter < 2 or len(self.action_history) < 2:
            return None

        last_two = self.action_history[-2:]
        if not all(h.get("action", {}).get("action_type") == "click" for h in last_two):
            return None

        eids = [h.get("action", {}).get("element_id") for h in last_two]
        if eids[0] is None or eids[0] != eids[1]:
            return None

        try:
            elem = ui_tree.get_element_by_id(int(eids[0]))
        except Exception:
            elem = None

        if not elem or not (elem.focusable or elem.type in ("textfield", "edittext")):
            return None

        text = self._extract_text_to_type(task, elem)
        if not text:
            return None

        logger.info(f"[T1] Click→type rescue: element {eids[0]} clicked repeatedly; typing now")
        return {
            "thought": "field is focused from previous click — typing now",
            "action_type": "type",
            "element_id": int(eids[0]),
            "text": text,
            "clear_first": True,
        }

    def _extract_text_to_type(self, task: MobileTaskRequest, element: Any) -> str:
        """Resolve best candidate text for a focused input field."""
        extra = task.extra_params or {}
        web_params = extra.get("web_params") or {}

        explicit = (web_params.get("text") or extra.get("text") or "").strip()
        if explicit:
            return explicit

        quoted = re.findall(r"['\"]([^'\"]{1,200})['\"]", task.ai_prompt or "")
        if quoted:
            hint_blob = (
                f"{getattr(element, 'hint_text', '') or ''} "
                f"{getattr(element, 'content_description', '') or ''} "
                f"{getattr(element, 'resource_id', '') or ''}"
            ).lower()
            if "subject" in hint_blob and len(quoted) >= 1:
                return quoted[0]
            if any(kw in hint_blob for kw in ("body", "message", "compose")) and len(quoted) >= 2:
                return quoted[1]
            return quoted[0]

        match = re.search(
            r"(?:type|enter|fill\s+(?:in|with)?|write|search\s+for|find)\s+['\"]?([^'\".,!?]{2,120})['\"]?",
            task.ai_prompt or "",
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip().strip("'\"")

        return ""

    def _detect_enter_needed(
        self,
        task: MobileTaskRequest,
        ui_tree: SemanticUITree,
    ) -> Optional[Dict[str, Any]]:
        """
        After a type action on a search field, press ENTER if IME didn't auto-submit.
        Fires only once per type sequence. Universal across any app.
        """
        if not self.action_history:
            return None

        last = self.action_history[-1]
        last_action = last.get("action", {})
        if last_action.get("action_type") != "type":
            return None

        # Only fire if typed text is non-trivial
        typed_text = (last_action.get("text") or "").strip()
        if not typed_text or len(typed_text) < 2:
            return None

        # Only fire if the field looks like a search/URL/query field
        eid = last_action.get("element_id")
        if eid is None:
            return None

        try:
            elem = ui_tree.get_element_by_id(int(eid))
        except Exception:
            elem = None

        if elem is None:
            return None

        blob = " ".join([
            (elem.hint_text or ""),
            (elem.content_description or ""),
            (elem.resource_id or ""),
            (elem.text or ""),
        ]).lower()

        is_search_field = any(kw in blob for kw in (
            "search", "query", "find", "url", "address", "omnibox",
            "search_src_text", "search_edit_text", "url_bar",
        ))
        if not is_search_field:
            return None

        # Don't fire if IME already submitted (flag was set)
        if self._search_just_submitted or self._search_needs_confirm:
            return None

        # Don't fire if text is already verified as applied and screen changed
        if self._search_pre_element_count > 0:
            return None

        # Don't fire if we already pressed enter this sequence
        enter_already_pressed = any(
            h.get("action", {}).get("action_type") == "global_action"
            and h.get("action", {}).get("global_action") == "ENTER"
            for h in self.action_history[-3:]
        )
        if enter_already_pressed:
            return None

        logger.info(f"[T1] Post-type ENTER: submitting search for '{typed_text[:40]}'")
        return {
            "thought": f"typed query is ready — pressing ENTER to submit",
            "action_type": "global_action",
            "global_action": "ENTER",
        }

    def _normalize_step_for_storage(self, step_instruction: str) -> str:
        normalized = re.sub(r"'[^']{3,}'", "<value>", step_instruction)
        normalized = re.sub(r'"[^"]{3,}"', "<value>", normalized)
        normalized = re.sub(r'[\w.+-]+@[\w-]+\.[a-z]+', '<email>', normalized)
        normalized = re.sub(r'\b\d{7,}\b', '<phone>', normalized)
        return normalized

    def _format_extra_params(self, extra_params: Dict[str, Any]) -> str:
        parts = []
        status_phrases = {
            "task completed", "screen changed", "timed out",
            "max steps", "completed", "failed",
        }
        for k, v in extra_params.items():
            if k in _INTERNAL_KEYS or v is None: continue

            if k == "input_content":
                raw = str(v).strip().lower()
                if any(phrase in raw for phrase in status_phrases) and len(raw) < 80:
                    logger.debug(f"[T3] Suppressing status input_content: '{str(v)[:40]}'")
                    continue

            if k == "input_content" and isinstance(v, str) and v.strip().startswith("{"):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, dict):
                        parts.append("GENERATED CONTENT:")
                        for fk, fv in parsed.items():
                            if fv: parts.append(f"  {fk}: {fv}")
                        continue
                except Exception:
                    pass
            parts.append(f"{k.upper()}: {v!r}")
        return ("\n".join(parts) + "\n⚠️ USE THESE EXACT VALUES.") if parts else ""

    def _is_gmail_compose_screen(self, ui_tree: SemanticUITree) -> bool:
        if ui_tree is None:
            return False
        has_to = any((e.text or "").lower() == "to" for e in ui_tree.elements)
        has_subject = any((e.text or "").lower() == "subject" for e in ui_tree.elements)
        has_send = any((e.content_description or "") == "Send" for e in ui_tree.elements)
        return has_send and (has_to or has_subject)

    # ══════════════════════════════════════════════════════════════════════
    #  APP VERIFICATION
    # ══════════════════════════════════════════════════════════════════════

    async def _verify_app_llm(self, target_app: str, ui_tree: SemanticUITree) -> Dict[str, Any]:
        app_name    = (ui_tree.app_name or "").lower()
        app_package = (ui_tree.app_package or "").lower()
        t = (target_app or "").lower().strip()

        # Deterministic alias acceptance before LLM verification.
        if t in {"google", "google search", "google page"} and "chrome" in app_package:
            return {"success": True, "actual": app_name, "reason": "google search can run inside chrome"}
        if t in {"chrome", "google chrome"} and ("chrome" in app_package or "chrome" in app_name):
            return {"success": True, "actual": app_name, "reason": "target matches chrome aliases"}

        cur_state   = self._detect_device_state(ui_tree)
        if "home" in cur_state and "home" in self._prev_device_state:
            return {"success": False, "actual": app_name, "reason": "still on home screen"}
        summary = " | ".join(
            (e.text or e.content_description or "")[:25]
            for e in ui_tree.elements[:10] if (e.text or e.content_description)
        )
        prompt = (f"Target: '{target_app}'\nPackage: {app_package}\nName: {app_name}\n"
                  f"Screen: {summary}\n\nIs this '{target_app}'?\n"
                  f'Reply ONLY: {{"correct":true/false,"reason":"one sentence"}}')
        try:
            logger.debug("[T1] App verify prompt:\n" + prompt)
            resp = await self._llm_chat_completion(
                model=self.model, messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=60,
            )
            self._track_llm_usage(resp)
            raw = resp.choices[0].message.content.strip()
            logger.info(f"[T1] App verify raw: {raw[:200]}")
            logger.debug(f"[T1] App verify raw full:\n{raw}")
            js = self._extract_json(raw)
            if js:
                d = json.loads(js)
                correct = bool(d.get("correct", True))
                logger.info(f"[T1] App verify: correct={correct} — {d.get('reason','')}")
                return {"success": correct, "actual": app_name, "reason": d.get("reason", "")}
            logger.warning("[T1] App verify: no JSON extracted from model response")
        except Exception as e:
            logger.warning(f"[T1] App verify LLM failed: {e}")
        if "home" in self._prev_device_state and "in_app" in cur_state:
            return {"success": True, "actual": app_name, "reason": "permissive accept"}
        return {"success": True, "actual": app_name, "reason": "verification fallback"}

    async def _verify_recipe_context(
        self,
        recipe: RecipeStep,
        current_goal: str,
    ) -> bool:
        """
        Lightweight sanity check for EXECUTE-band cached steps.
        Verifies whether the first retrieved step matches current goal/context.
        """
        if recipe.similarity > 0.92:
            logger.debug(f"[T2] Context verify skipped (high sim={recipe.similarity:.3f})")
            return True

        is_nav = any(
            kw in (recipe.step_instruction or "").lower()
            for kw in ("open", "launch", "start", "navigate to")
        )
        if is_nav:
            logger.debug("[T2] Context verify skipped — nav step always valid")
            return True

        screen_summary = " | ".join(
            (e.text or e.content_description or "")[:20]
            for e in (self.current_ui_tree.elements[:8] if self.current_ui_tree else [])
            if (e.text or e.content_description)
        ) or "unknown screen"

        prompt = (
            f"Cached step: '{recipe.step_instruction}'\n"
            f"Current goal: '{current_goal}'\n"
            f"Screen elements include: {screen_summary}\n\n"
            f"Is this cached step appropriate for achieving the current goal?\n"
            f'Reply ONLY: {{"ok":true/false,"r":"one phrase"}}'
        )
        try:
            logger.debug("[T2] Context verify prompt:\n" + prompt)
            resp = await self._llm_chat_completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=35,
            )
            self._track_llm_usage(resp)
            raw = resp.choices[0].message.content.strip()
            logger.info(f"[T2] Context verify raw: {raw[:200]}")
            logger.debug(f"[T2] Context verify raw full:\n{raw}")
            js = self._extract_json(raw)
            if js:
                d = json.loads(js)
                ok = bool(d.get("ok", True))
                logger.info(f"[T2] Context verify: ok={ok} — {d.get('r','')}")
                return ok
            logger.warning("[T2] Context verify: no JSON extracted; defaulting to permissive True")
        except Exception as e:
            logger.debug(f"[T2] Context verify failed: {e}")
        return True

    # ══════════════════════════════════════════════════════════════════════
    #  LEARNING
    # ══════════════════════════════════════════════════════════════════════

    def _store_learned_steps(self, step_instruction, overall_goal, app, actions):
        if not actions or not self.current_ui_tree: return
        sig       = build_screen_signature(self.current_ui_tree)

        is_nav_task = any(
            kw in step_instruction.lower()
            for kw in ("open", "launch", "start", "navigate to", "open the", "open default")
        )

        selectors: List[Dict[str, str]] = []
        stored_action_type = "global_action" if is_nav_task else "click"
        if not is_nav_task and self.last_clicked_element:
            elem = self.current_ui_tree.get_element_by_id(self.last_clicked_element)
            if elem:
                for by, attr in [
                    ("content_desc",     elem.content_description),
                    ("resource_id_tail", (elem.resource_id or "").split(":id/")[-1]
                                         if ":id/" in (elem.resource_id or "") else ""),
                    ("resource_id",      elem.resource_id),
                    ("text",             elem.text),
                ]:
                    if attr: selectors.append({"by": by, "value": attr})
        last = self.action_history[-1] if self.action_history else {}
        atype = stored_action_type if is_nav_task else last.get("action", {}).get("action_type", "click")
        normalized_step = self._normalize_step_for_storage(step_instruction)
        record_id = self.task_memory.store(
            step_instruction=normalized_step, overall_goal=overall_goal,
            app=app, action_type=atype, screen_signature=sig,
            selectors=selectors, demonstrated=0, success_count=1,
        )
        if record_id:
            logger.info(
                f"[CACHE] Stored Tier 3 success: '{normalized_step[:55]}' (from '{step_instruction[:45]}') id={record_id[:8]}"
                + (" (nav task — no selectors)" if is_nav_task else "")
            )

    def _extract_screen_content(self, ui_tree: SemanticUITree, max_chars: int = 1500) -> str:
        """
        Extract readable text content from current screen.
        Used when the task goal requires returning data to the coordinator.
        Filters out boilerplate (nav bars, icons) and keeps content elements.
        """
        lines = []
        seen = set()
        for e in ui_tree.elements:
            text = (e.text or "").strip()
            desc = (e.content_description or "").strip()
            label = text or desc
            if not label or len(label) < 3:
                continue
            # Skip navigation boilerplate
            if label.lower() in {"back", "more", "menu", "home", "search", "close", 
                                 "share", "bookmark", "tab", "new tab", "reload"}:
                continue
            if label in seen:
                continue
            seen.add(label)
            lines.append(label)
            if sum(len(l) for l in lines) > max_chars:
                break
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════
    #  SCREEN DETECTION
    # ══════════════════════════════════════════════════════════════════════

    def _is_in_time_picker(self, ui_tree: SemanticUITree) -> bool:
        has_ampm = has_ok = has_time = False
        for e in ui_tree.elements:
            t = (e.text or "").upper().strip()
            d = (e.content_description or "").lower()
            if e.type == "button" and t in ("AM", "PM"): has_ampm = True
            if e.type == "button" and t in ("OK", "CANCEL"): has_ok = True
            if "o'clock" in d or "minute" in d: has_time = True
        if not has_time:
            has_time = any(e.type == "textfield" and (e.text or "").strip().isdigit()
                           for e in ui_tree.elements)
        return has_ampm and has_ok and has_time

    def _is_alarm_list_screen(self, ui_tree: SemanticUITree) -> bool:
        if ui_tree is None:
            return False
        in_clock = "clock" in (ui_tree.app_name or "").lower()
        if not in_clock:
            return False
        has_switch = any(e.type == "switch" for e in ui_tree.elements)
        has_add = any(
            "add alarm" in (e.content_description or e.text or "").lower()
            for e in ui_tree.elements
        )
        return has_switch and has_add

    def _detect_device_state(self, ui_tree: SemanticUITree) -> str:
        app_name    = (ui_tree.app_name or "").lower()
        screen_name = (ui_tree.screen_name or "").lower()
        if "aura" in app_name: return "in_aura"
        home_indicators = ["launcher", "home screen", "desktop", "wallpaper",
                           "homescreen", "pixel launcher", "android launcher"]
        if any(h in app_name or h in screen_name for h in home_indicators): return "home_screen"
        if "app drawer" in screen_name or "all apps" in screen_name: return "app_drawer"
        return f"in_app_{app_name.replace('.', '_')}"

    def _detect_stuck(self) -> bool:
        if self.stuck_counter <= 5: return False
        if len(self.previous_ui_trees) >= 4:
            counts = [len(t.elements) for t in self.previous_ui_trees[-4:]]
            if len(set(counts)) > 1: return False
            if counts[0] <= 3: return True
        if len(self.action_history) >= 3:
            recent = [h["action"]["action_type"] for h in self.action_history[-3:]]
            if "type" in recent or "scroll" in recent: return False
        return self.stuck_counter >= 6

    def _screen_changed_significantly(self, current_ui: SemanticUITree) -> bool:
        if not self._initial_ui_signature: return False
        init_count, init_screen = self._initial_ui_signature
        cur_count  = len(current_ui.elements)
        cur_screen = current_ui.screen_name or ""
        if init_count > 0 and abs(cur_count - init_count) / init_count >= 0.20: return True
        if init_screen and cur_screen and init_screen.lower() != cur_screen.lower(): return True
        return False

    def _detect_thought_loop(self, thought: str) -> bool:
        norm = thought.lower().strip()
        self.recent_thoughts.append(norm)
        if len(self.recent_thoughts) > 5: self.recent_thoughts.pop(0)
        if len(self.recent_thoughts) >= 3 and len(set(self.recent_thoughts[-3:])) == 1:
            logger.error(f"🔁 THOUGHT LOOP: '{thought}'")
            return True
        return False

    def _verify_complete_claim(self, action_json: Dict, goal: str) -> Dict:
        gl = goal.lower()
        if not self.current_ui_tree:
            return action_json

        if any(kw in gl for kw in ("fill", "type", "enter", "set")):
            expected = []
            em = re.search(r"(?:to|recipient)\s+([a-zA-Z0-9._%+-]+@\S+)", gl)
            if em: expected.append(em.group(1))
            sm = re.search(r"subject\s+['\"]?([^'\"]+)['\"]?", gl)
            if sm: expected.append(sm.group(1).strip())
            tm = re.search(r"(\d{1,2}:\d{2}\s*(?:am|pm))", gl, re.IGNORECASE)
            if tm: expected.append(tm.group(1).strip())
            if expected:
                screen_text = " ".join(
                    (e.text or "") + " " + (e.content_description or "")
                    for e in self.current_ui_tree.elements
                ).lower()
                missing = [v for v in expected if v.lower() not in screen_text]
                if missing:
                    logger.error(f"[T3] Complete rejected: {missing} not on screen")
                    return {"action_type": "scroll", "direction": "down", "duration": 300,
                            "thought": "verify failed — scroll"}

        if any(kw in gl for kw in ("search", "find", "look for")):
            target_match = re.search(
                r"(?:search|find|look)\s+for\s+['\"]?(\w+)['\"]?", gl, re.IGNORECASE
            )
            if target_match:
                target = target_match.group(1).lower()
                screen_text = " ".join(
                    (e.text or "") + " " + (e.content_description or "")
                    for e in self.current_ui_tree.elements
                ).lower()
                results_indicators = {"result", "install", "open", "get", "download", "free"}
                has_target = target in screen_text
                has_results = any(ind in screen_text for ind in results_indicators)
                if not has_target and not has_results:
                    logger.error(f"[T3] Complete rejected: search target '{target}' not visible")
                    return {"action_type": "scroll", "direction": "up", "duration": 300,
                            "thought": "search results not confirmed — scroll up"}

        return action_json

    # ══════════════════════════════════════════════════════════════════════
    #  UTILITIES
    # ══════════════════════════════════════════════════════════════════════

    def _extract_target_app(self, goal: str) -> Optional[str]:
        m = re.match(
            r"(?:please\s+)?(?:open|launch|start|navigate\s+to)\s+(?:the\s+)?(.+)$",
            (goal or "").strip(),
            re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip().lower()
            # Stop at conjunctions/follow-up intents like: "open chrome and search ..."
            raw = re.split(r"\b(?:and|then|to|for|on|in|at|with|from)\b", raw, maxsplit=1)[0]
            raw = re.sub(r"[^a-z0-9\s]", " ", raw)
            raw = re.sub(r"\s+", " ", raw).strip()
            raw = re.sub(r"\s+(login|home|main|app|page|screen|application|applications|mobile|device)$", "", raw).strip()

            if not raw:
                return None

            tokens = [t for t in raw.split() if t not in {"the", "a", "an", "my", "this", "that", "app", "it"}]
            if not tokens:
                return None

            c = " ".join(tokens[:2]).strip()
            target_aliases = {
                "email": "gmail",
                "mail": "gmail",
                "default email": "gmail",
                "email app": "gmail",
                "default email app": "gmail",
                "mail app": "gmail",
                "google maps": "maps",
                "google chrome": "chrome",
                "chrome browser": "chrome",
                "play store": "play_store",
                "app store": "play_store",
                "google play": "play_store",
                "youtube": "youtube",
            }
            if c in target_aliases:
                return target_aliases[c]
            for alias, canonical in target_aliases.items():
                if alias in c:
                    return canonical

            if c and not any(w in c.split() for w in _GENERIC_PAGE_WORDS):
                return c
        return None

    def _target_is_generic_page(self, target: str) -> bool:
        return bool(set(target.lower().split()) & _GENERIC_PAGE_WORDS)

    def _in_target_app(self, app_name: str, device_state: str) -> bool:
        if not app_name or app_name == "unknown":
            return True
        normalized = _normalize_app_token(app_name)
        state = (device_state or "").lower()
        if not state.startswith("in_app_"):
            return False
        if normalized and normalized in state:
            return True

        for canonical, aliases in _APP_PACKAGE_ALIASES.items():
            if normalized == canonical or normalized in aliases:
                if any(alias in state for alias in aliases):
                    return True

        return False

    def _is_click_task(self, goal: str) -> bool:
        return bool(re.search(r"\b(click|tap|press)\b", goal.lower()))

    def _typed_value_applied(self, action_json: Dict[str, Any], ui_tree: SemanticUITree) -> bool:
        eid = action_json.get("element_id")
        text = (action_json.get("text") or "").strip()
        if eid is None or not text:
            return True
        try:
            elem = ui_tree.get_element_by_id(int(eid))
        except Exception:
            elem = None
        if not elem:
            return False
        class_name = (elem.class_name or "").lower()
        elem_type = (elem.type or "").lower()
        if class_name == "android.webkit.webview" or elem_type == "webview":
            logger.debug(f"[T3] Type verify skipped — WebView element {eid}")
            return True
        live = (elem.text or "").strip()
        return live == text or text in live

    def _get_live_field_value(self, element_id: int) -> Optional[str]:
        if not self.current_ui_tree: return None
        for e in self.current_ui_tree.elements:
            if e.element_id == element_id:
                return (e.text or "").strip()
        return None

    def _wait_for_action(self, action_type: Optional[str]) -> float:
    # If we're inside a time picker, minimize waits — screen doesn't change between fields
        if (self.current_ui_tree and self._is_in_time_picker(self.current_ui_tree)):
            return {"click": 0.3, "type": 0.3, "global_action": 1.0}.get(action_type or "", 0.3)

        # Extended wait after search submission so suggestions/results can load
        if action_type == "type" and self._search_just_submitted:
            self._search_just_submitted = False
            self._search_needs_confirm = True  # T1 will handle clicking first suggestion
            self._search_confirm_app = (self.current_ui_tree.app_package or "") if self.current_ui_tree else ""
            # ── NEW: snapshot element count before results load ──
            self._search_pre_element_count = len(self.current_ui_tree.elements) if self.current_ui_tree else 0
            return 2.5

        return {
            "click": 3.5, "global_action": 3.0,
            "type": 0.8, "scroll": 1.5, "swipe": 2.0, "wait": 0.1,
        }.get(action_type or "", 1.0)

    def _extract_json(self, text: str) -> Optional[str]:
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*",     "", text)
        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        return m.group(0).strip() if m else None

    def _reset_state(self):
        self.failed_elements.clear()
        self.app_drawer_attempted     = False
        self._app_drawer_phase        = 0
        self.incomplete_ui_count      = 0
        self.stuck_counter            = 0
        self.last_action_was_click    = False
        self.thought_loop_recoveries  = 0
        self.recent_thoughts.clear()
        self._switched_to_text_input  = False
        self._keyboard_dismissed      = False
        self._prev_device_state       = "unknown"
        self._t2_completed_steps      = []
        self._add_alarm_clicked       = False
        self._add_alarm_screen_sig    = ""
        self._popup_tap_attempted     = set()
        self.typed_texts              = {}
        self.action_history           = []
        self.previous_ui_trees        = []
        self._time_picker_pm_attempts = 0
        self._last_t2_hint_record_id  = None
        self._search_just_submitted   = False
        self._search_needs_confirm    = False
        self._search_confirm_app      = ""
        self._search_pre_element_count = 0
        self._email_just_sent         = False
        self.token_usage = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls          = 0
        self.total_ui_elements_seen   = 0
        self.max_ui_elements_seen     = 0
        self.ui_samples_count         = 0
        self.tier_stats = {"tier1": 0, "tier2": 0, "tier3_llm": 0}
        self._initial_ui_signature    = None

    # ── Device I/O ─────────────────────────────────────────────────────────

    def _resolve_u2_serial(self) -> str:
        """Resolve a usable ADB serial from task/context/logical device ids."""
        logical_ids = {"", "default_device", "android_device_1", "android_device"}

        candidates: List[str] = []
        if self.current_task is not None:
            extra = (self.current_task.extra_params or {})
            ctx = (self.current_task.context or {})
            for key in ("adb_serial", "android_serial", "uiautomator_serial", "device_serial", "serial", "device_id"):
                v = (extra.get(key) or ctx.get(key) or "").strip()
                if v:
                    candidates.append(v)

        env_serial = (os.getenv("AURA_ANDROID_SERIAL") or os.getenv("ANDROID_SERIAL") or "").strip()
        if env_serial:
            candidates.append(env_serial)

        if self.device_id:
            candidates.append((self.device_id or "").strip())

        # Deduplicate while preserving order
        seen: Set[str] = set()
        candidates = [c for c in candidates if c and not (c in seen or seen.add(c))]

        connected_serials: List[str] = []
        try:
            import adbutils
            connected_serials = [d.serial for d in adbutils.adb.device_list() if getattr(d, "serial", None)]
        except Exception as e:
            logger.debug(f"[UIA2] adb serial discovery unavailable: {e}")

        for c in candidates:
            if c in logical_ids:
                continue
            if connected_serials and c in connected_serials:
                return c

        if connected_serials:
            # Deterministic preference: emulator first, otherwise first connected device
            for s in connected_serials:
                if s.startswith("emulator-"):
                    return s
            return connected_serials[0]

        # No discovered devices; if candidate is non-logical, allow u2 to attempt direct connect.
        for c in candidates:
            if c not in logical_ids:
                return c
        return ""

    def _get_u2_device(self):
        resolved_serial = self._resolve_u2_serial()
        if self._u2_device is None or resolved_serial != self._u2_serial:
            if resolved_serial:
                logger.info(f"[UIA2] Connecting with serial='{resolved_serial}' (requested='{self.device_id}')")
                self._u2_device = u2.connect(resolved_serial)
            else:
                logger.info(f"[UIA2] Connecting with default device selection (requested='{self.device_id}')")
                self._u2_device = u2.connect()
            self._u2_serial = resolved_serial
        return self._u2_device

    async def _fetch_ui_tree_from_device(self) -> Optional[SemanticUITree]:
        try:
            device = await asyncio.wait_for(
                asyncio.to_thread(self._get_u2_device),
                timeout=_UIA2_CONNECT_TIMEOUT,
            )
            xml_dump = await asyncio.wait_for(
                asyncio.to_thread(device.dump_hierarchy),
                timeout=max(self.uiautomator_timeout, _UIA2_HIERARCHY_TIMEOUT),
            )
            ui_tree = self._parse_uia2_tree(xml_dump)
            if ui_tree is not None:
                return ui_tree
        except asyncio.TimeoutError:
            logger.warning(
                "[UIA2] UI tree fetch timed out while waiting for device or hierarchy dump"
            )
        except Exception as e:
            logger.warning(f"[UIA2] UI tree fetch error via uiautomator2 client: {e}")

        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{self.uiautomator_base_url}/dump/hierarchy",
                    timeout=5.0,
                )
                if r.status_code == 200:
                    data: Any
                    try:
                        data = r.json()
                    except Exception:
                        data = r.text
                    ui_tree = self._parse_uia2_tree(data)
                    if ui_tree is not None:
                        return ui_tree
        except Exception as e:
            logger.debug(f"UI tree fetch error via HTTP fallback: {e}")
        return None

    async def _fetch_ui_tree_with_retries(
        self, action_type: str, max_attempts: int = 3, retry_delay: float = 2.0,
    ) -> Optional[SemanticUITree]:
        min_elems = 5 if action_type == "click" else 2
        for attempt in range(max_attempts):
            ui = await self._fetch_ui_tree_from_device()
            if not ui:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                logger.warning(
                    f"[UIA2] Could not fetch UI tree after {max_attempts} attempts "
                    f"(action_type={action_type}, device={self.device_id}, serial={self._u2_serial or 'unresolved'})"
                )
                return None
            if len(ui.elements) >= min_elems: return ui
            if attempt < max_attempts - 1: await asyncio.sleep(retry_delay)
            else: return ui
        return None

    async def _execute_action_on_device(self, action: UIAction) -> ActionResult:
        if action.action_type == "wait":
            duration_ms = int(action.duration or 1000)
            await asyncio.sleep(duration_ms / 1000.0)
            return ActionResult(
                action_id=action.action_id,
                success=True,
                error=None,
                execution_time_ms=duration_ms,
            )

        # ── Fast path: Global keycodes via uiautomator2 directly (most reliable) ──
        if action.action_type == "global_action" and action.global_action in ("ENTER", "SEARCH", "BACK", "HOME"):
            try:
                device = await asyncio.wait_for(
                    asyncio.to_thread(self._get_u2_device),
                    timeout=3.0,
                )
                key_map = {
                    "ENTER":  "enter",
                    "SEARCH": "search",
                    "BACK":   "back",
                    "HOME":   "home",
                }
                key_name = key_map.get(action.global_action, action.global_action.lower())
                await asyncio.to_thread(device.press, key_name)
                return ActionResult(
                    action_id=action.action_id,
                    success=True,
                    error=None,
                    execution_time_ms=0,
                )
            except Exception as e:
                logger.debug(f"[UIA2] global_action {action.global_action} via u2 failed: {e}")
                # fall through to HTTP path

        if action.action_type == "type":
            try:
                device = await asyncio.wait_for(
                    asyncio.to_thread(self._get_u2_device),
                    timeout=3.0,
                )
                element = None

                # Focus the target field first when available.
                if self.current_ui_tree and action.element_id is not None:
                    try:
                        element = self.current_ui_tree.get_element_by_id(int(action.element_id))
                    except Exception:
                        element = None
                    if element is not None:
                        cx, cy = self._uia_center(getattr(element, "bounds", None))
                        if cx > 0 or cy > 0:
                            await asyncio.to_thread(device.click, cx, cy)
                            await asyncio.sleep(0.20)

                if action.clear_first:
                    success = await self._robust_clear_and_type(device, element, action.text or "")
                    if not success:
                        logger.error("[UIA2] All clear strategies exhausted — typing without clear")
                        await asyncio.to_thread(device.send_keys, action.text or "", clear=False)
                else:
                    await asyncio.to_thread(
                        device.send_keys,
                        action.text or "",
                        clear=False,
                    )

                is_search_field = False
                if self.current_ui_tree and action.element_id is not None:
                    try:
                        typed_elem = self.current_ui_tree.get_element_by_id(int(action.element_id))
                    except Exception:
                        typed_elem = None
                    if typed_elem is not None:
                        blob = " ".join([
                            (typed_elem.hint_text or ""),
                            (typed_elem.content_description or ""),
                            (typed_elem.resource_id or ""),
                        ]).lower()
                        is_search_field = any(kw in blob for kw in (
                            "search", "query", "find", "url", "address", "omnibox"
                        ))

                if is_search_field:
                    await asyncio.sleep(0.3)
                    submitted = False
                    try:
                        await asyncio.to_thread(device.send_action, "search")
                        logger.info("[UIA2] Search field: sent IME action 'search' after typing")
                        submitted = True
                    except Exception as e:
                        logger.debug(f"[UIA2] IME search action failed: {e}")
                        try:
                            await asyncio.to_thread(device.press, "enter")
                            logger.info("[UIA2] Search field: IME failed; pressed Enter fallback")
                            submitted = True
                        except Exception as enter_e:
                            logger.debug(f"[UIA2] Enter fallback failed: {enter_e}")

                    if submitted:
                        self._search_just_submitted = True

                return ActionResult(
                    action_id=action.action_id,
                    success=True,
                    error=None,
                    execution_time_ms=0,
                )
            except Exception as e:
                logger.warning(f"Type execute error via uiautomator2 client: {e}")
                return ActionResult(
                    action_id=action.action_id,
                    success=False,
                    error=str(e),
                    execution_time_ms=0,
                )

        try:
            async with httpx.AsyncClient() as client:
                payload = self._action_to_uia2(action)
                r = await client.post(
                    f"{self.uiautomator_base_url}/jsonrpc/0",
                    json=payload,
                    timeout=self.uiautomator_timeout,
                )
                response_data: Dict[str, Any]
                try:
                    response_data = r.json()
                except Exception:
                    response_data = {"error": {"message": r.text or f"HTTP {r.status_code}"}}
                success = r.status_code == 200 and not response_data.get("error")
                return ActionResult(
                    action_id=action.action_id,
                    success=success,
                    error=None if success else response_data.get("error", {}).get("message", f"HTTP {r.status_code}"),
                    execution_time_ms=int(r.elapsed.total_seconds() * 1000),
                )
        except Exception as e:
            logger.error(f"Action execute error: {e}")
            return ActionResult(action_id=action.action_id, success=False,
                                error=str(e), execution_time_ms=0)

    async def _robust_clear_and_type(self, device, element, text: str) -> bool:
        """Multi-strategy clear+type fallback chain."""
        try:
            await asyncio.to_thread(device.send_keys, text, clear=True)
            return True
        except Exception as e:
            logger.debug(f"[UIA2] Clear strategy 1 failed: {e}")

        try:
            await self._resource_id_set_text(device, element, text)
            return True
        except Exception as e:
            logger.debug(f"[UIA2] Clear strategy 2 failed: {e}")

        try:
            if element is not None:
                cx, cy = self._uia_center(getattr(element, "bounds", None))
                if cx > 0 or cy > 0:
                    await asyncio.to_thread(device.double_click, cx, cy, 0.1)
                    await asyncio.sleep(0.12)
                    await asyncio.to_thread(device.click, cx, cy)
                    await asyncio.sleep(0.10)
            await asyncio.to_thread(device.send_keys, text, clear=False)
            return True
        except Exception as e:
            logger.debug(f"[UIA2] Clear strategy 3 failed: {e}")

        return False

    async def _resource_id_set_text(self, device, element, text: str) -> None:
        """Bypass keyboard clear by direct set_text when resource id exists."""
        if element and element.resource_id:
            rid = element.resource_id
            await asyncio.to_thread(lambda: device(resourceId=rid).set_text(text))
            return
        raise ValueError("no resource_id available for set_text fallback")

    def _to_ui_action(self, action_json: Dict) -> Optional[UIAction]:
        atype = action_json.get("action_type")
        if not atype: return None
        kwargs: Dict[str, Any] = {"action_type": atype,
                                   "text": action_json.get("text"),
                                   "duration": action_json.get("duration", 1000)}
        if atype == "coordinate_tap":
            dw = getattr(self, "_device_width",  1080)
            dh = getattr(self, "_device_height", 2340)
            kwargs["x"] = int(dw * action_json.get("x_percent", 50) / 100)
            kwargs["y"] = int(dh * action_json.get("y_percent", 85) / 100)
        elif atype == "swipe":
            kwargs["start_x_percent"] = action_json.get("start_x_percent", 50)
            kwargs["start_y_percent"] = action_json.get("start_y_percent", 80)
            kwargs["end_x_percent"]   = action_json.get("end_x_percent",   50)
            kwargs["end_y_percent"]   = action_json.get("end_y_percent",   20)
        elif atype in ("click", "type"):
            eid = action_json.get("element_id")
            if eid is None:
                logger.error(f"Missing element_id for {atype}")
                return None
            try: kwargs["element_id"] = int(eid)
            except (ValueError, TypeError):
                logger.error(f"Invalid element_id: {eid}")
                return None
            if atype == "type":
                kwargs["clear_first"] = bool(action_json.get("clear_first", False))
        if action_json.get("direction"):    kwargs["direction"]    = action_json["direction"]
        if action_json.get("global_action"): kwargs["global_action"] = action_json["global_action"]
        try: return UIAction(**kwargs)
        except Exception as e:
            logger.error(f"UIAction build error: {e}")
            return None

    # ── Stats ───────────────────────────────────────────────────────────────

    def _track_llm_usage(self, response: Any):
        try:
            usage = getattr(response, "usage", None)
            if usage:
                self.token_usage["prompt"]     += int(getattr(usage, "prompt_tokens",     0) or 0)
                self.token_usage["completion"] += int(getattr(usage, "completion_tokens", 0) or 0)
                self.token_usage["total"]      += int(getattr(usage, "total_tokens",      0) or 0)
            self.total_llm_calls += 1
        except Exception: pass

    def _update_ui_stats(self, ui_tree: SemanticUITree):
        n = len(ui_tree.elements)
        self.total_ui_elements_seen += n
        self.max_ui_elements_seen    = max(self.max_ui_elements_seen, n)
        self.ui_samples_count       += 1

    def _log_task_metrics(self, status, steps, elapsed, actions):
        avg   = self.total_ui_elements_seen / max(1, self.ui_samples_count)
        t     = self.tier_stats
        total = sum(t.values())
        saved = t.get("tier1", 0) + t.get("tier2", 0)
        logger.info("\n" + "="*70 + "\n📊 TASK METRICS\n" + "="*70)
        logger.info(f"Status: {status} | Steps: {steps} | Actions: {len(actions)} | Elapsed: {elapsed:.2f}s")
        logger.info(f"LLM calls: {self.total_llm_calls} | Tokens: {self.token_usage}")
        logger.info(f"UI: samples={self.ui_samples_count} avg={avg:.1f} max={self.max_ui_elements_seen}")
        logger.info(f"Tiers — T1:{t.get('tier1',0)} T2:{t.get('tier2',0)} T3:{t.get('tier3_llm',0)}")
        if total > 0: logger.info(f"LLM savings: {saved}/{total} ({saved/total*100:.0f}%)")
        logger.info(f"Memory: {self.task_memory.stats()}\n" + "="*70)

    def _build_result(self, task_id, status, steps, actions, elapsed,
                      error=None, completion_reason=None) -> MobileTaskResult:
        self._log_task_metrics(status, steps, elapsed, actions)
        return MobileTaskResult(
            task_id=task_id, status=status, steps_taken=steps,
            actions_executed=actions, execution_time_ms=int(elapsed * 1000),
            error=error, completion_reason=completion_reason,
            token_usage=dict(self.token_usage), llm_calls=self.total_llm_calls,
        )

    def _build_error_result(self, task_id: str, error: str) -> MobileTaskResult:
        self._log_task_metrics("failed", 0, 0.0, [])
        return MobileTaskResult(
            task_id=task_id, status="failed", steps_taken=0, actions_executed=[],
            execution_time_ms=0, error=error,
            token_usage=dict(self.token_usage), llm_calls=self.total_llm_calls,
        )

    # FIX 2: expose as static for handler
    @staticmethod
    def smart_timeout(goal: str, default: int) -> int:
        return compute_smart_timeout(goal, default)


# ── Backward compat ─────────────────────────────────────────────────────────
class MobileStrategy(MobileReActStrategy):
    pass


# ── Integration function ────────────────────────────────────────────────────
async def execute_mobile_task(task: Dict[str, Any], device_id: str = "emulator-5554") -> ExecutionResult:
    from datetime import datetime
    try:
        timeout = compute_smart_timeout(task.get("ai_prompt", ""), task.get("timeout_seconds", 30))
        extra_params = dict(task.get("extra_params", {}) or {})
        if not extra_params.get("overall_goal"):
            extra_params["overall_goal"] = task.get("goal") or task.get("ai_prompt", "")
        if not extra_params.get("goal") and task.get("goal"):
            extra_params["goal"] = task.get("goal")
        mobile_task = MobileTaskRequest(
            task_id=task.get("task_id", "unknown"), ai_prompt=task.get("ai_prompt", ""),
            device_id=device_id, session_id=task.get("session_id", "default"),
            context=extra_params, extra_params=extra_params,
            max_steps=15, timeout_seconds=timeout,
        )
        uiautomator_port = int(
            (task.get("extra_params", {}) or {}).get("uiautomator_port")
            or task.get("uiautomator_port", 9008)
        )
        strategy = MobileStrategy(device_id, uiautomator_port=uiautomator_port)
        result   = await strategy.execute_task(mobile_task)
        return ExecutionResult(
            status="success" if result.status == "success" else "failed",
            task_id=result.task_id, context="mobile", action="react_loop",
            details=result.completion_reason or result.error or "Mobile task executed",
            logs=[], timestamp=datetime.now().isoformat(),
            duration=result.execution_time_ms / 1000.0,
            metadata={"token_usage": result.token_usage or {}, "llm_calls": result.llm_calls,
                      "steps_taken": result.steps_taken},
            error=result.error,
        )
    except Exception as e:
        logger.error(f"❌ execute_mobile_task: {e}", exc_info=True)
        from datetime import datetime
        return ExecutionResult(
            status="failed", task_id=task.get("task_id", "unknown"),
            context="mobile", action="react_loop", details="", logs=[],
            timestamp=datetime.now().isoformat(), duration=0.0, error=str(e),
        )