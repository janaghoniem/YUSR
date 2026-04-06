"""
mobile_strategy.py

MobileReActStrategy — 3-Tier ReAct loop for Android UI automation.

Tier 1  Deterministic handlers        (0 tokens · 0 ms)
Tier 2  ChromaDB semantic retrieval   (0 tokens · ~5 ms)
Tier 3  LLM ReAct loop                (~400 ms/step)



Debug logging:
    [CACHE] prefix — all Tier 2 / ChromaDB events
    [T1]   prefix — Tier 1 decisions
    [T2]   prefix — Tier 2 decisions
    [T3]   prefix — Tier 3 LLM decisions
"""

import asyncio
import json
import logging
import re
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

logger = logging.getLogger(__name__)

# ── Module-level singleton ─────────────────────────────────────────────────
_shared_task_memory: Optional[TaskMemory] = None

def _get_task_memory() -> TaskMemory:
    global _shared_task_memory
    if _shared_task_memory is None:
        _shared_task_memory = TaskMemory()
    return _shared_task_memory

# ── FIX 1: Session-level app memory ───────────────────────────────────────
_session_last_app: str = "unknown"
_session_last_id: str = ""

_PKG_MAP: Dict[str, str] = {
    "com.google.android.deskclock":   "clock",
    "com.google.android.gm":          "gmail",
    "com.android.chrome":             "chrome",
    "com.google.android.calendar":    "calendar",
    "com.google.android.contacts":    "contacts",
    "com.android.vending":            "play_store",
    "com.google.android.apps.maps":   "maps",
    "com.google.android.youtube":     "youtube",
    "com.google.android.calculator":  "calculator",
}

# ── App name canonical aliases ─────────────────────────────────────────────
# Maps coordinator-supplied variants → canonical names used in ChromaDB.
_APP_NAME_ALIASES: Dict[str, str] = {
    # Play Store variants
    "app store":         "play_store",
    "google play":       "play_store",
    "play store":        "play_store",
    "google play store": "play_store",
    "playstore":         "play_store",
    "appstore":          "play_store",
    # Gmail variants
    "email":             "gmail",
    "mail":              "gmail",
    "google mail":       "gmail",
    # Maps
    "google maps":       "maps",
    # Chrome variants
    "chrome browser":    "chrome",
    "google chrome":     "chrome",
    "browser":           "chrome",
    # Clock
    "clock app":         "clock",
    "google clock":      "clock",
}


def _normalize_app_name(name: str) -> str:
    """Canonicalise coordinator-supplied app name to ChromaDB app key."""
    n = (name or "").strip().lower()
    return _APP_NAME_ALIASES.get(n, n)

_LAUNCHER_PACKAGE_HINTS: Tuple[str, ...] = (
    "launcher", "systemui", "quickstep", "trebuchet", "pixel",
)


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

def _resolve_app(extra_params: Dict[str, Any], live_package: str = "") -> str:
    """
    Determine app identifier for ChromaDB queries.
    Priority: coordinator extra_params → live package → session memory.
    """
    global _session_last_app

    raw_explicit = (extra_params.get("app_name") or "").strip().lower()
    explicit = _normalize_app_name(raw_explicit)
    if explicit and explicit != "unknown":
        _session_last_app = explicit
        logger.info(
            f"[CACHE] app from coordinator: '{explicit}'"
            + (f" (raw='{raw_explicit}')" if raw_explicit != explicit else "")
        )
        return explicit

    if live_package and not _is_non_target_package(live_package):
        pkg      = live_package.lower()
        inferred = next((v for k, v in _PKG_MAP.items() if k in pkg),
                        pkg.rsplit(".", 1)[-1])
        _session_last_app = inferred
        logger.info(f"[CACHE] app inferred from package '{live_package}': '{inferred}'")
        return inferred

    if _session_last_app and _session_last_app != "unknown":
        logger.info(f"[CACHE] app from session memory: '{_session_last_app}'")
        return _session_last_app

    return "unknown"


# ── Generic page words — signals URL/descriptor not app name ───────────────
_GENERIC_PAGE_WORDS: Set[str] = {
    "page", "search", "default", "url", "website", "site", "content",
    "home", "tab", "view", "result", "results", "query", "screen",
    "address", "navigation", "navigate", "web",
}

# ── Coordinator-internal extra_params keys ─────────────────────────────────
_INTERNAL_KEYS = {
    "input_from", "device_id", "app_name", "file_path",
    "max_steps", "timeout_seconds", "language",
}

_SIG_VERIFY_THRESHOLD = 0.50


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 2: smart timeout as module-level function (handler can call it too)
# ══════════════════════════════════════════════════════════════════════════════

def compute_smart_timeout(goal: str, default: int) -> int:
    g = goal.lower()
    if any(k in g for k in ("alarm", "schedule", "set time")):  return max(90, default)
    if any(k in g for k in ("email", "compose", "recipient")):  return max(90, default)
    if any(k in g for k in ("chrome", "browser", "search", "navigate", "fill",
                             "pharmacy", "google.com", "type url")):
        return max(90, default)
    if any(k in g for k in ("search", "find", "look for")):     return max(60, default)
    return max(30, default)


# ══════════════════════════════════════════════════════════════════════════════
#  PRUNED TREE + SIGNATURE
# ══════════════════════════════════════════════════════════════════════════════

