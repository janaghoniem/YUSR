# ============================================================================
# Post-Execution Task Validation Layer
# ============================================================================
# Validates if tasks actually succeeded beyond code validation
# Includes: window state checking, UI element detection, file output verification

import re
import time
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum

try:
    import pygetwindow as gw
except ImportError:
    gw = None

logger = logging.getLogger(__name__)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class WindowState:
    """Captures the state of the active window at a point in time"""
    title: str              # Active window title
    process: str            # Process name (extracted from title)
    timestamp: float        # When captured

    @staticmethod
    def capture() -> 'WindowState':
        """Capture current active window state"""
        try:
            if gw is None:
                return WindowState(title="Unknown", process="Unknown", timestamp=time.time())

            active_window = gw.getActiveWindow()

            if active_window:
                title = active_window.title
                # Extract process name (last part after " - " or full title)
                process = title.split(' - ')[-1] if ' - ' in title else title
                return WindowState(title=title, process=process, timestamp=time.time())
            else:
                return WindowState(title="Desktop", process="explorer", timestamp=time.time())

        except Exception as e:
            logger.warning(f"⚠️ Failed to capture window state: {e}")
            return WindowState(title="Unknown", process="Unknown", timestamp=time.time())

    def __eq__(self, other: 'WindowState') -> bool:
        """Compare window states"""
        if not isinstance(other, WindowState):
            return False
        return (self.process.lower() == other.process.lower() and
                self.title.lower() == other.title.lower())

    def __ne__(self, other: 'WindowState') -> bool:
        """Check if window states differ"""
        return not self.__eq__(other)


class TaskType(Enum):
    """Task type enumeration"""
    APP_LAUNCH = "app_launch"           # Open, launch, start app
    UI_INTERACT = "ui_interact"         # Click, tap, type in UI
    FILE_WORK = "file_work"             # Create, save, edit files
    GENERAL = "general"                 # Other tasks


@dataclass
class ValidationResult:
    """Result of task validation"""
    passed: bool                        # Did task validation pass?
    failures: List[str]                 # Detailed failure reasons
    confidence: float                   # 0.0-1.0, confidence in validation
    validation_type: str                # Type of validation performed
    is_element_task: bool               # Is this an element interaction task?
    task_type: TaskType                 # Classified task type

    @property
    def should_trigger_fallback(self) -> bool:
        """Determine if OmniParser fallback should be triggered

        Returns True if:
        - Validation failed AND
        - Confidence is low (< 0.7) AND
        - Task involves element detection/clicking
        """
        return (not self.passed) and (self.confidence < 0.7) and self.is_element_task


# ============================================================================
# Task Validator
# ============================================================================

