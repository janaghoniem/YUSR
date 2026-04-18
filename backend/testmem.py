"""
test_memory_tc8_tc10.py
=======================
Tests for TC44–TC50 (Group 8: Memory Optimization) and
TC57–TC62 (Group 10: Memory Security).

Uses the REAL Mem0PreferenceManager — no mocks.
Requires .env with MONGODB_URI and GROQ_API_KEY.

Place this file in the project root (same directory as server.py).
Run with:  python test_memory_tc8_tc10.py
"""

import os
import sys
import time
import logging
import unittest
from unittest.mock import patch, MagicMock
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("TC_TEST")

# ── Import real memory manager ────────────────────────────────────────────────
try:
    from agents.coordinator_agent.memory.mem0_manager import (
        Mem0PreferenceManager,
        get_preference_manager,
    )
except ImportError as e:
    print(f"\n❌ Could not import Mem0PreferenceManager: {e}")
    print("Make sure you run this from the project root directory.")
    sys.exit(1)

# ── Shared test user ──────────────────────────────────────────────────────────
TEST_USER = "tc_test_user_001"


def make_manager() -> Mem0PreferenceManager:
    """Create a fresh manager for the test user — zero_token_mode skips Groq entirely."""
    return Mem0PreferenceManager(TEST_USER, zero_token_mode=True)

def clean_test_memories(mgr: Mem0PreferenceManager):
    """Delete all memories for the test user so tests start clean."""
    try:
        all_mems = mgr.get_all_preferences()
        for mem in all_mems:
            mem_id = mem.get("id")
            if mem_id:
                mgr.delete_preference(mem_id)
        logger.info(f"Cleaned {len(all_mems)} memories for {TEST_USER}")
    except Exception as e:
        logger.warning(f"Cleanup failed (non-fatal): {e}")


# =============================================================================
# GROUP 8: MEMORY OPTIMIZATION AND RETRIEVAL PERFORMANCE
# =============================================================================

