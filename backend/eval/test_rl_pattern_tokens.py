"""
RL Pattern Learning Token Overhead Test
=========================================
Measures the token overhead introduced by the RL pattern learning system
(Exp 4) compared to unoptimized Mem0 (Exp 2) and optimized Mem0 (Exp 3).

CRITICAL: This test correctly separates the experiments:
- Exp 2: Unoptimized Mem0 (extraction on EVERY turn, single category)
- Exp 3: Optimized Mem0 (guard skips task commands, 4 categories, no RL)
- Exp 4: RL Branch (extraction on EVERY turn, 6 categories via signals + pattern learning)

Exp 4 represents the CURRENT state of rl-branch WITHOUT the guard optimization.
Exp 5 (future) will add guard to RL branch for best of both worlds.

Uses the EXACT same turn data as the harness LATENCY_QUERIES so results
are directly comparable to Exp 1-4 results.

Run from backend/ directory:
    python eval/test_rl_pattern_tokens.py

Zero Groq tokens consumed — all token counting is local.
"""

import os, sys, json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

# ── Token counter (same as harness) ──────────────────────────────────────────
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // 4)

# ── Colours ───────────────────────────────────────────────────────────────────
G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; C = "\033[96m"
B = "\033[1m";  X = "\033[0m"

def h(t):  print(f"\n{B}{C}{'='*68}{X}\n{B}{C}  {t}{X}\n{B}{C}{'='*68}{X}")
def ok(t): print(f"  {G}v{X}  {t}")
def warn(t):print(f"  {Y}!{X}  {t}")
def row(label, val, color=None):
    c = color or X
    print(f"  {label:<55} {c}{val}{X}")

# ══════════════════════════════════════════════════════════════════════════════
#  EXACT SAME TURNS AS HARNESS LATENCY_QUERIES + personal turns
#  (same 20 task queries from harness, same 6 personal from token guard test)
# ══════════════════════════════════════════════════════════════════════════════

ALL_TURNS = [
    # ── 20 turns from harness LATENCY_QUERIES ─────────────────────────────
    ("open browser",                                   "task"),
    ("search for news",                                "task"),
    ("set alarm for 7am",                              "task"),
    ("send email to colleague",                        "task"),
    ("open calculator",                                "task"),
    ("navigate to Amazon",                             "task"),
    ("play music",                                     "task"),
    ("take a screenshot",                              "task"),
    ("open notepad",                                   "task"),
    ("search for flights",                             "task"),
    ("open settings",                                  "task"),
    ("create new document",                            "task"),
    ("check the weather",                              "task"),
    ("open YouTube",                                   "task"),
    ("find a recipe",                                  "task"),
    ("login to Gmail",                                 "task"),
    ("open calendar",                                  "task"),
    ("search Python tutorials",                        "task"),
    ("open file manager",                              "task"),
    ("turn off wifi",                                  "task"),
    # ── 6 personal/pattern turns ──────────────────────────────────────────
    ("my name is Sara",                                "personal"),
    ("I work as a software engineer",                  "personal"),
    ("I wake up at 7 AM every day",                    "personal"),
    ("I prefer dark mode always",                      "personal"),
    ("keep it short please",                           "style"),
    ("I always use Chrome instead of Edge",            "app_pref"),
]

# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT TEMPLATES — exact prompts used in the real code
# ══════════════════════════════════════════════════════════════════════════════

def build_extraction_prompt(input_text: str) -> str:
    """Exact prompt from language_agent.py personal-info extractor"""
    return (
        'You are a personal-info extractor. Read the user message below.\n'
        'If it contains personal information (name, age, location, job, hobby,\n'
        'preference, or any fact about the user), extract it.\n'
        'If it does NOT contain personal info, return exactly: {"personal_info": null}\n\n'
        f'USER MESSAGE: "{input_text}"\n\n'
        'Return ONLY valid JSON, no markdown, no explanation:\n'
        '{"personal_info": "one-sentence summary of what the user revealed, or null"}'
    )

def build_mem0_write_prompt(memory_text: str) -> str:
    """
    Approximation of what Mem0 sends to Groq internally when memory.add() is called.
    Mem0 sends the message content to Groq for fact extraction + deduplication.
    Measured average: ~951 tokens per write call.
    We use a realistic approximation here.
    """
    return (
        "You are a memory manager. Extract and store the key information from the "
        "following user message as a concise memory fact. If a similar memory already "
        "exists, decide whether to update it or create a new one.\n\n"
        f"User message: {memory_text}\n\n"
        "Existing memories: [retrieved via vector search]\n\n"
        "Output the memory to store as a single clear sentence."
    )

# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL DETECTION — exact same logic as language_agent.py
# ══════════════════════════════════════════════════════════════════════════════

_TASK_PREFIXES = (
    "open ", "launch ", "start ", "close ", "search ", "find ",
    "navigate", "go to", "click ", "type ", "press ", "scroll",
    "take a screenshot", "set alarm", "play ", "pause ", "stop ",
    "افتح", "ابحث", "اكتب", "انتقل",
)
_APP_SIGNALS    = ["instead of", "always use", "i prefer", "use chrome",
                   "use word", "use notepad", "use edge", "use telegram", "use whatsapp"]
_STYLE_SIGNALS  = ["keep it short", "be brief", "bullet points", "no long answers",
                   "always respond in", "respond in arabic", "respond in english",
                   "use simple language", "be concise"]
_PATTERN_SIGNALS= ["i usually", "i always", "every morning", "every night",
                   "i work", "i wake up", "i sleep", "night shift", "i start my day"]

def is_task(text: str) -> bool:
    return any(text.lower().strip().startswith(p) for p in _TASK_PREFIXES)

def detect_app_signal(text: str) -> bool:
    return any(s in text.lower() for s in _APP_SIGNALS)

def detect_style_signal(text: str) -> bool:
    return any(s in text.lower() for s in _STYLE_SIGNALS)

def detect_pattern_signal(text: str) -> bool:
    return any(s in text.lower() for s in _PATTERN_SIGNALS)

# ══════════════════════════════════════════════════════════════════════════════
#  CHECK IF RL FILES ACTUALLY EXIST AND ARE IMPORTABLE
# ══════════════════════════════════════════════════════════════════════════════

