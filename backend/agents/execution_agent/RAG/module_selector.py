# ============================================================================
# Module Selection Layer
# ============================================================================
"""
Smart module selector that intercepts tasks and recommends the best library
before the LLM starts writing code.

This prevents the LLM from defaulting to pyautogui when better, more reliable
libraries are available (e.g., python-docx for Word, openpyxl for Excel).
"""

from dataclasses import dataclass
from typing import Optional, List
import logging
import os

logger = logging.getLogger(__name__)


# ============================================================================
# Common Data Extraction Rules (DRY Principle)
# ============================================================================

CRITICAL_DATA_EXTRACTION_RULES = """
================================================================================
CRITICAL - DATA EXTRACTION AND OUTPUT RULES (NON-NEGOTIABLE)
================================================================================

When your code performs data extraction, copying, reading, or retrieval:

**YOU MUST OUTPUT THE ACTUAL DATA BEFORE ANY STATUS MESSAGE**

This is CRITICAL for multi-step workflows where subsequent tasks depend on your output.

✅ CORRECT PATTERN:
```python
# Extract/copy/read the data
content = pyperclip.paste()  # or file.read(), or extracted_data, etc.

# OUTPUT THE ACTUAL DATA FIRST
print(content)

# THEN print success indicator
print("EXECUTION_SUCCESS")
```

❌ WRONG PATTERN (DATA IS LOST):
```python
content = pyperclip.paste()
if content:
    print("EXECUTION_SUCCESS")  # ← Only status message, actual data is lost!
```

**Why this matters:**
- The next task in the pipeline receives your printed output as its input
- If you only print "EXECUTION_SUCCESS", the next task receives an empty string
- The entire workflow fails silently
- Always output data BEFORE status messages

**FILE TYPE SPECIFIC EXTRACTION METHODS:**

PDF FILES - Use PyPDF2 for accurate text extraction:
```python
from PyPDF2 import PdfReader
import os

try:
    pdf_path = "path/to/file.pdf"  # Use actual file path

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"File not found: {{pdf_path}}")

    # Extract text from PDF with None handling
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:  # Handle None and empty strings
            text += page_text

    # Check if we extracted any content
    if not text.strip():
        text = "[No text content found in PDF]"

    # OUTPUT THE ACTUAL PDF CONTENT FIRST
    print(text)

    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"Exception: {{e}}")
```

COPYING FILE CONTENT (NON-PDF):
```python
import pyautogui
import pyperclip
import time

try:
    # Select all and copy
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.2)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.2)

    # Get the copied content
    content = pyperclip.paste()

    # OUTPUT THE ACTUAL CONTENT FIRST
    print(content)

    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"Exception: {{e}}")
```

READING A FILE:
```python
import os

try:
    filepath = "D:/Downloads/file.txt"

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {{filepath}}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # OUTPUT THE FILE CONTENT FIRST
    print(content)

    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"Exception: {{e}}")
```

EXTRACTING TEXT FROM UI:
```python
import pyautogui
import pyperclip
import time

try:
    # Select text
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.1)

    # Copy to clipboard
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.2)

    # Extract from clipboard
    extracted_text = pyperclip.paste()

    # OUTPUT THE EXTRACTED TEXT FIRST
    print(extracted_text)

    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"Exception: {{e}}")
```

WEB SCRAPING / DATA EXTRACTION:
```python
# Whatever extraction method you use...
extracted_data = element.get_text()

# OUTPUT THE EXTRACTED DATA FIRST
print(extracted_data)

# THEN indicate success
print("EXECUTION_SUCCESS")
```

**SMART FILE TYPE DETECTION:**
- **PDF files (.pdf)**: Use PyPDF2 for text extraction
- **Office files (.docx, .xlsx, .pptx)**: Use appropriate libraries (python-docx, openpyxl, python-pptx)
- **Text files (.txt, .py, .json, etc.)**: Use file reading with UTF-8 encoding
- **Other files**: Use clipboard copying as fallback

**Remember:**
- Data output FIRST
- Status message SECOND
- Choose the RIGHT extraction method based on file type
- This applies to ANY task that extracts, copies, reads, or retrieves information
- Failure to follow this pattern breaks the entire workflow
"""


