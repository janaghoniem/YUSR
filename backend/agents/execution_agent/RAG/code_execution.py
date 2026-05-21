import json
import logging
import asyncio
import os
import sys
import re
import subprocess
from typing import Dict, Any, Optional
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ============================================================================
# Execution Stop Manager - Handle interrupt/stop commands
# ============================================================================

class ExecutionStopManager:
    """Manages stop signals for running tasks and their subprocesses.

    Notes:
      - This manager is used from both async and sync contexts (some callers use
        asyncio.run / run_coroutine_threadsafe). Using a threading lock avoids
        event-loop affinity issues with asyncio.Lock.
      - Never hold the lock while waiting on subprocess termination.
    """

    def __init__(self):
        import threading

        self._stopped_tasks = set()  # task_ids marked for stop
        self._running_processes = {}  # task_id -> list[subprocess.Popen]
        self._lock = threading.RLock()

    async def mark_task_for_stop(self, task_id: str):
        """Mark a task for stopping and terminate any tracked subprocesses."""
        with self._lock:
            logger.warning(f"⏹️ [STOP] Marking task {task_id} for termination")
            self._stopped_tasks.add(task_id)

        # Kill outside the lock to prevent deadlocks and avoid blocking other calls.
        await self.kill_task_processes(task_id)

    async def is_task_stopped(self, task_id: str) -> bool:
        """Check if a task has been marked for stop."""
        with self._lock:
            return task_id in self._stopped_tasks

    async def register_process(self, task_id: str, process):
        """Register a subprocess for tracking."""
        with self._lock:
            self._running_processes.setdefault(task_id, []).append(process)
            logger.debug(f"📋 [STOP] Registered process {process.pid} for task {task_id}")

    async def kill_task_processes(self, task_id: str):
        """Terminate all tracked subprocesses for a task.

        Kill order: terminate() → wait(timeout=2) → kill() on timeout.
        Stop-state cleanup is handled by cleanup_task() after the task unwinds.
        """
        with self._lock:
            processes = self._running_processes.pop(task_id, [])

        if not processes:
            return

        logger.warning(f"🔪 [STOP] Terminating {len(processes)} process(es) for task {task_id}")

        for proc in processes:
            try:
                if proc.poll() is None:
                    logger.warning(f"   → Terminating PID {proc.pid}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                        logger.info(f"   ✅ Terminated PID {proc.pid}")
                    except subprocess.TimeoutExpired:
                        logger.warning(f"   🔪 Force killing PID {proc.pid}")
                        proc.kill()
                        proc.wait()
            except Exception as e:
                pid = getattr(proc, 'pid', '?')
                logger.error(f"   ❌ Failed to terminate PID {pid}: {e}")

    async def cleanup_task(self, task_id: str):
        """Clean up stop state and any lingering tracked processes for a task."""
        with self._lock:
            self._stopped_tasks.discard(task_id)
            self._running_processes.pop(task_id, None)


# Global stop manager instance
_execution_stop_manager = ExecutionStopManager()


# ============================================================================
# Task Models
# ============================================================================

# ============================================================================
# TESTING FLAGS
# ============================================================================
FORCE_OMNIPARSER_TEST = False  # 🧪 Enable OmniParser testing
OMNIPARSER_TEST_KEYWORDS = []  # Only force on these tasks

class ActionTask:
    def __init__(self, task_id: str, ai_prompt: str, device: str, context: str, 
                 target_agent: str, extra_params: Optional[Dict[str, Any]] = None,
                 web_params: Optional[Dict[str, Any]] = None, depends_on: Optional[List[str]] = None):
        self.task_id = task_id
        self.ai_prompt = ai_prompt
        self.device = device
        self.context = context
        self.target_agent = target_agent
        self.extra_params = extra_params or {}
        self.web_params = web_params or {}
        self.depends_on = depends_on
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionTask':
        return cls(
            task_id=data.get('task_id', ''),
            ai_prompt=data.get('ai_prompt', ''),
            device=data.get('device', 'desktop'),
            context=data.get('context', 'local'),
            target_agent=data.get('target_agent', 'action'),
            extra_params=data.get('extra_params', {}),
            web_params=data.get('web_params', {}),
            depends_on=data.get('depends_on')
        )
    
    def dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'ai_prompt': self.ai_prompt,
            'device': self.device,
            'context': self.context,
            'target_agent': self.target_agent,
            'extra_params': self.extra_params,
            'web_params': self.web_params,
            'depends_on': self.depends_on
        }

class TaskResult:
    def __init__(
        self,
        task_id: str,
        status: str,
        content: Optional[str] = None,
        error: Optional[str] = None,
        details: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        needs_clarification: bool = False,
        clarification_question: Optional[str] = None,
        clarification_type: Optional[str] = None,
        recoverable: bool = False,
    ):
        self.task_id = task_id
        self.status = status
        self.content = content
        self.error = error
        self.details = details
        self.metadata = metadata or {}
        self.needs_clarification = needs_clarification
        self.clarification_question = clarification_question
        self.clarification_type = clarification_type
        self.recoverable = recoverable
    
    def dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'status': self.status,
            'content': self.content,
            'error': self.error,
            'details': self.details,
            'metadata': self.metadata,
            'needs_clarification': self.needs_clarification,
            'clarification_question': self.clarification_question,
            'clarification_type': self.clarification_type,
            'recoverable': self.recoverable,
        }

# ============================================================================
# RAG Task Adapter
# ============================================================================

class RAGTaskAdapter:
    @staticmethod
    def build_rag_query(task: ActionTask) -> str:
        """Build RAG query WITHOUT routing suffix (used for LLM prompt)"""
        query_parts = [task.ai_prompt]

        if task.extra_params:
            if 'app_name' in task.extra_params:
                query_parts.append(f"Application: {task.extra_params['app_name']}")
            if 'url' in task.extra_params:
                query_parts.append(f"URL: {task.extra_params['url']}")
            if 'file_path' in task.extra_params:
                query_parts.append(f"File: {task.extra_params['file_path']}")
            if 'text_to_type' in task.extra_params:
                query_parts.append(f"Text to type: {task.extra_params['text_to_type']}")
            if 'input_content' in task.extra_params:
                content = task.extra_params['input_content']
                # Use smart truncation that preserves data integrity
                if len(content) > 5000:
                    query_parts.append(f"Input data: {content[:4900]}...\n[TRUNCATED - Content too large]")
                else:
                    query_parts.append(f"Input data: {content}")

        # ⚠️ REMOVED routing suffix "(desktop automation)" / "(Playwright web automation)"
        # Routing suffix was causing file_agent to receive wrong file names
        # e.g., find_file("GKE | (desktop automation)") instead of find_file("GKE")

        query = " | ".join(query_parts)
        logger.debug(f"🔍 RAG Query: {query[:100]}...")
        return query
    
    @staticmethod
    def execution_result_to_task_result(task: ActionTask, execution_result) -> TaskResult:
        if execution_result.validation_passed and execution_result.security_passed:
            status = "success"
            content = execution_result.stdout
            error = None
        else:
            status = "failed"
            content = None
            errors = []
            if execution_result.validation_errors:
                errors.extend(execution_result.validation_errors)
            if execution_result.security_violations:
                errors.extend(execution_result.security_violations)
            if execution_result.stderr:
                errors.append(f"stderr: {execution_result.stderr[:200]}")
            error = " | ".join(errors)
        
        return TaskResult(
            task_id=task.task_id,
            status=status,
            content=content,
            error=error
        )

# ============================================================================
# Coordinator RAG Bridge (Desktop Tasks)
# ============================================================================

