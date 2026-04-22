# 📁 File Agent Module - Architecture Guide

## Overview
The **File Agent** is a lightweight, efficient file operation handler that separates concerns:
- **Utility Layer** (`file_utils.py`) - Fast file searching using Windows `where` command
- **Code Generation Layer** (module_selector.py) - Simple prompts that call utilities

## Architecture

```
User Request
    ↓
Module Router [Detects "file", ".txt", "open file", etc.]
    ↓
Code Generator [Generates minimal code]
    ↓  
Generated Code: 
  from file_utils import open_file
  open_file("API ENDPOINTS.txt")
    ↓
Execution (Fast + Reliable)
```

## Core Utilities

### `file_utils.py` Functions

| Function | Purpose | Usage |
|----------|---------|-------|
| `find_file(filename)` | Find first file match | `path = find_file("test.txt")` |
| `find_all_files(pattern)` | Find all matches | `files = find_all_files("*.pdf")` |
| `open_file(filename)` | Find and open | `open_file("report.pdf")` |
| `get_file_path(filename)` | Get path (no open) | `path = get_file_path("config.txt")` |

### Fast Search Strategy
1. Try quick common locations first (~/.Desktop, ~/Documents, ~/Downloads)
2. Use Windows `where /r` for system-wide search
3. Fallback to glob-based search if timeout
4. Return first match (fastest)

### Features
- ✅ **Fast**: Uses Windows native `where` command
- ✅ **Robust**: Timeout + fallback mechanisms
- ✅ **Simple**: No complex globbing in code gen
- ✅ **Reliable**: Independent testing + logging

## Generated Code Examples

### Example 1: Open a File
```python
from agents.execution_agent.RAG.file_utils import open_file

if open_file("API ENDPOINTS.txt"):
    print("[FILE]: Found and opened")
    print("EXECUTION_SUCCESS")
else:
    print("EXECUTION_FAILED: File not found")
```

### Example 2: Get Path for Pipeline
```python
from agents.execution_agent.RAG.file_utils import get_file_path
import os

path = get_file_path("report.pdf")
if path:
    print(f"[FILE]: {path}")
    print("EXECUTION_SUCCESS")
else:
    print("File not found")
```

### Example 3: Search Multiple Files
```python
from agents.execution_agent.RAG.file_utils import find_all_files

files = find_all_files("*.txt")
if files:
    for f in files:
        print(f"[FOUND]: {f}")
    print("EXECUTION_SUCCESS")
else:
    print("No files found")
```

## Benefits vs. Complex Code Gen

| Aspect | Complex Gen | File Agent |
|--------|-----------|-----------|
| Code Length | 50+ lines | 2-5 lines |
| Reliability | Depends on LLM | Tested independently |
| Maintenance | Hard (regex, edge cases) | Easy (isolated module) |
| Speed | Slow (full glob) | Fast (where command) |
| Testing | Implicit | Unit testable |
| Error Handling | LLM generated | Robust |

## Integration Points

1. **Module Selector**: Routes file tasks → File module
2. **Code Generator**: Uses file_utils import guidance
3. **Execution Pipeline**: Runs generated code immediately
4. **Task Validator**: Checks [FILE]: markers in output

## Testing

```bash
# Test the utilities directly
python -m backend.agents.execution_agent.RAG.file_utils

# Test module detection
python -m backend.agents.execution_agent.RAG.module_selector
```

## Future Enhancements
- [ ] Cache recent file paths
- [ ] Add file metadata (size, date modified)
- [ ] Support network/UNC paths
- [ ] Add compression support (zip, rar)