# ============================================================================
# Dynamic Folder Configuration
# ============================================================================

def get_agent_folder(subfolder: str) -> str:
    """
    Get dynamic agent folder path that works across different systems

    Args:
        subfolder: The specific subfolder ('docs', 'excel', 'ppts')

    Returns:
        Full path to the agent subfolder
    """
    import os

    # Try multiple common desktop locations - fully dynamic for team compatibility
    possible_paths = [
        os.path.expanduser("~/OneDrive/Desktop/agent"),
        os.path.expanduser("~/Desktop/agent"),
        os.path.expanduser("~/Documents/agent"),
    ]

    for base_path in possible_paths:
        if os.path.exists(os.path.dirname(base_path)) or os.path.exists(base_path):
            agent_folder = os.path.join(base_path, subfolder)
            os.makedirs(agent_folder, exist_ok=True)
            return agent_folder.replace("\\", "\\\\")

    # Final fallback - create in user's home directory
    fallback_path = os.path.join(os.path.expanduser("~"), "agent", subfolder)
    os.makedirs(fallback_path, exist_ok=True)
    return fallback_path.replace("\\", "\\\\")


# ============================================================================
# Module Definitions
# ============================================================================

@dataclass
class ModuleGuidance:
    """Guidance for the LLM about which module to use"""
    module_name: str
    library_name: str
    library_import: str
    keywords: List[str]  # Task keywords that trigger this module
    guidance: str  # Instruction block to inject into prompt

    def get_override_block(self) -> str:
        """Get the formatted override block for the prompt"""
        return f"""[MODULE OVERRIDE: {self.module_name}]
Library: {self.library_name}
Import: {self.library_import}

{self.guidance}
[/MODULE OVERRIDE]"""