class CoordinatorRAGBridge:
    def __init__(self, rag_system, sandbox_pipeline):
        self.rag = rag_system
        self.sandbox = sandbox_pipeline
        self.adapter = RAGTaskAdapter()
        self.omniparser = None
        self.last_file_path = None  # Tracks active file across sequential tasks

        # Initialize task validator for post-execution validation
        from agents.execution_agent.RAG.task_validator import TaskValidator, WindowState
        self.validator = TaskValidator()
        self.window_state_class = WindowState

        # 🔥 PRE-LOAD FILE INDEX to avoid timeout on first find_file() call
        # This is critical - builds cache on first agent run (~5-15s), instant on subsequent runs
        # try:
        #     logger.info("📂 [INIT] Pre-loading file index for fast file searches...")
        #     from agents.execution_agent.RAG.file_agent import preload_index
        #     preload_index()
        #     logger.info("✅ [INIT] File index ready")
        # except Exception as e:
        #     logger.warning(f"⚠️ [INIT] Could not pre-load file index: {e}")
        #     logger.info("   (Index will be loaded on first find_file() call)")


    #added by shahd for omniparser
    def _detect_element_coordinates(self, element_description: str) -> Optional[tuple]:
        """
        Use OmniParser to detect UI element coordinates
        IMPROVED: Captures only the active window, not entire desktop
        """
        try:
            if self.omniparser is None:
                logger.info("🔄 Initializing OmniParser detector...")
                from agents.execution_agent.fallback.omniparser_detector import OmniParserDetector
                import logging
                omni_logger = logging.getLogger("OmniParser")
                self.omniparser = OmniParserDetector(omni_logger)
                logger.info("✅ OmniParser ready")
            
            # ====================================================================
            # NEW: Capture only the active window
            # ====================================================================
            import pygetwindow as gw
            import time
            
            # Get active window
            active_window = gw.getActiveWindow()
            
            if active_window:
                logger.info(f"🪟 Active window: '{active_window.title}'")
                logger.info(f"   Position: ({active_window.left}, {active_window.top})")
                logger.info(f"   Size: {active_window.width}x{active_window.height}")
                
                # Bring window to front just in case
                try:
                    active_window.activate()
                    time.sleep(0.3)
                except:
                    pass
                
                # Take screenshot of just this window's region
                import pyautogui
                screenshot = pyautogui.screenshot(region=(
                    active_window.left,
                    active_window.top,
                    active_window.width,
                    active_window.height
                ))
                
                # Save temporarily for debugging
                import tempfile
                import os
                temp_path = os.path.join(tempfile.gettempdir(), "omniparser_window.png")
                screenshot.save(temp_path)
                logger.info(f"💾 Saved window screenshot to: {temp_path}")
                
                # Use OmniParser on this window-only screenshot
                logger.info(f"🔍 Using OmniParser to find: '{element_description}'")
                result = self.omniparser.detect_element_by_text(
                    element_description,
                    screenshot_path=temp_path
                )
                
                # Adjust coordinates to screen space (add window offset)
                if result.success and result.coordinates:
                    screen_x = result.coordinates[0] + active_window.left
                    screen_y = result.coordinates[1] + active_window.top
                    adjusted_coords = (screen_x, screen_y)
                    
                    logger.info(f"✅ Found at window coords: {result.coordinates}")
                    logger.info(f"✅ Adjusted to screen coords: {adjusted_coords}")
                    
                    return adjusted_coords
                else:
                    logger.warning(f"❌ OmniParser couldn't find: '{element_description}'")
                    return None
            
            else:
                logger.warning("⚠️ No active window detected, falling back to full screen")
                # Fallback to full screen
                logger.info(f"🔍 Using OmniParser to find: '{element_description}'")
                result = self.omniparser.detect_element_by_text(element_description)
                
                if result.success and result.coordinates:
                    logger.info(f"✅ Found at coordinates: {result.coordinates}")
                    return result.coordinates
                else:
                    logger.warning(f"❌ OmniParser couldn't find: '{element_description}'")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ OmniParser detection error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_element_description(self, task: ActionTask, error_msg: str) -> Optional[str]:
        """
        Extract UI element from task prompt - handle both capitalized and lowercase names

        Examples:
        - "Click on the join button in Zoom" → "join button"
        - "Click the Send button" → "send button"
        - "Navigate to gaming section" → "gaming"
        - "Open YouTube app" → "YouTube"
        """
        prompt = task.ai_prompt.lower()

        # Skip OmniParser for app launch tasks
        app_launch_keywords = ['open', 'launch', 'start', 'run']
        is_app_launch = any(keyword in prompt.split()[:2] for keyword in app_launch_keywords)

        if is_app_launch:
            logger.info(f"⏭️ Skipping OmniParser - this is an app launch task")
            return None

        # ═══════════════════════════════════════════════════════════════════
        # Strategy 1: Extract from "click on/the X [location]" patterns
        # Handle both lowercase and capitalized: "join button" or "Send Button"
        # ═══════════════════════════════════════════════════════════════════

        click_patterns = [
            # "click on the join button in Zoom" → "join button"
            r'click\s+on\s+(?:the\s+)?([a-z]+(?:\s+[a-z]+)*?)(?:\s+(?:in|at|from|for)\s|\s*$)',
            # "click the send button" → "send button"
            r'click\s+(?:the\s+)?([a-z]+(?:\s+[a-z]+)*?)(?:\s+(?:in|at|from|for)\s|\s*$)',
            # "tap/press the X" → "X"
            r'(?:tap|press)\s+(?:on\s+)?(?:the\s+)?([a-z]+(?:\s+[a-z]+)*?)(?:\s+(?:in|at|from|for)\s|\s*$)',
        ]

        for pattern in click_patterns:
            match = re.search(pattern, prompt)
            if match:
                element_text = match.group(1).strip()

                # Filter out common action words
                stopwords = {'the', 'a', 'an', 'on', 'in', 'at'}
                if element_text not in stopwords:
                    logger.info(f"📝 Extracted from click pattern: '{element_text}'")
                    return element_text

        # Strategy 2: Look for "X section/tab/menu" patterns
        section_patterns = [
            r'(?:to|the)\s+([a-z]+)\s+(?:section|tab|menu|page)',
            r'([a-z]+)\s+(?:section|tab|menu|page)',
        ]

        for pattern in section_patterns:
            match = re.search(pattern, prompt)
            if match:
                element_text = match.group(1).strip()
                logger.info(f"📝 Extracted from section pattern: '{element_text}'")
                return element_text

        # Strategy 3: Look for capitalized words (last resort)
        capital_words = re.findall(r'\b[A-Z][a-z]+\b', task.ai_prompt)
        if capital_words:
            stopwords = {'Click', 'Open', 'The', 'In', 'Microsoft', 'Store', 'On',
                        'Navigate', 'Go', 'To', 'Close', 'Switch', 'Tap', 'Press'}
            filtered = [w for w in capital_words if w not in stopwords]
            if filtered:
                element_text = filtered[0]
                logger.info(f"📝 Extracted from capitalized words: '{element_text}'")
                return element_text

        # If nothing found, skip OmniParser
        logger.warning(f"⚠️ Could not extract UI element - skipping OmniParser")
        return None


    def _regenerate_code_with_coordinates(self,
                                                task: ActionTask, 
                                                coordinates: tuple,
                                                element_description: str) -> str:
            """
            Generate code that clicks specific coordinates found by OmniParser
            FIXED: Uses textwrap.dedent to remove leading whitespace
            
            Args:
                task: Original task
                coordinates: (x, y) from OmniParser
                element_description: What was detected
            
            Returns:
                Python code string
            """
            import textwrap  # ← ADD THIS!
            x, y = coordinates
            
            # Determine action type
            action = "click"
            if 'double' in task.ai_prompt.lower():
                action = "double_click"
            elif 'type' in task.ai_prompt.lower() or 'enter' in task.ai_prompt.lower():
                action = "click_then_type"
            
            # Generate code based on action
            if action == "click":
                code = textwrap.dedent(f"""
                    import pyautogui
                    import time

                    try:
                        # OmniParser detected '{element_description}' at ({x}, {y})
                        pyautogui.click(x={x}, y={y})
                        time.sleep(0.5)
                        print("EXECUTION_SUCCESS: Clicked {element_description}")
                    except Exception as e:
                        print(f"FAILED: {{str(e)}}")
                """).strip()  # ← .strip() removes leading/trailing whitespace
            
            elif action == "double_click":
                code = textwrap.dedent(f"""
                    import pyautogui
                    import time

                    try:
                        # OmniParser detected '{element_description}' at ({x}, {y})
                        pyautogui.doubleClick(x={x}, y={y})
                        time.sleep(0.5)
                        print("EXECUTION_SUCCESS: Double-clicked {element_description}")
                    except Exception as e:
                        print(f"FAILED: {{str(e)}}")
                """).strip()
            
            elif action == "click_then_type":
                text_to_type = task.extra_params.get('text_to_type', '')
                code = textwrap.dedent(f"""
                    import pyautogui
                    import time

                    try:
                        # OmniParser detected '{element_description}' at ({x}, {y})
                        pyautogui.click(x={x}, y={y})
                        time.sleep(0.3)
                        pyautogui.write('{text_to_type}', interval=0.05)
                        time.sleep(0.2)
                        print("EXECUTION_SUCCESS: Typed into {element_description}")
                    except Exception as e:
                        print(f"FAILED: {{str(e)}}")
                """).strip()
            else:
                # Fallback to simple click
                code = textwrap.dedent(f"""
                    import pyautogui
                    import time

                    try:
                        pyautogui.click(x={x}, y={y})
                        time.sleep(0.5)
                        print("EXECUTION_SUCCESS")
                    except Exception as e:
                        print(f"FAILED: {{str(e)}}")
                """).strip()
            
            return code
    
    # async def execute_action_task(
    #     self,
    #     task: ActionTask,
    #     max_retries: int = 3,
    #     enable_cache: bool = False,  # Enable cache by default
    #     cache_threshold: float = 0.85
    # ) -> TaskResult:
    #     """
    #     Execute a single ActionTask using RAG pipeline with cache support
        
    #     Args:
    #         task: ActionTask from coordinator
    #         max_retries: Maximum retry attempts
    #         enable_cache: Whether to check/use cache
    #         cache_threshold: Similarity threshold for cache hit (0.85 = 85%)
            
    #     Returns:
    #         TaskResult for coordinator
    #     """
    #     logger.info(f"🔄 Processing task {task.task_id}: {task.ai_prompt[:50]}...")
    def _extract_template_from_full_code(self, full_code: str) -> str:
        """
        Extract the function template from full code (removes __main__ block)

        Args:
            full_code: Complete generated code with function + __main__

        Returns:
            Just the function definition (without __main__)
        """
        # Find where __main__ starts
        if 'if __name__ == "__main__":' in full_code:
            template = full_code.split('if __name__ == "__main__":')[0].strip()
            return template
        # If no __main__, return as-is
        return full_code

    async def _attempt_omniparser_fallback(self, task: ActionTask, error_context: str) -> Optional[TaskResult]:
        """
        Attempt OmniParser fallback when UI element detection is needed

        Args:
            task: The ActionTask to process
            error_context: Error message from execution failure

        Returns:
            TaskResult if fallback succeeds, None if fallback fails or not applicable
        """
        try:
            # Extract what to look for
            element_desc = self._extract_element_description(task, error_context)

            if element_desc is None:
                logger.info("⏭️ Skipping OmniParser - not a UI interaction task")
                return None
            elif not element_desc:
                logger.info("⏭️ Could not extract element description")
                return None

            # Try to detect coordinates
            logger.info(f"🎯 Attempting OmniParser detection for: '{element_desc}'")
            coords = self._detect_element_coordinates(element_desc)

            if not coords:
                logger.warning(f"❌ OmniParser couldn't find: '{element_desc}'")
                return None

            logger.info(f"✅ OmniParser found element at {coords}!")

            # Generate new code with exact coordinates
            new_code = self._regenerate_code_with_coordinates(
                task, coords, element_desc
            )

            # Execute the new code
            logger.info("🔄 Executing OmniParser-assisted code...")
            logger.debug(f"Generated code:\n{new_code}")

            exec_result = self.sandbox.execute_code(
                code=new_code,
                use_docker=False,
                retry_on_failure=False,
                task_id=task.task_id  # ✅ PASS TASK ID FOR SUBPROCESS TRACKING
            )

            if exec_result.validation_passed and exec_result.security_passed:
                logger.info(f"✅✅✅ Task succeeded with OmniParser assistance!")

                # Parse [FILE]: from stdout
                if exec_result.stdout:
                    for line in exec_result.stdout.splitlines():
                        if line.startswith('[FILE]:'):
                            self.last_file_path = line[7:].strip()
                            logger.info(f"[FILE CONTEXT] Captured: {self.last_file_path}")
                            break

                return self.adapter.execution_result_to_task_result(task, exec_result)
            else:
                logger.warning("⚠️ OmniParser-assisted code also failed")
                logger.debug(f"OmniParser execution stdout: {exec_result.stdout}")
                logger.debug(f"OmniParser execution stderr: {exec_result.stderr}")
                return None

        except Exception as e:
            logger.warning(f"⚠️ OmniParser fallback error: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None

    async def _execute_click_task_with_omniparser(self, task: ActionTask) -> TaskResult:
        """Execute click tasks using OmniParser for element detection

        For tasks like "Click the Send button", use OmniParser to:
        1. Find the button coordinates
        2. Generate code to click those exact coordinates
        3. Execute and return result
        """
        try:
            logger.info(f"🎯 Executing click task with OmniParser: {task.ai_prompt}")

            # Initialize OmniParser if needed
            if self.omniparser is None:
                logger.info("🔄 Initializing OmniParser...")
                from agents.execution_agent.fallback.omniparser_detector import OmniParserDetector
                import logging as logging_module
                omni_logger = logging_module.getLogger("OmniParser")
                self.omniparser = OmniParserDetector(omni_logger)
                logger.info("✅ OmniParser ready")

            # Extract what element to click
            element_desc = self._extract_element_description(task, task.ai_prompt)

            if not element_desc:
                logger.warning("⚠️ Could not extract element description from task")
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error="Could not determine what to click"
                )

            logger.info(f"🔍 Finding element: '{element_desc}'")

            # Use OmniParser to find coordinates
            coords = self._detect_element_coordinates(element_desc)

            if not coords:
                logger.warning(f"❌ OmniParser could not find: '{element_desc}'")
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error=f"Element not found: {element_desc}"
                )

            logger.info(f"✅ Found element at coordinates: {coords}")

            # Generate code to click those coordinates
            click_code = self._regenerate_code_with_coordinates(task, coords, element_desc)

            logger.info(f"🔧 Executing click code at {coords}...")
            logger.debug(f"Generated code:\n{click_code}")

            # Execute the code
            exec_result = self.sandbox.execute_code(
                code=click_code,
                use_docker=False,
                retry_on_failure=False,
                task_id=task.task_id  # ✅ PASS TASK ID FOR SUBPROCESS TRACKING
            )

            # Return result
            if exec_result.validation_passed and exec_result.security_passed:
                logger.info(f"✅ Click task succeeded!")
                return self.adapter.execution_result_to_task_result(task, exec_result)
            else:
                logger.warning(f"⚠️ Click execution failed")
                logger.debug(f"Stdout: {exec_result.stdout}")
                logger.debug(f"Stderr: {exec_result.stderr}")
                return TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error=f"Click failed: {', '.join(exec_result.validation_errors)}"
                )

        except Exception as e:
            logger.error(f"❌ Click task exception: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                error=str(e)
            )

    async def execute_action_task(self, task: ActionTask, max_retries: int = 3, enable_cache: bool = False) -> TaskResult:
        logger.info(f"🖥️ Processing DESKTOP task {task.task_id}: {task.ai_prompt[:50]}...")

        # Only process action tasks
        if task.target_agent != "action":
            logger.warning(f"⚠️ Task {task.task_id} is not an action task, skipping RAG")
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                error="Not an action task - should be handled by reasoning agent"
            )

        # ========================================================================
        # FAST PATH: CLICK TASKS - Use OmniParser directly (no guessing)
        # ========================================================================
        is_click_task = any(
            keyword in task.ai_prompt.lower()
            for keyword in ['click', 'tap', 'press', 'select', 'button']
        )

        if is_click_task:
            logger.info("🖱️ Click task detected - using OmniParser for direct clicking")
            return await self._execute_click_task_with_omniparser(task)

        # ========================================================================
        # STEP 0A: ROUTE TASK TO MODULE (word/excel/powerpoint/general)
        # ========================================================================
        from agents.execution_agent.RAG.module_router import ModuleRouter
        router = ModuleRouter()
        module = router.route_task(task.ai_prompt)
        logger.info(f"[ROUTING] Task routed to module: '{module}'")

        # ========================================================================
        # CAPTURE WINDOW STATE FOR TASK VALIDATION
        # ========================================================================
        before_state = self.window_state_class.capture()
        logger.debug(f"📸 Captured initial window state: {before_state.process}")

        # Build enhanced query for RAG
        rag_query = self.adapter.build_rag_query(task)

        # Inject active file context ONLY for dependent tasks (not new independent tasks)
        if self.last_file_path and task.depends_on:
            rag_query = f"[ACTIVE FILE: {self.last_file_path}]\n\n{rag_query}"
            logger.info(f"[FILE CONTEXT] Injecting active file: {self.last_file_path}")
        elif not task.depends_on:
            # New independent task - clear file path so it doesn't bleed into next tasks
            logger.info(f"[FILE CONTEXT] Clearing file context for new independent task")
            self.last_file_path = None

        # ========================================================================
        # STEP 0B: CHECK CACHE FIRST (if enabled) - FILTERED BY MODULE
        # ========================================================================
        if enable_cache and hasattr(self.sandbox, 'action_cache'):
            try:
                logger.info(f"🔍 Checking cache in '{module}' module for task {task.task_id}...")
                cached_template = self.sandbox.action_cache.search_cache(
                    query=rag_query,
                    module=module  # ← SEARCH ONLY IN THIS MODULE
                )

                if cached_template and cached_template.get('cache_hit'):
                    logger.info(f"✅ TEMPLATE CACHE HIT! Similarity: {cached_template['similarity']:.2%}")

                    # Use mini prompt to LLM to just write __main__ with new parameters
                    from agents.execution_agent.RAG.cache_mini_prompt import generate_cache_hit_mini_prompt, extract_function_name

                    template_code = cached_template['template']
                    mini_prompt = generate_cache_hit_mini_prompt(
                        template_code=template_code,
                        user_query=rag_query,
                        input_data=None
                    )

                    logger.info(f"⚡ Using mini prompt to fill template __main__ section...")

                    # Generate ONLY the __main__ section with LLM
                    try:
                        rag_result = self.rag.generate_code(
                            mini_prompt,
                            cache_key=None,  # Don't cache the mini prompt response
                            start_context_index=0,
                            num_contexts=1,
                            screen_state={'active_window': 'N/A', 'process': 'N/A', 'controls': []},
                            task_id=task.task_id  # ✅ PASS TASK ID FOR STOP SIGNAL HANDLING
                        )

                        main_code = rag_result.get('code', '').strip()
                        if not main_code:
                            logger.warning(f"⚠️ Mini prompt did not generate code, falling back to full generation...")
                            raise Exception("Empty main code from mini prompt")

                        # Extract ONLY the __main__ block (LLM might return full code with function)
                        if 'if __name__' in main_code:
                            # Take everything from 'if __name__' onward
                            parts = main_code.split('if __name__')
                            main_code = "if __name__" + parts[-1]  # Reconstruct with if __name__
                        elif not main_code.startswith('if __name__'):
                            main_code = f"if __name__ == \"__main__\":\n" + main_code

                        # Combine template function with new __main__
                        # Remove __main__ from cached template if it exists
                        if 'if __name__' in template_code:
                            func_def = template_code.split('if __name__')[0].strip()
                        else:
                            func_def = template_code.strip()
                                     # Fix wrapper function calls - LLM might generate a wrapper instead of using template
                        # Extract the actual template function name and replace any wrapper calls
                        from agents.execution_agent.RAG.cache_mini_prompt import extract_function_name
                        actual_func_name = extract_function_name(template_code)

                        # Replace common wrapper patterns (function_name_application, function_name_app, etc.)
                        import re
                        wrapper_patterns = [
                            f'{actual_func_name}_application',
                            f'{actual_func_name}_app',
                            f'{actual_func_name}_task',
                            f'{actual_func_name}_execute'
                        ]

                        for wrapper_name in wrapper_patterns:
                            # Replace wrapper function call with actual template function
                            # Match: success = wrapper_name() or result = wrapper_name()
                            main_code = re.sub(
                                rf'(\w+)\s*=\s*{wrapper_name}\s*\(',
                                rf'\1 = {actual_func_name}(',
                                main_code
                            )


                        filled_code = f"{func_def}\n\n{main_code}"

                        logger.info(f"📝 Generated __main__ section ({len(main_code)} chars)")

                        # Execute the filled template
                        import subprocess
                        import tempfile
                        import sys
                        import time

                        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.py', delete=False) as f:
                            f.write(filled_code)
                            temp_file = f.name

                        try:
                            start_time = time.time()
                            result = subprocess.run(
                                [sys.executable, temp_file],
                                capture_output=True,
                                text=True,
                                timeout=30
                            )
                            execution_time = time.time() - start_time

                            # Create execution result
                            from agents.execution_agent.RAG.execution import ExecutionResult, ExecutionStatus
                            from datetime import datetime
                            import hashlib

                            # Generate code hash
                            code_hash = hashlib.md5(filled_code.encode()).hexdigest()

                            exec_result = ExecutionResult(
                                status=ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.FAILED,
                                exit_code=result.returncode,
                                stdout=result.stdout,
                                stderr=result.stderr,
                                execution_time=execution_time,
                                timestamp=datetime.now().isoformat(),
                                validation_passed=True,
                                validation_errors=[],
                                security_passed=True,
                                security_violations=[],
                                code_hash=code_hash
                            )

                            logger.info(f"✅ Template executed in {execution_time:.3f}s (cache hit reuse)")

                            # Parse [FILE]: from stdout
                            if exec_result.stdout:
                                for line in exec_result.stdout.splitlines():
                                    if line.startswith('[FILE]:'):
                                        self.last_file_path = line[7:].strip()
                                        logger.info(f"[FILE CONTEXT] Captured: {self.last_file_path}")
                                        break

                            # ============================================================
                            # Validate cache hit execution (validate only on failures)
                            # ============================================================
                            if not (exec_result.validation_passed and exec_result.security_passed):
                                after_state = self.window_state_class.capture()
                                task_validation = self.validator.validate_execution(
                                    task=task,
                                    exec_result=exec_result,
                                    before_state=before_state,
                                    after_state=after_state,
                                    omniparser=self.omniparser,
                                    is_cache_hit=True
                                )

                                if not task_validation.passed and task_validation.should_trigger_fallback:
                                    logger.warning(f"⚠️ Cache hit validation failed, attempting OmniParser fallback...")
                                    fallback_result = await self._attempt_omniparser_fallback(task, "Cache hit validation failed")
                                    if fallback_result:
                                        return fallback_result

                                # If task validation failed (even without fallback), return failure
                                if not task_validation.passed:
                                    logger.error(f"❌ Cache hit task validation failed: {task_validation.failures}")
                                    return TaskResult(
                                        task_id=task.task_id,
                                        status="failed",
                                        error=f"Task validation failed: {'; '.join(task_validation.failures)}"
                                    )

                            return self.adapter.execution_result_to_task_result(task, exec_result)

                        except Exception as e:
                            logger.warning(f"⚠️ Template execution failed: {e}")
                            logger.info("🔄 Falling back to full RAG generation...")
                        finally:
                            import os
                            try:
                                os.unlink(temp_file)
                            except:
                                pass

                    except Exception as e:
                        logger.warning(f"⚠️ Mini prompt generation failed: {e}")
                        logger.info("🔄 Falling back to full RAG generation...")

                else:
                    logger.info(f"❌ Cache miss in '{module}' module - proceeding with full RAG generation")

            except Exception as cache_error:
                logger.warning(f"⚠️ Cache check failed: {cache_error}")
                logger.info("🔄 Proceeding with RAG generation...")

        # ========================================================================
        # CACHE MISS OR DISABLED - FULL RAG + SANDBOX FLOW
        # ========================================================================
        import pygetwindow as gw

        try:
            active_window = gw.getActiveWindow()

            if active_window:
                screen_state = {
                    'active_window': active_window.title,
                    'process': active_window.title.split(' - ')[-1] if ' - ' in active_window.title else 'Unknown',
                    'controls': []  # pygetwindow doesn't provide controls
                }
                logger.info(f"🪟 Active window: '{active_window.title}'")
            else:
                # Fallback if no active window
                screen_state = {
                    'active_window': 'Desktop',
                    'process': 'explorer',
                    'controls': []
                }
                logger.info("🪟 No active window, using Desktop")

        except Exception as e:
            logger.warning(f"⚠️ Could not get active window: {e}")
            # Fallback
            screen_state = {
                'active_window': 'Unknown',
                'process': 'Unknown',
                'controls': []
            }

        attempt = 0
        error_context = ""
        start_context_index = 0
        
        while attempt < max_retries:
            if await _execution_stop_manager.is_task_stopped(task.task_id):
                logger.warning(f"⏹️ Task {task.task_id} marked for stop, aborting execution loop")
                return TaskResult(
                    task_id=task.task_id,
                    status="stopped",
                    error="Task stopped by user"
                )

            attempt += 1
            logger.info(f"🔍 Attempt {attempt}/{max_retries} for task {task.task_id}")
            
            enhanced_query = f"\nCurrent Screen: {screen_state['active_window']}\nVisible Controls: {', '.join(screen_state['controls'])}"
            # Build enhanced query with error context if retry
            enhanced_query = rag_query
            if error_context:
                enhanced_query += f"\n\nPrevious attempt failed: {error_context}"
                enhanced_query += "\nPlease provide an alternative approach."
            
            try:
                # Step 1: Generate code using RAG
                logger.info(f"🤖 Generating code with RAG...")
                rag_result = await asyncio.to_thread(
                    self.rag.generate_code,
                    enhanced_query,
                    cache_key=task.ai_prompt,  # Use original prompt for cache key
                    start_context_index=start_context_index,
                    num_contexts=self.rag.config.top_k,
                    screen_state=screen_state,
                    task_id=task.task_id  # ✅ PASS TASK ID FOR STOP SIGNAL HANDLING
                )

                if await _execution_stop_manager.is_task_stopped(task.task_id):
                    logger.warning(f"⏹️ Task {task.task_id} marked for stop, aborting after code generation")
                    return TaskResult(
                        task_id=task.task_id,
                        status="stopped",
                        error="Task stopped by user"
                    )
                
                generated_code = rag_result.get('code', '')
                
                if not generated_code:
                    logger.warning(f"⚠️ No code generated for task {task.task_id}")
                    
                    if rag_result.get('contexts_used', 0) == 0:
                        logger.error("❌ No more contexts available")
                        break
                    
                    start_context_index += self.rag.config.top_k
                    continue
                
                logger.info(f"✅ Generated {len(generated_code)} chars of code")
                logger.debug(f"Generated code preview: {generated_code[:200]}...")

                # Step 2: Execute in LOCAL sandbox
                logger.info(f"🔧 Executing code in local sandbox...")
                exec_result = await asyncio.to_thread(
                    self.sandbox.execute_code,
                    code=generated_code,
                    use_docker=False,
                    retry_on_failure=False,
                    task_id=task.task_id  # ✅ PASS TASK ID FOR SUBPROCESS TRACKING
                )

                if await _execution_stop_manager.is_task_stopped(task.task_id):
                    logger.warning(f"⏹️ Task {task.task_id} marked for stop, aborting after execution")
                    return TaskResult(
                        task_id=task.task_id,
                        status="stopped",
                        error="Task stopped by user"
                    )

                # ========================================================================
                # NEW: Post-Execution Task Validation
                # ========================================================================
                # Capture window state AFTER execution
                after_state = self.window_state_class.capture()
                logger.debug(f"📸 Captured window state after execution: {after_state.process}")

                # Validate task succeeded (beyond code-level validation)
                task_validation = self.validator.validate_execution(
                    task=task,
                    exec_result=exec_result,
                    before_state=before_state,
                    after_state=after_state,
                    omniparser=self.omniparser,
                    is_cache_hit=False  # This is full RAG execution, not cache hit
                )

                # Check if validation failed and should trigger OmniParser fallback
                if not task_validation.passed and task_validation.should_trigger_fallback:
                    logger.warning(f"⚠️ Task validation failed but OmniParser-eligible. Attempting fallback...")
                    fallback_result = await self._attempt_omniparser_fallback(task, error_context)
                    if fallback_result:
                        return fallback_result
                    # If fallback fails or returns None, continue to normal failure handling below

                # If task validation failed, retry with different code generation approach
                if not task_validation.passed:
                    logger.warning(f"⚠️ Task validation failed (attempt {attempt}/{max_retries}): {task_validation.failures}")

                    # Prepare for retry with error context
                    error_context = f"Validation failed: {'; '.join(task_validation.failures)}"
                    start_context_index += self.rag.config.top_k

                    if attempt >= max_retries:
                        logger.error(f"❌ Max retries exhausted ({max_retries} attempts)")
                        return TaskResult(
                            task_id=task.task_id,
                            status="failed",
                            error=f"Task validation failed after {max_retries} attempts: {'; '.join(task_validation.failures)}"
                        )

                    logger.info(f"🔄 Retrying with alternative approach...")
                    continue

                # Step 3: Check execution result
                if exec_result.validation_passed and exec_result.security_passed:
                    logger.info(f"✅ Task {task.task_id} completed successfully")

                    # Parse [FILE]: from stdout to track active file for next task
                    if exec_result.stdout:
                        for line in exec_result.stdout.splitlines():
                            if line.startswith('[FILE]:'):
                                self.last_file_path = line[7:].strip()
                                logger.info(f"[FILE CONTEXT] Captured: {self.last_file_path}")
                                break

                    # ============================================================
                    # CACHE THE SUCCESSFUL RESULT (if cache enabled and available)
                    # ============================================================
                    if enable_cache and hasattr(self.sandbox, 'action_cache'):
                        try:
                            logger.info(f"💾 Caching validated template in '{module}' module...")

                            # Extract just the function template (without the __main__ block)
                            template_code = self._extract_template_from_full_code(generated_code)

                            self.sandbox.action_cache.store_action(
                                query=rag_query,
                                code=template_code,  # Store just the function template
                                module=module,  # ← STORE WITH MODULE
                                execution_result=exec_result
                            )
                            logger.info(f"✅ Template cached successfully in '{module}' module")
                        except Exception as cache_error:
                            logger.warning(f"⚠️ Failed to cache action: {cache_error}")
                            # Don't fail the task if caching fails
                    
                    return self.adapter.execution_result_to_task_result(task, exec_result)
                
                # Execution failed - prepare for retry
                logger.warning(f"⚠️ Execution failed (attempt {attempt})")
                error_context = f"Errors: {', '.join(exec_result.validation_errors)}"
                if exec_result.stderr:
                    error_context += f" | stderr: {exec_result.stderr[:200]}"

                # ========================================================================
                # NEW: Try OmniParser detection if error suggests element not found
                # ========================================================================
                if any(keyword in error_context.lower() for keyword in 
                    [
                        'not found', 'cannot find', 'no such element', 'failed to locate',
                        'modulenotfounderror', 'importerror',
                        'pywinauto', 'uiautomation',
                        'element', 'button', 'window',
                        'failed:', 'error:',
                        'locateonscreen'
                    ]):

                    logger.info(f"🔍 OmniParser trigger check:")
                    logger.info(f"   Error context: {error_context[:200]}")
                    logger.info(f"   Attempting OmniParser fallback...")
                                                    
                    logger.warning("🔍 Error suggests element detection issue - trying OmniParser...")
                    
                    # Extract what to look for
                    element_desc = self._extract_element_description(task, error_context)
                    
                    if element_desc is None:
                        logger.info("⏭️ Skipping OmniParser - not a UI interaction task")
                        # Don't continue here - let it retry with next context
                    elif element_desc:
                        # Try to detect coordinates
                        logger.info(f"🎯 Valid UI element detected: '{element_desc}'")
                        coords = self._detect_element_coordinates(element_desc)
                        
                        if coords:
                            logger.info(f"✅ OmniParser found element at {coords}!")
                            
                            # Generate new code with exact coordinates
                            new_code = self._regenerate_code_with_coordinates(
                                task, coords, element_desc
                            )

                            # Execute the new code
                            logger.info("🔄 Executing OmniParser-assisted code...")
                            logger.debug(f"Generated code:\n{new_code}")  # Use debug level, not info

                            exec_result = await asyncio.to_thread(
                                self.sandbox.execute_code,
                                code=new_code,
                                use_docker=False,
                                retry_on_failure=False,
                                task_id=task.task_id  # ✅ PASS TASK ID FOR SUBPROCESS TRACKING
                            )
                            
                            if exec_result.validation_passed and exec_result.security_passed:
                                logger.info(f"✅✅✅ Task succeeded with OmniParser assistance!")
                                return self.adapter.execution_result_to_task_result(task, exec_result)
                            else:
                                logger.warning("⚠️ OmniParser-assisted code also failed")
                                logger.debug(f"OmniParser execution stdout: {exec_result.stdout}")
                                logger.debug(f"OmniParser execution stderr: {exec_result.stderr}")
                        else:
                            logger.warning(f"❌ OmniParser couldn't find: '{element_desc}'")
                
                logger.debug(f"Error context for retry: {error_context}")
                
                start_context_index += self.rag.config.top_k
                
            except Exception as e:
                logger.error(f"❌ Exception during RAG execution: {e}")
                import traceback
                logger.debug(f"Traceback: {traceback.format_exc()}")
                error_context = str(e)
        
        # All retries exhausted
        logger.error(f"❌ Task {task.task_id} failed after {max_retries} attempts")
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error=f"Failed after {max_retries} attempts: {error_context}"
        )

