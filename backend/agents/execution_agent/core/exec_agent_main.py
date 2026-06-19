"""
Execution Agent - Main Module
Multi-Modal Automation System

Author: Accessibility AI Team
Version: 1.0.0
"""

import time
import platform
from datetime import datetime
from typing import Dict
from dataclasses import asdict

# from backend.agents.execution_agent.core.exec_agent_config import Config, ExecutionContext, ActionStatus
# from backend.agents.execution_agent.core.exec_agent_models import ExecutionTask, ExecutionResult
# from backend.agents.execution_agent.utils.exec_agent_logger import setup_logging
# from backend.agents.execution_agent.core.exec_agent_deps import check_dependencies
# from backend.agents.execution_agent.layers.exec_agent_vision import VisionLayer
# from backend.agents.execution_agent.layers.exec_agent_action import ActionLayer
# from backend.agents.execution_agent.layers.exec_agent_safety import SafetyLayer


# ✅ CORRECT:
from .exec_agent_config import Config, ExecutionContext, ActionStatus
from .exec_agent_models import ExecutionTask, ExecutionResult
from .exec_agent_deps import check_dependencies

# Import from other packages
from ..layers.exec_agent_vision import VisionLayer
from ..layers.exec_agent_action import ActionLayer
from ..layers.exec_agent_safety import SafetyLayer
from ..strategies.local_strategy import LocalStrategy
from ..strategies.system_strategy import SystemStrategy
from ..strategies.mobile_strategy import MobileStrategy  # ← NEW: Mobile strategy
from ..utils import setup_logging

try:
    from ..strategies.web_strategy import WebStrategy
except ImportError:
    WebStrategy = None

import logging
logger = logging.getLogger(__name__)

