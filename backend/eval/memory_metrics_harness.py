"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         YUSR AI — Memory Architecture Metrics Harness                       ║
║                  WITH BUILT-IN BASELINE SIMULATION                          ║
║                                                                              ║
║  PLACE THIS FILE AT:                                                         ║
║    backend/eval/memory_metrics_harness.py                                   ║
║                                                                              ║
║  CREATE ALONGSIDE IT:                                                        ║
║    backend/eval/__init__.py    ← empty file                                 ║
║                                                                              ║
║  HOW TO RUN (from the backend/ directory):                                  ║
║                                                                              ║
║  STEP 1 — Seed Mem0 preferences (skip if already done):                     ║
║    python -m eval.memory_metrics_harness --seed_prefs                       ║
║                                                                              ║
║  STEP 2 — Seed baseline conversation history:                               ║
║    python -m eval.memory_metrics_harness --seed_baseline                    ║
║                                                                              ║
║  STEP 3 — Verify what was stored:                                           ║
║    python -m eval.memory_metrics_harness --list_prefs                       ║
║    python -m eval.memory_metrics_harness --list_baseline                    ║
║                                                                              ║
║  STEP 4 — Run full comparison (Mem0 vs Baseline):                           ║
║    python -m eval.memory_metrics_harness                                    ║
║                                                                              ║
║  STEP 5 — Faster run (skip live Groq call):                                 ║
║    python -m eval.memory_metrics_harness --no_groq                          ║
║                                                                              ║
║  FLAGS:                                                                      ║
║    --runs          Timed retrieval calls for latency (default: 30)          ║
║    --write_runs    Timed write calls               (default: 10)            ║
║    --no_groq       Use token estimates only (skip live Groq call)           ║
║    --list_prefs    Print stored Mem0 preferences then exit                  ║
║    --list_baseline Print stored baseline conversations then exit            ║
║    --seed_prefs    Seed test preferences into Mem0 then exit                ║
║    --seed_baseline Seed fake conversation history into MongoDB then exit    ║
║    --clear_baseline Delete all baseline data for USER_ID then exit         ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARCHITECTURE COMPARISON
────────────────────────────────────────────────────────────────────────────────

  BASELINE (raw MongoDB, no Mem0)
  ─────────────────────────────────────────────────────────────────────────────
  • Each user turn is appended as-is to collection "baseline_conversations"
  • On every new request, ALL past messages are loaded and concatenated into
    a single string, then injected into the LLM prompt as the full context
  • Retrieval = full table scan (find all docs for user, sort by timestamp)
  • No deduplication, no relevance filtering — grows unboundedly
  • Mirrors exactly what the system did before Mem0 was added:
    LanguageAgent._save_conversation() → language_agent_conversations
    → injected raw into decompose_task_to_actions() preferences block

  MEM0 (vector store + LLM extraction)
  ─────────────────────────────────────────────────────────────────────────────
  • Each completed task is summarised by Groq into preference facts
  • Stored as dense vectors (all-MiniLM-L6-v2) in Atlas vector index
  • Retrieval = 3x ANN search with query expansion, filtered by score >= 0.25
  • Only relevant memories are injected (limit=10)
  • Cross-session — persists across conversations

WHAT THIS MEASURES
────────────────────────────────────────────────────────────────────────────────

  GROUP A — Latency
    mem_read_latency_mem0      → get_relevant_preferences()  (ANN search)
    mem_read_latency_baseline  → load_baseline_context()     (MongoDB find)
    mem_write_latency_mem0     → add_preference()            (LLM + embed + insert)
    mem_write_latency_baseline → save_baseline_turn()        (raw MongoDB insert)
    embed_latency              → raw HuggingFace embed call

  GROUP B — Recall@k  (Mem0 only — baseline returns everything by definition)

  GROUP C — Token Counts
    Mem0      → only relevant memories injected (<= 10 items)
    Baseline  → full conversation history injected (grows with every message)