# ============================================================================
# Web RAG Bridge (✅ FIXED)
# ============================================================================

class CoordinatorWebRAGBridge:
    def __init__(self, web_pipeline):
        self.web = web_pipeline
        self.adapter = RAGTaskAdapter()
    
    async def execute_web_action_task(self, task: ActionTask, session_id: str = "default", max_retries: int = 2) -> TaskResult:
        logger.info(f"🌐 Processing WEB task {task.task_id}: {task.ai_prompt[:50]}...")
        
        if task.target_agent != "action":
            logger.warning(f"⚠️ Task {task.task_id} is not an action task")
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                error="Not an action task - should be handled by reasoning agent"
            )
        
        attempt = 0
        error_context = ""
        
        while attempt < max_retries:
            if await _execution_stop_manager.is_task_stopped(task.task_id):
                logger.warning(f"⏹️ Web task {task.task_id} marked for stop, aborting execution loop")
                return TaskResult(
                    task_id=task.task_id,
                    status="stopped",
                    error="Task stopped by user"
                )

            attempt += 1
            logger.info(f"🔄 Attempt {attempt}/{max_retries} for task {task.task_id}")
            
            enhanced_query = task.ai_prompt
            if error_context:
                enhanced_query += f" | Previous errors: {error_context[:100]}"
            
            try:
                logger.info(f"🔍 Executing web task...")
                
                task_dict = {
                    'task_id': task.task_id,
                    'ai_prompt': enhanced_query,
                    'web_params': task.web_params,
                    'extra_params': task.extra_params,
                }
                
                exec_result = await self.web.execute_web_task(task_dict, session_id)

                if await _execution_stop_manager.is_task_stopped(task.task_id):
                    logger.warning(f"⏹️ Web task {task.task_id} marked for stop, aborting after execution")
                    return TaskResult(
                        task_id=task.task_id,
                        status="stopped",
                        error="Task stopped by user"
                    )
                
                # ✅ FIX: exec_result is a WebExecutionResult dataclass, not a dict
                if exec_result.validation_passed and exec_result.security_passed:
                    logger.info(f"✅ Web task {task.task_id} completed successfully")
                    return TaskResult(
                        task_id=task.task_id,
                        status="success",
                        content=exec_result.output or '',
                        error=None
                    )

                auth_required_prefix = "AUTH_REQUIRED:"
                exec_error = (exec_result.error or "").strip()
                if exec_error.startswith(auth_required_prefix):
                    question = exec_error[len(auth_required_prefix):].strip() or "I need credentials to continue."
                    logger.info(f"🛑 Pausing web task for credentials: {question}")
                    return TaskResult(
                        task_id=task.task_id,
                        status="awaiting_confirmation",
                        content=question,
                        error=exec_error,
                        details="google_auth_required",
                        metadata={"domain": "google_auth", "attempt": attempt},
                        needs_clarification=True,
                        clarification_question=question,
                        clarification_type="credentials_required",
                        recoverable=True,
                    )
                
                logger.warning(f"⚠️ Web execution failed (attempt {attempt})")
                # ✅ FIX: Access dataclass attributes, not dict keys
                error_context = exec_result.error or 'Unknown error'
                
            except Exception as e:
                logger.error(f"❌ Exception during web execution: {e}")
                error_context = str(e)
            
            # Brief delay before retry to let browser/driver recover
            if attempt < max_retries:
                await asyncio.sleep(2)
        
        logger.error(f"❌ Web task {task.task_id} failed after {max_retries} attempts")
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error=f"Failed after {max_retries} attempts: {error_context}"
        )

