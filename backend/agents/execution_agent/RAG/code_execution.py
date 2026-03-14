import json
import logging
import asyncio
import os
import sys
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

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
                 web_params: Optional[Dict[str, Any]] = None, depends_on: Optional[str] = None):
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
    def __init__(self, task_id: str, status: str, content: Optional[str] = None, error: Optional[str] = None):
        self.task_id = task_id
        self.status = status
        self.content = content
        self.error = error
    
    def dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'status': self.status,
            'content': self.content,
            'error': self.error
        }

# ============================================================================
# RAG Task Adapter
# ============================================================================

class RAGTaskAdapter:
    @staticmethod
    def build_rag_query(task: ActionTask) -> str:
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
                query_parts.append(f"Input data: {task.extra_params['input_content'][:200]}...")
        
        if task.context == "local":
            query_parts.append("(desktop automation)")
        elif task.context == "web":
            query_parts.append("(Playwright web automation)")
        
        enhanced_query = " | ".join(query_parts)
        logger.debug(f"🔍 Enhanced query: {enhanced_query[:100]}...")
        return enhanced_query
    
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
        Extract what UI element to look for based on task and error
        """
        prompt = task.ai_prompt.lower()
        
        # Skip OmniParser for app launch tasks
        app_launch_keywords = ['open', 'launch', 'start', 'run']
        is_app_launch = any(keyword in prompt.split()[:2] for keyword in app_launch_keywords)
        
        if is_app_launch:
            logger.info(f"⏭️ Skipping OmniParser - this is an app launch task")
            return None
        
        # ========================================================================
        # IMPROVED: Extract just the element name, not the full phrase
        # ========================================================================
        import re
        
        # Pattern 1: "click on X" → extract X
        # Example: "click on Gaming" → "Gaming"
        pattern1 = r'click\s+(?:on\s+)?(?:the\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
        match = re.search(pattern1, task.ai_prompt)  # Use original case!
        if match:
            element_text = match.group(1).strip()
            logger.info(f"📝 Extracted element name: '{element_text}'")
            return element_text
        
        # Pattern 2: "click the X button/icon" → extract X
        # Example: "click the Submit button" → "Submit"
        pattern2 = r'click\s+(?:the\s+)?([A-Z][a-z]+)(?:\s+button|\s+icon)'
        match = re.search(pattern2, task.ai_prompt)
        if match:
            element_text = match.group(1).strip()
            logger.info(f"📝 Extracted button/icon name: '{element_text}'")
            return element_text
        
        # Pattern 3: Fallback - look for capitalized words (likely UI element names)
        # Example: "Gaming in Microsoft Store" → "Gaming"
        capital_words = re.findall(r'\b[A-Z][a-z]+\b', task.ai_prompt)
        if capital_words:
            # Filter out common words
            stopwords = {'Click', 'Open', 'The', 'In', 'Microsoft', 'Store', 'Discord', 'On'}
            filtered = [w for w in capital_words if w not in stopwords]
            if filtered:
                element_text = filtered[0]  # Take first meaningful word
                logger.info(f"📝 Extracted from capitalized words: '{element_text}'")
                return element_text
        
        # If nothing found, skip OmniParser
        logger.warning(f"⚠️ Could not extract specific UI element - skipping OmniParser")
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
            
        
        
        # Build enhanced query for RAG
        rag_query = self.adapter.build_rag_query(task)

        # Inject active file context so consecutive tasks work on the same file
        if self.last_file_path:
            rag_query = f"[ACTIVE FILE: {self.last_file_path}]\n\n{rag_query}"
            logger.info(f"[FILE CONTEXT] Injecting active file: {self.last_file_path}")
        
        # ========================================================================
        # STEP 0: CHECK CACHE FIRST (if enabled and available)
        # ========================================================================
        if enable_cache and hasattr(self.sandbox, 'action_cache'):
            try:
                logger.info(f"🔍 Checking cache for task {task.task_id}...")
                cached_action = self.sandbox.action_cache.search_cache(
                    rag_query,  # Use enhanced query for better matching
                    # threshold=cache_threshold
                )
                
                if cached_action:
                    logger.info(f"✅ CACHE HIT! Similarity: {cached_action['similarity']:.2%}")
                    logger.info(f"⚡ Skipping RAG + Sandbox - using validated code!")
                    
                    # Execute cached code directly (already validated)
                    import subprocess
                    import tempfile
                    import sys
                    import time
                    
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                        f.write(cached_action['code'])
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
                        
                        exec_result = ExecutionResult(
                            status=ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.FAILED,
                            exit_code=result.returncode,
                            stdout=result.stdout,
                            stderr=result.stderr,
                            execution_time=execution_time,
                            timestamp=datetime.now().isoformat(),
                            validation_passed=True,  # Already validated when cached
                            validation_errors=[],
                            security_passed=True,    # Already validated when cached
                            security_violations=[],
                            code_hash=cached_action['metadata']['code_hash']
                        )
                        
                        logger.info(f"✅ Cached code executed in {execution_time:.3f}s")
                        
                        # Convert to TaskResult
                        return self.adapter.execution_result_to_task_result(task, exec_result)
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Cached code execution failed: {e}")
                        logger.info("🔄 Falling back to RAG generation...")
                        # Continue to RAG flow below
                    finally:
                        import os
                        try:
                            os.unlink(temp_file)
                        except:
                            pass
                else:
                    logger.info(f"❌ Cache miss - proceeding with RAG generation")
                    
            except Exception as cache_error:
                logger.warning(f"⚠️ Cache check failed: {cache_error}")
                logger.info("🔄 Proceeding with RAG generation...")
                # Continue to RAG flow below
        
        # ========================================================================
        # CACHE MISS OR DISABLED - FULL RAG + SANDBOX FLOW
        # ========================================================================
        # from agents.execution_agent.RAG.fast_window_detector import get_current_window

        # Get current screen state BEFORE generating code
        # With this:
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
                rag_result = self.rag.generate_code(
                    enhanced_query,
                    cache_key=task.ai_prompt,  # Use original prompt for cache key
                    start_context_index=start_context_index,
                    num_contexts=self.rag.config.top_k,
                    screen_state=screen_state
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
                exec_result = self.sandbox.execute_code(
                    code=generated_code,
                    use_docker=False,
                    retry_on_failure=False
                )
                
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
                            logger.info(f"💾 Caching validated action...")
                            self.sandbox.action_cache.store_action(
                                query=rag_query,  # Use enhanced query for better future matching
                                code=generated_code,
                                execution_result=exec_result
                            )
                            logger.info(f"✅ Action cached successfully")
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
            #     if any(keyword in error_context.lower() for keyword in 
            #         [
            #             'not found', 'cannot find', 'no such element', 'failed to locate',
            #             'modulenotfounderror', 'importerror',
            #             'pywinauto', 'uiautomation',
            #             'element', 'button', 'window',
            #             'failed:', 'error:',
            #             'locateonscreen'
            #         ]):

            #         logger.info(f"🔍 OmniParser trigger check:")
            #         logger.info(f"   Error context: {error_context[:200]}")
            #         logger.info(f"   Attempting OmniParser fallback...")
                                                    
            #         logger.warning("🔍 Error suggests element detection issue - trying OmniParser...")
                    
            #         # Extract what to look for
            #         element_desc = self._extract_element_description(task, error_context)
                    
            #         if element_desc is None:
            #             logger.info("⏭️ Skipping OmniParser - not a UI interaction task")
            #             # Don't continue here - let it retry with next context
            #         elif element_desc:
            #             # Try to detect coordinates
            #             logger.info(f"🎯 Valid UI element detected: '{element_desc}'")
            #             coords = self._detect_element_coordinates(element_desc)
                        
            #             if coords:
            #                 logger.info(f"✅ OmniParser found element at {coords}!")
                            
            #                 # Generate new code with exact coordinates
            #                 new_code = self._regenerate_code_with_coordinates(
            #                     task, coords, element_desc
            #                 )
                            
            #                 # Execute the new code
            #                 logger.info("🔄 Executing OmniParser-assisted code...")
            #                 logger.debug(f"Generated code:\n{new_code}")  # Use debug level, not info
                            
            #                 exec_result = self.sandbox.execute_code(
            #                     code=new_code,
            #                     use_docker=False,
            #                     retry_on_failure=False
            #                 )
                            
            #                 if exec_result.validation_passed and exec_result.security_passed:
            #                     logger.info(f"✅✅✅ Task succeeded with OmniParser assistance!")
            #                     return self.adapter.execution_result_to_task_result(task, exec_result)
            #                 else:
            #                     logger.warning("⚠️ OmniParser-assisted code also failed")
            #                     logger.debug(f"OmniParser execution stdout: {exec_result.stdout}")
            #                     logger.debug(f"OmniParser execution stderr: {exec_result.stderr}")
            #             else:
            #                 logger.warning(f"❌ OmniParser couldn't find: '{element_desc}'")
                
            #     logger.debug(f"Error context for retry: {error_context}")
                
            #     start_context_index += self.rag.config.top_k
                
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
                }
                
                exec_result = await self.web.execute_web_task(task_dict, session_id)
                
                # ✅ FIX: exec_result is a WebExecutionResult dataclass, not a dict
                if exec_result.validation_passed and exec_result.security_passed:
                    logger.info(f"✅ Web task {task.task_id} completed successfully")
                    return TaskResult(
                        task_id=task.task_id,
                        status="success",
                        content=exec_result.output or '',
                        error=None
                    )
                
                logger.warning(f"⚠️ Web execution failed (attempt {attempt})")
                # ✅ FIX: Access dataclass attributes, not dict keys
                error_context = exec_result.error or 'Unknown error'
                
            except Exception as e:
                logger.error(f"❌ Exception during web execution: {e}")
                error_context = str(e)
        
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
    
    async def handle_execution_request(message):
        try:
            task_data = message.payload
            task_id = message.task_id or task_data.get('task_id', 'unknown')
            session_id = message.session_id
            
            logger.info(f"🎯 Task received: {task_data.get('ai_prompt', 'Unknown')}")
            logger.info(f"   Context: {task_data.get('context', 'NO CONTEXT')}")
            logger.info(f"   Target Agent: {task_data.get('target_agent', 'NO AGENT')}")
            
            task = ActionTask.from_dict(task_data)
            
            if task.context == "web":
                logger.info(f"🌐 WEB TASK - Using Playwright pipeline")
                result = await web_bridge.execute_web_action_task(task, session_id)
            else:
                logger.info(f"🖥️ DESKTOP TASK - Using RAG + pyautogui pipeline")
                result = await desktop_bridge.execute_action_task(task)
            
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
    
    from agents.utils.protocol import Channels
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)
    
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
            desktop_rag_config = RAGConfig(library_name="pyautogui",retrieval_mode="local",use_rag=False)
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
            
            sandbox_config = SandboxConfig(timeout_seconds=30)
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
            web_pipeline.shared_groq_client = desktop_rag.llm.client
            logger.info("🔗 Shared Groq client from desktop RAG to web pipeline")
            
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
    
    async def handle_execution_request(message):
        try:
            task_data = message.payload
            task = ActionTask.from_dict(task_data)
            
            if task.context == "web":
                logger.error("❌ Web tasks not supported (web pipeline not initialized)")
                result = TaskResult(
                    task_id=task.task_id,
                    status="failed",
                    error="Web automation not available"
                )
            else:
                result = await bridge.execute_action_task(task)
            
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
    
    from agents.utils.protocol import Channels
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)
    
    logger.info("✅ Desktop-Only Execution Agent started")
    
    while True:
        await asyncio.sleep(1)

# ============================================================================
# Fallback Execution Agent
# ============================================================================

async def start_simple_execution_agent(broker_instance):
    from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels
    
    async def handle_execution_request(message):
        try:
            task_data = message.payload
            task_id = task_data.get('task_id', 'unknown')
            ai_prompt = task_data.get('ai_prompt', '')
            
            logger.info(f"🎯 Fallback execution agent received task {task_id}: {ai_prompt[:50]}...")
            
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
            
        except Exception as e:
            logger.error(f"❌ Error in fallback execution: {e}")
    
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)
    logger.info("✅ Fallback Execution Agent started")
    
    while True:
        await asyncio.sleep(1)