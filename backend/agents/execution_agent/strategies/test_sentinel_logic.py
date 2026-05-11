#!/usr/bin/env python3
"""Test the placeholder extraction and injection logic directly."""

from mobile_template_cache import PlaceholderExtractor, CodeTemplate

# Simulated harness code that uses variables (no string literals for values)
harness_code = '''def set_alarm_time(d, case):
    # Navigate to time picker
    time_field = d(description="time")
    time_field.click()
    time.sleep(0.5)
    
    # Extract time components
    hour = case.hour
    minute = case.minute if hasattr(case, 'minute') else "00"
    
    # Type the time (using uiautomator2)
    d.send_keys(f"{hour}:{minute}")
    time.sleep(0.3)
    
    # Confirm
    d(text="OK").click()
'''

# Parameters that would be extracted
params = {
    'alarm_hour': '7',
    'alarm_minute': '30',
    'alarm_period': 'AM'
}

print("=" * 60)
print("TEST: PlaceholderExtractor on harness code with variables")
print("=" * 60)
print()

# Test extraction
extractor = PlaceholderExtractor()
extracted_code, schema = extractor.extract(harness_code, params)

print(f"Original code has {len(harness_code)} chars")
print(f"Schema extracted: {schema}")
print()

print("First 10 lines of extracted code:")
for i, line in enumerate(extracted_code.split('\n')[:10], 1):
    print(f"  {i:2d}: {line}")
print()

if not schema:
    print("⚠️  Schema is empty (extraction failed - expected for variable-based code)")
    print("   This is OK - the sentinel injection should handle this.")
else:
    print(f"✅ Schema extracted: {list(schema.keys())}")

# Now test what the injection code would do
print()
print("=" * 60)
print("TEST: Sentinel injection simulation")
print("=" * 60)
print()

_INTERNAL_KEYS = {'app_start', 'app_stop', 'sleep_duration'}

if params:
    if not schema:
        schema = {k: PlaceholderExtractor.PARAM_DESCRIPTIONS.get(k, f"str — {k}") for k in params.keys() if k not in _INTERNAL_KEYS}
        print(f"Created schema from params: {list(schema.keys())}")
    
    missing_keys = [k for k in schema.keys() if f"{{{k}}}" not in extracted_code]
    print(f"Missing placeholder keys: {missing_keys}")
    
    if missing_keys:
        sentinel_parts = [f"{{{k}}}" for k in missing_keys]
        sentinel = f"# Auto-injected param placeholders\n_PARAMS_SENTINEL = {repr('|'.join(sentinel_parts))}\n"
        final_code = sentinel + extracted_code
        print(f"✅ Sentinel added with {len(missing_keys)} placeholder(s)")
        print()
        print("Sentinel code:")
        print(sentinel)
    else:
        print("✅ No placeholders needed - all keys present in code")

print()
print("=" * 60)
print("CONCLUSION")
print("=" * 60)
if not schema and params:
    print("✅ Sentinel injection fix working:")
    print("   - When extraction fails (empty schema), sentinel is added")
    print("   - Sentinel contains all {placeholder} tokens from params")
    print("   - Runtime injection can fill placeholders even for variable-based code")
else:
    print("Schema was populated, sentinel may not be needed")
