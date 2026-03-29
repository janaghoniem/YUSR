# """
# Test script for Mem0 preference storage and retrieval
# Tests before/after memory ingestion to verify no bloating
# """

# import os
# import sys
# import asyncio
# from dotenv import load_dotenv

# # Add parent directory to path
# sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# load_dotenv()

# from agents.coordinator_agent.memory.mem0_manager import get_preference_manager
# from pymongo import MongoClient

# # MongoDB connection
# MONGODB_URI = os.getenv("MONGODB_URI")
# mongo_client = MongoClient(MONGODB_URI)
# db = mongo_client["yusr_db"]

# def print_header(title):
#     print(f"\n{'='*70}")
#     print(f"{title:^70}")
#     print(f"{'='*70}\n")

# def print_section(title):
#     print(f"\n{'-'*70}")
#     print(f"  {title}")
#     print(f"{'-'*70}")

# def inspect_mongodb_collections():
#     """Show all collections and their document counts"""
#     print_section("MongoDB Collections Status")
    
#     collections = {
#         "language_agent_conversations": "Agent working memory",
#         "mem0_preferences": "User preferences (Mem0)",
#         "checkpoints": "LangGraph task state",
#         "chat_history": "Frontend display (if exists)"
#     }
    
#     for coll_name, description in collections.items():
#         count = db[coll_name].count_documents({})
#         print(f"  • {coll_name}: {count} documents")
#         print(f"    └─ {description}")
    
#     print()

# def clear_all_memory():
#     """Clear all memory stores for fresh start"""
#     print_section("Clearing All Memory")
    
#     collections_to_clear = [
#         "language_agent_conversations",
#         "mem0_preferences",
#         "checkpoints"
#     ]
    
#     for coll in collections_to_clear:
#         count = db[coll].count_documents({})
#         if count > 0:
#             db[coll].delete_many({})
#             print(f"  ✅ Cleared {coll}: {count} documents deleted")
#         else:
#             print(f"  ℹ️  {coll}: Already empty")
    
#     print()

# def test_mem0_ingestion(user_id="test_user"):
#     """Ingest test preferences into Mem0"""
#     print_section(f"Ingesting Test Preferences for user: {user_id}")
    
#     pref_mgr = get_preference_manager(user_id)
    
#     # Test data: Mix of relevant and irrelevant preferences
#     test_preferences = [
#         # App preferences
#         {
#             "text": "User prefers Google Chrome for web browsing",
#             "category": "app_usage",
#             "confidence": "high"
#         },
#         {
#             "text": "User prefers Microsoft Word for document editing",
#             "category": "app_usage",
#             "confidence": "high"
#         },
#         {
#             "text": "User always uses Calculator for quick math",
#             "category": "app_usage",
#             "confidence": "medium"
#         },
        
#         # User data
#         {
#             "text": "User's Moodle username is student123",
#             "category": "user_data",
#             "confidence": "high"
#         },
#         {
#             "text": "User downloads files to C:\\Users\\Downloads",
#             "category": "user_data",
#             "confidence": "high"
#         },
        
#         # Workflow patterns
#         {
#             "text": "User's typical workflow: Moodle → download PDF → summarize in Word",
#             "category": "workflow",
#             "confidence": "medium"
#         },
#         {
#             "text": "User prefers dark mode for all applications",
#             "category": "app_preference",
#             "confidence": "medium"
#         },
        
#         # ❌ NOISE (what we DON'T want to store)
#         # These are commented out to show what NOT to do
#         # {
#         #     "text": "User requested: open calculator. Successfully completed 1 steps.",
#         #     "category": "conversation_history",  # ❌ BAD
#         # },
#     ]
    
#     stored_count = 0
#     for pref in test_preferences:
#         result = pref_mgr.add_preference(
#             pref["text"],
#             metadata={
#                 "category": pref["category"],
#                 "confidence": pref.get("confidence", "medium"),
#                 "test_data": True
#             }
#         )
#         if result:
#             stored_count += 1
#             print(f"  ✅ Stored: {pref['text'][:60]}...")
    
