"""
Test Mem0 preference storage and retrieval across sessions
"""

import asyncio
from agents.coordinator_agent.memory.mem0_manager import get_preference_manager

async def test_mem0_preferences():
    print("="*70)
    print("🧪 TESTING MEM0 PREFERENCES (LONG-TERM MEMORY)")
    print("="*70)
    
    user_id = "test_user_123"
    
    # Test 1: Store preferences in "Session 1"
    print("\n📝 SESSION 1: Storing preferences...")
    pref_mgr = get_preference_manager(user_id)
    
    pref_mgr.add_preference(
        "User prefers to open Calculator for math tasks",
        metadata={"category": "app_preferences"}
    )
    
    pref_mgr.add_preference(
        "User frequently works with Discord server 'YUSR Team'",
        metadata={"category": "communication"}
    )
    
    pref_mgr.add_preference(
        "User prefers dark mode in all applications",
        metadata={"category": "ui_preferences"}
    )
    
    print("✅ Stored 3 preferences")
    
    # Test 2: Retrieve in "Session 2" (different session, same user)
    print("\n🔍 SESSION 2: Retrieving preferences for 'open calculator'...")
    
    # Simulate new session by creating new manager instance
    pref_mgr_session2 = get_preference_manager(user_id)
    
    query = "open calculator"
    preferences = pref_mgr_session2.get_relevant_preferences(query, limit=3)
    
    print(f"\n📚 Retrieved {len(preferences)} relevant preferences:")
    for i, pref in enumerate(preferences, 1):
        # Mem0 returns strings directly in latest version
        if isinstance(pref, str):
            memory_text = pref
        elif isinstance(pref, dict):
            memory_text = pref.get('memory', pref.get('text', pref.get('content', 'Unknown')))
        else:
            memory_text = str(pref)
        print(f"   {i}. {memory_text}")
    
    # Test 3: Format for LLM
    print("\n📄 Formatted for LLM:")
    formatted = pref_mgr_session2.format_for_llm(preferences)
    print(formatted)
    
    # Test 4: Get all preferences
    print("\n📋 All preferences for user:")
    all_prefs = pref_mgr_session2.get_all_preferences()
    print(f"   Total: {len(all_prefs)} preferences stored")
    
    print("\n" + "="*70)
    print("✅ Mem0 test completed!")
    print("="*70)

if __name__ == "__main__":
    asyncio.run(test_mem0_preferences())