# Pre-defined module overrides
MODULES = {
"word": ModuleGuidance(
    module_name="Word",
    library_name="python-docx",
    library_import="from docx import Document",
    keywords=["word", "document","page", "paragraph", "heading", "table", "text formatting"],
    guidance="""
Use ONLY these word_tools functions. Never import python-docx directly. Never use pyautogui.

IMPORT (copy exactly):
try:
    from agents.execution_agent.RAG.scripts.word_tools import (
        doc_create, doc_open, doc_find, doc_load,
        doc_add_heading, doc_add_paragraph, doc_add_table,
        doc_save, doc_launch
    )
except ImportError:
    from scripts.word_tools import (
        doc_create, doc_open, doc_find, doc_load,
        doc_add_heading, doc_add_paragraph, doc_add_table,
        doc_save, doc_launch
    )

ACTIVE FILE RESOLUTION (do this first, every time):
if "[ACTIVE FILE:" in PROMPT:
    active_file = "<extract path from [ACTIVE FILE: ...]>"
else:
    active_file = None  # No active file — must create new one for EDIT tasks

TASK → EXACT EXECUTION PATTERN:

[LAUNCH] task says "open word", "launch word", "start word" (no filename):
    doc_launch()

[OPEN] task says "open <filename>":
    path = doc_find("<filename>")
    doc_open(path)

[CREATE] task says "create", "new document", "make a doc":
    path = doc_create("<descriptive_name_from_task>")
    # DO NOT open. Stop here. Let next task do edits.

[EDIT] task says "write", "add", "insert", "type", "put", "save":
    ⚠️ CRITICAL: If active_file is None, MUST create new document first:
    if active_file is None:
        active_file = doc_create("<descriptive_name_for_content>")

    doc = doc_load(active_file)
    # chain only what the task asks for:
    doc = doc_add_heading(doc, "<text>", level=1)   # only if heading needed
    doc = doc_add_paragraph(doc, "<text>")           # only if paragraph needed
    doc = doc_add_table(doc, ["H1","H2"], [["r1c1","r1c2"]])  # only if table needed
    path = doc_save(doc, active_file)

    # If task mentions "save", ALWAYS open the document after saving
    if "save" in task_prompt.lower():
        doc_open(active_file)

[SAVE/CONFIRM] task says "press save", "click ok", "confirm":
    doc_open(active_file)
    # DO NOT call doc_save(). File is already saved. Just open it.

RULES:
- ⚠️ CRITICAL: For EDIT tasks, ALWAYS check if active_file is None BEFORE calling doc_load(). If None, call doc_create() first.
- Always call doc_save() at the end of any EDIT task — never leave a doc unsaved
- Never call doc_open() inside an EDIT task — doc_save() opens it automatically
- Never hardcode folder paths — word_tools handles folder detection internally
- doc_load() does not print anything — that is expected behaviour
"""
),
"excel": ModuleGuidance(
    module_name="Excel",
    library_name="openpyxl",
    library_import="from openpyxl import Workbook",
    keywords=["excel", "spreadsheet", "xlsx", "sheet", "cell", "row", "column", "formula", "data table"],
    guidance="""
Use ONLY these excel_tools functions. Never import openpyxl directly. Never use pyautogui.

IMPORT (copy exactly):
try:
    from agents.execution_agent.RAG.scripts.excel_tools import (
        xl_create, xl_open, xl_find, xl_load,
        xl_set_cell, xl_write_headers, xl_write_row,
        xl_set_formula, xl_save, xl_launch
    )
except ImportError:
    from scripts.excel_tools import (
        xl_create, xl_open, xl_find, xl_load,
        xl_set_cell, xl_write_headers, xl_write_row,
        xl_set_formula, xl_save, xl_launch
    )

ACTIVE FILE RESOLUTION (do this first, every time):
if "[ACTIVE FILE:" in PROMPT:
    active_file = "<extract path from [ACTIVE FILE: ...]>"
else:
    active_file = None  # No active file — must create new one for EDIT tasks

TASK → EXACT EXECUTION PATTERN:

[LAUNCH] task says "open excel", "launch excel", "start excel" (no filename):
    xl_launch()

[OPEN] task says "open <filename>":
    path = xl_find("<filename>")
    xl_open(path)

[CREATE] task says "create", "new spreadsheet", "make a sheet":
    path = xl_create("<descriptive_name_from_task>")
    # DO NOT open. Stop here. Let next task do edits.

[EDIT] task says "write", "fill", "add", "insert", "enter", "update", "save":
    ⚠️ CRITICAL: If active_file is None, MUST create new spreadsheet first:
    if active_file is None:
        active_file = xl_create("<descriptive_name_for_content>")

    wb = xl_load(active_file)
    ws = wb.active
    # chain only what the task asks for:
    ws = xl_write_headers(ws, ["H1","H2"])           # only if headers needed
    ws = xl_write_row(ws, 2, ["val1","val2"])         # only if row data needed
    ws = xl_set_cell(ws, 1, 1, "value")               # only if single cell needed
    ws = xl_set_formula(ws, 1, 3, "=A1+B1")          # only if formula needed
    path = xl_save(wb, active_file)

    # If task mentions "save", ALWAYS open the spreadsheet after saving
    if "save" in task_prompt.lower():
        xl_open(active_file)

[SAVE/CONFIRM] task says "press save", "click ok", "confirm":
    xl_open(active_file)
    # DO NOT call xl_save(). File is already saved. Just open it.

RULES:
- ⚠️ CRITICAL: For EDIT tasks, ALWAYS check if active_file is None BEFORE calling xl_load(). If None, call xl_create() first.
- Always call xl_save() at the end of any EDIT task — never leave a workbook unsaved
- Never call xl_open() inside an EDIT task — xl_save() opens it automatically
- Never hardcode folder paths — excel_tools handles folder detection internally
- xl_load() does not print anything — that is expected behaviour
- ws = wb.active must be called after xl_load() to get the worksheet
"""
),

"powerpoint": ModuleGuidance(
    module_name="PowerPoint",
    library_name="python-pptx",
    library_import="from pptx import Presentation",
    keywords=["powerpoint", "pptx", "slide", "presentation", "bullet point", "layout", "slide show"],
    guidance="""
Use ONLY these ppt_tools functions. Never import python-pptx directly. Never use pyautogui.

IMPORT (copy exactly):
try:
    from agents.execution_agent.RAG.scripts.ppt_tools import (
        ppt_create, ppt_open, ppt_find, ppt_load,
        ppt_add_title_slide, ppt_add_content_slide,
        ppt_add_bullet_slide, ppt_save, ppt_launch
    )
except ImportError:
    from scripts.ppt_tools import (
        ppt_create, ppt_open, ppt_find, ppt_load,
        ppt_add_title_slide, ppt_add_content_slide,
        ppt_add_bullet_slide, ppt_save, ppt_launch
    )

ACTIVE FILE RESOLUTION (do this first, every time):
if "[ACTIVE FILE:" in PROMPT:
    active_file = "<extract path from [ACTIVE FILE: ...]>"
else:
    active_file = None  # No active file — must create new one for EDIT tasks

TASK → EXACT EXECUTION PATTERN:

[LAUNCH] task says "open powerpoint", "launch powerpoint", "start powerpoint" (no filename):
    ppt_launch()

[OPEN] task says "open <filename>":
    path = ppt_find("<filename>")
    ppt_open(path)

[CREATE] task says "create", "new presentation", "make a deck":
    path = ppt_create("<descriptive_name_from_task>")
    # DO NOT open. Stop here. Let next task do edits.

[EDIT] task says "add slide", "insert slide", "write", "put", "type", "save":
    ⚠️ CRITICAL: If active_file is None, MUST create new presentation first:
    if active_file is None:
        active_file = ppt_create("<descriptive_name_for_content>")

    prs = ppt_load(active_file)
    # chain only what the task asks for:
    prs = ppt_add_title_slide(prs, "<title>", "<subtitle>")      # only if title slide needed
    prs = ppt_add_content_slide(prs, "<title>", "<content>")     # only if content slide needed
    prs = ppt_add_bullet_slide(prs, "<title>", ["b1","b2","b3"]) # only if bullet slide needed
    path = ppt_save(prs, active_file)

    # If task mentions "save", ALWAYS open the presentation after saving
    if "save" in task_prompt.lower():
        ppt_open(active_file)

[SAVE/CONFIRM] task says "press save", "click ok", "confirm":
    ppt_open(active_file)
    # DO NOT call ppt_save(). File is already saved. Just open it.

RULES:
- ⚠️ CRITICAL: For EDIT tasks, ALWAYS check if active_file is None BEFORE calling ppt_load(). If None, call ppt_create() first.
- Always call ppt_save() at the end of any EDIT task — never leave a presentation unsaved
- Never call ppt_open() inside an EDIT task — ppt_save() opens it automatically
- Never hardcode folder paths — ppt_tools handles folder detection internally
- ppt_load() does not print anything — that is expected behaviour
- Use ppt_add_title_slide() for the FIRST slide only — use ppt_add_content_slide() or ppt_add_bullet_slide() for all subsequent slides
"""
),
    "file": ModuleGuidance(
        module_name="File",
        library_name="file_agent (smart file search with fuzzy matching)",
        library_import="try:\n    from agents.execution_agent.RAG.file_agent import find_file, open_file\nexcept ImportError:\n    from file_agent import find_file, open_file",
        keywords=["file", "open file", "find file", "search file", "locate file", "read file", "delete file",".pdf", ".xlsx", ".pptx", ".csv"],
        guidance="""
Use the file_agent module for fast, intelligent file operations with fuzzy matching.

SETUP:
try:
    from agents.execution_agent.RAG.file_agent import find_file, open_file
except ImportError:
    from file_agent import find_file, open_file
import os

============ KEY FEATURES ============
✅ Fuzzy matching - "flutter quiz" matches "flutter_quiz_answers.pdf"
✅ Caching - first search indexes files, subsequent searches <0.5s
✅ Smart fallback - uses system search if cached index doesn't match
✅ Structured responses - JSON with status, path, confidence, suggestions

============ RETURN FORMAT ============
All find_file() calls return a dict:

Success (single match):
    {"status": "found", "path": "C:\\..\\file.pdf", "confidence": 0.92}

Multiple candidates (task is ambiguous):
    {"status": "multiple", "paths": [
        {"path": "C:\\..\\report_2024.pdf", "confidence": 0.88},
        {"path": "C:\\..\\report_draft.pdf", "confidence": 0.81}
    ], "count": 2}

Not found:
    {"status": "not_found", "suggestions": ["Did you mean: report_backup.pdf?"]}

============ OPERATION TYPES ============

[1] OPEN FILE DIRECTLY — task says "open", "launch", "view" + filename:
    result = find_file("flutter_quiz_answers.docx")
    if result["status"] == "found":
        success = open_file("flutter_quiz_answers.docx")
        if success:
            print("EXECUTION_SUCCESS")
    elif result["status"] == "multiple":
        # Multiple matches - use first or ask for clarification
        print("File search returned multiple results, using first match")
        success = open_file(result["paths"][0]["path"])
        if success:
            print("EXECUTION_SUCCESS")
    else:
        print("EXECUTION_FAILED: File not found")

[2] GET FILE PATH (for downstream tasks) — task says "find", "locate", "where is":
    result = find_file("report.pdf")
    if result["status"] == "found":
        print(f"[FILE]: {result['path']}")
        print("EXECUTION_SUCCESS")
    elif result["status"] == "multiple":
        # Return first match to downstream task
        print(f"[FILE]: {result['paths'][0]['path']}")
        print("EXECUTION_SUCCESS")
    else:
        print("File not found")

[3] SEARCH MULTIPLE FILES — task says "search", "find all", "list":
    result = find_file("*.txt")  # or search for pattern
    if result["status"] == "found":
        print(f"[FOUND]: {result['path']}")
        print("EXECUTION_SUCCESS")
    elif result["status"] == "multiple":
        for match in result["paths"]:
            print(f"[FOUND]: {match['path']}")
        print("EXECUTION_SUCCESS")
    else:
        print("No files found")

[4] READ FILE CONTENTS — task says "read", "show contents", "display":
    result = find_file("config.txt")
    if result["status"] == "found":
        path = result["path"]
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        print(content)  # OUTPUT DATA FIRST
        print(f"[FILE]: {path}")
        print("EXECUTION_SUCCESS")
    elif result["status"] == "multiple":
        # Read first match
        path = result["paths"][0]["path"]
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        print(content)
        print(f"[FILE]: {path}")
        print("EXECUTION_SUCCESS")
    else:
        print("File not found")

[5] DELETE FILE — task says "delete", "remove", "erase":
    result = find_file("old_backup.zip")
    if result["status"] == "found":
        os.remove(result["path"])
        print(f"[DELETED]: {result['path']}")
        print("EXECUTION_SUCCESS")
    else:
        print("File not found")

[6] CREATE FILE — task says "create", "new", "make":
    save_dir = os.path.expanduser('~\\\\Documents')
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, "new_file.txt")
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write("content")
    print(f"[FILE]: {save_path}")
    print("EXECUTION_SUCCESS")

============ KEY POINTS ============
✅ ALWAYS check result["status"] before accessing path/paths
✅ Fuzzy matching means you can use partial queries: "flutter quiz" not just "flutter_quiz_answers.docx"
✅ If "multiple" status, use first match or ask for clarification
✅ open_file() outputs [FILE]: <path> automatically
✅ OUTPUT DATA BEFORE status messages (critical for pipelines)
✅ Use [FILE]: marker for downstream tasks
✅ Cache automatically refreshes after 24 hours, manual refresh via find_file(query, "full")
"""
    ),
    "notepad": ModuleGuidance(
        module_name="Notepad",
        library_name="notepad_tools (native text file handling)",
        library_import="from agents.execution_agent.RAG.scripts.notepad_tools import (...)",
        keywords=["notepad", "text editor", "note", "text entry", "type text", "write note", "arabic text"],
        guidance="""
Use ONLY these notepad_tools functions. Never use pyautogui for text entry unless absolutely necessary.
For all text input tasks, use notepad_type() with clipboard for reliable Unicode/Arabic support.

IMPORT (copy exactly):
try:
    from agents.execution_agent.RAG.scripts.notepad_tools import (
        notepad_launch, notepad_create, notepad_open, notepad_find,
        notepad_load, notepad_save, notepad_write, notepad_type,
        notepad_append, notepad_read
    )
except ImportError:
    from scripts.notepad_tools import (
        notepad_launch, notepad_create, notepad_open, notepad_find,
        notepad_load, notepad_save, notepad_write, notepad_type,
        notepad_append, notepad_read
    )

TASK → EXACT EXECUTION PATTERN:

[LAUNCH] task says "open notepad", "launch notepad", "start notepad":
    notepad_launch()

[OPEN] task says "open <filename>":
    path = notepad_find("<filename>")
    notepad_open(path)

[CREATE] task says "create", "new note", "make a note":
    path = notepad_create("<descriptive_name_from_task>")
    # DO NOT open. Stop here. Let next task do edits.

[TYPE/WRITE] task says "type", "write", "enter text", "add text":
    # CRITICAL: Only call notepad_launch() if this is the FIRST task for notepad
    # If the task depends on a previous notepad task, Notepad is ALREADY OPEN
    # DO NOT call notepad_launch() again - this prevents duplicate launches
    notepad_type("Your text here")
    # That's it - just type! Don't launch again.

[WRITE TO FILE] task says "write to file", "save note", "create note with content":
    path = notepad_create("<filename>")
    path = notepad_write(path, "Your content here")
    # notepad_write() both writes AND opens the file

[APPEND] task says "add to", "append to", "continue writing":
    path = notepad_append("<filename>", "\\nAdditional text")

[READ] task says "read", "show contents", "get text":
    notepad_read("<filename>")
    # Outputs content then EXECUTION_SUCCESS

[SPECIAL: ARABIC TEXT HANDLING]
    # notepad_type() uses clipboard which handles Arabic perfectly
    # Only launch if needed - don't launch twice!
    notepad_type("???? ??????? ????? '????? ???????'")  # Arabic text works correctly!

RULES:
- ONLY call notepad_launch() if the task explicitly says to open/launch
- If task is about typing/writing and depends on previous task, do NOT call notepad_launch()
- Always use notepad_type() for typing into active window (handles Unicode/Arabic)
- Use notepad_write() for direct file creation with content
- notepad_type() uses clipboard paste for 100% accurate character input
- Never hardcode folder paths — notepad_tools handles folder detection
- For ambiguous "text editor" tasks, launch Notepad as default
- notepad_load() returns content string, doesn't print
- PREVENT DUPLICATE LAUNCHES: Check context before calling notepad_launch()
"""
    ),
}