# ============================================================================
# Unified Execution Agent
# ============================================================================

async def start_execution_agent_with_rag(broker_instance, desktop_rag, sandbox_pipeline, web_pipeline):
    desktop_bridge = CoordinatorRAGBridge(desktop_rag, sandbox_pipeline)
    web_bridge = CoordinatorWebRAGBridge(web_pipeline)

    # ════════════════════════════════════════════════════════════════
    # STOP SIGNAL HANDLER
    # ════════════════════════════════════════════════════════════════
    async def handle_execution_stop_signal(message):
        """Handle stop/interrupt signal from coordinator"""
        try:
            task_id = message.payload.get('task_id')
            command = message.payload.get('command', 'stop')

            if task_id:
                logger.warning(f"⏹️ [STOP SIGNAL] Received command '{command}' for task {task_id}")
                await _execution_stop_manager.mark_task_for_stop(task_id)
            else:
                logger.warning(f"⏹️ [STOP SIGNAL] Received command '{command}' for all tasks")
                # TODO: Implement stop-all logic if needed
        except Exception as e:
            logger.error(f"❌ [STOP SIGNAL] Error handling stop signal: {e}")

    # ════════════════════════════════════════════════════════════════
    # EXECUTION REQUEST HANDLER
    # ════════════════════════════════════════════════════════════════
    async def handle_execution_request(message):
        try:
            task_data = message.payload
            task_id = message.task_id or task_data.get('task_id', 'unknown')
            session_id = message.session_id

            logger.info(f"🎯 Task received: {task_data.get('ai_prompt', 'Unknown')}")
            logger.info(f"   Context: {task_data.get('context', 'NO CONTEXT')}")
            logger.info(f"   Target Agent: {task_data.get('target_agent', 'NO AGENT')}")

            # ✅ CHECK FOR STOP SIGNAL IMMEDIATELY
            if await _execution_stop_manager.is_task_stopped(task_id):
                logger.warning(f"⏹️ Task {task_id} marked for stop, aborting execution")
                result = TaskResult(
                    task_id=task_id,
                    status="stopped",
                    error="Task stopped by user"
                )

                from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels

                response_msg = AgentMessage(
                    message_type=MessageType.EXECUTION_RESPONSE,
                    sender=AgentType.EXECUTION,
                    receiver=AgentType.COORDINATOR,
                    session_id=session_id,
                    task_id=task_id,
                    response_to=message.message_id,
                    payload=result.dict()
                )

                await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
                await _execution_stop_manager.cleanup_task(task_id)
                return

            task = ActionTask.from_dict(task_data)

            try:
                if task.context == "web":
                    logger.info(f"🌐 WEB TASK - Using Playwright pipeline")
                    result = await web_bridge.execute_web_action_task(task, session_id)
                else:
                    logger.info(f"🖥️ DESKTOP TASK - Using RAG + pyautogui pipeline")
                    result = await desktop_bridge.execute_action_task(task)
            finally:
                # ✅ CLEANUP STOP STATE AFTER EXECUTION
                await _execution_stop_manager.cleanup_task(task_id)

            from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels

            response_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.EXECUTION,
                receiver=AgentType.COORDINATOR,
                session_id=session_id,
                task_id=task_id,
                response_to=message.message_id,
                payload=result.dict()
            )

            await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
            logger.info(f"✅ Sent result for task {task_id}: {result.status}")

        except Exception as e:
            logger.error(f"❌ Error processing execution request: {e}")

            task_id = message.task_id or getattr(message.payload, 'task_id', 'unknown')
            import traceback
            traceback.print_exc()

            error_result = TaskResult(
                task_id=task_id,
                status="failed",
                error=str(e)
            )

            from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels

            error_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.EXECUTION,
                receiver=AgentType.COORDINATOR,
                session_id=message.session_id,
                task_id=message.task_id,
                response_to=message.message_id,
                payload=error_result.dict()
            )

            await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, error_msg)
            await _execution_stop_manager.cleanup_task(task_id)

    from agents.utils.protocol import Channels

    # ✅ SUBSCRIBE TO BOTH EXECUTION REQUEST AND STOP SIGNALS
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)

    # Check if EXECUTION_STOP_SIGNAL channel exists, if not we'll handle it via another mechanism
    try:
        broker_instance.subscribe(Channels.EXECUTION_STOP_SIGNAL, handle_execution_stop_signal)
        logger.info("✅ Stop signal handler registered")
    except (AttributeError, ValueError) as e:
        logger.warning(f"⚠️ EXECUTION_STOP_SIGNAL channel not available yet: {e}")
        logger.info("   (Stop signals will be available once channel is added to protocol.py)")
    
    logger.info("✅ Unified Execution Agent started:")
    logger.info("   🌐 Web tasks → Playwright pipeline")
    logger.info("   🖥️ Desktop tasks → Desktop RAG pipeline")
    
    while True:
        await asyncio.sleep(1)

