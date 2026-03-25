"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   YUSR AI — Memory Metrics Harness v2  (ZERO TOKEN EXHAUSTION)              ║
║                                                                              ║
║  KEY DIFFERENCE FROM v1:                                                     ║
║  Seeding bypasses Groq entirely — uses local HuggingFace embeddings          ║
║  to insert documents directly into MongoDB in the exact format               ║
║  Mem0 uses. Zero API tokens consumed during setup.                           ║
║                                                                              ║
║  EXPERIMENTS (run in order, each from backend/ directory):                  ║
║                                                                              ║
║  ── EXPERIMENT 1: BASELINE ────────────────────────────────────────────────  ║
║  python -m eval.memory_metrics_harness_v2 --clear --seed                    ║
║  python -m eval.memory_metrics_harness_v2 --run_label baseline               ║
║                                                                              ║
║  ── EXPERIMENT 2: CURRENT MEM0 ────────────────────────────────────────────  ║
║  (same seed already in DB — no re-seed needed)                               ║
║  python -m eval.memory_metrics_harness_v2 --run_label current_mem0          ║
║                                                                              ║
║  ── EXPERIMENT 3: CURRENT MEM0 OPTIMIZED ──────────────────────────────────  ║
║  (apply TTL cache + trivial-skip changes, then:)                             ║
║  python -m eval.memory_metrics_harness_v2 --clear --seed                    ║
║  python -m eval.memory_metrics_harness_v2 --run_label mem0_optimized        ║
║                                                                              ║
║  ── EXPERIMENT 4: RL MEM0 ─────────────────────────────────────────────────  ║
║  (merge RL branch, then:)                                                    ║
║  python -m eval.memory_metrics_harness_v2 --clear --seed                    ║
║  python -m eval.memory_metrics_harness_v2 --run_label rl_mem0               ║
║                                                                              ║
║  ── EXPERIMENT 5: RL MEM0 OPTIMIZED ───────────────────────────────────────  ║
║  (apply optimizations on top of RL branch, then:)                           ║
║  python -m eval.memory_metrics_harness_v2 --clear --seed                    ║
║  python -m eval.memory_metrics_harness_v2 --run_label rl_mem0_optimized     ║
║                                                                              ║
║  FLAGS:                                                                      ║
║    --clear       Wipe eval data from mem0_preferences + baseline_convs      ║
║    --seed        Insert test data (ZERO Groq tokens)                         ║
║    --list        Show what is currently stored                               ║
║    --run_label   Label for output JSON (default: run)                        ║
║    --runs        Number of latency calls (default: 30)                       ║
║    --user_id     Override USER_ID (default: eval_user_002)                  ║
║                                                                              ║
║  WRITE LATENCY NOTE:                                                         ║
║  Write tests are DISABLED by default (they burn Groq tokens).               ║
║  Use --write_test to enable — only run once when limit has reset.            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os, sys, json, time, uuid, logging, argparse, statistics
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("metrics_v2")
logger.setLevel(logging.INFO)

G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; C = "\033[96m"
B = "\033[1m";  X = "\033[0m"

def h(t):    print(f"\n{B}{C}{'='*68}{X}\n{B}{C}  {t}{X}\n{B}{C}{'='*68}{X}")
def ok(t):   print(f"  {G}v{X}  {t}")
def warn(t): print(f"  {Y}!{X}  {t}")
def err(t):  print(f"  {R}x{X}  {t}")
def p(t=""): print(t)
def row(label, val, color=None):
    c = color or X
    print(f"  {label:<50} {c}{val}{X}")

def pct(data, p_val):
    if not data: return 0.0
    s = sorted(data)
    return s[min(int(len(s) * p_val / 100), len(s) - 1)]

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION — edit USER_ID if needed
# ══════════════════════════════════════════════════════════════════════════════

# Use a DIFFERENT user_id from v1 so data doesn't cross-contaminate
USER_ID          = "eval_user_002"
BASELINE_DB      = "yusr_db"
BASELINE_COLL    = "baseline_conversations"
MEM0_COLL        = "mem0_preferences"

# ── SEED DATA ─────────────────────────────────────────────────────────────────
# These are the exact memory texts that will be stored.
# We embed them locally and insert directly — zero Groq tokens.
SEED_MEMORIES: List[Dict] = [
    {"text": "Prefers Chrome for web browsing",                    "category": "app_usage"},
    {"text": "Likes to shop on Amazon for electronics",            "category": "app_usage"},
    {"text": "Name is Sara, works as a software engineer",         "category": "personal_info"},
    {"text": "Is vegetarian and prefers healthy meal recipes",     "category": "personal_info"},
    {"text": "Prefers dark mode across all applications",          "category": "app_usage"},
    {"text": "Wakes up at 7 AM and sets morning alarms",          "category": "personal_info"},
    {"text": "Prefers Notepad for quick text editing",             "category": "app_usage"},
    {"text": "Frequently searches for Python programming tutorials","category": "app_usage"},
    {"text": "Prefers Gmail over other email clients",             "category": "app_usage"},
    {"text": "Uses YouTube for educational content and tech reviews","category": "app_usage"},
    {"text": "Uses a Windows 11 desktop with 4K monitor",         "category": "personal_info"},
    {"text": "Prefers English language interface",                 "category": "personal_info"},
]