class ExecutionAgent:
    """
    Main Execution Agent
    Coordinates Vision, Action, Safety layers
    Routes tasks to appropriate strategies
    """
    
    def __init__(self, log_level=None):
        """ 
        Initialize Execution Agent
        
        Args:
            log_level: Logging level (default from Config)
        """
        # Create directories
        Config.create_directories()
        
        # Setup logging
        self.logger = setup_logging("ExecutionAgent", log_level)
        self.logger.info("="*60)
        self.logger.info("Execution Agent Initialized")
        self.logger.info(f"Platform: {platform.system()} {platform.architecture()[0]}")
        self.logger.info("="*60)
        
        # Initialize layers
        self.vision_layer = VisionLayer(self.logger)
        self.safety_layer = SafetyLayer(self.logger)
        self.action_layer = ActionLayer(self.logger, self.vision_layer)
        
        # Initialize strategies
        local_strategy = LocalStrategy(
            self.logger, self.vision_layer, self.action_layer, self.safety_layer
        )
        
        # Integrate PowerPoint handler
        try:
            from backend.agents.execution_agent.handlers.exec_agent_ppt_handler import integrate_powerpoint_handler
            local_strategy = integrate_powerpoint_handler(local_strategy, self.logger)
            self.logger.info("PowerPoint handler integrated")
        except ImportError:
            self.logger.warning("PowerPoint handler not available")
        
        # Initialize mobile strategy (separate from desktop)
        mobile_strategy = MobileStrategy(device_id="default_device")
        self.logger.info("✅ Mobile strategy initialized")
        
        self.strategies = {
            ExecutionContext.LOCAL.value: local_strategy,
            ExecutionContext.SYSTEM.value: SystemStrategy(
                self.logger, self.safety_layer
            ),
            "mobile": mobile_strategy  # ← NEW: Mobile context
        }

        if WebStrategy is not None:
            self.strategies[ExecutionContext.WEB.value] = WebStrategy(
                self.logger, self.safety_layer
            )
        else:
            self.logger.warning("Web strategy module is unavailable; WEB context disabled")
        
        # Check dependencies
        self.dependencies = check_dependencies(self.logger)
    
    def execute(self, task: ExecutionTask) -> ExecutionResult:
        """
        Main execution entry point
        Routes task to appropriate strategy
        
        Args:
            task: ExecutionTask object
        
        Returns:
            ExecutionResult object
        """
        self.logger.info(f"Received task: {task.action_type} (context: {task.context})")
        
        # Validate task
        if not task.context or not task.strategy:
            return ExecutionResult(
                status=ActionStatus.FAILED.value,
                task_id=task.task_id,
                context=task.context or "unknown",
                action=task.action_type,
                details="Invalid task: missing context or strategy",
                logs=["Task validation failed"],
                timestamp=datetime.now().isoformat(),
                duration=0.0,
                error="Invalid task structure"
            )
        
        # Get appropriate strategy
        strategy = self.strategies.get(task.context)
        
        if not strategy:
            return ExecutionResult(
                status=ActionStatus.FAILED.value,
                task_id=task.task_id,
                context=task.context,
                action=task.action_type,
                details=f"Unknown context: {task.context}",
                logs=["Unknown execution context"],
                timestamp=datetime.now().isoformat(),
                duration=0.0,
                error="Unknown context"
            )
        
        # Execute with retry logic
        attempt = 0
        last_error = None
        
        while attempt < task.retry_count:
            try:
                self.logger.info(f"Execution attempt {attempt + 1}/{task.retry_count}")
                result = strategy.execute(task)
                
                # Log to audit trail
                self.safety_layer.audit_log_action(
                    action=task.action_type,
                    status=result.status,
                    details=asdict(result)
                )
                if result.status == ActionStatus.SUCESS.value:
                    if hasattr(result,'metadata') and result.metadata:
                        if result.metadata.get('detection_method') == 'omniparser':
                            self.logger.warning(f"⚠️ SUCCESS via OmniParser fallback!")
                    
                    self.logger.info(f"✅ Task completed successfully")
                
                if result.status == ActionStatus.SUCCESS.value:
                    self.logger.info(f"Task completed successfully: {task.action_type}")
                    return result
                
                elif result.status == ActionStatus.AWAITING_CONFIRMATION.value:
                    self.logger.info("Task awaiting user confirmation")
                    return result
                
                else:
                    last_error = result.error
                    attempt += 1
                    
                    if attempt < task.retry_count:
                        self.logger.warning(f"Attempt failed, retrying... ({attempt}/{task.retry_count})")
                        time.sleep(2)
            
            except Exception as e:
                last_error = str(e)
                self.logger.error(f"Execution error: {e}")
                attempt += 1
                
                if attempt < task.retry_count:
                    time.sleep(2)
        
        # All retries exhausted
        self.logger.error(f"Task failed after {task.retry_count} attempts")
        return ExecutionResult(
            status=ActionStatus.FAILED.value,
            task_id=task.task_id,
            context=task.context,
            action=task.action_type,
            details=f"Failed after {task.retry_count} attempts",
            logs=[f"All retry attempts exhausted. Last error: {last_error}"],
            timestamp=datetime.now().isoformat(),
            duration=0.0,
            error=last_error
        )
    
    def execute_from_dict(self, task_dict: Dict) -> Dict:
        """
        Execute task from dictionary (for Coordinator Agent integration)
        
        Args:
            task_dict: Dictionary with task parameters
            
        Returns:
            Result dictionary
        """
        try:
            task = ExecutionTask(
                action_type=task_dict.get("action", "unknown"),
                context=task_dict.get("context", "local"),
                strategy=task_dict.get("strategy", "default"),
                params=task_dict.get("params", {}),
                task_id=task_dict.get("task_id"),
                priority=task_dict.get("priority", "normal"),
                timeout=task_dict.get("timeout", Config.DEFAULT_TIMEOUT),
                retry_count=task_dict.get("retry_count", Config.DEFAULT_RETRY_COUNT),
                fallback_strategies=task_dict.get("fallback_strategies")
            )
            
            result = self.execute(task)
            return result.to_dict()
        
        except Exception as e:
            self.logger.error(f"Error parsing task: {e}")
            return {
                "status": "failed",
                "error": f"Task parsing error: {e}",
                "timestamp": datetime.now().isoformat()
            }
    
    def get_status(self) -> Dict:
        """
        Get agent status and capabilities
        
        Returns:
            Status dictionary
        """
        return {
            "agent": "ExecutionAgent",
            "version": "1.0.0",
            "platform": f"{platform.system()} {platform.architecture()[0]}",
            "dependencies": self.dependencies,
            "strategies": list(self.strategies.keys()),
            "undo_queue_size": len(self.safety_layer.undo_queue),
            "audit_log_size": len(self.safety_layer.audit_log)
        }