# ============================================================================
# Server Initialization
# ============================================================================

async def initialize_execution_agent_for_server(broker_instance):
    """Server-compatible initialization with BOTH desktop and web"""
    from dotenv import load_dotenv
    load_dotenv()
    
    if hasattr(broker_instance, '_rag_execution_subscribed'):
        logger.warning("⚠️ RAG Execution agent already subscribed, skipping")
        return
    broker_instance._rag_execution_subscribed = True
    
    try:
        # Desktop RAG System
        try:
            logger.info("🔧 Initializing Desktop RAG system ..")
            from agents.execution_agent.RAG.code_generation import RAGSystem, RAGConfig

            logger.info("🔧 Initializing RAG system...")
            desktop_rag_config = RAGConfig(library_name="pyautogui",retrieval_mode="API",use_rag=False)
            desktop_rag = RAGSystem(desktop_rag_config)
            desktop_rag.initialize()
            logger.info("✅ RAG system ready")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Desktop RAG: {e}")
            logger.info("📦 Starting fallback execution agent...")
            await start_simple_execution_agent(broker_instance)
            return
        
        # Desktop Sandbox
        try:
            logger.info("🔧 Initializing Desktop sandbox pipeline...")
            
            from agents.execution_agent.RAG.execution import (
                SandboxExecutionPipeline, 
                SandboxConfig,
                ActionCache
                # LocalSandbox  # Make sure this is imported
            )
            
            sandbox_config = SandboxConfig(timeout_seconds=60)
            # Try to enable cache, but continue without it if it fails
            enable_cache = False
            try:
                # Test if ChromaDB is available
                import chromadb
                logger.info( "📦 ChromaDB available - will attempt to enable action cache")
            except ImportError:
                logger.warning("⚠️ ChromaDB not available - disabling action cache")
                enable_cache = False

            # Initialize sandbox with optional cache
            try:
                sandbox_pipeline = SandboxExecutionPipeline(
                    sandbox_config,
                    enable_cache=enable_cache
                )
                
                if hasattr(sandbox_pipeline, 'action_cache') and sandbox_pipeline.action_cache:
                    logger.info(f"✅ Action cache enabled with {sandbox_pipeline.action_cache.collection.count()} cached actions")
                else:
                    logger.info("📝 Running without action cache")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize with cache: {e}")
                # Fallback: create without cache
                sandbox_pipeline = SandboxExecutionPipeline(sandbox_config, enable_cache=False)
            
            logger.info("✅ Desktop sandbox pipeline ready")
            
        except ImportError as ie:
            logger.error(f"❌ Sandbox import failed: {ie}")
            await start_simple_execution_agent(broker_instance)
            return
        except Exception as e:
            logger.error(f"❌ Sandbox initialization error: {e}")
            await start_simple_execution_agent(broker_instance)
            return
        
        # Playwright Web Pipeline
        web_pipeline = None
        try:
            logger.info("🔧 Initializing Playwright web pipeline...")
            
            from agents.execution_agent.RAG.web.web_execution import WebExecutionPipeline, WebExecutionConfig
            
            web_config = WebExecutionConfig(
                headless=False,
                timeout_seconds=30
            )
            web_pipeline = WebExecutionPipeline(web_config)
            
            # ✅ SHARE Groq client from desktop RAG to avoid API key issues
            # Only share if it's a real Groq SDK client (not a string like "mistral_rest")
            desktop_client = desktop_rag.llm.client
            if desktop_client is not None and not isinstance(desktop_client, str):
                web_pipeline.shared_groq_client = desktop_client
                logger.info("🔗 Shared Groq client from desktop RAG to web pipeline")
            else:
                logger.info(f"ℹ️ Desktop RAG uses {desktop_rag.config.llm_provider} — web pipeline will init its own Groq client")
            
            await web_pipeline.initialize()
            
            logger.info("✅ Playwright web pipeline ready")
            
        except ImportError as ie:
            logger.error(f"❌ Web pipeline import failed: {ie}")
            logger.info("⚠️ Web tasks will not be available")
            web_pipeline = None
        except Exception as e:
            logger.error(f"❌ Web pipeline initialization error: {e}")
            logger.info("⚠️ Web tasks will not be available")
            web_pipeline = None
        
        # Start Unified Execution Agent
        if web_pipeline:
            logger.info("🚀 Starting UNIFIED execution agent with Desktop + Web...")
            await start_execution_agent_with_rag(
                broker_instance, 
                desktop_rag, 
                sandbox_pipeline,
                web_pipeline
            )
        else:
            logger.warning("⚠️ Starting with DESKTOP RAG only (no web support)")
            await start_desktop_only_execution_agent(broker_instance, desktop_rag, sandbox_pipeline)
    
    except ImportError as e:
        logger.error(f"❌ Failed to import RAG components: {e}")
        logger.info("📦 Starting fallback execution agent...")
        await start_simple_execution_agent(broker_instance)

