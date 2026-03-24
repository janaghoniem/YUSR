# backend/check_mem0_search.py
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# Test 1: Direct MongoDB — how many docs exist for eval_user_001
client = MongoClient(os.getenv("MONGODB_URI"))
coll = client["yusr_db"]["mem0_preferences"]
count = coll.count_documents({"payload.user_id": "eval_user_001"})
print(f"Docs for eval_user_001: {count}")

# Test 2: Does Mem0's own search find them?
from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
pref_mgr = get_preference_manager("eval_user_001")

# Try get_all first
all_prefs = pref_mgr.get_all_preferences()
print(f"\nget_all_preferences() returned: {len(all_prefs)} items")
for p in all_prefs[:3]:
    print(f"  - {p.get('memory', '')[:60]}")

# Try search
results = pref_mgr.get_relevant_preferences("open browser", limit=5, min_score=0.0)
print(f"\nget_relevant_preferences('open browser', min_score=0.0) returned: {len(results)} items")
for r in results:
    print(f"  score={r.get('score','?'):.3f}  {r.get('memory','')[:60]}")