def check_rl_files():
    pl_path = BACKEND_DIR / "agents/coordinator_agent/memory/pattern_learner.py"
    fs_path = BACKEND_DIR / "agents/coordinator_agent/memory/feedback_store.py"

    h("RL File Detection")
    pl_exists = pl_path.exists()
    fs_exists = fs_path.exists()

    row("pattern_learner.py exists at expected path", str(pl_exists),
        G if pl_exists else R)
    row("feedback_store.py exists at expected path",  str(fs_exists),
        G if fs_exists else R)

    if pl_exists:
        # Try to read and count functions/classes to understand what it does
        src = pl_path.read_text(encoding="utf-8", errors="replace")
        classes = [l.strip() for l in src.splitlines() if l.strip().startswith("class ")]
        funcs   = [l.strip() for l in src.splitlines() if l.strip().startswith("def ")]
        uses_groq = "groq" in src.lower() or "llm" in src.lower()
        uses_mem0 = "mem0" in src.lower() or "add_preference" in src.lower()

        print(f"\n  pattern_learner.py summary:")
        row("  Classes defined", str(len(classes)))
        for c in classes:
            print(f"      {c}")
        row("  Functions defined", str(len(funcs)))
        row("  Uses LLM/Groq calls", str(uses_groq), Y if uses_groq else G)
        row("  Writes to Mem0", str(uses_mem0), Y if uses_mem0 else G)

        # Count Groq/LLM call sites specifically
        groq_call_lines = [l.strip() for l in src.splitlines()
                           if "groq" in l.lower() and "import" not in l.lower()
                           and not l.strip().startswith("#")]
        llm_call_lines  = [l.strip() for l in src.splitlines()
                           if ".invoke(" in l or "chat.completions" in l
                           or "ainvoke(" in l
                           and not l.strip().startswith("#")]
        all_llm_sites = list(set(groq_call_lines + llm_call_lines))
        row("  LLM call sites found", str(len(all_llm_sites)),
            Y if len(all_llm_sites) > 0 else G)
        for site in all_llm_sites[:5]:
            print(f"      {site[:70]}")

    if fs_exists:
        src = fs_path.read_text(encoding="utf-8", errors="replace")
        classes = [l.strip() for l in src.splitlines() if l.strip().startswith("class ")]
        funcs   = [l.strip() for l in src.splitlines() if l.strip().startswith("def ")]
        print(f"\n  feedback_store.py summary:")
        row("  Classes defined", str(len(classes)))
        for c in classes:
            print(f"      {c}")
        row("  Functions defined", str(len(funcs)))

    # Now try actual import
    import_ok_pl = False
    import_ok_fs = False
    import_err_pl = ""
    import_err_fs = ""

    try:
        from agents.coordinator_agent.memory.pattern_learner import PatternLearner
        import_ok_pl = True
        ok("PatternLearner imported successfully")
    except Exception as e:
        import_err_pl = str(e)
        warn(f"PatternLearner import failed: {e}")

    try:
        from agents.coordinator_agent.memory.feedback_store import FeedbackStore
        import_ok_fs = True
        ok("FeedbackStore imported successfully")
    except Exception as e:
        import_err_fs = str(e)
        warn(f"FeedbackStore import failed: {e}")

    return {
        "pl_exists": pl_exists,
        "fs_exists": fs_exists,
        "pl_import_ok": import_ok_pl,
        "fs_import_ok": import_ok_fs,
        "pl_import_err": import_err_pl,
        "fs_import_err": import_err_fs,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  EXP 2 — Unoptimized Mem0, single category, no guard
# ══════════════════════════════════════════════════════════════════════════════

def simulate_exp2(turns):
    """
    Exp 2 behaviour (baseline):
    - LLM extraction call fires on EVERY turn (no guard)
    - Only personal_info stored if LLM finds it
    - No signal detection
    - Each Mem0 write = ~951 tokens (Mem0 internal LLM)
    """
    extraction_tokens = 0
    extraction_calls  = 0
    mem0_write_tokens = 0
    mem0_write_calls  = 0
    categories_stored = []

    for text, turn_type in turns:
        # Extraction always runs
        prompt = build_extraction_prompt(text)
        extraction_tokens += count_tokens(prompt)
        extraction_calls  += 1

        # Assume LLM finds personal info on personal/style/app_pref turns
        if turn_type in ("personal", "style", "app_pref"):
            mem0_write_tokens += count_tokens(build_mem0_write_prompt(text))
            mem0_write_calls  += 1
            categories_stored.append("personal_info")

    return {
        "extraction_calls":  extraction_calls,
        "extraction_tokens": extraction_tokens,
        "mem0_write_calls":  mem0_write_calls,
        "mem0_write_tokens": mem0_write_tokens,
        "total_tokens":      extraction_tokens + mem0_write_tokens,
        "categories":        list(set(categories_stored)),
    }

# ══════════════════════════════════════════════════════════════════════════════
#  EXP 3 — Optimized Mem0: guard + 4 categories, no RL
# ══════════════════════════════════════════════════════════════════════════════

def simulate_exp3(turns):
    """
    Exp 3 behaviour (Mem0 optimized):
    - Token guard: skip extraction for task commands
    - 4 categories via signal detection (no extra LLM cost for signals)
    - Each Mem0 write = ~951 tokens
    """
    extraction_tokens = 0
    extraction_calls  = 0
    mem0_write_tokens = 0
    mem0_write_calls  = 0
    skipped_by_guard  = 0
    categories_stored = []
    per_turn = []

    for text, turn_type in turns:
        _is_task = is_task(text)
        stores   = []
        ext_tok  = 0

        # Extraction only for non-task turns
        if not _is_task:
            prompt = build_extraction_prompt(text)
            ext_tok = count_tokens(prompt)
            extraction_tokens += ext_tok
            extraction_calls  += 1
            if turn_type in ("personal",):
                stores.append("personal_info")
        else:
            skipped_by_guard += 1

        # Signal detection — always runs, zero cost
        if detect_app_signal(text):
            stores.append("app_preference")
        if detect_style_signal(text):
            stores.append("communication_style")
        if detect_pattern_signal(text):
            stores.append("behavior_pattern")

        # Each category stored = 1 Mem0 write
        for cat in stores:
            mem0_write_tokens += count_tokens(build_mem0_write_prompt(text))
            mem0_write_calls  += 1
            categories_stored.append(cat)

        per_turn.append({
            "text": text, "turn_type": turn_type,
            "is_task": _is_task, "ext_tok": ext_tok,
            "stores": stores,
        })

    return {
        "extraction_calls":  extraction_calls,
        "extraction_tokens": extraction_tokens,
        "mem0_write_calls":  mem0_write_calls,
        "mem0_write_tokens": mem0_write_tokens,
        "total_tokens":      extraction_tokens + mem0_write_tokens,
        "skipped_by_guard":  skipped_by_guard,
        "categories":        list(set(categories_stored)),
        "per_turn":          per_turn,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  EXP 4 — RL Mem0 (actual RL branch WITHOUT guard optimization)
# ══════════════════════════════════════════════════════════════════════════════

def simulate_exp4_rl_actual(turns, pattern_uses_llm: bool = False,
                             tokens_per_pattern_llm_call: int = 350):
    """
    ACTUAL Exp 4 behaviour (current rl-branch):
    
    This matches the current state of rl-branch as of Exp 4:
    - NO token guard (extraction runs on EVERY turn - same as Exp 2)
    - HAS signal detection for 6 categories (app_pref, style, pattern)
    - HAS pattern learner that promotes detected patterns to learned_pattern
    - HAS feedback store for reinforcement learning
    - HAS stricter min_score=0.50 for reads (not measured here)
    - HAS profile injection fix (not measured here)
    
    This is NOT the same as Exp 3 + RL. That will be Exp 5.
    Use this to measure PURE RL overhead compared to Exp 2 baseline.
    """
    extraction_tokens   = 0
    extraction_calls    = 0
    mem0_write_tokens   = 0
    mem0_write_calls    = 0
    pattern_llm_tokens  = 0
    pattern_llm_calls   = 0
    patterns_detected   = 0
    patterns_promoted   = 0
    categories_stored   = []
    per_turn = []

    for text, turn_type in turns:
        # Extraction runs on EVERY turn (no guard - same as Exp 2)
        prompt = build_extraction_prompt(text)
        ext_tok = count_tokens(prompt)
        extraction_tokens += ext_tok
        extraction_calls  += 1
        
        stores = []
        
        # Signal detection runs on all turns (zero cost)
        # This gives us categories from signals
        if detect_app_signal(text):
            stores.append("app_preference")
        if detect_style_signal(text):
            stores.append("communication_style")
        if detect_pattern_signal(text):
            stores.append("behavior_pattern")
        
        # Pattern learner (RL component) - runs on all turns
        # Detects patterns and promotes them to learned_pattern at high confidence
        detected_this_turn = []
        if detect_pattern_signal(text):
            detected_this_turn.append("behavior_pattern")
            patterns_detected += 1
            
            # If pattern_learner uses LLM for analysis (check your actual code)
            if pattern_uses_llm:
                pattern_llm_tokens += tokens_per_pattern_llm_call
                pattern_llm_calls  += 1
            
            # At high confidence (75%+ acceptance rate), patterns get promoted
            # This is the reinforcement learning aspect
            stores.append("learned_pattern:behavior_pattern")
            patterns_promoted += 1
        
        # If turn is personal info, LLM extraction already stored that
        # But in RL branch, we also have signal detection so multiple categories possible
        if turn_type == "personal":
            stores.append("personal_info")
        
        # Each store = one Mem0 write call (~951 tokens)
        for s in stores:
            mem0_write_tokens += count_tokens(build_mem0_write_prompt(text))
            mem0_write_calls  += 1
            categories_stored.append(s)
        
        per_turn.append({
            "text": text, "turn_type": turn_type,
            "ext_tok": ext_tok,
            "stores": stores,
            "patterns_detected": detected_this_turn,
        })
    
    return {
        "extraction_calls":    extraction_calls,
        "extraction_tokens":   extraction_tokens,
        "mem0_write_calls":    mem0_write_calls,
        "mem0_write_tokens":   mem0_write_tokens,
        "pattern_llm_calls":   pattern_llm_calls,
        "pattern_llm_tokens":  pattern_llm_tokens,
        "patterns_detected":   patterns_detected,
        "patterns_promoted":   patterns_promoted,
        "total_tokens":        extraction_tokens + mem0_write_tokens + pattern_llm_tokens,
        "categories":          list(set(categories_stored)),
        "per_turn":            per_turn,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  PRINT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def print_per_turn_table(label: str, per_turn: list):
    h(f"Per-Turn Breakdown — {label}")
    print(f"\n  {'Turn':<45} {'Ext tok':>8}  {'Writes':>7}  {'Categories stored'}")
    print(f"  {'-'*45} {'-'*8}  {'-'*7}  {'-'*40}")
    for t in per_turn:
        cats = ", ".join(t["stores"]) if t["stores"] else "(none)"
        print(f"  {t['text'][:43]:<45} "
              f"{t['ext_tok']:>8}  {len(t['stores']):>7}  {cats[:50]}")

def print_experiment_summary(label: str, r: dict, color=None):
    c = color or X
    print(f"\n  {c}{B}{label}{X}")
    row(f"  Extraction calls (personal-info LLM)", str(r["extraction_calls"]))
    row(f"  Extraction tokens",                    f"{r['extraction_tokens']} tok")
    row(f"  Mem0 write calls",                     str(r["mem0_write_calls"]))
    row(f"  Mem0 write tokens (~951/write)",       f"{r['mem0_write_tokens']} tok")
    if "pattern_llm_calls" in r and r["pattern_llm_calls"] > 0:
        row(f"  Pattern LLM calls",                f"{r['pattern_llm_calls']}")
        row(f"  Pattern LLM tokens",               f"{r['pattern_llm_tokens']} tok")
    row(f"  TOTAL tokens this session",            f"{r['total_tokens']} tok", G)
    if "skipped_by_guard" in r:
        row(f"  Turns skipped by guard",            str(r.get("skipped_by_guard", 0)), G)
    if "patterns_detected" in r:
        row(f"  Patterns detected (RL)",            str(r.get("patterns_detected", 0)))
        row(f"  Patterns promoted to memory (RL)",  str(r.get("patterns_promoted", 0)))
    row(f"  Categories stored",                    str(r["categories"]))

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{B}{'='*68}")
    print(f"  RL Pattern Learning Token Overhead Test")
    print(f"  Same {len(ALL_TURNS)} turns as harness (20 task + 6 personal/pattern)")
    print(f"  Zero Groq tokens consumed")
    print(f"{'='*68}{X}\n")
    
    print(f"  {Y}IMPORTANT: This test correctly separates the experiments:{X}")
    print(f"  • Exp 2: Unoptimized Mem0 (extraction on EVERY turn, 1 category)")
    print(f"  • Exp 3: Optimized Mem0 (guard + 4 categories, no RL)")
    print(f"  • Exp 4: RL Branch (extraction on EVERY turn, 6 categories + RL)")
    print(f"  • Exp 5: NOT INCLUDED — will add guard to RL branch for best results")
    print()

    # Step 1: Check RL files
    rl_info = check_rl_files()

    # Determine if pattern learner uses LLM (read from source if available)
    pattern_uses_llm = False
    pl_path = BACKEND_DIR / "agents/coordinator_agent/memory/pattern_learner.py"
    if pl_path.exists():
        src = pl_path.read_text(encoding="utf-8", errors="replace")
        pattern_uses_llm = (
            "groq" in src.lower() or
            ".invoke(" in src or
            "chat.completions" in src or
            "ainvoke(" in src
        )
        if pattern_uses_llm:
            warn("Pattern learner uses LLM calls — token overhead will be non-zero")
        else:
            ok("Pattern learner appears rule-based — zero extra tokens for pattern detection")

    # Step 2: Run all 3 simulations
    r2 = simulate_exp2(ALL_TURNS)
    r3 = simulate_exp3(ALL_TURNS)
    r4_actual = simulate_exp4_rl_actual(ALL_TURNS, pattern_uses_llm=pattern_uses_llm)

    # Step 3: Print per-turn tables
    print_per_turn_table("Exp 3 — Mem0 Optimized", r3["per_turn"])
    print_per_turn_table("Exp 4 — RL Branch (actual)", r4_actual["per_turn"])

    # Step 4: Per-experiment summaries
    h("Per-Experiment Token Summary")
    print_experiment_summary("Exp 2 — Unoptimized Mem0 (baseline)",  r2, Y)
    print_experiment_summary("Exp 3 — Optimized Mem0 (guard + 4 cats)", r3, G)
    print_experiment_summary("Exp 4 — RL Branch (actual, no guard)", r4_actual, C)

    # Step 5: Cross-experiment comparison table
    h("Cross-Experiment Token Comparison")
    print(f"\n  {'Metric':<45} {'Exp2':>10}  {'Exp3':>10}  {'Exp4 (RL)':>12}")
    print(f"  {'-'*45} {'-'*10}  {'-'*10}  {'-'*12}")

    def cmp_row(label, v2, v3, v4, suffix=""):
        print(f"  {label:<45} {str(v2)+suffix:>10}  {str(v3)+suffix:>10}  "
              f"{str(v4)+suffix:>12}")

    cmp_row("Extraction calls (LLM)",
            r2["extraction_calls"], r3["extraction_calls"],
            r4_actual["extraction_calls"])
    cmp_row("Extraction tokens",
            r2["extraction_tokens"], r3["extraction_tokens"],
            r4_actual["extraction_tokens"], " tok")
    cmp_row("Mem0 write calls",
            r2["mem0_write_calls"], r3["mem0_write_calls"],
            r4_actual["mem0_write_calls"])
    cmp_row("Mem0 write tokens",
            r2["mem0_write_tokens"], r3["mem0_write_tokens"],
            r4_actual["mem0_write_tokens"], " tok")
    
    if r4_actual["pattern_llm_calls"] > 0:
        cmp_row("Pattern LLM tokens (RL only)",
                0, 0, r4_actual["pattern_llm_tokens"], " tok")
    
    cmp_row("TOTAL tokens",
            r2["total_tokens"], r3["total_tokens"],
            r4_actual["total_tokens"], " tok")

    # Step 6: Show overhead analysis
    h("RL Pattern Learning Overhead Analysis")
    
    base_tokens = r2["total_tokens"]
    opt_savings = base_tokens - r3["total_tokens"]
    rl_overhead = r4_actual["total_tokens"] - base_tokens
    
    print(f"\n  {G}Exp 3 (Optimized Mem0) saves: {opt_savings} tokens ({opt_savings/base_tokens*100:.1f}%){X}")
    print(f"  {R}Exp 4 (RL Branch) adds overhead: {rl_overhead} tokens (+{rl_overhead/base_tokens*100:.1f}%){X}")
    print(f"\n  {Y}RL overhead per turn: {rl_overhead/len(ALL_TURNS):.1f} tokens/turn{X}")
    print(f"  {Y}RL overhead per session: {rl_overhead} tokens for {len(ALL_TURNS)} turns{X}")
    
    print(f"\n  {C}Key Insight:{X}")
    print(f"  • Exp 4 currently has {r4_actual['extraction_calls']} extraction calls (same as Exp 2)")
    print(f"  • Adding guard from Exp 3 to RL branch would save ~{opt_savings} tokens")
    print(f"  • Combined system (Exp 5) would have: {base_tokens - opt_savings + rl_overhead} tokens")
    print(f"  • That's a net {((base_tokens - (base_tokens - opt_savings + rl_overhead))/base_tokens*100):.1f}% reduction")
    print(f"    from baseline while keeping RL pattern learning capabilities")

    # Step 7: Daily limit projection
    h("Daily Token Budget Projection (100k TPD limit)")
    print(f"  Assumes same {len(ALL_TURNS)}-turn session repeated throughout the day\n")
    
    for label, r in [("Exp 2 (Unoptimized Mem0)", r2), 
                     ("Exp 3 (Mem0 optimized)", r3),
                     ("Exp 4 (RL Branch, no guard)", r4_actual)]:
        if r["total_tokens"] > 0:
            sessions_per_day = int(100_000 / r["total_tokens"])
            turns_per_day    = sessions_per_day * len(ALL_TURNS)
        else:
            sessions_per_day = 999
            turns_per_day    = 999999
        row(f"  {label}",
            f"{sessions_per_day} sessions/day  ({turns_per_day} turns)")

    # Step 8: Save JSON
    out_path = BACKEND_DIR / "eval" / "results" / "rl_token_overhead_test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "turns_tested": len(ALL_TURNS),
        "pattern_uses_llm": pattern_uses_llm,
        "rl_files": rl_info,
        "exp2_unoptimized": {k: v for k, v in r2.items() if k != "per_turn"},
        "exp3_optimized": {k: v for k, v in r3.items() if k != "per_turn"},
        "exp4_rl_actual": {k: v for k, v in r4_actual.items() if k != "per_turn"},
        "analysis": {
            "baseline_tokens": r2["total_tokens"],
            "optimized_savings": r2["total_tokens"] - r3["total_tokens"],
            "rl_overhead": r4_actual["total_tokens"] - r2["total_tokens"],
            "rl_overhead_per_turn": (r4_actual["total_tokens"] - r2["total_tokens"]) / len(ALL_TURNS),
        }
    }
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    ok(f"JSON saved -> {out_path}")

    print()

if __name__ == "__main__":
    main()