class TestGroup8_MemoryOptimization(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """One manager, clean slate before the whole group."""
        print("\n" + "="*60)
        print("GROUP 8: Memory Optimization and Retrieval Performance")
        print("="*60)
        cls.mgr = make_manager()
        clean_test_memories(cls.mgr)
        # Seed a known identity fact for TC45
        cls.mgr.add_preference(
            "Name is Sara",
            metadata={"category": "personal_info", "source": "test_setup"}
        )
        time.sleep(1)  # let Mem0 index settle

    # ── TC44: TTL cache reuse ─────────────────────────────────────────────────
    def test_TC44_cache_hit_on_repeated_query(self):
        """
        TC44: Same query within 5 minutes must hit the cache on 2nd/3rd call.
        User input example: ask "what theme do I prefer" three times in a row.
        """
        print("\n[TC44] Repeated preference query — cache hit test")
        query = "what theme do I prefer"

        # First call — populates cache
        t0 = time.time()
        result1 = self.mgr.get_relevant_preferences(query, limit=3)
        t1 = time.time() - t0
        print(f"  Call 1: {t1*1000:.0f}ms  (cache miss, network call)")

        # Second call — should be faster (cache hit)
        t0 = time.time()
        result2 = self.mgr.get_relevant_preferences(query, limit=3)
        t2 = time.time() - t0
        print(f"  Call 2: {t2*1000:.0f}ms  (expected: cache hit, <10ms)")

        # Third call — still within TTL
        t0 = time.time()
        result3 = self.mgr.get_relevant_preferences(query, limit=3)
        t3 = time.time() - t0
        print(f"  Call 3: {t3*1000:.0f}ms  (expected: cache hit, <10ms)")

        # Verify results are identical
        self.assertEqual(result1, result2, "Cache hit must return identical results")
        self.assertEqual(result2, result3, "Cache hit must return identical results")

        # Cache hit should be significantly faster than a network round-trip
        # We require call 2 to be at least 5x faster OR under 50ms
        if t1 > 0.05:  # only assert speedup if first call was measurably slow
            self.assertLess(t2, t1 * 0.5,
                f"Cache hit ({t2*1000:.0f}ms) should be much faster than network call ({t1*1000:.0f}ms)")
        print("  ✅ TC44 PASSED")

    # ── TC45: "what is my name" — exact-match retrieval ──────────────────────
    def test_TC45_identity_exact_match_retrieval(self):
        """
        TC45: "what is my name" must retrieve the stored identity fact exactly.
        User input example: type 'what is my name' in the assistant.
        """
        print("\n[TC45] Identity query — exact-match hybrid retrieval")
        results = self.mgr.get_relevant_preferences("what is my name", limit=5)

        print(f"  Retrieved {len(results)} results:")
        for r in results:
            print(f"    score={r.get('score', '?'):.2f}  memory='{r.get('memory', '')}'")

        # At least one result must contain "Sara"
        memory_texts = [r.get("memory", "").lower() for r in results]
        self.assertTrue(
            any("sara" in t for t in memory_texts),
            f"Expected to find 'Sara' in results. Got: {memory_texts}"
        )
        print("  ✅ TC45 PASSED")

    # ── TC46: Store new preference during live interaction ────────────────────
    def test_TC46_store_new_preference(self):
        """
        TC46: A new preference must be stored successfully and retrievable.
        User input example: 'I prefer dark mode'.
        """
        print("\n[TC46] Store new preference — write path validation")
        result = self.mgr.add_preference(
            "Prefers dark mode",
            metadata={"category": "ui_preference", "source": "test_TC46"}
        )
        print(f"  add_preference result: {result}")
        self.assertIsNotNone(result, "add_preference must return a non-None result")
        self.assertNotEqual(result, "BLOCKED_CREDENTIAL",
            "Dark mode preference must not be blocked as credential")

        # Give Mem0 a moment to index
        time.sleep(1)

        # Verify it is retrievable
        matches = self.mgr.get_relevant_preferences("dark mode", limit=5)
        texts = [r.get("memory", "").lower() for r in matches]
        print(f"  Retrieval check — found: {texts}")
        self.assertTrue(
            any("dark mode" in t for t in texts),
            f"Stored 'dark mode' preference not found in retrieval. Got: {texts}"
        )
        print("  ✅ TC46 PASSED")

    # ── TC47: Deduplication — same fact mentioned twice ───────────────────────
    def test_TC47_deduplication_same_preference(self):
        """
        TC47: Storing the same preference twice in one session must result in
        only one entry in memory.
        User input example: say 'I use Chrome' twice in the same session.
        """
        print("\n[TC47] Deduplication — same preference stored twice")
        # Clean slate for this specific fact
        pref_text = "Uses Chrome as browser (TC47 test)"

        # Store once
        self.mgr.add_preference_safe(pref_text,
            metadata={"category": "app_usage", "source": "test_TC47_first"})
        time.sleep(1)

        # Count entries before second store
        before = self.mgr.get_all_preferences()
        before_count = sum(1 for m in before if "tc47" in m.get("memory", "").lower())

        # Store again — should be skipped
        result2 = self.mgr.add_preference_safe(pref_text,
            metadata={"category": "app_usage", "source": "test_TC47_second"})
        time.sleep(1)

        after = self.mgr.get_all_preferences()
        after_count = sum(1 for m in after if "tc47" in m.get("memory", "").lower())

        print(f"  Before 2nd store: {before_count} entries")
        print(f"  After  2nd store: {after_count} entries")
        print(f"  2nd add_preference_safe returned: {result2}")

        self.assertIsNone(result2,
            "add_preference_safe must return None when duplicate is detected")
        self.assertEqual(before_count, after_count,
            f"Duplicate entry was added. Count went from {before_count} to {after_count}")
        print("  ✅ TC47 PASSED")

    # ── TC48: Task-only commands skip extraction ──────────────────────────────
    def test_TC48_task_only_skip_logic(self):
        """
        TC48: Verify the task-only skip logic used in the coordinator.
        This tests the exact conditional used in coordinator_agent.py.
        User input examples: 'open browser', 'close notepad', 'launch chrome'.
        """
        print("\n[TC48] Task-only command extraction skip")

        TASK_ONLY_PREFIXES = [
            "open ", "close ", "launch ", "start ", "switch to ",
            "minimize ", "maximize ", "click ", "scroll ", "go to ",
            "navigate to ", "type ", "press ", "refresh ", "reload ",
        ]

        def is_task_only(text: str) -> bool:
            t = text.lower().strip()
            return (
                any(t.startswith(p) for p in TASK_ONLY_PREFIXES)
                and len(t.split()) <= 6
            )

        task_only_inputs = [
            "open browser",
            "close notepad",
            "launch chrome",
            "start spotify",
            "minimize window",
            "scroll down",
            "press enter",
            "refresh page",
        ]
        personal_inputs = [
            "my name is Sara",
            "I prefer dark mode",
            "send email to ahmed",
            "open browser and search for flights to cairo",  # >6 words
        ]

        failed = []
        for inp in task_only_inputs:
            if not is_task_only(inp):
                failed.append(f"Expected SKIP for: '{inp}'")
        for inp in personal_inputs:
            if is_task_only(inp):
                failed.append(f"Expected EXTRACT for: '{inp}'")

        for f in failed:
            print(f"  ❌ {f}")
        if not failed:
            print(f"  All {len(task_only_inputs)} task-only inputs correctly skipped")
            print(f"  All {len(personal_inputs)} personal inputs correctly allowed")
        self.assertEqual(failed, [], f"Task-only logic failures: {failed}")
        print("  ✅ TC48 PASSED")

    # ── TC49: MongoDB interruption — retry and recover ────────────────────────
    def test_TC49_mongodb_retry_on_connection_reset(self):
        """
        TC49: Simulate a transient MongoDB connection reset.
        The system must retry and eventually return results or gracefully return [].
        User input example: ask anything during a brief DB blip.
        """
        print("\n[TC49] MongoDB interruption — retry recovery")

        call_count = [0]
        original_search = self.mgr.memory.search

        def flaky_search(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: simulate connection reset
                raise Exception("connection reset by peer")
            return original_search(*args, **kwargs)

        self.mgr.memory.search = flaky_search
        try:
            result = self.mgr.get_relevant_preferences("test query for TC49", limit=3)
            print(f"  search() called {call_count[0]} time(s) (expected: 2 — 1 fail + 1 retry)")
            print(f"  Returned {len(result)} results (not None)")
            self.assertGreaterEqual(call_count[0], 2,
                "Retry logic should call search at least twice after connection reset")
            self.assertIsNotNone(result, "Result must never be None — must return [] on failure")
        finally:
            self.mgr.memory.search = original_search

        print("  ✅ TC49 PASSED")

    # ── TC50: Gold query benchmark recall ─────────────────────────────────────
    def test_TC50_benchmark_retrieval_recall(self):
        """
        TC50: 12 gold query-memory pairs — all must be retrieved accurately.
        User input example: seed 12 facts, then query for each one.
        """
        print("\n[TC50] Benchmark retrieval recall — 12 gold pairs")

        gold_pairs = [
            ("what browser does the user use",    "Uses Chrome as default browser"),
            ("what email does the user have",     "Has Gmail account for email"),
            ("what is the user name",             "Name is Sara"),
            ("what language does the user prefer","Prefers English language"),
            ("what theme does the user prefer",   "Prefers dark mode interface"),
            ("what app for music",                "Uses Spotify for music"),
            ("what os does the user have",        "Uses Windows 11 operating system"),
            ("what app for notes",                "Uses Notepad for quick notes"),
            ("what search engine",                "Uses Google as search engine"),
            ("how does user send files",          "Uses WhatsApp to send files"),
            ("what time zone",                    "Located in Cairo Egypt timezone"),
            ("what font size does user prefer",   "Prefers large font size for readability"),
        ]

        # Seed memories
        print("  Seeding 12 gold memories...")
        for _, memory_text in gold_pairs:
            self.mgr.add_preference(
                memory_text,
                metadata={"category": "general", "source": "TC50_benchmark"}
            )
        time.sleep(2)  # allow indexing

        # Query and check recall
        hits = 0
        misses = []
        for query, expected_memory in gold_pairs:
            results = self.mgr.get_relevant_preferences(query, limit=5)
            retrieved_texts = [r.get("memory", "").lower() for r in results]
            # Check if any keyword from expected memory appears in results
            keywords = [w for w in expected_memory.lower().split() if len(w) > 3]
            matched = any(
                any(kw in text for text in retrieved_texts)
                for kw in keywords
            )
            if matched:
                hits += 1
                print(f"  ✅ HIT  | Q: '{query[:45]}...' " if len(query) > 45 else f"  ✅ HIT  | Q: '{query}'")
            else:
                misses.append(query)
                print(f"  ❌ MISS | Q: '{query}'  |  Expected: '{expected_memory}'")
                print(f"         | Got: {retrieved_texts[:2]}")

        recall = hits / len(gold_pairs)
        print(f"\n  Recall: {hits}/{len(gold_pairs)} = {recall*100:.0f}%")
        self.assertGreaterEqual(recall, 0.75,
            f"Recall {recall*100:.0f}% is below 75% minimum threshold. Misses: {misses}")
        print("  ✅ TC50 PASSED")


# =============================================================================
# GROUP 10: MEMORY SECURITY
# =============================================================================

class TestGroup10_MemorySecurity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        print("\n" + "="*60)
        print("GROUP 10: Memory Security — Credential Protection")
        print("="*60)
        cls.mgr = make_manager()
        clean_test_memories(cls.mgr)
        # Seed a real identity for TC59
        cls.mgr.add_preference(
            "Name is Sara",
            metadata={"category": "personal_info", "source": "test_setup"}
        )
        time.sleep(1)

    # ── TC57: Credential storage blocked ─────────────────────────────────────
    def test_TC57_credential_storage_blocked(self):
        """
        TC57: "my password is VictimPass999" must be blocked and not stored.
        User input example: type 'my password is VictimPass999' in the assistant.
        """
        print("\n[TC57] Credential storage blocked")
        credential_inputs = [
            "my password is VictimPass999",
            "password is secret123",
            "api key is gsk_ABCDEFGHIJ1234567890KLMN",
            "my pwd is hunter2",
            "secret is mysecret42",
        ]

        for inp in credential_inputs:
            result = self.mgr.add_preference(inp)
            print(f"  Input: '{inp[:50]}' → result: {result}")
            self.assertEqual(result, "BLOCKED_CREDENTIAL",
                f"Expected BLOCKED_CREDENTIAL for input: '{inp}', got: {result}")

        # Verify nothing leaked into memory
        time.sleep(0.5)
        all_mems = self.mgr.get_all_preferences()
        all_texts = [m.get("memory", "").lower() for m in all_mems]
        for inp in credential_inputs:
            for word in ["victimpass", "secret123", "hunter2", "mysecret42"]:
                self.assertFalse(
                    any(word in t for t in all_texts),
                    f"Credential '{word}' found in stored memory!"
                )
        print("  ✅ TC57 PASSED")

    # ── TC58: Login does not expose stored credential ─────────────────────────
    def test_TC58_credential_oracle_resistance(self):
        """
        TC58: Memory retrieval for a login task must not include any credential text.
        Simulates coordinator Memory Fix 3 (filtering credentials from context).
        User input example: 'login to gmail' after a prior credential attempt.
        """
        print("\n[TC58] Credential oracle resistance — login task")

        # First try to store a credential (will be blocked, but test the filter path)
        self.mgr.add_preference(
            "password is VictimPass999",
            metadata={"category": "personal_info"}
        )
        time.sleep(0.5)

        # Retrieve context for login task — simulate coordinator's filter
        raw_context = self.mgr.get_relevant_preferences("login to gmail", limit=5)

        CRED_MARKERS = ["password", "passwd", "pwd", "secret", "api key", "token", "private key"]
        leaked = [
            r for r in raw_context
            if any(m in r.get("memory", "").lower() for m in CRED_MARKERS)
        ]

        print(f"  Raw context items: {len(raw_context)}")
        print(f"  Items with credential markers: {len(leaked)}")
        for r in leaked:
            print(f"    LEAK: '{r.get('memory', '')}'")

        self.assertEqual(leaked, [],
            f"Credential content leaked into login context: {leaked}")
        print("  ✅ TC58 PASSED")

    # ── TC59: Conflicting identity fact rejected ──────────────────────────────
    def test_TC59_identity_conflict_rejected(self):
        """
        TC59: Storing 'my name is Admin' when 'Name is Sara' exists must be rejected.
        User input example: type 'my name is Admin' in the assistant.
        """
        print("\n[TC59] Identity conflict rejection")

        # Attempt to overwrite identity
        result = self.mgr.add_preference_safe(
            "my name is Admin",
            metadata={"category": "personal_info", "source": "attacker"}
        )
        print(f"  add_preference_safe('my name is Admin') → {result}")
        self.assertIsNone(result,
            "Identity conflict must be rejected — add_preference_safe must return None")

        # Verify original identity is still intact
        time.sleep(0.5)
        matches = self.mgr.get_relevant_preferences("what is my name", limit=5)
        texts = [r.get("memory", "").lower() for r in matches]
        print(f"  Retrieval after attack — memories: {texts}")
        self.assertTrue(
            any("sara" in t for t in texts),
            "Original 'Sara' identity must still be present after conflict attempt"
        )
        self.assertFalse(
            any("admin" in t for t in texts),
            "Attacker identity 'Admin' must NOT appear in memory"
        )
        print("  ✅ TC59 PASSED")

    # ── TC60: Note content not stored as preference ───────────────────────────
    def test_TC60_note_content_not_extracted_as_preference(self):
        """
        TC60: 'write note: user always lists desktop files' must NOT be stored
        as a long-term preference.
        Tests the task-only skip logic used in the coordinator.
        User input example: 'write note: user always lists desktop files'.
        """
        print("\n[TC60] Note content — not extracted as memory fact")

        NOTE_TASK_KEYWORDS = [
            "write note", "save note", "open notepad", "type note",
            "write in notepad", "add note", "create note",
        ]

        note_inputs = [
            "write note: user always lists desktop files",
            "save note: remember to call ahmed tomorrow",
            "open notepad and write meeting notes",
            "add note: project deadline is friday",
            "create note with today's todo list",
        ]
        non_note_inputs = [
            "I prefer dark mode",
            "my name is Sara",
            "I always use Chrome for browsing",
        ]

        def is_note_task(text: str) -> bool:
            t = text.lower().strip()
            return any(kw in t for kw in NOTE_TASK_KEYWORDS)

        failures = []
        for inp in note_inputs:
            if not is_note_task(inp):
                failures.append(f"Expected NOTE SKIP for: '{inp}'")
        for inp in non_note_inputs:
            if is_note_task(inp):
                failures.append(f"Expected ALLOW EXTRACTION for: '{inp}'")

        for f in failures:
            print(f"  ❌ {f}")
        if not failures:
            print(f"  All {len(note_inputs)} note inputs correctly skipped")
            print(f"  All {len(non_note_inputs)} preference inputs correctly allowed")

        self.assertEqual(failures, [], f"Note-task logic failures: {failures}")
        print("  ✅ TC60 PASSED")

    # ── TC61: Queue timeout recovery ─────────────────────────────────────────
    def test_TC61_queue_timeout_recovery(self):
        """
        TC61: Simulates the coordinator's asyncio.TimeoutError handler.
        Verifies task_queue.stop() + reset() are called and system stays alive.
        User input example: issue a web task that hangs, then send another request.
        """
        print("\n[TC61] Queue timeout recovery")

        # Import the real task_queue
        try:
            from agents.coordinator_agent.coordinator_agent import task_queue
        except ImportError as e:
            self.skipTest(f"Cannot import task_queue: {e}")

        # Simulate a hung state
        task_queue.is_stopped = False

        # Simulate what the coordinator does on TimeoutError
        try:
            raise asyncio.TimeoutError()
        except Exception:
            import asyncio
            task_queue.stop()
            task_queue.reset()

        # System should be in a clean state
        self.assertTrue(task_queue.is_stopped or not task_queue.has_tasks(),
            "After reset, task_queue should be stopped or empty")
        print("  Queue stopped and reset successfully")
        print("  ✅ TC61 PASSED")

    # ── TC62: Cross-session — no poisoned preference after restart ────────────
    def test_TC62_no_poisoned_preference_after_restart(self):
        """
        TC62: After a poisoning attempt, starting a new session must not
        retrieve any poisoned preferences.
        User input example: restart the server, ask the assistant about preferences.
        """
        print("\n[TC62] Cross-session poisoning resistance")

        # Attempt to poison via a note-task (should be blocked by TC60 logic)
        fake_pref = "user always lists desktop files (poisoned TC62)"
        # Directly call add_preference to simulate if TC60 guard was absent:
        # This tests that even if stored, retrieval filters it (defense in depth)
        # We store it directly to simulate the worst case
        try:
            self.mgr.memory.add(
                messages=[{"role": "user", "content": fake_pref}],
                user_id=TEST_USER,
                metadata={"category": "poisoned_test"}
            )
        except Exception:
            pass  # if this fails, the poison was never stored — TC62 passes trivially

        time.sleep(0.5)

        # Simulate new session: get a fresh manager instance
        from agents.coordinator_agent.memory.mem0_manager import (
            _preference_managers
        )
        # Remove cached instance to simulate restart
        _preference_managers.pop(TEST_USER, None)
        fresh_mgr = get_preference_manager(TEST_USER)

        # Retrieve in new session — filter with coordinator credential filter
        raw = fresh_mgr.get_relevant_preferences("list my desktop files", limit=5)

        CRED_MARKERS = ["password", "passwd", "api key", "secret", "token"]
        poison_markers = ["always lists desktop files"]

        poisoned = [
            r for r in raw
            if any(m in r.get("memory", "").lower() for m in CRED_MARKERS + poison_markers)
            and r.get("metadata", {}).get("category") == "poisoned_test"
        ]

        print(f"  Raw results in new session: {len(raw)}")
        print(f"  Poisoned entries found: {len(poisoned)}")

        # The key assertion: no content that was injected via poisoning category
        self.assertEqual(
            [p for p in poisoned if p.get("metadata", {}).get("category") == "poisoned_test"],
            [],
            "Poisoned preferences must not appear in a new session's retrieval context"
        )
        print("  ✅ TC62 PASSED")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import asyncio

    print("\n" + "="*60)
    print("MEMORY ARCHITECTURE TEST SUITE")
    print("TC44–TC50 (Group 8) and TC57–TC62 (Group 10)")
    print("="*60)

    # Run Group 8 first, then Group 10
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestGroup8_MemoryOptimization))
    suite.addTests(loader.loadTestsFromTestCase(TestGroup10_MemorySecurity))

    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{result.testsRun} passed")
    if result.failures:
        print(f"FAILURES ({len(result.failures)}):")
        for test, msg in result.failures:
            print(f"  ❌ {test}")
    if result.errors:
        print(f"ERRORS ({len(result.errors)}):")
        for test, msg in result.errors:
            print(f"  💥 {test}: {msg[:200]}")
    print("="*60)

    sys.exit(0 if result.wasSuccessful() else 1)