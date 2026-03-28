"""
mobile_strategy.py

MobileReActStrategy — 3-Tier ReAct loop for Android UI automation.

Tier 1  Deterministic handlers        (0 tokens · 0 ms)
Tier 2  ChromaDB semantic retrieval   (0 tokens · ~5 ms)
Tier 3  LLM ReAct loop                (~400 ms/step)

Fixes applied:
    FIX 1  Session app memory — last known app_name persists across tasks
            so task_2 inherits "clock" even if coordinator omits app_name.
    FIX 2  Outer timeout uses smart-adjusted value, not the raw coordinator value.
    FIX 3  App drawer — two-phase HOME then scroll; fires whenever target not
            visible on home screen, not only after 5 stuck steps.
    FIX 4  LLM system prompt — explicitly blocks Google/web search for apps;
            explains app drawer correctly.
    FIX 5  Cache hit/miss always logged at INFO with similarity + matched step
            + primary selector — enough to debug without opening ChromaDB.
    FIX 6  Time picker AM/PM — attempt counter; after 2 PM clicks proceed to OK
            regardless of whether accessibility tree reflects the toggle.
    FIX 7  Thought loop detection only fires for Tier 3 LLM decisions.

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
    return ""

def _resolve_app(extra_params: Dict[str, Any], live_package: str = "") -> str:
    """
    Determine app identifier for ChromaDB queries.
    Priority: coordinator extra_params → live package → session memory.
    """
    global _session_last_app

    explicit = (extra_params.get("app_name") or "").strip().lower()
    if explicit and explicit != "unknown":
        _session_last_app = explicit
        logger.info(f"[CACHE] app from coordinator: '{explicit}'")
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
    if any(k in g for k in ("alarm", "schedule", "set time")):  return max(60, default)
    if any(k in g for k in ("email", "compose", "recipient")):  return max(60, default)
    if any(k in g for k in ("search", "find", "look for")):     return max(45, default)
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
        self.llm_client = AsyncGroq(api_key="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")
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
            or (task.context or {}).get("goal")
            or (task.context or {}).get("overall_goal")
            or task.ai_prompt
        )
        logger.info(f"[CACHE] overall_goal resolved to: '{overall_goal[:60]}'")
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
        t2_result: RetrievalResult = self.task_memory.query(
            step_instruction  = task.ai_prompt,
            overall_goal      = overall_goal,
            app               = app,
            current_signature = "",
        )
        self._log_cache_result(t2_result, task.ai_prompt, app)   # FIX 5

        # Fallback: if step query misses but goal differs, query by goal to pull full sequence
        if t2_result.band == "none" and overall_goal != task.ai_prompt:
            t2_goal_query = self.task_memory.query(
                step_instruction  = overall_goal,
                overall_goal      = overall_goal,
                app               = app,
                current_signature = "",
                top_k             = 8,
            )
            if t2_goal_query.band in ("execute", "hint"):
                logger.info(
                    f"[CACHE] Goal-based fallback: band={t2_goal_query.band} "
                    f"(step query was MISS, goal query found {len(t2_goal_query.recipes)} steps)"
                )
                t2_result = t2_goal_query
                self._log_cache_result(t2_result, overall_goal, app)

        if t2_result.band == "execute":
            script_result = await self._execute_tier2_script(
                task, t2_result, overall_goal, app, actions_executed, start_time,
            )
            if script_result is not None:
                return script_result
            logger.info("[T2] Handing off to Tier 3")

        tier3_hint = ""
        if t2_result.band == "hint" and app and app != "unknown":
            in_right_app = (
                app in self.device_state.lower()
                or self.device_state.startswith(f"in_app_{app}")
            )
            if not in_right_app:
                logger.info(
                    f"[CACHE] Suppressing hint — not in '{app}' yet "
                    f"(state={self.device_state}). Will re-query on app entry."
                )
                tier3_hint = ""
            else:
                tier3_hint = t2_result.hint_text or ""
        elif t2_result.band == "hint":
            tier3_hint = t2_result.hint_text or ""

        # ── ReAct Loop ────────────────────────────────────────────────────
        for step in range(task.max_steps):
            logger.info(f"\n{'='*70}\n📍 STEP {step+1}/{task.max_steps}\n{'='*70}")

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > task.timeout_seconds:
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
                txt_typed = action_json.get("text", "")
                if eid in self.typed_texts and self.typed_texts[eid] == txt_typed:
                    live = self._get_live_field_value(eid)
                    if live and txt_typed.lower() in live.lower():
                        return self._build_result(task.task_id, "success", step+1,
                            actions_executed, asyncio.get_event_loop().time() - start_time,
                            completion_reason="Completed (text already typed in field)")
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
                    self._prev_device_state = self.device_state
                    prev_state_for_requery  = self.device_state
                    self.device_state       = new_state
                    self.stuck_counter      = 0
                    tier3_hint              = ""

                    # In-loop Tier 2 re-query when transitioning into an app
                    fresh_t2 = await self._requery_tier2_on_app_entry(
                        task, overall_goal, app, prev_state_for_requery
                    )
                    if fresh_t2 is not None:
                        if fresh_t2.band == "execute":
                            script_result = await self._execute_tier2_script(
                                task, fresh_t2, overall_goal, app,
                                actions_executed, start_time,
                            )
                            if script_result is not None:
                                return script_result
                            logger.info("[T2] In-loop script failed → continue T3")
                        elif fresh_t2.band == "hint":
                            tier3_hint = fresh_t2.hint_text or ""
                            logger.info(f"[CACHE] In-loop hint injected: {tier3_hint[:80]}")
                else:
                    self.stuck_counter += 1

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
                    if not self._typed_value_applied(action_json, new_ui):
                        eid = action_json.get("element_id")
                        logger.warning(
                            f"[TYPE] Value did not apply for element={eid}; refocusing field for retry"
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
        Re-run Tier 2 when transitioning into an app from launcher-like states.
        This enables dynamic retrieval after navigation from home/app-drawer.
        """
        just_entered_app = (
            prev_state in ("home_screen", "app_drawer", "in_aura", "unknown")
            and self.device_state.startswith("in_app_")
        )
        if not just_entered_app or not self.current_ui_tree:
            return None

        current_sig = build_screen_signature(self.current_ui_tree)
        fresh = self.task_memory.query(
            step_instruction  = task.ai_prompt,
            overall_goal      = overall_goal,
            app               = app,
            current_signature = current_sig,
        )
        self._log_cache_result(fresh, task.ai_prompt, app)

        if fresh.band != "none":
            logger.info(f"[CACHE] In-loop Tier 2 re-query: band={fresh.band} after entering {app}")
        return fresh

    async def _execute_tier2_script(
        self, task, t2_result, overall_goal, app, actions_executed, start_time,
    ) -> Optional[MobileTaskResult]:
        logger.info(f"[T2] Guided script: {len(t2_result.recipes)} steps")
        self.tier_stats["tier2"] += 1
        self._t2_completed_steps = []

        for i, recipe in enumerate(t2_result.recipes):
            logger.info(f"[T2] Step {i+1}: {recipe.action_type} | '{recipe.step_instruction[:55]}'")
            logger.debug(f"[CACHE] T2 selectors: {recipe.selectors}")

            live_sig  = build_screen_signature(self.current_ui_tree)
            sig_match = _signature_jaccard(recipe.screen_signature, live_sig)
            if recipe.screen_signature and sig_match < _SIG_VERIFY_THRESHOLD:
                logger.warning(f"[T2] Sig mismatch ({sig_match:.0%}) — Tier 3")
                return None

            # Context verification for EXECUTE band (catches goal-context mismatch)
            if t2_result.band == "execute" and i == 0:
                context_ok = await self._verify_recipe_context(recipe, task.ai_prompt)
                if not context_ok:
                    logger.warning("[T2] Context verify rejected recipe → downgrade to hint")
                    t2_result.hint_text = self.task_memory._build_hint(
                        t2_result.recipes,
                        task.ai_prompt,
                    )
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

        # Safety: AURA exit — HOME not BACK (BACK only navigates within Flutter)
        if "aura" in (ui_tree.app_name or "").lower():
            logger.info("[T1] AURA detected → HOME")
            return {"thought": "exit AURA app", "action_type": "global_action", "global_action": "HOME"}

        # Safety: hard stuck recovery
        if self.stuck_counter >= 8:
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

        # Deterministic compose-field typing for email tasks (To/Subject/Body)
        compose = self._handle_compose_field_typing(goal, ui_tree)
        if compose:
            return compose

        # Already on target app
        goal_lower = goal.lower()
        if any(kw in goal_lower for kw in ("open", "launch", "start", "navigate")):
            target = self._extract_target_app(goal)
            if target:
                app_id = f"{(ui_tree.app_package or '').lower()} {(ui_tree.app_name or '').lower()}"
                if target.lower().split()[0] in app_id:
                    logger.info(f"[T1] '{target}' already open → complete")
                    return {"thought": f"'{target}' is already open", "action_type": "complete"}

        # App drawer — phased swipe strategy when target app is not visible
        if device_state == "home_screen":
            target = self._extract_target_app(goal)
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
                        logger.info("[T1] App drawer phase 2 — swipe up gesture")
                        return {
                            "thought": "swipe up to open app drawer",
                            "action_type": "swipe",
                            "start_x_percent": 50,
                            "start_y_percent": 80,
                            "end_x_percent": 50,
                            "end_y_percent": 20,
                            "duration": 600,
                        }
                    elif self._app_drawer_phase == 2:
                        self._app_drawer_phase = 3
                        logger.info("[T1] App drawer phase 3 — second swipe attempt")
                        return {
                            "thought": "swipe up again to fully open app drawer",
                            "action_type": "swipe",
                            "start_x_percent": 50,
                            "start_y_percent": 75,
                            "end_x_percent": 50,
                            "end_y_percent": 15,
                            "duration": 800,
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
        DISMISS_VOCAB = {
            "got it", "ok", "okay", "dismiss", "done", "accept", "allow",
            "continue", "agree", "understood", "close", "skip", "later",
            "not now", "no thanks", "deny", "x",
        }
        dismiss_elements = [
            e for e in ui_tree.elements
            if (e.clickable or e.focusable)
            and any(kw in ((e.content_description or "") + " " + (e.text or "")).lower()
                    for kw in DISMISS_VOCAB)
        ]
        if not dismiss_elements or len(ui_tree.elements) > 8:
            return None
        elem  = dismiss_elements[0]
        label = (elem.content_description or elem.text or "dismiss")[:30]
        logger.info(f"[T1] Global interstitial '{label}' → elem {elem.element_id}")
        return {"thought": f"dismiss overlay ('{label}')",
                "action_type": "click", "element_id": elem.element_id}

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

        # Fallback ordering
        if hour_fid is None:
            tfs = [e for e in ui_tree.elements if e.type == "textfield"]
            if len(tfs) >= 1:
                hour_fid = tfs[0].element_id
                if (tfs[0].text or "").strip().isdigit():
                    try: displayed_h = int(tfs[0].text.strip())
                    except ValueError: pass
            if len(tfs) >= 2:
                minute_fid = tfs[1].element_id
                if (tfs[1].text or "").strip().isdigit():
                    displayed_m = tfs[1].text.strip()

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

    # ══════════════════════════════════════════════════════════════════════
    #  TIER 3 — LLM REACT
    # ══════════════════════════════════════════════════════════════════════

    async def _llm_react(
        self, goal, overall_goal, pruned_tree, thought_history,
        step_number, hint_context, handoff_context, extra_params,
    ) -> Tuple[str, Optional[Dict]]:

        history_ctx  = ("Prior thoughts: " + " → ".join(thought_history[-3:])) if thought_history else ""
        param_ctx    = self._format_extra_params(extra_params)
        compose_ctx  = self._compose_screen_context(goal)
        alarm_ctx    = self._alarm_list_context(goal)
        time_ctx     = self._time_picker_context(goal)
        blacklist_str = (f"⛔ Do NOT click element IDs: {sorted(list(self.failed_elements))}. "
                         if self.failed_elements else "")

        # FIX 4: simplified system prompt — less rule-heavy, more context-driven
        system_prompt = f"""You are an Android UI automation agent. You operate in a ReAct loop.

RESPONSE FORMAT (every time):
Thought: <one short sentence explaining what you see and plan>
Action: {{"action_type": "...", ...}}

RULES:
- Use element_id from the current UI tree to interact. Never click by coordinates unless explicitly necessary.
- To open an app: find its icon on the home screen or app drawer and click it. Never use the Google search bar.
- Handle popups (permissions, onboarding) by clicking "Allow", "Got it", or "Skip" when they appear.
- When typing, first click the text field to focus, then type the exact value.
- Do not claim the task is complete unless the required fields are filled and the expected screen appears.

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
            f"\nCURRENT SCREEN:\n{pruned_tree}\n"
            + (f"\n{history_ctx}" if history_ctx else "")
            + "\n\nRespond with Thought and Action."
        )

        raw_response = ""
        for attempt in range(2):
            suffix = "\n\nSTRICT: Start with 'Thought:' on line 1." if attempt == 1 else ""
            try:
                response = await self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user",   "content": user_prompt + suffix}],
                    temperature=0.2, max_tokens=300,
                )
                raw_response = response.choices[0].message.content.strip()
                self._track_llm_usage(response)
                logger.debug(f"[T3] Raw LLM:\n{raw_response}")
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
            resp = await self.llm_client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=60,
            )
            self._track_llm_usage(resp)
            js = self._extract_json(resp.choices[0].message.content.strip())
            if js:
                d = json.loads(js)
                correct = bool(d.get("correct", True))
                logger.info(f"[T1] App verify: correct={correct} — {d.get('reason','')}")
                return {"success": correct, "actual": app_name, "reason": d.get("reason", "")}
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
        if recipe.similarity > 0.97:
            logger.debug(f"[T2] Context verify skipped (high sim={recipe.similarity:.3f})")
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
            resp = await self.llm_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=35,
            )
            self._track_llm_usage(resp)
            js = self._extract_json(resp.choices[0].message.content.strip())
            if js:
                d = json.loads(js)
                ok = bool(d.get("ok", True))
                logger.info(f"[T2] Context verify: ok={ok} — {d.get('r','')}")
                return ok
        except Exception as e:
            logger.debug(f"[T2] Context verify failed: {e}")
        return True

    # ══════════════════════════════════════════════════════════════════════
    #  LEARNING
    # ══════════════════════════════════════════════════════════════════════

    def _store_learned_steps(self, step_instruction, overall_goal, app, actions):
        if not actions or not self.current_ui_tree: return
        sig       = build_screen_signature(self.current_ui_tree)
        selectors: List[Dict[str, str]] = []
        if self.last_clicked_element:
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
        atype = last.get("action", {}).get("action_type", "click")
        record_id = self.task_memory.store(
            step_instruction=step_instruction, overall_goal=overall_goal,
            app=app, action_type=atype, screen_signature=sig,
            selectors=selectors, demonstrated=0, success_count=1,
        )
        if record_id:
            logger.info(f"[CACHE] Stored Tier 3 success: '{step_instruction[:55]}' id={record_id[:8]}")

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
        if not any(kw in gl for kw in ("fill", "type", "enter", "set")): return action_json
        if not self.current_ui_tree: return action_json
        expected = []
        em = re.search(r"(?:to|recipient)\s+([a-zA-Z0-9._%+-]+@\S+)", gl)
        if em: expected.append(em.group(1))
        sm = re.search(r"subject\s+['\"]?([^'\"]+)['\"]?", gl)
        if sm: expected.append(sm.group(1).strip())
        tm = re.search(r"(\d{1,2}:\d{2}\s*(?:am|pm))", gl, re.IGNORECASE)
        if tm: expected.append(tm.group(1).strip())
        if not expected: return action_json
        screen_text = " ".join(
            (e.text or "") + " " + (e.content_description or "")
            for e in self.current_ui_tree.elements
        ).lower()
        missing = [v for v in expected if v.lower() not in screen_text]
        if missing:
            logger.error(f"[T3] Complete rejected: {missing} not on screen")
            return {"action_type": "scroll", "direction": "down", "duration": 300,
                    "thought": "verify failed — scroll"}
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
            c = re.sub(r"\s+(login|home|main|app|page|screen)$", "",
                       m.group(1).strip().lower()).strip()
            if c and not any(w in c.split() for w in _GENERIC_PAGE_WORDS):
                if c not in ("the", "a", "an", "my", "this", "that", "app", "it"):
                    return c
        m2 = re.search(r"\b(?:in|on|from|using|via)\s+(\w+(?:\s+\w+)?)\s*$", goal, re.IGNORECASE)
        if m2:
            c = m2.group(1).strip().lower()
            if c not in ("the", "a", "an", "my", "this", "that", "it"):
                if not any(w in c.split() for w in _GENERIC_PAGE_WORDS):
                    return c
        return None

    def _target_is_generic_page(self, target: str) -> bool:
        return bool(set(target.lower().split()) & _GENERIC_PAGE_WORDS)

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