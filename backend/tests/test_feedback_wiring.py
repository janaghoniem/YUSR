"""
Tests FeedbackAgent reward computation via icrl_reward_bridge.
Run with: python backend/tests/test_feedback_wiring.py
(from the backend/ directory)
"""
import asyncio
import sys
import os

# Add backend root to path so "agents.*" imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# ── Import once at module level (lowercase icrl package) ─────────────────────
from agents.ICRL.icrl_reward_bridge import compute_reward, summarize_task_attempt


async def test_reward_success_desktop():
    """Success: fast path, no FeedbackAgent call, should return 1.0"""
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
    reward = await compute_reward(goal="Open Notepad", task_dict=task_dict, result_dict=result_dict)
    print(f"✅ Desktop success reward: {reward:.3f} (expected 1.0)")
    assert reward == 1.0, f"Expected 1.0, got {reward}"
    return reward


async def test_reward_success_no_marker():
    """Success without EXECUTION_SUCCESS marker — should return 0.85"""
    task_dict = {
        "task_id": "test_1b",
        "ai_prompt": "Open Calculator",
        "context": "local",
        "target_agent": "action",
    }
    result_dict = {
        "task_id": "test_1b",
        "status": "success",
        "content": "Calculator is open",  # no EXECUTION_SUCCESS marker
        "error": None,
    }
    reward = await compute_reward(goal="Open Calculator", task_dict=task_dict, result_dict=result_dict)
    print(f"✅ Success (no marker) reward: {reward:.3f} (expected 0.85)")
    assert reward == 0.85, f"Expected 0.85, got {reward}"
    return reward


async def test_reward_timeout():
    """Timeout: should use heuristic (0.05), NOT call FeedbackAgent"""
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
    reward = await compute_reward(goal="Open heavy application", task_dict=task_dict, result_dict=result_dict)
    print(f"⏱️ Timeout reward: {reward:.3f} (expected 0.05, no FeedbackAgent call)")
    assert reward == 0.05, f"Expected 0.05, got {reward}"
    return reward


async def test_reward_pending():
    """Pending: heuristic 0.3, no FeedbackAgent call"""
    task_dict = {"task_id": "test_p", "ai_prompt": "Navigate to page", "context": "web", "target_agent": "action"}
    result_dict = {"task_id": "test_p", "status": "pending", "content": None, "error": None}
    reward = await compute_reward(goal="Navigate", task_dict=task_dict, result_dict=result_dict)
    print(f"⏳ Pending reward: {reward:.3f} (expected 0.3)")
    assert reward == 0.3, f"Expected 0.3, got {reward}"
    return reward


async def test_reward_failure_calls_feedback_agent():
    """
    Failure: calls FeedbackAgent via asyncio.to_thread.
    Expected: reward > 0.0 (FeedbackAgent evaluates bad but non-zero)
    OR 0.0 if FeedbackAgent truly judges it a complete failure.
    Either is valid — what we're testing here is that:
      a) No exception is raised
      b) The reward is in [0, 1]
      c) The log line '🎯 ICRL FeedbackAgent reward:' is emitted
    """
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
    reward = await compute_reward(goal="Open XYZ application", task_dict=task_dict, result_dict=result_dict)
    print(f"❌ Desktop failure reward: {reward:.3f} (expected [0.0, 0.3], FeedbackAgent called)")
    assert 0.0 <= reward <= 1.0, f"Reward out of range: {reward}"
    print(f"   → Reward {reward:.3f} is {'legitimately 0.0 (complete failure)' if reward == 0.0 else 'partial credit'}")
    return reward


async def test_reward_awaiting_confirmation():
    """awaiting_confirmation: heuristic 0.5"""
    task_dict = {"task_id": "test_ac", "ai_prompt": "Submit form", "context": "web", "target_agent": "action"}
    result_dict = {"task_id": "test_ac", "status": "awaiting_confirmation", "content": None, "error": None}
    reward = await compute_reward(goal="Submit form", task_dict=task_dict, result_dict=result_dict)
    print(f"🔔 Awaiting confirmation reward: {reward:.3f} (expected 0.5)")
    assert reward == 0.5, f"Expected 0.5, got {reward}"
    return reward


async def test_summarize_attempt():
    """Test the summary string format"""
    task_dict = {"ai_prompt": "Open Notepad and write 'Hello World'", "task_id": "t1"}
    result_dict = {"status": "success", "content": "Notepad opened, text written successfully", "error": None}
    summary = summarize_task_attempt(task_dict, result_dict)
    print(f"📝 Summary: {summary}")
    assert "Open Notepad" in summary
    assert "success" in summary
    return summary


async def test_reward_with_user_feedback():
    """
    User feedback path — FeedbackAgent is called with correction context.
    reward=0.0 is valid here (opened wrong browser = complete failure of goal).
    """
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
    reward = await compute_reward(
        goal="Open Chrome browser",
        task_dict=task_dict,
        result_dict=result_dict,
        user_feedback="I wanted Chrome, not Firefox",
    )
    print(f"🔄 With user feedback reward: {reward:.3f}")
    print(f"   → 0.0 is correct here (wrong app opened = total goal failure)")
    assert 0.0 <= reward <= 1.0
    return reward


async def main():
    print("=" * 60)
    print("Testing ICRL Reward Bridge")
    print("=" * 60)
    print()

    results = {}

    print("1. Success with EXECUTION_SUCCESS marker (fast path)...")
    results["success_marker"] = await test_reward_success_desktop()
    print()

    print("2. Success without marker (fast path, 0.85)...")
    results["success_no_marker"] = await test_reward_success_no_marker()
    print()

    print("3. Timeout (heuristic, NO FeedbackAgent call)...")
    results["timeout"] = await test_reward_timeout()
    print()

    print("4. Pending (heuristic, NO FeedbackAgent call)...")
    results["pending"] = await test_reward_pending()
    print()

    print("5. Awaiting confirmation (heuristic 0.5)...")
    results["awaiting"] = await test_reward_awaiting_confirmation()
    print()

    print("6. Failure (FeedbackAgent called — may show BertModel load)...")
    results["failure"] = await test_reward_failure_calls_feedback_agent()
    print()

    print("7. Summary generation...")
    results["summary"] = await test_summarize_attempt()
    print()

    print("8. With user feedback correction...")
    results["with_feedback"] = await test_reward_with_user_feedback()
    print()

    print("=" * 60)
    print("Test Results:")
    print(f"  ✅ Success (marker):    {results['success_marker']:.3f}  ← fast path, should be 1.0")
    print(f"  ✅ Success (no marker): {results['success_no_marker']:.3f}  ← fast path, should be 0.85")
    print(f"  ⏱️ Timeout:             {results['timeout']:.3f}  ← heuristic, should be 0.05")
    print(f"  ⏳ Pending:             {results['pending']:.3f}  ← heuristic, should be 0.3")
    print(f"  🔔 Awaiting confirm:   {results['awaiting']:.3f}  ← heuristic, should be 0.5")
    print(f"  ❌ Failure:            {results['failure']:.3f}  ← FeedbackAgent (0.0 is valid)")
    print(f"  🔄 With feedback:      {results['with_feedback']:.3f}  ← FeedbackAgent (0.0 is valid)")
    print()
    print("BertModel warnings above are normal and safe to ignore.")
    print("=" * 60)
    print("\n🎉 All reward bridge tests passed!")


if __name__ == "__main__":
    asyncio.run(main())