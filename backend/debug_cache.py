#!/usr/bin/env python3
"""
debug_cache.py  —  AURA Cache Diagnostic v3
============================================
Run from backend root:

    python debug_cache.py                    # all 55 cases, failures only
    python debug_cache.py --verbose          # all cases with top-5 hits
    python debug_cache.py --app clock        # just clock cases
    python debug_cache.py --tasks            # task-level action debugging
    python debug_cache.py --tasks --verbose  # full action details

Changes from v2:
  - keyword_rerank fixed: extracts step from composite doc correctly,
    uses dataclasses.replace so RecipeStep.similarity is mutated properly
  - Added --tasks mode: simulates what Tier 2 would actually DO for
    real coordinator task payloads (what element, what action, what text)
  - Reports before/after rerank scores so you can see the delta
"""

import argparse
import dataclasses
import json
import os
import re
import re as _re_goal
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

CHROMA_PATH = os.path.expanduser(
    "~/Development/AURA/backend/agents/execution_agent/strategies/task_memory_db"
)
EMBED_MODEL       = "BAAI/bge-small-en-v1.5"
THRESHOLD_EXECUTE = 0.85
THRESHOLD_HINT    = 0.65
STOPWORDS = {
    'the', 'a', 'an', 'to', 'on', 'in', 'of', 'at', 'for', 'and', 'or',
    'with', 'by', 'is', 'it', 'please', 'now', 'then', 'next',
}


# ── normalize for overlap (parameter-aware) ────────────────────────────────
def normalize_step_for_overlap(text: str) -> str:
    """
    Replace volatile values (numbers, emails) with placeholders.
    Treats "Set hours to 7" and "Set hours to 3" as identical after normalization.
    Only used for overlap calculation; does not modify stored text.
    """
    text = re.sub(r'\b\d+(?::\d+)?(?:\s*(?:am|pm))?\b', 'NUM', text, flags=re.I)
    text = re.sub(r'\b[\w.%+-]+@[\w.-]+\.[a-zA-Z]{2,}\b', 'EMAIL', text)
    return text


def _normalize_goal_for_embedding(goal: str) -> str:
    """Must be identical to task_memory.py's goal embedding normalization."""
    goal = _re_goal.sub(
        r'\b[\w.+%-]{2,}@[\w.-]+\.[a-zA-Z]{2,}\b',
        'EMAIL_ADDR',
        goal,
    )
    goal = _re_goal.sub(
        r'\b\d{1,2}:\d{2}\s*(?:am|pm)\b',
        'TIME_VALUE',
        goal,
        flags=_re_goal.IGNORECASE,
    )
    goal = _re_goal.sub(r'\b\d{1,2}:\d{2}\b', 'TIME_VALUE', goal)
    goal = _re_goal.sub(r'\b\d{1,2}\s*(?:AM|PM|am|pm)\b', 'TIME_VALUE', goal)
    goal = _re_goal.sub(r'https?://\S+', 'URL', goal)
    goal = _re_goal.sub(r'\b\d{7,}\b', 'PHONE_NUM', goal)
    return goal.strip()

# ── composite document — must match task_memory.py exactly ────────────────
def build_composite_document(overall_goal: str, step_instruction: str) -> str:
    """
    Must match task_memory.py _build_composite_document exactly.
    Goal uses _normalize_goal_for_embedding (TIME_VALUE, EMAIL_ADDR).
    Step uses normalize_step_for_overlap (NUM for any digit).
    """
    g = _normalize_goal_for_embedding((overall_goal or "").strip())
    s = normalize_step_for_overlap((step_instruction or "").strip())
    return f"{s} [SEP] {s} [SEP] {g}" if g else s