# ============================================================================
# Module Selector
# ============================================================================

class ModuleSelector:
    """
    Intelligently select the best library for a given task.

    Usage:
        selector = ModuleSelector()
        guidance = selector.select_module(task_description)
        if guidance:
            prompt_with_override = selector.inject_override(prompt, guidance)
    """

    def __init__(self):
        """Initialize module selector with predefined modules"""
        self.modules = MODULES
        logger.info(f"[OK] ModuleSelector initialized with {len(self.modules)} modules")

    def select_module(self, task: str) -> Optional[ModuleGuidance]:
        """
        Analyze task and select the most appropriate module.

        Args:
            task: Task description or prompt

        Returns:
            ModuleGuidance if a module matches, None otherwise
        """
        if not task:
            return None

        task_lower = task.lower()

        # Score each module by matching keywords
        scores = {}

        for module_name, guidance in self.modules.items():
            # Count keyword matches
            matches = sum(1 for keyword in guidance.keywords if keyword in task_lower)
            if matches > 0:
                scores[module_name] = matches

        # Return the module with the highest score
        if scores:
            best_module = max(scores, key=scores.get)
            match_count = scores[best_module]
            logger.info(f"[SELECTED] module '{best_module}' ({match_count} keyword matches)")
            return self.modules[best_module]

        logger.debug(f"[NONE] No module selected for task")
        return None

    def inject_override(self, prompt: str, guidance: ModuleGuidance) -> str:
        """
        Inject module override block into the prompt.

        The override is inserted BEFORE the task description so the LLM
        sees it as a hard constraint.

        Args:
            prompt: Original prompt
            guidance: ModuleGuidance object

        Returns:
            Prompt with override block injected at the top
        """
        override_block = guidance.get_override_block()

        # Insert at the beginning, followed by the original prompt
        enriched_prompt = f"{override_block}\n\n{prompt}"

        logger.info(f"[INJECTED] {guidance.module_name} override into prompt")
        return enriched_prompt

    def enrich_prompt(self, prompt: str) -> str:
        """
        Automatically select and inject module override if applicable.

        Args:
            prompt: Original prompt

        Returns:
            Enriched prompt with override (if module matched), or original prompt
        """
        guidance = self.select_module(prompt)

        if guidance:
            return self.inject_override(prompt, guidance)

        return prompt

    def add_custom_module(self, module_name: str, guidance: ModuleGuidance) -> None:
        """
        Add a custom module override at runtime.

        Args:
            module_name: Name of the module (for lookups)
            guidance: ModuleGuidance object
        """
        self.modules[module_name] = guidance
        logger.info(f"[CUSTOM] Added custom module: {module_name}")

    def get_module_info(self, module_name: str) -> Optional[str]:
        """
        Get human-readable info about a module.

        Args:
            module_name: Name of the module

        Returns:
            Formatted string with module info
        """
        if module_name not in self.modules:
            return None

        guidance = self.modules[module_name]
        return f"""
{guidance.module_name} Module
========================================
Library: {guidance.library_name}
Import: {guidance.library_import}

Triggers on keywords:
{', '.join(guidance.keywords)}

Override guidance:
{guidance.guidance}
"""