# ============================================================================
# Desktop-Only Fallback
# ============================================================================

async def start_desktop_only_execution_agent(broker_instance, rag_system, sandbox_pipeline):
    bridge = CoordinatorRAGBridge(rag_system, sandbox_pipeline)

    # ════════════════════════════════════════════════════════════════
    # STOP SIGNAL HANDLER
    # ════════════════════════════════════════════════════════════════
    async def handle_execution_stop_signal(message):
        """Handle stop/interrupt signal from coordinator"""
        try:
            task_id = message.payload.get('task_id')
            command = message.payload.get('command', 'stop')

            if task_id:
                logger.warning(f"⏹️ [STOP SIGNAL] Received command '{command}' for task {task_id}")
                await _execution_stop_manager.mark_task_for_stop(task_id)
            else:
                logger.warning(f"⏹️ [STOP SIGNAL] Received command '{command}' for all tasks")
        except Exception as e:
            logger.error(f"❌ [STOP SIGNAL] Error handling stop signal: {e}")

    async def handle_execution_request(message):
        try:
            task_data = message.payload
            task_id = message.task_id or task_data.get('task_id', 'unknown')
            task = ActionTask.from_dict(task_data)

            # ✅ CHECK FOR STOP SIGNAL IMMEDIATELY
            if await _execution_stop_manager.is_task_stopped(task_id):
                logger.warning(f"⏹️ Task {task_id} marked for stop, aborting execution")
                result = TaskResult(
                    task_id=task_id,
                    status="stopped",
                    error="Task stopped by user"
                )

                from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels

                response_msg = AgentMessage(
                    message_type=MessageType.EXECUTION_RESPONSE,
                    sender=AgentType.EXECUTION,
                    receiver=AgentType.COORDINATOR,
                    session_id=message.session_id,
                    task_id=task_id,
                    response_to=message.message_id,
                    payload=result.dict()
                )

                await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
                await _execution_stop_manager.cleanup_task(task_id)
                return

            try:
                if task.context == "web":
                    logger.error("❌ Web tasks not supported (web pipeline not initialized)")
                    result = TaskResult(
                        task_id=task.task_id,
                        status="failed",
                        error="Web automation not available"
                    )
                else:
                    result = await bridge.execute_action_task(task)
            finally:
                # ✅ CLEANUP STOP STATE AFTER EXECUTION
                await _execution_stop_manager.cleanup_task(task_id)

            from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels

            response_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.EXECUTION,
                receiver=AgentType.COORDINATOR,
                session_id=message.session_id,
                task_id=task.task_id,
                response_to=message.message_id,
                payload=result.dict()
            )

            await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)

        except Exception as e:
            logger.error(f"❌ Error in desktop execution: {e}")
            task_id = message.task_id or getattr(message.payload, 'task_id', 'unknown')
            await _execution_stop_manager.cleanup_task(task_id)

    from agents.utils.protocol import Channels
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)

    # Check if EXECUTION_STOP_SIGNAL channel exists
    try:
        broker_instance.subscribe(Channels.EXECUTION_STOP_SIGNAL, handle_execution_stop_signal)
        logger.info("✅ Stop signal handler registered")
    except (AttributeError, ValueError) as e:
        logger.warning(f"⚠️ EXECUTION_STOP_SIGNAL channel not available yet: {e}")

    logger.info("✅ Desktop-Only Execution Agent started")

    while True:
        await asyncio.sleep(1)