"""

import os, sys, json, time, logging, argparse, statistics
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# ── make  backend/  importable ────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("metrics")
logger.setLevel(logging.INFO)

# ── terminal colours ──────────────────────────────────────────────────────────
G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; C = "\033[96m"
B = "\033[1m";  X = "\033[0m"

def h(t):    print(f"\n{B}{C}{'='*68}{X}\n{B}{C}  {t}{X}\n{B}{C}{'='*68}{X}")
def ok(t):   print(f"  {G}v{X}  {t}")
def warn(t): print(f"  {Y}!{X}  {t}")
def err(t):  print(f"  {R}x{X}  {t}")
def p(t=""):  print(t)
def row(label, val, color=None):
    c = color or X
    print(f"  {label:<50} {c}{val}{X}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 0 — CONFIGURATION  (edit these before running)
# ══════════════════════════════════════════════════════════════════════════════

# ─── USER ID ──────────────────────────────────────────────────────────────────
USER_ID: str = "eval_user_001"          # <- CHANGE THIS

# ─── BASELINE MONGODB COLLECTION ──────────────────────────────────────────────
# This collection mirrors what LanguageAgent._save_conversation() produced
# before Mem0 was introduced.  It stores raw conversation turns.
BASELINE_COLLECTION = "baseline_conversations"
BASELINE_DB         = "yusr_db"

# ─── SEED PREFERENCES (for Mem0) ──────────────────────────────────────────────
SEED_PREFERENCES: List[Dict] = [
    {
        "text": "User prefers Chrome for web browsing over other browsers",
        "metadata": {"category": "app_usage", "confidence": "high"}
    },
    {
        "text": "User likes to shop on Amazon for electronics and tech products",
        "metadata": {"category": "app_usage", "confidence": "high"}
    },
    {
        "text": "User name is Sara and she works as a software engineer",
        "metadata": {"category": "personal_info", "confidence": "high"}
    },
    {
        "text": "User is vegetarian and prefers healthy meal recipes",
        "metadata": {"category": "personal_info", "confidence": "high"}
    },
    {
        "text": "User prefers dark mode across all applications and websites",
        "metadata": {"category": "app_usage", "confidence": "high"}
    },
    {
        "text": "User usually wakes up at 7 AM and sets morning alarms",
        "metadata": {"category": "personal_info", "confidence": "medium"}
    },
    {
        "text": "User prefers to use Notepad for quick text editing tasks",
        "metadata": {"category": "app_usage", "confidence": "medium"}
    },
    {
        "text": "User frequently searches for Python programming tutorials",
        "metadata": {"category": "app_usage", "confidence": "high"}
    },
    {
        "text": "User prefers Gmail over other email clients for communication",
        "metadata": {"category": "app_usage", "confidence": "high"}
    },
    {
        "text": "User likes to use YouTube for watching educational content and tech reviews",
        "metadata": {"category": "app_usage", "confidence": "medium"}
    },
    {
        "text": "User device is a Windows 11 desktop with a 4K monitor",
        "metadata": {"category": "personal_info", "confidence": "high"}
    },
    {
        "text": "User prefers English language interface for all applications",
        "metadata": {"category": "personal_info", "confidence": "high"}
    },
]

# ─── SEED BASELINE CONVERSATIONS ──────────────────────────────────────────────
# These simulate what a real multi-session conversation history looks like.
# The baseline system would have injected ALL of this into every LLM prompt.
# Designed to produce ~30 messages across 3 sessions — a realistic scenario
# after a few days of using the app.
SEED_BASELINE_SESSIONS: List[Dict] = [
    {
        "session_id": "baseline_session_001",
        "messages": [
            {"role": "system",    "content": "You are a Conversational Clarity Agent."},
            {"role": "user",      "content": "open chrome browser"},
            {"role": "assistant", "content": "Opening Chrome browser."},
            {"role": "user",      "content": "go to amazon.com"},
            {"role": "assistant", "content": "Navigating to Amazon.com."},
            {"role": "user",      "content": "search for wireless headphones"},
            {"role": "assistant", "content": "Searching Amazon for wireless headphones."},
            {"role": "user",      "content": "show me the top 3 results"},
            {"role": "assistant", "content": "Extracting the top 3 product titles and prices from Amazon search results."},
            {"role": "user",      "content": "open notepad and write the first result"},
            {"role": "assistant", "content": "Opening Notepad and typing the first search result."},
        ]
    },
    {
        "session_id": "baseline_session_002",
        "messages": [
            {"role": "system",    "content": "You are a Conversational Clarity Agent."},
            {"role": "user",      "content": "set alarm for 7am tomorrow"},
            {"role": "assistant", "content": "Setting alarm for 7:00 AM tomorrow."},
            {"role": "user",      "content": "open gmail"},
            {"role": "assistant", "content": "Opening Gmail in Chrome."},
            {"role": "user",      "content": "send email to my manager about the project update"},
            {"role": "assistant", "content": "Who should I send the email to and what is the subject?"},
            {"role": "user",      "content": "manager@company.com subject Project Update"},
            {"role": "assistant", "content": "Composing and sending email to manager@company.com."},
            {"role": "user",      "content": "search for python tutorials on youtube"},
            {"role": "assistant", "content": "Searching YouTube for Python tutorials."},
            {"role": "user",      "content": "open the first result"},
            {"role": "assistant", "content": "Opening the first YouTube result for Python tutorials."},
        ]
    },
    {
        "session_id": "baseline_session_003",
        "messages": [
            {"role": "system",    "content": "You are a Conversational Clarity Agent."},
            {"role": "user",      "content": "change display to dark mode"},
            {"role": "assistant", "content": "Opening display settings and enabling dark mode."},
            {"role": "user",      "content": "find a vegetarian recipe for dinner"},
            {"role": "assistant", "content": "Searching for vegetarian dinner recipes."},
            {"role": "user",      "content": "open settings and check for windows updates"},
            {"role": "assistant", "content": "Opening Windows Settings and navigating to Windows Update."},
            {"role": "user",      "content": "what is my device resolution"},
            {"role": "assistant", "content": "Checking display resolution on your Windows 11 desktop."},
            {"role": "user",      "content": "change system language to english"},
            {"role": "assistant", "content": "Navigating to language settings and setting system language to English."},
            {"role": "user",      "content": "take a screenshot and save it to desktop"},
            {"role": "assistant", "content": "Taking a screenshot and saving it to the Desktop."},
        ]
    },
]

# ─── GOLD SET (for Mem0 Recall@k) ─────────────────────────────────────────────
GOLD_SET: List[Tuple[str, str]] = [
    ("User prefers Chrome for web browsing",              "open browser"),
    ("User likes to shop on Amazon for electronics",      "buy wireless headphones"),
    ("User name is Sara and she works as a software",     "what is my name"),
    ("User is vegetarian and prefers healthy meal",       "suggest dinner recipes"),
    ("User prefers dark mode across all applications",    "change display settings"),
    ("User usually wakes up at 7 AM and sets morning",   "set alarm for 7am"),
    ("User prefers to use Notepad for quick text",        "open text editor"),
    ("User frequently searches for Python programming",   "search Python tutorials"),
    ("User prefers Gmail over other email clients",       "open email"),
    ("User likes to use YouTube for watching educational","play video"),
    ("User device is a Windows 11 desktop",               "what device am I using"),
    ("User prefers English language interface",           "change language settings"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — SAMPLE QUERIES
# ══════════════════════════════════════════════════════════════════════════════

LATENCY_QUERIES: List[str] = [
    "open browser",
    "search for news",
    "set alarm for 7am",
    "send email to colleague",
    "open calculator",
    "navigate to Amazon",
    "play music",
    "take a screenshot",
    "open notepad",
    "search for flights",
    "open settings",
    "create new document",
    "check the weather",
    "open YouTube",
    "find a recipe",
    "login to Gmail",
    "open calendar",
    "search Python tutorials",
    "open file manager",
    "turn off wifi",
]

SAMPLE_TASK_REQUEST: Dict = {
    "confirmation": (
        "Search Amazon for wireless headphones and extract the top 3 "
        "product titles and prices"
    ),
    "device_type": "desktop",
    "user_id": USER_ID,
}


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def pct(data: List[float], p_val: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    return s[min(int(len(s) * p_val / 100), len(s) - 1)]

_tiktoken_warned = False

def count_tokens(text: str) -> int:
    global _tiktoken_warned
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        if not _tiktoken_warned:
            warn("tiktoken not installed — using char/4 estimate.")
            warn("Accurate counts:  pip install tiktoken")
            _tiktoken_warned = True
        return max(1, len(text) // 4)

def get_mongo_client():
    """Get a MongoDB client from MONGODB_URI env var."""
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise ValueError("MONGODB_URI not set in backend/.env")
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


# ══════════════════════════════════════════════════════════════════════════════
#  BASELINE MEMORY SYSTEM
#  Pure MongoDB — no vectors, no LLM extraction, no relevance filtering.
#  This is what the system looked like BEFORE Mem0 was added.
# ══════════════════════════════════════════════════════════════════════════════

class BaselineMemoryManager:
    """
    Simulates the pre-Mem0 memory architecture.

    Storage:  MongoDB collection  yusr_db.baseline_conversations
              Each document = one session, contains a 'messages' list.
              Mirrors the schema written by LanguageAgent._save_conversation().

    Retrieval: Load ALL message documents for user_id, concatenate every
               turn into a single flat string, return it as the context block.
               No scoring, no filtering — everything is always returned.
               This is the same injection that decompose_task_to_actions()
               performed before the # USER PREFERENCES block was introduced.
    """

    def __init__(self, user_id: str):
        self.user_id  = user_id
        self.client   = get_mongo_client()
        self.db       = self.client[BASELINE_DB]
        self.coll     = self.db[BASELINE_COLLECTION]
        # Ensure index for fast user_id lookups
        self.coll.create_index([("user_id", 1), ("timestamp", -1)])

    # ── WRITE ─────────────────────────────────────────────────────────────────
    def save_turn(self, user_message: str, assistant_response: str,
                  session_id: str = "default") -> str:
        """
        Append a user/assistant turn to the baseline store.
        Mirrors LanguageAgent._save_conversation() behaviour.
        """
        doc_id = f"{self.user_id}_{session_id}"
        self.coll.update_one(
            {"doc_id": doc_id, "user_id": self.user_id},
            {
                "$push": {
                    "messages": {
                        "$each": [
                            {"role": "user",      "content": user_message,      "ts": time.time()},
                            {"role": "assistant", "content": assistant_response, "ts": time.time()},
                        ]
                    }
                },
                "$set": {
                    "session_id": session_id,
                    "timestamp":  time.time(),
                    "user_id":    self.user_id,
                },
            },
            upsert=True,
        )
        return doc_id

    def save_session(self, session_id: str, messages: List[Dict]) -> str:
        """Bulk-insert a full session (used by --seed_baseline)."""
        doc_id = f"{self.user_id}_{session_id}"
        self.coll.replace_one(
            {"doc_id": doc_id, "user_id": self.user_id},
            {
                "doc_id":     doc_id,
                "user_id":    self.user_id,
                "session_id": session_id,
                "messages":   messages,
                "timestamp":  time.time(),
            },
            upsert=True,
        )
        return doc_id

    # ── READ ──────────────────────────────────────────────────────────────────
    def load_context(self) -> str:
        """
        Load ALL messages for user_id and flatten into a single string.
        This is what would have been injected into the LLM prompt wholesale.
        No relevance filtering — the full history is returned every time.
        """
        docs = list(
            self.coll.find(
                {"user_id": self.user_id},
                sort=[("timestamp", 1)]
            )
        )
        if not docs:
            return ""

        lines = []
        for doc in docs:
            for msg in doc.get("messages", []):
                role    = msg.get("role", "?").upper()
                content = msg.get("content", "")
                if content:
                    lines.append(f"{role}: {content}")

        return "\n".join(lines)

    def count_messages(self) -> int:
        """Total number of stored messages across all sessions."""
        pipeline = [
            {"$match": {"user_id": self.user_id}},
            {"$project": {"n": {"$size": "$messages"}}},
            {"$group": {"_id": None, "total": {"$sum": "$n"}}},
        ]
        result = list(self.coll.aggregate(pipeline))
        return result[0]["total"] if result else 0

    def count_sessions(self) -> int:
        return self.coll.count_documents({"user_id": self.user_id})

    def clear(self) -> int:
        result = self.coll.delete_many({"user_id": self.user_id})
        return result.deleted_count

    def get_all_sessions(self) -> List[Dict]:
        return list(self.coll.find({"user_id": self.user_id}, sort=[("timestamp", 1)]))


# ══════════════════════════════════════════════════════════════════════════════
#  SEED FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def seed_mem0_preferences(pref_mgr) -> int:
    """Write SEED_PREFERENCES into Mem0."""
    h(f"Seeding Mem0 Preferences   user_id='{USER_ID}'")
    ok(f"Preparing to seed {len(SEED_PREFERENCES)} preferences ...")
    p()

    existing       = pref_mgr.get_all_preferences()
    existing_texts = {
        e.get("memory", "").strip().lower()
        for e in (existing or [])
        if isinstance(e, dict)
    }
    ok(f"Found {len(existing_texts)} preferences already stored — skipping exact duplicates.")
    p()

    stored = 0
    skipped = 0
    for item in SEED_PREFERENCES:
        text = item["text"]
        meta = item.get("metadata", {})
        if text.strip().lower() in existing_texts:
            print(f"  {Y}SKIP {X}  {text[:65]}")
            skipped += 1
            continue
        try:
            pref_mgr.add_preference(text, metadata=meta)
            print(f"  {G}STORE{X}  [{meta.get('category','general'):<20}]  {text[:55]}")
            stored += 1
            time.sleep(0.3)
        except Exception as exc:
            print(f"  {R}ERROR{X}  {text[:50]}  ->  {exc}")

    p()
    ok(f"Seeded {stored} new Mem0 preferences  ({skipped} skipped).")
    return stored


def seed_baseline_conversations(baseline_mgr: BaselineMemoryManager) -> int:
    """
    Write SEED_BASELINE_SESSIONS into MongoDB baseline collection.
    Simulates ~3 sessions x ~10 messages = realistic pre-Mem0 history.
    """
    h(f"Seeding Baseline Conversations   user_id='{USER_ID}'")
    ok(f"Preparing to seed {len(SEED_BASELINE_SESSIONS)} sessions ...")
    p()

    total_msgs = 0
    for sess in SEED_BASELINE_SESSIONS:
        sid  = sess["session_id"]
        msgs = sess["messages"]
        baseline_mgr.save_session(session_id=sid, messages=msgs)
        total_msgs += len(msgs)
        print(f"  {G}STORED{X}  session={sid}  ({len(msgs)} messages)")

    p()
    ok(f"Seeded {len(SEED_BASELINE_SESSIONS)} sessions  ({total_msgs} total messages).")
    ok(f"Collection:  {BASELINE_DB}.{BASELINE_COLLECTION}")
    ok("Run --list_baseline to verify.")
    return total_msgs


# ══════════════════════════════════════════════════════════════════════════════
#  LIST UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def list_mem0_preferences(pref_mgr):
    h(f"Stored Mem0 Preferences   user_id='{USER_ID}'")
    prefs = pref_mgr.get_all_preferences()
    if not prefs:
        warn("No Mem0 preferences stored yet.")
        warn(f"Run:  python -m eval.memory_metrics_harness --seed_prefs")
        return
    ok(f"Found {len(prefs)} preferences:\n")
    for i, pref in enumerate(prefs, 1):
        mem = pref.get("memory", "")
        cat = (pref.get("metadata") or {}).get("category", "?")
        mid = pref.get("id", "?")
        print(f"  {i:>3}.  [{cat:<20}]  id={str(mid)[:8]}...  {mem[:75]}")
    p()
    ok("Copy memory text fragments into GOLD_SET at the top of this file.")


def list_baseline_conversations(baseline_mgr: BaselineMemoryManager):
    h(f"Stored Baseline Conversations   user_id='{USER_ID}'")
    sessions = baseline_mgr.get_all_sessions()
    if not sessions:
        warn("No baseline conversations stored yet.")
        warn(f"Run:  python -m eval.memory_metrics_harness --seed_baseline")
        return
    ok(f"Found {baseline_mgr.count_sessions()} sessions  ({baseline_mgr.count_messages()} total messages):\n")
    for sess in sessions:
        sid  = sess.get("session_id", "?")
        msgs = sess.get("messages", [])
        ts   = datetime.fromtimestamp(sess.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M")
        print(f"  {G}*{X}  session={sid}  messages={len(msgs)}  saved={ts}")
        for m in msgs[:3]:
            role    = m.get("role", "?")
            content = m.get("content", "")[:70]
            print(f"       {role:<10} {content}")
        if len(msgs) > 3:
            print(f"       ... {len(msgs)-3} more messages")
        p()


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP A — LATENCY
# ══════════════════════════════════════════════════════════════════════════════

def measure_read_latency_mem0(pref_mgr, runs: int) -> Dict:
    """Times Mem0 ANN vector search (3x query expansion per call)."""
    h("GROUP A — Memory READ Latency  [MEM0]   get_relevant_preferences()")
    ok(f"Running {runs} timed calls  (limit=10, min_score=0.25) ...")

    times_ms: List[float] = []
    n_results: List[int]  = []

    for i in range(runs):
        query = LATENCY_QUERIES[i % len(LATENCY_QUERIES)]
        t0    = time.perf_counter()
        res   = pref_mgr.get_relevant_preferences(query, limit=10, min_score=0.25)
        times_ms.append((time.perf_counter() - t0) * 1000)
        n_results.append(len(res) if res else 0)
        if (i + 1) % 5 == 0:
            print(f"    ... {i+1}/{runs}", end="\r")

    print()
    out = {
        "system":      "mem0",
        "n_calls":     runs,
        "p50_ms":      round(pct(times_ms, 50), 1),
        "p95_ms":      round(pct(times_ms, 95), 1),
        "min_ms":      round(min(times_ms), 1),
        "max_ms":      round(max(times_ms), 1),
        "mean_ms":     round(statistics.mean(times_ms), 1),
        "avg_results": round(statistics.mean(n_results), 1),
    }
    p()
    row("p50 latency",           f"{out['p50_ms']} ms",   G)
    row("p95 latency",           f"{out['p95_ms']} ms",   Y)
    row("min / max",             f"{out['min_ms']} ms  /  {out['max_ms']} ms")
    row("mean",                  f"{out['mean_ms']} ms")
    row("avg memories returned", f"{out['avg_results']}")
    p()
    ok(f"p50 {out['p50_ms']}ms {'<= 300ms OK' if out['p50_ms']<=300 else '> 300ms — check Atlas vector index'}")
    ok(f"p95 {out['p95_ms']}ms {'<= 1500ms OK' if out['p95_ms']<=1500 else '> 1500ms — consider index tuning'}")
    return out


def measure_read_latency_baseline(baseline_mgr: BaselineMemoryManager, runs: int) -> Dict:
    """
    Times raw MongoDB full-history load.
    No filtering — fetches ALL messages for the user every time.
    This is what happened before Mem0: inject the entire conversation into
    every LLM prompt.
    """
    h("GROUP A — Memory READ Latency  [BASELINE]   load_context()")
    ok(f"Running {runs} timed calls  (full history load, no filtering) ...")

    times_ms: List[float] = []
    n_chars:  List[int]   = []

    for i in range(runs):
        t0  = time.perf_counter()
        ctx = baseline_mgr.load_context()
        times_ms.append((time.perf_counter() - t0) * 1000)
        n_chars.append(len(ctx))
        if (i + 1) % 5 == 0:
            print(f"    ... {i+1}/{runs}", end="\r")

    print()
    out = {
        "system":         "baseline",
        "n_calls":        runs,
        "p50_ms":         round(pct(times_ms, 50), 1),
        "p95_ms":         round(pct(times_ms, 95), 1),
        "min_ms":         round(min(times_ms), 1),
        "max_ms":         round(max(times_ms), 1),
        "mean_ms":        round(statistics.mean(times_ms), 1),
        "avg_chars":      round(statistics.mean(n_chars), 0),
        "total_messages": baseline_mgr.count_messages(),
        "total_sessions": baseline_mgr.count_sessions(),
    }
    p()
    row("p50 latency",                   f"{out['p50_ms']} ms",   G)
    row("p95 latency",                   f"{out['p95_ms']} ms",   Y)
    row("min / max",                     f"{out['min_ms']} ms  /  {out['max_ms']} ms")
    row("mean",                          f"{out['mean_ms']} ms")
    row("avg context returned (chars)",  f"{int(out['avg_chars']):,}")
    row("total stored messages",         f"{out['total_messages']}")
    row("total sessions",                f"{out['total_sessions']}")
    p()
    warn("Baseline latency is MongoDB round-trip only — no embedding, no scoring.")
    warn("Context grows unboundedly: more sessions = longer load + more tokens.")
    return out


def measure_write_latency_mem0(pref_mgr, runs: int = 10) -> Dict:
    """Times Mem0 write: Groq LLM extraction + embedding + vector insert."""
    h("GROUP A — Memory WRITE Latency  [MEM0]   add_preference()")
    ok(f"Running {runs} timed write calls (will be deleted after) ...")

    times_ms: List[float]   = []
    inserted_ids: List[str] = []

    for i in range(runs):
        text = f"[EVAL_BENCH_{i}] User uses test option {i} for evaluation"
        t0   = time.perf_counter()
        res  = pref_mgr.add_preference(text, metadata={"category": "eval_test", "eval": True})
        times_ms.append((time.perf_counter() - t0) * 1000)
        if isinstance(res, dict):
            for r in (res.get("results") or []):
                if isinstance(r, dict) and r.get("id"):
                    inserted_ids.append(r["id"])

    cleaned = sum(1 for mid in inserted_ids if pref_mgr.delete_preference(mid))
    ok(f"Cleaned up {cleaned}/{len(inserted_ids)} test memories")

    out = {
        "system":  "mem0",
        "n_calls": runs,
        "p50_ms":  round(pct(times_ms, 50), 1),
        "p95_ms":  round(pct(times_ms, 95), 1),
        "min_ms":  round(min(times_ms), 1),
        "max_ms":  round(max(times_ms), 1),
        "mean_ms": round(statistics.mean(times_ms), 1),
    }
    p()
    row("p50 write latency", f"{out['p50_ms']} ms", G)
    row("p95 write latency", f"{out['p95_ms']} ms", Y)
    row("min / max",         f"{out['min_ms']} ms  /  {out['max_ms']} ms")
    p()
    warn("Mem0 write = Groq LLM extraction + embed + Atlas vector insert.")
    warn("Writes run AFTER the HTTP response is sent — they do NOT block the user.")
    return out


def measure_write_latency_baseline(baseline_mgr: BaselineMemoryManager, runs: int = 10) -> Dict:
    """
    Times raw MongoDB insert (no LLM, no embedding).
    Each write appends one user+assistant turn to the baseline collection.
    """
    h("GROUP A — Memory WRITE Latency  [BASELINE]   save_turn()")
    ok(f"Running {runs} timed write calls (will be deleted after) ...")

    times_ms: List[float] = []
    test_session = f"_eval_test_{int(time.time())}"

    for i in range(runs):
        user_msg = f"[EVAL] test user message number {i}"
        asst_msg = f"[EVAL] test assistant response number {i}"
        t0 = time.perf_counter()
        baseline_mgr.save_turn(user_msg, asst_msg, session_id=test_session)
        times_ms.append((time.perf_counter() - t0) * 1000)

    # Cleanup test session
    deleted = baseline_mgr.coll.delete_many({
        "user_id":    baseline_mgr.user_id,
        "session_id": test_session
    }).deleted_count
    ok(f"Cleaned up {deleted} test documents")

    out = {
        "system":  "baseline",
        "n_calls": runs,
        "p50_ms":  round(pct(times_ms, 50), 1),
        "p95_ms":  round(pct(times_ms, 95), 1),
        "min_ms":  round(min(times_ms), 1),
        "max_ms":  round(max(times_ms), 1),
        "mean_ms": round(statistics.mean(times_ms), 1),
    }
    p()
    row("p50 write latency", f"{out['p50_ms']} ms", G)
    row("p95 write latency", f"{out['p95_ms']} ms", Y)
    row("min / max",         f"{out['min_ms']} ms  /  {out['max_ms']} ms")
    p()
    ok("Baseline write = raw MongoDB upsert only (no LLM, no embedding).")
    return out


def measure_embed_latency(runs: int = 20) -> Dict:
    """Times the raw HuggingFace embed call (called 3x per Mem0 read)."""
    h("GROUP A — Embedding Latency   HuggingFace all-MiniLM-L6-v2")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        ok("Model loaded")
    except ImportError:
        warn("sentence-transformers not installed — skipping")
        warn("pip install sentence-transformers")
        return {"skipped": True}

    texts = [
        "user prefers Chrome for web browsing",
        "navigate to Amazon search page",
        "set alarm for 7am tomorrow",
        "user likes dark mode",
        "login to Gmail",
    ]
    times_ms: List[float] = []
    for i in range(runs):
        t0 = time.perf_counter()
        model.encode([texts[i % len(texts)]])
        times_ms.append((time.perf_counter() - t0) * 1000)

    out = {
        "n_calls": runs,
        "p50_ms":  round(pct(times_ms, 50), 1),
        "p95_ms":  round(pct(times_ms, 95), 1),
        "min_ms":  round(min(times_ms), 1),
        "max_ms":  round(max(times_ms), 1),
        "mean_ms": round(statistics.mean(times_ms), 1),
    }
    p()
    row("p50 embed latency",                    f"{out['p50_ms']} ms")
    row("p95 embed latency",                    f"{out['p95_ms']} ms")
    row("Per-READ overhead  (3x expand x p50)", f"~{round(out['p50_ms']*3,1)} ms", Y)
    row("Per-WRITE overhead (1x p50)",          f"~{out['p50_ms']} ms")
    row("Baseline embed overhead",              "0 ms  (no embedding)", G)
    return out


def compare_read_latency(lr_mem0: Dict, lr_base: Dict) -> Dict:
    """Side-by-side read latency comparison."""
    h("GROUP A — Read Latency Comparison   Baseline vs Mem0")
    p()
    print(f"  {'Metric':<45} {'Baseline':>12}  {'Mem0':>12}  {'Delta':>10}")
    print(f"  {'-'*45} {'-'*12}  {'-'*12}  {'-'*10}")

    def cmp_row(label, b_val, m_val):
        if b_val and m_val:
            delta = m_val - b_val
            pct_d = delta / b_val * 100 if b_val else 0
            sign  = "+" if delta > 0 else ""
            color = R if delta > 0 else G
            print(
                f"  {label:<45} {b_val:>11.1f}ms  {m_val:>11.1f}ms  "
                f"{color}{sign}{pct_d:.0f}%{X}"
            )
        else:
            print(f"  {label:<45} {'N/A':>12}  {'N/A':>12}  {'N/A':>10}")

    cmp_row("p50 read latency",  lr_base.get("p50_ms"), lr_mem0.get("p50_ms"))
    cmp_row("p95 read latency",  lr_base.get("p95_ms"), lr_mem0.get("p95_ms"))
    cmp_row("mean read latency", lr_base.get("mean_ms"), lr_mem0.get("mean_ms"))

    p()
    warn("Mem0 read is slower due to 3x embedding + ANN vector search.")
    warn("Baseline read is a simple MongoDB find() with no embedding overhead.")
    warn("The tradeoff: Mem0 injects LESS context (fewer tokens) per call.")

    return {
        "baseline_p50_ms": lr_base.get("p50_ms"),
        "mem0_p50_ms":     lr_mem0.get("p50_ms"),
        "baseline_p95_ms": lr_base.get("p95_ms"),
        "mem0_p95_ms":     lr_mem0.get("p95_ms"),
    }


def compare_write_latency(lw_mem0: Dict, lw_base: Dict) -> Dict:
    """Side-by-side write latency comparison."""
    h("GROUP A — Write Latency Comparison   Baseline vs Mem0")
    p()
    print(f"  {'Metric':<45} {'Baseline':>12}  {'Mem0':>12}  {'Delta':>10}")
    print(f"  {'-'*45} {'-'*12}  {'-'*12}  {'-'*10}")

    for label, bk, mk in [
        ("p50 write latency", "p50_ms", "p50_ms"),
        ("p95 write latency", "p95_ms", "p95_ms"),
        ("mean write latency","mean_ms","mean_ms"),
    ]:
        b = lw_base.get(bk, 0)
        m = lw_mem0.get(mk, 0)
        if b and m:
            delta = m - b
            pct_d = delta / b * 100 if b else 0
            sign  = "+"  if delta > 0 else ""
            color = R    if delta > 0 else G
            print(
                f"  {label:<45} {b:>11.1f}ms  {m:>11.1f}ms  "
                f"{color}{sign}{pct_d:.0f}%{X}"
            )

    p()
    warn("Mem0 write is much slower: it calls Groq LLM to extract preferences")
    warn("before embedding and inserting.  This happens ASYNC after the HTTP")
    warn("response is already sent — so the USER never waits for it.")
    ok("Baseline write is a simple upsert — near-zero overhead.")

    return {
        "baseline_p50_ms": lw_base.get("p50_ms"),
        "mem0_p50_ms":     lw_mem0.get("p50_ms"),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP B — RECALL@k  (Mem0 only)
# ══════════════════════════════════════════════════════════════════════════════

def measure_recall(pref_mgr) -> Dict:
    h("GROUP B — Context Recall Accuracy   Recall@k  [MEM0 only]")

    if not GOLD_SET:
        warn("GOLD_SET is empty — skipping.")
        return {"skipped": True}

    ok(f"Testing {len(GOLD_SET)} items ...")
    p()
    hits = {"@1": 0, "@5": 0, "@10": 0}
    details = []

    for pref_sub, query in GOLD_SET:
        results = pref_mgr.get_relevant_preferences(query, limit=10, min_score=0.0)
        texts   = [r.get("memory", "") for r in (results or [])]

        h1  = any(pref_sub[:40].lower() in t.lower() for t in texts[:1])
        h5  = any(pref_sub[:40].lower() in t.lower() for t in texts[:5])
        h10 = any(pref_sub[:40].lower() in t.lower() for t in texts[:10])
        if h1:  hits["@1"]  += 1
        if h5:  hits["@5"]  += 1
        if h10: hits["@10"] += 1

        tag = (f"{G}HIT@1 {X}" if h1 else
               f"{Y}HIT@5 {X}" if h5 else
               f"{Y}HIT@10{X}" if h10 else
               f"{R}MISS  {X}")
        print(f"  [{tag}]  q='{query[:35]:<35}'  p='{pref_sub[:35]}'")
        details.append({
            "query": query, "pref": pref_sub,
            "h1": h1, "h5": h5, "h10": h10,
            "top": texts[0] if texts else ""
        })

    n = len(GOLD_SET)
    r1, r5, r10 = [round(hits[k]/n*100, 1) for k in ("@1","@5","@10")]
    p()
    row("Recall@1   (correct preference is top result)", f"{r1}%",
        G if r1 >= 50 else R)
    row("Recall@5   (correct preference in top 5)",      f"{r5}%",
        G if r5 >= 70 else Y)
    row("Recall@10  (correct preference in top 10)",     f"{r10}%",
        G if r10 >= 80 else Y)
    p()
    warn("Baseline recall = 100% trivially — it always returns everything.")
    warn("But that comes at the cost of massive token usage (see Group C).")
    warn("Reference: Mem0 paper LOCOMO benchmark = 66.9% LLM-as-Judge accuracy")
    return {
        "n": n, "recall_at_1": r1, "recall_at_5": r5, "recall_at_10": r10,
        "hits": hits, "details": details
    }


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP C — TOKEN COUNTS
# ══════════════════════════════════════════════════════════════════════════════

def _rebuild_decompose_prompt(task_request: Dict, preferences_context: str) -> str:
    device_type = task_request.get("device_type", "desktop")
    device_hint = (
        f"The user is on a {device_type} device. "
        "Tailor task recommendations accordingly.\n\n"
    )
    return (
        f"{device_hint}"
        "You are the AURA Task Decomposition Agent. "
        "Convert user requests into low-level executable tasks.\n\n"
        "# USER REQUEST\n"
        f"{json.dumps(task_request, indent=2)}\n\n"
        "# USER PREFERENCES\n"
        f"{preferences_context}\n"
    )


def measure_tokens_mem0(pref_mgr, task_request: Dict, call_groq: bool = True) -> Dict:
    h("GROUP C — Token Count  WITH Mem0  (selective retrieval)")

    query = task_request.get("confirmation", "")
    ok(f"Querying Mem0 for: '{query[:65]}'")

    t0 = time.perf_counter()
    memories = pref_mgr.get_relevant_preferences(query, limit=10, min_score=0.25)
    retrieval_ms = round((time.perf_counter() - t0) * 1000, 1)

    prefs_ctx  = (pref_mgr.format_for_llm(memories) if memories
                  else "No stored user preferences.")
    prompt     = _rebuild_decompose_prompt(task_request, prefs_ctx)
    est_tokens = count_tokens(prompt)

    ok(f"Retrieved {len(memories) if memories else 0} memories in {retrieval_ms} ms")
    p()
    row("Preferences context (chars)",   f"{len(prefs_ctx):,}")
    row("Memories injected",             f"{len(memories) if memories else 0}")
    row("Estimated prompt tokens",       f"{est_tokens:,}", G)

    groq_actual: Optional[Dict] = None
    if call_groq:
        ok("Calling Groq to capture real response_metadata.token_usage ...")
        try:
            from agents.coordinator_agent.config.settings import LLM_MODEL, GROQ_API_KEY
            from langchain_groq import ChatGroq
            import asyncio

            llm = ChatGroq(
                model=LLM_MODEL, temperature=0.1, max_tokens=32,
                groq_api_key=GROQ_API_KEY
            )
            loop     = asyncio.new_event_loop()
            response = loop.run_until_complete(
                llm.ainvoke(prompt + "\n\nReturn ONLY: []\n")
            )
            loop.close()

            if hasattr(response, "response_metadata"):
                usage = response.response_metadata.get("token_usage", {})
                groq_actual = {
                    "prompt_tokens":     usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens":      usage.get("total_tokens", 0),
                }
                ok(
                    f"Groq actual  ->  prompt: {groq_actual['prompt_tokens']:,}  "
                    f"completion: {groq_actual['completion_tokens']:,}  "
                    f"total: {groq_actual['total_tokens']:,}"
                )
        except Exception as e:
            warn(f"Groq live call skipped: {e}")

    return {
        "prefs_context_chars": len(prefs_ctx),
        "n_memories_injected": len(memories) if memories else 0,
        "estimated_tokens":    est_tokens,
        "groq_actual":         groq_actual,
        "retrieval_ms":        retrieval_ms,
    }


def measure_tokens_baseline(baseline_mgr: BaselineMemoryManager,
                             task_request: Dict,
                             call_groq: bool = True) -> Dict:
    """
    Token count for the BASELINE.
    Loads the entire conversation history from our baseline MongoDB collection
    and injects it wholesale — exactly what happened before Mem0.
    """
    h("GROUP C — Token Count  BASELINE  (full history injection)")

    history_str = baseline_mgr.load_context()

    if not history_str:
        warn(f"No baseline conversation history found for user_id='{USER_ID}'")
        warn("Run:  python -m eval.memory_metrics_harness --seed_baseline")
        return {"skipped": True, "reason": "no baseline history found"}

    baseline_prompt = _rebuild_decompose_prompt(task_request, history_str)
    history_tokens  = count_tokens(history_str)
    total_tokens    = count_tokens(baseline_prompt)

    ok(f"Loaded {baseline_mgr.count_messages()} messages "
       f"from {baseline_mgr.count_sessions()} sessions")
    p()
    row("Messages in history",         f"{baseline_mgr.count_messages()}")
    row("Sessions in history",         f"{baseline_mgr.count_sessions()}")
    row("History string (chars)",      f"{len(history_str):,}")
    row("History tokens alone",        f"{history_tokens:,}")
    row("Full baseline prompt tokens", f"{total_tokens:,}", R)
    p()
    warn("This grows with every message — unbounded without Mem0.")

    groq_actual: Optional[Dict] = None
    if call_groq:
        ok("Calling Groq to capture real response_metadata.token_usage ...")
        try:
            from agents.coordinator_agent.config.settings import LLM_MODEL, GROQ_API_KEY
            from langchain_groq import ChatGroq
            import asyncio

            llm = ChatGroq(
                model=LLM_MODEL, temperature=0.1, max_tokens=32,
                groq_api_key=GROQ_API_KEY
            )
            loop     = asyncio.new_event_loop()
            response = loop.run_until_complete(
                llm.ainvoke(baseline_prompt + "\n\nReturn ONLY: []\n")
            )
            loop.close()

            if hasattr(response, "response_metadata"):
                usage = response.response_metadata.get("token_usage", {})
                groq_actual = {
                    "prompt_tokens":     usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens":      usage.get("total_tokens", 0),
                }
                ok(
                    f"Groq actual  ->  prompt: {groq_actual['prompt_tokens']:,}  "
                    f"completion: {groq_actual['completion_tokens']:,}  "
                    f"total: {groq_actual['total_tokens']:,}"
                )
        except Exception as e:
            warn(f"Groq live call skipped: {e}")

    return {
        "n_messages":       baseline_mgr.count_messages(),
        "n_sessions":       baseline_mgr.count_sessions(),
        "history_chars":    len(history_str),
        "history_tokens":   history_tokens,
        "estimated_tokens": total_tokens,
        "groq_actual":      groq_actual,
    }


def compare_tokens(tok_mem0: Dict, tok_base: Dict) -> Dict:
    h("GROUP C — Token Comparison   Baseline vs Mem0")

    if tok_mem0.get("skipped") or tok_base.get("skipped"):
        warn("One or both token measurements were skipped.")
        return {}

    # Prefer real Groq counts over estimates
    mem0_tok = (
        (tok_mem0.get("groq_actual") or {}).get("prompt_tokens")
        or tok_mem0.get("estimated_tokens", 0)
    )
    base_tok = (
        (tok_base.get("groq_actual") or {}).get("prompt_tokens")
        or tok_base.get("estimated_tokens", 0)
    )

    if not base_tok:
        warn("Baseline token count is 0 — cannot compare.")
        return {}

    saved    = base_tok - mem0_tok
    pct_save = round(saved / base_tok * 100, 1) if base_tok else 0

    COST_PER_M = 0.59   # Groq llama-3.3-70b input cost USD/1M tokens
    base_cost  = round(base_tok / 1e6 * COST_PER_M, 6)
    mem0_cost  = round(mem0_tok / 1e6 * COST_PER_M, 6)

    p()
    print(f"  {'Metric':<48} {'Baseline':>14}  {'Mem0':>14}  {'Change':>8}")
    print(f"  {'-'*48} {'-'*14}  {'-'*14}  {'-'*8}")
    change_color = G if pct_save > 0 else R
    change_sign  = "-" if pct_save > 0 else "+"
    print(
        f"  {'Prompt tokens per decompose call':<48} "
        f"{base_tok:>14,}  {mem0_tok:>14,}  "
        f"{change_color}{change_sign}{abs(pct_save)}%{X}"
    )
    print(
        f"  {'Input cost per call  (USD, llama-3.3-70b)':<48} "
        f"${base_cost:>13.6f}  ${mem0_cost:>13.6f}  "
        f"{change_color}{change_sign}{abs(pct_save)}%{X}"
    )
    print(
        f"  {'Tokens saved per call':<48} "
        f"{'':>14}  {'':>14}  {change_color}{saved:,}{X}"
    )
    p()

    if pct_save >= 80:
        ok(f"{pct_save}% token reduction — matches Mem0 paper ~90% claim")
    elif pct_save >= 50:
        ok(f"{pct_save}% token reduction — solid improvement over baseline")
    elif pct_save > 0:
        warn(f"{pct_save}% token reduction — lower than expected; "
             "seed more baseline sessions to widen the gap.")
    else:
        warn("No saving detected — baseline history may be too short.")
        warn("Add more sessions via --seed_baseline and re-run.")

    return {
        "baseline_tokens": base_tok,
        "mem0_tokens":     mem0_tok,
        "saved_tokens":    saved,
        "pct_saved":       pct_save,
        "baseline_cost":   base_cost,
        "mem0_cost":       mem0_cost,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY TABLE
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(lr_m, lr_b, lw_m, lw_b, le, rec, tok_m, tok_b, cmp_tok):
    h("COPY-PASTE SUMMARY  ->  paste into Experiment Log Results Table")
    p()
    print(f"  {'Metric':<55} {'Baseline':>16}  {'Mem0':>16}  {'Change':>10}")
    print(f"  {'-'*55} {'-'*16}  {'-'*16}  {'-'*10}")

    def _row(m, bv, mv, ch=""):
        print(f"  {m:<55} {bv:>16}  {mv:>16}  {ch:>10}")

    # READ latency
    if not lr_m.get("skipped") and not lr_b.get("skipped"):
        delta_r   = lr_m['p50_ms'] - lr_b['p50_ms']
        sign_r    = f"+{delta_r:.0f}ms" if delta_r > 0 else f"{delta_r:.0f}ms"
        delta_r95 = lr_m['p95_ms'] - lr_b['p95_ms']
        sign_r95  = f"+{delta_r95:.0f}ms" if delta_r95 > 0 else f"{delta_r95:.0f}ms"
        _row("READ latency p50",
             f"{lr_b['p50_ms']} ms",  f"{lr_m['p50_ms']} ms",  sign_r)
        _row("READ latency p95",
             f"{lr_b['p95_ms']} ms",  f"{lr_m['p95_ms']} ms",  sign_r95)

    # WRITE latency
    if not lw_m.get("skipped") and not lw_b.get("skipped"):
        delta_w = lw_m['p50_ms'] - lw_b['p50_ms']
        sign_w  = f"+{delta_w:.0f}ms" if delta_w > 0 else f"{delta_w:.0f}ms"
        _row("WRITE latency p50",
             f"{lw_b['p50_ms']} ms",  f"{lw_m['p50_ms']} ms",  sign_w)

    # Embed overhead
    if not le.get("skipped"):
        _row("Embed overhead per READ (3x p50 embed)",
             "0 ms",  f"~{round(le['p50_ms']*3,1)} ms",  "NEW")

    # Token counts
    if cmp_tok:
        _row("Prompt tokens per decompose call",
             f"{cmp_tok['baseline_tokens']:,} tok",
             f"{cmp_tok['mem0_tokens']:,} tok",
             f"-{cmp_tok['pct_saved']}%")

    # Recall
    if not rec.get("skipped"):
        _row("Cross-session Recall@1",
             "100% (all returned)",  f"{rec['recall_at_1']}%",  "selective")
        _row("Cross-session Recall@5",
             "100%",  f"{rec['recall_at_5']}%",  "selective")
        _row("Cross-session Recall@10",
             "100%",  f"{rec['recall_at_10']}%", "selective")

    # Context growth
    if not lr_b.get("skipped"):
        _row("Avg context returned (chars)",
             f"{int(lr_b.get('avg_chars',0)):,} chars",
             f"{tok_m.get('prefs_context_chars',0):,} chars",
             "")

    p()
    ok("Copy the rows above into the Results Table in memory_experiment_log.docx")


# ══════════════════════════════════════════════════════════════════════════════
#  SAVE
# ══════════════════════════════════════════════════════════════════════════════

def save(out_dir: Path, data: Dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"memory_metrics_{ts}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    ok(f"JSON saved -> {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="YUSR Memory Metrics Harness — Mem0 vs Baseline"
    )
    ap.add_argument("--user_id",        default=None,
                    help=f"Override USER_ID constant (default: '{USER_ID}')")
    ap.add_argument("--runs",           type=int, default=30,
                    help="Timed retrieval calls for latency (default: 30)")
    ap.add_argument("--write_runs",     type=int, default=10,
                    help="Timed write calls (default: 10)")
    ap.add_argument("--no_groq",        action="store_true",
                    help="Skip live Groq call — use token estimates only")
    ap.add_argument("--list_prefs",     action="store_true",
                    help="Print stored Mem0 preferences and exit")
    ap.add_argument("--list_baseline",  action="store_true",
                    help="Print stored baseline conversations and exit")
    ap.add_argument("--seed_prefs",     action="store_true",
                    help="Seed SEED_PREFERENCES into Mem0 and exit")
    ap.add_argument("--seed_baseline",  action="store_true",
                    help="Seed SEED_BASELINE_SESSIONS into MongoDB and exit")
    ap.add_argument("--clear_baseline", action="store_true",
                    help="Delete all baseline data for USER_ID and exit")
    ap.add_argument("--output",         default=None,
                    help="Output dir for JSON results (default: backend/eval/results/)")
    args = ap.parse_args()

    effective_user_id = args.user_id if args.user_id else USER_ID

    # banner
    print(f"\n{B}{'='*68}")
    print(f"  YUSR AI — Memory Metrics Harness  (Mem0 vs Baseline)")
    print(f"  user_id    : {effective_user_id}")
    print(f"  date       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  backend    : {BACKEND_DIR}")
    print(f"  baseline   : {BASELINE_DB}.{BASELINE_COLLECTION}")
    print(f"{'='*68}{X}\n")

    # init Mem0
    ok(f"Initialising Mem0PreferenceManager ...")
    try:
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        pref_mgr = get_preference_manager(effective_user_id)
        ok("Mem0 ready")
    except Exception as e:
        err(f"Mem0 init failed: {e}")
        err("Check MONGODB_URI and GROQ_API_KEY in backend/.env")
        sys.exit(1)

    # init Baseline
    ok(f"Initialising BaselineMemoryManager ...")
    try:
        baseline_mgr = BaselineMemoryManager(effective_user_id)
        ok(f"Baseline ready  "
           f"({baseline_mgr.count_sessions()} sessions, "
           f"{baseline_mgr.count_messages()} messages)")
    except Exception as e:
        err(f"Baseline init failed: {e}")
        err("Check MONGODB_URI in backend/.env")
        sys.exit(1)

    # utility modes
    if args.seed_prefs:
        seed_mem0_preferences(pref_mgr)
        p(); ok("Done. Run --list_prefs to verify.")
        sys.exit(0)

    if args.seed_baseline:
        seed_baseline_conversations(baseline_mgr)
        sys.exit(0)

    if args.list_prefs:
        list_mem0_preferences(pref_mgr)
        sys.exit(0)

    if args.list_baseline:
        list_baseline_conversations(baseline_mgr)
        sys.exit(0)

    if args.clear_baseline:
        n = baseline_mgr.clear()
        ok(f"Deleted {n} baseline documents for user_id='{effective_user_id}'")
        sys.exit(0)

    # patch user_id into sample request
    task_req            = dict(SAMPLE_TASK_REQUEST)
    task_req["user_id"] = effective_user_id

    results: Dict = {
        "user_id":   effective_user_id,
        "timestamp": datetime.now().isoformat(),
        "runs":      args.runs,
    }

    # ── GROUP A — LATENCY ─────────────────────────────────────────────────────
    lr_mem0     = measure_read_latency_mem0(pref_mgr, args.runs)
    lr_baseline = measure_read_latency_baseline(baseline_mgr, args.runs)
    lw_mem0     = measure_write_latency_mem0(pref_mgr, args.write_runs)
    lw_baseline = measure_write_latency_baseline(baseline_mgr, args.write_runs)
    le          = measure_embed_latency(runs=20)
    cmp_lat_r   = compare_read_latency(lr_mem0, lr_baseline)
    cmp_lat_w   = compare_write_latency(lw_mem0, lw_baseline)

    results.update(
        latency_read_mem0       = lr_mem0,
        latency_read_baseline   = lr_baseline,
        latency_write_mem0      = lw_mem0,
        latency_write_baseline  = lw_baseline,
        latency_embed           = le,
        latency_comparison_read = cmp_lat_r,
        latency_comparison_write= cmp_lat_w,
    )

    # ── GROUP B — RECALL ──────────────────────────────────────────────────────
    rec = measure_recall(pref_mgr)
    results["recall"] = rec

    # ── GROUP C — TOKENS ──────────────────────────────────────────────────────
    tok_mem0     = measure_tokens_mem0(pref_mgr, task_req,
                                       call_groq=not args.no_groq)
    tok_baseline = measure_tokens_baseline(baseline_mgr, task_req,
                                           call_groq=not args.no_groq)
    cmp_tok      = compare_tokens(tok_mem0, tok_baseline)

    results.update(
        tokens_mem0       = tok_mem0,
        tokens_baseline   = tok_baseline,
        tokens_comparison = cmp_tok,
    )

    # ── SUMMARY + SAVE ────────────────────────────────────────────────────────
    print_summary(lr_mem0, lr_baseline, lw_mem0, lw_baseline, le,
                  rec, tok_mem0, tok_baseline, cmp_tok)

    out_dir = Path(args.output) if args.output else BACKEND_DIR / "eval" / "results"
    save(out_dir, results)

    h("DONE")
    ok(f"All measurements complete for user_id='{effective_user_id}'")
    p()


if __name__ == "__main__":
    main()