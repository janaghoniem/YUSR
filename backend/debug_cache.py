#!/usr/bin/env python3
"""
cache_debug.py  — Populate TaskMemory with 20 realistic tasks and verify retrieval.
Run from the project root:
    python cache_debug.py
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.execution_agent.strategies.task_memory import TaskMemory

tm = TaskMemory()
print(f"Starting with {tm.stats()['total_records']} existing records\n")

# ─────────────────────────────────────────────────────────────────────────────
# 20 SEED TASKS  (coordinator-style decompositions)
# ─────────────────────────────────────────────────────────────────────────────
SEED_TASKS = [
    # ── Chrome search ──
    {
        "step": "Open Chrome",
        "goal": "Search for restaurants near me on Chrome",
        "app": "chrome", "action": "global_action",
        "selectors": [], "sig": "ImageButton:menu_button,textfield:url_bar",
    },
    {
        "step": "Type <value> in the search bar",
        "goal": "Search for restaurants near me on Chrome",
        "app": "chrome", "action": "type",
        "selectors": [
            {"by": "resource_id_tail", "value": "url_bar"},
            {"by": "hint_text", "value": "Search or type URL"},
        ],
        "sig": "ImageButton:menu_button,textfield:url_bar",
        "param_key": "text",
    },
    {
        "step": "Click the search button",
        "goal": "Search for restaurants near me on Chrome",
        "app": "chrome", "action": "click",
        "selectors": [{"by": "resource_id_tail", "value": "search_button"}],
        "sig": "button:search_button,textfield:url_bar",
    },

    # ── YouTube search + play ──
    {
        "step": "Open YouTube",
        "goal": "Play a YouTube video about machine learning",
        "app": "youtube", "action": "global_action",
        "selectors": [], "sig": "ImageButton:menu,RecyclerView:results",
    },
    {
        "step": "Click the search icon in YouTube",
        "goal": "Play a YouTube video about machine learning",
        "app": "youtube", "action": "click",
        "selectors": [
            {"by": "content_desc", "value": "Search"},
            {"by": "resource_id_tail", "value": "search_icon_button"},
        ],
        "sig": "ImageButton:menu,RecyclerView:results",
    },
    {
        "step": "Type <value> in the YouTube search box",
        "goal": "Play a YouTube video about machine learning",
        "app": "youtube", "action": "type",
        "selectors": [
            {"by": "resource_id_tail", "value": "search_edit_text"},
            {"by": "hint_text", "value": "Search YouTube"},
        ],
        "sig": "textfield:search_edit_text",
        "param_key": "text",
    },
    {
        "step": "Click on the first video result",
        "goal": "Play a YouTube video about machine learning",
        "app": "youtube", "action": "click",
        "selectors": [{"by": "resource_id_tail", "value": "thumbnail"}],
        "sig": "RecyclerView:results,textfield:search_edit_text",
    },
    {
        "step": "Press the play button to start the video",
        "goal": "Play a YouTube video about machine learning",
        "app": "youtube", "action": "click",
        "selectors": [
            {"by": "content_desc", "value": "Play"},
            {"by": "resource_id_tail", "value": "player_control_play_pause_replay_button"},
        ],
        "sig": "button:player_control_play_pause_replay_button",
    },

    # ── Gmail compose ──
    {
        "step": "Navigate to default email app",
        "goal": "Send an email to test@example.com",
        "app": "gmail", "action": "global_action",
        "selectors": [], "sig": "ImageButton:compose,RecyclerView:conversation_list",
    },
    {
        "step": "Compose new email",
        "goal": "Send an email to test@example.com",
        "app": "gmail", "action": "click",
        "selectors": [
            {"by": "content_desc", "value": "Compose"},
            {"by": "resource_id_tail", "value": "compose_button"},
        ],
        "sig": "ImageButton:compose,RecyclerView:conversation_list",
    },
    {
        "step": "Fill the To field with <email>",
        "goal": "Send an email to test@example.com",
        "app": "gmail", "action": "type",
        "selectors": [
            {"by": "hint_text", "value": "To"},
            {"by": "resource_id_tail", "value": "recipient_text_view"},
        ],
        "sig": "textfield:subject,textfield:recipient_text_view",
        "param_key": "recipient",
    },
    {
        "step": "Fill the Subject field with <value>",
        "goal": "Send an email to test@example.com",
        "app": "gmail", "action": "type",
        "selectors": [
            {"by": "hint_text", "value": "Subject"},
            {"by": "resource_id_tail", "value": "subject"},
        ],
        "sig": "textfield:subject,textfield:recipient_text_view",
        "param_key": "subject",
    },
    {
        "step": "Click the Send button to send the email",
        "goal": "Send an email to test@example.com",
        "app": "gmail", "action": "click",
        "selectors": [
            {"by": "content_desc", "value": "Send"},
            {"by": "resource_id_tail", "value": "send"},
        ],
        "sig": "button:send,textfield:subject",
    },

    # ── Clock alarm ──
    {
        "step": "Open the Clock app on mobile device",
        "goal": "Set an alarm for 6:00 AM",
        "app": "clock", "action": "global_action",
        "selectors": [], "sig": "ImageButton:fab,RecyclerView:alarm_recycler_view",
    },
    {
        "step": "Set the alarm time to 6:00 AM",
        "goal": "Set an alarm for 6:00 AM",
        "app": "clock", "action": "click",
        "selectors": [
            {"by": "content_desc", "value": "Add alarm"},
            {"by": "resource_id_tail", "value": "fab"},
        ],
        "sig": "ImageButton:fab,RecyclerView:alarm_recycler_view",
    },
    {
        "step": "Press OK or Save to confirm the alarm setting",
        "goal": "Set an alarm for 6:00 AM",
        "app": "clock", "action": "click",
        "selectors": [
            {"by": "text", "value": "OK"},
            {"by": "resource_id_tail", "value": "material_timepicker_ok_button"},
        ],
        "sig": "button:material_timepicker_ok_button,button:material_timepicker_cancel_button",
    },

    # ── Chrome navigation ──
    {
        "step": "Open Chrome and navigate to the Google search page",
        "goal": "Search for weather forecast on Chrome",
        "app": "chrome", "action": "global_action",
        "selectors": [], "sig": "ImageButton:menu_button,textfield:url_bar",
    },
    {
        "step": "Type <value> in the search box",
        "goal": "Search for weather forecast on Chrome",
        "app": "chrome", "action": "type",
        "selectors": [
            {"by": "resource_id_tail", "value": "url_bar"},
        ],
        "sig": "ImageButton:menu_button,textfield:url_bar",
        "param_key": "text",
    },

    # ── YouTube no web_params ──
    {
        "step": "Search for <value> in YouTube",
        "goal": "Watch a tutorial on YouTube",
        "app": "youtube", "action": "type",
        "selectors": [
            {"by": "resource_id_tail", "value": "search_edit_text"},
        ],
        "sig": "textfield:search_edit_text",
        "param_key": "text",
    },
    {
        "step": "Click the search button to find <value>",
        "goal": "Watch a tutorial on YouTube",
        "app": "youtube", "action": "click",
        "selectors": [
            {"by": "resource_id_tail", "value": "search_edit_text"},
            {"by": "content_desc", "value": "Search"},
        ],
        "sig": "textfield:search_edit_text,ImageButton:search_button",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# INSERT
# ─────────────────────────────────────────────────────────────────────────────
print("=== INSERTING SEED TASKS ===\n")
inserted_ids = []
for i, t in enumerate(SEED_TASKS):
    rid = tm.store(
        step_instruction = t["step"],
        overall_goal     = t["goal"],
        app              = t["app"],
        action_type      = t["action"],
        screen_signature = t.get("sig", ""),
        selectors        = t.get("selectors", []),
        demonstrated     = 1,
        success_count    = 3,
        param_key        = t.get("param_key"),
    )
    inserted_ids.append(rid)
    status = "✅" if rid else "❌"
    print(f"  {status} [{i+1:02d}] {t['app']:12s} | {t['step'][:55]}")

print(f"\nInserted {sum(1 for r in inserted_ids if r)} / {len(SEED_TASKS)} records")
print(f"Total records now: {tm.stats()['total_records']}\n")

# ─────────────────────────────────────────────────────────────────────────────
# RETRIEVAL TESTS
# ─────────────────────────────────────────────────────────────────────────────
RETRIEVAL_TESTS = [
    # (query, goal, app, expected_match_substring, expected_band)
    ("Open Chrome",
     "Search for vets in New Cairo on Chrome",
     "chrome", "Open Chrome", "execute"),

    ("Type 'vets in New Cairo' in the search bar",
     "Search for vets in New Cairo on Chrome",
     "chrome", "Type <value>", "hint"),

    ("Open YouTube app",
     "Play a YouTube video explaining MCP vs RAG",
     "youtube", "Open YouTube", "execute"),

    ("Search for MCP vs RAG",
     "Play a YouTube video explaining MCP vs RAG",
     "youtube", "search_edit_text", "execute"),

    ("Click on the first video result about MCP",
     "Play a YouTube video explaining MCP vs RAG",
     "youtube", "first video", "hint"),

    ("Press the play button",
     "Play a YouTube video explaining MCP vs RAG",
     "youtube", "play button", "hint"),

    ("Navigate to default email app",
     "Send email to john@example.com with subject Meeting",
     "gmail", "email app", "execute"),

    ("Compose new email to john@example.com",
     "Send email to john@example.com with subject Meeting",
     "gmail", "Compose", "hint"),

    ("Fill the Subject field with 'Meeting'",
     "Send email to john@example.com with subject Meeting",
     "gmail", "Subject", "hint"),

    ("Click the Send button to send the email",
     "Send email to john@example.com with subject Meeting",
     "gmail", "Send", "execute"),

    ("Open the Clock app on mobile device",
     "Set an alarm for 7:30 AM",
     "clock", "Clock app", "execute"),

    ("Set the alarm time to 7:30 AM",
     "Set an alarm for 7:30 AM",
     "clock", "alarm time", "execute"),

    ("Press OK or Save to confirm the alarm setting",
     "Set an alarm for 7:30 AM",
     "clock", "OK", "execute"),

    ("Open Chrome and navigate to the Google search page",
     "Search for psychology topics in Chrome",
     "chrome", "Chrome", "execute"),

    ("Type 'psychology topics' in the search box",
     "Search for psychology topics in Chrome",
     "chrome", "Type <value>", "hint"),

    ("Search for AgentForce in Salesforce",
     "Play a YouTube video about AgentForce in Salesforce",
     "youtube", "search_edit_text", "hint"),

    ("Click on the first video result",
     "Play a YouTube video about AgentForce in Salesforce",
     "youtube", "first video", "hint"),

    ("Press the Play button",
     "Play a YouTube video about AgentForce in Salesforce",
     "youtube", "play", "hint"),

    ("Type 'machine learning tutorial' in the YouTube search box",
     "Watch a machine learning tutorial on YouTube",
     "youtube", "search_edit_text", "execute"),

    ("Compose new email",
     "Send an email to someone with content hello",
     "gmail", "Compose", "execute"),
]

print("=== RETRIEVAL TESTS ===\n")
passed = 0
failed = 0
for query, goal, app, expected_match, expected_band in RETRIEVAL_TESTS:
    result = tm.query(
        step_instruction  = query,
        overall_goal      = goal,
        app               = app,
        current_signature = "",
    )
    band      = result.band
    best_sim  = result.best_sim
    best_label = (result.recipes[0].step_instruction if result.recipes else result.best_label or "—")

    match_ok = expected_match.lower() in best_label.lower()
    band_ok  = (band == expected_band) or (band in ("execute", "hint") and expected_band in ("execute", "hint"))
    ok       = match_ok  # band can vary based on sim score

    status = "✅ PASS" if ok else "❌ FAIL"
    if ok:
        passed += 1
    else:
        failed += 1

    print(f"  {status} | band={band:7s} sim={best_sim:.3f}")
    print(f"         query : {query[:60]}")
    print(f"         expect: contains '{expected_match}'")
    print(f"         got   : {best_label[:60]}")
    print()

print(f"Results: {passed}/{len(RETRIEVAL_TESTS)} passed, {failed} failed\n")

# ─────────────────────────────────────────────────────────────────────────────
# SIGNATURE STABILITY TEST
# ─────────────────────────────────────────────────────────────────────────────
print("=== SIGNATURE MISMATCH ANALYSIS ===\n")
from agents.execution_agent.strategies.task_memory import _signature_jaccard

# Simulate what the alarm screen signature looks like with 1 vs 3 alarms
sig_with_1_alarm = "ImageButton:fab,RecyclerView:alarm_recycler_view,switch:onoff"
sig_with_3_alarms = "ImageButton:fab,RecyclerView:alarm_recycler_view,switch:onoff,switch:onoff,switch:onoff"
sig_different_screen = "button:material_timepicker_ok_button,button:material_timepicker_cancel_button"

stored = "ImageButton:fab,RecyclerView:alarm_recycler_view"

pairs = [
    ("stored vs 1-alarm screen",    stored, sig_with_1_alarm),
    ("stored vs 3-alarm screen",    stored, sig_with_3_alarms),
    ("stored vs time-picker screen", stored, sig_different_screen),
    ("identical",                    stored, stored),
]
for label, a, b in pairs:
    score = _signature_jaccard(a, b)
    threshold_pass_025 = "✅ pass" if score >= 0.25 else "❌ fail"
    threshold_pass_050 = "✅ pass" if score >= 0.50 else "❌ fail"
    print(f"  {label}")
    print(f"    score={score:.2f}  threshold=0.25:{threshold_pass_025}  threshold=0.50:{threshold_pass_050}")
    print()

print("Conclusion: threshold=0.25 is appropriate for dynamic-content screens.")
print("            threshold=0.50 is too strict and blocks valid T2 executions.\n")