#     print(f"\n  📊 Total stored: {stored_count}/{len(test_preferences)}")
#     print()

# def test_retrieval(user_id="test_user"):
#     """Test retrieval with different queries"""
#     print_section("Testing Mem0 Retrieval")
    
#     pref_mgr = get_preference_manager(user_id)
    
#     test_queries = [
#         {
#             "query": "open calculator",
#             "expected": "Should find calculator preference",
#             "min_score": 0.65
#         },
#         {
#             "query": "browse the web",
#             "expected": "Should find Chrome preference",
#             "min_score": 0.65
#         },
#         {
#             "query": "login to moodle",
#             "expected": "Should find Moodle username + workflow",
#             "min_score": 0.60
#         },
#         {
#             "query": "create a document",
#             "expected": "Should find Word preference",
#             "min_score": 0.65
#         },
#         {
#             "query": "play music",
#             "expected": "Should find NOTHING (no music preferences)",
#             "min_score": 0.65
#         }
#     ]
    
#     for test in test_queries:
#         print(f"\n  Query: '{test['query']}'")
#         print(f"  Expected: {test['expected']}")
#         print(f"  Min Score: {test['min_score']}\n")
        
#         memories = pref_mgr.get_relevant_preferences(
#             query=test['query'],
#             limit=5,
#             min_score=test['min_score']
#         )
        
#         if memories:
#             print(f"  📋 Found {len(memories)} relevant preferences:")
#             for i, mem in enumerate(memories, 1):
#                 score = mem.get('score', 0.0)
#                 text = mem.get('memory') or mem.get('text', 'Unknown')
#                 category = mem.get('metadata', {}).get('category', 'unknown')
#                 print(f"    {i}. [Score: {score:.2f}] [{category}] {text[:60]}...")
#         else:
#             print(f"  ℹ️  No relevant preferences found (as expected)")
    
#     print()

# def test_bloat_scenario(user_id="test_user"):
#     """Simulate what happens with conversation history bloat"""
#     print_section("BLOAT TEST: What if we stored conversation logs?")
    
#     print("  ❌ BAD PRACTICE: Storing task completion logs\n")
    
#     pref_mgr = get_preference_manager(user_id)
    
#     # Simulate 10 task completions (BAD)
#     bad_logs = [
#         "User requested: open calculator. Successfully completed 1 steps.",
#         "User requested: open notepad. Successfully completed 1 steps.",
#         "User requested: open chrome. Successfully completed 1 steps.",
#         "User requested: close calculator. Successfully completed 1 steps.",
#         "User requested: minimize chrome. Successfully completed 1 steps.",
#         "User requested: open word. Successfully completed 1 steps.",
#         "User requested: save document. Successfully completed 2 steps.",
#         "User requested: close word. Successfully completed 1 steps.",
#         "User requested: open excel. Successfully completed 1 steps.",
#         "User requested: create spreadsheet. Successfully completed 3 steps.",
#     ]
    
#     print("  Ingesting 10 verbose conversation logs...\n")
#     for log in bad_logs:
#         pref_mgr.add_preference(
#             log,
#             metadata={"category": "conversation_history", "bloat_test": True}
#         )
#         print(f"    • {log}")
    
#     print("\n  Now testing retrieval for 'open calculator':\n")
    
#     memories = pref_mgr.get_relevant_preferences(
#         query="open calculator",
#         limit=10,
#         min_score=0.50  # Lower threshold to show pollution
#     )
    
#     print(f"  ⚠️  Retrieved {len(memories)} memories (many irrelevant!):\n")
#     for i, mem in enumerate(memories, 1):
#         score = mem.get('score', 0.0)
#         text = mem.get('memory') or mem.get('text', 'Unknown')
#         print(f"    {i}. [Score: {score:.2f}] {text[:70]}...")
    
