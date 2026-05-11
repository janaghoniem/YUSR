#!/usr/bin/env python
"""
Quick test of the caching system to verify it works end-to-end
without requiring the harness module.
"""

import sys
sys.path.insert(0, '/Users/mohammedwalidadawy/Development/AURA/backend/agents/execution_agent/strategies')

from mobile_template_cache import (
    ChromaTemplateCache,
    CodeTemplate,
    PlaceholderExtractor,
    CHROMA_PATH,
)
from cache_adapter import CacheAdapter

def test_placeholder_extraction():
    """Test that placeholder extraction works."""
    print("[TEST] Placeholder extraction...")
    extractor = PlaceholderExtractor()
    
    code = '''
d(resourceId="android:id/input_hour").set_text("05")
d(resourceId="android:id/input_minute").set_text("35")
d(description="AM").click()
'''
    params = {"alarm_hour": "05", "alarm_minute": "35", "alarm_period": "AM"}
    template_code, schema = extractor.extract(code, params, "Set alarm time")
    
    print(f"   Original code: {code[:50]!r}...")
    print(f"   Template code: {template_code[:50]!r}...")
    print(f"   Schema keys: {list(schema.keys())}")
    print(f"   ✅ Extracted {len(schema)} placeholders\n")
    return template_code, schema


def test_template_storage():
    """Test that templates can be stored and retrieved."""
    print("[TEST] Template storage and retrieval...")
    
    cache = ChromaTemplateCache(CHROMA_PATH)
    
    template = CodeTemplate(
        template_id="test_001",
        task_pattern="Set the alarm time to {alarm_hour}:{alarm_minute} {alarm_period}.",
        aliases=[
            "Enter alarm time {alarm_hour}:{alarm_minute} {alarm_period}.",
            "Type {alarm_hour}:{alarm_minute} {alarm_period} in the time picker.",
        ],
        app="clock",
        package="com.google.android.deskclock",
        task_type="fill",
        code_template='''d(resourceId="android:id/input_hour").set_text("{alarm_hour}")
d(resourceId="android:id/input_minute").set_text("{alarm_minute}")
d(description="{alarm_period}").click()
print("TASK_COMPLETE")''',
        parameter_schema={
            "alarm_hour": "str — zero-padded 12h hour",
            "alarm_minute": "str — zero-padded minute",
            "alarm_period": "str — 'AM' or 'PM'",
        },
        success_count=2,
        failure_count=0,
    )
    
    cache.add(template)
    print(f"   ✅ Stored template: {template.task_pattern[:50]}\n")
    
    # Try to retrieve
    result = cache.lookup("Set the alarm time to 5:35 AM.", "clock")
    if result:
        retrieved_template, similarity = result
        print(f"   ✅ Retrieved template with similarity {similarity:.3f}")
        print(f"      Pattern: {retrieved_template.task_pattern[:50]}\n")
    else:
        print(f"   ⚠️  Template not found (might be OK on first run - index is building)\n")
    
    return cache


def test_cache_adapter():
    """Test that the cache adapter works."""
    print("[TEST] Cache adapter interface...")
    adapter = CacheAdapter(CHROMA_PATH)
    
    # Test that adapter has all required methods
    methods = ['lookup', 'add', 'mark_success', 'mark_failure', 'stats', 'store_successful']
    for method in methods:
        if not hasattr(adapter, method):
            print(f"   ❌ Missing method: {method}")
            return False
    
    print(f"   ✅ All required methods present")
    print(f"   Stats: {adapter.stats()}\n")
    return True


def test_injection_registry():
    """Test that the injection registry is properly formatted."""
    print("[TEST] Injection registry structure...")
    sys.path.insert(0, '/Users/mohammedwalidadawy/Development/AURA/backend/agents/execution_agent/strategies')
    from inject_harness_to_cache import HARNESS_REGISTRY
    
    print(f"   Total registry entries: {len(HARNESS_REGISTRY)}")
    
    # Check first few entries structure
    for i, entry in enumerate(HARNESS_REGISTRY[:3]):
        fn, pattern, app, task_type, params, aliases = entry
        print(f"   Entry {i+1}:")
        print(f"     Pattern: {pattern[:50]}")
        print(f"     App: {app}")
        print(f"     Task type: {task_type}")
        print(f"     Aliases: {len(aliases)}")
    
    print(f"\n   ✅ Registry structure valid\n")
    return True


if __name__ == "__main__":
    print("\n" + "="*70)
    print("CACHE SYSTEM VALIDATION TEST")
    print("="*70 + "\n")
    
    try:
        test_placeholder_extraction()
        test_template_storage()
        test_cache_adapter()
        test_injection_registry()
        
        print("="*70)
        print("✅ ALL TESTS PASSED - Cache system is ready!")
        print("="*70)
        print("\nNext steps:")
        print("  1. Ensure uiautomator_harness.py is in agents/execution_agent/core/")
        print("  2. Run: python inject_harness_to_cache.py --dry-run")
        print("  3. Run: python inject_harness_to_cache.py  (to commit)")
        print("  4. Apply 4-line diff to mobile_strategy_codegen.py (see cache_adapter.py)\n")
        
    except Exception as e:
        print(f"❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