# ============================================================================
# Quick Helper Functions
# ============================================================================

def select_module(task: str) -> Optional[ModuleGuidance]:
    """Quick function to select a module for a task."""
    selector = ModuleSelector()
    return selector.select_module(task)


def enrich_prompt(prompt: str) -> str:
    """Quick function to enrich a prompt with module overrides."""
    selector = ModuleSelector()
    return selector.enrich_prompt(prompt)


# ============================================================================
# Testing
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*70)
    print("MODULE SELECTOR TEST")
    print("="*70 + "\n")

    selector = ModuleSelector()

    test_tasks = [
        "Create a Word document with a title and two paragraphs",
        "Create an Excel spreadsheet with sales data",
        "Generate a PowerPoint presentation with 5 slides",
        "Write a Python script using pyautogui",
        "Open file explorer and navigate to Documents",
        "Open the file named API ENDPOINTS.txt",
        "Find and open my resume.pdf",
        "Search for all Excel files on Desktop",
        "Read the contents of config.txt",
        "Delete the old backup.zip file",
    ]

    for i, task in enumerate(test_tasks, 1):
        print(f"Test {i}: {task}")
        guidance = selector.select_module(task)

        if guidance:
            print(f"  [YES] Module: {guidance.module_name}")
            print(f"  Library: {guidance.library_name}")
        else:
            print(f"  [NO] No module selected (will use default pyautogui)")

        print()