# ── keyword rerank — parameter-aware with goal overlap ────────────────────
def keyword_rerank(query_step: str, pairs: list, query_goal: str = "") -> list:
    """
    Rerank by blending three components:
      1. raw cosine similarity (0.6 weight)
      2. step-level keyword overlap (parameter-normalized, 0.2 weight)
      3. goal-level keyword overlap (parameter-normalized, 0.2 weight)
    
    pairs = list of (raw_cosine_sim: float, metadata_dict: dict)
    Returns same format, resorted by blended score.
    """
    # Normalize query step and goal for overlap calculation
    q_step_norm = normalize_step_for_overlap(query_step)
    q_goal_norm = normalize_step_for_overlap(query_goal) if query_goal else ""
    
    # Extract keywords from normalized query
    q_words_step = set(re.findall(r'\b\w+\b', q_step_norm.lower())) - STOPWORDS
    q_words_goal = set(re.findall(r'\b\w+\b', q_goal_norm.lower())) - STOPWORDS if q_goal_norm else set()
    q_symbols = set(re.findall(r'[+#@&]', query_step))
    q_words_step.update(q_symbols)
    
    out = []
    for sim, meta in pairs:
        step_text = meta.get("step_instruction", "")
        goal_text = meta.get("overall_goal", "")
        
        # Normalize stored step and goal
        step_norm = normalize_step_for_overlap(step_text)
        goal_norm = normalize_step_for_overlap(goal_text)
        
        # Extract keywords from normalized stored step and goal
        s_words_step = set(re.findall(r'\b\w+\b', step_norm.lower())) - STOPWORDS
        s_words_goal = set(re.findall(r'\b\w+\b', goal_norm.lower())) - STOPWORDS
        s_symbols = set(re.findall(r'[+#@&]', step_text))
        s_words_step.update(s_symbols)
        
        # Compute overlaps
        step_overlap = len(q_words_step & s_words_step) / max(len(q_words_step), 1)
        goal_overlap = len(q_words_goal & s_words_goal) / max(len(q_words_goal), 1) if q_words_goal else 0.0
        
        # Blend: 0.6 raw + 0.3 step_overlap + 0.1 goal_overlap
        blended = sim * 0.6 + step_overlap * 0.3 + goal_overlap * 0.1

        # Goal-context penalty for dangerous wrong-purpose matches.
        if (
            len(q_words_goal) >= 3
            and goal_overlap < 0.12
            and sim > 0.85
        ):
            blended = blended * 0.75
        
        # Hard penalty only for semantically weak zero-overlap matches
        if step_overlap == 0 and len(q_words_step) >= 2 and sim < 0.82:
            blended = min(blended, 0.65)
        
        out.append((blended, sim, meta))  # keep raw_sim for reporting
    return sorted(out, key=lambda x: -x[0])


def band_label(sim: float) -> str:
    if sim >= THRESHOLD_EXECUTE: return "EXECUTE ✅"
    if sim >= THRESHOLD_HINT:    return "HINT    🟡"
    return "MISS    ❌"


def band_str(sim: float) -> str:
    if sim >= THRESHOLD_EXECUTE: return "execute"
    if sim >= THRESHOLD_HINT:    return "hint"
    return "miss"


# ── 55 similarity test cases ──────────────────────────────────────────────
@dataclass
class Case:
    label:            str
    step_instruction: str
    overall_goal:     str
    app:              str
    expected_band:    str   # "execute" | "hint" | "miss"
    note:             str = ""

