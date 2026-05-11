#!/usr/bin/env python3
"""Quick test to verify sentinel injection fix worked."""

import sys
from mobile_template_cache import ChromaTemplateCache, PlaceholderExtractor

def main():
    cache = ChromaTemplateCache()
    extractor = PlaceholderExtractor()
    
    # Get the fill template for clock (set alarm time)
    res = cache.lookup("Enter 7:30 AM into the alarm time field", "clock", task_type="fill")
    
    if not res:
        print("❌ NO CACHE HIT")
        sys.exit(1)
    
    tmpl, sim = res
    print(f"✅ Cache hit with similarity {sim:.3f}")
    print(f"   Task type: {tmpl.task_type}")
    print(f"   Pattern: {tmpl.task_pattern}")
    print(f"   Schema keys: {list(tmpl.parameter_schema.keys())}")
    print()
    
    # Check if sentinel was added
    if "_PARAMS_SENTINEL" in tmpl.code_template:
        print("✅ Sentinel found in code template")
        # Extract and show sentinel
        for line in tmpl.code_template.split('\n'):
            if "_PARAMS_SENTINEL" in line:
                print(f"   {line[:100]}")
    else:
        print("❌ No sentinel found")
    
    print()
    print("Code snippet (first 15 lines):")
    for i, line in enumerate(tmpl.code_template.split('\n')[:15], 1):
        print(f"  {i:2d}: {line}")
    
    # Now verify that injection would work
    print()
    print("Testing parameter injection:")
    test_params = {
        'alarm_hour': '7',
        'alarm_minute': '30',
        'alarm_period': 'AM'
    }
    
    injected = extractor.inject(tmpl.code_template, test_params)
    print(f"Injection successful: {'{alarm_hour}' not in injected}")
    
    # Show what changed
    if "{alarm_hour}" in tmpl.code_template and "{alarm_hour}" not in injected:
        print("✅ Placeholders were replaced")
    
if __name__ == "__main__":
    main()
