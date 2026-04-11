"""
Unit tests for ICRL buffer and prompt builder.
Run with: python -m pytest backend/tests/test_icrl_buffer.py -v
"""
import pytest
from agents.ICRL.icrl_buffer import ICRLBuffer
from agents.ICRL.icrl_prompt_builder import build_icrl_context_block, inject_icrl_into_execution_prompt


def test_buffer_empty():
    buf = ICRLBuffer(goal="open calculator")
    assert buf.attempt_count == 0
    assert buf.best_reward == 0.0
    assert buf.get_explorative_context() == []
    assert build_icrl_context_block(buf) == ""


def test_buffer_single_attempt():
    buf = ICRLBuffer(goal="open calculator")
    buf.add("Tried Win+R then calc", reward=0.2, result_snippet="Window not found")
    assert buf.attempt_count == 1
    assert buf.best_reward == 0.2
    ctx = buf.get_explorative_context()
    assert len(ctx) == 1
    assert ctx[0].reward == 0.2


def test_buffer_explorative_strategy_keeps_best_plus_recent():
    buf = ICRLBuffer(goal="open calculator")
    buf.add("Attempt A", reward=0.1)
    buf.add("Attempt B", reward=0.8)  # best
    buf.add("Attempt C", reward=0.5)
    buf.add("Attempt D", reward=0.3)  # most recent (low reward)

    ctx = buf.get_explorative_context()
    rewards_in_context = [a.reward for a in ctx]

    # Should include best (0.8), second-best (0.5), and most-recent (0.3)
    # But NOT 0.1 (below NEGATIVE_REWARD_THRESHOLD=0.3 and not most recent)
    assert 0.8 in rewards_in_context
    assert 0.3 in rewards_in_context  # most recent always included
    # Best appears LAST (ascending order for recency bias)
    assert ctx[-1].reward == max(rewards_in_context)


def test_buffer_filters_negatives_from_best_slots():
    buf = ICRLBuffer(goal="open calculator")
    buf.add("Very bad attempt", reward=0.05)
    buf.add("Slightly bad", reward=0.15)
    buf.add("Mediocre", reward=0.35)

    ctx = buf.get_explorative_context()
    # Only mediocre (0.35 >= threshold) + most_recent (0.35) should appear
    # The 0.05 and 0.15 are below NEGATIVE_REWARD_THRESHOLD=0.3 AND not most recent
    rewards = [a.reward for a in ctx]
    assert 0.05 not in rewards
    assert 0.15 not in rewards
    assert 0.35 in rewards


def test_icrl_instruction_alternates():
    buf = ICRLBuffer(goal="test")
    buf.add("attempt", reward=0.5)

    instr_exploit = buf.get_icrl_instruction(round_number=1)
    instr_explore = buf.get_icrl_instruction(round_number=2)

    assert "EXPLOITATION" in instr_exploit
    assert "IMPROVES" in instr_exploit
    assert "EXPLORATION" in instr_explore
    assert "DIFFERENT" in instr_explore


def test_icrl_no_instruction_on_first_round():
    buf = ICRLBuffer(goal="test")
    assert buf.get_icrl_instruction(round_number=0) == ""


def test_context_block_format():
    buf = ICRLBuffer(goal="search Google for weather")
    buf.add("Opened Chrome, typed query", reward=0.7, result_snippet="Page loaded")
    buf.add("Used address bar shortcut", reward=0.9, result_snippet="Results shown")

    block = build_icrl_context_block(buf)
    assert "Reward: 0.70" in block
    assert "Reward: 0.90" in block
    assert "PREVIOUS ATTEMPTS" in block
    assert "In-Context RL History" in block


def test_should_stop():
    buf = ICRLBuffer(goal="test")
    buf.add("attempt", reward=0.5)
    assert not buf.should_stop()

    buf.add("good attempt", reward=0.95)
    assert buf.should_stop()


def test_inject_into_execution_prompt():
    buf = ICRLBuffer(goal="open notepad")
    buf.add("tried subprocess", reward=0.1)
    buf.add("tried pyautogui Win key", reward=0.6)

    base = "Open Notepad application on desktop"
    enriched = inject_icrl_into_execution_prompt(base, buf, round_number=1)

    assert "PREVIOUS ATTEMPTS" in enriched
    assert "CURRENT TASK" in enriched
    assert base in enriched
    assert "Reward: 0.10" in enriched or "Reward: 0.60" in enriched


if __name__ == "__main__":
    # Quick manual run
    print("Running ICRL buffer tests...")
    test_buffer_empty()
    print("✅ test_buffer_empty")
    test_buffer_single_attempt()
    print("✅ test_buffer_single_attempt")
    test_buffer_explorative_strategy_keeps_best_plus_recent()
    print("✅ test_buffer_explorative_strategy_keeps_best_plus_recent")
    test_buffer_filters_negatives_from_best_slots()
    print("✅ test_buffer_filters_negatives_from_best_slots")
    test_icrl_instruction_alternates()
    print("✅ test_icrl_instruction_alternates")
    test_icrl_no_instruction_on_first_round()
    print("✅ test_icrl_no_instruction_on_first_round")
    test_context_block_format()
    print("✅ test_context_block_format")
    test_should_stop()
    print("✅ test_should_stop")
    test_inject_into_execution_prompt()
    print("✅ test_inject_into_execution_prompt")
    print("\n🎉 All tests passed!")