"""
Test LangGraph MongoDB checkpointer for session persistence
"""

from pymongo import MongoClient
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.checkpoint.base import Checkpoint
from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")

def test_session_persistence():
    print("="*70)
    print("🧪 TESTING LANGGRAPH SESSION PERSISTENCE (SHORT-TERM)")
    print("="*70)
    
    # Initialize checkpointer
    client = MongoClient(MONGODB_URI)
    checkpointer = MongoDBSaver(client.yusr_db)
    
    session_id = "test_session_456"
    
    # Test 1: Save checkpoint
    print(f"\n💾 Saving checkpoint for session: {session_id}")
    
    config = {"configurable": {"thread_id": session_id}}
    
    # Simulate saving state
    checkpoint = Checkpoint(
        v=1,
        id="checkpoint_1",
        ts="2024-01-20T10:00:00",
        channel_values={
            "messages": [
                {"role": "user", "content": "open calculator"},
                {"role": "assistant", "content": "Opening calculator..."}
            ]
        },
        channel_versions={"messages": 1},  # Version for each channel
        versions_seen={}
    )
    
    try:
        # MongoDBSaver.put(config, checkpoint, metadata, new_versions)
        new_versions = {"messages": 1}
        checkpointer.put(config, checkpoint, metadata={"user": "test_user"}, new_versions=new_versions)
        print("✅ Checkpoint saved")
    except Exception as e:
        print(f"❌ Failed to save checkpoint: {e}")
        print(f"   Error details: {str(e)}")
        client.close()
        return
    
    # Test 2: Retrieve checkpoint
    print(f"\n🔍 Retrieving checkpoint for session: {session_id}")
    
    try:
        retrieved = checkpointer.get(config)
        
        if retrieved:
            print("✅ Checkpoint retrieved:")
            print(f"   Checkpoint ID: {retrieved.id}")
            print(f"   Timestamp: {retrieved.ts}")
            messages = retrieved.channel_values.get('messages', [])
            print(f"   Messages: {messages}")
            print(f"   Number of messages: {len(messages)}")
        else:
            print("❌ No checkpoint found")
    except Exception as e:
        print(f"❌ Failed to retrieve checkpoint: {e}")
        print(f"   Error: {str(e)}")
    
    # Test 3: List all checkpoints
    print("\n📋 All checkpoints in database:")
    
    db = client["yusr_db"]
    
    try:
        count = db.checkpoints.count_documents({})
        print(f"   Total checkpoints: {count}")
        
        if count > 0:
            # Show recent checkpoints
            print("\n   Recent checkpoints:")
            for doc in db.checkpoints.find().limit(5):
                thread_id = doc.get("thread_id", "unknown")
                checkpoint_ns = doc.get("checkpoint_ns", "")
                checkpoint_id = doc.get("checkpoint", {}).get("id", "unknown")
                print(f"      - Thread: {thread_id}, Checkpoint ID: {checkpoint_id}")
        else:
            print("   No checkpoints found in database")
    except Exception as e:
        print(f"❌ Failed to list checkpoints: {e}")
    
    # Test 4: Verify checkpoint data integrity
    print("\n🔍 Verifying checkpoint data integrity:")
    try:
        # Get the saved checkpoint from MongoDB directly
        saved_doc = db.checkpoints.find_one({"thread_id": session_id})
        if saved_doc:
            print("✅ Checkpoint document found in MongoDB")
            print(f"   Document keys: {list(saved_doc.keys())}")
            checkpoint_data = saved_doc.get("checkpoint", {})
            channel_values = checkpoint_data.get("channel_values", {})
            print(f"   Stored messages: {channel_values.get('messages', [])}")
        else:
            print("❌ Checkpoint document not found in MongoDB")
    except Exception as e:
        print(f"❌ Failed to verify data: {e}")
    
    print("\n" + "="*70)
    print("✅ Session persistence test completed!")
    print("="*70)
    
    client.close()

if __name__ == "__main__":
    test_session_persistence()