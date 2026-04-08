"""
Tests FeedbackAgent reward computation via icrl_reward_bridge.
Run with: python backend/tests/test_icrl_reward_bridge.py
"""
import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


async def test_reward_success_desktop():
    """Test successful desktop action (Notepad, Calculator, etc.)"""
    from agents.ICRL.icrl_reward_bridge import compute_reward
    
    task_dict = {
        "task_id": "test_1",
        "ai_prompt": "Open Notepad application using Win+R then type notepad",
        "context": "local",
        "target_agent": "action",
    }
    result_dict = {
        "task_id": "test_1",
        "status": "success",
        "content": "EXECUTION_SUCCESS: Notepad opened successfully",
        "error": None,
    }
    
    reward = await compute_reward(
        goal="Open Notepad",
        task_dict=task_dict,
        result_dict=result_dict,
    )
    print(f"✅ Desktop success reward: {reward:.3f} (expected ~1.0)")
    return reward


async def test_reward_partial_success():
    """Test partial success (app opened but wrong window)"""
    from agents.ICRL.icrl_reward_bridge import compute_reward
    
    task_dict = {
        "task_id": "test_2",
        "ai_prompt": "Open calculator and compute 5+3",
        "context": "local",
        "target_agent": "action",
    }
    result_dict = {
        "task_id": "test_2",
        "status": "partial",
        "content": "Calculator opened but calculation not performed",
        "error": None,
    }
    
    reward = await compute_reward(
        goal="Open calculator and compute 5+3",
        task_dict=task_dict,
        result_dict=result_dict,
    )
    print(f"⚠️ Partial success reward: {reward:.3f} (expected 0.3-0.7)")
    return reward


async def test_reward_failure_desktop():
    """Test failure (app not found)"""
    from agents.ICRL.icrl_reward_bridge import compute_reward
    
    task_dict = {
        "task_id": "test_3",
        "ai_prompt": "Open nonexistent application XYZ",
        "context": "local",
        "target_agent": "action",
    }
    result_dict = {
        "task_id": "test_3",
        "status": "failed",
        "content": None,
        "error": "Application 'XYZ' not found in Start Menu",
    }
    
    reward = await compute_reward(
        goal="Open XYZ application",
        task_dict=task_dict,
        result_dict=result_dict,
    )
    print(f"❌ Desktop failure reward: {reward:.3f} (expected < 0.3)")
    return reward


async def test_reward_timeout():
    """Test timeout scenario"""
    from agents.ICRL.icrl_reward_bridge import compute_reward
    
    task_dict = {
        "task_id": "test_4",
        "ai_prompt": "Open heavy application that takes too long",
        "context": "local",
        "target_agent": "action",
    }
    result_dict = {
        "task_id": "test_4",
        "status": "timeout",
        "content": None,
        "error": "Operation timed out after 30 seconds",
    }
    
    reward = await compute_reward(
        goal="Open heavy application",
        task_dict=task_dict,
        result_dict=result_dict,
    )
    print(f"⏱️ Timeout reward: {reward:.3f} (expected ~0.05)")
    return reward


async def test_summarize_attempt():
    """Test the summary generation"""
    from agents.ICRL.icrl_reward_bridge import summarize_task_attempt
    
    task_dict = {
        "ai_prompt": "Open Notepad and write 'Hello World'",
        "task_id": "t1"
    }
    result_dict = {
        "status": "success",
        "content": "Notepad opened, text written successfully",
        "error": None
    }
    
    summary = summarize_task_attempt(task_dict, result_dict)
    print(f"📝 Summary: {summary}")
    assert "Open Notepad" in summary
    assert "success" in summary
    return summary


async def test_reward_with_user_feedback():
    """Test reward with user correction feedback"""
    from agents.ICRL.icrl_reward_bridge import compute_reward
    
    task_dict = {
        "task_id": "test_5",
        "ai_prompt": "Open Chrome browser",
        "context": "local",
        "target_agent": "action",
    }
    result_dict = {
        "task_id": "test_5",
        "status": "failed",
        "content": "Opened Firefox instead",
        "error": "Wrong browser opened",
    }
    
    # User provides correction
    user_feedback = "I wanted Chrome, not Firefox"
    
    reward = await compute_reward(
        goal="Open Chrome browser",
        task_dict=task_dict,
        result_dict=result_dict,
        user_feedback=user_feedback,
    )
    print(f"🔄 With user feedback reward: {reward:.3f}")
    return reward


async def main():
    print("=" * 60)
    print("Testing ICRL Reward Bridge")
    print("=" * 60)
    print()
    
    # Test 1: Success
    print("1. Testing desktop success...")
    reward1 = await test_reward_success_desktop()
    print()
    
    # Test 2: Partial success
    print("2. Testing partial success...")
    reward2 = await test_reward_partial_success()
    print()
    
    # Test 3: Failure
    print("3. Testing desktop failure...")
    reward3 = await test_reward_failure_desktop()
    print()
    
    # Test 4: Timeout
    print("4. Testing timeout...")
    reward4 = await test_reward_timeout()
    print()
    
    # Test 5: Summary generation
    print("5. Testing summary generation...")
    summary = await test_summarize_attempt()
    print()
    
    # Test 6: User feedback
    print("6. Testing with user feedback...")
    reward5 = await test_reward_with_user_feedback()
    print()
    
    # Summary
    print("=" * 60)
    print("Test Results Summary:")
    print(f"  ✅ Desktop success: {reward1:.3f}")
    print(f"  ⚠️ Partial success: {reward2:.3f}")
    print(f"  ❌ Desktop failure: {reward3:.3f}")
    print(f"  ⏱️ Timeout: {reward4:.3f}")
    print(f"  🔄 With feedback: {reward5:.3f}")
    print(f"  📝 Summary: {summary[:50]}...")
    print("=" * 60)
    
    # Validate expectations
    assert reward1 >= 0.8, "Success reward should be high"
    assert reward3 < 0.5, "Failure reward should be low"
    assert reward4 <= 0.3, "Timeout reward should be very low"
    
    print("\n🎉 All reward bridge tests passed!")


if __name__ == "__main__":
    asyncio.run(main())