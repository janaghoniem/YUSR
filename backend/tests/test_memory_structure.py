"""
Test MongoDB structure for both LangGraph checkpoints and Mem0 preferences
"""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")

async def check_mongodb_structure():
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client["yusr_db"]
    
    print("="*70)
    print("📊 MONGODB DATABASE STRUCTURE")
    print("="*70)
    
    # List all collections
    collections = await db.list_collection_names()
    print(f"\n✅ Collections in 'yusr_db': {collections}\n")
    
    # Check LangGraph checkpoints
    if "checkpoints" in collections:
        count = await db.checkpoints.count_documents({})
        print(f"🔹 LangGraph Checkpoints: {count} documents")
        
        # Show sample
        sample = await db.checkpoints.find_one()
        if sample:
            print(f"   Sample checkpoint keys: {list(sample.keys())}")
    
    # Check Mem0 preferences
    if "mem0_preferences" in collections:
        count = await db.mem0_preferences.count_documents({})
        print(f"🔹 Mem0 Preferences: {count} documents")
        
        # Show sample
        sample = await db.mem0_preferences.find_one()
        if sample:
            print(f"   Sample preference keys: {list(sample.keys())}")
    
    print("\n" + "="*70)
    client.close()

if __name__ == "__main__":
    asyncio.run(check_mongodb_structure())