def build_pruned_tree_string(ui_tree: SemanticUITree) -> str:
    w = max(ui_tree.screen_width,  1)
    h = max(ui_tree.screen_height, 1)
    lines = [f"Screen: {ui_tree.screen_name or ui_tree.app_name}"]
    for elem in ui_tree.elements:
        if elem.visibility != "visible":            continue
        if not elem.clickable and not elem.focusable: continue
        if not elem.enabled and not elem.content_description: continue
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

    def __init__(self, device_id: str = "default_device"):
        self.device_id   = device_id
        self.backend_url = "http://localhost:8000"

        from groq import AsyncGroq
        self.llm_client = AsyncGroq(api_key="")
        self.model = "llama-3.3-70b-versatile"

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
        logger.info(f"✅ MobileReActStrategy ready | device={device_id} | memory={self.task_memory.stats()}")

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
        app          = _resolve_app(task.extra_params)   # FIX 1: session fallback
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
        ui_tree = await self._fetch_ui_tree_with_retries("wait", max_attempts=2, retry_delay=1.5)
        if not ui_tree:
            return self._build_error_result(task.task_id, "Failed to get initial UI tree")

        self.current_ui_tree       = ui_tree
        self.previous_ui_trees.append(ui_tree)
        self.device_state          = self._detect_device_state(ui_tree)
        self._prev_device_state    = self.device_state
        self._update_ui_stats(ui_tree)
        self._initial_ui_signature = (len(ui_tree.elements), ui_tree.screen_name or "")
        current_sig                = build_screen_signature(ui_tree)

        # FIX 1: refine app using live screen if coordinator didn't supply one
        app = _resolve_app(task.extra_params, ui_tree.app_package or "")
        logger.info(f"✅ Initial screen: {ui_tree.screen_name or ui_tree.app_name} "
                    f"({len(ui_tree.elements)} elements) | state={self.device_state} | app={app}")

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

        # ── ReAct Loop ────────────────────────────────────────────────────
        for step in range(task.max_steps):
            logger.info(f"\n{'='*70}\n📍 STEP {step+1}/{task.max_steps}\n{'='*70}")

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > task.timeout_seconds:
                self._penalize_last_t2_hint("timeout")
                return self._build_result(task.task_id, "timeout",
                    step, actions_executed, elapsed, error=f"Timeout after {task.timeout_seconds}s")

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

            t1 = self._tier1(task.ai_prompt, self.current_ui_tree, self.device_state)
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
                return self._build_result(task.task_id, "success", step+1,
                    actions_executed, elapsed, completion_reason=action_json.get("reason","Task completed"))

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
                    is_chrome = "chrome" in ((self.current_ui_tree.app_package or "").lower() if self.current_ui_tree else "")
                    if is_chrome:
                        logger.info("[T1] Chrome omnibox: skipping verification (known accessibility gap)")
                        del self.typed_texts[eid]
                    else:
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

                refreshed_app = _resolve_app(task.extra_params, new_ui.app_package or "")
                if refreshed_app != app:
                    logger.info(f"[CACHE] app updated from live screen: {app} → {refreshed_app}")
                    app = refreshed_app

                new_state = self._detect_device_state(new_ui)
                if new_state != self.device_state:
                    logger.info(f"🔄 State: {self.device_state} → {new_state}")
                    prev_state_snapshot     = self.device_state
                    self._prev_device_state = self.device_state
                    self.device_state       = new_state
                    self.stuck_counter      = 0
                    tier3_hint              = ""

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
                if action_json.get("action_type") == "click":
                    is_email_goal = (
                        "send" in task.ai_prompt.lower()
                        or "email" in (overall_goal or "").lower()
                    )
                    in_gmail_now = (
                        "gm" in (new_ui.app_package or "").lower()
                        or "gmail" in (new_ui.app_name or "").lower()
                    )
                    if is_email_goal and in_gmail_now and prev_ui_tree is not None:
                        was_composing = any(
                            "compose" in (e.content_description or "").lower()
                            or "send" in (e.content_description or "").lower()
                            for e in prev_ui_tree.elements
                        )
                        now_in_inbox = (
                            len(new_ui.elements) > 25 and
                            any("compose" in (e.content_description or "").lower() for e in new_ui.elements) and
                            any(
                                "inbox" in (e.text or "").lower()
                                or "unread" in (e.text or "").lower()
                                for e in new_ui.elements
                            )
                        )
                        if was_composing and now_in_inbox:
                            logger.info("✅ Email sent — returned to inbox")
                            elapsed = asyncio.get_event_loop().time() - start_time
                            self._store_learned_steps(task.ai_prompt, overall_goal, app, actions_executed)
                            return self._build_result(
                                task.task_id,
                                "success",
                                step + 1,
                                actions_executed,
                                elapsed,
                                completion_reason="Email sent — returned to inbox",
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
                        and len(new_ui.elements) >= 5):
                    # During launcher navigation (home/app drawer), app verify is expected to fail.
                    # Skip verification until we're no longer in those transient states.
                    if new_state in ("home_screen", "app_drawer"):
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
                    is_chrome = "chrome" in (new_ui.app_package or "").lower()
                    if is_chrome:
                        logger.info("[T1] Chrome omnibox: skipping post-type verification (known accessibility gap)")
                        self.stuck_counter = 0
                    elif self._typed_value_applied(action_json, new_ui):
                        live_val = (self._get_live_field_value(int(eid)) or "") if eid is not None else ""
                        logger.info(f"[T3] Type verified: field {eid} shows '{live_val[:30]}'")
                        self.stuck_counter = 0
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
                                    refreshed_app = _resolve_app(task.extra_params, retry_ui.app_package or "")
                                    if refreshed_app != app:
                                        logger.info(
                                            f"[CACHE] app updated from live screen: {app} → {refreshed_app}"
                                        )
                                        app = refreshed_app
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
        if hint_is_nav and not is_nav_step:
            logger.info(
                f"[CACHE] Hint rejected: navigation hint for non-navigation step "
                f"(hint='{(top_recipe.step_instruction or '')[:40]}')"
            )
            return RetrievalResult(band="none", recipes=[], best_sim=0.0, best_label="", hint_text="")
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

    def _tier1(self, goal: str, ui_tree: SemanticUITree, device_state: str) -> Optional[Dict[str, Any]]:
        if not ui_tree or not ui_tree.elements:
            return None

        # Chrome submit: if previous step typed in Chrome, submit immediately.
        pkg = (ui_tree.app_package or "").lower()
        if "chrome" in pkg and self.action_history:
            last = self.action_history[-1]
            if (last.get("action") or {}).get("action_type") == "type":
                for e in ui_tree.elements:
                    blob = f"{e.content_description or ''} {e.text or ''}".lower()
                    if any(kw in blob for kw in ("go", "search", "submit")) and e.clickable:
                        logger.info(f"[T1] Chrome post-type: click IME submit id={e.element_id}")
                        return {
                            "thought": "submit Chrome search via keyboard Go button",
                            "action_type": "click",
                            "element_id": e.element_id,
                        }
                logger.info("[T1] Chrome post-type: coordinate tap on keyboard Go key")
                return {
                    "thought": "submit Chrome search via keyboard Go key (coordinate)",
                    "action_type": "coordinate_tap",
                    "x_percent": 95,
                    "y_percent": 93,
                    "duration": 100,
                }

        # Safety: AURA exit — HOME not BACK (BACK only navigates within Flutter)
        if "aura" in (ui_tree.app_name or "").lower():
            logger.info("[T1] AURA detected → HOME")
            return {"thought": "exit AURA app", "action_type": "global_action", "global_action": "HOME"}

        # Safety: hard stuck recovery
        if self.stuck_counter >= 8:
            if self._is_in_time_picker(ui_tree):
                logger.info(f"[T1] Stuck {self.stuck_counter} — inside time picker, suppressing HOME recovery")
            else:
                logger.info(f"[T1] Stuck {self.stuck_counter} steps → HOME")
                return {"thought": "stuck recovery — HOME", "action_type": "global_action", "global_action": "HOME"}

        # Global interstitial
        popup = self._global_interstitial_handler(ui_tree)
        if popup: return popup

        # State verification
        state_check = self._state_verification(goal, ui_tree)
        if state_check: return state_check

        # Android system time picker
        if self._is_in_time_picker(ui_tree):
            logger.info(f"[T1] Time picker detected")
            return self._handle_time_picker(goal, ui_tree)
        else:
            # Log why it didn't trigger — for debugging
            has_ampm = any(e.type == "button" and (e.text or "").upper() in ("AM","PM") for e in ui_tree.elements)
            has_ok   = any(e.type == "button" and (e.text or "").upper() in ("OK","CANCEL") for e in ui_tree.elements)
            has_time = any("o'clock" in (e.content_description or "").lower() or "minute" in (e.content_description or "").lower() for e in ui_tree.elements)
            if has_ampm or has_ok:
                logger.info(f"[T1] Time picker NOT detected: ampm={has_ampm} ok={has_ok} time={has_time}")

        # Android alarm list
        if self._is_alarm_list_screen(ui_tree):
            alarm = self._handle_alarm_list(goal, ui_tree)
            if alarm: return alarm
        else:
            self._add_alarm_clicked = False
            self._add_alarm_screen_sig = ""

        # Play Store navigation guardrails
        play_store_action = self._handle_play_store_navigation(goal, ui_tree)
        if play_store_action:
            return play_store_action

        # Chrome search submission (IME/search suggestion fallback)
        chrome_submit = self._handle_chrome_search_submit(goal, ui_tree)
        if chrome_submit:
            return chrome_submit

        # Deterministic compose-field typing for email tasks (To/Subject/Body)
        compose = self._handle_compose_field_typing(goal, ui_tree)
        if compose:
            return compose

        # Already on target app — only valid if we're actually inside the app
        goal_lower = goal.lower()
        if any(kw in goal_lower for kw in ("open", "launch", "start", "navigate")):
            target = self._extract_target_app(goal)
            if target and not self._target_is_generic_page(target):
                in_target = self._in_target_app(target, device_state)
                logger.info(
                    f"[T1] Open-app guard | target='{target}' | state='{device_state}' | in_target={in_target}"
                )
                if in_target:
                    logger.info(f"[T1] '{target}' already open → complete")
                    return {"thought": f"'{target}' is already open", "action_type": "complete"}

        # App drawer — only trigger for explicit navigation goals
        if device_state == "home_screen":
            goal_is_nav = any(kw in goal.lower() for kw in ("open", "launch", "start", "navigate to"))
            target = self._extract_target_app(goal) if goal_is_nav else None
            if target and not self._target_is_generic_page(target):
                app_visible = any(
                    target.lower() in (e.text or "").lower()
                    for e in ui_tree.elements if e.clickable
                )
                if not app_visible:
                    if self._app_drawer_phase == 0:
                        self._app_drawer_phase = 1
                        logger.info(f"[T1] '{target}' not visible → HOME (phase 1)")
                        return {"thought": f"go to launcher root to find {target}",
                                "action_type": "global_action", "global_action": "HOME"}
                    elif self._app_drawer_phase == 1:
                        self._app_drawer_phase = 2
                        logger.info("[T1] App drawer phase 2 — fling from bottom dock")
                        return {
                            "thought": "fling up from bottom dock to open app drawer",
                            "action_type": "swipe",
                            "start_x_percent": 50,
                            "start_y_percent": 92,
                            "end_x_percent": 50,
                            "end_y_percent": 15,
                            "duration": 300,
                        }
                    elif self._app_drawer_phase == 2:
                        self._app_drawer_phase = 3
                        logger.info("[T1] App drawer phase 3 — second fling attempt")
                        return {
                            "thought": "second fling up to fully open app drawer",
                            "action_type": "swipe",
                            "start_x_percent": 50,
                            "start_y_percent": 88,
                            "end_x_percent": 50,
                            "end_y_percent": 10,
                            "duration": 250,
                        }
                    elif self._app_drawer_phase == 3:
                        drawer_elem = next(
                            (
                                e for e in ui_tree.elements
                                if any(
                                    kw in (e.content_description or "").lower()
                                    for kw in ("all apps", "app drawer", "apps")
                                )
                            ),
                            None,
                        )
                        if drawer_elem:
                            self._app_drawer_phase = 4
                            logger.info(f"[T1] App drawer phase 4 — tap drawer button id={drawer_elem.element_id}")
                            return {
                                "thought": "tap All Apps button",
                                "action_type": "click",
                                "element_id": drawer_elem.element_id,
                            }
                        self._app_drawer_phase = 4
                        logger.info("[T1] App drawer phase 4 — HOME reset, hand to T3")
                        return {
                            "thought": "reset to home, T3 will find app",
                            "action_type": "global_action",
                            "global_action": "HOME",
                        }
        return None

    def _global_interstitial_handler(self, ui_tree: SemanticUITree) -> Optional[Dict]:
        # Never treat Chrome web content as a dialog/overlay.
        pkg = (ui_tree.app_package or "").lower()
        if "chrome" in pkg and len(ui_tree.elements) > 15:
            return None

        DISMISS_VOCAB = {
            "got it", "ok", "okay", "dismiss", "done", "accept", "allow",
            "continue", "agree", "understood", "close", "skip", "later",
            "not now", "no thanks", "deny", "x",
        }
        IGNORE_VOCAB = {
            "continue to app", "continue shopping", "continue browsing",
            "no thanks, just browsing",
        }

        dismiss_elements = []
        for e in ui_tree.elements:
            if not (e.clickable or e.focusable):
                continue
            blob = ((e.content_description or "") + " " + (e.text or "")).lower().strip()
            if any(kw in blob for kw in DISMISS_VOCAB):
                if not any(ig in blob for ig in IGNORE_VOCAB):
                    dismiss_elements.append(e)

        if not dismiss_elements:
            return None

        total = len(ui_tree.elements)
        n_cta = len(dismiss_elements)

        if total <= 8:
            elem = dismiss_elements[0]
            label = (elem.content_description or elem.text or "dismiss")[:30]
            logger.info(f"[T1] Interstitial (sparse) '{label}' → elem {elem.element_id}")
            return {"thought": f"dismiss overlay ('{label}')",
                    "action_type": "click", "element_id": elem.element_id}

        has_search_bar = any(
            "search" in (e.hint_text or "").lower() or
            "search" in (e.content_description or "").lower()
            for e in ui_tree.elements if e.type == "textfield"
        )
        has_nav_tabs = any(
            e.type in ("tab", "tabwidget") or
            "selected" in (e.content_description or "").lower()
            for e in ui_tree.elements
        )

        if n_cta >= 2 and not has_search_bar and not has_nav_tabs:
            positive_vocab = {"continue", "accept", "allow", "ok", "yes", "got it", "agree"}
            positive = next(
                (e for e in dismiss_elements
                 if any(kw in ((e.content_description or "") + (e.text or "")).lower()
                        for kw in positive_vocab)),
                dismiss_elements[0]
            )
            label = (positive.content_description or positive.text or "continue")[:30]
            logger.info(
                f"[T1] Modal dialog detected ({n_cta} CTAs, no search bar) → "
                f"click '{label}' id={positive.element_id}"
            )
            return {
                "thought": f"dismiss modal dialog ('{label}')",
                "action_type": "click",
                "element_id": positive.element_id,
            }

        n_buttons = sum(1 for e in ui_tree.elements
                        if e.type in ("button", "imagebutton") and e.clickable)
        if n_buttons <= 4 and n_cta >= 1:
            elem = dismiss_elements[0]
            label = (elem.content_description or elem.text or "dismiss")[:30]
            logger.info(f"[T1] Interstitial (few buttons) '{label}' → elem {elem.element_id}")
            return {"thought": f"dismiss overlay ('{label}')",
                    "action_type": "click", "element_id": elem.element_id}

        return None

    def _state_verification(self, goal: str, ui_tree: SemanticUITree) -> Optional[Dict]:
        gl = goal.lower()
        if any(kw in gl for kw in ("install", "download app", "get app")):
            for e in ui_tree.elements:
                if (e.text or "").strip().lower() in ("open", "uninstall") and e.clickable:
                    logger.info(f"[T1] State verify: goal=install but button='{e.text}' → done")
                    return {"thought": f"already installed ('{e.text}')",
                            "action_type": "complete", "reason": "App already installed"}
        if any(kw in gl for kw in ("enable", "turn on", "activate")):
            for e in ui_tree.elements:
                if e.type == "switch" and ("enabled" in (e.content_description or "").lower()
                                           or "on" in (e.content_description or "").lower()):
                    logger.info("[T1] State verify: switch already enabled → done")
                    return {"thought": "switch already enabled",
                            "action_type": "complete", "reason": "Already enabled"}
        return None

    def _handle_time_picker(self, goal: str, ui_tree: SemanticUITree) -> Optional[Dict]:
        # Switch to text input mode first if not done
        for elem in ui_tree.elements:
            desc = (elem.content_description or "").lower()
            if ("text input" in desc or "keyboard" in desc) and not self._switched_to_text_input:
                logger.info("[T1] Time picker → switch to text input")
                self._switched_to_text_input = True
                return {"thought": "switch to text input mode",
                        "action_type": "click", "element_id": elem.element_id}

        time_match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", goal.lower())
        if not time_match:
            return None

        target_h, target_m, target_per = int(time_match.group(1)), time_match.group(2), time_match.group(3).upper()

        hour_fid = minute_fid = ok_btn = None
        displayed_h = displayed_m = displayed_p = None

        for elem in ui_tree.elements:
            text = (elem.text or "").strip()
            desc = (elem.content_description or "").lower()
            if elem.type == "button" and text.upper() == "OK":
                ok_btn = elem.element_id
            if elem.type == "button" and text.upper() in ("AM", "PM"):
                displayed_p = text.upper()
            if elem.type == "textfield":
                if "hour" in desc:
                    hour_fid = elem.element_id
                    if text.isdigit():
                        try: displayed_h = int(text)
                        except ValueError: pass
                elif "minute" in desc or "min" in desc:
                    minute_fid = elem.element_id
                    if text.isdigit():
                        displayed_m = text
            if "o'clock" in desc:
                try: displayed_h = int(text)
                except ValueError: pass
            if "minutes" in desc and elem.type != "textfield":
                displayed_m = text

        # Fallback ordering (independent for hour/minute)
        tfs = [e for e in ui_tree.elements if e.type == "textfield"]
        if hour_fid is None and len(tfs) >= 1:
            hour_fid = tfs[0].element_id
            if (tfs[0].text or "").strip().isdigit():
                try: displayed_h = int(tfs[0].text.strip())
                except ValueError: pass
        if minute_fid is None and len(tfs) >= 2:
            minute_fid = tfs[1].element_id
            displayed_m = (tfs[1].text or "").strip()

        logger.debug(
            f"[T1] Time picker fields: hour_fid={hour_fid} (val={displayed_h}) "
            f"minute_fid={minute_fid} (val={displayed_m}) period={displayed_p} ok={ok_btn}"
        )

        h2 = str(target_h).zfill(2)
        hour_ok   = (displayed_h == target_h)
        minute_ok = (displayed_m == target_m)
        period_ok = (displayed_p == target_per)

        # Deterministic typing for hour/minute before handing to LLM
        if hour_fid is not None and not hour_ok:
            logger.info(f"[T1] Time picker: set hour to {h2}")
            return {
                "thought": f"set hour to {h2}",
                "action_type": "type",
                "element_id": hour_fid,
                "text": h2,
                "clear_first": True,
            }

        if minute_fid is not None and not minute_ok:
            logger.info(f"[T1] Time picker: set minute to {target_m}")
            return {
                "thought": f"set minute to {target_m}",
                "action_type": "type",
                "element_id": minute_fid,
                "text": target_m,
                "clear_first": True,
            }

        if ok_btn and hour_ok and minute_ok:
            if period_ok:
                # All correct — click OK
                logger.info(f"[T1] Time picker: {target_h}:{target_m} {target_per} ✓ → OK")
                self._time_picker_pm_attempts = 0
                return {"thought": "time correct — click OK",
                        "action_type": "click", "element_id": ok_btn}

            # FIX 6: if we've clicked PM twice, assume it worked and click OK anyway
            if self._time_picker_pm_attempts >= 2:
                logger.info(f"[T1] PM clicked {self._time_picker_pm_attempts}× — assuming toggled, clicking OK")
                self._time_picker_pm_attempts = 0
                return {"thought": "AM/PM assumed toggled — click OK",
                        "action_type": "click", "element_id": ok_btn}

            # Click the target period button
            pm_elem = next(
                (e for e in ui_tree.elements
                 if e.type == "button" and (e.text or "").upper() == target_per),
                None,
            )
            if pm_elem:
                self._time_picker_pm_attempts += 1
                logger.info(f"[T1] Click {target_per} (attempt {self._time_picker_pm_attempts})")
                return {"thought": f"click {target_per}",
                        "action_type": "click", "element_id": pm_elem.element_id}

        if hour_fid is not None or minute_fid is not None:
            logger.info("[T1] Time picker: waiting for UI to settle (fields found but state unclear)")
            return {"thought": "time picker active — wait for UI",
                    "action_type": "wait", "duration": 500}

        return None

    def _handle_play_store_navigation(
        self, goal: str, ui_tree: SemanticUITree
    ) -> Optional[Dict[str, Any]]:
        """Deterministic guardrails for Play Store navigation/search."""
        pkg = (ui_tree.app_package or "").lower()
        if "vending" not in pkg and "play" not in (ui_tree.app_name or "").lower():
            return None

        gl = goal.lower()
        elements = ui_tree.elements

        on_app_page = any(
            (e.text or "").strip().lower() in ("install", "open", "update", "uninstall")
            and e.clickable
            for e in elements
        )
        looking_for_search = any(kw in gl for kw in ("search", "find", "type", "look"))

        if on_app_page and looking_for_search:
            target_app = re.search(r"(?:for|search for|find|download)\s+(\w+)", gl)
            target_name = target_app.group(1).lower() if target_app else ""
            app_title_visible = any(
                target_name in (e.text or "").lower()
                for e in elements if e.type in ("text", "textview") and len(e.text or "") > 2
            )
            if not app_title_visible and target_name:
                logger.info(f"[T1] Play Store: wrong app page (looking for '{target_name}') → BACK")
                return {"thought": f"wrong app page — go back to search for {target_name}",
                        "action_type": "global_action", "global_action": "BACK"}

        bad_fields = {"ask ai", "ask about", "write a review", "add a review",
                      "your review", "share your thoughts"}
        for e in elements:
            blob = ((e.content_description or "") + " " + (e.hint_text or "") +
                    " " + (e.text or "")).lower()
            if any(bad in blob for bad in bad_fields) and e.focusable:
                if e.element_id == self.last_clicked_element:
                    logger.info("[T1] Play Store: 'Ask AI/review' field detected — BACK")
                    return {"thought": "wrong field (Ask AI) — go back",
                            "action_type": "global_action", "global_action": "BACK"}

        if any(kw in gl for kw in ("search", "type", "find", "look")):
            for e in elements:
                hint = (e.hint_text or "").lower()
                desc = (e.content_description or "").lower()
                if any(kw in hint or kw in desc for kw in
                       ("search for apps", "search apps", "search games",
                        "search the store", "search play")):
                    logger.info(f"[T1] Play Store: found search bar id={e.element_id}")
                    return {"thought": "click Play Store search bar",
                            "action_type": "click", "element_id": e.element_id}

            for e in elements:
                blob = ((e.content_description or "") + " " + (e.text or "")).lower()
                if blob.strip() == "search" and e.clickable and e.type in (
                    "button", "imagebutton", "imageview"
                ):
                    logger.info(f"[T1] Play Store: found search icon id={e.element_id}")
                    return {"thought": "click Play Store search icon",
                            "action_type": "click", "element_id": e.element_id}

        return None

    def _handle_compose_field_typing(self, goal: str, ui_tree: SemanticUITree) -> Optional[Dict[str, Any]]:
        """Deterministic typing for Gmail compose fields to avoid click loops."""
        gl = goal.lower().strip()

        field_kind: Optional[str] = None
        text_value: Optional[str] = None

        m_to = re.search(r"fill\s+the\s+to\s+field\s+with\s+(.+)$", gl)
        m_subject = re.search(r"fill\s+the\s+subject\s+field\s+with\s+(.+)$", gl)
        m_body = re.search(r"fill\s+the\s+email\s+body\s+with\s+(.+)$", gl)

        if m_to:
            field_kind = "to"
            text_value = m_to.group(1).strip().strip("'\"")
        elif m_subject:
            field_kind = "subject"
            text_value = m_subject.group(1).strip().strip("'\"")
        elif m_body:
            field_kind = "body"
            text_value = m_body.group(1).strip().strip("'\"")

        if not field_kind or not text_value:
            return None

        def _norm(s: str) -> str:
            return (s or "").strip().lower()

        # Find target field by semantics in text/hint/content-desc/resource-id.
        target = None
        for e in ui_tree.elements:
            if e.type not in ("textfield", "edittext") and not (e.focusable or e.clickable):
                continue

            blob = " ".join([
                e.text or "",
                e.hint_text or "",
                e.content_description or "",
                e.resource_id or "",
            ]).lower()

            if field_kind == "to" and any(k in blob for k in ("to", "recipient", "address")):
                target = e
                break
            if field_kind == "subject" and "subject" in blob:
                target = e
                break
            if field_kind == "body" and any(k in blob for k in ("compose email", "message body", "body", "message")):
                target = e
                break

        if target is None:
            return None

        existing = _norm(target.text or "")
        want = _norm(text_value)
        if want and want in existing:
            logger.info(f"[T1] Compose {field_kind} already contains target text")
            return {"thought": f"{field_kind} already filled", "action_type": "complete"}

        logger.info(f"[T1] Compose: type {field_kind} field")
        return {
            "thought": f"type {field_kind} value",
            "action_type": "type",
            "element_id": target.element_id,
            "text": text_value,
        }

    def _handle_alarm_list(self, goal: str, ui_tree: SemanticUITree) -> Optional[Dict]:
        tm = re.search(r"(\d{1,2}:\d{2}\s*(?:am|pm)?)", goal.lower())
        if not tm: return None
        target_t = tm.group(1).strip().lower()
        elements = ui_tree.elements
        for i, elem in enumerate(elements):
            cd = (elem.content_description or "").lower()
            tx = (elem.text or "").lower()
            if target_t not in cd and target_t not in tx: continue
            if "currently enabled" in cd or "alarm is on" in cd:
                logger.info(f"[T1] Alarm {target_t} enabled → complete")
                return {"thought": f"alarm {target_t} already enabled", "action_type": "complete"}
            if "currently disabled" in cd or "alarm is off" in cd:
                for j in range(i+1, min(i+4, len(elements))):
                    if elements[j].type == "switch":
                        return {"thought": f"toggle alarm {target_t}",
                                "action_type": "click", "element_id": elements[j].element_id}
        for elem in ui_tree.elements:
            if "add alarm" in (elem.content_description or "").lower():
                sig = build_screen_signature(ui_tree)
                if self._add_alarm_clicked and self._add_alarm_screen_sig == sig:
                    logger.info("[T1] Add alarm already clicked on same screen — avoid loop")
                    return None
                self._add_alarm_clicked = True
                self._add_alarm_screen_sig = sig
                return {"thought": "add new alarm", "action_type": "click", "element_id": elem.element_id}
        return None

    def _handle_chrome_search_submit(
        self, goal: str, ui_tree: SemanticUITree
    ) -> Optional[Dict[str, Any]]:
        """
        Handles Chrome address bar submission when no explicit search button exists.
        Priority:
          1) IME action button (search/go/submit)
          2) first autocomplete suggestion
          3) complete if results page already loaded
        """
        pkg = (ui_tree.app_package or "").lower()
        name = (ui_tree.app_name or "").lower()
        if "chrome" not in pkg and "chrome" not in name:
            return None

        gl = goal.lower()
        # Guard: this is a submission helper, so only run after we've typed in Chrome recently.
        # Exception: explicit navigate/url tasks may type URL here directly.
        has_typed_in_chrome = any(
            (h.get("action") or {}).get("action_type") == "type"
            and "chrome" in ((h.get("device_state") or "").lower().replace("_", ""))
            for h in self.action_history[-5:]
        ) or bool(self.typed_texts)

        is_search_task = any(
            kw in gl for kw in (
                "search", "find", "navigate", "type", "fill",
                "click search", "click the search", "submit", "address bar", "url", "google",
            )
        )
        is_navigate = "navigate to" in gl or "open url" in gl
        if not has_typed_in_chrome and not is_navigate:
            logger.debug("[T1] Chrome submit handler skipped — no prior type in Chrome")
            return None
        if not is_search_task and not is_navigate:
            return None

        # 0) Navigation tasks must use URL from task metadata, not the goal text.
        if is_navigate and self.current_task:
            web_params = ((self.current_task.extra_params or {}).get("web_params") or {})
            if not isinstance(web_params, dict):
                web_params = {}
            url = (web_params.get("url") or "").strip()
            if url:
                for e in ui_tree.elements:
                    if e.type != "textfield":
                        continue
                    txt = (e.text or "").strip().lower()
                    if txt == "" or "search or type" in txt or "search or type url" in txt:
                        if e.focusable or e.clickable:
                            logger.info(f"[T1] Chrome: navigate to URL '{url[:40]}' id={e.element_id}")
                            return {
                                "thought": "type URL into Chrome address bar",
                                "action_type": "type",
                                "element_id": e.element_id,
                                "text": url,
                                "clear_first": True,
                            }

        # 1) Search tasks: extract the actual search query, but reject page descriptions.
        if is_search_task:
            query_match = re.search(
                r"(?:type|search for|find)\s+['\"]?(.+?)['\"]?\s*(?:in|into|on)?\s*(?:the\s+)?(?:search\s+bar|address\s+bar|chrome)?\s*$",
                goal, re.IGNORECASE
            )
            if query_match:
                query_text = query_match.group(1).strip().strip("'\"")
                if any(w in query_text.lower() for w in ("page", "screen", "bar", "button", "icon")):
                    query_text = None
                if query_text:
                    for e in ui_tree.elements:
                        if e.type != "textfield":
                            continue
                        txt = (e.text or "").strip().lower()
                        if txt == "" or "search or type" in txt or "search or type url" in txt:
                            if e.focusable or e.clickable:
                                logger.info(f"[T1] Chrome: type query '{query_text[:30]}' id={e.element_id}")
                                return {
                                    "thought": "type search query into Chrome address bar",
                                    "action_type": "type",
                                    "element_id": e.element_id,
                                    "text": query_text,
                                    "clear_first": True,
                                }

        # 2) IME action button (keyboard Go/Search)
        for e in ui_tree.elements:
            if not e.clickable:
                continue
            blob = f"{e.content_description or ''} {e.text or ''}".lower()
            if any(kw in blob for kw in ("search", "go", "submit", "done")) and e.type in (
                "button", "imagebutton", "imageview"
            ):
                logger.info(f"[T1] Chrome: IME submit button id={e.element_id}")
                return {
                    "thought": "click Chrome keyboard submit button",
                    "action_type": "click",
                    "element_id": e.element_id,
                }

        # 3) First autocomplete suggestion (any clickable non-nav row)
        nav_words = {"back", "tab", "tabs", "menu", "settings", "bookmark",
                     "bookmarks", "more", "close", "new", "reload", "history"}
        skip_descs = {
            "ask ai", "search with", "performance", "measure",
            "voice search", "voice", "start voice", "microphone",
        }
        for e in ui_tree.elements:
            if not e.clickable:
                continue
            blob = f"{e.content_description or ''} {e.text or ''}".lower().strip()
            if len(blob) < 3:
                continue
            if any(w in blob.split() for w in nav_words):
                continue
            if any(s in blob for s in skip_descs):
                continue
            if e.type not in ("textfield", "imagebutton") and blob:
                logger.info(f"[T1] Chrome: click first suggestion id={e.element_id}: '{blob[:40]}'")
                return {
                    "thought": f"click Chrome search suggestion: '{blob[:30]}'",
                    "action_type": "click",
                    "element_id": e.element_id,
                }

        # 4) Results already loaded
        if len(ui_tree.elements) > 30:
            for e in ui_tree.elements:
                if e.type == "textfield" and (e.text or "").strip():
                    bar_text = (e.text or "").strip().lower()
                    if not bar_text.startswith("search or type"):
                        logger.info("[T1] Chrome: address bar has content → results loaded → complete")
                        return {
                            "thought": "Chrome results loaded — complete",
                            "action_type": "complete",
                            "reason": "Search results loaded",
                        }

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  TIER 3 — LLM REACT
    # ══════════════════════════════════════════════════════════════════════

    async def _llm_react(
        self, goal, overall_goal, pruned_tree, thought_history,
        step_number, hint_context, handoff_context, extra_params, app,
    ) -> Tuple[str, Optional[Dict]]:

        history_ctx  = ("Prior thoughts: " + " → ".join(thought_history[-3:])) if thought_history else ""
        param_ctx    = self._format_extra_params(extra_params)
        compose_ctx  = self._compose_screen_context(goal)
        alarm_ctx    = self._alarm_list_context(goal)
        time_ctx     = self._time_picker_context(goal)
        blacklist_str = (f"⛔ Do NOT click element IDs: {sorted(list(self.failed_elements))}. "
                         if self.failed_elements else "")

        valid_action_types = (
            "click", "type", "scroll", "swipe",
            "global_action", "coordinate_tap", "wait", "complete",
        )
        system_prompt = f"""You are an Android UI automation agent operating in a ReAct loop.

RESPONSE FORMAT (every time):
Thought: <one short sentence explaining what you see and plan>
Action: {{"action_type": "...", ...}}

    VALID action_type values (use only these exact strings):
    - {", ".join(valid_action_types)}

    Rules:
    - To go back, use {{"action_type": "global_action", "global_action": "BACK"}}.
    - Never use invalid action types like navigate_back / press_back / go_back / open_app.
    - To open an app, click its icon in the UI tree. Never use browser/search for app launching.
    - Never invent element IDs; only use IDs visible in CURRENT SCREEN.
    - For type actions, set "clear_first": true.
    - Handle popups by clicking "Allow", "Got it", "Skip", or "OK".
    - Declare complete only with visible on-screen evidence.

CHROME-SPECIFIC:
- After typing in the Chrome address bar, DO NOT keep clicking the address bar.
- Instead, look for an autocomplete suggestion and click it.
- If search results are already showing (many elements visible), declare complete.
- NEVER declare the task failed just because you cannot see a 'Search' button.

PLAY STORE-SPECIFIC:
- The search bar is at the TOP of the Play Store home screen (not bottom tabs).
- Bottom tabs are navigation tabs; do not use them to start a search.
- To search for an app, click the top search bar/icon.
- Never type into "Ask AI about this app" or review fields.
- If you land on a wrong app page, press BACK before searching again.

Example:
Thought: The screen shows a "Compose" button. I will click it to start a new email.
Action: {{"action_type": "click", "element_id": 42}}"""

        user_prompt = (
            f"OVERALL GOAL: {overall_goal}\n"
            f"CURRENT STEP: {goal}\n"
            f"Step {step_number} | Device state: {self.device_state}\n"
            + (time_ctx or "") + (alarm_ctx or "") + (compose_ctx or "")
            + (f"\n{param_ctx}"       if param_ctx       else "")
            + (f"\n{hint_context}"    if hint_context     else "")
            + (f"\n{handoff_context}" if handoff_context  else "")
            + f"\n{blacklist_str}\n"
            + (
                f"\n⚠️ THIS STEP REQUIRES APP: {app.upper()}\n"
                f"You are currently on: {self.device_state}\n"
                f"DO NOT perform this step here. Navigate to {app} first.\n"
                if app and app != "unknown" and not self._in_target_app(app, self.device_state)
                else ""
            )
            + f"\nCURRENT SCREEN:\n{pruned_tree}\n"
            + (f"\n{history_ctx}" if history_ctx else "")
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
                response = await self.llm_client.chat.completions.create(
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

    def _time_picker_context(self, goal: str) -> str:
        if not self.current_ui_tree or not self._is_in_time_picker(self.current_ui_tree):
            return ""
        tm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", goal.lower())
        if not tm:
            return ""

        target_h = int(tm.group(1))
        target_m = tm.group(2)
        target_p = tm.group(3).upper()
        h2 = str(target_h).zfill(2)

        # Read live values from the actual tree
        hour_fid = hour_val = minute_fid = minute_val = None
        ampm_id = ampm_cur = None
        ok_id = None

        for e in self.current_ui_tree.elements:
            text = (e.text or "").strip()
            desc = (e.content_description or "").lower()
            if e.type == "button" and text.upper() == "OK":
                ok_id = e.element_id
            if e.type == "button" and text.upper() in ("AM", "PM"):
                ampm_id = e.element_id
                ampm_cur = text.upper()
            if e.type == "textfield":
                if "hour" in desc:
                    hour_fid = e.element_id
                    hour_val = text
                elif "minute" in desc or "min" in desc:
                    minute_fid = e.element_id
                    minute_val = text

        # Fallback: first textfield = hour, second = minute
        if hour_fid is None:
            tfs = [e for e in self.current_ui_tree.elements if e.type == "textfield"]
            if len(tfs) >= 1:
                hour_fid = tfs[0].element_id
                hour_val = (tfs[0].text or "").strip()
            if len(tfs) >= 2:
                minute_fid = tfs[1].element_id
                minute_val = (tfs[1].text or "").strip()

        hour_ok   = (hour_val == h2)
        minute_ok = (minute_val == target_m)
        period_ok = (ampm_cur == target_p)

        status = lambda ok: "✅" if ok else "❌"

        lines = [
            f"\n⏰ TIME PICKER — READ THE ACTUAL VALUES BELOW, DO NOT ASSUME:",
            f"",
            f"  {status(hour_ok)}   HOUR:   field shows '{hour_val}'  →  needs '{h2}'   (elem {hour_fid})",
            f"  {status(minute_ok)} MINUTE: field shows '{minute_val}'  →  needs '{target_m}'   (elem {minute_fid})",
            f"  {status(period_ok)} PERIOD: currently '{ampm_cur}'     →  needs '{target_p}'    (elem {ampm_id})",
            f"",
            f"DO EXACTLY ONE ACTION PER STEP — in this priority order:",
            f"  1. ❌ HOUR wrong?   → type '{h2}' into elem {hour_fid}",
            f"  2. ❌ MINUTE wrong? → type '{target_m}' into elem {minute_fid}",
            f"  3. ❌ PERIOD wrong? → click '{target_p}' button (elem {ampm_id})",
            f"  4. ✅ ALL correct?  → click OK (elem {ok_id})",
            f"",
            f"⛔ NEVER click OK unless ALL THREE show ✅ above.",
            f"⛔ DO NOT guess — the ✅/❌ above show the truth.",
        ]
        return "\n".join(lines) + "\n"

    def _alarm_list_context(self, goal: str) -> str:
        if not self.current_ui_tree or not self._is_alarm_list_screen(self.current_ui_tree): return ""
        if not any(kw in goal.lower() for kw in ("alarm", "set alarm")): return ""
        tm = re.search(r"(\d{1,2}:\d{2}\s*(?:am|pm)?)", goal.lower())
        if not tm: return ""
        t = tm.group(1).strip().lower()
        for i, e in enumerate(self.current_ui_tree.elements):
            cd = (e.content_description or "").lower()
            if t not in cd and t not in (e.text or "").lower(): continue
            if "currently enabled" in cd: return f"\n⏰ ALARM: {t} enabled → complete.\n"
            if "currently disabled" in cd:
                for j in range(i+1, min(i+4, len(self.current_ui_tree.elements))):
                    if self.current_ui_tree.elements[j].type == "switch":
                        return (f"\n⏰ ALARM: {t} off → click SWITCH elem "
                                f"{self.current_ui_tree.elements[j].element_id}.\n")
        return f"\n⏰ ALARM: {t} not found → click 'Add alarm'.\n"

    def _compose_screen_context(self, goal: str) -> str:
        if not self.current_ui_tree: return ""
        elements = self.current_ui_tree.elements
        texts    = [(e.text or "").lower() for e in elements]
        has_to   = any(t in ("to", "to:") for t in texts)
        has_subj = any("subject" in t for t in texts)
        has_send = any("send" in (e.content_description or "").lower() for e in elements)
        if not ((has_to or has_subj) and has_send): return ""
        nav_up = next((e.element_id for e in elements
                       if "navigate up" in (e.content_description or "").lower()), None)
        ctx = ("\n📧 COMPOSE SCREEN:\n"
               "⛔ Already composing — do NOT look for compose button.\n"
               "⛔ Do NOT click Navigate Up — closes the email.\n"
               "✅ TYPE into To / Subject / Body fields.\n")
        if nav_up:
            ctx += f"⛔ Element {nav_up} = Navigate Up — never click.\n"
        return ctx

    def _format_extra_params(self, extra_params: Dict[str, Any]) -> str:
        parts = []
        for k, v in extra_params.items():
            if k in _INTERNAL_KEYS or v is None: continue
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

    # ══════════════════════════════════════════════════════════════════════
    #  APP VERIFICATION
    # ══════════════════════════════════════════════════════════════════════

    async def _verify_app_llm(self, target_app: str, ui_tree: SemanticUITree) -> Dict[str, Any]:
        app_name    = (ui_tree.app_name or "").lower()
        app_package = (ui_tree.app_package or "").lower()
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
            resp = await self.llm_client.chat.completions.create(
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
            resp = await self.llm_client.chat.completions.create(
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
        record_id = self.task_memory.store(
            step_instruction=step_instruction, overall_goal=overall_goal,
            app=app, action_type=atype, screen_signature=sig,
            selectors=selectors, demonstrated=0, success_count=1,
        )
        if record_id:
            logger.info(
                f"[CACHE] Stored Tier 3 success: '{step_instruction[:55]}' id={record_id[:8]}"
                + (" (nav task — no selectors)" if is_nav_task else "")
            )

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
        return (any(e.type == "switch" for e in ui_tree.elements) and
                any("add alarm" in (e.content_description or "").lower() for e in ui_tree.elements))

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
        if init_count > 0 and abs(cur_count - init_count) / init_count >= 0.30: return True
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
            r"(?:please\s+)?(?:open|launch|start|navigate\s+to)\s+(?:the\s+)?(\w+(?:\s+\w+)?)",
            goal, re.IGNORECASE,
        )
        if m:
            c = re.sub(r"\s+(login|home|main|app|page|screen|application|applications)$", "",
                       m.group(1).strip().lower()).strip()
            if c and not any(w in c.split() for w in _GENERIC_PAGE_WORDS):
                if c not in ("the", "a", "an", "my", "this", "that", "app", "it"):
                    return c
        return None

    def _target_is_generic_page(self, target: str) -> bool:
        return bool(set(target.lower().split()) & _GENERIC_PAGE_WORDS)

    def _in_target_app(self, app_name: str, device_state: str) -> bool:
        if not app_name or app_name == "unknown":
            return True
        normalized = _normalize_app_name(app_name)
        state = (device_state or "").lower()
        if not state.startswith("in_app_"):
            return False
        if normalized.lower() in state:
            return True

        pkg_to_app = {
            "com_android_vending": "play_store",
            "com_google_android_gm": "gmail",
            "com_google_android_deskclock": "clock",
            "com_google_android_calendar": "calendar",
            "com_google_android_contacts": "contacts",
            "com_android_chrome": "chrome",
            "com_google_android_apps_maps": "maps",
            "com_google_android_youtube": "youtube",
        }
        lower_app = normalized.lower()
        for pkg_fragment, canonical in pkg_to_app.items():
            if pkg_fragment in state and canonical == lower_app:
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
        self.token_usage = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls          = 0
        self.total_ui_elements_seen   = 0
        self.max_ui_elements_seen     = 0
        self.ui_samples_count         = 0
        self.tier_stats = {"tier1": 0, "tier2": 0, "tier3_llm": 0}
        self._initial_ui_signature    = None

    # ── Device I/O ─────────────────────────────────────────────────────────

    async def _fetch_ui_tree_from_device(self) -> Optional[SemanticUITree]:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{self.backend_url}/device/{self.device_id}/ui-tree", timeout=5.0)
                if r.status_code == 200:
                    data = r.json()
                    if not data or data.get("_synthetic"): return None
                    return SemanticUITree(**data)
        except Exception as e:
            logger.debug(f"UI tree fetch error: {e}")
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
                return None
            if len(ui.elements) >= min_elems: return ui
            if attempt < max_attempts - 1: await asyncio.sleep(retry_delay)
            else: return ui
        return None

    async def _execute_action_on_device(self, action: UIAction) -> ActionResult:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{self.backend_url}/device/{self.device_id}/execute-action",
                    json=action.model_dump(), timeout=10.0,
                )
                if r.status_code == 200: return ActionResult(**r.json())
                return ActionResult(action_id=action.action_id, success=False,
                                    error=f"HTTP {r.status_code}", execution_time_ms=0)
        except Exception as e:
            logger.error(f"Action execute error: {e}")
            return ActionResult(action_id=action.action_id, success=False,
                                error=str(e), execution_time_ms=0)

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
        strategy = MobileStrategy(device_id)
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