# ============================================================================
# Fallback Execution Agent
# ============================================================================

async def start_simple_execution_agent(broker_instance):
    from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels

    # ════════════════════════════════════════════════════════════════
    # STOP SIGNAL HANDLER
    # ════════════════════════════════════════════════════════════════
    async def handle_execution_stop_signal(message):
        """Handle stop/interrupt signal from coordinator"""
        try:
            task_id = message.payload.get('task_id')
            command = message.payload.get('command', 'stop')

            if task_id:
                logger.warning(f"⏹️ [STOP SIGNAL] Received command '{command}' for task {task_id}")
                await _execution_stop_manager.mark_task_for_stop(task_id)
            else:
                logger.warning(f"⏹️ [STOP SIGNAL] Received command '{command}' for all tasks")
        except Exception as e:
            logger.error(f"❌ [STOP SIGNAL] Error handling stop signal: {e}")

    async def handle_execution_request(message):
        try:
            task_data = message.payload
            task_id = task_data.get('task_id', 'unknown')
            ai_prompt = task_data.get('ai_prompt', '')

            logger.info(f"🎯 Fallback execution agent received task {task_id}: {ai_prompt[:50]}...")

            # ✅ CHECK FOR STOP SIGNAL IMMEDIATELY
            if await _execution_stop_manager.is_task_stopped(task_id):
                logger.warning(f"⏹️ Task {task_id} marked for stop, returning stopped status")
                result = {
                    'task_id': task_id,
                    'status': 'stopped',
                    'content': None,
                    'error': 'Task stopped by user'
                }

                response_msg = AgentMessage(
                    message_type=MessageType.EXECUTION_RESPONSE,
                    sender=AgentType.EXECUTION,
                    receiver=AgentType.COORDINATOR,
                    session_id=message.session_id,
                    task_id=task_id,
                    response_to=message.message_id,
                    payload=result
                )

                await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
                await _execution_stop_manager.cleanup_task(task_id)
                return

            result = {
                'task_id': task_id,
                'status': 'pending',
                'content': f"Task '{ai_prompt}' awaiting RAG execution",
                'error': None
            }

            response_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.EXECUTION,
                receiver=AgentType.COORDINATOR,
                session_id=message.session_id,
                task_id=task_id,
                response_to=message.message_id,
                payload=result
            )

            await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
            logger.info(f"⏳ Sent pending status for task {task_id}")
            await _execution_stop_manager.cleanup_task(task_id)

        except Exception as e:
            logger.error(f"❌ Error in fallback execution: {e}")
            task_id = task_data.get('task_id', 'unknown') if 'task_data' in locals() else 'unknown'
            await _execution_stop_manager.cleanup_task(task_id)

    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)

    # Check if EXECUTION_STOP_SIGNAL channel exists
    try:
        broker_instance.subscribe(Channels.EXECUTION_STOP_SIGNAL, handle_execution_stop_signal)
        logger.info("✅ Stop signal handler registered")
    except (AttributeError, ValueError) as e:
        logger.warning(f"⚠️ EXECUTION_STOP_SIGNAL channel not available yet: {e}")

    logger.info("✅ Fallback Execution Agent started")

    while True:
        await asyncio.sleep(1)
