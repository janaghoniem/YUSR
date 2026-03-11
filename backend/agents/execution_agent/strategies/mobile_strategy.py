"""
Enhanced ReAct Loop for ANY Mobile Task - FULLY FIXED VERSION
================================================================

FIXES IMPLEMENTED:
1. ✅ App verification after clicks (detects wrong app opened)
2. ✅ Element blacklist (never clicks same wrong element twice)
3. ✅ App drawer support (scroll UP to find apps not on home screen)
4. ✅ Success verification (doesn't exit too early)
5. ✅ Incomplete UI detection (waits for full load)
6. ✅ Better coordinate matching using content_description
7. ✅ Proper stuck detection with recovery strategies

CRITICAL CHANGES:
- Blacklisted elements tracking (failed_elements set)
- App verification immediately after click
- Dynamic app drawer detection
- Task completion verification
- Incomplete UI handling (only 2-3 elements bug)
"""

import logging
import asyncio
import json
import re
import httpx
from typing import Optional, List, Dict, Any, Set

from agents.utils.device_protocol import (
    MobileTaskRequest, MobileTaskResult, UIAction, ActionResult,
    SemanticUITree
)
from agents.execution_agent.core.exec_agent_models import ExecutionResult
from agents.execution_agent.strategies.action_knowledge_base import ActionKB

logger = logging.getLogger(__name__)

# Shared ActionKB instance — survives across tasks so learned entries persist
_shared_action_kb: ActionKB | None = None

def _get_action_kb() -> ActionKB:
    global _shared_action_kb
    if _shared_action_kb is None:
        _shared_action_kb = ActionKB()
    return _shared_action_kb