CASES: List[Case] = [

    # ── CLOCK (12) ───────────────────────────────────────────────────────
    Case("clock-01 exact: + icon",
         "click on the + icon on the screen.",
         "Set an alarm for 7:00 AM", "clock", "execute",
         "Nearly verbatim in dataset"),
    Case("clock-02 synonym: plus icon",
         "Click on the plus icon",
         "Set an alarm for 7:00 AM", "clock", "execute"),
    Case("clock-03 set hours",
         "Set the hours to 7",
         "Set an alarm for 7:00 AM", "clock", "execute",
         "Direct action match"),
    Case("clock-04 set minutes",
         "Set the minutes to 30",
         "Set an alarm for 7:30 AM", "clock", "execute"),
    Case("clock-05 click AM",
         "click on the AM.",
         "Set an alarm for 7:00 AM", "clock", "execute"),
    Case("clock-06 save alarm",
         "Save the Alarm",
         "Set an alarm for 7:00 AM", "clock", "execute"),
    Case("clock-07 click OK",
         "Click on OK",
         "Set an alarm for 7:00 AM", "clock", "execute"),
    Case("clock-08 stopwatch icon",
         "Click on the stopwatch icon",
         "Start the stopwatch in the clock app", "clock", "execute"),
    Case("clock-09 scroll minute dial",
         "Scroll down the minute dial to set the minutes to 15",
         "Prepon the alarm of 4:30 AM by 15 minutes", "clock", "execute"),
    Case("clock-10 navigate alarm section",
         "Go to the alarm section",
         "Set an alarm for 7:00 AM", "clock", "hint",
         "Navigational step"),
    Case("clock-11 coordinator: set time",
         "Set the alarm time to 4:35 AM",
         "Set an alarm for 4:35 AM", "clock", "hint"),
    Case("clock-12 coordinator: open clock app",
         "Open the Clock app on mobile device",
         "Set an alarm for 4:35 AM", "clock", "miss",
         "No open-app steps in clock dataset"),

    # ── GMAIL (10) ───────────────────────────────────────────────────────
    Case("gmail-01 exact: enter email ID",
         "enter the email ID akashgahlot@google.com",
         "Send an email to akashgahlot@google.com", "gmail", "execute"),
    Case("gmail-02 exact: type recipient",
         "Type lucaskramer733@gmail.com on the screen.",
         "Send an email to lucaskramer733@gmail.com", "gmail", "execute"),
    Case("gmail-03 exact: send icon lowercase",
         "click on the send icon",
         "Send an email to hayaadawy@icloud.com", "gmail", "execute"),
    Case("gmail-04 exact: send button top right",
         "Click on the Send button at the top right of the screen",
         "Send an email to hayaadawy@icloud.com", "gmail", "execute"),
    Case("gmail-05 exact: open mail",
         "Open the Mail from dbwscratch.test.id3@gmail.com",
         "Reply to email from dbwscratch.test.id3@gmail.com", "gmail", "execute"),
    Case("gmail-06 exact: body text",
         "Type Acknowledge in the Mail body",
         "Reply to email with content Acknowledge", "gmail", "execute"),
    Case("gmail-07 hint: compose button",
         "Click on Compose at the bottom right corner",
         "Send an email to dbwscratch.test.id2@gmail.com with subject Transfer.", "gmail", "hint"),
    Case("gmail-08 hint: fill To field",
         "Fill the To field with hayaadawy@icloud.com",
         "Send an email to hayaadawy@icloud.com with subject hello world", "gmail", "hint"),
    Case("gmail-09 hint: reply button",
         "Click on the reply button next to the recipient mail id",
         "Reply to email from dbwscratch.test.id3@gmail.com", "gmail", "execute",
         "Exact phrase — should be execute"),
    Case("gmail-10 miss: + icon for email",
         "click on the + icon on the screen.",
         "Send an email to someone", "gmail", "miss",
         "Clock action — MISS after keyword rerank"),

    # ── CALENDAR (5) ─────────────────────────────────────────────────────
    Case("cal-01 new event FAB",
         "Click on the + button to create a new event",
         "Create a calendar event for tomorrow at 3 PM", "calendar", "hint"),
    Case("cal-02 event title",
         "Type the event title in the title field",
         "Create a calendar event titled Meeting", "calendar", "hint"),
    Case("cal-03 save event",
         "Click Save to save the event",
         "Create a calendar event for tomorrow", "calendar", "hint"),
    Case("cal-04 select date",
         "Click on the date field to select a date",
         "Schedule an event for next Monday", "calendar", "hint"),
    Case("cal-05 miss: wrong app",
         "click on the + icon on the screen.",
         "Set an alarm for 7:00 AM", "calendar", "miss",
         "Clock query against calendar"),

    # ── CONTACTS (4) ─────────────────────────────────────────────────────
    Case("contacts-01 add new",
         "Click on the Add Contact button",
         "Add a new contact named John", "contacts", "hint"),
    Case("contacts-02 type name",
         "Type the contact name in the name field",
         "Add contact John Smith", "contacts", "hint"),
    Case("contacts-03 save",
         "Click Save to save the contact",
         "Add a new contact", "contacts", "hint"),
    Case("contacts-04 search",
         "Search for a contact in the search bar",
         "Find contact named Alice", "contacts", "hint"),

    # ── MAPS (4) ─────────────────────────────────────────────────────────
    Case("maps-01 search bar",
         "Click on the search bar",
         "Get directions to Cairo Airport", "maps", "hint"),
    Case("maps-02 type destination",
         "Type the destination in the search bar",
         "Navigate to Cairo Airport", "maps", "hint"),
    Case("maps-03 directions button",
         "Click on Directions button",
         "Get directions to Cairo Airport", "maps", "hint"),
    Case("maps-04 start navigation",
         "Click Start to begin navigation",
         "Navigate to Cairo Airport", "maps", "hint"),

    # ── PLAY STORE (4) ───────────────────────────────────────────────────
    Case("store-01 search bar",
         "Click on the search bar in the Play Store",
         "Download the Talabat app", "play_store", "hint"),
    Case("store-02 type app name",
         "Type Talabat in the search bar",
         "Download the Talabat app", "play_store", "hint"),
    Case("store-03 install button",
         "Click on the Install button",
         "Download the Talabat app", "play_store", "hint"),
    Case("store-04 accept permissions",
         "Click Accept to accept the app permissions",
         "Download and install Talabat", "play_store", "hint"),

    # ── NOTES (4) ────────────────────────────────────────────────────────
    Case("notes-01 new note",
         "Click on the + button to create a new note",
         "Create a note with the text hello world", "notes", "hint"),
    Case("notes-02 type note",
         "Type hello world in the note body",
         "Create a note with text hello world", "notes", "hint"),
    Case("notes-03 save note",
         "Click the back button to save the note",
         "Create a note", "notes", "hint"),
    Case("notes-04 title field",
         "Click on the Title field and type Meeting Notes",
         "Create a note titled Meeting Notes", "notes", "hint"),

    # ── CHROME (3) ───────────────────────────────────────────────────────
    Case("chrome-01 address bar",
         "Click on the address bar",
         "Navigate to google.com in Chrome", "chrome", "hint"),
    Case("chrome-02 type url",
         "Type google.com in the address bar",
         "Navigate to google.com in Chrome", "chrome", "hint"),
    Case("chrome-03 new tab",
         "Click on the new tab button",
         "Open a new tab in Chrome", "chrome", "hint"),

    # ── YOUTUBE (3) ──────────────────────────────────────────────────────
    Case("yt-01 search icon",
         "Click on the search icon",
         "Search for a video on YouTube", "youtube", "hint"),
    Case("yt-02 type query",
         "Type cooking videos in the search bar",
         "Search for cooking videos on YouTube", "youtube", "hint"),
    Case("yt-03 play video",
         "Click on the video thumbnail to play it",
         "Watch a cooking video on YouTube", "youtube", "hint"),

    # ── SETTINGS (3) ─────────────────────────────────────────────────────
    Case("settings-01 wifi settings",
         "Click on Wi-Fi to open Wi-Fi settings",
         "Enable Wi-Fi on the device", "settings", "hint"),
    Case("settings-02 wifi toggle",
         "Click on the Wi-Fi toggle to enable it",
         "Turn on Wi-Fi", "settings", "hint"),
    Case("settings-03 bluetooth",
         "Click on Bluetooth settings",
         "Enable Bluetooth on the device", "settings", "hint"),

    # ── ADVERSARIAL (8) ──────────────────────────────────────────────────
    Case("hard-01 topical pollution: pause button",
         "Click on the Pause button",
         "Open the Clock app on mobile device", "clock", "miss",
         "Same app, wrong action — rerank must kill this"),
    Case("hard-02 cross-app: + icon in gmail",
         "click on the + icon on the screen.",
         "Send an email to someone", "gmail", "miss",
         "Clock action queried against gmail"),
    Case("hard-03 too vague",
         "Click on the button",
         "Set an alarm for 7 AM", "clock", "miss",
         "Generic — should be penalised"),
    Case("hard-04 FAB paraphrase",
         "Tap the add alarm floating action button",
         "Set an alarm for 8 AM", "clock", "hint"),
    Case("hard-05 typo resilience",
         "Clik on the send icn",
         "Send an email", "gmail", "hint"),
    Case("hard-06 different time same structure",
         "Set the hours to 3",
         "Prepon the alarm of 4:30 AM by 15 minutes", "clock", "execute"),
    Case("hard-07 confirm alarm paraphrase",
         "Press OK or Save to confirm the alarm setting",
         "Set an alarm for 4:35 AM", "clock", "hint"),
    Case("hard-08 off-domain",
         "Navigate to the payment screen and enter credit card",
         "Set an alarm for 7 AM", "clock", "miss",
         "Completely wrong domain"),
]


