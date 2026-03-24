"""
Token guard test — measures Groq extraction call tokens saved.
This is SEPARATE from the harness Group C measurement.

Harness Group C measures: coordinator decomposition prompt tokens
This script measures: language_agent personal-info extraction call tokens

Both are real Groq costs, but at different pipeline stages.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# Use same encoder as harness (char/4 fallback if tiktoken missing)
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // 4)

# ── Exact same extraction prompt as language_agent.py ──────────────────
def build_extraction_prompt(input_text: str) -> str:
    return (
        'You are a personal-info extractor. Read the user message below.\n'
        'If it contains personal information (name, age, location, job, hobby,\n'
        'preference, or any fact about the user), extract it.\n'
        'If it does NOT contain personal info, return exactly: {"personal_info": null}\n\n'
        f'USER MESSAGE: "{input_text}"\n\n'
        'Return ONLY valid JSON, no markdown, no explanation:\n'
        '{"personal_info": "one-sentence summary of what the user revealed, or null"}'
    )

# ── Exact same task prefixes as language_agent.py ──────────────────────
_TASK_PREFIXES = (
    "open ", "launch ", "start ", "close ", "search ", "find ",
    "navigate", "go to", "click ", "type ", "press ", "scroll",
    "take a screenshot", "set alarm", "play ", "pause ", "stop ",
    "افتح", "ابحث", "اكتب", "انتقل",
)

def is_task_command(input_text: str) -> bool:
    return any(input_text.lower().strip().startswith(p) for p in _TASK_PREFIXES)

# ── Test turns — drawn from LATENCY_QUERIES in harness + personal turns ─
# Using LATENCY_QUERIES from harness so the test reflects the same workload
HARNESS_LATENCY_QUERIES = [
    ("open browser",              "task"),
    ("search for news",           "task"),
    ("set alarm for 7am",         "task"),
    ("send email to colleague",   "task"),
    ("open calculator",           "task"),
    ("navigate to Amazon",        "task"),
    ("play music",                "task"),
    ("take a screenshot",         "task"),
    ("open notepad",              "task"),
    ("search for flights",        "task"),
    ("open settings",             "task"),
    ("create new document",       "task"),
    ("check the weather",         "task"),    # ← does NOT start with task prefix
    ("open YouTube",              "task"),
    ("find a recipe",             "task"),
    ("login to Gmail",            "task"),    # ← does NOT start with task prefix
    ("open calendar",             "task"),
    ("search Python tutorials",   "task"),
    ("open file manager",         "task"),
    ("turn off wifi",             "task"),    # ← does NOT start with task prefix
]

# Personal info turns — these should NEVER be skipped
PERSONAL_TURNS = [
    ("my name is Sara",                        "personal"),
    ("I work as a software engineer",          "personal"),
    ("I wake up at 7 AM every day",            "personal"),
    ("I prefer dark mode always",              "personal"),
    ("keep it short please",                   "personal"),
    ("I always use Chrome instead of Edge",    "personal"),
]

ALL_TURNS = HARNESS_LATENCY_QUERIES + PERSONAL_TURNS

print("\n" + "="*68)
print("  TOKEN GUARD TEST")
print(f"  Total turns: {len(ALL_TURNS)}  "
      f"({len(HARNESS_LATENCY_QUERIES)} from harness queries + "
      f"{len(PERSONAL_TURNS)} personal)")
print("="*68)

# ── WITHOUT guard ───────────────────────────────────────────────────────
print("\nWITHOUT GUARD (unoptimized — calls LLM on every turn):")
print("-"*68)
tokens_without = 0
calls_without  = 0
for input_text, turn_type in ALL_TURNS:
    prompt = build_extraction_prompt(input_text)
    t      = count_tokens(prompt)
    tokens_without += t
    calls_without  += 1
    print(f"  CALLED  [{turn_type:8}]  '{input_text:<40}'  {t} tok")

# ── WITH guard ──────────────────────────────────────────────────────────
print("\nWITH GUARD (optimized — skips task commands):")
print("-"*68)
tokens_with = 0
calls_with  = 0
skipped     = 0
wrong_skips = []  # should be empty — guard should never skip personal turns

for input_text, turn_type in ALL_TURNS:
    if is_task_command(input_text):
        skipped += 1
        print(f"  SKIPPED [{turn_type:8}]  '{input_text:<40}'  0 tok")
        if turn_type == "personal":
            wrong_skips.append(input_text)
    else:
        prompt = build_extraction_prompt(input_text)
        t      = count_tokens(prompt)
        tokens_with += t
        calls_with  += 1
        print(f"  CALLED  [{turn_type:8}]  '{input_text:<40}'  {t} tok")

# ── Results ─────────────────────────────────────────────────────────────
print("\n" + "="*68)
print("  RESULTS")
print("="*68)
total = len(ALL_TURNS)
task_count     = sum(1 for _, t in ALL_TURNS if t == "task")
personal_count = sum(1 for _, t in ALL_TURNS if t == "personal")

print(f"\n  Turns tested         : {total}  "
      f"({task_count} task, {personal_count} personal)")
print(f"\n  WITHOUT guard        : {calls_without} LLM calls  |  {tokens_without} tokens")
print(f"  WITH    guard        : {calls_with} LLM calls  |  {tokens_with} tokens")

saved   = tokens_without - tokens_with
pct     = saved / tokens_without * 100 if tokens_without else 0
print(f"\n  Tokens saved         : {saved}  ({pct:.1f}%)")
print(f"  LLM calls eliminated : {skipped}/{total}  ({skipped/total*100:.1f}%)")

avg_tok_per_call = tokens_without / calls_without if calls_without else 0
print(f"  Avg tokens per call  : {avg_tok_per_call:.0f}")

# Daily limit impact — using same 100k TPD limit as in our analysis
print(f"\n  At 100k TPD limit:")
print(f"    Without guard : {int(100_000 / (tokens_without/total))}"
      f" turns/day before exhaustion")
print(f"    With    guard : {int(100_000 / (tokens_with/total)) if tokens_with else 'unlimited'}"
      f" turns/day before exhaustion")

# Safety check — guard must never skip personal turns
if wrong_skips:
    print(f"\n  ⚠️  WARNING: Guard incorrectly skipped {len(wrong_skips)} personal turns:")
    for s in wrong_skips:
        print(f"    - '{s}'")
else:
    print(f"\n  ✅ Guard correctly preserved all {personal_count} personal turns")

# Note on how this relates to harness Group C
print(f"""
  NOTE — Relationship to harness Group C:
  Harness Group C measures coordinator decomposition prompt (~354 tok baseline,
  ~103 tok Mem0). This script measures the language_agent extraction call
  (~{avg_tok_per_call:.0f} tok each). These are two separate Groq calls in the
  pipeline — both contribute to daily token consumption.
""")
print("="*68)