class MobileReActStrategy:
    """
    FULLY FIXED ReAct loop with app verification and element blacklisting
    """
    
    # Dynamic alias map: what users say → package-name fragments
    # Only needed because Android packages differ from common names
    # e.g. com.google.android.gm ≠ "gmail"
    _APP_PACKAGE_ALIASES = {
        'gmail':      ['gm', 'mail'],
        'email':      ['gm', 'mail', 'gmail'],
        'mail':       ['gm', 'gmail'],
        'phone':      ['dialer', 'telecom', 'incallui'],
        'clock':      ['deskclock'],
        'alarm':      ['deskclock', 'clock'],
        'timer':      ['deskclock', 'clock'],
        'messages':   ['messaging', 'mms'],
        'play store': ['vending', 'finsky'],
        'store':      ['vending', 'finsky'],
        'browser':    ['chrome'],
        'music':      ['youtube.music', 'spotify'],
    }
    
    def __init__(self, device_id: str = "default_device"):
        self.device_id = device_id
        self.backend_url = "http://localhost:8000"
        
        # Initialize Groq LLM
        from groq import AsyncGroq
        
        api_key = "" 
        self.llm_client = AsyncGroq(api_key=api_key)
        self.model = "llama-3.3-70b-versatile"
        
        # Device state tracking
        self.current_ui_tree: Optional[SemanticUITree] = None
        self.previous_ui_trees: List[SemanticUITree] = []
        self.action_history: List[Dict] = []
        self.device_state: str = "unknown"
        self.stuck_counter: int = 0
        
        # ✅ NEW: Blacklist and verification tracking
        self.failed_elements: Set[int] = set()  # Elements that opened wrong apps
        self.last_clicked_element: Optional[int] = None
        self.last_action_was_click: bool = False  # Track if last action was a click
        self.app_drawer_attempted: bool = False
        self.incomplete_ui_count: int = 0
        
        # ✅ NEW: Track typed text to prevent duplicates
        self.typed_texts: Dict[int, str] = {}  # element_id -> last typed text
        
        # ✅ FIX: Track consecutive duplicate skips to prevent infinite loops
        self.consecutive_skips: int = 0
        
        # ✅ FIX: Store current task for access in _think_and_decide
        self.current_task: Optional[MobileTaskRequest] = None

        # ✅ NEW: LLM usage + UI stats
        self.token_usage: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls: int = 0
        self.total_ui_elements_seen: int = 0
        self.max_ui_elements_seen: int = 0
        self.ui_samples_count: int = 0

        # ✅ ACTION KNOWLEDGE BASE (Tier 2)
        self.action_kb = _get_action_kb()

        # ✅ TIER TRACKING — how many decisions came from each tier
        self.tier_stats: Dict[str, int] = {"tier1": 0, "tier2a": 0, "tier2b": 0, "tier3_llm": 0}
        
        # ✅ T2A REPEAT GUARD — detect when cached action loops without progress
        self._t2a_last_cache_key: Optional[str] = None
        self._t2a_repeat_count: int = 0
        self._T2A_MAX_REPEATS: int = 2  # invalidate after 2 consecutive same-action hits
        
        # ✅ FIX 1: Retry logic - prevent permanent blacklisting after 1 failure
        self.element_retry_count: Dict[int, int] = {}  # element_id → retry attempts
        self.MAX_ELEMENT_RETRIES: int = 3  # Retry 3 times before blacklisting
        
        # ✅ FIX 3: Thought loop detection - prevent infinite loops
        self.recent_thoughts: List[str] = []  # Track last 5 thoughts
        self.STUCK_THOUGHT_THRESHOLD: int = 3  # Same thought 3 times = stuck
        self.thought_loop_recoveries: int = 0  # Escalation: 0=invalidate KB, 1+=HOME
        self._initial_ui_signature: Optional[tuple] = None  # (elem_count, screen_name)
        self._suppress_kb: bool = False  # Bypass T2A+T2B after thought loop
        self._keyboard_dismissed: bool = False  # Track if we already dismissed keyboard this task
        
        logger.info(f"✅ Initialized MobileReActStrategy for device {device_id}")
        logger.info(f"   📚 ActionKB: {self.action_kb.get_stats()}")
    
    def _detect_thought_loop(self, current_thought: str) -> bool:
        """Detect if LLM is repeating the same thought (stuck in loop)"""
        # Normalize thought
        normalized = current_thought.lower().strip()
        
        # Add to history (keep last 5)
        self.recent_thoughts.append(normalized)
        if len(self.recent_thoughts) > 5:
            self.recent_thoughts.pop(0)
        
        # Check last 3 thoughts
        if len(self.recent_thoughts) >= 3:
            last_3 = self.recent_thoughts[-3:]
            
            # All 3 identical = stuck!
            if len(set(last_3)) == 1:
                logger.error(f"🔁 THOUGHT LOOP DETECTED!")
                logger.error(f"   Repeated thought: '{current_thought}'")
                logger.error(f"   Last 3 thoughts: {last_3}")
                return True
        
        return False
    
    def _screen_changed_significantly(self, current_ui: SemanticUITree) -> bool:
        """
        Check if current screen changed significantly from initial state.
        Used to detect when a click/tap task has already succeeded
        (e.g. clicked Compose → compose screen appeared).
        """
        if not self._initial_ui_signature:
            return False
        
        initial_count, initial_screen = self._initial_ui_signature
        current_count = len(current_ui.elements)
        current_screen = current_ui.screen_name or ""
        
        # Element count changed by >= 30%
        if initial_count > 0:
            change_ratio = abs(current_count - initial_count) / initial_count
            if change_ratio >= 0.3:
                return True
        
        # Screen name changed meaningfully
        if (initial_screen and current_screen
                and initial_screen.lower() != current_screen.lower()):
            return True
        
        return False
    
    def _is_click_or_button_task(self, goal: str) -> bool:
        """Check if goal is asking to click/tap/press something"""
        return bool(re.search(r'\b(click|tap|press)\b', goal.lower()))
    
    def _tier1_app_drawer_fallback(self, ui_elements: list, target_app: Optional[str]) -> Optional[Dict[str, Any]]:
        """T1.5: Open app drawer when target app not visible on home screen"""
        # Only on home screen
        if self.device_state != "home_screen":
            return None
        
        if not target_app:
            return None
        
        # Check if target app is visible on home screen
        app_visible = any(
            target_app.lower() in str(elem.text or "").lower()
            for elem in ui_elements
        )
        
        # If app NOT visible and we've blacklisted elements, try app drawer
        if not app_visible and len(self.failed_elements) > 0:
            logger.info(f"⚡ T1.5: Target app '{target_app}' not visible + {len(self.failed_elements)} blacklisted elements")
            logger.info(f"⚡ Opening app drawer to search for '{target_app}'")
            
            return {
                "thought": f"open app drawer to find {target_app}",
                "action_type": "scroll",
                "direction": "up",
                "duration": 500
            }
        
        return None
    
    async def execute_task(self, task: MobileTaskRequest) -> MobileTaskResult:
        """Execute ANY task using FULLY FIXED ReAct loop"""
        
        # ✅ FIX: Store task for access in other methods
        self.current_task = task
        
        # ✅ CRITICAL: Dynamic timeout based on task complexity
        original_timeout = task.timeout_seconds
        task.timeout_seconds = self._calculate_smart_timeout(task.ai_prompt, task.timeout_seconds)
        
        if task.timeout_seconds != original_timeout:
            logger.info(f"⏱️ Timeout adjusted: {original_timeout}s → {task.timeout_seconds}s (task complexity)")
        
        logger.info(f"\n{'='*70}")
        logger.info(f"🎯 STARTING FULLY FIXED REACT LOOP")
        logger.info(f"{'='*70}")
        logger.info(f"Goal: {task.ai_prompt}")
        logger.info(f"Device: {task.device_id}")
        logger.info(f"Max Steps: {task.max_steps}")
        logger.info(f"Timeout: {task.timeout_seconds}s")
        logger.info(f"{'='*70}\n")
        
        # Reset tracking for new task
        self.failed_elements.clear()
        self.app_drawer_attempted = False
        self.incomplete_ui_count = 0
        self.stuck_counter = 0
        self.last_action_was_click = False
        self.consecutive_skips = 0  # ✅ FIX: Reset duplicate skip counter
        self.typed_texts.clear()  # Clear typed text tracking
        self.thought_loop_recoveries = 0  # Reset escalation counter
        self.recent_thoughts.clear()  # Reset thought history
        self._suppress_kb = False  # Reset KB bypass
        self._keyboard_dismissed = False  # Reset keyboard dismiss tracking
        self.token_usage = {"prompt": 0, "completion": 0, "total": 0}
        self.total_llm_calls = 0
        self.total_ui_elements_seen = 0
        self.max_ui_elements_seen = 0
        self.ui_samples_count = 0
        self.tier_stats = {"tier1": 0, "tier2a": 0, "tier2b": 0, "tier3_llm": 0}
        
        start_time = asyncio.get_event_loop().time()
        actions_executed: List[UIAction] = []
        thought_history: List[str] = []
        
        # Get initial UI state
        logger.info(f"👁️ Getting initial UI state...")
        await asyncio.sleep(1.5)
        
        ui_tree = await self._fetch_ui_tree_with_retries("wait", max_attempts=2, retry_delay=1.5)
        
        if not ui_tree:
            return self._build_error_result(task.task_id, "Failed to get initial UI tree")
        
        self.current_ui_tree = ui_tree
        self.previous_ui_trees.append(ui_tree)
        self.device_state = self._detect_device_state(ui_tree)
        self._update_ui_stats(ui_tree)
        
        logger.info(f"✅ Initial UI captured: {ui_tree.screen_name or ui_tree.app_name}")
        logger.info(f"   Elements found: {len(ui_tree.elements)}")
        logger.info(f"   Device state: {self.device_state}")
        logger.info(f"   App: {ui_tree.app_name}")
        
        # ✅ Track initial UI state for screen-change-based completion
        self._initial_ui_signature = (len(ui_tree.elements), ui_tree.screen_name or "")
        
        # Log elements for debugging
        if ui_tree.elements:
            logger.info(f"   📦 UI Elements:")
            for elem in ui_tree.elements[:15]:
                elem_text = elem.text[:40] if elem.text else "(no text)"
                logger.info(f"      [{elem.element_id}] {elem.type:12} | {elem_text}")
        
        # Extract target app from goal
        target_app = self._extract_target_app(task.ai_prompt)
        logger.info(f"🎯 Target app: {target_app}")
        
        # ReAct Loop
        for step in range(task.max_steps):
            logger.info(f"\n{'='*70}")
            logger.info(f"📍 STEP {step + 1}/{task.max_steps}")
            logger.info(f"{'='*70}")
            
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > task.timeout_seconds:
                logger.warning(f"⏱️ Timeout reached")
                return self._build_result(
                    task_id=task.task_id,
                    status="timeout",
                    steps=step,
                    actions=actions_executed,
                    elapsed=elapsed,
                    error=f"Timeout after {task.timeout_seconds}s"
                )
            
            # ✅ CRITICAL FIX: Handle incomplete UI trees BEFORE verification
            if len(self.current_ui_tree.elements) < 5:
                self.incomplete_ui_count += 1
                logger.warning(f"⚠️ Incomplete UI ({len(self.current_ui_tree.elements)} elements) - count: {self.incomplete_ui_count}")
                
                if self.incomplete_ui_count >= 3:
                    logger.error("❌ UI stuck loading - going BACK")
                    back_action = UIAction(action_type="global_action", global_action="BACK", duration=1000)
                    await self._execute_action_on_device(back_action)
                    await asyncio.sleep(3.0)  # ✅ Wait longer for BACK
                    
                    ui_tree = await self._fetch_ui_tree_with_retries("global_action")
                    if ui_tree:
                        self.current_ui_tree = ui_tree
                        self.device_state = self._detect_device_state(ui_tree)
                        self._update_ui_stats(ui_tree)
                    
                    self.incomplete_ui_count = 0
                    continue
            else:
                self.incomplete_ui_count = 0
            
            # Check if stuck (but NOT in time picker!)
            if self._detect_stuck_in_loop() and not self._is_in_time_picker(self.current_ui_tree):
                # ✅ SMART RECOVERY: check if task is actually already done
                if self._screen_changed_significantly(self.current_ui_tree):
                    logger.info(f"✅ Stuck but screen changed from initial — task likely DONE")
                    elapsed_now = asyncio.get_event_loop().time() - start_time
                    return self._build_result(
                        task_id=task.task_id,
                        status="success",
                        steps=step,
                        actions=actions_executed,
                        elapsed=elapsed_now,
                        completion_reason="Task completed (screen changed from initial state)"
                    )
                
                # Screen hasn't changed → try BACK (less destructive than HOME)
                logger.error(f"❌ Stuck in same screen — pressing BACK")
                back_action = UIAction(action_type="global_action", global_action="BACK", duration=1000)
                await self._execute_action_on_device(back_action)
                await asyncio.sleep(3.0)
                
                ui_tree = await self._fetch_ui_tree_with_retries("global_action")
                if ui_tree:
                    self.current_ui_tree = ui_tree
                    self.device_state = self._detect_device_state(ui_tree)
                    logger.info(f"⬅️ Pressed BACK — new state: {self.device_state}")
                    self._update_ui_stats(ui_tree)
            
            # ========================================================================
            # 3-TIER DECISION PIPELINE
            # ========================================================================
            action_json = None
            thought = ""
            decision_tier = ""  # track which tier resolved this step
            
            # ── TIER 1: DETERMINISTIC SHORTCUTS (0 tokens, 0 ms) ──────
            tier1_action = self._tier1_deterministic_shortcuts(
                goal=task.ai_prompt,
                ui_tree=self.current_ui_tree,
                device_state=self.device_state,
                stuck_counter=self.stuck_counter
            )
            if tier1_action:
                thought = tier1_action["thought"]
                action_json = tier1_action
                decision_tier = "tier1"
                logger.info(f"⚡ TIER 1 SHORTCUT: {thought}")
            
            # ✅ FIX 6: Try app drawer fallback if target app not visible
            if action_json is None:
                app_drawer_action = self._tier1_app_drawer_fallback(
                    self.current_ui_tree.elements,
                    target_app
                )
                if app_drawer_action:
                    thought = app_drawer_action["thought"]
                    action_json = app_drawer_action
                    decision_tier = "tier1_app_drawer"
                    logger.info(f"⚡ TIER 1.5 (APP DRAWER FALLBACK): {thought}")
            
            # ── TIER 2: ACTION KB (0 tokens, <1 ms) ──────────────────
            if action_json is None and self._suppress_kb:
                logger.info("🔇 KB suppressed (thought loop recovery) → skipping to LLM")
            elif action_json is None:
                # Build params dict from coordinator extra_params
                kb_params = self._build_kb_params(task)

                
                kb_action = self.action_kb.lookup(
                    app_package=self.current_ui_tree.app_package or "",
                    elements=self.current_ui_tree.elements,
                    goal=task.ai_prompt,
                    blacklist=self.failed_elements,
                    params=kb_params,
                )
                if kb_action:
                    thought = kb_action.get("thought", "knowledge base match")
                    action_json = kb_action
                    # Distinguish 2A vs 2B via KB stats delta
                    old_2a = self.action_kb.tier2a_hits
                    decision_tier = "tier2a" if self.action_kb.tier2a_hits > (self.tier_stats.get("_prev_2a", 0)) else "tier2b"
                    self.tier_stats["_prev_2a"] = self.action_kb.tier2a_hits
                    logger.info(f"📖 TIER 2 ({decision_tier.upper()}): {thought}")
                    
                    # ✅ T2A REPEAT GUARD: detect stale cache loops
                    if decision_tier == "tier2a":
                        from .action_knowledge_base import fingerprint_screen, normalise_goal
                        _fp = fingerprint_screen(
                            self.current_ui_tree.app_package or "",
                            self.current_ui_tree.elements,
                        )
                        _ck = f"{_fp}:{normalise_goal(task.ai_prompt)}"
                        if _ck == self._t2a_last_cache_key:
                            self._t2a_repeat_count += 1
                            logger.warning(
                                f"🔁 T2A repeat #{self._t2a_repeat_count} for "
                                f"cache_key={_ck[:30]}…"
                            )
                            if self._t2a_repeat_count >= self._T2A_MAX_REPEATS:
                                logger.warning(
                                    f"🗑️ T2A stale — invalidating & falling through to LLM"
                                )
                                self.action_kb.invalidate(
                                    self.current_ui_tree.app_package or "",
                                    self.current_ui_tree.elements,
                                    task.ai_prompt,
                                )
                                action_json = None  # fall through to T3
                                decision_tier = ""
                                self._t2a_repeat_count = 0
                                self._t2a_last_cache_key = None
                        else:
                            self._t2a_last_cache_key = _ck
                            self._t2a_repeat_count = 1
            
            # ── TIER 3: LLM FALLBACK (tokens, ~1-2s) ─────────────────
            if action_json is None:
                decision_tier = "tier3_llm"
                logger.info(f"🤔 TIER 3 (LLM): Analyzing current screen...")
                
                observation = self.current_ui_tree.to_semantic_string()
                logger.info(f"📋 Observation:\n{observation}\n")
                
                thought, action_json = await self._think_and_decide(
                    goal=task.ai_prompt,
                    observation=observation,
                    thought_history=thought_history,
                    step_number=step + 1
                )
            
            # Update tier counters
            if decision_tier and decision_tier in self.tier_stats:
                self.tier_stats[decision_tier] += 1
            
            if not action_json:
                logger.error(f"❌ Failed to generate valid action")
                return self._build_result(
                    task_id=task.task_id,
                    status="failed",
                    steps=step,
                    actions=actions_executed,
                    elapsed=elapsed,
                    error="LLM failed to generate valid action"
                )
            
            thought_history.append(thought)
            logger.info(f"💭 Thought: {thought}")
            
            # ✅ FIX 4: Detect thought loop BEFORE executing action
            if self._detect_thought_loop(thought):
                # ── First: check if the task is actually DONE ──
                if self._screen_changed_significantly(self.current_ui_tree):
                    logger.info("✅ Thought loop but screen changed — task is DONE!")
                    elapsed_now = asyncio.get_event_loop().time() - start_time
                    return self._build_result(
                        task_id=task.task_id,
                        status="success",
                        steps=step + 1,
                        actions=actions_executed,
                        elapsed=elapsed_now,
                        completion_reason="Task completed (screen changed from initial state)"
                    )
                
                self.thought_loop_recoveries += 1
                
                if self.thought_loop_recoveries <= 1:
                    # ── SOFT RECOVERY: suppress KB (T2A+T2B) + fall through to LLM ──
                    logger.error("🚨 THOUGHT LOOP DETECTED — Soft recovery (suppress KB → LLM)")
                    self._suppress_kb = True
                    try:
                        self.action_kb.invalidate(
                            self.current_ui_tree.app_package or "",
                            self.current_ui_tree.elements,
                            task.ai_prompt,
                        )
                    except Exception:
                        pass
                    self.recent_thoughts.clear()

                    # ── For click tasks: dismiss keyboard via scroll (NOT BACK — BACK exits compose!) ──
                    if self._is_click_or_button_task(task.ai_prompt):
                        logger.info("⌨️ Click task — dismissing keyboard (scroll DOWN) before LLM retry")
                        scroll_action = UIAction(
                            action_type="scroll",
                            direction="down",
                            duration=300,
                        )
                        await self._execute_action_on_device(scroll_action)
                        await asyncio.sleep(1.5)
                        fresh_ui = await self._fetch_ui_tree_with_retries("scroll")
                        if fresh_ui:
                            self.current_ui_tree = fresh_ui
                            self._update_ui_stats(fresh_ui)
                            self.device_state = self._detect_device_state(fresh_ui)
                            logger.info(f"✅ Fresh UI after keyboard dismiss: {len(fresh_ui.elements)} elements")

                    logger.info("🔇 KB suppressed for rest of task — LLM takes over")
                    continue
                else:
                    # ── ESCALATED: check if typing was already done ──
                    if self.typed_texts:
                        goal_lower = task.ai_prompt.lower()
                        for _eid, _txt in self.typed_texts.items():
                            if _txt.lower() in goal_lower:
                                logger.info(f"✅ Thought loop but text '{_txt}' already typed — task DONE")
                                elapsed_now = asyncio.get_event_loop().time() - start_time
                                return self._build_result(
                                    task_id=task.task_id,
                                    status="success",
                                    steps=step + 1,
                                    actions=actions_executed,
                                    elapsed=elapsed_now,
                                    completion_reason=f"Completed (text already typed)"
                                )
                    # ── ESCALATED: fail fast instead of HOME (less destructive) ──
                    logger.error("🚨 THOUGHT LOOP PERSISTS — failing task (no HOME)")
                    elapsed_now = asyncio.get_event_loop().time() - start_time
                    return self._build_result(
                        task_id=task.task_id,
                        status="failed",
                        steps=step + 1,
                        actions=actions_executed,
                        elapsed=elapsed_now,
                        error="Thought loop — unable to complete task on current screen"
                    )
            
            # Check if goal achieved
            if action_json.get("action_type") == "complete":
                logger.info(f"\n{'='*70}")
                logger.info(f"✅ GOAL ACHIEVED!")
                logger.info(f"{'='*70}")
                
                return self._build_result(
                    task_id=task.task_id,
                    status="success",
                    steps=step + 1,
                    actions=actions_executed,
                    elapsed=elapsed,
                    completion_reason=action_json.get("reason", "Task completed")
                )
            
            # ✅ CRITICAL FIX #4: Check blacklist before clicking
            if action_json.get("action_type") == "click":
                element_id = action_json.get("element_id")
                if element_id and element_id in self.failed_elements:
                    logger.warning(f"🚫 Skipping blacklisted element {element_id}")
                    
                    # ✅ FIX: Mark as redirect so it NEVER gets cached to T2A
                    decision_tier = "blacklist_redirect"
                    
                    # Try app drawer instead
                    if not self.app_drawer_attempted and self._is_home_screen(self.current_ui_tree):
                        logger.info("📱 Opening app drawer (scroll UP)")
                        action_json = {
                            "action_type": "scroll",
                            "direction": "up",
                            "duration": 500
                        }
                        self.app_drawer_attempted = True
                    else:
                        logger.info("⏭️ Skipping to next iteration")
                        continue
            
            # ACT
            logger.info(f"🎬 ACT: Executing action...")
            logger.info(f"   Type: {action_json.get('action_type')}")
            
            action = self._json_to_ui_action(action_json)
            logger.info(f"   Action: {action.model_dump()}")
            
            # Track clicked element
            if action_json.get("action_type") == "click":
                self.last_clicked_element = action_json.get("element_id")
                self.last_action_was_click = True
            else:
                self.last_action_was_click = False
            
            # ✅ CRITICAL FIX #2: Validate TYPE actions - prevent duplicates
            if action_json.get("action_type") == "type":
                element_id = action_json.get("element_id")
                text_to_type = action_json.get("text", "")
                
                # Check if we already typed this EXACT text in this field
                if element_id in self.typed_texts:
                    if self.typed_texts[element_id] == text_to_type:
                        logger.warning(f"🚫 Already typed '{text_to_type}' in element {element_id}")
                        logger.warning(f"⏭️ Skipping duplicate type action")
                        
                        # ✅ FIX: Immediately complete — we already typed this text
                        logger.info(f"✅ Text '{text_to_type}' already in field — task DONE")
                        elapsed = asyncio.get_event_loop().time() - start_time
                        return self._build_result(
                            task_id=task.task_id,
                            status="success",
                            steps=step + 1,
                            actions=actions_executed,
                            elapsed=elapsed,
                            completion_reason=f"Completed (text already typed in field)"
                        )
                
                # Store what we're about to type
                self.typed_texts[element_id] = text_to_type
            
            # ✅ PRE-CLICK KEYBOARD DISMISS: Before clicking Send/submit on compose screens,
            # scroll to dismiss keyboard first. The keyboard can intercept click coordinates.
            if (action_json.get("action_type") == "click"
                    and not self._keyboard_dismissed
                    and self.device_state == "in_app_gm"
                    and self._is_click_or_button_task(task.ai_prompt)):
                # Check if the target element has "Send" or similar in content_description
                target_eid = action_json.get("element_id")
                target_elem = self.current_ui_tree.get_element_by_id(target_eid) if target_eid else None
                target_desc = ""
                if target_elem:
                    target_desc = (target_elem.content_description or "").lower()
                if any(kw in target_desc for kw in ["send", "submit", "post"]):
                    logger.info("⌨️ Pre-click: Dismissing keyboard before clicking Send")
                    scroll_action = UIAction(action_type="scroll", direction="down", duration=300)
                    await self._execute_action_on_device(scroll_action)
                    await asyncio.sleep(1.0)
                    # Re-capture UI and re-resolve the element
                    fresh_ui = await self._fetch_ui_tree_with_retries("scroll")
                    if fresh_ui:
                        self.current_ui_tree = fresh_ui
                        self._update_ui_stats(fresh_ui)
                        # Re-resolve the Send button element_id in new UI
                        for elem in fresh_ui.elements:
                            ed = (elem.content_description or "").lower()
                            if "send" in ed and (elem.clickable or elem.focusable):
                                action_json["element_id"] = elem.element_id
                                action = UIAction(**{k: v for k, v in action_json.items() if k != "action_id"})
                                logger.info(f"   Re-resolved Send button → element {elem.element_id}")
                                break
                    self._keyboard_dismissed = True

            # Track action history
            self.action_history.append({
                "step": step + 1,
                "action": action_json,
                "device_state": self.device_state
            })
            
            result = await self._execute_action_on_device(action)
            actions_executed.append(action)
            
            if not result.success:
                logger.warning(f"⚠️ Action execution failed: {result.error}")
            else:
                logger.info(f"✅ Action executed successfully")
                # ── LEARN: cache successful LLM actions to Tier 2A ────
                if decision_tier == "tier3_llm" and action_json.get("action_type") in ("click", "type", "scroll"):
                    # ✅ Prevent cache poisoning: don't cache home-screen clicks
                    # for non-navigation goals (e.g. clicking Gmail for "fill body")
                    should_cache = True
                    if self.device_state == "home_screen" and action_json.get("action_type") == "click":
                        goal_lower = task.ai_prompt.lower()
                        is_nav = any(kw in goal_lower for kw in ['open ', 'launch ', 'start '])
                        if not is_nav:
                            should_cache = False
                            logger.debug(f"⏭️ Skip cache: home-screen click for non-navigation goal")
                    
                    if should_cache:
                        try:
                            self.action_kb.cache_success(
                                app_package=self.current_ui_tree.app_package or "",
                                elements=self.current_ui_tree.elements,
                                goal=task.ai_prompt,
                                action=action_json,
                            )
                        except Exception as cache_err:
                            logger.debug(f"Cache write failed (non‑fatal): {cache_err}")
            
            # OBSERVE
            logger.info(f"👁️ OBSERVE: Getting new UI state...")
            
            wait_time = self._get_wait_time_for_action(action_json.get("action_type"))
            logger.info(f"⏳ Waiting {wait_time}s for UI to stabilize...")
            await asyncio.sleep(wait_time)
            
            # ✅ CRITICAL: Progressive UI fetching - apps take time to load!
            new_ui_tree = await self._fetch_ui_tree_with_retries(
                action_type=action_json.get("action_type"),
                max_attempts=3,
                retry_delay=2.0
            )
            
            if new_ui_tree:
                self.current_ui_tree = new_ui_tree
                self.previous_ui_trees.append(new_ui_tree)
                self._update_ui_stats(new_ui_tree)
                
                if len(self.previous_ui_trees) > 5:
                    self.previous_ui_trees.pop(0)
                
                new_device_state = self._detect_device_state(new_ui_tree)
                
                logger.info(f"✅ New UI captured: {new_ui_tree.screen_name or new_ui_tree.app_name}")
                logger.info(f"   Elements: {len(new_ui_tree.elements)}")
                logger.info(f"   Device state: {new_device_state}")
                
                if new_ui_tree.elements:
                    logger.info(f"   📦 UI Elements:")
                    for elem in new_ui_tree.elements[:10]:
                        elem_text = elem.text[:40] if elem.text else "(no text)"
                        logger.info(f"      [{elem.element_id}] {elem.type:12} | {elem_text}")
                
                if new_device_state != self.device_state:
                    logger.info(f"🔄 Device state changed: {self.device_state} → {new_device_state}")
                    self.device_state = new_device_state
                    self.stuck_counter = 0
                else:
                    self.stuck_counter += 1
                
                # ✅ AUTO-COMPLETE: Click/tap tasks when screen changed after a click
                if (self.last_action_was_click
                        and self._is_click_or_button_task(task.ai_prompt)
                        and self._screen_changed_significantly(new_ui_tree)):
                    logger.info(f"\n{'='*70}")
                    logger.info(f"✅✅✅ CLICK TASK COMPLETE (screen changed)")
                    logger.info(f"{'='*70}")
                    elapsed_now = asyncio.get_event_loop().time() - start_time
                    return self._build_result(
                        task_id=task.task_id,
                        status="success",
                        steps=step + 1,
                        actions=actions_executed,
                        elapsed=elapsed_now,
                        completion_reason="Click task completed — screen changed from initial"
                    )
                
                # ✅ CRITICAL: App verification AFTER UI fully loaded (moved from top of loop)
                # Only verify for CLICK actions and when we have a target app
                if self.last_action_was_click and target_app and len(new_ui_tree.elements) >= 5:
                    verification = self._verify_app_opened(target_app, new_ui_tree)
                    
                    if not verification["success"]:
                        # ✅ FIX 2: Retry logic instead of immediate blacklisting
                        element_id = self.last_clicked_element
                        retry_count = self.element_retry_count.get(element_id, 0) + 1
                        self.element_retry_count[element_id] = retry_count
                        
                        logger.error(f"❌ WRONG APP OPENED!")
                        logger.error(f"   Expected: {verification['expected_app']}")
                        logger.error(f"   Got: {verification['actual_app']}")
                        
                        if retry_count < self.MAX_ELEMENT_RETRIES:
                            # Give it another chance
                            logger.warning(f"⚠️ Element {element_id} failed (attempt {retry_count}/{self.MAX_ELEMENT_RETRIES})")
                            logger.info(f"🔄 Will retry element {element_id} instead of blacklisting immediately")
                            logger.info(f"⬅️ Going BACK to retry...")
                            
                            back_action = UIAction(action_type="global_action", global_action="BACK", duration=1000)
                            await self._execute_action_on_device(back_action)
                            await asyncio.sleep(2.0)
                            
                            ui_tree = await self._fetch_ui_tree_with_retries("global_action")
                            if ui_tree:
                                self.current_ui_tree = ui_tree
                                self.device_state = self._detect_device_state(ui_tree)
                                self._update_ui_stats(ui_tree)
                            
                            self.last_action_was_click = False
                            continue
                        else:
                            # Exhausted retries - now blacklist
                            logger.error(f"❌ Element {element_id} failed {self.MAX_ELEMENT_RETRIES} times")
                            logger.error(f"🚫 PERMANENTLY blacklisting element {element_id}")
                            self.failed_elements.add(element_id)
                            logger.info(f"⬅️ Going BACK to try different approach")
                            
                            back_action = UIAction(action_type="global_action", global_action="BACK", duration=1000)
                            await self._execute_action_on_device(back_action)
                            await asyncio.sleep(2.0)
                            
                            ui_tree = await self._fetch_ui_tree_with_retries("global_action")
                            if ui_tree:
                                self.current_ui_tree = ui_tree
                                self.device_state = self._detect_device_state(ui_tree)
                                self._update_ui_stats(ui_tree)
                            
                            self.last_action_was_click = False
                            continue
                    
                    # ✅ CRITICAL: Verify task is ACTUALLY complete
                    # Only auto-complete for navigation goals ("open X")
                    elif verification["success"] and verification["confidence"] > 0.8:
                        if self._is_task_truly_complete(task.ai_prompt, new_ui_tree):
                            logger.info(f"\n{'='*70}")
                            logger.info(f"✅✅✅ TASK COMPLETE: {verification['actual_app']}")
                            logger.info(f"{'='*70}")
                            
                            return self._build_result(
                                task_id=task.task_id,
                                status="success",
                                steps=step + 1,
                                actions=actions_executed,
                                elapsed=elapsed,
                                completion_reason=f"Successfully opened {verification['actual_app']}"
                            )
                        else:
                            logger.info(f"✅ Correct app ({verification['actual_app']}) — continuing to complete goal")
            else:
                logger.warning(f"⚠️ Failed to get new UI tree")
        
        # Max steps reached
        logger.warning(f"\n{'='*70}")
        logger.warning(f"⚠️ MAX STEPS REACHED")
        logger.warning(f"{'='*70}")
        
        return self._build_result(
            task_id=task.task_id,
            status="failed",
            steps=task.max_steps,
            actions=actions_executed,
            elapsed=asyncio.get_event_loop().time() - start_time,
            error=f"Max steps ({task.max_steps}) reached"
        )
    
    def _calculate_smart_timeout(self, goal: str, default_timeout: int) -> int:
        """
        Calculate smart timeout based on task complexity
        
        Time pickers: 45s (multiple clicks in dialog)
        Multi-step tasks: 60s (multiple actions)
        Search tasks: 45s (type + wait for results)
        Simple navigation: 30s (just open app)
        """
        goal_lower = goal.lower()
        
        # ✅ CRITICAL: Time picker tasks need MORE time!
        time_keywords = ["set alarm", "set time", "alarm at", "schedule"]
        if any(kw in goal_lower for kw in time_keywords):
            return max(45, default_timeout)  # At least 45 seconds for time pickers
        
        # Multi-step tasks (look for conjunctions or sequences)
        multi_step_keywords = ["and then", "after", "followed by", "and", "with"]
        if any(kw in goal_lower for kw in multi_step_keywords):
            # Count keywords to estimate complexity
            keyword_count = sum(1 for kw in multi_step_keywords if kw in goal_lower)
            if keyword_count >= 2:
                return max(90, default_timeout)  # Very complex
            return max(60, default_timeout)  # Medium complexity
        
        # Search tasks need moderate time
        search_keywords = ["search", "find", "look for", "query"]
        if any(kw in goal_lower for kw in search_keywords):
            return max(45, default_timeout)  # At least 45 seconds
        
        # Navigation tasks (just opening apps)
        open_keywords = ["open", "launch", "start", "close"]
        if any(kw in goal_lower for kw in open_keywords) and len(goal_lower.split()) <= 4:
            return max(30, default_timeout)  # At least 30 seconds
        
        # Default: use provided timeout but minimum 30s
        return max(30, default_timeout)
    
    def _get_content_from_task_params(self, task: 'MobileTaskRequest') -> str:
        """
        Get any parameters passed from coordinator via extra_params
        
        This allows coordinator to pass ANY field values (text, numbers, etc.)
        without hardcoding email-specific logic here
        """
        context_parts = []
        
        # Check extra_params (from coordinator)
        if hasattr(task, 'extra_params') and task.extra_params:
            for key, value in task.extra_params.items():
                # Skip internal params like input_from, device_id, etc.
                if key in ['input_from', 'device_id', 'app_name', 'file_path', 'url']:
                    continue
                
                # Add any user-facing parameter
                context_parts.append(f"📝 {key.upper()}: \"{value}\" (from coordinator)")
        
        if context_parts:
            return "\n" + "\n".join(context_parts) + "\n⚠️ USE THESE EXACT VALUES - DO NOT IMPROVISE!"
        
        return ""
    
    def _extract_target_app(self, goal: str) -> Optional[str]:
        """
        Dynamically extract the target app name from a goal string.
        Uses NLP-style regex parsing — no hardcoded app dictionary.
        
        Examples:
          "Open Gmail"                       → "gmail"
          "Send email in Gmail"              → "gmail"
          "Fill the subject field in Gmail"  → "gmail"
          "Search for cats on YouTube"       → "youtube"
          "Set alarm to 7:30 AM"             → None
        """
        # Pattern 1: "open/launch/start/navigate to <App>"
        m = re.search(r'\b(?:open|launch|start|navigate\s+to)\s+(?:the\s+)?(\w+(?:\s+\w+)?)', goal, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().lower()
            # Strip trailing noise words from coordinator prompts
            candidate = re.sub(r'\s+(login|home|main|app|page|screen)\s*(page|screen)?$', '', candidate)
            if candidate not in ('the', 'a', 'an', 'my', 'this', 'that', 'app', 'it', ''):
                return candidate
        
        # Pattern 2: "in/on/from <App>" (typically at end of phrase)
        m = re.search(r'\b(?:in|on|from|using|via)\s+(\w+(?:\s+\w+)?)\s*$', goal, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().lower()
            if candidate not in ('the', 'a', 'an', 'my', 'this', 'that', 'it'):
                return candidate
        
        # Pattern 3: "in/on <App>" mid-sentence  (e.g. "click the send button in Gmail")
        m = re.search(r'\b(?:in|on)\s+(\w+)\b', goal, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().lower()
            # Only accept if it looks like an app name (capitalised in original)
            orig_match = re.search(r'\b(?:in|on)\s+(\w+)', goal)
            if orig_match and orig_match.group(1)[0].isupper():
                return candidate
        
        return None
    
    @staticmethod
    def _app_matches_target(target: str, app_identity: str) -> bool:
        """
        Dynamically check if an app's identity (package + name) matches
        a target string.  Uses _APP_PACKAGE_ALIASES only for known
        Android-specific mismatches (gm ≠ gmail, etc.).
        """
        target = target.lower().strip()
        app_identity = app_identity.lower()
        
        # Direct match
        if target in app_identity:
            return True
        
        # Alias match (for Android packages that don't match common names)
        aliases = MobileReActStrategy._APP_PACKAGE_ALIASES.get(target, [])
        return any(alias in app_identity for alias in aliases)
    
    def _verify_app_opened(self, target_app: str, ui_tree: SemanticUITree) -> Dict[str, Any]:
        """
        Verify if correct app was opened.
        Uses dynamic package-name matching — no hardcoded keyword dictionary.
        """
        app_name = (ui_tree.app_name or "").lower()
        app_package = (ui_tree.app_package or "").lower()
        app_identity = f"{app_package} {app_name}"
        target_lower = target_app.lower().strip()
        
        is_correct = self._app_matches_target(target_lower, app_identity)
        
        if is_correct:
            return {
                "success": True,
                "expected_app": target_app,
                "actual_app": app_name,
                "confidence": 0.9,
                "reason": f"Successfully opened {target_app}"
            }
        else:
            return {
                "success": False,
                "expected_app": target_app,
                "actual_app": app_name,
                "confidence": 0.85,
                "reason": f"Expected {target_app} but got {app_name}"
            }
    
    def _is_task_truly_complete(self, goal: str, ui_tree: SemanticUITree) -> bool:
        """
        Check if task is ACTUALLY complete.
        
        Only returns True for NAVIGATION goals ("open X", "launch X").
        For action goals ("fill", "type", "click", "send"), the LLM must
        explicitly emit a "complete" action — we never auto-complete those
        just because the right app is open.
        """
        elements = ui_tree.elements
        
        # Must have functional UI
        if not elements or len(elements) < 5:
            return False
        
        # Only auto-complete for navigation goals
        goal_lower = goal.lower()
        is_navigation = any(kw in goal_lower for kw in ['open ', 'launch ', 'start ', 'go to ', 'navigate '])
        
        if not is_navigation:
            # For fill/type/click/send goals, NEVER auto-complete
            # Let the LLM decide when the action is actually done
            logger.info(f"⚠️ Non-navigation goal — skipping auto-complete")
            return False
        
        return True
    
    def _is_in_time_picker(self, ui_tree: SemanticUITree) -> bool:
        """Check if currently in time picker dialog"""
        if not ui_tree or not ui_tree.elements:
            return False
        
        # Time picker has specific elements:
        # - Hour/minute display elements
        # - AM/PM buttons
        # - OK/Cancel buttons
        has_am_pm = False
        has_ok_cancel = False
        has_time_display = False
        
        for elem in ui_tree.elements:
            text = (elem.text or "").upper()
            desc = (elem.content_description or "").lower()
            
            # Check for AM/PM buttons
            if text in ["AM", "PM"] and elem.type == "button":
                has_am_pm = True
            
            # Check for OK/Cancel buttons
            if text in ["OK", "CANCEL"] and elem.type == "button":
                has_ok_cancel = True
            
            # Check for time display (hour/minute indicators)
            if "o'clock" in desc or "minutes" in desc:
                has_time_display = True
        
        # Must have all three to be time picker
        is_picker = has_am_pm and has_ok_cancel and has_time_display
        
        if is_picker:
            logger.info("⏰ Detected time picker dialog - stuck detection DISABLED")
        
        return is_picker
    
    def _is_home_screen(self, ui_tree: SemanticUITree) -> bool:
        """Check if on home screen"""
        app_name = ui_tree.app_name.lower()
        device_state = self._detect_device_state(ui_tree)
        return "home" in device_state or "launcher" in app_name
    
    def _detect_device_state(self, ui_tree: SemanticUITree) -> str:
        """Detect current device state"""
        app_name = ui_tree.app_name.lower()
        screen_name = ui_tree.screen_name.lower() if ui_tree.screen_name else ""
        
        # Check AURA app first
        if "aura" in app_name or "aura_project" in app_name:
            logger.info(f"🔍 Detected AURA app: {app_name}")
            return "in_aura"
        
        # Home screen indicators
        home_indicators = [
            "launcher", "home screen", "desktop", "wallpaper",
            "homescreen", "main screen", "pixel launcher",
            "android launcher", "trebuchet", "nova launcher"
        ]
        
        if any(indicator in app_name or indicator in screen_name for indicator in home_indicators):
            return "home_screen"
        
        if "app drawer" in screen_name or "all apps" in screen_name:
            return "app_drawer"
        
        return f"in_app_{app_name.replace('.', '_')}"
    
    def _detect_stuck_in_loop(self) -> bool:
        """
        Detect if stuck - BUT be smart about it!
        
        NOT stuck if:
        - UI elements are changing (means app is responding)
        - Element count is fluctuating (means activity happening)
        - Recent actions succeeded
        
        Only stuck if:
        - Same state for 6+ steps (increased from 4)
        - AND same element count
        - AND no successful actions
        """
        # Need at least 6 steps to detect stuck (was 4, too aggressive!)
        if self.stuck_counter <= 5:
            return False
        
        logger.warning(f"⚠️ Stuck counter: {self.stuck_counter}")
        
        # Check if UI is changing
        if len(self.previous_ui_trees) >= 4:
            recent_element_counts = [len(tree.elements) for tree in self.previous_ui_trees[-4:]]
            
            # If element counts are changing, we're NOT stuck!
            if len(set(recent_element_counts)) > 1:
                logger.info(f"✅ UI elements changing: {recent_element_counts} - NOT stuck")
                return False
            
            # If UI has very few elements and not changing, might be stuck
            if recent_element_counts[0] <= 3:
                logger.error(f"❌ UI frozen with only {recent_element_counts[0]} elements")
                return True
        
        # Check recent action history
        if len(self.action_history) >= 3:
            recent_actions = [h["action"]["action_type"] for h in self.action_history[-3:]]
            
            # If doing the SAME global action repeatedly, we're stuck
            if len(set(recent_actions)) == 1 and recent_actions[0] == "global_action":
                logger.warning(f"⚠️ Same global action repeated 3 times: {recent_actions}")
                return True
            
            # If we're actively typing or clicking different things, NOT stuck
            if "type" in recent_actions or "scroll" in recent_actions:
                logger.info(f"✅ Active typing/scrolling: {recent_actions} - NOT stuck")
                return False
        
        # Only declare stuck if counter is REALLY high (6+)
        if self.stuck_counter >= 6:
            logger.error(f"❌ Truly stuck: {self.stuck_counter} steps in same state")
            return True
        
        return False
    
    def _get_wait_time_for_action(self, action_type: str) -> float:
        """Get wait time based on action type"""
        wait_times = {
            "click": 3.5,       # ✅ Increased from 2.5 — apps need time to load
            "global_action": 3.0,
            "type": 0.8,
            "scroll": 0.5,
            "wait": 0.1
        }
        return wait_times.get(action_type, 1.0)
    
    def _build_kb_params(self, task: MobileTaskRequest) -> Dict[str, str]:
        """
        Build a params dict for ActionKB selector interpolation.
        Maps coordinator extra_params + web_params into {app_name}, {input}, {recipient}, etc.
        """
        params: Dict[str, str] = {}
        goal_lower = task.ai_prompt.lower()
        
        # Extract app name from goal for {app_name} placeholder
        target = self._extract_target_app(task.ai_prompt)
        if target:
            params["app_name"] = target
        
        # ✅ FIX: Extract text from web_params (for mobile tasks with fill/type actions)
        # E.g., web_params: {"action": "fill", "text": "hello world"} → {input}: "hello world"
        if hasattr(task, 'web_params') and task.web_params:
            text_value = task.web_params.get("text")
            if text_value:
                params["input"] = str(text_value)
                logger.info(f"📋 Extracted text from web_params: input='{text_value}'")
        
        # Pass through coordinator extra_params
        if hasattr(task, 'extra_params') and task.extra_params:
            for key, value in task.extra_params.items():
                if key not in ('input_from', 'device_id', 'file_path', 'url'):
                    params[key] = str(value)
        
        return params
    
    def _tier1_deterministic_shortcuts(
        self,
        goal: str,
        ui_tree: SemanticUITree,
        device_state: str,
        stuck_counter: int
    ) -> Optional[Dict[str, Any]]:
        """
        TIER 1: Pure deterministic rules (no LLM, 0 tokens)
        
        Returns action dict if a rule matches, None otherwise (falls through to LLM)
        
        Rules (checked in priority order):
        1. In AURA app → BACK (exit immediately)
        2. Time picker with time set AND OK visible → Click OK (save action)
        3. Goal already achieved → Complete (simple goal patterns)
        4. Stuck for too long → Force HOME (recovery)
        5. Home screen stuck → Scroll UP to app drawer (recovery)
        """
        
        if not ui_tree or not ui_tree.elements:
            return None
        
        # ─────────────────────────────────────────────────────────────────
        # RULE 1: AURA EXIT
        # ─────────────────────────────────────────────────────────────────
        if device_state == "in_aura":
            logger.info(f"⚡ T1: AURA app detected → EXIT via BACK")
            return {
                "thought": "exit aura app",
                "action_type": "global_action",
                "global_action": "BACK"
            }
        
        # ─────────────────────────────────────────────────────────────────
        # RULE 2: TIME PICKER - OK BUTTON WITH TIME SET
        # ─────────────────────────────────────────────────────────────────
        if self._is_in_time_picker(ui_tree):
            # Look for OK button
            ok_button = None
            time_elements_visible = False
            
            for elem in ui_tree.elements:
                # Look for OK/CANCEL buttons
                if elem.type == "button" and (elem.text or "").upper() in ["OK", "CANCEL"]:
                    if (elem.text or "").upper() == "OK":
                        ok_button = elem
                
                # Look for time display elements (hour/minute)
                elem_text = (elem.text or "").strip()
                if elem_text and len(elem_text) <= 2 and elem_text.isdigit():
                    # Looks like a number (hour/minute)
                    time_elements_visible = True
            
            if ok_button and time_elements_visible:
                logger.info(f"⚡ T1: Time picker complete (time set + OK visible) → CLICK OK")
                return {
                    "thought": "click ok to confirm time",
                    "action_type": "click",
                    "element_id": ok_button.element_id
                }
        
        # ─────────────────────────────────────────────────────────────────
        # RULE 3: GOAL ALREADY ACHIEVED (simple patterns)
        # ─────────────────────────────────────────────────────────────────
        goal_lower = goal.lower()
        
        # Pattern: "Open X" / "Launch X" / "Navigate to X"
        if any(kw in goal_lower for kw in ["open", "launch", "start", "navigate"]):
            target = self._extract_target_app(goal)
            if target:
                app_name_lower = (ui_tree.app_name or "").lower()
                app_pkg_lower = (ui_tree.app_package or "").lower()
                app_identity = f"{app_pkg_lower} {app_name_lower}"
                
                if self._app_matches_target(target, app_identity):
                    logger.info(f"⚡ T1: Goal achieved → {target} is already open (pkg={app_pkg_lower})")
                    return {
                        "thought": f"goal achieved - {target} is open",
                        "action_type": "complete"
                    }
        
        # Pattern: "Set alarm to HH:MM PM/AM"
        # Extract the time from goal
        time_pattern = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)?', goal_lower)
        if time_pattern and "alarm" in goal_lower:
            target_time = time_pattern.group(0)
            # Check if this exact time is visible on screen
            for elem in ui_tree.elements:
                elem_text = (elem.text or "").lower()
                if target_time in elem_text or f"{target_time} alarm" in elem_text:
                    logger.info(f"⚡ T1: Goal achieved → Alarm {target_time} is set")
                    return {
                        "thought": f"goal achieved - alarm set to {target_time}",
                        "action_type": "complete"
                    }
        
        # Pattern: "Click X" or "Press X"
        if any(kw in goal_lower for kw in ["click", "press", "tap"]):
            # Extract what to click
            target = goal_lower.replace("click", "").replace("press", "").replace("tap", "").strip()
            target = target.rstrip(".")
            
            # Look for a button/element with matching text
            for elem in ui_tree.elements:
                elem_text = (elem.text or "").lower()
                elem_desc = (elem.content_description or "").lower()
                
                if target in elem_text or target in elem_desc:
                    if elem.clickable or elem.type == "button":
                        logger.info(f"⚡ T1: Goal achieved → {target} found and clickable")
                        return {
                            "thought": f"goal achieved - {target} already visible",
                            "action_type": "complete"
                        }
        
        # ─────────────────────────────────────────────────────────────────
        # RULE 4: STUCK RECOVERY - FORCE HOME
        # ─────────────────────────────────────────────────────────────────
        if stuck_counter >= 8:
            logger.info(f"⚡ T1: Stuck for {stuck_counter} steps → FORCE HOME")
            return {
                "thought": "stuck recovery - force home",
                "action_type": "global_action",
                "global_action": "HOME"
            }
        
        # ─────────────────────────────────────────────────────────────────
        # RULE 5: HOME SCREEN STUCK - SCROLL TO APP DRAWER
        # ─────────────────────────────────────────────────────────────────
        if device_state == "home_screen" and stuck_counter >= 5:
            if not self.app_drawer_attempted:
                logger.info(f"⚡ T1: Stuck on home screen → SCROLL UP to app drawer")
                self.app_drawer_attempted = True
                return {
                    "thought": "scroll to app drawer",
                    "action_type": "scroll",
                    "direction": "up",
                    "duration": 500
                }
        
        # ─────────────────────────────────────────────────────────────────
        # NO TIER 1 MATCH: Fall through to LLM
        # ─────────────────────────────────────────────────────────────────
        return None
    
    async def _think_and_decide(
        self,
        goal: str,
        observation: str,
        thought_history: List[str],
        step_number: int
    ) -> tuple[str, Optional[Dict]]:
        """Tier 3: Minimal LLM prompt — only fires for novel screens."""
        
        history_context = ""
        if thought_history:
            history_context = "Prior: " + " → ".join(thought_history[-3:])
        
        # Get exact content from coordinator params
        exact_content_context = ""
        if self.current_task:
            exact_content_context = self._get_content_from_task_params(self.current_task)
        
        # ── Build compact blacklist string ──
        bl = f"Skip IDs {sorted(list(self.failed_elements))}. " if self.failed_elements else ""

        prompt = f"""Goal: {goal}
State: {self.device_state} | Step {step_number}{' | ' + exact_content_context.strip() if exact_content_context.strip() else ''}
{bl}
Screen:
{observation}
{history_context}
IMPORTANT: If your goal was to click/press a button and a NEW screen has appeared (different fields/layout than before), the button was ALREADY CLICKED — respond with "complete".
IMPORTANT: If the screen still shows the SAME form/layout (same fields, compose screen, etc.), the button was NOT successfully clicked — try clicking it again or scroll to find it.
If the Send button is not visible, it may be hidden — try scrolling UP on the form area, or look for an element with description containing "Send".
A TEXTFIELD named "Compose email" is the email BODY, NOT the compose button.
Pick ONE action. If goal is already done, use "complete".
JSON format — no markdown:
- Click: {{"thought":"why","action_type":"click","element_id":N}}
- Type:  {{"thought":"why","action_type":"type","element_id":N,"text":"..."}}
- Scroll: {{"thought":"why","action_type":"scroll","direction":"up"}}
- Back/Home: {{"thought":"why","action_type":"global_action","global_action":"BACK"}}
- Done: {{"thought":"why","action_type":"complete"}}"""
        
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Android automation agent. ONE JSON action, no markdown. If the screen already shows the RESULT of the goal (e.g. compose form visible after 'click compose'), use complete."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,
                max_tokens=200
            )
            
            response_text = response.choices[0].message.content.strip()
            logger.debug(f"🤖 Raw LLM response:\n{response_text}")

            self._track_llm_usage(response)
            
            json_str = self._extract_json_from_response(response_text)
            
            if not json_str:
                logger.error(f"❌ No JSON found in LLM response")
                return ("Failed to parse response", None)
            
            action_json = json.loads(json_str)
            thought = action_json.get("thought", "No thought provided")
            
            return (thought, action_json)
        
        except Exception as e:
            logger.error(f"❌ LLM error: {e}", exc_info=True)
            return (f"Error: {e}", None)
    
    def _extract_json_from_response(self, text: str) -> Optional[str]:
        """Extract JSON from LLM response"""
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            return match.group(0).strip()
        
        return None
    
    def _json_to_ui_action(self, action_json: Dict) -> UIAction:
        """Convert JSON to UIAction"""
        action_type = action_json.get("action_type")
        
        if not action_type:
            raise ValueError("Missing action_type")
        
        kwargs = {
            "action_type": action_type,
            "text": action_json.get("text"),
            "duration": action_json.get("duration", 1000),
        }
        
        if action_type in ["click", "type"]:
            element_id = action_json.get("element_id")
            if element_id is not None:
                try:
                    kwargs["element_id"] = int(element_id)
                except (ValueError, TypeError):
                    logger.error(f"❌ Invalid element_id: {element_id}")
                    kwargs["action_type"] = "scroll"
                    kwargs["direction"] = "down"
                    return UIAction(**kwargs)
            else:
                logger.error(f"❌ No element_id for {action_type}")
                kwargs["action_type"] = "scroll"
                kwargs["direction"] = "down"
                return UIAction(**kwargs)
        
        direction = action_json.get("direction")
        if direction:
            kwargs["direction"] = direction
        
        global_action = action_json.get("global_action")
        if global_action:
            kwargs["global_action"] = global_action
        
        return UIAction(**kwargs)
    
    async def _fetch_ui_tree_from_device(self) -> Optional[SemanticUITree]:
        """Fetch UI tree from device"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.backend_url}/device/{self.device_id}/ui-tree",
                    timeout=5.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if not data:
                        logger.error(f"❌ Empty UI tree response")
                        return None
                    
                    if data.get("_synthetic"):
                        logger.warning(f"⚠️ Received synthetic screen - ignoring")
                        return None
                    
                    return SemanticUITree(**data)
                else:
                    logger.error(f"❌ HTTP {response.status_code}")
                    return None
        
        except Exception as e:
            logger.error(f"❌ Error fetching UI tree: {e}")
            return None
    
    async def _fetch_ui_tree_with_retries(
        self,
        action_type: str,
        max_attempts: int = 3,
        retry_delay: float = 2.0
    ) -> Optional[SemanticUITree]:
        """
        ✅ CRITICAL: Fetch UI tree with retries for incomplete UIs
        
        Apps take time to load! If we get a partial UI (< 5 elements),
        wait longer and try again. This prevents premature verification.
        
        Args:
            action_type: Type of action that was just executed
            max_attempts: Max number of fetch attempts (default 3)
            retry_delay: Seconds to wait between retries (default 2.0)
        
        Returns:
            SemanticUITree or None
        """
        for attempt in range(max_attempts):
            ui_tree = await self._fetch_ui_tree_from_device()
            
            if not ui_tree:
                if attempt < max_attempts - 1:
                    logger.warning(f"⚠️ Attempt {attempt + 1}: No UI tree - retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"❌ Failed to get UI tree after {max_attempts} attempts")
                    return None
            
            # Check if UI is fully loaded
            element_count = len(ui_tree.elements)
            
            # For clicks, we expect a full UI (>= 5 elements)
            # For other actions, minimal validation
            min_elements = 5 if action_type == "click" else 2
            
            if element_count >= min_elements:
                if attempt > 0:
                    logger.info(f"✅ UI fully loaded on attempt {attempt + 1} ({element_count} elements)")
                return ui_tree
            
            # UI incomplete - wait and retry
            if attempt < max_attempts - 1:
                logger.warning(f"⚠️ Attempt {attempt + 1}: UI incomplete ({element_count} elements, need {min_elements}+)")
                logger.warning(f"⏳ Waiting {retry_delay}s for app to finish loading...")
                await asyncio.sleep(retry_delay)
            else:
                logger.warning(f"⚠️ UI still incomplete after {max_attempts} attempts ({element_count} elements)")
                logger.warning(f"⚠️ Proceeding anyway - app may be slow")
                return ui_tree
        
        return None
    
    async def _execute_action_on_device(self, action: UIAction) -> ActionResult:
        """Execute action on device"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.backend_url}/device/{self.device_id}/execute-action",
                    json=action.model_dump(),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return ActionResult(**data)
                else:
                    return ActionResult(
                        action_id=action.action_id,
                        success=False,
                        error=f"HTTP {response.status_code}",
                        execution_time_ms=0
                    )
        
        except Exception as e:
            logger.error(f"❌ Error executing action: {e}")
            return ActionResult(
                action_id=action.action_id,
                success=False,
                error=str(e),
                execution_time_ms=0
            )

    def _track_llm_usage(self, response: Any) -> None:
        """Track LLM token usage when available"""
        try:
            usage = getattr(response, "usage", None) or getattr(response.choices[0], "usage", None)
            if usage:
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))
                self.token_usage["prompt"] += prompt_tokens
                self.token_usage["completion"] += completion_tokens
                self.token_usage["total"] += total_tokens
            self.total_llm_calls += 1
        except Exception as e:
            logger.warning(f"⚠️ Failed to read LLM usage: {e}")

    def _update_ui_stats(self, ui_tree: SemanticUITree) -> None:
        """Track UI element statistics"""
        try:
            element_count = len(ui_tree.elements)
            self.total_ui_elements_seen += element_count
            self.max_ui_elements_seen = max(self.max_ui_elements_seen, element_count)
            self.ui_samples_count += 1
        except Exception as e:
            logger.warning(f"⚠️ Failed to update UI stats: {e}")
    
    def _build_result(
        self,
        task_id: str,
        status: str,
        steps: int,
        actions: List[UIAction],
        elapsed: float,
        error: Optional[str] = None,
        completion_reason: Optional[str] = None
    ) -> MobileTaskResult:
        """Build task result"""
        self._log_task_metrics(status=status, steps=steps, elapsed=elapsed, actions=actions)
        return MobileTaskResult(
            task_id=task_id,
            status=status,
            steps_taken=steps,
            actions_executed=actions,
            execution_time_ms=int(elapsed * 1000),
            error=error,
            completion_reason=completion_reason,
            token_usage=dict(self.token_usage),
            llm_calls=self.total_llm_calls
        )
    
    def _build_error_result(self, task_id: str, error: str) -> MobileTaskResult:
        """Build error result"""
        self._log_task_metrics(status="failed", steps=0, elapsed=0.0, actions=[])
        return MobileTaskResult(
            task_id=task_id,
            status="failed",
            steps_taken=0,
            actions_executed=[],
            execution_time_ms=0,
            error=error,
            token_usage=dict(self.token_usage),
            llm_calls=self.total_llm_calls
        )

    def _log_task_metrics(self, status: str, steps: int, elapsed: float, actions: List[UIAction]) -> None:
        """Log token usage and complexity estimates"""
        avg_elements = (self.total_ui_elements_seen / self.ui_samples_count) if self.ui_samples_count else 0
        logger.info("\n" + "=" * 70)
        logger.info("📊 TASK METRICS")
        logger.info("=" * 70)
        logger.info(f"Status: {status}")
        logger.info(f"Steps taken: {steps}")
        logger.info(f"Actions executed: {len(actions)}")
        logger.info(f"Elapsed (s): {elapsed:.2f}")
        logger.info(f"LLM calls: {self.total_llm_calls}")
        logger.info(
            f"Tokens used — prompt: {self.token_usage['prompt']}, completion: {self.token_usage['completion']}, total: {self.token_usage['total']}"
        )
        logger.info(
            f"UI stats — samples: {self.ui_samples_count}, avg elements: {avg_elements:.2f}, max elements: {self.max_ui_elements_seen}"
        )
        # Tier breakdown
        t = self.tier_stats
        total_decisions = sum(v for k, v in t.items() if not k.startswith('_'))
        logger.info(
            f"Tier breakdown — T1: {t.get('tier1',0)}, T2A: {t.get('tier2a',0)}, "
            f"T2B: {t.get('tier2b',0)}, T3/LLM: {t.get('tier3_llm',0)}  "
            f"(total decisions: {total_decisions})"
        )
        if total_decisions > 0:
            saved = t.get('tier1',0) + t.get('tier2a',0) + t.get('tier2b',0)
            logger.info(
                f"Token savings — {saved}/{total_decisions} decisions resolved WITHOUT LLM "
                f"({saved/total_decisions*100:.0f}% free)"
            )
        # ActionKB stats
        logger.info(f"ActionKB stats: {self.action_kb.get_stats()}")
        logger.info("=" * 70 + "\n")


# Backward compatibility
class MobileStrategy(MobileReActStrategy):
    pass


# Integration function
async def execute_mobile_task(
    task: Dict[str, Any],
    device_id: str = "emulator-5554"
) -> ExecutionResult:
    """Execute mobile task - called by ExecutionAgent"""
    
    try:
        mobile_task = MobileTaskRequest(
            task_id=task.get("task_id"),
            ai_prompt=task.get("ai_prompt"),
            device_id=device_id,
            session_id=task.get("session_id", "default"),
            context=task.get("extra_params", {}),
            extra_params=task.get("extra_params", {}),
            max_steps=15,
            timeout_seconds=task.get("timeout_seconds", 30)
        )
        
        strategy = MobileStrategy(device_id)
        result = await strategy.execute_task(mobile_task)
        
        return ExecutionResult(
            task_id=result.task_id,
            status="success" if result.status == "success" else "failed",
            content=result.completion_reason or result.error,
            error=result.error
        )
    
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        return ExecutionResult(
            task_id=task.get("task_id", "unknown"),
            status="failed",
            error=str(e)
        )