#     print("\n  📊 ANALYSIS:")
#     relevant_count = sum(1 for m in memories if m.get('score', 0) >= 0.70)
#     noise_count = len(memories) - relevant_count
#     print(f"    • Highly relevant (≥0.70): {relevant_count}")
#     print(f"    • Noise (<0.70): {noise_count}")
#     print(f"    • Signal-to-Noise Ratio: {relevant_count}/{len(memories)}")
    
#     if noise_count > relevant_count:
#         print("\n  ❌ BLOAT DETECTED: More noise than signal!")
#         print("     This would cause the agent to ask unnecessary questions.")
    
#     print()

# async def main():
#     """Run all tests"""
    
#     print_header("Mem0 Memory Bloat Test Suite")
    
#     print("This script tests:")
#     print("  1. Memory ingestion (good vs bad practices)")
#     print("  2. Retrieval with relevance filtering")
#     print("  3. Bloat scenario (what NOT to do)")
#     print()
    
#     input("Press ENTER to start tests...")
    
#     # Step 1: Inspect current state
#     print_header("STEP 1: Inspect Current State")
#     inspect_mongodb_collections()
    
#     # Step 2: Clear memory
#     print_header("STEP 2: Clear All Memory")
#     clear_all_memory()
#     inspect_mongodb_collections()
    
#     # Step 3: Test retrieval with EMPTY memory
#     print_header("STEP 3: Test Retrieval (BEFORE Ingestion)")
#     test_retrieval("test_user")
    
#     # Step 4: Ingest GOOD preferences
#     print_header("STEP 4: Ingest Valid Preferences")
#     test_mem0_ingestion("test_user")
#     inspect_mongodb_collections()
    
#     # Step 5: Test retrieval with preferences
#     print_header("STEP 5: Test Retrieval (AFTER Ingestion)")
#     test_retrieval("test_user")
    
#     # Step 6: Bloat scenario
#     print_header("STEP 6: Bloat Scenario (BAD PRACTICE)")
#     test_bloat_scenario("test_user")
#     inspect_mongodb_collections()
    
#     # Summary
#     print_header("TEST SUMMARY")
#     print("✅ Tests completed!")
#     print("\nKey Takeaways:")
#     print("  1. Store ONLY actionable preferences, NOT logs")
#     print("  2. Use relevance score filtering (min_score >= 0.65)")
#     print("  3. Separate preferences from conversation history")
#     print("  4. Monitor mem0_preferences collection size")
#     print()

# if __name__ == "__main__":
#     asyncio.run(main())


"""
Test Mem0 memory retrieval - verify fixes
"""

import os
from dotenv import load_dotenv
load_dotenv()

from agents.coordinator_agent.memory.mem0_manager import get_preference_manager

def test_memory_retrieval():
    print("=" * 70)
    print("🧪 TESTING MEM0 MEMORY RETRIEVAL")
    print("=" * 70)
    
    # Initialize manager
    user_id = "test_user"
    pref_mgr = get_preference_manager(user_id)
    
    # Test queries
    test_queries = [
        "what web browser do I use",
        "which browser do I prefer",
        "my browser preference",
        "chrome or firefox",
        "what apps do I like"
    ]
    
    print(f"\n📋 Testing {len(test_queries)} queries...\n")
    
    for i, query in enumerate(test_queries, 1):
        print(f"\n{'─' * 70}")
        print(f"Query {i}: '{query}'")
        print('─' * 70)
        
        results = pref_mgr.get_relevant_preferences(query, limit=3)
        
        if results:
            print(f"✅ Found {len(results)} relevant memories:\n")
            for j, mem in enumerate(results, 1):
                score = mem.get('score', 0.0)
                memory_text = mem.get('memory', mem.get('text', 'Unknown'))
                print(f"  {j}. [Score: {score:.3f}] {memory_text}")
        else:
            print("❌ No memories found (threshold too high?)")
    
    print(f"\n{'=' * 70}")
    print("🏁 Test Complete")
    print('=' * 70)

if __name__ == "__main__":
    test_memory_retrieval()