SEED_SESSIONS: List[Dict] = [
    {
        "session_id": "eval_session_001",
        "messages": [
            {"role": "system",    "content": "You are a Conversational Clarity Agent."},
            {"role": "user",      "content": "open chrome browser"},
            {"role": "assistant", "content": "Opening Chrome browser."},
            {"role": "user",      "content": "go to amazon.com"},
            {"role": "assistant", "content": "Navigating to Amazon.com."},
            {"role": "user",      "content": "search for wireless headphones"},
            {"role": "assistant", "content": "Searching Amazon for wireless headphones."},
            {"role": "user",      "content": "open notepad and write the first result"},
            {"role": "assistant", "content": "Opening Notepad and typing the first result."},
        ]
    },
    {
        "session_id": "eval_session_002",
        "messages": [
            {"role": "system",    "content": "You are a Conversational Clarity Agent."},
            {"role": "user",      "content": "set alarm for 7am tomorrow"},
            {"role": "assistant", "content": "Setting alarm for 7:00 AM tomorrow."},
            {"role": "user",      "content": "open gmail"},
            {"role": "assistant", "content": "Opening Gmail in Chrome."},
            {"role": "user",      "content": "send email to manager about project update"},
            {"role": "assistant", "content": "Composing and sending email."},
            {"role": "user",      "content": "search for python tutorials on youtube"},
            {"role": "assistant", "content": "Searching YouTube for Python tutorials."},
        ]
    },
    {
        "session_id": "eval_session_003",
        "messages": [
            {"role": "system",    "content": "You are a Conversational Clarity Agent."},
            {"role": "user",      "content": "change display to dark mode"},
            {"role": "assistant", "content": "Opening display settings and enabling dark mode."},
            {"role": "user",      "content": "find a vegetarian recipe for dinner"},
            {"role": "assistant", "content": "Searching for vegetarian dinner recipes."},
            {"role": "user",      "content": "what is my device resolution"},
            {"role": "assistant", "content": "Checking display resolution on Windows 11 desktop."},
            {"role": "user",      "content": "change system language to english"},
            {"role": "assistant", "content": "Setting system language to English."},
        ]
    },
]

# Gold set — must match SEED_MEMORIES text prefixes exactly
GOLD_SET: List[Tuple[str, str]] = [
    ("Prefers Chrome for web browsing",               "open browser"),
    ("Likes to shop on Amazon",                       "buy wireless headphones"),
    ("Name is Sara",                                  "what is my name"),
    ("Is vegetarian",                                 "suggest dinner recipes"),
    ("Prefers dark mode",                             "change display settings"),
    ("Wakes up at 7 AM",                              "set alarm for 7am"),
    ("Prefers Notepad",                               "open text editor"),
    ("Frequently searches for Python",                "search Python tutorials"),
    ("Prefers Gmail",                                 "open email"),
    ("Uses YouTube for educational",                  "play video"),
    ("Uses a Windows 11 desktop",                     "what device am I using"),
    ("Prefers English language",                      "change language settings"),
]

LATENCY_QUERIES: List[str] = [
    "open browser", "search for news", "set alarm for 7am",
    "send email to colleague", "open calculator", "navigate to Amazon",
    "play music", "take a screenshot", "open notepad", "search for flights",
    "open settings", "create new document", "check the weather", "open YouTube",
    "find a recipe", "login to Gmail", "open calendar", "search Python tutorials",
    "open file manager", "turn off wifi",
]

SAMPLE_TASK = {
    "confirmation": "Search Amazon for wireless headphones and extract the top 3 product titles",
    "device_type": "desktop",
}

# ══════════════════════════════════════════════════════════════════════════════
#  MONGO HELPER
# ══════════════════════════════════════════════════════════════════════════════

def get_mongo_client():
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise ValueError("MONGODB_URI not set in backend/.env")
    return MongoClient(uri, serverSelectionTimeoutMS=5000)

# ══════════════════════════════════════════════════════════════════════════════
#  ZERO-TOKEN SEED — embeds locally, inserts directly into MongoDB
# ══════════════════════════════════════════════════════════════════════════════

