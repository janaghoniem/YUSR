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

logger = logging.getLogger(__name__)


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
        keywords=["word", "document", "docx", "page", "paragraph", "heading", "table", "text formatting"],
        guidance="""
Work with .docx files using python-docx. Identify TASK TYPE first, then follow ONLY that branch.

SETUP:
import os, glob
from docx import Document
from datetime import datetime
folder = 'D:\\\\OneDrive\\\\Desktop\\\\agent\\\\docs\\\\'
os.makedirs(folder, exist_ok=True)
files = glob.glob(os.path.join(folder, '*.docx'))
latest = max(files, key=os.path.getctime) if files else None
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
# Read [ACTIVE FILE] from prompt if present:
active_file = '<path from [ACTIVE FILE: ...]>' if '[ACTIVE FILE:' in PROMPT else latest

TASK TYPE — match ONE, execute ONLY that branch:

[1] LAUNCH APP — task says "open word application", "open microsoft word", "launch word", "start word"
    AND does NOT mention a specific document/file to edit:
    os.system('start winword')
    print("EXECUTION_SUCCESS")
    # Done. No file operations.

[2] CREATE — task says "create/new/make/generate" + document/file:
    save_path = os.path.join(folder, f'descriptive_name_{ts}.docx')
    doc = Document()
    doc.save(save_path)
    os.startfile(save_path)  # Open in Word so user can see it
    print(f"[FILE]: {save_path}")
    print("EXECUTION_SUCCESS")
    # Done. No further editing.

[3] SAVE / CONFIRM — task says "save", "press ok", "press save", "click ok", "click save",
    "confirm saving", "press enter to confirm":
    check_path = active_file
    if check_path and os.path.exists(check_path):
        print(f"[FILE]: {check_path}")
    print("EXECUTION_SUCCESS")
    # Done. NO doc.save() call.

[4] DEFAULT — task says write/type/add/modify/edit/set/insert/format/heading/paragraph:
    doc = Document(active_file) if active_file and os.path.exists(active_file) else Document()
    save_path = active_file if active_file and os.path.exists(active_file) else os.path.join(folder, f'document_{ts}.docx')
    # ... apply changes using python-docx ...
    try:
        doc.save(save_path)
    except PermissionError:
        save_path = os.path.splitext(save_path)[0] + '_v2.docx'
        doc.save(save_path)
    print(f"[FILE]: {save_path}")
    print("EXECUTION_SUCCESS")

Key python-docx patterns:
- doc.add_heading('Title', level=0)   # document title
- doc.add_heading('Section', level=1) # section heading
- doc.add_paragraph('text')           # body paragraph
- p.add_run('bold').bold = True

AVOID: subprocess, pyautogui, Word UI, mouse/keyboard events
"""
    ),
    "excel": ModuleGuidance(
        module_name="Excel",
        library_name="openpyxl",
        library_import="from openpyxl import Workbook",
        keywords=["excel", "spreadsheet", "xlsx", "sheet", "cell", "row", "column", "formula", "data table"],
        guidance="""
CRITICAL: Do NOT open any application UI.
Work directly with .xlsx files using openpyxl library.

FOLDER: D:\\OneDrive\\Desktop\\agent\\excel\\
import os, glob
folder = 'D:\\\\OneDrive\\\\Desktop\\\\agent\\\\excel\\\\'
os.makedirs(folder, exist_ok=True)
files = glob.glob(os.path.join(folder, '*.xlsx'))
latest = max(files, key=os.path.getctime) if files else None

CHECK [ACTIVE FILE] IN PROMPT: If prompt contains [ACTIVE FILE: path], use that as save_path.

TASK TYPE:
- "open/show/view/display" only: os.startfile(latest); print(f"[FILE]: {latest}"); print("EXECUTION_SUCCESS"); exit
- "save" only: print(f"[FILE]: {latest}"); print("EXECUTION_SUCCESS"); exit
- "create/new/make/generate": wb = Workbook(); save_path = os.path.join(folder, 'descriptive_name.xlsx')
- Default (add/update/modify/edit/enter): load latest or create if none:
    from openpyxl import load_workbook
    wb = load_workbook(latest) if latest else Workbook()
    ws = wb.active
    save_path = latest if latest else os.path.join(folder, 'spreadsheet.xlsx')

Key patterns:
- Create: wb = Workbook(); ws = wb.active; ws['A1'] = value; wb.save(save_path)
- Modify: wb = load_workbook(latest); ws = wb.active; ws['A1'] = value; wb.save(save_path)
- Formulas: ws['C1'] = '=A1+B1'

SAVE (with lock fallback):
try:
    wb.save(save_path)
except PermissionError:
    save_path = os.path.join(folder, 'spreadsheet_v2.xlsx')
    wb.save(save_path)
print(f"[FILE]: {save_path}")
print("EXECUTION_SUCCESS")

DO NOT call os.startfile unless task says "open/show/view".
AVOID: subprocess, pyautogui data entry, Excel UI, mouse/keyboard events
"""
    ),
    "powerpoint": ModuleGuidance(
        module_name="PowerPoint",
        library_name="python-pptx",
        library_import="from pptx import Presentation",
        keywords=["powerpoint", "pptx", "slide", "presentation", "bullet point", "layout", "slide show"],
        guidance="""
CRITICAL: Do NOT open any application UI.
Work directly with .pptx files using python-pptx library.

FOLDER: D:\\OneDrive\\Desktop\\agent\\ppts\\
import os, glob
folder = 'D:\\\\OneDrive\\\\Desktop\\\\agent\\\\ppts\\\\'
os.makedirs(folder, exist_ok=True)
files = glob.glob(os.path.join(folder, '*.pptx'))
latest = max(files, key=os.path.getctime) if files else None

CHECK [ACTIVE FILE] IN PROMPT: If prompt contains [ACTIVE FILE: path], use that as save_path.

TASK TYPE:
- "open/show/view/display" only: os.startfile(latest); print(f"[FILE]: {latest}"); print("EXECUTION_SUCCESS"); exit
- "save" only: print(f"[FILE]: {latest}"); print("EXECUTION_SUCCESS"); exit
- "create/new/make/generate": prs = Presentation(); save_path = os.path.join(folder, 'descriptive_name.pptx')
- Default (add/modify/edit/insert/type): load latest or create if none:
    prs = Presentation(latest) if latest else Presentation()
    save_path = latest if latest else os.path.join(folder, 'presentation.pptx')

Key patterns:
- Create: prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[0]); prs.save(save_path)
- Modify: prs = Presentation(latest); slide = prs.slides.add_slide(...); prs.save(save_path)
- Add content: slide.shapes.title.text = 'Title'; tf = slide.placeholders[1]; tf.text = 'Content'

SAVE (with lock fallback):
try:
    prs.save(save_path)
except PermissionError:
    save_path = os.path.join(folder, 'presentation_v2.pptx')
    prs.save(save_path)
print(f"[FILE]: {save_path}")
print("EXECUTION_SUCCESS")

DO NOT call os.startfile unless task says "open/show/view".
AVOID: subprocess, pyautogui slide editing, PowerPoint UI, mouse/keyboard events
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