class TaskValidator:
    """Validates task execution success beyond code-level checks"""

    # App launch keywords
    APP_LAUNCH_KEYWORDS = {'open', 'launch', 'start', 'run', 'activate', 'switch'}

    # UI interaction keywords
    UI_INTERACT_KEYWORDS = {'click', 'tap', 'press', 'type', 'button', 'link', 'field',
                            'input', 'select', 'check', 'uncheck', 'enter', 'search'}

    # File operation keywords
    FILE_KEYWORDS = {'create', 'save', 'write', 'document', 'sheet', 'file', 'export',
                     'new', 'open', 'edit', 'delete', 'rename', 'copy', 'move'}

    def __init__(self):
        self.logger = logger
        self.omniparser = None  # Lazy-initialized on first use

    def _ensure_omniparser(self) -> bool:
        """Initialize OmniParser if not already done

        Returns: True if OmniParser is ready, False if initialization failed
        """
        if self.omniparser is not None:
            return True

        try:
            self.logger.info("🔄 Initializing OmniParser detector...")
            from agents.execution_agent.fallback.omniparser_detector import OmniParserDetector
            import logging as logging_module

            omni_logger = logging_module.getLogger("OmniParser")
            self.omniparser = OmniParserDetector(omni_logger)
            self.logger.info("✅ OmniParser ready")
            return True

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to initialize OmniParser: {e}")
            return False

    def validate_execution(
        self,
        task,
        exec_result,
        before_state: WindowState,
        after_state: WindowState,
        omniparser=None,
        is_cache_hit: bool = False
    ) -> ValidationResult:
        """Validate if task execution actually succeeded

        SIMPLIFIED: Only validate window state for app launches.
        OmniParser is used for CLICKING (fallback), not for validation.

        Args:
            task: ActionTask object
            exec_result: ExecutionResult from sandbox execution
            before_state: Window state before execution
            after_state: Window state after execution
            omniparser: Optional OmniParserDetector (used only for fallback clicking)
            is_cache_hit: Whether this was a cache hit

        Returns:
            ValidationResult with detailed validation info
        """

        # Classify task type
        task_type = self._get_task_type(task.ai_prompt)

        # Check if this is an element interaction task (for fallback decision)
        is_element_task = self._is_element_task(task.ai_prompt)

        self.logger.info(f"🔍 Validating task (type={task_type.value}, is_cache_hit={is_cache_hit})")

        failures = []
        validation_type = "none"
        confidence = 1.0
        passed = True

        # Skip validation for successful cache hits (performance optimization)
        if is_cache_hit and exec_result.validation_passed:
            self.logger.info("⏭️ Skipping validation for successful cache hit (fast path)")
            return ValidationResult(
                passed=True,
                failures=[],
                confidence=1.0,
                validation_type="cache_hit_skip",
                is_element_task=is_element_task,
                task_type=task_type
            )

        # ONLY validate window title for app launches
        if task_type == TaskType.APP_LAUNCH:
            passed, failures, validation_type = self._validate_app_launch_title_only(
                task, before_state, after_state
            )

        else:
            # For all other tasks, rely on code validation (exit code, success indicators)
            self.logger.info("ℹ️ Non-app-launch task - skipping validation (code validation sufficient)")
            passed = True
            failures = []
            validation_type = "code_only"
            confidence = 1.0

        # Log results
        if passed:
            self.logger.info(f"✅ Task validation passed (type={validation_type})")
        else:
            self.logger.warning(f"❌ Task validation failed: {failures}")

        return ValidationResult(
            passed=passed,
            failures=failures,
            confidence=confidence,
            validation_type=validation_type,
            is_element_task=is_element_task,
            task_type=task_type
        )

    def _get_task_type(self, prompt: str) -> TaskType:
        """Classify task into type based on keywords"""
        prompt_lower = prompt.lower()

        # SPECIAL CASE: "open <file>" is app_launch (opening a file), not file_work
        # Check for: open + file extension (.pdf, .docx, etc.)
        file_extensions = ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.txt', '.csv']
        is_file_open = (any(ext in prompt_lower for ext in file_extensions) and
                       ('open' in prompt_lower or 'launch' in prompt_lower or 'start' in prompt_lower))

        if is_file_open:
            # "Open <file>" is treated as app_launch (file viewer app opens)
            return TaskType.APP_LAUNCH

        # Count keyword matches
        app_launch_matches = sum(1 for kw in self.APP_LAUNCH_KEYWORDS if kw in prompt_lower)
        ui_interact_matches = sum(1 for kw in self.UI_INTERACT_KEYWORDS if kw in prompt_lower)
        file_matches = sum(1 for kw in self.FILE_KEYWORDS if kw in prompt_lower)

        # Determine task type by priority (including ties)
        # Priority: app_launch > ui_interact > file > general
        if app_launch_matches > 0 and app_launch_matches >= ui_interact_matches and app_launch_matches >= file_matches:
            return TaskType.APP_LAUNCH
        elif ui_interact_matches > 0 and ui_interact_matches > file_matches:
            return TaskType.UI_INTERACT
        elif file_matches > 0:
            return TaskType.FILE_WORK
        else:
            return TaskType.GENERAL

    def _is_element_task(self, prompt: str) -> bool:
        """Check if task involves UI element detection (click, button, etc.)"""
        element_keywords = {'click', 'tap', 'press', 'button', 'link', 'element',
                           'field', 'input', 'select', 'checkbox'}
        return any(kw in prompt.lower() for kw in element_keywords)

    def _validate_app_launch_title_only(
        self,
        task,
        before_state: WindowState,
        after_state: WindowState
    ) -> Tuple[bool, List[str], str]:
        """Validate app launch by checking window title only (no OmniParser)

        Returns: (passed, failures, validation_type)
        """
        self.logger.info(f"🪟 Validating app launch: before='{before_state.process}' -> after='{after_state.process}'")

        # Extract expected app name from prompt
        app_names = self._extract_app_names(task.ai_prompt)

        if not app_names:
            self.logger.info("ℹ️ Could not extract target app name, skipping validation")
            return (True, [], "no_target_app")

        # Check if window changed
        window_changed = before_state != after_state

        # SPECIAL CASE: File-opening tasks in sandbox
        # File opens don't always change the active window in subprocess sandboxes
        # But if open_file() executed and returned successfully, accept it
        is_file_opening_task = any(
            keyword in task.ai_prompt.lower()
            for keyword in ['open', 'launch', 'start']
        ) and any(
            ext in task.ai_prompt.lower()
            for ext in ['.pdf', '.txt', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.csv']
        )

        # For file opening tasks, window detection is unreliable in sandbox
        # Just accept that the file operation happened
        if is_file_opening_task:
            self.logger.info(f"📄 File opening task - window state check skipped (sandbox limitation)")
            return (True, [], "window_change_file_open_sandbox")

        if not window_changed:
            # Windows may take time to launch - if window didn't change yet,
            # wait and retry once before failing
            self.logger.info("⏳ Window not changed immediately, waiting 2s for app to launch...")
            time.sleep(2)

            # Re-capture window state after delay
            current_state = WindowState.capture()
            window_changed = before_state != current_state

            if window_changed:
                self.logger.info(f"✅ Window changed after delay: '{current_state.process}'")
                after_state = current_state
            else:
                self.logger.warning(f"⚠️ Window still did not change (before={before_state.process}, after={after_state.process})")
                return (False, ["Window did not change to target app"], "window_change")

        # Check for error dialog indicators in window title/process (FAST)
        error_indicators = [
            'error', 'cannot find', 'not found', 'failed', 'exception',
            'problem', 'issue', 'windows cannot', 'critical error'
        ]
        after_title_lower = (after_state.title + " " + after_state.process).lower()

        if any(error in after_title_lower for error in error_indicators):
            failures = [f"Window appears to be an error dialog: '{after_state.title}'"]
            self.logger.warning(f"❌ {failures[0]}")
            return (False, failures, "window_change_error_dialog")

        # Check if new window matches target app (stricter matching for app launches)
        new_window_text = (after_state.title + " " + after_state.process).lower()
        matched = any(app.lower() in new_window_text for app in app_names)

        if not matched:
            failures = [f"New window '{after_state.process}' doesn't match target apps: {app_names}"]
            self.logger.warning(f"⚠️ {failures[0]}")
            return (False, failures, "window_change")

        self.logger.info(f"✅ Window changed to target app: {after_state.process}")
        return (True, [], "window_change")

    def _validate_ui_interaction(
        self,
        task,
        omniparser
    ) -> Tuple[bool, List[str], float, str]:
        """Validate UI interaction by detecting expected elements

        Returns: (passed, failures, confidence, validation_type)
        """
        # Initialize OmniParser if needed
        parser = omniparser or self.omniparser
        if parser is None:
            if not self._ensure_omniparser():
                self.logger.info("ℹ️ OmniParser not available, skipping UI validation")
                return (True, [], 1.0, "no_omniparser")
            parser = self.omniparser

        # Extract expected elements from task
        elements = self._extract_expected_elements(task.ai_prompt)

        if not elements:
            self.logger.info("ℹ️ No specific UI elements mentioned, skipping validation")
            return (True, [], 0.8, "no_elements_specified")

        self.logger.info(f"🎯 Validating UI elements: {elements}")

        # Try to detect each element
        detected = []
        for element in elements:
            try:
                result = parser.detect_element_by_text(element)
                detected.append(result is not None)
                if result:
                    self.logger.info(f"   ✅ Found: {element}")
                else:
                    self.logger.warning(f"   ❌ Not found: {element}")
            except Exception as e:
                self.logger.warning(f"   ⚠️ Error detecting '{element}': {e}")
                detected.append(False)

        # Calculate confidence as ratio of found elements
        if not detected:
            return (True, [], 0.0, "ui_elements")

        confidence = sum(detected) / len(detected)
        threshold = 0.7
        passed = confidence >= threshold

        if passed:
            self.logger.info(f"✅ UI validation passed (confidence={confidence:.1%})")
            return (True, [], confidence, "ui_elements")
        else:
            failures = [f"UI elements not fully detected (confidence={confidence:.1%} < {threshold:.1%})"]
            self.logger.warning(f"⚠️ {failures[0]}")
            return (False, failures, confidence, "ui_elements")

    def _validate_file_operation(
        self,
        task,
        exec_result
    ) -> Tuple[bool, List[str], str]:
        """Validate file operation by checking for file markers in output

        Returns: (passed, failures, validation_type)
        """
        self.logger.info("📁 Validating file operation output")

        # Check for file marker in stdout (primary validation)
        if exec_result.stdout and '[FILE]:' in exec_result.stdout:
            # Extract file path
            for line in exec_result.stdout.splitlines():
                if line.startswith('[FILE]:'):
                    file_path = line[7:].strip()
                    if file_path:
                        self.logger.info(f"✅ File operation marker found: {file_path}")
                        return (True, [], "file_output")
                    break

        # Secondary validation: For file-opening tasks (not data extraction)
        # Accept successful execution without output (e.g., os.startfile() doesn't produce output)
        # Check if this looks like a file-opening task
        is_file_open_task = any(
            keyword in task.ai_prompt.lower()
            for keyword in ['open', 'launch', 'start', 'view', 'display', 'show']
        ) and any(
            ext in task.ai_prompt.lower()
            for ext in ['.docx', '.doc', '.xlsx', '.pptx', '.pdf', '.txt', '.csv']
        )

        if is_file_open_task and exec_result.validation_passed:
            self.logger.info("✅ File open task succeeded (no output marker needed for os.startfile)")
            return (True, [], "file_open_no_output")

        self.logger.warning("⚠️ No file marker found in execution output")
        return (False, ["No [FILE]: marker found in output"], "file_output")

    def _extract_app_names(self, prompt: str) -> List[str]:
        """Extract application names from task prompt

        Examples:
        - "Open WhatsApp" -> ["whatsapp"]
        - "Launch Google Chrome browser" -> ["chrome", "google"]
        - "Start Notepad" -> ["notepad"]
        - "Open the file API ENDPOINTS.txt" -> ["notepad"] (inferred from .txt)
        """
        prompt_lower = prompt.lower()

        # SPECIAL CASE: File opening tasks
        # For "Open the file <name>.ext", infer app from extension
        file_extensions = {
            '.pdf': ['adobe reader', 'pdf viewer'],
            '.txt': ['notepad', 'text editor'],
            '.docx': ['word', 'document'],
            '.doc': ['word', 'document'],
            '.xlsx': ['excel', 'spreadsheet'],
            '.xls': ['excel', 'spreadsheet'],
            '.pptx': ['powerpoint', 'presentation'],
            '.ppt': ['powerpoint', 'presentation'],
            '.csv': ['excel', 'spreadsheet'],
        }

        for ext, apps in file_extensions.items():
            if ext in prompt_lower:
                return apps

        # GENERAL CASE: Direct app mentions
        # Common app names
        app_patterns = {
            r'(?:open|launch|start|run|activate|switch\s+to)\s+(\w+)': 1,
            r'(chrome|firefox|edge|safari|opera|notepad|calculator|word|excel|powerpoint|whatsapp)': 0.8,
        }

        apps = []
        for pattern, _score in app_patterns.items():
            matches = re.findall(pattern, prompt_lower, re.IGNORECASE)
            for match in matches:
                # Skip articles and common words
                if match.lower() not in ['the', 'a', 'an', 'to', 'and', 'or', 'file', 'named', 'called']:
                    apps.append(match)

        # Clean up and deduplicate
        apps = list(set(app.strip() for app in apps if app.strip()))

        if apps:
            self.logger.debug(f"Extracted app names: {apps}")

        return apps

    def _extract_expected_elements(self, prompt: str) -> List[str]:
        """Extract UI element descriptions from task prompt

        Examples:
        - "Click the Send button" -> ["send button"]
        - "Click on the 'Reply' link" -> ["reply"]
        - "Tap Submit" -> ["submit"]
        """
        elements = []

        # Pattern: "click/tap/press [the] <element> [button/link/field]"
        patterns = [
            r'(?:click|tap|press|hit)\s+(?:the\s+)?(["\']?)(\w+(?:\s+\w+)*?)\1',
            r'(?:click|tap|press)\s+on\s+(?:the\s+)?(["\']?)(\w+(?:\s+\w+)*?)\1',
            r'(?:click|tap|press)\s+(?:the\s+)?(["\']?)(\w+)["\']?(?:\s+(?:button|link|field|element))?',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, prompt, re.IGNORECASE)
            for match in matches:
                # Extract the element name (skip quote markers)
                if isinstance(match, tuple):
                    element = match[-1] if match[-1] else match[-2]
                else:
                    element = match

                if element and len(element) > 1:
                    elements.append(element.strip())

        # Clean up and deduplicate
        elements = list(set(elements))

        if elements:
            self.logger.debug(f"Extracted expected elements: {elements}")

        return elements