# ── Task-level debug payloads ─────────────────────────────────────────────
# These mirror exactly what the coordinator sends to the mobile strategy.
# For each task we show: what Tier 2 would retrieve, what action/selector
# it would use, and whether that action makes sense for the described screen.

TASK_CASES = [
    # FORMAT: step_instruction, overall_goal, app, description, mock_screen_elements
    {
        "description": "Alarm: open clock → step 1",
        "task_id":     "task_1",
        "step":        "Open the Clock app on mobile device",
        "goal":        "Set an alarm for 4:35 AM",
        "app":         "clock",
        "screen_hint": "Home screen with app icons",
    },
    {
        "description": "Alarm: set time → step 2",
        "task_id":     "task_2",
        "step":        "Set the alarm time to 4:35 AM",
        "goal":        "Set an alarm for 4:35 AM",
        "app":         "clock",
        "screen_hint": "Clock app, alarm list visible",
    },
    {
        "description": "Alarm: tap + to add alarm",
        "task_id":     "task_1b",
        "step":        "click on the + icon on the screen.",
        "goal":        "Set an alarm for 7:00 AM",
        "app":         "clock",
        "screen_hint": "Clock app alarm list, FAB visible",
    },
    {
        "description": "Alarm: set hours to 4",
        "task_id":     "task_2b",
        "step":        "Set the hours to 4",
        "goal":        "Set an alarm for 4:35 AM",
        "app":         "clock",
        "screen_hint": "Time picker dialog",
    },
    {
        "description": "Alarm: save the alarm",
        "task_id":     "task_3",
        "step":        "Press OK or Save to confirm the alarm setting",
        "goal":        "Set an alarm for 4:35 AM",
        "app":         "clock",
        "screen_hint": "Time picker with OK button",
    },
    {
        "description": "Email: open gmail",
        "task_id":     "task_1",
        "step":        "Open default email app",
        "goal":        "Send an email to hayaadawy@icloud.com with subject hello world",
        "app":         "gmail",
        "screen_hint": "Home screen",
    },
    {
        "description": "Email: fill To field",
        "task_id":     "task_2",
        "step":        "Fill the To field with hayaadawy@icloud.com",
        "goal":        "Send an email to hayaadawy@icloud.com with subject hello world",
        "app":         "gmail",
        "screen_hint": "Gmail compose screen",
    },
    {
        "description": "Email: fill subject",
        "task_id":     "task_3",
        "step":        "Fill the Subject field with 'hello world'",
        "goal":        "Send an email to hayaadawy@icloud.com with subject hello world",
        "app":         "gmail",
        "screen_hint": "Gmail compose screen with To already filled",
    },
    {
        "description": "Email: fill body",
        "task_id":     "task_4",
        "step":        "Fill the email body with 'heyyyyyyyy'",
        "goal":        "Send an email to hayaadawy@icloud.com with subject hello world",
        "app":         "gmail",
        "screen_hint": "Gmail compose screen",
    },
    {
        "description": "Email: click send",
        "task_id":     "task_5",
        "step":        "Click the Send button to send the email",
        "goal":        "Send an email to hayaadawy@icloud.com with subject hello world",
        "app":         "gmail",
        "screen_hint": "Gmail compose screen filled",
    },
    {
        "description": "Download: open play store",
        "task_id":     "task_1",
        "step":        "Open the App Store on mobile device",
        "goal":        "Download the Talabat app",
        "app":         "play_store",
        "screen_hint": "Home screen",
    },
    {
        "description": "Download: search Talabat",
        "task_id":     "task_2",
        "step":        "Search for Talabat in the App Store",
        "goal":        "Download the Talabat app",
        "app":         "play_store",
        "screen_hint": "Play Store home",
    },
    {
        "description": "Download: click install",
        "task_id":     "task_3",
        "step":        "Click the Get or Install button for the Talabat app",
        "goal":        "Download the Talabat app",
        "app":         "play_store",
        "screen_hint": "Talabat app page in Play Store",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
def query_and_rerank(col, model, step: str, goal: str, app: str, top_k: int = 10):
    """Query ChromaDB and apply keyword rerank. Returns (pairs, fallback_used)."""
    doc       = build_composite_document(goal, step)
    embedding = model.encode([doc], normalize_embeddings=True).tolist()

    fallback = False
    try:
        raw = col.query(
            query_embeddings=embedding,
            n_results=min(top_k, col.count()),
            where={"app": app},
            include=["metadatas", "distances"],
        )
        pairs_raw = [(max(0.0, 1.0 - d), m)
                     for d, m in zip(raw["distances"][0], raw["metadatas"][0])]
    except Exception:
        pairs_raw = []

    if not pairs_raw:
        fallback = True
        raw2 = col.query(
            query_embeddings=embedding,
            n_results=min(5, col.count()),
            include=["metadatas", "distances"],
        )
        pairs_raw = [(max(0.0, 1.0 - d), m)
                     for d, m in zip(raw2["distances"][0], raw2["metadatas"][0])]

    # Apply rerank: returns (blended_sim, raw_sim, meta)
    reranked = keyword_rerank(step, pairs_raw, query_goal=goal)
    return reranked, fallback


# ─────────────────────────────────────────────────────────────────────────────
def run_similarity_cases(col, model, cases, verbose: bool):
    print("─" * 72)
    print(f"RUNNING {len(cases)} SIMILARITY TEST CASES")
    print("─" * 72)

    results = []

    for case in cases:
        reranked, fallback = query_and_rerank(col, model, case.step_instruction,
                                              case.overall_goal, case.app)
        top_blended = reranked[0][0] if reranked else 0.0
        top_raw     = reranked[0][1] if reranked else 0.0
        top_meta    = reranked[0][2] if reranked else {}
        top_step    = top_meta.get("step_instruction", "n/a")[:60]

        actual = band_str(top_blended)
        ok     = (actual == case.expected_band)
        icon   = "✅" if ok else "❌"
        results.append((case, actual, top_blended, top_raw, top_step, ok))

        if verbose or not ok:
            fb = "  ⚠️ fallback=unfiltered" if fallback else ""
            print(f"\n{icon} [{case.label}]")
            print(f"   step    : '{case.step_instruction[:70]}'")
            print(f"   goal    : '{case.overall_goal[:70]}'")
            print(f"   app     : {case.app}{fb}")
            print(f"   expect  : {case.expected_band:<8}  got: {actual}  "
                  f"({band_label(top_blended)}  blended={top_blended:.3f}  raw={top_raw:.3f})")
            print(f"   top hit : '{top_step}'")
            if case.note:
                print(f"   note    : {case.note}")
            if verbose and reranked:
                print(f"   top-5 (blended | raw | step):")
                for b, r, m in reranked[:5]:
                    print(f"     {band_label(b)} {b:.3f}|{r:.3f}  "
                          f"app='{m.get('app','')}'  "
                          f"'{m.get('step_instruction','')[:55]}'")

    return results


# ─────────────────────────────────────────────────────────────────────────────
def run_task_cases(col, model, verbose: bool):
    print("\n" + "═" * 72)
    print("TASK-LEVEL ACTION DEBUGGING")
    print("What Tier 2 would actually DO for real coordinator payloads")
    print("═" * 72)

    for tc in TASK_CASES:
        reranked, fallback = query_and_rerank(col, model, tc["step"],
                                              tc["goal"], tc["app"], top_k=5)

        top_blended = reranked[0][0] if reranked else 0.0
        top_meta    = reranked[0][2] if reranked else {}
        actual_band = band_str(top_blended)

        print(f"\n{'─'*72}")
        print(f"  Task     : [{tc['task_id']}] {tc['description']}")
        print(f"  Step     : '{tc['step']}'")
        print(f"  Goal     : '{tc['goal']}'")
        print(f"  App      : {tc['app']}{'  ⚠️ fallback' if fallback else ''}")
        print(f"  Screen   : {tc['screen_hint']}")
        print(f"  Band     : {band_label(top_blended)}  (sim={top_blended:.3f})")

        if actual_band == "miss":
            print(f"  Result   : 🔴 MISS — Tier 3 LLM will handle this (no cache guidance)")
        elif actual_band == "hint":
            print(f"  Result   : 🟡 HINT — injected into LLM prompt as context")
            print(f"  Hint step: '{top_meta.get('step_instruction','')[:65]}'")
            print(f"  Action   : {top_meta.get('action_type','?')}")
            selectors = json.loads(top_meta.get("selectors","[]") or "[]")
            if selectors:
                sel = selectors[0]
                print(f"  Selector : by={sel.get('by')}  value='{sel.get('value','')[:40]}'")
        elif actual_band == "execute":
            print(f"  Result   : 🟢 EXECUTE — Tier 2 will run this deterministically")
            print(f"  Step     : '{top_meta.get('step_instruction','')[:65]}'")
            print(f"  Action   : {top_meta.get('action_type','?')}")
            selectors = json.loads(top_meta.get("selectors","[]") or "[]")
            if selectors:
                print(f"  Selectors (priority order):")
                for s in selectors[:3]:
                    print(f"    by={s.get('by'):<20} value='{s.get('value','')[:40]}'")
            typed = top_meta.get("typed_value","")
            if typed:
                print(f"  Typed val: '{typed}'")
            param_key = top_meta.get("param_key","")
            if param_key:
                print(f"  Param key: '{param_key}'  "
                      f"(coordinator injects value from extra_params['{param_key}'])")
            sig = top_meta.get("screen_signature","")
            if sig:
                print(f"  Sig check: '{sig[:60]}'")
                print(f"  ⚠️  Sig must match live screen at ≥60% Jaccard or Tier 2 aborts")

        if verbose and reranked:
            print(f"\n  All retrieved (blended|raw):")
            for b, r, m in reranked[:5]:
                print(f"    {band_label(b)} {b:.3f}|{r:.3f}  "
                      f"'{m.get('step_instruction','')[:60]}'")


# ─────────────────────────────────────────────────────────────────────────────
def print_summary(results):
    print("\n" + "=" * 72)
    print("SIMILARITY TEST SUMMARY")
    print("=" * 72)

    passed = sum(1 for *_, ok in results if ok)
    n = len(results)
    print(f"\n  Overall: {passed}/{n}  ({passed/n*100:.0f}%)\n")

    app_res: dict = {}
    for case, actual, blended, raw, step, ok in results:
        app_res.setdefault(case.app, []).append((ok, blended))

    print(f"  {'App':<20} {'Pass/Total':<12} Rate   Avg-blended")
    print(f"  {'─'*20} {'─'*12} {'─'*6} {'─'*11}")
    for app in sorted(app_res):
        vals = app_res[app]
        p    = sum(1 for ok, _ in vals if ok)
        t    = len(vals)
        avg  = sum(b for _, b in vals) / t
        print(f"  {app:<20} {p}/{t:<11} {p/t*100:.0f}%    {avg:.3f}")

    failures = [(c,a,b,r,s) for c,a,b,r,s,ok in results if not ok]
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for case, actual, blended, raw, step, in failures:
            print(f"    ❌  [{case.label}]")
            print(f"        expected={case.expected_band}  got={actual}  "
                  f"blended={blended:.3f}  raw={raw:.3f}")
            print(f"        query : '{case.step_instruction[:65]}'")
            print(f"        top   : '{step}'")

    # Ranges
    e_sims = [b for c,a,b,r,s,ok in results if c.expected_band=="execute"]
    h_sims = [b for c,a,b,r,s,ok in results if c.expected_band=="hint"]
    m_sims = [b for c,a,b,r,s,ok in results if c.expected_band=="miss"]

    print(f"\n  THRESHOLD ANALYSIS (blended scores):")
    if e_sims: print(f"    execute range : {min(e_sims):.3f} – {max(e_sims):.3f}  "
                     f"(THRESHOLD_EXECUTE={THRESHOLD_EXECUTE})")
    if h_sims: print(f"    hint range    : {min(h_sims):.3f} – {max(h_sims):.3f}  "
                     f"(THRESHOLD_HINT={THRESHOLD_HINT})")
    if m_sims: print(f"    miss range    : {min(m_sims):.3f} – {max(m_sims):.3f}")

    # Overlap detection
    if e_sims and m_sims and min(e_sims) < max(m_sims):
        print(f"\n  ⚠️  EXECUTE and MISS ranges still overlap!")
        print(f"     execute min={min(e_sims):.3f}  miss max={max(m_sims):.3f}")
        print(f"     Element verification gate in _execute_tier2_script is REQUIRED")
        print(f"     to prevent wrong actions on correct-scoring but wrong-context steps.")
    else:
        print(f"\n  ✅  EXECUTE and MISS ranges do not overlap — thresholds are clean.")

    print("\n" + "=" * 72)


# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--app",   default=None,
                        help="Filter similarity cases by app name")
    parser.add_argument("--tasks", action="store_true",
                        help="Run task-level action debugging (coordinator payloads)")
    parser.add_argument("--skip-similarity", action="store_true",
                        help="Skip similarity test cases, only run --tasks")
    args = parser.parse_args()

    print("=" * 72)
    print("  AURA Cache Diagnostic v3")
    print(f"  ChromaDB        : {CHROMA_PATH}")
    print(f"  Model           : {EMBED_MODEL}")
    print(f"  THRESHOLD_EXECUTE: {THRESHOLD_EXECUTE}")
    print(f"  THRESHOLD_HINT   : {THRESHOLD_HINT}")
    print("=" * 72)

    try:
        import chromadb
    except ImportError:
        print("❌  pip install chromadb"); sys.exit(1)

    if not os.path.exists(CHROMA_PATH):
        print(f"❌  ChromaDB not found: {CHROMA_PATH}"); sys.exit(1)

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        col = client.get_collection("golden_paths")
    except Exception as e:
        print(f"❌  Collection error: {e}"); sys.exit(1)

    total = col.count()
    print(f"\n✅  Collection: {total:,} records\n")
    if total == 0:
        print("❌  EMPTY — reimport dataset first"); sys.exit(1)

    # Format check
    print("─" * 72)
    print("FORMAT CHECK")
    print("─" * 72)
    samp = col.get(where={"app": "clock"}, limit=2, include=["metadatas","documents"])
    format_ok = True
    if samp["ids"]:
        for doc, meta in zip(samp.get("documents",[]), samp["metadatas"]):
            step     = meta.get("step_instruction","")
            goal     = meta.get("overall_goal","")
            expected = build_composite_document(goal, step)
            stored   = (doc or "")
            step_normalized = normalize_step_for_overlap(step)
            ok       = stored.startswith(step_normalized[:25])
            if not ok: format_ok = False
            print(f"  {'✅' if ok else '❌ WRONG FORMAT'}")
            print(f"    stored  : '{stored[:90]}'")
            if not ok:
                print(f"    expected: '{expected[:90]}'")
    if not format_ok:
        print("\n  ⛔  Delete task_memory_db/ and reimport — wrong embedding format.")
    else:
        print("  ✅  Format correct\n")

    # App distribution
    print("─" * 72)
    print("APP DISTRIBUTION (sample 500)")
    print("─" * 72)
    samp2 = col.get(limit=5000, include=["metadatas"])
    counts: dict = {}
    for m in samp2["metadatas"]:
        counts[m.get("app","?")] = counts.get(m.get("app","?"),0) + 1
    for a,c in sorted(counts.items(), key=lambda x:-x[1])[:12]:
        print(f"  {a:<35} {c:>4}")
    print()

    # Load model
    print("─" * 72)
    print("LOADING EMBEDDING MODEL …")
    print("─" * 72)
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL)
        print(f"  ✅  {EMBED_MODEL}\n")
    except Exception as e:
        print(f"❌  {e}"); sys.exit(1)

    # Similarity cases
    results = []
    if not args.skip_similarity:
        cases = [c for c in CASES if args.app is None or c.app == args.app]
        results = run_similarity_cases(col, model, cases, args.verbose)
        print_summary(results)

    # Task-level cases
    if args.tasks:
        run_task_cases(col, model, args.verbose)


if __name__ == "__main__":
    main()