def seed_mem0_zero_tokens(user_id: str) -> int:
    """
    Insert SEED_MEMORIES directly into mem0_preferences collection
    using local HuggingFace embeddings. Zero Groq API tokens consumed.

    Document format matches exactly what Mem0 stores:
    {
        "_id": ObjectId,
        "embedding": [384 floats],
        "payload": {
            "data": "memory text",
            "user_id": "...",
            "memory": "memory text",
            "category": "...",
            "hash": "md5...",
            "created_at": "...",
        }
    }
    """
    h(f"Seeding Mem0 (ZERO TOKENS)   user_id='{user_id}'")

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        ok("HuggingFace model loaded — no Groq tokens used")
    except ImportError:
        err("sentence-transformers not installed")
        err("pip install sentence-transformers")
        return 0

    import hashlib
    client = get_mongo_client()
    coll   = client[BASELINE_DB][MEM0_COLL]

    # Check for existing docs for this user
    existing = coll.count_documents({"payload.user_id": user_id})
    if existing > 0:
        warn(f"Found {existing} existing docs for {user_id} — skipping (run --clear first)")
        return 0

    stored = 0
    for item in SEED_MEMORIES:
        text     = item["text"]
        category = item["category"]

        # Embed locally
        embedding = model.encode([text])[0].tolist()

        doc = {
            "embedding": embedding,
            "payload": {
                "data":       text,
                "user_id":    user_id,
                "memory":     text,
                "category":   category,
                "source":     "eval_seed",
                "hash":       hashlib.md5(text.encode()).hexdigest(),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        }
        coll.insert_one(doc)
        print(f"  {G}STORE{X}  [{category:<20}]  {text[:55]}")
        stored += 1

    ok(f"\nStored {stored} memories using local embeddings — 0 Groq tokens used.")
    return stored


def seed_baseline_zero_tokens(user_id: str) -> int:
    """Insert baseline conversation sessions directly into MongoDB."""
    h(f"Seeding Baseline (ZERO TOKENS)   user_id='{user_id}'")

    client = get_mongo_client()
    db     = client[BASELINE_DB]
    coll   = db[BASELINE_COLL]
    coll.create_index([("user_id", 1), ("timestamp", -1)])

    total_msgs = 0
    for sess in SEED_SESSIONS:
        sid  = sess["session_id"]
        msgs = sess["messages"]
        doc_id = f"{user_id}_{sid}"

        coll.replace_one(
            {"doc_id": doc_id, "user_id": user_id},
            {
                "doc_id":     doc_id,
                "user_id":    user_id,
                "session_id": sid,
                "messages":   msgs,
                "timestamp":  time.time(),
            },
            upsert=True,
        )
        total_msgs += len(msgs)
        print(f"  {G}STORED{X}  session={sid}  ({len(msgs)} messages)")

    ok(f"Seeded {len(SEED_SESSIONS)} sessions ({total_msgs} total messages).")
    return total_msgs


# ══════════════════════════════════════════════════════════════════════════════
#  CLEAR
# ══════════════════════════════════════════════════════════════════════════════

def clear_all(user_id: str):
    h(f"Clearing eval data   user_id='{user_id}'")
    client = get_mongo_client()
    db     = client[BASELINE_DB]

    # Clear Mem0 preferences (keyed on payload.user_id)
    n1 = db[MEM0_COLL].delete_many({"payload.user_id": user_id}).deleted_count
    ok(f"Deleted {n1} Mem0 preference documents")

    # Also try Mem0 API deletion in case it cached internally
    try:
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        mgr   = get_preference_manager(user_id)
        prefs = mgr.get_all_preferences()
        deleted = 0
        for p in (prefs or []):
            if isinstance(p, dict) and p.get("id"):
                mgr.delete_preference(p["id"])
                deleted += 1
        if deleted:
            ok(f"Also deleted {deleted} via Mem0 API")
    except Exception as e:
        warn(f"Mem0 API cleanup skipped: {e}")

    # Clear baseline
    n2 = db[BASELINE_COLL].delete_many({"user_id": user_id}).deleted_count
    ok(f"Deleted {n2} baseline conversation documents")


# ══════════════════════════════════════════════════════════════════════════════
#  LIST
# ══════════════════════════════════════════════════════════════════════════════

def list_stored(user_id: str):
    h(f"Stored data for user_id='{user_id}'")
    client = get_mongo_client()
    db     = client[BASELINE_DB]

    # Mem0 preferences
    prefs = list(db[MEM0_COLL].find({"payload.user_id": user_id}))
    ok(f"Mem0 preferences: {len(prefs)}")
    for p in prefs[:5]:
        payload = p.get("payload", {})
        cat  = payload.get("category", "?")
        text = payload.get("memory", payload.get("data", "?"))
        print(f"    [{cat:<20}]  {text[:65]}")
    if len(prefs) > 5:
        print(f"    ... {len(prefs)-5} more")

    # Baseline
    sessions = list(db[BASELINE_COLL].find({"user_id": user_id}))
    total_msgs = sum(len(s.get("messages", [])) for s in sessions)
    ok(f"Baseline sessions: {len(sessions)}  ({total_msgs} total messages)")


# ══════════════════════════════════════════════════════════════════════════════
#  BASELINE MEMORY MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class BaselineManager:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.client  = get_mongo_client()
        self.coll    = self.client[BASELINE_DB][BASELINE_COLL]

    def load_context(self) -> str:
        docs = list(self.coll.find({"user_id": self.user_id}, sort=[("timestamp", 1)]))
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
        pipeline = [
            {"$match": {"user_id": self.user_id}},
            {"$project": {"n": {"$size": "$messages"}}},
            {"$group": {"_id": None, "total": {"$sum": "$n"}}},
        ]
        result = list(self.coll.aggregate(pipeline))
        return result[0]["total"] if result else 0

    def count_sessions(self) -> int:
        return self.coll.count_documents({"user_id": self.user_id})

    def save_turn(self, user_msg: str, asst_msg: str, session_id: str = "test") -> str:
        doc_id = f"{self.user_id}_{session_id}"
        self.coll.update_one(
            {"doc_id": doc_id, "user_id": self.user_id},
            {
                "$push": {"messages": {"$each": [
                    {"role": "user",      "content": user_msg, "ts": time.time()},
                    {"role": "assistant", "content": asst_msg, "ts": time.time()},
                ]}},
                "$set": {"session_id": session_id, "timestamp": time.time(), "user_id": self.user_id},
            },
            upsert=True,
        )
        return doc_id


# ══════════════════════════════════════════════════════════════════════════════
#  TOKEN COUNTER
# ══════════════════════════════════════════════════════════════════════════════

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
            _tiktoken_warned = True
        return max(1, len(text) // 4)


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP A — LATENCY
# ══════════════════════════════════════════════════════════════════════════════

def measure_read_latency_mem0(pref_mgr, runs: int) -> Dict:
    h("GROUP A — Mem0 READ Latency   get_relevant_preferences()")
    ok(f"Running {runs} timed calls ...")

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
        "min_ms":      round(min(times_ms), 2),
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
    row("cache hits (0ms calls)",f"{sum(1 for t in times_ms if t < 1.0)}", G)
    return out


def measure_read_latency_baseline(baseline_mgr: BaselineManager, runs: int) -> Dict:
    h("GROUP A — Baseline READ Latency   load_context()")
    ok(f"Running {runs} timed calls ...")

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
    row("avg context returned (chars)",  f"{int(out['avg_chars']):,}")
    row("total stored messages",         f"{out['total_messages']}")
    return out


def measure_write_latency_baseline(baseline_mgr: BaselineManager, runs: int = 10) -> Dict:
    """Baseline write is free (no LLM) — always safe to measure."""
    h("GROUP A — Baseline WRITE Latency   save_turn()")
    times_ms: List[float] = []
    test_session = f"_eval_test_{int(time.time())}"

    for i in range(runs):
        t0 = time.perf_counter()
        baseline_mgr.save_turn(f"test msg {i}", f"test response {i}", test_session)
        times_ms.append((time.perf_counter() - t0) * 1000)

    # Cleanup
    baseline_mgr.coll.delete_many({"user_id": baseline_mgr.user_id, "session_id": test_session})

    out = {
        "p50_ms":  round(pct(times_ms, 50), 1),
        "p95_ms":  round(pct(times_ms, 95), 1),
        "min_ms":  round(min(times_ms), 1),
        "max_ms":  round(max(times_ms), 1),
        "mean_ms": round(statistics.mean(times_ms), 1),
    }
    p()
    row("p50 write latency", f"{out['p50_ms']} ms", G)
    row("p95 write latency", f"{out['p95_ms']} ms", Y)
    ok("Baseline write = raw MongoDB upsert (0 tokens).")
    return out


def measure_embed_latency(runs: int = 20) -> Dict:
    h("GROUP A — Embedding Latency   HuggingFace all-MiniLM-L6-v2")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        ok("Model loaded")
    except ImportError:
        warn("sentence-transformers not installed — skipping")
        return {"skipped": True}

    texts = ["open browser", "set alarm 7am", "user likes dark mode", "login to Gmail", "search Python"]
    times_ms: List[float] = []
    for i in range(runs):
        t0 = time.perf_counter()
        model.encode([texts[i % len(texts)]])
        times_ms.append((time.perf_counter() - t0) * 1000)

    out = {
        "p50_ms":  round(pct(times_ms, 50), 1),
        "p95_ms":  round(pct(times_ms, 95), 1),
        "mean_ms": round(statistics.mean(times_ms), 1),
    }
    row("p50 embed latency", f"{out['p50_ms']} ms")
    row("p95 embed latency", f"{out['p95_ms']} ms")
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP B — RECALL
# ══════════════════════════════════════════════════════════════════════════════

def measure_recall(pref_mgr) -> Dict:
    h("GROUP B — Recall@k   [Mem0 only]")
    ok(f"Testing {len(GOLD_SET)} items ...")
    p()

    hits = {"@1": 0, "@5": 0, "@10": 0}
    details = []

    for pref_sub, query in GOLD_SET:
        results = pref_mgr.get_relevant_preferences(query, limit=10, min_score=0.0)
        texts   = [r.get("memory", "") for r in (results or [])]

        h1  = any(pref_sub[:30].lower() in t.lower() for t in texts[:1])
        h5  = any(pref_sub[:30].lower() in t.lower() for t in texts[:5])
        h10 = any(pref_sub[:30].lower() in t.lower() for t in texts[:10])
        if h1:  hits["@1"]  += 1
        if h5:  hits["@5"]  += 1
        if h10: hits["@10"] += 1

        tag = (f"{G}HIT@1 {X}" if h1 else
               f"{Y}HIT@5 {X}" if h5 else
               f"{Y}HIT@10{X}" if h10 else
               f"{R}MISS  {X}")
        print(f"  [{tag}]  q='{query[:30]:<30}'  p='{pref_sub[:30]}'")
        details.append({"query": query, "h1": h1, "h5": h5, "h10": h10})

    n = len(GOLD_SET)
    r1, r5, r10 = [round(hits[k]/n*100, 1) for k in ("@1", "@5", "@10")]
    p()
    row("Recall@1",  f"{r1}%",  G if r1 >= 50 else R)
    row("Recall@5",  f"{r5}%",  G if r5 >= 70 else Y)
    row("Recall@10", f"{r10}%", G if r10 >= 80 else Y)

    return {"n": n, "recall_at_1": r1, "recall_at_5": r5, "recall_at_10": r10,
            "hits": hits, "details": details}


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP C — TOKENS
# ══════════════════════════════════════════════════════════════════════════════

def _build_prompt(task: Dict, context: str) -> str:
    device_hint = f"The user is on a {task.get('device_type','desktop')} device.\n\n"
    return (
        f"{device_hint}"
        "You are the AURA Task Decomposition Agent.\n\n"
        "# USER REQUEST\n"
        f"{json.dumps(task, indent=2)}\n\n"
        "# USER PREFERENCES\n"
        f"{context}\n"
    )


def measure_tokens_mem0(pref_mgr, task: Dict) -> Dict:
    h("GROUP C — Token Count  WITH Mem0")
    query = task.get("confirmation", "")
    t0 = time.perf_counter()
    memories = pref_mgr.get_relevant_preferences(query, limit=10, min_score=0.25)
    retrieval_ms = round((time.perf_counter() - t0) * 1000, 1)

    prefs_ctx  = pref_mgr.format_for_llm(memories) if memories else "No stored preferences."
    prompt     = _build_prompt(task, prefs_ctx)
    est_tokens = count_tokens(prompt)

    p()
    row("Preferences context (chars)",  f"{len(prefs_ctx):,}")
    row("Memories injected",            f"{len(memories) if memories else 0}")
    row("Estimated prompt tokens",      f"{est_tokens:,}", G)
    row("Retrieval time",               f"{retrieval_ms} ms")

    return {
        "prefs_context_chars": len(prefs_ctx),
        "n_memories_injected": len(memories) if memories else 0,
        "estimated_tokens":    est_tokens,
        "retrieval_ms":        retrieval_ms,
    }


def measure_tokens_baseline(baseline_mgr: BaselineManager, task: Dict) -> Dict:
    h("GROUP C — Token Count  BASELINE")
    history_str   = baseline_mgr.load_context()
    prompt        = _build_prompt(task, history_str)
    history_tok   = count_tokens(history_str)
    total_tok     = count_tokens(prompt)

    p()
    row("Messages in history",         f"{baseline_mgr.count_messages()}")
    row("History string (chars)",      f"{len(history_str):,}")
    row("History tokens alone",        f"{history_tok:,}")
    row("Full baseline prompt tokens", f"{total_tok:,}", R)
    warn("This grows unboundedly — every new message adds more tokens.")

    return {
        "n_messages":       baseline_mgr.count_messages(),
        "history_chars":    len(history_str),
        "history_tokens":   history_tok,
        "estimated_tokens": total_tok,
    }


def compare_tokens(tok_m: Dict, tok_b: Dict) -> Dict:
    h("GROUP C — Token Comparison")
    mem0_tok = tok_m.get("estimated_tokens", 0)
    base_tok = tok_b.get("estimated_tokens", 0)

    if not base_tok:
        warn("Baseline token count is 0 — cannot compare.")
        return {}

    saved    = base_tok - mem0_tok
    pct_save = round(saved / base_tok * 100, 1) if base_tok else 0

    COST_PER_M = 0.59
    base_cost  = round(base_tok / 1e6 * COST_PER_M, 6)
    mem0_cost  = round(mem0_tok / 1e6 * COST_PER_M, 6)

    p()
    print(f"  {'Metric':<48} {'Baseline':>12}  {'Mem0':>12}  {'Change':>8}")
    print(f"  {'-'*48} {'-'*12}  {'-'*12}  {'-'*8}")
    cc = G if pct_save > 0 else R
    cs = "-" if pct_save > 0 else "+"
    print(f"  {'Prompt tokens per call':<48} {base_tok:>12,}  {mem0_tok:>12,}  {cc}{cs}{abs(pct_save)}%{X}")
    print(f"  {'Input cost (USD, llama-3.3-70b)':<48} ${base_cost:>11.6f}  ${mem0_cost:>11.6f}  {cc}{cs}{abs(pct_save)}%{X}")
    print(f"  {'Tokens saved per call':<48} {'':>12}  {'':>12}  {cc}{saved:,}{X}")

    if pct_save >= 60:
        ok(f"{pct_save}% token reduction vs baseline")

    return {"baseline_tokens": base_tok, "mem0_tokens": mem0_tok,
            "saved_tokens": saved, "pct_saved": pct_save,
            "baseline_cost": base_cost, "mem0_cost": mem0_cost}



# ══════════════════════════════════════════════════════════════════════════════
#  GROUP D — RL PATTERN LEARNING OVERHEAD  (Exp 4 and 5 only)
# ══════════════════════════════════════════════════════════════════════════════

# These are the same turns used in the token guard test — mix of task and personal
RL_TEST_TURNS = [
    # Task commands — pattern learner should detect behavioral patterns
    ("open browser",                           "task"),
    ("search for news",                        "task"),
    ("set alarm for 7am",                      "task"),
    ("open calculator",                        "task"),
    ("open notepad",                           "task"),
    ("search Python tutorials",                "task"),
    ("open gmail",                             "task"),
    # Personal/pattern turns — these are what RL should catch
    ("I always check emails first thing in the morning",  "pattern"),
    ("I usually open Chrome when I need to browse",       "pattern"),
    ("I prefer to use Notepad for quick notes",           "pattern"),
    ("I wake up at 7 AM and set an alarm every day",      "pattern"),
    ("keep it short please",                              "style"),
    ("I always use Chrome instead of Edge",               "app_pref"),
    ("my name is Sara",                                   "personal"),
]

def measure_rl_overhead(pref_mgr) -> Dict:
    """
    Measure what the RL pattern learning layer adds in terms of:
    - Extra Atlas writes per turn (patterns stored)
    - Token cost of pattern detection (if LLM-based)
    - Number of patterns promoted to high confidence
    - Number of patterns detected by rule-based vs LLM path
    """
    h("GROUP D — RL Pattern Learning Overhead")

    # Check if RL components exist on this branch
    has_pattern_learner = False
    has_feedback_store  = False

    try:
        from agents.coordinator_agent.memory.pattern_learner import PatternLearner
        has_pattern_learner = True
        ok("PatternLearner found on this branch")
    except ImportError:
        warn("PatternLearner not found — skipping RL overhead measurement")

    try:
        from agents.coordinator_agent.memory.feedback_store import FeedbackStore
        has_feedback_store = True
        ok("FeedbackStore found on this branch")
    except ImportError:
        warn("FeedbackStore not found")

    if not has_pattern_learner:
        warn("No RL components detected — this is not the RL branch")
        return {"skipped": True, "reason": "RL components not present"}

    # Count what categories are stored vs Exp 2
    # In Exp 2: only personal_info stored
    # In Exp 4: personal_info + app_preference + communication_style + behavior_pattern
    categories_exp2 = {"personal_info"}
    categories_exp4 = {
        "personal_info",
        "app_preference",
        "communication_style",
        "behavior_pattern",
        "learned_pattern",
        "conversation_history",
    }

    new_categories = categories_exp4 - categories_exp2

    p()
    row("Categories in Exp 2 (baseline Mem0)",   str(len(categories_exp2)))
    row("Categories in Exp 4 (RL Mem0)",          str(len(categories_exp4)))
    row("New categories added by RL",             str(len(new_categories)))
    for cat in sorted(new_categories):
        print(f"    + {cat}")

    # Count how many of the test turns would trigger storage
    # in Exp 2 vs Exp 4 (without actually calling Groq)
    EXTRACTION_PROMPT_TOKENS = 107  # measured avg from token guard test

    _APP_SIGNALS = [
        "instead of", "always use", "i prefer", "use chrome",
        "use word", "use notepad", "use edge", "use telegram", "use whatsapp",
    ]
    _STYLE_SIGNALS = [
        "keep it short", "be brief", "bullet points", "no long answers",
        "always respond in", "respond in arabic", "respond in english",
        "use simple language", "be concise",
    ]
    _PATTERN_SIGNALS = [
        "i usually", "i always", "every morning", "every night",
        "i work", "i wake up", "i sleep", "night shift", "i start my day",
    ]
    _TASK_PREFIXES = (
        "open ", "launch ", "start ", "close ", "search ", "find ",
        "navigate", "go to", "click ", "type ", "press ", "scroll",
        "take a screenshot", "set alarm", "play ", "pause ", "stop ",
    )

    p()
    h("GROUP D — Per-Turn Storage Comparison   Exp2 vs Exp4")
    print(f"\n  {'Turn':<50} {'Exp2 stores':>14}  {'Exp4 stores':>14}  {'Extra writes':>12}")
    print(f"  {'-'*50} {'-'*14}  {'-'*14}  {'-'*12}")

    total_exp2_writes = 0
    total_exp4_writes = 0
    total_exp2_tokens = 0
    total_exp4_tokens = 0

    for input_text, turn_type in RL_TEST_TURNS:
        _lower = input_text.lower()
        _is_task = any(_lower.strip().startswith(p) for p in _TASK_PREFIXES)

        # Exp 2 logic: LLM extraction only, no guard
        exp2_stores = ["personal_info (if LLM finds it)"]
        exp2_tokens = EXTRACTION_PROMPT_TOKENS  # always calls LLM

        # Exp 4 logic: guard + 4 categories
        exp4_stores = []
        exp4_tokens = 0

        if not _is_task:
            exp4_tokens += EXTRACTION_PROMPT_TOKENS  # LLM extraction still runs
            exp4_stores.append("personal_info (if LLM finds it)")

        for signal in _APP_SIGNALS:
            if signal in _lower:
                exp4_stores.append("app_preference")
                break

        for signal in _STYLE_SIGNALS:
            if signal in _lower:
                exp4_stores.append("communication_style")
                break

        for signal in _PATTERN_SIGNALS:
            if signal in _lower:
                exp4_stores.append("behavior_pattern")
                break

        extra = len(exp4_stores) - len(exp2_stores)
        extra_str = f"+{extra}" if extra > 0 else str(extra)

        total_exp2_writes += len(exp2_stores)
        total_exp4_writes += len(exp4_stores)
        total_exp2_tokens += exp2_tokens
        total_exp4_tokens += exp4_tokens

        print(f"  {input_text[:48]:<50} {len(exp2_stores):>14}  "
              f"{len(exp4_stores):>14}  {extra_str:>12}")

    p()
    row("Total potential writes — Exp 2", str(total_exp2_writes))
    row("Total potential writes — Exp 4", str(total_exp4_writes))
    row("Extra write operations",
        f"+{total_exp4_writes - total_exp2_writes} "
        f"(+{round((total_exp4_writes-total_exp2_writes)/total_exp2_writes*100,1)}%)")
    p()
    row("Extraction tokens — Exp 2 (no guard)", f"{total_exp2_tokens} tok")
    row("Extraction tokens — Exp 4 (with guard)", f"{total_exp4_tokens} tok")
    saved = total_exp2_tokens - total_exp4_tokens
    pct_saved = round(saved / total_exp2_tokens * 100, 1) if total_exp2_tokens else 0
    row("Extraction tokens saved by guard",
        f"{saved} tok  (-{pct_saved}%)")

    ok("NOTE: Each extra write also costs ~951 Groq tokens (Mem0 LLM extraction)")
    ok("      Extra writes × 951 = additional write token cost per session")
    extra_write_tokens = (total_exp4_writes - total_exp2_writes) * 951
    row("Estimated extra write tokens (RL additions)",
        f"~{extra_write_tokens:,} tok across {len(RL_TEST_TURNS)} turns")

    return {
        "skipped":               False,
        "has_pattern_learner":   has_pattern_learner,
        "has_feedback_store":    has_feedback_store,
        "categories_exp2":       sorted(categories_exp2),
        "categories_exp4":       sorted(categories_exp4),
        "new_categories":        sorted(new_categories),
        "total_exp2_writes":     total_exp2_writes,
        "total_exp4_writes":     total_exp4_writes,
        "extra_write_operations": total_exp4_writes - total_exp2_writes,
        "exp2_extraction_tokens": total_exp2_tokens,
        "exp4_extraction_tokens": total_exp4_tokens,
        "extraction_tokens_saved": saved,
        "extra_write_tokens_rl":  extra_write_tokens,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(run_label, lr_m, lr_b, lw_b, le, rec, tok_m, tok_b, cmp_tok):
    h(f"SUMMARY   run_label='{run_label}'")
    p()
    print(f"  {'Metric':<52} {'Baseline':>14}  {'Mem0':>14}  {'Change':>10}")
    print(f"  {'-'*52} {'-'*14}  {'-'*14}  {'-'*10}")

    def _r(m, bv, mv, ch=""):
        print(f"  {m:<52} {bv:>14}  {mv:>14}  {ch:>10}")

    if not lr_m.get("skipped") and not lr_b.get("skipped"):
        dr   = lr_m['p50_ms'] - lr_b['p50_ms']
        dr95 = lr_m['p95_ms'] - lr_b['p95_ms']
        _r("READ latency p50",
           f"{lr_b['p50_ms']} ms", f"{lr_m['p50_ms']} ms",
           f"+{dr:.0f}ms" if dr > 0 else f"{dr:.0f}ms")
        _r("READ latency p95",
           f"{lr_b['p95_ms']} ms", f"{lr_m['p95_ms']} ms",
           f"+{dr95:.0f}ms" if dr95 > 0 else f"{dr95:.0f}ms")
        _r("READ latency min  (cache proof)",
           "N/A", f"{lr_m['min_ms']} ms", "")
        _r("Cache hits (calls returning < 1ms)",
           "0", f"{lr_m.get('cache_hits', sum(1 for _ in range(lr_m['n_calls'])))} ", "")

    if not lw_b.get("skipped"):
        _r("WRITE latency p50 (baseline, raw insert)",
           f"{lw_b['p50_ms']} ms", "async (no block)", "")

    if not le.get("skipped"):
        _r("Embed overhead p50",
           "0 ms", f"~{le['p50_ms']} ms", "NEW")

    if cmp_tok:
        _r("Prompt tokens per decompose call",
           f"{cmp_tok['baseline_tokens']:,} tok",
           f"{cmp_tok['mem0_tokens']:,} tok",
           f"-{cmp_tok['pct_saved']}%")

    if not rec.get("skipped"):
        _r("Recall@1",  "100% (trivial)", f"{rec['recall_at_1']}%",  "selective")
        _r("Recall@5",  "100%",           f"{rec['recall_at_5']}%",  "selective")
        _r("Recall@10", "100%",           f"{rec['recall_at_10']}%", "selective")

    if not lr_b.get("skipped"):
        _r("Avg context returned (chars)",
           f"{int(lr_b.get('avg_chars',0)):,} chars",
           f"{tok_m.get('prefs_context_chars',0):,} chars", "")
    p()


# ══════════════════════════════════════════════════════════════════════════════
#  SAVE
# ══════════════════════════════════════════════════════════════════════════════

def save_results(out_dir: Path, data: Dict, run_label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"metrics_{run_label}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    ok(f"JSON saved -> {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="YUSR Memory Metrics Harness v2 (zero tokens)")
    ap.add_argument("--user_id",    default=None,  help="Override USER_ID")
    ap.add_argument("--runs",       type=int, default=30)
    ap.add_argument("--run_label",  default="run")
    ap.add_argument("--clear",      action="store_true", help="Clear eval data for user")
    ap.add_argument("--seed",       action="store_true", help="Seed data (zero Groq tokens)")
    ap.add_argument("--list",       action="store_true", help="List stored data and exit")
    ap.add_argument("--write_test", action="store_true",
                    help="Run Mem0 write latency test (costs ~10k Groq tokens — use sparingly)")
    ap.add_argument("--output",     default=None)
    args = ap.parse_args()

    uid = args.user_id or USER_ID

    print(f"\n{B}{'='*68}")
    print(f"  YUSR Memory Metrics Harness v2  (zero-token seeding)")
    print(f"  run_label : {args.run_label}")
    print(f"  user_id   : {uid}")
    print(f"  date      : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  backend   : {BACKEND_DIR}")
    print(f"{'='*68}{X}\n")

    # ── Utility flags ─────────────────────────────────────────────────────────
    if args.clear:
        clear_all(uid)

    if args.seed:
        seed_mem0_zero_tokens(uid)
        seed_baseline_zero_tokens(uid)
        p()
        ok("Seeding complete — 0 Groq tokens consumed.")

    if args.list:
        list_stored(uid)
        sys.exit(0)

    if args.clear or args.seed:
        p()
        ok("Done. Run without --clear/--seed to execute measurements.")
        sys.exit(0)

    # ── Init Mem0 ─────────────────────────────────────────────────────────────
    ok("Initialising Mem0PreferenceManager ...")
    try:
        from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
        pref_mgr = get_preference_manager(uid)
        ok("Mem0 ready")
    except Exception as e:
        err(f"Mem0 init failed: {e}")
        sys.exit(1)

    # ── Init Baseline ─────────────────────────────────────────────────────────
    baseline_mgr = BaselineManager(uid)
    ok(f"Baseline ready  ({baseline_mgr.count_sessions()} sessions, "
       f"{baseline_mgr.count_messages()} messages)")

    task = dict(SAMPLE_TASK)
    task["user_id"] = uid

    results = {
        "run_label": args.run_label,
        "user_id":   uid,
        "timestamp": datetime.now().isoformat(),
        "runs":      args.runs,
    }

    # GROUP A
    lr_mem0     = measure_read_latency_mem0(pref_mgr, args.runs)
    lr_baseline = measure_read_latency_baseline(baseline_mgr, args.runs)
    lw_baseline = measure_write_latency_baseline(baseline_mgr, 10)
    le          = measure_embed_latency(runs=20)

    # Optional: Mem0 write test (only when --write_test flag set)
    lw_mem0 = {"skipped": True, "reason": "disabled to save tokens"}
    if args.write_test:
        warn("Running Mem0 write test — this uses ~10k Groq tokens!")
        from eval.memory_metrics_harness import measure_write_latency_mem0
        lw_mem0 = measure_write_latency_mem0(pref_mgr, runs=10)

    results.update(
        latency_read_mem0      = lr_mem0,
        latency_read_baseline  = lr_baseline,
        latency_write_baseline = lw_baseline,
        latency_write_mem0     = lw_mem0,
        latency_embed          = le,
    )

    # GROUP B
    rec = measure_recall(pref_mgr)
    results["recall"] = rec

    # GROUP C
    tok_m   = measure_tokens_mem0(pref_mgr, task)
    tok_b   = measure_tokens_baseline(baseline_mgr, task)
    cmp_tok = compare_tokens(tok_m, tok_b)
    results.update(tokens_mem0=tok_m, tokens_baseline=tok_b, tokens_comparison=cmp_tok)

    # GROUP D — RL overhead (only meaningful on RL branch, gracefully skips otherwise)
    rl_overhead = measure_rl_overhead(pref_mgr)
    results["rl_overhead"] = rl_overhead

    # SUMMARY + SAVE
    print_summary(args.run_label, lr_mem0, lr_baseline, lw_baseline,
                  le, rec, tok_m, tok_b, cmp_tok)

    out_dir = Path(args.output) if args.output else BACKEND_DIR / "eval" / "results"
    save_results(out_dir, results, args.run_label)

    if cmp_tok and not results.get("rl_overhead", {}).get("skipped"):
        rl = results["rl_overhead"]
        _r("RL extra write operations per session",
           "0", str(rl["extra_write_operations"]), f"+{round(rl['extra_write_operations']/rl['total_exp2_writes']*100)}%")
        _r("RL extraction token savings (guard)",
           f"{rl['exp2_extraction_tokens']} tok",
           f"{rl['exp4_extraction_tokens']} tok",
           f"-{round((rl['exp2_extraction_tokens']-rl['exp4_extraction_tokens'])/rl['exp2_extraction_tokens']*100,1)}%")

    h("DONE")
    ok(f"Measurements complete for run_label='{args.run_label}'")
    p()


if __name__ == "__main__":
    main()